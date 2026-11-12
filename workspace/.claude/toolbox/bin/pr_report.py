#!/usr/bin/env python3
"""Fetch one PR's review comments, review state and failed checks through `gh`, trimmed to what someone addressing the
review needs, and print it as one JSON object. Read-only: it never replies, resolves a thread, re-runs or edits. The
`pr-reporter` agent is its caller, so the raw `gh api` JSON (every comment with its diff hunk, reactions and URLs) and a
failed check's CI log never reach the session that asked.

  pr_report.py <PR url>
  pr_report.py <owner/name | repo | checkout> <number>

`owner/name` is used as given. Any other first argument is a repo name or checkout path of the workspace, resolved
through the config (the repo's `origin` gives the GitHub name).

threads       each review thread, oldest first: path, line, reviewer (who opened it), the first comment's body, up to
              three replies, and `state`: `open`; `resolved` (someone resolved the thread); or `outdated` (a later
              commit changed the commented lines, so the comment is probably addressed, though no one said so). Bot
              reviewers are kept, with `bot: true`. Diff hunks are never included.
reviews       `states`, each reviewer's standing (APPROVED, CHANGES_REQUESTED, COMMENTED or DISMISSED), and `bodies`,
              the review summaries that have text. A reviewer's later APPROVED or CHANGES_REQUESTED replaces the earlier
              one; a COMMENTED review changes nothing.
conversation  comments on the PR itself from anyone but a bot (`bots_skipped` counts those left out). The bots are
              GitHub apps, `*[bot]` logins, the few that comment on every PR (watch_task.BOTS) and the workspace
              config's `pr.bots`, read when the script runs inside a workspace.
checks        `failed`: each check that didn't pass or pend, one per workflow and name (a re-run replaces its earlier
              run), with its failing step and `errors`, the error lines of that step's log (at most 12; the first nine
              and last three when more match, else the log's last eight lines). A check that isn't a GitHub Actions job
              has no log: its `note` says so. `pending` names the checks still running; `passed` counts the rest.

Every body is cut to 500 characters, a reply to 300, an error line to 300; at most 200 threads and 10 failed checks are
listed (`more` says how many were left out). A log that can't be fetched leaves `errors` empty with `note` saying why.

Exit codes: 0 the report, 2 bad input, 3 gh failed (the PR doesn't exist, no access, no network).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "lib")]

import config  # noqa: E402
import gitgh  # noqa: E402
import watch_task  # noqa: E402
from gitgh import OK, PRECHECK_FAILED, STEP_FAILED, Failed, emit, gh, gh_json  # noqa: E402

PR_URL = re.compile(r"github\.com/([^/\s]+/[^/\s]+)/pull/(\d+)")
SHORT_REF = re.compile(r"([^/\s#]+/[^/\s#]+)#(\d+)")
ACTIONS_URL = re.compile(r"/actions/runs/(\d+)(?:/jobs?/(\d+))?")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
LOG_STAMP = re.compile(r"^﻿?\d{4}-\d\d-\d\dT[\d:.]+Z\s?")
BODY_CHARS, REPLY_CHARS, LINE_CHARS = 500, 300, 300
MAX_THREADS, MAX_FAILED, MAX_ERRORS, TAIL_FALLBACK, MAX_PAGES = 200, 10, 12, 8, 5
STEP_FAILED_CONCLUSIONS = {"failure", "timed_out", "cancelled", "startup_failure", "action_required"}
# Lines of a failed step's log that say what went wrong. Maven's `[ERROR]` boilerplate says nothing; its indented
# `[ERROR]   Test.method:42 ...` lines are the failures, which no surefire report is here to give.
ERROR_LINE = re.compile(
    r"##\[error\]|\[ERROR\]|\bFAIL(?:ED|URE)?\b|\bFAILURE!|Exception\b|AssertionError|Caused by:|npm ERR!|Traceback|"
    r"^\s*error(?:\[\w+\])?:|\berror TS\d+|\bTests run:.*(?:Failures|Errors): [1-9]|exit code [1-9]|Error:", re.IGNORECASE)

ERROR_NOISE = re.compile(r"^\[ERROR\]\s*($|->|To see the full|Re-run Maven|For more information|After correcting|"
                         r"Please refer to|\[Help|BUILD |Failures:\s*$|Errors:\s*$)")

THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $cursor) {
        totalCount
        pageInfo { hasNextPage endCursor }
        nodes {
          isResolved isOutdated path line originalLine
          first: comments(first: 1) {
            nodes { author { login __typename } body url }
          }
          latest: comments(last: 3) {
            totalCount
            nodes { author { login __typename } body url }
          }
        }
      }
    }
  }
}
"""
PR_FIELDS = "number,url,title,state,headRefOid,reviewDecision,statusCheckRollup,comments,reviews"


