#!/usr/bin/env bash
# Renders the status screens offscreen (no hardware needed) and checks that the
# panel is never left with stale rows. Writes the frames plus a contact sheet to
# the output directory (default /tmp/zcode-tv-ui).
#
#   tools/uitest/run.sh [output directory]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="${1:-/tmp/zcode-tv-ui}"
rm -rf "$OUT"
mkdir -p "$OUT"

clang -std=c11 -Wall -Wextra -Werror -O1 -g -fsanitize=address,undefined \
    -I "$ROOT/main" -I "$ROOT/tools/uitest/fakeinc" \
    "$ROOT/main/gfx.c" "$ROOT/main/ui.c" "$ROOT/main/zmodel.c" "$ROOT/tools/uitest/main.c" \
    -o "$OUT/uitest"

"$OUT/uitest" "$OUT"

PYTHON="${PYTHON:-python3}"   # set PYTHON to an interpreter that has Pillow installed
"$PYTHON" -I "$ROOT/tools/uitest/to_png.py" "$OUT"
