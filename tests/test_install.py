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

    def written(self, plan: str) -> list:
        """What a run would write: its Create and Update sections."""
        return self.section(plan, "Create") + self.section(plan, "Update")

    def state(self) -> dict:
        return json.loads((self.repo / ".install-state.json").read_text(encoding="utf-8"))

    def assertCopy(self, target: Path, source: Path):
        """A real file (never a link) holding the source's bytes, with the source's executable bit."""
        self.assertTrue(target.is_file() and not target.is_symlink(), f"{target} isn't a copy")
        self.assertEqual(target.read_bytes(), source.read_bytes())
        self.assertEqual(os.access(target, os.X_OK), os.access(source, os.X_OK))


class OtherToolboxes(InstallerTest):
    def test_installs_another_toolbox_whole_against_its_own_repo(self):
        code, plan = self.run_install("--apply")
        self.assertEqual(code, 0, plan)
        self.assertCopy(
            self.team_target / ".claude" / "rules" / "tm-conventions.md",
            self.work / "team" / ".claude" / "rules" / "tm-conventions.md",
        )
        self.assertCopy(self.home / "rules" / "style.md", self.repo / "global" / "rules" / "style.md")
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
        self.assertTrue(any("tm-conventions.md" in line for line in self.section(plan, "Create")))

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
        self.assertEqual([line for line in self.section(plan, "Create") if "team-workspace" in line], [])

    def test_external_files_are_copied_and_a_missing_one_is_a_conflict(self):
        outside = write(self.tmp / "outside" / "shared-rule.md", "# Shared\n")
        self.manifest(self.work, external=[
            {"source": str(outside), "target": ".claude/rules/themis/shared-rule.md"},
            {"source": str(self.tmp / "outside" / "gone.md"), "target": ".claude/rules/themis/gone.md"},
        ])
        code, plan = self.run_install("--apply")
        self.assertEqual(code, 1)
        self.assertCopy(self.team_target / ".claude" / "rules" / "themis" / "shared-rule.md", outside)
        self.assertTrue(any(line.startswith("[missing-source] ") and "gone.md" in line
                            for line in self.section(plan, "Conflicts, left alone")), plan)
        self.assertFalse((self.team_target / ".claude" / "rules" / "themis" / "gone.md").exists())


