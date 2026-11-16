"""The pr-reporter agent: its file, its docs beside the toolbox runtime, its report contract, and that it installs with
the pr feature together with pr_report.py (which test_pr_report.py covers).

Run from the repo root:

    python3 -m unittest discover -s core/workspace/.claude/toolbox/tests
"""

from __future__ import annotations

import importlib.util
import os
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

DOCS = TOOLBOX / "agents" / "pr-reporter"
AGENT = CLAUDE / "agents" / "pr-reporter.md"

PR_LINE = "pr: acme/pantry#12 OPEN; review: CHANGES_REQUESTED; checks: 0 failed, 0 pending, 4 passed\n"
COMMENTS = PR_LINE + """found: 3 comments
comments:
  - src/Shelf.java:42 | rita | change | rename `cans` to `canCount`
  - src/Rack.java:7 | sam | question | why a list and not a set? replied: order matters for the label printer
  - src/Old.java:19 | rita | resolved | (outdated: a later commit changed these lines) stray blank line
"""
FAILED = """pr: acme/pantry#12 OPEN; review: NONE; checks: 1 failed, 1 pending, 1 passed
found: none
failed:
  ! build | Run mvn verify
      [ERROR] Tests run: 3, Failures: 1, Errors: 0, Skipped: 0 <<< FAILURE!
      [ERROR] ShelfTest.countsCans:42 expected: <3> but was: <2>
      ##[error]Process completed with exit code 1.
notes: pending: deploy
"""
MIXED = COMMENTS + """failed:
  ! sonar | unknown
      not a GitHub Actions job: no log to read
"""
NOTHING = "pr: acme/pantry#12 OPEN; review: APPROVED; checks: 0 failed, 0 pending, 4 passed\nfound: none\n"
BLOCKED = "found: blocked: gh failed: Could not resolve to a PullRequest with the number of 99\n"
OTHER_KINDS = PR_LINE + """found: 3 comments
comments:
  - review body | rita | change | add tests for the empty shelf (CHANGES_REQUESTED)
  - conversation | sam | question | is this behind a flag?
  - src/Shelf.java:file | rita | nit | optional: split this class
"""


