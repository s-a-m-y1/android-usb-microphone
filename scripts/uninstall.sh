#!/usr/bin/env bash
# Remove the desktop installation made by scripts/install.sh.
set -uo pipefail
PREFIX="$HOME/.local"

echo "== Android USB Microphone: uninstaller =="

# stop a running daemon if any
pkill -f aum-pw-source 2>/dev/null && echo "Stopped aum-pw-source."

rm -f "$PREFIX/libexec/aum-pw-source" \
      "$PREFIX/bin/aum-mic" \
      "$PREFIX/share/applications/aum-mic.desktop" \
      "$HOME/.config/autostart/aum-desktop.desktop"
rmdir "$PREFIX/libexec" 2>/dev/null
rm -rf "$HOME/.cache/aum"

# remove the phone app if present
if command -v adb >/dev/null && adb devices 2>/dev/null | grep -qw device; then
    adb uninstall com.aum.mic 2>/dev/null && echo "Removed Android app from phone." || true
fi

echo "Uninstalled. (Repository sources and .sdk were not touched.)"
