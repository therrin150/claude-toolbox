"""The jira-reader agent: its template, its docs beside the toolbox runtime, its report contract, and that it installs
with the jira feature.

Run from the repo root:

    python3 -m unittest discover -s workspace/.claude/toolbox/tests
"""

from __future__ import annotations

import importlib.util
import re
import sys
import unittest
from pathlib import Path

import yaml

TOOLBOX = Path(__file__).resolve().parents[1]
WORKSPACE_FOLDER = TOOLBOX.parents[1]
CLAUDE = WORKSPACE_FOLDER / ".claude"
sys.path.insert(0, str(TOOLBOX / "lib"))

import frontmatter  # noqa: E402

DOCS = TOOLBOX / "agents" / "jira-reader"
TEMPLATE = CLAUDE / "agents" / "jira-reader.md.tmpl"
JIRA = {"mcp_server": "atlassian-work", "site": "example.atlassian.net",
        "cloud_id": "00000000-0000-0000-0000-000000000000", "project": "ABC", "account": "jdoe@example.com"}

ISSUES = """found: 3 issues
fields: status, assignee, summary
issues:
  - ABC-1 | In Progress | Allen Sudweeks | Add the thing
  - ABC-2 | To Do | unassigned | Fix the other thing
  - ABC-3 | Done | Allen Sudweeks | Remove the old thing
"""

SEARCH = """found: 2 issues
fields: summary
query: project = ABC AND sprint in openSprints() AND statusCategory != Done AND type = Story
issues:
  - ABC-10 | Add jira-reader
  - ABC-11 | Add pr-reporter
notes: 4 more issues match; narrow the JQL
"""

NONE = "found: none\nfields: summary\nquery: project = ABC AND labels = nothing\n"
BLOCKED = "found: blocked: the prompt gave neither issues nor a JQL\n"
MISSING = ISSUES + "missing: ABC-99: not found\n"


def render(text: str) -> str:
    return re.sub(r"\{\{\s*tracker\.jira\.(\w+)\s*\}\}", lambda match: JIRA[match.group(1)], text)


