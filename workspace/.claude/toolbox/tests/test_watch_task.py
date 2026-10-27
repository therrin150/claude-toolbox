"""Tests for bin/watch_task.py, with a scripted `gh` (each `pr view` returns the next scripted snapshot of that PR, the
last one sticking) and a task record in a scratch workspace.

Run from the repo root:

    python3 -m unittest discover -s workspace/.claude/toolbox/tests
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import yaml

TOOLBOX = Path(__file__).resolve().parents[1]
WATCH = TOOLBOX / "bin" / "watch_task.py"
TASKS = TOOLBOX / "bin" / "tasks.py"

FAKE_GH = textwrap.dedent('''\
    import json, os, sys
    path = os.environ["WATCH_FAKE"]
    state = json.load(open(path))
    args = sys.argv[1:]
    def opt(name):
        return args[args.index(name) + 1] if name in args else None
    if args[:2] == ["api", "user"]:
        print(state["user"])
    elif args[:2] == ["api", "--paginate"]:
        print(json.dumps(state.get("inline", {}).get(args[2], [])))
    elif args[:2] == ["pr", "view"]:
        views = state["prs"][opt("--repo") + "#" + args[2]]
        view = views.pop(0) if len(views) > 1 else views[0]
        json.dump(state, open(path, "w"))
        print(json.dumps(view))
    elif args[:2] == ["run", "list"]:
        print(json.dumps(state.get("runs", {}).get(opt("--commit"), [])))
    elif args[:2] == ["run", "view"]:
        print(json.dumps(state["run_views"][args[2]]))
    elif args[:2] == ["run", "watch"]:
        pass
    else:
        print("unsupported", args, file=sys.stderr)
        sys.exit(1)
    ''')

GREEN = [{"name": "build", "status": "COMPLETED", "conclusion": "SUCCESS"}]
RED = [{"name": "build", "status": "COMPLETED", "conclusion": "FAILURE"}]
PENDING = [{"name": "build", "status": "IN_PROGRESS", "conclusion": ""}]


def pr(number: int, state: str = "OPEN", review: str = "", checks=None, comments=None, reviews=None, **extra) -> dict:
    return {"number": number, "url": f"https://github.com/acme/api/pull/{number}", "title": "t", "state": state,
            "reviewDecision": review, "mergeStateStatus": "CLEAN", "statusCheckRollup": checks or [],
            "comments": comments or [], "reviews": reviews or [], **extra}


class WatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp)
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        (bin_dir / "fake_gh.py").write_text(FAKE_GH)
        gh = bin_dir / "gh"
        gh.write_text(f"#!/bin/sh\nexec {sys.executable} {bin_dir / 'fake_gh.py'} \"$@\"\n")
        gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
        self.state = self.tmp / "gh.json"
        self.root = self.tmp / "ws"
        (self.root / ".claude").mkdir(parents=True)
        raw = {"version": 1, "features": ["tasks", "pr"], "workspace": {"name": "pantry", "layout": "single"},
               "user": {"handle": "jdoe", "name": "Jordan"}, "vault": {"path": "vault"},
               "tracker": {"kind": "vault", "key_prefix": "PT"}, "pr": {"bots": ["scanner"]}}
        (self.root / ".claude" / "toolbox.local.yml").write_text(yaml.safe_dump(raw))
        self.env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                    "WATCH_FAKE": str(self.state), "TOOLBOX_WORKSPACE": str(self.root)}
        subprocess.run([sys.executable, str(TASKS), "create-defect", "--summary", "Shelf Count Wrong"],
                       env=self.env, cwd=self.root, check=True, capture_output=True)

    def prs(self, *urls: str) -> None:
        subprocess.run([sys.executable, str(TASKS), "append", "PT-1", "prs", *urls], env=self.env, cwd=self.root,
                       check=True, capture_output=True)

    def script(self, prs: dict, **extra) -> None:
        self.state.write_text(json.dumps({"user": "jdoe", "prs": prs, **extra}))

    def watch(self, *args: str) -> tuple[int, dict]:
        proc = subprocess.run([sys.executable, str(WATCH), *args], capture_output=True, text=True, env=self.env,
                              cwd=self.root, timeout=60)
        return proc.returncode, json.loads(proc.stdout)

    def test_status_skips_the_user_and_bots(self):
        self.prs("https://github.com/acme/api/pull/1")
        comments = [{"author": {"login": "jdoe"}, "body": "mine", "url": "u1", "createdAt": "2026-10-27T00:00:00Z"},
                    {"author": {"login": "scanner"}, "body": "quality gate", "url": "u2"},
                    {"author": {"login": "renovate[bot]"}, "body": "bump", "url": "u3"},
                    {"author": {"login": "pat"}, "body": "why this?", "url": "u4"}]
        reviews = [{"author": {"login": "pat"}, "body": "", "state": "COMMENTED", "submittedAt": "x"}]
        inline = {"repos/acme/api/pulls/1/comments": [
            {"user": {"login": "pat"}, "body": "rename", "html_url": "u5", "path": "a.py", "line": 3},
            {"user": {"login": "lint", "type": "Bot"}, "body": "style", "html_url": "u6"}]}
        self.script({"acme/api#1": [pr(1, checks=PENDING, comments=comments, reviews=reviews)]}, inline=inline)
        code, out = self.watch("status", "PT-1")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["overall"], "waiting")
        snap = out["prs"][0]
        self.assertEqual((snap["checks"], snap["pending"]), ("pending", 1))
        self.assertEqual([(c["author"], c["kind"]) for c in snap["comments"]], [("pat", "comment"), ("pat", "inline")])

    def test_prs_exits_ready_at_once(self):
        self.prs("https://github.com/acme/api/pull/1")
        self.script({"acme/api#1": [pr(1, review="APPROVED", checks=GREEN)]})
        code, out = self.watch("prs", "PT-1", "--interval", "0")
        self.assertEqual((code, out["events"]), (0, [{"event": "ready"}]))

    def test_prs_wakes_on_a_failed_check_and_on_a_merge(self):
        self.prs("https://github.com/acme/api/pull/1", "https://github.com/acme/api/pull/2")
        self.script({"acme/api#1": [pr(1, checks=PENDING), pr(1, checks=PENDING), pr(1, checks=RED)],
                     "acme/api#2": [pr(2, checks=PENDING), pr(2, checks=PENDING), pr(2, state="MERGED")]})
        code, out = self.watch("prs", "PT-1", "--interval", "0")
        self.assertEqual(code, 0, out)
        self.assertEqual([e["event"] for e in out["events"]], ["ci-failed", "merged"])
        self.assertEqual(out["events"][0]["checks"], ["build"])

    def test_prs_times_out(self):
        self.prs("https://github.com/acme/api/pull/1")
        self.script({"acme/api#1": [pr(1, checks=PENDING)]})
        code, out = self.watch("prs", "PT-1", "--interval", "0.05", "--max-hours", "0.00005")
        self.assertEqual(out["events"][0]["event"], "timeout")

    def test_bad_input(self):
        code, out = self.watch("status", "PT-9")
        self.assertEqual(code, 2)
        self.prs("https://example.test/acme/api/pull/1")
        code, out = self.watch("status", "PT-1")
        self.assertEqual(code, 2)
        self.assertIn("not a GitHub PR URL", out["error"])

    def test_no_prs(self):
        self.script({})
        code, out = self.watch("prs", "PT-1")
        self.assertEqual((code, out["error"]), (2, "the task has no prs"))

    def test_pipeline_watches_the_default_branch_runs_of_the_merge_commit(self):
        self.prs("https://github.com/acme/api/pull/1")
        run = {"databaseId": 7, "workflowName": "release", "event": "push", "headBranch": "main",
               "status": "in_progress", "createdAt": "2026-10-27T01:00:00Z"}
        other = {**run, "databaseId": 8, "headBranch": "feature"}
        self.script({"acme/api#1": [pr(1, state="MERGED", mergeCommit={"oid": "abc1234def"})]},
                    runs={"abc1234def": [run, other]},
                    run_views={"7": {"status": "completed", "conclusion": "success", "workflowName": "release",
                                     "number": 12}})
        code, out = self.watch("pipeline", "PT-1")
        self.assertEqual(code, 0, out)
        self.assertEqual([p["number"] for p in out["pipelines"]], [12])
        self.assertIn("no main checkout", out["pipelines"][0]["tags"])

    def test_pipeline_refuses_an_open_pr_and_reports_a_failed_run(self):
        self.prs("https://github.com/acme/api/pull/1")
        self.script({"acme/api#1": [pr(1)]})
        self.assertEqual(self.watch("pipeline", "PT-1")[0], 2)
        run = {"databaseId": 7, "workflowName": "release", "event": "push", "headBranch": "main",
               "status": "completed", "createdAt": "2026-10-27T01:00:00Z"}
        self.script({"acme/api#1": [pr(1, state="MERGED", mergeCommit={"oid": "abc"})]}, runs={"abc": [run]},
                    run_views={"7": {"status": "completed", "conclusion": "failure", "workflowName": "release",
                                     "number": 3}})
        code, out = self.watch("pipeline", "PT-1")
        self.assertEqual(code, 3)
        self.assertIn("release #3: failure", out["error"])


    def test_pipeline_without_prs_watches_the_commits_publish_pushed(self):
        """No pr.required: the shared-library cycle's release wait watches the commits publish.py noted."""
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.root, check=True)
        subprocess.run(["git", "remote", "add", "origin", "git@github.com:acme/pantry.git"], cwd=self.root, check=True)
        self.script({})
        code, out = self.watch("pipeline", "PT-1")
        self.assertEqual(code, 2)
        self.assertIn("recorded no merged commits", out["error"])
        publish = self.root / "vault" / "Tasks" / ".publish" / "PT-1"
        publish.mkdir(parents=True, exist_ok=True)
        (publish / "merged.json").write_text(json.dumps({"pantry": "fed4321cba"}))
        run = {"databaseId": 9, "workflowName": "release", "event": "push", "headBranch": "main",
               "status": "completed", "createdAt": "2026-10-27T01:00:00Z"}
        self.script({}, runs={"fed4321cba": [run]},
                    run_views={"9": {"status": "completed", "conclusion": "success", "workflowName": "release",
                                     "number": 31}})
        code, out = self.watch("pipeline", "PT-1")
        self.assertEqual(code, 0, out)
        self.assertEqual([(p["repo"], p["merge_commit"], p["pr"], p["number"]) for p in out["pipelines"]],
                         [("pantry", "fed4321", None, 31)])

if __name__ == "__main__":
    unittest.main()
