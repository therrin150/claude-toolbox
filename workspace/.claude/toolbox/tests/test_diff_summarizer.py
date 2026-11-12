"""The diff-summarizer agent: its file, its docs beside the toolbox runtime, its report contract, that it installs with
the git feature together with diff_summary.py (which test_diff_summary.py covers), and that git-draft-commit-message
drafts from its summary instead of the raw diff.

Run from the repo root:

    python3 -m unittest discover -s workspace/.claude/toolbox/tests
"""

from __future__ import annotations

import importlib.util
import os
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

DOCS = TOOLBOX / "agents" / "diff-summarizer"
AGENT = CLAUDE / "agents" / "diff-summarizer.md"
DRAFT_SKILL = CLAUDE / "skills" / "git-draft-commit-message" / "SKILL.md"

SWEEP_REPORT = """found: 1 repo
demo: +96 -62, 31 files, against origin/main
  - rename Shelf to Rack | 30 files under src
  - rename the Shelf class to Rack | src/Shelf.java
  - add spare to Rack.count | src/Shelf.java
  ! System.out.println added | src/Shelf.java:3
notes: none
"""
TWO_REPOS = """found: 2 repos
service: +4 -1, 2 files, against origin/main
  - bump jackson 2.15.2 to 2.16.0 | pom.xml
  - add retry to FetchClient | src/FetchClient.java, src/Retry.java
client: +0 -0, 0 files, against origin/main
"""
NOTHING = "found: none\n"
BLOCKED = "found: blocked: no checkout named nowhere\n"


