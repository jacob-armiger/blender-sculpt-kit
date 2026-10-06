#!/bin/bash
# Run the Sculpt Kit tests in a throwaway Blender profile, so your own
# preferences, add-ons and keymap are never touched.
#
#   tests/run.sh                    headless checks, then the interactive ones
#   tests/run.sh --headless         headless checks only (no Blender window)
#   tests/run.sh sim_zbrush_look    only the named interactive tests
#
# The interactive tests open a Blender window for a few seconds each and drive
# it with simulated mouse and key events.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BLENDER="${BLENDER:-/Applications/Blender.app/Contents/MacOS/Blender}"
PROFILE="$ROOT/.test-profile"
OUT="$ROOT/tests/out"
ZIP="$(ls -t "$ROOT"/dist/sculpt_kit-*.zip 2>/dev/null | head -1)"

HEADLESS=1
INTERACTIVE=1
ONLY=()
for arg in "$@"; do
    case "$arg" in
        --headless) INTERACTIVE=0 ;;
        *) ONLY+=("$arg"); HEADLESS=0 ;;
    esac
done

if [ -z "$ZIP" ]; then
    echo "No build in dist/. Run tools/build.sh first." >&2
    exit 1
fi

rm -rf "$PROFILE" "$OUT"
mkdir -p "$PROFILE" "$OUT"
export BLENDER_USER_RESOURCES="$PROFILE"
export SCULPT_KIT_TEST_OUT="$OUT"
quiet() { grep -v "^Blender [0-9]\|^Blender quit\|pyrna_enum_to_py\|^Read prefs\|^$" || true; }
status=0

echo "== install $(basename "$ZIP") into a clean profile"
"$BLENDER" --command extension install-file --repo user_default --enable "$ZIP" 2>&1 | quiet

if [ "$HEADLESS" -eq 1 ]; then
    echo "== headless checks"
    "$BLENDER" -b --python-exit-code 1 --python "$ROOT/tests/headless_checks.py" >"$OUT/headless.console.txt" 2>&1
    status=$?
    grep -E "^(PASS|FAIL|Traceback)|passed$|failed$" "$OUT/headless.console.txt" || true
    grep -qE "^(FAIL|Traceback)" "$OUT/headless.console.txt" && status=1
fi

if [ "$INTERACTIVE" -eq 1 ]; then
    # A saved file to open, so Blender starts without its splash screen.
    "$BLENDER" -b --python-expr "import bpy; bpy.ops.wm.save_as_mainfile(filepath=r'$OUT/start.blend')" >/dev/null 2>&1
    for test in "$ROOT"/tests/sim_*.py; do
        name="$(basename "$test" .py)"
        if [ "${#ONLY[@]}" -gt 0 ] && [[ ! " ${ONLY[*]} " == *" $name "* ]]; then
            continue
        fi
        echo "== $name"
        "$BLENDER" --enable-event-simulate --window-geometry 60 60 1500 1000 "$OUT/start.blend" \
            --python "$test" >"$OUT/$name.console.txt" 2>&1
        if [ -f "$OUT/$name.txt" ]; then
            cat "$OUT/$name.txt"
            grep -qE "^(FAIL|ERROR)" "$OUT/$name.txt" && status=1
            # Errors in code that Blender calls while drawing do not stop a test. They are in its console.
            if grep -q "^Traceback" "$OUT/$name.console.txt"; then
                echo "ERROR  $name: Python errors while it ran (see $OUT/$name.console.txt)"
                status=1
            fi
        else
            echo "ERROR  $name produced no results (see $OUT/$name.console.txt)"
            status=1
        fi
    done
fi

echo
[ "$status" -eq 0 ] && echo "ALL TESTS PASSED" || echo "SOME TESTS FAILED"
exit "$status"
