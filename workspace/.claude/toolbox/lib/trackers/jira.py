"""The `jira` tracker: keys come from Jira, where the jira-filer agent files a discussion's drafts; a task's requirement
is imported from Jira into its record; status writes back per `tracker.jira.write_back`.

A script can't call an MCP tool, so the session does every Jira call and hands this module what came back, saved as
the tool returned it:

- `import <KEY> --issue <file>`: a `getJiraIssue` response becomes a task record.
- `file <discussion>` with no keys prints the filing plan: the drafts to file, in order, and what they carry.
- `file <discussion> --keys 00=ABC-1 01=ABC-2 --issues <file>`: the keys the agent returned, checked against a
  `searchJiraIssuesUsingJql` response for them (summary, issue type, parent epic, site), are written into the drafts,
  which then move into the tasks folder as the vault tracker moves them.
- `set` returns a `write_back` the session carries out (references/jira-write-back.md).

Field ids (acceptance criteria, sprint), issue type names, labels and the site come from config, never from here.
"""

from __future__ import annotations

import datetime
import json
import re
from pathlib import Path

import frontmatter
import records
from records import KEY_RE, PLAN, TICKETS, Record, TaskError, draft_number
from trackers import describe
from trackers.vault import Entry, VaultTracker

ISSUE_TYPES = {"story": "Story", "epic": "Epic", "defect": "Bug"}
STATUSES = {"in-progress": "In Progress", "review": "In Review", "done": "Done"}
BLOCKS = "blocks"                 # the link type whose inward side is a dependency
AC_HEADING = re.compile(r"^(?:#{1,6}\s*|\*\*|h\d\.\s*)Acceptance Criteria\**:?\s*$", re.I | re.M)
PAIR = re.compile(r"^(\d{1,2})=([A-Z][A-Z0-9]*-\d+)$")


class NeedsImport(TaskError):
    """A key with no task record: the session fetches the issue and runs `import`."""

    def __init__(self, message: str, key: str):
        super().__init__(message)
        self.key = key


