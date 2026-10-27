"""Trackers: where a workspace's ticket keys come from, where a task's requirement comes from, and what writes back.

| Tracker | Keys | Requirement | Writes back |
|---|---|---|---|
| `vault` | a local counter (`<PREFIX>-<n>`), under the tasks lock | the record: it is the ticket | n/a |
| `jira` | Jira, filed by the jira-filer agent | Jira, imported into the record | per config |
| `none` | none: a slug of the summary or plan title | the draft or `plan.md`, copied into the record | nothing |

Every tracker has the same interface (`Tracker`); `get(store)` returns the workspace's. A command a tracker doesn't
support raises `Unsupported` with the reason, which `tasks.py` prints.
"""

from __future__ import annotations

from pathlib import Path

import records
from records import Record, TaskError

COMMANDS = ("context", "work-id", "get", "set", "append", "set-list", "log", "import", "file", "next-key",
            "create-defect", "queue", "epic-status", "check")


class Unsupported(TaskError):
    """The workspace's tracker doesn't do this command."""


class Tracker:
    name = ""
    requirement = ""            # where a task's requirement comes from: record, source_doc or jira
    unsupported: dict = {}      # command -> why this tracker doesn't do it

    def __init__(self, store: records.Store):
        self.store = store
        self.cfg = store.cfg

    def require(self, command: str) -> None:
        if command in self.unsupported:
            raise Unsupported(f"{command}: tracker is {self.name}; {self.unsupported[command]}")

    # Keys

    def next_keys(self, count: int, dry_run: bool = False) -> dict:
        """Hand out `count` new keys: {"keys": [...], "next": <n>}. The caller holds the tasks lock."""
        raise Unsupported(f"next-key: tracker is {self.name}; {self.unsupported.get('next-key', '')}")

    # Requirement source

    def find_or_make(self, ref: str, dry_run: bool = False) -> tuple[Record, Path | None]:
        """The record a `context` argument names, made if need be: (record, the source it was just made from)."""
        raise NotImplementedError

    def work_id(self, ref: str) -> dict:
        """The work id, key and summary a `context` argument would get, writing nothing."""
        raise NotImplementedError

    def file(self, discussion: str, dry_run: bool = False, keys: list[str] | None = None,
             issues: Path | None = None) -> dict:
        """File a discussion's approved drafts. `keys` (`NN=KEY`) and `issues` (a saved search response) are the
        jira tracker's: the keys Jira gave, and the issues to check them against."""
        raise Unsupported(f"file: tracker is {self.name}; {self.unsupported.get('file', '')}")

    def import_issue(self, key: str, issue: Path, dry_run: bool = False) -> dict:
        raise Unsupported(f"import: tracker is {self.name}; {self.unsupported.get('import', '')}")

    def create_defect(self, values: dict, dry_run: bool = False) -> dict:
        raise Unsupported(f"create-defect: tracker is {self.name}; {self.unsupported.get('create-defect', '')}")

    # Write-back

    def write_back(self, record: Record, changes: dict) -> dict | None:
        """What the tracker needs done after `set` changed a record, for the skill to do; None for nothing."""
        return None

    # Shared

    def existing(self, kind: str, value) -> Record | None:
        """The record a resumed source already made, or None. A key, work id or record path names it directly."""
        if kind == "record":
            return value
        if kind in ("plan", "draft"):
            return self.store.by_source(value)
        return None

    def source_work_id(self, kind: str, value, key: str | None = None) -> dict:
        """work-id's answer for a source with no record yet."""
        if kind == "plan":
            summary = self.store.plan_values(value)["summary"]
        else:
            summary = self.store.draft_values(value)[0]["summary"]
        return {"work_id": key or records.slugify(summary), "key": key, "summary": summary, "doc": None}


def describe(record: Record) -> dict:
    return {"work_id": record.work_id, "key": record.key, "summary": record.summary, "doc": str(record.path)}


def get(store: records.Store) -> Tracker:
    """The workspace's tracker, from config `tracker.kind`."""
    kind = store.cfg.tracker
    if kind == "vault":
        from trackers.vault import VaultTracker
        return VaultTracker(store)
    if kind == "none":
        from trackers.none import NoneTracker
        return NoneTracker(store)
    if kind == "jira":
        from trackers.jira import JiraTracker
        return JiraTracker(store)
    raise TaskError(f"tracker.kind: unknown tracker {kind!r}")
