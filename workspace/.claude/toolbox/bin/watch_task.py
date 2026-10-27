#!/usr/bin/env python3
"""Watch a task's PRs, and after a merge the default branch's runs of its merge commits, so the user never has to relay
"it's approved", "CI is done" or "it's published". Read-only: it never merges, comments, re-runs or writes the record.

  watch_task.py status   <work id | record path> [--since <ISO time>]
  watch_task.py prs      <work id | record path> [--interval 120] [--max-hours 24]
  watch_task.py pipeline <work id | record path> [--max-hours 3]

The PRs are the task record's `prs` (tasks.py's records find it by work id).

status    one snapshot of every PR in `prs` that's still open: review decision, checks, merge state, and the comments
          and reviews with text from anyone but the user and the bots (with --since, only those posted after it).
          Bots are GitHub apps, `*[bot]` logins, a few that comment on every PR, and the config's `pr.bots`.
          `overall` is `merged` (none open), `ready` (every open PR approved, its checks reported and all green)
          or `waiting`.
prs       the background watcher (start it with run_in_background). It exits at once when `overall` is already
          `merged` or `ready`; otherwise it polls every --interval seconds and exits on the first change worth waking
          the session for, printing the events and the snapshot:
            ready              every open PR approved and green
            merged, closed     a PR that was open no longer is (the others may still be open)
            changes-requested  a PR's review decision became CHANGES_REQUESTED
            ci-failed          a check that wasn't failing now is
            comments           a comment, or a review with text or inline comments, from anyone else since it started
            timeout            --max-hours passed with nothing to report; start it again
          A failed gh call is retried at the next poll; three in a row exit 3.
pipeline  once every PR in `prs` is merged: for each, the default branch's runs of its merge commit (`gh run list
          --commit <sha>`, events push and workflow_dispatch, the newest per workflow), waiting up to 15 minutes for
          one to appear, and watches each (`gh run watch --exit-status`). A task with no `prs` (no `pr.required`)
          uses the commits publish.py pushed to the default branch, from the publish folder's `merged.json`. A green
          run means whatever the default branch's pipeline publishes is out; `tags` names the tags on the merge commit
          (a build number, say). After a failure, a re-run or a dispatch on the same commit is picked up by running it
          again. work-task's release wait, between its shared-library and consumers cycles.

Exit codes: 0 an event (prs), a snapshot (status) or every pipeline green, 2 bad input, 3 a pipeline failed or didn't
start, or gh kept failing.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import config  # noqa: E402
import gitgh  # noqa: E402
import records  # noqa: E402
from gitgh import OK, PRECHECK_FAILED, STEP_FAILED, Failed, emit, gh, gh_json, run  # noqa: E402

PR_URL = re.compile(r"github\.com/([^/]+/[^/]+)/pull/(\d+)")
FIELDS = "number,url,title,state,reviewDecision,mergeStateStatus,statusCheckRollup,comments,reviews"
GREEN = {"SUCCESS", "NEUTRAL", "SKIPPED"}
PENDING = {"PENDING", "EXPECTED", "QUEUED", "IN_PROGRESS", "WAITING", "REQUESTED", ""}
RUN_EVENTS = {"push", "workflow_dispatch"}
BODY_CHARS = 500
# Apps that comment on every PR. gh's GraphQL authors carry no bot flag (the REST API's inline comments do: type Bot).
BOTS = {"copilot-pull-request-reviewer", "github-actions", "dependabot"}


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def task_prs(cfg: config.Config, ref: str) -> tuple[dict, list[tuple[str, str]]]:
    record = records.Store(cfg).find(ref)
    pairs = []
    for url in record.fields.get("prs") or []:
        m = PR_URL.search(str(url))
        if not m:
            raise records.TaskError(f"not a GitHub PR URL in prs: {url}")
        pairs.append((m.group(1), m.group(2)))
    return {"task": record.work_id, "doc": str(record.path), "status": record.status,
            "stage": record.fields.get("stage")}, pairs


def check_state(check: dict) -> str:
    """green, pending or failing, for a CheckRun (status, conclusion) or a StatusContext (state)."""
    if check.get("__typename") == "CheckRun" or "conclusion" in check:
        if (check.get("status") or "") != "COMPLETED":
            return "pending"
        result = check.get("conclusion") or ""
    else:
        result = check.get("state") or ""
    return "green" if result in GREEN else "pending" if result in PENDING else "failing"


def item(author: str, kind: str, body: str, url: str, **extra) -> dict:
    body = (body or "").strip()
    return {"author": author, "kind": kind, **extra,
            "body": body if len(body) <= BODY_CHARS else body[:BODY_CHARS] + "…", "url": url}


def pr_snapshot(repo: str, number: str, me: str, bots: set, since: str | None) -> dict:
    pr = gh_json("pr", "view", number, "--repo", repo, "--json", FIELDS)
    rollup = pr.get("statusCheckRollup") or []
    states = {(c.get("name") or c.get("context") or "?"): check_state(c) for c in rollup}
    failing = sorted(n for n, s in states.items() if s == "failing")
    pending = sorted(n for n, s in states.items() if s == "pending")
    snap = {"repo": repo.split("/")[1], "number": pr["number"], "url": pr["url"], "state": pr["state"],
            "review": pr.get("reviewDecision") or "NONE", "merge_state": pr.get("mergeStateStatus"),
            "checks": "failing" if failing else "pending" if pending else "green" if rollup else "none",
            "failing": failing, "pending": len(pending)}
    if pr["state"] != "OPEN":
        return snap

    def fresh(author: dict, at: str | None) -> bool:
        login = (author or {}).get("login") or ""
        bot = login in bots or login.endswith("[bot]") or (author or {}).get("type") == "Bot"
        return login != me and not bot and (since is None or (at or "") > since)

    def who(author: dict | None) -> str:
        return (author or {}).get("login") or "ghost"  # a deleted account has no author

    found = [item(who(c.get("author")), "comment", c.get("body"), c.get("url"))
             for c in pr.get("comments") or [] if fresh(c.get("author"), c.get("createdAt"))]
    inline = False
    for r in pr.get("reviews") or []:
        if not fresh(r.get("author"), r.get("submittedAt")):
            continue
        if (r.get("body") or "").strip():
            found.append(item(who(r.get("author")), "review", r["body"], r.get("url") or pr["url"],
                              state=r.get("state")))
        if r.get("state") in ("COMMENTED", "CHANGES_REQUESTED"):
            inline = True  # inline comments come only through the REST API
    if inline:
        for c in gh_json("api", "--paginate", f"repos/{repo}/pulls/{number}/comments") or []:
            if fresh(c.get("user"), c.get("created_at")):
                found.append(item(who(c.get("user")), "inline", c.get("body"), c.get("html_url"),
                                  path=c.get("path"), line=c.get("line") or c.get("original_line")))
    snap["comments"] = found
    return snap


def snapshot(pairs: list[tuple[str, str]], me: str, bots: set, since: str | None, only: set | None = None) -> dict:
    prs = [pr_snapshot(repo, number, me, bots, since) for repo, number in pairs
           if only is None or (repo, number) in only]
    open_prs = [p for p in prs if p["state"] == "OPEN"]
    if not open_prs:
        overall = "merged"
    # "none" isn't green: right after a push, the new head has no checks yet.
    elif all(p["review"] == "APPROVED" and p["checks"] == "green" for p in open_prs):
        overall = "ready"
    else:
        overall = "waiting"
    return {"overall": overall, "prs": prs, "at": now()}


def changes(base: dict, snap: dict) -> list[dict]:
    before = {p["url"]: p for p in base["prs"]}
    events = []
    for p in snap["prs"]:
        b = before.get(p["url"])
        if not b or b["state"] != "OPEN":
            continue
        tag = {"repo": p["repo"], "number": p["number"], "url": p["url"]}
        if p["state"] != "OPEN":
            events.append({"event": p["state"].lower(), **tag})
            continue
        if p["review"] == "CHANGES_REQUESTED" and b["review"] != "CHANGES_REQUESTED":
            events.append({"event": "changes-requested", **tag})
        new_failing = sorted(set(p["failing"]) - set(b["failing"]))
        if new_failing:
            events.append({"event": "ci-failed", **tag, "checks": new_failing})
        if p.get("comments"):
            events.append({"event": "comments", **tag, "count": len(p["comments"])})
    if snap["overall"] == "ready" and base["overall"] != "ready":
        events.insert(0, {"event": "ready"})
    return events


def login() -> str:
    return gh("api", "user", "--jq", ".login")


def bots_of(cfg: config.Config) -> set:
    return BOTS | set(cfg.pr.get("bots") or [])


def cmd_status(cfg: config.Config, args) -> tuple[dict, int]:
    out, pairs = task_prs(cfg, args.task)
    return {**out, **snapshot(pairs, login(), bots_of(cfg), args.since)}, OK


def cmd_prs(cfg: config.Config, args) -> tuple[dict, int]:
    out, pairs = task_prs(cfg, args.task)
    if not pairs:
        return {**out, "error": "the task has no prs"}, PRECHECK_FAILED
    me, bots, started = login(), bots_of(cfg), now()
    base = snapshot(pairs, me, bots, started)
    if base["overall"] in ("merged", "ready"):
        return {**out, "events": [{"event": base["overall"]}], **base}, OK
    watched = {PR_URL.search(p["url"]).groups() for p in base["prs"] if p["state"] == "OPEN"}
    deadline, failures = time.monotonic() + args.max_hours * 3600, 0
    while time.monotonic() < deadline:
        time.sleep(args.interval)
        try:
            snap = snapshot(pairs, me, bots, started, watched)
        except Failed as e:
            failures += 1
            if failures >= 3:
                return {**out, "error": f"gh failed three polls in a row: {e}", **base}, STEP_FAILED
            continue
        failures = 0
        events = changes(base, snap)
        if events:
            return {**out, "events": events, "since": started, **snap}, OK
    return {**out, "events": [{"event": "timeout", "hours": args.max_hours}], **base}, OK


def default_runs(repo: str, sha: str, branch: str) -> list[dict]:
    runs = gh_json("run", "list", "--repo", repo, "--commit", sha, "--json",
                   "databaseId,workflowName,event,headBranch,status,conclusion,url,createdAt,number,attempt") or []
    newest = {}
    for r in sorted(runs, key=lambda r: r["createdAt"]):
        if r["event"] in RUN_EVENTS and r["headBranch"] == branch:
            newest[r["workflowName"]] = r
    return list(newest.values())


def merged_commits(cfg: config.Config, wid: str) -> list[tuple[str, str]]:
    """(owner/name, sha) for each commit publish.py pushed straight to the default branch, from merged.json."""
    path = records.Store(cfg).publish_dir(wid) / "merged.json"
    try:
        merged = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except ValueError as e:
        raise records.TaskError(f"{path}: not JSON: {e}") from None
    return [(gitgh.resolve(cfg, repo).gh_repo, sha) for repo, sha in sorted(merged.items())]


def cmd_pipeline(cfg: config.Config, args) -> tuple[dict, int]:
    out, pairs = task_prs(cfg, args.task)
    commits = []
    for repo, number in pairs:
        pr = gh_json("pr", "view", number, "--repo", repo, "--json", "state,mergeCommit,url")
        sha = (pr.get("mergeCommit") or {}).get("oid")
        if pr["state"] != "MERGED" or not sha:
            return {**out, "error": f"{pr['url']} is {pr['state']}, not merged"}, PRECHECK_FAILED
        commits.append((repo, sha, pr["url"]))
    if not pairs:
        commits = [(repo, sha, None) for repo, sha in merged_commits(cfg, out["task"])]
    if not commits:
        return {**out, "error": "the task has no prs, and publish.py recorded no merged commits"}, PRECHECK_FAILED
    out["pipelines"] = []
    for repo, sha, url in commits:
        appear_by = time.monotonic() + args.appear_minutes * 60
        while not (runs := default_runs(repo, sha, cfg.default_branch)):
            if time.monotonic() > appear_by:
                return {**out, "error": f"no {cfg.default_branch} run for {repo}@{sha[:7]} after "
                                        f"{args.appear_minutes:g} minutes"}, STEP_FAILED
            time.sleep(30)
        tags = None
        for r in runs:
            if r["status"] != "completed":
                run(["gh", "run", "watch", str(r["databaseId"]), "--repo", repo, "--exit-status", "--interval", "30"],
                    cfg.root, int(args.max_hours * 3600), check=False)
            done = gh_json("run", "view", str(r["databaseId"]), "--repo", repo, "--json",
                           "status,conclusion,url,number,attempt,workflowName")
            entry = {"repo": repo.split("/")[-1], "pr": url, "merge_commit": sha[:7], **done}
            out["pipelines"].append(entry)
            if done.get("conclusion") != "success":
                return {**out, "error": f"{repo} {done['workflowName']} #{done.get('number')}: "
                                        f"{done.get('conclusion') or done.get('status')}"}, STEP_FAILED
            tags = tags if tags is not None else commit_tags(cfg, repo, sha)
            entry["tags"] = tags
    return out, OK


def commit_tags(cfg: config.Config, repo: str, sha: str) -> list[str] | str:
    """The tags on the merge commit (the run's own number usually isn't the build number). Build tags are often
    annotated, so this asks the main checkout's origin, whose `^{}` lines name the commit each tag points at."""
    checkout = cfg.root / repo.split("/")[1] if cfg.nested else cfg.root
    if not (checkout / ".git").exists():
        return f"no main checkout at {checkout} to look up tags"
    proc = run(["git", "ls-remote", "--tags", "origin"], checkout, 90, check=False)
    if proc.returncode != 0:
        return f"git ls-remote failed: {(proc.stderr or proc.stdout).strip()}"
    tags = {ref.removeprefix("refs/tags/").removesuffix("^{}")
            for line in proc.stdout.splitlines() for obj, _, ref in [line.partition("\t")] if obj == sha}
    return sorted(tags)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("status")
    p.add_argument("task")
    p.add_argument("--since", help="only comments and reviews after this ISO time")
    p = sub.add_parser("prs")
    p.add_argument("task")
    p.add_argument("--interval", type=float, default=120, help="seconds between polls")
    p.add_argument("--max-hours", type=float, default=24)
    p = sub.add_parser("pipeline")
    p.add_argument("task")
    p.add_argument("--max-hours", type=float, default=3, help="per run")
    p.add_argument("--appear-minutes", type=float, default=15, help="how long to wait for a run to start")
    args = parser.parse_args(argv)
    try:
        cfg = config.load()
        return emit(*{"status": cmd_status, "prs": cmd_prs, "pipeline": cmd_pipeline}[args.cmd](cfg, args))
    except (config.ConfigError, records.TaskError) as e:
        return emit({"error": str(e)}, PRECHECK_FAILED)
    except Failed as e:
        return emit({"error": str(e)}, STEP_FAILED)


if __name__ == "__main__":
    sys.exit(main())
