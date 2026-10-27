#!/usr/bin/env python3
"""Find and delete the local `<user.handle>/*` branches whose PRs have merged.

  branches.py scan [repo...] [--except repo...] [--branch B] [--out plan.json]
  branches.py delete --plan plan.json [--branch B]

scan is read-only (it fetches a PR's head ref when it needs to). Repos default to every repo of the workspace (the root
for a single-repo workspace). --branch limits it to one branch name.

A squash- or rebase-merged branch's own commits are never reachable from the default branch, so `git branch -d`,
`git cherry` and tree comparison all call it unmerged. So the PR decides. A branch is eligible only when it has a MERGED
PR and its local tip is that PR's headRefOid or an ancestor of it (checked against refs/pull/<n>/head, which GitHub
keeps after the branch is deleted): behind is fine, since every local commit is still in what merged. Everything else
is kept, with a reason:
  No PR, PR open, PR closed (not merged), Local tip ahead of PR (local commits in no PR), Checked out (the current
  branch, or checked out in a worktree: `worktrees.py remove` clears those), gh error (never delete unverified)

With `pr.required: false` no PR exists to decide: a branch is eligible when its tip is an ancestor of origin/<default>,
which is what a direct merge leaves.

delete re-checks each eligible branch (still at the scanned SHA, not checked out anywhere) and runs `git branch -D`,
which squash merges need; the scan is what makes it safe. It only ever deletes local branches.

Exit codes: 0 done, 2 bad input, 3 a delete failed.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import config  # noqa: E402
import gitgh  # noqa: E402
from gitgh import OK, PRECHECK_FAILED, STEP_FAILED, Failed, emit, git, git_ok, prs_for_branch  # noqa: E402


def checked_out(main: Path) -> dict:
    """branch -> path of the worktree (main or linked) that has it checked out."""
    found, path = {}, None
    for line in git(main, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            path = line.removeprefix("worktree ")
        elif line.startswith("branch refs/heads/"):
            found[line.removeprefix("branch refs/heads/")] = path
    return found


def classify(cfg: config.Config, co: gitgh.Checkout, name: str, sha: str) -> dict:
    main = co.main
    entry = {"repo": co.name, "path": str(main), "branch": name, "sha": sha}
    if not cfg.pr["required"]:
        if git_ok(main, "merge-base", "--is-ancestor", sha, f"origin/{cfg.default_branch}"):
            return {**entry, "pr": None, "behind": 0}
        return {**entry, "keep": "Not in origin/" + cfg.default_branch}
    try:
        prs = prs_for_branch(co.gh_repo, name)
    except Failed as e:
        return {**entry, "keep": "gh error", "detail": str(e)}
    merged = [p for p in prs if p["state"] == "MERGED"]
    for pr in merged:
        head = pr["headRefOid"]
        if sha == head:
            return {**entry, "pr": pr["number"], "url": pr["url"], "behind": 0}
        git(main, "fetch", "origin", f"refs/pull/{pr['number']}/head", check=False, timeout=90)
        if git_ok(main, "merge-base", "--is-ancestor", sha, head):
            behind = int(git(main, "rev-list", "--count", f"{sha}..{head}"))
            return {**entry, "pr": pr["number"], "url": pr["url"], "behind": behind}
    if merged:
        return {**entry, "keep": "Local tip ahead of PR", "pr": merged[0]["number"]}
    if any(p["state"] == "OPEN" for p in prs):
        return {**entry, "keep": "PR open"}
    if prs:
        return {**entry, "keep": "PR closed, not merged"}
    return {**entry, "keep": "No PR"}


def scan(cfg: config.Config, repo_names: list[str] | None = None, only_branch: str | None = None) -> dict:
    checkouts = gitgh.resolve_all(cfg, repo_names) if repo_names is not None else gitgh.repos(cfg)
    prefix = f"{cfg.handle}/"
    jobs, kept = [], []
    for co in checkouts:
        main = co.main
        refs = git(main, "for-each-ref", "--format=%(refname:short) %(objectname)", f"refs/heads/{prefix}*")
        busy = checked_out(main)
        for line in refs.splitlines():
            name, sha = line.split()
            if only_branch and name != only_branch:
                continue
            if name in busy:
                where = "" if Path(busy[name]) == main else f" in worktree {busy[name]}"
                kept.append({"repo": co.name, "path": str(main), "branch": name, "sha": sha,
                             "keep": f"Checked out{where}"})
                continue
            jobs.append((co, name, sha))
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda job: classify(cfg, *job), jobs))
    eligible = [r for r in results if "keep" not in r]
    kept += [r for r in results if "keep" in r]
    return {"repos": [c.name for c in checkouts], "eligible": eligible,
            "kept": sorted(kept, key=lambda r: (r["repo"], r["branch"]))}


def delete(plan: dict, only_branch: str | None = None) -> tuple[dict, int]:
    out = {"deleted": [], "skipped": []}
    for entry in plan["eligible"]:
        if only_branch and entry["branch"] != only_branch:
            continue
        main, name = Path(entry["path"]), entry["branch"]
        now = git(main, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}", check=False)
        if now != entry["sha"]:
            out["skipped"].append({**entry, "reason": "gone" if not now else "moved since the scan"})
            continue
        if name in checked_out(main):
            out["skipped"].append({**entry, "reason": "checked out since the scan"})
            continue
        try:
            git(main, "branch", "-D", name)
        except Failed as e:
            out.update(error=str(e), stopped_at=f"{entry['repo']} {name}")
            return out, STEP_FAILED
        out["deleted"].append({k: entry.get(k) for k in ("repo", "branch", "sha", "pr", "behind")})
    return out, OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("repos", nargs="*", help="default: every repo of the workspace")
    s.add_argument("--except", dest="skip", nargs="+", default=[], metavar="REPO")
    s.add_argument("--branch", help="only this branch name")
    s.add_argument("--out", help="also write the plan here, for delete --plan")
    d = sub.add_parser("delete")
    d.add_argument("--plan", required=True, help="a scan's JSON output")
    d.add_argument("--branch", help="only this branch name")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "scan":
            cfg = config.load()
            names = [r for r in (args.repos or [c.name for c in gitgh.repos(cfg)]) if r not in args.skip]
            plan = scan(cfg, names, args.branch)
            if args.out:
                Path(args.out).write_text(json.dumps(plan, indent=2))
            return emit(plan, OK)
        return emit(*delete(json.loads(Path(args.plan).read_text()), args.branch))
    except (Failed, config.ConfigError) as e:
        return emit({"error": str(e)}, PRECHECK_FAILED)


if __name__ == "__main__":
    sys.exit(main())
