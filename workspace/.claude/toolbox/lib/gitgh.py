"""Git and gh plumbing for a workspace, shared by worktrees.py, publish.py and branches.py.

- repos() lists the workspace's repos: the root for a `single` workspace, every child folder holding a `.git` for a
  `nested` one. resolve() turns a repo name or a checkout path (a main checkout or a task's worktree) into a Checkout.
- git() runs in a checkout with stdin closed and terminal prompts off, so a credential miss fails fast instead of
  hanging on a prompt nobody sees.
- gh() runs with stdin closed, prompts off and a 90s timeout, from the home folder and never inside a repo (so
  `gh pr merge --delete-branch` can't switch or delete a local branch). Every caller passes --repo: without it, a
  large `gh pr create` batch once hung on a prompt and created nothing.
- prs_for_branch() is the `--state all` lookup that every create and retry goes through, because a gh call that timed
  out may still have done its work, and `gh pr create` isn't idempotent.
- branch_patterns() turns the config's `git.branch` into the regexes a task branch must match.

Scripts import it by path from their own resolved location:

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import config as toolbox_config

GH_TIMEOUT = 90
GIT_TIMEOUT = 120
QUIET_ENV = {"GIT_TERMINAL_PROMPT": "0", "GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1"}

# Exit codes shared by the scripts.
OK, PRECHECK_FAILED, STEP_FAILED, HALTED = 0, 2, 3, 4

TRAILER = re.compile(r"^(co-authored-by|signed-off-by):|🤖", re.IGNORECASE | re.MULTILINE)
TICKET_KEY = re.compile(r"\b[A-Z][A-Z0-9]*-\d+\b")
KEY_ID = re.compile(r"[A-Z][A-Z0-9]*-\d+")
SLICE_ID = re.compile(r"([A-Z][A-Z0-9]*-\d+)\.[1-9]\d*")  # a limited-scope run of a ticket: <KEY>.<n>
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def env() -> dict:
    """The environment for git and gh: the caller's, with prompts and update notices off."""
    return {**os.environ, **QUIET_ENV}


class Failed(Exception):
    """A step failed; the message says which command and why."""


