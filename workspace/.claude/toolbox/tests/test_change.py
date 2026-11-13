"""The `start-change`, `check-change`, `review-change`, `publish-change` and `finish-change` stage skills:
`bin/change.py`'s gates against real git worktrees, `run_checks.py`'s record of what was checked, and the skills' text.

Fixtures: a single-repo workspace (vault inside the repo, `vault` tracker) and a nested one (two repos, a vault beside
them). No services run; the integration gate reads a lock folder the test points TOOLBOX_LOCKS at.

Run from the repo root:

    python3 -m unittest discover -s workspace/.claude/toolbox/tests
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_work_task import Walkthrough  # noqa: E402

import frontmatter  # noqa: E402

TOOLBOX = Path(__file__).resolve().parents[1]
CLAUDE = TOOLBOX.parent
SKILLS = CLAUDE / "skills"
FEATURES = CLAUDE.parent / "features.yml"


class Stage(Walkthrough):
    def change(self, *args: str, code: int = 0) -> dict:
        return self.script("change.py", *args, code=code)

    def record(self, wid: str) -> dict:
        return self.fields(wid)


class SingleStage(Stage):
    def setUp(self):
        super().setUp()
        self.make_repo(self.root, "pantry", {"app/src/Shelf.java": "class Shelf {}\n", "web/index.ts": "export {}\n",
                                             "vault/README.md": "vault\n"})
        self.locks = self.tmp / "locks"
        self.env["TOOLBOX_LOCKS"] = str(self.locks)
        self.configure({
            "version": 1, "features": ["tasks", "worktrees", "tests", "services-lock"],
            "workspace": {"name": "pantry", "layout": "single", "default_branch": "main", "areas": {
                "app": {"unit": "true", "integration": "true", "start": "true", "files": ["*.java"]},
                "web": {"unit": "true", "files": ["*.ts"]}}},
            "user": {"handle": "jdoe", "name": "Jordan"}, "vault": {"path": "vault"},
            "tracker": {"kind": "vault", "key_prefix": "PT"}, "pr": {"required": False},
        })

    # start

    def test_ad_hoc_change_gets_a_keyless_record_a_branch_and_a_worktree(self):
        # Scenario "Ad-Hoc Change Started"
        out = self.change("start", "--summary", "Fix Shelf Rounding")
        self.assertEqual((out["work_id"], out["made"], out["branch"]), ("fix-shelf-rounding", True,
                                                                         "jdoe/fix-shelf-rounding"))
        tree = self.root / ".worktrees" / "fix-shelf-rounding"
        self.assertEqual([(r["status"], r["path"]) for r in out["results"]], [("created", str(tree))])
        self.assertTrue((tree / ".git").exists())
        self.assertEqual(sh(tree, "git", "branch", "--show-current"), "jdoe/fix-shelf-rounding")
        record = self.root / "vault" / "Tasks" / "fix-shelf-rounding.md"
        self.assertEqual(out["doc"], str(record))
        fields = self.record("fix-shelf-rounding")
        self.assertEqual((fields["key"], fields["type"], fields["summary"], fields["worktrees"]),
                         (None, "task", "Fix Shelf Rounding", [str(tree)]))
        self.assertEqual((fields["status"], fields["stage"], fields["owner"]), ("in-progress", "implementing", "claude"))
        self.assertIn("## Description\n\nFix Shelf Rounding\n", record.read_text())

    def test_the_description_is_the_one_given(self):
        self.change("start", "--summary", "Fix Shelf Rounding", "--description", "Round counts half up.")
        text = (self.root / "vault" / "Tasks" / "fix-shelf-rounding.md").read_text()
        self.assertIn("## Description\n\nRound counts half up.\n", text)
        self.assertFalse(self.context("fix-shelf-rounding", "s1")["needs_requirement"])

    def test_a_record_with_a_worktree_refuses_a_second_start(self):
        # Scenario "Worktree Already Exists", by summary and by work id.
        self.change("start", "--summary", "Fix Shelf Rounding")
        tree = str(self.root / ".worktrees" / "fix-shelf-rounding")
        for args in (("--summary", "Fix Shelf Rounding"), ("fix-shelf-rounding",)):
            out = self.change("start", *args, code=1)
            self.assertEqual([r["status"] for r in out["results"]], ["refused"])
            self.assertIn(tree, out["results"][0]["reason"])
            self.assertFalse(out["made"])
        self.assertEqual(self.record("fix-shelf-rounding")["worktrees"], [tree])

    def test_a_stale_listed_worktree_whose_folder_is_gone_is_started_again(self):
        # Only a worktree on disk counts: a path the record still lists after `worktrees.py remove` doesn't.
        self.change("start", "--summary", "Fix Shelf Rounding")
        tree = str(self.root / ".worktrees" / "fix-shelf-rounding")
        self.script("worktrees.py", "remove", "fix-shelf-rounding")
        self.assertEqual(self.record("fix-shelf-rounding")["worktrees"], [tree])
        out = self.change("start", "fix-shelf-rounding")
        self.assertEqual([r["status"] for r in out["results"]], ["attached"])
        self.assertEqual(self.record("fix-shelf-rounding")["worktrees"], [tree])  # listed once

    def test_a_done_record_is_not_started_again(self):
        self.change("start", "--summary", "Fix Shelf Rounding")
        self.script("worktrees.py", "remove", "fix-shelf-rounding")
        self.tasks("set", "fix-shelf-rounding", "status=done")
        for args in (("--summary", "Fix Shelf Rounding"), ("fix-shelf-rounding",)):
            self.assertIn("is done", self.change("start", *args, code=1)["refused"])
        self.assertFalse((self.root / ".worktrees" / "fix-shelf-rounding").exists())

    def test_a_keyed_record_needs_a_branch_description(self):
        self.tasks("create-defect", "--summary", "Counts drift")
        out = self.change("start", "PT-1", code=1)
        self.assertIn("description is required", out["error"])
        out = self.change("start", "PT-1", "--desc", "counts-drift")
        self.assertEqual(out["branch"], "jdoe/PT-1-counts-drift")
        self.assertEqual(self.record("PT-1")["worktrees"], [str(self.root / ".worktrees" / "PT-1")])

    def test_names_either_a_record_or_a_summary(self):
        self.assertIn("not both or neither", self.change("start", code=1)["refused"])
        self.assertIn("not both or neither", self.change("start", "x", "--summary", "X", code=1)["refused"])

    def test_an_unknown_record_is_an_error_and_makes_nothing(self):
        out = self.change("start", "no-such-change", code=1)
        self.assertIn("error", out)
        self.assertFalse((self.root / "vault" / "Tasks" / "no-such-change.md").exists())

    # check

    def test_a_worktree_with_no_changes_has_nothing_to_check(self):
        # Scenario "Nothing to Check"
        self.change("start", "--summary", "Fix Shelf Rounding")
        out = self.change("check", "fix-shelf-rounding", "unit", code=1)
        self.assertFalse(out["ok"])
        self.assertIn("no change to check", out["refused"])

    def test_an_id_with_no_worktree_has_nothing_to_check(self):
        out = self.change("check", "fix-shelf-rounding", "unit", code=1)
        self.assertIn("no change to check", out["refused"])
        self.assertIn("no worktree", out["refused"])

    def test_changes_no_area_looks_at_have_nothing_to_check(self):
        self.change("start", "--summary", "Fix Shelf Rounding")
        (self.root / ".worktrees" / "fix-shelf-rounding" / "app" / "README.md").write_text("notes\n")
        out = self.change("check", "fix-shelf-rounding", "unit", code=1)
        self.assertIn("no change to check", out["refused"])
        self.assertIn("app", out["refused"])

    def test_a_change_gives_the_areas_for_the_runner(self):
        self.change("start", "--summary", "Fix Shelf Rounding")
        tree = self.root / ".worktrees" / "fix-shelf-rounding"
        (tree / "app" / "src" / "Shelf.java").write_text("class Shelf { int count; }\n")
        for mode in ("build", "unit", "format"):
            out = self.change("check", "fix-shelf-rounding", mode)
            self.assertEqual((out["ok"], out["mode"], out["areas"]), (True, mode, [f"app={tree / 'app'}"]))
        self.assertEqual(out["bin"], str(TOOLBOX / "bin"))
        self.assertEqual(out["checkouts"][0]["diff"], "1 file, +1 −1")

    def test_a_broken_worktree_is_an_error_not_a_missing_worktree(self):
        self.change("start", "--summary", "Fix Shelf Rounding")
        tree = self.root / ".worktrees" / "fix-shelf-rounding"
        sh(tree, "git", "update-ref", "-d", "refs/remotes/origin/main")
        out = self.change("check", "fix-shelf-rounding", "unit", code=1)
        self.assertIn("error", out)
        self.assertNotIn("refused", out)

    def test_a_bad_mode_is_a_usage_error(self):
        self.script("change.py", "check", "x", "lint", code=2)

    def lock(self, task: str, *extra: str) -> dict:
        return self.script("services_lock.py", "acquire", "--task", task, *extra)

    def test_integration_refuses_while_the_lock_is_held(self):
        self.change("start", "--summary", "Fix Shelf Rounding")
        tree = self.root / ".worktrees" / "fix-shelf-rounding"
        (tree / "app" / "src" / "Shelf.java").write_text("class Shelf { int count; }\n")
        self.assertTrue(self.change("check", "fix-shelf-rounding", "integration")["ok"])  # lock free
        self.lock("other-task", "--worktree", "/elsewhere")
        out = self.change("check", "fix-shelf-rounding", "integration", code=1)
        self.assertIn("services lock pantry is held by task other-task at /elsewhere", out["refused"])
        # The change's own earlier run is named as such.
        self.script("services_lock.py", "release", "--force")
        self.lock("fix-shelf-rounding")
        out = self.change("check", "fix-shelf-rounding", "integration", code=1)
        self.assertIn("this change's own earlier run still holds it", out["refused"])
        # The other modes never look at the lock.
        self.assertTrue(self.change("check", "fix-shelf-rounding", "unit")["ok"])

    def test_integration_runs_over_an_expired_lock(self):
        self.change("start", "--summary", "Fix Shelf Rounding")
        (self.root / ".worktrees" / "fix-shelf-rounding" / "app" / "src" / "Shelf.java").write_text("class Shelf { int c; }\n")
        self.lock("other-task")
        owner_file = next(self.locks.glob("*/*/owner.json"))
        owner = json.loads(owner_file.read_text())
        owner["expires"] = "2020-01-01T00:00:00+00:00"
        owner_file.write_text(json.dumps(owner))
        self.assertTrue(self.change("check", "fix-shelf-rounding", "integration")["ok"])


class NestedStage(Stage):
    def setUp(self):
        super().setUp()
        for name in ("api", "web"):
            self.make_repo(self.root / name, name, {"src/Main.java": "class Main {}\n"})
        (self.root / "vault").mkdir()
        self.locks = self.tmp / "locks"
        self.env["TOOLBOX_LOCKS"] = str(self.locks)
        self.configure({
            "version": 1, "features": ["tasks", "worktrees", "tests", "services-lock"],
            "workspace": {"name": "fleet", "layout": "nested", "default_branch": "main",
                          "areas": {"*": {"unit": "true", "integration": "true", "start": "true",
                                          "files": ["*.java"]}}},
            "user": {"handle": "jdoe", "name": "Jordan"}, "vault": {"path": "vault"},
            "tracker": {"kind": "vault", "key_prefix": "PT"}, "pr": {"required": False},
        })

    def test_repos_are_required(self):
        self.assertIn("--repos is required", self.change("start", "--summary", "Fix Both", code=1)["error"])
        self.assertFalse((self.root / "vault" / "Tasks" / "fix-both.md").exists())  # a bad call makes no record

    def test_a_bad_repo_makes_no_record(self):
        self.assertIn("error", self.change("start", "--summary", "Fix Both", "--repos", "nope", code=1))
        self.assertFalse((self.root / "vault" / "Tasks" / "fix-both.md").exists())

    def test_a_repo_with_a_worktree_is_refused_and_the_others_are_still_created(self):
        self.change("start", "--summary", "Fix Both", "--repos", "api")
        out = self.change("start", "fix-both", "--repos", "api,web", code=1)
        by_repo = {r["repo"]: r for r in out["results"]}
        api = self.root / ".worktrees" / "fix-both" / "api"
        self.assertEqual((by_repo["api"]["status"], by_repo["web"]["status"]), ("refused", "created"))
        self.assertIn(str(api), by_repo["api"]["reason"])
        self.assertEqual(sorted(self.record("fix-both")["worktrees"]),
                         [str(api), str(self.root / ".worktrees" / "fix-both" / "web")])

    def test_integration_checks_each_repos_lock(self):
        self.change("start", "--summary", "Fix Both", "--repos", "api,web")
        tree = self.root / ".worktrees" / "fix-both"
        (tree / "web" / "src" / "Main.java").write_text("class Main { int x; }\n")
        self.script("services_lock.py", "acquire", "api", "--task", "other-task")
        # Only web changed, and web's lock is free: api's lock doesn't matter to this check.
        self.assertEqual(self.change("check", "fix-both", "integration")["areas"], [f"web={tree / 'web'}"])
        (tree / "api" / "src" / "Main.java").write_text("class Main { int y; }\n")
        out = self.change("check", "fix-both", "integration", code=1)
        self.assertIn("services lock api is held by task other-task", out["refused"])


PASS = "echo 'Tests run: 1, Failures: 0, Errors: 0, Skipped: 0'"
MESSAGE = "fix shelf rounding\n\nRound counts half up, so a half shelf counts as one.\n"


class PublishStage(Stage):
    """The review and publish gates, on a single-repo workspace whose unit command passes and whose `build` fails."""

    def setUp(self):
        super().setUp()
        self.make_repo(self.root, "pantry", {"app/src/Shelf.java": "class Shelf {}\n", "web/index.ts": "export {}\n",
                                             "vault/README.md": "vault\n"})
        self.configure({
            "version": 1, "features": ["tasks", "worktrees", "tests"],
            "workspace": {"name": "pantry", "layout": "single", "default_branch": "main", "areas": {
                "app": {"unit": PASS, "build": "false", "files": ["*.java"]},
                "web": {"unit": PASS, "files": ["*.ts"]}}},
            "user": {"handle": "jdoe", "name": "Jordan"}, "vault": {"path": "vault"},
            "tracker": {"kind": "vault", "key_prefix": "PT"}, "pr": {"required": False},
        })
        self.wid = "fix-shelf-rounding"
        self.tree = self.root / ".worktrees" / self.wid
        self.publish_dir = self.root / "vault" / "Tasks" / ".publish" / self.wid

    def start(self, edit: bool = True) -> None:
        self.change("start", "--summary", "Fix Shelf Rounding")
        if edit:
            (self.tree / "app" / "src" / "Shelf.java").write_text("class Shelf { int count; }\n")

    def approve(self, text: str = MESSAGE) -> None:
        self.publish_dir.mkdir(parents=True, exist_ok=True)
        (self.publish_dir / "commit.txt").write_text(text)

    def run_check(self, mode: str, area: str = "app", code: int | None = None) -> dict:
        return self.script("run_checks.py", mode, f"{area}={self.tree / area}", code=code)

    # review

    def test_a_worktree_with_no_changes_has_nothing_to_review(self):
        # Scenario "Review With No Diff"
        self.start(edit=False)
        out = self.change("review", self.wid, code=1)
        self.assertFalse(out["ok"])
        self.assertIn("nothing to review", out["refused"])

    def test_an_id_with_no_worktree_has_nothing_to_review(self):
        out = self.change("review", self.wid, code=1)
        self.assertIn("nothing to review", out["refused"])
        self.assertIn("no worktree", out["refused"])

    def test_a_clean_checkout_has_nothing_to_review(self):
        out = self.change("review", "--checkouts", str(self.root), code=1)
        self.assertIn("nothing to review", out["refused"])

    def test_a_change_gives_the_checkouts_for_the_review(self):
        self.start()
        for args in ((self.wid,), ("--checkouts", str(self.tree))):
            out = self.change("review", *args)
            self.assertEqual(out["ok"], True)
            self.assertEqual([(c["repo"], c["path"], c["branch"], c["diff"], c["arg"]) for c in out["checkouts"]],
                             [("pantry", str(self.tree), "jdoe/fix-shelf-rounding", "1 file, +1 −1",
                               f"pantry={self.tree}")])

    def test_review_needs_a_work_id_or_checkouts(self):
        self.assertIn("error", self.change("review", code=1))

    def test_review_never_looks_at_checks_or_messages(self):
        self.start()  # nothing ran, nothing approved
        self.assertTrue(self.change("review", self.wid)["ok"])

    # publish: refusals

    def test_a_checkout_on_the_default_branch_is_refused_by_name(self):
        # Scenario "Publish on the Default Branch"
        (self.root / "app" / "src" / "Shelf.java").write_text("class Shelf { int count; }\n")
        out = self.change("publish", self.wid, "--checkouts", str(self.root), code=1)
        self.assertFalse(out["ok"])
        self.assertIn("is on main, the default branch", out["refused"])
        # With no diff to blame, and with an approved message, it still names the branch.
        sh(self.root, "git", "checkout", "--", ".")
        self.approve()
        out = self.change("publish", self.wid, "--checkouts", str(self.root), code=1)
        self.assertIn("is on main, the default branch", out["refused"])
        self.assertIn("is on main", self.change("publish", self.wid, "--checkouts", str(self.root), "--draft",
                                                code=1)["refused"])

    def test_a_task_worktree_on_the_default_branch_is_refused(self):
        self.start()
        sh(self.root, "git", "checkout", "--detach")  # frees main for the worktree
        sh(self.tree, "git", "stash", "-u")
        sh(self.tree, "git", "checkout", "main")
        sh(self.tree, "git", "stash", "pop")
        self.assertIn("is on main, the default branch", self.change("publish", self.wid, code=1)["refused"])

    def test_a_detached_head_is_refused(self):
        self.start()
        sh(self.tree, "git", "checkout", "--detach")
        self.assertIn("detached HEAD", self.change("publish", self.wid, "--draft", code=1)["refused"])

    def test_a_worktree_with_no_changes_has_nothing_to_publish(self):
        self.start(edit=False)
        self.approve()
        out = self.change("publish", self.wid, code=1)
        self.assertIn("nothing to publish", out["refused"])

    def test_an_id_with_no_worktree_has_nothing_to_publish(self):
        out = self.change("publish", self.wid, code=1)
        self.assertIn("nothing to publish", out["refused"])
        self.assertIn("no worktree", out["refused"])

    def test_no_message_is_not_approved(self):
        self.start()
        out = self.change("publish", self.wid, code=1)
        self.assertIn("no approved commit message for pantry", out["refused"])
        self.assertIn(str(self.publish_dir / "commit.txt"), out["refused"])

    def test_a_message_that_cant_be_committed_is_not_approved(self):
        self.start()
        for text, problem in (("fix shelf rounding\n", "no body"),
                              ("fix shelf rounding\n\nBody.\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n",
                               "trailer or attribution"),
                              ("\n\n", "no subject")):
            self.approve(text)
            self.assertIn(problem, self.change("publish", self.wid, code=1)["refused"])

    def test_a_draft_check_needs_no_message_and_says_where_it_goes(self):
        self.start()
        out = self.change("publish", self.wid, "--draft")
        self.assertEqual((out["ok"], out["draft"], out["pr_required"], out["announce"]), (True, True, False, False))
        self.assertEqual(out["messages"], {"pantry": str(self.publish_dir / "commit.txt")})
        self.assertEqual(out["announce_file"], str(self.publish_dir / "announce.txt"))
        self.assertEqual(out["checkouts"][0]["diff"], "1 file, +1 −1")
        self.assertEqual(out["bin"], str(TOOLBOX / "bin"))

    def test_publish_runs_nothing_and_writes_nothing(self):
        self.start()
        self.approve()
        before = sh(self.tree, "git", "status", "--porcelain"), sh(self.tree, "git", "rev-parse", "HEAD")
        self.change("publish", self.wid)
        self.assertEqual((sh(self.tree, "git", "status", "--porcelain"), sh(self.tree, "git", "rev-parse", "HEAD")),
                         before)
        self.assertEqual(self.record(self.wid)["status"], "in-progress")

    # publish: the checks warning

    def test_changes_nobody_checked_warn_and_still_go_ahead_once_approved(self):
        # Scenario "Publish Without Checks"
        self.start()
        out = self.change("publish", self.wid, "--draft")
        self.assertEqual(len(out["warnings"]), 1)
        self.assertIn("checks haven't run on the current diff of pantry's app", out["warnings"][0])
        self.assertEqual(out["checks"][0]["unchecked"], ["app"])
        self.approve()
        out = self.change("publish", self.wid)
        self.assertTrue(out["ok"])
        self.assertIn("checks haven't run", out["warnings"][0])

    def test_a_passing_run_on_the_current_diff_clears_the_warning(self):
        self.start()
        self.run_check("unit")
        out = self.change("publish", self.wid, "--draft")
        self.assertEqual(out["warnings"], [])
        self.assertEqual(out["checks"][0]["results"], {"app": {"passed": ["unit"], "failed": []}})

    def test_an_edit_after_the_run_makes_it_stale(self):
        self.start()
        self.run_check("unit")
        (self.tree / "app" / "src" / "Shelf.java").write_text("class Shelf { int count = 1; }\n")
        self.assertEqual(len(self.change("publish", self.wid, "--draft")["warnings"]), 1)
        self.run_check("unit")
        self.assertEqual(self.change("publish", self.wid, "--draft")["warnings"], [])

    def test_a_new_untracked_file_after_the_run_makes_it_stale(self):
        self.start()
        self.run_check("unit")
        (self.tree / "app" / "src" / "Bin.java").write_text("class Bin {}\n")
        self.assertEqual(len(self.change("publish", self.wid, "--draft")["warnings"]), 1)

    def test_committing_the_checked_diff_keeps_the_run_current(self):
        self.start()
        self.run_check("unit")
        sh(self.tree, "git", "add", "-A")
        sh(self.tree, "git", "commit", "-m", "x")
        self.assertEqual(self.change("publish", self.wid, "--draft")["warnings"], [])

    def test_a_failed_run_is_not_a_check(self):
        self.start()
        self.run_check("build", code=1)
        out = self.change("publish", self.wid, "--draft")
        self.assertEqual(len(out["warnings"]), 1)
        self.assertEqual(out["checks"][0]["results"], {"app": {"passed": [], "failed": ["build"]}})

    def test_a_skipped_run_leaves_no_mark(self):
        self.start()
        self.run_check("integration")  # no integration command: skipped
        self.run_check("format")  # no format command: skipped
        self.assertEqual(self.change("publish", self.wid, "--draft")["checks"][0]["results"], {})

    def test_only_the_areas_the_change_touches_need_a_run(self):
        self.start()
        (self.tree / "web" / "index.ts").write_text("export const a = 1\n")
        self.run_check("unit", "app")
        out = self.change("publish", self.wid, "--draft")
        self.assertEqual(out["checks"][0]["unchecked"], ["web"])
        self.run_check("unit", "web")
        self.assertEqual(self.change("publish", self.wid, "--draft")["warnings"], [])

    def test_a_change_no_area_looks_at_has_nothing_to_warn_about(self):
        self.start(edit=False)
        (self.tree / "app" / "README.md").write_text("notes\n")
        out = self.change("publish", self.wid, "--draft")
        self.assertEqual((out["warnings"], out["checks"][0]["areas"]), ([], []))

    def test_the_marks_live_in_the_git_dir_so_the_diff_never_sees_them(self):
        self.start()
        self.run_check("unit")
        self.assertEqual(sh(self.tree, "git", "status", "--porcelain"), "M app/src/Shelf.java")
        marks = Path(sh(self.tree, "git", "rev-parse", "--absolute-git-dir")) / "toolbox-checks.json"
        self.assertEqual(json.loads(marks.read_text())["app"]["unit"]["result"], "pass")

    def test_a_config_problem_is_not_a_failed_check(self):
        self.start()
        self.run_check("unit", "app")  # a pass first
        out = self.script("run_checks.py", "unit", f"app={self.tree / 'nope'}", code=1)
        self.assertEqual(out["areas"][0]["result"], "fail")
        self.assertEqual(self.change("publish", self.wid, "--draft")["checks"][0]["results"],
                         {"app": {"passed": ["unit"], "failed": []}})

    def test_a_non_utf8_change_has_its_own_fingerprint(self):
        sys.path.insert(0, str(TOOLBOX / "lib"))
        import checkmarks
        self.start()
        data = self.tree / "app" / "src" / "Blob.bin"
        data.write_bytes(b"\xff\xfe one")
        first = checkmarks.fingerprint(self.tree, "origin/main")
        data.write_bytes(b"\xfe\xff one")  # both decode to the same replacement characters
        self.assertNotEqual(first, checkmarks.fingerprint(self.tree, "origin/main"))

    def test_a_run_outside_a_git_checkout_still_reports(self):
        folder = self.tmp / "plain"
        folder.mkdir()
        out = self.script("run_checks.py", "unit", f"app={folder}", code=0)
        self.assertEqual(out["result"], "pass")


class NestedPublishStage(Stage):
    def setUp(self):
        super().setUp()
        for name in ("api", "web"):
            self.make_repo(self.root / name, name, {"src/Main.java": "class Main {}\n"})
        (self.root / "vault").mkdir()
        self.configure({
            "version": 1, "features": ["tasks", "worktrees", "tests"],
            "workspace": {"name": "fleet", "layout": "nested", "default_branch": "main",
                          "areas": {"*": {"unit": PASS, "files": ["*.java"]}}},
            "user": {"handle": "jdoe", "name": "Jordan"}, "vault": {"path": "vault"},
            "tracker": {"kind": "vault", "key_prefix": "PT"}, "pr": {"required": False},
        })
        self.change("start", "--summary", "Fix Both", "--repos", "api,web")
        self.tree = self.root / ".worktrees" / "fix-both"
        self.publish_dir = self.root / "vault" / "Tasks" / ".publish" / "fix-both"

    def test_each_repo_has_its_own_message_and_checks(self):
        (self.tree / "api" / "src" / "Main.java").write_text("class Main { int a; }\n")
        (self.tree / "web" / "src" / "Main.java").write_text("class Main { int w; }\n")
        self.script("run_checks.py", "unit", f"api={self.tree / 'api'}")
        out = self.change("publish", "fix-both", "--draft")
        self.assertEqual(out["messages"], {"api": str(self.publish_dir / "api.txt"),
                                           "web": str(self.publish_dir / "web.txt")})
        self.assertEqual([(c["repo"], c["unchecked"]) for c in out["checks"]], [("api", []), ("web", ["web"])])
        self.assertEqual(len(out["warnings"]), 1)
        self.publish_dir.mkdir(parents=True)
        (self.publish_dir / "api.txt").write_text(MESSAGE)
        out = self.change("publish", "fix-both", code=1)
        self.assertIn("no approved commit message for web", out["refused"])
        (self.publish_dir / "web.txt").write_text(MESSAGE)
        self.assertTrue(self.change("publish", "fix-both")["ok"])

    def test_only_the_changed_repos_are_reviewed(self):
        (self.tree / "web" / "src" / "Main.java").write_text("class Main { int w; }\n")
        out = self.change("review", "fix-both")
        self.assertEqual([c["repo"] for c in out["checkouts"]], ["web"])
        self.assertEqual(out["checkouts"][0]["arg"], f"web={self.tree / 'web'}")

    def test_a_repo_without_changes_needs_no_message_and_is_left_out(self):
        (self.tree / "web" / "src" / "Main.java").write_text("class Main { int w; }\n")
        self.publish_dir.mkdir(parents=True)
        (self.publish_dir / "web.txt").write_text(MESSAGE)  # api has no message and no changes
        out = self.change("publish", "fix-both")
        self.assertEqual([c["repo"] for c in out["checkouts"]], ["web"])
        self.assertEqual(list(out["messages"]), ["web"])
        self.assertEqual([c["repo"] for c in out["checks"]], ["web"])
        self.assertEqual(list(self.change("publish", "fix-both", "--draft")["messages"]), ["web"])

    def test_an_untouched_repo_on_the_default_branch_doesnt_block_the_other(self):
        (self.tree / "web" / "src" / "Main.java").write_text("class Main { int w; }\n")
        self.publish_dir.mkdir(parents=True)
        (self.publish_dir / "web.txt").write_text(MESSAGE)
        sh(self.tree / "api", "git", "checkout", "--detach")
        self.assertTrue(self.change("publish", "fix-both")["ok"])
        # A repo with changes on the default branch still refuses, naming it.
        sh(self.root / "api", "git", "checkout", "--detach")
        sh(self.tree / "api", "git", "checkout", "main")
        (self.tree / "api" / "src" / "Main.java").write_text("class Main { int a; }\n")
        out = self.change("publish", "fix-both", code=1)
        self.assertIn("api", out["refused"])
        self.assertIn("is on main, the default branch", out["refused"])


class FinishStage(Stage):
    """The finish gate, on a single-repo workspace with PRs that need an approving review and green checks. The fake
    `gh` keeps the PRs in a JSON file the tests edit."""

    def setUp(self):
        super().setUp()
        self.make_repo(self.root, "pantry", {"app/src/Shelf.java": "class Shelf {}\n", "vault/README.md": "vault\n"})
        self.configure({
            "version": 1, "features": ["tasks", "worktrees", "pr"],
            "workspace": {"name": "pantry", "layout": "single", "default_branch": "main",
                          "areas": {"app": {"unit": PASS, "files": ["*.java"]}}},
            "user": {"handle": "jdoe", "name": "Jordan"}, "vault": {"path": "vault"},
            "tracker": {"kind": "vault", "key_prefix": "PT"}, "git": {"org": "acme"},
            "pr": {"required": True, "wait_for_ci": True, "review_required": True},
        })
        self.wid = "fix-shelf-rounding"
        self.tree = self.root / ".worktrees" / self.wid
        self.gh = self.tmp / "gh.json"
        self.env["GH_FAKE_GREEN"] = "1"

    def published(self) -> None:
        """A change with an open PR: started, edited, committed and pushed by publish.py."""
        self.change("start", "--summary", "Fix Shelf Rounding")
        (self.tree / "app" / "src" / "Shelf.java").write_text("class Shelf { int count; }\n")
        folder = self.root / "vault" / "Tasks" / ".publish" / self.wid
        folder.mkdir(parents=True)
        (folder / "commit.txt").write_text(MESSAGE)
        self.script("publish.py", "run", self.wid, "--stop-after", "open-pr", "--no-announce")

    def edit_gh(self, edit) -> None:
        state = json.loads(self.gh.read_text())
        edit(state)
        self.gh.write_text(json.dumps(state))

    def pr(self) -> dict:
        return json.loads(self.gh.read_text())["prs"][0]

    def approve_pr(self) -> None:
        self.edit_gh(lambda s: s["prs"][0].update(reviewDecision="APPROVED"))

    def test_an_approved_green_change_is_ready_and_the_scripts_then_finish_it(self):
        # Scenario "Approved and Green"
        self.published()
        self.approve_pr()
        out = self.change("finish", self.wid)
        self.assertEqual((out["ok"], out["merge_needed"], out["merge_method"]), (True, True, "squash"))
        self.assertEqual([(c["repo"], c["status"], c["number"]) for c in out["checkouts"]], [("pantry", "ready", 1)])
        self.assertEqual(out["merge_command"], f"publish.py merge {self.wid}")
        self.assertEqual(self.pr()["state"], "OPEN")  # the gate merges nothing
        self.assertTrue(self.tree.exists())
        merged = self.script("publish.py", "merge", self.wid)
        self.assertEqual(merged["merged"][0]["repo"], "pantry")
        cleaned = self.script("publish.py", "cleanup", self.wid)
        self.assertEqual([w["status"] for w in cleaned["worktrees"]], ["removed"])
        self.assertEqual(self.pr()["state"], "MERGED")
        self.assertFalse(self.tree.exists())
        self.assertEqual(self.record(self.wid)["status"], "done")

    def test_an_untracked_file_refuses_by_name_and_nothing_is_merged(self):
        # Scenario "Untracked File in a Worktree"
        self.published()
        self.approve_pr()
        (self.tree / "scratch.txt").write_text("x\n")
        out = self.change("finish", self.wid, code=1)
        self.assertFalse(out["ok"])
        self.assertIn("scratch.txt", out["refused"])
        self.assertIn("pantry", out["refused"])
        self.assertEqual(self.pr()["state"], "OPEN")
        self.assertTrue((self.tree / "scratch.txt").exists())
        self.assertNotEqual(self.record(self.wid)["status"], "done")

    def test_an_edit_the_pr_lacks_is_refused_even_when_the_pr_is_merged(self):
        self.published()
        self.approve_pr()
        self.script("publish.py", "merge", self.wid)
        (self.tree / "app" / "src" / "Shelf.java").write_text("class Shelf { int late; }\n")
        out = self.change("finish", self.wid, code=1)
        self.assertIn("app/src/Shelf.java", out["refused"])

    def test_a_pr_nobody_approved_is_refused(self):
        self.published()
        out = self.change("finish", self.wid, code=1)
        self.assertIn("review", out["refused"])
        self.assertIn("Nothing was merged or removed", out["refused"])
        self.assertEqual(self.pr()["state"], "OPEN")

    def test_without_review_required_approval_is_not_asked_for(self):
        raw = yaml.safe_load((self.root / ".claude" / "toolbox.local.yml").read_text())
        raw["pr"]["review_required"] = False
        (self.root / ".claude" / "toolbox.local.yml").write_text(yaml.safe_dump(raw))
        self.published()
        self.assertTrue(self.change("finish", self.wid)["ok"])

    def test_failing_checks_are_refused(self):
        self.published()
        self.approve_pr()
        self.edit_gh(lambda s: s["ci"].update({"1": [[{"name": "build", "status": "COMPLETED",
                                                        "conclusion": "FAILURE"}]]}))
        out = self.change("finish", self.wid, code=1)
        self.assertIn("checks failed: build", out["refused"])

    def test_a_pr_behind_the_default_branch_is_refused_with_the_way_out(self):
        self.published()
        self.approve_pr()
        self.edit_gh(lambda s: s["prs"][0].update(mergeStateStatus="BEHIND"))
        self.assertIn("run push to rebase", self.change("finish", self.wid, code=1)["refused"])

    def test_a_conflicting_pr_is_refused(self):
        self.published()
        self.approve_pr()
        self.edit_gh(lambda s: s["prs"][0].update(mergeable="CONFLICTING"))
        self.assertIn("conflicting", self.change("finish", self.wid, code=1)["refused"])

    def test_unpushed_commits_are_refused(self):
        self.published()
        self.approve_pr()
        (self.tree / "late.txt").write_text("x\n")
        sh(self.tree, "git", "add", "-A")
        sh(self.tree, "git", "commit", "-m", "late")
        self.assertIn("isn't pushed", self.change("finish", self.wid, code=1)["refused"])

    def test_a_branch_with_no_pr_is_refused(self):
        self.change("start", "--summary", "Fix Shelf Rounding")
        self.assertIn("no PR for the branch", self.change("finish", self.wid, code=1)["refused"])

    def test_a_merged_pr_is_ready_for_cleanup_alone(self):
        self.published()
        self.approve_pr()
        self.script("publish.py", "merge", self.wid)
        out = self.change("finish", self.wid)
        self.assertEqual((out["merge_needed"], out["checkouts"][0]["status"]), (False, "already-merged"))
        self.script("publish.py", "cleanup", self.wid)
        self.assertEqual(self.record(self.wid)["status"], "done")

    def test_an_id_with_no_worktree_has_nothing_to_finish(self):
        self.assertIn("nothing to finish", self.change("finish", self.wid, code=1)["refused"])

    def test_the_default_branch_has_no_change_to_finish(self):
        out = self.change("finish", self.wid, "--checkouts", "pantry", code=1)
        self.assertIn("main, the default branch", out["refused"])

    def test_a_squash_of_several_commits_is_reported_for_confirmation(self):
        self.published()
        self.approve_pr()
        (self.tree / "second.txt").write_text("x\n")
        sh(self.tree, "git", "add", "-A")
        sh(self.tree, "git", "commit", "-m", "second")
        sh(self.tree, "git", "push")
        out = self.change("finish", self.wid)
        self.assertEqual(out["confirm_squash"], ["pantry"])
        self.assertEqual(out["checkouts"][0]["commits"], ["fix shelf rounding", "second"])

    def test_checkouts_outside_a_task_name_the_commands_without_a_record(self):
        self.published()
        self.approve_pr()
        out = self.change("finish", self.wid, "--checkouts", str(self.tree))
        self.assertIn(f"--checkouts {self.tree} --no-record", out["merge_command"])
        self.assertIn("--no-record", out["cleanup_command"])


def sh(cwd: Path, *args: str) -> str:
    import subprocess
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class SkillTextTest(unittest.TestCase):
    NAMES = ("start-change", "check-change", "review-change", "publish-change", "finish-change")

    def head(self, name: str) -> dict:
        path = SKILLS / name / "SKILL.md"
        head, _ = frontmatter.split(path.read_text(), path)
        return yaml.safe_load(head)

    def test_each_skill_has_one_description_line_and_its_name(self):
        for name in self.NAMES:
            text = (SKILLS / name / "SKILL.md").read_text()
            self.assertEqual(len(re.findall(r"^description:", text, re.M)), 1, name)
            self.assertEqual(self.head(name)["name"], name)

    def test_the_skills_name_the_script_commands_that_exist(self):
        import subprocess
        for name, command in (("start-change", "start"), ("check-change", "check"), ("review-change", "review"),
                              ("publish-change", "publish"), ("finish-change", "finish")):
            text = (SKILLS / name / "SKILL.md").read_text()
            self.assertIn(f".claude/toolbox/bin/change.py {command} ", text)
            proc = subprocess.run([sys.executable, str(TOOLBOX / "bin" / "change.py"), command, "--help"],
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_start_names_its_refusal_and_the_ad_hoc_record(self):
        text = " ".join((SKILLS / "start-change" / "SKILL.md").read_text().split())
        self.assertIn("already has a worktree", text)
        self.assertIn("keyless record", text)
        self.assertIn("never checks that anything ran before it", text)

    def test_check_names_its_refusals_and_delegates_to_the_runner(self):
        text = " ".join((SKILLS / "check-change" / "SKILL.md").read_text().split())
        self.assertIn("no change to check", text)
        self.assertIn("services lock is held", text)
        self.assertIn("`subagent_type: check-runner`", text)
        self.assertIn("never that an earlier stage ran", text)
        self.assertIn("Agent", self.head("check-change")["allowed-tools"])
        self.assertTrue((CLAUDE / "agents" / "check-runner.md").is_file())

    def test_review_names_its_refusal_and_opens_the_review_page(self):
        text = " ".join((SKILLS / "review-change" / "SKILL.md").read_text().split())
        self.assertIn("nothing to review", text)
        self.assertIn("`local-review-ui`", text)
        self.assertIn("Finishing Is Not Approval", text)
        self.assertIn("Skill", self.head("review-change")["allowed-tools"])
        self.assertTrue((SKILLS / "local-review-ui" / "SKILL.md").is_file())

    def test_publish_names_its_gates_and_drafts_through_the_skill(self):
        text = " ".join((SKILLS / "publish-change" / "SKILL.md").read_text().split())
        for phrase in ("on the default branch", "nothing to publish", "checks haven't run",
                       "`git-draft-commit-message`", "`diff-summarizer`", "publish.py run", "--stop-after open-pr",
                       "never that review, verification or checks ran", "It never merges", "--draft"):
            self.assertIn(phrase, text)
        self.assertIn("Skill", self.head("publish-change")["allowed-tools"])
        self.assertTrue((SKILLS / "git-draft-commit-message" / "SKILL.md").is_file())
        self.assertIn("--stop-after", (TOOLBOX / "bin" / "publish.py").read_text())

    def test_the_publish_skill_never_runs_git_writes_itself(self):
        text = (SKILLS / "publish-change" / "SKILL.md").read_text()
        for command in ("git commit", "git push", "gh pr create", "gh pr merge"):
            self.assertNotIn(command, text)

    def test_finish_names_its_refusals_and_merges_through_the_scripts(self):
        text = " ".join((SKILLS / "finish-change" / "SKILL.md").read_text().split())
        for phrase in ("uncommitted or untracked", "isn't approved", "checks", "mergeable", "publish.py merge",
                       "publish.py cleanup", "--confirm-squash", "never that review, verification or checks ran",
                       "`gh-merge-pull-request`"):
            self.assertIn(phrase, text)
        self.assertTrue((SKILLS / "gh-merge-pull-request" / "SKILL.md").is_file())

    def test_the_finish_skill_never_runs_git_or_gh_writes_itself(self):
        text = (SKILLS / "finish-change" / "SKILL.md").read_text()
        for command in ("git commit", "git push", "git worktree remove", "git branch -D", "gh pr merge"):
            self.assertNotIn(command, text)

    def test_neither_skill_names_a_project_skill(self):
        for name in self.NAMES:
            self.assertNotRegex((SKILLS / name / "SKILL.md").read_text(), r"`rm-[a-z-]+`")

    def test_installed_with_the_features_they_need(self):
        features = yaml.safe_load(FEATURES.read_text())
        paths = {f: v["paths"] if isinstance(v, dict) else v for f, v in features.items()}
        for feature in ("tasks", "worktrees"):  # the record and the worktrees
            self.assertIn(".claude/skills/start-change", paths[feature])
        for feature in ("tests", "worktrees"):  # the runner, and a task worktree to check
            self.assertIn(".claude/skills/check-change", paths[feature])
        for feature in ("review-ui", "worktrees"):  # the review page, and a task worktree to review
            self.assertIn(".claude/skills/review-change", paths[feature])
        for feature in ("git", "worktrees"):  # the message draft, and a task worktree to publish
            self.assertIn(".claude/skills/publish-change", paths[feature])
        for feature in ("pr", "worktrees"):  # the merge, and a task worktree to remove
            self.assertIn(".claude/skills/finish-change", paths[feature])


if __name__ == "__main__":
    unittest.main()
