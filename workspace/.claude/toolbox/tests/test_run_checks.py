"""Tests for bin/run_checks.py: result extraction from Maven-style output, own-code stack trimming, placeholders, and the
integration bracket (lock, start, tests, stop, release) with fake commands and a real services lock.

Run from the repo root:

    python3 -m unittest discover -s workspace/.claude/toolbox/tests
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import yaml

TOOLBOX = Path(__file__).resolve().parents[1]
BIN = TOOLBOX / "bin"
RUN_CHECKS = BIN / "run_checks.py"
SERVICES_LOCK = BIN / "services_lock.py"

sys.path.insert(0, str(BIN))
sys.path.insert(0, str(TOOLBOX / "lib"))
import run_checks  # noqa: E402

FAILING_LOG = """\
[INFO] -------------------------------------------------------
[INFO]  T E S T S
[INFO] -------------------------------------------------------
[INFO] Running org.familysearch.FooTest
[ERROR] Tests run: 3, Failures: 1, Errors: 1, Skipped: 0, Time elapsed: 0.05 s <<< FAILURE! -- in org.familysearch.FooTest
[ERROR] org.familysearch.FooTest.breaks -- Time elapsed: 0.01 s <<< FAILURE!
org.opentest4j.AssertionFailedError: expected: <1> but was: <2>
\tat org.junit.jupiter.api.AssertionFailureBuilder.build(AssertionFailureBuilder.java:151)
\tat org.junit.jupiter.api.AssertEquals.fail(AssertEquals.java:55)
\tat org.familysearch.FooTest.breaks(FooTest.java:12)
\tat java.base/jdk.internal.reflect.DirectMethodHandleAccessor.invoke(DirectMethodHandleAccessor.java:103)
\tat org.springframework.Foo.bar(Foo.java:1)

[ERROR] org.familysearch.FooTest.explodes -- Time elapsed: 0.01 s <<< ERROR!
java.lang.IllegalStateException: wrapper
\tat org.familysearch.Service.run(Service.java:40)
\tat org.springframework.Foo.bar(Foo.java:1)
\tat com.allensudweeks.Other.go(Other.java:9)
Caused by: java.io.IOException: disk
\tat java.base/java.io.File.read(File.java:1)
\tat org.familysearch.Repo.load(Repo.java:7)
\t... 12 more

[INFO]
[INFO] Results:
[ERROR] Failures:
[ERROR]   FooTest.breaks:12 expected: <1> but was: <2>
[ERROR] Tests run: 3, Failures: 1, Errors: 1, Skipped: 0
[INFO] BUILD FAILURE
"""

PASSING_LOG = """\
[INFO] Running org.familysearch.FooTest
[INFO] Tests run: 4, Failures: 0, Errors: 0, Skipped: 0, Time elapsed: 0.05 s -- in org.familysearch.FooTest
[INFO] Tests run: 5, Failures: 0, Errors: 0, Skipped: 1
[INFO] BUILD SUCCESS
"""

OLD_STYLE_LOG = """\
Running org.familysearch.BarTest
Tests run: 2, Failures: 1, Errors: 0, Skipped: 0, Time elapsed: 0.1 sec <<< FAILURE!
breaks(org.familysearch.BarTest)  Time elapsed: 0.02 sec  <<< FAILURE!
java.lang.AssertionError:
Expecting actual:
  "left"
to be equal to:
  "right"
\tat org.familysearch.BarTest.breaks(BarTest.java:30)

Results :

Failed tests:
  breaks(org.familysearch.BarTest)

