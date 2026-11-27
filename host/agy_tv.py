#!/usr/bin/env python3
"""AGY TV host side: serial bridge daemon, AGY hook handler, and transcript tracker.

    agy_tv.py bridge [--port /dev/ttyACM0] [--daemon] [--no-watch] [--dry-run]
    agy_tv.py hook                        # runs from AGY hooks.json, JSON on stdin/stdout
    agy_tv.py send "ZTV1 C ping"          # raw line to the device, for testing
    agy_tv.py status                      # is the bridge running?

The bridge owns the serial port for the whole session: hooks are short-lived
processes, and re-opening the USB Serial/JTAG port per event would reset the
ESP32 (DTR/RTS drive its EN/BOOT lines). Hooks forward their event over a local
AF_UNIX datagram socket and return valid pass-through JSON immediately.

In addition to real-time AGY hooks (SessionStart, PreInvocation, PreToolUse,
PostToolUse, PostInvocation, Stop), the bridge reads the active conversation's
transcript.jsonl to enrich the display with the resolved model name, context and
output token counts, thinking summaries, and final responses. If no hooks have
fired yet, it also passively watches the newest active AGY transcript under
~/.gemini/{antigravity-cli,antigravity,antigravity-ide}/brain/.

Hook failures are silent by design: a status gadget must never block AGY.
"""
import glob
import json
import os
import re
import socket
import sys
import time

SOCKET_PATH = "/tmp/agy-tv.bridge.sock"
LOG_PATH = "/tmp/agy-tv.log"
PID_PATH = "/tmp/agy-tv.bridge.pid"

PROTO = "ZTV1"
DONE_SECONDS = 30  # keep in sync with CONFIG_ZTV_UI_DONE_SECONDS
HEARTBEAT_SECONDS = 2.0
HOOK_PRIORITY_SECONDS = 10.0  # hooks own state while firing; passive watch backs off
WATCH_ACTIVE_WINDOW = 180.0   # only auto-follow transcripts modified recently
MSG_MAX_BYTES = 180           # keeps total line < 640B for v1 firmware compatibility

AGY_APP_DIRS = (
    os.path.expanduser("~/.gemini/antigravity-cli"),
    os.path.expanduser("~/.gemini/antigravity"),
    os.path.expanduser("~/.gemini/antigravity-ide"),
)

SHORT_TOOL_NAMES = {
    "run_command": "run_command",
    "read_file": "read_file",
    "view_file": "view_file",
    "write_file": "write_file",
    "write_to_file": "write_file",
    "edit_file": "edit_file",
    "replace_file_content": "edit_file",
    "multi_replace_file_content": "multi_edit",
    "grep_search": "grep",
    "find_by_name": "find_files",
    "list_dir": "list_dir",
    "invoke_subagent": "subagent",
    "manage_subagents": "subagents",
    "define_subagent": "def_agent",
    "manage_task": "task",
    "run_workflow": "workflow",
    "search_web": "web_search",
    "read_url_content": "read_url",
    "ask_question": "ask_question",
    "send_message": "message",
    "schedule": "schedule",
    "swarm": "swarm",
}

USER_REQ_RE = re.compile(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.DOTALL)


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
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    text = "".join(c if c >= " " and c != '"' else " " for c in text)
    raw = text.encode("utf-8")
    if len(raw) > limit:
        raw = raw[:limit]
        while raw and (raw[-1] & 0xC0) == 0x80:  # do not cut a multi-byte character in half
            raw = raw[:-1]
        raw = raw.rstrip()
    return raw.decode("utf-8", "ignore")


def quote(text):
    return '"%s"' % (text or "")


def shorten_model(model):
    m = (model or "").strip()
    if not m or m.lower() == "auto":
        return ""
    if m.startswith("models/"):
        m = m[len("models/"):]
    return clip(m, 26)


def shorten_tool(tool):
    t = (tool or "").strip()
    if t in SHORT_TOOL_NAMES:
        return SHORT_TOOL_NAMES[t]
    return clip(t, 18)


def encode_frame(frame):
    parts = ["%s S st=%s" % (PROTO, frame["state"])]
    parts.append("tool=%s" % quote(clip(frame.get("tool", ""), 18)))
    parts.append("n=%d f=%d el=%d todo=%d/%d" % (
        frame.get("tools", 0), frame.get("fails", 0), frame.get("elapsed", 0),
        frame.get("todo_done", 0), frame.get("todo_total", 0),
    ))
    parts.append("cur=%s" % quote(clip(frame.get("cur", ""), 100)))
    parts.append("msg=%s" % quote(clip(frame.get("msg", ""), MSG_MAX_BYTES)))
    parts.append("proj=%s" % quote(clip(frame.get("proj", ""), 28)))
    parts.append("sid=%s" % quote(clip(frame.get("sid", ""), 8)))
    parts.append("clk=%s" % quote(frame.get("clock", "")))
    parts.append("date=%s" % quote(clip(frame.get("date", ""), 18)))
    if frame.get("model"):
        parts.append("model=%s" % quote(clip(frame["model"], 26)))
    if frame.get("inv"):
        parts.append("inv=%d" % int(frame["inv"]))
    if frame.get("step"):
        parts.append("step=%d" % int(frame["step"]))
    if frame.get("ctx"):
        parts.append("ctx=%d" % int(frame["ctx"]))
    if frame.get("out"):
        parts.append("out=%d" % int(frame["out"]))
    if frame.get("sub"):
        parts.append("sub=%d" % int(frame["sub"]))
    if frame.get("bg"):
        parts.append("bg=%d" % int(frame["bg"]))
    return " ".join(parts)


