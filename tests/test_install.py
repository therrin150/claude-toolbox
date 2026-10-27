"""Tests for install.py, against temporary toolboxes, targets and workspaces.

Run from the repo root:

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import install  # noqa: E402

RULE = Path(".claude/rules/toolbox-config.md")
CONFIG = Path(".claude/toolbox.local.yml")


def write(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def skill(folder: Path, name: str) -> None:
    write(folder / name / "SKILL.md", f"---\nname: {name}\ndescription: test\n---\n")


def workspace_config(name: str, features: list) -> str:
    return yaml.safe_dump({
        "version": 1,
        "features": features,
        "workspace": {"name": name, "layout": "single"},
        "user": {"handle": "tester", "name": "Tester"},
        "vault": {"path": "vault"},
        "tracker": {"kind": "none"},
    })


class InstallerTest(unittest.TestCase):
    """A toolbox with `global` and the per-workspace `workspace` domain, another toolbox with `team`, and two
    workspaces: ws1 with the `services-lock` feature, ws2 (a git repo) without."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(self.tmp)], check=False))

        self.repo = self.tmp / "claude-toolbox"
        self.home = self.tmp / "home" / ".claude"
        self.home.mkdir(parents=True)
        write(self.repo / "domains.yml", yaml.safe_dump({"domains": {
            "global": {"source": "global", "target": str(self.home)},
            "workspace": {"source": "workspace", "target": "per-workspace", "ignore": [".claude/toolbox/tests"]},
        }}))
        write(self.repo / "global" / "rules" / "style.md", "# Style\n")
        source = self.repo / "workspace"
        skill(source / ".claude" / "skills", "always")
        skill(source / ".claude" / "skills", "lock-local-services")
        write(source / ".claude" / "toolbox" / "bin" / "tool.py", "print('hi')\n")
        write(source / ".claude" / "toolbox" / "tests" / "test_tool.py", "")
        write(source / "features.yml", yaml.safe_dump({"services-lock": [".claude/skills/lock-local-services"]}))

        self.work = self.tmp / "work-toolbox"
        self.team_target = self.tmp / "team-workspace"
        self.team_target.mkdir()
        write(self.work / "domains.yml", yaml.safe_dump({"domains": {
            "team": {"source": "team", "target": str(self.team_target), "prefix": "tm-"},
        }}))
        write(self.work / "team" / ".claude" / "rules" / "tm-conventions.md", "# Conventions\n")

        self.ws1 = self.tmp / "ws1"
        self.ws2 = self.tmp / "ws2"
        write(self.ws1 / CONFIG, workspace_config("one", ["services-lock"]))
        write(self.ws2 / CONFIG, workspace_config("two", []))
        subprocess.run(["git", "init", "-q", str(self.ws2)], check=True)

        self.machine(domains=["global", "team"], toolboxes=[self.work], workspaces=[self.ws1, self.ws2])
        patcher = mock.patch.multiple(
            install,
            REPO=self.repo,
            MANIFEST=self.repo / "domains.yml",
            MACHINE=self.repo / "machine.yml",
            MACHINE_EXAMPLE=self.repo / "machine.example.yml",
            STATE=self.repo / ".install-state.json",
            PLAN=self.repo / "install-plan.txt",
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    # Helpers

    def machine(self, domains, toolboxes=(), workspaces=()):
        write(self.repo / "machine.yml", yaml.safe_dump({
            "domains": list(domains),
            "toolboxes": [str(path) for path in toolboxes],
            "workspaces": [str(path) for path in workspaces],
        }))

    def run_install(self, *args) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = install.main(list(args))
        return code, (self.repo / "install-plan.txt").read_text(encoding="utf-8")

    def section(self, plan: str, title: str) -> list:
        lines = plan.splitlines()
        start = next(index for index, line in enumerate(lines) if line.startswith(f"== {title} ("))
        items = []
        for line in lines[start + 1:]:
            if not line.startswith("  "):
                break
            items.append(line.strip())
        return items

    def state(self) -> dict:
        return json.loads((self.repo / ".install-state.json").read_text(encoding="utf-8"))

    def assertLinked(self, target: Path, source: Path):
        self.assertTrue(target.is_symlink(), f"{target} isn't a link")
        self.assertEqual(Path(os.readlink(target)), source)


class OtherToolboxes(InstallerTest):
    def test_installs_another_toolboxs_domain_against_its_own_repo(self):
        code, plan = self.run_install("--apply")
        self.assertEqual(code, 0, plan)
        self.assertLinked(
            self.team_target / ".claude" / "rules" / "tm-conventions.md",
            self.work / "team" / ".claude" / "rules" / "tm-conventions.md",
        )
        self.assertLinked(self.home / "rules" / "style.md", self.repo / "global" / "rules" / "style.md")
        self.assertIn("Domains: global, team, workspace", plan)

    def test_a_domain_defined_twice_is_a_problem_and_the_first_wins(self):
        write(self.work / "domains.yml", yaml.safe_dump({"domains": {
            "team": {"source": "team", "target": str(self.team_target), "prefix": "tm-"},
            "global": {"source": "team", "target": str(self.team_target)},
        }}))
        code, plan = self.run_install()
        self.assertEqual(code, 1)
        problems = self.section(plan, "Problems")
        self.assertTrue(any("domain global: defined in both" in line for line in problems), problems)
        self.assertTrue(any("home/.claude/rules/style.md" in line for line in self.section(plan, "Create links")))

    def test_a_missing_toolbox_is_a_problem(self):
        self.machine(domains=["global"], toolboxes=[self.tmp / "nowhere"])
        code, plan = self.run_install()
        self.assertEqual(code, 1)
        self.assertTrue(any("no domains.yml" in line for line in self.section(plan, "Problems")))


class PerWorkspace(InstallerTest):
    def test_each_workspace_gets_what_its_features_allow(self):
        code, plan = self.run_install("--apply")
        self.assertEqual(code, 0, plan)
        source = self.repo / "workspace" / ".claude"
        for ws in (self.ws1, self.ws2):
            self.assertLinked(ws / ".claude" / "skills" / "always", source / "skills" / "always")
            self.assertLinked(ws / ".claude" / "toolbox" / "bin" / "tool.py", source / "toolbox" / "bin" / "tool.py")
            self.assertFalse((ws / ".claude" / "toolbox" / "tests").exists())
            self.assertFalse((ws / "features.yml").exists())
        self.assertLinked(
            self.ws1 / ".claude" / "skills" / "lock-local-services", source / "skills" / "lock-local-services"
        )
        self.assertFalse((self.ws2 / ".claude" / "skills" / "lock-local-services").exists())
        self.assertEqual(self.state()["links"][install.norm(self.ws1 / ".claude/skills/always")]["workspace"],
                         install.norm(self.ws1))

    def test_a_dropped_feature_removes_its_links(self):
        self.run_install("--apply")
        write(self.ws1 / CONFIG, workspace_config("one", []))
        code, plan = self.run_install()
        self.assertEqual(self.section(plan, "Remove stale links"), [str(self.ws1 / ".claude/skills/lock-local-services")])
        self.assertEqual(self.section(plan, "Create links"), [])
        self.run_install("--apply")
        self.assertFalse((self.ws1 / ".claude" / "skills" / "lock-local-services").exists())
        self.assertTrue((self.ws1 / ".claude" / "skills" / "always").is_symlink())

    def test_unknown_features_and_missing_feature_paths_warn(self):
        write(self.ws2 / CONFIG, workspace_config("two", ["telepathy"]))
        write(self.repo / "workspace" / "features.yml", yaml.safe_dump({
            "services-lock": [".claude/skills/lock-local-services", ".claude/skills/not-yet"],
        }))
        _, plan = self.run_install()
        warnings = self.section(plan, "Warnings")
        self.assertTrue(any("'telepathy' isn't in any features.yml" in line for line in warnings), warnings)
        self.assertTrue(any(".claude/skills/not-yet doesn't exist" in line for line in warnings), warnings)

    def test_a_workspace_without_config_gets_nothing_and_keeps_what_it_had(self):
        self.run_install("--apply")
        (self.ws1 / CONFIG).unlink()
        code, plan = self.run_install("--apply")
        self.assertEqual(code, 1)
        problems = self.section(plan, "Problems")
        self.assertTrue(any("ws1" in line and "/setup-workspace" in line for line in problems), problems)
        self.assertEqual(self.section(plan, "Remove stale links"), [])
        self.assertTrue((self.ws1 / ".claude" / "skills" / "always").is_symlink())
        self.assertTrue((self.ws1 / RULE).is_file())

    def test_an_invalid_config_is_a_problem(self):
        write(self.ws1 / CONFIG, "version: 1\n")
        code, plan = self.run_install()
        self.assertEqual(code, 1)
        self.assertTrue(any("invalid" in line and "workspace.name: required" in line
                            for line in self.section(plan, "Problems")))
        self.assertFalse(any(str(self.ws1) in line for line in self.section(plan, "Create links")))


class GeneratedRule(InstallerTest):
    def test_create_update_stale_and_teardown(self):
        code, plan = self.run_install()
        self.assertIn(f"{self.ws1 / RULE} (new)", self.section(plan, "Write generated rules"))
        self.assertFalse((self.ws1 / RULE).exists())

        self.run_install("--apply")
        text = (self.ws1 / RULE).read_text(encoding="utf-8")
        self.assertIn("`one`", text)
        self.assertFalse((self.ws1 / RULE).is_symlink())
        self.assertIn(install.norm(self.ws1 / RULE), self.state()["generated"])

        _, plan = self.run_install()
        self.assertEqual(self.section(plan, "Write generated rules"), [])
        self.assertIn(f"{self.ws1 / RULE} (generated)", self.section(plan, "Already in place"))

        write(self.ws1 / CONFIG, workspace_config("uno", ["services-lock"]))
        _, plan = self.run_install()
        self.assertEqual(self.section(plan, "Write generated rules"), [f"{self.ws1 / RULE} (changed)"])
        self.run_install("--apply")
        self.assertIn("`uno`", (self.ws1 / RULE).read_text(encoding="utf-8"))

        self.machine(domains=["global", "team"], toolboxes=[self.work], workspaces=[self.ws2])
        _, plan = self.run_install()
        self.assertEqual(self.section(plan, "Remove generated rules"), [str(self.ws1 / RULE)])
        self.run_install("--apply")
        self.assertFalse((self.ws1 / RULE).exists())
        self.assertFalse((self.ws1 / ".claude" / "skills").exists())
        self.assertTrue((self.ws1 / CONFIG).is_file())

        self.run_install("--teardown", "--apply")
        self.assertFalse((self.ws2 / RULE).exists())
        self.assertEqual(self.state()["generated"], {})
        self.assertEqual(self.state()["links"], {})

    def test_a_file_the_installer_didnt_write_is_a_conflict(self):
        write(self.ws1 / RULE, "# Mine\n")
        code, plan = self.run_install("--apply")
        self.assertEqual(code, 1)
        self.assertTrue(any("[not-generated]" in line for line in self.section(plan, "Conflicts, left alone")))
        self.assertEqual((self.ws1 / RULE).read_text(encoding="utf-8"), "# Mine\n")

    def test_an_unrecorded_generated_rule_is_adopted(self):
        self.run_install("--apply")
        state = self.state()
        state["generated"] = {}
        (self.repo / ".install-state.json").write_text(json.dumps(state), encoding="utf-8")
        code, plan = self.run_install("--apply")
        self.assertEqual(code, 0, plan)
        self.assertIn(install.norm(self.ws1 / RULE), self.state()["generated"])


class GitExcludes(InstallerTest):
    def exclude(self) -> str:
        path = self.ws2 / ".git" / "info" / "exclude"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def test_a_repo_workspace_excludes_links_config_and_rule(self):
        self.run_install("--apply")
        text = self.exclude()
        self.assertIn("# >>> claude-toolbox:workspace (managed by install.py)", text)
        for entry in ("/.claude/toolbox.local.yml", "/.claude/rules/toolbox-config.md", "/.claude/skills/always",
                      "/.claude/toolbox/bin/tool.py"):
            self.assertIn(f"\n{entry}\n", text)
        self.assertNotIn("lock-local-services", text)
        self.assertEqual(
            subprocess.run(["git", "-C", str(self.ws2), "status", "--porcelain"], capture_output=True, text=True,
                           check=True).stdout,
            "",
        )
        self.assertFalse((self.ws1 / ".git").exists())

        self.run_install("--teardown", "--apply")
        self.assertNotIn("claude-toolbox:workspace", self.exclude())


class AdoptedState(InstallerTest):
    """Links and state left by another toolbox's retired installer are this installer's own."""

    def adopt(self, source: Path):
        target = self.team_target / ".claude" / "rules" / "tm-conventions.md"
        target.parent.mkdir(parents=True)
        os.symlink(source, target)
        write(self.repo / ".install-state.json", json.dumps({
            "dirs": {install.norm(target.parent.parent): {"path": str(target.parent.parent), "domain": "team"},
                     install.norm(target.parent): {"path": str(target.parent), "domain": "team"}},
            "links": {install.norm(target): {"target": str(target), "source": str(source), "domain": "team"}},
        }))
        return target

    def test_adopted_links_are_in_place(self):
        self.adopt(self.work / "team" / ".claude" / "rules" / "tm-conventions.md")
        self.machine(domains=["team"], toolboxes=[self.work])
        code, plan = self.run_install()
        self.assertEqual(code, 0, plan)
        self.assertEqual(self.section(plan, "Create links"), [])
        self.assertEqual(self.section(plan, "Create folders"), [])
        self.assertEqual(self.section(plan, "Already in place"),
                         [str(self.team_target / ".claude" / "rules" / "tm-conventions.md")])

    def test_a_link_into_another_listed_toolbox_is_replaced_not_a_conflict(self):
        old = write(self.work / "team" / ".claude" / "rules" / "tm-old.md")
        self.adopt(old)
        self.machine(domains=["team"], toolboxes=[self.work])
        code, plan = self.run_install()
        self.assertEqual(code, 0, plan)
        self.assertEqual(len(self.section(plan, "Replace links")), 1)

    def test_adopted_links_go_when_their_domain_is_dropped(self):
        target = self.adopt(self.work / "team" / ".claude" / "rules" / "tm-conventions.md")
        self.machine(domains=["global"], toolboxes=[self.work])
        self.run_install("--apply")
        self.assertFalse(target.is_symlink())
        self.assertFalse((self.team_target / ".claude").exists())


class WorkspaceOption(InstallerTest):
    def test_acts_on_the_named_workspace_only(self):
        code, plan = self.run_install("--workspace", str(self.ws1), "--apply")
        self.assertEqual(code, 0, plan)
        self.assertIn("Domains: workspace", plan)
        self.assertTrue((self.ws1 / ".claude" / "skills" / "always").is_symlink())
        self.assertFalse((self.ws2 / ".claude" / "skills").exists())
        self.assertFalse((self.home / "rules").exists())

        self.run_install("--apply")
        self.run_install("--workspace", str(self.ws1), "--teardown", "--apply")
        self.assertFalse((self.ws1 / ".claude" / "skills").exists())
        self.assertFalse((self.ws1 / RULE).exists())
        self.assertTrue((self.ws2 / ".claude" / "skills" / "always").is_symlink())
        self.assertTrue((self.home / "rules" / "style.md").is_symlink())

    def test_an_unlisted_workspace_warns(self):
        ws3 = self.tmp / "ws3"
        write(ws3 / CONFIG, workspace_config("three", []))
        _, plan = self.run_install("--workspace", str(ws3))
        self.assertTrue(any("not in machine.yml" in line for line in self.section(plan, "Warnings")))

    def test_rejects_a_domain_that_isnt_per_workspace(self):
        with self.assertRaises(SystemExit) as raised:
            self.run_install("--workspace", str(self.ws1), "--domain", "global")
        self.assertIn("per-workspace domains only", str(raised.exception.code))

    def test_domain_option_still_skips_workspaces(self):
        code, plan = self.run_install("--domain", "global", "--apply")
        self.assertEqual(code, 0, plan)
        self.assertFalse((self.ws1 / ".claude" / "skills").exists())
        self.assertTrue((self.home / "rules" / "style.md").is_symlink())


if __name__ == "__main__":
    unittest.main()
