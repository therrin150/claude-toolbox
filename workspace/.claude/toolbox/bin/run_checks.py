#!/usr/bin/env python3
"""Run an area's build, unit tests, formatter or integration tests from the workspace config, wait for the run, and print
what matters as one JSON object: the result, the counts, each failing test with its assertion and expected/actual, and
each stack trace trimmed to the code that is ours. The raw output never leaves the script, and no log path is printed.

  run_checks.py build|unit|format|integration <area>[=<dir>]... [--test T] [--skip-tests] [--task K]
                [--ports 8080,8081] [--health-url U]... [--start-wait-seconds N] [--jobs N]

Each area's command is the config's workspace.areas.<area>.build, .unit, .format or .integration (the "*" area's when it
has none), run in <dir>, the area's folder in a checkout or worktree; a bare <area> means the area's folder in the
workspace root. `build` runs the build command with its unit tests unless --skip-tests, which replaces {skip_tests} in
the command (`mvn install {skip_tests}`) with -DskipTests; without the flag {skip_tests} is replaced with nothing.
--test T replaces {test} the same way; a command with no placeholder for a flag given fails with that reason.

`integration` is the whole bracket, around the area's `start` and `integration` commands: acquire the services lock,
start the area's services, run its integration tests, stop the services, release the lock, in that order. Stop and
release always run, whatever happens in between. A lock another checkout holds is `blocked`, with the holder, and
nothing is started; the caller waits and runs it again. A nested workspace locks per area (repo); a single one locks
once for every area given. --task names the work in the lock; --ports and --health-url are what start-local-services
documents.

The result comes from the tail of the run's output, in Maven's surefire format: the `Tests run:` summary lines give
the counts, each `<<< FAILURE!` or `<<< ERROR!` block a failing test (its first lines are the assertion; expected and
actual are read from JUnit's, AssertJ's and Hamcrest-style messages), and BUILD FAILURE or a compile error gives the
build's errors. Any other tool gets its exit code, the first error line of its output and the last lines. A unit or
integration run that ran no tests is a `fail`: a run that never reached its tests is never a pass.

A stack trace keeps its first frame, every `Caused by` line, and the frames that start with one of the config's
checks.own_code_prefixes (such as `org.familysearch.`); with none configured, every frame stays.

Prints one JSON object: mode, result (pass, fail, blocked or skipped), areas (each with its own result, counts and
failures), and for integration the services started and stopped and whether the lock is released. Exit 0 when every
area passed or was skipped, 1 on a failure, 3 when blocked.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "lib")]

import config  # noqa: E402
import summarize_reports  # noqa: E402

OK, FAIL, BLOCKED = 0, 1, 3
MODES = ("build", "unit", "format", "integration")
TAIL_LINES = 10
MAX_LINE = 300
MAX_ASSERTION_LINES = 12
MAX_FAILURES = 50
MAX_BUILD_ERRORS = 8
STOP_SECONDS = 180

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
MAVEN_LINE = re.compile(r"^\[(?:INFO|ERROR|WARNING|DEBUG)\]")
SUMMARY = re.compile(r"^(?:\[\w+\]\s+)?Tests run:\s*(\d+),\s*Failures:\s*(\d+),\s*Errors:\s*(\d+),\s*Skipped:\s*(\d+)\s*$")
HEADER = re.compile(r"^(?:\[\w+\]\s+)?(?P<name>\S.*?)\s+(?:--\s+)?Time elapsed:\s*[\d.,]+\s*s(?:ec)?\s*<<<\s*"
                    r"(?P<kind>FAILURE|ERROR)!\s*$")
OLD_NAME = re.compile(r"^(?P<method>[^\s(]+)\((?P<cls>[\w.$]+)\)$")
BUILD_OUTCOME = re.compile(r"\] BUILD (SUCCESS|FAILURE)")
FRAME = re.compile(r"^\s*at\s+(?P<frame>\S.*)$")
CAUSE = re.compile(r"^\s*(Caused by:.*)$")
CLASS_NAME = re.compile(r"^[A-Za-z_][\w.$]*$")
PROBLEM = re.compile(r"\b(error|failed|failure|exception|fatal|cannot find|not found)\b|✖|✗", re.IGNORECASE)

# The three message shapes an assertion's expected and actual come in.
EXPECTED_ACTUAL = (
    re.compile(r"expected:\s*<(?P<expected>.*?)>\s*but was:\s*<(?P<actual>.*?)>", re.DOTALL),
    re.compile(r"expected:\s*(?P<expected>.+?)\s*\n\s*but was:\s*(?P<actual>.+)"),
    re.compile(r"Expecting actual:\s*\n\s*(?P<actual>.+?)\s*\n\s*to be equal to:\s*\n\s*(?P<expected>.+?)\s*(?:\n|$)",
               re.DOTALL),
)


_children: set[subprocess.Popen] = set()
_children_lock = threading.Lock()


def run_command(command: str, cwd: Path, out) -> int:
    """Run a shell command in its own process group, so that ending this script ends the command's whole tree."""
    process = subprocess.Popen(command, shell=True, cwd=cwd, stdout=out, stderr=subprocess.STDOUT,
                               stdin=subprocess.DEVNULL, start_new_session=True)
    with _children_lock:
        _children.add(process)
    try:
        return process.wait()
    finally:
        with _children_lock:
            _children.discard(process)


