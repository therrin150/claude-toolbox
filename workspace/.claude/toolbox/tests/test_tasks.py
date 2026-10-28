"""Tests for bin/tasks.py and lib/{frontmatter,records,board}.py and lib/trackers/, against temporary vaults.

Run from the repo root:

    python3 -m unittest discover -s workspace/.claude/toolbox/tests
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import yaml

TOOLBOX = Path(__file__).resolve().parents[1]
TASKS = TOOLBOX / "bin" / "tasks.py"
sys.path.insert(0, str(TOOLBOX / "lib"))

import config  # noqa: E402
import frontmatter  # noqa: E402
import records  # noqa: E402

DISCUSSION = "Discussions/2026/10/2026-10-20-pantry-shelves"
TODAY = datetime.date.today().isoformat()


def note(fields: dict, body: str = "") -> str:
    return "---\n" + yaml.safe_dump(fields, sort_keys=False) + "---\n\n" + textwrap.dedent(body).lstrip("\n")


class Vault(unittest.TestCase):
    """A scratch workspace with a vault; each test picks its tracker."""

    tracker = "vault"
    extra: dict = {}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp)
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(config.ENV_WORKSPACE, None)
        self.root = self.tmp / "ws"
        (self.root / ".claude").mkdir(parents=True)
        raw = {
            "version": 1,
            "features": ["tasks"],
            "workspace": {"name": "pantry", "layout": "single"},
            "user": {"handle": "jdoe", "name": "Jordan"},
            "vault": {"path": "vault"},
            "tracker": {"kind": self.tracker},
            **self.extra,
        }
        if self.tracker == "vault":
            raw["tracker"]["key_prefix"] = "PT"
        if self.tracker == "jira":
            raw["tracker"]["jira"] = {"site": "example.atlassian.net", "cloud_id": "0000", "project": "PT",
                                      "account": "jdoe@example.com"}
        (self.root / config.CONFIG_PATH).write_text(yaml.safe_dump(raw), encoding="utf-8")
        self.vault = self.root / "vault"
        self.tasks = self.vault / "Tasks"
        self.disc = self.vault / DISCUSSION
        (self.disc / "tickets").mkdir(parents=True)

    # Helpers

    def run_tasks(self, *args: str, ok: bool = True, cwd: Path | None = None) -> dict:
        proc = subprocess.run([sys.executable, str(TASKS), *args], cwd=cwd or self.root, capture_output=True,
                              text=True)
        out = json.loads(proc.stdout)
        if ok:
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        else:
            self.assertNotEqual(proc.returncode, 0, proc.stdout)
        return out

    def store(self) -> records.Store:
        return records.Store(config.load_workspace(self.root))

    def write(self, rel: str, fields: dict, body: str = "") -> Path:
        path = self.vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(note(fields, body), encoding="utf-8")
        return path

    def plan(self, **fields) -> Path:
        return self.write(f"{DISCUSSION}/plan.md", {"type": "plan", "title": "Pantry Shelves", "epic": None,
                                                     **fields}, """
            # Pantry Shelves

            ## Proposed Epic

            [[00-pantry-shelves]]

            ## Proposed Stories

            (a Dataview block)

            ## Open Questions

            None.
            """)

    def draft(self, name: str, **fields) -> Path:
        fields.setdefault("type", "story")
        fields.setdefault("status", "approved")
        return self.write(f"{DISCUSSION}/tickets/{name}.md", fields, f"""
            # {fields['summary']}

            ## Description

            Do it.
            """)

    def record(self, key: str, **fields) -> Path:
        fields = {"type": "story", "key": key, "summary": f"Task {key}", "status": "todo", "owner": "claude",
                  "depends_on": [], **fields}
        return self.write(f"Tasks/{key}-{records.slugify(fields['summary'])}.md", fields, "# x\n\n## Log\n")

    def fields(self, path: Path) -> dict:
        return frontmatter.fields(path)


# ---------------------------------------------------------------------------------------------------------------------


class FrontmatterTest(unittest.TestCase):
    def test_update_keeps_other_fields_as_written(self):
        text = "---\nb: [1, 2]   # flow\nlist:\n  - x\nc: 3\n---\nbody\n"
        new = frontmatter.updated(text, {"list": ["y", "z"], "d": "a: b"})
        self.assertIn("b: [1, 2]   # flow\n", new)
        self.assertIn("list:\n  - y\n  - z\nc: 3\n", new)
        self.assertEqual(frontmatter.parse(new)[0]["d"], "a: b")
        self.assertTrue(new.endswith("---\nbody\n"))

    def test_values_round_trip(self):
        values = {"s": "true", "n": 12, "e": None, "l": [], "u": [{"session": "a", "tokens": 5}],
                  "t": {"tokens": 5}, "w": "[[Plan|alias]]", "d": datetime.date(2026, 10, 26)}
        loaded = frontmatter.parse(frontmatter.compose(frontmatter.render(values), ""))[0]
        self.assertEqual(loaded, values)

    def test_refuses_what_yaml_cant_read(self):
        with self.assertRaises(frontmatter.FrontmatterError):
            frontmatter.parse("no frontmatter\n")
        with self.assertRaises(frontmatter.FrontmatterError):
            frontmatter.parse("---\na: b\n")

    def test_write_is_atomic(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        frontmatter.write_atomic(tmp / "a.md", "x")
        self.assertEqual(sorted(p.name for p in tmp.iterdir()), ["a.md"])


class TemplateTest(unittest.TestCase):
    def test_templates_hold_each_types_fields_in_order(self):
        for kind, fields in records.FIELDS.items():
            text = (records.TEMPLATES / f"{kind}.md").read_text(encoding="utf-8")
            fm, body = frontmatter.split(text)
            names = [line.split(":", 1)[0] for line in fm.splitlines() if line and not line.startswith(" ")]
            self.assertEqual(tuple(names), fields, kind)
            self.assertIn(f"type: {kind}\n", fm)
            self.assertTrue(body.rstrip().endswith("## Log"), kind)


# ---------------------------------------------------------------------------------------------------------------------


class FileTest(Vault):
    def setUp(self):
        super().setUp()
        self.plan()
        self.draft("00-pantry-shelves", type="epic", summary="Pantry Shelves")
        self.draft("01-add-shelf-model", summary="Add Shelf Model", depends_on=[])
        self.draft("02-show-shelves", summary="Show Shelves: List", depends_on=[1], owner="jordan")
        self.draft("03-later", summary="Later Thing", depends_on=[2], status="draft")
        (self.disc / "notes.md").write_text(
            f"See [[{DISCUSSION}/tickets/01-add-shelf-model|the model]] and [[02-show-shelves#Description]].\n")

    def test_dry_run_writes_nothing(self):
        before = sorted(p.relative_to(self.vault) for p in self.vault.rglob("*"))
        out = self.run_tasks("file", "2026-10-20-pantry-shelves", "--dry-run")
        self.assertEqual([f["key"] for f in out["filed"]], ["PT-1", "PT-2", "PT-3"])
        self.assertTrue(out["dry_run"])
        self.assertEqual(sorted(p.relative_to(self.vault) for p in self.vault.rglob("*")), before)

    def test_files_approved_drafts(self):
        out = self.run_tasks("file", DISCUSSION)
        self.assertEqual([(f["key"], f["type"]) for f in out["filed"]],
                         [("PT-1", "epic"), ("PT-2", "story"), ("PT-3", "story")])
        self.assertEqual([Path(s["draft"]).name for s in out["skipped"]], ["03-later.md"])
        self.assertEqual(sorted(p.name for p in (self.disc / "tickets").iterdir()), ["03-later.md"])

        story = self.fields(self.tasks / "PT-3" / "PT-3-show-shelves-list.md")
        self.assertEqual(story["key"], "PT-3")
        self.assertEqual(story["epic"], "PT-1")
        self.assertEqual(story["depends_on"], ["PT-2"])
        self.assertEqual(story["owner"], "jordan")
        self.assertEqual(story["status"], "todo")
        self.assertEqual(story["source_doc"], f"{DISCUSSION}/tickets/02-show-shelves.md")
        self.assertEqual(str(story["created"]), TODAY)
        self.assertNotIn("order", story)
        self.assertEqual(tuple(story), records.FIELDS["story"])
        self.assertIn("## Log", (self.tasks / "PT-3" / "PT-3-show-shelves-list.md").read_text())
        self.assertEqual(tuple(self.fields(self.tasks / "PT-1" / "PT-1-pantry-shelves.md")), records.FIELDS["epic"])

        notes = (self.disc / "notes.md").read_text()
        self.assertIn("[[Tasks/PT-2/PT-2-add-shelf-model|the model]]", notes)
        self.assertIn("[[PT-3-show-shelves-list#Description]]", notes)
        plan = (self.disc / "plan.md").read_text()
        self.assertEqual(self.fields(self.disc / "plan.md")["epic"], "PT-1")
        self.assertIn("[[PT-1-pantry-shelves]]", plan)
        self.assertIn("- [[Tasks/PT-2/PT-2-add-shelf-model|PT-2]] Add Shelf Model\n", plan)
        self.assertIn("Not filed yet: [[" + DISCUSSION + "/tickets/03-later|03-later]]", plan)
        self.assertNotIn("Dataview", plan)
        self.assertIn("## Open Questions", plan)
        self.assertEqual(self.store().records()[0].key, "PT-1")

    def test_a_later_run_turns_earlier_draft_numbers_into_keys(self):
        self.run_tasks("file", DISCUSSION)
        later = self.disc / "tickets" / "03-later.md"
        later.write_text(later.read_text().replace("status: draft", "status: approved"))
        out = self.run_tasks("file", DISCUSSION)
        self.assertEqual([(f["key"], f["depends_on"]) for f in out["filed"]], [("PT-4", ["PT-3"])])
        plan = (self.disc / "plan.md").read_text()
        self.assertIn("- [[Tasks/PT-4/PT-4-later-thing|PT-4]] Later Thing", plan)
        self.assertNotIn("Not filed yet", plan)

    def test_a_run_that_stopped_before_moving_is_resumed_with_the_same_key(self):
        draft = self.disc / "tickets" / "01-add-shelf-model.md"
        frontmatter.update(draft, {"key": "PT-7"})  # an earlier run wrote the key, then died
        out = self.run_tasks("file", DISCUSSION)
        resumed = [f for f in out["filed"] if f["resumed"]]
        self.assertEqual([(f["key"], f["summary"]) for f in resumed], [("PT-7", "Add Shelf Model")])
        self.assertEqual(self.fields(self.tasks / "PT-9" / "PT-9-show-shelves-list.md")["depends_on"], ["PT-7"])

    def test_reserved_key(self):
        epic = self.disc / "tickets" / "00-pantry-shelves.md"
        frontmatter.update(epic, {"reserved_key": "PT-40"})
        out = self.run_tasks("file", DISCUSSION)
        self.assertEqual([f["key"] for f in out["filed"]], ["PT-40", "PT-1", "PT-2"])
        self.assertEqual(self.fields(self.tasks / "PT-1" / "PT-1-add-shelf-model.md")["epic"], "PT-40")

    def test_a_dependency_on_an_unfiled_draft_fails_before_writing(self):
        self.draft("04-blocked", summary="Blocked Thing", depends_on=[3])
        out = self.run_tasks("file", DISCUSSION, ok=False)
        self.assertIn("depends on draft 03", out["error"])
        self.assertFalse(self.tasks.exists() and any(self.tasks.glob("*.md")))

    def test_context_on_an_unfiled_draft_says_to_file(self):
        out = self.run_tasks("context", f"{DISCUSSION}/tickets/01-add-shelf-model.md", ok=False)
        self.assertIn("tasks.py file 2026-10-20-pantry-shelves", out["error"])


class NextKeyTest(Vault):
    def test_starts_after_the_highest_key_given_out(self):
        self.record("PT-4")
        self.draft("00-epic", type="epic", summary="Epic", reserved_key="PT-6")
        out = self.run_tasks("next-key", "--count", "3")
        self.assertEqual(out["keys"], ["PT-5", "PT-7", "PT-8"])
        self.assertEqual(yaml.safe_load((self.tasks / records.COUNTER).read_text()), {"next": 9})

    def test_skips_keys_in_use(self):
        self.tasks.mkdir(parents=True)
        (self.tasks / records.COUNTER).write_text("next: 2\n")
        self.record("PT-3")
        self.draft("00-epic", type="epic", summary="Epic", reserved_key="PT-4")
        self.assertEqual(self.run_tasks("next-key", "--count", "2")["keys"], ["PT-2", "PT-5"])

    def test_dry_run_leaves_the_counter(self):
        self.assertEqual(self.run_tasks("next-key", "--dry-run")["keys"], ["PT-1"])
        self.assertEqual(self.run_tasks("next-key")["keys"], ["PT-1"])

    def test_concurrent_calls_never_share_a_key(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            outs = list(pool.map(lambda _: self.run_tasks("next-key", "--count", "3"), range(8)))
        keys = [key for out in outs for key in out["keys"]]
        self.assertEqual(len(keys), 24)
        self.assertEqual(sorted(keys, key=records.key_number), [f"PT-{n}" for n in range(1, 25)])


class VaultContextTest(Vault):
    def test_from_a_key_records_the_session(self):
        self.plan()
        path = self.record("PT-3", source_doc=f"{DISCUSSION}/tickets/01-x.md", sessions=[])
        out = self.run_tasks("context", "PT-3", "--session", "s1")
        self.assertEqual((out["doc"], out["work_id"], out["made"]), (str(path), "PT-3", False))
        self.assertTrue(out["new_session"])
        self.assertEqual(out["fields"]["sessions"], [])
        self.assertEqual(out["plan"], str(self.disc / "plan.md"))
        self.assertTrue(Path(out["publish_dir"]).is_dir())
        self.assertEqual(Path(out["publish_dir"]), self.tasks / ".publish" / "PT-3")
        self.assertEqual(self.fields(path)["sessions"], ["s1"])
        again = self.run_tasks("context", "PT-3", "--session", "s1")
        self.assertFalse(again["new_session"])
        self.assertEqual(self.fields(path)["sessions"], ["s1"])

    def test_from_a_record_path_inside_a_worktree(self):
        path = self.record("PT-3")
        worktree = self.root / ".worktrees" / "PT-3" / "src"
        worktree.mkdir(parents=True)
        out = self.run_tasks("context", str(path), cwd=worktree)
        self.assertEqual(out["work_id"], "PT-3")

    def test_from_a_plan_makes_a_keyed_task_once(self):
        self.plan(epic="PT-1")
        out = self.run_tasks("context", f"{DISCUSSION}/plan.md", "--session", "s1")
        self.assertTrue(out["made"])
        self.assertTrue(out["needs_requirement"])
        self.assertEqual(out["work_id"], "PT-1")
        fields = self.fields(Path(out["doc"]))
        self.assertEqual((fields["type"], fields["epic"], fields["summary"]), ("task", "PT-1", "Pantry Shelves"))
        self.assertEqual(fields["source"], f"[[{DISCUSSION}/plan|2026-10-20-pantry-shelves]]")
        resumed = self.run_tasks("context", str(self.disc / "plan.md"), "--session", "s2")
        self.assertEqual((resumed["doc"], resumed["made"]), (out["doc"], False))
        self.assertTrue(resumed["new_session"])
        self.assertEqual(len(list(self.tasks.glob("*/*.md"))), 1)
        self.assertEqual(self.run_tasks("work-id", f"{DISCUSSION}/plan.md")["work_id"], "PT-1")

    def test_plan_dry_run(self):
        self.plan()
        out = self.run_tasks("context", f"{DISCUSSION}/plan.md", "--dry-run", "--session", "s1")
        self.assertTrue(out["dry_run"] and out["made"] and out["new_session"])
        self.assertFalse(self.tasks.exists() and any(self.tasks.glob("*.md")))
        self.assertIsNone(self.run_tasks("work-id", f"{DISCUSSION}/plan.md")["work_id"])

    def test_unknown_key(self):
        self.assertIn("no task record", self.run_tasks("context", "PT-9", ok=False)["error"])


class NoneContextTest(Vault):
    tracker = "none"

    def test_from_a_draft_copies_it_and_resumes_the_copy(self):
        self.plan()
        draft = self.draft("01-add-shelf-model", summary="Add Shelf Model", status="draft")
        self.assertEqual(self.run_tasks("work-id", str(draft))["work_id"], "add-shelf-model")
        out = self.run_tasks("context", str(draft), "--session", "s1")
        self.assertEqual((out["work_id"], out["key"], out["made"]), ("add-shelf-model", None, True))
        self.assertFalse(out["needs_requirement"])
        self.assertTrue(draft.exists())
        record = self.tasks / "add-shelf-model.md"
        fields = self.fields(record)
        self.assertEqual((fields["status"], fields["source_doc"]), ("todo", f"{DISCUSSION}/tickets/01-add-shelf-model.md"))
        self.assertEqual(fields["sessions"], ["s1"])
        for source in (str(draft), "add-shelf-model", str(record)):
            again = self.run_tasks("context", source)
            self.assertEqual((again["doc"], again["made"]), (str(record), False), source)
        self.assertEqual(len(list(self.tasks.glob("*.md"))), 1)

    def test_a_renumbered_draft_finds_its_copy_by_summary(self):
        draft = self.draft("01-add-shelf-model", summary="Add Shelf Model")
        first = self.run_tasks("context", str(draft))
        moved = draft.with_name("02-add-shelf-model.md")
        draft.rename(moved)
        self.assertEqual(self.run_tasks("context", str(moved))["doc"], first["doc"])

    def test_from_a_plan(self):
        self.plan()
        out = self.run_tasks("context", f"{DISCUSSION}/plan.md")
        self.assertEqual((out["work_id"], out["needs_requirement"], out["requirement"]),
                         ("pantry-shelves", True, "source_doc"))
        self.assertEqual(self.fields(Path(out["doc"]))["type"], "task")

    def test_keys_and_vault_commands_fail_with_the_reason(self):
        self.assertIn("tasks have no keys", self.run_tasks("context", "PT-1", ok=False)["error"])
        for args in (["next-key"], ["queue"], ["check"], ["epic-status"], ["file", DISCUSSION],
                     ["create-defect", "--summary", "Broken"], ["import", "PT-1", "--issue", "x.json"]):
            out = self.run_tasks(*args, ok=False)
            self.assertIn(f"{args[0]}: tracker is none", out["error"])


# ---------------------------------------------------------------------------------------------------------------------


class SliceTest(Vault):
    """Limited-scope runs of one ticket: slices in the ticket's key folder, each with its own work id."""

    def ticket(self, **fields) -> Path:
        fields = {"type": "story", "key": "PT-1", "summary": "Shelf Labels", "status": "todo", "owner": "claude",
                  "depends_on": ["PT-9"], "sessions": ["s0"], "prs": ["https://x/pull/1"], **fields}
        return self.write("Tasks/PT-1/PT-1-shelf-labels.md", fields, """
            # Shelf Labels

            ## Description

            Print labels for every shelf.

            ## Acceptance Criteria

            GIVEN a shelf WHEN printed THEN it has a label

            ## Log

            - 2026-10-01: an earlier run
            """)

    def test_limited_scope_makes_a_numbered_slice(self):
        ticket = self.ticket()
        out = self.run_tasks("context", "PT-1", "--limited-scope", "Label Model Only", "--session", "s1")
        doc = Path(out["doc"])
        self.assertEqual(doc, self.tasks / "PT-1" / "PT-1.1-label-model-only.md")
        self.assertEqual((out["work_id"], out["key"], out["slice"], out["made"]), ("PT-1.1", "PT-1", 1, True))
        self.assertEqual(out["made_from"], str(ticket))
        self.assertTrue(out["needs_scope"])
        self.assertFalse(out["needs_requirement"])
        self.assertEqual(out["publish_dir"], str(self.tasks / ".publish" / "PT-1.1"))
        self.assertEqual([r["work_id"] for r in out["other_runs"]], ["PT-1"])
        self.assertEqual(out["unfinished_runs"], [])
        fields = self.fields(doc)
        self.assertEqual(list(fields)[:3], ["type", "key", "slice"])
        self.assertEqual((fields["summary"], fields["status"], fields["slice"], fields["depends_on"]),
                         ("Label Model Only", "todo", 1, ["PT-9"]))
        self.assertEqual((fields["sessions"], fields["prs"]), (["s1"], []))
        body = doc.read_text()
        self.assertIn("# Label Model Only\n\nA limited-scope slice of [[Tasks/PT-1/PT-1-shelf-labels|PT-1]]", body)
        self.assertLess(body.index("## Scope"), body.index("## Description"))
        self.assertIn("Print labels for every shelf.", body)
        self.assertIn("GIVEN a shelf", body)
        self.assertNotIn("an earlier run", body)
        self.assertEqual(body.count("## Log"), 1)

    def test_the_bare_key_names_the_ticket_and_lists_its_slices(self):
        self.ticket()
        self.run_tasks("context", "PT-1", "--limited-scope", "Label Model Only")
        self.run_tasks("set", "PT-1.1", "status=in-progress", "stage=implementing")
        out = self.run_tasks("context", "PT-1", "--session", "s2")
        self.assertEqual((out["work_id"], out["slice"], out["needs_scope"]), ("PT-1", None, False))
        self.assertEqual(out["unfinished_runs"], ["PT-1.1"])
        self.assertEqual(out["other_runs"][0]["stage"], "implementing")
        self.assertEqual(self.run_tasks("work-id", "PT-1")["work_id"], "PT-1")
        self.assertEqual(self.run_tasks("get", "PT-1.1", "status")["fields"], {"status": "in-progress"})

    def test_resumes_a_slice_by_its_work_id(self):
        self.ticket()
        made = self.run_tasks("context", "PT-1", "--limited-scope", "Label Model Only", "--session", "s1")
        again = self.run_tasks("context", "PT-1.1", "--session", "s1")
        self.assertEqual((again["doc"], again["made"], again["new_session"]), (made["doc"], False, False))
        self.assertEqual(self.run_tasks("work-id", "PT-1.1")["work_id"], "PT-1.1")
        self.assertIn("no slice", self.run_tasks("context", "PT-1.7", ok=False)["error"])

    def test_slices_run_in_parallel(self):
        self.ticket()
        self.run_tasks("context", "PT-1", "--limited-scope", "Label Model Only")
        self.run_tasks("set", "PT-1.1", "status=in-progress", "stage=implementing")
        out = self.run_tasks("context", "PT-1", "--limited-scope", "Label Printing")
        self.assertEqual((out["work_id"], out["unfinished_runs"]), ("PT-1.2", ["PT-1.1"]))

    def test_a_done_slice_doesnt_hold_the_next(self):
        self.ticket()
        self.run_tasks("context", "PT-1", "--limited-scope", "Label Model Only")
        self.run_tasks("set", "PT-1.1", "status=done")
        self.assertEqual(self.run_tasks("context", "PT-1", "--limited-scope", "Label Printing")["work_id"], "PT-1.2")

    def test_only_a_ticket_named_by_its_key_is_sliced(self):
        self.ticket()
        self.run_tasks("context", "PT-1", "--limited-scope", "Label Model Only")
        self.assertIn("by its key", self.run_tasks("context", "PT-1.1", "--limited-scope", "x", ok=False)["error"])
        self.record("PT-2", type="epic", summary="Pantry")
        self.assertIn("epic", self.run_tasks("context", "PT-2", "--limited-scope", "x", ok=False)["error"])
        self.assertIn("title", self.run_tasks("context", "PT-1", "--limited-scope", " ", ok=False)["error"])

    def test_a_ticket_from_before_key_folders_slices_into_its_folder(self):
        self.record("PT-3", summary="Old Layout")
        out = self.run_tasks("context", "PT-3", "--limited-scope", "First Part")
        self.assertEqual(Path(out["doc"]), self.tasks / "PT-3" / "PT-3.1-first-part.md")
        self.assertEqual(self.run_tasks("work-id", "PT-3")["doc"], str(self.tasks / "PT-3-old-layout.md"))

    def test_slices_stay_out_of_the_queue_and_the_check(self):
        self.ticket(depends_on=[])
        self.run_tasks("context", "PT-1", "--limited-scope", "Label Model Only")
        queue = self.run_tasks("queue")
        self.assertEqual([i["work_id"] for i in queue["ready"]], ["PT-1"])
        self.assertEqual(self.run_tasks("check")["problems"], [])

    def test_git_takes_a_slice_id(self):
        import gitgh
        cfg = config.load_workspace(self.root)
        self.assertEqual(gitgh.check_work_id("PT-1.2"), "PT-1.2")
        self.assertEqual(gitgh.branch_for(cfg, "PT-1.2", "label-model"),
                         gitgh.branch_for(cfg, "PT-1", "slice-2-label-model"))
        self.assertEqual(gitgh.branch_for(cfg, "PT-1.2", "slice-2-label-model"),
                         gitgh.branch_for(cfg, "PT-1.2", "label-model"))
        self.assertNotEqual(gitgh.branch_for(cfg, "PT-1.2", "labels"), gitgh.branch_for(cfg, "PT-1.3", "labels"))
        self.assertTrue(any(p.match(gitgh.branch_for(cfg, "PT-1.2", "labels")) for p in gitgh.branch_patterns(cfg)))
        with self.assertRaises(gitgh.Failed):
            gitgh.check_work_id("PT-1.0")


