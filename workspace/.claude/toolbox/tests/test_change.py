"""The `start-change` and `check-change` stage skills: `bin/change.py`'s gates against real git worktrees, and the
skills' text.

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


def sh(cwd: Path, *args: str) -> str:
    import subprocess
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class SkillTextTest(unittest.TestCase):
    NAMES = ("start-change", "check-change")

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
        for name, command in (("start-change", "start"), ("check-change", "check")):
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


if __name__ == "__main__":
    unittest.main()