class ToolboxOption(InstallerTest):
    def test_acts_on_the_named_toolbox_only(self):
        code, plan = self.run_install("--toolbox", "work-toolbox", "--apply")
        self.assertEqual(code, 0, plan)
        self.assertIn("Toolboxes: work-toolbox", plan)
        self.assertTrue((self.team_target / ".claude" / "rules" / "tm-conventions.md").is_file())
        self.assertFalse((self.home / "rules").exists())
        self.assertFalse((self.ws1 / ".claude" / "skills").exists())

    def test_this_repo_by_path_stands_for_both_its_folders(self):
        code, plan = self.run_install("--toolbox", str(self.repo), "--apply")
        self.assertEqual(code, 0, plan)
        self.assertIn("Toolboxes: claude-toolbox\n", plan)
        self.assertTrue((self.home / "rules" / "style.md").is_file())
        self.assertTrue((self.ws1 / ".claude" / "skills" / "always" / "SKILL.md").is_file())
        self.assertFalse((self.team_target / ".claude").exists())

    def test_teardown_of_one_toolbox_leaves_the_rest(self):
        self.run_install("--apply")
        self.run_install("--toolbox", str(self.work), "--teardown", "--apply")
        self.assertFalse((self.team_target / ".claude").exists())
        self.assertTrue((self.home / "rules" / "style.md").is_file())

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
        self.assertEqual(self.section(plan, "Remove"), [])
        self.assertTrue((self.team_target / ".claude" / "rules" / "tm-conventions.md").is_file())
        self.assertIn("work-toolbox:team", self.exclude())

    def test_a_toolbox_yml_still_listing_domains_is_held(self):
        self.old_install()
        (self.work / "domains.yml").rename(self.work / "toolbox.yml")
        code, plan = self.run_install()
        self.assertEqual(code, 1)
        self.assertTrue(any("still has `domains:`" in line for line in self.section(plan, "Problems")))
        self.assertEqual(self.section(plan, "Remove"), [])

    def test_migrating_leaves_the_install_untouched(self):
        self.old_install()
        before = self.exclude()
        (self.work / "domains.yml").unlink()
        self.manifest(self.work)
        self.machine(toolboxes=[self.work], workspaces=[self.ws1, self.ws2])
        code, plan = self.run_install()
        self.assertEqual(code, 0, plan)
        for title in ("Create folders", "Replace links", "Create", "Update", "Remove", "Update git excludes",
                      "Conflicts, left alone", "Warnings"):
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
            self.assertCopy(ws / ".claude" / "skills" / "always" / "SKILL.md",
                            source / "skills" / "always" / "SKILL.md")
            self.assertCopy(ws / ".claude" / "toolbox" / "bin" / "tool.py", source / "toolbox" / "bin" / "tool.py")
            self.assertFalse((ws / ".claude" / "toolbox" / "tests").exists())
            self.assertFalse((ws / "features.yml").exists())
            self.assertEqual([path for path in ws.rglob("*") if path.is_symlink()], [])
        self.assertCopy(self.ws1 / ".claude" / "skills" / "lock-local-services" / "SKILL.md",
                        source / "skills" / "lock-local-services" / "SKILL.md")
        self.assertFalse((self.ws2 / ".claude" / "skills" / "lock-local-services").exists())
        record = self.state()["files"][install.norm(self.ws1 / ".claude/skills/always/SKILL.md")]
        self.assertEqual(record["workspace"], install.norm(self.ws1))

    def test_a_dropped_feature_removes_its_files(self):
        self.run_install("--apply")
        write(self.ws1 / CONFIG, workspace_config("one", []))
        code, plan = self.run_install()
        self.assertEqual(self.section(plan, "Remove"),
                         [str(self.ws1 / ".claude/skills/lock-local-services/SKILL.md")])
        self.assertEqual(self.section(plan, "Create"), [])
        self.run_install("--apply")
        self.assertFalse((self.ws1 / ".claude" / "skills" / "lock-local-services").exists())
        self.assertTrue((self.ws1 / ".claude" / "skills" / "always" / "SKILL.md").is_file())

    def test_unknown_features_and_missing_feature_paths_warn(self):
        write(self.ws2 / CONFIG, workspace_config("two", ["telepathy"]))
        write(self.repo / "workspace" / "features.yml", yaml.safe_dump({
            "services-lock": [".claude/skills/lock-local-services", ".claude/skills/not-yet"],
        }))
        _, plan = self.run_install()
        warnings = self.section(plan, "Warnings")
        self.assertTrue(any("'telepathy' isn't in any features.yml" in line for line in warnings), warnings)
        self.assertTrue(any(".claude/skills/not-yet doesn't exist" in line for line in warnings), warnings)

    def test_a_feature_with_unmet_needs_holds_the_workspace(self):
        write(self.repo / "workspace" / "features.yml", yaml.safe_dump({
            "services-lock": {"paths": [".claude/skills/lock-local-services"], "needs": ["workspace.areas"]},
        }))
        code, plan = self.run_install("--apply")
        self.assertEqual(code, 1)
        problems = self.section(plan, "Problems")
        self.assertTrue(any(str(self.ws1) in line and "services-lock needs workspace.areas" in line
                            and "nothing installed or removed there" in line for line in problems), problems)
        self.assertFalse((self.ws1 / RULE).exists())
        self.assertFalse((self.ws1 / ".claude" / "skills" / "always").exists())
        self.assertTrue((self.ws2 / ".claude" / "skills" / "always" / "SKILL.md").is_file())  # ws2 picks nothing

        raw = yaml.safe_load(workspace_config("one", ["services-lock"]))
        raw["workspace"]["areas"] = {"app": {"start": "docker compose up -d"}}
        write(self.ws1 / CONFIG, yaml.safe_dump(raw))
        code, plan = self.run_install("--apply")
        self.assertEqual(code, 0, plan)
        self.assertTrue((self.ws1 / ".claude" / "skills" / "lock-local-services" / "SKILL.md").is_file())

    def test_unmet_needs_keep_what_a_workspace_had(self):
        self.run_install("--apply")
        write(self.repo / "workspace" / "features.yml", yaml.safe_dump({
            "services-lock": {"paths": [".claude/skills/lock-local-services"], "needs": ["tracker.jira"]},
        }))
        code, plan = self.run_install("--apply")
        self.assertEqual(code, 1)
        self.assertEqual(self.section(plan, "Remove"), [])
        self.assertTrue((self.ws1 / ".claude" / "skills" / "lock-local-services" / "SKILL.md").is_file())

    def test_a_need_that_isnt_a_config_key_warns(self):
        write(self.repo / "workspace" / "features.yml", yaml.safe_dump({
            "services-lock": {"paths": [".claude/skills/lock-local-services"], "needs": ["trackr"]},
        }))
        _, plan = self.run_install()
        self.assertTrue(any("services-lock: needs trackr, which isn't a config key" in line
                            for line in self.section(plan, "Warnings")))

    def test_a_malformed_features_file_exits(self):
        write(self.repo / "workspace" / "features.yml", yaml.safe_dump({"services-lock": {"path": []}}))
        with self.assertRaises(SystemExit) as caught:
            self.run_install()
        self.assertIn("services-lock: unknown key path", str(caught.exception))

    def test_a_workspace_without_vault_or_tracker_installs(self):
        write(self.ws2 / CONFIG, yaml.safe_dump({
            "version": 1, "features": [], "workspace": {"name": "two", "layout": "single"},
            "user": {"handle": "tester", "name": "Tester"},
        }))
        code, plan = self.run_install("--apply")
        self.assertEqual(code, 0, plan)
        self.assertTrue((self.ws2 / ".claude" / "skills" / "always" / "SKILL.md").is_file())
        rule = (self.ws2 / RULE).read_text(encoding="utf-8")
        self.assertNotIn("**Vault:**", rule)
        self.assertNotIn("**Tracker:**", rule)

    def test_a_workspace_without_config_gets_nothing_and_keeps_what_it_had(self):
        self.run_install("--apply")
        (self.ws1 / CONFIG).unlink()
        code, plan = self.run_install("--apply")
        self.assertEqual(code, 1)
        problems = self.section(plan, "Problems")
        self.assertTrue(any("ws1" in line and "/setup-workspace" in line for line in problems), problems)
        self.assertEqual(self.section(plan, "Remove"), [])
        self.assertTrue((self.ws1 / ".claude" / "skills" / "always" / "SKILL.md").is_file())
        self.assertTrue((self.ws1 / RULE).is_file())

    def test_an_invalid_config_is_a_problem(self):
        write(self.ws1 / CONFIG, "version: 1\n")
        code, plan = self.run_install()
        self.assertEqual(code, 1)
        self.assertTrue(any("invalid" in line and "workspace.name: required" in line
                            for line in self.section(plan, "Problems")))
        self.assertFalse(any(str(self.ws1) in line for line in self.section(plan, "Create")))


