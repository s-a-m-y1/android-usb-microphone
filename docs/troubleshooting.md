# Troubleshooting

Each message shown by the desktop app, and what to do.

## "ADB not found"

```bash
sudo apt install adb
```

Then restart the desktop app.

## "No Android device detected"

1. Use a **data** USB cable (charge-only cables are the #1 cause).
2. Enable USB debugging (see `docs/usb-debugging.md`).
3. `adb devices` must list the phone as `device`.
4. Accept any "Allow USB debugging?" dialog on the phone.

## "USB debugging is not authorized"

The phone is attached but not trusted. Unlock the screen; a dialog
"Allow USB debugging?" is waiting — tap **Allow** (with "Always allow").
If it never appears: `adb kill-server && adb start-server`, replug.

## "Cannot reach the phone over USB" / "Phone never sent a handshake"

- The phone app must be **started** and **Start** pressed (when the Android
  app is installed).
- Without the app, the desktop pushes `poc/aum-stream.jar` automatically —
  make sure the repository is intact and `adb devices` shows `device`.
- Another app holding the microphone? Close other recorders on the phone.

## "Unable to create PipeWire audio source"

- Check PipeWire is running: `systemctl --user status pipewire wireplumber`.
- Install the development files and rebuild:
  `sudo apt install libpipewire-0.3-dev && bash scripts/build-desktop.sh`.
- Check `~/.cache/aum/aum-pw-source.log`.

## "Audio stream disconnected"

Transient USB error. With **Auto reconnect** enabled (default) the app
reconnects within a second or two. If it loops, replug the cable.

## "Android service stopped"

The phone app's foreground service was killed (very low memory, or battery
optimization). Disable battery optimization for *Android USB Microphone*:
Settings → Battery → App battery usage → Unrestricted.

## OBS / app doesn't show the source

- Check `pactl list sources short | grep android_usb_mic`.
- In OBS use **Audio Input Capture** → device *Android USB Microphone*.
- **Restart the browser** after the first ever creation of the device
  (browsers cache the device list at startup).
- Make sure only one instance of the desktop app is running (the daemon is a
  singleton; duplicates would show as multiple `android_usb_mic` entries).

## Browser test (verified)

Firefox was tested end-to-end: `getUserMedia` returns a track labelled
"Android USB Microphone", WebAudio receives samples at 48 kHz. Chromium and
Chrome use the same PulseAudio/PipeWire path and behave the same.

## The audio is silent / very quiet

- The desktop shows a level meter — talk into the phone; it must move.
- **Phone screen off**: Android silences an app's microphone when the screen
  is off. The desktop app lifts this automatically over USB (appops
  "allow" for the app's uid) on every connect — verified working. If your
  OEM blocks it, keep the screen on; the app also holds a screen wake lock
  while streaming so the display never times out by itself.
- Android may be sending to a "voice call" route if the phone is in a call.
- The capture uses the `VOICE_RECOGNITION` source (flat response, no AGC).
  If your phone routes oddly, try speaking into the bottom mic.

## APK install hangs at "Notifying the device"

An on-phone confirmation is waiting (realme/Oppo/Xiaomi): unlock the phone
and approve. Or enable Developer options → **Install via USB** /
**USB install**. Check for a notification from "Security" or "App market".

## Log files

- Daemon log: `~/.cache/aum/aum-pw-source.log`
- Run the app with `aum-mic -v` for verbose transport logs.
