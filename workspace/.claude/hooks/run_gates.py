#!/usr/bin/env python3
"""The hook gate dispatcher: one script, registered once per event, that runs every installed gate for that event.

Rules that say "never do X in the main session" are enforced by gates instead of prose. A gate is a module in
`gates/<event>/<name>.py` beside this script (<event> is the hook event in snake case: PreToolUse is `pre_tool_use`).
It declares:

  TOOLS      the tool names it looks at (a set of strings, e.g. {"Bash"}, not a bare str); a gate whose TOOLS leave
             out the call's tool never runs
  MAIN_ONLY  True to skip the gate when the input has an `agent_type`, i.e. the call comes from a subagent
  check(event)  returns a denial message (a str, which should name the agent or skill to use instead) or None

The dispatcher reads the hook input from stdin once and runs the matching gates in file-name order. The first denial
ends the run: for PreToolUse it is printed as a `permissionDecision: deny`, for any other event as `decision: block`
(the form PostToolUse, SubagentStop and the like honor). Exit code is always 0.

Fail open: a gate that fails to load, raises, or returns something other than a str or None is skipped with a line
`gate <event>/<name>: <error>` on stderr, and bad input or a missing gates folder runs nothing. One broken gate never
blocks the session.

The gates folder is listed on every call, so adding or deleting a gate file takes effect on the next tool call with no
settings change and no restart. Files starting with `_` are not gates. The folder is found from the path this script
was run by, not its resolved target: the installer links files one at a time, so gates installed beside a linked
dispatcher are found where they are linked.

Registered (by the installer's core settings fragment) as:
  {"matcher": "*", "hooks": [{"type": "command",
   "command": "python3 \\"$CLAUDE_PROJECT_DIR/.claude/hooks/run_gates.py\\""}]}
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True

GATES = Path(os.path.abspath(__file__)).parent / "gates"


def event_dir(name: str) -> str:
    """PreToolUse -> pre_tool_use."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def warn(event: str, gate: str, error: object) -> None:
    print(f"gate {event}/{gate}: {error}", file=sys.stderr)


def load(path: Path):
    spec = importlib.util.spec_from_file_location(f"gate_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(event: dict) -> str | None:
    """The first gate's denial message for this call, or None."""
    name = event_dir(str(event.get("hook_event_name") or ""))
    folder = GATES / name
    if not name or not folder.is_dir():
        return None
    tool = event.get("tool_name")
    from_agent = bool(event.get("agent_type"))
    try:
        files = sorted(p for p in folder.glob("*.py") if not p.name.startswith("_"))
    except OSError:
        return None
    for path in files:
        try:
            gate = load(path)
            if not isinstance(gate.TOOLS, (set, frozenset, list, tuple)):
                raise TypeError(f"TOOLS is a {type(gate.TOOLS).__name__}, not a set of tool names")
            if tool not in gate.TOOLS or (gate.MAIN_ONLY and from_agent):
                continue
            message = gate.check(event)
            if message is not None and not isinstance(message, str):
                raise TypeError(f"check returned {type(message).__name__}, not str or None")
        except FileNotFoundError as error:
            if path.exists():  # the gate's own failure, not its removal after the listing
                warn(name, path.stem, f"{type(error).__name__}: {error}")
            continue
        except Exception as error:  # a gate may fail in any way; none may block the call
            warn(name, path.stem, f"{type(error).__name__}: {error}")
            continue
        if message:
            return message
    return None


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    if not isinstance(event, dict):
        return 0
    message = run(event)
    if message:
        name = event.get("hook_event_name")
        if name == "PreToolUse":
            out = {"hookSpecificOutput": {"hookEventName": name, "permissionDecision": "deny",
                                          "permissionDecisionReason": message}}
        else:
            out = {"decision": "block", "reason": message}
        print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