class GeneratedRule(InstallerTest):
    def test_create_update_stale_and_teardown(self):
        code, plan = self.run_install()
        self.assertIn(f"{self.ws1 / RULE} (new)", self.written(plan))
        self.assertFalse((self.ws1 / RULE).exists())

        self.run_install("--apply")
        text = (self.ws1 / RULE).read_text(encoding="utf-8")
        self.assertIn("`one`", text)
        self.assertFalse((self.ws1 / RULE).is_symlink())
        self.assertEqual(self.state()["files"][install.norm(self.ws1 / RULE)]["hash"], install.digest(text.encode()))

        _, plan = self.run_install()
        self.assertEqual(self.written(plan), [])
        self.assertIn(f"{self.ws1 / RULE} (generated)", self.section(plan, "Unchanged"))

        write(self.ws1 / CONFIG, workspace_config("uno", ["services-lock"]))
        _, plan = self.run_install()
        self.assertEqual(self.written(plan), [f"{self.ws1 / RULE} (changed)"])
        self.run_install("--apply")
        self.assertIn("`uno`", (self.ws1 / RULE).read_text(encoding="utf-8"))

        self.machine(toolboxes=[self.work], workspaces=[self.ws2])
        _, plan = self.run_install()
        self.assertIn(str(self.ws1 / RULE), self.section(plan, "Remove"))
        self.run_install("--apply")
        self.assertFalse((self.ws1 / RULE).exists())
        self.assertFalse((self.ws1 / ".claude" / "skills").exists())
        self.assertTrue((self.ws1 / CONFIG).is_file())

        self.run_install("--teardown", "--apply")
        self.assertFalse((self.ws2 / RULE).exists())
        self.assertEqual(self.state()["files"], {})

    def test_a_file_the_installer_didnt_write_is_a_conflict(self):
        write(self.ws1 / RULE, "# Mine\n")
        code, plan = self.run_install("--apply")
        self.assertEqual(code, 1)
        self.assertTrue(any(line.startswith("[unmanaged] ") for line in self.section(plan, "Conflicts, left alone")))
        self.assertEqual((self.ws1 / RULE).read_text(encoding="utf-8"), "# Mine\n")

    def test_an_unrecorded_identical_file_is_adopted(self):
        self.run_install("--apply")
        state = self.state()
        del state["files"][install.norm(self.ws1 / RULE)]
        (self.repo / ".install-state.json").write_text(json.dumps(state), encoding="utf-8")
        code, plan = self.run_install("--apply")
        self.assertEqual(code, 0, plan)
        self.assertIn(install.norm(self.ws1 / RULE), self.state()["files"])


