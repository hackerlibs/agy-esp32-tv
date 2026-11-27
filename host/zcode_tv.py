#!/usr/bin/env python3
"""ZCode TV host side: a serial bridge daemon and the fast hook entry point.

    zcode_tv.py bridge [--port /dev/cu.usbmodem14201] [--daemon]
    zcode_tv.py hook                        # runs from ZCode hooks, JSON on stdin
    zcode_tv.py send "ZTV1 C ping"          # raw line to the device, for testing
    zcode_tv.py status                      # is the bridge running?

The bridge owns the serial port for the whole session: hooks are short-lived
processes, and re-opening the USB Serial/JTAG port per event would reset the
ESP32 (DTR/RTS drive its EN/BOOT lines). Hooks therefore talk to the bridge
over a local datagram socket and exit; the bridge translates their events into
status frames and keeps the device fed with heartbeats.

Hook failures are silent by design: a status gadget must never block the agent.
"""
import json
import os
import socket
import sys
import time

SOCKET_PATH = "/tmp/zcode-tv.bridge.sock"
LOG_PATH = "/tmp/zcode-tv.log"
PID_PATH = "/tmp/zcode-tv.bridge.pid"

PROTO = "ZTV1"
STATE_NAMES = {  # device-side state tokens, see main/zlink.h
    "idle": "IDLE", "think": "THINK", "tool": "TOOL", "perm": "PERM", "err": "ERR", "done": "DONE",
}
DONE_SECONDS = 30  # keep in sync with CONFIG_ZTV_UI_DONE_SECONDS
HEARTBEAT_SECONDS = 2.0
MSG_MAX_BYTES = 200  # device buffer is 240 bytes of UTF-8; leave room


def log(msg):
    line = "%s %s" % (time.strftime("%H:%M:%S"), msg)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    return line


def clip(text, limit=MSG_MAX_BYTES):
    """UTF-8 safe truncation; also drop quotes and control characters, which the
    device's line protocol cannot carry."""
    text = "".join(c if c >= " " and c != '"' else " " for c in (text or ""))
    raw = text.encode("utf-8")
    if len(raw) > limit:
        raw = raw[:limit]
        while raw and (raw[-1] & 0xC0) == 0x80:  # do not cut a multi-byte character in half
            raw = raw[:-1]
        raw = raw.rstrip()
    return raw.decode("utf-8", "ignore")


def quote(text):
    return '"%s"' % (text or "")


def encode_frame(frame):
    parts = ["%s S st=%s" % (PROTO, frame["state"])]
    parts.append("tool=%s" % quote(clip(frame.get("tool", ""), 18)))
    parts.append("n=%d f=%d el=%d todo=%d/%d" % (frame.get("tools", 0), frame.get("fails", 0),
                                                 frame.get("elapsed", 0), frame.get("todo_done", 0),
                                                 frame.get("todo_total", 0)))
    parts.append("cur=%s" % quote(clip(frame.get("cur", ""), 110)))
    parts.append("msg=%s" % quote(clip(frame.get("msg", ""))))
    parts.append("proj=%s" % quote(clip(frame.get("proj", ""), 28)))
    parts.append("sid=%s" % quote(clip(frame.get("sid", ""), 8)))
    parts.append("clk=%s" % quote(frame.get("clock", "")))
    parts.append("date=%s" % quote(clip(frame.get("date", ""), 18)))
    return " ".join(parts)


def clock_now():
    return time.strftime("%H:%M:%S")


def date_now():
    return time.strftime("%Y-%m-%d %a").upper()[:18]


# ------------------------------------------------------------------ tool summaries

def describe_input(tool, payload):
    """One line about what the tool call is doing."""
    if not isinstance(payload, dict):
        return clip(str(payload), 90)
    if tool in ("Bash",):
        return clip(payload.get("command", ""), 110)
    if tool in ("Edit", "Write", "Read", "NotebookEdit"):
        return clip(payload.get("file_path", ""), 110)
    if tool in ("Agent", "Task"):
        return clip(payload.get("description", "") or payload.get("prompt", ""), 110)
    if tool in ("WebFetch", "WebSearch"):
        return clip(payload.get("url", "") or payload.get("query", ""), 110)
    if tool in ("Grep", "Glob", "Skill"):
        return clip(payload.get("pattern", "") or payload.get("skill", "") or payload.get("command", ""), 110)
    try:
        return clip(json.dumps(payload, ensure_ascii=False), 90)
    except (TypeError, ValueError):
        return ""


def describe_error(error):
    if isinstance(error, dict):
        error = error.get("message", "")
    return clip(str(error), 180)


# ------------------------------------------------------------------ hook entry

