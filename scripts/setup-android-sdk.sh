#!/usr/bin/env bash
# Bootstrap a self-contained Android SDK + JDK + Kotlin under .sdk/ so the
# APK can be built without root and without Android Studio.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SDK="$ROOT/.sdk"
mkdir -p "$SDK" "$SDK/dl"
cd "$SDK"

JDK_URL="https://api.adoptium.net/v3/binary/latest/17/ga/linux/x64/jdk/hotspot/normal/eclipse"
CMDTOOLS_URL="https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip"
KOTLIN_URL="https://github.com/JetBrains/kotlin/releases/download/v2.0.20/kotlin-compiler-2.0.20.zip"

if [[ ! -x jdk/bin/java ]]; then
    echo "Downloading Temurin JDK 17..."
    curl -L -o dl/jdk.tar.gz "$JDK_URL"
    mkdir -p jdk && tar -xzf dl/jdk.tar.gz -C jdk --strip-components=1
fi

if [[ ! -x cmdtools/bin/sdkmanager ]]; then
    echo "Downloading Android cmdline-tools..."
    curl -L -o dl/cmdtools.zip "$CMDTOOLS_URL"
    mkdir -p cmdtools && unzip -q dl/cmdtools.zip -d cmdtools
    mv cmdtools/cmdline-tools/* cmdtools/ && rmdir cmdtools/cmdline-tools
fi

if [[ ! -x kotlin/kotlinc/bin/kotlinc ]]; then
    echo "Downloading Kotlin compiler..."
    curl -L -o dl/kotlin.zip "$KOTLIN_URL"
    mkdir -p kotlin && unzip -q dl/kotlin.zip -d kotlin
fi

export JAVA_HOME="$SDK/jdk"
yes | cmdtools/bin/sdkmanager --sdk_root="$SDK" --licenses >/dev/null
cmdtools/bin/sdkmanager --sdk_root="$SDK" "platforms;android-35" "build-tools;35.0.0"

echo "SDK ready under $SDK"