class WorkspaceRules(InstallerTest):
    """The workspace folder's rules are written into each workspace with a generated note."""

    def setUp(self):
        super().setUp()
        self.source = write(self.repo / "workspace" / ".claude" / "rules" / "house.md", "# House\n\nBe kind.\n")
        self.gated = write(self.repo / "workspace" / ".claude" / "rules" / "locks.md", "# Locks\n")
        write(self.repo / "workspace" / "features.yml", yaml.safe_dump({
            "services-lock": [".claude/skills/lock-local-services", ".claude/rules/locks.md"],
        }))
        self.target = self.ws1 / ".claude" / "rules" / "house.md"
        self.note = f"Generated from `{self.source}` on every toolbox sync; edits here are lost."

    def test_a_rule_installs_as_a_file_with_the_generated_note(self):
        code, plan = self.run_install("--apply")
        self.assertEqual(code, 0, plan)
        self.assertIn(f"{self.target} (new, copied from house.md)", self.written(plan))
        self.assertFalse(self.target.is_symlink())
        text = self.target.read_text(encoding="utf-8")
        self.assertTrue(text.startswith(f"# House\n\n{self.note}"), text)
        self.assertTrue(text.endswith("\n\nBe kind.\n"), text)
        self.assertEqual(self.state()["files"][install.norm(self.target)]["hash"], install.digest(text.encode()))
        self.assertTrue((self.ws1 / ".claude" / "rules" / "locks.md").is_file())
        self.assertFalse((self.ws2 / ".claude" / "rules" / "locks.md").exists())
        self.assertIn("\n/.claude/rules/house.md\n",
                      (self.ws2 / ".git" / "info" / "exclude").read_text(encoding="utf-8"))

        _, plan = self.run_install()
        self.assertIn(f"{self.target} (copied)", self.section(plan, "Unchanged"))
        self.assertEqual(self.written(plan), [])

    def test_the_installers_old_link_is_replaced(self):
        self.run_install("--apply")
        state = self.state()
        legacy = {"dirs": state["dirs"], "links": {}}
        for path in (self.target, self.ws2 / ".claude" / "rules" / "house.md"):
            path.unlink()
            os.symlink(self.source, path)
            legacy["links"][install.norm(path)] = {"target": str(path), "source": str(self.source),
                                                   "part": "workspace", "workspace": install.norm(path.parents[2])}
        (self.repo / ".install-state.json").write_text(json.dumps(legacy), encoding="utf-8")

        code, plan = self.run_install()
        self.assertEqual(code, 0, plan)
        self.assertIn(f"{self.target} (replaces a link, copied from house.md)", self.written(plan))
        self.run_install("--apply")
        self.assertFalse(self.target.is_symlink())
        self.assertIn(self.note, self.target.read_text(encoding="utf-8"))
        self.assertFalse(any(entry.get("link") for entry in self.state()["files"].values()))
        self.assertEqual(self.source.read_text(encoding="utf-8"), "# House\n\nBe kind.\n")  # never written through

    def test_a_source_edit_rewrites_it(self):
        self.run_install("--apply")
        write(self.source, "# House\n\nBe kinder.\n")
        _, plan = self.run_install()
        self.assertIn(f"{self.target} (changed, copied from house.md)", self.written(plan))
        self.run_install("--apply")
        self.assertIn("Be kinder.", self.target.read_text(encoding="utf-8"))

    def test_a_removed_source_prunes_it(self):
        self.run_install("--apply")
        self.source.unlink()
        _, plan = self.run_install()
        self.assertIn(str(self.target), self.section(plan, "Remove"))
        self.run_install("--apply")
        self.assertFalse(self.target.exists())
        self.assertNotIn(install.norm(self.target), self.state()["files"])

    def test_frontmatter_stays_first(self):
        write(self.source, "---\npaths:\n  - \"src/**\"\n---\n\n# House\n\nBe kind.\n")
        self.run_install("--apply")
        text = self.target.read_text(encoding="utf-8")
        self.assertTrue(text.startswith(f"---\npaths:\n  - \"src/**\"\n---\n\n# House\n\n{self.note}"), text)


