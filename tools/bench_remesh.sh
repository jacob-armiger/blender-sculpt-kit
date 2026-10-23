#!/bin/bash
# Benchmark the quad-remesh engines on a sculpt, in a throwaway Blender profile with the current source built
# and installed (so your own preferences and add-ons are never touched).
#
#   tools/bench_remesh.sh                                  a noisy 1.3M-face sphere, both engines, 20k and 100k
#   tools/bench_remesh.sh figure.blend --object Figure     an object from a .blend
#   tools/bench_remesh.sh figure.blend --targets 20000 --engines INSTANT
#
# Results: tests/out/bench/bench.json and bench.blend (the results in a row next to the original).
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BLENDER="${BLENDER:-/Applications/Blender.app/Contents/MacOS/Blender}"
PROFILE="$ROOT/.bench-profile"
OUT="$ROOT/tests/out/bench"
BLEND=""
if [[ "${1:-}" == *.blend ]]; then
    BLEND="$1"
    shift
fi

"$ROOT/tools/build.sh" >/dev/null || exit 1
ZIP="$(ls -t "$ROOT"/dist/sculpt_kit-*.zip | head -1)"

rm -rf "$PROFILE" "$OUT"
mkdir -p "$PROFILE" "$OUT"
export BLENDER_USER_RESOURCES="$PROFILE"
export SCULPT_KIT_BENCH_OUT="$OUT"
echo "== install $(basename "$ZIP") into a clean profile"
"$BLENDER" --command extension install-file --repo user_default --enable "$ZIP" 2>&1 | grep -v "^Blender [0-9]\|^$" || true

echo "== bench"
"$BLENDER" -b ${BLEND:+"$BLEND"} --python-exit-code 1 --python "$ROOT/tools/bench_remesh.py" -- "$@" 2>&1 \
    | tee "$OUT/console.txt" | grep "^BENCH\|^Traceback\|Error"
