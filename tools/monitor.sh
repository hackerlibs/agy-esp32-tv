#!/usr/bin/env python3
"""Serial monitor for the ZCode TV board.

Opens the port with the line states the ESP32-C3 USB-Serial/JTAG peripheral
expects for normal operation (DTR and RTS both asserted) and never toggles
them afterwards, so the chip is not reset or pushed into download mode while
you watch. Plain terminal tools can get this wrong and make the board look
dead -- see README, "The board looks dead".

    tools/monitor.sh [port]        # default: first /dev/cu.usbmodem*
"""
import select
import sys
import termios
import time

import serial


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else None
    if port is None:
        import glob
        ports = sorted(glob.glob("/dev/cu.usbmodem*"))
        if not ports:
            sys.exit("no /dev/cu.usbmodem* port found")
        port = ports[0]
    ser = serial.Serial(port, 115200, timeout=0.1, dsrdtr=False, rtscts=False)
    ser.dtr = True
    ser.rts = True
    print("watching %s -- Ctrl-C to exit (never resets the board)\n" % port)
    stdin = sys.stdin.fileno()
    old = termios.tcgetattr(stdin)
    termios.tcsetattr(stdin, termios.TCSADRAIN,
                      [old[0], old[1], old[2] & ~termios.ICANON & ~termios.ECHO, 0, old[4], old[5], old[6][:-1] + [b"\x00"]])
    try:
        while True:
            ready, _, _ = select.select([stdin], [], [], 0.05)
            if ready:
                data = sys.stdin.read(1)
                if data == "\x03":  # Ctrl-C
                    break
                ser.write(data.encode("utf-8", "replace"))
            chunk = ser.read(4096)
            if chunk:
                sys.stdout.write(chunk.decode("utf-8", "replace"))
                sys.stdout.flush()
    except KeyboardInterrupt:
        pass
    finally:
        termios.tcsetattr(stdin, termios.TCSADRAIN, old)
        ser.close()


if __name__ == "__main__":
    main()
