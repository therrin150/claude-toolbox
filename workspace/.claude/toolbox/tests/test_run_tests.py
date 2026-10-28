"""Tests for bin/run_tests.py and bin/summarize_reports.py, with fake build commands and hand-written Maven reports.

Run from the repo root:

    python3 -m unittest discover -s workspace/.claude/toolbox/tests
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

TOOLBOX = Path(__file__).resolve().parents[1]
RUN_TESTS = TOOLBOX / "bin" / "run_tests.py"
SUMMARIZE = TOOLBOX / "bin" / "summarize_reports.py"

PASSING_SUITE = ('<testsuite tests="3" failures="0" errors="0" skipped="1" time="0.5">'
                 '<testcase classname="a.FooTest" name="x"/></testsuite>')
FAILING_SUITE = ('<testsuite tests="2" failures="1" errors="0" skipped="0" time="0.2">'
                 '<testcase classname="a.BarTest" name="breaks"><failure message="expected 1 but was 2">trace</failure>'
                 '</testcase></testsuite>')


class RunTestsCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = self.tmp / "ws"
        (self.root / ".claude").mkdir(parents=True)

    def configure(self, areas: dict) -> None:
        raw = {"version": 1, "workspace": {"name": "stack", "layout": "single", "areas": areas},
               "user": {"handle": "jdoe", "name": "Jordan"}, "vault": {"path": "vault"}, "tracker": {"kind": "none"}}
        (self.root / ".claude" / "toolbox.local.yml").write_text(yaml.safe_dump(raw), encoding="utf-8")

    def folder(self, name: str) -> Path:
        path = self.root / name
        path.mkdir(exist_ok=True)
        return path

    def run_script(self, *args: str) -> tuple[int, str]:
        env = {**os.environ, "TOOLBOX_WORKSPACE": str(self.root), "TMPDIR": str(self.tmp)}
        result = subprocess.run([sys.executable, str(RUN_TESTS), *args], capture_output=True, text=True, env=env,
                                cwd=self.tmp, timeout=60)
        return result.returncode, result.stdout + result.stderr

    def reports(self, area: str, kind: str, suite: str) -> None:
        folder = self.folder(area) / "target" / f"{kind}-reports"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "TEST-a.Foo.xml").write_text(suite)


class NonMavenTest(RunTestsCase):
    def test_passing_command(self):
        self.configure({"web": {"unit": "echo all good"}})
        self.folder("web")
        code, out = self.run_script("unit", "web")
        self.assertEqual(code, 0)
        self.assertIn("web (unit): pass · exit 0", out)

    def test_failing_command_names_the_first_error_line_and_the_log(self):
        self.configure({"web": {"unit": "echo starting; echo 'Error: widget broke'; echo more; exit 2"}})
        self.folder("web")
        code, out = self.run_script("unit", "web", "--label", "T-1")
        self.assertEqual(code, 1)
        self.assertIn("web (unit): fail · exit 2", out)
        self.assertIn("first error: Error: widget broke", out)
        self.assertIn(str(self.tmp / "T-1-web-unit.log"), out)

    def test_area_without_a_command_is_skipped_not_failed(self):
        self.configure({"web": {"unit": "true"}, "api": {"start": "true"}})
        self.folder("web"), self.folder("api")
        code, out = self.run_script("unit", "web", "api")
        self.assertEqual(code, 0)
        self.assertIn("api (unit): skipped", out)

    def test_format_runs_in_the_given_folder_and_ignores_maven_reports(self):
        self.configure({"web": {"format": "touch formatted"}, "api": {"unit": "true"}})
        self.reports("web", "surefire", FAILING_SUITE)
        tree = self.tmp / "tree" / "web"
        tree.mkdir(parents=True)
        code, out = self.run_script("format", f"web={tree}", "api")
        self.assertEqual(code, 0)
        self.assertIn("web (format): pass · exit 0", out)
        self.assertIn("api (format): skipped", out)
        self.assertTrue((tree / "formatted").exists())

    def test_failed_format_names_the_error(self):
        self.configure({"web": {"format": "echo 'error: bad syntax in a.ts'; exit 1"}})
        self.folder("web")
        code, out = self.run_script("format", "web")
        self.assertEqual(code, 1)
        self.assertIn("first error: error: bad syntax in a.ts", out)

    def test_star_area_gives_every_area_the_same_command(self):
        self.configure({"*": {"integration": "echo ran in $(basename $PWD)"}})
        self.folder("one"), self.folder("two")
        code, out = self.run_script("integration", "one", "two")
        self.assertEqual(code, 0)
        self.assertIn("one (integration): pass", out)
        self.assertIn("two (integration): pass", out)

    def test_area_equals_dir_runs_there(self):
        self.configure({"app": {"unit": "test -f marker"}})
        elsewhere = self.tmp / "worktree" / "app"
        elsewhere.mkdir(parents=True)
        (elsewhere / "marker").write_text("")
        code, out = self.run_script("unit", f"app={elsewhere}")
        self.assertEqual((code, "pass" in out), (0, True))

    def test_test_replaces_the_placeholder_or_fails_without_one(self):
        self.configure({"a": {"unit": "test '{test}' = FooTest"}, "b": {"unit": "true"}})
        self.folder("a"), self.folder("b")
        code, out = self.run_script("unit", "a", "--test", "FooTest")
        self.assertEqual(code, 0, out)
        code, out = self.run_script("unit", "b", "--test", "FooTest")
        self.assertEqual(code, 1)
        self.assertIn("no {test}", out)

    def test_without_test_the_placeholder_is_empty(self):
        self.configure({"a": {"unit": "test 'x{test}' = x"}})
        self.folder("a")
        code, out = self.run_script("unit", "a")
        self.assertEqual(code, 0, out)

    def test_a_missing_folder_fails(self):
        self.configure({"gone": {"unit": "true"}})
        code, out = self.run_script("unit", "gone")
        self.assertEqual(code, 1)
        self.assertIn("isn't a folder", out)


class MavenTest(RunTestsCase):
    def test_surefire_reports_make_the_report(self):
        self.configure({"app": {"unit": "true"}})
        self.folder("app")
        # The command writes its reports, as a build would, after the run starts.
        self.configure({"app": {"unit": f"mkdir -p target/surefire-reports && printf '%s' '{PASSING_SUITE}' "
                                        "> target/surefire-reports/TEST-a.Foo.xml; echo '[INFO] BUILD SUCCESS'"}})
        code, out = self.run_script("unit", "app")
        self.assertEqual(code, 0, out)
        self.assertIn("app (surefire): pass · 3 tests, 0 failed, 1 skipped", out)

    def test_failed_test_shows_its_first_assertion_line(self):
        self.configure({"app": {"unit": f"mkdir -p target/surefire-reports && printf '%s' '{FAILING_SUITE}' "
                                        "> target/surefire-reports/TEST-a.Bar.xml; exit 1"}})
        self.folder("app")
        code, out = self.run_script("unit", "app")
        self.assertEqual(code, 1)
        self.assertIn("BarTest#breaks [failure]: expected 1 but was 2", out)

    def test_stale_reports_from_an_earlier_run_are_not_read(self):
        self.reports("app", "surefire", PASSING_SUITE)
        old = 1_000_000_000
        os.utime(self.root / "app" / "target" / "surefire-reports" / "TEST-a.Foo.xml", (old, old))
        self.configure({"app": {"unit": "exit 1"}})
        code, out = self.run_script("unit", "app")
        self.assertEqual(code, 1)
        self.assertIn("fail · exit 1", out)

    def test_integration_falls_back_to_surefire_when_failsafe_found_none(self):
        self.configure({"app": {"integration": f"mkdir -p target/surefire-reports && printf '%s' '{PASSING_SUITE}' "
                                                "> target/surefire-reports/TEST-a.Foo.xml; "
                                                "echo '[INFO] BUILD SUCCESS'"}})
        self.folder("app")
        code, out = self.run_script("integration", "app")
        self.assertEqual(code, 0, out)
        self.assertIn("app (surefire): pass", out)

    def test_a_build_that_never_reached_its_tests_is_not_a_pass(self):
        self.configure({"app": {"unit": "echo '[ERROR] COMPILATION ERROR : Foo.java'; echo '[INFO] BUILD FAILURE'; "
                                        "exit 1"}})
        self.folder("app")
        code, out = self.run_script("unit", "app")
        self.assertEqual(code, 1)
        self.assertIn("build-failed", out)
        self.assertIn("COMPILATION ERROR", out)


class SummarizeTest(RunTestsCase):
    def test_json_and_exit_code(self):
        self.reports("app", "failsafe", FAILING_SUITE)
        result = subprocess.run([sys.executable, str(SUMMARIZE), str(self.root / "app"), "--kind", "failsafe"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("BarTest#breaks", result.stdout)


if __name__ == "__main__":
    unittest.main()
