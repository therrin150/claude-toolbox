"""Tests for the jira tracker (lib/trackers/jira.py) through bin/tasks.py: import, file and write-back, against saved,
anonymized MCP responses in fixtures/jira/ for a test site whose field ids no real site uses.

Run from the repo root:

    python3 -m unittest discover -s workspace/.claude/toolbox/tests
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_tasks import DISCUSSION, TODAY, Vault  # noqa: E402  (puts lib/ on the path)

import config  # noqa: E402
import frontmatter  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "jira"
CLOUD = "00000000-test-4000-8000-000000000001"
AC_FIELD = "customfield_54321"
SPRINT_FIELD = "customfield_54322"


class Jira(Vault):
    tracker = "jira"
    jira: dict = {}

    def setUp(self):
        super().setUp()
        path = self.root / config.CONFIG_PATH
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        raw["tracker"]["jira"] = {
            "mcp_server": "tracker-test",
            "site": "acme-test.atlassian.net",
            "cloud_id": CLOUD,
            "project": "ACME",
            "account": "tester@example.com",
            "fields": {"acceptance_criteria": AC_FIELD, "sprint": SPRINT_FIELD},
            "issue_types": {"story": "User Story", "defect": "Defect"},
            "labels": ["acme-backlog"],
            **self.jira,
        }
        path.write_text(yaml.safe_dump(raw), encoding="utf-8")

    def fixture(self, name: str, change=None) -> str:
        """A fixture's path, or a changed copy of it in the scratch folder."""
        source = FIXTURES / name
        if not change:
            return str(source)
        data = json.loads(source.read_text(encoding="utf-8"))
        change(data)
        copy = self.tmp / f"changed-{name}"
        copy.write_text(json.dumps(data), encoding="utf-8")
        return str(copy)


def story_fields(data: dict) -> dict:
    return data["issues"]["nodes"][0]["fields"]


# ---------------------------------------------------------------------------------------------------------------------


