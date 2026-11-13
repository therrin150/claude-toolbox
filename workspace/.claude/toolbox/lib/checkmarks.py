"""Which checks passed on which diff, so the publish stage can warn that a change was never checked in its current state.

`run_checks.py` records each area's result here when a run ends; `change.py publish` reads it. A mark is keyed by the
checkout, the area and the mode and holds the diff's fingerprint, so editing a file (or rebasing onto different code)
makes every earlier mark stale. Committing the diff doesn't: the fingerprint is the working tree against the merge base
with the default branch, so it is the same before and after the commit.

The marks live in the checkout's own git dir (`<git-dir>/toolbox-checks.json`, per worktree), which git never tracks, so
they add no file to the change and go with the worktree when it's removed. Recording is best effort: a checkout that
isn't a git repo, or has no `origin/<default>`, simply has no marks, and nothing here ever fails a check.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import subprocess
from pathlib import Path

FILE = "toolbox-checks.json"
COUNTS = ("build", "unit", "integration")  # the modes that show the code works; `format` alone doesn't


def git_bytes(path: Path, *args: str) -> bytes | None:
    done = subprocess.run(["git", "-C", str(path), *args], capture_output=True, stdin=subprocess.DEVNULL, timeout=60)
    return done.stdout if done.returncode == 0 else None


def git(path: Path, *args: str) -> str | None:
    out = git_bytes(path, *args)
    return out.decode("utf-8", "replace") if out is not None else None


def toplevel(path: Path) -> Path | None:
    out = git(path, "rev-parse", "--show-toplevel")
    return Path(out.strip()) if out and out.strip() else None


def fingerprint(checkout: Path, base: str) -> str | None:
    """A hash of the checkout's whole change against its merge base with `base`: tracked changes, staged or not, and
    untracked files by name and content. None when there is no merge base (no such `base`, not a git checkout)."""
    point = git(checkout, "merge-base", base, "HEAD")
    if not point:
        return None
    tracked = git_bytes(checkout, "diff", "--binary", point.strip())
    untracked = git_bytes(checkout, "ls-files", "--others", "--exclude-standard", "-z")
    if tracked is None or untracked is None:
        return None
    digest = hashlib.sha256(tracked)  # raw bytes: a non-UTF-8 change must not collapse into another
    for name in sorted(n for n in untracked.split(b"\0") if n):
        digest.update(b"\0" + name + b"\0")
        try:
            digest.update((checkout / name.decode("utf-8", "surrogateescape")).read_bytes())
        except OSError:
            pass
    return digest.hexdigest()


def marks_path(checkout: Path) -> Path | None:
    out = git(checkout, "rev-parse", "--absolute-git-dir")
    return Path(out.strip()) / FILE if out and out.strip() else None


def load(checkout: Path) -> dict:
    path = marks_path(checkout)
    try:
        return json.loads(path.read_text()) if path and path.is_file() else {}
    except (OSError, ValueError):
        return {}


def record(checkout: Path, base: str, mode: str, area: str, result: str, print_: str | None = None) -> bool:
    """Mark `area`'s `mode` run in `checkout` as `result` ("pass" or "fail") against the diff `print_`, the fingerprint
    taken before the run (default: now). False when nothing could be written."""
    path, print_ = marks_path(checkout), print_ or fingerprint(checkout, base)
    if not path or not print_:
        return False
    marks = load(checkout)
    marks.setdefault(area, {})[mode] = {"result": result, "diff": print_,
                                        "at": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}
    try:
        path.write_text(json.dumps(marks, indent=2))
    except OSError:
        return False
    return True


def status(checkout: Path, base: str) -> dict:
    """Per area, the modes that passed (`passed`) and failed (`failed`) on the checkout's diff as it is now."""
    now = fingerprint(checkout, base)
    out: dict[str, dict] = {}
    for area, modes in load(checkout).items():
        for mode, mark in modes.items():
            if mark.get("diff") == now and now:
                key = "passed" if mark.get("result") == "pass" else "failed"
                out.setdefault(area, {"passed": [], "failed": []})[key].append(mode)
    return out


def unchecked(checkout: Path, base: str, areas: list[str]) -> list[str]:
    """The `areas` with no passing build, unit or integration run on the diff as it is now."""
    marks = status(checkout, base)
    return [a for a in areas if not set(marks.get(a, {}).get("passed", [])) & set(COUNTS)]