class JiraTracker(VaultTracker):
    name = "jira"
    requirement = "jira"
    unsupported = {
        "next-key": "Jira assigns keys",
        "create-defect": "draft the defect in a discussion and file it with create-tickets",
        "queue": "Jira holds the queue",
        "epic-status": "Jira holds epic status",
        "check": "Jira holds the dependencies",
    }

    @property
    def jira(self) -> dict:
        return self.cfg.jira

    @property
    def issue_types(self) -> dict:
        return {**ISSUE_TYPES, **(self.jira.get("issue_types") or {})}

    def browse_url(self, key: str) -> str:
        return f"https://{self.jira['site']}/browse/{key}"

    # -----------------------------------------------------------------------------------------------------------------
    # Requirement source

    def find_or_make(self, ref: str, dry_run: bool = False) -> tuple[Record, Path | None]:
        kind, value = self.store.classify(ref)
        if kind == "key":
            record = self.store.by_key(value)
            if record:
                return record, None
            raise self.not_imported(value)
        if kind == "work_id":
            raise TaskError(f"{ref}: no task record named {ref}; a Jira task's work id is its key")
        found = self.existing(kind, value)
        if found:
            return found, None
        if kind == "draft":
            raise TaskError(f"{value}: a draft becomes a task when it's filed in Jira; run create-tickets for "
                            f"{value.parent.parent.name} first")
        # A plan has no Jira issue: its task is keyless, named for the plan's title.
        values = self.store.plan_values(value)
        values["key"] = None
        path = self.store.record_path(None, values["summary"])
        if path.exists():
            raise TaskError(f"{path} already has the work id {path.stem}, for another source: "
                            f"{self.store.record(path).fields.get('source_doc')}")
        return self.store.new_record(path, self.store.fill("task", values), dry_run), value

    def not_imported(self, key: str) -> TaskError:
        self.check_key(key)
        for draft in self.store.all_drafts():
            try:
                if frontmatter.fields(draft).get("key") == key:
                    return TaskError(self.missing_key(key))
            except frontmatter.FrontmatterError:
                continue
        return NeedsImport(f"{key}: no task record yet; fetch the issue with getJiraIssue, save the response to a "
                           f"file, and run `tasks.py import {key} --issue <file>`", key)

    def missing_key(self, key: str) -> str:
        for draft in self.store.all_drafts():
            try:
                if frontmatter.fields(draft).get("key") == key:
                    return (f"{key}: its draft {draft} holds the key but was never moved; run create-tickets for "
                            f"{draft.parent.parent.name} again, which records it")
            except frontmatter.FrontmatterError:
                continue
        return f"{key}: no task record with that key in {self.store.tasks_dir}"

    def check_key(self, key: str) -> None:
        if not self.is_key(key):
            raise TaskError(f"{key}: not a {self.prefix} key; this workspace's Jira project is {self.prefix}")

    def work_id(self, ref: str) -> dict:
        kind, value = self.store.classify(ref)
        if kind == "key":
            record = self.store.by_key(value)
            if record:
                return describe(record)
            self.check_key(value)
            return {"work_id": value, "key": value, "summary": None, "doc": None,
                    "note": "no task record yet; context says how to import it"}
        if kind == "work_id":
            raise TaskError(f"{ref}: no task record named {ref}; a Jira task's work id is its key")
        found = self.existing(kind, value)
        if found:
            return describe(found)
        if kind == "plan":
            return self.source_work_id(kind, value)
        out = self.source_work_id(kind, value)
        key = out["key"] = self.store.draft_values(value)[0]["key"]
        out["work_id"] = key
        if not key:
            out["note"] = "the key comes when the discussion is filed in Jira"
        return out

    # -----------------------------------------------------------------------------------------------------------------
    # Import

    def import_issue(self, key: str, issue: Path, dry_run: bool = False) -> dict:
        """A task record from a saved `getJiraIssue` response, for an issue no discussion here filed."""
        self.check_key(key)
        existing = self.store.by_key(key)
        if existing:
            raise TaskError(f"{key} already has a task record: {existing.path}")
        missing = self.not_imported(key)
        if not isinstance(missing, NeedsImport):
            raise missing
        response = load_response(issue)
        self.check_site(response, issue)
        node = issue_nodes(response, issue).get(key)
        if not node:
            raise TaskError(f"{issue}: holds no issue {key}")
        fields = node.get("fields") or {}
        summary = str(fields.get("summary") or "").strip()
        if not records.slugify(summary):
            raise TaskError(f"{issue}: {key} has no summary")

        kind = self.task_type(fields)
        description = text_of(fields.get("description"))
        ac_field = (self.jira.get("fields") or {}).get("acceptance_criteria")
        if ac_field:
            acceptance = text_of(fields.get(ac_field))
        else:
            description, acceptance = split_acceptance(description)
        values = {
            "type": kind,
            "key": key,
            "summary": summary,
            "epic": self.parent_epic(fields),
            "owner": records.CLAUDE,
            "depends_on": dependencies(fields),
            "created": datetime.date.today(),
            "found_in": None,
            "legacy_id": None,
            "source": None,
            "source_doc": None,
        }
        status = ((fields.get("status") or {}).get("statusCategory") or {}).get("key")
        url = node.get("webUrl") or self.browse_url(key)
        sections = [f"# {summary}", f"Jira: [{key}]({url})", "## Description", description or ""]
        if kind != "epic":
            sections += ["## Acceptance Criteria", acceptance or ""]
        body = "\n\n".join(s for s in sections) + "\n"
        body = re.sub(r"\n{3,}", "\n\n", body)
        text = self.store.fill(kind, values, body)
        if status == "done":
            text = frontmatter.updated(text, {"status": "done"})
        path = self.store.record_path(key, summary)
        record = self.store.new_record(path, text, dry_run)
        out = {**describe(record), "type": kind, "epic": values["epic"], "depends_on": values["depends_on"],
               "status": record.status, "url": url}
        if dry_run:
            out["dry_run"] = True
        return out

    def task_type(self, fields: dict) -> str:
        name = ((fields.get("issuetype") or {}).get("name") or "").lower()
        types = self.issue_types
        if name and name == types["epic"].lower():
            return "epic"
        if name and name == types["defect"].lower():
            return "defect"
        return "story"

    def parent_epic(self, fields: dict) -> str | None:
        """The parent's key, when the parent is an epic (or the response doesn't say what it is)."""
        parent = fields.get("parent") or {}
        key = parent.get("key")
        kind = (((parent.get("fields") or {}).get("issuetype")) or {}).get("name")
        if key and (not kind or kind.lower() == self.issue_types["epic"].lower()):
            return key
        return None

    def check_site(self, response: dict, source: Path) -> None:
        """A response from another Jira site (another cloud id) is never taken."""
        cloud = (response.get("context") or {}).get("cloudId") if isinstance(response, dict) else None
        if cloud and cloud != self.jira["cloud_id"]:
            raise TaskError(f"{source}: came from cloud id {cloud}, not this workspace's {self.jira['cloud_id']} "
                            f"({self.jira['site']})")

    # -----------------------------------------------------------------------------------------------------------------
    # Filing

    def file(self, discussion: str, dry_run: bool = False, keys: list[str] | None = None,
             issues: Path | None = None) -> dict:
        """With no keys, the filing plan. With keys, record them: check each against the saved issues, write it into
        its draft, and move the drafts into the tasks folder (the vault tracker's `place`)."""
        folder = self.store.resolve_discussion(discussion)
        if not keys:
            if issues:
                raise TaskError("file: --issues needs --keys, the keys Jira gave each draft")
            return self.filing_plan(folder)
        if not issues:
            raise TaskError("file: --keys needs --issues, the saved search response for those keys, to check them "
                            "against")
        mapping = self.parse_keys(keys)
        entries, skipped = self.gather(folder)
        by_number = {e.number: e for e in entries}
        filed_before = self.filed_numbers(folder)
        held = {r.key: r for r in self.store.records() if r.key}

        problems, already = [], []
        for number, key in mapping.items():
            if number in filed_before:
                if filed_before[number] != key:
                    problems.append(f"draft {number} was filed as {filed_before[number]}, not {key}")
                else:
                    already.append({"number": number, "key": key})
                continue
            entry = by_number.get(number)
            if not entry:
                waiting = [s for s in skipped if draft_number(Path(s["draft"])) == number]
                problems.append(f"draft {number}: {waiting[0]['reason']}" if waiting
                                else f"no draft {number} in {folder / TICKETS}")
                continue
            if entry.reserved:
                problems.append(f"{entry.draft}: reserved_key is for the vault tracker; Jira assigns keys")
            if entry.values["key"] and entry.values["key"] != key:
                problems.append(f"{entry.draft}: already holds {entry.values['key']}, not {key}; never file a "
                                "draft twice")
            if key in held:
                problems.append(f"{key}: already the key of {held[key].path}")
            entry.values["key"] = key
        if problems:
            raise TaskError("file: " + "; ".join(problems))

        taking = [e for e in entries if e.values["key"]]
        for entry in entries:
            if not entry.values["key"]:
                skipped.append({"draft": str(entry.draft), "reason": "approved, but no Jira key given"})
        epics = self.epic_entries(folder, taking)
        report = self.verify(folder, taking, epics, issues)
        out = self.place(folder, taking, skipped, epics, dry_run) if taking else {
            "discussion": str(folder), "filed": [], "skipped": skipped, "links_repaired": [], "plan": None}
        out["already_filed"] = already
        out["verified"] = report["verified"]
        out["warnings"] = report["warnings"]
        for item in out["filed"]:
            item["url"] = self.browse_url(item["key"])
        if dry_run:
            out["dry_run"] = True
        return out

    def parse_keys(self, pairs: list[str]) -> dict[str, str]:
        mapping = {}
        for pair in pairs:
            match = PAIR.match(pair.strip())
            if not match:
                raise TaskError(f"file: expected NN=KEY, like 01={self.prefix}-12; got {pair!r}")
            number, key = f"{int(match.group(1)):02d}", match.group(2)
            self.check_key(key)
            if mapping.get(number, key) != key:
                raise TaskError(f"file: draft {number} given two keys, {mapping[number]} and {key}")
            mapping[number] = key
        repeated = sorted({k for k in mapping.values() if list(mapping.values()).count(k) > 1})
        if repeated:
            raise TaskError(f"file: {', '.join(repeated)} given to more than one draft")
        return mapping

    def verify(self, folder: Path, entries: list[Entry], epics: list[Entry], issues: Path) -> dict:
        """Check every key against the saved issues: it exists, its summary is the draft's, its issue type is the
        draft type's, and a story's parent is the discussion's epic. A Blocks link a dependency should have and
        doesn't, or a configured label missing, is a warning: the issue is filed either way."""
        response = load_response(issues)
        self.check_site(response, issues)
        nodes = issue_nodes(response, issues)
        plan = folder / PLAN
        epic_key = epics[0].values["key"] if epics else (
            frontmatter.fields(plan).get("epic") if plan.is_file() else None)
        numbered = {**self.filed_numbers(folder), **{e.number: e.values["key"] for e in entries}}
        problems, warnings, verified = [], [], []
        for entry in entries:
            key = entry.values["key"]
            node = nodes.get(key)
            if not node:
                problems.append(f"{key}: not in {issues}")
                continue
            fields = node.get("fields") or {}
            summary = " ".join(str(fields.get("summary") or "").split())
            if summary != " ".join(entry.values["summary"].split()):
                problems.append(f"{key}: Jira's summary is {summary!r}, but draft {entry.number}'s is "
                                f"{entry.values['summary']!r}")
            kind = (fields.get("issuetype") or {}).get("name")
            want = self.issue_types[entry.values["type"]]
            if kind is not None and kind.lower() != want.lower():
                problems.append(f"{key}: filed as a {kind}, but draft {entry.number} is a {entry.values['type']} "
                                f"({want})")
            parent = (fields.get("parent") or {}).get("key")
            want_parent = entry.values["epic"] or epic_key if entry.values["type"] != "epic" else None
            if "parent" in fields and want_parent and parent != want_parent:
                problems.append(f"{key}: its parent is {parent or 'none'}, not the epic {want_parent}")
            if "issuelinks" in fields and entry.values["type"] != "epic":
                linked = set(dependencies(fields))
                for value in entry.values["depends_on"]:
                    text = str(value).strip()
                    dep = numbered.get(f"{int(text):02d}") if text.isdigit() else text
                    if dep and dep not in linked:
                        warnings.append(f"{key}: no Blocks link from {dep}, which it depends on")
            if "labels" in fields:
                for label in self.jira.get("labels") or []:
                    if label not in (fields.get("labels") or []):
                        warnings.append(f"{key}: missing the label {label}")
            verified.append(key)
        if problems:
            raise TaskError("file: the keys don't match Jira; nothing written. " + "; ".join(problems))
        return {"verified": verified, "warnings": warnings}

    def filing_plan(self, folder: Path) -> dict:
        """What create-tickets files and in which order: the epic first, then the stories by number. Writes
        nothing."""
        entries, skipped = self.gather(folder) if self.store.drafts(folder) else ([], [])
        epics = self.epic_entries(folder, entries)
        plan = folder / PLAN
        plan_epic = frontmatter.fields(plan).get("epic") if plan.is_file() else None
        filed = self.filed_numbers(folder)
        rows = []
        for entry in sorted(entries, key=lambda e: (e.values["type"] != "epic", e.number)):
            extra = frontmatter.fields(entry.draft)
            rows.append({
                "number": entry.number,
                "draft": str(entry.draft),
                "type": entry.values["type"],
                "issue_type": self.issue_types[entry.values["type"]],
                "summary": entry.values["summary"],
                "key": entry.values["key"],
                "epic": None if entry.values["type"] == "epic" else entry.values["epic"] or (
                    "the epic draft's key" if epics else plan_epic),
                "depends_on": [filed.get(f"{int(d):02d}", int(d)) if str(d).isdigit() else d
                               for d in entry.values["depends_on"]],
                "estimate": extra.get("estimate") if self.jira.get("estimates") else None,
                "sprint": extra.get("sprint") or None,
                "labels": list(dict.fromkeys([*(self.jira.get("labels") or []), *(extra.get("labels") or [])])),
                "components": extra.get("components") or [],
            })
        missing_epic = [r["number"] for r in rows if r["type"] != "epic" and not r["epic"]]
        return {
            "discussion": str(folder),
            "plan_only": True,
            "to_file": [r for r in rows if not r["key"]],
            "keyed_not_moved": [r for r in rows if r["key"]],
            "already_filed": [{"number": n, "key": k, "url": self.browse_url(k)} for n, k in sorted(filed.items())],
            "skipped": skipped,
            "no_epic": missing_epic,
            "fields": self.jira.get("fields") or {},
        }

    # -----------------------------------------------------------------------------------------------------------------
    # Write-back

    def write_back(self, record: Record, changes: dict) -> dict | None:
        """The transition `set` asks for, for the session to make: none unless `write_back: transitions`, the status
        changed, the task has a key, and the config names a Jira status for the new one."""
        if self.jira["write_back"] != "transitions" or "status" not in changes or not record.key:
            return None
        if record.type == "epic":
            return None
        target = {**STATUSES, **(self.jira.get("statuses") or {})}.get(changes["status"])
        if not target:
            return None
        return {"tool": f"mcp__{self.jira['mcp_server']}__transitionJiraIssue", "cloud_id": self.jira["cloud_id"],
                "key": record.key, "status": changes["status"], "to": target,
                "how": "references/jira-write-back.md"}


