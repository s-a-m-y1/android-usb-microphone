"""Fallback transport: shell-uid capture over `adb exec-out`.

Used when the Android app is not installed. Requires only the prebuilt
`aum-stream.jar` pushed to /data/local/tmp; audio is captured on the phone
with AudioRecord (shell uid holds RECORD_AUDIO) and piped to stdout, which
adb transports over USB. The subprocess IS the connection: when it dies,
the link is down.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
from typing import Callable

from .protocol import FrameParser, FrameType

log = logging.getLogger(__name__)

DEVICE_JAR = "/data/local/tmp/aum-stream.jar"


class ExecOutError(Exception):
    pass


def jar_available_locally(root: str) -> bool:
    return bool(shutil.which("adb")) and \
        __import__("pathlib").Path(root, "poc", "aum-stream.jar").exists()


class ExecOutClient:
    """Same callback interface as PhoneClient."""

    def __init__(self, adb_binary: str, jar_host_path: str,
                 on_pcm: Callable[[bytes], None],
                 on_json: Callable[[dict], None],
                 on_closed: Callable[[str], None],
                 rate: int = 48000) -> None:
        self._adb = adb_binary
        self._jar = jar_host_path
        self._on_pcm = on_pcm
        self._on_json = on_json
        self._on_closed = on_closed
        self._rate = rate
        self._proc: subprocess.Popen | None = None
        self._closing = threading.Event()
        self.hello: dict = {}

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None \
            and not self._closing.is_set()

    def connect(self, timeout: float = 5.0) -> dict:
        self._closing.clear()
        cmd = [self._adb, "exec-out",
               "app_process", f"-Djava.class.path={DEVICE_JAR}",
               "/system/bin", "com.aum.poc.MicStream", str(self._rate)]
        try:
            self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                          stderr=subprocess.DEVNULL,
                                          bufsize=0)
        except OSError as e:
            raise ExecOutError(f"adb exec-out failed to start: {e}") from e

        parser = FrameParser()
        import time as _t
        deadline = _t.monotonic() + timeout
        while _t.monotonic() < deadline:
            data = self._read_some()
            if not data:
                if self._proc.poll() is not None:
                    self.close()
                    raise ExecOutError(
                        "Phone capture process exited immediately "
                        "(mic permission or busy microphone)")
                continue
            for frame in parser.feed(data):
                if frame.type == FrameType.JSON:
                    try:
                        obj = frame.json
                    except Exception:
                        continue
                    if obj.get("event") == "hello":
                        self.hello = obj
                        threading.Thread(target=self._read_loop,
                                         name="aum-execout-reader",
                                         daemon=True).start()
                        return obj
                self._dispatch(frame)
        self.close()
        raise ExecOutError("No handshake from phone capture process")

    def _read_some(self) -> bytes:
        # os.read returns whatever is available on the pipe without waiting
        # for the full buffer (unlike BufferedReader.read)
        try:
            import os
            return os.read(self._proc.stdout.fileno(), 65536)
        except (OSError, ValueError):
            return b""

    def _read_loop(self) -> None:
        parser = FrameParser()
        proc = self._proc
        try:
            while not self._closing.is_set() and proc.poll() is None:
                data = self._read_some()
                if not data:
                    break
                for frame in parser.feed(data):
                    self._dispatch(frame)
        except (OSError, ValueError):
            pass
        finally:
            client_initiated = self._closing.is_set()
            self.close()
            if not client_initiated:
                self._on_closed("Audio stream disconnected (exec-out)")

    def _dispatch(self, frame) -> None:
        if frame.type == FrameType.PCM:
            self._on_pcm(frame.payload)
        elif frame.type in (FrameType.JSON, FrameType.PHONE_EVENT):
            try:
                self._on_json(frame.json)
            except Exception:
                pass

    def close(self) -> None:
        self._closing.set()
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass
