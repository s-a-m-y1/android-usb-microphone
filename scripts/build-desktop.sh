#!/usr/bin/env bash
# Build the desktop components: PipeWire daemon + fallback capture jar (if a
# JDK+SDK are available under .sdk/).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "[desktop] building aum-pw-source (PipeWire virtual source)"
mkdir -p build
gcc -O2 -Wall -Wextra \
    -o build/aum-pw-source desktop/pipewire/aum_pw_source.c \
    $(pkg-config --cflags --libs libpipewire-0.3) -lm -lpthread
echo "[desktop] OK: build/aum-pw-source"

gcc -O2 -Wall -o build/test_ringbuf desktop/pipewire/test_ringbuf.c
echo "[desktop] OK: build/test_ringbuf"

# Optional: rebuild the exec-out fallback jar when an SDK is bootstrapped
if [[ -x .sdk/jdk/bin/javac && -f .sdk/platforms/android-35/android.jar ]]; then
    echo "[desktop] building poc/aum-stream.jar (fallback capture)"
    export JAVA_HOME="$ROOT/.sdk/jdk"
    export PATH="$JAVA_HOME/bin:$PATH"
    rm -rf poc/classes2 poc/dex2
    mkdir -p poc/classes2 poc/dex2
    .sdk/jdk/bin/javac --release 11 \
        -classpath .sdk/platforms/android-35/android.jar \
        -d poc/classes2 poc/MicStream.java
    if [[ -x .sdk/build-tools/35.0.0/d8 ]]; then
        .sdk/build-tools/35.0.0/d8 --release --min-api 26 \
            --output poc/dex2 poc/classes2/com/aum/poc/*.class
        (cd poc/dex2 && "$JAVA_HOME/bin/jar" cf ../aum-stream.jar classes.dex)
        echo "[desktop] OK: poc/aum-stream.jar"
    fi
fi
echo "[desktop] done"
