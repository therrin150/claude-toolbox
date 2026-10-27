"""Tests for the local-review-ui skill's review.py: notes, the wait deadline, forget and prune, against a temporary
review home and temporary git repos. One test starts the real server on a free port and opens a page.

Run from the repo root:

    python3 -m unittest discover -s workspace/.claude/toolbox/tests
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "local-review-ui" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import common  # noqa: E402
import review  # noqa: E402

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.com"}


def run(cwd: Path, *args: str) -> str:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, env=dict(os.environ, **GIT_ENV))
    assert result.returncode == 0, f"{' '.join(args)}: {result.stderr}"
    return result.stdout


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ReviewCase(unittest.TestCase):
    """A review home, a repo with a change on a feature branch, and a way to run review.py's commands in process."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        patcher = mock.patch.dict(os.environ, {"LOCAL_REVIEW_HOME": str(self.home),
                                               "LOCAL_REVIEW_PORT": str(free_port())})
        patcher.start()
        self.addCleanup(patcher.stop)
        # wait installs handlers for these; put the runner's back afterwards.
        for signum in (signal.SIGINT, signal.SIGTERM):
            self.addCleanup(signal.signal, signum, signal.getsignal(signum))
        self.repo = self.make_repo("app")

    def make_repo(self, name: str) -> Path:
        origin = self.tmp / "origins" / f"{name}.git"
        origin.mkdir(parents=True)
        run(origin, "git", "init", "--bare", "--initial-branch=main")
        repo = self.tmp / name
        run(self.tmp, "git", "clone", "--quiet", str(origin), str(repo))
        run(repo, "git", "checkout", "--quiet", "-B", "main")
        filler = "".join(f"  // filler {n}\n" for n in range(1, 31))
        (repo / "App.java").write_text(f"class App {{\n  int one() {{ return 1; }}\n  int two() {{ return 2; }}\n{filler}}}\n")
        run(repo, "git", "add", "-A")
        run(repo, "git", "commit", "--quiet", "-m", "initial")
        run(repo, "git", "push", "--quiet", "-u", "origin", "main")
        run(repo, "git", "remote", "set-head", "origin", "main")
        run(repo, "git", "checkout", "--quiet", "-b", "jdoe/DM-7-change")
        (repo / "App.java").write_text(
            f"class App {{\n  int one() {{ return 11; }}\n  int two() {{ return 2; }}\n{filler}}}\n")
        return repo

    def cli(self, *args: str, stdin: str = "") -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", ["review.py", *args]), mock.patch.object(sys, "stdin", io.StringIO(stdin)), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = review.main()
        return code, out.getvalue(), err.getvalue()

    def open(self, repo: Path | None = None, *extra: str) -> dict:
        """`open` without a server or a browser: the server isn't what these tests are about."""
        with mock.patch.object(review, "ensure_server", return_value={"pid": 0, "version": "test"}):
            code, out, err = self.cli("open", "--no-browser", str(repo or self.repo), *extra)
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def registration(self, review_id: str) -> dict:
        return common.load_registration(review_id)

    def comments(self, review_id: str) -> dict:
        return common.load_comments(self.registration(review_id))

    def store_files(self, review_id: str) -> list[str]:
        return sorted(p.name for p in common.store_dir(self.registration(review_id)).iterdir())


class Open(ReviewCase):
    def test_id_comes_from_the_branch_key_and_the_store_is_under_the_work_id(self):
        opened = self.open(self.repo, "--resume-hint", "/work-task DM-7", "--title", "DM-7 Change")
        self.assertEqual(opened["id"], "DM-7")
        self.assertEqual(Path(opened["store"]), self.home / "reviews" / "DM-7")
        self.assertEqual(self.registration("DM-7")["resumeHint"], "/work-task DM-7")
        self.assertEqual(self.registration("DM-7")["title"], "DM-7 Change")
        self.assertEqual(self.registration("DM-7")["repos"][0]["branch"], "jdoe/DM-7-change")

    def test_a_later_open_keeps_the_hint_and_an_empty_one_clears_it(self):
        self.open(self.repo, "--resume-hint", "/work-task DM-7")
        self.open(self.repo)
        self.assertEqual(self.registration("DM-7")["resumeHint"], "/work-task DM-7")
        self.open(self.repo, "--resume-hint", "")
        self.assertIsNone(self.registration("DM-7")["resumeHint"])

    def test_opens_a_real_page_on_the_server(self):
        self.addCleanup(self.cli, "stop")
        with mock.patch.object(review.webbrowser, "open", return_value=False):
            code, out, err = self.cli("open", str(self.repo))
        self.assertEqual(code, 0, err)
        opened = json.loads(out)
        with urllib.request.urlopen(opened["url"], timeout=5) as page:
            self.assertEqual(page.status, 200)
            self.assertIn("<title>Local Review</title>", page.read().decode("utf-8"))
        with urllib.request.urlopen(opened["url"].rsplit("/review/", 1)[0] + "/assets/app.js", timeout=5) as script:
            self.assertEqual(script.status, 200)


