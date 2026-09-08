"""User configuration (JSON at ~/.config/aum/config.json)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "aum"
CONFIG_FILE = CONFIG_DIR / "config.json"


@dataclass
class Settings:
    sample_rate: int = 48000
    buffer_ms: int = 120            # ring buffer duration in the daemon
    device_name: str = "Android USB Microphone"
    auto_reconnect: bool = True
    start_minimized: bool = False
    launch_on_startup: bool = False

    def validate(self) -> None:
        if self.sample_rate not in (16000, 24000, 32000, 44100, 48000):
            raise ValueError(f"unsupported sample rate {self.sample_rate}")
        if not (20 <= self.buffer_ms <= 2000):
            raise ValueError(f"buffer {self.buffer_ms}ms outside 20..2000")
        if not self.device_name.strip():
            raise ValueError("device name must not be empty")

    @classmethod
    def load(cls) -> "Settings":
        s = cls()
        try:
            raw = json.loads(CONFIG_FILE.read_text())
            known = {k: v for k, v in raw.items() if k in s.__dataclass_fields__}
            s = cls(**known)
        except (FileNotFoundError, json.JSONDecodeError, TypeError):
            pass
        return s

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(asdict(self), indent=2))

    @staticmethod
    def startup_desktop_file() -> Path:
        return Path.home() / ".config" / "autostart" / "aum-desktop.desktop"

    def apply_launch_on_startup(self, enabled: bool) -> None:
        """Install/remove an XDG autostart entry."""
        target = self.startup_desktop_file()
        target.parent.mkdir(parents=True, exist_ok=True)
        if enabled:
            exec_path = Path.home() / ".local" / "bin" / "aum-mic"
            target.write_text(
                "[Desktop Entry]\n"
                "Type=Application\n"
                "Name=Android USB Microphone\n"
                f"Exec={exec_path} --start-minimized\n"
                "X-GNOME-Autostart-enabled=true\n"
            )
        elif target.exists():
            target.unlink()