def load_contract_lib():
    spec = importlib.util.spec_from_file_location("report_contract_under_test", TOOLBOX / "lib" / "report_contract.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class JiraReaderAgentTest(unittest.TestCase):
    def setUp(self):
        self.text = render(TEMPLATE.read_text())
        head, _ = frontmatter.split(self.text, TEMPLATE)
        self.fields = yaml.safe_load(head)
        self.tools = [tool.strip() for tool in str(self.fields["tools"]).split(",")]

    def test_renders_with_no_placeholder_left(self):
        self.assertNotIn("{{", self.text)
        self.assertIn("Project `ABC` on `example.atlassian.net`", " ".join(self.text.split()))
        self.assertIn("00000000-0000-0000-0000-000000000000", self.text)

    def test_runs_on_haiku(self):
        self.assertEqual(self.fields["name"], "jira-reader")
        self.assertEqual(self.fields["model"], "haiku")

    def test_has_only_the_servers_read_tools_and_read(self):
        self.assertEqual(self.tools[0], "Read")
        self.assertEqual(sorted(self.tools[1:]), sorted(
            f"mcp__atlassian-work__{tool}" for tool in ("getJiraIssue", "searchJiraIssuesUsingJql", "discover", "executeRead")))

    def test_no_tool_can_write_jira_the_vault_or_spawn_an_agent(self):
        for tool in self.tools:
            self.assertNotRegex(tool, r"(?i)create|edit|comment|transition|worklog|link|update|executeWrite")
            self.assertNotIn(tool, {"Bash", "Edit", "Write", "Agent"})

    def test_has_a_turn_limit_and_skips_the_claude_md_files(self):
        self.assertIs(self.fields["omitClaudeMd"], True)
        self.assertIsInstance(self.fields["maxTurns"], int)
        self.assertGreater(self.fields["maxTurns"], 0)

    def test_the_description_says_what_it_returns_and_when_not_to_use_it(self):
        description = self.fields["description"]
        for needle in ("only the fields it was asked for", "JQL", "jira-creator"):
            self.assertIn(needle, description)

    def test_never_list_forbids_writes_pasted_json_and_advice(self):
        text = " ".join(self.text.split())
        for needle in ("Call a write tool", "paste an issue's JSON", "never spawn another agent", "advise what to do"):
            self.assertIn(needle, text)

    def test_the_input_names_issues_or_jql_fields_and_the_root(self):
        for needle in ("**issues**", "**jql**", "**fields**", "**root**"):
            self.assertIn(needle, self.text)

    def test_its_docs_exist_beside_the_toolbox_runtime(self):
        for name in ("procedure.md", "report.md"):
            self.assertTrue((DOCS / name).is_file(), name)

    def test_docs_named_in_the_agent_file_exist_beside_it(self):
        for name in re.findall(r"`([\w-]+\.md)`", self.text):
            self.assertTrue((DOCS / name).is_file(), name)

    def test_the_procedure_asks_jira_for_only_the_fields_wanted(self):
        procedure = " ".join((DOCS / "procedure.md").read_text().split())
        for needle in ("`fields` argument", "add no `expand`", "never ask for all fields", "responseContentFormat", "found: none", "`missing`",
                       "every issue line has the same fields in the same order"):
            self.assertIn(needle, procedure)


class JiraReaderReportContractTest(unittest.TestCase):
    def setUp(self):
        self.lib = load_contract_lib()

    def check(self, report: str) -> str | None:
        return self.lib.check_report("jira-reader", report, TOOLBOX)

    def test_the_template_declares_a_contract(self):
        self.assertIsNotNone(self.lib.load("jira-reader", TOOLBOX))

    def test_three_issues_with_the_fields_asked_for_go_through(self):
        self.assertIsNone(self.check(ISSUES))

    def test_a_jql_search_listing_key_and_summary_goes_through(self):
        self.assertIsNone(self.check(SEARCH))

    def test_none_blocked_and_missing_go_through(self):
        for report in (NONE, BLOCKED, MISSING):
            self.assertIsNone(self.check(report))

    def test_a_report_without_the_found_line_is_denied(self):
        self.assertIn("missing found", self.check(ISSUES.replace("found: 3 issues\n", "")))

    def test_an_issue_not_led_by_its_key_is_denied(self):
        self.assertIn("issue that isn't a key", self.check(ISSUES + "  - In Progress | ABC-4 | x\n"))
        self.assertIn("issue that isn't a key", self.check(ISSUES + "  - Add the thing\n"))

    def test_a_bare_key_with_no_values_goes_through(self):
        self.assertIsNone(self.check(ISSUES + "  - ABC-4\n"))

    def test_an_issue_line_that_runs_on_is_denied(self):
        self.assertIn("issue line over 1500 characters", self.check(ISSUES + "  - ABC-4 | " + "word " * 320 + "\n"))

    def test_pasted_json_is_denied(self):
        self.assertIn("pasted JSON", self.check(ISSUES + '{"key": "ABC-1"}\n'))
        self.assertIn("pasted JSON", self.check(ISSUES + '  "fields": {\n'))

    def test_a_summary_with_generics_or_a_quoted_key_goes_through(self):
        self.assertIsNone(self.check(ISSUES + '  - ABC-4 | To Do | unassigned | Map<String, Foo> and List<T> handling\n'))
        self.assertIsNone(self.check(ISSUES + '  - ABC-5 | To Do | unassigned | Rename "foo": bar\n'))

    def test_a_long_description_with_the_other_fields_goes_through(self):
        self.assertIsNone(self.check(ISSUES + "  - ABC-6 | To Do | x | " + "w" * 300 + " | " + "w" * 300 + "\n"))

    def test_rendered_html_is_denied(self):
        self.assertIn("rendered HTML", self.check(ISSUES + "<p>Add the thing</p>\n"))

    def test_an_api_url_is_denied(self):
        self.assertIn("API urls", self.check(ISSUES + "  self: https://example.atlassian.net/rest/api/3/issue/1\n"))

    def test_a_code_block_is_denied(self):
        self.assertIn("code block", self.check(ISSUES + "```\nstatus\n```\n"))

    def test_a_table_is_denied(self):
        self.assertIn("table rows", self.check(ISSUES + "| Key | Status |\n"))

    def test_more_than_200_issues_are_denied(self):
        many = "found: 201 issues\nfields: summary\nissues:\n" + "".join(f"  - ABC-{n} | s\n" for n in range(1, 202))
        self.assertIn("issues: 201 lines, at most 200", self.check(many))


class JiraReaderInstallTest(unittest.TestCase):
    def setUp(self):
        self.features = yaml.safe_load((WORKSPACE_FOLDER / "features.yml").read_text())

    def test_is_listed_under_the_jira_feature_only(self):
        listed = [feature for feature, entry in self.features.items()
                  if any("jira-reader" in path for path in (entry["paths"] if isinstance(entry, dict) else entry))]
        self.assertEqual(listed, ["jira"])

    def test_the_listed_path_is_the_template_and_the_jira_feature_needs_the_tracker(self):
        self.assertIn(".claude/agents/jira-reader.md.tmpl", self.features["jira"]["paths"])
        self.assertIn("tracker.jira", self.features["jira"]["needs"])
        self.assertTrue(TEMPLATE.is_file())


if __name__ == "__main__":
    unittest.main()
