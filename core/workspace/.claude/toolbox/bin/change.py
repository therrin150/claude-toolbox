#!/usr/bin/env python3
"""The gates of the `start-change`, `check-change`, `review-change`, `publish-change` and `finish-change` stage skills,
so any session gets the same refusals.

Each stage checks only its own requirements, never that an earlier stage ran. Every command prints one JSON object;
exit 0 when it went ahead, 1 when it refused or failed (the reason prints as `refused` or `error`), 2 bad arguments.

  start (<ref> | --summary <text>) [--repos a,b] [--desc kebab-description] [--description <text>]
      Find or make the change's task record, then give it a branch and a worktree per repo (as worktrees.py create).
      <ref> is a work id (a key, a slice's <KEY>.<n>, a keyless record's slug) or a record's path. --summary names an
      ad-hoc change instead: the record whose slug it makes, or, with none, a new keyless `task` record (any tracker)
      whose Description is --description, else the summary. Refuses, per repo, when the record already has a worktree
      for that repo on disk, naming it (the other repos are still created), and refuses a record that is `done`.
      Arguments are checked before anything is written, so a bad call makes no record.
      --repos is required in a nested workspace; --desc is required for a record with a key. A record still `todo`
      becomes `in-progress`, stage `implementing`, owner `claude`, once a worktree exists.

  check <work id> <build|unit|format|integration>
      The areas to hand a `check-runner`, from the task's worktrees (as worktrees.py changes). Refuses when there is
      no change to check: no worktree, no changed file, or changes only in areas whose `files` globs the checks
      ignore. For integration, also refuses while the services lock is held, by another task or this change's own
      earlier run (the runner would be blocked either way; an expired lock is taken over by the run), naming the holder. Runs nothing itself.

  review (<work id> | --checkouts a,b)
      The checkouts to hand `local-review-ui`. Refuses when there is nothing to review: no worktree, or no changed file
      against origin/<default>. Opens nothing itself.

  publish <work id> [--checkouts a,b] [--draft]
      The gate before `publish.py`. Looks only at the checkouts that changed (a nested change's untouched repo is
      neither published nor drafted for). Refuses, naming the checkout, when one is on the default branch (or detached;
      with no changes anywhere, any checkout on it), when there is nothing to publish (no worktree, no changed file),
      and, without --draft, when a changed checkout has no approved
      commit message (the file `publish.py` reads, a valid one). --draft is the check before the message exists: the
      same refusals but the message, plus where each message goes. Warns, never refuses, for an area the changes touch
      with no passing build, unit or integration run on the diff as it is now (`run_checks.py` records them). Runs
      nothing itself.

  finish (<work id> | --checkouts a,b) [--no-record]
      The gate before `publish.py merge` and `cleanup`. Refuses, naming the checkout and the reason, before anything is
      merged or removed, when there is no worktree to finish, a checkout is on the default branch (or detached), a
      checkout has uncommitted or untracked files (each named), or a PR isn't ready: not approved (with
      `pr.review_required`), checks failing or still running (with `pr.wait_for_ci`), not mergeable, behind the
      default branch, or HEAD not pushed (without `pr.required`: not a fast-forward of origin/<default>). A PR already
      merged is fine: cleanup alone remains. Reports each PR (number, title, commits, whether its squash collapses
      several commits, whether it merges with --admin) and the two commands to run. Merges and removes nothing itself.
"""

from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "lib"))
sys.path.insert(0, str(HERE))

import checkmarks  # noqa: E402
import config  # noqa: E402
import gitgh  # noqa: E402
import publish  # noqa: E402
import records  # noqa: E402
import worktrees  # noqa: E402
from gitgh import Failed  # noqa: E402

OK, REFUSED, USAGE = 0, 1, 2
MODES = ("build", "unit", "format", "integration")


class Refused(Exception):
    """The stage's own requirement isn't met; the message says which."""


# ---------------------------------------------------------------------------------------------------------------------
# start


def adhoc_record(store: records.Store, summary: str, description: str | None) -> tuple[records.Record, bool]:
    """The record the summary's slug names, else a new keyless `task` record for it."""
    with store.lock():
        found = store.by_stem(records.slugify(summary))
        if found:
            return found, False
        values = {"type": "task", "key": None, "summary": " ".join(summary.split()), "epic": None,
                  "owner": records.CLAUDE, "depends_on": [], "created": datetime.date.today(), "legacy_id": None,
                  "source": None, "source_doc": None}
        body = (f"# {values['summary']}\n\n## Description\n\n{(description or values['summary']).strip()}\n\n"
                "## Acceptance Criteria\n\n<!-- Written by the session: how to tell it's done. -->\n")
        text = store.fill("task", values, body)
        return store.new_record(store.record_path(None, summary), text, False), True