def kill_children() -> None:
    with _children_lock:
        running = list(_children)
    for process in running:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def terminate(*_) -> None:
    """SIGTERM: end the running commands, then unwind through the bracket's finally, which stops the services and
    releases the lock."""
    kill_children()
    sys.exit(143)


def clip(line: str) -> str:
    line = line.strip()
    return line if len(line) <= MAX_LINE else line[:MAX_LINE] + "…"


# ---------------------------------------------------------------------------------------------------------------------
# Reading a run's output


def expected_actual(assertion: str) -> tuple[str | None, str | None]:
    for pattern in EXPECTED_ACTUAL:
        found = pattern.search(assertion)
        if found:
            return clip(found["expected"]), clip(found["actual"])
    return None, None


def trim_stack(frames: list[str], prefixes: list[str]) -> list[str]:
    """The first frame, every `Caused by` line, and the frames of our own code; with no prefixes, every frame."""
    kept, first = [], True
    for line in frames:
        cause = CAUSE.match(line)
        if cause:
            kept.append(clip(cause.group(1)))
            continue
        frame = FRAME.match(line)
        if not frame:
            continue  # `... 12 more`, `Suppressed:`
        text = frame["frame"].strip()
        own = not prefixes or text.split("/", 1)[-1].startswith(tuple(prefixes))
        if first or own:
            kept.append(f"at {text}")
        first = False
    return kept


def split_failure(block: list[str], prefixes: list[str]) -> tuple[str | None, str, list[str]]:
    """A failure block's exception class, its assertion text (the message lines before the first frame), and its trimmed
    stack trace."""
    message = []
    index = 0
    while index < len(block) and not FRAME.match(block[index]) and not CAUSE.match(block[index]):
        message.append(block[index].rstrip())
        index += 1
    while message and not message[-1].strip():
        message.pop()
    exception = None
    if message:
        head, sep, rest = message[0].partition(": ")
        if sep and CLASS_NAME.match(head):
            exception, message[0] = head, rest
        elif CLASS_NAME.match(message[0].strip()):
            exception, message[0] = message[0].strip(), ""
    assertion = "\n".join(message[:MAX_ASSERTION_LINES]).strip()
    return exception, assertion, trim_stack(block[index:], prefixes)


