# USB debugging setup (Android)

The USB-only transport needs USB debugging. No root, no Wi-Fi.

## 1. Enable developer options

- Open **Settings → About phone → Version**
- Tap **Build number** 7 times until "You are now a developer".

## 2. Enable USB debugging

- **Settings → Additional settings → Developer options**
- Enable **USB debugging**.
- On realme/Oppo also enable **USB debugging (Security settings)** only if
  you want the desktop to control/install things; plain streaming does not
  need it.

## 3. Connect and authorize

1. Connect the phone with a **data** USB cable.
2. On the phone, set USB mode to *File transfer / MTP* (some devices need
   this for adb; *No data transfer* usually also works on Android 11+).
3. Run:

   ```bash
   adb devices
   ```

4. A dialog appears on the phone: **“Allow USB debugging?”** — check
   *Always allow from this computer* and tap **Allow**.
5. `adb devices` must now show:

   ```
   0I74325I271005CA    device
   ```

State meanings:

| state          | meaning                                             |
| -------------- | --------------------------------------------------- |
| `device`       | ready                                               |
| `unauthorized` | accept the "Allow USB debugging?" dialog on the phone |
| `offline`      | replug the cable, restart `adb kill-server && adb start-server` |
| (missing)      | charge-only cable or USB debugging off              |

## 4. Grant microphone permission (optional shortcut)

The app asks on-screen; you can also pre-grant over adb:

```bash
adb shell pm grant com.aum.mic android.permission.RECORD_AUDIO
```

## Troubleshooting adb itself

- `adb kill-server && adb start-server`
- Plug into a USB 2.0 port directly on the PC (hubs are flaky).
- On Ubuntu, the packaged `adb` (android-tools) needs no udev rules for
  modern devices; if the device never appears, install
  `android-sdk-platform-tools-common` for the udev rules.
