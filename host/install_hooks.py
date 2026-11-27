#!/usr/bin/env python3
"""Register the ZCode TV hooks in the workspace configuration.

Writes the `hooks` block of <repo>/.zcode/config.json (created if missing,
merged if present) and enables the hook runner. Every supported hook event is
routed to host/zcode_tv.py hook, which forwards the event to the bridge. The
hook itself is silent and always exits 0, so the session is never blocked --
with or without a running bridge.

    python3 host/install_hooks.py            # install / update
    python3 host/install_hooks.py --remove   # remove our hooks again
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK_COMMAND = "python3 %s hook" % os.path.join(HERE, "zcode_tv.py")
EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PermissionRequest",
          "PostToolUse", "PostToolUseFailure", "Stop")

CONFIG_DIR = os.path.join(os.path.dirname(HERE), ".zcode")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")


def our_hook_entries():
    return [{"type": "command", "command": HOOK_COMMAND, "timeout": 5,
             "statusMessage": "ZCode TV status display"}]


def main():
    remove = "--remove" in sys.argv
    os.makedirs(CONFIG_DIR, exist_ok=True)
    config = {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            try:
                config = json.load(f)
            except ValueError:
                sys.exit("%s is not valid JSON; fix or remove it first" % CONFIG_PATH)

    hooks = config.get("hooks") if isinstance(config.get("hooks"), dict) else {}
    hooks.setdefault("events", {})
    changed = 0
    for event in EVENTS:
        matchers = hooks["events"].setdefault(event, [])
        for matcher in matchers:
            before = len(matcher.get("hooks", []))
            matcher["hooks"] = [h for h in matcher.get("hooks", [])
                                if "zcode_tv.py" not in str(h.get("command", ""))]
            changed += before - len(matcher["hooks"])
        matchers[:] = [m for m in matchers if m.get("hooks")]
        if not remove:
            hooks["events"][event] = matchers + [{"hooks": our_hook_entries()}]
            changed += 1
    if remove:
        for event in EVENTS:
            if event in hooks["events"] and not hooks["events"][event]:
                del hooks["events"][event]
    if hooks.get("events"):
        hooks["enabled"] = True
        config["hooks"] = hooks
    else:
        config.pop("hooks", None)

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
        f.write("\n")
    action = "removed our hooks from" if remove else "registered hooks in"
    print("%s %s (%d hook(s) %s)" % (action, CONFIG_PATH, changed if remove else len(EVENTS),
                                     "removed" if remove else "registered"))
    print("restart the ZCode session so the new configuration takes effect,")
    print("then start the bridge:  python3 %s bridge --daemon" % os.path.join(HERE, "zcode_tv.py"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
