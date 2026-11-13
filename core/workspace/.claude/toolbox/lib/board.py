"""The vault tracker's views over every task record: the dispatcher's queue, epic status, and the consistency check.

- queue(): what waits on the user, what Claude can resume, what it may start under `work.wip`, and what's blocked.
  Claude's count is its own tasks in progress; the user's is every task they own that isn't done. When the user's
  count reaches their limit nothing new starts, though Claude still resumes what was handed back. Start order is
  `<tasks>/Backlog.md` (one key per line, first key on each line counts), then the rest in key order, then keyless
  records by date filed and draft number; a task whose `depends_on` aren't all done waits, wherever it is listed.
- epic_status(): each epic's status, derived from its stories (`epic: <key>`).
- check(): unknown ids and cycles in `depends_on`, done tasks depending on undone ones, and records that break the
  contract.

A task is named by its work id: its key, or a keyless record's slug (`depends_on` takes either). Under the `jira`
tracker Jira holds the keyed tasks, so queue and check see only the keyless records (filed with `file --keyless`); a
Jira key in their `depends_on` is Jira's to check, and a queue blocker until a record for it says it's done.
"""

from __future__ import annotations

from pathlib import Path

import frontmatter
import records
from records import CLAUDE, KEY_RE, Record, draft_number, key_number

STARTED = ("in-progress", "review", "done", "stopped")


def item(record: Record) -> dict:
    return {"key": record.key, "work_id": record.work_id, "summary": record.summary, "type": record.type, "status": record.status,
            "owner": record.owner, "stage": record.fields.get("stage"), "depends_on": record.depends_on,
            "doc": str(record.path)}


def backlog_order(store: records.Store) -> list[str]:
    path = store.tasks_dir / records.BACKLOG
    if not path.is_file():
        return []
    order = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = KEY_RE.search(line)
        if match and match.group(0) not in order:
            order.append(match.group(0))
    return order


def tracked(store: records.Store, tracker=None) -> list[Record]:
    """The records queue and check look at: all of them, or under the jira tracker only the keyless ones."""
    found = store.records()
    return [r for r in found if not r.key] if tracker is not None and tracker.name == "jira" else found


def by_id(found: list[Record]) -> dict[str, Record]:
    """A ticket's own record by work id: its key, or a keyless record's slug. Slices are never named by a dependency."""
    return {r.work_id: r for r in found if not r.slice}


def sort_id(work_id: str) -> tuple[int, str]:
    return key_number(work_id), work_id


def blockers(record: Record, by_key: dict[str, Record]) -> list[dict]:
    return [{"key": key, "status": by_key[key].status if key in by_key else None}
            for key in record.depends_on if key not in by_key or by_key[key].status != "done"]


def queue(store: records.Store, tracker=None) -> dict:
    cfg = store.cfg
    user = cfg.owner
    found = tracked(store, tracker)
    tasks = [r for r in found if r.type != "epic"]
    by_key = by_id(found)
    wip = cfg.get("work.wip")
    rank = {key: index for index, key in enumerate(backlog_order(store))}

    def filed(r: Record):
        return str(r.fields.get("created") or ""), draft_number(Path(r.fields.get("source_doc") or "")) or ""

    def oldest(r: Record):
        return str(r.fields.get("created") or ""), key_number(r.key), r.work_id

    def priority(r: Record):
        return (0, rank[r.key]) if r.key in rank else (1, key_number(r.key), *filed(r), r.work_id)

    waiting = sorted((r for r in tasks if r.owner == user and r.status != "done"), key=oldest)
    resumable = sorted((r for r in tasks if r.owner == CLAUDE and r.status == "in-progress"), key=priority)
    todo = sorted((r for r in tasks if r.status == "todo" and not r.slice), key=priority)  # a slice never queues
    ready = [r for r in todo if r.owner == CLAUDE and not blockers(r, by_key)]
    blocked = [r for r in todo if blockers(r, by_key)]
    stopped = sorted((r for r in tasks if r.owner == CLAUDE and r.status == "stopped"), key=priority)

    load = {CLAUDE: len(resumable), user: len(waiting)}
    limits = {CLAUDE: wip.get(CLAUDE), user: wip.get(user)}
    if load[user] >= limits[user]:
        capacity = 0
        held = f"{user} owns {load[user]} open tasks, at or over the limit of {limits[user]}: nothing new starts"
    else:
        capacity = max(0, limits[CLAUDE] - load[CLAUDE])
        held = None if capacity else (f"claude has {load[CLAUDE]} tasks in progress, at or over the limit of "
                                      f"{limits[CLAUDE]}")
    return {
        "user": user,
        "limits": limits,
        "load": load,
        "capacity": capacity,
        "held": held,
        "waiting_on_user": [item(r) for r in waiting],
        "resumable": [item(r) for r in resumable],
        "start": [item(r) for r in ready[:capacity]],
        "ready": [item(r) for r in ready],
        "blocked": [{**item(r), "blockers": blockers(r, by_key)} for r in blocked],
        "stopped": [item(r) for r in stopped],
    }


