#!/bin/bash
# tools/stroke_rig.sh OUTDIR : run tools/stroke_rig.py in the test profile with RIG_PLAN (and RIG_VARIANTS) set.
# The newest build must be installed in the test profile first (tests/run.sh --headless does that).
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BLENDER="${BLENDER:-/Applications/Blender.app/Contents/MacOS/Blender}"
OUT="${1:?output folder}"
mkdir -p "$OUT"
export BLENDER_USER_RESOURCES="$ROOT/.test-profile" SCULPT_KIT_TEST_OUT="$OUT"
[ -f "$OUT/start.blend" ] || "$BLENDER" -b --python-expr "import bpy; bpy.ops.wm.save_as_mainfile(filepath=r'$OUT/start.blend')" >/dev/null 2>&1
"$BLENDER" --enable-event-simulate --window-geometry 60 60 1500 1000 "$OUT/start.blend" --python "$ROOT/tools/stroke_rig.py" >"$OUT/rig.console.txt" 2>&1
cat "$OUT/rig.txt"; grep -n "Traceback" -A6 "$OUT/rig.console.txt" | head -20
