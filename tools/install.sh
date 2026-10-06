#!/bin/bash
# Install the newest build from dist/ into Blender and enable it.
# Blender must be closed: an open Blender writes its old preferences back when
# it quits, which would switch the extension off again.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BLENDER="${BLENDER:-/Applications/Blender.app/Contents/MacOS/Blender}"

# Compare whole program paths, so a shell whose command line merely mentions Blender does not count.
if ps -axo comm= | grep -qx "$BLENDER"; then
    echo "Blender is running. Quit it, then run this again." >&2
    exit 1
fi

ZIP="$(ls -t "$ROOT"/dist/sculpt_kit-*.zip | head -1)"
"$BLENDER" --command extension install-file --repo user_default --enable "$ZIP"
echo "Installed $ZIP"
