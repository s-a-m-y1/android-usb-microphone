#!/usr/bin/env bash
# Run the desktop app from the repository (development mode).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# build if needed
[[ -x "$ROOT/build/aum-pw-source" ]] || bash "$ROOT/scripts/build-desktop.sh"

export PYTHONPATH="$ROOT/desktop/src${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m aum "$@"