# ---------------------------------------------------------------------------------------------------------------------
# Saved responses


def load_response(path: Path) -> dict:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as error:
        raise TaskError(f"{path}: can't read it: {error.strerror}") from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise TaskError(f"{path}: not JSON ({error.msg}, line {error.lineno}); save the tool's response as "
                        "returned") from None
    if not isinstance(data, (dict, list)):
        raise TaskError(f"{path}: not an issue response")
    return data if isinstance(data, dict) else {"issues": data}


def issue_nodes(response: dict, source: Path) -> dict[str, dict]:
    """Key -> issue, from a `getJiraIssue` or `searchJiraIssuesUsingJql` response: `{"issues": {"nodes": [...]}}`,
    `{"issues": [...]}`, or a bare issue."""
    issues = response.get("issues")
    if isinstance(issues, dict):
        issues = issues.get("nodes")
    if issues is None and "key" in response:
        issues = [response]
    if not isinstance(issues, list):
        raise TaskError(f"{source}: no issues in it")
    return {node["key"]: node for node in issues if isinstance(node, dict) and node.get("key")}


def dependencies(fields: dict) -> list[str]:
    """Keys this issue is blocked by: the inward side of its Blocks links."""
    found = []
    for link in fields.get("issuelinks") or []:
        kind = (link.get("type") or {})
        names = {str(kind.get("name") or "").lower(), str(kind.get("outward") or "").lower()}
        inward = (link.get("inwardIssue") or {}).get("key")
        if BLOCKS in names and inward and KEY_RE.fullmatch(inward):
            found.append(inward)
    return list(dict.fromkeys(found))


