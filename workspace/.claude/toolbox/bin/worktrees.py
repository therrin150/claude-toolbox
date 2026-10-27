#!/usr/bin/env python3
"""A task's git worktrees: one per repo under `<workspace.worktrees>/<work id>/<repo>` in a nested workspace, or
`<workspace.worktrees>/<work id>` in a single-repo one, on the branch `git.branch` names.

<work id> is the task's ticket key when it has one, else a kebab-case slug of its summary or title. The branch is
`git.branch` filled in: `{handle}/{key}-{description}` for a key (--desc or --branch required), `{handle}/{description}`
for a slug (--desc, else the id itself).

Never touches a main checkout's working tree, never stashes, never deletes a branch, never forces. Every command prints
one JSON object; exit 0 when every repo succeeded, 1 when any repo was refused or failed (the others still ran).

Commands:
  create <id> [--repos a,b] [--desc kebab-description | --branch full/branch-name]
      Per repo: "exists" (already set up), "attached" (local branch reused), "from-remote" (remote branch resumed),
      or "created" (new branch from the latest origin/<default>, with no upstream). --repos is required in a nested
      workspace and ignored in a single-repo one.
  remove <id> [--repos a,b]
      Per repo: "removed", or "refused" when the worktree has uncommitted or untracked files. Branches are kept. A
      leftover folder that is no longer a worktree is "refused" too, with its files listed. The <id> folder goes once
      it's empty.
  list [id]
      Every task worktree: id, repo, branch, dirty file count, commits ahead of origin/<default>, and whether the
      remote branch exists.
  changes <id>
      Per worktree, its whole change against the branch point (the merge-base with origin/<default>): committed,
      uncommitted and untracked files alike, read without touching the index. `diff` is the stat line a stop report
      shows ("4 files, +50 −7"). `areas` are the areas whose checks apply, as `<area>=<dir>` for run_tests.py: in a
      nested workspace the repo, in a single one each configured area folder holding a changed file, plus, when the
      config has a "*" area, the workspace itself for changed files outside every area folder. An area's `files` globs
      (fnmatch, against the path from the area's folder or the file's name) narrow what counts; an area with changes
      but none matching is listed under `skipped`.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import config  # noqa: E402
import gitgh  # noqa: E402
from gitgh import Failed, git, git_ok, worktree_path  # noqa: E402

FOLDER_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def checked_out_at(main: Path, branch: str) -> str | None:
    """Path of the worktree (main or linked) that has the branch checked out, if any."""
    path = None
    for line in git(main, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            path = line.removeprefix("worktree ")
        elif line == f"branch refs/heads/{branch}":
            return path
    return None


def create_one(cfg: config.Config, wid: str, co: gitgh.Checkout, branch: str) -> dict:
    main, path = co.path, worktree_path(cfg, wid, co.name)
    result = {"repo": co.name, "path": str(path), "branch": branch}
    if path.exists():
        here = git(path, "branch", "--show-current", check=False)
        if here == branch:
            return {**result, "status": "exists"}
        return {**result, "status": "failed", "reason": f"{path} exists but is on {here or 'no branch'}"}
    try:
        git(main, "fetch", "--quiet", "origin")
    except Failed as e:
        return {**result, "status": "failed", "reason": f"git fetch failed: {e}"}
    path.parent.mkdir(parents=True, exist_ok=True)
    base = f"origin/{cfg.default_branch}"
    if git_ok(main, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"):
        elsewhere = checked_out_at(main, branch)
        if elsewhere:
            return {**result, "status": "failed", "reason": f"{branch} is already checked out at {elsewhere}"}
        args, status = ["worktree", "add", str(path), branch], "attached"
    elif git_ok(main, "show-ref", "--verify", "--quiet", f"refs/remotes/origin/{branch}"):
        args, status = ["worktree", "add", "--track", "-b", branch, str(path), f"origin/{branch}"], "from-remote"
    else:
        args, status = ["worktree", "add", "--no-track", "-b", branch, str(path), base], "created"
    try:
        git(main, *args)
    except Failed as e:
        return {**result, "status": "failed", "reason": str(e)}
    return {**result, "status": status, "head": git(path, "rev-parse", "--short", "HEAD")}


def remove_path(path: Path, repo: str, main: Path) -> dict:
    """Remove one linked worktree, refusing one with uncommitted or untracked files. Never a main checkout."""
    result = {"repo": repo, "path": str(path)}
    if not path.exists():
        return {**result, "status": "absent"}
    if not (path / ".git").exists():
        files = sorted(str(p.relative_to(path)) for p in path.iterdir())
        return {**result, "status": "refused", "reason": "leftover folder is not a worktree", "files": files}
    if path.resolve() == main.resolve():
        return {**result, "status": "refused", "reason": "a main checkout is never removed"}
    dirty = gitgh.status_lines(path)
    if dirty:
        return {**result, "status": "refused", "reason": f"{len(dirty)} uncommitted or untracked file(s)",
                "files": dirty}
    name = git(path, "branch", "--show-current", check=False)
    try:
        git(main, "worktree", "remove", str(path))
    except Failed as e:
        return {**result, "status": "refused", "reason": str(e)}
    git(main, "worktree", "prune", check=False)
    return {**result, "status": "removed", "branch": name, "note": "branch kept"}


def remove_one(cfg: config.Config, wid: str, co: gitgh.Checkout) -> dict:
    return remove_path(worktree_path(cfg, wid, co.name), co.name, co.main)


def remove(cfg: config.Config, wid: str, repo_names: list[str] | None = None) -> list[dict]:
    """Remove a task's worktrees (all of them, or the named repos'), then its folder when empty."""
    base = cfg.worktrees_dir / wid
    if cfg.nested:
        existing = sorted(p.name for p in base.iterdir() if p.is_dir()) if base.is_dir() else []
        names = repo_names if repo_names else existing
        by_name = {c.name: c for c in gitgh.repos(cfg)}
        results = []
        for name in names:
            co = by_name.get(name)
            if co is None:
                results.append({"repo": name, "status": "refused", "reason": "not a repo of this workspace"})
            else:
                results.append(remove_one(cfg, wid, co))
    else:
        results = [remove_one(cfg, wid, gitgh.repos(cfg)[0])]
    if base.is_dir() and not any(base.iterdir()):
        base.rmdir()
    return results


def describe(cfg: config.Config, co: gitgh.Checkout, wid: str) -> dict:
    name = git(co.path, "branch", "--show-current", check=False)
    ahead = git(co.path, "rev-list", "--count", f"origin/{cfg.default_branch}..HEAD", check=False)
    remote = git_ok(co.path, "show-ref", "--verify", "--quiet", f"refs/remotes/origin/{name}") if name else False
    return {
        "id": wid,
        "repo": co.name,
        "path": str(co.path),
        "branch": name,
        "dirty": len(git(co.path, "status", "--porcelain", check=False).splitlines()),
        "ahead_of_default": int(ahead) if ahead.isdigit() else None,
        "remote_branch": remote,
    }


def listing(cfg: config.Config, wid: str | None) -> list[dict]:
    base = cfg.worktrees_dir
    folders = [base / wid] if wid else (sorted(p for p in base.iterdir() if p.is_dir()) if base.is_dir() else [])
    return [describe(cfg, co, folder.name) for folder in folders for co in gitgh.task_worktrees(cfg, folder.name)]


def changed_files(path: Path, base: str) -> list[dict]:
    """Every file changed since the merge-base with `base`, tracked or not: path, insertions, deletions (None for a
    binary file). Untracked files count as all insertions; the index is never touched."""
    point = git(path, "merge-base", base, "HEAD")
    files = []
    for line in git(path, "diff", "--numstat", "--no-renames", point).splitlines():
        added, deleted, name = line.split("\t", 2)
        files.append({"path": name, "insertions": None if added == "-" else int(added),
                      "deletions": None if deleted == "-" else int(deleted)})
    for name in git(path, "ls-files", "--others", "--exclude-standard", "-z").split("\0"):
        if not name:
            continue
        data = (path / name).read_bytes() if (path / name).is_file() else b""
        lines = None if b"\0" in data else data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
        files.append({"path": name, "insertions": lines, "deletions": None if lines is None else 0})
    return files


def stat_line(files: list[dict]) -> str:
    plus = sum(f["insertions"] or 0 for f in files)
    minus = sum(f["deletions"] or 0 for f in files)
    return f"{len(files)} file{'' if len(files) == 1 else 's'}, +{plus} −{minus}"


def matches(rel: str, globs: list[str]) -> bool:
    return not globs or any(fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(Path(rel).name, g) for g in globs)


def area_hits(cfg: config.Config, co: gitgh.Checkout, files: list[dict]) -> tuple[list[dict], list[dict]]:
    """The areas a checkout's changed files touch: (applies, skipped), each {area, dir, files}."""
    groups: dict[str, tuple[Path, list[str]]] = {}
    if cfg.nested:
        groups[co.name] = (co.path, [f["path"] for f in files])
    else:
        listed = [a for a in cfg.data["workspace"]["areas"] if a != config.ANY_AREA]
        for f in files:
            area = next((a for a in listed if f["path"].startswith(a.rstrip("/") + "/")), None)
            if area:
                groups.setdefault(area, (co.path / area, []))[1].append(f["path"][len(area.rstrip("/")) + 1:])
            elif config.ANY_AREA in cfg.data["workspace"]["areas"]:
                groups.setdefault(cfg.name, (co.path, []))[1].append(f["path"])
    applies, skipped = [], []
    for area, (folder, rels) in sorted(groups.items()):
        globs = cfg.area_files(area)
        hits = [r for r in rels if matches(r, globs)]
        entry = {"area": area, "dir": str(folder), "files": len(hits)}
        if hits:
            applies.append(entry)
        else:
            skipped.append({**entry, "files": len(rels), "reason": f"no changed file matches {', '.join(globs)}"})
    return applies, skipped


