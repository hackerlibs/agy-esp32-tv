#!/usr/bin/env bash
# Build (and optionally flash) the firmware. Wraps the ESP-IDF environment so
# no manual export is needed.
#
#   tools/build.sh              build only
#   tools/build.sh --flash      build and flash via the first usbmodem port
#   tools/build.sh --monitor    build, flash, then open a serial monitor
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IDF="${IDF_PATH:-$HOME/esp/esp-idf}"
PORT="${PORT:-$(ls /dev/cu.usbmodem* 2>/dev/null | head -1 || true)}"

# shellcheck disable=SC1091
. "$IDF/export.sh" >/dev/null

cd "$ROOT"
if [ "${1:-}" = "--flash" ] || [ "${1:-}" = "--monitor" ]; then
    if [ -z "$PORT" ]; then echo "no /dev/cu.usbmodem* port found" >&2; exit 1; fi
    idf.py -p "$PORT" -b 921600 flash
    if [ "${1:-}" = "--monitor" ]; then exec python3 "$ROOT/tools/monitor.sh" "$PORT"; fi
else
    idf.py build "$@"
fi