def clock_now():
    return time.strftime("%H:%M:%S")


def date_now():
    return time.strftime("%Y-%m-%d %a").upper()[:18]


def maybe_unquote_json(val):
    """Transcript tool_call args sometimes store JSON-encoded strings."""
    if isinstance(val, str) and len(val) >= 2 and val[0] in ('"', "[", "{"):
        try:
            return json.loads(val)
        except ValueError:
            return val
    return val


def normalize_args(args):
    if isinstance(args, str):
        parsed = maybe_unquote_json(args)
        if isinstance(parsed, dict):
            args = parsed
        else:
            return {}
    if not isinstance(args, dict):
        return {}
    return {k: maybe_unquote_json(v) for k, v in args.items()}


def extract_user_prompt(raw):
    text = str(raw or "")
    m = USER_REQ_RE.search(text)
    if m:
        text = m.group(1)
    return clip(text, MSG_MAX_BYTES)


def extract_steps(args):
    """Parse AGY update_steps (or legacy TodoWrite) payload -> (done, total, current_title)."""
    if not isinstance(args, dict):
        return None
    steps = args.get("steps")
    if not isinstance(steps, list):
        steps = args.get("todos")
    if not isinstance(steps, list):
        return None

    total = len(steps)
    done = 0
    in_prog = []
    failed = []
    pending = []
    for item in steps:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "").lower()
        title = str(item.get("title") or item.get("content") or "").strip()
        if status in ("done", "completed", "skipped"):
            done += 1
        elif status == "in_progress":
            if title:
                in_prog.append(title)
        elif status == "failed":
            if title:
                failed.append(title)
        elif title:
            pending.append(title)

    cur = ""
    if in_prog:
        cur = in_prog[0] if len(in_prog) == 1 else "%s (+%d)" % (in_prog[0], len(in_prog) - 1)
    elif failed:
        cur = "FAILED: %s" % failed[0]
    elif pending:
        cur = pending[0]
    elif total > 0 and done == total:
        cur = "All %d steps done" % total
    return done, total, clip(cur, 100)


def short_path(path):
    p = str(path or "").strip()
    if not p:
        return ""
    parts = [x for x in p.replace("\\", "/").split("/") if x]
    if len(parts) <= 2:
        return p
    return "/".join(parts[-2:])


def describe_input(tool, payload):
    """One crisp line summarizing what an AGY (or legacy) tool call is doing."""
    args = normalize_args(payload)
    if not args:
        return clip(str(payload or ""), 110)

    if tool in ("run_command", "Bash"):
        summary = args.get("toolSummary") or ""
        cmd = args.get("CommandLine") or args.get("command") or ""
        if summary and cmd:
            return clip("%s: %s" % (summary, cmd), 140)
        return clip(cmd or summary, 140)

    if tool in ("read_file", "view_file", "write_file", "write_to_file", "edit_file",
                "replace_file_content", "multi_replace_file_content",
                "Edit", "Write", "Read", "NotebookEdit"):
        path = (args.get("Path") or args.get("AbsolutePath") or args.get("TargetFile")
                or args.get("file_path") or "")
        s = short_path(path)
        start = args.get("StartLine")
        end = args.get("EndLine")
        if start and end:
            s = "%s:%s-%s" % (s, start, end)
        elif start:
            s = "%s:%s" % (s, start)
        return clip(s, 110)

    if tool in ("grep_search", "Grep"):
        q = args.get("Query") or args.get("pattern") or ""
        where = short_path(args.get("SearchPath") or args.get("path") or "")
        return clip(("%s in %s" % (q, where)) if where else q, 110)

    if tool in ("find_by_name", "Glob"):
        pat = args.get("Pattern") or args.get("pattern") or ""
        where = short_path(args.get("SearchDirectory") or args.get("path") or "")
        return clip(("%s in %s" % (pat, where)) if where else pat, 110)

    if tool in ("list_dir",):
        return clip(short_path(args.get("DirectoryPath") or ""), 110)

    if tool in ("invoke_subagent", "Agent", "Task"):
        role = args.get("Role") or args.get("TypeName") or ""
        desc = args.get("description") or args.get("Prompt") or args.get("prompt") or ""
        return clip(("%s: %s" % (role, desc)) if role and desc else (role or desc), 140)

    if tool in ("manage_subagents", "manage_task"):
        act = args.get("Action") or ""
        tid = args.get("TaskId") or ""
        return clip(("%s %s" % (act, tid)).strip(), 110)

    if tool == "run_workflow":
        return clip(args.get("Description") or args.get("ScriptPath") or "workflow", 130)

    if tool == "ask_question":
        qs = args.get("questions")
        if isinstance(qs, list) and qs and isinstance(qs[0], dict):
            return clip(qs[0].get("question") or "Waiting for your answer", 150)
        return "Waiting for your answer"

    if tool in ("search_web", "WebSearch"):
        return clip(args.get("query") or "", 110)

    if tool in ("read_url_content", "WebFetch"):
        return clip(args.get("Url") or args.get("url") or "", 110)

    if tool == "schedule":
        when = args.get("CronExpression") or ("%ss" % args["DurationSeconds"] if args.get("DurationSeconds") else "")
        prompt = args.get("Prompt") or ""
        return clip(("%s %s" % (when, prompt)).strip(), 110)

    if tool == "swarm":
        act = args.get("action") or ""
        name = args.get("name") or args.get("title") or ""
        brief = args.get("brief") or ""
        return clip(" ".join(x for x in (act, name, brief) if x), 130)

    if tool == "send_message":
        return clip(args.get("Message") or "", 110)

    try:
        return clip(json.dumps(args, ensure_ascii=False), 110)
    except (TypeError, ValueError):
        return ""


