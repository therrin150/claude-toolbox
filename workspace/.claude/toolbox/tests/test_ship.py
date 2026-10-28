"""Tests for the global ship-toolbox-change script, against a temporary toolbox repo with a local bare remote and the
fake gh. Run from the repo root:

    python3 -m unittest discover -s workspace/.claude/toolbox/tests
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TESTS = Path(__file__).resolve().parent
SHIP = TESTS.parents[3] / "global" / "skills" / "ship-toolbox-change" / "scripts"
sys.path.insert(0, str(SHIP))

import ship  # noqa: E402

INSTALL = """\
import pathlib
pathlib.Path("install-plan.txt").write_text("== Create links (1)\\n  ~/.claude/skills/new-skill\\n\\n"
                                            "== Already in place (3)\\n  ~/.claude/CLAUDE.md\\n")
"""
MESSAGE = "add a skill\n\nThe toolbox lacked it.\n"


def sh(cwd: Path, *args: str) -> str:
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class ShipCase(unittest.TestCase):
    manifest = "ship: pr\nbranch_prefix: tester\nsource: glue\ntarget: ~/somewhere\n"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp)
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(f"#!/bin/sh\nexec {sys.executable} {TESTS / 'fake_gh.py'} \"$@\"\n")
        gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
        self.bare = self.tmp / "remotes" / "toolbox.git"
        self.state = self.tmp / "gh-state.json"
        patcher = mock.patch.dict(os.environ, {
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "GH_FAKE_STATE": str(self.state), "GH_FAKE_DEFAULT": "main",
            "GH_FAKE_REMOTES": json.dumps({"remotes/toolbox": str(self.bare)}),
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Tester", "GIT_AUTHOR_EMAIL": "t@example.test",
            "GIT_COMMITTER_NAME": "Tester", "GIT_COMMITTER_EMAIL": "t@example.test",
        })
        patcher.start()
        self.addCleanup(patcher.stop)
        sh(self.tmp, "git", "init", "--bare", "-b", "main", str(self.bare))
        self.repo = self.tmp / "toolbox"
        self.repo.mkdir()
        sh(self.repo, "git", "init", "-b", "main")
        (self.repo / "toolbox.yml").write_text(self.manifest)
        (self.repo / "install.py").write_text(INSTALL)
        (self.repo / ".gitignore").write_text("install-plan.txt\n")
        sh(self.repo, "git", "add", "-A")
        sh(self.repo, "git", "commit", "-m", "initial")
        sh(self.repo, "git", "remote", "add", "origin", str(self.bare))
        sh(self.repo, "git", "push", "-u", "origin", "main")
        sh(self.repo, "git", "remote", "set-head", "origin", "main")

    def cli(self, *argv: str, repo: Path | None = None) -> tuple[int, dict]:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = ship.main([*argv, "--repo-dir", str(repo or self.repo)])
        return code, json.loads(buffer.getvalue())

    def message(self, text: str = MESSAGE) -> str:
        path = self.tmp / "message.txt"
        path.write_text(text)
        return str(path)

    def gh_state(self) -> dict:
        return json.loads(self.state.read_text()) if self.state.exists() else {"prs": [], "calls": []}

    def remote_subject(self, ref: str = "main") -> str:
        return sh(self.tmp, "git", "--git-dir", str(self.bare), "log", "-1", "--format=%s", f"refs/heads/{ref}")


class TestPrMode(ShipCase):
    def test_branches_commits_opens_merges_and_cleans_up(self):
        (self.repo / "skill.md").write_text("x\n")
        code, out = self.cli("run", "--message-file", self.message())
        self.assertEqual(code, 0, out)
        self.assertEqual(out["precheck"]["mode"], "pr")
        self.assertEqual(out["commit"]["created_branch"], "tester/add-a-skill")
        self.assertEqual(out["pr"]["title"], "add a skill")
        self.assertEqual(out["pr"]["created"], True)
        self.assertEqual(self.gh_state()["prs"][0]["state"], "MERGED")
        self.assertEqual(self.remote_subject(), "add a skill")
        self.assertEqual(out["cleanup"]["remote_branch_deleted"], True)
        self.assertEqual(out["cleanup"]["local_branch_deleted"], True)
        self.assertEqual(sh(self.repo, "git", "branch", "--show-current"), "main")
        self.assertEqual(sh(self.repo, "git", "rev-parse", "HEAD"), sh(self.repo, "git", "rev-parse", "origin/main"))
        self.assertEqual(sh(self.tmp, "git", "--git-dir", str(self.bare), "branch", "--list", "tester/*"), "")
        self.assertEqual(out["cleanup"]["install_preview"], {"Create links": ["~/.claude/skills/new-skill"]})

    def test_a_glue_toolbox_previews_with_claude_toolboxs_installer(self):
        sh(self.repo, "git", "rm", "-q", "install.py")
        sh(self.repo, "git", "commit", "-m", "no installer")
        sh(self.repo, "git", "push")
        installer = self.tmp / "claude-toolbox"
        installer.mkdir()
        (installer / "install.py").write_text(INSTALL)
        (self.repo / "skill.md").write_text("x\n")
        with mock.patch.object(ship, "CLAUDE_TOOLBOX", installer):
            code, out = self.cli("run", "--message-file", self.message())
        self.assertEqual(code, 0, out)
        self.assertEqual(out["cleanup"]["install_preview"], {"Create links": ["~/.claude/skills/new-skill"]})

    def test_the_default_when_toolbox_yml_has_no_ship_key(self):
        (self.repo / "toolbox.yml").write_text("source: glue\ntarget: ~/somewhere\n")
        sh(self.repo, "git", "commit", "-am", "no ship key")
        sh(self.repo, "git", "push")
        (self.repo / "skill.md").write_text("x\n")
        code, out = self.cli("precheck")
        self.assertEqual((out["precheck"]["mode"], out["precheck"]["new_branch"]), ("pr", True))

    def test_nothing_to_ship(self):
        code, out = self.cli("precheck")
        self.assertEqual(code, 2)
        self.assertIn("nothing to ship", out["precheck"]["problems"][0])

    def test_uncommitted_changes_need_a_message(self):
        (self.repo / "skill.md").write_text("x\n")
        code, out = self.cli("run")
        self.assertEqual(code, 2)
        self.assertIn("--message-file", out["precheck"]["problems"][0])

    def test_a_trailer_is_refused_before_anything_changes(self):
        (self.repo / "skill.md").write_text("x\n")
        code, out = self.cli("run", "--message-file", self.message(MESSAGE + "\nCo-Authored-By: A <a@b.test>\n"))
        self.assertEqual(code, 3)
        self.assertIn("trailer", out["error"])
        self.assertEqual(sh(self.repo, "git", "log", "-1", "--format=%s"), "initial")

    def test_an_open_pr_is_reused(self):
        sh(self.repo, "git", "switch", "-c", "tester/mine")
        (self.repo / "skill.md").write_text("x\n")
        sh(self.repo, "git", "add", "-A")
        sh(self.repo, "git", "commit", "-m", "mine\n\nBody.")
        sh(self.repo, "git", "push", "-u", "origin", "tester/mine")
        self.state.write_text(json.dumps({"next": 8, "calls": [], "ci": {}, "prs": [{
            "number": 7, "repo": "remotes/toolbox", "head": "tester/mine", "state": "OPEN",
            "url": "https://example.test/remotes/toolbox/pull/7",
            "headRefOid": sh(self.repo, "git", "rev-parse", "HEAD")}]}))
        code, out = self.cli("run")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["pr"]["created"], False)
        self.assertEqual(len(self.gh_state()["prs"]), 1)
        self.assertEqual(self.gh_state()["prs"][0]["state"], "MERGED")
        self.assertEqual(self.remote_subject(), "mine")

    def test_not_a_toolbox(self):
        (self.repo / "toolbox.yml").unlink()
        sh(self.repo, "git", "commit", "-am", "drop it")
        code, out = self.cli("precheck")
        self.assertEqual(code, 2)
        self.assertIn("not a toolbox repo", out["error"])

    def test_the_old_manifest_name_says_to_rename_it(self):
        sh(self.repo, "git", "mv", "toolbox.yml", "domains.yml")
        sh(self.repo, "git", "commit", "-m", "old name")
        code, out = self.cli("precheck")
        self.assertEqual(code, 2)
        self.assertIn("rename it to toolbox.yml", out["error"])

    def test_ships_from_a_worktree_and_removes_it(self):
        worktree = self.tmp / "wt"
        sh(self.repo, "git", "worktree", "add", "-b", "tester/from-worktree", str(worktree), "origin/main")
        (worktree / "skill.md").write_text("x\n")
        code, out = self.cli("run", "--message-file", self.message(), repo=worktree)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["cleanup"]["worktree_removed"], str(worktree))
        self.assertFalse(worktree.exists())
        self.assertEqual(sh(self.repo, "git", "branch", "--list", "tester/*"), "")
        self.assertEqual(sh(self.repo, "git", "rev-parse", "HEAD"), sh(self.repo, "git", "rev-parse", "origin/main"))


class TestDirectMode(ShipCase):
    manifest = "ship: direct\n"

    def test_commits_and_pushes_to_the_default_branch(self):
        (self.repo / "skill.md").write_text("x\n")
        code, out = self.cli("run", "--message-file", self.message())
        self.assertEqual(code, 0, out)
        self.assertEqual(out["precheck"]["mode"], "direct")
        self.assertEqual(out["push"]["pushed_to"], "main")
        self.assertEqual(self.remote_subject(), "add a skill")
        self.assertEqual(self.gh_state()["calls"], [])  # no PR, so gh is never called
        self.assertEqual(out["cleanup"]["install_preview"], {"Create links": ["~/.claude/skills/new-skill"]})

    def test_rebases_when_the_remote_moved(self):
        clone = self.tmp / "clone"
        sh(self.tmp, "git", "clone", str(self.bare), str(clone))
        (clone / "other.md").write_text("x\n")
        sh(clone, "git", "add", "-A")
        sh(clone, "git", "commit", "-m", "someone else")
        sh(clone, "git", "push")
        (self.repo / "skill.md").write_text("x\n")
        code, out = self.cli("run", "--message-file", self.message())
        self.assertEqual(code, 0, out)
        self.assertIn("rebased_onto", out["push"])
        log = sh(self.tmp, "git", "--git-dir", str(self.bare), "log", "--format=%s", "-2", "refs/heads/main")
        self.assertEqual(log.splitlines(), ["add a skill", "someone else"])

    def test_commits_already_made_are_pushed(self):
        (self.repo / "skill.md").write_text("x\n")
        sh(self.repo, "git", "add", "-A")
        sh(self.repo, "git", "commit", "-m", "already committed\n\nBody.")
        code, out = self.cli("run")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.remote_subject(), "already committed")


if __name__ == "__main__":
    unittest.main()
