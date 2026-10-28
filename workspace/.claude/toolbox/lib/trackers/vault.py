"""The `vault` tracker: the task record is the ticket. Keys come from a local counter, `<tasks>/.counter.yml`, changed
only under the tasks lock, so parallel sessions never share a key. Drafts become tasks when `file` moves them out of
their discussion's `tickets/` folder into the tasks folder; a task can also start from a discussion's `plan.md`."""

from __future__ import annotations

import datetime
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

import frontmatter
import records
from records import KEY_RE, PLAN, TICKETS, Record, TaskError, draft_number, key_number
from trackers import Tracker, describe


@dataclass
class Entry:
    """One draft `file` is filing."""

    draft: Path
    number: str
    values: dict
    body: str
    resumed: bool                  # the draft already holds its key: an earlier run stopped before moving it
    reserved: str | None = None
    target: Path | None = None


class VaultTracker(Tracker):
    name = "vault"
    requirement = "record"
    unsupported = {"import": "import reads a Jira issue"}

    @property
    def prefix(self) -> str:
        return self.cfg.key_prefix

    def is_key(self, value) -> bool:
        return isinstance(value, str) and KEY_RE.fullmatch(value) is not None and value.startswith(f"{self.prefix}-")

    # -----------------------------------------------------------------------------------------------------------------
    # Keys

    @property
    def counter_path(self) -> Path:
        return self.store.tasks_dir / records.COUNTER

    def used_numbers(self) -> set[int]:
        """Numbers no new key may take: every record's key, and every draft's key or reserved key."""
        return self.held_numbers() | self.reserved_numbers()

    def held_numbers(self) -> set[int]:
        """Numbers given out: every record's key, and the key of a draft a stopped `file` run left unmoved."""
        held = {key_number(r.key) for r in self.store.records() if self.is_key(r.key)}
        return held | self.draft_numbers("key")

    def reserved_numbers(self) -> set[int]:
        return self.draft_numbers("reserved_key")

    def draft_numbers(self, name: str) -> set[int]:
        found = set()
        for draft in self.store.all_drafts():
            try:
                value = frontmatter.fields(draft).get(name)
            except frontmatter.FrontmatterError:
                continue
            if self.is_key(value):
                found.add(key_number(value))
        return found

    def counter(self) -> int | None:
        if not self.counter_path.is_file():
            return None
        data = yaml.safe_load(self.counter_path.read_text(encoding="utf-8")) or {}
        value = data.get("next") if isinstance(data, dict) else None
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise TaskError(f"{self.counter_path}: `next` must be a positive whole number; got {value!r}")
        return value

    def next_keys(self, count: int, dry_run: bool = False) -> dict:
        """Hand out `count` keys from the counter, skipping any number a record or draft already holds. With no
        counter yet, counting starts after the highest key given out (a reserved key doesn't move it). The caller
        holds the tasks lock."""
        held = self.held_numbers()
        used = held | self.reserved_numbers()
        start = self.counter()
        number = start if start is not None else max(held, default=0) + 1
        keys = []
        while len(keys) < count:
            if number not in used:
                keys.append(f"{self.prefix}-{number}")
            number += 1
        if not dry_run and count:
            frontmatter.write_atomic(self.counter_path, f"next: {number}\n")
        return {"keys": keys, "next": number, "counter": str(self.counter_path)}

    # -----------------------------------------------------------------------------------------------------------------
    # Requirement source

    def find_or_make(self, ref: str, dry_run: bool = False) -> tuple[Record, Path | None]:
        kind, value = self.store.classify(ref)
        if kind == "key":
            record = self.store.by_key(value)
            if record:
                return record, None
            raise TaskError(self.missing_key(value))
        if kind == "work_id":
            raise TaskError(f"{ref}: no task record named {ref}; with the vault tracker a task's work id is its key")
        found = self.existing(kind, value)
        if found:
            return found, None
        if kind == "draft":
            raise TaskError(f"{value}: a draft becomes a task when it's filed; run `tasks.py file "
                            f"{value.parent.parent.name}` first")
        values = self.store.plan_values(value)
        values["key"] = self.next_keys(1, dry_run)["keys"][0]
        path = self.store.record_path(values["key"], values["summary"])
        return self.store.new_record(path, self.store.fill("task", values), dry_run), value

    def missing_key(self, key: str) -> str:
        for draft in self.store.all_drafts():
            try:
                if frontmatter.fields(draft).get("key") == key:
                    return (f"{key}: its draft {draft} was given the key but never moved; run `tasks.py file "
                            f"{draft.parent.parent.name}` again")
            except frontmatter.FrontmatterError:
                continue
        return f"{key}: no task record with that key in {self.store.tasks_dir}"

    def work_id(self, ref: str) -> dict:
        kind, value = self.store.classify(ref)
        if kind == "key":
            record = self.store.by_key(value)
            if not record:
                raise TaskError(self.missing_key(value))
            return describe(record)
        if kind == "work_id":
            raise TaskError(f"{ref}: no task record named {ref}; with the vault tracker a task's work id is its key")
        found = self.existing(kind, value)
        if found:
            return describe(found)
        out = self.source_work_id(kind, value)
        out["work_id"] = None
        out["note"] = ("the key is assigned when context makes the record" if kind == "plan"
                       else "the key is assigned when the discussion is filed")
        return out

    # -----------------------------------------------------------------------------------------------------------------
    # Filing

    def file(self, discussion: str, dry_run: bool = False, keys: list[str] | None = None,
             issues: Path | None = None) -> dict:
        """Give every approved draft in a discussion's `tickets/` folder a key and move it into the tasks folder.

        Order, so a run that dies part way is safe to repeat: the counter moves first; each draft gets its key and
        the record contract written in place before it moves; links are repaired after every move. A draft that
        already holds a key was given it by such a run, and keeps it.
        """
        if keys or issues:
            raise TaskError(f"file: --keys and --issues are for the jira tracker; the {self.name} tracker assigns "
                            "its own keys")
        folder = self.store.resolve_discussion(discussion)
        entries, skipped = self.gather(folder)
        epics = self.epic_entries(folder, entries)
        held = {r.key: r for r in self.store.records() if r.key}
        for entry in entries:
            if entry.reserved is not None:
                if entry.values["type"] != "epic" or not self.is_key(entry.reserved):
                    raise TaskError(f"{entry.draft}: reserved_key must be a {self.prefix}- key, on an epic draft")
                if entry.reserved in held:
                    raise TaskError(f"{entry.draft}: reserved key {entry.reserved} is taken by "
                                    f"{held[entry.reserved].path}")
                entry.values["key"] = entry.reserved

        fresh = [e for e in entries if not e.values["key"]]
        counter = self.next_keys(len(fresh), dry_run)
        for entry, key in zip(fresh, counter["keys"]):
            entry.values["key"] = key
        out = self.place(folder, entries, skipped, epics, dry_run)
        out["next_key"] = f"{self.prefix}-{counter['next']}"
        return out

    def gather(self, folder: Path) -> tuple[list[Entry], list[dict]]:
        """The drafts a `file` run takes (approved ones, and any an earlier run keyed), and the ones it skips."""
        drafts = self.store.drafts(folder)
        if not drafts:
            raise TaskError(f"{folder}: no drafts in its {TICKETS}/ folder")
        entries, skipped = [], []
        for draft in drafts:
            number = draft_number(draft)
            fields = frontmatter.fields(draft)
            if not number:
                skipped.append({"draft": str(draft), "reason": "no NN- number at the start of its name"})
                continue
            values, body = self.store.draft_values(draft)
            if values["key"]:
                entries.append(Entry(draft, number, values, body, resumed=True))
            elif fields.get("status") == "approved":
                entries.append(Entry(draft, number, values, body, resumed=False,
                                     reserved=fields.get("reserved_key") or None))
            else:
                skipped.append({"draft": str(draft), "reason": f"status is {fields.get('status') or 'missing'}; "
                                "only approved drafts are filed"})
        return entries, skipped

    def epic_entries(self, folder: Path, entries: list[Entry]) -> list[Entry]:
        epics = [e for e in entries if e.values["type"] == "epic"]
        if len(epics) > 1:
            raise TaskError(f"{folder}: {len(epics)} epic drafts; a discussion files at most one")
        for entry in entries:
            if entry.values["type"] == "epic" and entry.values["depends_on"]:
                raise TaskError(f"{entry.draft}: epics carry no depends_on; put it on their stories")
        return epics

    def place(self, folder: Path, entries: list[Entry], skipped: list[dict], epics: list[Entry],
              dry_run: bool) -> dict:
        """Move keyed entries into the tasks folder: draft numbers in depends_on become keys, stories get the
        discussion's epic, each draft gets the record contract before it moves, then links and the plan follow."""
        store = self.store
        plan = folder / PLAN
        plan_epic = frontmatter.fields(plan).get("epic") if plan.is_file() else None
        numbered = self.filed_numbers(folder)
        numbered.update({e.number: e.values["key"] for e in entries})
        epic_key = epics[0].values["key"] if epics else plan_epic

        for entry in entries:
            entry.values["depends_on"] = [self.dependency(entry.draft, d, numbered) for d in entry.values["depends_on"]]
            if entry.values["type"] != "epic":
                entry.values["epic"] = entry.values["epic"] or epic_key
            entry.target = store.record_path(entry.values["key"], entry.values["summary"])
            if entry.target.exists():
                raise TaskError(f"{entry.draft}: {entry.target} already exists")

        renames = {}
        for entry in entries:
            text = store.fill(entry.values["type"], entry.values, entry.body)
            if not dry_run:
                frontmatter.write_atomic(entry.draft, text)  # the key is recorded before the move
                entry.target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(entry.draft, entry.target)
            renames[entry.draft.stem] = (entry.target.stem, store.rel(entry.target.parent) + "/" + entry.target.stem)

        repaired = self.repair(folder, renames, dry_run)
        plan_out = self.update_plan(plan, folder, entries, skipped, epic_key if epics else None, dry_run)
        out = {
            "discussion": str(folder),
            "filed": [{"draft": str(e.draft), "record": str(e.target), "key": e.values["key"],
                       "type": e.values["type"], "summary": e.values["summary"],
                       "epic": e.values.get("epic") if e.values["type"] != "epic" else None,
                       "depends_on": e.values["depends_on"], "resumed": e.resumed} for e in entries],
            "skipped": skipped,
            "links_repaired": repaired,
            "plan": plan_out,
        }
        if dry_run:
            out["dry_run"] = True
        return out

    def filed_numbers(self, folder: Path) -> dict[str, str]:
        """Draft number -> key, for the drafts of this discussion an earlier run filed."""
        tickets = self.store.rel(folder / TICKETS)
        numbered = {}
        for record in self.store.records():
            source = record.fields.get("source_doc") or ""
            if record.key and source.rsplit("/", 1)[0] == tickets:
                number = draft_number(Path(source))
                if number:
                    numbered[number] = record.key
        return numbered

    def dependency(self, draft: Path, value, numbered: dict[str, str]) -> str:
        """A `depends_on` entry as a key: a draft number of this discussion becomes that draft's key."""
        text = str(value).strip()
        if KEY_RE.fullmatch(text):
            return text
        if text.isdigit():
            number = f"{int(text):02d}"
            if number in numbered:
                return numbered[number]
            raise TaskError(f"{draft}: depends on draft {number}, which isn't filed and isn't being filed")
        raise TaskError(f"{draft}: depends_on {value!r} is neither a draft number nor a key")

    def repair(self, folder: Path, renames: dict, dry_run: bool) -> list[str]:
        """Fix wikilinks to the moved drafts in the discussion's notes and the tasks folder."""
        if not renames:
            return []
        paths = sorted(set(folder.rglob("*.md")) | set(self.store.tasks_dir.glob("*.md"))
                       | set(self.store.tasks_dir.glob("*/*.md")))
        touched = []
        for path in paths:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            fixed = records.repair_links(text, renames)
            if fixed != text:
                touched.append(str(path))
                if not dry_run:
                    frontmatter.write_atomic(path, fixed)
        return touched

    def update_plan(self, plan: Path, folder: Path, entries: list[Entry], skipped: list[dict],
                    epic_key: str | None, dry_run: bool) -> dict | None:
        """Point the plan at what was filed: its `epic` when this run filed the epic, and its `## Proposed Stories`
        section rewritten as links to every task filed from the discussion."""
        if not plan.is_file() or not entries:
            return None
        text = plan.read_text(encoding="utf-8")
        fields, _, _ = frontmatter.parse(text, plan)
        out = {"path": str(plan), "epic": None, "proposed_stories": False}
        if epic_key and not fields.get("epic"):
            text = frontmatter.updated(text, {"epic": epic_key}, plan)
            out["epic"] = epic_key

        tickets = self.store.rel(folder / TICKETS)
        filed = {e.number: (e.values["key"], e.values["summary"], e.target, e.values["type"]) for e in entries}
        for record in self.store.records():
            source = record.fields.get("source_doc") or ""
            number = draft_number(Path(source))
            if record.key and number and source.rsplit("/", 1)[0] == tickets and number not in filed:
                filed[number] = (record.key, record.summary, record.path, record.type)
        lines = [f"- {self.store.link(path, key)} {summary}"
                 for _, (key, summary, path, kind) in sorted(filed.items()) if kind != "epic"]
        waiting = [self.store.link(Path(s["draft"]), Path(s["draft"]).stem) for s in skipped]
        content = ["", "Filed as task records:", "", *lines] if lines else ["", "No stories filed yet."]
        if waiting:
            content += ["", "Not filed yet: " + ", ".join(waiting)]
        _, fm, body = frontmatter.parse(text, plan)
        match = records.section(body, "Proposed Stories")
        if match:
            body = body[:match.start(1)] + "\n".join(content) + "\n\n" + body[match.end(1):].lstrip("\n")
            text = frontmatter.compose(fm, body)
            out["proposed_stories"] = True
        if not dry_run:
            frontmatter.write_atomic(plan, text)
        return out

    # -----------------------------------------------------------------------------------------------------------------
    # Defects

    def create_defect(self, values: dict, dry_run: bool = False) -> dict:
        """A defect record straight into the tasks folder, with the next key. The caller holds the tasks lock."""
        summary = str(values.get("summary") or "").strip()
        if not records.slugify(summary):
            raise TaskError("create-defect: a summary is required")
        owner = values.get("owner") or records.CLAUDE
        problem = self.store.check_value("owner", owner)
        depends = values.get("depends_on") or []
        problem = problem or next((self.store.check_value("depends_on", d) for d in depends
                                   if self.store.check_value("depends_on", d)), None)
        if problem:
            raise TaskError(problem)
        filled = {**values, "summary": summary, "owner": owner, "depends_on": depends,
                  "created": datetime.date.today(), "key": self.next_keys(1, dry_run)["keys"][0]}
        path = self.store.record_path(filled["key"], summary)
        record = self.store.new_record(path, self.store.fill("defect", filled), dry_run)
        out = describe(record)
        if dry_run:
            out["dry_run"] = True
        return out