def split_acceptance(description: str) -> tuple[str, str]:
    """A description holding its own `Acceptance Criteria` heading (a site with no AC field), split at it."""
    match = AC_HEADING.search(description or "")
    if not match:
        return description, ""
    return description[:match.start()].rstrip(), description[match.end():].strip()


def text_of(value) -> str:
    """A field's text: markdown as the tool returned it, or an ADF document rendered to markdown."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return adf_to_markdown(value).strip()
    return str(value).strip()


def adf_to_markdown(node: dict) -> str:
    """Enough ADF for descriptions and AC: paragraphs, headings, lists, code, panels, hard breaks, bold and code marks.
    Unknown nodes keep their text."""
    kind = node.get("type")
    children = node.get("content") or []

    def inner(sep: str = "") -> str:
        return sep.join(adf_to_markdown(child) for child in children)

    if kind == "text":
        text = node.get("text", "")
        marks = {m.get("type") for m in node.get("marks") or []}
        if "code" in marks:
            text = f"`{text}`"
        if "strong" in marks:
            text = f"**{text}**"
        if "em" in marks:
            text = f"*{text}*"
        return text
    if kind == "hardBreak":
        return "\n"
    if kind == "paragraph":
        return inner()
    if kind == "heading":
        return "#" * int((node.get("attrs") or {}).get("level", 2)) + " " + inner()
    if kind in ("bulletList", "orderedList"):
        lines = []
        for index, item in enumerate(children, 1):
            marker = f"{index}." if kind == "orderedList" else "-"
            text = adf_to_markdown(item).replace("\n", "\n  ")
            lines.append(f"{marker} {text}")
        return "\n".join(lines)
    if kind == "listItem":
        return inner("\n")
    if kind == "codeBlock":
        return "```\n" + inner() + "\n```"
    if kind == "panel":
        panel = (node.get("attrs") or {}).get("panelType", "info")
        body = inner("\n\n").replace("\n", "\n> ")
        return f"> [!{panel}]\n> {body}"
    if kind == "doc" or children:
        return inner("\n\n")
    return node.get("text", "")
