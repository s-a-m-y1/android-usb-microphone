"""PhoneClient behaviour tests against a fake phone (TCP server).

Covers: handshake parsing, PCM dispatch, JSON events, disconnect detection,
and reconnect behaviour of a fresh client instance.
"""

import socket
import struct
import threading
import time
import unittest

from aum.transport.phone_client import PhoneClient, PhoneLinkError
from aum.transport.protocol import encode_frame, encode_json, FrameType


class FakePhone:
    """A TCP server that pretends to be the Android app."""

    def __init__(self) -> None:
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self.conn: socket.socket | None = None
        self.received = bytearray()

    def accept(self, timeout: float = 3.0) -> socket.socket:
        self.sock.settimeout(timeout)
        conn, _ = self.sock.accept()
        self.conn = conn
        return conn

    def send_hello(self, conn: socket.socket, rate: int = 48000) -> None:
        conn.sendall(encode_json(FrameType.JSON,
                                 {"event": "hello", "rate": rate,
                                  "channels": 1, "format": "s16le"}))

    def send_pcm(self, conn: socket.socket, payload: bytes) -> None:
        conn.sendall(encode_frame(FrameType.PCM, payload))


class TestPhoneClient(unittest.TestCase):
    def setUp(self):
        self.phone = FakePhone()
        self.pcm_chunks: list[bytes] = []
        self.json_events: list[dict] = []
        self.closed_reasons: list[str] = []
        self.client = PhoneClient(
            "127.0.0.1", self.phone.port,
            on_pcm=self.pcm_chunks.append,
            on_json=self.json_events.append,
            on_closed=self.closed_reasons.append,
        )

    def tearDown(self):
        self.client.close()
        if self.phone.conn:
            try:
                self.phone.conn.close()
            except OSError:
                pass
        self.phone.sock.close()

    def wait_until(self, pred, timeout=3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if pred():
                return True
            time.sleep(0.02)
        return False

    def test_handshake(self):
        threading.Thread(target=lambda: (
            self.phone.send_hello(self.phone.accept()), ), daemon=True).start()
        hello = self.client.connect()
        self.assertEqual(hello["rate"], 48000)
        self.assertEqual(hello["format"], "s16le")
        self.assertTrue(self.client.alive)

    def test_pcm_and_events_dispatch(self):
        def server():
            conn = self.phone.accept()
            self.phone.send_hello(conn)
            self.phone.send_pcm(conn, b"\x01\x02")
            self.phone.send_pcm(conn, b"\x03\x04")
            conn.sendall(encode_json(FrameType.PHONE_EVENT,
                                     {"event": "level", "rms": 0.5}))
        threading.Thread(target=server, daemon=True).start()
        self.client.connect()
        self.assertTrue(self.wait_until(
            lambda: len(self.pcm_chunks) == 2))
        self.assertEqual(self.pcm_chunks, [b"\x01\x02", b"\x03\x04"])
        self.assertTrue(self.wait_until(
            lambda: self.json_events and self.json_events[0]["rms"] == 0.5))

    def test_disconnect_detected(self):
        def server():
            conn = self.phone.accept()
            self.phone.send_hello(conn)
            time.sleep(0.1)
            conn.close()   # simulate USB/adb drop
        threading.Thread(target=server, daemon=True).start()
        self.client.connect()
        self.assertTrue(self.wait_until(lambda: bool(self.closed_reasons)))
        self.assertFalse(self.client.alive)
        self.assertIn("disconnected", self.closed_reasons[0].lower())

    def test_no_listener_raises(self):
        dead = PhoneClient("127.0.0.1", 1, lambda p: None,
                           lambda j: None, lambda r: None)
        with self.assertRaises(PhoneLinkError):
            dead.connect(timeout=1.0)

    def test_reconnect_after_disconnect(self):
        """After the phone drops the link, a NEW client connects cleanly."""
        conn_holder = {}

        def server():
            conn = self.phone.accept()
            conn_holder["conn"] = conn
            self.phone.send_hello(conn)
            # then closed by tearDown

        threading.Thread(target=server, daemon=True).start()
        self.client.connect()
        self.client.close()

        client2 = PhoneClient(
            "127.0.0.1", self.phone.port,
            on_pcm=self.pcm_chunks.append,
            on_json=self.json_events.append,
            on_closed=self.closed_reasons.append,
        )
        try:
            threading.Thread(target=lambda: (
                self.phone.send_hello(self.phone.accept()), ),
                daemon=True).start()
            hello = client2.connect()
            self.assertEqual(hello["event"], "hello")
        finally:
            client2.close()


if __name__ == "__main__":
    unittest.main()