def cut(text: str | None, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


def login(author: dict | None) -> str:
    return (author or {}).get("login") or "ghost"  # a deleted account has no author


def is_bot(author: dict | None, bots: set) -> bool:
    """GitHub apps, `*[bot]` logins, the few that comment on every PR, and the config's `pr.bots` (a scanner's service
    account is a plain user to GitHub)."""
    name = login(author)
    return (author or {}).get("__typename") == "Bot" or name.endswith("[bot]") or name in bots


def configured_bots() -> set:
    try:
        return watch_task.bots_of(config.load())
    except config.ConfigError:  # no workspace here (a PR url needs none)
        return set(watch_task.BOTS)


def parse_target(cfg_loader, ref: list[str]) -> tuple[str, int]:
    """(owner/name, number) from a PR url, `owner/name#n`, or a repo and a number."""
    if len(ref) == 1:
        found = PR_URL.search(ref[0]) or SHORT_REF.fullmatch(ref[0])
        if not found:
            raise ValueError(f"{ref[0]!r} is not a PR url (https://github.com/<owner>/<name>/pull/<n>) or "
                             "owner/name#<n>; or pass a repo and a number")
        return found.group(1), int(found.group(2))
    if len(ref) != 2 or not ref[1].isdigit():
        raise ValueError("give a PR url, or a repo and a PR number")
    repo = ref[0]
    if re.fullmatch(r"[^/\s]+/[^/\s]+", repo) and not Path(repo).expanduser().exists():
        return repo, int(ref[1])
    return gitgh.resolve(cfg_loader(), repo).gh_repo, int(ref[1])


# ---------------------------------------------------------------------------------------------------------------------
# Review threads, reviews and conversation


def fetch_threads(repo: str, number: int) -> tuple[list[dict], int]:
    """The review threads read (up to MAX_PAGES pages) and how many the PR has in all."""
    owner, name = repo.split("/", 1)
    nodes, cursor, total = [], None, 0
    for _ in range(MAX_PAGES):
        args = ["api", "graphql", "-f", f"query={THREADS_QUERY}", "-f", f"owner={owner}", "-f", f"name={name}",
                "-F", f"number={number}"]
        if cursor:
            args += ["-f", f"cursor={cursor}"]
        data = gitgh.gh_json(*args) or {}
        threads = (((data.get("data") or {}).get("repository") or {}).get("pullRequest") or {}).get("reviewThreads")
        if threads is None:
            raise Failed(f"{repo}#{number}: no review threads in the response: {cut(str(data), 200)}")
        nodes += threads.get("nodes") or []
        total = threads.get("totalCount") or total
        page = threads.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            break
        cursor = page.get("endCursor")
    return nodes, max(total, len(nodes))


def thread_item(node: dict, bots: set) -> dict:
    firsts = (node.get("first") or {}).get("nodes") or []
    latest = (node.get("latest") or {}).get("nodes") or []
    first = firsts[0] if firsts else {}
    total = (node.get("latest") or {}).get("totalCount") or len(firsts)
    replies = latest[len(latest) - min(3, max(total - 1, 0)):] if total > 1 else []  # the last three, never the first
    state = "resolved" if node.get("isResolved") else "outdated" if node.get("isOutdated") else "open"
    line = node.get("line") or node.get("originalLine")
    item = {"path": node.get("path"), "line": line, "reviewer": login(first.get("author")), "state": state,
            "body": cut(first.get("body"), BODY_CHARS), "url": first.get("url"),
            "reply_count": max(total - 1, 0),
            "replies": [{"author": login(c.get("author")), "body": cut(c.get("body"), REPLY_CHARS)}
                        for c in replies]}
    if state == "outdated" and not node.get("line"):
        item["line_is_original"] = True
    if is_bot(first.get("author"), bots):
        item["bot"] = True
    return item


def review_summary(reviews: list[dict]) -> dict:
    states, bodies = {}, []
    for review in sorted(reviews, key=lambda r: r.get("submittedAt") or ""):
        state, who = review.get("state") or "", login(review.get("author"))
        if state == "PENDING":
            continue
        if state in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED") or who not in states:
            states[who] = state
        if (review.get("body") or "").strip():
            bodies.append({"reviewer": who, "state": state, "body": cut(review.get("body"), BODY_CHARS)})
    return {"states": states, "bodies": bodies[-20:]}


# ---------------------------------------------------------------------------------------------------------------------
# Checks


def latest_checks(rollup: list[dict]) -> list[dict]:
    """One check per workflow and name: a re-run replaces the run it re-ran."""
    latest = {}
    for check in rollup:
        key = (check.get("workflowName") or "", check.get("name") or check.get("context") or "?")
        if key not in latest or (check.get("startedAt") or "") >= (latest[key].get("startedAt") or ""):
            latest[key] = check
    return list(latest.values())


def clean(line: str) -> str:
    return LOG_STAMP.sub("", ANSI.sub("", line)).strip()


def error_lines(lines: list[str]) -> list[str]:
    """The lines of a failed step's log that say why: error-looking ones minus Maven's boilerplate, the first nine and
    last three when there are more than MAX_ERRORS; with none, the log's last few lines."""
    texts = [t for t in (clean(line) for line in lines) if t]
    hits = [t for t in texts if ERROR_LINE.search(t) and not ERROR_NOISE.match(t)]
    if len(hits) > MAX_ERRORS:
        hits = hits[:MAX_ERRORS - 3] + hits[-3:]
    return [cut(t, LINE_CHARS) for t in (hits or texts[-TAIL_FALLBACK:])]


def check_detail(repo: str, check: dict, jobs_by_run: dict) -> dict:
    name = check.get("name") or check.get("context") or "?"
    url = check.get("detailsUrl") or check.get("targetUrl") or ""
    out = {"name": name, "workflow": check.get("workflowName") or None,
           "result": (check.get("conclusion") or check.get("state") or "").lower(), "url": url}
    found = ACTIONS_URL.search(url)
    if not found:
        out.update(step=None, errors=[], note="not a GitHub Actions job: no log to read")
        if check.get("description"):
            out["description"] = cut(check["description"], LINE_CHARS)
        return out
    run_id, job_id = found.group(1), found.group(2)
    step = None
    try:
        if run_id not in jobs_by_run:
            jobs_by_run[run_id] = (gh_json("run", "view", run_id, "--repo", repo, "--json", "jobs") or {}).get("jobs") or []
        jobs = jobs_by_run[run_id]
        job = next((j for j in jobs if job_id and str(j.get("databaseId")) == job_id),
                   next((j for j in jobs if j.get("name") == name), None))
        step = next((s.get("name") for s in (job or {}).get("steps") or []
                     if (s.get("conclusion") or "").lower() in STEP_FAILED_CONCLUSIONS), None)
        job_id = job_id or (str(job["databaseId"]) if job else None)
        if job_id is None:
            out.update(step=step, errors=[], note="the run has no job named like the check; no log read")
            return out
        log = gh("run", "view", run_id, "--repo", repo, "--job", job_id, "--log-failed")
    except Failed as error:
        out.update(step=step, errors=[], note=f"log unavailable: {cut(str(error), 200)}")
        return out
    rows = [line.split("\t", 2) for line in log.splitlines()]
    if step is None:  # the log's own step column, when the jobs listing had none
        step = next((row[1] for row in rows if len(row) == 3), None)
    out.update(step=step, errors=error_lines([row[-1] for row in rows]))
    return out


def checks_report(repo: str, rollup: list[dict]) -> dict:
    failed, pending, passed = [], [], 0
    for check in latest_checks(rollup):
        state = watch_task.check_state(check)
        if state == "green":
            passed += 1
        elif state == "pending":
            pending.append(check.get("name") or check.get("context") or "?")
        else:
            failed.append(check)
    jobs_by_run: dict = {}
    listed = [check_detail(repo, c, jobs_by_run) for c in failed[:MAX_FAILED]]
    out = {"failed": listed, "pending": sorted(pending), "passed": passed}
    if len(failed) > MAX_FAILED:
        out["more_failed"] = len(failed) - MAX_FAILED
    return out


# ---------------------------------------------------------------------------------------------------------------------


def report(repo: str, number: int, bots: set) -> dict:
    pr = gh_json("pr", "view", str(number), "--repo", repo, "--json", PR_FIELDS)
    nodes, total_threads = fetch_threads(repo, number)
    threads = [thread_item(n, bots) for n in nodes[:MAX_THREADS]]
    skipped = [c for c in pr.get("comments") or [] if is_bot(c.get("author"), bots)]
    conversation = [{"author": login(c.get("author")), "body": cut(c.get("body"), BODY_CHARS), "url": c.get("url")}
                    for c in pr.get("comments") or [] if not is_bot(c.get("author"), bots)]
    out = {"repo": repo, "number": pr["number"], "url": pr["url"], "title": cut(pr.get("title"), 200),
           "state": pr["state"], "head": (pr.get("headRefOid") or "")[:10],
           "review_decision": pr.get("reviewDecision") or "NONE",
           "reviews": review_summary(pr.get("reviews") or []),
           "threads": threads, "conversation": conversation, "bots_skipped": len(skipped),
           "checks": checks_report(repo, pr.get("statusCheckRollup") or [])}
    if total_threads > len(threads):
        out["more_threads"] = total_threads - len(threads)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ref", nargs="+", help="a PR url, or a repo and a PR number")
    args = parser.parse_args(argv)
    try:
        repo, number = parse_target(config.load, args.ref)
        return emit(report(repo, number, configured_bots()), OK)
    except (ValueError, config.ConfigError) as error:
        return emit({"error": str(error)}, PRECHECK_FAILED)
    except Failed as error:
        return emit({"error": str(error)}, STEP_FAILED)


if __name__ == "__main__":
    sys.exit(main())
