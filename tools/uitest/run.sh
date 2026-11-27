#!/usr/bin/env bash
# Renders the AGY status screens offscreen (no hardware needed) and checks that
# the panel is never left with stale rows. Writes the frames plus a contact
# sheet to the output directory (default /tmp/agy-tv-ui).
#
#   tools/uitest/run.sh [output directory]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="${1:-/tmp/agy-tv-ui}"
rm -rf "$OUT"
mkdir -p "$OUT"

if [ -n "${CC:-}" ]; then
    COMPILER="$CC"
elif command -v clang >/dev/null 2>&1; then
    COMPILER=clang
else
    COMPILER=gcc
fi

"$COMPILER" -std=c11 -Wall -Wextra -Werror -O1 -g -fsanitize=address,undefined \
    -I "$ROOT/main" -I "$ROOT/tools/uitest/fakeinc" \
    "$ROOT/main/gfx.c" "$ROOT/main/ui.c" "$ROOT/main/zmodel.c" "$ROOT/tools/uitest/main.c" \
    -o "$OUT/uitest"

"$OUT/uitest" "$OUT"

PYTHON="${PYTHON:-python3}"
"$PYTHON" -I "$ROOT/tools/uitest/to_png.py" "$OUT"
