"""The jira-creator agent: its template (renamed from jira-filer), its docs beside the toolbox runtime, its report
contract, and that it installs with the jira feature.

Run from the repo root:

    python3 -m unittest discover -s workspace/.claude/toolbox/tests
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
import unittest
from pathlib import Path

import yaml

TOOLBOX = Path(__file__).resolve().parents[1]
WORKSPACE_FOLDER = TOOLBOX.parents[1]
CLAUDE = WORKSPACE_FOLDER / ".claude"
sys.path.insert(0, str(TOOLBOX / "lib"))

import frontmatter  # noqa: E402

DOCS = TOOLBOX / "agents" / "jira-creator"
TEMPLATE = CLAUDE / "agents" / "jira-creator.md.tmpl"
JIRA = {"mcp_server": "atlassian-work", "site": "example.atlassian.net",
        "cloud_id": "00000000-0000-0000-0000-000000000000", "project": "ABC", "account": "jdoe@example.com"}

FILED = """Filed: 00=ABC-20 01=ABC-21 02=ABC-22
Not filed: 03 (the create failed: timeout)
Comments: 01 posted
Links: ABC-21 blocks ABC-22
Problems: none
"""
NONE_FILED = "Filed: none\nProblems: 02-bad.md: line 9: unsupported markdown: HTML\n"


def render(text: str) -> str:
    return re.sub(r"\{\{\s*tracker\.jira\.(\w+)\s*\}\}", lambda match: JIRA[match.group(1)], text)


def load_contract_lib():
    spec = importlib.util.spec_from_file_location("report_contract_under_test", TOOLBOX / "lib" / "report_contract.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class JiraCreatorAgentTest(unittest.TestCase):
    def setUp(self):
        self.text = render(TEMPLATE.read_text())
        head, _ = frontmatter.split(self.text, TEMPLATE)
        self.fields = yaml.safe_load(head)
        self.tools = [tool.strip() for tool in str(self.fields["tools"]).split(",")]

    def test_renders_with_no_placeholder_left(self):
        self.assertNotIn("{{", self.text)
        self.assertIn("Project `ABC` on `example.atlassian.net`", " ".join(self.text.split()))
        self.assertIn("00000000-0000-0000-0000-000000000000", self.text)

    def test_is_jira_creator_on_haiku_and_jira_filer_is_gone(self):
        self.assertEqual(self.fields["name"], "jira-creator")
        self.assertEqual(self.fields["model"], "haiku")
        self.assertFalse((CLAUDE / "agents" / "jira-filer.md.tmpl").exists())

    def test_has_read_and_bash_then_the_servers_jira_tools_and_nothing_that_spawns_or_edits_files(self):
        self.assertEqual(self.tools[:2], ["Read", "Bash"])
        for tool in self.tools[2:]:
            self.assertRegex(tool, r"^mcp__atlassian-work__\w+$")
        for needed in ("atlassianUserInfo", "getAccessibleAtlassianResources", "createJiraIssue", "editJiraIssue",
                       "addCommentToJiraIssue", "createIssueLink", "getIssueLinkTypes", "searchJiraIssuesUsingJql"):
            self.assertIn(f"mcp__atlassian-work__{needed}", self.tools)
        for tool in self.tools:
            self.assertNotIn(tool, {"Edit", "Write", "Agent"})

    def test_does_not_yet_have_the_worklog_tool(self):
        self.assertFalse([tool for tool in self.tools if "orklog" in tool])

    def test_has_a_turn_limit_and_skips_the_claude_md_files(self):
        self.assertIs(self.fields["omitClaudeMd"], True)
        self.assertGreater(self.fields["maxTurns"], 0)

    def test_the_description_says_the_script_converts_first(self):
        text = " ".join(self.fields["description"].split())
        for needle in ("script converts each draft's markdown first", "Returns the keys it created", "create-tickets"):
            self.assertIn(needle, text)

    def test_never_list_holds_the_converter_before_any_create_and_no_hand_written_content(self):
        text = " ".join(self.text.split())
        for needle in ("Create or edit an issue before the converter has run clean over every draft",
                       "send a description, AC or comment you wrote or changed yourself",
                       "never write markdown or ADF yourself", "file an epic's stories when its epic failed"):
            self.assertIn(needle, text)

    def test_docs_named_in_the_agent_file_exist_beside_it(self):
        for name in re.findall(r"`([\w-]+\.md)`", self.text):
            self.assertTrue((DOCS / name).is_file(), name)

    def test_the_procedure_runs_the_script_before_creating_and_stops_on_failure(self):
        procedure = " ".join((DOCS / "procedure.md").read_text().split())
        for needle in ("draft_to_jira.py", "--ac-field", "before creating anything", "Create nothing, from this draft or "
                       "any other", "`contentFormat: \"adf\"`", "`testing_notes_comment`", "Never change the converted content"):
            self.assertIn(needle, procedure)

    def test_the_procedure_leaves_room_for_worklogs_without_adding_them(self):
        section = (DOCS / "procedure.md").read_text().split("## 6. Worklogs", 1)[1]
        self.assertIn("addWorklogToJiraIssue", section)
        self.assertIn("Not yet", section)
        self.assertIn("not supported yet", section)

    def test_the_script_the_procedure_names_exists(self):
        self.assertTrue((TOOLBOX / "bin" / "draft_to_jira.py").is_file())


class JiraCreatorReportContractTest(unittest.TestCase):
    def setUp(self):
        self.lib = load_contract_lib()

    def check(self, report: str) -> str | None:
        return self.lib.check_report("jira-creator", report, TOOLBOX)

    def test_the_template_declares_a_contract(self):
        self.assertIsNotNone(self.lib.load("jira-creator", TOOLBOX))

    def test_a_filing_report_and_a_conversion_failure_go_through(self):
        self.assertIsNone(self.check(FILED))
        self.assertIsNone(self.check(NONE_FILED))

    def test_a_report_without_filed_or_problems_is_denied(self):
        self.assertIn("missing filed", self.check(FILED.replace("Filed: 00=ABC-20 01=ABC-21 02=ABC-22\n", "")))
        self.assertIn("missing problems", self.check("Filed: none\n"))

    def test_a_filed_line_that_isnt_draft_numbers_and_keys_is_denied(self):
        self.assertIn("missing filed", self.check(FILED.replace("00=ABC-20", "ABC-20")))

    def test_pasted_json_adf_urls_and_code_blocks_are_denied(self):
        self.assertIn("pasted JSON", self.check(FILED + '{"version": 1}\n'))
        self.assertIn("ADF", self.check(FILED + 'it sent "type": "doc" as given\n'))
        self.assertIn("API urls", self.check(FILED + "https://example.atlassian.net/rest/api/3/issue/1\n"))
        self.assertIn("code block", self.check(FILED + "```\nx\n```\n"))


class JiraCreatorInstallTest(unittest.TestCase):
    def setUp(self):
        self.features = yaml.safe_load((WORKSPACE_FOLDER / "features.yml").read_text())

    def test_is_listed_under_the_jira_feature_as_the_template(self):
        self.assertIn(".claude/agents/jira-creator.md.tmpl", self.features["jira"]["paths"])
        self.assertIn("tracker.jira", self.features["jira"]["needs"])
        self.assertNotIn(".claude/agents/jira-filer.md.tmpl", self.features["jira"]["paths"])

    def test_no_tracked_file_still_calls_it_jira_filer(self):
        repo = WORKSPACE_FOLDER.parent
        tracked = subprocess.run(["git", "-C", str(repo), "ls-files"], capture_output=True, text=True)
        if tracked.returncode != 0:
            self.skipTest("not a git checkout")
        for name in tracked.stdout.splitlines():
            path = repo / name
            if path.is_file() and path != Path(__file__):
                self.assertNotIn("jira-filer", path.read_text(errors="ignore"), name)


if __name__ == "__main__":
    unittest.main()