class Note(ReviewCase):
    def test_note_opens_a_claude_thread_that_read_lists_only_after_the_reviewer_replies(self):
        self.open()
        code, out, err = self.cli("note", "DM-7", "--repo", "app", "--path", "App.java", "--line", "2",
                                  "--body", "Keep the literal, or name the constant?")
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertNotIn("warning", result)
        note = self.comments("DM-7")["comments"][0]
        self.assertEqual((note["author"], note["round"], note["side"], note["status"]),
                         ("claude", None, "new", "open"))
        self.assertEqual(note["lineText"], "  int one() { return 11; }")

        _, listed, _ = self.cli("read", "DM-7")
        self.assertIn("No comments need a response", listed)

        common.update_comments(self.registration("DM-7"), lambda data: data["comments"][0]["replies"].append(
            {"author": "user", "body": "Name it.", "at": common.now_iso()}))
        _, listed, _ = self.cli("read", "DM-7")
        self.assertIn("Keep the literal, or name the constant?", listed)
        self.assertIn("Name it.", listed)

    def test_note_on_the_old_side_reads_the_base_and_a_line_outside_the_diff_needs_no_warning(self):
        self.open()
        _, out, _ = self.cli("note", "DM-7", "--repo", "app", "--path", "App.java", "--line", "2", "--side", "old",
                             "--body", "Why 1?")
        self.assertEqual(self.comments("DM-7")["comments"][0]["lineText"], "  int one() { return 1; }")
        # The page widens the diff to show a line outside its context, so the note is placed without a warning.
        _, out, _ = self.cli("note", "DM-7", "--repo", "app", "--path", "App.java", "--line", "30", "--body", "Far.")
        self.assertNotIn("warning", json.loads(out))

    def test_note_rejects_an_unknown_repo_a_bad_range_and_an_empty_body(self):
        self.open()
        code, _, err = self.cli("note", "DM-7", "--repo", "nope", "--path", "App.java", "--line", "1", "--body", "x")
        self.assertEqual(code, 1)
        self.assertIn("no repo 'nope'", err)
        code, _, err = self.cli("note", "DM-7", "--repo", "app", "--path", "App.java", "--line", "99", "--body", "x")
        self.assertEqual(code, 1)
        self.assertIn("34 lines", err)
        code, _, err = self.cli("note", "DM-7", "--repo", "app", "--path", "App.java", "--line", "1", "--body", " ")
        self.assertEqual((code, err.strip()), (1, "empty note"))
        self.assertEqual(self.comments("DM-7")["comments"], [])

    def test_note_body_comes_from_stdin_when_omitted(self):
        self.open()
        code, _, err = self.cli("note", "DM-7", "--repo", "app", "--path", "App.java", "--line", "1",
                                stdin="From stdin.\n")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.comments("DM-7")["comments"][0]["body"], "From stdin.")


class Wait(ReviewCase):
    def wait(self, *args: str) -> dict:
        code, out, err = self.cli("wait", "DM-7", "--interval", "0.05", *args)
        self.assertEqual(code, 0, err)
        return json.loads(out.strip().splitlines()[-1])

    def test_times_out_with_minutes_or_until_and_releases_the_listener(self):
        self.open()
        event = self.wait("--minutes", "0.02", "--session", "s1")
        self.assertEqual((event["event"], event["id"], event["session"]), ("timeout", "DM-7", "s1"))
        self.assertIsNone(common.listener_state(self.registration("DM-7")))

        past = common.parse_iso(common.now_iso()).replace(microsecond=0).isoformat()  # an ISO deadline already passed
        self.assertEqual(self.wait("--until", past)["event"], "timeout")

    def test_until_takes_a_clock_time_within_the_next_day(self):
        self.open()
        until = review.parse_until("00:00")
        self.assertGreater(until.timestamp(), common.parse_iso(common.now_iso()).timestamp())
        self.assertLess(until.timestamp() - common.parse_iso(common.now_iso()).timestamp(), 24 * 3600 + 1)

    def test_a_finished_round_ends_the_wait_at_once_and_done_is_reported(self):
        self.open()
        registration = self.registration("DM-7")
        common.update_comments(registration, lambda data: data["rounds"].append(
            {"n": 1, "submittedAt": common.now_iso(), "summary": "Two things.", "done": False}))
        event = self.wait("--minutes", "5")
        self.assertEqual((event["event"], event["round"], event["summary"]), ("submitted", 1, "Two things."))
        self.cli("read", "DM-7")  # acknowledges the round
        common.update_comments(registration, lambda data: data.update(closedAt=common.now_iso()))
        self.assertEqual(self.wait("--minutes", "5")["event"], "closed")