class RecordCommandsTest(Vault):
    extra = {"work": {"stages": ["awaiting-release"]}}

    def test_set_validates_and_clears(self):
        path = self.record("PT-1")
        self.run_tasks("set", "PT-1", "status=in-progress", "stage=awaiting-release", "work_note=waits: on PT-2")
        fields = self.fields(path)
        self.assertEqual((fields["status"], fields["stage"], fields["work_note"]),
                         ("in-progress", "awaiting-release", "waits: on PT-2"))
        self.run_tasks("set", "PT-1", "stage=")
        self.assertIsNone(self.fields(path)["stage"])
        self.run_tasks("set", "PT-1", "owner", "jordan")
        self.assertEqual(self.fields(path)["owner"], "jordan")
        for pair, reason in (("status=blocked", "status"), ("owner=sam", "owner"), ("stage=shipping", "stage"),
                             ("key=PT-9", "not a field set takes")):
            self.assertIn(reason, self.run_tasks("set", "PT-1", pair, ok=False)["error"])
        dry = self.run_tasks("set", "PT-1", "status=done", "--dry-run")
        self.assertTrue(dry["dry_run"])
        self.assertEqual(self.fields(path)["status"], "in-progress")

    def test_lists(self):
        path = self.record("PT-1")
        self.assertEqual(self.run_tasks("append", "PT-1", "prs", "u1", "u2")["added"], ["u1", "u2"])
        self.assertEqual(self.run_tasks("append", "PT-1", "prs=u2", "u3")["added"], ["u3"])
        self.run_tasks("set-list", "PT-1", "worktrees", "/a", "/b")
        self.assertEqual(self.fields(path)["worktrees"], ["/a", "/b"])
        self.run_tasks("set-list", "PT-1", "worktrees")
        self.assertEqual(self.fields(path)["worktrees"], [])
        self.assertIn("not a key", self.run_tasks("append", "PT-1", "depends_on", "two", ok=False)["error"])
        self.assertIn("not a list field", self.run_tasks("append", "PT-1", "status", "x", ok=False)["error"])

    def test_get(self):
        self.record("PT-1", sessions=["s"])
        out = self.run_tasks("get", "PT-1", "sessions", "status")
        self.assertEqual(out["fields"], {"sessions": ["s"], "status": "todo"})
        self.assertEqual(out["publish_dir"], str(self.tasks / ".publish" / "PT-1"))

    def test_log(self):
        path = self.record("PT-1")
        path.write_text(path.read_text() + "- 2026-01-01: first\n\n## After\n\ntext\n")
        self.run_tasks("log", "PT-1", "Asked", "about", "shelves")
        self.assertIn("## Log\n- 2026-01-01: first\n- " + TODAY + ": Asked about shelves\n\n## After\n",
                      path.read_text())

    def test_create_defect(self):
        out = self.run_tasks("create-defect", "--summary", "Shelf Count Wrong", "--steps", "1. Open a shelf",
                             "--found-in", "PT-1", "--owner", "jordan")
        path = Path(out["doc"])
        self.assertEqual(path.name, "PT-1-shelf-count-wrong.md")
        fields = self.fields(path)
        self.assertEqual((fields["type"], fields["found_in"], fields["owner"], fields["status"]),
                         ("defect", "PT-1", "jordan", "todo"))
        self.assertIn("## Steps to Reproduce\n\n<!--", path.read_text())
        self.assertIn("1. Open a shelf\n\n## Expected\n\n## Actual", path.read_text())
        self.assertEqual(tuple(fields), records.FIELDS["defect"])


