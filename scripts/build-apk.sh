#!/usr/bin/env bash
# Build the Android APK without Gradle, using a self-contained SDK under
# .sdk/ (see scripts/setup-android-sdk.sh) or a system-wide ANDROID_HOME.
#
# Output: build/AndroidUsbMic.apk (debug-signed, installable with adb install)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# ---------------------------------------------------------------- locate SDK
if [[ -d "$ROOT/.sdk/build-tools" ]]; then
    SDK="$ROOT/.sdk"
    BT="$(ls -d "$SDK"/build-tools/* | sort -V | tail -1)"
    PLATFORM="$(ls -d "$SDK"/platforms/android-* | sort -V | tail -1)"
    JAVA_HOME="${JAVA_HOME:-$SDK/jdk}"
    KOTLINC="$SDK/kotlin/kotlinc/bin/kotlinc"
else
    SDK="${ANDROID_HOME:-$HOME/Android/Sdk}"
    BT="$(ls -d "$SDK"/build-tools/* | sort -V | tail -1)"
    PLATFORM="$(ls -d "$SDK"/platforms/android-* | sort -V | tail -1)"
    JAVA_HOME="${JAVA_HOME:-}"
    KOTLINC="$(command -v kotlinc || echo "$SDK/cmdline-tools/latest/bin/kotlinc")"
fi
export JAVA_HOME
PATH="$JAVA_HOME/bin:$BT:$PATH"

echo "[1/6] aapt2: compiling resources"
rm -rf build/apk && mkdir -p build/apk/gen build/apk/res
RES=( android/app/src/main/res )
AAPT2_ARGS=()
for r in "${RES[@]}"; do
    "$BT/aapt2" compile --dir "$r" -o build/apk/res.zip
done

echo "[2/6] aapt2: linking resources + manifest"
# aapt2 (non-gradle) needs the package attribute; inject it into a copy so the
# source manifest stays compatible with AGP, which forbids it.
sed 's|<manifest |<manifest package="com.aum.mic" |' \
    android/app/src/main/AndroidManifest.xml > build/apk/AndroidManifest.xml
"$BT/aapt2" link \
    -o build/apk/base.apk \
    -I "$PLATFORM/android.jar" \
    --manifest build/apk/AndroidManifest.xml \
    -R build/apk/res.zip \
    --java build/apk/gen \
    --auto-add-overlay

echo "[3/6] compiling R.java + Kotlin sources (kotlinc takes a minute)"
find build/apk/gen -name '*.java' > build/apk/sources.txt
find android/app/src/main/java -name '*.kt' >> build/apk/sources.txt
# R.java is Java: compile it with javac first
"$JAVA_HOME/bin/javac" --release 11 -classpath "$PLATFORM/android.jar" \
    -d build/apk/jout $(find build/apk/gen -name '*.java')
"$JAVA_HOME/bin/jar" cf build/apk/r-classes.jar -C build/apk/jout .
# Kotlin sources resolve R against the compiled jout classes
"$KOTLINC" \
    -classpath "$PLATFORM/android.jar:build/apk/jout" \
    -d build/apk/app-unsigned.jar \
    -include-runtime \
    $(find android/app/src/main/java -name '*.kt')

echo "[4/6] d8: dexing"
"$BT/d8" --release --min-api 26 --lib "$PLATFORM/android.jar" \
    --output build/apk build/apk/app-unsigned.jar build/apk/r-classes.jar

echo "[5/6] packaging + zipalign"
cp build/apk/base.apk build/AndroidUsbMic-unsigned.apk
(cd build/apk && zip -q -u ../AndroidUsbMic-unsigned.apk classes.dex)
"$BT/zipalign" -f 4 build/AndroidUsbMic-unsigned.apk build/AndroidUsbMic-aligned.apk

echo "[6/6] signing (debug key)"
KEYSTORE="${AUM_KEYSTORE:-$HOME/.config/aum/debug.keystore}"
mkdir -p "$(dirname "$KEYSTORE")"
if [[ ! -f "$KEYSTORE" ]]; then
    "$JAVA_HOME/bin/keytool" -genkeypair -keystore "$KEYSTORE" \
        -alias androiddebugkey -keyalg RSA -keysize 2048 -validity 10000 \
        -storepass android -keypass android \
        -dname "CN=Android Debug,O=Android,C=US" >/dev/null 2>&1
fi
"$BT/apksigner" sign --ks "$KEYSTORE" --ks-pass pass:android \
    --key-pass pass:android --out build/AndroidUsbMic.apk \
    build/AndroidUsbMic-aligned.apk

rm -f build/AndroidUsbMic-unsigned.apk build/AndroidUsbMic-aligned.apk
echo "OK: build/AndroidUsbMic.apk"
