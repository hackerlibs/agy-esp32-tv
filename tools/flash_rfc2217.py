#!/usr/bin/env python3
"""Flash AGY TV firmware to an ESP32-C3 over a remote RFC2217 proxy.

Solves three pitfalls of flashing ESP32-C3 (native USB-Serial/JTAG) through
esp_rfc2217_server over an SSH reverse tunnel:
  1. Wait 0.6s after open() with --before no_reset (the server's custom reset
     already triggers on DTR_ON at connect time) and raise SYNC_TIMEOUT for RTT.
  2. Use --no-stub --compress so the chip stays in mask ROM (running the RAM
     stub can cause macOS to re-enumerate /dev/cu.usbmodem* -> Errno 6).
  3. Patch pyserial's rfc2217.Serial.timeout setter AFTER open() so esptool's
     per-block timeout save/restore does not renegotiate Telnet port options
     on every command (~10x faster).

Usage:
    python3 tools/flash_rfc2217.py                           # flash all 3 segments
    python3 tools/flash_rfc2217.py --app-only                # flash 0x10000 agy_tv.bin only
    python3 tools/flash_rfc2217.py --port 'rfc2217://127.0.0.1:4000?ign_set_control'

Requires ESP-IDF's Python environment (or any Python with esptool + pyserial).
Automatically re-execs with ~/.espressif/python_env/idf5.5_*/bin/python if needed.
"""
import argparse
import glob
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PORT = "rfc2217://127.0.0.1:4000?ign_set_control&timeout=10"
PID_FILES = ("/tmp/agy-tv.bridge.pid", "/tmp/zcode-tv.bridge.pid")


def maybe_reexec_idf_python():
    try:
        import esptool  # noqa: F401
        return
    except ImportError:
        pass
    candidates = sorted(glob.glob(os.path.expanduser("~/.espressif/python_env/idf*/bin/python")))
    if candidates and os.path.abspath(sys.executable) != os.path.abspath(candidates[-1]):
        os.execv(candidates[-1], [candidates[-1]] + sys.argv)
    sys.exit("esptool not found; activate ESP-IDF (source ~/esp/esp-idf/export.sh) or install esptool")


def check_bridge_stopped():
    for pid_path in PID_FILES:
        if os.path.exists(pid_path):
            try:
                pid = int(open(pid_path).read().strip())
                os.kill(pid, 0)
                sys.exit("bridge is running (pid %d in %s); stop it first: kill %d" % (pid, pid_path, pid))
            except (ValueError, ProcessLookupError, PermissionError):
                pass


def normalize_port(port):
    port = port.replace("ign_set_controls", "ign_set_control")
    if port.startswith("rfc2217://") and "ign_set_control" not in port:
        port += ("&" if "?" in port else "?") + "ign_set_control"
    if port.startswith("rfc2217://") and "timeout=" not in port:
        port += "&timeout=10"
    return port


def main():
    maybe_reexec_idf_python()
    parser = argparse.ArgumentParser(description="Flash AGY TV firmware over RFC2217.")
    parser.add_argument("--port", default=os.environ.get("AGY_TV_PORT", DEFAULT_PORT),
                        help="RFC2217 URL (default: %(default)s)")
    parser.add_argument("--app-only", action="store_true",
                        help="Only flash 0x10000 build/agy_tv.bin (skip bootloader & partition table)")
    parser.add_argument("--build-dir", default=os.path.join(ROOT, "build"),
                        help="ESP-IDF build directory (default: %(default)s)")
    args = parser.parse_args()

    check_bridge_stopped()
    port = normalize_port(args.port)

    segments = []
    if not args.app_only:
        segments += [
            ("0x0", os.path.join(args.build_dir, "bootloader", "bootloader.bin")),
            ("0x8000", os.path.join(args.build_dir, "partition_table", "partition-table.bin")),
        ]
    segments.append(("0x10000", os.path.join(args.build_dir, "agy_tv.bin")))

    for _, path in segments:
        if not os.path.isfile(path):
            sys.exit("missing build artifact: %s (run tools/build.sh first)" % path)

    import serial.rfc2217 as rfc2217
    import esptool
    from esptool import loader

    loader.SYNC_TIMEOUT = 0.5
    loader.DEFAULT_TIMEOUT = 15

    orig_connect = loader.ESPLoader.connect

    def patched_connect(self, mode="default_reset", attempts=7, detecting=False, warnings=True):
        time.sleep(0.6)  # let esp_rfc2217_server finish CustomReset triggered on open()

        @property
        def _fast_timeout(s):
            return s._timeout

        @_fast_timeout.setter
        def _fast_timeout(s, value):
            s._timeout = value

        rfc2217.Serial.timeout = _fast_timeout
        return orig_connect(self, mode=mode, attempts=attempts, detecting=detecting, warnings=warnings)

    loader.ESPLoader.connect = patched_connect

    cmd = [
        "--chip", "esp32c3",
        "--port", port,
        "-b", "115200",
        "--before", "no_reset",
        "--after", "hard_reset",
        "--no-stub",
        "write_flash",
        "--compress",
        "--flash_mode", "dio",
        "--flash_size", "4MB",
        "--flash_freq", "80m",
    ]
    for offset, path in segments:
        cmd.extend([offset, path])

    print("Flashing %s via %s ..." % (", ".join(f"{o}:{os.path.basename(p)}" for o, p in segments), port))
    esptool.main(cmd)


if __name__ == "__main__":
    main()
