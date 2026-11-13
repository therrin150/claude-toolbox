"""Every Jira read in the toolbox's skills goes through `jira-reader`, and the doc that says how to call it exists.

Run from the repo root:

    python3 -m unittest discover -s core/workspace/.claude/toolbox/tests
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from toolbox_tree import CLAUDE, SKILLS, TOOLBOX  # noqa: E402

DOC = TOOLBOX / "references" / "jira-reads.md"
DOC_NAME = ".claude/toolbox/references/jira-reads.md"

# the skill files that read Jira, and the field set each names in the doc
CALLERS = {
    SKILLS / "work-task" / "SKILL.md": (),
    SKILLS / "work-task" / "references" / "new-record.md": ("import",),
    SKILLS / "work-task" / "references" / "work.md": ("tracker-check", "triage"),
    SKILLS / "work-tasks" / "SKILL.md": ("triage",),
    SKILLS / "create-tickets" / "SKILL.md": ("filed",),
    SKILLS / "propose-tickets" / "references" / "jira.md": ("epics",),
    SKILLS / "jira-find-current-sprint" / "SKILL.md": ("sprints",),
}

DIRECT = re.compile(r"`(?:mcp__<server>__)?(?:getJiraIssue|searchJiraIssuesUsingJql)`")


class JiraReadsDocTest(unittest.TestCase):
    def setUp(self) -> None:
        self.raw = DOC.read_text()
        self.doc = " ".join(self.raw.split())

    def test_the_doc_exists_and_gives_the_prompt_and_the_modes(self) -> None:
        for needle in ("root:", "issues:", "jql:", "fields:", "save:", "`model: sonnet`", "spawn the reader once more"):
            self.assertIn(needle, self.doc, needle)

    def test_every_set_a_caller_names_is_defined(self) -> None:
        sets = set(re.findall(r"^\| `([\w-]+)` \|", self.raw, re.M))
        for path, names in CALLERS.items():
            for name in names:
                self.assertIn(name, sets, f"{path.name}: {name}")

    def test_the_callers_exist_name_the_doc_and_make_no_direct_read(self) -> None:
        for path, names in CALLERS.items():
            self.assertTrue(path.is_file(), path)
            text = path.read_text()
            self.assertIn(DOC_NAME, text, path)
            self.assertIn("`jira-reader`", text, path)
            self.assertIsNone(DIRECT.search(text), f"{path}: a direct Jira read")
            for name in names:
                self.assertIn(f"`{name}`", text, f"{path.name} should name set {name}")

    def test_the_gates_message_names_the_doc(self) -> None:
        gate = (CLAUDE / "hooks" / "gates" / "pre_tool_use" / "jira_read_gate.py").read_text()
        self.assertIn(DOC_NAME, gate)


if __name__ == "__main__":
    unittest.main()
