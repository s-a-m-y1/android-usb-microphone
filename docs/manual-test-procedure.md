# Manual test procedure

Prerequisites: Ubuntu with PipeWire, `adb` installed, Android phone with USB
debugging, data USB cable, OBS Studio installed.

1. **Connect** the Android phone with the USB cable.
2. **Enable USB debugging** on the phone (docs/usb-debugging.md).
3. **Authorize the computer** when the phone asks ("Always allow").
4. Verify: `adb devices` shows the serial with state `device`.
5. **Launch the desktop application**:
   ```bash
   scripts/run.sh
   ```
   Expected: the device card shows `✓ <model>`, USB dot is green/red
   correctly, "Virtual Device" shows *Ready* once the daemon is up.
6. **Start streaming**: click *Start Microphone*.
   - If the Android app is installed: launch it on the phone and press
     *Start* (grant the microphone permission when asked).
   - Without the app, the desktop switches to direct USB capture
     automatically.
   Expected: status *Streaming*, the mic level bar moves when you speak near
   the phone, dot green.
7. **Verify the virtual device exists**:
   ```bash
   pactl list sources short | grep android_usb_mic
   ```
8. **Open OBS** → Settings → Audio → Mic/Auxiliary Audio Device →
   *Android USB Microphone*. Or add an **Audio Input Capture** source and
   select it.
9. **Record**: start recording in OBS, speak near the phone for 10 s, stop.
   Playback must contain your voice. Alternatively:
   ```bash
   pw-record --target android_usb_mic /tmp/x.wav
   ```
10. **Disconnect USB** while OBS is recording. Expected: the desktop status
    goes to "Disconnected", no crash, OBS may record silence.
11. **Reconnect USB**. Expected: within a few seconds the status returns to
    *Streaming* (auto reconnect) and OBS again records the phone mic.
12. **Settings round-trip**: open Settings, change device name to
    "My Phone Mic", click save. `pactl list sources short` must now show a
    source with that description after the daemon restarts.
13. **Stop**: click *Stop*. The virtual source stays (Ready) but carries
    silence; quitting the app removes it.

Pass criteria: steps 7, 9, 11, 12 succeed with no GUI freeze and no clicks
during normal USB operation.
