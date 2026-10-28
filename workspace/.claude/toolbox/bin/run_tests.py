#!/usr/bin/env python3
"""Run areas' unit or integration tests, or their formatters, from the workspace config and print one brief report
when they've finished. The script behind run-unit-tests and run-integration-tests, and work-task's format step; a
session runs it in the background and wakes once.

  run_tests.py unit|integration|format <area>[=<dir>]... [--test T] [--label L] [--jobs N]

Each area's command is the config's workspace.areas.<area>.unit, .integration or .format (the "*" area's when it has
none). It
runs in <dir>, the area's folder in a worktree or checkout; a bare <area> means the workspace root's folder of that
name. Areas run in parallel (--jobs caps how many), each with its output in $TMPDIR/<label>-<area>-<kind>.log (--label
defaults to the workspace name; a task's key keeps two worktrees' logs apart).

--test T replaces {test} in the command, for running one class or method; an area whose command has no {test} fails
with that reason rather than running everything. Without --test, {test} is replaced with nothing, so one command serves
both (Maven's -Dtest={test} with an empty value runs every test).

The report is summarize_reports.py's when the run left Maven reports (surefire for unit; failsafe for integration, or
surefire when failsafe found none, as with modules that name their integration tests *Test); any other build tool gets
its exit code, the first error line of its output, and the log's tail. An area with no command is skipped, which is
neither a pass nor a failure. A run that never reached its tests is never a pass. A format run reports only its exit
code (and on failure the first error line and the log's tail); it takes no --test.

Exit 0 when every area passed or was skipped, 1 otherwise.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[0] / "lib")]

import config  # noqa: E402
import summarize_reports  # noqa: E402

TAIL_LINES = 10
PROBLEM = re.compile(r"\b(error|failed|failure|exception|fatal|cannot find|not found)\b|✖|✗", re.IGNORECASE)
MAX_LINE = 300


def log_path(label: str, area: str, kind: str) -> Path:
    tmp = Path(os.environ.get("TMPDIR") or tempfile.gettempdir())
    return tmp / f"{label}-{area}-{kind}.log"


def first_problem(lines: list[str]) -> str | None:
    for line in lines:
        if PROBLEM.search(line):
            line = line.strip()
            return line if len(line) <= MAX_LINE else line[:MAX_LINE] + "…"
    return None


def maven_summary(path: Path, kind: str, log: Path, started: float) -> dict | None:
    """The Maven report for this run, or None when it left no reports and its log has no Maven outcome."""
    kinds = ["surefire"] if kind == "unit" else ["failsafe", "surefire"]
    for index, reports in enumerate(kinds):
        result = summarize_reports.summarize(path, reports, log, since=started - 1)
        if result["status"] != "no-tests" or index == len(kinds) - 1:
            break
    maven = result["tests"] or result["failed"] or result["build"]["outcome"] not in ("unfinished", "no-log")
    return result if maven else None


def run_area(name: str, path: Path, cfg: config.Config, kind: str, test: str | None, label: str) -> dict:
    command = cfg.command(name, kind)
    if not command:
        return {"area": name, "status": "skipped", "reason": f"workspace.areas.{name}.{kind} isn't set in the config"}
    if test:
        if "{test}" not in command:
            return {"area": name, "status": "fail", "reason": f"--test given, but the {kind} command has no {{test}}"}
    command = command.replace("{test}", test or "")
    if not path.is_dir():
        return {"area": name, "status": "fail", "reason": f"{path} isn't a folder"}
    log = log_path(label, name, kind)
    started = time.time()
    with open(log, "w") as out:
        code = subprocess.run(command, shell=True, cwd=path, stdout=out, stderr=subprocess.STDOUT,
                              stdin=subprocess.DEVNULL).returncode
    seconds = time.time() - started
    result = {"area": name, "kind": kind, "exit": code, "seconds": seconds, "log": str(log), "maven": None}
    result["maven"] = maven_summary(path, kind, log, started) if kind != "format" else None
    result["status"] = result["maven"]["status"] if result["maven"] else ("pass" if code == 0 else "fail")
    if not result["maven"]:
        lines = log.read_text(errors="replace").splitlines()
        result["problem"] = first_problem(lines) if code else None
        result["tail"] = lines[-TAIL_LINES:] if code else []
    return result


def text(result: dict, kind: str) -> str:
    if result["status"] == "skipped":
        return f"{result['area']} ({kind}): skipped · {result['reason']}"
    if "reason" in result:
        return f"{result['area']} ({kind}): {result['status']} · {result['reason']}"
    if result["maven"]:
        return summarize_reports.text(result["maven"])
    head = f"{result['area']} ({kind}): {result['status']} · exit {result['exit']} ({result['seconds']:.0f}s)"
    if result["status"] == "pass":
        return head
    lines = [head]
    if result["problem"]:
        lines.append(f"  first error: {result['problem']}")
    lines.append(f"  log: {result['log']}")
    lines += [f"    {line}" for line in result["tail"]]
    return "\n".join(lines)


def parse_areas(specs: list[str], cfg: config.Config) -> list[tuple[str, Path]]:
    areas = []
    for spec in specs:
        name, _, folder = spec.partition("=")
        areas.append((name, Path(folder).expanduser().resolve() if folder else cfg.root / name))
    return areas


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("kind", choices=["unit", "integration", "format"])
    parser.add_argument("areas", nargs="+")
    parser.add_argument("--test")
    parser.add_argument("--label")
    parser.add_argument("--jobs", type=int)
    args = parser.parse_args(argv)
    try:
        cfg = config.load()
    except config.ConfigError as error:
        sys.exit("; ".join(error.errors))
    areas = parse_areas(args.areas, cfg)
    label = args.label or cfg.name
    with ThreadPoolExecutor(max_workers=args.jobs or len(areas)) as pool:
        results = list(pool.map(lambda a: run_area(a[0], a[1], cfg, args.kind, args.test, label), areas))
    for result in results:
        print(text(result, args.kind))
    return 0 if all(r["status"] in ("pass", "skipped") for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