def parse_failures(lines: list[str], prefixes: list[str]) -> list[dict]:
    failures, seen = [], set()
    index = 0
    while index < len(lines):
        header = HEADER.match(lines[index])
        index += 1
        if not header or SUMMARY.match(lines[index - 1]) or "Tests run:" in lines[index - 1]:
            continue
        block = []
        while index < len(lines) and not MAVEN_LINE.match(lines[index]) and not HEADER.match(lines[index]):
            block.append(lines[index])
            index += 1
        old = OLD_NAME.match(header["name"])
        cls, _, method = (old["cls"], "", old["method"]) if old else header["name"].rpartition(".")
        exception, assertion, stack = split_failure(block, prefixes)
        expected, actual = expected_actual(assertion)
        failure = {"test": f"{cls.rsplit('.', 1)[-1]}#{method}" if cls else method, "kind": header["kind"].lower(),
                   "exception": exception, "assertion": assertion, "expected": expected, "actual": actual,
                   "stack": stack}
        key = (failure["test"], assertion)
        if key not in seen:
            seen.add(key)
            failures.append(failure)
    return failures


def build_errors(log: Path) -> list[str]:
    return [error.removeprefix("[ERROR]").strip() for error in summarize_reports.read_log(log)["errors"]
            ][:MAX_BUILD_ERRORS]


def first_problem(lines: list[str]) -> str | None:
    return next((clip(line) for line in lines if PROBLEM.search(line)), None)


def read_output(log: Path, mode: str, code: int, prefixes: list[str]) -> dict:
    """What a finished run's output says: result, counts, failing tests, and for a failure with none, why."""
    lines = [ANSI.sub("", line) for line in log.read_text(errors="replace").splitlines()]
    log.write_text("\n".join(lines) + "\n")  # read_log, below, matches on plain `[ERROR]` prefixes
    totals = [tuple(int(n) for n in found.groups()) for found in map(SUMMARY.match, lines) if found]
    outcome = next((found[1] for found in map(BUILD_OUTCOME.search, reversed(lines)) if found), None)
    maven = bool(totals) or outcome is not None
    failures = parse_failures(lines, prefixes) if maven else []
    report: dict = {"tests": None, "failed": None, "skipped": None}
    if totals:
        report |= {"tests": sum(t[0] for t in totals), "failed": sum(t[1] + t[2] for t in totals),
                   "skipped": sum(t[3] for t in totals)}
    ok = code == 0 and not failures and outcome != "FAILURE" and not (report["failed"] or 0)
    result, reason = ("pass" if ok else "fail"), None
    if ok and mode in ("unit", "integration") and maven and not report["tests"]:
        result, reason = "fail", f"the {mode} run ran no tests"
    report["result"] = result
    report["failures"] = failures[:MAX_FAILURES]
    if len(failures) > MAX_FAILURES:
        report["more_failures"] = len(failures) - MAX_FAILURES
    if result == "fail":
        if reason:
            report["reason"] = reason
        if not failures:
            if maven:
                report["build_errors"] = build_errors(log)
            else:
                report["problem"] = first_problem(lines)
            report["tail"] = [clip(line) for line in lines if line.strip()][-TAIL_LINES:]
    return report


# ---------------------------------------------------------------------------------------------------------------------
# Running one area's command


def expand(command: str, args) -> tuple[str | None, str | None]:
    """The command with its placeholders filled, or None and why it can't be."""
    for flag, placeholder, value in (("--test", "{test}", args.test), ("--skip-tests", "{skip_tests}",
                                                                      "-DskipTests" if args.skip_tests else None)):
        if value and placeholder not in command:
            return None, f"{flag} given, but the {args.mode} command has no {placeholder}"
        command = command.replace(placeholder, value or "")
    return command, None


def area_folder(spec: str, cfg: config.Config) -> tuple[str, Path]:
    name, _, folder = spec.partition("=")
    return name, Path(folder).expanduser().resolve() if folder else cfg.root / cfg.area_dir(name)


def run_area(name: str, path: Path, cfg: config.Config, args) -> dict:
    mode = args.mode
    command = cfg.command(name, mode)
    if not command:
        return {"area": name, "result": "skipped", "reason": f"workspace.areas.{name}.{mode} isn't set in the config"}
    command, problem = expand(command, args)
    if problem:
        return {"area": name, "result": "fail", "reason": problem}
    if not path.is_dir():
        return {"area": name, "result": "fail", "reason": f"{path} isn't a folder"}
    prefixes = cfg.get("checks.own_code_prefixes")
    fd, temp = tempfile.mkstemp(prefix=f"run-checks-{name}-", suffix=".log")
    log = Path(temp)
    started = time.time()
    try:
        with os.fdopen(fd, "w") as out:
            code = run_command(command, path, out)
        seconds = round(time.time() - started, 1)
        return {"area": name, "exit": code, "seconds": seconds, **read_output(log, mode, code, prefixes)}
    finally:
        log.unlink(missing_ok=True)


