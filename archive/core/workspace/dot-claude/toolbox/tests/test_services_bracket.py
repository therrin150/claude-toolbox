"""The by-hand services bracket: its reference doc, the bracket skills that stop being model-invocable or go away,
and the feature lists that install them. No check-runner agent exists any more: sessions run `run_checks.py`.

Run from the repo root:

    python3 -m unittest discover -s core/workspace/.claude/toolbox/tests
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

import yaml

from toolbox_tree import REPO, TOOLBOX, features  # noqa: E402
WORKSPACE_FOLDER = TOOLBOX.parents[1]
CLAUDE = WORKSPACE_FOLDER / ".claude"
sys.path.insert(0, str(TOOLBOX / "lib"))

import frontmatter  # noqa: E402

BRACKET = TOOLBOX / "references" / "services-bracket.md"
REMOVED_SKILLS = ("run-integration-tests", "lock-local-services", "unlock-local-services")
USER_ONLY_SKILLS = ("start-local-services", "stop-local-services")


def agent_fields(path: Path) -> dict:
    head, _ = frontmatter.split(path.read_text(), path)
    return yaml.safe_load(head)


class BracketDocTest(unittest.TestCase):
    def test_the_check_runner_agent_and_its_docs_are_gone(self):
        self.assertFalse((CLAUDE / "agents" / "check-runner.md").exists())
        self.assertFalse((TOOLBOX / "agents" / "check-runner").exists())
        self.assertFalse((CLAUDE / "agents" / "services-runner.md").exists())

    def test_the_bracket_doc_keeps_the_lock_rules_the_removed_skills_held(self):
        text = " ".join(BRACKET.read_text().split())
        for needle in ("--max-seconds 540", "45 minutes", "taken-over", "never use `--force`", "NOT RELEASED",
                       "services_lock.py stop [name] --token <token>", "--manual"):
            self.assertIn(needle.lower(), text.lower(), needle)


class BracketSkillsTest(unittest.TestCase):
    def test_start_and_stop_are_not_model_invocable(self):
        for name in USER_ONLY_SKILLS:
            fields = agent_fields(CLAUDE / "skills" / name / "SKILL.md")
            self.assertIs(fields.get("disable-model-invocation"), True, name)

    def test_the_other_bracket_skills_are_removed(self):
        for name in REMOVED_SKILLS:
            self.assertFalse((CLAUDE / "skills" / name).exists(), name)

    def test_nothing_in_the_repo_names_a_removed_skill_or_the_old_agent(self):
        names = REMOVED_SKILLS + ("services-runner", "check-runner")
        scanned = [REPO / "README.md", *(REPO / folder for folder in ("core", "features"))]
        for path in (found for root in scanned for found in ([root] if root.is_file() else root.rglob("*"))):
            if "__pycache__" in path.parts or not path.is_file():
                continue
            if path.suffix not in (".md", ".py", ".yml", ".tmpl", ".txt"):
                continue
            if path == Path(__file__).resolve() or path.name in ("test_work_task.py", "test_change.py"):
                continue
            text = path.read_text(errors="replace")
            for name in names:
                self.assertNotIn(name, text, f"{path.relative_to(REPO)} still names {name}")

    def test_start_skill_is_a_user_entry_point_with_its_extension_hooks_intact(self):
        text = (CLAUDE / "skills" / "start-local-services" / "SKILL.md").read_text()
        for hook in ("preflight", "prepare-the-checkout", "start-the-services", "wait-for-health"):
            self.assertIn(f"`{hook}`", text)
        self.assertIn("references/services-bracket.md", text)


class FeaturesTest(unittest.TestCase):
    def setUp(self):
        self.features = features()

    def paths(self, feature):
        entry = self.features[feature]
        return entry["paths"] if isinstance(entry, dict) else entry

    def test_the_tests_feature_installs_check_change_and_no_agent(self):
        paths = self.paths("tests")
        self.assertIn(".claude/skills/check-change", paths)
        self.assertNotIn(".claude/agents/check-runner.md", paths)

    def test_services_lock_keeps_the_user_skills_and_the_script_only(self):
        paths = self.paths("services-lock")
        self.assertEqual(sorted(paths), [".claude/skills/start-local-services", ".claude/skills/stop-local-services",
                                         ".claude/toolbox/bin/services_lock.py"])

    def test_every_listed_path_exists(self):
        for feature in self.features:
            for path in self.paths(feature):
                self.assertTrue((WORKSPACE_FOLDER / path).exists() or (WORKSPACE_FOLDER / (path + ".tmpl")).exists(),
                                f"{feature}: {path}")


if __name__ == "__main__":
    unittest.main()