def describe_error(error):
    if isinstance(error, dict):
        error = error.get("message") or error.get("error") or str(error)
    return clip(str(error or ""), 170)


def infer_project_from_paths(cwd, workspace_paths, fallback="agy"):
    if cwd:
        base = os.path.basename(str(cwd).rstrip("/\\"))
        if base:
            return clip(base, 28)
    if isinstance(workspace_paths, list):
        for p in workspace_paths:
            if p:
                base = os.path.basename(str(p).rstrip("/\\"))
                if base:
                    return clip(base, 28)
    return fallback


# ------------------------------------------------------------------ AGY hook entry

def hook_response(event):
    """Exact pass-through JSON stdout required by the AGY hooks contract."""
    if event == "PreToolUse":
        # "allow" delegates to the next hook or default policy (never auto-approves).
        return {"decision": "allow"}
    if event == "Stop":
        return {"decision": "stop"}
    return {}


def hook_main():
    event = ""
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.stdout.write("{}\n")
        return 0
    if not isinstance(payload, dict):
        sys.stdout.write("{}\n")
        return 0

    event = str(payload.get("hookEventName") or payload.get("hook_event_name") or "")
    tool_call = payload.get("toolCall") if isinstance(payload.get("toolCall"), dict) else {}
    tool_name = str(tool_call.get("name") or payload.get("tool_name") or payload.get("toolName") or "")
    tool_args = tool_call.get("args")
    if not isinstance(tool_args, dict):
        tool_args = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else None

    ws = payload.get("workspacePaths")
    if not isinstance(ws, list):
        ws = []

    message = {
        "ev": event,
        "cid": str(payload.get("conversationId") or payload.get("session_id") or payload.get("sessionId") or ""),
        "cwd": payload.get("cwd") or "",
        "ws": ws[:4],
        "model": payload.get("modelName") or "",
        "prompt": payload.get("lastUserInput") or payload.get("prompt") or "",
        "transcript": payload.get("transcriptPath") or "",
        "tool": tool_name,
        "input": tool_args,
        "step_idx": payload.get("stepIdx"),
        "inv_num": payload.get("invocationNum"),
        "init_steps": payload.get("initialNumSteps"),
        "error": payload.get("error"),
        "reason": payload.get("terminationReason") or payload.get("reason") or "",
        "fully_idle": payload.get("fullyIdle"),
        "exec_num": payload.get("executionNum"),
        "response": payload.get("last_assistant_message") or "",
        "source": payload.get("source") or "",
    }
    try:
        blob = json.dumps(message, ensure_ascii=False).encode("utf-8")
        if len(blob) > 60000:
            # Trim huge tool inputs (e.g. write_file Content) so AF_UNIX DGRAM never fails with EMSGSIZE
            message["input"] = {
                k: (v[:300] if isinstance(v, str) and len(v) > 300 else v)
                for k, v in (tool_args or {}).items()
                if k not in ("Content", "Replacement", "Target", "Script")
            }
            blob = json.dumps(message, ensure_ascii=False).encode("utf-8")
        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        s.sendto(blob, SOCKET_PATH)  # connectionless: a stopped bridge is not an error
        s.close()
    except OSError:
        pass

    sys.stdout.write(json.dumps(hook_response(event)) + "\n")
    sys.stdout.flush()
    return 0


