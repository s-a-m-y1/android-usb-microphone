# PipeWire integration notes

## What the app creates

`aum-pw-source` opens a `pw_stream` with:

- direction `PW_DIRECTION_OUTPUT` (we *produce* audio),
- `PW_KEY_MEDIA_CLASS = "Audio/Source"` → wireplumber presents the node as a
  capture source (a microphone),
- `PW_KEY_NODE_NAME = "android_usb_mic"`,
- format S16LE, mono, 48 kHz (configurable),
- node latency 512/48000 (~10.7 ms).

The daemon's realtime process callback pulls PCM16 frames from the lock-free
ring buffer and writes them into the PipeWire buffer; on underrun it outputs
silence and bumps a counter (visible in the GUI status).

## Verification

```bash
pactl list sources short          # shows android_usb_mic
wpctl status                      # under "Sources" as "Android USB Microphone"
pw-record --target android_usb_mic /tmp/x.wav
pw-dump | jq '.. | .props? | select(.?.node.name == "android_usb_mic")'
```

In OBS: Audio settings → Mic/Auxiliary Audio → *Android USB Microphone* (or
add an "Audio Input Capture" source). In browsers/Discord, pick it as the
microphone; wireplumber routes them automatically.

## Buffering

- The GUI setting "Ring buffer size (ms)" (default 120) sets how much audio
  is buffered between USB and PipeWire. Increase it if the phone or cable is
  unreliable; decrease for lower latency at the risk of dropouts.
- PipeWire quantum can be adjusted globally with `pw-metadata`, but the app
  requests its own node latency and does not touch your system-wide settings.

## PulseAudio apps

`pipewire-pulse` (default on Ubuntu) makes the virtual source look exactly
like an ALSA/Pulse source, so even old PulseAudio-only apps can use it.

## Conflict avoidance

The daemon uses a per-user socket `$XDG_RUNTIME_DIR/aum-mic.sock` and does
not modify any PipeWire/WirePlumber config files. Stopping the app removes
the node; nothing persists system-wide.
