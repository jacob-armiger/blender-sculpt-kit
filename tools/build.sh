#!/bin/bash
# Build the brush library, the colour themes and the grey matcap, and package the extension into dist/.
#   tools/build.sh             also builds the local Archive library
#   tools/build.sh --release   for a release: skips it
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# --release skips the local Archive library (see tools/build_assets.py).
if [[ "${1:-}" == "--release" ]]; then
    export SCULPTKIT_RELEASE=1
fi
BLENDER="${BLENDER:-/Applications/Blender.app/Contents/MacOS/Blender}"

"$BLENDER" -b --factory-startup --python-exit-code 1 --python "$ROOT/tools/build_assets.py"
"$BLENDER" -b --factory-startup --python-exit-code 1 --python "$ROOT/tools/build_theme.py"
"$BLENDER" -b --factory-startup --python-exit-code 1 --python "$ROOT/tools/build_matcap.py"

mkdir -p "$ROOT/dist"
"$BLENDER" --factory-startup --command extension build \
    --source-dir "$ROOT/sculpt_kit" --output-dir "$ROOT/dist"