# ------------------------------------------------------------------ transcript enrichment & fallback

class TranscriptState:
    """Incrementally parses an AGY transcript.jsonl for model, tokens, steps, and replies."""

    def __init__(self, path):
        self.path = path
        self.inode = None
        self.offset = 0
        self.model = ""
        self.ctx_tokens = 0
        self.out_tokens = 0
        self.step_index = 0
        self.inv_count = 0
        self.tool_count = 0
        self.fail_count = 0
        self.last_prompt = ""
        self.last_thinking = ""
        self.last_reply = ""
        self.last_tool = ""
        self.last_tool_msg = ""
        self.last_error = ""
        self.todo_done = 0
        self.todo_total = 0
        self.todo_cur = ""
        self.running_tasks = 0
        self.last_entry_type = ""
        self.last_entry_had_tools = False
        self.last_entry_status = ""
        self.mtime = 0.0

    def reset_for_file(self, inode):
        self.inode = inode
        self.offset = 0
        self.out_tokens = 0
        self.inv_count = 0
        self.tool_count = 0
        self.fail_count = 0
        self.running_tasks = 0

    def poll(self):
        """Read any newly appended JSONL lines. Returns True if new entries were parsed."""
        try:
            st = os.stat(self.path)
        except OSError:
            return False
        self.mtime = st.st_mtime
        if self.inode != st.st_ino or st.st_size < self.offset:
            self.reset_for_file(st.st_ino)
        if st.st_size == self.offset:
            return False

        parsed_any = False
        try:
            with open(self.path, "rb") as f:
                f.seek(self.offset)
                raw = f.read()
                if not raw:
                    return False
                # Only advance past complete lines so a half-written line is retried next tick.
                last_nl = raw.rfind(b"\n")
                if last_nl < 0:
                    return False
                chunk = raw[:last_nl + 1]
                self.offset += len(chunk)
        except OSError:
            return False

        for line_bytes in chunk.splitlines():
            if not line_bytes:
                continue
            try:
                entry = json.loads(line_bytes.decode("utf-8", "replace"))
            except ValueError:
                continue
            if isinstance(entry, dict):
                self._ingest(entry)
                parsed_any = True
        return parsed_any

    def _ingest(self, e):
        etype = str(e.get("type") or "")
        status = str(e.get("status") or "")
        idx = e.get("step_index")
        if isinstance(idx, int) and idx > self.step_index:
            self.step_index = idx

        self.last_entry_type = etype
        self.last_entry_status = status

        if etype == "USER_INPUT":
            prompt = extract_user_prompt(e.get("content") or "")
            if prompt:
                self.last_prompt = prompt
            self.last_entry_had_tools = False
            self.last_error = ""
            return

        if etype == "PLANNER_RESPONSE":
            self.inv_count += 1
            model = shorten_model(e.get("model") or "")
            if model:
                self.model = model
            inp = e.get("input_tokens") or 0
            cache = e.get("cache_read_tokens") or 0
            out = e.get("output_tokens") or 0
            if isinstance(inp, int) and isinstance(cache, int) and (inp + cache) > 0:
                self.ctx_tokens = inp + cache
            if isinstance(out, int) and out > 0:
                self.out_tokens += out
            thinking = str(e.get("thinking") or "").strip()
            if thinking:
                self.last_thinking = clip(thinking.splitlines()[0], 100)
            content = str(e.get("content") or "").strip()
            if content:
                self.last_reply = clip(content, MSG_MAX_BYTES)
            tcs = e.get("tool_calls")
            self.last_entry_had_tools = isinstance(tcs, list) and len(tcs) > 0
            if isinstance(tcs, list):
                for tc in tcs:
                    if not isinstance(tc, dict):
                        continue
                    tname = str(tc.get("name") or "")
                    targs = normalize_args(tc.get("args"))
                    if tname in ("update_steps", "TodoWrite"):
                        res = extract_steps(targs)
                        if res:
                            self.todo_done, self.todo_total, self.todo_cur = res
                    elif tname:
                        self.last_tool = tname
                        self.last_tool_msg = describe_input(tname, targs)
            return

        if etype == "GENERIC":
            self.tool_count += 1
            if status == "RUNNING":
                self.running_tasks += 1
            elif status == "ERROR":
                self.fail_count += 1
                err = describe_error(e.get("error") or e.get("content") or "tool error")
                if err:
                    self.last_error = err


def find_newest_transcript():
    """Find the most recently modified active AGY transcript.jsonl across AGY surfaces."""
    best_path = None
    best_mtime = 0.0
    cutoff = time.time() - WATCH_ACTIVE_WINDOW
    for base in AGY_APP_DIRS:
        pattern = os.path.join(base, "brain", "*", ".system_generated", "logs", "transcript.jsonl")
        for path in glob.glob(pattern):
            try:
                mt = os.path.getmtime(path)
            except OSError:
                continue
            if mt >= cutoff and mt > best_mtime:
                best_mtime = mt
                best_path = path
    return best_path