def load_contract_lib():
    spec = importlib.util.spec_from_file_location("report_contract_under_test", TOOLBOX / "lib" / "report_contract.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DiffSummarizerAgentTest(unittest.TestCase):
    def setUp(self):
        self.text = AGENT.read_text()
        head, _ = frontmatter.split(self.text, AGENT)
        self.fields = yaml.safe_load(head)
        self.tools = {tool.strip() for tool in str(self.fields["tools"]).split(",")}

    def test_runs_on_haiku(self):
        self.assertEqual(self.fields["name"], "diff-summarizer")
        self.assertEqual(self.fields["model"], "haiku")

    def test_can_run_the_script_and_read_the_diff_file_but_not_edit_write_or_spawn(self):
        self.assertEqual(self.tools, {"Bash", "Read"})

    def test_has_a_turn_limit_and_skips_the_claude_md_files(self):
        self.assertIs(self.fields["omitClaudeMd"], True)
        self.assertIsInstance(self.fields["maxTurns"], int)
        self.assertGreater(self.fields["maxTurns"], 0)

    def test_the_description_says_what_it_returns_and_that_it_never_says_why(self):
        description = " ".join(self.fields["description"].split())
        for needle in ("origin/<default>", "one line", "mechanical sweep", "out of place", "lines added and removed",
                       "Never says why"):
            self.assertIn(needle, description)

    def test_never_list_forbids_git_of_its_own_intent_and_the_message(self):
        text = " ".join(self.text.split())
        for needle in ("never edit or create files", "run git yourself", "spawn another agent", "no `git` command of "
                       "your own", "say why a change was made", "write the commit message"):
            self.assertIn(needle, text)

    def test_the_input_names_the_checkouts_the_bin_folder_and_the_base(self):
        for needle in ("**checkouts**", "**bin**", "**base**", "**focus**"):
            self.assertIn(needle, self.text)

    def test_its_docs_exist_beside_the_toolbox_runtime_and_are_the_ones_it_names(self):
        for name in ("procedure.md", "report.md"):
            self.assertTrue((DOCS / name).is_file(), name)
        for name in re.findall(r"`([\w-]+\.md)`", self.text):
            self.assertTrue((DOCS / name).is_file(), name)

    def test_the_procedure_runs_only_the_script_and_groups_by_change(self):
        procedure = " ".join((DOCS / "procedure.md").read_text().split())
        for needle in ("diff_summary.py", "Never run `git` yourself", "found: blocked", "found: none", "`sweeps`",
                       "`flags`", "## Focus", "a 40-file rename is one line", "It never says why", "never add, subtract or estimate"):
            self.assertIn(needle, procedure)

    def test_the_script_it_runs_exists_and_is_executable(self):
        script = TOOLBOX / "bin" / "diff_summary.py"
        self.assertTrue(script.is_file())
        self.assertTrue(os.access(script, os.X_OK))


class DiffSummarizerReportContractTest(unittest.TestCase):
    def setUp(self):
        self.lib = load_contract_lib()

    def check(self, report: str) -> str | None:
        return self.lib.check_report("diff-summarizer", report, TOOLBOX)

    def test_the_template_declares_a_contract(self):
        self.assertIsNotNone(self.lib.load("diff-summarizer", TOOLBOX))

    def test_a_sweep_a_logic_change_and_a_flag_go_through(self):
        self.assertIsNone(self.check(SWEEP_REPORT))

    def test_two_repos_one_unchanged_go_through(self):
        self.assertIsNone(self.check(TWO_REPOS))

    def test_nothing_and_blocked_go_through(self):
        self.assertIsNone(self.check(NOTHING))
        self.assertIsNone(self.check(BLOCKED))

    def test_a_report_without_the_found_line_is_denied(self):
        self.assertIn("missing found", self.check(SWEEP_REPORT.replace("found: 1 repo\n", "")))

    def test_a_change_without_its_files_is_denied(self):
        self.assertIn("change or flag without its files", self.check(SWEEP_REPORT + "  - add a method\n"))
        self.assertIn("change or flag without its files", self.check(SWEEP_REPORT + "  ! stray file |\n"))

    def test_a_stray_line_that_is_no_repo_change_flag_or_note_is_denied(self):
        for stray in ("The diff renames a class.\n", "- a bullet at the left edge | a.py\n", " - one space | a.py\n",
                      "    - four spaces | a.py\n", "demo: 3 files\n"):
            self.assertIn("no repo, change, flag or note", self.check(SWEEP_REPORT + stray), stray)

    def test_a_reason_for_a_change_is_denied(self):
        for why in ("rename Shelf to Rack because the name was confusing", "add retry so that calls survive",
                    "add retry in order to cope", "fix the count to fix the bug"):
            self.assertIn("why", self.check(SWEEP_REPORT + f"  - {why} | src/A.java\n"), why)

    def test_a_diff_hunk_a_diff_header_a_code_block_pasted_json_or_a_table_is_denied(self):
        self.assertIn("diff hunk", self.check(SWEEP_REPORT + "@@ -1,3 +1,4 @@\n"))
        self.assertIn("diff header", self.check(SWEEP_REPORT + "diff --git a/x b/x\n"))
        self.assertIn("code block", self.check(SWEEP_REPORT + "```\nx\n```\n"))
        self.assertIn("pasted JSON", self.check(SWEEP_REPORT + '{"files": []}\n'))
        self.assertIn("table rows", self.check(SWEEP_REPORT + "| a | b |\n"))

    def test_a_line_over_400_characters_is_denied(self):
        self.assertIn("line over 400 characters", self.check(SWEEP_REPORT + "  - " + "x" * 400 + " | a\n"))

    def test_more_than_150_changes_or_60_flags_is_a_dump_and_is_denied(self):
        many = SWEEP_REPORT + "".join(f"  - change {n} | f{n}.py\n" for n in range(150))
        self.assertIn("change lines", self.check(many))
        flags = SWEEP_REPORT + "".join(f"  ! stray {n} | f{n}.py\n" for n in range(60))
        self.assertIn("out of place lines", self.check(flags))


class DiffSummarizerInstallTest(unittest.TestCase):
    def setUp(self):
        self.features = yaml.safe_load((WORKSPACE_FOLDER / "features.yml").read_text())

    def test_the_agent_and_its_script_install_with_the_git_feature_only(self):
        for name in ("diff-summarizer", "diff_summary"):
            listed = [feature for feature, entry in self.features.items()
                      if any(name in path for path in (entry["paths"] if isinstance(entry, dict) else entry))]
            self.assertEqual(listed, ["git"], name)

    def test_the_listed_paths_exist(self):
        for path in (".claude/agents/diff-summarizer.md", ".claude/toolbox/bin/diff_summary.py"):
            self.assertIn(path, self.features["git"])
            self.assertTrue((WORKSPACE_FOLDER / path).is_file(), path)

    def test_the_docs_folder_is_unlisted_like_every_agents_docs(self):
        self.assertFalse([p for entry in self.features.values()
                          for p in (entry["paths"] if isinstance(entry, dict) else entry)
                          if p.startswith(".claude/toolbox/agents")])


class DraftSkillTest(unittest.TestCase):
    def setUp(self):
        self.text = " ".join(DRAFT_SKILL.read_text().split())

    def test_it_spawns_the_summarizer_and_never_reads_the_whole_diff(self):
        for needle in ("`diff-summarizer`", "Never run `git diff`", "never read the diff file"):
            self.assertIn(needle, self.text)

    def test_it_drafts_from_the_summary_the_record_and_the_style_files(self):
        self.assertIn("the summary, the task record and the style files", self.text)

    def test_a_vague_line_is_followed_up_with_the_agents_focus_input_not_the_diff(self):
        self.assertIn("spawn it again with **focus** set to that file", self.text)

    def test_the_old_diff_input_is_gone(self):
        self.assertNotIn("the diff against `origin/<default branch>` (committed and uncommitted) and its file list",
                         self.text)


if __name__ == "__main__":
    unittest.main()
