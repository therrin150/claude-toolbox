"""The vault-researcher agent: its file, its docs beside the toolbox runtime, its report contract, and that it installs
with the obsidian feature.

Run from the repo root:

    python3 -m unittest discover -s core/workspace/.claude/toolbox/tests
"""

from __future__ import annotations

import importlib.util
import re
import sys
import unittest
from pathlib import Path

import yaml

from toolbox_tree import TOOLBOX, features  # noqa: E402
WORKSPACE_FOLDER = TOOLBOX.parents[1]
CLAUDE = WORKSPACE_FOLDER / ".claude"
sys.path.insert(0, str(TOOLBOX / "lib"))

import frontmatter  # noqa: E402

DOCS = TOOLBOX / "agents" / "vault-researcher"
PLAN = "Discussions/2026/11/2026-11-04-toolbox-per-project-modules/plan"

RELATED = f"""found: 2 items
searched: installer, install.py, sync-toolbox in Discussions, Tasks
items:
  - [[{PLAN}|Toolbox Per-Project Modules]] - discussion, draft
    decisions: features are chosen per workspace in toolbox.local.yml (2026-11-04); a module system was rejected
    open: whether knowledge skills install by feature or always
  - [[Tasks/install-toolbox-files-as-copies-with-manifest|Install Toolbox Files as Copies With Manifest]] - task, todo
    decisions: none
    open: none
notes: the second item is blocked on the Phase 2 spike
"""

NONE = "found: none\nsearched: quantum cache, qubit in Discussions, Tasks\n"
BLOCKED = "found: blocked: the prompt gave no vault\nsearched: nothing\n"


def agent_fields(path: Path) -> dict:
    head, _ = frontmatter.split(path.read_text(), path)
    return yaml.safe_load(head)