# ------------------------------------------------------------------ bridge state

class Session:
    """What the device currently shows for an AGY conversation."""

    def __init__(self, cid="", proj="agy"):
        self.reset(cid, proj)

    def reset(self, cid, proj):
        self.cid = cid
        self.sid = clip(cid, 8)
        self.proj = proj or "agy"
        self.model = ""
        self.transcript_path = ""
        self.state = "idle"
        self.tool = ""
        self.msg = ""
        self.cur = ""
        self.last_prompt = ""
        self.tools = 0
        self.fails = 0
        self.todo_done = 0
        self.todo_total = 0
        self.inv_count = 0
        self.step_index = 0
        self.ctx_tokens = 0
        self.out_tokens = 0
        self.sub_count = 0
        self.bg_count = 0
        self.turn_started = None
        self.done_at = None

    def start_new_turn(self, prompt, now=None):
        now = now or time.time()
        self.turn_started = now
        self.done_at = None
        self.tools = 0
        self.fails = 0
        self.inv_count = 0
        self.sub_count = 0
        self.bg_count = 0
        self.tool = ""
        if prompt:
            self.last_prompt = prompt

    def elapsed(self):
        if self.state == "done" and self.done_at is not None:
            return max(0, int(self.done_at - (self.turn_started or self.done_at)))
        if self.turn_started is None:
            return 0
        return max(0, int(time.time() - self.turn_started))

    def frame(self):
        return {
            "state": self.state,
            "tool": self.tool,
            "msg": self.msg,
            "cur": self.cur,
            "proj": self.proj or "agy",
            "sid": self.sid,
            "model": self.model,
            "tools": self.tools,
            "fails": self.fails,
            "elapsed": self.elapsed(),
            "todo_done": self.todo_done,
            "todo_total": self.todo_total,
            "inv": self.inv_count,
            "step": self.step_index,
            "ctx": self.ctx_tokens,
            "out": self.out_tokens,
            "sub": self.sub_count,
            "bg": self.bg_count,
            "clock": clock_now(),
            "date": date_now(),
        }