def start(cfg: config.Config, store: records.Store, args) -> dict:
    if bool(args.ref) == bool(args.summary):
        raise Refused("name the change by a record (<ref>) or by --summary, not both or neither")
    # Everything that can fail on the arguments comes before anything is written, so a bad call makes no record.
    record = None
    if args.ref:
        record = store.find(args.ref)
        wid = record.work_id
    else:
        wid = records.slugify(args.summary)
        if not wid:
            raise records.TaskError(f"no summary to name a task record after: {args.summary!r}")
        record = store.by_stem(wid)
    if record and record.status == "done":
        raise Refused(f"{wid} is done ({record.path}); start a new change under a new summary")
    gitgh.check_work_id(wid)
    branch = gitgh.branch_for(cfg, wid, args.desc)
    if cfg.nested:
        if not args.repos:
            raise Failed("--repos is required in a nested workspace")
        checkouts = gitgh.resolve_all(cfg, [r.strip() for r in args.repos.split(",") if r.strip()])
    else:
        checkouts = gitgh.repos(cfg)
    made = record is None
    if made:
        record, _ = adhoc_record(store, args.summary, args.description)

    results = []
    for co in checkouts:
        path = gitgh.worktree_path(cfg, wid, co.name)
        if path.exists():
            results.append({"repo": co.name, "path": str(path), "status": "refused",
                            "reason": f"{wid} already has a worktree for {co.name}: {path}"})
        else:
            results.append(worktrees.create_one(cfg, wid, co, branch))
    ready = [r["path"] for r in results if r["status"] in ("created", "attached", "from-remote")]
    with store.lock():
        if ready:
            store.append(record, "worktrees", ready)
            if record.status in (None, "todo"):
                store.set_fields(record, {"status": "in-progress", "owner": records.CLAUDE, "stage": "implementing"})
    return {"work_id": wid, "doc": str(record.path), "made": made, "branch": branch, "status": record.status,
            "results": results}


# ---------------------------------------------------------------------------------------------------------------------
# check


def held_locks(names: list[str]) -> list[dict]:
    """The unexpired services locks among `names`, from services_lock.py (which reads the same lock folder)."""
    done = subprocess.run([sys.executable, str(HERE / "services_lock.py"), "status"], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=60)
    try:
        locks = json.loads(done.stdout).get("locks") or []
    except json.JSONDecodeError:
        raise Failed(f"services_lock.py status gave no JSON: {(done.stdout + done.stderr).strip()[-300:]}")
    return [lock for lock in locks if lock["name"] in names and not lock.get("expired")]


def holder(lock: dict) -> str:
    owner = lock.get("owner") or {}
    who = f"task {owner['task']}" if owner.get("task") else f"a {lock.get('kind', 'agent')} lock"
    return f"{who}{' at ' + owner['worktree'] if owner.get('worktree') else ''}"


def hint(lock: dict, wid: str) -> str:
    if (lock.get("owner") or {}).get("task") == wid:
        return "this change's own earlier run still holds it (its runner would be blocked too); stop that run first"
    return "wait for it or ask the user"


def check(cfg: config.Config, wid: str, mode: str) -> dict:
    gitgh.check_work_id(wid)
    if not gitgh.task_worktrees(cfg, wid):
        raise Refused(f"no change to check: {wid} has no worktree under {cfg.worktrees_dir}")
    found = worktrees.changes(cfg, wid)  # a git failure here is an error, not a refusal
    if not any(co["files"] for co in found["checkouts"]):
        raise Refused(f"no change to check: {wid}'s worktree has no changes against origin/{cfg.default_branch}")
    if not found["areas"]:
        names = ", ".join(sorted({s["area"] for s in found["skipped"]})) or "none"
        raise Refused(f"no change to check: {wid}'s changes touch nothing an area's checks look at (skipped: {names})")
    if mode == "integration":
        names = sorted({area.split("=", 1)[0] for area in found["areas"]}) if cfg.nested else [cfg.name]
        for lock in held_locks(names):
            raise Refused(f"the services lock {lock['name']} is held by {holder(lock)}; {hint(lock, wid)}")
    return {"ok": True, "work_id": wid, "mode": mode, "areas": found["areas"], "skipped": found["skipped"],
            "checkouts": [{"repo": co["repo"], "path": co["path"], "diff": co["diff"]} for co in found["checkouts"]],
            "bin": str(HERE)}


# ---------------------------------------------------------------------------------------------------------------------
# review and publish