def load_contract_lib():
    spec = importlib.util.spec_from_file_location("report_contract_under_test", TOOLBOX / "lib" / "report_contract.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VaultResearcherAgentTest(unittest.TestCase):
    def setUp(self):
        self.path = CLAUDE / "agents" / "vault-researcher.md"
        self.fields = agent_fields(self.path)
        self.body = self.path.read_text()

    def test_runs_on_haiku(self):
        self.assertEqual(self.fields["name"], "vault-researcher")
        self.assertEqual(self.fields["model"], "haiku")

    def test_is_read_only_and_cannot_spawn_an_agent(self):
        tools = {tool.strip() for tool in str(self.fields["tools"]).split(",")}
        self.assertEqual(tools, {"Read", "Grep", "Glob"})  # no Bash, Edit, Write or Agent

    def test_has_a_turn_limit_and_skips_the_claude_md_files(self):
        self.assertIs(self.fields["omitClaudeMd"], True)
        self.assertIsInstance(self.fields["maxTurns"], int)
        self.assertGreater(self.fields["maxTurns"], 0)

    def test_the_description_says_what_it_reports_and_when_not_to_use_it(self):
        description = self.fields["description"]
        for needle in ("wikilink", "status", "decisions and open questions", "read it yourself"):
            self.assertIn(needle, description)

    def test_never_list_forbids_edits_non_wikilinks_and_advice(self):
        text = " ".join(self.body.split())
        for needle in ("any other way than its vault wikilink", "never spawn another agent", "advise what the asker"):
            self.assertIn(needle, text)

    def test_the_input_names_the_topic_the_root_and_the_vault(self):
        for needle in ("**topic**", "**root**", "**vault**"):
            self.assertIn(needle, self.body)

    def test_its_docs_exist_beside_the_toolbox_runtime(self):
        for name in ("procedure.md", "report.md"):
            self.assertTrue((DOCS / name).is_file(), name)

    def test_docs_named_in_the_agent_file_exist_beside_it(self):
        for name in re.findall(r"`?([\w-]+\.md)`?", self.body):
            self.assertTrue((DOCS / name).is_file(), name)

    def test_the_procedure_covers_discussions_tasks_status_and_nothing_found(self):
        procedure = " ".join((DOCS / "procedure.md").read_text().split())
        for needle in ("plan.md", "frontmatter", "Design Decisions", "## Decided", "Open Questions",
                       "found: none", "Tasks/.publish", "Never give a bare path"):
            self.assertIn(needle, procedure)


class VaultResearcherReportContractTest(unittest.TestCase):
    def setUp(self):
        self.lib = load_contract_lib()

    def check(self, report: str) -> str | None:
        return self.lib.check_report("vault-researcher", report, TOOLBOX)

    def test_the_template_declares_a_contract(self):
        self.assertIsNotNone(self.lib.load("vault-researcher", TOOLBOX))

    def test_related_items_with_status_decisions_and_questions_go_through(self):
        self.assertIsNone(self.check(RELATED))

    def test_none_and_blocked_go_through(self):
        self.assertIsNone(self.check(NONE))
        self.assertIsNone(self.check(BLOCKED))

    def test_a_report_without_the_found_line_is_denied(self):
        self.assertIn("missing found", self.check(RELATED.replace("found: 2 items\n", "")))

    def test_a_report_without_the_searched_line_is_denied(self):
        reason = self.check(RELATED.replace("searched: installer, install.py, sync-toolbox in Discussions, Tasks\n", ""))
        self.assertIn("missing searched", reason)

    def test_an_item_named_by_a_path_is_denied(self):
        bad = RELATED + f"  - {PLAN}.md - discussion, draft\n"
        self.assertIn("item that isn't a wikilink", self.check(bad))

    def test_an_item_named_by_a_markdown_link_is_denied(self):
        bad = RELATED + f"  - [Per-Project Modules]({PLAN}.md) - discussion, draft\n"
        self.assertIn("item that isn't a wikilink", self.check(bad))

    def test_an_item_with_no_status_is_denied(self):
        bad = RELATED + f"  - [[{PLAN}|Toolbox Per-Project Modules]]\n"
        self.assertIn("item that isn't a wikilink", self.check(bad))

    def test_an_item_with_no_kind_is_denied(self):
        bad = RELATED + f"  - [[{PLAN}|Toolbox Per-Project Modules]] - draft\n"
        self.assertIn("item that isn't a wikilink", self.check(bad))

    def test_a_wikilink_without_a_title_goes_through(self):
        self.assertIsNone(self.check(RELATED + "  - [[Tasks/add-vault-researcher]] - task, in-progress\n"))

    def test_a_code_block_is_denied(self):
        self.assertIn("code block", self.check(RELATED + "```\nstatus: draft\n```\n"))

    def test_pasted_frontmatter_is_denied(self):
        self.assertIn("frontmatter lines", self.check(RELATED + "---\n"))
        self.assertIn("frontmatter lines", self.check(RELATED + "created: 2026-11-04\n"))

    def test_a_pasted_table_is_denied(self):
        self.assertIn("table rows", self.check(RELATED + "| Date | Decision |\n"))

    def test_an_item_line_that_runs_on_is_denied(self):
        bad = RELATED + f"  - [[{PLAN}|" + "x" * 300 + "]] - discussion, draft\n"
        self.assertIn("item line over 300 characters", self.check(bad))

    def test_a_decisions_line_that_pastes_a_plan_is_denied(self):
        bad = RELATED + "    decisions: " + "word " * 170 + "\n"
        self.assertIn("decisions or open line over 800 characters", self.check(bad))

    def test_more_than_30_items_are_denied(self):
        many = "found: 31 items\nsearched: x in Discussions\nitems:\n" + "".join(
            f"  - [[Tasks/t{n}|T{n}]] - task, todo\n    decisions: none\n    open: none\n" for n in range(31))
        self.assertIn("items: 31 lines, at most 30", self.check(many))


class VaultResearcherInstallTest(unittest.TestCase):
    def test_is_listed_under_the_obsidian_feature_only(self):
        found = features()
        listed = [feature for feature, entry in found.items()
                  if any("vault-researcher" in path for path in entry["paths"])]
        self.assertEqual(listed, ["obsidian"])

    def test_the_listed_path_is_the_agent_file(self):
        self.assertIn(".claude/agents/vault-researcher.md", features()["obsidian"]["paths"])
        self.assertTrue((WORKSPACE_FOLDER / ".claude/agents/vault-researcher.md").is_file())


if __name__ == "__main__":
    unittest.main()