class Bridge:
    def __init__(self, port, watch=True, dry_run=False):
        self.port = port
        self.watch = watch
        self.dry_run = dry_run
        self.ser = None
        self.sock = None
        self.sessions = {}
        self.active_cid = ""
        self.session = Session()
        self.transcripts = {}  # path -> TranscriptState
        self.current_frame_line = ""
        self.last_hook_ts = 0.0
        self.last_watch_scan = 0.0
        self.last_reconnect_try = 0.0

    def get_session(self, cid, proj):
        key = cid or "default"
        sess = self.sessions.get(key)
        if sess is None:
            sess = Session(cid, proj)
            self.sessions[key] = sess
        elif proj:
            sess.proj = proj
        self.active_cid = key
        self.session = sess
        return sess

    def get_transcript(self, path):
        if not path:
            return None
        tr = self.transcripts.get(path)
        if tr is None:
            tr = TranscriptState(path)
            self.transcripts[path] = tr
        return tr

    def enrich_from_transcript(self, s):
        if not s.transcript_path:
            return None
        tr = self.get_transcript(s.transcript_path)
        if tr is None:
            return None
        tr.poll()
        if tr.model:
            s.model = tr.model
        if tr.ctx_tokens > 0:
            s.ctx_tokens = tr.ctx_tokens
        if tr.out_tokens > 0:
            s.out_tokens = tr.out_tokens
        if tr.step_index > s.step_index:
            s.step_index = tr.step_index
        # Recover steps if update_steps ran before the bridge started
        if s.todo_total == 0 and tr.todo_total > 0:
            s.todo_done = tr.todo_done
            s.todo_total = tr.todo_total
            s.cur = tr.todo_cur
        return tr

    def open_serial(self):
        if self.dry_run:
            return True
        import serial  # only needed by the bridge, keeps hook start-up fast

        port = self.port or find_port()
        if not port:
            return False
        try:
            ser = serial.Serial(port, 115200, timeout=0.1, write_timeout=1.0,
                                dsrdtr=False, rtscts=False)
            ser.dtr = True   # both asserted: the safe "run" combination
            ser.rts = True   # (opening the port resets the chip once: boot splash)
            time.sleep(0.3)
            ser.reset_input_buffer()
            self.ser = ser
            self.port = port
            return True
        except Exception as exc:
            log("open_serial(%s) failed: %s" % (port, exc))
            self.ser = None
            return False

    def close_serial(self):
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None

    def probe_device(self):
        """One ping after start-up, so the log shows the device is alive."""
        if self.dry_run or self.ser is None:
            return self.dry_run
        deadline = time.time() + 4  # the panel takes ~1.3 s to boot
        while time.time() < deadline:
            self.send("ZTV1 C ping")
            time.sleep(1.0)
            if self.ser is not None and self.ser.in_waiting:
                return True
        return False

    def send(self, line):
        if self.dry_run:
            print(line, flush=True)
            return
        if self.ser is None:
            return
        try:
            self.ser.write((line + "\n").encode("utf-8"))
        except Exception as exc:
            log("send failed (%s), will reconnect" % exc)
            self.close_serial()

    def push_frame(self):
        line = encode_frame(self.session.frame())
        if line == self.current_frame_line:
            return  # heartbeat: the device compares and skips identical content
        self.send(line)
        self.current_frame_line = line

    def handle_event(self, data):
        ev = str(data.get("ev") or "")
        cid = str(data.get("cid") or "")
        proj = infer_project_from_paths(data.get("cwd"), data.get("ws"), fallback=self.session.proj or "agy")
        s = self.get_session(cid, proj)
        self.last_hook_ts = time.time()

        model = shorten_model(data.get("model") or "")
        if model:
            s.model = model
        tr_path = str(data.get("transcript") or "")
        if tr_path:
            s.transcript_path = tr_path

        step_idx = data.get("step_idx")
        if isinstance(step_idx, int) and step_idx > s.step_index:
            s.step_index = step_idx
        init_steps = data.get("init_steps")
        if isinstance(init_steps, int) and init_steps > s.step_index:
            s.step_index = init_steps

        prompt = extract_user_prompt(data.get("prompt") or "")

        if ev == "SessionStart":
            s.reset(cid, proj)
            if model:
                s.model = model
            if tr_path:
                s.transcript_path = tr_path
            self.enrich_from_transcript(s)
            log("session start cid=%s proj=%s model=%s" % (s.sid, s.proj, s.model or "auto"))

        elif ev == "UserPromptSubmit":
            # Legacy ZCode compatibility
            s.start_new_turn(prompt)
            s.state = "think"
            s.msg = prompt
            log("prompt: %s" % clip(prompt, 60))

        elif ev == "PreInvocation":
            inv_num = data.get("inv_num")
            new_turn = (
                (isinstance(inv_num, int) and inv_num == 0)
                or s.state in ("idle", "done")
                or (prompt and prompt != s.last_prompt and s.state == "idle")
            )
            if new_turn:
                s.start_new_turn(prompt)
            if isinstance(inv_num, int):
                s.inv_count = max(s.inv_count, inv_num + 1)
            else:
                s.inv_count += 1
            s.state = "think"
            s.done_at = None
            tr = self.enrich_from_transcript(s)
            if prompt:
                s.last_prompt = prompt
                s.msg = prompt
            elif tr and tr.last_prompt:
                s.msg = tr.last_prompt
            if s.todo_total == 0 and tr and tr.last_thinking:
                s.cur = tr.last_thinking
            log("pre-inv #%d cid=%s model=%s ctx=%d" % (s.inv_count, s.sid, s.model, s.ctx_tokens))

        elif ev == "PostInvocation":
            tr = self.enrich_from_transcript(s)
            if s.state == "think" and s.todo_total == 0 and tr and tr.last_thinking:
                s.cur = tr.last_thinking

        elif ev == "PreToolUse":
            tool = str(data.get("tool") or "")
            payload = data.get("input") or {}
            if tool in ("update_steps", "TodoWrite"):
                parsed = extract_steps(normalize_args(payload))
                if parsed:
                    s.todo_done, s.todo_total, s.cur = parsed
                    if s.state in ("idle", "done"):
                        s.turn_started = s.turn_started or time.time()
                        s.state = "think"
                        s.done_at = None
                    log("steps %d/%d cur=%s" % (s.todo_done, s.todo_total, clip(s.cur, 48)))
            elif tool == "ask_question":
                s.turn_started = s.turn_started or time.time()
                s.state = "perm"
                s.done_at = None
                s.tool = "ask_question"
                s.msg = describe_input(tool, payload)
                log("ask_question: %s" % clip(s.msg, 60))
            elif tool:
                if tool in ("invoke_subagent", "run_workflow"):
                    s.sub_count += 1
                elif tool == "run_command" and isinstance(payload, dict) and payload.get("IsDaemon"):
                    s.bg_count += 1
                s.tool = shorten_tool(tool)
                s.msg = describe_input(tool, payload)
                if s.state in ("idle", "done", "perm", "err"):
                    s.turn_started = s.turn_started or time.time()
                if s.todo_total == 0 and not s.cur and (prompt or s.last_prompt):
                    s.cur = clip(prompt or s.last_prompt, 100)
                s.state = "tool"
                s.done_at = None
                log("tool %s: %s" % (s.tool, clip(s.msg, 60)))

        elif ev == "PermissionRequest":
            # Legacy ZCode compatibility
            s.state = "perm"
            s.tool = shorten_tool(data.get("tool") or "")
            s.msg = clip(str(data.get("reason") or describe_input(s.tool, data.get("input") or {})))
            log("permission: %s" % clip(s.msg, 60))

        elif ev == "PostToolUse":
            tool = str(data.get("tool") or "")
            err = data.get("error")
            if tool not in ("update_steps", "TodoWrite"):
                s.tools += 1
            if tool == "manage_subagents":
                act = str((data.get("input") or {}).get("Action") or "")
                if act == "kill_all":
                    s.sub_count = 0
                elif act == "kill" and s.sub_count > 0:
                    s.sub_count -= 1
            elif tool == "manage_task":
                act = str((data.get("input") or {}).get("Action") or "")
                if act == "kill" and s.bg_count > 0:
                    s.bg_count -= 1

            self.enrich_from_transcript(s)
            if err:
                s.fails += 1
                s.state = "err"
                s.tool = shorten_tool(tool)
                s.msg = describe_error(err) or "tool failed"
                log("tool error (%s): %s" % (s.tool, clip(s.msg, 60)))
            elif s.state in ("tool", "perm"):
                s.state = "think"

        elif ev == "PostToolUseFailure":
            # Legacy ZCode compatibility
            s.fails += 1
            s.state = "err"
            s.tool = shorten_tool(data.get("tool") or "")
            s.msg = describe_error(data.get("error")) or "tool failed"
            log("tool failed: %s" % clip(s.msg, 60))

        elif ev == "Stop":
            tr = self.enrich_from_transcript(s)
            reason = str(data.get("reason") or "")
            err = describe_error(data.get("error") or "")
            fully_idle = data.get("fully_idle")
            if fully_idle is True:
                s.bg_count = 0
            elif fully_idle is False and s.bg_count == 0:
                s.bg_count = 1

            s.done_at = time.time()
            if err or reason.upper() == "ERROR":
                s.state = "err"
                s.tool = "stop"
                s.msg = err or reason or "execution error"
            else:
                s.state = "done"
                reply_text = clip(data.get("response") or "")
                if not reply_text and tr and tr.last_reply:
                    reply_text = tr.last_reply
                if not reply_text:
                    if reason.upper() == "USER_CANCELED":
                        reply_text = "Canceled by user"
                    elif reason and reason.upper() not in ("NO_TOOL_CALL", "MODEL_STOP"):
                        reply_text = "Stopped: %s" % reason
                    elif fully_idle is False:
                        reply_text = "Turn finished (background tasks still active)"
                    else:
                        reply_text = "Turn completed"
                s.msg = reply_text
            log("stop (%s): tools=%d fails=%d ctx=%d el=%ds" % (
                reason or "ok", s.tools, s.fails, s.ctx_tokens, s.elapsed()))

        self.push_frame()

    def poll_passive_transcript(self):
        """Fallback when hooks are not installed/active: watch newest AGY transcript."""
        if not self.watch:
            return
        now = time.time()
        if now - self.last_hook_ts < HOOK_PRIORITY_SECONDS:
            return
        if now - self.last_watch_scan < 1.5:
            return
        self.last_watch_scan = now

        path = find_newest_transcript()
        if not path:
            return
        tr = self.get_transcript(path)
        changed = tr.poll()
        if not changed and self.session.transcript_path == path:
            # Check whether a running transcript went quiet -> likely finished or idle
            return

        # Extract conversation id from .../brain/<cid>/.system_generated/logs/transcript.jsonl
        parts = path.replace("\\", "/").split("/")
        cid = parts[-4] if len(parts) >= 4 else "watch"
        s = self.get_session(cid, self.session.proj or "agy")
        s.transcript_path = path
        if tr.model:
            s.model = tr.model
        s.ctx_tokens = tr.ctx_tokens
        s.out_tokens = tr.out_tokens
        s.step_index = tr.step_index
        s.inv_count = tr.inv_count
        s.tools = tr.tool_count
        s.fails = tr.fail_count
        s.todo_done = tr.todo_done
        s.todo_total = tr.todo_total
        if tr.todo_total > 0:
            s.cur = tr.todo_cur
        elif tr.last_thinking:
            s.cur = tr.last_thinking
        elif tr.last_prompt:
            s.cur = tr.last_prompt

        age = max(0.0, now - tr.mtime)
        if age > 45.0:
            s.state = "idle"
            s.msg = ""
            s.turn_started = None
        elif tr.last_entry_type == "PLANNER_RESPONSE" and not tr.last_entry_had_tools and tr.last_entry_status == "DONE":
            s.state = "done"
            s.done_at = tr.mtime
            s.msg = tr.last_reply or "Turn completed"
        elif tr.last_entry_type == "GENERIC" and tr.last_entry_status == "ERROR":
            s.state = "err"
            s.tool = shorten_tool(tr.last_tool or "tool")
            s.msg = tr.last_error or "tool failed"
            s.turn_started = s.turn_started or tr.mtime
        elif tr.last_entry_type == "PLANNER_RESPONSE" and tr.last_entry_had_tools:
            s.state = "perm" if tr.last_tool == "ask_question" else "tool"
            s.tool = shorten_tool(tr.last_tool)
            s.msg = tr.last_tool_msg or tr.last_prompt
            s.turn_started = s.turn_started or tr.mtime
            s.done_at = None
        else:
            s.state = "think"
            s.msg = tr.last_prompt or tr.last_thinking or "AGY working..."
            s.turn_started = s.turn_started or tr.mtime
            s.done_at = None
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
        """Socket + serial + transcript + timer loop."""
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

            if not self.dry_run and self.ser is None:
                now = time.time()
                if now - self.last_reconnect_try >= 2.0:
                    self.last_reconnect_try = now
                    if self.open_serial():
                        log("serial reconnected on %s" % self.port)
                        self.current_frame_line = ""  # force re-push

            if self.ser is not None:
                try:
                    if self.ser.in_waiting:
                        reader_lines += self.ser.read(max(1, self.ser.in_waiting))
                        while b"\n" in reader_lines:
                            raw, reader_lines = reader_lines.split(b"\n", 1)
                            line = raw.decode("utf-8", "replace").strip()
                            if line:
                                log("dev: %s" % clip(line, 160))
                except Exception as exc:
                    log("serial read error (%s), will reconnect" % exc)
                    self.close_serial()

            self.poll_passive_transcript()
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
        if not self.dry_run and not self.open_serial():
            log("no ESP32 serial port yet; waiting for device...")
        self.open_socket()
        alive = self.probe_device()
        log("bridge up on %s, device %s (watch=%s)" % (
            self.port or ("dry-run" if self.dry_run else "waiting"),
            "alive" if alive else "NOT RESPONDING",
            "on" if self.watch else "off",
        ))
        with open(PID_PATH, "w") as f:
            f.write(str(os.getpid()))
        try:
            self.pump()
        except KeyboardInterrupt:
            pass
        finally:
            log("bridge stopped")
            self.close_serial()
            for p in (SOCKET_PATH, PID_PATH):
                try:
                    os.unlink(p)
                except OSError:
                    pass