@dataclass(frozen=True)
class Checkout:
    name: str
    path: Path
    org: str | None = None

    @property
    def gh_repo(self) -> str:
        """`owner/name` from origin's URL, else the configured org and the repo's folder name."""
        url = git(self.path, "remote", "get-url", "origin", check=False)
        match = re.search(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?/?$", url)
        if match:
            return f"{match.group(1)}/{match.group(2)}"
        return f"{self.org}/{self.name}" if self.org else self.name

    @property
    def main(self) -> Path:
        """The repo's main checkout (itself, unless this is a linked worktree)."""
        return Path(git(self.path, "rev-parse", "--path-format=absolute", "--git-common-dir")).parent


# ---------------------------------------------------------------------------------------------------------------------
# The workspace's repos


def repos(cfg: toolbox_config.Config) -> list[Checkout]:
    """The workspace's main checkouts: the root for `single`, each child repo for `nested`, alphabetical."""
    org = cfg.get("git.org")
    if not cfg.nested:
        return [Checkout(cfg.name, cfg.root, org)]
    return sorted((Checkout(child.name, child, org) for child in cfg.root.iterdir()
                   if child.is_dir() and not child.name.startswith(".") and (child / ".git").exists()),
                  key=lambda c: c.name)


def resolve(cfg: toolbox_config.Config, arg: str) -> Checkout:
    """A checkout path (absolute, ~ or relative) or a repo name of the workspace."""
    org = cfg.get("git.org")
    path = Path(arg).expanduser()
    if not path.is_absolute() and not path.exists():
        path = cfg.root / arg
    path = path.resolve()
    if not (path / ".git").exists():
        names = [c.name for c in repos(cfg)]
        if not cfg.nested and arg in (cfg.name, "."):
            return Checkout(cfg.name, cfg.root, org)
        raise Failed(f"{arg}: not a git checkout ({path}); the workspace's repos are {', '.join(names) or 'none'}")
    return Checkout(path.name if cfg.nested else cfg.name, path, org)


def resolve_all(cfg: toolbox_config.Config, args: list[str]) -> list[Checkout]:
    """Every argument resolved, alphabetical by repo name, which is the order batches run in."""
    checkouts = [resolve(cfg, a) for a in args]
    names = [c.name for c in checkouts]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise Failed(f"the same repo twice in one batch: {', '.join(dupes)}")
    return sorted(checkouts, key=lambda c: c.name)


def worktree_path(cfg: toolbox_config.Config, work_id: str, repo: str) -> Path:
    """Where a task's worktree of a repo lives: `<worktrees>/<id>/<repo>` nested, `<worktrees>/<id>` single."""
    base = cfg.worktrees_dir / work_id
    return base / repo if cfg.nested else base


def task_worktrees(cfg: toolbox_config.Config, work_id: str) -> list[Checkout]:
    """The checkouts that exist for a task, alphabetical."""
    base = cfg.worktrees_dir / work_id
    if not base.is_dir():
        return []
    org = cfg.get("git.org")
    if not cfg.nested:
        return [Checkout(cfg.name, base, org)] if (base / ".git").exists() else []
    return [Checkout(p.name, p, org) for p in sorted(base.iterdir()) if p.is_dir() and (p / ".git").exists()]


def default_branch(cfg: toolbox_config.Config) -> str:
    return cfg.default_branch


def check_work_id(raw: str) -> str:
    """A ticket key, a slice id or a slug; anything else (a path, an empty string) would name a folder outside the
    worktrees."""
    if not (KEY_ID.fullmatch(raw) or SLICE_ID.fullmatch(raw) or SLUG.fullmatch(raw)):
        raise Failed(f"{raw!r} is not a work id: a ticket key (ABC-123), a slice (ABC-123.2) or a lowercase "
                     "kebab-case slug")
    return raw


def branch_for(cfg: toolbox_config.Config, work_id: str, desc: str | None = None) -> str:
    """The task's branch from `git.branch`: the key and description for a key id, the slug alone for a slug id. A
    slice keeps its ticket's key and leads the description with `slice-<n>-`, so parallel slices never share one."""
    if desc and not SLUG.fullmatch(desc):
        raise Failed(f"{desc!r} is not kebab-case")
    sliced = SLICE_ID.fullmatch(work_id)
    if sliced and desc:
        prefix = f"slice-{work_id.rsplit('.', 1)[1]}-"
        desc = desc if desc.startswith(prefix) else prefix + desc
    if sliced:
        work_id = sliced.group(1)
    if KEY_ID.fullmatch(work_id):
        if not desc:
            raise Failed(f"{work_id} is a ticket key: a kebab-case description is required for the branch name")
        return cfg.branch(desc, work_id)
    return cfg.branch(desc or work_id)


def branch_patterns(cfg: toolbox_config.Config) -> list[re.Pattern]:
    """Regexes a task branch must match: `git.branch` with the key, and with the key and its separator dropped."""
    pattern = cfg.get("git.branch")
    slug, key = r"[a-z0-9]+(?:-[a-z0-9]+)*", r"[A-Z][A-Z0-9]*-\d+"
    keyless = pattern.replace("{key}-", "").replace("-{key}", "").replace("{key}", "")
    result = []
    for text in (pattern, keyless):
        parts = re.split(r"(\{handle\}|\{key\}|\{slug\})", text)
        built = "".join({"{handle}": re.escape(cfg.handle), "{key}": key, "{slug}": slug}.get(p, re.escape(p))
                        for p in parts)
        result.append(re.compile(f"^{built}$"))
    return result


# ---------------------------------------------------------------------------------------------------------------------
# Running git and gh


def run(cmd: list[str], cwd: Path, timeout: int, check: bool = True) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, stdin=subprocess.DEVNULL, env=env(),
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        raise Failed(f"{' '.join(cmd[:4])}: timed out after {timeout}s") from None
    if check and proc.returncode != 0:
        raise Failed(f"{' '.join(cmd)}: {(proc.stderr or proc.stdout).strip()}")
    return proc


def git(path: Path, *args: str, check: bool = True, timeout: int = GIT_TIMEOUT) -> str:
    return run(["git", *args], path, timeout, check).stdout.strip()


def git_ok(path: Path, *args: str) -> bool:
    return run(["git", *args], path, GIT_TIMEOUT, check=False).returncode == 0


def gh(*args: str, check: bool = True) -> str:
    return run(["gh", *args], Path.home(), GH_TIMEOUT, check).stdout.strip()


def gh_json(*args: str):
    return json.loads(gh(*args) or "null")


def prs_for_branch(gh_repo: str, branch: str, state: str = "all",
                   fields: str = "number,url,state,headRefOid") -> list[dict]:
    return gh_json("pr", "list", "--repo", gh_repo, "--head", branch, "--state", state, "--json", fields) or []


def branch(path: Path) -> str:
    return git(path, "branch", "--show-current")


def remote_sha(path: Path, name: str) -> str | None:
    return git(path, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{name}", check=False) or None


def operation_in_progress(path: Path) -> list[str]:
    found = [ref for ref in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD")
             if git_ok(path, "rev-parse", "--verify", "--quiet", ref)]
    for name in ("rebase-merge", "rebase-apply"):
        if Path(git(path, "rev-parse", "--path-format=absolute", "--git-path", name)).exists():
            found.append(name)
    return found


def status_lines(path: Path) -> list[str]:
    """`git status --porcelain` with every untracked file listed, not just its folder. Not stripped: the first line's
    leading space is part of its status code."""
    out = run(["git", "status", "--porcelain", "--untracked-files=all"], path, GIT_TIMEOUT).stdout
    return [line for line in out.splitlines() if line]


def commits(path: Path, since: str, merges: bool = False) -> list[dict]:
    """Commits in since..HEAD, oldest first: non-merge ones by default, or only merge commits."""
    flag = "--merges" if merges else "--no-merges"
    out = git(path, "log", "--reverse", flag, "--format=%H%x1f%s%x1f%b%x1e", f"{since}..HEAD")
    result = []
    for record in out.split("\x1e"):
        record = record.strip("\n")
        if record:
            sha, subject, body = (record.split("\x1f") + ["", ""])[:3]
            result.append({"sha": sha, "subject": subject, "body": body.strip()})
    return result


def emit(out: dict, code: int) -> int:
    print(json.dumps(out, indent=2, default=str))
    return code
