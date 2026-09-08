"""Client for the phone's audio socket (via adb forward) — USB-only transport.

Reads framed PCM/JSON from the phone, dispatches to callbacks, and reports
link state. The receive loop runs on its own thread; callbacks are invoked
from that thread and must be fast (the engine forwards PCM into the daemon
without blocking).
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from typing import Callable

from .protocol import Frame, FrameParser, FrameType

log = logging.getLogger(__name__)


class PhoneLinkError(Exception):
    pass


class PhoneClient:
    """One connection to the phone app. Reconnect responsibility belongs to
    the engine; this class just manages a single socket cleanly."""

    def __init__(
        self,
        host: str,
        port: int,
        on_pcm: Callable[[bytes], None],
        on_json: Callable[[dict], None],
        on_closed: Callable[[str], None],
    ) -> None:
        self._host = host
        self._port = port
        self._on_pcm = on_pcm
        self._on_json = on_json
        self._on_closed = on_closed
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._closing = threading.Event()
        self.hello: dict = {}

    @property
    def alive(self) -> bool:
        return self._sock is not None and not self._closing.is_set()

    def connect(self, timeout: float = 3.0) -> dict:
        """Connect and wait for the phone's hello handshake. Returns it."""
        try:
            self._sock = socket.create_connection((self._host, self._port),
                                                  timeout=timeout)
        except OSError as e:
            self._sock = None
            raise PhoneLinkError(
                f"Cannot reach the phone over USB ({e}). "
                "Is the Android app running with 'Start' pressed?") from e
        self._sock.settimeout(5.0)
        parser = FrameParser()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data = self._sock.recv(65536)
            except socket.timeout:
                continue
            except OSError as e:
                self.close()
                raise PhoneLinkError(f"Socket error after connect: {e}") from e
            if not data:
                self.close()
                raise PhoneLinkError("Phone closed the connection immediately")
            for frame in parser.feed(data):
                if frame.type in (FrameType.JSON, FrameType.PHONE_EVENT):
                    try:
                        obj = frame.json
                    except FrameError:
                        continue
                    if obj.get("event") == "hello":
                        self.hello = obj
                        self._spawn_reader()
                        return obj
                self._dispatch(frame)  # frames bundled with the hello
        self.close()
        raise PhoneLinkError("Phone never sent a handshake (is 'Start' pressed?)")

    def _spawn_reader(self) -> None:
        self._thread = threading.Thread(target=self._read_loop,
                                        name="aum-phone-reader", daemon=True)
        self._thread.start()

    def _read_loop(self) -> None:
        assert self._sock is not None
        parser = FrameParser()
        buffer = bytearray()
        try:
            self._sock.settimeout(None)
            while not self._closing.is_set():
                data = self._sock.recv(65536)
                if not data:
                    break
                for frame in parser.feed(data):
                    self._dispatch(frame)
        except OSError:
            pass
        finally:
            client_initiated = self._closing.is_set()
            self.close()
            if not client_initiated:
                self._on_closed("Audio stream disconnected from phone")

    def _dispatch(self, frame: Frame) -> None:
        if frame.type == FrameType.PCM:
            self._on_pcm(frame.payload)
        elif frame.type in (FrameType.JSON, FrameType.PHONE_EVENT):
            try:
                self._on_json(frame.json)
            except FrameError:
                log.debug("unparseable JSON frame dropped")

    def close(self) -> None:
        self._closing.set()
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
