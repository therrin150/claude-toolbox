#!/usr/bin/env python3
"""The hook gate dispatcher: one global script, registered once per event in ~/.claude/settings.json, that runs every
installed gate for that event, global and workspace alike.

Rules that say "never do X in the main session" are enforced by gates instead of prose. A gate is a module named
`<name>.py` in a `gates/<event>/` folder (<event> is the hook event in snake case: PreToolUse is `pre_tool_use`). Two
folders are read, global gates first:

  global     `gates/<event>/` beside this script, i.e. ~/.claude/hooks/gates/<event>/
  workspace  the nearest `.claude/hooks/gates/<event>/` found walking up from the session's directory
             ($CLAUDE_PROJECT_DIR, else the input's `cwd`), so a session started in a repo or a worktree under a
             workspace still finds the workspace's gates

A gate declares:

  TOOLS      the tool names it looks at (a set of strings, e.g. {"Bash"}, not a bare str); a gate whose TOOLS leave
             out the call's tool never runs. `"*"` matches every tool, and is the only value that matches an event with
             no tool (SubagentStop, Stop, ...), so a gate for such an event declares {"*"}
  MAIN_ONLY  True to skip the gate when the input has an `agent_type`, i.e. the call comes from a subagent
  check(event)  returns a denial message (a str, which should name the agent or skill to use instead) or None

The dispatcher reads the hook input from stdin once and runs the matching gates in file-name order within each folder.
Two gates may share a file name across folders; both run. The first denial ends the run: for PreToolUse it is printed
as a `permissionDecision: deny`, for any other event as `decision: block` (the form PostToolUse, SubagentStop and the
like honor). Exit code is always 0.

Fail open: a gate that fails to load, raises, or returns something other than a str or None is skipped with a line
`gate <event>/<name>: <error>` on stderr, and bad input or a missing gates folder runs nothing. One broken gate never
blocks the session.

The folders are listed on every call, so adding or deleting a gate file takes effect on the next tool call with no
settings change and no restart. Files starting with `_` are not gates. The global folder is found from the path this
script was run by, not its resolved target: the installer links files one at a time, so gates installed beside a linked
dispatcher are found where they are linked.

Registered (by the installer's settings fragment) as the one hook per event. The command skips a missing script, since
a hook command that exits 2 blocks the call:
  {"matcher": "*", "hooks": [{"type": "command",
   "command": "f=\\"$HOME/.claude/hooks/run_gates.py\\"; [ ! -f \\"$f\\" ] || python3 \\"$f\\""}]}
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
WORKSPACE_GATES = Path(".claude") / "hooks" / "gates"


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


def workspace_folder(event: dict, name: str) -> Path | None:
    """The nearest `.claude/hooks/gates/<name>/` at or above the session's directory, other than the global one."""
    start = os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd")
    if not start:
        return None
    try:
        own = (GATES / name).resolve()
        for parent in [Path(start), *Path(start).parents]:
            folder = parent / WORKSPACE_GATES / name
            if folder.is_dir() and folder.resolve() != own:
                return folder
    except OSError:
        pass
    return None


def gate_files(event: dict, name: str) -> list:
    """Every gate file to consider: the global folder's, then the nearest workspace folder's, each in name order."""
    files = []
    for folder in (GATES / name, workspace_folder(event, name)):
        try:
            if folder is not None and folder.is_dir():
                files += sorted(p for p in folder.glob("*.py") if not p.name.startswith("_"))
        except OSError:
            continue
    return files


def run(event: dict) -> str | None:
    """The first gate's denial message for this call, or None."""
    name = event_dir(str(event.get("hook_event_name") or ""))
    if not name:
        return None
    tool = event.get("tool_name")
    from_agent = bool(event.get("agent_type"))
    for path in gate_files(event, name):
        try:
            gate = load(path)
            if not isinstance(gate.TOOLS, (set, frozenset, list, tuple)):
                raise TypeError(f"TOOLS is a {type(gate.TOOLS).__name__}, not a set of tool names")
            if not ("*" in gate.TOOLS or tool in gate.TOOLS) or (gate.MAIN_ONLY and from_agent):
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
