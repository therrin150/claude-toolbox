"""The `investigate` entry skill: how a session reaches it, what it does, and how a project hooks into it.

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
sys.path.insert(0, str(TOOLBOX / "lib"))

import extensions  # noqa: E402
import frontmatter  # noqa: E402

SKILL_DIR = CLAUDE / "skills" / "investigate"
SKILL = SKILL_DIR / "SKILL.md"
RULE = CLAUDE / "rules" / "session-types.md"


def description() -> str:
    head, _ = frontmatter.split(SKILL.read_text(), SKILL)
    return yaml.safe_load(head)["description"]


class InvestigateSkillTest(unittest.TestCase):
    def test_description_is_one_line_and_names_the_openings(self):
        # Scenario "Alert-Driven Investigation": "this slowdown alert fired, let's look" starts `investigate`.
        self.assertEqual(len(re.findall(r"^description:", SKILL.read_text(), re.M)), 1)
        text = description()
        for opening in ("this alert fired", "why is X restricted", "look into this production problem", "investigate ABC-12"):
            self.assertIn(f'"{opening}"', text)

    def test_the_session_types_rule_routes_those_openings_to_it(self):
        row = next(line for line in RULE.read_text().splitlines() if line.endswith("| `investigate` |"))
        self.assertIn('"this alert fired"', row)
        self.assertIn('"why is X restricted"', row)
        self.assertIn('"investigate ABC-12"', row)  # Scenario "Support Ticket About a DGS"

    def test_can_write_the_discussion(self):
        head, _ = frontmatter.split(SKILL.read_text(), SKILL)
        tools = yaml.safe_load(head)["allowed-tools"]
        for tool in ("Write", "Edit"):
            self.assertIn(tool, tools)

    def test_findings_go_into_a_discussion(self):
        # Scenario "Alert-Driven Investigation": its findings go into a discussion folder.
        text = SKILL.read_text()
        self.assertIn("`discussion` skill's layout", text)
        self.assertIn("`plan.md`", text)
        self.assertRegex(text, r"investigation trail")

    def test_ends_in_a_defect_when_there_is_a_fix(self):
        end = SKILL.read_text().split("## 5. End", 1)[1].split("## Extension Points", 1)[0]
        self.assertIn("`create-defect`", end)

    def test_is_read_only(self):
        text = " ".join(SKILL.read_text().split())
        self.assertIn("It changes nothing", text)
        self.assertIn("Never write to production", text)

    def test_uses_the_read_only_agents_not_the_raw_tools(self):
        text = SKILL.read_text()
        for agent in ("vault-researcher", "jira-reader", "workspace-searcher"):
            self.assertIn(f"`{agent}`", text)
            self.assertTrue((CLAUDE / "agents" / f"{agent}.md").is_file() or (CLAUDE / "agents" / f"{agent}.md.tmpl").is_file(),
                            f"{agent} is not an agent in this repo")

    def test_names_no_project_skill(self):
        # The generic skill never names a project's prefixed skill: an extension hooks those in.
        self.assertNotRegex(SKILL.read_text(), r"`rm-[a-z-]+`")

    def test_declares_two_hooks_each_run_in_the_text(self):
        points, problems = extensions.skill_points(SKILL_DIR)
        self.assertEqual(problems, [])
        self.assertEqual([point.id for point in points], ["subject-steps", "end"])
        for point in points:
            self.assertEqual(point.verbs, ("add",))
            self.assertRegex(SKILL.read_text(), rf"[Hh]ook `{point.id}`")

    def test_carries_the_extensions_paragraph(self):
        flat = " ".join(SKILL.read_text().split())
        self.assertIn("config extensions investigate --apply", flat)

    def test_installs_with_the_tasks_feature(self):
        # The skill ends in `create-defect` and reads task records, so it sits with the `tasks` feature.
        features = yaml.safe_load((CLAUDE.parent / "features.yml").read_text())
        self.assertIn(".claude/skills/investigate", features["tasks"]["paths"])
        self.assertIn(".claude/skills/create-defect", features["tasks"]["paths"])


if __name__ == "__main__":
    unittest.main()
