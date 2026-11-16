"""The workspace-searcher agent: its file, its docs beside the toolbox runtime, its report contract, and that it installs
into every workspace.

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

DOCS = TOOLBOX / "agents" / "workspace-searcher"

CALLERS = """found: 3 locations in 2 repos
searched: SubmissionDataCache, submissionDataCache in themis-status, themis-reporting
locations:
  - themis-status/src/main/java/org/familysearch/status/StatusService.java:48 - reads it to answer a status lookup
  - themis-status/src/main/java/org/familysearch/status/CacheConfig.java:22 - wires it as a bean
  - themis-reporting/src/test/java/org/familysearch/reporting/ReportingIT.java:91 - seeds it before the run (test)
notes: 12 more locations in themis-status; narrow to a package to list them
"""

NONE = "found: none\nsearched: SubmissionDataCache in themis-status, themis-reporting\n"
BLOCKED = "found: blocked: the prompt gave no root\nsearched: nothing\n"


def agent_fields(path: Path) -> dict:
    head, _ = frontmatter.split(path.read_text(), path)
    return yaml.safe_load(head)


def load_contract_lib():
    spec = importlib.util.spec_from_file_location("report_contract_under_test", TOOLBOX / "lib" / "report_contract.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WorkspaceSearcherAgentTest(unittest.TestCase):
    def setUp(self):
        self.path = CLAUDE / "agents" / "workspace-searcher.md"
        self.fields = agent_fields(self.path)
        self.body = self.path.read_text()

    def test_runs_on_haiku(self):
        self.assertEqual(self.fields["name"], "workspace-searcher")
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
        self.assertIn("one-line role", description)
        self.assertIn("never file contents", description)
        self.assertIn("read it yourself", description)

    def test_never_list_forbids_contents_and_advice(self):
        text = " ".join(self.body.split())
        for needle in ("quote more than the one-line role", "never spawn another agent", "advice on what to change"):
            self.assertIn(needle, text)

    def test_its_docs_exist_beside_the_toolbox_runtime(self):
        for name in ("procedure.md", "report.md"):
            self.assertTrue((DOCS / name).is_file(), name)

    def test_docs_named_in_the_agent_file_exist_beside_it(self):
        for name in re.findall(r"`?([\w-]+\.md)`?", self.body):
            self.assertTrue((DOCS / name).is_file(), name)

    def test_the_procedure_skips_worktrees_so_no_hit_appears_twice(self):
        procedure = (DOCS / "procedure.md").read_text()
        self.assertIn(".worktrees", procedure)
        self.assertIn("import", procedure)
        self.assertIn("(test)", procedure)


class WorkspaceSearcherReportContractTest(unittest.TestCase):
    def setUp(self):
        self.lib = load_contract_lib()

    def check(self, report: str) -> str | None:
        return self.lib.check_report("workspace-searcher", report, TOOLBOX)

    def test_the_template_declares_a_contract(self):
        self.assertIsNotNone(self.lib.load("workspace-searcher", TOOLBOX))

    def test_a_list_of_callers_with_roles_goes_through(self):
        self.assertIsNone(self.check(CALLERS))

    def test_none_and_blocked_go_through(self):
        self.assertIsNone(self.check(NONE))
        self.assertIsNone(self.check(BLOCKED))

    def test_a_report_without_the_found_line_is_denied(self):
        self.assertIn("missing found", self.check(CALLERS.replace("found: 3 locations in 2 repos\n", "")))

    def test_a_report_without_the_searched_line_is_denied(self):
        reason = self.check(CALLERS.replace("searched: SubmissionDataCache, submissionDataCache in themis-status, "
                                            "themis-reporting\n", ""))
        self.assertIn("missing searched", reason)

    def test_a_location_with_no_role_is_denied(self):
        bad = CALLERS + "  - themis-status/src/main/java/org/familysearch/status/Other.java:7\n"
        self.assertIn("location without a role", self.check(bad))

    def test_a_code_block_is_denied(self):
        bad = CALLERS + "```java\nSubmissionDataCache cache = new SubmissionDataCache();\n```\n"
        self.assertIn("code block", self.check(bad))

    def test_numbered_file_lines_are_denied(self):
        bad = CALLERS + "   48: private final SubmissionDataCache cache;\n"
        self.assertIn("file contents", self.check(bad))

    def test_a_role_that_runs_on_like_a_pasted_line_is_denied(self):
        path = "themis-status/themis-status-app/src/main/java/org/familysearch/themis/status/service/Long.java"
        bad = CALLERS + f"  - {path}:9 - " + "x" * (260 - len(f"  - {path}:9 - ")) + "\n"  # the whole line is 260
        self.assertIn("over 260 characters", self.check(bad))
        self.assertIsNone(self.check(CALLERS + f"  - {path}:9 - " + "x" * (259 - len(f"  - {path}:9 - ")) + "\n"))

    def test_a_long_real_path_with_a_normal_role_goes_through(self):
        path = "themis-status/themis-status-app/src/main/java/org/familysearch/themis/status/service/Long.java"
        good = CALLERS + f"  - {path}:9 - reads it to decide whether to re-evaluate the submission\n"
        self.assertIsNone(self.check(good))

    def test_more_than_150_locations_are_denied(self):
        many = "found: 151 locations in 1 repo\nsearched: X in a\nlocations:\n" + "".join(
            f"  - a/F{n}.java:{n} - uses it\n" for n in range(1, 152))
        self.assertIn("locations: 151 lines, at most 150", self.check(many))


class WorkspaceSearcherInstallTest(unittest.TestCase):
    def test_is_listed_under_no_feature_so_it_installs_into_every_workspace(self):
        for feature, entry in features().items():
            for path in entry["paths"]:
                self.assertNotIn("workspace-searcher", path, feature)


if __name__ == "__main__":
    unittest.main()
