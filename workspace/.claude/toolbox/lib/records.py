"""Task records: finding and making them, work ids, discussion drafts, templates, the publish folder and the lock.

Every task has a task record, `<tasks>/<work id>-<slug>.md` with a key, `<tasks>/<slug>.md` without one, in the
vault's tasks folder (config `vault.tasks`). The work id is the record's key when it has one, else its file name's
stem. What a record holds is in `.claude/rules/ticket-docs.md`; the templates beside `bin/` are what new records are
made from. Which sources make a record, and how keys are assigned, is the tracker's business (`trackers/`); this
module is the part every tracker shares.

Other scripts import it by path, as `bin/tasks.py` does:

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
    import config, records
    store = records.Store(config.load())
    record = store.find("PT-12")
"""

from __future__ import annotations

import contextlib
import datetime
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import config
import frontmatter

TOOLBOX = Path(__file__).resolve().parents[1]
TEMPLATES = TOOLBOX / "templates"
WORKTREES_PY = TOOLBOX / "bin" / "worktrees.py"

TICKETS = "tickets"           # a discussion's drafts folder
PLAN = "plan.md"
QUESTIONS = "questions.md"
PUBLISH = ".publish"          # <tasks>/.publish/<work id>/
LOCK = ".lock"                # held by every write in the tasks folder
COUNTER = ".counter.yml"      # the vault tracker's next key
BACKLOG = "Backlog.md"        # the vault tracker's priority order, one key per line
LOG = "Log"                   # the record body's dated log section

TYPES = ("story", "epic", "defect", "task")
DRAFT_TYPES = ("story", "epic", "defect")
STATUSES = ("todo", "in-progress", "review", "done", "stopped")
DRAFT_STATUSES = ("draft", "approved")
STAGES = ("implementing", "testing", "code-review", "publish-review", "awaiting-merge", "awaiting-ci")
CYCLES = ("shared-library", "consumers")  # `cycle`, set only while a task runs work-task's two cycles
CLAUDE = "claude"

SCALAR_FIELDS = ("status", "owner", "stage", "work_note", "cycle")  # plus the config's work.extra_fields
LIST_FIELDS = ("worktrees", "sessions", "prs", "depends_on")

# Each type's frontmatter, in order. The templates hold exactly these fields; tests check that they agree.
WORKED = ("type", "key", "summary", "epic", "status", "owner", "stage", "depends_on", "created")
PROGRESS = ("legacy_id", "source", "source_doc", "worktrees", "sessions", "prs", "work_note", "usage",
            "usage_total")
FIELDS = {
    "story": WORKED + PROGRESS,
    "task": WORKED + PROGRESS,
    "defect": WORKED + ("found_in",) + PROGRESS,
    "epic": ("type", "key", "summary", "status", "created", "legacy_id", "source", "source_doc"),
}

KEY_RE = re.compile(r"[A-Z][A-Z0-9]*-\d+")
SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")  # a keyless work id; never a key, which is uppercase
NN_RE = re.compile(r"^(\d\d)-")
PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
TEMPLATE_FIELD = re.compile(r"^(\w+):[ \t]*\{\{(\w+)\}\}[ \t]*$")
COMMENT = re.compile(r"<!--.*?-->", re.S)


class TaskError(Exception):
    """A command can't do what it was asked; the message says why."""


def slugify(text: str) -> str:
    """Lowercase, with every run of other characters collapsed to `-`: the discussion `slug` rule."""
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")


def key_number(key: str | None) -> int:
    match = re.search(r"-(\d+)$", key or "")
    return int(match.group(1)) if match else sys.maxsize


def draft_number(path: Path) -> str | None:
    match = NN_RE.match(path.name)
    return match.group(1) if match else None


@dataclass
class Record:
    """A task record: its path and frontmatter. `exists` is False for one a dry run would make."""

    path: Path
    fields: dict
    body: str = ""
    exists: bool = True

    @property
    def key(self) -> str | None:
        return self.fields.get("key") or None

    @property
    def work_id(self) -> str:
        return self.key or self.path.stem

    @property
    def type(self) -> str | None:
        return self.fields.get("type")

    @property
    def status(self) -> str | None:
        return self.fields.get("status")

    @property
    def owner(self) -> str | None:
        return self.fields.get("owner")

    @property
    def summary(self) -> str | None:
        return self.fields.get("summary")

    @property
    def depends_on(self) -> list:
        value = self.fields.get("depends_on") or []
        return [str(item) for item in value] if isinstance(value, list) else [str(value)]


