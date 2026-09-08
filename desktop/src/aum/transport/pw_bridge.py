"""Bridge to the aum-pw-source PipeWire daemon over its local unix socket.

The daemon owns the PipeWire stream (realtime); we just push framed PCM into
its ring buffer and read status events. PCM writes come from the phone reader
thread; event reads happen on a dedicated thread.
"""

from __future__ import annotations

import json
import logging
import socket
import threading
from typing import Callable

from ..transport.protocol import FrameType, encode_frame, encode_json

log = logging.getLogger(__name__)

TYPE_PCM = FrameType.PCM
TYPE_JSON = FrameType.JSON


class DaemonBridgeError(Exception):
    pass


class PwBridge:
    def __init__(self, socket_path: str,
                 on_event: Callable[[dict], None] | None = None) -> None:
        self.socket_path = socket_path
        self._on_event = on_event
        self._sock: socket.socket | None = None
        self._send_lock = threading.Lock()
        self._reader: threading.Thread | None = None
        self._closing = threading.Event()

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def connect(self) -> None:
        try:
            self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._sock.connect(self.socket_path)
        except OSError as e:
            self._sock = None
            raise DaemonBridgeError(
                f"Cannot reach the PipeWire daemon at {self.socket_path}: {e}") from e
        self._closing.clear()
        self._reader = threading.Thread(target=self._event_loop,
                                        name="aum-pw-events", daemon=True)
        self._reader.start()

    def send_config(self, cfg: dict) -> None:
        self._send(encode_json(TYPE_JSON, cfg))

    def send_pcm(self, payload: bytes) -> None:
        self._send(encode_frame(TYPE_PCM, payload))

    def flush(self) -> None:
        self._send(encode_json(TYPE_JSON, {"cmd": "flush"}))

    def _send(self, data: bytes) -> None:
        sock = self._sock
        if sock is None:
            raise DaemonBridgeError("daemon socket is not connected")
        with self._send_lock:
            try:
                sock.sendall(data)
            except OSError as e:
                self._sock = None
                raise DaemonBridgeError(f"daemon socket write failed: {e}") from e

    def _event_loop(self) -> None:
        sock = self._sock
        if sock is None:
            return
        buf = bytearray()
        try:
            while not self._closing.is_set():
                data = sock.recv(65536)
                if not data:
                    break
                buf.extend(data)
                while len(buf) >= 4:
                    length = buf[2] | (buf[3] << 8)
                    if len(buf) < 4 + length:
                        break
                    payload = bytes(buf[4:4 + length])
                    del buf[:4 + length]
                    if self._on_event:
                        try:
                            self._on_event(json.loads(payload.decode("utf-8")))
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            log.debug("bad event JSON from daemon")
        except OSError:
            pass

    def close(self) -> None:
        self._closing.set()
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