# ---------------------------------------------------------------------------------------------------------------------
# The integration bracket


class Lock:
    """services_lock.py, run as its own command so the lock behaves exactly as it does for a person."""

    def __init__(self, name_args: list[str]):
        self.name_args = name_args

    def run(self, command: str, *args: str, timeout: float | None = None) -> tuple[int, dict]:
        argv = [sys.executable, str(HERE / "services_lock.py"), command, *self.name_args, *args]
        try:
            done = subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=timeout)
        except subprocess.TimeoutExpired:
            return 124, {"result": "timeout"}
        try:
            return done.returncode, json.loads(done.stdout)
        except json.JSONDecodeError:
            return done.returncode, {"result": "unreadable", "output": (done.stdout + done.stderr).strip()[-500:]}


def without_token(owner: dict | None) -> dict | None:
    return {key: value for key, value in (owner or {}).items() if key not in ("token", "pid_starts", "stops")} or None


def holds(lock: Lock, token: str) -> bool:
    locks = lock.run("status")[1].get("locks") or []
    return any((entry.get("owner") or {}).get("token") == token for entry in locks)


def release(lock: Lock, token: str) -> dict:
    """Stop what the lock lists and release it, then check the lock no longer carries our token."""
    code, stopped = lock.run("stop", "--token", token, timeout=STOP_SECONDS)
    if holds(lock, token):
        lock.run("release", "--token", token)
    return {"stopped": code == 0 and not stopped.get("still_listening"), "released": not holds(lock, token),
            "still_listening": stopped.get("still_listening") or None}


def bracket(group: list[tuple[str, Path]], cfg: config.Config, args) -> dict:
    """One acquire → start → tests → stop → release bracket around the areas that share a lock."""
    name_args = [group[0][0]] if cfg.nested else []
    lock = Lock(name_args)
    lock_name = group[0][0] if cfg.nested else cfg.name
    out: dict = {"areas": [], "services": {"started": []}, "lock": {"name": lock_name}, "notes": []}
    ports = args.ports or ""
    code, got = lock.run("acquire", "--task", args.task or "run-checks", "--worktree", str(group[0][1]),
                         *(["--ports", ports] if ports else []))
    if code != 0:
        out["result"] = "blocked"
        out["holder"] = without_token(got.get("owner"))
        out["lock"]["held_by"] = got.get("kind")
        out["reason"] = "the services lock is held elsewhere" if code == 3 else f"couldn't take the lock: {got}"
        out["lock"]["released"] = True  # never taken
        return out
    token = got["owner"]["token"]
    if got.get("result") == "taken-over":
        out["notes"].append({"takeover": True, "previous": without_token(got.get("previous")),
                             "previous_kind": got.get("previous_kind")})
    start_logs: list[Path] = []
    try:
        for area, path in group:
            start_log = Path(tempfile.mkstemp(prefix=f"run-checks-{area}-start-", suffix=".log")[1])
            start_logs.append(start_log)
            code, started = lock.run("start", "--token", token, "--area", area, "--checkout", str(path),
                                     "--log", str(start_log),
                                     *(["--ports", ports] if ports else []),
                                     *[arg for url in args.health_url or [] for arg in ("--health-url", url)],
                                     "--wait-seconds", str(args.start_wait_seconds))
            if code != 0:
                out["areas"].append({"area": area, "result": "fail" if code == 6 else "blocked", "stage": "start",
                                     "reason": f"start {started.get('result')}: {started.get('note') or ''}".strip(),
                                     "tail": started.get("log_tail") or []})
                break
            out["services"]["started"].append({"area": area, "ports": started.get("ports")})
        else:
            out["areas"] = [run_area(area, path, cfg, args) for area, path in group]
    finally:
        done = release(lock, token)
        for start_log in start_logs:
            start_log.unlink(missing_ok=True)
        out["services"] |= {"stopped": done["stopped"], "still_listening": done["still_listening"]}
        out["lock"]["released"] = done["released"]
    return out


