"""Tests for `tasks.py file --keyless`: slug-named records filed from a discussion's drafts under any tracker, and
`check` and `queue` naming tasks by key or slug.

Run from the repo root:

    python3 -m unittest discover -s workspace/.claude/toolbox/tests
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_jira import Jira  # noqa: E402  (puts lib/ on the path)
from test_tasks import DISCUSSION, Vault  # noqa: E402

import frontmatter  # noqa: E402


class Drafts:
    """Three drafts: 01, a dependent 02 (also an owner other than claude) and an unapproved 03."""

    def drafts(self):
        self.plan()
        self.draft("01-add-shelf-model", summary="Add Shelf Model", depends_on=[])
        self.draft("02-show-shelves", summary="Show Shelves: List", depends_on=[1])
        self.draft("03-later", summary="Later Thing", depends_on=[2], status="draft")
        (self.disc / "notes.md").write_text(f"See [[{DISCUSSION}/tickets/01-add-shelf-model|the model]].\n")

    def keyless(self, *extra: str, ok: bool = True) -> dict:
        return self.run_tasks("file", DISCUSSION, "--keyless", *extra, ok=ok)


class KeylessFileTest(Drafts, Jira):
    def setUp(self):
        super().setUp()
        self.drafts()

    def test_files_approved_drafts_as_slug_named_records(self):
        out = self.keyless()
        self.assertEqual([(f["key"], f["work_id"], f["depends_on"]) for f in out["filed"]],
                         [(None, "add-shelf-model", []), (None, "show-shelves-list", ["add-shelf-model"])])
        first, second = self.tasks / "add-shelf-model.md", self.tasks / "show-shelves-list.md"
        self.assertEqual((self.fields(first)["key"], self.fields(first)["type"], self.fields(first)["status"]),
                         (None, "story", "todo"))
        self.assertEqual(self.fields(second)["depends_on"], ["add-shelf-model"])
        self.assertEqual(self.fields(second)["source_doc"], f"{DISCUSSION}/tickets/02-show-shelves.md")
        self.assertIn("## Log", second.read_text())
        self.assertEqual(sorted(p.name for p in (self.disc / "tickets").iterdir()), ["03-later.md"])

    def test_the_plan_links_to_both_records_and_other_links_are_repaired(self):
        self.keyless()
        plan = (self.disc / "plan.md").read_text()
        self.assertIn("- [[Tasks/add-shelf-model|Add Shelf Model]]\n", plan)
        self.assertIn("- [[Tasks/show-shelves-list|Show Shelves: List]]\n", plan)
        self.assertIn("Not filed yet: [[" + DISCUSSION + "/tickets/03-later|03-later]]", plan)
        self.assertNotIn("Dataview", plan)
        self.assertIn("[[Tasks/add-shelf-model|the model]]", (self.disc / "notes.md").read_text())

    def test_a_draft_not_approved_is_left_alone(self):
        later = self.disc / "tickets" / "03-later.md"
        before = later.read_text()
        out = self.keyless()
        self.assertEqual(later.read_text(), before)
        self.assertEqual([Path(s["draft"]).name for s in out["skipped"]], ["03-later.md"])
        self.assertFalse((self.tasks / "later-thing.md").exists())

    def test_dry_run_writes_nothing(self):
        before = sorted(p.relative_to(self.vault) for p in self.vault.rglob("*"))
        out = self.keyless("--dry-run")
        self.assertTrue(out["dry_run"])
        self.assertEqual(len(out["filed"]), 2)
        self.assertEqual(sorted(p.relative_to(self.vault) for p in self.vault.rglob("*")), before)

    def test_context_check_and_queue_work_by_slug(self):
        self.keyless()
        out = self.run_tasks("context", "show-shelves-list")
        self.assertEqual((out["work_id"], out["key"], out["made"], out["summary"]),
                         ("show-shelves-list", None, False, "Show Shelves: List"))
        check = self.run_tasks("check")
        self.assertEqual((check["ok"], check["checked"], check["problems"]), (True, 2, []))
        queue = self.run_tasks("queue")
        self.assertEqual([i["work_id"] for i in queue["ready"]], ["add-shelf-model"])
        self.assertEqual([(i["work_id"], i["blockers"]) for i in queue["blocked"]],
                         [("show-shelves-list", [{"key": "add-shelf-model", "status": "todo"}])])

    def test_a_done_dependency_unblocks(self):
        self.keyless()
        self.run_tasks("set", "add-shelf-model", "status=done")
        self.assertEqual([i["work_id"] for i in self.run_tasks("queue")["ready"]], ["show-shelves-list"])

    def test_a_later_run_turns_earlier_draft_numbers_into_slugs(self):
        self.keyless()
        later = self.disc / "tickets" / "03-later.md"
        later.write_text(later.read_text().replace("status: draft", "status: approved"))
        out = self.keyless()
        self.assertEqual([(f["work_id"], f["depends_on"]) for f in out["filed"]],
                         [("later-thing", ["show-shelves-list"])])
        plan = (self.disc / "plan.md").read_text()
        self.assertIn("- [[Tasks/add-shelf-model|Add Shelf Model]]", plan)
        self.assertIn("- [[Tasks/later-thing|Later Thing]]", plan)
        self.assertNotIn("Not filed yet", plan)

    def test_a_run_that_stopped_before_moving_is_finished(self):
        draft = self.disc / "tickets" / "01-add-shelf-model.md"
        frontmatter.update(draft, {"status": "todo"})  # an earlier run wrote the record contract, then died
        check = self.run_tasks("check", ok=False)
        self.assertEqual([p["kind"] for p in check["problems"]], ["half-filed"])
        out = self.keyless()
        self.assertEqual([(f["work_id"], f["resumed"]) for f in out["filed"]],
                         [("add-shelf-model", True), ("show-shelves-list", False)])
        self.assertTrue(self.run_tasks("check")["ok"])

    def test_an_epic_draft_is_refused_before_writing(self):
        self.draft("00-pantry-shelves", type="epic", summary="Pantry Shelves")
        out = self.keyless(ok=False)
        self.assertIn("needs a key", out["error"])
        self.assertFalse(self.tasks.exists() and any(self.tasks.glob("*.md")))

    def test_a_dependency_on_an_unfiled_draft_fails_before_writing(self):
        self.draft("04-blocked", summary="Blocked Thing", depends_on=[3])
        out = self.keyless(ok=False)
        self.assertIn("depends on draft 03", out["error"])
        self.assertFalse(self.tasks.exists() and any(self.tasks.glob("*.md")))

    def test_two_drafts_with_one_slug_are_refused(self):
        self.draft("04-again", summary="Add Shelf Model")
        self.assertIn("more than one draft", self.keyless(ok=False)["error"])

    def test_refuses_jira_arguments(self):
        out = self.keyless("--keys", "01=ACME-1", ok=False)
        self.assertIn("no keys", out["error"])

    def test_a_draft_holding_a_key_is_skipped_and_left_alone(self):
        keyed = self.draft("04-keyed", summary="Keyed Thing")
        frontmatter.update(keyed, {"key": "ACME-40"})
        out = self.keyless()
        self.assertEqual([(Path(s["draft"]).name, "ACME-40" in s["reason"]) for s in out["skipped"]],
                         [("03-later.md", False), ("04-keyed.md", True)])
        self.assertTrue(keyed.exists())

    def test_the_jira_plan_and_keys_still_work_without_the_flag(self):
        self.assertTrue(self.run_tasks("file", DISCUSSION)["plan_only"])


class JiraScopeTest(Drafts, Jira):
    """Under jira, queue and check see only keyless records, and a Jira key in depends_on is Jira's to check."""

    def setUp(self):
        super().setUp()
        self.drafts()
        self.keyless()

    def test_a_jira_key_dependency_is_not_unknown(self):
        self.run_tasks("append", "show-shelves-list", "depends_on", "ACME-7")
        self.assertTrue(self.run_tasks("check")["ok"])
        blocked = {i["work_id"]: i["blockers"] for i in self.run_tasks("queue")["blocked"]}
        self.assertIn({"key": "ACME-7", "status": None}, blocked["show-shelves-list"])

    def test_an_unknown_slug_is_a_problem(self):
        self.run_tasks("append", "show-shelves-list", "depends_on", "no-such-record")
        out = self.run_tasks("check", ok=False)
        self.assertEqual([(p["kind"], p["detail"]) for p in out["problems"]],
                         [("unknown-key", "depends on no-such-record, which has no task record")])

    def test_keyed_records_are_jiras(self):
        self.write("Tasks/ACME-9/ACME-9-imported.md", {"type": "story", "key": "ACME-9", "summary": "Imported",
                                                        "status": "in-progress", "owner": "claude",
                                                        "depends_on": ["ACME-1"]}, "# x\n\n## Log\n")
        self.assertEqual(self.run_tasks("check")["checked"], 2)
        queue = self.run_tasks("queue")
        self.assertEqual(queue["resumable"], [])
        self.assertEqual(queue["load"]["claude"], 0)

    def test_epic_status_stays_jiras(self):
        self.assertIn("Jira holds epic status", self.run_tasks("epic-status", ok=False)["error"])