def hook_main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # a broken payload must never block the session
    if not isinstance(payload, dict):
        return 0

    event = payload.get("hook_event_name") or payload.get("hookEventName") or ""
    tool = payload.get("tool_name") or payload.get("toolName") or ""
    message = {
        "ev": event,
        "sid": clip(str(payload.get("session_id") or payload.get("sessionId") or ""), 8),
        "cwd": payload.get("cwd") or "",
        "tool": tool,
        "input": payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else None,
        "prompt": payload.get("prompt"),
        "reason": payload.get("reason"),
        "error": payload.get("error"),
        "interrupt": bool(payload.get("is_interrupt")),
        "stop_hook_active": bool(payload.get("stop_hook_active")),
        "response": payload.get("last_assistant_message"),
        "tool_calls": payload.get("tool_call_count") or payload.get("toolCallCount"),
        "source": payload.get("source"),
    }
    blob = json.dumps(message, ensure_ascii=False).encode("utf-8")
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        s.sendto(blob, SOCKET_PATH)  # connectionless: a stopped bridge is not an error
        s.close()
    except OSError:
        pass
    return 0  # empty output, exit 0: ZCode treats this as a pass-through hook


# ------------------------------------------------------------------ bridge

class Session:
    """What the device currently shows for the active session."""

    def __init__(self):
        self.reset("", "")

    def reset(self, sid, proj):
        self.sid, self.proj = sid, proj
        self.state = "idle"
        self.tool = ""
        self.msg = ""
        self.cur = ""
        self.tools = 0
        self.fails = 0
        self.todo_done = 0
        self.todo_total = 0
        self.turn_started = None
        self.done_at = None

    def elapsed(self):
        if self.state == "done" and self.done_at is not None:
            return max(0, int(self.done_at - (self.turn_started or self.done_at)))
        if self.turn_started is None:
            return 0
        return max(0, int(time.time() - self.turn_started))

    def frame(self):
        return {
            "state": self.state, "tool": self.tool, "msg": self.msg, "cur": self.cur,
            "proj": self.proj or "zcode", "sid": self.sid, "tools": self.tools,
            "fails": self.fails, "elapsed": self.elapsed(),
            "todo_done": self.todo_done, "todo_total": self.todo_total,
            "clock": clock_now(), "date": date_now(),
        }


class Bridge:
    def __init__(self, port):
        self.port = port
        self.ser = None
        self.sock = None
        self.session = Session()
        self.current_frame_line = ""

    def open_serial(self):
        import serial  # only needed by the bridge, keeps hook start-up fast

        ser = serial.Serial(self.port, 115200, timeout=0.1, write_timeout=1.0,
                            dsrdtr=False, rtscts=False)
        ser.dtr = True   # both asserted: the safe "run" combination
        ser.rts = True   # (opening the port resets the chip once: boot splash)
        time.sleep(0.3)
        ser.reset_input_buffer()
        self.ser = ser

    def send(self, line):
        try:
            self.ser.write((line + "\n").encode("utf-8"))
        except (serial.SerialException, OSError) as exc:
            log("send failed: %s" % exc)

    def push_frame(self):
        line = encode_frame(self.session.frame())
        if line == self.current_frame_line:
            return  # heartbeat: the device compares and skips identical content
        self.send(line)
        self.current_frame_line = line

    def handle_event(self, data):
        ev = data.get("ev", "")
        s = self.session
        cwd = data.get("cwd") or ""
        proj = os.path.basename(cwd.rstrip("/")) if cwd else s.proj

        if ev == "SessionStart":
            fresh = Session()
            fresh.reset(clip(str(data.get("sid") or ""), 8), proj)
            # a resume keeps counters; startup/clear start clean
            if data.get("source") in ("resume", "compact"):
                fresh.tools, fresh.fails = s.tools, s.fails
            self.session = fresh
            log("session start (%s) proj=%s" % (data.get("source") or "?", proj))
        elif ev == "UserPromptSubmit":
            s.reset(s.sid, proj)
            s.sid = clip(str(data.get("sid") or ""), 8)
            s.turn_started = time.time()
            s.state = "think"
            s.msg = clip(str(data.get("prompt") or ""))
            log("prompt: %s" % clip(str(data.get("prompt") or ""), 60))
        elif ev == "PreToolUse":
            tool = data.get("tool") or ""
            payload = data.get("input") or {}
            if tool == "TodoWrite" and isinstance(payload.get("todos"), list):
                todos = payload["todos"]
                s.todo_total = len(todos)
                s.todo_done = sum(1 for t in todos if isinstance(t, dict) and t.get("status") == "completed")
                s.cur = clip(next((t.get("content", "") for t in todos
                                   if isinstance(t, dict) and t.get("status") == "in_progress"), ""))
                log("plan %d/%d cur=%s" % (s.todo_done, s.todo_total, clip(s.cur, 40)))
            elif tool:
                s.tool = clip(tool, 18)
                s.msg = describe_input(tool, payload)
                if s.state in ("idle", "done", "perm", "err"):
                    s.turn_started = s.turn_started or time.time()
                s.state = "tool"
                s.done_at = None
        elif ev == "PermissionRequest":
            s.state = "perm"
            s.tool = clip(data.get("tool") or "", 18)
            s.msg = clip(str(data.get("reason") or describe_input(s.tool, data.get("input") or {})))
            log("permission: %s" % clip(s.msg, 60))
        elif ev == "PostToolUse":
            s.tools += 1
            if s.state == "tool":
                s.state = "think"  # the model continues with the tool result
        elif ev == "PostToolUseFailure":
            s.fails += 1
            s.state = "err"
            s.tool = clip(data.get("tool") or "", 18)
            s.msg = describe_error(data.get("error")) or "tool failed"
            log("tool failed: %s" % clip(s.msg, 60))
        elif ev == "Stop":
            s.state = "done"
            s.done_at = time.time()
            s.msg = clip(str(data.get("response") or ""))
            log("stop: tools=%d fails=%d elapsed=%ds" % (s.tools, s.fails, s.elapsed()))
        self.push_frame()

    def expire_done(self):
        s = self.session
        if s.state == "done" and s.done_at is not None and time.time() - s.done_at > DONE_SECONDS:
            s.state = "idle"
            s.msg = ""
            s.turn_started = None
            self.push_frame()

    def open_socket(self):
        try:
            os.unlink(SOCKET_PATH)
        except OSError:
            pass
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        sock.bind(SOCKET_PATH)
        sock.settimeout(0.25)
        os.chmod(SOCKET_PATH, 0o666)
        self.sock = sock

    def pump(self):
        """Socket + serial + timer loop."""
        reader_lines = b""
        while True:
            try:
                data, _ = self.sock.recvfrom(65536)
                try:
                    self.handle_event(json.loads(data.decode("utf-8")))
                except (ValueError, UnicodeDecodeError) as exc:
                    log("bad hook payload: %s" % exc)
            except socket.timeout:
                pass
            except ConnectionResetError:
                pass

            if self.ser is not None and self.ser.in_waiting:
                reader_lines += self.ser.read(max(1, self.ser.in_waiting))
                while b"\n" in reader_lines:
                    raw, reader_lines = reader_lines.split(b"\n", 1)
                    line = raw.decode("utf-8", "replace").strip()
                    if line:
                        log("dev: %s" % clip(line, 160))

            if self.session.state != "done":  # DONE keeps its own expiry timer below
                self.push_frame()
            self.expire_done()
            time.sleep(HEARTBEAT_SECONDS / 4)

    def run(self):
        if os.path.exists(PID_PATH):
            try:
                os.kill(int(open(PID_PATH).read().strip()), 0)
                sys.exit("bridge already running (pid in %s)" % PID_PATH)
            except (ValueError, ProcessLookupError, PermissionError):
                pass
        self.open_serial()
        self.open_socket()
        with open(PID_PATH, "w") as f:
            f.write(str(os.getpid()))
        log("bridge up on %s" % self.port)
        try:
            self.pump()
        except KeyboardInterrupt:
            pass
        finally:
            log("bridge stopped")
            os.unlink(SOCKET_PATH)
            os.unlink(PID_PATH)