def resolve(cfg: config.Config, wid: str | None, checkouts: str | None, stage: str) -> tuple[list[gitgh.Checkout], dict]:
    """The change's checkouts (a task's worktrees, or `--checkouts`) and what they changed; refuses when it has none."""
    if checkouts:
        names = [c.strip() for c in checkouts.split(",") if c.strip()]
        found = gitgh.resolve_all(cfg, names)
    else:
        if not wid:
            raise Failed("name the change by a work id or --checkouts")
        gitgh.check_work_id(wid)
        found = gitgh.task_worktrees(cfg, wid)
        if not found:
            raise Refused(f"nothing to {stage}: {wid} has no worktree under {cfg.worktrees_dir}")
    return found, worktrees.changes(cfg, wid or "", found)


def review(cfg: config.Config, wid: str | None, checkouts: str | None) -> dict:
    found, changed = resolve(cfg, wid, checkouts, "review")
    if not any(co["files"] for co in changed["checkouts"]):
        where = f"{wid}'s worktree" if wid and not checkouts else "the checkout"
        raise Refused(f"nothing to review: {where} has no changes against origin/{cfg.default_branch}")
    return {"ok": True, "work_id": wid, "checkouts": [
        {"repo": co["repo"], "path": co["path"], "branch": co["branch"], "diff": co["diff"],
         "arg": f"{co['repo']}={co['path']}"} for co in changed["checkouts"] if co["files"]]}


def publish_check(cfg: config.Config, wid: str, checkouts: str | None, draft: bool) -> dict:
    found, changed = resolve(cfg, wid, checkouts, "publish")
    dirty = [co for co in changed["checkouts"] if co["files"]]
    # The branch first: a checkout on the default branch may have no diff to blame. Only a repo with changes is
    # published, so an untouched repo of a nested change never blocks the others; with no changes anywhere, every
    # checkout is looked at, so the refusal still names the branch.
    for co in dirty or changed["checkouts"]:
        if not co["branch"]:
            raise Refused(f"{co['repo']} ({co['path']}) is on a detached HEAD; publish from a branch (start-change)")
        if co["branch"] == cfg.default_branch:
            raise Refused(f"{co['repo']} ({co['path']}) is on {co['branch']}, the default branch; a change publishes "
                          "from its own branch (start-change)")
    if not dirty:
        raise Refused(f"nothing to publish: {wid}'s changes against origin/{cfg.default_branch} are empty")
    ns = argparse.Namespace(checkouts=checkouts, repos=",".join(co["repo"] for co in dirty), dry_run=True,
                            no_record=False, publish_dir=None, message_file=None, message_dir=None,
                            announce_file=None)
    ctx = publish.Context(cfg, wid, ns)
    messages = {}
    for co in ctx.checkouts:
        path = ctx.message_path(co)
        messages[co.name] = str(path)
        if not draft:
            _, problems = publish.check_message(path)
            if problems:
                raise Refused(f"no approved commit message for {co.name}: {'; '.join(problems)}. Draft it with "
                              "git-draft-commit-message, show it to the user and, once approved, save it at that path")
    base = f"origin/{cfg.default_branch}"
    warnings, checks = [], []
    for co in dirty:
        path = Path(co["path"])
        stale = checkmarks.unchecked(path, base, co["areas"])
        checks.append({"repo": co["repo"], "areas": co["areas"], "unchecked": stale,
                       "results": checkmarks.status(path, base)})
        for area in stale:
            warnings.append(f"checks haven't run on the current diff of {co['repo']}'s {area}: no passing build, unit "
                            "or integration run since its last change")
    return {"ok": True, "work_id": wid, "draft": draft, "pr_required": bool(cfg.pr["required"]),
            "announce": bool(cfg.pr["required"] and cfg.announce),
            "checkouts": [{"repo": co["repo"], "path": co["path"], "branch": co["branch"], "diff": co["diff"]}
                          for co in dirty],
            "messages": messages, "announce_file": str(ctx.announce_path()), "checks": checks, "warnings": warnings,
            "bin": str(HERE)}


# ---------------------------------------------------------------------------------------------------------------------
# finish


