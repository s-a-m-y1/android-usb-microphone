#!/usr/bin/env bash
# Build everything: desktop daemon, tests binary, and (when the Android SDK
# is bootstrapped via scripts/setup-android-sdk.sh) the Android APK.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bash "$ROOT/scripts/build-desktop.sh"
if [[ -x "$ROOT/.sdk/cmdtools/bin/sdkmanager" || -d "$HOME/Android/Sdk" ]]; then
    bash "$ROOT/scripts/build-apk.sh"
else
    echo "[build] Android SDK not found - skipping APK (run scripts/setup-android-sdk.sh)"
fi
echo "[build] all done"