Tests run: 2, Failures: 1, Errors: 0, Skipped: 0
"""


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


class ParsingTest(unittest.TestCase):
    """The functions that read output, without running anything."""

    def lines(self, text: str) -> list[str]:
        return text.splitlines()

    def test_old_surefire_header_and_assertj_expected_actual(self):
        failures = run_checks.parse_failures(self.lines(OLD_STYLE_LOG), ["org.familysearch."])
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["test"], "BarTest#breaks")
        self.assertEqual(failures[0]["expected"], '"right"')
        self.assertEqual(failures[0]["actual"], '"left"')
        self.assertEqual(failures[0]["stack"], ["at org.familysearch.BarTest.breaks(BarTest.java:30)"])

    def test_assertj_equals_on_two_lines(self):
        self.assertEqual(run_checks.expected_actual("\nexpected: 2\n but was: 1"), ("2", "1"))

    def test_message_without_expected_and_actual(self):
        self.assertEqual(run_checks.expected_actual("boom"), (None, None))

    def test_no_prefixes_keeps_every_frame(self):
        frames = ["\tat a.B.c(B.java:1)", "\tat x.Y.z(Y.java:2)", "Caused by: java.io.IOException: x",
                  "\t... 3 more"]
        self.assertEqual(run_checks.trim_stack(frames, []),
                         ["at a.B.c(B.java:1)", "at x.Y.z(Y.java:2)", "Caused by: java.io.IOException: x"])

    def test_prefixes_keep_the_first_frame_causes_and_own_frames(self):
        frames = ["\tat a.B.c(B.java:1)", "\tat x.Y.z(Y.java:2)", "\tat own.Q.r(Q.java:3)",
                  "Caused by: java.io.IOException: x", "\tat java.base/own.S.t(S.java:4)"]
        self.assertEqual(run_checks.trim_stack(frames, ["own."]),
                         ["at a.B.c(B.java:1)", "at own.Q.r(Q.java:3)", "Caused by: java.io.IOException: x",
                          "at java.base/own.S.t(S.java:4)"])

    def test_repeated_failure_is_listed_once(self):
        failures = run_checks.parse_failures(self.lines(FAILING_LOG + FAILING_LOG), [])
        self.assertEqual([f["test"] for f in failures], ["FooTest#breaks", "FooTest#explodes"])


class RunChecksCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = self.tmp / "ws"
        (self.root / ".claude").mkdir(parents=True)
        self.locks = self.tmp / "locks"
        self.env = {**os.environ, "TOOLBOX_WORKSPACE": str(self.root), "TOOLBOX_LOCKS": str(self.locks),
                    "TMPDIR": str(self.tmp)}

    def configure(self, areas: dict, layout: str = "single", prefixes: list[str] | None = None) -> None:
        raw = {"version": 1, "workspace": {"name": "stack", "layout": layout, "areas": areas},
               "user": {"handle": "jdoe", "name": "Jordan"}, "vault": {"path": "vault"}, "tracker": {"kind": "none"},
               "checks": {"own_code_prefixes": prefixes or []}}
        (self.root / ".claude" / "toolbox.local.yml").write_text(yaml.safe_dump(raw), encoding="utf-8")

    def folder(self, name: str, **files: str) -> Path:
        path = self.root / name
        path.mkdir(exist_ok=True)
        for file, text in files.items():
            (path / file.replace("_", ".")).write_text(text)
        return path

    def run_checks(self, *args: str, timeout: float = 60) -> tuple[int, dict | None, str]:
        done = subprocess.run([sys.executable, str(RUN_CHECKS), *args], capture_output=True, text=True, env=self.env,
                              cwd=self.tmp, timeout=timeout)
        try:
            report = json.loads(done.stdout)
        except json.JSONDecodeError:
            report = None
        return done.returncode, report, done.stdout + done.stderr

    def lock(self, *args: str) -> dict:
        done = subprocess.run([sys.executable, str(SERVICES_LOCK), *args], capture_output=True, text=True,
                              env=self.env, cwd=self.tmp)
        return json.loads(done.stdout)

    def assert_no_leftovers(self, output: str) -> None:
        """No log path in what the script printed, and no log file left behind."""
        self.assertNotIn(".log", output)
        self.assertNotIn(str(self.tmp), output)
        self.assertEqual(list(self.tmp.glob("run-checks-*")), [])
        self.assertEqual(list(self.tmp.glob("*start*.log")), [], "the services' start log is removed too")


class UnitTest(RunChecksCase):
    def test_failing_test_reports_assertion_expected_actual_and_trimmed_stacks(self):
        self.configure({"api": {"unit": "cat out.log; exit 1"}}, prefixes=["org.familysearch.", "com.allensudweeks."])
        self.folder("api", out_log=FAILING_LOG)
        code, report, output = self.run_checks("unit", "api")
        self.assertEqual(code, 1)
        self.assertEqual((report["mode"], report["result"]), ("unit", "fail"))
        area = report["areas"][0]
        self.assertEqual((area["result"], area["tests"], area["failed"], area["skipped"]), ("fail", 3, 2, 0))
        breaks, explodes = area["failures"]
        self.assertEqual(breaks["test"], "FooTest#breaks")
        self.assertEqual(breaks["exception"], "org.opentest4j.AssertionFailedError")
        self.assertEqual(breaks["assertion"], "expected: <1> but was: <2>")
        self.assertEqual((breaks["expected"], breaks["actual"]), ("1", "2"))
        self.assertEqual(breaks["stack"], ["at org.junit.jupiter.api.AssertionFailureBuilder.build"
                                           "(AssertionFailureBuilder.java:151)",
                                           "at org.familysearch.FooTest.breaks(FooTest.java:12)"])
        self.assertEqual((explodes["test"], explodes["kind"]), ("FooTest#explodes", "error"))
        self.assertEqual(explodes["assertion"], "wrapper")
        self.assertEqual(explodes["stack"], ["at org.familysearch.Service.run(Service.java:40)",
                                             "at com.allensudweeks.Other.go(Other.java:9)",
                                             "Caused by: java.io.IOException: disk",
                                             "at org.familysearch.Repo.load(Repo.java:7)"])
        self.assertNotIn("springframework", output)
        self.assert_no_leftovers(output)

    def test_passing_run_reports_counts(self):
        self.configure({"api": {"unit": "cat out.log"}})
        self.folder("api", out_log=PASSING_LOG)
        code, report, output = self.run_checks("unit", "api")
        self.assertEqual(code, 0)
        area = report["areas"][0]
        self.assertEqual((area["result"], area["tests"], area["failed"], area["skipped"]), ("pass", 5, 0, 1))
        self.assertEqual(area["failures"], [])
        self.assert_no_leftovers(output)

    def test_a_run_that_ran_no_tests_is_not_a_pass(self):
        self.configure({"api": {"unit": "echo '[INFO] Tests run: 0, Failures: 0, Errors: 0, Skipped: 0'; "
                                        "echo '[INFO] BUILD SUCCESS'"}})
        self.folder("api")
        code, report, _ = self.run_checks("unit", "api")
        self.assertEqual(code, 1)
        self.assertEqual(report["areas"][0]["result"], "fail")
        self.assertIn("ran no tests", report["areas"][0]["reason"])

    def test_non_maven_failure_has_the_first_problem_and_the_tail(self):
        self.configure({"web": {"unit": "echo starting; echo 'Error: widget broke'; echo more; exit 2"}})
        self.folder("web")
        code, report, output = self.run_checks("unit", "web")
        area = report["areas"][0]
        self.assertEqual((code, area["result"], area["exit"]), (1, "fail", 2))
        self.assertEqual(area["problem"], "Error: widget broke")
        self.assertEqual(area["tail"], ["starting", "Error: widget broke", "more"])
        self.assert_no_leftovers(output)

    def test_test_placeholder(self):
        self.configure({"api": {"unit": "echo '[INFO] Tests run: 1, Failures: 0, Errors: 0, Skipped: 0'"}})
        folder = self.folder("api")
        code, report, _ = self.run_checks("unit", "api", "--test", "FooTest#breaks")
        self.assertEqual(report["areas"][0]["result"], "fail", "the command has no {test}")
        self.configure({"api": {"unit": "echo '[INFO] Tests run: 1, Failures: 0, Errors: 0, Skipped: 0'; "
                                        "echo '[INFO] BUILD SUCCESS'; echo -Dtest={test} > picked.txt"}})
        self.run_checks("unit", "api", "--test", "FooTest#breaks")
        self.assertEqual((folder / "picked.txt").read_text().strip(), "-Dtest=FooTest#breaks")
        self.run_checks("unit", "api")
        self.assertEqual((folder / "picked.txt").read_text().strip(), "-Dtest=")

    def test_area_folder_by_name_or_explicit_dir(self):
        self.configure({"api": {"unit": "echo ran-here > where.txt"}})
        elsewhere = self.tmp / "checkout" / "api"
        elsewhere.mkdir(parents=True)
        self.run_checks("unit", f"api={elsewhere}")
        self.assertTrue((elsewhere / "where.txt").exists())

    def test_area_without_a_command_is_skipped(self):
        self.configure({"web": {"unit": "true"}, "api": {"integration": "true"}})
        self.folder("web"), self.folder("api")
        code, report, _ = self.run_checks("unit", "api")
        self.assertEqual((code, report["result"], report["areas"][0]["result"]), (0, "skipped", "skipped"))

    def test_skip_tests_flag_is_for_build_only(self):
        self.configure({"api": {"unit": "true"}})
        self.folder("api")
        code, _, output = self.run_checks("unit", "api", "--skip-tests")
        self.assertEqual(code, 2)
        self.assertIn("--skip-tests goes with build", output)


class BuildAndFormatTest(RunChecksCase):
    def test_skip_tests_fills_the_placeholder(self):
        self.configure({"api": {"build": "echo '[INFO] BUILD SUCCESS'; echo 'flags=[{skip_tests}]' > flags.txt"}})
        folder = self.folder("api")
        code, report, _ = self.run_checks("build", "api", "--skip-tests")
        self.assertEqual((code, report["areas"][0]["result"]), (0, "pass"))
        self.assertEqual((folder / "flags.txt").read_text().strip(), "flags=[-DskipTests]")
        self.run_checks("build", "api")
        self.assertEqual((folder / "flags.txt").read_text().strip(), "flags=[]")

    def test_skip_tests_with_a_command_that_has_no_placeholder_fails(self):
        self.configure({"api": {"build": "echo '[INFO] BUILD SUCCESS'"}})
        self.folder("api")
        code, report, _ = self.run_checks("build", "api", "--skip-tests")
        self.assertEqual(code, 1)
        self.assertIn("{skip_tests}", report["areas"][0]["reason"])

    def test_compile_error_is_a_failure_with_the_build_errors(self):
        log = ("[INFO] Compiling 3 source files\n[ERROR] /ws/api/Foo.java:[3,1] cannot find symbol\n"
               "[ERROR]   symbol: class Bar\n[ERROR] -> [Help 1]\n[INFO] BUILD FAILURE\n")
        self.configure({"api": {"build": "cat out.log; exit 1"}})
        self.folder("api", out_log=log)
        code, report, output = self.run_checks("build", "api")
        area = report["areas"][0]
        self.assertEqual((code, area["result"]), (1, "fail"))
        self.assertIn("/ws/api/Foo.java:[3,1] cannot find symbol", area["build_errors"])
        self.assertEqual(area["failures"], [])
        self.assert_no_leftovers(output)

    def test_colored_maven_output_still_gives_build_errors(self):
        log = "\x1b[1;31m[ERROR]\x1b[m /ws/api/Foo.java:[3,1] cannot find symbol\n\x1b[1;31m[ERROR] BUILD FAILURE\x1b[m\n"
        log = log.replace("[ERROR] BUILD FAILURE", "[INFO] BUILD FAILURE")
        self.configure({"api": {"build": "cat out.log; exit 1"}})
        self.folder("api", out_log=log)
        _, report, _ = self.run_checks("build", "api")
        self.assertEqual(report["areas"][0]["build_errors"], ["/ws/api/Foo.java:[3,1] cannot find symbol"])

    def test_build_with_unit_tests_reports_their_failures(self):
        self.configure({"api": {"build": "cat out.log; exit 1"}}, prefixes=["org.familysearch."])
        self.folder("api", out_log=FAILING_LOG)
        _, report, _ = self.run_checks("build", "api")
        self.assertEqual(report["areas"][0]["failed"], 2)

    def test_format_reports_its_exit_code(self):
        self.configure({"api": {"format": "echo 'Error: bad style'; exit 1"}, "web": {"format": "true"}})
        self.folder("api"), self.folder("web")
        code, report, _ = self.run_checks("format", "api", "web")
        by_area = {a["area"]: a for a in report["areas"]}
        self.assertEqual((code, by_area["api"]["result"], by_area["web"]["result"]), (1, "fail", "pass"))
        self.assertEqual(by_area["api"]["problem"], "Error: bad style")


class IntegrationTest(RunChecksCase):
    def serve_config(self, port: int, integration: str, **extra) -> None:
        self.configure({"api": {"start": f"exec python3 -m http.server {port} --bind 127.0.0.1",
                                "integration": integration, **extra}}, prefixes=["org.familysearch."])

    def lock_folder(self) -> Path:
        return self.locks / "stack" / "stack"

    def test_passing_bracket_starts_tests_stops_and_releases(self):
        port = free_port()
        self.serve_config(port, f"cat out.log; python3 check.py {port}")
        folder = self.folder("api", out_log=PASSING_LOG, check_py=(
            "import socket, sys\n"
            "socket.create_connection(('127.0.0.1', int(sys.argv[1])), 1)\n"
            "open('seen.txt', 'w').write('served=yes')\n"))
        code, report, output = self.run_checks("integration", "api", "--ports", str(port), "--task", "T-1")
        self.assertEqual((code, report["result"]), (0, "pass"), output)
        self.assertEqual((folder / "seen.txt").read_text().strip(), "served=yes", "tests ran against live services")
        self.assertEqual(report["services"]["started"], [{"area": "api", "ports": [port]}])
        self.assertTrue(report["services"]["stopped"])
        self.assertTrue(report["lock"]["released"])
        self.assertFalse(listening(port))
        self.assertFalse(self.lock_folder().exists())
        self.assert_no_leftovers(output)

    def test_failing_validation_tests_still_stop_the_services_and_release_the_lock(self):
        port = free_port()
        self.serve_config(port, "cat out.log; exit 1")
        self.folder("api", out_log=FAILING_LOG)
        code, report, output = self.run_checks("integration", "api", "--ports", str(port))
        self.assertEqual((code, report["result"]), (1, "fail"))
        self.assertEqual(report["areas"][0]["failed"], 2)
        self.assertEqual(report["areas"][0]["failures"][0]["test"], "FooTest#breaks")
        self.assertTrue(report["services"]["stopped"])
        self.assertTrue(report["lock"]["released"])
        self.assertFalse(listening(port), "the services it started are stopped")
        self.assertFalse(self.lock_folder().exists(), "the services lock is released")
        self.assertEqual(self.lock("status")["locks"], [])
        self.assert_no_leftovers(output)

    def test_lock_held_elsewhere_is_blocked_and_starts_nothing(self):
        port = free_port()
        folder = self.folder("api")
        self.configure({"api": {"start": f"touch started.marker; exec python3 -m http.server {port} --bind 127.0.0.1",
                                "integration": "touch ran.marker"}})
        held = self.lock("acquire", "--task", "OTHER-9", "--worktree", "/elsewhere/api")
        code, report, output = self.run_checks("integration", "api", "--ports", str(port))
        self.assertEqual((code, report["result"]), (3, "blocked"))
        self.assertEqual((report["holder"]["task"], report["holder"]["worktree"]), ("OTHER-9", "/elsewhere/api"))
        self.assertNotIn(held["owner"]["token"], output)
        self.assertEqual(report["services"]["started"], [])
        self.assertFalse((folder / "started.marker").exists(), "no service is started")
        self.assertFalse((folder / "ran.marker").exists())
        self.assertFalse(listening(port))
        owner = self.lock("status")["locks"][0]["owner"]
        self.assertEqual(owner["token"], held["owner"]["token"], "the other holder keeps its lock")

    def test_a_start_that_fails_is_reported_and_releases(self):
        self.configure({"api": {"start": "echo 'Error: port in use'; exit 1", "integration": "touch ran.marker"}})
        folder = self.folder("api")
        code, report, output = self.run_checks("integration", "api", "--start-wait-seconds", "10")
        area = report["areas"][0]
        self.assertEqual((code, report["result"], area["stage"]), (1, "fail", "start"))
        self.assertIn("Error: port in use", area["tail"])
        self.assertFalse((folder / "ran.marker").exists(), "never run tests against a failed start")
        self.assertTrue(report["lock"]["released"])
        self.assertFalse(self.lock_folder().exists())
        self.assert_no_leftovers(output)

    def test_terminating_the_run_stops_services_and_releases_the_lock(self):
        port = free_port()
        self.serve_config(port, "sleep 60")
        self.folder("api")
        process = subprocess.Popen([sys.executable, str(RUN_CHECKS), "integration", "api", "--ports", str(port)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.env, cwd=self.tmp)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        deadline = time.time() + 30
        while time.time() < deadline and not listening(port):
            time.sleep(0.2)
        self.assertTrue(listening(port), "services came up")
        time.sleep(0.5)
        process.send_signal(signal.SIGTERM)
        process.communicate(timeout=60)
        self.assertFalse(listening(port))
        self.assertFalse(self.lock_folder().exists())

    def test_terminating_the_run_ends_a_command_that_is_not_the_shell_itself(self):
        port = free_port()
        # `sleep 60; true` keeps sh from exec'ing sleep directly, as a real `mvn ...; ...` or `cd x && mvn` would.
        self.serve_config(port, "touch it.started; sleep 61; true")
        folder = self.folder("api")
        process = subprocess.Popen([sys.executable, str(RUN_CHECKS), "integration", "api", "--ports", str(port)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.env, cwd=self.tmp)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        deadline = time.time() + 30
        while time.time() < deadline and not (folder / "it.started").exists():
            time.sleep(0.2)
        process.send_signal(signal.SIGTERM)
        process.communicate(timeout=60)
        time.sleep(0.5)
        left = subprocess.run(["pgrep", "-f", "sleep 61"], capture_output=True, text=True).stdout.split()
        self.assertEqual(left, [], "the integration command's own processes are ended")
        self.assertFalse(self.lock_folder().exists())

    def test_terminating_a_unit_run_does_not_wait_for_it(self):
        self.configure({"api": {"unit": "touch it.started; sleep 62; true"}})
        folder = self.folder("api")
        process = subprocess.Popen([sys.executable, str(RUN_CHECKS), "unit", "api"], stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, env=self.env, cwd=self.tmp)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        deadline = time.time() + 30
        while time.time() < deadline and not (folder / "it.started").exists():
            time.sleep(0.2)
        process.send_signal(signal.SIGTERM)
        process.communicate(timeout=20)
        self.assertEqual(process.returncode, 143)

    def test_blocked_lock_reports_every_area_of_the_bracket(self):
        self.configure({"api": {"start": "true", "integration": "true"}, "web": {"start": "true",
                                                                                   "integration": "true"}})
        self.folder("api"), self.folder("web")
        self.lock("acquire", "--task", "OTHER-9")
        code, report, _ = self.run_checks("integration", "api", "web")
        self.assertEqual(code, 3)
        self.assertEqual({a["area"]: a["result"] for a in report["areas"]}, {"api": "blocked", "web": "blocked"})

    def test_area_without_start_is_blocked_before_the_lock(self):
        self.configure({"api": {"integration": "true"}})
        self.folder("api")
        code, report, _ = self.run_checks("integration", "api")
        self.assertEqual((code, report["result"]), (3, "blocked"))
        self.assertIn("start isn't set", report["areas"][0]["reason"])
        self.assertFalse(self.locks.exists())

    def test_area_without_integration_is_skipped_without_taking_the_lock(self):
        self.configure({"api": {"unit": "true"}})
        self.folder("api")
        code, report, _ = self.run_checks("integration", "api")
        self.assertEqual((code, report["result"]), (0, "skipped"))
        self.assertFalse(self.locks.exists())

    def test_nested_workspace_locks_per_repo(self):
        self.configure({"*": {"start": "sleep 300", "integration": "echo '[INFO] Tests run: 1, Failures: 0, "
                                                                    "Errors: 0, Skipped: 0'; echo '[INFO] BUILD SUCCESS'"}},
                       layout="nested")
        self.folder("alpha"), self.folder("beta")
        self.lock("acquire", "beta", "--task", "OTHER-9")
        code, report, output = self.run_checks("integration", "alpha")
        self.assertEqual((code, report["result"]), (0, "pass"), output)
        self.assertEqual(report["lock"]["name"], "alpha")
        code, report, _ = self.run_checks("integration", "beta")
        self.assertEqual((code, report["result"]), (3, "blocked"))
        self.assertEqual(report["holder"]["task"], "OTHER-9")


if __name__ == "__main__":
    unittest.main()
