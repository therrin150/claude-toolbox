"""Tests for publish.py and branches.py after a PR was squash-merged on GitHub and its branch deleted: nothing is
rebased or pushed again, and the branch is still deletable.

Run from the repo root:

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

BIN = Path(__file__).resolve().parents[1] / "workspace" / ".claude" / "toolbox" / "bin"
sys.path.insert(0, str(BIN))

import branches  # noqa: E402
import publish  # noqa: E402
from gitgh import OK, PRECHECK_FAILED, Checkout  # noqa: E402

BRANCH = "tester/ABC-1-thing"


def git(path: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=path, check=True, capture_output=True, text=True).stdout.strip()


def clone(origin: Path, path: Path) -> Path:
    git(origin.parent, "clone", "-q", str(origin), str(path))
    for key, value in (("user.name", "Tester"), ("user.email", "tester@example.com"), ("commit.gpgsign", "false"),
                       ("core.hooksPath", "/dev/null")):
        git(path, "config", key, value)
    return path


def commit(path: Path, name: str, text: str, message: str) -> str:
    (path / name).write_text(text)
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", message)
    return git(path, "rev-parse", "HEAD")


class Ctx(publish.Context):
    """A Context over one checkout with the config publish.py reads, without loading a workspace."""

    def __init__(self, path: Path):
        self.cfg = SimpleNamespace(
            pr={"required": True, "wait_for_ci": True, "review_required": True, "merge": "squash"},
            default_branch="master", handle="tester", announce=None, vault_dir=path / "vault",
            get=lambda key: "{handle}/{key}-{slug}", has=lambda feature: False)
        self.wid, self.args, self.dry_run, self.record = "ABC-1", SimpleNamespace(), False, False
        self.publish_dir = path / ".publish"
        self.checkouts = [Checkout("work", path)]


class SquashMergedTest(unittest.TestCase):
    """The task branch's PR #7 was squash-merged into master on GitHub, which then deleted the branch."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.origin = root / "origin.git"
        git(root, "init", "-q", "--bare", "-b", "master", str(self.origin))
        self.work = clone(self.origin, root / "work")
        commit(self.work, "a.txt", "one\n", "base")
        git(self.work, "push", "-q", "origin", "HEAD:master")
        git(self.work, "checkout", "-q", "-b", BRANCH)
        self.head = commit(self.work, "a.txt", "two\n", "ABC-1 change")
        git(self.work, "push", "-q", "-u", "origin", BRANCH)

        other = clone(self.origin, root / "other")
        git(other, "merge", "-q", "--squash", f"origin/{BRANCH}")
        git(other, "commit", "-q", "-m", "ABC-1 change (#7)")
        git(other, "push", "-q", "origin", "master")
        git(other, "push", "-q", "origin", "--delete", BRANCH)
        git(self.work, "fetch", "-q", "--prune")

        self.pr = {"number": 7, "url": "https://github.com/o/r/pull/7", "state": "MERGED", "headRefOid": self.head}
        for module in (publish, branches):
            patcher = mock.patch.object(module, "prs_for_branch", return_value=[self.pr])
            patcher.start()
            self.addCleanup(patcher.stop)

    def remote_branch(self) -> str:
        return git(self.origin, "branch", "--list", BRANCH)

    def test_push_skips_a_merged_branch(self):
        out, code = publish.step_push(Ctx(self.work))
        self.assertEqual(code, OK, out)
        self.assertEqual(out["skipped"], [{"repo": "work", "merged_pr": 7}])
        self.assertEqual((out["rebased"], out["pushed"]), ([], []))
        self.assertEqual(git(self.work, "rev-parse", "HEAD"), self.head)
        self.assertEqual(self.remote_branch(), "")

    def test_push_refuses_commits_after_the_merge(self):
        commit(self.work, "b.txt", "new\n", "ABC-1 follow-up")
        out, code = publish.step_push(Ctx(self.work))
        self.assertEqual(code, PRECHECK_FAILED, out)
        self.assertIn("commits after PR #7 merged; they belong on a new branch", out["precheck"][0]["problems"])
        self.assertEqual(self.remote_branch(), "")

    def test_open_pr_skips_a_merged_branch(self):
        out, code = publish.step_open_pr(Ctx(self.work))
        self.assertEqual(code, OK, out)
        self.assertEqual(out["skipped"], "every PR is merged; nothing to open")

    def test_run_only_cleans_up(self):
        cleanup = mock.Mock(return_value=({"next": "release"}, OK))
        with mock.patch.object(publish, "step_cleanup", cleanup):
            out, code = publish.step_run(Ctx(self.work))
        self.assertEqual(code, OK, out)
        self.assertEqual(list(out["steps"]), ["cleanup"])
        self.assertEqual((out["skipped"], out["next"]), ("every PR is merged; cleanup only", "release"))
        self.assertEqual(self.remote_branch(), "")

    def test_branch_rebased_onto_its_squash_commit_is_deletable(self):
        git(self.work, "rebase", "-q", "origin/master")
        sha = git(self.work, "rev-parse", "HEAD")
        self.assertEqual(sha, git(self.work, "rev-parse", "origin/master"))
        entry = branches.classify(Ctx(self.work).cfg, Checkout("work", self.work), BRANCH, sha)
        self.assertNotIn("keep", entry)
        self.assertEqual(entry["pr"], 7)


if __name__ == "__main__":
    unittest.main()
