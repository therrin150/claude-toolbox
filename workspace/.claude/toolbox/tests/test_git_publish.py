"""Tests for lib/gitgh.py, bin/worktrees.py, bin/branches.py and bin/publish.py, against temporary git repos with a
local bare remote each. `gh` and tasks.py are fakes (fake_gh.py, fake_tasks.py); nothing touches a network.

Run from the repo root:

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

import yaml

TOOLBOX = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLBOX / "lib"))
sys.path.insert(0, str(TOOLBOX / "bin"))

import branches  # noqa: E402
import config  # noqa: E402
import gitgh  # noqa: E402
import publish  # noqa: E402
import worktrees  # noqa: E402

TEMPLATE = "Describe the change.\n\n# Checklist\n- [ ] Included the ticket number\n- [ ] Added tests\n"
MESSAGE = "DM-1 add the thing\n\nThe thing was missing, so add it.\n"


def sh(cwd: Path, *args: str) -> str:
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class Workbench(unittest.TestCase):
    """A scratch workspace per test: `layout` is single or nested, with a bare remote per repo and a fake gh."""

    layout = "single"
    repo_names = ("api", "web")
    pr = {}
    features = ["tasks", "worktrees", "pr"]
    announce = True

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.root = self.tmp / "ws"
        self.root.mkdir()
        self.remotes = {}
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(f"#!/bin/sh\nexec {sys.executable} {TESTS / 'fake_gh.py'} \"$@\"\n")
        gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
        self.state = self.tmp / "gh-state.json"
        self.tasks_log = self.tmp / "tasks.log"
        patcher = mock.patch.dict(os.environ, {
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "GH_FAKE_STATE": str(self.state),
            "GH_FAKE_DEFAULT": "main",
            "TASKS_LOG": str(self.tasks_log),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Tester", "GIT_AUTHOR_EMAIL": "t@example.test",
            "GIT_COMMITTER_NAME": "Tester", "GIT_COMMITTER_EMAIL": "t@example.test",
        })
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(config.ENV_WORKSPACE, None)
        self.addCleanup(os.chdir, os.getcwd())
        self.make_workspace()
        os.chdir(self.root)

    # Construction

    def make_repo(self, path: Path, remote_name: str) -> None:
        bare = self.tmp / "remotes" / f"{remote_name}.git"
        sh(self.tmp, "git", "init", "--bare", "-b", "main", str(bare))
        path.mkdir(parents=True, exist_ok=True)
        sh(path, "git", "init", "-b", "main")
        (path / ".github").mkdir()
        (path / ".github" / "pull_request_template.md").write_text(TEMPLATE)
        (path / "README.md").write_text("hello\n")
        sh(path, "git", "add", "-A")
        sh(path, "git", "commit", "-m", "initial")
        sh(path, "git", "remote", "add", "origin", str(bare))
        sh(path, "git", "push", "-u", "origin", "main")
        self.remotes[f"remotes/{remote_name}"] = str(bare)
        exclude = path / ".git" / "info" / "exclude"
        exclude.write_text(".worktrees/\n.claude/\nvault/Tasks/.publish/\n")

    def make_workspace(self) -> None:
        if self.layout == "single":
            self.make_repo(self.root, "demo")
            (self.root / "vault").mkdir()
            (self.root / "vault" / "note.md").write_text("a note\n")
            sh(self.root, "git", "add", "-A")
            sh(self.root, "git", "commit", "-m", "add the vault")
            sh(self.root, "git", "push")
        else:
            for name in self.repo_names:
                self.make_repo(self.root / name, name)
        (self.root / "vault").mkdir(exist_ok=True)
        raw = {
            "version": 1, "features": self.features,
            "workspace": {"name": "demo", "layout": self.layout, "default_branch": "main"},
            "user": {"handle": "tester", "name": "Tester"},
            "vault": {"path": "vault"},
            "tracker": {"kind": "vault", "key_prefix": "DM"},
            "git": {"org": "acme"},
            "pr": {"required": True, "wait_for_ci": True, **self.pr},
        }
        if self.announce:
            raw["pr"]["announce"] = {"channel": "C123"}
        cfg_path = self.root / ".claude" / "toolbox.local.yml"
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(yaml.safe_dump(raw))
        stub = self.root / ".claude" / "toolbox" / "bin"
        stub.mkdir(parents=True)
        shutil.copy(TESTS / "fake_tasks.py", stub / "tasks.py")
        os.environ["GH_FAKE_REMOTES"] = json.dumps(self.remotes)

    # Running the scripts

    def cli(self, module, *argv: str) -> tuple[int, dict]:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = module.main(list(argv))
        return code, json.loads(buffer.getvalue())

    def gh_state(self) -> dict:
        return json.loads(self.state.read_text()) if self.state.exists() else {"prs": [], "calls": [], "ci": {}}

    def edit_gh(self, change) -> None:
        state = self.gh_state()
        change(state)
        self.state.write_text(json.dumps(state))

    def tasks_calls(self) -> list[list[str]]:
        if not self.tasks_log.exists():
            return []
        return [json.loads(line) for line in self.tasks_log.read_text().splitlines()]

    # The task

    def remote(self, repo: str = "api") -> str:
        return self.remotes[f"remotes/{'demo' if self.layout == 'single' else repo}"]

    def repos_arg(self) -> list[str]:
        return ["--repos", "api"] if self.layout == "nested" else []

    def worktree(self, wid: str, repo: str = "api") -> Path:
        base = self.root / ".worktrees" / wid
        return base / repo if self.layout == "nested" else base

    def start(self, wid: str = "DM-1", desc: str = "add-the-thing") -> dict:
        args = ["create", wid, "--desc", desc]
        if self.layout == "nested":
            args += ["--repos", ",".join(self.repo_names)]
        code, out = self.cli(worktrees, *args)
        self.assertEqual(code, 0, out)
        return out

    def change(self, wid: str = "DM-1", repo: str = "api", name: str = "feature.txt") -> Path:
        path = self.worktree(wid, repo)
        (path / name).write_text(f"{repo} change\n")
        return path

    def approve(self, wid: str = "DM-1", message: str = MESSAGE, sentence: str = "adds the thing") -> Path:
        publish_dir = self.root / "vault" / "Tasks" / ".publish" / wid
        publish_dir.mkdir(parents=True, exist_ok=True)
        if self.layout == "single":
            (publish_dir / "commit.txt").write_text(message)
        else:
            for name in self.repo_names:
                (publish_dir / f"{name}.txt").write_text(message)
        (publish_dir / "announce.txt").write_text(sentence + "\n")
        return publish_dir

    def publish(self, step: str, *extra: str, wid: str = "DM-1") -> tuple[int, dict]:
        return self.cli(publish, step, wid, *extra)

    def run_all(self, *extra: str, wid: str = "DM-1") -> tuple[int, dict]:
        return self.publish("run", "--interval", "0", "--grace", "0", *extra, wid=wid)


class TestGitgh(Workbench):
    def test_repos_single(self):
        cfg = config.load()
        self.assertEqual([(c.name, c.path) for c in gitgh.repos(cfg)], [("demo", self.root)])

    def test_branch_names(self):
        cfg = config.load()
        self.assertEqual(gitgh.branch_for(cfg, "DM-1", "add-the-thing"), "tester/DM-1-add-the-thing")
        self.assertEqual(gitgh.branch_for(cfg, "fix-login"), "tester/fix-login")
        self.assertEqual(gitgh.branch_for(cfg, "fix-login", "short"), "tester/short")
        with self.assertRaises(gitgh.Failed):
            gitgh.branch_for(cfg, "DM-1")
        with self.assertRaises(gitgh.Failed):
            gitgh.check_work_id("../escape")

    def test_branch_patterns(self):
        patterns = gitgh.branch_patterns(config.load())
        matches = lambda name: any(p.match(name) for p in patterns)  # noqa: E731
        self.assertTrue(matches("tester/DM-1-add-the-thing"))
        self.assertTrue(matches("tester/fix-login"))
        self.assertFalse(matches("other/DM-1-add-the-thing"))
        self.assertFalse(matches("main"))

    def test_gh_repo_comes_from_origin(self):
        co = gitgh.repos(config.load())[0]
        self.assertEqual(co.gh_repo, "remotes/demo")
        sh(self.root, "git", "remote", "set-url", "origin", "git@github.com:acme/demo.git")
        self.assertEqual(co.gh_repo, "acme/demo")
        sh(self.root, "git", "remote", "set-url", "origin", "https://github.com/acme/demo")
        self.assertEqual(co.gh_repo, "acme/demo")


class TestGitghNested(Workbench):
    layout = "nested"

    def test_repos_discovers_children(self):
        (self.root / "docs").mkdir()  # not a repo
        self.assertEqual([c.name for c in gitgh.repos(config.load())], ["api", "web"])

    def test_resolve_by_name_and_path(self):
        cfg = config.load()
        self.assertEqual(gitgh.resolve(cfg, "web").path, self.root / "web")
        self.assertEqual(gitgh.resolve(cfg, str(self.root / "api")).name, "api")
        with self.assertRaises(gitgh.Failed):
            gitgh.resolve(cfg, "nope")


class WorktreeTests:
    def test_create_list_remove(self):
        out = self.start()
        self.assertEqual({r["status"] for r in out["results"]}, {"created"})
        self.assertEqual(out["branch"], "tester/DM-1-add-the-thing")
        path = self.worktree("DM-1")
        self.assertEqual(sh(path, "git", "branch", "--show-current"), "tester/DM-1-add-the-thing")
        code, out = self.cli(worktrees, "list")
        self.assertEqual(code, 0)
        self.assertEqual({r["branch"] for r in out["results"]}, {"tester/DM-1-add-the-thing"})
        self.assertEqual(out["results"][0]["ahead_of_default"], 0)
        code, out = self.cli(worktrees, "remove", "DM-1")
        self.assertEqual(code, 0, out)
        self.assertFalse((self.root / ".worktrees" / "DM-1").exists())
        # The branch survives the removal.
        self.assertIn("tester/DM-1-add-the-thing", sh(self.root if self.layout == "single" else self.root / "api",
                                                       "git", "branch", "--list", "tester/*"))

    def test_create_again_says_exists(self):
        self.start()
        out = self.start()
        self.assertEqual({r["status"] for r in out["results"]}, {"exists"})

    def test_branches_from_the_latest_default(self):
        repo = self.root if self.layout == "single" else self.root / "api"
        clone = self.tmp / "clone"
        sh(self.tmp, "git", "clone", self.remote(), str(clone))
        (clone / "newer.txt").write_text("newer\n")
        sh(clone, "git", "add", "-A")
        sh(clone, "git", "commit", "-m", "newer")
        sh(clone, "git", "push")
        self.start()
        self.assertTrue((self.worktree("DM-1") / "newer.txt").exists())
        self.assertFalse((repo / "newer.txt").exists())

    def test_remove_refuses_uncommitted_and_untracked(self):
        self.start()
        path = self.change()
        code, out = self.cli(worktrees, "remove", "DM-1")
        self.assertEqual(code, 1)
        refused = [r for r in out["results"] if r["status"] == "refused"]
        self.assertEqual(len(refused), 1)
        self.assertIn("uncommitted or untracked", refused[0]["reason"])
        self.assertTrue(path.exists())
        self.assertTrue((path / "feature.txt").exists())

    def test_key_needs_a_description(self):
        code, out = self.cli(worktrees, "create", "DM-2", *(["--repos", "api"] if self.layout == "nested" else []))
        self.assertEqual(code, 1)
        self.assertIn("description", out["error"])

    def test_keyless_slug_branch(self):
        args = ["create", "fix-login"] + (["--repos", "api"] if self.layout == "nested" else [])
        code, out = self.cli(worktrees, *args)
        self.assertEqual(out["branch"], "tester/fix-login")

    def test_resumes_remote_branch(self):
        self.start()
        self.change()
        self.approve()
        self.assertEqual(self.publish("commit", *self.repos_arg())[0], 0)
        self.assertEqual(self.publish("push", *self.repos_arg())[0], 0)
        self.cli(worktrees, "remove", "DM-1", *self.repos_arg())
        repo = self.root if self.layout == "single" else self.root / "api"
        sh(repo, "git", "branch", "-D", "tester/DM-1-add-the-thing")
        out = self.cli(worktrees, "create", "DM-1", "--desc", "add-the-thing", *self.repos_arg())[1]
        self.assertEqual(out["results"][0]["status"], "from-remote")


class ChangesTests:
    def set_areas(self, areas: dict) -> None:
        path = self.root / ".claude" / "toolbox.local.yml"
        raw = yaml.safe_load(path.read_text())
        raw["workspace"]["areas"] = areas
        path.write_text(yaml.safe_dump(raw))

    def test_changes_count_committed_uncommitted_and_untracked_without_staging(self):
        self.start()
        path = self.worktree("DM-1")
        (path / "README.md").write_text("hello\nmore\n")
        sh(path, "git", "commit", "-am", "more")
        (path / "README.md").write_text("changed\nmore\n")
        (path / "new.txt").write_text("a\nb\nc")
        code, out = self.cli(worktrees, "changes", "DM-1")
        self.assertEqual(code, 0, out)
        first = out["checkouts"][0]
        self.assertEqual(sorted(first["files"]), ["README.md", "new.txt"])
        self.assertEqual(first["diff"], "2 files, +5 −1")
        self.assertEqual(sh(path, "git", "diff", "--cached", "--name-only"), "")


class TestWorktreesSingle(ChangesTests, WorktreeTests, Workbench):
    layout = "single"

    def test_changes_map_files_to_area_folders(self):
        self.set_areas({"app": {"unit": "true", "files": ["*.java"]}, "web": {"unit": "true", "files": ["*.ts"]},
                        "docs": {"unit": "true"}, "*": {"format": "true", "files": ["*.sh"]}})
        self.start()
        path = self.worktree("DM-1")
        for name in ("app/src/A.java", "app/notes.md", "web/x.ts", "vault/n.md", "run.sh"):
            (path / name).parent.mkdir(parents=True, exist_ok=True)
            (path / name).write_text("x\n")
        out = self.cli(worktrees, "changes", "DM-1")[1]
        self.assertEqual(out["areas"], [f"app={path / 'app'}", f"demo={path}", f"web={path / 'web'}"])
        self.assertEqual(out["checkouts"][0]["areas"], ["app", "demo", "web"])
        self.assertEqual(out["skipped"], [])
        (path / "app" / "src" / "A.java").unlink()
        (path / "run.sh").unlink()
        out = self.cli(worktrees, "changes", "DM-1")[1]
        self.assertEqual(out["areas"], [f"web={path / 'web'}"])
        self.assertEqual([(s["area"], s["reason"]) for s in out["skipped"]],
                         [("app", "no changed file matches *.java"), ("demo", "no changed file matches *.sh")])

    def test_changes_without_a_star_area_ignore_files_outside_the_areas(self):
        self.set_areas({"app": {"unit": "true"}})
        self.start()
        (self.worktree("DM-1") / "vault").mkdir(exist_ok=True)
        (self.worktree("DM-1") / "vault" / "n.md").write_text("x\n")
        out = self.cli(worktrees, "changes", "DM-1")[1]
        self.assertEqual((out["areas"], out["skipped"]), ([], []))
        self.assertEqual(out["checkouts"][0]["diff"], "1 file, +1 −0")

    def test_worktree_is_one_folder_per_task(self):
        self.start()
        self.assertTrue((self.root / ".worktrees" / "DM-1" / ".git").exists())
        self.assertEqual(len(gitgh.task_worktrees(config.load(), "DM-1")), 1)


class TestWorktreesNested(ChangesTests, WorktreeTests, Workbench):
    layout = "nested"

    def test_changes_are_per_repo_with_star_globs(self):
        self.set_areas({"*": {"unit": "true", "files": ["*.java", "pom.xml"]}, "web": {"files": ["*.ts"]}})
        self.start()
        (self.worktree("DM-1", "api") / "sub").mkdir()
        (self.worktree("DM-1", "api") / "sub" / "pom.xml").write_text("<p/>\n")
        (self.worktree("DM-1", "web") / "README.md").write_text("docs only\n")
        out = self.cli(worktrees, "changes", "DM-1")[1]
        self.assertEqual(out["areas"], [f"api={self.worktree('DM-1', 'api')}"])
        self.assertEqual([(s["area"], s["files"]) for s in out["skipped"]], [("web", 1)])
        self.assertEqual([c["repo"] for c in out["checkouts"]], ["api", "web"])

    def test_changes_without_worktrees_fail(self):
        code, out = self.cli(worktrees, "changes", "DM-9")
        self.assertEqual(code, 1)
        self.assertIn("no worktrees", out["error"])

    def test_one_worktree_per_repo(self):
        self.start()
        for name in ("api", "web"):
            self.assertTrue((self.root / ".worktrees" / "DM-1" / name / ".git").exists())
        code, out = self.cli(worktrees, "remove", "DM-1", "--repos", "api")
        self.assertEqual(code, 0)
        self.assertTrue((self.root / ".worktrees" / "DM-1" / "web").exists())

    def test_repos_required(self):
        code, out = self.cli(worktrees, "create", "fix-login")
        self.assertEqual(code, 1)
        self.assertIn("--repos", out["error"])


class TestCommit(Workbench):
    def test_commits_with_the_message_byte_for_byte(self):
        self.start()
        path = self.change()
        self.approve()
        code, out = self.publish("commit")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["committed"][0]["subject"], "DM-1 add the thing")
        self.assertEqual(sh(path, "git", "log", "-1", "--format=%B").rstrip("\n"), MESSAGE.rstrip("\n"))
        self.assertEqual(sh(path, "git", "status", "--porcelain"), "")

    def test_a_clean_tree_is_not_an_error(self):
        self.start()
        self.approve()
        code, out = self.publish("commit")
        self.assertEqual(code, 0)
        self.assertEqual(out["precheck"][0]["action"], "clean")

    def test_no_body_halts_with_nothing_changed(self):
        self.start()
        path = self.change()
        self.approve(message="DM-1 add the thing\n")
        code, out = self.publish("commit")
        self.assertEqual(code, 2)
        self.assertIn("no body", out["precheck"][0]["problems"][0])
        self.assertIn("feature.txt", sh(path, "git", "status", "--porcelain"))

    def test_trailer_is_refused(self):
        self.start()
        self.change()
        self.approve(message=MESSAGE + "\nCo-Authored-By: Someone <s@example.test>\n")
        code, out = self.publish("commit")
        self.assertEqual(code, 2)
        self.assertIn("trailer", out["precheck"][0]["problems"][0])

    def test_missing_message(self):
        self.start()
        self.change()
        code, out = self.publish("commit")
        self.assertEqual(code, 2)
        self.assertIn("no commit message", out["precheck"][0]["problems"][0])

    def test_not_on_the_default_branch_or_a_stray_branch(self):
        self.start()
        path = self.change()
        self.approve()
        sh(path, "git", "switch", "-c", "stray-branch")
        code, out = self.publish("commit")
        self.assertEqual(code, 2)
        self.assertIn("doesn't match git.branch", out["precheck"][0]["problems"][0])

    def test_warns_when_subject_lacks_the_branch_key(self):
        self.start()
        self.change()
        self.approve(message="add the thing\n\nBody.\n")
        code, out = self.publish("commit", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("DM-1", out["precheck"][0]["warning"])

    def test_suspicious_files_are_flagged(self):
        self.start()
        path = self.change()
        (path / "scratch.txt").write_text("x\n")
        (path / "target").mkdir()
        (path / "target" / "out.class").write_text("x\n")
        self.approve()
        code, out = self.publish("commit", "--dry-run")
        self.assertEqual(code, 0)
        self.assertEqual(sorted(out["precheck"][0]["suspicious"]), ["scratch.txt", "target/out.class"])

    def test_dry_run_changes_nothing(self):
        self.start()
        path = self.change()
        self.approve()
        code, out = self.publish("commit", "--dry-run")
        self.assertTrue(out["dry_run"])
        self.assertIn("feature.txt", sh(path, "git", "status", "--porcelain"))

    def test_message_dir_and_file_overrides(self):
        self.start()
        path = self.change()
        elsewhere = self.tmp / "msg.txt"
        elsewhere.write_text("DM-1 elsewhere\n\nBody.\n")
        code, out = self.publish("commit", "--message-file", str(elsewhere))
        self.assertEqual(code, 0, out)
        self.assertEqual(sh(path, "git", "log", "-1", "--format=%s"), "DM-1 elsewhere")


class TestPush(Workbench):
    def committed(self) -> Path:
        self.start()
        path = self.change()
        self.approve()
        self.assertEqual(self.publish("commit")[0], 0)
        return path

    def advance_default(self, name: str, text: str = "x\n", message: str = "advance") -> None:
        clone = self.tmp / f"clone-{name}"
        sh(self.tmp, "git", "clone", self.remotes["remotes/demo"], str(clone))
        target = clone / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        sh(clone, "git", "add", "-A")
        sh(clone, "git", "commit", "-m", message)
        sh(clone, "git", "push")

    def test_pushes_and_sets_upstream(self):
        path = self.committed()
        code, out = self.publish("push")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["pushed"][0]["branch"], "tester/DM-1-add-the-thing")
        self.assertEqual(sh(path, "git", "rev-parse", "HEAD"),
                         sh(self.root, "git", "--git-dir", self.remotes["remotes/demo"], "rev-parse",
                            "refs/heads/tester/DM-1-add-the-thing"))
        self.assertEqual(self.publish("push")[1]["pushed"], [])  # nothing left to push

    def test_rebases_onto_new_code_commits(self):
        path = self.committed()
        self.advance_default("other.txt")
        code, out = self.publish("push")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["rebased"][0]["brought_in"], ["advance"])
        self.assertTrue((path / "other.txt").exists())
        self.assertEqual(sh(path, "git", "rev-list", "--count", "origin/main..HEAD"), "1")

    def test_vault_only_commits_do_not_rebase(self):
        path = self.committed()
        self.advance_default("vault/note.md", "edited\n", "edit the note")
        code, out = self.publish("push")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["rebased"], [])
        self.assertEqual(sh(path, "git", "rev-list", "--count", "HEAD..origin/main"), "1")

    def test_conflict_halts_and_a_rerun_resumes(self):
        path = self.committed()
        self.advance_default("feature.txt", "someone else\n", "same file")
        code, out = self.publish("push")
        self.assertEqual(code, 4, out)
        self.assertEqual(out["conflicts"][0]["files"], ["feature.txt"])
        self.assertIn("rebase-merge", gitgh.operation_in_progress(path)[0])
        # Run again while the rebase is still open: refused, nothing changes.
        code, out = self.publish("push")
        self.assertEqual(code, 2)
        self.assertIn("rebase", out["precheck"][0]["problems"][0])
        # The session resolves and continues; the rerun pushes.
        (path / "feature.txt").write_text("resolved\n")
        sh(path, "git", "add", "feature.txt")
        subprocess.run(["git", "rebase", "--continue"], cwd=path, check=True, capture_output=True,
                       env={**os.environ, "GIT_EDITOR": "true"})
        code, out = self.publish("push")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["pushed"][0]["repo"], "demo")

    def test_a_rebased_branch_already_on_the_remote_is_pushed_with_lease(self):
        path = self.committed()
        self.assertEqual(self.publish("push")[0], 0)
        self.advance_default("other.txt")
        code, out = self.publish("push")
        self.assertEqual(code, 0, out)
        self.assertEqual(sh(path, "git", "rev-parse", "HEAD"),
                         sh(self.root, "git", "--git-dir", self.remotes["remotes/demo"], "rev-parse",
                            "refs/heads/tester/DM-1-add-the-thing"))

    def test_uncommitted_changes_block(self):
        path = self.committed()
        (path / "late.txt").write_text("x\n")
        code, out = self.publish("push")
        self.assertEqual(code, 2)
        self.assertIn("not clean", out["precheck"][0]["problems"][0])


class TestOpenPr(Workbench):
    def pushed(self):
        self.start()
        self.change()
        self.approve()
        self.assertEqual(self.publish("commit")[0], 0)
        self.assertEqual(self.publish("push")[0], 0)

    def test_opens_from_the_template_and_prints_the_announcement(self):
        self.pushed()
        code, out = self.publish("open-pr")
        self.assertEqual(code, 0, out)
        pr = out["prs"][0]
        self.assertEqual(pr["status"], "created")
        self.assertEqual(pr["title"], "DM-1 add the thing")
        stored = self.gh_state()["prs"][0]
        self.assertEqual(stored["body"], "The thing was missing, so add it.\n\n# Checklist\n"
                                         "- [x] Included the ticket number\n- [x] Added tests\n")
        self.assertEqual(out["announce"], {"mcp_server": "slack", "channel": "C123",
                                           "text": f"{pr['url']} - adds the thing"})
        self.assertEqual(self.tasks_calls(), [["append", "DM-1", "prs", pr["url"]],
                                              ["set", "DM-1", "status=review", "stage=awaiting-ci"]])

    def test_a_rerun_reuses_the_open_pr_and_does_not_reannounce(self):
        self.pushed()
        first = self.publish("open-pr")[1]
        code, out = self.publish("open-pr")
        self.assertEqual(code, 0)
        self.assertEqual(out["prs"][0]["status"], "exists")
        self.assertEqual(out["prs"][0]["url"], first["prs"][0]["url"])
        self.assertIsNone(out["announce"])
        self.assertEqual(len(self.gh_state()["prs"]), 1)
        self.assertEqual(self.publish("open-pr", "--announce-existing")[1]["announce"]["text"].split(" - ")[0],
                         first["prs"][0]["url"])

    def test_unpushed_branch_is_a_problem(self):
        self.start()
        self.change()
        self.approve()
        self.publish("commit")
        code, out = self.publish("open-pr")
        self.assertEqual(code, 2)
        self.assertIn("isn't pushed", out["problems"][0])
        self.assertEqual(self.gh_state()["prs"], [])

    def test_missing_announcement_text_is_a_problem_unless_skipped(self):
        self.pushed()
        (self.root / "vault" / "Tasks" / ".publish" / "DM-1" / "announce.txt").unlink()
        code, out = self.publish("open-pr")
        self.assertEqual(code, 2)
        self.assertIn("announcement", out["problems"][0])
        code, out = self.publish("open-pr", "--no-announce")
        self.assertEqual(code, 0)
        self.assertNotIn("announce", out)

    def test_a_failed_create_stops_and_a_retry_opens_once(self):
        self.pushed()
        with mock.patch.dict(os.environ, {"GH_FAKE_CREATE_FAILS": "1"}):
            code, out = self.publish("open-pr")
            self.assertEqual(code, 3)
            self.assertEqual(out["not_opened"], ["demo"])
            code, out = self.publish("open-pr")
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.gh_state()["prs"]), 1)

    def test_reviewer_is_passed_through(self):
        self.pushed()
        self.publish("open-pr", "--reviewer", "someone")
        self.assertEqual(self.gh_state()["prs"][0]["reviewer"], "someone")

    def test_template_without_a_checklist_heading_and_no_template(self):
        self.start()
        path = self.change()
        (path / ".github" / "pull_request_template.md").write_text("Just prose.\n")
        sh(path, "git", "add", "-A")
        self.approve()
        self.publish("commit")
        self.publish("push")
        self.publish("open-pr")
        self.assertEqual(self.gh_state()["prs"][0]["body"], "The thing was missing, so add it.\n\nJust prose.\n")

    def test_ticket_box_stays_unticked_without_a_key(self):
        self.start("fix-login", "fix-login")
        self.change("fix-login")
        self.approve("fix-login", message="fix login\n\nThe login was broken.\n")
        self.assertEqual(self.publish("commit", wid="fix-login")[0], 0)
        self.publish("push", wid="fix-login")
        self.publish("open-pr", wid="fix-login")
        body = self.gh_state()["prs"][0]["body"]
        self.assertIn("- [ ] Included the ticket number", body)
        self.assertIn("- [x] Added tests", body)


class TestOpenPrNested(Workbench):
    layout = "nested"

    def test_a_batch_announces_once_in_a_thread(self):
        self.start()
        for name in ("api", "web"):
            self.change(repo=name)
        self.approve()
        self.assertEqual(self.publish("commit")[0], 0)
        self.assertEqual(self.publish("push")[0], 0)
        code, out = self.publish("open-pr")
        self.assertEqual(code, 0, out)
        self.assertEqual([p["repo"] for p in out["prs"]], ["api", "web"])
        self.assertEqual(out["announce"]["text"], "2 pull requests (in-thread) - adds the thing")
        self.assertEqual(out["announce"]["thread_text"].splitlines()[0], f"- api - {out['prs'][0]['url']}")
        calls = self.tasks_calls()
        self.assertEqual(calls[0][:3], ["append", "DM-1", "prs"])
        self.assertEqual(len(calls[0]), 5)


class TestWaitCi(Workbench):
    def with_pr(self) -> int:
        self.start()
        self.change()
        self.approve()
        for step in ("commit", "push", "open-pr"):
            self.assertEqual(self.publish(step)[0], 0)
        return self.gh_state()["prs"][0]["number"]

    def rollup(self, number: int, *sequence: list) -> None:
        self.edit_gh(lambda s: s["ci"].update({str(number): list(sequence)}))

    def run_wait(self, *extra: str) -> tuple[int, dict]:
        return self.publish("wait-ci", "--interval", "0", *extra)

    def test_waits_for_pending_then_green(self):
        number = self.with_pr()
        self.rollup(number, [{"name": "build", "status": "IN_PROGRESS"}],
                    [{"name": "build", "status": "COMPLETED", "conclusion": "SUCCESS"}])
        code, out = self.run_wait()
        self.assertEqual(code, 0, out)
        self.assertEqual(out["checks"]["demo"]["state"], "green")

    def test_a_failed_check_exits_3(self):
        number = self.with_pr()
        self.rollup(number, [{"name": "build", "status": "COMPLETED", "conclusion": "FAILURE"}])
        code, out = self.run_wait()
        self.assertEqual(code, 3)
        self.assertIn("build: FAILURE", out["error"])

    def test_commit_statuses_count_too(self):
        number = self.with_pr()
        self.rollup(number, [{"context": "ci/legacy", "state": "PENDING"}],
                    [{"context": "ci/legacy", "state": "SUCCESS"}])
        self.assertEqual(self.run_wait()[0], 0)

    def test_timeout_exits_4(self):
        number = self.with_pr()
        self.rollup(number, [{"name": "build", "status": "QUEUED"}])
        code, out = self.run_wait("--timeout", "0")
        self.assertEqual(code, 4)
        self.assertEqual(out["pending"], ["demo"])

    def test_no_checks_count_as_green_after_the_grace(self):
        self.with_pr()
        code, out = self.run_wait("--grace", "0")
        self.assertEqual(code, 0)
        self.assertEqual(out["checks"]["demo"]["state"], "no-checks")

    def test_no_checks_within_the_grace_keeps_waiting(self):
        self.with_pr()
        code, out = self.run_wait("--grace", "3600", "--timeout", "0")
        self.assertEqual(code, 4)


class TestMerge(Workbench):
    def with_pr(self, *, message: str = MESSAGE) -> int:
        self.start()
        self.change()
        self.approve(message=message)
        for step in ("commit", "push", "open-pr"):
            self.assertEqual(self.publish(step)[0], 0)
        number = self.gh_state()["prs"][0]["number"]
        self.edit_gh(lambda s: s["ci"].update({str(number): [[{"name": "build", "status": "COMPLETED",
                                                                "conclusion": "SUCCESS"}]]}))
        return number

    def test_merges_with_the_configured_method(self):
        self.with_pr()
        code, out = self.publish("merge")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["merged"][0]["repo"], "demo")
        stored = self.gh_state()["prs"][0]
        self.assertEqual(stored["state"], "MERGED")
        self.assertEqual(stored["merge_flag"], ["--squash"])
        self.assertEqual(self.publish("merge")[1]["precheck"][0]["status"], "already-merged")

    def test_failing_checks_block_everything(self):
        number = self.with_pr()
        self.edit_gh(lambda s: s["ci"].update({str(number): [[{"name": "build", "status": "COMPLETED",
                                                                "conclusion": "FAILURE"}]]}))
        code, out = self.publish("merge")
        self.assertEqual(code, 2)
        self.assertIn("checks failed", out["precheck"][0]["problems"][0])
        self.assertEqual(self.gh_state()["prs"][0]["state"], "OPEN")

    def test_behind_is_blocked_with_the_way_out(self):
        self.with_pr()
        self.edit_gh(lambda s: s["prs"][0].update(mergeStateStatus="BEHIND"))
        code, out = self.publish("merge")
        self.assertEqual(code, 2)
        self.assertIn("run push to rebase", out["precheck"][0]["problems"][0])

    def test_unpushed_work_blocks(self):
        self.with_pr()
        (self.worktree("DM-1") / "late.txt").write_text("x\n")
        code, out = self.publish("merge")
        self.assertEqual(code, 2)
        self.assertIn("uncommitted", out["precheck"][0]["problems"][0])

    def test_a_squash_of_several_commits_needs_confirmation(self):
        self.with_pr()
        path = self.worktree("DM-1")
        (path / "second.txt").write_text("x\n")
        sh(path, "git", "add", "-A")
        sh(path, "git", "commit", "-m", "second commit")
        sh(path, "git", "push")
        code, out = self.publish("merge")
        self.assertEqual(code, 4)
        self.assertEqual(out["needs_confirmation"][0]["commits"], ["DM-1 add the thing", "second commit"])
        self.assertEqual(self.gh_state()["prs"][0]["state"], "OPEN")
        self.assertEqual(self.publish("merge", "--confirm-squash")[0], 0)

    def test_merge_method_comes_from_config(self):
        raw = yaml.safe_load((self.root / ".claude" / "toolbox.local.yml").read_text())
        raw["pr"]["merge"] = "rebase"
        (self.root / ".claude" / "toolbox.local.yml").write_text(yaml.safe_dump(raw))
        self.with_pr()
        self.publish("merge")
        self.assertEqual(self.gh_state()["prs"][0]["merge_flag"], ["--rebase"])


class TestMergeWithReview(Workbench):
    def setUp(self):
        super().setUp()
        raw = yaml.safe_load((self.root / ".claude" / "toolbox.local.yml").read_text())
        raw["pr"]["review_required"] = True
        (self.root / ".claude" / "toolbox.local.yml").write_text(yaml.safe_dump(raw))

    def test_waits_for_approval_then_merges(self):
        self.start()
        self.change()
        self.approve()
        for step in ("commit", "push", "open-pr"):
            self.assertEqual(self.publish(step)[0], 0)
        number = self.gh_state()["prs"][0]["number"]
        self.edit_gh(lambda s: s["ci"].update({str(number): [[{"name": "b", "status": "COMPLETED",
                                                                "conclusion": "SUCCESS"}]]}))
        code, out = self.publish("merge")
        self.assertEqual(code, 4, out)
        self.assertIn("waiting for review", out["halted"])
        self.edit_gh(lambda s: s["prs"][0].update(reviewDecision="APPROVED"))
        self.assertEqual(self.publish("merge")[0], 0)


class TestRun(Workbench):
    def prepare(self) -> None:
        self.start()
        for name in (("api", "web") if self.layout == "nested" else ("api",)):
            self.change(repo=name)
        self.approve()

    def green(self) -> None:
        self.edit_gh(lambda s: s["ci"].update({
            str(p["number"]): [[{"name": "build", "status": "COMPLETED", "conclusion": "SUCCESS"}]]
            for p in s["prs"]}))

    def test_the_whole_sequence(self):
        self.prepare()
        with mock.patch.dict(os.environ, {"GH_FAKE_GREEN": "1"}):
            code, out = self.run_all()
        self.assertEqual(code, 0, out)
        self.assertTrue(out["done"])
        self.assertEqual(list(out["steps"]), ["commit", "push", "open-pr", "wait-ci", "merge", "cleanup"])
        self.assertEqual(out["announce"]["channel"], "C123")
        self.assertEqual({p["state"] for p in self.gh_state()["prs"]}, {"MERGED"})
        self.assertFalse((self.root / ".worktrees" / "DM-1").exists())
        self.assertEqual(sh(self.root, "git", "--git-dir", self.remote(), "log", "-1", "--format=%s",
                            "refs/heads/main"), "DM-1 add the thing")
        self.assertEqual(sh(self.root if self.layout == "single" else self.root / "api",
                            "git", "branch", "--list", "tester/*"), "")
        self.assertEqual(self.tasks_calls()[-2:], [["set-list", "DM-1", "worktrees"],
                                                   ["set", "DM-1", "status=done", "stage="]])

    def test_stop_after_open_pr_then_resume_to_the_end(self):
        self.prepare()
        code, out = self.run_all("--stop-after", "open-pr")
        self.assertEqual(code, 0, out)
        self.assertEqual((out["stopped_after"], list(out["steps"])), ("open-pr", ["commit", "push", "open-pr"]))
        self.assertNotIn("done", out)
        self.assertEqual({p["state"] for p in self.gh_state()["prs"]}, {"OPEN"})
        with mock.patch.dict(os.environ, {"GH_FAKE_GREEN": "1"}):
            code, out = self.run_all()
        self.assertEqual(code, 0, out)
        self.assertTrue(out["done"])
        self.assertEqual({p["state"] for p in self.gh_state()["prs"]}, {"MERGED"})

    def test_preflight_failure_changes_nothing(self):
        self.prepare()
        self.approve(message="DM-1 no body\n")
        code, out = self.run_all()
        self.assertEqual(code, 2)
        self.assertIn("no body", out["problems"][0])
        self.assertIn("feature.txt", sh(self.worktree("DM-1"), "git", "status", "--porcelain"))
        self.assertEqual(self.tasks_calls(), [])

    def test_a_halt_resumes_where_it_stopped(self):
        self.prepare()
        with mock.patch.dict(os.environ, {"GH_FAKE_CREATE_FAILS": "1"}):
            code, out = self.run_all()
        self.assertEqual(code, 3)
        self.assertEqual(out["stopped_at"], "open-pr")
        self.assertTrue(out["steps"]["commit"]["committed"])
        # The retry doesn't commit or push again; it opens the PR, then waits for CI that never reports.
        code, out = self.run_all("--timeout", "0", "--grace", "3600")
        self.assertEqual(code, 4, out)
        self.assertEqual(out["stopped_at"], "wait-ci")
        self.assertEqual(out["steps"]["commit"]["committed"], [])
        self.assertEqual(out["steps"]["push"]["pushed"], [])
        self.assertEqual(len(self.gh_state()["prs"]), 2 if self.layout == "nested" else 1)
        self.assertEqual(sh(self.worktree("DM-1"), "git", "rev-list", "--count", "origin/main..HEAD"), "1")

    def test_a_dirty_worktree_keeps_the_task_open(self):
        self.prepare()
        # Drive the steps by hand so a stray file can appear between merge and cleanup.
        for step in ("commit", "push", "open-pr"):
            self.assertEqual(self.publish(step)[0], 0)
        self.green()
        self.assertEqual(self.publish("wait-ci", "--interval", "0")[0], 0)
        self.assertEqual(self.publish("merge")[0], 0)
        (self.worktree("DM-1") / "stray.txt").write_text("x\n")
        code, out = self.publish("cleanup")
        self.assertEqual(code, 3, out)
        self.assertIn("refused", out["error"])
        self.assertTrue(self.worktree("DM-1").exists())
        self.assertNotIn(["set", "DM-1", "status=done", "stage="], self.tasks_calls())
        (self.worktree("DM-1") / "stray.txt").unlink()
        code, out = self.publish("cleanup")
        self.assertEqual(code, 0, out)
        self.assertIn(["set", "DM-1", "status=done", "stage="], self.tasks_calls())

    def test_cleanup_of_explicit_checkouts_outside_a_task(self):
        self.prepare()
        for step in ("commit", "push", "open-pr"):
            self.publish(step)
        self.green()
        self.assertEqual(self.publish("merge")[0], 0)
        paths = ",".join(str(self.worktree("DM-1", name)) for name in (("api", "web") if self.layout == "nested"
                                                                     else ("api",)))
        code, out = self.publish("cleanup", "--checkouts", paths, "--no-record")
        self.assertEqual(code, 0, out)
        self.assertFalse((self.root / ".worktrees" / "DM-1").exists())

    def test_cleanup_refuses_an_unmerged_task(self):
        self.prepare()
        for step in ("commit", "push", "open-pr"):
            self.publish(step)
        code, out = self.publish("cleanup")
        self.assertEqual(code, 2)
        self.assertIn("not merged", out["error"])
        self.assertTrue(self.worktree("DM-1").exists())

    def test_record_failure_is_reported_after_the_work_succeeds(self):
        self.prepare()
        self.publish("commit")
        self.publish("push")
        with mock.patch.dict(os.environ, {"TASKS_FAIL": "1"}):
            code, out = self.publish("open-pr")
        self.assertEqual(code, 3)
        self.assertEqual(out["prs"][0]["status"], "created")
        self.assertIn("simulated failure", out["record_errors"][0])

    def test_no_record_flag_and_missing_tasks_feature(self):
        self.prepare()
        self.publish("commit")
        self.publish("push")
        self.publish("open-pr", "--no-record")
        self.assertEqual(self.tasks_calls(), [])


class TestRunNested(TestRun):
    layout = "nested"

    def test_two_repos_in_one_run(self):
        self.prepare()
        for step in ("commit", "push", "open-pr"):
            self.assertEqual(self.publish(step)[0], 0)
        self.green()
        self.assertEqual(self.publish("wait-ci", "--interval", "0")[0], 0)
        code, out = self.publish("merge")
        self.assertEqual(code, 0, out)
        self.assertEqual([m["repo"] for m in out["merged"]], ["api", "web"])
        code, out = self.publish("cleanup")
        self.assertEqual(code, 0, out)
        self.assertEqual(sorted(w["repo"] for w in out["worktrees"]), ["api", "web"])
        self.assertFalse((self.root / ".worktrees" / "DM-1").exists())
        for name in ("api", "web"):
            self.assertEqual(sh(self.root / name, "git", "branch", "--list", "tester/*"), "")

    def test_repos_filter(self):
        self.prepare()
        code, out = self.publish("commit", "--repos", "web")
        self.assertEqual([c["repo"] for c in out["committed"]], ["web"])
        self.assertIn("feature.txt", sh(self.worktree("DM-1", "api"), "git", "status", "--porcelain"))


class TestRunWithoutPrs(Workbench):
    pr = {"required": False}
    announce = False

    def test_merges_straight_to_the_default_branch(self):
        self.start()
        self.change()
        self.approve()
        code, out = self.run_all()
        self.assertEqual(code, 0, out)
        self.assertEqual(out["steps"]["open-pr"]["skipped"][:12], "pr.required ")
        self.assertEqual(out["steps"]["wait-ci"]["skipped"][:12], "pr.required ")
        remote = self.remote()
        self.assertEqual(sh(self.root, "git", "--git-dir", remote, "log", "-1", "--format=%s", "refs/heads/main"),
                         "DM-1 add the thing")
        self.assertEqual(self.gh_state()["calls"], [])  # gh is never called
        self.assertFalse((self.root / ".worktrees" / "DM-1").exists())
        self.assertEqual(sh(self.root, "git", "branch", "--list", "tester/*"), "")
        self.assertEqual(self.tasks_calls()[-1], ["set", "DM-1", "status=done", "stage="])
        # The branch was never pushed, so the remote has none to delete.
        self.assertEqual(sh(self.root, "git", "--git-dir", remote, "branch", "--list", "tester/*"), "")

    def test_rebases_first_so_the_push_is_a_fast_forward(self):
        self.start()
        self.change()
        self.approve()
        clone = self.tmp / "clone"
        sh(self.tmp, "git", "clone", self.remotes["remotes/demo"], str(clone))
        (clone / "other.txt").write_text("x\n")
        sh(clone, "git", "add", "-A")
        sh(clone, "git", "commit", "-m", "advance")
        sh(clone, "git", "push")
        code, out = self.run_all()
        self.assertEqual(code, 0, out)
        self.assertEqual(out["steps"]["push"]["rebased"][0]["brought_in"], ["advance"])
        log = sh(self.root, "git", "--git-dir", self.remotes["remotes/demo"], "log", "--format=%s", "refs/heads/main")
        self.assertEqual(log.splitlines()[:2], ["DM-1 add the thing", "advance"])

    def test_merge_refuses_a_branch_that_is_not_a_fast_forward(self):
        self.start()
        self.change()
        self.approve()
        self.publish("commit")
        clone = self.tmp / "clone"
        sh(self.tmp, "git", "clone", self.remotes["remotes/demo"], str(clone))
        (clone / "other.txt").write_text("x\n")
        sh(clone, "git", "add", "-A")
        sh(clone, "git", "commit", "-m", "advance")
        sh(clone, "git", "push")
        code, out = self.publish("merge")
        self.assertEqual(code, 2)
        self.assertIn("not a fast-forward", out["precheck"][0]["problems"][0])


class TestNoTasksFeature(Workbench):
    features = ["worktrees", "pr"]

    def test_never_calls_tasks(self):
        self.start()
        self.change()
        self.approve()
        self.publish("commit")
        self.publish("push")
        self.publish("open-pr")
        self.assertEqual(self.tasks_calls(), [])


class TestBranches(Workbench):
    def make_branch(self, name: str, *, commit: bool = True) -> str:
        sh(self.root, "git", "branch", name, "main")
        if commit:
            sh(self.root, "git", "switch", name)
            (self.root / f"{name.split('/')[-1]}.txt").write_text("x\n")
            sh(self.root, "git", "add", "-A")
            sh(self.root, "git", "commit", "-m", name)
            sh(self.root, "git", "switch", "main")
        return sh(self.root, "git", "rev-parse", name)

    def add_pr(self, branch: str, state: str, sha: str, number: int = 1) -> None:
        self.edit_gh(lambda s: s["prs"].append({"number": number, "repo": "remotes/demo", "head": branch,
                                                "state": state, "url": f"https://example.test/{number}",
                                                "headRefOid": sha}))

    def test_scan_decides_by_the_pr(self):
        merged = self.make_branch("tester/merged")
        self.add_pr("tester/merged", "MERGED", merged, 1)
        opened = self.make_branch("tester/open")
        self.add_pr("tester/open", "OPEN", opened, 2)
        self.make_branch("tester/nopr")
        ahead = self.make_branch("tester/ahead")
        self.add_pr("tester/ahead", "MERGED", sh(self.root, "git", "rev-parse", "main"), 3)
        self.make_branch("other/theirs")
        plan = branches.scan(config.load())
        self.assertEqual([e["branch"] for e in plan["eligible"]], ["tester/merged"])
        reasons = {k["branch"]: k["keep"] for k in plan["kept"]}
        self.assertEqual(reasons, {"tester/open": "PR open", "tester/nopr": "No PR",
                                   "tester/ahead": "Local tip ahead of PR"})

    def test_a_branch_behind_its_pr_is_eligible(self):
        base = sh(self.root, "git", "rev-parse", "main")
        sh(self.root, "git", "branch", "tester/behind", "main")
        sh(self.root, "git", "switch", "-c", "tester/pr-tip", "main")
        (self.root / "more.txt").write_text("x\n")
        sh(self.root, "git", "add", "-A")
        sh(self.root, "git", "commit", "-m", "more")
        tip = sh(self.root, "git", "rev-parse", "HEAD")
        sh(self.root, "git", "switch", "main")
        self.add_pr("tester/behind", "MERGED", tip)
        # GitHub keeps refs/pull/<n>/head after the branch goes; the local tip is checked against it.
        sh(self.root, "git", "update-ref", "refs/pull/1/head", tip)
        entry = branches.scan(config.load(), None, "tester/behind")["eligible"][0]
        self.assertEqual(entry["sha"], base)

    def test_checked_out_branches_are_kept(self):
        self.start()
        sha = sh(self.worktree("DM-1"), "git", "rev-parse", "HEAD")
        self.add_pr("tester/DM-1-add-the-thing", "MERGED", sha)
        plan = branches.scan(config.load())
        self.assertEqual(plan["eligible"], [])
        self.assertIn("Checked out in worktree", plan["kept"][0]["keep"])

    def test_delete_rechecks_the_tip(self):
        sha = self.make_branch("tester/merged")
        self.add_pr("tester/merged", "MERGED", sha)
        plan = branches.scan(config.load())
        sh(self.root, "git", "switch", "tester/merged")
        (self.root / "late.txt").write_text("x\n")
        sh(self.root, "git", "add", "-A")
        sh(self.root, "git", "commit", "-m", "late")
        sh(self.root, "git", "switch", "main")
        out, code = branches.delete(plan)
        self.assertEqual(code, 0)
        self.assertEqual(out["deleted"], [])
        self.assertEqual(out["skipped"][0]["reason"], "moved since the scan")

    def test_cli_scan_and_delete(self):
        sha = self.make_branch("tester/merged")
        self.add_pr("tester/merged", "MERGED", sha)
        plan_file = self.tmp / "plan.json"
        code, out = self.cli(branches, "scan", "--out", str(plan_file))
        self.assertEqual(code, 0)
        code, out = self.cli(branches, "delete", "--plan", str(plan_file))
        self.assertEqual(code, 0)
        self.assertEqual([d["branch"] for d in out["deleted"]], ["tester/merged"])
        self.assertEqual(sh(self.root, "git", "branch", "--list", "tester/merged"), "")

    def test_scan_with_every_repo_excluded_scans_nothing(self):
        sha = self.make_branch("tester/merged")
        self.add_pr("tester/merged", "MERGED", sha)
        code, out = self.cli(branches, "scan", "--except", "demo")
        self.assertEqual(out["eligible"], [])


class TestBranchesDirect(Workbench):
    pr = {"required": False}

    def test_without_prs_a_branch_in_the_default_is_eligible(self):
        sh(self.root, "git", "branch", "tester/in-main", "main")
        sh(self.root, "git", "branch", "tester/ahead", "main")
        sh(self.root, "git", "switch", "tester/ahead")
        (self.root / "x.txt").write_text("x\n")
        sh(self.root, "git", "add", "-A")
        sh(self.root, "git", "commit", "-m", "x")
        sh(self.root, "git", "switch", "main")
        plan = branches.scan(config.load())
        self.assertEqual([e["branch"] for e in plan["eligible"]], ["tester/in-main"])
        self.assertEqual(self.gh_state()["calls"], [])


if __name__ == "__main__":
    unittest.main()