class ImportTest(Jira):
    def test_context_on_an_unknown_key_says_to_import(self):
        out = self.run_tasks("context", "ACME-12", ok=False)
        self.assertEqual(out["needs_import"], "ACME-12")
        self.assertIn("tasks.py import ACME-12 --issue", out["error"])
        self.assertEqual(self.run_tasks("work-id", "ACME-12")["work_id"], "ACME-12")
        self.assertIn("not a ACME key", self.run_tasks("context", "PT-1", ok=False)["error"])

    def test_imports_a_story(self):
        out = self.run_tasks("import", "ACME-12", "--issue", self.fixture("issue-story.json"))
        doc = self.tasks / "ACME-12" / "ACME-12-show-pantry-shelf-counts.md"
        self.assertEqual(out["doc"], str(doc))
        self.assertEqual(out["url"], "https://acme-test.atlassian.net/browse/ACME-12")
        fields = self.fields(doc)
        self.assertEqual({k: fields[k] for k in ("type", "key", "epic", "status", "owner", "depends_on")},
                         {"type": "story", "key": "ACME-12", "epic": "ACME-10", "status": "todo", "owner": "claude",
                          "depends_on": ["ACME-11"]})
        self.assertEqual(str(fields["created"]), TODAY)
        text = doc.read_text()
        self.assertIn("Jira: [ACME-12](https://acme-test.atlassian.net/browse/ACME-12)", text)
        self.assertIn("## Description\n\nHouseholds can't see how full a shelf is.", text)
        self.assertIn("## Acceptance Criteria\n\n**Scenario: Shelf with Items**\n\nGIVEN a shelf holding `3` items\n"
                      "WHEN the shelves are listed\nTHEN its `count` is 3\n", text)
        self.assertTrue(text.rstrip().endswith("## Log"))

        context = self.run_tasks("context", "ACME-12", "--session", "s1")
        self.assertEqual((context["requirement"], context["made"], context["needs_requirement"]),
                         ("jira", False, False))
        again = self.run_tasks("import", "ACME-12", "--issue", self.fixture("issue-story.json"), ok=False)
        self.assertIn("already has a task record", again["error"])

    def test_imports_the_trimmed_shape_work_task_writes(self):
        # work-task's Setup saves only what import reads, with markdown text, rather than the whole response.
        trimmed = {"context": {"cloudId": CLOUD}, "key": "ACME-12",
                   "webUrl": "https://acme-test.atlassian.net/browse/ACME-12", "fields": {
                       "summary": "Show Pantry Shelf Counts", "issuetype": {"name": "User Story"},
                       "status": {"statusCategory": {"key": "new"}},
                       "parent": {"key": "ACME-10", "fields": {"issuetype": {"name": "Epic"}}},
                       "issuelinks": [{"type": {"name": "Blocks"}, "inwardIssue": {"key": "ACME-11"}}],
                       "description": "Households can't see how full a shelf is.",
                       AC_FIELD: "GIVEN a shelf holding `3` items\nWHEN the shelves are listed\nTHEN its `count` is 3"}}
        path = self.tmp / "ACME-12-issue.json"
        path.write_text(json.dumps(trimmed), encoding="utf-8")
        self.run_tasks("import", "ACME-12", "--issue", str(path))
        doc = self.tasks / "ACME-12" / "ACME-12-show-pantry-shelf-counts.md"
        fields = self.fields(doc)
        self.assertEqual((fields["type"], fields["epic"], fields["depends_on"]), ("story", "ACME-10", ["ACME-11"]))
        self.assertIn("## Acceptance Criteria\n\nGIVEN a shelf holding `3` items\n", doc.read_text())

    def test_dry_run_writes_nothing(self):
        out = self.run_tasks("import", "ACME-12", "--issue", self.fixture("issue-story.json"), "--dry-run")
        self.assertTrue(out["dry_run"])
        self.assertFalse(self.tasks.exists() and any(self.tasks.glob("*.md")))

    def test_a_site_with_no_ac_field_splits_the_description(self):
        def ac_in_description(data):
            fields = story_fields(data)
            fields["description"] += "\n\n## Acceptance Criteria\n\n**Scenario: Empty Shelf**\nGIVEN x\nTHEN y"
            del fields[AC_FIELD]
        self.rewrite_jira(fields={})
        self.run_tasks("import", "ACME-12", "--issue", self.fixture("issue-story.json", ac_in_description))
        text = (self.tasks / "ACME-12" / "ACME-12-show-pantry-shelf-counts.md").read_text()
        self.assertIn("on `GET /shelves`.\n\n## Acceptance Criteria\n\n**Scenario: Empty Shelf**\nGIVEN x\nTHEN y\n",
                      text)
        self.assertEqual(text.count("Acceptance Criteria"), 1)

    def test_types_and_done_status_from_jira(self):
        def done_defect(data):
            fields = story_fields(data)
            fields["issuetype"] = {"name": "Defect"}
            fields["status"]["statusCategory"]["key"] = "done"
            fields["parent"] = {"key": "ACME-5", "fields": {"issuetype": {"name": "User Story"}}}
        self.run_tasks("import", "ACME-12", "--issue", self.fixture("issue-story.json", done_defect))
        fields = self.fields(self.tasks / "ACME-12" / "ACME-12-show-pantry-shelf-counts.md")
        self.assertEqual((fields["type"], fields["status"], fields["epic"]), ("defect", "done", None))

    def test_refuses_another_issue_or_site(self):
        wrong_key = self.run_tasks("import", "ACME-99", "--issue", self.fixture("issue-story.json"), ok=False)
        self.assertIn("holds no issue ACME-99", wrong_key["error"])

        def other_site(data):
            data["context"]["cloudId"] = "11111111-test-4000-8000-000000000002"
        other = self.run_tasks("import", "ACME-12", "--issue", self.fixture("issue-story.json", other_site), ok=False)
        self.assertIn("not this workspace's", other["error"])
        self.assertFalse(self.tasks.exists() and any(self.tasks.glob("*.md")))

    def rewrite_jira(self, **values):
        path = self.root / config.CONFIG_PATH
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        raw["tracker"]["jira"].update(values)
        path.write_text(yaml.safe_dump(raw), encoding="utf-8")