def load_contract_lib():
    spec = importlib.util.spec_from_file_location("report_contract_under_test", TOOLBOX / "lib" / "report_contract.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PrReporterAgentTest(unittest.TestCase):
    def setUp(self):
        self.text = AGENT.read_text()
        head, _ = frontmatter.split(self.text, AGENT)
        self.fields = yaml.safe_load(head)
        self.tools = {tool.strip() for tool in str(self.fields["tools"]).split(",")}

    def test_runs_on_haiku(self):
        self.assertEqual(self.fields["name"], "pr-reporter")
        self.assertEqual(self.fields["model"], "haiku")

    def test_can_run_the_script_and_read_docs_but_not_edit_write_or_spawn(self):
        self.assertEqual(self.tools, {"Bash", "Read"})

    def test_has_a_turn_limit_and_skips_the_claude_md_files(self):
        self.assertIs(self.fields["omitClaudeMd"], True)
        self.assertIsInstance(self.fields["maxTurns"], int)
        self.assertGreater(self.fields["maxTurns"], 0)

    def test_the_description_says_what_it_returns_and_that_it_never_edits_or_replies(self):
        description = " ".join(self.fields["description"].split())
        for needle in ("review comments", "failed CI checks", "already resolved", "never edits code or replies"):
            self.assertIn(needle, description)

    def test_never_list_forbids_gh_of_its_own_replies_edits_and_advice(self):
        text = " ".join(self.text.split())
        for needle in ("never edit or create files", "reply to or resolve a comment", "re-run a check", "run git",
                       "spawn another agent", "no `gh` command of your own", "advise how to fix"):
            self.assertIn(needle, text)

    def test_the_input_names_the_pr_and_the_bin_folder(self):
        for needle in ("**pr**", "**bin**"):
            self.assertIn(needle, self.text)

    def test_its_docs_exist_beside_the_toolbox_runtime_and_are_the_ones_it_names(self):
        for name in ("procedure.md", "report.md"):
            self.assertTrue((DOCS / name).is_file(), name)
        for name in re.findall(r"`([\w-]+\.md)`", self.text):
            self.assertTrue((DOCS / name).is_file(), name)

    def test_the_procedure_runs_only_the_script_and_sorts_by_the_four_kinds(self):
        procedure = " ".join((DOCS / "procedure.md").read_text().split())
        for needle in ("pr_report.py", "never fall back to a `gh` command of your own", "found: blocked", "`resolved`",
                       "`change`", "`question`", "`nit`", "`outdated`", "found: none", "exactly as given"):
            self.assertIn(needle, procedure)

    def test_the_script_it_runs_exists_and_is_executable(self):
        script = TOOLBOX / "bin" / "pr_report.py"
        self.assertTrue(script.is_file())
        self.assertTrue(os.access(script, os.X_OK))


class PrReporterReportContractTest(unittest.TestCase):
    def setUp(self):
        self.lib = load_contract_lib()

    def check(self, report: str) -> str | None:
        return self.lib.check_report("pr-reporter", report, TOOLBOX)

    def test_the_template_declares_a_contract(self):
        self.assertIsNotNone(self.lib.load("pr-reporter", TOOLBOX))

    def test_three_comments_one_resolved_go_through(self):
        self.assertIsNone(self.check(COMMENTS))

    def test_a_failed_check_with_its_step_and_error_lines_goes_through(self):
        self.assertIsNone(self.check(FAILED))

    def test_comments_and_a_failed_non_actions_check_go_through(self):
        self.assertIsNone(self.check(MIXED))

    def test_review_body_conversation_and_whole_file_comments_go_through(self):
        self.assertIsNone(self.check(OTHER_KINDS))

    def test_nothing_to_do_and_blocked_go_through(self):
        self.assertIsNone(self.check(NOTHING))
        self.assertIsNone(self.check(BLOCKED))

    def test_a_report_without_the_pr_or_found_line_is_denied(self):
        self.assertIn("missing found", self.check(COMMENTS.replace("found: 3 comments\n", "")))
        self.assertIn("missing pr line", self.check(COMMENTS.replace(PR_LINE, "")))

    def test_a_comment_with_an_unknown_kind_or_a_missing_field_is_denied(self):
        self.assertIn("comment without its four fields", self.check(COMMENTS + "  - a.py:3 | rita | maybe | x\n"))
        self.assertIn("comment without its four fields", self.check(COMMENTS + "  - a.py:3 | rita | change\n"))
        self.assertIn("comment without its four fields", self.check(COMMENTS + "  - a.py | rita | change | x\n"))
        self.assertIn("comment without its four fields", self.check(COMMENTS + "  - rita says rename it\n"))

    def test_a_failed_check_with_no_step_is_denied(self):
        self.assertIn("failed check without a step", self.check(FAILED.replace("build | Run mvn verify", "build")))

    def test_a_raw_log_line_with_its_timestamp_is_denied(self):
        raw = FAILED + "2026-11-10T10:00:00.1234567Z [INFO] building module 1\n"
        self.assertIn("raw log line", self.check(raw))
        self.assertIn("raw log line", self.check(FAILED + "      2026-11-10T10:00:00.1234567Z [ERROR] x\n"))

    def test_more_than_150_error_lines_is_a_log_dump_and_is_denied(self):
        dump = FAILED + "".join(f"      [ERROR] line {n}\n" for n in range(150))
        self.assertIn("error lines", self.check(dump))
        self.assertIsNone(self.check(FAILED + "".join(f"      [ERROR] line {n}\n" for n in range(100))))

    def test_more_than_ten_failed_checks_are_denied(self):
        many = FAILED + "".join(f"  ! job{n} | step\n" for n in range(10))
        self.assertIn("failed checks", self.check(many))

    def test_an_error_line_over_400_characters_is_denied(self):
        self.assertIn("error line over 400 characters", self.check(FAILED + "      [ERROR] " + "x" * 400 + "\n"))

    def test_an_error_line_that_starts_like_json_or_a_table_row_is_not_mistaken_for_it(self):
        self.assertIsNone(self.check(FAILED + "      [ERROR] a:b\n      | col | col |\n      {\"a\": 1}\n"))

    def test_a_comment_that_runs_on_is_denied(self):
        self.assertIn("comment over 600 characters",
                      self.check(COMMENTS + "  - a.py:3 | rita | change | " + "word " * 130 + "\n"))

    def test_a_diff_hunk_pasted_json_a_code_block_or_a_table_is_denied(self):
        self.assertIn("diff hunk", self.check(COMMENTS + "@@ -1,3 +1,4 @@\n"))
        self.assertIn("pasted JSON", self.check(COMMENTS + '{"path": "a.py"}\n'))
        self.assertIn("code block", self.check(COMMENTS + "```\nx\n```\n"))
        self.assertIn("table rows", self.check(COMMENTS + "| a | b |\n"))

    def test_an_api_url_is_denied(self):
        self.assertIn("api urls", self.check(COMMENTS + "notes: https://api.github.com/repos/acme/pantry/pulls/12\n"))


class PrReporterInstallTest(unittest.TestCase):
    def setUp(self):
        self.features = features()

    def test_the_agent_and_its_script_install_with_the_pr_feature_only(self):
        for name in ("pr-reporter", "pr_report"):
            listed = [feature for feature, entry in self.features.items()
                      if any(name in path for path in (entry["paths"] if isinstance(entry, dict) else entry))]
            self.assertEqual(listed, ["pr"], name)

    def test_the_listed_paths_exist(self):
        paths = self.features["pr"]["paths"]
        for path in (".claude/agents/pr-reporter.md", ".claude/toolbox/bin/pr_report.py"):
            self.assertIn(path, paths)
            self.assertTrue((WORKSPACE_FOLDER / path).is_file(), path)

    def test_the_docs_folder_is_unlisted_like_every_agents_docs(self):
        self.assertFalse([p for entry in self.features.values()
                          for p in (entry["paths"] if isinstance(entry, dict) else entry)
                          if "agents/pr-reporter" in p and p.endswith("/")])


if __name__ == "__main__":
    unittest.main()
