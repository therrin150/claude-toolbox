"""The `none` tracker: no ticket keys. A task's work id is a slug of its summary or plan title, and its requirement is
the draft or `plan.md` it starts from, copied into the record. Nothing writes back."""

from __future__ import annotations

from pathlib import Path

import records
from records import Record, TaskError
from trackers import Tracker, describe

NO_KEYS = "tasks have no keys"
NO_QUEUE = "there's no queue without keys; name the task to work"


class NoneTracker(Tracker):
    name = "none"
    requirement = "source_doc"
    unsupported = {
        "next-key": NO_KEYS,
        "file": "there's nothing to file drafts into; start a task from its draft with context",
        "import": "import reads a Jira issue",
        "create-defect": "a defect needs a tracker to be filed in",
        "queue": NO_QUEUE,
        "epic-status": NO_QUEUE,
        "check": NO_QUEUE,
    }

    def find_or_make(self, ref: str, dry_run: bool = False) -> tuple[Record, Path | None]:
        kind, value = self.store.classify(ref)
        if kind == "key":
            raise TaskError(f"{ref}: tracker is none; {NO_KEYS}. Pass a work id, a draft or a plan.md path")
        if kind == "work_id":
            raise TaskError(f"{ref}: no task record {ref}.md in {self.store.tasks_dir}; start from a draft or a "
                            f"{records.PLAN} path")
        found = self.existing(kind, value) or (self.same_summary(value) if kind == "draft" else None)
        if found:
            return found, None
        if kind == "plan":
            values = self.store.plan_values(value)
            text = self.store.fill("task", values)
        else:
            values, body = self.store.draft_values(value)
            values["key"] = None
            text = self.store.fill(values["type"], values, body)
        path = self.store.record_path(None, values["summary"])
        if path.exists():
            raise TaskError(f"{path} already has the work id {path.stem}, for another source: "
                            f"{self.store.record(path).fields.get('source_doc')}")
        return self.store.new_record(path, text, dry_run), value

    def same_summary(self, draft: Path) -> Record | None:
        """A record copied from a draft that has since been renumbered: same discussion, same summary."""
        summary = self.store.draft_values(draft)[0]["summary"]
        discussion = draft.parent.parent.resolve()
        hits = [r for r in self.store.records()
                if r.summary == summary and self.store.discussion_of(r)
                and self.store.discussion_of(r).resolve() == discussion]
        return hits[0] if len(hits) == 1 else None

    def work_id(self, ref: str) -> dict:
        kind, value = self.store.classify(ref)
        if kind == "key":
            raise TaskError(f"{ref}: tracker is none; {NO_KEYS}")
        if kind == "work_id":
            raise TaskError(f"{ref}: no task record {ref}.md in {self.store.tasks_dir}")
        found = self.existing(kind, value) or (self.same_summary(value) if kind == "draft" else None)
        return describe(found) if found else self.source_work_id(kind, value)