@dataclass
class Scan:
    records: list[Record] = field(default_factory=list)
    unreadable: list[tuple[Path, str]] = field(default_factory=list)


class Store:
    """The task records of one workspace."""

    def __init__(self, cfg):
        self.cfg = cfg

    # -----------------------------------------------------------------------------------------------------------------
    # Paths

    @property
    def vault_dir(self) -> Path:
        return self.cfg.vault_dir

    @property
    def tasks_dir(self) -> Path:
        return self.cfg.tasks_dir

    @property
    def discussions_dir(self) -> Path:
        return self.cfg.discussions_dir

    def rel(self, path: Path) -> str:
        """A path relative to the vault, as frontmatter and wikilinks give it."""
        return Path(path).resolve().relative_to(self.vault_dir.resolve()).as_posix()

    def link(self, path: Path, alias: str | None = None) -> str:
        target = self.rel(path).removesuffix(".md")
        return f"[[{target}|{alias}]]" if alias else f"[[{target}]]"

    def path_of(self, ref: str) -> Path | None:
        """The file a path argument names, as given or relative to the vault; None when there is none."""
        given = Path(ref).expanduser()
        for candidate in (given, self.vault_dir / given):
            if candidate.is_file():
                return candidate.resolve()
        return None

    def record_path(self, key: str | None, summary: str) -> Path:
        """`<tasks>/<KEY>-<slug>.md`, or `<tasks>/<slug>.md` with no key."""
        slug = slugify(summary)
        if not slug:
            raise TaskError(f"no summary to name a task record after: {summary!r}")
        return self.tasks_dir / (f"{key}-{slug}.md" if key else f"{slug}.md")

    def publish_dir(self, work_id: str) -> Path:
        """Where a task's approved commit messages, announcement text and stop reports live."""
        return self.tasks_dir / PUBLISH / work_id

    def is_record_path(self, path: Path) -> bool:
        path = Path(path).resolve()
        return path.suffix == ".md" and path.parent == self.tasks_dir.resolve() and not path.name.startswith(".")

    def _in_discussions(self, path: Path) -> bool:
        return Path(path).resolve().is_relative_to(self.discussions_dir.resolve())

    def is_plan(self, path: Path) -> bool:
        return Path(path).name == PLAN and self._in_discussions(path)

    def is_draft(self, path: Path) -> bool:
        path = Path(path)
        return path.suffix == ".md" and path.parent.name == TICKETS and self._in_discussions(path)

    def discussion_of(self, record: Record) -> Path | None:
        """The discussion a record came from, by its `source_doc`: a plan's folder, or a draft's discussion."""
        source = record.fields.get("source_doc")
        if not source:
            return None
        path = self.vault_dir / source
        if path.name == PLAN:
            return path.parent
        if path.parent.name == TICKETS:
            return path.parent.parent
        return None

    def resolve_discussion(self, ref: str) -> Path:
        """A discussion folder: absolute, relative to the vault, or a bare name found under the discussions folder."""
        given = Path(ref).expanduser()
        for candidate in (given, self.vault_dir / given):
            if candidate.is_dir():
                return candidate.resolve()
        hits = sorted(p for p in self.discussions_dir.rglob(given.name) if p.is_dir()) \
            if self.discussions_dir.is_dir() else []
        if len(hits) == 1:
            return hits[0].resolve()
        if len(hits) > 1:
            raise TaskError(f"{ref}: names {len(hits)} discussions: {', '.join(map(str, hits))}")
        raise TaskError(f"{ref}: no such discussion in {self.discussions_dir}")

    def drafts(self, discussion: Path) -> list[Path]:
        folder = Path(discussion) / TICKETS
        return sorted(folder.glob("*.md")) if folder.is_dir() else []

    def all_drafts(self) -> list[Path]:
        if not self.discussions_dir.is_dir():
            return []
        return sorted(self.discussions_dir.rglob(f"{TICKETS}/*.md"))

    # -----------------------------------------------------------------------------------------------------------------
    # Reading records

    def scan(self) -> Scan:
        """Every task record in the tasks folder. Notes that aren't records (Backlog.md, a dashboard) are skipped;
        records whose frontmatter can't be read are listed as unreadable."""
        found = Scan()
        if not self.tasks_dir.is_dir():
            return found
        for path in sorted(self.tasks_dir.glob("*.md")):
            if path.name.startswith(".") or path.name == BACKLOG:
                continue
            try:
                fields, body = frontmatter.read(path)
            except frontmatter.FrontmatterError as error:
                found.unreadable.append((path, str(error)))
                continue
            if fields.get("type") in TYPES:
                found.records.append(Record(path.resolve(), fields, body))
        return found

    def records(self) -> list[Record]:
        return self.scan().records

    def record(self, path: Path) -> Record:
        try:
            fields, body = frontmatter.read(path)
        except frontmatter.FrontmatterError as error:
            raise TaskError(str(error)) from None
        return Record(Path(path).resolve(), fields, body)

    def _one(self, hits: list[Record], what: str) -> Record | None:
        if len(hits) > 1:
            raise TaskError(f"{what}: expected one task record, found {len(hits)}: "
                            f"{', '.join(str(r.path) for r in hits)}")
        return hits[0] if hits else None

    def by_key(self, key: str) -> Record | None:
        records = self.records()
        hits = [r for r in records if r.key == key]
        return self._one(hits or [r for r in records if r.path.name.startswith(f"{key}-")], key)

    def by_stem(self, stem: str) -> Record | None:
        return self._one([r for r in self.records() if r.path.stem == stem], stem)

    def by_source(self, source: Path) -> Record | None:
        """The record made from a draft or plan (its `source_doc`)."""
        rel = self.rel(source)
        return self._one([r for r in self.records() if r.fields.get("source_doc") == rel], rel)

    def find(self, ref: str) -> Record:
        """A record from a work id (a key, or a keyless record's stem) or its path."""
        if KEY_RE.fullmatch(ref):
            record = self.by_key(ref)
            if record:
                return record
            raise TaskError(f"{ref}: no task record with that key in {self.tasks_dir}")
        if SLUG_RE.fullmatch(ref):
            record = self.by_stem(ref)
            if record:
                return record
        path = self.path_of(ref)
        if path and self.is_record_path(path):
            return self.record(path)
        if path:
            raise TaskError(f"{path}: not a task record in {self.tasks_dir}")
        raise TaskError(f"{ref}: not a work id or a task record in {self.tasks_dir}")

    def classify(self, ref: str) -> tuple[str, object]:
        """What a `context` or `work-id` argument names: ("key", key), ("record", Record), ("work_id", slug),
        ("plan", path) or ("draft", path)."""
        if KEY_RE.fullmatch(ref):
            return "key", ref
        if SLUG_RE.fullmatch(ref):
            record = self.by_stem(ref)
            return ("record", record) if record else ("work_id", ref)
        path = self.path_of(ref)
        if not path:
            raise TaskError(f"{ref}: not a key, a work id or a file")
        if self.is_record_path(path):
            return "record", self.record(path)
        if self.is_plan(path):
            return "plan", path
        if self.is_draft(path):
            return "draft", path
        raise TaskError(f"{path}: not a task record, a draft in a discussion's {TICKETS}/ folder, or a "
                        f"discussion's {PLAN}")

    # -----------------------------------------------------------------------------------------------------------------
    # Making records

    def template(self, kind: str) -> str:
        if kind not in TYPES:
            raise TaskError(f"{kind}: not a task type ({', '.join(TYPES)})")
        return (TEMPLATES / f"{kind}.md").read_text(encoding="utf-8")

    def fill(self, kind: str, values: dict, body: str | None = None) -> str:
        """A new record's text: the type's template with `{{field}}` placeholders filled from `values`. Frontmatter
        placeholders take YAML values; body placeholders take text. `body` replaces the template's body."""
        fm, template_body = frontmatter.split(self.template(kind), f"templates/{kind}.md")
        lines = []
        for line in fm.rstrip("\n").split("\n"):
            match = TEMPLATE_FIELD.match(line)
            lines.append(f"{match.group(1)}:{frontmatter.render_value(values.get(match.group(2)))}"
                         if match else line)
        fm = "\n".join(lines) + "\n"
        frontmatter.check(fm, f"a new {kind}")
        if body is None:
            body = PLACEHOLDER.sub(lambda m: str(values.get(m.group(1)) or ""), template_body)
            body = re.sub(r"\n{3,}", "\n\n", body)  # an empty placeholder leaves no gap of its own
        return frontmatter.compose(fm, with_log(body))

    def plan_values(self, plan: Path) -> dict:
        """What a record made from a plan takes from it: its title and epic, and links back."""
        fields, _ = frontmatter.read(plan)
        title = str(fields.get("title") or "").strip()
        if not slugify(title):
            raise TaskError(f"{plan}: no title in its frontmatter to name a task record after")
        return {"summary": title, "epic": fields.get("epic") or None, "owner": CLAUDE,
                "created": datetime.date.today(), "source": self.link(plan, plan.parent.name),
                "source_doc": self.rel(plan), "depends_on": []}

    def draft_values(self, draft: Path) -> tuple[dict, str]:
        """A draft's fields as a record takes them, and its body."""
        fields, body = frontmatter.read(draft)
        kind = fields.get("type")
        if kind not in DRAFT_TYPES:
            raise TaskError(f"{draft}: type is {kind!r}; a draft is one of {', '.join(DRAFT_TYPES)}")
        summary = str(fields.get("summary") or "").strip()
        if not slugify(summary):
            raise TaskError(f"{draft}: no summary")
        plan = draft.parent.parent / PLAN
        source = self.link(plan, plan.parent.name) if plan.is_file() else None
        depends = fields.get("depends_on") or []
        values = {
            "type": kind,
            "key": fields.get("key") or None,
            "summary": summary,
            "epic": fields.get("epic") or None,
            "owner": fields.get("owner") or CLAUDE,
            "depends_on": depends if isinstance(depends, list) else [depends],
            "created": fields.get("created") or datetime.date.today(),
            "found_in": fields.get("found_in") or None,
            "legacy_id": fields.get("legacy_id") or None,
            "source": fields.get("source") or source,
            "source_doc": fields.get("source_doc") or self.rel(draft),
        }
        return values, body

    def new_record(self, path: Path, text: str, dry_run: bool) -> Record:
        """Write a new record, refusing to overwrite one."""
        if path.exists():
            raise TaskError(f"{path} already exists")
        fields, _, body = frontmatter.parse(text, path)
        if not dry_run:
            frontmatter.write_atomic(path, text)
        return Record(path, fields, body, exists=not dry_run)

    # -----------------------------------------------------------------------------------------------------------------
    # Changing records

    def owners(self) -> tuple[str, str]:
        return CLAUDE, self.cfg.owner

    def stages(self) -> tuple[str, ...]:
        return STAGES + tuple(s for s in self.cfg.get("work.stages") if s not in STAGES)

    def check_value(self, field_name: str, value) -> str | None:
        """Why `value` can't go in `field_name`, or None when it can."""
        if field_name == "status" and value not in STATUSES:
            return f"status: {value!r} is not one of {', '.join(STATUSES)}"
        if field_name == "owner" and value not in self.owners():
            return f"owner: {value!r} is not one of {', '.join(self.owners())}"
        if field_name == "stage" and value and value not in self.stages():
            return f"stage: {value!r} is not one of {', '.join(self.stages())} (config work.stages adds more)"
        if field_name == "depends_on" and self.cfg.key_prefix and not KEY_RE.fullmatch(str(value)):
            return f"depends_on: {value!r} is not a key"
        if field_name == "cycle" and value:
            if value not in CYCLES:
                return f"cycle: {value!r} is not one of {', '.join(CYCLES)}"
            if not self.cfg.shared_libraries:
                return "cycle: this workspace configures no shared libraries (shared_libraries.names), so a task " \
                       "runs one cycle and has no cycle field"
        allowed = self.cfg.extra_fields.get(field_name)
        if value and isinstance(allowed, list) and value not in allowed:
            return f"{field_name}: {value!r} is not one of {', '.join(allowed)} (config work.extra_fields)"
        if value and allowed == "text" and "\n" in str(value):
            return f"{field_name}: one line only"
        return None

    def settable(self) -> tuple[str, ...]:
        """The fields set takes: the built-in scalars, then the config's work.extra_fields."""
        return SCALAR_FIELDS + tuple(self.cfg.extra_fields)

    def check_names(self, record: Record, names: list[str]) -> None:
        """Refuse a field name get can't answer for: neither a record field, a declared extra field, nor in the
        record already (an older record's own field)."""
        known = set(config.RECORD_FIELDS) | set(self.cfg.extra_fields) | set(record.fields)
        unknown = [name for name in names if name not in known]
        if unknown:
            raise TaskError(f"{', '.join(unknown)}: not a task-record field; a workspace declares its own under "
                            "the config's work.extra_fields")

    def set_fields(self, record: Record, values: dict, dry_run: bool = False) -> dict:
        """Set scalar fields (status, owner, stage, work_note, cycle, and the config's extra fields). An empty
        value clears the field."""
        for name, value in values.items():
            if name not in self.settable():
                extra = "; the config's work.extra_fields declares any others" if name not in config.RECORD_FIELDS \
                    else ""
                raise TaskError(f"{name}: not a field set takes ({', '.join(self.settable())}){extra}; lists take "
                                "append or set-list")
            problem = self.check_value(name, value)
            if problem:
                raise TaskError(problem)
        # Clearing a field the record doesn't have yet (a one-cycle task's `cycle`) adds nothing.
        writes = {name: value or None for name, value in values.items() if value or name in record.fields}
        if writes:
            frontmatter.update(record.path, writes, dry_run)
        record.fields.update(values)
        return values

    def _list(self, record: Record, name: str, values: list[str]) -> list:
        if name not in LIST_FIELDS:
            raise TaskError(f"{name}: not a list field ({', '.join(LIST_FIELDS)})")
        for value in values:
            problem = self.check_value(name, value)
            if problem:
                raise TaskError(problem)
        current = record.fields.get(name) or []
        if not isinstance(current, list):
            raise TaskError(f"{name} is not a list in {record.path}")
        return current

    def append(self, record: Record, name: str, values: list[str], dry_run: bool = False) -> list[str]:
        """Add the values not already in a list field; returns the ones added."""
        current = self._list(record, name, values)
        added = [v for v in dict.fromkeys(values) if v not in current]
        if added:
            frontmatter.update(record.path, {name: current + added}, dry_run)
            record.fields[name] = current + added
        return added

    def set_list(self, record: Record, name: str, values: list[str], dry_run: bool = False) -> list[str]:
        """Replace a list field; no values empties it."""
        self._list(record, name, values)
        values = list(dict.fromkeys(values))
        frontmatter.update(record.path, {name: values}, dry_run)
        record.fields[name] = values
        return values

    def log(self, record: Record, text: str, dry_run: bool = False, today: datetime.date | None = None) -> str:
        """Append a dated entry to the record's `## Log`."""
        entry = f"- {(today or datetime.date.today()).isoformat()}: {' '.join(text.split())}"
        _, fm, body = frontmatter.parse(record.path.read_text(encoding="utf-8"), record.path)
        new = frontmatter.compose(fm, add_log_entry(body, entry))
        if not dry_run:
            frontmatter.write_atomic(record.path, new)
        return entry

    # -----------------------------------------------------------------------------------------------------------------
    # Locking

    @contextlib.contextmanager
    def lock(self, dry_run: bool = False):
        """Serialize writes in the tasks folder across processes. A dry run writes nothing, so it takes no lock."""
        if dry_run:
            yield
            return
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        with open(self.tasks_dir / LOCK, "a+") as handle:
            _lock(handle)
            try:
                yield
            finally:
                _unlock(handle)

    # -----------------------------------------------------------------------------------------------------------------
    # Context

    def context(self, record: Record, made_from: Path | None, tracker, session: str | None,
                dry_run: bool = False) -> dict:
        """What a session needs to start or resume a task, from its record. Records the session."""
        fields = dict(record.fields)
        discussion = self.discussion_of(record)
        plan = discussion / PLAN if discussion else None
        questions = discussion / QUESTIONS if discussion else None
        publish = self.publish_dir(record.work_id)
        out = {
            "doc": str(record.path),
            "work_id": record.work_id,
            "key": record.key,
            "summary": record.summary,
            "tracker": tracker.name,
            "requirement": tracker.requirement,
            "made": made_from is not None,
            "made_from": str(made_from) if made_from else None,
            "fields": fields,
            "needs_requirement": needs_requirement(record.body),
            "plan": str(plan) if plan and plan.is_file() else None,
            "questions": str(questions) if questions and questions.is_file() else None,
            "publish_dir": str(publish),
            "worktrees": worktrees(record.work_id),
            "new_session": None,
        }
        if session:
            out["new_session"] = session not in (fields.get("sessions") or [])
            if record.exists:
                self.append(record, "sessions", [session], dry_run)
        if not dry_run:
            publish.mkdir(parents=True, exist_ok=True)
        if dry_run:
            out["dry_run"] = True
        return out


