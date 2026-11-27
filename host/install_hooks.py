#!/usr/bin/env python3
"""Register AGY TV lifecycle hooks in Antigravity's hooks.json.

By default installs globally into ~/.gemini/config/hooks.json so every AGY
project on this machine pushes live status to the ESP32 TV. Pass --workspace
to install into <repo>/.agents/hooks.json (with a portable relative command).

    python3 host/install_hooks.py                # install globally (~/.gemini/config/hooks.json)
    python3 host/install_hooks.py --workspace    # install in this repo (.agents/hooks.json)
    python3 host/install_hooks.py --remove       # remove from global hooks.json
    python3 host/install_hooks.py --workspace --remove
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
HOOK_NAME = "agy-tv"


def build_hook_spec(command):
    handler = {"type": "command", "command": command, "timeout": 5}
    return {
        "enabled": True,
        "SessionStart": [handler],
        "PreInvocation": [handler],
        "PreToolUse": [{"matcher": "*", "hooks": [handler]}],
        "PostToolUse": [{"matcher": "*", "hooks": [handler]}],
        "PostInvocation": [handler],
        "Stop": [handler],
    }


def main():
    parser = argparse.ArgumentParser(description="Install or remove AGY TV hooks in hooks.json.")
    parser.add_argument("--workspace", nargs="?", const=REPO_ROOT, default=None,
                        help="Install into <dir>/.agents/hooks.json instead of ~/.gemini/config/hooks.json")
    parser.add_argument("--remove", action="store_true",
                        help="Remove the agy-tv hook block")
    args = parser.parse_args()

    if args.workspace is not None:
        ws_dir = os.path.abspath(args.workspace)
        config_dir = os.path.join(ws_dir, ".agents")
        config_path = os.path.join(config_dir, "hooks.json")
        if ws_dir == REPO_ROOT:
            # Working directory of a hook is the folder containing hooks.json (.agents/)
            command = "python3 ../host/agy_tv.py hook"
        else:
            command = "python3 %s hook" % os.path.join(HERE, "agy_tv.py")
    else:
        config_dir = os.path.expanduser("~/.gemini/config")
        config_path = os.path.join(config_dir, "hooks.json")
        command = "python3 %s hook" % os.path.join(HERE, "agy_tv.py")

    os.makedirs(config_dir, exist_ok=True)
    config = {}
    if os.path.exists(config_path):
        with open(config_path, encoding="utf-8") as f:
            raw = f.read().strip()
            if raw:
                try:
                    config = json.loads(raw)
                except ValueError as exc:
                    sys.exit("%s is not valid JSON (%s); fix or remove it first" % (config_path, exc))
                if not isinstance(config, dict):
                    sys.exit("%s must contain a JSON object" % config_path)

    if args.remove:
        removed = config.pop(HOOK_NAME, None) is not None
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print("%s %s in %s" % ("removed" if removed else "not present:", HOOK_NAME, config_path))
        return 0

    config[HOOK_NAME] = build_hook_spec(command)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print("registered %s hooks in %s" % (HOOK_NAME, config_path))
    print("  command: %s" % command)
    print("restart your AGY session so the new hooks take effect,")
    print("then start the bridge:  python3 %s bridge --daemon" % os.path.join(HERE, "agy_tv.py"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
