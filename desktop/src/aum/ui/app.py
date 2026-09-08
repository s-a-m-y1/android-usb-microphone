"""GTK4 / libadwaita GUI for Android USB Microphone.

All audio/adb work happens in the Engine's threads; this module only renders
EngineStatus snapshots pushed on the GTK main loop.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gtk, GLib  # noqa: E402

from ..core.engine import AppState, Engine, EngineStatus  # noqa: E402
from ..core.settings import Settings  # noqa: E402

STATE_LABELS = {
    AppState.NO_ADB: "ADB not found",
    AppState.NO_DEVICE: "Disconnected",
    AppState.UNAUTHORIZED: "Waiting for authorization",
    AppState.READY: "Ready",
    AppState.CONNECTING: "Connecting…",
    AppState.STREAMING: "Streaming",
    AppState.ERROR: "Error",
}


def _css() -> None:
    css = b"""
    .dot { border-radius: 9999px; min-width: 12px; min-height: 12px; }
    .dot-green { background: #2ec27e; }
    .dot-yellow { background: #f5c211; }
    .dot-red { background: #e01b24; }
    .dot-off { background: #77767b; }
    .level { min-height: 14px; }
    """
    provider = Gtk.CssProvider()
    provider.load_from_data(css)
    display = Gdk.Display.get_default()
    if display is not None:
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


class SettingsDialog(Adw.PreferencesWindow):
    def __init__(self, parent: Adw.ApplicationWindow, settings: Settings,
                 on_saved) -> None:
        super().__init__(transient_for=parent, modal=True)
        self.set_title("Settings")
        self._settings = settings
        self._on_saved = on_saved

        page = Adw.PreferencesPage()
        group_audio = Adw.PreferencesGroup(title="Audio")
        page.add(group_audio)

        self._rate = Adw.ComboRow(title="Sample rate")
        rates = ["16000 Hz", "24000 Hz", "32000 Hz", "44100 Hz", "48000 Hz"]
        for r in rates:
            self._rate.model.append(r)
        self._rate.selected = rates.index(f"{settings.sample_rate} Hz") \
            if f"{settings.sample_rate} Hz" in rates else 4
        group_audio.add(self._rate)

        self._buffer = Adw.SpinRow.new_with_range(20, 2000, 10)
        self._buffer.set_title("Ring buffer size (ms)")
        self._buffer.set_value(settings.buffer_ms)
        group_audio.add(self._buffer)

        self._name = Adw.EntryRow(title="Audio device name")
        self._name.set_text(settings.device_name)
        group_audio.add(self._name)

        group_behaviour = Adw.PreferencesGroup(title="Behaviour")
        page.add(group_behaviour)

        self._reconnect = Adw.SwitchRow(title="Auto reconnect",
            subtitle="Reconnect automatically after USB disconnects")
        self._reconnect.set_active(settings.auto_reconnect)
        group_behaviour.add(self._reconnect)

        self._minimized = Adw.SwitchRow(title="Start minimized",
            subtitle="Do not show the window at startup")
        self._minimized.set_active(settings.start_minimized)
        group_behaviour.add(self._minimized)

        self._startup = Adw.SwitchRow(title="Launch on startup",
            subtitle="Start the app when you log in")
        self._startup.set_active(settings.launch_on_startup)
        group_behaviour.add(self._startup)

        self.add(page)

    def save(self) -> None:
        rates = [16000, 24000, 32000, 44100, 48000]
        self._settings.sample_rate = rates[self._rate.get_selected()]
        self._settings.buffer_ms = int(self._buffer.get_value())
        self._settings.device_name = self._name.get_text().strip() or \
            "Android USB Microphone"
        self._settings.auto_reconnect = self._reconnect.get_active()
        self._settings.start_minimized = self._minimized.get_active()
        self._settings.launch_on_startup = self._startup.get_active()
        self._settings.validate()
        self._settings.save()
        self._settings.apply_launch_on_startup(self._settings.launch_on_startup)
        self._on_saved()


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application) -> None:
        super().__init__(application=app)
        self.set_title("Android USB Microphone")
        self.set_default_size(460, 620)
        _css()

        self.settings = Settings.load()
        self.engine = Engine(self.settings, self._on_engine_status)
        self.engine.start_background()

        self._build_ui()
        self.connect("close-request", self._on_close)

        if self.settings.start_minimized:
            self.set_hide_on_close(True)

    # ------------------------------------------------------------- widgets
    def _build_ui(self) -> None:
        content = Adw.ToolbarView()
        header = Adw.HeaderBar()
        menu = Gtk.MenuButton(icon_name="open-menu-symbolic")
        popover = Gtk.PopoverMenu()
        settings_action = Gtk.Button(label="Settings")
        settings_action.connect("clicked", self._on_settings_clicked)
        settings_action.add_css_class("flat")
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.append(settings_action)
        menu.set_popover(popover)
        header.pack_end(menu)
        content.add_top_bar(header)

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                       margin_top=12, margin_bottom=24,
                       margin_start=18, margin_end=18)
        content.set_content(page)
        self._banner = Adw.Banner(button_label="")
        page.append(self._banner)

        # --- device card
        dev_group = Adw.PreferencesGroup(title="Device")
        self._device_row = Adw.ActionRow(title="No Android device detected",
                                         subtitle="Connect the phone via USB")
        icon = Gtk.Image.new_from_icon_name("phone-symbolic")
        self._device_row.add_prefix(icon)
        dev_group.add(self._device_row)
        page.append(dev_group)

        # --- USB status
        usb_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._dot = Gtk.Label()
        self._dot.add_css_class("dot")
        self._dot.add_css_class("dot-off")
        usb_box.append(self._dot)
        self._usb_label = Gtk.Label(label="USB: checking…",
                                    halign=Gtk.Align.START,
                                    hexpand=True)
        usb_box.append(self._usb_label)
        page.append(usb_box)

        # --- mic level
        mic_group = Adw.PreferencesGroup(title="Microphone")
        self._level_bar = Gtk.ProgressBar()
        self._level_bar.add_css_class("level")
        self._level_bar.set_fraction(0.0)
        mic_group.add(self._level_bar)
        self._format_label = Gtk.Label(label="Sample Rate: 48000 Hz  ·  "
                                             "Format: PCM 16-bit Mono",
                                       halign=Gtk.Align.START,
                                       margin_top=6)
        mic_group.add(self._format_label)
        page.append(mic_group)

        # --- buttons
        self._start_btn = Gtk.Button(label="Start Microphone")
        self._start_btn.add_css_class("suggested-action")
        self._start_btn.add_css_class("pill")
        self._start_btn.connect("clicked", self._on_start)
        page.append(self._start_btn)

        self._stop_btn = Gtk.Button(label="Stop")
        self._stop_btn.add_css_class("pill")
        self._stop_btn.set_sensitive(False)
        self._stop_btn.connect("clicked", self._on_stop)
        page.append(self._stop_btn)

        # --- virtual device
        vd_group = Adw.PreferencesGroup(title="Virtual Device")
        self._virtual_row = Adw.ActionRow(
            title=self.settings.device_name,
            subtitle="Status: checking PipeWire…")
        vd_group.add(self._virtual_row)
        page.append(vd_group)

        self.set_content(content)

    # ------------------------------------------------------------ handlers
    def _on_start(self, _btn: Gtk.Button) -> None:
        self.engine.ensure_daemon()
        self.engine.start_streaming()

    def _on_stop(self, _btn: Gtk.Button) -> None:
        self.engine.stop_streaming()

    def _on_settings_clicked(self, _btn: Gtk.Button) -> None:
        dlg = SettingsDialog(self, self.settings, self._on_settings_saved)
        dlg.present()

    def _on_settings_saved(self) -> None:
        # restart the daemon with the new parameters
        self.engine.shutdown()
        self.engine = Engine(self.settings, self._on_engine_status)
        self.engine.start_background()
        self.engine.ensure_daemon()
        self._virtual_row.set_title(self.settings.device_name)
        self._set_status(self.engine.status)

    def _on_close(self, _win: Gtk.ApplicationWindow) -> bool:
        self.engine.shutdown()
        return False  # allow close

    # -------------------------------------------------------------- render
    def _on_engine_status(self, st: EngineStatus) -> None:
        GLib.idle_add(self._set_status, st)

    def _set_status(self, st: EngineStatus) -> None:
        if not hasattr(self, "_dot"):   # engine may fire before widgets exist
            return
        color = {"green": "dot-green", "yellow": "dot-yellow",
                 "red": "dot-red", "off": "dot-off"}[
            {"green": "green", "yellow": "yellow", "red": "red"}.get(
                _state_color(st.state), "off")]
        self._dot.remove_css_class("dot-green")
        self._dot.remove_css_class("dot-yellow")
        self._dot.remove_css_class("dot-red")
        self._dot.remove_css_class("dot-off")
        self._dot.add_css_class(color)

        streaming = st.state == AppState.STREAMING
        connecting = st.state in (AppState.CONNECTING,)
        if streaming:
            usb = "USB connection: Connected  ·  Transport: ADB / USB"
        elif connecting:
            usb = "USB connection: Connecting…"
        else:
            usb = "USB connection: Disconnected"
        self._usb_label.set_text(usb)

        dev = st.device
        if dev is not None:
            mark = "✓" if dev.usable else "…"
            self._device_row.set_title(f"{mark} {dev.display_name}")
            self._device_row.set_subtitle(f"Serial {dev.serial}")
        else:
            self._device_row.set_title("No Android device detected")
            self._device_row.set_subtitle("Connect the phone via USB")

        self._level_bar.set_fraction(st.level)
        ch_name = "Mono" if st.channels == 1 else "Stereo"
        self._format_label.set_text(
            f"Sample Rate: {st.rate} Hz  ·  Format: PCM 16-bit {ch_name}")

        self._start_btn.set_sensitive(
            st.state in (AppState.READY, AppState.NO_DEVICE) and not streaming)
        self._stop_btn.set_sensitive(streaming or connecting)

        self._virtual_row.set_title(self.settings.device_name)
        if not st.daemon_running:
            vsub = "Status: Offline (PipeWire source not running)"
        elif not st.source_ready:
            vsub = "Status: Starting…"
        elif streaming:
            vsub = "Status: Streaming · visible in OBS, browsers, Discord"
        else:
            vsub = "Status: Ready"
        self._virtual_row.set_subtitle(vsub)

        msg = st.message
        if st.state == AppState.UNAUTHORIZED:
            msg = ("USB debugging is not authorized. Unlock the phone and "
                   "tap 'Allow' on the USB debugging prompt.")
        elif st.state == AppState.NO_ADB:
            msg = ("ADB not found. Install it with: sudo apt install adb")
        elif st.state == AppState.NO_DEVICE:
            msg = ("No Android device detected. Connect the phone with a "
                   "data USB cable and enable USB debugging.")
        self._banner.set_title(msg if msg else "")
        self._banner.set_revealed(bool(msg))
        return False


def _state_color(state: AppState) -> str:
    return {
        AppState.NO_ADB: "red",
        AppState.NO_DEVICE: "red",
        AppState.UNAUTHORIZED: "yellow",
        AppState.READY: "green",
        AppState.CONNECTING: "yellow",
        AppState.STREAMING: "green",
        AppState.ERROR: "red",
    }[state]


class AumApp(Adw.Application):
    def __init__(self, autostart_stream: bool = False,
                 screenshot_path: str | None = None) -> None:
        super().__init__(application_id="io.github.aum.Desktop")
        self.win: MainWindow | None = None
        self.autostart_stream = autostart_stream
        self.screenshot_path = screenshot_path

    def do_activate(self) -> None:
        Adw.StyleManager.get_default().set_color_scheme(
            Adw.ColorScheme.PREFER_DARK)
        if self.win is None:
            self.win = MainWindow(self)
            if self.autostart_stream:
                try:
                    self.win.engine.ensure_daemon()
                    self.win.engine.start_streaming()
                except Exception:
                    pass
        self.win.present()
        if self.screenshot_path:
            GLib.timeout_add_seconds(6, self._save_screenshot)
        return False

    def _save_screenshot(self) -> bool:
        try:
            win = self.win
            snap = Gtk.Snapshot()
            Gtk.Widget.do_snapshot(win, snap)
            paintable = snap.to_paintable(None)
            texture = paintable.get_current_image()
            texture.save_to_png(self.screenshot_path)
            print(f"[screenshot] saved {self.screenshot_path}", flush=True)
        except Exception as e:
            print(f"[screenshot] failed: {e}", flush=True)
        self.quit()
        return False

    def do_shutdown(self) -> None:
        if self.win is not None:
            self.win.engine.shutdown()
        Adw.Application.do_shutdown(self)
