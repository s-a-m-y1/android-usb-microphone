"""Engine: orchestrates adb, the phone link, the daemon bridge, and state.

State machine:

    NO_ADB ──┐
    NO_DEVICE ── unauthorized ──> CONNECTING ──> STREAMING
                        ▲            │  ▲            │
                        └── retry ───┘  └─ reconnect ┘
                                     ▼
                                  ERROR

The GUI subscribes via `on_state`; all heavy work runs on dedicated threads
(adb watcher, phone reader, daemon event reader). The GUI thread only renders.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Callable

from ..adb.adb import Adb, AdbDevice, AdbError, DeviceState
from ..audio.wav import LevelMeter, WavWriter
from ..transport.phone_client import PhoneClient, PhoneLinkError
from ..transport.pw_bridge import PwBridge, DaemonBridgeError
from .daemon import PwDaemon, DaemonError, default_socket_path
from .settings import Settings

log = logging.getLogger(__name__)


class AppState(Enum):
    NO_ADB = auto()          # adb binary missing
    NO_DEVICE = auto()       # no android device on usb
    UNAUTHORIZED = auto()    # device present but usb debugging not authorized
    READY = auto()           # daemon up, device present, not streaming
    CONNECTING = auto()      # trying to reach the phone app
    STREAMING = auto()       # receiving PCM
    ERROR = auto()


STATE_COLORS = {
    AppState.NO_ADB: "red",
    AppState.NO_DEVICE: "red",
    AppState.UNAUTHORIZED: "yellow",
    AppState.READY: "green",
    AppState.CONNECTING: "yellow",
    AppState.STREAMING: "green",
    AppState.ERROR: "red",
}


@dataclass
class EngineStatus:
    state: AppState = AppState.NO_DEVICE
    message: str = ""
    device: AdbDevice | None = None
    level: float = 0.0
    rate: int = 48000
    channels: int = 1
    format: str = "PCM 16-bit Mono"
    underruns: int = 0
    bytes_received: int = 0
    forward_port: int | None = None
    transport: str = ""
    daemon_running: bool = False
    source_ready: bool = False


@dataclass
class _Intent:
    """What the engine should be doing (set from GUI thread)."""
    streaming: bool = False
    generation: int = 0           # bumped on every start/stop to cancel loops


class Engine:
    def __init__(self, settings: Settings,
                 on_status: Callable[[EngineStatus], None]) -> None:
        self.settings = settings
        self.on_status = on_status
        self.adb = Adb()
        self.daemon = PwDaemon()
        self.meter = LevelMeter()
        self.status = EngineStatus(rate=settings.sample_rate)
        self._intent = _Intent()
        self._lock = threading.RLock()
        self._client: PhoneClient | None = None
        self._bridge: PwBridge | None = None
        self._watcher: threading.Thread | None = None
        self._wav: WavWriter | None = None
        self._stop_event = threading.Event()
        self._level_emit_at = 0.0

    # ------------------------------------------------------------ plumbing
    def _emit(self, **kw) -> None:
        with self._lock:
            for k, v in kw.items():
                setattr(self.status, k, v)
            snapshot = EngineStatus(**vars(self.status).copy())
        try:
            self.on_status(snapshot)
        except Exception:  # UI callback must never kill the engine
            log.exception("on_status callback failed")

    # ------------------------------------------------------ daemon control
    def ensure_daemon(self) -> None:
        if self.daemon.running and self.status.source_ready:
            return
        try:
            self.daemon.start(description=self.settings.device_name,
                              rate=self.settings.sample_rate,
                              buffer_ms=self.settings.buffer_ms)
            self._emit(daemon_running=True)
        except DaemonError as e:
            self._emit(state=AppState.ERROR, message=str(e),
                       daemon_running=False)
            raise

    def start_background(self) -> None:
        """Start the adb watcher; call once at app startup."""
        self._stop_event.clear()
        self._watcher = threading.Thread(target=self._watch_loop,
                                         name="aum-watch", daemon=True)
        self._watcher.start()

    def shutdown(self) -> None:
        self._stop_event.set()
        self._stop_streaming_locked(cancel_generation=True)
        self.daemon.stop()
        self._emit(daemon_running=False, source_ready=False)

    # ----------------------------------------------------- streaming intent
    def start_streaming(self) -> None:
        with self._lock:
            self._intent.streaming = True
            self._intent.generation += 1
        threading.Thread(target=self._connect_loop, name="aum-connect",
                         daemon=True).start()

    def stop_streaming(self) -> None:
        with self._lock:
            self._intent.streaming = False
            self._intent.generation += 1
            self._stop_streaming_locked()

    def _stop_streaming_locked(self, cancel_generation: bool = False) -> None:
        if cancel_generation:
            self._intent.streaming = False
            self._intent.generation += 1
        client, self._client = self._client, None
        if client:
            client.close()
        port = self.status.forward_port
        if port is not None and self.status.device is not None:
            self.adb.unforward(port, self.status.device.serial)
            self._emit(forward_port=None)
        if self._wav:
            self._wav.close()
            self._wav = None

    # ----------------------------------------------------------- watch loop
    def _watch_loop(self) -> None:
        last_serial: str | None = None
        while not self._stop_event.is_set():
            try:
                if not self.adb.exists():
                    self._emit(state=AppState.NO_ADB,
                               message="ADB not found",
                               device=None, daemon_running=self.daemon.running)
                else:
                    devices = self.adb.devices()
                    auth = [d for d in devices if d.usable]
                    unauth = [d for d in devices
                              if d.state == DeviceState.UNAUTHORIZED]
                    if auth:
                        dev = auth[0]
                        if dev.serial != last_serial:
                            log.info("device detected: %s", dev)
                            last_serial = dev.serial
                            if self._intent.streaming:
                                # (re)connect against the new device
                                self.start_streaming()
                        self._emit(state=AppState.READY if not self._intent.streaming
                                   else self.status.state,
                                   device=dev, message="",
                                   daemon_running=self.daemon.running)
                    elif unauth:
                        self._emit(
                            state=AppState.UNAUTHORIZED,
                            device=unauth[0],
                            message="USB debugging is not authorized. "
                                    "Unlock the phone and allow this computer.",
                            daemon_running=self.daemon.running)
                    else:
                        last_serial = None
                        msg = ("No Android device detected. "
                               "Connect the phone with a USB cable "
                               "(not charge-only).")
                        self._emit(state=AppState.NO_DEVICE, device=None,
                                   message=msg,
                                   daemon_running=self.daemon.running)
            except AdbError as e:
                self._emit(state=AppState.ERROR, message=str(e),
                           daemon_running=self.daemon.running)
            self._stop_event.wait(2.0)

    # -------------------------------------------------------- connect loop
    def _connect_loop(self) -> None:
        gen = self._intent.generation
        try:
            self.ensure_daemon()
        except DaemonError:
            return
        if self.status.state not in (AppState.READY, AppState.STREAMING,
                                     AppState.CONNECTING, AppState.ERROR):
            # watcher will re-evaluate; bail to avoid clobbering states
            self._emit(state=AppState.READY)
        while (self._intent.streaming and self._intent.generation == gen
               and not self._stop_event.is_set()):
            self._emit(state=AppState.CONNECTING, message="Connecting to phone…")
            try:
                self._connect_once()
                # connected: reader runs; wait until it drops
                while (self._client and self._client.alive
                       and self._intent.streaming
                       and self._intent.generation == gen):
                    time.sleep(0.2)
                if not self._intent.streaming or self._intent.generation != gen:
                    break
                self._emit(state=AppState.READY,
                           message="Audio stream disconnected. Reconnecting…")
            except (PhoneLinkError, DaemonBridgeError, AdbError) as e:
                self._emit(state=AppState.CONNECTING, message=str(e))
            if not self.settings.auto_reconnect:
                with self._lock:
                    self._intent.streaming = False
                self._emit(state=AppState.READY,
                           message="Disconnected (auto reconnect off)")
                break
            self._stop_event.wait(1.5)
        if self._intent.generation == gen and not self._intent.streaming:
            self._emit(state=AppState.READY, message="Stopped",
                       level=0.0)

    def _connect_once(self) -> None:
        dev = self.status.device
        if dev is None:
            raise AdbError("No Android device detected")
        if not self._bridge or not self._bridge.connected:
            bridge = PwBridge(default_socket_path(),
                              on_event=self._on_daemon_event)
            bridge.connect()
            self._bridge = bridge

        if self._app_installed(dev.serial):
            port = self.adb.forward_auto("aum_mic", dev.serial)
            self._emit(forward_port=port,
                       transport="Android app · adb forward")
            client = PhoneClient(
                "127.0.0.1", port,
                on_pcm=self._on_pcm,
                on_json=self._on_phone_json,
                on_closed=self._on_phone_closed,
            )
        else:
            jar = self._ensure_stream_jar(dev.serial)
            self._emit(forward_port=None,
                       transport="Direct USB capture (adb exec-out)")
            from ..transport.execout import ExecOutClient
            client = ExecOutClient(
                self.adb.binary, jar,
                on_pcm=self._on_pcm,
                on_json=self._on_phone_json,
                on_closed=self._on_phone_closed,
                rate=self.settings.sample_rate,
            )
        hello = client.connect()
        self._client = client
        rate = hello.get("rate", self.settings.sample_rate)
        ch = hello.get("channels", 1)
        self._emit(state=AppState.STREAMING, message="Streaming from phone",
                   rate=rate, channels=ch,
                   format=f"PCM 16-bit {'Mono' if ch == 1 else 'Stereo'}",
                   underruns=0, bytes_received=0)
        log.info("streaming: rate=%s ch=%s", rate, ch)

    def _app_installed(self, serial: str) -> bool:
        out = self.adb.shell(serial, "pm", "path", "com.aum.mic")
        return "package:" in out

    def _ensure_stream_jar(self, serial: str) -> str:
        jar = Path(__file__).resolve().parents[4] / "poc" / "aum-stream.jar"
        if not jar.exists():
            raise AdbError(
                "Android app is not installed and the fallback capture jar "
                "is missing. Run scripts/build-desktop.sh.")
        self.adb._run("push", str(jar), "/data/local/tmp/aum-stream.jar")
        return str(jar)

    # -------------------------------------------------------------- events
    def _on_pcm(self, pcm: bytes) -> None:
        bridge = self._bridge
        if bridge is None:
            return
        try:
            bridge.send_pcm(pcm)
        except DaemonBridgeError as e:
            log.warning("daemon write failed: %s", e)
            self._emit(state=AppState.ERROR, message=str(e))
            return
        with self._lock:
            self.status.bytes_received += len(pcm)
        now = time.monotonic()
        if now >= self._level_emit_at:
            self._level_emit_at = now + 0.08  # ~12 fps meter
            self._emit(level=self.meter.level(pcm))
        if self._wav:
            self._wav.write(pcm)

    def _on_phone_json(self, obj: dict) -> None:
        ev = obj.get("event")
        if ev == "level":
            pass  # desktop computes its own level from PCM
        elif ev == "hello":
            log.info("phone hello: %s", obj)

    def _on_phone_closed(self, reason: str) -> None:
        self._emit(level=0.0, message=reason)

    def _on_daemon_event(self, obj: dict) -> None:
        ev = obj.get("event")
        if ev == "source-ready":
            self._emit(source_ready=True)
        elif ev == "stats":
            self._emit(underruns=obj.get("underruns", 0),
                       source_ready=True)
        elif ev == "error":
            self._emit(state=AppState.ERROR, message=obj.get("message", ""))

    # ------------------------------------------------------------- diag wav
    def start_wav_capture(self, path: str) -> None:
        """Record the incoming stream to a wav file (Phase-1 verification)."""
        with self._lock:
            if self._wav:
                self._wav.close()
            self._wav = WavWriter(path, rate=self.status.rate,
                                  channels=self.status.channels)

    def stop_wav_capture(self) -> float | None:
        with self._lock:
            if self._wav:
                dur = self._wav.seconds
                self._wav.close()
                self._wav = None
                return dur
        return None
