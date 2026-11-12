"""The main checkout gate: the main session never edits a repo's main checkout while a task worktree of that repo exists.

The git-conventions rule says you work in the main checkout, so a session that opens a file by its main-checkout path
edits your copy. A task's worktree is where its edits go. Denied in the main session:

- `Edit` and `Write` of a file under a repo's main checkout (a child repo of a nested workspace, the root of a single
  one) when `git worktree list` for that repo shows a linked worktree under the workspace's worktrees folder, which is
  where a task's worktrees live and what the records' `worktrees` field lists.

Allowed: a file in a worktree itself, a file in a repo with no task worktree, a file outside every repo, and anything
in the vault (the rule says a vault inside a repo is written in the main checkout, never a worktree's stale copy).
Without a readable workspace config, or when git fails, the edit goes ahead.

A subagent is never denied (MAIN_ONLY). The message names the worktree path to use instead.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True

TOOLS = {"Edit", "Write"}
MAIN_ONLY = True
GIT_TIMEOUT = 10

_here = os.path.abspath(__file__)
_spec = importlib.util.spec_from_file_location("_gate_commands", os.path.join(os.path.dirname(_here), "_commands.py"))
cmd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cmd)


def inside(path: Path, folder: Path) -> bool:
    return path == folder or folder in path.parents


def main_checkout(cfg, path: Path) -> Path | None:
    """The main checkout of the repo a file belongs to: the workspace root of a single-repo workspace, the child repo
    of a nested one; None for a file outside every repo."""
    if not cfg.nested:
        return cfg.root if inside(path, cfg.root) and (cfg.root / ".git").exists() else None
    try:
        child = cfg.root / path.relative_to(cfg.root).parts[0]
    except (ValueError, IndexError):
        return None
    return child if (child / ".git").is_dir() else None


def task_worktrees(main: Path, worktrees_dir: Path) -> list[Path]:
    """The linked worktrees of a repo that sit under the worktrees folder, from `git worktree list`."""
    found = []
    out = subprocess.run(["git", "-C", str(main), "worktree", "list", "--porcelain"], capture_output=True, text=True,
                         timeout=GIT_TIMEOUT, check=True).stdout
    for line in out.splitlines():
        if line.startswith("worktree "):
            path = Path(line.removeprefix("worktree ")).resolve()
            if path != main and inside(path, worktrees_dir):
                found.append(path)
    return sorted(found)


def check(event: dict) -> str | None:
    raw = (event.get("tool_input") or {}).get("file_path")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        cfg = cmd.workspace_config(event)
        if cfg is None:
            return None
        base = Path(event.get("cwd") or os.getcwd())
        path = (base / Path(raw).expanduser()).resolve()
        worktrees_dir = cfg.worktrees_dir.resolve()
        vault = cfg.vault_dir
        if inside(path, worktrees_dir) or (vault is not None and inside(path, vault.resolve())):
            return None
        main = main_checkout(cfg, path)
        if main is None:
            return None
        main = main.resolve()
        found = task_worktrees(main, worktrees_dir)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if not found:
        return None
    relative = path.relative_to(main)
    where = "; ".join(str(w / relative) for w in found)
    return (
        f"`{path}` is in the main checkout of `{main.name}`, and a task worktree of it exists, so the edit "
        f"belongs in the worktree: the user works in the main checkout. Edit {where} instead."
    )