def find_port():
    for candidate in sorted(os.listdir("/dev")):
        if candidate.startswith("cu.usbmodem"):
            return "/dev/" + candidate
    return None


def status_main():
    running = False
    if os.path.exists(PID_PATH):
        try:
            os.kill(int(open(PID_PATH).read().strip()), 0)
            running = True
        except (ValueError, ProcessLookupError, PermissionError):
            pass
    print("bridge: %s" % ("running" if running else "not running"))
    print("socket: %s" % ("present" if os.path.exists(SOCKET_PATH) else "absent"))
    return 0


def send_main(line):
    """One raw line to the device, for testing. Refuses while the bridge owns the port."""
    if os.path.exists(PID_PATH):
        try:
            os.kill(int(open(PID_PATH).read().strip()), 0)
            sys.exit("bridge is running and owns the port; stop it first (kill $(cat %s))" % PID_PATH)
        except (ValueError, ProcessLookupError, PermissionError):
            pass
    import serial

    port = find_port()
    if not port:
        sys.exit("no ESP32 serial port found")
    ser = serial.Serial(port, 115200, timeout=0.5, dsrdtr=False, rtscts=False)
    ser.dtr = True   # safe "run" line states
    ser.rts = True   # (opening resets the chip once)
    time.sleep(0.3)
    ser.reset_input_buffer()
    ser.write((line + "\n").encode("utf-8"))
    deadline = time.time() + 1.5
    buf = b""
    while time.time() < deadline:
        buf += ser.read(256)
        if b"\n" in buf:
            break
    ser.close()
    print(buf.decode("utf-8", "replace").strip())
    return 0


def main():
    argv = sys.argv[1:]
    cmd = argv[0] if argv else "bridge"
    if cmd == "hook":
        return hook_main()
    if cmd == "bridge":
        port = None
        if "--port" in argv:
            port = argv[argv.index("--port") + 1]
        port = port or find_port()
        if not port:
            sys.exit("no ESP32 serial port found (looked for /dev/cu.usbmodem*)")
        bridge = Bridge(port)
        if "--daemon" in argv:
            if os.fork() > 0:
                time.sleep(0.5)
                return 0
            os.setsid()
        bridge.run()
        return 0
    if cmd == "send":
        return send_main(argv[1] if len(argv) > 1 else "ZTV1 C ping")
    if cmd == "status":
        return status_main()
    sys.exit(__doc__)


if __name__ == "__main__":
    sys.exit(main())