def finish_check(cfg: config.Config, wid: str, checkouts: str | None, no_record: bool) -> dict:
    if checkouts:
        found = gitgh.resolve_all(cfg, [c.strip() for c in checkouts.split(",") if c.strip()])
    else:
        gitgh.check_work_id(wid)
        found = gitgh.task_worktrees(cfg, wid)
        if not found:
            raise Refused(f"nothing to finish: {wid} has no worktree under {cfg.worktrees_dir}")
    for co in found:
        name = gitgh.branch(co.path)
        if not name:
            raise Refused(f"{co.name} ({co.path}) is on a detached HEAD; there is no branch to finish")
        if name == cfg.default_branch:
            raise Refused(f"{co.name} ({co.path}) is on {name}, the default branch; there is no change branch to finish")
    # Before the PR: a stray file is the user's to settle, and cleanup would refuse it after the merge.
    for co in found:
        dirty = gitgh.status_lines(co.path)
        if dirty:
            raise Refused(f"{co.name} ({co.path}) has uncommitted or untracked files: "
                          f"{', '.join(line[3:] for line in dirty)}. Commit or remove them first; nothing was merged "
                          "or removed")
    ns = argparse.Namespace(checkouts=checkouts, repos=None, dry_run=True, no_record=no_record, publish_dir=None,
                            message_file=None, message_dir=None, announce_file=None)
    ctx = publish.Context(cfg, wid, ns)
    prechecks = [publish.merge_precheck_one(ctx, co) for co in ctx.checkouts]
    blocked = [c for c in prechecks if c["status"] == "blocked"]
    if blocked:
        raise Refused("not ready to finish: " + "; ".join(f"{c['repo']}: {', '.join(c['problems'])}" for c in blocked)
                      + ". Nothing was merged or removed")
    flags = f" --checkouts {checkouts}" if checkouts else ""
    flags += " --no-record" if no_record or checkouts else ""
    return {"ok": True, "work_id": wid, "pr_required": bool(cfg.pr["required"]), "merge_method": cfg.pr["merge"],
            "checkouts": [{k: c[k] for k in ("repo", "path", "branch", "status", "number", "url", "title", "commits",
                                             "squash_collapses", "admin_merge", "merge_commit") if k in c}
                          for c in prechecks],
            "merge_needed": any(c["status"] == "ready" for c in prechecks),
            "confirm_squash": [c["repo"] for c in prechecks if c.get("squash_collapses")],
            "admin_merge": [c["repo"] for c in prechecks if c.get("admin_merge")],
            "merge_command": f"publish.py merge {wid}{flags}", "cleanup_command": f"publish.py cleanup {wid}{flags}",
            "bin": str(HERE)}


# ---------------------------------------------------------------------------------------------------------------------


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = top.add_subparsers(dest="command", required=True)
    sub = commands.add_parser("start")
    sub.add_argument("ref", nargs="?", help="work id or record path")
    sub.add_argument("--summary", help="an ad-hoc change: the record its slug names, or a new keyless one")
    sub.add_argument("--description", help="the new record's Description (default: the summary)")
    sub.add_argument("--repos", help="comma-separated repo names (nested workspaces)")
    sub.add_argument("--desc", help="kebab-case description for the branch name")
    sub = commands.add_parser("check")
    sub.add_argument("id", help="work id")
    sub.add_argument("mode", choices=MODES)
    sub = commands.add_parser("review")
    sub.add_argument("id", nargs="?", help="work id")
    sub.add_argument("--checkouts", help="comma-separated repo names or paths, instead of the work id's worktrees")
    sub = commands.add_parser("publish")
    sub.add_argument("id", help="work id")
    sub.add_argument("--checkouts", help="comma-separated repo names or paths, instead of the work id's worktrees")
    sub.add_argument("--draft", action="store_true", help="the check before the commit message exists")
    sub = commands.add_parser("finish")
    sub.add_argument("id", help="work id")
    sub.add_argument("--checkouts", help="comma-separated repo names or paths, instead of the work id's worktrees")
    sub.add_argument("--no-record", action="store_true", help="the change has no task record")
    return top


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        cfg = config.load()
        if args.command == "start":
            out = start(cfg, records.Store(cfg), args)
            code = REFUSED if any(r["status"] in ("refused", "failed") for r in out["results"]) else OK
        elif args.command == "review":
            out, code = review(cfg, args.id, args.checkouts), OK
        elif args.command == "publish":
            out, code = publish_check(cfg, args.id, args.checkouts, args.draft), OK
        elif args.command == "finish":
            out, code = finish_check(cfg, args.id, args.checkouts, args.no_record), OK
        else:
            out, code = check(cfg, args.id, args.mode), OK
        print(json.dumps(out, indent=2, default=str))
        return code
    except Refused as e:
        print(json.dumps({"ok": False, "refused": str(e)}))
        return REFUSED
    except (Failed, records.TaskError) as e:
        print(json.dumps({"error": str(e)}))
        return REFUSED
    except config.ConfigError as e:
        print(json.dumps({"error": "; ".join(e.errors)}))
        return REFUSED


if __name__ == "__main__":
    sys.exit(main())