class Forget(ReviewCase):
    def test_forget_drops_the_registration_and_keeps_comments_unless_asked(self):
        self.open()
        self.cli("note", "DM-7", "--repo", "app", "--path", "App.java", "--line", "1", "--body", "x")
        store = common.store_dir(self.registration("DM-7"))
        code, out, _ = self.cli("forget", "DM-7")
        self.assertEqual((code, json.loads(out)["deleted"]), (0, []))
        self.assertIsNone(common.load_registration("DM-7"))
        self.assertTrue((store / "comments.json").is_file())

        self.open()
        code, out, _ = self.cli("forget", "DM-7", "--delete-comments")
        self.assertEqual(json.loads(out)["deleted"], [str(store / "comments.json")])
        self.assertFalse((store / "comments.json").exists())
        self.assertTrue(store.is_dir())  # forget leaves the folder and its locks

    def test_forget_of_an_unknown_review_fails(self):
        code, _, err = self.cli("forget", "NOPE-1")
        self.assertEqual(code, 1)
        self.assertIn("no review 'NOPE-1'", err)


class Prune(ReviewCase):
    def prune(self, *args: str) -> dict:
        code, out, err = self.cli("prune", *args)
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def kept(self, result: dict) -> dict:
        return {k["id"]: k["reason"] for k in result["kept"]}

    def test_a_review_whose_repo_still_exists_is_kept(self):
        self.open()
        result = self.prune()
        self.assertEqual(result["pruned"], [])
        self.assertIn("still exists: app", self.kept(result)["DM-7"])

    def test_a_deleted_branch_prunes_the_review_comments_locks_and_store(self):
        self.open()
        self.cli("note", "DM-7", "--repo", "app", "--path", "App.java", "--line", "1", "--body", "x")
        store = common.store_dir(self.registration("DM-7"))
        run(self.repo, "git", "stash", "--quiet")
        run(self.repo, "git", "checkout", "--quiet", "main")
        run(self.repo, "git", "branch", "-D", "jdoe/DM-7-change")

        dry = self.prune("--dry-run")
        self.assertEqual([p["id"] for p in dry["pruned"]], ["DM-7"])
        self.assertIn("branch jdoe/DM-7-change no longer exists", dry["pruned"][0]["reason"])
        self.assertTrue(dry["dryRun"])
        self.assertIsNotNone(common.load_registration("DM-7"))
        self.assertTrue((store / "comments.json").is_file())

        result = self.prune()
        self.assertEqual([p["id"] for p in result["pruned"]], ["DM-7"])
        self.assertIsNone(common.load_registration("DM-7"))
        self.assertFalse(store.exists())
        self.assertEqual(sorted(p.name for p in (self.home / "registry").iterdir()), [])

    def test_a_missing_path_prunes_and_a_fresh_listener_protects(self):
        self.open()
        listener = common.listener_path(self.registration("DM-7"))
        listener.write_text(json.dumps({"session": "s9", "seenAt": common.now_iso()}))
        shutil.rmtree(self.repo)
        self.assertIn("a session is listening (session s9)", self.kept(self.prune())["DM-7"])

        listener.unlink()
        result = self.prune()
        self.assertIn("path no longer exists", result["pruned"][0]["reason"])
        self.assertIsNone(common.load_registration("DM-7"))

    def test_only_reviews_with_every_repo_gone_are_pruned(self):
        other = self.make_repo("lib")
        self.open(self.repo, "--id", "TWO")
        self.open(other, "--id", "TWO")
        shutil.rmtree(other)
        self.assertIn("still exists: app", self.kept(self.prune())["TWO"])

    def test_a_review_without_repos_is_kept(self):
        self.open()
        common.update_json(common.registration_path("DM-7"), {}, lambda data: data.update(repos=[]))
        self.assertEqual(self.kept(self.prune())["DM-7"], "no repos registered")


if __name__ == "__main__":
    unittest.main()