def find_port():
    """Find an ESP32 USB-Serial/JTAG port on macOS or Linux."""
    patterns = (
        "/dev/cu.usbmodem*",
        "/dev/ttyACM*",
        "/dev/ttyUSB*",
        "/dev/cu.usbserial*",
    )
    for pat in patterns:
        matches = sorted(glob.glob(pat))
        if matches:
            return matches[0]
    return None


def status_main():
    running = False
    pid = None
    if os.path.exists(PID_PATH):
        try:
            pid = int(open(PID_PATH).read().strip())
            os.kill(pid, 0)
            running = True
        except (ValueError, ProcessLookupError, PermissionError):
            pass
    print("bridge: %s%s" % ("running" if running else "not running", " (pid %d)" % pid if running else ""))
    print("socket: %s (%s)" % ("present" if os.path.exists(SOCKET_PATH) else "absent", SOCKET_PATH))
    print("port:   %s" % (find_port() or "none detected"))
    print("log:    %s" % LOG_PATH)
    return 0


def send_main(line, port=None):
    """One raw line to the device, for testing. Refuses while the bridge owns the port."""
    if os.path.exists(PID_PATH):
        try:
            os.kill(int(open(PID_PATH).read().strip()), 0)
            sys.exit("bridge is running and owns the port; stop it first (kill $(cat %s))" % PID_PATH)
        except (ValueError, ProcessLookupError, PermissionError):
            pass
    import serial

    port = port or find_port()
    if not port:
        sys.exit("no ESP32 serial port found (looked for /dev/cu.usbmodem*, /dev/ttyACM*, /dev/ttyUSB*)")
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
            idx = argv.index("--port")
            if idx + 1 >= len(argv):
                sys.exit("--port requires a device path")
            port = argv[idx + 1]
        watch = "--no-watch" not in argv
        dry_run = "--dry-run" in argv
        bridge = Bridge(port, watch=watch, dry_run=dry_run)
        if "--daemon" in argv:
            if os.fork() > 0:
                time.sleep(0.5)
                return 0
            os.setsid()
        bridge.run()
        return 0
    if cmd == "send":
        port = None
        rest = argv[1:]
        if "--port" in rest:
            idx = rest.index("--port")
            port = rest[idx + 1]
            rest = rest[:idx] + rest[idx + 2:]
        return send_main(rest[0] if rest else "ZTV1 C ping", port=port)
    if cmd == "status":
        return status_main()
    sys.exit(__doc__)


if __name__ == "__main__":
    sys.exit(main())
