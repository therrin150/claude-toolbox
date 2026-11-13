"""Tests for `toolbox.py features`, on a temporary workspace, home folder and toolbox (a copy of install.py with this
repo's core and features folders linked in), never this machine's machine.yml, install state or `~/.claude`.

Run from the repo root:

    python3 -m unittest discover -s core/workspace/.claude/toolbox/tests -p test_features.py
"""

from __future__ import annotations

import json
import os
import shutil
import site
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import yaml

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))

from toolbox_tree import REPO  # noqa: E402

sys.path.insert(0, str(REPO / "core" / "workspace" / ".claude" / "toolbox" / "lib"))

import features as feature_lib  # noqa: E402

CONFIG = Path(".claude/toolbox.local.yml")
WORKSPACE_CONFIG = """\
version: 1
features: [worktrees, git, java]  # asked
workspace:
  name: ws
  layout: single
user:
  handle: jdoe
  name: Jordan
vault:
  path: vault
tracker:
  kind: none
"""
MACHINE = "toolboxes: []\nworkspaces:\n  - {ws}\nfeatures:\n  - discussion  # the design skill\n  - rule-shell\n"


class FeaturesTest(unittest.TestCase):
    """A toolbox, a home folder (`~/.claude` for the global features) and one single-repo workspace, installed and
    clean: global `discussion` and `rule-shell`, and workspace `worktrees`, `git` and `java`."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)

        self.toolbox = self.tmp / "toolbox"
        self.toolbox.mkdir()
        shutil.copy(REPO / "install.py", self.toolbox / "install.py")
        for folder in ("core", "features"):
            (self.toolbox / folder).symlink_to(REPO / folder)
        (self.toolbox / "toolbox.yml").write_text("ship: direct\n")

        self.home = self.tmp / "home"
        (self.home / ".claude").mkdir(parents=True)
        self.ws = self.tmp / "ws"
        for folder in ("vault/Tasks", "vault/Discussions"):
            (self.ws / folder).mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.ws)], check=True)
        (self.ws / CONFIG).parent.mkdir(parents=True, exist_ok=True)
        (self.ws / CONFIG).write_text(WORKSPACE_CONFIG)
        self.machine_file = self.toolbox / "machine.yml"
        self.machine_file.write_text(MACHINE.format(ws=self.ws))

        self.env = {
            **{k: v for k, v in os.environ.items() if not k.startswith(("TOOLBOX_", "CLAUDE_CODE_"))},
            "HOME": str(self.home),
            "PYTHONUSERBASE": site.getuserbase(),  # PyYAML may be a user-site install, found through the real home
            "TOOLBOX_INSTALLER": str(self.toolbox / "install.py"),
            "CLAUDE_CODE_SESSION_ID": "this-session",
            "TOOLBOX_LOCKS": str(self.tmp / "locks"),
        }
        proc = subprocess.run([sys.executable, str(self.toolbox / "install.py"), "--apply"], capture_output=True,
                              text=True, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    # Helpers

    def toolbox_py(self, *args: str) -> subprocess.CompletedProcess:
        script = self.ws / ".claude" / "toolbox" / "bin" / "toolbox.py"
        self.assertTrue(script.is_file() and not script.is_symlink())  # an installed copy
        return subprocess.run([sys.executable, str(script), *args], cwd=self.ws, capture_output=True, text=True,
                              env=self.env)

    def listing(self) -> dict:
        proc = self.toolbox_py("features", "--json")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return {(f["name"], f["scope"]): {p["where"]: p for p in f["installs"]}
                for f in json.loads(proc.stdout)["features"]}

    def run_json(self, *args: str) -> tuple[int, dict]:
        proc = self.toolbox_py("features", *args)
        return proc.returncode, json.loads(proc.stdout)

    def skill(self, name: str) -> Path:
        return self.ws / ".claude" / "skills" / name / "SKILL.md"

    def record(self, name: str, status: str) -> None:
        (self.ws / "vault" / "Tasks" / f"{name}.md").write_text(
            f"---\ntype: story\nkey:\nsummary: {name}\nstatus: {status}\nowner: claude\n---\n\n# {name}\n")

    def session(self, project: str, uuid: str, minutes_ago: int) -> None:
        folder = self.home / ".claude" / "projects" / project
        folder.mkdir(parents=True, exist_ok=True)
        transcript = folder / f"{uuid}.jsonl"
        transcript.write_text("{}\n")
        stamp = time.time() - minutes_ago * 60
        os.utime(transcript, (stamp, stamp))

    @property
    def mangled(self) -> str:
        return "".join(c if c.isalnum() else "-" for c in str(self.ws))


class Listing(FeaturesTest):
    def test_lists_scope_where_installed_and_what_drifted(self):
        self.skill("java-conventions").write_text("edited by hand\n")
        (self.home / ".claude" / "skills" / "discussion" / "SKILL.md").write_text("edited by hand\n")
        listed = self.listing()
        place = str(self.ws).replace(str(Path.home()), "~")
        self.assertEqual(listed[("java", "workspace")][place]["state"], "drifted")
        self.assertEqual(listed[("java", "workspace")][place]["drift"], {"edited": 1, "outdated": 0, "missing": 0})
        self.assertEqual(listed[("git", "workspace")][place]["state"], "in-sync")
        self.assertTrue(listed[("git", "workspace")][place]["installed"])
        self.assertEqual(listed[("angular", "workspace")][place]["state"], "off")
        (home_place,) = listed[("discussion", "global")]
        self.assertEqual(listed[("discussion", "global")][home_place]["state"], "drifted")
        self.assertEqual(listed[("rule-shell", "global")][home_place]["state"], "in-sync")

    def test_the_table_shows_scope_where_and_the_drift(self):
        self.skill("java-conventions").write_text("edited by hand\n")
        out = self.toolbox_py("features").stdout
        lines = {tuple(line.split()[:2]): line for line in out.splitlines()}
        self.assertIn("SCOPE", out.splitlines()[0])
        java = lines[("java", "workspace")]
        self.assertIn("drifted (1 edited)", java)
        self.assertIn("in-sync", lines[("git", "workspace")])
        self.assertIn("global", lines[("rule-shell", "global")])

    def test_needs_no_workspace(self):
        script = self.ws / ".claude" / "toolbox" / "bin" / "toolbox.py"
        proc = subprocess.run([sys.executable, str(script), "features"], cwd=self.tmp, capture_output=True, text=True,
                              env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("rule-shell", proc.stdout)


class Disable(FeaturesTest):
    def test_the_preview_names_a_task_in_progress_and_removes_nothing(self):
        self.record("add-the-thing", "in-progress")
        self.record("done-thing", "done")
        code, out = self.run_json("disable", "java", "--scope", "workspace")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["concurrent"]["tasks"], [{"task": "add-the-thing", "summary": "add-the-thing"}])
        self.assertTrue(any(path.endswith("java-conventions/SKILL.md") for path in out["preview"]["Remove"]))
        self.assertFalse(out["applied"])
        self.assertTrue(self.skill("java-conventions").is_file())
        self.assertIn("java", (self.ws / CONFIG).read_text())

    def test_applying_without_the_confirmation_is_refused_and_writes_nothing(self):
        self.record("add-the-thing", "in-progress")
        before = (self.ws / CONFIG).read_text()
        code, out = self.run_json("disable", "java", "--scope", "workspace", "--apply")
        self.assertEqual(code, 3, out)
        self.assertIn("--confirm-concurrent", out["refused"])
        self.assertEqual(out["concurrent"]["tasks"][0]["task"], "add-the-thing")
        self.assertFalse(out["applied"])
        self.assertEqual((self.ws / CONFIG).read_text(), before)
        self.assertTrue(self.skill("java-conventions").is_file())

    def test_applying_with_the_confirmation_edits_the_config_keeping_its_comment_and_syncs(self):
        code, out = self.run_json("disable", "java", "--scope", "workspace", "--apply", "--confirm-concurrent")
        self.assertEqual(code, 0, out)
        self.assertTrue(out["applied"])
        self.assertIn("features: [worktrees, git]  # asked\n", (self.ws / CONFIG).read_text())
        self.assertFalse(self.skill("java-conventions").exists())
        self.assertTrue(self.skill("git-commit").is_file())
        self.assertEqual(self.toolbox_py("config", "validate").returncode, 0)

    def test_the_preview_lists_worktrees_held_locks_and_other_sessions_only(self):
        (self.ws / ".worktrees" / "RMGT-1").mkdir(parents=True)
        lock = self.tmp / "locks" / "ws" / "stack"
        lock.mkdir(parents=True)
        (lock / "owner.json").write_text(
            json.dumps({"task": "RMGT-1", "manual": True, "expires": "2999-01-01T00:00:00+00:00"}))
        expired = self.tmp / "locks" / "ws" / "old"
        expired.mkdir()
        (expired / "owner.json").write_text(json.dumps({"expires": "2000-01-01T00:00:00+00:00"}))
        self.session(self.mangled, "this-session", 1)
        self.session(self.mangled, "other-session", 5)
        self.session(self.mangled, "stale-session", 300)
        self.session(self.mangled + "--worktrees-RMGT-1", "worktree-session", 10)
        self.session(self.mangled + "2", "sibling-workspace-session", 2)
        _, out = self.run_json("disable", "java", "--scope", "workspace")
        concurrent = out["concurrent"]
        self.assertEqual(concurrent["worktrees"], ["RMGT-1"])
        self.assertEqual(concurrent["locks"], [{"name": "stack", "task": "RMGT-1", "manual": True}])
        self.assertEqual([s["session"] for s in concurrent["sessions"]], ["other-session", "worktree-session"])
        self.assertEqual(concurrent["tasks"], [])

    def test_an_unreadable_task_record_is_named_not_passed_over(self):
        (self.ws / "vault" / "Tasks" / "broken.md").write_text("---\n: [unclosed\n---\n")
        _, out = self.run_json("disable", "java", "--scope", "workspace")
        self.assertEqual(out["concurrent"]["tasks"], [])
        self.assertEqual(len(out["concurrent"]["tasks_not_checked"]), 1)
        self.assertTrue(out["concurrent"]["tasks_not_checked"][0].startswith("broken.md"))

    def test_a_global_disable_lists_sessions_anywhere(self):
        self.session("-some-other-project", "elsewhere", 3)
        code, out = self.run_json("disable", "rule-shell", "--scope", "global")
        self.assertEqual(code, 0, out)
        self.assertEqual([s["session"] for s in out["concurrent"]["sessions"]], ["elsewhere"])
        self.assertTrue(any(path.endswith("rules/shell.md") for path in out["preview"]["Remove"]), out["preview"])

    def test_a_global_disable_edits_machine_yml_and_removes_the_files(self):
        shell = self.home / ".claude" / "rules" / "shell.md"
        self.assertTrue(shell.is_file())
        code, out = self.run_json("disable", "rule-shell", "--scope", "global", "--apply", "--confirm-concurrent")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.machine_file.read_text(),
                         MACHINE.format(ws=self.ws).replace("  - rule-shell\n", ""))
        self.assertFalse(shell.exists())
        self.assertTrue((self.home / ".claude" / "skills" / "discussion" / "SKILL.md").is_file())

    def test_a_feature_another_picked_feature_requires_is_refused(self):
        code, out = self.run_json("disable", "worktrees", "--scope", "workspace")
        self.assertEqual(code, 2)
        self.assertIn("worktrees is required by git", out["errors"][0])
        code, out = self.run_json("disable", "worktrees", "git", "--scope", "workspace")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["features"], ["worktrees", "git"])

    def test_a_feature_that_is_already_off_is_a_no_op(self):
        code, out = self.run_json("disable", "angular", "--scope", "workspace", "--apply", "--confirm-concurrent")
        self.assertEqual(code, 0, out)
        self.assertEqual((out["features"], out["already"], out["applied"]), ([], ["angular"], False))


class Enable(FeaturesTest):
    def test_previews_then_writes_the_config_and_installs(self):
        code, out = self.run_json("enable", "angular", "bruno", "--scope", "workspace")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["features"], ["angular", "bruno"])
        self.assertTrue(any("angular-conventions/SKILL.md" in path for path in out["preview"]["Create"]))
        self.assertNotIn("concurrent", out)
        self.assertEqual((self.ws / CONFIG).read_text(), WORKSPACE_CONFIG)
        self.assertFalse(self.skill("angular-conventions").exists())

        code, out = self.run_json("enable", "angular", "bruno", "--scope", "workspace", "--apply")
        self.assertEqual(code, 0, out)
        self.assertIn("features: [worktrees, git, java, angular, bruno]  # asked\n", (self.ws / CONFIG).read_text())
        self.assertTrue(self.skill("angular-conventions").is_file())
        self.assertEqual(self.listing()[("angular", "workspace")][str(self.ws).replace(str(Path.home()), "~")]["state"],
                         "in-sync")

    def test_a_global_enable_writes_machine_yml_as_a_block_item(self):
        code, out = self.run_json("enable", "rule-obsidian", "--scope", "global", "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.machine_file.read_text(), MACHINE.format(ws=self.ws) + "  - rule-obsidian\n")
        self.assertTrue((self.home / ".claude" / "rules" / "obsidian.md").is_file())

    def test_a_feature_that_requires_one_that_is_not_picked_is_refused(self):
        (self.ws / CONFIG).write_text(WORKSPACE_CONFIG.replace("[worktrees, git, java]", "[java]"))
        code, out = self.run_json("enable", "git", "--scope", "workspace", "--apply")
        self.assertEqual(code, 2)
        self.assertIn("git requires worktrees", out["errors"][0])
        self.assertIn("features: [java]", (self.ws / CONFIG).read_text())

    def test_a_feature_needing_config_the_workspace_lacks_is_refused(self):
        (self.ws / CONFIG).write_text(
            WORKSPACE_CONFIG.replace("vault:\n  path: vault\ntracker:\n  kind: none\n", ""))
        code, out = self.run_json("enable", "obsidian", "--scope", "workspace")
        self.assertEqual(code, 2)
        self.assertIn("obsidian needs", out["errors"][0])

    def test_unknown_and_wrong_scope_features_are_refused(self):
        code, out = self.run_json("enable", "nope", "--scope", "workspace")
        self.assertEqual((code, "no such feature: nope" in out["errors"][0]), (2, True))
        code, out = self.run_json("enable", "rule-shell", "--scope", "workspace")
        self.assertEqual(code, 2)
        self.assertIn("a global feature, not a workspace one", out["errors"][0])


class EditFeatures(unittest.TestCase):
    """The text edit alone: comments and the list's style stay."""

    def test_a_one_line_list_keeps_its_comment_and_order(self):
        text = "version: 1\nfeatures: [a, 'b', c]  # asked\nx: 1\n"
        self.assertEqual(feature_lib.edit_features(text, ["d"], ["b"]),
                         "version: 1\nfeatures: [a, c, d]  # asked\nx: 1\n")

    def test_a_block_list_keeps_comments_and_indent(self):
        text = "toolboxes: []\nfeatures:\n    - a  # one\n    # note\n    - b\nother: 1\n"
        self.assertEqual(feature_lib.edit_features(text, ["z"], ["a"]),
                         "toolboxes: []\nfeatures:\n    # note\n    - b\n    - z\nother: 1\n")

    def test_a_missing_or_empty_list_is_made(self):
        self.assertEqual(feature_lib.edit_features("toolboxes: []\n", ["z"], []), "toolboxes: []\nfeatures:\n  - z\n")
        self.assertEqual(feature_lib.edit_features("features:\nother: 1\n", ["z"], []), "features:\n  - z\nother: 1\n")
        self.assertEqual(feature_lib.edit_features("features: []\n", ["z"], []), "features: [z]\n")

    def test_a_list_spanning_lines_in_flow_style_is_refused(self):
        with self.assertRaises(feature_lib.FeaturesError):
            feature_lib.edit_features("features: [a,\n  b]\n", ["z"], [])


if __name__ == "__main__":
    unittest.main()