class QueueTest(Vault):
    extra = {"work": {"wip": {"claude": 2, "jordan": 2}}}

    def test_groups_limits_and_order(self):
        self.record("PT-1", type="epic")
        self.record("PT-2", status="done")
        self.record("PT-3", status="in-progress")
        self.record("PT-4")
        self.record("PT-5", depends_on=["PT-2"])
        self.record("PT-6", depends_on=["PT-3"])
        self.record("PT-7", depends_on=["PT-2"])
        self.record("PT-8", owner="jordan", status="review")
        self.tasks.joinpath(records.BACKLOG).write_text("# Backlog\n\n- PT-7\n- [[PT-6-task-pt-6]]\n")
        out = self.run_tasks("queue")
        keys = {group: [i["key"] for i in out[group]]
                for group in ("waiting_on_user", "resumable", "ready", "start", "blocked")}
        self.assertEqual(keys, {"waiting_on_user": ["PT-8"], "resumable": ["PT-3"], "ready": ["PT-7", "PT-4", "PT-5"],
                                "start": ["PT-7"], "blocked": ["PT-6"]})
        self.assertEqual(out["blocked"][0]["blockers"], [{"key": "PT-3", "status": "in-progress"}])
        self.assertEqual((out["load"], out["capacity"]), ({"claude": 1, "jordan": 1}, 1))

    def test_user_limit_holds_new_starts(self):
        self.record("PT-1", owner="jordan")
        self.record("PT-2", owner="jordan", status="in-progress")
        self.record("PT-3")
        self.record("PT-4", status="in-progress")
        out = self.run_tasks("queue")
        self.assertEqual(out["capacity"], 0)
        self.assertEqual([i["key"] for i in out["start"]], [])
        self.assertEqual([i["key"] for i in out["resumable"]], ["PT-4"])
        self.assertIn("nothing new starts", out["held"])

    def test_epic_status(self):
        self.record("PT-1", type="epic", summary="Shelves")
        self.record("PT-2", type="epic", summary="Labels")
        self.record("PT-3", epic="PT-1", status="done")
        self.record("PT-4", epic="PT-1", status="todo")
        self.record("PT-5", epic="PT-2", status="done")
        out = {e["key"]: e for e in self.run_tasks("epic-status")["epics"]}
        self.assertEqual((out["PT-1"]["status"], out["PT-1"]["done"], out["PT-1"]["total"]), ("in-progress", 1, 2))
        self.assertEqual(out["PT-2"]["status"], "done")
        self.assertEqual([e["key"] for e in self.run_tasks("epic-status", "PT-2")["epics"]], ["PT-2"])


