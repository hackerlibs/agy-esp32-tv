#!/usr/bin/env bash
# Put the stock "xiao dianshi" firmware back. The flash image was dumped and
# verified byte-for-byte before this project touched the board, so this is a
# full restore including the stock NVS contents.
#
#   tools/restore_original.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP="$ROOT/backup/esp32c3_original_4MB.bin"

detect_port() {
    for pat in /dev/cu.usbmodem* /dev/ttyACM* /dev/ttyUSB* /dev/cu.usbserial*; do
        for p in $pat; do
            if [ -e "$p" ]; then
                echo "$p"
                return 0
            fi
        done
    done
    return 0
}

PORT="${PORT:-$(detect_port)}"

if [ -z "$PORT" ]; then
    echo "no ESP32 serial port found (looked for /dev/cu.usbmodem*, /dev/ttyACM*, /dev/ttyUSB*)" >&2
    exit 1
fi
if [ ! -f "$BACKUP" ]; then echo "missing $BACKUP" >&2; exit 1; fi
for pidfile in /tmp/agy-tv.bridge.pid /tmp/zcode-tv.bridge.pid; do
    if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
        echo "the bridge is running and owns the port: kill \$(cat $pidfile) first" >&2
        exit 1
    fi
done

echo "This replaces the whole 4 MB flash (AGY TV firmware -> stock firmware)."
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