# ---------------------------------------------------------------------------------------------------------------------
# Bodies


def section(body: str, heading: str) -> re.Match | None:
    """The `## <heading>` section of a body: group 1 is its content, up to the next `## ` heading or the end."""
    return re.search(rf"^## {re.escape(heading)}[ \t]*\n(.*?)(?=^## |\Z)", body, re.M | re.S)


def needs_requirement(body: str) -> bool:
    """True when the record's `## Description` holds nothing but comments: a plan's skeleton, or an unfilled draft.
    The session writes the requirement, or stops."""
    match = section(COMMENT.sub("", body), "Description")
    return not (match and match.group(1).strip())


def with_log(body: str) -> str:
    """A body ending in a `## Log` section, adding an empty one when it has none."""
    if section(body, LOG) or re.search(rf"^## {LOG}[ \t]*$", body, re.M):
        return body
    return body.rstrip("\n") + f"\n\n## {LOG}\n"


def add_log_entry(body: str, entry: str) -> str:
    body = with_log(body)
    heading = re.search(rf"^## {LOG}[ \t]*$\n?", body, re.M)
    rest = body[heading.end():]
    following = re.search(r"^## ", rest, re.M)
    content, after = (rest[:following.start()], rest[following.start():]) if following else (rest, "")
    content = content.rstrip("\n")
    content = f"{content}\n{entry}\n" if content.strip() else f"\n{entry}\n"
    return body[:heading.end()] + content + ("\n" + after if after else "")


