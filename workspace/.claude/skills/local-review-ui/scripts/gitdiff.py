"""Read-only git diff helpers: a repo's whole change against its branch point, committed, staged, unstaged and
untracked.

Never writes to a repo. GIT_OPTIONAL_LOCKS=0 stops `git status` from refreshing and rewriting the index, and
untracked files are read from disk rather than marked with `git add -N`.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path

FULL_CONTEXT = 1_000_000
MAX_DIFF_BYTES = 4_000_000
MAX_UNTRACKED_BYTES = 2_000_000

# GIT_LITERAL_PATHSPECS: a file path after `--` is a name, not a pattern, so `f[1].txt` or `:x.txt` can't match
# other files and splice their diffs in.
_ENV = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", GIT_LITERAL_PATHSPECS="1", LC_ALL="C")
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)$")


class GitError(RuntimeError):
    pass


def git(repo: str, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        # diff.suppressBlankEmpty would print blank context lines without their leading space, which parse_patch
        # can't tell from the patch's trailing newline, and every later line number would shift.
        ["git", "-c", "core.quotepath=off", "-c", "diff.suppressBlankEmpty=false", "-C", repo, *args],
        capture_output=True, env=_ENV,
    )
    if check and result.returncode != 0:
        raise GitError(result.stderr.decode("utf-8", "replace").strip() or f"git {args[0]} failed")
    return result.stdout.decode("utf-8", "replace")


def git_status(repo: str, *args: str) -> tuple[int, str, str]:
    """(exit code, stdout, stderr) of a git command, for callers that tell failures apart. Raises GitError when git
    can't run at all."""
    try:
        result = subprocess.run(["git", "-C", repo, *args], capture_output=True, env=_ENV)
    except OSError as e:
        raise GitError(f"couldn't run git: {e}") from None
    return (result.returncode, result.stdout.decode("utf-8", "replace").strip(),
            result.stderr.decode("utf-8", "replace").strip())


def is_work_tree(path: str) -> bool:
    return Path(path).is_dir() and git(path, "rev-parse", "--is-inside-work-tree", check=False).strip() == "true"


def top_level(path: str) -> str:
    return str(Path(git(path, "rev-parse", "--show-toplevel").strip()))


def current_branch(repo: str) -> str:
    return git(repo, "rev-parse", "--abbrev-ref", "HEAD", check=False).strip() or "HEAD"


def branch_name(repo: str) -> str | None:
    """The checked-out branch, None for a detached HEAD."""
    return git(repo, "branch", "--show-current", check=False).strip() or None


def work_tree_state(path: str) -> bool:
    """Whether the folder is a git work tree. Unlike is_work_tree, a failure that isn't git saying "not a
    repository" (unsafe ownership, a broken config, a git that won't run) raises GitError instead of reading as "no",
    for callers that delete on a "no"."""
    code, out, err = git_status(path, "rev-parse", "--is-inside-work-tree")
    if code == 0 and out in ("true", "false"):
        return out == "true"
    if code != 0 and "not a git repository" in err:
        return False
    raise GitError(f"couldn't tell whether {path} is a git work tree: {err or 'git printed nothing'}")


