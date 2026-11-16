"""Tests for finished records going home: `tasks.py set status=done` and `tasks.py rehome` move a done record into its
discussion's `tickets/` folder (making the discussion when it has none), and everything still finds it there.

Run from the repo root:

    python3 -m unittest discover -s core/workspace/.claude/toolbox/tests
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_tasks import DISCUSSION, Vault  # noqa: E402  (puts lib/ on the path)


class RehomeTest(Vault):
    def setUp(self):
        super().setUp()
        self.plan()
        self.draft("01-add-shelf", summary="Add Shelf", status="draft")
        self.shelf = self.write("Tasks/add-shelf.md", {
            "type": "story", "key": None, "summary": "Add Shelf", "status": "in-progress", "owner": "claude",
            "depends_on": [], "source_doc": f"{DISCUSSION}/tickets/01-add-shelf.md", "worktrees": [], "sessions": [],
            "prs": []}, "# Add Shelf\n\n## Log\n")
        (self.disc / "tickets" / "01-add-shelf.md").unlink()  # filing moved the draft out
        self.write("Notes/see.md", {"type": "reference"}, "See [[Tasks/add-shelf|Add Shelf]] and [[add-shelf]].\n")

    def test_set_done_moves_the_record_into_its_discussion_with_its_draft_name(self):
        out = self.run_tasks("set", "add-shelf", "status=done", "stage=")
        home = self.disc / "tickets" / "01-add-shelf.md"
        self.assertEqual(out["doc"], str(home))
        self.assertTrue(home.is_file() and not self.shelf.exists())
        self.assertEqual(self.fields(home)["status"], "done")
        self.assertEqual((self.vault / "Notes/see.md").read_text().split("---\n")[-1].strip(),
                         f"See [[{DISCUSSION}/tickets/01-add-shelf|Add Shelf]] and [[01-add-shelf]].")

    def test_an_archived_record_is_still_found_and_still_a_record(self):
        self.run_tasks("set", "add-shelf", "status=done")
        self.assertEqual(self.run_tasks("get", "add-shelf", "status")["fields"], {"status": "done"})
        self.assertEqual(self.store().find("add-shelf").work_id, "add-shelf")
        self.assertEqual([r.work_id for r in self.store().records()], ["add-shelf"])
        self.assertEqual(self.store().drafts(self.disc), [])
        out = self.run_tasks("check")
        self.assertTrue(out["ok"], out)

    def test_a_dependent_still_sees_its_blocker_done(self):
        self.write("Tasks/show-shelf.md", {"type": "story", "key": None, "summary": "Show Shelf", "status": "todo",
                                            "owner": "claude", "depends_on": ["add-shelf"]}, "# x\n")
        self.run_tasks("set", "add-shelf", "status=done")
        queue = self.run_tasks("queue")
        self.assertEqual(self.run_tasks("check")["problems"], [])
        self.assertNotIn("add-shelf", str(queue.get("blocked")))

    def test_a_keyed_record_goes_home_and_its_key_folder_goes_away(self):
        path = self.write("Tasks/PT-4/PT-4-add-shelf.md", {
            "type": "story", "key": "PT-4", "summary": "Add Bin", "status": "review", "owner": "claude",
            "depends_on": [], "source_doc": f"{DISCUSSION}/tickets/02-add-bin.md"}, "# x\n")
        self.run_tasks("set", "PT-4", "status=done")
        self.assertTrue((self.disc / "tickets" / "02-add-bin.md").is_file())
        self.assertFalse(path.parent.exists())
        self.assertEqual(self.store().find("PT-4").work_id, "PT-4")
        self.assertTrue(self.run_tasks("check")["ok"])

    def test_a_record_without_a_discussion_gets_one(self):
        defect = self.write("Tasks/PT-9/PT-9-shelf-count-wrong.md", {
            "type": "defect", "key": "PT-9", "summary": "Shelf Count Wrong", "status": "done", "owner": "claude",
            "depends_on": [], "created": "2026-11-02", "source": None, "source_doc": None}, "# x\n")
        out = self.run_tasks("rehome", "PT-9")
        folder = self.vault / "Discussions/2026/11/2026-11-02-shelf-count-wrong"
        self.assertTrue(out["discussion_created"])
        self.assertEqual(out["doc"], str(folder / "tickets/PT-9-shelf-count-wrong.md"))
        self.assertTrue(not defect.exists() and (folder / "plan.md").is_file())
        plan = self.fields(folder / "plan.md")
        self.assertEqual((plan["type"], plan["title"], plan["slug"], plan["status"]),
                         ("plan", "Shelf Count Wrong", "shelf-count-wrong", "done"))
        moved = self.fields(folder / "tickets/PT-9-shelf-count-wrong.md")
        self.assertEqual(moved["source_doc"], "Discussions/2026/11/2026-11-02-shelf-count-wrong/plan.md")
        self.assertTrue(self.run_tasks("check")["ok"])

    def test_a_record_made_from_a_plan_goes_to_its_discussions_tickets_folder(self):
        task = self.write("Tasks/PT-1/PT-1-pantry-shelves.md", {
            "type": "task", "key": "PT-1", "summary": "Pantry Shelves", "status": "done", "owner": "claude",
            "depends_on": [], "source_doc": f"{DISCUSSION}/plan.md"}, "# x\n")
        out = self.run_tasks("rehome", "PT-1")
        self.assertEqual(out["doc"], str(self.disc / "tickets" / "PT-1-pantry-shelves.md"))
        self.assertFalse(task.exists())
        self.assertFalse(out["discussion_created"])

    def test_only_done_records_go_home_and_nothing_is_overwritten(self):
        self.assertIn("only a done record", self.run_tasks("rehome", "add-shelf", ok=False)["error"])
        (self.disc / "tickets" / "01-add-shelf.md").write_text("taken\n")
        self.shelf.write_text(self.shelf.read_text().replace("in-progress", "done"))
        self.assertIn("already exists", self.run_tasks("rehome", "add-shelf", ok=False)["error"])
        self.assertTrue(self.shelf.exists())

    def test_dry_run_moves_nothing_and_done_sweeps_every_done_record(self):
        self.shelf.write_text(self.shelf.read_text().replace("in-progress", "done"))
        dry = self.run_tasks("rehome", "--done", "--dry-run")
        self.assertEqual([r["doc"] for r in dry["rehomed"]], [str(self.disc / "tickets" / "01-add-shelf.md")])
        self.assertTrue(self.shelf.exists())
        self.run_tasks("rehome", "--done")
        self.assertFalse(self.shelf.exists())
        self.assertEqual(self.run_tasks("rehome", "--done")["rehomed"], [])


if __name__ == "__main__":
    unittest.main()
