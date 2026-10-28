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
    """This toolbox, its `global` folder installing into a fake ~/.claude and its `workspace` folder into each
    workspace; another toolbox, work-toolbox, installing its `team` folder; and two workspaces: ws1 with the
    `services-lock` feature, ws2 (a git repo) without."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(self.tmp)], check=False))

        self.repo = self.tmp / "claude-toolbox"
        self.home = self.tmp / "home" / ".claude"
        self.home.mkdir(parents=True)
        write(self.repo / "toolbox.yml", "ship: direct\n")
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
        self.manifest(self.work)
        write(self.work / "team" / ".claude" / "rules" / "tm-conventions.md", "# Conventions\n")

        self.ws1 = self.tmp / "ws1"
        self.ws2 = self.tmp / "ws2"
        write(self.ws1 / CONFIG, workspace_config("one", ["services-lock"]))
        write(self.ws2 / CONFIG, workspace_config("two", []))
        subprocess.run(["git", "init", "-q", str(self.ws2)], check=True)

        self.machine(toolboxes=[self.work], workspaces=[self.ws1, self.ws2])
        own = {**install.OWN_PARTS, "global": {"source": "global", "target": str(self.home)}}
        patcher = mock.patch.multiple(
            install,
            REPO=self.repo,
            OWN_PARTS=own,
            MACHINE=self.repo / "machine.yml",
            MACHINE_EXAMPLE=self.repo / "machine.example.yml",
            STATE=self.repo / ".install-state.json",
            PLAN=self.repo / "install-plan.txt",
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    # Helpers

    def machine(self, toolboxes=(), workspaces=(), **extra):
        write(self.repo / "machine.yml", yaml.safe_dump({
            "toolboxes": [str(path) for path in toolboxes],
            "workspaces": [str(path) for path in workspaces],
            **extra,
        }))

    def manifest(self, toolbox: Path, **extra) -> None:
        write(toolbox / "toolbox.yml", yaml.safe_dump({
            "ship": "pr", "source": "team", "target": str(self.team_target), "prefix": "tm-", **extra,
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
    def test_installs_another_toolbox_whole_against_its_own_repo(self):
        code, plan = self.run_install("--apply")
        self.assertEqual(code, 0, plan)
        self.assertLinked(
            self.team_target / ".claude" / "rules" / "tm-conventions.md",
            self.work / "team" / ".claude" / "rules" / "tm-conventions.md",
        )
        self.assertLinked(self.home / "rules" / "style.md", self.repo / "global" / "rules" / "style.md")
        self.assertIn("Toolboxes: claude-toolbox, work-toolbox", plan)
        self.assertFalse((self.team_target / "toolbox.yml").exists())

    def test_a_toolbox_name_taken_twice_is_a_problem_and_the_first_wins(self):
        other = self.tmp / "elsewhere" / "work-toolbox"
        self.manifest(other)
        self.machine(toolboxes=[self.work, other])
        code, plan = self.run_install()
        self.assertEqual(code, 1)
        problems = self.section(plan, "Problems")
        self.assertTrue(any("elsewhere/work-toolbox: the name work-toolbox is taken" in line for line in problems),
                        problems)
        self.assertTrue(any("tm-conventions.md" in line for line in self.section(plan, "Create links")))

    def test_a_missing_toolbox_is_a_problem(self):
        self.machine(toolboxes=[self.tmp / "nowhere"])
        code, plan = self.run_install()
        self.assertEqual(code, 1)
        self.assertTrue(any("no toolbox.yml" in line for line in self.section(plan, "Problems")))

    def test_a_manifest_without_source_or_with_unknown_keys_is_a_problem(self):
        write(self.work / "toolbox.yml", yaml.safe_dump({"target": str(self.team_target), "colour": "blue"}))
        code, plan = self.run_install()
        self.assertEqual(code, 1)
        problems = self.section(plan, "Problems")
        self.assertTrue(any("needs `source`" in line and "unknown key(s): colour" in line for line in problems),
                        problems)
        self.assertEqual([line for line in self.section(plan, "Create links") if "team-workspace" in line], [])


class ToolboxOption(InstallerTest):
    def test_acts_on_the_named_toolbox_only(self):
        code, plan = self.run_install("--toolbox", "work-toolbox", "--apply")
        self.assertEqual(code, 0, plan)
        self.assertIn("Toolboxes: work-toolbox", plan)
        self.assertTrue((self.team_target / ".claude" / "rules" / "tm-conventions.md").is_symlink())
        self.assertFalse((self.home / "rules").exists())
        self.assertFalse((self.ws1 / ".claude" / "skills").exists())

    def test_this_repo_by_path_stands_for_both_its_folders(self):
        code, plan = self.run_install("--toolbox", str(self.repo), "--apply")
        self.assertEqual(code, 0, plan)
        self.assertIn("Toolboxes: claude-toolbox\n", plan)
        self.assertTrue((self.home / "rules" / "style.md").is_symlink())
        self.assertTrue((self.ws1 / ".claude" / "skills" / "always").is_symlink())
        self.assertFalse((self.team_target / ".claude").exists())

    def test_teardown_of_one_toolbox_leaves_the_rest(self):
        self.run_install("--apply")
        self.run_install("--toolbox", str(self.work), "--teardown", "--apply")
        self.assertFalse((self.team_target / ".claude").exists())
        self.assertTrue((self.home / "rules" / "style.md").is_symlink())

    def test_an_unknown_toolbox_exits(self):
        with self.assertRaises(SystemExit) as raised:
            self.run_install("--toolbox", "nowhere")
        self.assertIn("Known toolboxes: claude-toolbox, work-toolbox", str(raised.exception.code))


class RetiredDomains(InstallerTest):
    """An install made when toolboxes held domains: machine.yml's `domains:`, a toolbox's `domains.yml`, and state
    recorded under each domain's name."""

    def old_install(self):
        """Install, then put the state, the work toolbox's manifest and machine.yml back as the old installer left
        them, with the team target a git repo whose exclude block carries the old marker."""
        subprocess.run(["git", "init", "-q", str(self.team_target)], check=True)
        self.run_install("--apply")
        state = self.state()
        for section in state.values():
            for entry in section.values():
                entry["domain"] = {"work-toolbox": "team"}.get(entry["part"], entry.pop("part"))
                entry.pop("part", None)
        (self.repo / ".install-state.json").write_text(json.dumps(state), encoding="utf-8")
        (self.work / "toolbox.yml").unlink()
        write(self.work / "domains.yml", yaml.safe_dump({"ship": "pr", "domains": {
            "team": {"source": "team", "target": str(self.team_target), "prefix": "tm-"},
        }}))
        self.machine(toolboxes=[self.work], workspaces=[self.ws1, self.ws2], domains=["global", "team"])

    def exclude(self) -> str:
        return (self.team_target / ".git" / "info" / "exclude").read_text(encoding="utf-8")

    def test_old_files_are_problems_and_hold_what_they_installed(self):
        self.old_install()
        code, plan = self.run_install("--apply")
        self.assertEqual(code, 1)
        problems = self.section(plan, "Problems")
        self.assertTrue(any("machine.yml: remove `domains:`" in line for line in problems), problems)
        self.assertTrue(any("rename its domains.yml to toolbox.yml" in line for line in problems), problems)
        self.assertEqual(self.section(plan, "Remove stale links"), [])
        self.assertTrue((self.team_target / ".claude" / "rules" / "tm-conventions.md").is_symlink())
        self.assertIn("work-toolbox:team", self.exclude())

    def test_a_toolbox_yml_still_listing_domains_is_held(self):
        self.old_install()
        (self.work / "domains.yml").rename(self.work / "toolbox.yml")
        code, plan = self.run_install()
        self.assertEqual(code, 1)
        self.assertTrue(any("still has `domains:`" in line for line in self.section(plan, "Problems")))
        self.assertEqual(self.section(plan, "Remove stale links"), [])

    def test_migrating_leaves_the_install_untouched(self):
        self.old_install()
        before = self.exclude()
        (self.work / "domains.yml").unlink()
        self.manifest(self.work)
        self.machine(toolboxes=[self.work], workspaces=[self.ws1, self.ws2])
        code, plan = self.run_install()
        self.assertEqual(code, 0, plan)
        for title in ("Create folders", "Create links", "Replace links", "Remove stale links", "Write generated rules",
                      "Remove generated rules", "Update git excludes", "Conflicts, left alone", "Warnings"):
            self.assertEqual(self.section(plan, title), [], title)

        self.run_install("--apply")
        self.assertEqual(self.exclude(), before)
        parts = {entry["part"] for section in self.state().values() for entry in section.values()}
        self.assertEqual(parts, {"global", "workspace", "work-toolbox"})
        self.run_install("--toolbox", "work-toolbox", "--teardown", "--apply")
        self.assertFalse((self.team_target / ".claude").exists())


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

        self.machine(toolboxes=[self.work], workspaces=[self.ws2])
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
        self.machine(toolboxes=[self.work])
        code, plan = self.run_install("--toolbox", "work-toolbox")
        self.assertEqual(code, 0, plan)
        self.assertEqual(self.section(plan, "Create links"), [])
        self.assertEqual(self.section(plan, "Create folders"), [])
        self.assertEqual(self.section(plan, "Already in place"),
                         [str(self.team_target / ".claude" / "rules" / "tm-conventions.md")])

    def test_a_link_into_another_listed_toolbox_is_replaced_not_a_conflict(self):
        old = write(self.work / "team" / ".claude" / "rules" / "tm-old.md")
        self.adopt(old)
        self.machine(toolboxes=[self.work])
        code, plan = self.run_install()
        self.assertEqual(code, 0, plan)
        self.assertEqual(len(self.section(plan, "Replace links")), 1)

    def test_adopted_links_go_when_their_toolbox_is_dropped(self):
        target = self.adopt(self.work / "team" / ".claude" / "rules" / "tm-conventions.md")
        self.machine()
        self.run_install("--apply")
        self.assertFalse(target.is_symlink())
        self.assertFalse((self.team_target / ".claude").exists())


class WorkspaceOption(InstallerTest):
    def test_acts_on_the_named_workspace_only(self):
        code, plan = self.run_install("--workspace", str(self.ws1), "--apply")
        self.assertEqual(code, 0, plan)
        self.assertIn("Toolboxes: claude-toolbox (workspace)", plan)
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

    def test_rejects_a_toolbox_with_nothing_per_workspace(self):
        with self.assertRaises(SystemExit) as raised:
            self.run_install("--workspace", str(self.ws1), "--toolbox", "work-toolbox")
        self.assertIn("acts only on what installs into every workspace", str(raised.exception.code))

    def test_with_this_repo_named_acts_on_its_workspace_folder_only(self):
        code, plan = self.run_install("--workspace", str(self.ws1), "--toolbox", "claude-toolbox", "--apply")
        self.assertEqual(code, 0, plan)
        self.assertTrue((self.ws1 / ".claude" / "skills" / "always").is_symlink())
        self.assertFalse((self.home / "rules").exists())


if __name__ == "__main__":
    unittest.main()
