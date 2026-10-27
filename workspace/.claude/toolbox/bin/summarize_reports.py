#!/usr/bin/env python3
"""Summarize a Maven build's surefire or failsafe reports: tests run, failed, and the first assertion or error line per
failing test. Shared by run-unit-tests (surefire) and run-integration-tests (failsafe, through run_tests.py), so both
report the same way and nobody greps a build log by hand. Maven only; other build tools report through their own
command's output.

  summarize_reports.py <dir>... --kind surefire|failsafe [--log <build log>] [--since <epoch seconds>] [--json]

<dir> is a checkout or a module: every target/<kind>-reports/TEST-*.xml under it is read. --since skips report files
older than the run (failsafe reports survive between runs, since validation runs without `clean`). --log adds the
build's outcome, total time and its first [ERROR] lines, which is how a build that failed before or outside the tests
shows up. With one --log, pass one <dir>.

Prints one short text block per dir, or JSON with --json. Exit 0 when every dir passed (tests ran, none failed, and
the log, if given, says BUILD SUCCESS), 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

PRUNE = {".git", ".idea", "node_modules", "src"}
MAX_LINE = 300
MAX_ERRORS = 8
# [ERROR] lines that are Maven's boilerplate or a restatement of test failures already read from the reports.
ERROR_NOISE = re.compile(r"^\[ERROR\]\s*($|->|To see the full|Re-run Maven|For more information|After correcting|"
                         r"Tests run:|Failures:|Errors:|  |Please refer to|\[Help|BUILD )")


def report_dirs(root: Path, kind: str):
    """Every target/<kind>-reports folder under root, without descending into sources or build output."""
    for here, dirs, _ in os.walk(root):
        if os.path.basename(here) == "target":
            reports = Path(here) / f"{kind}-reports"
            if reports.is_dir():
                yield reports
            dirs[:] = []
            continue
        dirs[:] = sorted(d for d in dirs if d not in PRUNE)


def first_line(text: str | None) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            line = line.strip()
            return line if len(line) <= MAX_LINE else line[:MAX_LINE] + "…"
    return ""


def module_name(root: Path, reports: Path) -> str:
    module = reports.parent.parent
    return root.resolve().name if module.resolve() == root.resolve() else str(module.relative_to(root))


def read_reports(root: Path, kind: str, since: float | None = None) -> dict:
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "seconds": 0.0}
    modules, failed = set(), []
    for reports in report_dirs(root, kind):
        for xml in sorted(reports.glob("TEST-*.xml")):
            if since and xml.stat().st_mtime < since:
                continue
            try:
                suite = ET.parse(xml).getroot()
            except ET.ParseError:
                failed.append({"module": module_name(root, reports), "test": xml.stem[len("TEST-"):],
                               "kind": "unreadable", "message": "report is not valid XML (run cut short?)"})
                continue
            modules.add(module_name(root, reports))
            for key in ("tests", "failures", "errors", "skipped"):
                totals[key] += int(suite.get(key) or 0)
            totals["seconds"] += float(suite.get("time") or 0)
            for case in suite.iter("testcase"):
                for kind_ in ("failure", "error"):
                    problem = case.find(kind_)
                    if problem is not None:
                        cls = (case.get("classname") or "").rsplit(".", 1)[-1]
                        failed.append({"module": module_name(root, reports), "test": f"{cls}#{case.get('name')}",
                                       "kind": kind_,
                                       "message": first_line(problem.get("message")) or first_line(problem.text)
                                       or problem.get("type", "")})
    return {**totals, "modules": len(modules), "failed": failed}


def read_log(log: Path) -> dict:
    try:
        lines = log.read_text(errors="replace").splitlines()
    except OSError as e:
        return {"outcome": "no-log", "log": str(log), "errors": [str(e)]}
    outcome = next((l.split("BUILD ", 1)[1].strip() for l in reversed(lines) if "] BUILD " in l), None)
    total = next((l.split("Total time:", 1)[1].strip() for l in reversed(lines) if "Total time:" in l), None)
    errors = list(dict.fromkeys(first_line(l) for l in lines
                                if l.startswith("[ERROR]") and not ERROR_NOISE.match(l)))
    return {"outcome": outcome or "unfinished", "total_time": total, "log": str(log), "errors": errors[:MAX_ERRORS]}


def summarize(root: Path, kind: str, log: Path | None = None, since: float | None = None) -> dict:
    result = {"dir": str(root), "name": root.resolve().name, "kind": kind, **read_reports(root, kind, since)}
    if log:
        result["build"] = read_log(log)
    build_ok = not log or result["build"]["outcome"] == "SUCCESS"
    if result["failed"]:
        result["status"] = "fail"
    elif not build_ok:
        result["status"] = "build-failed"
    elif result["tests"] == 0:
        result["status"] = "no-tests"
    else:
        result["status"] = "pass"
    return result


def text(r: dict) -> str:
    n_failed = r["failures"] + r["errors"]
    took = (r.get("build") or {}).get("total_time") or f"{r['seconds']:.0f}s in tests"
    head = (f"{r['name']} ({r['kind']}): {r['status']} · {r['tests']} tests, {n_failed} failed, "
            f"{r['skipped']} skipped, {r['modules']} modules ({took})")
    out = [head]
    out += [f"  - {f['module']} · {f['test']} [{f['kind']}]: {f['message']}" for f in r["failed"]]
    build = r.get("build")
    if build and r["status"] != "pass":
        out.append(f"  build: {build['outcome']} · log {build['log']}")
        if r["status"] != "fail":
            out += [f"    {e}" for e in build["errors"]]
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dirs", nargs="+", type=Path)
    parser.add_argument("--kind", required=True, choices=["surefire", "failsafe"])
    parser.add_argument("--log", type=Path)
    parser.add_argument("--since", type=float)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.log and len(args.dirs) > 1:
        parser.error("--log goes with one dir")
    results = [summarize(d.expanduser(), args.kind, args.log, args.since) for d in args.dirs]
    print(json.dumps(results, indent=2) if args.json else "\n".join(text(r) for r in results))
    return 0 if all(r["status"] == "pass" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