class GitExcludes(InstallerTest):
    def exclude(self) -> str:
        path = self.ws2 / ".git" / "info" / "exclude"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def test_a_repo_workspace_excludes_files_config_and_rule(self):
        self.run_install("--apply")
        text = self.exclude()
        self.assertIn("# >>> claude-toolbox:workspace (managed by install.py)", text)
        for entry in ("/.claude/toolbox.local.yml", "/.claude/rules/toolbox-config.md",
                      "/.claude/skills/always/SKILL.md", "/.claude/toolbox/bin/tool.py"):
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
    """Links and state left by another toolbox's retired installer are replaced by copies, never conflicts."""

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

    def test_adopted_links_are_replaced_by_copies(self):
        target = self.adopt(self.work / "team" / ".claude" / "rules" / "tm-conventions.md")
        self.machine(toolboxes=[self.work])
        code, plan = self.run_install("--toolbox", "work-toolbox")
        self.assertEqual(code, 0, plan)
        self.assertEqual(self.section(plan, "Create"), [])
        self.assertEqual(self.section(plan, "Create folders"), [])
        self.assertEqual(self.written(plan), [f"{target} (replaces a link, copied from tm-conventions.md)"])
        self.run_install("--toolbox", "work-toolbox", "--apply")
        self.assertCopy(target, self.work / "team" / ".claude" / "rules" / "tm-conventions.md")

    def test_a_link_into_another_listed_toolbox_is_replaced_not_a_conflict(self):
        old = write(self.work / "team" / ".claude" / "rules" / "tm-old.md")
        self.adopt(old)
        self.machine(toolboxes=[self.work])
        code, plan = self.run_install()
        self.assertEqual(code, 0, plan)
        self.assertEqual(len([line for line in self.section(plan, "Update") if "replaces a link" in line]), 1)

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
        self.assertTrue((self.ws1 / ".claude" / "skills" / "always" / "SKILL.md").is_file())
        self.assertFalse((self.ws2 / ".claude" / "skills").exists())
        self.assertFalse((self.home / "rules").exists())

        self.run_install("--apply")
        self.run_install("--workspace", str(self.ws1), "--teardown", "--apply")
        self.assertFalse((self.ws1 / ".claude" / "skills").exists())
        self.assertFalse((self.ws1 / RULE).exists())
        self.assertTrue((self.ws2 / ".claude" / "skills" / "always" / "SKILL.md").is_file())
        self.assertTrue((self.home / "rules" / "style.md").is_file())

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
        self.assertTrue((self.ws1 / ".claude" / "skills" / "always" / "SKILL.md").is_file())
        self.assertFalse((self.home / "rules").exists())