class FileTest(Jira):
    jira = {"estimates": True}

    def setUp(self):
        super().setUp()
        self.plan()
        self.draft("00-pantry-shelves", type="epic", summary="Pantry Shelves")
        self.draft("01-add-shelf-model", summary="Add Shelf Model", estimate="1d", labels=["storage"])
        self.draft("02-show-shelves", summary="Show Shelves: List", depends_on=[1], sprint="Sprint 4")
        self.draft("03-later", summary="Later Thing", depends_on=[2], status="draft")
        self.keys = ["00=ACME-20", "01=ACME-21", "2=ACME-22"]

    def test_plan_lists_what_to_file(self):
        before = sorted(p.relative_to(self.vault) for p in self.vault.rglob("*"))
        out = self.run_tasks("file", DISCUSSION)
        self.assertTrue(out["plan_only"])
        rows = [(r["number"], r["issue_type"], r["epic"], r["depends_on"]) for r in out["to_file"]]
        self.assertEqual(rows, [("00", "Epic", None, []), ("01", "User Story", "the epic draft's key", []),
                                ("02", "User Story", "the epic draft's key", [1])])
        first = out["to_file"][1]
        self.assertEqual((first["estimate"], first["labels"]), ("1d", ["acme-backlog", "storage"]))
        self.assertEqual(out["to_file"][2]["sprint"], "Sprint 4")
        self.assertEqual(out["fields"], {"acceptance_criteria": AC_FIELD, "sprint": SPRINT_FIELD})
        self.assertEqual([Path(s["draft"]).name for s in out["skipped"]], ["03-later.md"])
        self.assertEqual(sorted(p.relative_to(self.vault) for p in self.vault.rglob("*")), before)

    def test_records_and_moves_the_keys(self):
        out = self.run_tasks("file", DISCUSSION, "--keys", *self.keys, "--issues", self.fixture("search-filed.json"))
        self.assertEqual([(f["key"], f["type"]) for f in out["filed"]],
                         [("ACME-20", "epic"), ("ACME-21", "story"), ("ACME-22", "story")])
        self.assertEqual(out["verified"], ["ACME-20", "ACME-21", "ACME-22"])
        self.assertEqual(out["filed"][1]["url"], "https://acme-test.atlassian.net/browse/ACME-21")
        self.assertEqual(out["warnings"], ["ACME-22: no Blocks link from ACME-21, which it depends on",
                                           "ACME-22: missing the label acme-backlog"])
        story = self.fields(self.tasks / "ACME-22" / "ACME-22-show-shelves-list.md")
        self.assertEqual((story["key"], story["epic"], story["depends_on"]), ("ACME-22", "ACME-20", ["ACME-21"]))
        self.assertNotIn("estimate", story)
        self.assertEqual(self.fields(self.disc / "plan.md")["epic"], "ACME-20")
        self.assertIn("- [[Tasks/ACME-21/ACME-21-add-shelf-model|ACME-21]] Add Shelf Model\n",
                      (self.disc / "plan.md").read_text())
        self.assertEqual(sorted(p.name for p in (self.disc / "tickets").iterdir()), ["03-later.md"])

        # Running it again finds nothing new to move, and says the numbers are filed.
        later = self.disc / "tickets" / "03-later.md"
        later.write_text(later.read_text().replace("status: draft", "status: approved"))
        plan = self.run_tasks("file", DISCUSSION)
        self.assertEqual([(r["number"], r["depends_on"]) for r in plan["to_file"]], [("03", ["ACME-22"])])
        self.assertEqual([a["key"] for a in plan["already_filed"]], ["ACME-20", "ACME-21", "ACME-22"])
        again = self.run_tasks("file", DISCUSSION, "--keys", "01=ACME-21", "--issues",
                               self.fixture("search-filed.json"))
        self.assertEqual((again["filed"], again["already_filed"]), ([], [{"number": "01", "key": "ACME-21"}]))
        self.assertEqual([Path(s["draft"]).name for s in again["skipped"]], ["03-later.md"])

    def test_the_epic_first_then_its_stories(self):
        self.run_tasks("file", DISCUSSION, "--keys", "00=ACME-20", "--issues", self.fixture("search-filed.json"))
        self.assertEqual(self.fields(self.disc / "plan.md")["epic"], "ACME-20")
        plan = self.run_tasks("file", DISCUSSION)
        self.assertEqual([r["epic"] for r in plan["to_file"]], ["ACME-20", "ACME-20"])
        out = self.run_tasks("file", DISCUSSION, "--keys", "01=ACME-21", "02=ACME-22", "--issues",
                             self.fixture("search-filed.json"))
        self.assertEqual([f["epic"] for f in out["filed"]], ["ACME-20", "ACME-20"])

    def test_a_mismatch_writes_nothing(self):
        before = {p: p.read_text() for p in self.vault.rglob("*.md")}

        def swapped(data):
            nodes = data["issues"]["nodes"]
            nodes[1]["fields"]["summary"], nodes[2]["fields"]["summary"] = (nodes[2]["fields"]["summary"],
                                                                            nodes[1]["fields"]["summary"])
            nodes[2]["fields"]["parent"] = {"key": "ACME-1"}
        out = self.run_tasks("file", DISCUSSION, "--keys", *self.keys, "--issues",
                             self.fixture("search-filed.json", swapped), ok=False)
        self.assertIn("nothing written", out["error"])
        self.assertIn("ACME-21: Jira's summary is 'Show Shelves: List'", out["error"])
        self.assertIn("ACME-22: its parent is ACME-1, not the epic ACME-20", out["error"])
        self.assertEqual({p: p.read_text() for p in self.vault.rglob("*.md")}, before)

    def test_refusals(self):
        issues = self.fixture("search-filed.json")
        cases = [
            (["file", DISCUSSION, "--keys", *self.keys], "--keys needs --issues"),
            (["file", DISCUSSION, "--issues", issues], "--issues needs --keys"),
            (["file", DISCUSSION, "--keys", "01=PT-3", "--issues", issues], "not a ACME key"),
            (["file", DISCUSSION, "--keys", "01=ACME-21", "02=ACME-21", "--issues", issues], "more than one draft"),
            (["file", DISCUSSION, "--keys", "03=ACME-23", "--issues", issues], "only approved drafts are filed"),
            (["file", DISCUSSION, "--keys", "07=ACME-27", "--issues", issues], "no draft 07"),
            (["file", DISCUSSION, "--keys", "01=ACME-30", "--issues", issues], "ACME-30: not in"),
        ]
        for args, message in cases:
            with self.subTest(message):
                self.assertIn(message, self.run_tasks(*args, ok=False)["error"])

        frontmatter.update(self.disc / "tickets" / "01-add-shelf-model.md", {"key": "ACME-40"})
        out = self.run_tasks("file", DISCUSSION, "--keys", "01=ACME-21", "--issues", issues, ok=False)
        self.assertIn("already holds ACME-40, not ACME-21; never file a draft twice", out["error"])
        self.assertIn("holds the key but was never moved", self.run_tasks("context", "ACME-40", ok=False)["error"])

        def other_site(data):
            data["context"]["cloudId"] = "11111111-test-4000-8000-000000000002"
        frontmatter.update(self.disc / "tickets" / "01-add-shelf-model.md", {"key": None})
        out = self.run_tasks("file", DISCUSSION, "--keys", *self.keys, "--issues",
                             self.fixture("search-filed.json", other_site), ok=False)
        self.assertIn("not this workspace's", out["error"])

    def test_vault_commands_say_why_not(self):
        self.assertIn("Jira assigns keys", self.run_tasks("next-key", ok=False)["error"])
        self.assertIn("create-tickets", self.run_tasks("create-defect", "--summary", "X", ok=False)["error"])
        self.assertIn("Jira holds the queue", self.run_tasks("queue", ok=False)["error"])

    def test_a_plan_makes_a_keyless_task(self):
        out = self.run_tasks("context", f"{DISCUSSION}/plan.md")
        self.assertEqual((out["work_id"], out["key"], out["made"]), ("pantry-shelves", None, True))
        self.assertIn("create-tickets", self.run_tasks("context", f"{DISCUSSION}/tickets/01-add-shelf-model.md",
                                                       ok=False)["error"])


