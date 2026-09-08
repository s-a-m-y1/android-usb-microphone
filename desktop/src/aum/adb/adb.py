"""ADB wrapper: detection, device listing, TCP/abstract-socket forwarding.

All phone communication rides on `adb forward`, which tunnels a host TCP port
to an abstract-namespace unix socket on the phone *through the USB cable*.
No Wi-Fi, no Bluetooth, no phone IP address involved.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

log = logging.getLogger(__name__)


class AdbError(Exception):
    """Raised when adb is missing or a command fails."""


class DeviceState(str, Enum):
    AUTHORIZED = "device"        # ready to use
    UNAUTHORIZED = "unauthorized"
    OFFLINE = "offline"
    RECOVERY = "recovery"
    SIDELOAD = "sideload"


@dataclass(frozen=True)
class AdbDevice:
    serial: str
    state: DeviceState
    model: str = ""
    product: str = ""

    @property
    def usable(self) -> bool:
        return self.state == DeviceState.AUTHORIZED

    @property
    def display_name(self) -> str:
        base = self.model or self.product or "Android Device"
        if self.state != DeviceState.AUTHORIZED:
            base += f" ({self.state.value})"
        return base


def _parse_devices(output: str) -> list[AdbDevice]:
    """Parse `adb devices -l` output into device records (unit-tested)."""
    devices: list[AdbDevice] = []
    for line in output.splitlines()[1:]:
        line = line.strip()
        if not line or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state_str = parts[0], parts[1]
        try:
            state = DeviceState(state_str)
        except ValueError:
            log.warning("unknown adb device state %r for %s", state_str, serial)
            continue
        model = product = ""
        for extra in parts[2:]:
            if extra.startswith("model:"):
                model = extra.split(":", 1)[1]
            elif extra.startswith("product:"):
                product = extra.split(":", 1)[1]
        devices.append(AdbDevice(serial, state, model, product))
    return devices


class Adb:
    """Thin, typed wrapper around the adb CLI."""

    def __init__(self, binary: str | None = None, timeout: float = 5.0) -> None:
        self.binary = binary or shutil.which("adb") or "adb"
        self.timeout = timeout

    # ------------------------------------------------------------- basics
    def exists(self) -> bool:
        return shutil.which(self.binary) is not None

    def _run(self, *args: str, check: bool = True) -> str:
        try:
            proc = subprocess.run(
                [self.binary, *args],
                capture_output=True, text=True, timeout=self.timeout,
            )
        except FileNotFoundError as e:
            raise AdbError("ADB not found. Install it with: sudo apt install adb") from e
        except subprocess.TimeoutExpired as e:
            raise AdbError(f"adb {args[0]} timed out") from e
        if check and proc.returncode != 0:
            raise AdbError(f"adb {' '.join(args)} failed: {proc.stderr.strip()}")
        return proc.stdout

    def version(self) -> str:
        out = self._run("version")
        return out.splitlines()[0].strip() if out else ""

    def devices(self) -> list[AdbDevice]:
        out = self._run("devices", "-l")
        return _parse_devices(out)

    def first_usable(self) -> AdbDevice | None:
        for d in self.devices():
            if d.usable:
                return d
        return None

    # ---------------------------------------------------------- transport
    def forward(self, host_port: int, device_socket: str, serial: str) -> None:
        """Tunnel host TCP `host_port` to the phone's abstract unix socket."""
        self._run("-s", serial, "forward",
                  f"tcp:{host_port}", f"localabstract:{device_socket}")

    def forward_auto(self, device_socket: str, serial: str,
                     start_port: int = 48157) -> int:
        """Find a free host port, set the forward, return the port."""
        import socket as _s
        for port in range(start_port, start_port + 64):
            with _s.socket() as probe:
                try:
                    probe.bind(("127.0.0.1", port))
                except OSError:
                    continue
            try:
                self.forward(port, device_socket, serial)
                return port
            except AdbError:
                continue
        raise AdbError("No free TCP port for adb forward (tried 64 ports)")

    def unforward(self, host_port: int, serial: str) -> None:
        try:
            self._run("-s", serial, "forward", "--remove", f"tcp:{host_port}",
                      check=False)
        except AdbError:
            pass

    def shell(self, serial: str, *cmd: str, timeout: float = 10.0) -> str:
        return self._run("-s", serial, "shell", *cmd, check=False)

    def launch_app(self, serial: str, autostart: bool = False) -> None:
        """Bring the phone app to the foreground (optionally auto-starting)."""
        extra = f" --es autostart {'true' if autostart else 'false'}"
        self.shell(serial, "am", "start", "-n",
                   "com.aum.mic/.MainActivity" + extra)

    def grant_mic(self, serial: str) -> bool:
        """Pre-grant microphone permission (dev convenience; the app itself
        also requests it at runtime)."""
        out = self.shell(serial, "pm", "grant", "com.aum.mic",
                         "android.permission.RECORD_AUDIO")
        return "Exception" not in out and "Error" not in out

    def stop_app(self, serial: str) -> None:
        self.shell(serial, "am", "force-stop", "com.aum.mic")


def device_list_summary(devices: list[AdbDevice]) -> str:
    if not devices:
        return "No Android device detected"
    return ", ".join(d.display_name for d in devices)


__all__ = ["Adb", "AdbDevice", "AdbError", "DeviceState", "_parse_devices"]