def branch_exists(repo: str, branch: str) -> bool:
    """Whether refs/heads/<branch> exists. `rev-parse --verify --quiet` exits 1 for a missing ref; any other failure
    raises GitError rather than reading as "deleted"."""
    code, _, err = git_status(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
    if code in (0, 1):
        return code == 0
    raise GitError(f"couldn't look up branch {branch} in {repo}: {err or 'git failed'}")


def _ref_exists(repo: str, ref: str) -> bool:
    return bool(git(repo, "rev-parse", "--verify", "--quiet", ref + "^{commit}", check=False).strip())


def resolve_base(repo: str, preferred: str | None) -> str:
    """The base ref to diff against: the one asked for, else origin's default branch, else master or main."""
    if preferred:
        if not _ref_exists(repo, preferred):
            raise GitError(f"base ref {preferred!r} not found in {repo}")
        return preferred
    head = git(repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD", check=False).strip()
    for ref in [head, "origin/master", "origin/main", "master", "main"]:
        if ref and _ref_exists(repo, ref):
            return ref
    raise GitError(f"no base ref found in {repo}; pass --base")


def merge_base(repo: str, base_ref: str) -> str:
    return git(repo, "merge-base", base_ref, "HEAD").strip()


def repo_base(registration: dict, repo: dict) -> tuple[str, str]:
    """The ref a registered repo is diffed against and its merge base with HEAD."""
    # Registrations from before per-repo bases have only the review-wide one.
    base = repo["base"] if "base" in repo else registration.get("base")
    base_ref = resolve_base(repo["path"], base)
    return base_ref, merge_base(repo["path"], base_ref)


def file_at(repo: str, rev: str, path: str) -> str:
    return git(repo, "show", f"{rev}:{path}")


def fingerprint(repo: str) -> str:
    """Changes whenever HEAD, the index or any changed file's content does, so the page knows to reload."""
    digest = hashlib.sha256(git(repo, "rev-parse", "HEAD", check=False).encode())
    status = git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all", check=False)
    digest.update(status.encode())
    entries = iter(status.split("\0"))
    for entry in entries:
        if len(entry) <= 3:
            continue
        if "R" in entry[:2] or "C" in entry[:2]:
            next(entries, None)  # a rename or copy's source path follows as a bare token, with no status prefix
        path = Path(repo) / entry[3:]
        try:
            stat = path.stat()
            digest.update(f"{entry[3:]}:{stat.st_mtime_ns}:{stat.st_size}".encode())
        except OSError:
            pass
    return digest.hexdigest()[:16]


def _untracked(repo: str) -> list[str]:
    return [p for p in git(repo, "ls-files", "--others", "--exclude-standard", "-z").split("\0") if p]


def _read_untracked(repo: str, path: str) -> tuple[bytes | None, str | None]:
    full = Path(repo) / path
    try:
        size = full.stat().st_size
        if size > MAX_UNTRACKED_BYTES:
            return None, "too-large"
        data = full.read_bytes()
    except OSError:
        return None, "unreadable"
    if b"\0" in data[:8000]:
        return None, "binary"
    return data, None


def changed_files(repo: str, base: str) -> list[dict]:
    """Every file that differs between <base> and the working tree, plus untracked files, with +/- counts."""
    files: dict[str, dict] = {}
    parts = git(repo, "diff", "--no-color", "--no-ext-diff", "-M", "--name-status", "-z", base).split("\0")
    i = 0
    while i < len(parts) and parts[i]:
        code = parts[i]
        if code[0] in "RC":
            old, new = parts[i + 1], parts[i + 2]
            i += 3
        else:
            old = new = parts[i + 1]
            i += 2
        files[new] = {"path": new, "oldPath": old, "status": code[0], "additions": 0, "deletions": 0,
                      "binary": False, "untracked": False}

    parts = git(repo, "diff", "--no-color", "--no-ext-diff", "-M", "--numstat", "-z", base).split("\0")
    i = 0
    while i < len(parts) and parts[i]:
        added, deleted, path = parts[i].split("\t", 2)
        if path == "":  # a rename: the old and new paths follow
            path = parts[i + 2]
            i += 3
        else:
            i += 1
        entry = files.get(path)
        if entry is None:
            continue
        if added == "-":
            entry["binary"] = True
        else:
            entry["additions"], entry["deletions"] = int(added), int(deleted)

    for path in _untracked(repo):
        data, problem = _read_untracked(repo, path)
        lines = 0 if data is None else len(data.decode("utf-8", "replace").splitlines())
        files[path] = {"path": path, "oldPath": path, "status": "A", "additions": lines, "deletions": 0,
                       "binary": problem == "binary", "untracked": True}

    for entry in files.values():
        entry["sig"] = _signature(repo, entry)
    return sorted(files.values(), key=lambda f: f["path"].lower())


def _signature(repo: str, entry: dict) -> str:
    """Changes when the file's working copy does, so a 'viewed' mark can lapse like GitHub's."""
    try:
        data = (Path(repo) / entry["path"]).read_bytes()
    except OSError:
        data = b"<deleted>"
    return hashlib.sha1(entry["oldPath"].encode() + b"\0" + data).hexdigest()[:12]


def parse_patch(text: str) -> list[dict]:
    """Unified diff body to hunks of numbered lines."""
    hunks: list[dict] = []
    hunk = None
    old_no = new_no = 0
    for raw in text.split("\n"):
        match = _HUNK.match(raw)
        if match:
            old_no, new_no = int(match.group(1)), int(match.group(3))
            hunk = {"oldStart": old_no, "newStart": new_no, "section": match.group(5), "lines": []}
            hunks.append(hunk)
            continue
        if hunk is None or raw == "":
            continue
        tag, body = raw[0], raw[1:]
        if tag == " ":
            hunk["lines"].append({"t": "ctx", "o": old_no, "n": new_no, "s": body})
            old_no += 1
            new_no += 1
        elif tag == "-":
            hunk["lines"].append({"t": "del", "o": old_no, "n": None, "s": body})
            old_no += 1
        elif tag == "+":
            hunk["lines"].append({"t": "add", "o": None, "n": new_no, "s": body})
            new_no += 1
        elif tag == "\\" and hunk["lines"]:
            hunk["lines"][-1]["noNewline"] = True
    return hunks


def file_diff(repo: str, base: str, entry: dict, context: int = 3) -> dict:
    result = {"path": entry["path"], "oldPath": entry["oldPath"], "status": entry["status"],
              "binary": entry["binary"], "hunks": [], "truncated": False}
    if entry["binary"]:
        return result

    if entry["untracked"]:
        data, problem = _read_untracked(repo, entry["path"])
        if data is None:
            result["binary"] = problem == "binary"
            result["truncated"] = problem == "too-large"
            return result
        text = data.decode("utf-8", "replace")
        lines = text.split("\n")
        no_newline = not text.endswith("\n")
        if not no_newline:
            lines = lines[:-1]
        hunk_lines = [{"t": "add", "o": None, "n": n + 1, "s": line.rstrip("\r")} for n, line in enumerate(lines)]
        if hunk_lines and no_newline:
            hunk_lines[-1]["noNewline"] = True
        if hunk_lines:
            result["hunks"] = [{"oldStart": 0, "newStart": 1, "section": "", "lines": hunk_lines}]
        return result

    paths = [entry["oldPath"], entry["path"]] if entry["oldPath"] != entry["path"] else [entry["path"]]
    text = git(repo, "diff", "--no-color", "--no-ext-diff", "--no-textconv", "-M", f"-U{context}",
               "--src-prefix=a/", "--dst-prefix=b/", base, "--", *paths)
    if len(text) > MAX_DIFF_BYTES:
        result["truncated"] = True
        return result
    if "\nBinary files " in text or text.startswith("Binary files "):
        result["binary"] = True
        return result
    result["hunks"] = parse_patch(text)
    for hunk in result["hunks"]:
        for line in hunk["lines"]:
            line["s"] = line["s"].rstrip("\r")
    return result