def derived_status(stories: list[Record]) -> str:
    if stories and all(r.status == "done" for r in stories):
        return "done"
    if any(r.status in STARTED for r in stories):
        return "in-progress"
    return "todo"


def epic_status(store: records.Store, key: str | None = None) -> dict:
    all_records = store.records()
    epics = [r for r in all_records if r.type == "epic" and (key is None or r.key == key)]
    if key and not epics:
        raise records.TaskError(f"{key}: no epic record with that key in {store.tasks_dir}")
    out = []
    for epic in sorted(epics, key=lambda r: key_number(r.key)):
        stories = [r for r in all_records if r.type != "epic" and not r.slice and epic.key
                   and r.fields.get("epic") == epic.key]
        counts = {status: sum(r.status == status for r in stories) for status in records.STATUSES}
        out.append({"key": epic.key, "summary": epic.summary, "doc": str(epic.path),
                    "status": derived_status(stories), "recorded_status": epic.status, "total": len(stories),
                    "done": counts["done"], "counts": {s: n for s, n in counts.items() if n}})
    return {"epics": out}


def check(store: records.Store, tracker=None) -> dict:
    scan = store.scan()
    jira = tracker is not None and tracker.name == "jira"
    found = [r for r in scan.records if not r.key] if jira else scan.records
    problems: list[dict] = []

    def problem(kind: str, path, detail: str) -> None:
        problems.append({"kind": kind, "doc": str(path), "detail": detail})

    def external(ref: str) -> bool:
        """A Jira key under the jira tracker: Jira's to check, not the vault's."""
        return jira and KEY_RE.fullmatch(ref) is not None

    for path, error in scan.unreadable:
        problem("unreadable", path, error)

    seen: dict[str, Record] = {}
    for record in found:
        if record.work_id in seen:
            problem("duplicate-key", record.path, f"{record.work_id} is also {seen[record.work_id].path}")
        seen.setdefault(record.work_id, record)
        if record.key and not record.path.name.startswith(f"{record.work_id}-"):
            problem("key-mismatch", record.path, f"work id is {record.work_id}, but the file name doesn't start "
                                                 "with it")
        for name in ("status", "owner", "stage"):
            value = record.fields.get(name)
            if record.type == "epic" and name != "status":
                continue
            reason = store.check_value(name, value) if value or name != "stage" else None
            if reason:
                problem("bad-value", record.path, reason)
        if record.type == "epic" and record.depends_on:
            problem("epic-depends", record.path, "epics carry no depends_on; put it on their stories")

    known = by_id(found)
    for record in found:
        epic = record.fields.get("epic")
        if record.type != "epic" and epic and not external(epic) and (epic not in known or known[epic].type != "epic"):
            problem("unknown-epic", record.path, f"epic {epic} is not an epic record")
        for dep in record.depends_on:
            if dep not in known:
                if not external(dep):
                    problem("unknown-key", record.path, f"depends on {dep}, which has no task record")
            elif record.status == "done" and known[dep].status != "done":
                problem("done-depends-on-undone", record.path,
                        f"{record.work_id} is done but depends on {dep}, which is {known[dep].status}")

    for cycle in cycles(known):
        problem("cycle", known[cycle[0]].path, "depends_on cycle: " + " -> ".join(cycle + [cycle[0]]))

    for draft in store.all_drafts():
        try:
            fields = frontmatter.fields(draft)
        except frontmatter.FrontmatterError as error:
            problem("unreadable", draft, str(error))
            continue
        if fields.get("key"):
            if not jira:  # under jira a keyed draft is create-tickets' to finish
                problem("half-filed", draft, f"was given {fields['key']} but never moved into the tasks folder; "
                                             "run file again")
        elif fields.get("status") in records.STATUSES:
            problem("half-filed", draft, "was written as a record but never moved into the tasks folder; run "
                                         "file --keyless again")

    return {"ok": not problems, "checked": len(found), "problems": problems}


def cycles(by_key: dict[str, Record]) -> list[list[str]]:
    """Each distinct cycle in the depends_on graph, as work ids, starting from its lowest (keys by number, then slugs)."""
    graph = {key: [d for d in r.depends_on if d in by_key] for key, r in by_key.items()}
    found, seen = [], set()
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(key: str) -> None:
        state[key] = 1
        stack.append(key)
        for dep in graph[key]:
            if state.get(dep) == 1:
                cycle = stack[stack.index(dep):]
                low = min(range(len(cycle)), key=lambda i: sort_id(cycle[i]))
                cycle = cycle[low:] + cycle[:low]
                if tuple(cycle) not in seen:
                    seen.add(tuple(cycle))
                    found.append(cycle)
            elif dep not in state:
                visit(dep)
        stack.pop()
        state[key] = 2

    for key in sorted(graph, key=sort_id):
        if key not in state:
            visit(key)
    return found
