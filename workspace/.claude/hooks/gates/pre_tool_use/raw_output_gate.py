"""The raw output gate: the main session never reads local service logs, surefire or failsafe reports, or build logs.

`check-runner` reads them through `run_checks.py`, which prints the failures with their assertions and trimmed traces,
so raw output never needs to reach the orchestrator's context. Denied in the main session:

- Read of such a file, Grep with such a path or glob, and Bash commands that read one (`cat`, `tail`, `grep`, `sed`,
  ...) on it. Listing a folder (`ls target/surefire-reports`) shows no output and passes.
- Such a file is anything under `target/surefire-reports/`, `target/failsafe-reports/`, `build/test-results/` or
  `build/reports/`, a run's `<name>-<area>-<start|build|unit|format|integration>.log`, a `build`, `mvn`, `maven`,
  `gradle`, `npm-debug`, `surefire` or `failsafe` `.log`, and any `.log` under a `target`, `build`, `logs` or `locks`
  folder.

A subagent is never denied (MAIN_ONLY). The message points to `check-runner`.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys

sys.dont_write_bytecode = True

TOOLS = {"Read", "Grep", "Bash"}
MAIN_ONLY = True

_here = os.path.abspath(__file__)
_spec = importlib.util.spec_from_file_location("_gate_commands", os.path.join(os.path.dirname(_here), "_commands.py"))
cmd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cmd)

READERS = {"cat", "head", "tail", "less", "more", "grep", "egrep", "fgrep", "rg", "ag", "awk", "sed", "bat", "strings",
           "tac", "nl", "cut", "sort", "uniq", "jq", "xmllint", "od", "xxd"}
REPORT_DIRS = re.compile(r"(^|/)(target/(surefire|failsafe)-reports|build/(test-results|reports))(/|$)")
LOG_NAME = re.compile(
    r"(^|/)([^/]*-(start|build|unit|format|integration)"
    r"|(build|mvn|maven|gradle|npm-debug|surefire|failsafe)[^/]*)\.log$"
)
PATTERN_FIRST = {"grep", "egrep", "fgrep", "rg", "ag", "awk", "sed", "jq"}  # the first non-option word is no path
LOG_DIRS = {"target", "build", "logs", "locks"}


def is_raw(path: str) -> bool:
    """Whether a path (or glob) names a report, service log or build log."""
    path = os.path.expanduser(path).replace("\\", "/")
    if REPORT_DIRS.search(path) or LOG_NAME.search(path):
        return True
    return path.endswith(".log") and bool(LOG_DIRS & set(path.split("/")[:-1]))


def target(event: dict) -> str | None:
    """The offending path of this call, or None."""
    tool, args = event.get("tool_name"), event.get("tool_input") or {}
    if tool == "Read":
        paths = [args.get("file_path")]
    elif tool == "Grep":
        paths = [args.get("path"), args.get("glob")]
    elif tool == "Bash":
        paths = []
        command = args.get("command")
        for simple in cmd.simple_commands(command) if isinstance(command, str) else []:
            name = os.path.basename(simple[0])
            if name in READERS:
                operands = [w for w in simple[1:] if not w.startswith("-")]
                paths += operands[1:] if name in PATTERN_FIRST else operands
    else:
        return None
    return next((p for p in paths if isinstance(p, str) and p and is_raw(p)), None)


def check(event: dict) -> str | None:
    path = target(event)
    if path is None:
        return None
    return (
        f"`{path[:100]}` is raw build, test or service output, which stays out of the main session's context. Spawn "
        f"`check-runner` with the mode that produced it (`build`, `unit`, `format` or `integration`): its report "
        f"already carries each failure's assertion and trimmed stack trace."
    )
