"""The session-types rule and the entry skills' descriptions: a session opened in plain words starts the right flow.

Run from the repo root:

    python3 -m unittest discover -s workspace/.claude/toolbox/tests
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

import yaml

TOOLBOX = Path(__file__).resolve().parents[1]
CLAUDE = TOOLBOX.parents[0]
REPO = CLAUDE.parents[1]
sys.path.insert(0, str(TOOLBOX / "lib"))

import frontmatter  # noqa: E402

RULE = CLAUDE / "rules" / "session-types.md"
SKILL_ROOTS = [CLAUDE / "skills", REPO / "global" / "skills"]

# Entry skills the rule names before they exist. `investigate` comes with the Agent-Oriented Toolbox story "Generalize
# Investigate Skill"; delete it from this set there, and the test then checks that the skill exists.
NOT_YET = {"investigate"}


def skill_path(name: str) -> Path | None:
    for root in SKILL_ROOTS:
        if (root / name / "SKILL.md").is_file():
            return root / name / "SKILL.md"
    return None


def description(name: str) -> str:
    path = skill_path(name)
    head, _ = frontmatter.split(path.read_text(), path)
    return yaml.safe_load(head)["description"]


def rule_rows() -> dict[str, str]:
    """The rule's table as {entry skill: the openings in its row}."""
    rows = {}
    for line in RULE.read_text().splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if line.startswith("|") and len(cells) == 2 and cells[1].startswith("`"):
            rows[cells[1].strip("`")] = cells[0]
    return rows


class SessionTypesRuleTest(unittest.TestCase):
    def test_names_an_entry_skill_for_each_session_type(self):
        self.assertEqual(set(rule_rows()), {"discussion", "investigate", "work-task"})

    def test_every_entry_skill_exists(self):
        for name in set(rule_rows()) - NOT_YET:
            self.assertIsNotNone(skill_path(name), f"the rule names `{name}`, which is not a skill in this repo")
        for name in NOT_YET:
            self.assertIsNone(skill_path(name), f"`{name}` exists now: remove it from NOT_YET")

    def test_is_short_enough_to_load_every_session(self):
        self.assertLessEqual(len(RULE.read_text().splitlines()), 30)

    def test_tells_the_session_to_start_the_skill_without_a_slash_command(self):
        self.assertIn("no slash command", RULE.read_text())

    def test_opening_with_a_ticket_key_starts_work_task(self):
        # Scenario "Opening Without a Slash Command": "work RMGT-1234" starts work-task for RMGT-1234.
        self.assertIn('"work ABC-12"', rule_rows()["work-task"])
        self.assertIn('"work ABC-12"', description("work-task"))

    def test_opening_with_a_design_request_starts_discussion(self):
        # Scenario "Design Session": "let's design a fix for the predict DLQ" starts discussion.
        self.assertIn('"let\'s design X"', rule_rows()["discussion"])
        self.assertIn('"let\'s design X"', description("discussion"))

    def test_installs_with_the_tasks_feature(self):
        # Every rule sits behind a feature; the rule names work-task, which the `tasks` feature installs.
        features = yaml.safe_load((CLAUDE.parent / "features.yml").read_text())
        paths = features["tasks"]["paths"]
        self.assertIn(".claude/rules/session-types.md", paths)
        self.assertIn(".claude/skills/work-task", paths)

    def test_work_task_description_still_names_the_slash_command(self):
        self.assertIn("/work-task", description("work-task"))

    def test_descriptions_stay_one_line(self):
        for name in ("discussion", "work-task"):
            text = skill_path(name).read_text()
            self.assertEqual(len(re.findall(r"^description:", text, re.M)), 1)


if __name__ == "__main__":
    unittest.main()
