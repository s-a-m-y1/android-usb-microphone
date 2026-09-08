"""Lifecycle manager for the aum-pw-source PipeWire daemon subprocess."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)


class DaemonError(Exception):
    pass


def default_socket_path() -> str:
    xrd = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
    return f"{xrd}/aum-mic.sock"


def find_daemon_binary() -> Path | None:
    """Locate the compiled daemon: install dir first, then the build tree."""
    candidates = [
        Path.home() / ".local" / "libexec" / "aum-pw-source",
        Path(__file__).resolve().parents[4] / "build" / "aum-pw-source",
    ]
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return c
    found = shutil.which("aum-pw-source")
    return Path(found) if found else None


class PwDaemon:
    """Runs one aum-pw-source process; idempotent start/stop."""

    def __init__(self, binary: Path | None = None) -> None:
        self.binary = binary or find_daemon_binary()
        self.proc: subprocess.Popen | None = None
        self.adopted_pid: int | None = None
        self.log_path = Path.home() / ".cache" / "aum" / "aum-pw-source.log"

    @property
    def running(self) -> bool:
        if self.proc is not None and self.proc.poll() is None:
            return True
        if self.adopted_pid is not None:
            try:
                os.kill(self.adopted_pid, 0)
                return True
            except (ProcessLookupError, PermissionError):
                self.adopted_pid = None
        return False

    @staticmethod
    def _read_pid(pid_path: str) -> int | None:
        try:
            return int(Path(pid_path).read_text().strip())
        except (OSError, ValueError):
            return None

    def start(self, name: str = "android_usb_mic",
              description: str = "Android USB Microphone",
              rate: int = 48000, channels: int = 1,
              buffer_ms: int = 120, latency: int = 512,
              socket_path: str | None = None) -> None:
        if self.running:
            return
        sock = socket_path or default_socket_path()
        if self.binary is None:
            raise DaemonError(
                "aum-pw-source is not built. Run scripts/build-desktop.sh "
                "(needs libpipewire-0.3-dev).")

        # Adopt an existing daemon (e.g. started by another app instance).
        if self._socket_exists(sock):
            self.adopted_pid = self._read_pid(sock + ".pid")
            log.info("adopting running aum-pw-source (pid=%s)",
                     self.adopted_pid)
            return

        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            str(self.binary),
            "--socket", sock,
            "--name", name,
            "--description", description,
            "--rate", str(rate),
            "--channels", str(channels),
            "--latency", str(latency),
            "--ring-ms", str(buffer_ms),
        ]
        log.info("starting daemon: %s", " ".join(cmd))
        self._logf = self.log_path.open("ab")
        self.proc = subprocess.Popen(cmd, stdout=self._logf, stderr=self._logf,
                                     start_new_session=True)
        # wait for the source to appear
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise DaemonError(
                    f"daemon exited with code {self.proc.returncode}; "
                    f"see {self.log_path}")
            time.sleep(0.1)
            if self._socket_exists(sock):
                return
        raise DaemonError("daemon did not create its control socket in time")

    @staticmethod
    def _socket_exists(path: str) -> bool:
        import socket as _s
        s = _s.socket(_s.AF_UNIX, _s.SOCK_STREAM)
        try:
            s.connect(path)
            return True
        except OSError:
            return False
        finally:
            s.close()

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        elif self.adopted_pid is not None:
            try:
                os.kill(self.adopted_pid, 15)
            except (ProcessLookupError, PermissionError):
                pass
        self.adopted_pid = None
        self.proc = None
        if hasattr(self, "_logf") and self._logf:
            self._logf.close()
            self._logf = None
