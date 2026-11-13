"""The git write gate: the main session never commits, pushes, merges or rewrites history by hand.

Those writes carry your approval (the commit message you approved, the PR table you approved), and `publish.py` (the
`git-commit` and `gh-merge-pull-request` skills) and `ship.py` (`ship-toolbox-change`) are the paths that hold it.
Denied in the main session:

- `git commit`, `push`, `merge`, `rebase` and `reset --hard`, with or without options before the subcommand
  (`git -C dir commit`, `git -c k=v push`);
- `gh pr merge`.

Allowed: every other git and gh command (`status`, `diff`, `log`, `add`, `branch`, `reset` without `--hard`, ...), the
two scripts above (they are not `git` or `gh` commands, so they are never matched), `git rebase --continue`, `--abort`,
`--skip` and `--quit`, which finish or back out of the rebase `publish.py push` leaves in progress on a conflict, and
`git merge --abort` and `--quit`, which only back out.

A subagent is never denied (MAIN_ONLY). The message names the publish step to use.
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

DENIED = {"commit", "push", "merge", "rebase"}
CONTINUATION = {
    "rebase": {"--continue", "--abort", "--skip", "--quit"},
    "merge": {"--abort", "--quit"},
}
GIT_OPTIONS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--config-env",
                          "--exec-path"}
GH_OPTIONS_WITH_VALUE = {"-R", "--repo", "--hostname"}


def positional(args: list[str], with_value: set[str]) -> tuple[list[str], list[str]]:
    """The words of a command that aren't options, and every word after the first of them, in order: the subcommand
    comes first, then its arguments. Options before the subcommand that take a separate value are skipped with it."""
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in with_value else 1
    return args[i:i + 1], args[i + 1:]


def git_write(args: list[str]) -> str | None:
    """The subcommand of this `git` call if it is a denied write, else None."""
    sub, rest = positional(args, GIT_OPTIONS_WITH_VALUE)
    if not sub:
        return None
    name = sub[0]
    if name == "reset":
        return name if "--hard" in rest else None
    if CONTINUATION.get(name, set()) & set(rest):
        return None
    return name if name in DENIED else None


def gh_write(args: list[str]) -> bool:
    """Whether this `gh` call is `pr merge`."""
    group, rest = positional(args, GH_OPTIONS_WITH_VALUE)
    if group != ["pr"]:
        return False
    action, _ = positional(rest, GH_OPTIONS_WITH_VALUE)
    return action == ["merge"]


def denied(simple: list[str]) -> str | None:
    """What a denied command is called in the message, or None if the main session may run it."""
    name, args = cmd.script_args(simple)
    if name == "git":
        sub = git_write(args)
        return f"git {sub}" + (" --hard" if sub == "reset" else "") if sub else None
    if name == "gh" and gh_write(args):
        return "gh pr merge"
    return None


def check(event: dict) -> str | None:
    command = (event.get("tool_input") or {}).get("command")
    if not isinstance(command, str) or not command.strip():
        return None
    for simple in cmd.simple_commands(command):
        what = denied(simple)
        if not what:
            continue
        step = (
            "the `gh-merge-pull-request` skill (`publish.py merge`)"
            if what in ("git merge", "gh pr merge")
            else "the `git-commit` skill (`publish.py commit` and `push`, with the message you approved)"
        )
        return (
            f"`{what}` is a publish step, and publishing goes through the approved path, not a direct call in the "
            f"main session. Use {step}; in a toolbox repo, the `ship-toolbox-change` skill (`ship.py`). Read-only git "
            f"(status, diff, log) and `git add` are unaffected."
        )
    return None
