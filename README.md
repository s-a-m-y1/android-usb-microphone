# Android USB Microphone

Turn an Android phone into a real microphone for Ubuntu over **USB only** —
no Wi-Fi, no Bluetooth, no internet, no root.

```
Android AudioRecord (48 kHz · PCM 16-bit · mono)
        ↓  USB cable (adb transport — abstract unix socket / exec-out)
Ubuntu desktop app
        ↓  ring buffer (jitter absorption)
PipeWire virtual source: "Android USB Microphone"
        ↓
OBS · Discord · Firefox · Chrome · Audacity · any recorder
```

The resulting source is a genuine PipeWire node (`Audio/Source`): it shows up
in `pactl list sources short`, GNOME sound settings, and every app that can
select a microphone.

## Security model

- The desktop talks to the phone **only through the USB cable via adb**
  (`adb forward` to a local-abstract unix socket on the phone, or
  `adb exec-out`).
- No TCP listener on the LAN, no HTTP server, no `0.0.0.0` sockets. The
  desktop↔daemon channel is a `0600` unix socket under `$XDG_RUNTIME_DIR`.
- The Android app does not even hold the `INTERNET` permission, so the audio
  physically cannot leave the device except through USB.
- The PipeWire daemon creates a user-private virtual microphone; nothing is
  routed through the internet.

## Components

| Path                | What it is                                                    |
| ------------------- | ------------------------------------------------------------- |
| `android/`          | Kotlin Android app (foreground service + AudioRecord + UI)    |
| `desktop/pipewire/` | `aum-pw-source` C daemon: PipeWire virtual source + ring buffer |
| `desktop/src/aum/`  | Python/GTK4 desktop app: adb management, transport, engine, UI |
| `poc/`              | Phase-1 proof of concept + `aum-stream.jar` fallback capture  |
| `scripts/`          | install / uninstall / build / run helpers                     |
| `desktop/tests/`    | unit + integration tests                                      |

Two transports are supported, chosen automatically:

1. **Android app (preferred)** — the app (`com.aum.mic`) runs a foreground
   service, listens on the abstract socket `aum_mic`, and the desktop tunnels
   to it with `adb forward tcp:<port> localabstract:aum_mic`.
2. **Direct USB capture (no install)** — if the app is not installed, the
   desktop pushes `poc/aum-stream.jar` to the phone and runs it under
   `app_process` as shell uid (which holds `RECORD_AUDIO`). Works instantly
   on any device with USB debugging, no APK install required.

## Requirements

- Ubuntu (tested on 26.04.1 LTS) with PipeWire + wireplumber (default).
- `adb` — `sudo apt install adb`
- Android 8+ phone with USB debugging enabled (tested on Android 15).
- A **data** USB cable (charge-only cables will not work).

## Quick start

```bash
# 1. install desktop parts (asks sudo only for missing apt packages)
bash scripts/install.sh

# 2. plug in the phone, enable USB debugging, allow the computer
adb devices          # must show:  <serial>  device

# 3. start the app
aum-mic
# or from the repo:  scripts/run.sh

# 4. click "Start Microphone"
```

Then open OBS → Settings/Audio or any app → choose
**“Android USB Microphone”** as the input device.

If the Android app is not installed, the desktop app automatically uses the
direct USB capture mode (no phone-side setup beyond USB debugging).

### Installing the Android app

```bash
bash scripts/setup-android-sdk.sh   # ~1 GB under .sdk/, no root needed
bash scripts/build-apk.sh           # -> build/AndroidUsbMic.apk
adb install -r build/AndroidUsbMic.apk
adb shell pm grant com.aum.mic android.permission.RECORD_AUDIO   # optional
```

> Some OEMs (realme/Oppo/Xiaomi) show an on-phone confirmation for
> “install via USB”. Approve it, or enable **Developer options →
> Install via USB** first. See `docs/usb-debugging.md`.

Press **Start** in the phone app; the desktop app then connects
automatically (auto-reconnect handles cable unplug/replug).

## Verifying it works

```bash
pactl list sources short | grep android_usb_mic
# 123  android_usb_mic  PipeWire  s16le 1ch 48000Hz  IDLE

# record 5 s from the virtual microphone exactly like OBS would:
pw-record --target android_usb_mic /tmp/check.wav
```

Phase-1-style capture without PipeWire:

```bash
scripts/run.sh --record 10 --out test.wav
```

## Development

```bash
bash scripts/build-desktop.sh      # build the C daemon + C test binary
bash scripts/build-apk.sh          # build the APK (after setup-android-sdk.sh)
bash scripts/build-all.sh          # both
PYTHONPATH=desktop/src python3 -m unittest discover -s desktop/tests -v
```

See `docs/` for USB debugging setup, PipeWire notes, troubleshooting, and the
manual test procedure.

## Architecture notes

- **Ring buffer**: lock-free SPSC (`desktop/pipewire/ringbuf.h`), sized via
  Settings → “Ring buffer size (ms)” (default 120 ms). USB jitter is absorbed
  in the ring; underruns produce silence, never clicks.
- **Realtime safety**: the PipeWire process callback only locks-free-reads
  the ring; socket I/O lives on separate threads; the GUI only renders
  snapshots (never blocks).
- **Latency**: node latency 512/48000 ≈ 10.7 ms + ring buffer depth.
- **Reconnect**: engine retries every 1.5 s while “Start” is active; adb
  device hot-plug is watched every 2 s.

## License

MIT — see `LICENSE`.