def changes(cfg: config.Config, wid: str) -> dict:
    checkouts = gitgh.task_worktrees(cfg, wid)
    if not checkouts:
        raise Failed(f"no worktrees for {wid} under {cfg.worktrees_dir}")
    out = {"id": wid, "checkouts": [], "areas": [], "skipped": []}
    for co in checkouts:
        files = changed_files(co.path, f"origin/{cfg.default_branch}")
        applies, skipped = area_hits(cfg, co, files)
        out["checkouts"].append({"repo": co.name, "path": str(co.path), "branch": branch_name(co.path),
                                 "diff": stat_line(files), "files": [f["path"] for f in files],
                                 "areas": [a["area"] for a in applies]})
        out["areas"] += [f"{a['area']}={a['dir']}" for a in applies]
        out["skipped"] += skipped
    return out


def branch_name(path: Path) -> str:
    return git(path, "branch", "--show-current", check=False)


def emit(results: list, **extra) -> int:
    print(json.dumps({**extra, "results": results}, indent=2))
    return 1 if any(r.get("status") in ("failed", "refused") for r in results) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("id", help="ticket key, or a slug of the summary or title")
    create.add_argument("--repos", help="comma-separated repo names (nested workspaces)")
    name = create.add_mutually_exclusive_group()
    name.add_argument("--desc")
    name.add_argument("--branch")
    remove_cmd = commands.add_parser("remove")
    remove_cmd.add_argument("id")
    remove_cmd.add_argument("--repos")
    listing_cmd = commands.add_parser("list")
    listing_cmd.add_argument("id", nargs="?")
    changes_cmd = commands.add_parser("changes")
    changes_cmd.add_argument("id")
    args = parser.parse_args(argv)

    try:
        cfg = config.load()
        if args.command == "create":
            gitgh.check_work_id(args.id)
            branch = args.branch or gitgh.branch_for(cfg, args.id, args.desc)
            if cfg.nested:
                if not args.repos:
                    raise Failed("--repos is required in a nested workspace")
                checkouts = gitgh.resolve_all(cfg, [r.strip() for r in args.repos.split(",") if r.strip()])
            else:
                checkouts = gitgh.repos(cfg)
            return emit([create_one(cfg, args.id, co, branch) for co in checkouts], id=args.id, branch=branch)
        if args.id and not FOLDER_ID.fullmatch(args.id):
            raise Failed(f"{args.id!r} is not a worktrees folder name")
        if args.command == "changes":
            print(json.dumps(changes(cfg, args.id), indent=2))
            return 0
        if args.command == "remove":
            names = [r.strip() for r in args.repos.split(",") if r.strip()] if args.repos else None
            return emit(remove(cfg, args.id, names), id=args.id)
        return emit(listing(cfg, args.id))
    except (Failed, config.ConfigError) as e:
        print(json.dumps({"error": str(e)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