class CopiesWithManifest(InstallerTest):
    """Every install is a copy, recorded in a manifest, and every target is classified against it."""

    def always(self, ws: Path | None = None) -> Path:
        return (ws or self.ws1) / ".claude" / "skills" / "always" / "SKILL.md"

    def source_file(self) -> Path:
        return self.repo / "workspace" / ".claude" / "skills" / "always" / "SKILL.md"

    def link_like_today(self) -> dict:
        """The install as the old installer left it: the `always` skill folder and the tool script linked into the
        repo, recorded in state's `links`; everything else installed as it is now."""
        self.run_install("--apply")
        source = self.repo / "workspace" / ".claude"
        state = self.state()
        legacy = {"dirs": state["dirs"], "links": {}, "generated": {}}
        for ws in (self.ws1, self.ws2):
            folder, script = ws / ".claude" / "skills" / "always", ws / ".claude" / "toolbox" / "bin" / "tool.py"
            for key in [install.norm(folder / "SKILL.md"), install.norm(script)]:
                del state["files"][key]
            shutil_rmtree(folder)
            script.unlink()
            os.symlink(source / "skills" / "always", folder, target_is_directory=True)
            os.symlink(source / "toolbox" / "bin" / "tool.py", script)
            for link, src in ((folder, source / "skills" / "always"), (script, source / "toolbox" / "bin" / "tool.py")):
                legacy["links"][install.norm(link)] = {"target": str(link), "source": str(src), "part": "workspace",
                                                       "workspace": install.norm(ws)}
        for key, entry in state["files"].items():  # the rest, as the old installer recorded it
            if entry["source"] and not entry.get("special"):
                legacy["links"].setdefault(key, {"target": entry["path"], "source": entry["source"],
                                                 "part": entry["part"], **({"workspace": entry["workspace"]}
                                                                           if "workspace" in entry else {})})
            elif entry["source"] or key != install.norm(self.home / install.POINTER_NAME):
                legacy["generated"][key] = {"path": entry["path"], "part": entry["part"],
                                            **({"workspace": entry["workspace"]} if "workspace" in entry else {}),
                                            **({"template": entry["source"]} if entry["source"] else {})}
        (self.home / install.POINTER_NAME).unlink()  # the pointer is new with copies
        (self.repo / ".install-state.json").write_text(json.dumps(legacy), encoding="utf-8")
        return legacy

    def test_first_apply_over_links(self):
        self.link_like_today()
        source = self.repo / "workspace" / ".claude"
        _, plan = self.run_install()
        self.assertEqual(sorted(self.section(plan, "Replace links")),
                         sorted(str(ws / ".claude" / "skills" / "always") for ws in (self.ws1, self.ws2)))
        self.assertTrue(any("(replaces a link, copied from tool.py)" in line for line in self.section(plan, "Update")))

        code, plan = self.run_install("--apply")
        self.assertEqual(code, 0, plan)
        for ws in (self.ws1, self.ws2):
            self.assertCopy(self.always(ws), self.source_file())
            self.assertCopy(ws / ".claude" / "toolbox" / "bin" / "tool.py", source / "toolbox" / "bin" / "tool.py")
            self.assertEqual([path for path in ws.rglob("*") if path.is_symlink()], [])
            entry = self.state()["files"][install.norm(self.always(ws))]
            self.assertEqual(entry["source"], str(self.source_file()))
            self.assertEqual(entry["hash"], install.digest(self.source_file().read_bytes()))
        # never written through the old links
        self.assertEqual(self.source_file().read_text(), "---\nname: always\ndescription: test\n---\n")
        self.assertFalse(any(entry.get("link") for entry in self.state()["files"].values()))

    def test_second_apply_reports_everything_unchanged(self):
        self.link_like_today()
        self.run_install("--apply")
        before = {path: path.stat().st_mtime_ns for path in self.ws1.rglob("*") if path.is_file()}
        code, plan = self.run_install("--apply")
        self.assertEqual(code, 0, plan)
        for title in ("Create folders", "Replace links", "Create", "Update", "Remove", "Update git excludes",
                      "Conflicts, left alone", "Problems", "Errors"):
            self.assertEqual(self.section(plan, title), [], title)
        unchanged = self.section(plan, "Unchanged")
        self.assertIn(f"{self.always(self.ws1)} (copied)", unchanged)
        self.assertIn(f"{self.ws2 / RULE} (generated)", unchanged)
        self.assertEqual({path: path.stat().st_mtime_ns for path in self.ws1.rglob("*") if path.is_file()}, before)

    def test_an_edited_installed_file_is_a_conflict_and_left_alone(self):
        self.run_install("--apply")
        write(self.always(), "---\nname: always\n---\nmy own steps\n")
        code, plan = self.run_install()
        self.assertEqual(code, 1)
        conflicts = self.section(plan, "Conflicts, left alone")
        self.assertEqual(len(conflicts), 1)
        self.assertTrue(conflicts[0].startswith(f"[edited] {self.always()}: edited on disk"), conflicts)
        self.assertEqual(self.written(plan), [])

        write(self.source_file(), "---\nname: always\ndescription: changed upstream\n---\n")
        code, plan = self.run_install("--apply")  # a source change doesn't override the edit either
        self.assertEqual(code, 1)
        self.assertEqual(self.always().read_text(), "---\nname: always\n---\nmy own steps\n")
        self.assertEqual(self.always(self.ws2).read_text(), self.source_file().read_text())  # others still update

    def test_an_unedited_file_updates_when_its_source_changes(self):
        self.run_install("--apply")
        write(self.source_file(), "---\nname: always\ndescription: changed upstream\n---\n")
        code, plan = self.run_install()
        self.assertEqual(code, 0, plan)
        self.assertEqual(sorted(self.section(plan, "Update")),
                         sorted(f"{self.always(ws)} (changed, copied from SKILL.md)" for ws in (self.ws1, self.ws2)))
        self.run_install("--apply")
        self.assertCopy(self.always(), self.source_file())
        self.assertEqual(self.state()["files"][install.norm(self.always())]["hash"],
                         install.digest(self.source_file().read_bytes()))

    def test_editing_back_to_the_source_is_unchanged(self):
        self.run_install("--apply")
        original = self.always().read_bytes()
        write(self.always(), "edited\n")
        write(self.source_file(), "edited\n")  # the source caught up with the edit
        _, plan = self.run_install()
        self.assertEqual(self.section(plan, "Conflicts, left alone"), [])
        self.assertIn(f"{self.always()} (copied)", self.section(plan, "Unchanged"))
        self.assertNotEqual(original, b"edited\n")

    def test_a_file_no_longer_wanted_is_removed_unless_edited(self):
        self.run_install("--apply")
        tool = self.repo / "workspace" / ".claude" / "toolbox" / "bin" / "tool.py"
        write(self.ws1 / ".claude" / "toolbox" / "bin" / "tool.py", "print('mine')\n")
        tool.unlink()
        code, plan = self.run_install()
        self.assertEqual(self.section(plan, "Remove"), [str(self.ws2 / ".claude" / "toolbox" / "bin" / "tool.py")])
        conflicts = self.section(plan, "Conflicts, left alone")
        self.assertEqual(len(conflicts), 1)
        self.assertTrue(conflicts[0].startswith(f"[edited-stale] {self.ws1 / '.claude/toolbox/bin/tool.py'}"),
                        conflicts)
        self.run_install("--apply")
        self.assertFalse((self.ws2 / ".claude" / "toolbox" / "bin" / "tool.py").exists())
        self.assertEqual((self.ws1 / ".claude" / "toolbox" / "bin" / "tool.py").read_text(), "print('mine')\n")
        self.assertIn(install.norm(self.ws1 / ".claude/toolbox/bin/tool.py"), self.state()["files"])

    def test_a_file_the_installer_didnt_write_is_a_conflict_and_a_deleted_one_is_created_again(self):
        write(self.always(), "mine\n")
        code, plan = self.run_install("--apply")
        self.assertEqual(code, 1)
        self.assertTrue(any(line.startswith(f"[unmanaged] {self.always()}")
                            for line in self.section(plan, "Conflicts, left alone")))
        self.assertEqual(self.always().read_text(), "mine\n")
        self.always().unlink()  # the way to take the toolbox's copy back
        code, plan = self.run_install("--apply")
        self.assertEqual(code, 0, plan)
        self.assertCopy(self.always(), self.source_file())

    def test_a_link_to_somewhere_else_is_a_conflict_and_left_alone(self):
        elsewhere = write(self.tmp / "elsewhere.md", "elsewhere\n")
        self.always().parent.mkdir(parents=True)
        os.symlink(elsewhere, self.always())
        code, plan = self.run_install("--apply")
        self.assertEqual(code, 1)
        self.assertTrue(any(line.startswith(f"[foreign] {self.always()}")
                            for line in self.section(plan, "Conflicts, left alone")))
        self.assertTrue(self.always().is_symlink())

    def test_a_folder_in_the_way_and_an_unreadable_file_are_conflicts(self):
        self.always().mkdir(parents=True)  # a folder where SKILL.md goes
        code, plan = self.run_install("--apply")
        self.assertEqual(code, 1)
        self.assertTrue(any(line.startswith(f"[folder] {self.always()}")
                            for line in self.section(plan, "Conflicts, left alone")), plan)
        self.assertTrue(self.always().is_dir())
        self.always().rmdir()
        self.run_install("--apply")
        if os.geteuid() != 0:
            self.always().chmod(0)
            self.addCleanup(self.always().chmod, 0o644)
            _, plan = self.run_install()
            self.assertTrue(any(line.startswith(f"[unreadable] {self.always()}")
                                for line in self.section(plan, "Conflicts, left alone")), plan)

    def test_a_link_that_cant_be_replaced_is_never_written_through(self):
        self.link_like_today()
        real = Path.unlink
        stuck = self.ws1 / ".claude" / "skills" / "always"

        def unlink(path, *args, **kwargs):
            if path == stuck:
                raise OSError("busy")
            return real(path, *args, **kwargs)

        write(self.source_file(), "new text\n")
        with mock.patch.object(Path, "unlink", unlink):
            code, plan = self.run_install("--apply")
        self.assertEqual(code, 1)
        self.assertTrue(any("busy" in line for line in self.section(plan, "Errors")), plan)
        self.assertTrue(stuck.is_symlink())
        self.assertEqual(self.source_file().read_text(), "new text\n")  # as written: nothing went through the link
        self.assertCopy(self.always(self.ws2), self.source_file())  # the other workspace went ahead

    def test_pycache_is_excluded_and_never_blocks_removing_a_folder(self):
        self.run_install("--apply")
        self.assertIn("\n__pycache__/\n", (self.ws2 / ".git" / "info" / "exclude").read_text(encoding="utf-8"))
        cache = self.ws2 / ".claude" / "toolbox" / "bin" / "__pycache__"
        write(cache / "tool.cpython-314.pyc", "x")  # what running an installed script leaves
        write(self.ws1 / CONFIG, workspace_config("one", []))
        self.machine(toolboxes=[self.work], workspaces=[self.ws1])  # ws2 is dropped
        self.run_install("--apply")
        self.assertFalse((self.ws2 / ".claude" / "toolbox").exists())

    def test_the_executable_bit_is_kept(self):
        tool = self.repo / "workspace" / ".claude" / "toolbox" / "bin" / "tool.py"
        tool.chmod(0o755)
        self.run_install("--apply")
        self.assertTrue(os.access(self.ws1 / ".claude" / "toolbox" / "bin" / "tool.py", os.X_OK))
        self.assertFalse(os.access(self.always(), os.X_OK))
        (self.ws1 / ".claude" / "toolbox" / "bin" / "tool.py").chmod(0o644)  # lost, say, by a copy elsewhere
        _, plan = self.run_install()
        self.assertTrue(any("tool.py (changed, copied from tool.py)" in line and str(self.ws1) in line
                            for line in self.section(plan, "Update")), plan)
        self.run_install("--apply")
        self.assertTrue(os.access(self.ws1 / ".claude" / "toolbox" / "bin" / "tool.py", os.X_OK))

    def test_a_failed_write_leaves_the_file_and_no_temp_behind(self):
        self.run_install("--apply")
        before = self.always().read_text()
        write(self.source_file(), "new text\n")
        real = os.replace

        def replace(source, destination):
            if Path(destination).name == "SKILL.md":
                raise OSError("disk full")
            real(source, destination)

        with mock.patch.object(install.os, "replace", side_effect=replace):
            code, plan = self.run_install("--apply")
        self.assertEqual(code, 1)
        self.assertTrue(any("disk full" in line for line in self.section(plan, "Errors")))
        self.assertEqual(self.always().read_text(), before)
        self.assertEqual([path.name for path in self.always().parent.iterdir()], ["SKILL.md"])
        self.assertEqual(self.state()["files"][install.norm(self.always())]["hash"], install.digest(before.encode()))

    def test_state_from_before_the_manifest_is_adopted(self):
        """`generated` entries carry no hash: an unchanged file is recorded as it is, a changed one is rewritten as
        it always was."""
        self.run_install("--apply")
        state = self.state()
        legacy = {"dirs": state["dirs"], "links": {}, "generated": {}}
        for key, entry in state["files"].items():
            legacy["generated"][key] = {"path": entry["path"], "part": entry["part"],
                                        **({"workspace": entry["workspace"]} if "workspace" in entry else {}),
                                        **({"template": entry["source"]} if entry["source"] else {})}
        (self.repo / ".install-state.json").write_text(json.dumps(legacy), encoding="utf-8")
        write(self.ws1 / RULE, "old rule\n")
        _, plan = self.run_install()
        self.assertEqual([line for line in self.written(plan)], [f"{self.ws1 / RULE} (changed)"])
        self.run_install("--apply")
        self.assertTrue(all(entry["hash"] for entry in self.state()["files"].values()))

    def test_the_toolbox_repo_is_recorded_for_scripts_installed_as_copies(self):
        self.run_install("--apply")
        pointer = self.home / install.POINTER_NAME
        self.assertEqual(pointer.read_text(encoding="utf-8"), f"{self.repo}\n")
        self.run_install("--toolbox", str(self.work), "--apply")
        self.assertTrue(pointer.is_file())  # a run that leaves the global folder alone keeps it
        self.run_install("--teardown", "--apply")
        self.assertFalse(pointer.exists())

    def test_preview_changes_nothing(self):
        self.run_install("--apply")
        write(self.source_file(), "changed upstream\n")
        before = (self.repo / ".install-state.json").read_text(), self.always().read_text()
        self.run_install()
        self.assertEqual(((self.repo / ".install-state.json").read_text(), self.always().read_text()), before)


def shutil_rmtree(path: Path) -> None:
    subprocess.run(["rm", "-rf", str(path)], check=True)


if __name__ == "__main__":
    unittest.main()