def integration(areas: list[tuple[str, Path]], cfg: config.Config, args) -> dict:
    if not (HERE / "services_lock.py").is_file():
        return {"result": "blocked", "reason": "the services-lock feature isn't installed (services_lock.py missing)",
                "areas": []}
    results, runnable = [], []
    for name, path in areas:
        if not cfg.command(name, "integration"):
            results.append({"area": name, "result": "skipped",
                            "reason": f"workspace.areas.{name}.integration isn't set in the config"})
        elif not cfg.command(name, "start"):
            results.append({"area": name, "result": "blocked",
                            "reason": f"workspace.areas.{name}.start isn't set in the config"})
        elif not path.is_dir():
            results.append({"area": name, "result": "fail", "reason": f"{path} isn't a folder"})
        else:
            runnable.append((name, path))
    out: dict = {"areas": results, "services": {"started": []}, "notes": []}
    # A nested workspace locks per repo, so each area is its own bracket; a single one has one lock for them all.
    groups = [[area] for area in runnable] if cfg.nested else ([runnable] if runnable else [])
    locks, stopped = [], []
    for group in groups:
        done = bracket(group, cfg, args)
        out["areas"] += done["areas"]
        out["services"]["started"] += done["services"]["started"]
        out["notes"] += done["notes"]
        for key in ("holder", "reason"):
            if key in done:
                out[key] = done[key]
        locks.append(done["lock"])
        stopped.append(done["services"].get("stopped", True))
        if done.get("result") == "blocked":
            out["areas"] += [{"area": area, "result": "blocked", "reason": done["reason"]} for area, _ in group]
    if locks:
        out["lock"] = locks[0] if len(locks) == 1 else locks
    out["services"]["stopped"] = all(stopped)
    return out


# ---------------------------------------------------------------------------------------------------------------------


def overall(results: list[dict]) -> str:
    states = {result["result"] for result in results}
    for state in ("fail", "blocked", "pass"):
        if state in states:
            return state
    return "skipped"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=MODES)
    parser.add_argument("areas", nargs="+")
    parser.add_argument("--test")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--task")
    parser.add_argument("--ports")
    parser.add_argument("--health-url", action="append")
    parser.add_argument("--start-wait-seconds", type=float, default=90)
    parser.add_argument("--jobs", type=int)
    args = parser.parse_args(argv)
    if args.skip_tests and args.mode != "build":
        parser.error("--skip-tests goes with build")
    if args.test and args.mode in ("build", "format"):
        parser.error(f"--test goes with unit or integration, not {args.mode}")
    try:
        cfg = config.load()
    except config.ConfigError as error:
        sys.exit("; ".join(error.errors))
    # A terminated run still stops its services and releases the lock: SIGTERM unwinds through the bracket's finally.
    signal.signal(signal.SIGTERM, terminate)
    areas = [area_folder(spec, cfg) for spec in args.areas]
    if args.mode == "integration":
        report = integration(areas, cfg, args)
    else:
        with ThreadPoolExecutor(max_workers=args.jobs or len(areas)) as pool:
            report = {"areas": list(pool.map(lambda area: run_area(area[0], area[1], cfg, args), areas))}
    report = {"mode": args.mode, "result": overall(report["areas"]), **report}
    if report["result"] == "blocked" and "reason" not in report:
        report["reason"] = next((a["reason"] for a in report["areas"] if a["result"] == "blocked"), None)
    print(json.dumps(report, indent=2, default=str))
    return {"pass": OK, "skipped": OK, "fail": FAIL, "blocked": BLOCKED}[report["result"]]


if __name__ == "__main__":
    sys.exit(main())
