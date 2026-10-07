#!/bin/bash
# Build the brush libraries, the colour themes and the grey matcap, and package the extension into dist/.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BLENDER="${BLENDER:-/Applications/Blender.app/Contents/MacOS/Blender}"

"$BLENDER" -b --factory-startup --python-exit-code 1 --python "$ROOT/tools/build_assets.py"
"$BLENDER" -b --factory-startup --python-exit-code 1 --python "$ROOT/tools/build_theme.py"
"$BLENDER" -b --factory-startup --python-exit-code 1 --python "$ROOT/tools/build_matcap.py"

mkdir -p "$ROOT/dist"
"$BLENDER" --factory-startup --command extension build \
    --source-dir "$ROOT/sculpt_kit" --output-dir "$ROOT/dist"
