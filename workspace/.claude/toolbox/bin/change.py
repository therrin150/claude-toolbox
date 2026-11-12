#!/usr/bin/env python3
"""The gates of the `start-change` and `check-change` stage skills, so any session gets the same refusals.

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

import config  # noqa: E402
import gitgh  # noqa: E402
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
    return top


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        cfg = config.load()
        if args.command == "start":
            out = start(cfg, records.Store(cfg), args)
            code = REFUSED if any(r["status"] in ("refused", "failed") for r in out["results"]) else OK
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
