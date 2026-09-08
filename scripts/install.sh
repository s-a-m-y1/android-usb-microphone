#!/usr/bin/env bash
# One-command install of the Android USB Microphone desktop app on Ubuntu.
#
#   bash scripts/install.sh
#
# Installs into ~/.local (no root needed except for missing system packages),
# builds the PipeWire daemon, and registers a launcher entry.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX="$HOME/.local"
LIBEXEC="$PREFIX/libexec"
BIN="$PREFIX/bin"
SHARE="$PREFIX/share/applications"

echo "== Android USB Microphone: desktop installer =="

need_pkgs=()
command -v gcc >/dev/null || need_pkgs+=(build-essential)
command -v adb >/dev/null || need_pkgs+=(adb)
command -v pkg-config >/dev/null || need_pkgs+=(pkg-config)
pkg-config --exists libpipewire-0.3 2>/dev/null || need_pkgs+=(libpipewire-0.3-dev)
python3 -c "import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1')" 2>/dev/null \
    || need_pkgs+=(python3-gi gir1.2-gtk-4.0 gir1.2-adw-1)

if ((${#need_pkgs[@]})); then
    echo "Installing missing system packages: ${need_pkgs[*]}"
    echo "(requires sudo; your password may be asked)"
    sudo apt-get update -qq
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${need_pkgs[@]}"
fi

echo "Building desktop components..."
bash "$ROOT/scripts/build-desktop.sh"

echo "Installing files to $PREFIX"
mkdir -p "$LIBEXEC" "$BIN" "$SHARE" "$HOME/.local/share/aum"
install -m 755 "$ROOT/build/aum-pw-source" "$LIBEXEC/aum-pw-source"
install -m 644 "$ROOT/poc/aum-stream.jar" "$PREFIX/share/aum/aum-stream.jar" \
    2>/dev/null || true

# launcher script
cat > "$BIN/aum-mic" <<EOF
#!/usr/bin/env bash
export PYTHONPATH="$ROOT/desktop/src\${PYTHONPATH:+:\$PYTHONPATH}"
exec python3 -m aum "\$@"
EOF
chmod +x "$BIN/aum-mic"

cat > "$SHARE/aum-mic.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Android USB Microphone
Comment=Use your Android phone's microphone as a PipeWire source over USB
Exec=$BIN/aum-mic
Icon=audio-input-microphone
Terminal=false
Categories=AudioVideo;Audio;
EOF

# make the installed daemon discoverable by the in-repo sources too
if [[ ! -e "$LIBEXEC/aum-pw-source" ]]; then
    echo "error: install failed" >&2
    exit 1
fi

# Optional: Android app. Skipped automatically if the APK is not built or adb
# has no device attached.
if [[ -f "$ROOT/build/AndroidUsbMic.apk" ]] && adb devices 2>/dev/null | grep -qw device; then
    echo "Installing Android app on the connected phone (approve the prompt on the phone)..."
    timeout 120 adb install -r "$ROOT/build/AndroidUsbMic.apk" \
        && adb shell pm grant com.aum.mic android.permission.RECORD_AUDIO 2>/dev/null \
        && adb shell pm grant com.aum.mic android.permission.POST_NOTIFICATIONS 2>/dev/null \
        && echo "Android app installed." \
        || echo "NOTE: APK install did not finish (see docs/troubleshooting.md). "
fi

echo
echo "Installed. Run 'aum-mic' (or use the app grid) to start."
echo "Virtual microphone name: 'Android USB Microphone'"
