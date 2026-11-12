"""The checks gate: the main session never runs a build, tests, a formatter or the services lock itself.

Each of those puts raw output in the orchestrator's context, where it costs on every later request, so they belong to
`check-runner` (its tools are Bash and Read, and it runs `run_checks.py`). Denied in the main session:

- `run_checks.py`, `run_tests.py` and `services_lock.py acquire` or `start`;
- the config's `build`, `unit`, `format` and `integration` commands for any area, matched on the words before the first
  `{placeholder}` of each command in them;
- raw `mvn`, `mvnw`, `gradle`, `gradlew`, and `npm`/`pnpm`/`yarn` `test`.

A subagent is never denied (MAIN_ONLY), so `check-runner` can run all of it. The message names the mode to spawn it
with.
"""

from __future__ import annotations

import importlib.util
import os
import sys

sys.dont_write_bytecode = True

TOOLS = {"Bash"}
MAIN_ONLY = True

_here = os.path.abspath(__file__)
_spec = importlib.util.spec_from_file_location("_gate_commands", os.path.join(os.path.dirname(_here), "_commands.py"))
cmd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cmd)

MODES = ("build", "unit", "format", "integration")
BUILD_TOOLS = {"mvn", "mvnw", "gradle", "gradlew"}
NODE_TOOLS = {"npm", "pnpm", "yarn"}
INTEGRATION_HINTS = ("integration", "failsafe", "-pvalidation")


def tool_mode(words: list[str]) -> str:
    """The check-runner mode a raw Maven or Gradle call is closest to; unknown goals read as a build."""
    text = " ".join(words).lower()
    if any(hint in text for hint in INTEGRATION_HINTS):
        return "integration"
    if any(hint in text for hint in ("spotless", "format", "fmt:")):
        return "format"
    if any(goal in words for goal in ("test", "verify", "check")) or "surefire" in text:
        return "unit"
    return "build"


def denied_mode(simple: list[str], areas: list[tuple[str, str]]) -> str | None:
    """The mode a check-runner should run this command under, or None if the main session may run it."""
    name, args = cmd.script_args(simple)
    if name in ("run_checks.py", "run_tests.py"):
        return next((a for a in args if a in MODES), "the mode that fits")
    if name == "services_lock.py":
        sub = next((a for a in args if not a.startswith("-")), None)
        return "integration" if sub in ("acquire", "start") else None
    if name in BUILD_TOOLS:
        return tool_mode(args)
    if name in NODE_TOOLS:
        goals = [a for a in args if not a.startswith("-")]
        if goals[:1] == ["test"] or goals[:2] == ["run", "test"] or goals[:1] == ["t"]:
            return "unit"
    for kind, template in areas:
        if cmd.matches_area_command(simple, template):
            return kind
    return None


def check(event: dict) -> str | None:
    command = (event.get("tool_input") or {}).get("command")
    if not isinstance(command, str) or not command.strip():
        return None
    simples = cmd.simple_commands(command)
    if not simples:
        return None
    areas = None
    for simple in simples:
        # the config is read only when a call isn't already one of the fixed shapes
        mode = denied_mode(simple, [])
        if mode is None:
            if areas is None:
                areas = cmd.area_commands(event)
            mode = denied_mode(simple, areas)
        if mode:
            return (
                f"`{' '.join(simple)[:80]}` is a check, and checks don't run in the main session: their output would "
                f"stay in this context for every later request. Spawn `check-runner` with mode `{mode}` (the areas "
                f"as <area>=<dir>, the bin path and the task in its prompt) and act on its report."
            )
    return None