class CyclesTest(Drafts, Vault):
    def test_a_cycle_of_slugs_is_reported_from_its_first(self):
        self.drafts()
        self.keyless()
        self.run_tasks("append", "add-shelf-model", "depends_on", "show-shelves-list")
        out = self.run_tasks("check", ok=False)
        cycle = next(p for p in out["problems"] if p["kind"] == "cycle")
        self.assertIn("add-shelf-model -> show-shelves-list -> add-shelf-model", cycle["detail"])


class VaultKeylessTest(Drafts, Vault):
    """The vault tracker files keyless on request, beside keyed records."""

    def setUp(self):
        super().setUp()
        self.drafts()

    def test_keyless_records_are_not_a_problem_and_order_by_draft_number(self):
        self.record("PT-1", summary="Keyed One")
        self.keyless()
        self.assertEqual(self.run_tasks("check")["problems"], [])
        self.assertEqual([i["work_id"] for i in self.run_tasks("queue")["ready"]], ["PT-1", "add-shelf-model"])

    def test_depends_on_takes_slugs_and_refuses_junk(self):
        self.keyless()
        self.assertEqual(self.run_tasks("append", "show-shelves-list", "depends_on", "PT-3")["added"], ["PT-3"])
        self.assertIn("neither a key nor", self.run_tasks("append", "show-shelves-list", "depends_on", "Not A Slug",
                                                           ok=False)["error"])


class NoneKeylessTest(Drafts, Vault):
    tracker = "none"

    def test_files_even_though_there_is_nothing_to_file_into(self):
        self.drafts()
        self.assertEqual(self.run_tasks("file", DISCUSSION, ok=False)["error"].split(";")[0],
                         "file: tracker is none")
        out = self.keyless()
        self.assertEqual([f["work_id"] for f in out["filed"]], ["add-shelf-model", "show-shelves-list"])
        self.assertEqual(self.run_tasks("context", "add-shelf-model")["work_id"], "add-shelf-model")


if __name__ == "__main__":
    unittest.main()
