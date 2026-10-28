#!/usr/bin/env python3
"""Plan what work-tasks starts: which tasks get a new session, which a resumed one, and each session's title and
prompt. Read-only: it starts nothing and writes no record. Finds the workspace from the current directory.

  dispatch.py plan [<source> ...] [--sessions <file>] [--summary KEY=text ...] [--offered <work id> ...]
                   [--limit <n>] [--no-limit]

<source> is a task the user named: a key (a bare number takes the workspace's key prefix), a work id, a task record, a
draft in a discussion's tickets/ folder, or a discussion's plan.md. Commas also separate sources.

  With sources, any tracker: each one gets a session (named tasks ignore the WIP limits: the user chose them).
  With none, the `vault` tracker's queue: `tasks.py queue`'s waiting-on-the-user list, then a resume for every task
  handed back to Claude, then new starts within `work.wip`. Under `jira` and `none` the user names the tasks.

--sessions   JSON from the session list: an array (or {"sessions": [...]}) of {"sessionId"|"id", "title",
             "archived"}. A task is running when an unarchived session's title is its work id, or starts with
             `<work id> ` (work-task renames its session that way). Without the flag nothing counts as running.
--summary    A summary for a key that has no task record yet (the session fetched it from Jira).
--offered    Work ids whose chip was already offered this conversation: reported as `offered`, not offered again.
--limit      Most sessions to launch now; the default is 3 with `work.dispatch: chips` (the app shows three cards per
             turn) and none with `direct`. The rest are `next`.

Prints JSON: `dispatch` (direct|chips), `initiation` (what start_session takes), `model`, `cwd` (the workspace
root, where every session starts), `mode` (queue|named), `launch` (the entries to start now, in order), `entries`
(everything, with a `group`: start, resume, running, offered, next, blocked, held, done or skipped, and a
`reason`), and in queue mode `waiting_on_user`, `limits`, `load`, `held` and `stopped`. Every entry has `work_id`,
`key`, `summary`, `title` (`<work id> <summary>`, at most 60 characters) and `prompt` (`/work-task <ref>`, nothing else:
the record holds the rest).

Exit codes: 0 ok, 1 failed (the reason prints as JSON), 2 bad arguments.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import board  # noqa: E402
import config  # noqa: E402
import frontmatter  # noqa: E402
import records  # noqa: E402
import trackers  # noqa: E402

TITLE_MAX = 60
CHIP_LIMIT = 3  # cards the app shows per turn


class Failed(Exception):
    pass


def title_for(work_id: str, summary: str | None) -> str:
    return (f"{work_id} {summary}" if summary else work_id)[:TITLE_MAX].rstrip()


def live_sessions(path: str | None) -> list[dict]:
    if not path:
        return []
    try:
        data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise Failed(f"{path}: not a readable session list: {error}")
    rows = data.get("sessions") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise Failed(f"{path}: expected an array of sessions, or an object with a `sessions` array")
    return [{"id": row.get("sessionId") or row.get("id"), "title": row.get("title") or ""}
            for row in rows if isinstance(row, dict) and not row.get("archived")]


def session_for(work_id: str | None, sessions: list[dict]) -> dict | None:
    if not work_id:
        return None
    for session in sessions:
        if session["title"] == work_id or session["title"].startswith(f"{work_id} "):
            return session
    return None


def entry(work_id: str | None, key: str | None, summary: str | None, ref: str | None, group: str, reason: str = "",
          **extra) -> dict:
    return {"work_id": work_id, "key": key, "summary": summary,
            "title": title_for(work_id, summary) if work_id else None,
            "prompt": f"/work-task {ref}" if ref else None, "group": group, "reason": reason, **extra}


def of_record(record: records.Record, group: str, reason: str = "", **extra) -> dict:
    return entry(record.work_id, record.key, record.summary, record.work_id, group, reason,
                 status=record.status, owner=record.owner, stage=record.fields.get("stage"), **extra)


def sources_from(raw: list[str], cfg: config.Config) -> list[str]:
    out = []
    for arg in raw:
        for part in re.split(r"[,\s]+", arg.strip()):
            if not part:
                continue
            if part.isdigit():
                if not cfg.key_prefix:
                    raise Failed(f"{part}: a bare number needs a key prefix, and tracker {cfg.tracker} has none; "
                                 "name the task by its work id or path")
                part = f"{cfg.key_prefix}-{part}"
            out.append(part)
    return out


# ---------------------------------------------------------------------------------------------------------------------
# Named sources


def named(store: records.Store, tracker, sources: list[str], summaries: dict, sessions: list[dict]) -> list[dict]:
    entries, seen = [], set()
    for source in sources:
        try:
            info = tracker.work_id(source)
            kind, value = store.classify(source)
        except (records.TaskError, frontmatter.FrontmatterError) as error:
            entries.append(entry(None, None, None, None, "skipped", str(error), source=source))
            continue
        work_id, key = info["work_id"], info["key"]
        summary = info["summary"] or summaries.get(key or work_id)
        if not work_id:
            entries.append(entry(None, key, summary, None, "skipped",
                                 info.get("note") or "no work id yet", source=source))
            continue
        if work_id in seen:
            continue
        seen.add(work_id)
        record = store.find(work_id) if info.get("doc") else None
        if record is not None and record.type == "epic":
            entries.append(of_record(record, "skipped", "an epic is never worked itself; name its stories",
                                     source=source))
            continue
        if record is not None and record.status == "done":
            entries.append(of_record(record, "done", "already done", source=source))
            continue
        # A path with no record yet is passed as is: context makes the record. Anything else has one, or is a key.
        ref = work_id if record is not None or kind in ("key", "work_id") else str(Path(str(value)).resolve())
        stage = record.fields.get("stage") if record else None
        state = ", ".join(part for part in (record.status if record else None, stage) if part)
        found = entry(work_id, key, summary, ref, "start" if record is None or record.status == "todo" else "resume",
                      state, source=source, status=record.status if record else None,
                      owner=record.owner if record else None, stage=stage)
        if info.get("note") and record is None:
            found["note"] = info["note"]
        entries.append(found)
    return entries


# ---------------------------------------------------------------------------------------------------------------------
# The vault tracker's queue


def from_queue(store: records.Store, sessions: list[dict]) -> tuple[list[dict], dict]:
    queue = board.queue(store)
    by_key = {r.key: r for r in store.records() if r.key}
    entries = []

    def add(item: dict, group: str, reason: str = "", **extra) -> None:
        record = by_key[item["key"]]
        entries.append(of_record(record, group, reason, **extra))

    for item in queue["waiting_on_user"]:
        record = by_key[item["key"]]
        session = session_for(record.work_id, sessions)
        entries.append(of_record(record, "waiting", record.fields.get("work_note") or "",
                                 work_note=record.fields.get("work_note"), session=session))
    for item in queue["resumable"]:
        add(item, "resume")
    for item in queue["start"]:
        add(item, "start")
    started = {item["key"] for item in queue["start"]}
    for item in queue["ready"]:
        if item["key"] not in started:
            add(item, "held", queue["held"] or "")
    for item in queue["blocked"]:
        add(item, "blocked", "waiting for " + ", ".join(f"{b['key']} ({b['status'] or 'no record'})"
                                                         for b in item["blockers"]))
    for item in queue["stopped"]:
        add(item, "stopped", by_key[item["key"]].fields.get("work_note") or "parked")
    return entries, {name: queue[name] for name in ("limits", "load", "capacity", "held")}


# ---------------------------------------------------------------------------------------------------------------------


def plan(args) -> dict:
    try:
        cfg = config.load()
    except config.ConfigError as error:
        raise Failed("; ".join(error.errors) if error.errors else str(error))
    store = records.Store(cfg)
    tracker = trackers.get(store)
    sessions = live_sessions(args.sessions)
    sources = sources_from(args.sources, cfg)
    summaries = {}
    for pair in args.summary or []:
        name, sep, text = pair.partition("=")
        if not sep:
            raise Failed(f"--summary expects KEY=text, got {pair!r}")
        summaries[name.strip()] = text.strip()

    extra: dict = {}
    if sources:
        mode, entries = "named", named(store, tracker, sources, summaries, sessions)
    elif cfg.tracker == "vault":
        mode = "queue"
        entries, queue_info = from_queue(store, sessions)
        extra = {"waiting_on_user": [e for e in entries if e["group"] == "waiting"], **queue_info}
    else:
        raise Failed(f"tracker is {cfg.tracker}: name the tasks to work (keys, task records, story docs or plans)")

    offered = set(args.offered or [])
    dispatch = cfg.get("work.dispatch")
    limit = None if args.no_limit else (args.limit if args.limit is not None
                                        else (CHIP_LIMIT if dispatch == "chips" else None))
    launch = []
    for item in entries:
        if item["group"] not in ("start", "resume"):
            continue
        session = session_for(item["work_id"], sessions)
        if session:
            item.update(group="running", session=session, reason="has a live session")
        elif item["work_id"] in offered:
            item.update(group="offered", reason="its card was offered already")
        elif limit is not None and len(launch) >= limit:
            item.update(group="next", reason=f"past this turn's {limit}")
        else:
            launch.append(item)
    return {"dispatch": dispatch, "initiation": "own_initiative" if dispatch == "chips" else "user_asked",
            "model": cfg.get("work.session_model"), "cwd": str(cfg.root), "mode": mode,
            "tracker": cfg.tracker, "launch": launch, "entries": entries, **extra}


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = top.add_subparsers(dest="command", required=True)
    sub = commands.add_parser("plan")
    sub.add_argument("sources", nargs="*")
    sub.add_argument("--sessions", help="the session list, as JSON")
    sub.add_argument("--summary", action="append", metavar="KEY=text")
    sub.add_argument("--offered", nargs="+", metavar="WORK_ID")
    sub.add_argument("--limit", type=int)
    sub.add_argument("--no-limit", action="store_true")
    return top


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        print(json.dumps(plan(args), indent=2, default=str))
    except (Failed, records.TaskError, frontmatter.FrontmatterError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, indent=2))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
