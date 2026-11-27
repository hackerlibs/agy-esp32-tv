#!/usr/bin/env bash
# Put the stock "xiao dianshi" firmware back. The flash image was dumped and
# verified byte-for-byte before this project touched the board, so this is a
# full restore including the stock NVS contents.
#
#   tools/restore_original.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP="$ROOT/backup/esp32c3_original_4MB.bin"
PORT="${PORT:-$(ls /dev/cu.usbmodem* 2>/dev/null | head -1 || true)}"

if [ -z "$PORT" ]; then echo "no /dev/cu.usbmodem* port found" >&2; exit 1; fi
if [ ! -f "$BACKUP" ]; then echo "missing $BACKUP" >&2; exit 1; fi
if [ -f /tmp/zcode-tv.bridge.pid ] && kill -0 "$(cat /tmp/zcode-tv.bridge.pid)" 2>/dev/null; then
    echo "the bridge is running and owns the port: kill \$(cat /tmp/zcode-tv.bridge.pid) first" >&2
    exit 1
fi

echo "This replaces the whole 4 MB flash (ZCode TV firmware -> stock firmware)."
printf "Continue? [y/N] "
read -r answer
[ "$answer" = "y" ] || { echo "aborted"; exit 1; }

# Keep using an esptool outside the IDF environment if it is not active.
if command -v esptool >/dev/null 2>&1; then
    ESPTOOL=(esptool)
elif [ -x "$HOME/.espressif/python_env/idf5.5_py3.13_env/bin/esptool" ]; then
    ESPTOOL=("$HOME/.espressif/python_env/idf5.5_py3.13_env/bin/esptool")
else
    echo "esptool not found (activate the ESP-IDF environment or: pip install esptool)" >&2
    exit 1
fi

"${ESPTOOL[@]}" --chip esp32c3 --port "$PORT" --baud 921600 \
    write-flash --flash-mode dio --flash-freq 80m --flash-size 4MB 0x0 "$BACKUP"
echo "stock firmware restored."