class CheckTest(Vault):
    def test_clean(self):
        self.record("PT-1", type="epic")
        self.record("PT-2", epic="PT-1")
        self.assertEqual(self.run_tasks("check"), {"ok": True, "checked": 2, "problems": []})

    def test_problems(self):
        self.record("PT-1", depends_on=["PT-9"])
        self.record("PT-2", depends_on=["PT-3"])
        self.record("PT-3", depends_on=["PT-4"])
        self.record("PT-4", depends_on=["PT-2"])
        self.record("PT-5", status="done", depends_on=["PT-6"])
        self.record("PT-6", status="todo", owner="nobody", epic="PT-1")
        self.draft("01-half", summary="Half", key="PT-20")
        out = self.run_tasks("check", ok=False)
        kinds = sorted((p["kind"], Path(p["doc"]).name.split("-task")[0]) for p in out["problems"])
        self.assertEqual(kinds, sorted([
            ("unknown-key", "PT-1"), ("cycle", "PT-2"), ("done-depends-on-undone", "PT-5"),
            ("bad-value", "PT-6"), ("unknown-epic", "PT-6"), ("half-filed", "01-half.md"),
        ]))
        cycle = next(p for p in out["problems"] if p["kind"] == "cycle")
        self.assertIn("PT-2 -> PT-3 -> PT-4 -> PT-2", cycle["detail"])


