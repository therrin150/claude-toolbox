"""The change-verifier agent: that it runs on Sonnet, and that it stays read-only.

Run from the repo root:

    python3 -m unittest discover -s core/workspace/.claude/toolbox/tests
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import yaml

from toolbox_tree import TOOLBOX  # noqa: E402
CLAUDE = TOOLBOX.parents[0]
sys.path.insert(0, str(TOOLBOX / "lib"))

import frontmatter  # noqa: E402


def agent_fields(path: Path) -> dict:
    head, _ = frontmatter.split(path.read_text(), path)
    return yaml.safe_load(head)


class ChangeVerifierAgentTest(unittest.TestCase):
    def setUp(self):
        self.path = CLAUDE / "agents" / "change-verifier.md"
        self.fields = agent_fields(self.path)

    def test_runs_on_sonnet(self):
        # Agent-Oriented Toolbox story "Trial change-verifier on Sonnet": the trial kept Sonnet (its log is the
        # discussion's change-verifier-trial.md). Its independence comes from its fresh context, not its model.
        self.assertEqual(self.fields["name"], "change-verifier")
        self.assertEqual(self.fields["model"], "sonnet")

    def test_is_read_only(self):
        tools = {tool.strip() for tool in str(self.fields["tools"]).split(",")}
        self.assertEqual(tools, {"Read", "Grep", "Glob"})


if __name__ == "__main__":
    unittest.main()