# ---------------------------------------------------------------------------------------------------------------------
# Links


WIKILINK = re.compile(r"(!?\[\[)([^\]|#]+)")


def repair_links(text: str, renames: dict[str, tuple[str, str]]) -> str:
    """Rewrite wikilink targets broken by moving notes on disk, which Obsidian's link updating never sees.

    `renames` maps an old file stem to (new stem, new vault-relative path without `.md`). A bare target gets the new
    stem; a target with a path gets the new path. Aliases and headings are kept.
    """
    def fix(match: re.Match) -> str:
        target = match.group(2)
        stem = target.rstrip().removesuffix(".md").split("/")[-1]
        if stem not in renames:
            return match.group(0)
        new_stem, new_path = renames[stem]
        return match.group(1) + (new_path if "/" in target else new_stem)

    return WIKILINK.sub(fix, text)


# ---------------------------------------------------------------------------------------------------------------------
# Worktrees and locking helpers


def worktrees(work_id: str):
    """`worktrees.py list <work id>`'s output, None when the script isn't installed, or an error string."""
    if not WORKTREES_PY.is_file():
        return None
    proc = subprocess.run([sys.executable, str(WORKTREES_PY), "list", work_id], capture_output=True, text=True)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return f"worktrees.py list {work_id} failed: {(proc.stderr or proc.stdout).strip()}"


try:
    import fcntl

    def _lock(handle):
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)

    def _unlock(handle):
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
except ImportError:  # Windows
    import msvcrt

    def _lock(handle):
        handle.seek(0)
        while True:
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                return
            except OSError:
                continue

    def _unlock(handle):
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
