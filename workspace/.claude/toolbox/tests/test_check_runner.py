"""The check-runner agent replaces services-runner: its file, its docs beside the toolbox runtime, the bracket skills
that stop being model-invocable or go away, and the feature lists that install them.

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
WORKSPACE_FOLDER = TOOLBOX.parents[1]
REPO = WORKSPACE_FOLDER.parent
CLAUDE = WORKSPACE_FOLDER / ".claude"
sys.path.insert(0, str(TOOLBOX / "lib"))

import frontmatter  # noqa: E402

DOCS = TOOLBOX / "agents" / "check-runner"
REMOVED_SKILLS = ("run-integration-tests", "lock-local-services", "unlock-local-services")
USER_ONLY_SKILLS = ("start-local-services", "stop-local-services")


def agent_fields(path: Path) -> dict:
    head, _ = frontmatter.split(path.read_text(), path)
    return yaml.safe_load(head)


class CheckRunnerAgentTest(unittest.TestCase):
    def setUp(self):
        self.path = CLAUDE / "agents" / "check-runner.md"
        self.fields = agent_fields(self.path)
        self.body = self.path.read_text()

    def test_runs_on_haiku_with_a_tool_list_that_makes_its_never_list_true(self):
        self.assertEqual(self.fields["name"], "check-runner")
        self.assertEqual(self.fields["model"], "haiku")
        tools = {tool.strip() for tool in str(self.fields["tools"]).split(",")}
        self.assertEqual(tools, {"Bash", "Read"})  # no Edit or Write, and nothing that spawns an agent
        self.assertNotIn("Agent", tools)

    def test_has_a_turn_limit_and_skips_the_claude_md_files(self):
        self.assertIs(self.fields["omitClaudeMd"], True)
        self.assertIsInstance(self.fields["maxTurns"], int)
        self.assertGreater(self.fields["maxTurns"], 0)

    def test_never_puts_a_log_path_in_its_report(self):
        self.assertIn("A log path is never in your report", self.body)
        template = (DOCS / "report.md").read_text()
        self.assertNotIn("logs:", template)
        self.assertNotIn("log:", template)

    def test_services_runner_is_gone(self):
        self.assertFalse((CLAUDE / "agents" / "services-runner.md").exists())

    def test_every_mode_and_the_agent_s_docs_exist(self):
        for name in ("procedure.md", "bracket.md", "report.md"):
            self.assertTrue((DOCS / name).is_file(), name)
        procedure = (DOCS / "procedure.md").read_text()
        for mode in ("build", "unit", "format", "integration"):
            self.assertIn(mode, procedure)
            self.assertIn(f"`{mode}`", self.body)
        self.assertIn("run_checks.py", procedure)

    def test_docs_named_in_the_agent_file_exist_beside_it(self):
        for name in re.findall(r"`?([\w-]+\.md)`?", self.body):
            self.assertTrue((DOCS / name).is_file() or (CLAUDE / "agents" / name).is_file(), name)

    def test_the_bracket_doc_keeps_the_lock_rules_the_removed_skills_held(self):
        text = " ".join((DOCS / "bracket.md").read_text().split())
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
        names = REMOVED_SKILLS + ("services-runner",)
        scanned = [REPO / "README.md", *(REPO / folder for folder in ("workspace", "global"))]
        for path in (found for root in scanned for found in ([root] if root.is_file() else root.rglob("*"))):
            if "__pycache__" in path.parts or not path.is_file():
                continue
            if path.suffix not in (".md", ".py", ".yml", ".tmpl", ".txt"):
                continue
            if path == Path(__file__).resolve() or path.name == "test_work_task.py":
                continue
            text = path.read_text(errors="replace")
            for name in names:
                self.assertNotIn(name, text, f"{path.relative_to(REPO)} still names {name}")

    def test_start_skill_is_a_user_entry_point_with_its_extension_hooks_intact(self):
        text = (CLAUDE / "skills" / "start-local-services" / "SKILL.md").read_text()
        for hook in ("preflight", "prepare-the-checkout", "start-the-services", "wait-for-health"):
            self.assertIn(f"`{hook}`", text)
        self.assertIn("check-runner/bracket.md", text)


class FeaturesTest(unittest.TestCase):
    def setUp(self):
        self.features = yaml.safe_load((WORKSPACE_FOLDER / "features.yml").read_text())

    def paths(self, feature):
        entry = self.features[feature]
        return entry["paths"] if isinstance(entry, dict) else entry

    def test_the_tests_feature_installs_check_runner(self):
        self.assertIn(".claude/agents/check-runner.md", self.paths("tests"))

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