class VaultFileKeysTest(Vault):
    def test_vault_refuses_keys(self):
        self.plan()
        self.draft("01-add-shelf-model", summary="Add Shelf Model")
        out = self.run_tasks("file", DISCUSSION, "--keys", "01=PT-1", ok=False)
        self.assertIn("for the jira tracker", out["error"])


class WriteBackTest(Jira):
    jira = {"write_back": "transitions", "statuses": {"review": "Code Review"}}

    def test_transitions(self):
        self.run_tasks("import", "ACME-12", "--issue", self.fixture("issue-story.json"))
        out = self.run_tasks("set", "ACME-12", "status=in-progress", "stage=implementing")
        self.assertEqual(out["write_back"], {
            "tool": "mcp__tracker-test__transitionJiraIssue", "cloud_id": CLOUD, "key": "ACME-12",
            "status": "in-progress", "to": "In Progress", "how": "references/jira-write-back.md"})
        self.assertEqual(self.run_tasks("set", "ACME-12", "status=review")["write_back"]["to"], "Code Review")
        self.assertIsNone(self.run_tasks("set", "ACME-12", "owner=jordan")["write_back"])
        self.assertIsNone(self.run_tasks("set", "ACME-12", "status=stopped", "work_note=waiting")["write_back"])

    def test_none_writes_nothing_back(self):
        path = self.root / config.CONFIG_PATH
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        raw["tracker"]["jira"]["write_back"] = "none"
        path.write_text(yaml.safe_dump(raw), encoding="utf-8")
        self.run_tasks("import", "ACME-12", "--issue", self.fixture("issue-story.json"))
        self.assertIsNone(self.run_tasks("set", "ACME-12", "status=done")["write_back"])


class ConfigTest(Jira):
    def test_unknown_type_or_status_names(self):
        path = self.root / config.CONFIG_PATH
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        raw["tracker"]["jira"]["issue_types"]["spike"] = "Spike"
        raw["tracker"]["jira"]["statuses"] = {"blocked": "Blocked"}
        path.write_text(yaml.safe_dump(raw), encoding="utf-8")
        with self.assertRaises(config.ConfigError) as caught:
            config.load_workspace(self.root)
        self.assertEqual(sorted(caught.exception.errors), [
            "tracker.jira.issue_types.spike: unknown; use story, epic, defect",
            "tracker.jira.statuses.blocked: unknown; use todo, in-progress, review, done, stopped"])


if __name__ == "__main__":
    unittest.main()