class CycleAndExtraFieldsTest(Vault):
    extra = {"workspace": {"name": "pantry", "layout": "single", "areas": {"lib": {}, "app": {}}},
             "shared_libraries": {"names": ["lib"], "update": {"command": "make bump"}},
             "work": {"extra_fields": {"pending_release": "text", "risk": ["low", "high"]}}}

    def test_cycle(self):
        path = self.record("PT-1")
        self.run_tasks("set", "PT-1", "cycle=shared-library", "stage=implementing")
        self.assertEqual(self.fields(path)["cycle"], "shared-library")
        self.assertEqual(self.run_tasks("get", "PT-1", "cycle")["fields"], {"cycle": "shared-library"})
        self.run_tasks("set", "PT-1", "cycle=consumers")
        self.assertEqual(self.fields(path)["cycle"], "consumers")
        self.assertIn("cycle: 'both' is not one of shared-library, consumers",
                      self.run_tasks("set", "PT-1", "cycle=both", ok=False)["error"])
        self.run_tasks("set", "PT-1", "cycle=")
        self.assertIsNone(self.fields(path)["cycle"])

    def test_declared_extra_fields(self):
        path = self.record("PT-1")
        self.run_tasks("set", "PT-1", "pending_release=PT-9 lib", "risk=high")
        self.assertEqual((self.fields(path)["pending_release"], self.fields(path)["risk"]), ("PT-9 lib", "high"))
        out = self.run_tasks("get", "PT-1", "pending_release", "risk")
        self.assertEqual(out["fields"], {"pending_release": "PT-9 lib", "risk": "high"})
        self.run_tasks("set", "PT-1", "pending_release=")
        self.assertIsNone(self.fields(path)["pending_release"])
        self.assertEqual(self.run_tasks("get", "PT-1", "pending_release")["fields"], {"pending_release": None})

    def test_invalid_and_undeclared_extra_fields(self):
        path = self.record("PT-1")
        before = path.read_text()
        self.assertIn("risk: 'medium' is not one of low, high (config work.extra_fields)",
                      self.run_tasks("set", "PT-1", "risk=medium", ok=False)["error"])
        out = self.run_tasks("set", "PT-1", "status=review", "release_train=7", ok=False)
        self.assertIn("release_train: not a field set takes", out["error"])
        self.assertIn("work.extra_fields declares any others", out["error"])
        self.assertIn("release_train: not a task-record field",
                      self.run_tasks("get", "PT-1", "release_train", ok=False)["error"])
        self.assertEqual(path.read_text(), before)  # a refused set writes nothing, not even its valid pairs

    def test_record_fields_agree_with_config(self):
        built_in = {name for fields in records.FIELDS.values() for name in fields} | {"cycle", "slice"}
        self.assertEqual(built_in, set(config.RECORD_FIELDS))


class NoSharedLibrariesTest(Vault):
    def test_cycle_is_refused_and_extra_fields_undeclared(self):
        self.record("PT-1")
        self.assertIn("configures no shared libraries",
                      self.run_tasks("set", "PT-1", "cycle=shared-library", ok=False)["error"])
        self.assertIn("pending_release: not a field set takes",
                      self.run_tasks("set", "PT-1", "pending_release=x", ok=False)["error"])
        self.run_tasks("set", "PT-1", "cycle=")  # clearing is always fine, and adds no field
        self.assertNotIn("cycle", self.fields(self.tasks / "PT-1-task-pt-1.md"))  # a record from before key folders


class ConfigStagesTest(unittest.TestCase):
    def test_stage_names(self):
        raw = {"version": 1, "workspace": {"name": "w", "layout": "single"}, "user": {"handle": "h", "name": "N"},
               "vault": {"path": "v"}, "tracker": {"kind": "none"}, "work": {"stages": ["awaiting-release", "Bad"]}}
        self.assertEqual(config.validate(raw)[1], ["work.stages[1]: must be lowercase words joined by -, like "
                                                   "awaiting-release; got 'Bad'"])


if __name__ == "__main__":
    unittest.main()
