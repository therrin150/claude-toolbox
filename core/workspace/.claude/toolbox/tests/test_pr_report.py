"""Tests for bin/pr_report.py, with a scripted `gh` that answers only the read calls the script makes (`pr view`, the
review threads GraphQL query, `run view --json jobs` and `run view --log-failed`) and records every call it gets.

Run from the repo root:

    python3 -m unittest discover -s core/workspace/.claude/toolbox/tests
"""

from __future__ import annotations

import importlib.util
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

from toolbox_tree import TOOLBOX  # noqa: E402
SCRIPT = TOOLBOX / "bin" / "pr_report.py"

FAKE_GH = textwrap.dedent('''\
    import json, os, sys
    path = os.environ["PR_FAKE"]
    state = json.load(open(path))
    args = sys.argv[1:]
    state.setdefault("calls", []).append(args)
    json.dump(state, open(path, "w"))
    def opt(name):
        return args[args.index(name) + 1] if name in args else None
    def fail(message):
        print(message, file=sys.stderr)
        sys.exit(1)
    if args[:2] == ["pr", "view"]:
        view = state["prs"].get(opt("--repo") + "#" + args[2])
        if view is None:
            fail("GraphQL: Could not resolve to a PullRequest with the number of " + args[2])
        print(json.dumps(view))
    elif args[:2] == ["api", "graphql"]:
        fields = [a for a in args if a.startswith("cursor=")]
        cursor = fields[0].split("=", 1)[1] if fields else ""
        print(json.dumps(state["threads"][cursor]))
    elif args[:2] == ["run", "view"] and "--json" in args:
        print(json.dumps({"jobs": state["jobs"][args[2]]}))
    elif args[:2] == ["run", "view"] and "--log-failed" in args:
        job = opt("--job")
        if job in state.get("log_errors", {}):
            fail(state["log_errors"][job])
        print(state["logs"][job])
    else:
        fail("unsupported " + " ".join(args))
    ''')

ALLOWED_CALLS = {("pr", "view"), ("api", "graphql"), ("run", "view")}
OWNER_REPO = "acme/pantry"


def load_module():
    sys.path.insert(0, str(TOOLBOX / "lib"))
    sys.path.insert(0, str(TOOLBOX / "bin"))
    spec = importlib.util.spec_from_file_location("pr_report_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def comment(login: str, body: str, bot: bool = False) -> dict:
    return {"author": {"login": login, "__typename": "Bot" if bot else "User"}, "body": body,
            "url": f"https://github.com/{OWNER_REPO}/pull/12#discussion_r1"}


def thread(path: str, line, body: str, login: str = "rita", resolved: bool = False, outdated: bool = False,
           replies: list | None = None, original_line=None, bot: bool = False) -> dict:
    """A thread as GraphQL returns it for the query's aliases: `first` (the opening comment) and `latest` (the last
    three, with the thread's total count)."""
    nodes = [comment(login, body, bot)] + (replies or [])
    return {"isResolved": resolved, "isOutdated": outdated, "path": path, "line": line,
            "originalLine": original_line if original_line is not None else line,
            "first": {"nodes": nodes[:1]}, "latest": {"totalCount": len(nodes), "nodes": nodes[-3:]}}


def page(nodes: list, next_cursor: str | None = None, total: int | None = None) -> dict:
    return {"data": {"repository": {"pullRequest": {"reviewThreads": {
        "totalCount": total if total is not None else len(nodes), "pageInfo": {"hasNextPage": next_cursor is not None, "endCursor": next_cursor}, "nodes": nodes}}}}}


def check_run(name: str, conclusion: str = "SUCCESS", status: str = "COMPLETED", url: str = "", workflow: str = "ci",
              started: str = "2026-11-10T10:00:00Z") -> dict:
    return {"__typename": "CheckRun", "name": name, "workflowName": workflow, "status": status,
            "conclusion": conclusion, "detailsUrl": url, "startedAt": started}


def pr_view(number: int = 12, threads_checks: list | None = None, reviews: list | None = None,
            comments: list | None = None) -> dict:
    return {"number": number, "url": f"https://github.com/{OWNER_REPO}/pull/{number}", "title": "Shelve the cans",
            "state": "OPEN", "headRefOid": "0123456789abcdef", "reviewDecision": "CHANGES_REQUESTED",
            "statusCheckRollup": threads_checks or [], "comments": comments or [], "reviews": reviews or []}


def log_lines(job: str, step: str, *messages: str) -> str:
    return "\n".join(f"{job}\t{step}\t2026-11-10T10:00:{n % 60:02d}.1234567Z {message}"
                     for n, message in enumerate(messages))


class PrReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp)
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        (bin_dir / "fake_gh.py").write_text(FAKE_GH)
        gh = bin_dir / "gh"
        gh.write_text(f"#!/bin/sh\nexec {sys.executable} {bin_dir / 'fake_gh.py'} \"$@\"\n")
        gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
        self.state_path = self.tmp / "gh.json"
        self.state = {"prs": {}, "threads": {"": page([])}, "jobs": {}, "logs": {}}
        self.env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "PR_FAKE": str(self.state_path)}

    def run_script(self, *ref: str, cwd: Path | None = None, env: dict | None = None):
        self.state_path.write_text(json.dumps(self.state))
        proc = subprocess.run([sys.executable, str(SCRIPT), *ref], env=env or self.env, cwd=cwd or self.tmp,
                              capture_output=True, text=True)
        try:
            out = json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"not JSON: {proc.stdout!r} {proc.stderr!r}")
        return proc.returncode, out

    def calls(self) -> list:
        return json.loads(self.state_path.read_text()).get("calls", [])

    def pr(self, view: dict, repo: str = OWNER_REPO) -> None:
        self.state["prs"][f"{repo}#{view['number']}"] = view

    def report(self, *ref: str) -> dict:
        code, out = self.run_script(*(ref or (f"https://github.com/{OWNER_REPO}/pull/12",)))
        self.assertEqual(code, 0, out)
        return out

    # -- review threads

    def test_three_comments_one_addressed_by_a_later_commit(self):
        self.state["threads"][""] = page([
            thread("src/Shelf.java", 42, "Rename `cans` to `canCount`.", "rita"),
            thread("src/Rack.java", 7, "Why a list here and not a set?", "sam",
                   replies=[comment("allen", "Order matters for the label printer."), comment("sam", "Ok.")]),
            thread("src/Old.java", None, "nit: stray blank line", "rita", outdated=True, original_line=19)])
        self.pr(pr_view())
        out = self.report()
        self.assertEqual([(t["path"], t["line"], t["reviewer"], t["state"]) for t in out["threads"]], [
            ("src/Shelf.java", 42, "rita", "open"), ("src/Rack.java", 7, "sam", "open"),
            ("src/Old.java", 19, "rita", "outdated")])
        self.assertEqual(out["threads"][0]["body"], "Rename `cans` to `canCount`.")
        self.assertEqual(out["threads"][1]["reply_count"], 2)
        self.assertEqual([r["author"] for r in out["threads"][1]["replies"]], ["allen", "sam"])
        self.assertTrue(out["threads"][2]["line_is_original"])
        self.assertEqual(out["review_decision"], "CHANGES_REQUESTED")

    def test_a_resolved_thread_is_resolved_even_when_its_lines_are_unchanged(self):
        self.state["threads"][""] = page([thread("a.py", 3, "Done?", resolved=True)])
        self.pr(pr_view())
        self.assertEqual(self.report()["threads"][0]["state"], "resolved")

    def test_a_thread_carries_no_diff_hunk_reactions_or_node_ids(self):
        node = thread("a.py", 3, "x")
        node["first"]["nodes"][0].update(diffHunk="@@ -1,3 +1,4 @@\n+secret", reactionGroups=[{"content": "+1"}])
        self.state["threads"][""] = page([node])
        self.pr(pr_view())
        text = json.dumps(self.report())
        self.assertNotIn("@@", text)
        self.assertNotIn("reactionGroups", text)

    def test_long_bodies_and_replies_are_cut(self):
        self.state["threads"][""] = page([thread("a.py", 3, "w " * 600, replies=[comment("allen", "r " * 400)])])
        self.pr(pr_view())
        thread_out = self.report()["threads"][0]
        self.assertLessEqual(len(thread_out["body"]), 501)
        self.assertTrue(thread_out["body"].endswith("…"))
        self.assertLessEqual(len(thread_out["replies"][0]["body"]), 301)

    def test_only_the_last_three_replies_are_kept_and_all_are_counted(self):
        replies = [comment("allen", f"reply {n}") for n in range(30)]
        self.state["threads"][""] = page([thread("a.py", 3, "x", replies=replies)])
        self.pr(pr_view())
        thread_out = self.report()["threads"][0]
        self.assertEqual(thread_out["reply_count"], 30)
        self.assertEqual([r["body"] for r in thread_out["replies"]], ["reply 27", "reply 28", "reply 29"])

    def test_a_bot_reviewer_is_kept_and_marked(self):
        self.state["threads"][""] = page([thread("a.py", 3, "possible NPE", "copilot-pull-request-reviewer", bot=True)])
        self.pr(pr_view())
        self.assertTrue(self.report()["threads"][0]["bot"])

    def test_a_deleted_reviewer_is_ghost(self):
        node = thread("a.py", 3, "x")
        node["first"]["nodes"][0]["author"] = None
        self.state["threads"][""] = page([node])
        self.pr(pr_view())
        self.assertEqual(self.report()["threads"][0]["reviewer"], "ghost")

    def test_a_pr_with_more_threads_than_the_pages_read_reports_the_true_remainder(self):
        self.state["threads"] = {"": page([thread("a.py", 1, "x")] * 100, "c1", total=900)}
        for n in range(1, 5):
            self.state["threads"][f"c{n}"] = page([thread("a.py", 1, "x")] * 100, f"c{n + 1}", total=900)
        self.pr(pr_view())
        out = self.report()
        self.assertEqual((len(out["threads"]), out["more_threads"]), (200, 700))

    def test_owner_and_name_are_sent_as_strings(self):
        self.pr(pr_view())
        self.report()
        [call] = [c for c in self.calls() if c[:2] == ["api", "graphql"]]
        self.assertIn("owner=acme", call)
        self.assertIn("name=pantry", call)
        self.assertEqual([c for c in call if c.startswith(("owner=", "name="))], ["owner=acme", "name=pantry"])
        for flag, value in zip(call, call[1:]):
            if value.startswith(("owner=", "name=")):
                self.assertEqual(flag, "-f")

    def test_threads_are_read_across_pages(self):
        self.state["threads"] = {"": page([thread("a.py", 1, "one")], "c1"), "c1": page([thread("b.py", 2, "two")])}
        self.pr(pr_view())
        self.assertEqual([t["path"] for t in self.report()["threads"]], ["a.py", "b.py"])

    def test_threads_over_the_limit_are_counted_not_listed(self):
        self.state["threads"][""] = page([thread("a.py", n + 1, f"c{n}") for n in range(100)], "c1")
        self.state["threads"]["c1"] = page([thread("b.py", n + 1, f"c{n}") for n in range(100)], "c2")
        self.state["threads"]["c2"] = page([thread("c.py", n + 1, f"c{n}") for n in range(5)], total=205)
        self.pr(pr_view())
        out = self.report()
        self.assertEqual(len(out["threads"]), 200)
        self.assertEqual(out["more_threads"], 5)

    # -- reviews and conversation

    def test_review_states_follow_the_latest_decisive_review_and_bodies_keep_their_text(self):
        reviews = [
            {"author": {"login": "rita"}, "state": "CHANGES_REQUESTED", "body": "Needs tests.", "submittedAt": "2026-11-10T09:00:00Z"},
            {"author": {"login": "rita"}, "state": "COMMENTED", "body": "", "submittedAt": "2026-11-10T11:00:00Z"},
            {"author": {"login": "sam"}, "state": "COMMENTED", "body": "", "submittedAt": "2026-11-10T09:30:00Z"},
            {"author": {"login": "sam"}, "state": "APPROVED", "body": "LGTM", "submittedAt": "2026-11-10T12:00:00Z"},
            {"author": {"login": "lee"}, "state": "PENDING", "body": "draft", "submittedAt": "2026-11-10T12:00:00Z"}]
        self.pr(pr_view(reviews=reviews))
        out = self.report()["reviews"]
        self.assertEqual(out["states"], {"rita": "CHANGES_REQUESTED", "sam": "APPROVED"})
        self.assertEqual([(b["reviewer"], b["state"], b["body"]) for b in out["bodies"]],
                         [("rita", "CHANGES_REQUESTED", "Needs tests."), ("sam", "APPROVED", "LGTM")])

    def test_conversation_comments_skip_bots_and_count_them(self):
        comments = [{"author": {"login": "rita"}, "body": "Can you also update the README?", "url": "u1"},
                    {"author": {"login": "codecov[bot]"}, "body": "coverage 80%", "url": "u2"},
                    {"author": {"login": "github-actions"}, "body": "deployed", "url": "u3"}]
        self.pr(pr_view(comments=comments))
        out = self.report()
        self.assertEqual([c["author"] for c in out["conversation"]], ["rita"])
        self.assertEqual(out["bots_skipped"], 2)

    # -- checks

    def failing_build(self, log: str, steps=None, job_url: str | None = None) -> None:
        url = job_url or f"https://github.com/{OWNER_REPO}/actions/runs/900/job/7001"
        self.pr(pr_view(threads_checks=[check_run("build", "FAILURE", url=url), check_run("lint"),
                                        check_run("deploy", "", "IN_PROGRESS")]))
        self.state["jobs"]["900"] = [
            {"databaseId": 7001, "name": "build", "conclusion": "failure",
             "steps": steps or [{"name": "Set up job", "conclusion": "success"},
                                {"name": "Run mvn verify", "conclusion": "failure"},
                                {"name": "Post checkout", "conclusion": "skipped"}]}]
        self.state["logs"]["7001"] = log

    def test_a_failed_build_names_the_check_the_step_and_the_error_lines_only(self):
        noise = [f"[INFO] downloading artifact {n}" for n in range(300)]
        self.failing_build(log_lines(
            "build", "Run mvn verify", *noise,
            "[ERROR] Tests run: 3, Failures: 1, Errors: 0, Skipped: 0, Time elapsed: 0.1 s <<< FAILURE!",
            "[ERROR] Failures: ",
            "[ERROR]   ShelfTest.countsCans:42 expected: <3> but was: <2>",
            "[ERROR] ",
            "[ERROR] To see the full stack trace of the errors, re-run Maven with the -e switch.",
            "[ERROR] -> [Help 1]",
            "##[error]Process completed with exit code 1."))
        out = self.report()["checks"]
        self.assertEqual((out["passed"], out["pending"]), (1, ["deploy"]))
        [failed] = out["failed"]
        self.assertEqual((failed["name"], failed["step"], failed["result"]), ("build", "Run mvn verify", "failure"))
        self.assertEqual(failed["errors"], [
            "[ERROR] Tests run: 3, Failures: 1, Errors: 0, Skipped: 0, Time elapsed: 0.1 s <<< FAILURE!",
            "[ERROR] ShelfTest.countsCans:42 expected: <3> but was: <2>",  # the log's indent is collapsed
            "##[error]Process completed with exit code 1."])
        self.assertNotIn("downloading", json.dumps(out))
        self.assertNotIn("2026-11-10T", json.dumps(out))  # no raw log timestamp

    def test_the_log_is_read_for_the_failed_job_only_through_log_failed(self):
        self.failing_build(log_lines("build", "Run mvn verify", "##[error]boom"))
        self.report()
        runs = [call for call in self.calls() if call[:2] == ["run", "view"]]
        self.assertEqual(runs, [["run", "view", "900", "--repo", OWNER_REPO, "--json", "jobs"],
                                ["run", "view", "900", "--repo", OWNER_REPO, "--job", "7001", "--log-failed"]])

    def test_a_log_with_no_error_looking_lines_gives_its_last_eight(self):
        self.failing_build(log_lines("build", "Run mvn verify", *[f"line {n}" for n in range(30)]))
        errors = self.report()["checks"]["failed"][0]["errors"]
        self.assertEqual(errors, [f"line {n}" for n in range(22, 30)])

    def test_many_error_lines_keep_the_first_nine_and_last_three(self):
        self.failing_build(log_lines("build", "Run mvn verify", *[f"[ERROR] problem {n}" for n in range(40)]))
        errors = self.report()["checks"]["failed"][0]["errors"]
        self.assertEqual(len(errors), 12)
        self.assertEqual(errors[:9], [f"[ERROR] problem {n}" for n in range(9)])
        self.assertEqual(errors[9:], [f"[ERROR] problem {n}" for n in (37, 38, 39)])

    def test_a_very_long_error_line_is_cut(self):
        self.failing_build(log_lines("build", "Run mvn verify", "[ERROR] " + "x" * 900))
        [line] = self.report()["checks"]["failed"][0]["errors"]
        self.assertLessEqual(len(line), 301)

    def test_the_failing_step_comes_from_the_log_when_the_jobs_list_has_none(self):
        self.failing_build(log_lines("build", "Compile", "##[error]boom"),
                           steps=[{"name": "Set up job", "conclusion": "success"}])
        self.assertEqual(self.report()["checks"]["failed"][0]["step"], "Compile")

    def test_a_check_with_no_job_id_in_its_url_finds_the_job_by_name(self):
        self.failing_build(log_lines("build", "Run mvn verify", "##[error]boom"),
                           job_url=f"https://github.com/{OWNER_REPO}/actions/runs/900")
        failed = self.report()["checks"]["failed"][0]
        self.assertEqual((failed["step"], failed["errors"]), ("Run mvn verify", ["##[error]boom"]))

    def test_a_log_gh_cant_fetch_is_a_note_not_a_failure(self):
        self.failing_build("")
        self.state["log_errors"] = {"7001": "gh: failed to get run log: log has expired (HTTP 410)"}
        failed = self.report()["checks"]["failed"][0]
        self.assertEqual(failed["errors"], [])
        self.assertEqual(failed["step"], "Run mvn verify")  # the jobs listing still names it
        self.assertIn("log unavailable", failed["note"])
        self.assertIn("expired", failed["note"])

    def test_an_external_status_check_has_no_log_and_says_so(self):
        self.pr(pr_view(threads_checks=[{"__typename": "StatusContext", "context": "sonar", "state": "FAILURE",
                                         "targetUrl": "https://sonar.example.test/dash/1",
                                         "description": "Quality gate failed"}]))
        [failed] = self.report()["checks"]["failed"]
        self.assertEqual((failed["name"], failed["errors"], failed["description"]),
                         ("sonar", [], "Quality gate failed"))
        self.assertIn("not a GitHub Actions job", failed["note"])
        self.assertFalse([c for c in self.calls() if c[0] == "run"])

    def test_a_re_run_replaces_the_run_it_re_ran(self):
        url = f"https://github.com/{OWNER_REPO}/actions/runs/900/job/7001"
        self.pr(pr_view(threads_checks=[
            check_run("build", "FAILURE", url=url, started="2026-11-10T10:00:00Z"),
            check_run("build", "SUCCESS", url=url, started="2026-11-10T11:00:00Z")]))
        out = self.report()["checks"]
        self.assertEqual((out["failed"], out["passed"]), ([], 1))
        self.assertFalse([c for c in self.calls() if c[0] == "run"])

    def test_cancelled_and_timed_out_checks_are_failures_and_skipped_neutral_ones_pass(self):
        self.pr(pr_view(threads_checks=[
            check_run("a", "CANCELLED"), check_run("b", "TIMED_OUT"), check_run("c", "SKIPPED"),
            check_run("d", "NEUTRAL")]))
        out = self.report()["checks"]
        self.assertEqual(sorted(c["name"] for c in out["failed"]), ["a", "b"])
        self.assertEqual(out["passed"], 2)

    def test_failed_checks_over_the_limit_are_counted_not_listed(self):
        self.pr(pr_view(threads_checks=[check_run(f"job{n}", "FAILURE") for n in range(13)]))
        out = self.report()["checks"]
        self.assertEqual((len(out["failed"]), out["more_failed"]), (10, 3))

    def test_two_failed_jobs_of_one_run_list_the_jobs_once(self):
        self.pr(pr_view(threads_checks=[
            check_run("build", "FAILURE", url=f"https://github.com/{OWNER_REPO}/actions/runs/900/job/7001"),
            check_run("test", "FAILURE", url=f"https://github.com/{OWNER_REPO}/actions/runs/900/job/7002")]))
        step = [{"name": "Run", "conclusion": "failure"}]
        self.state["jobs"]["900"] = [{"databaseId": 7001, "name": "build", "steps": step},
                                     {"databaseId": 7002, "name": "test", "steps": step}]
        self.state["logs"] = {"7001": log_lines("build", "Run", "##[error]one"),
                              "7002": log_lines("test", "Run", "##[error]two")}
        out = self.report()["checks"]["failed"]
        self.assertEqual([(c["name"], c["errors"]) for c in out], [("build", ["##[error]one"]), ("test", ["##[error]two"])])
        self.assertEqual(len([c for c in self.calls() if c[:2] == ["run", "view"] and "--json" in c]), 1)

    # -- what it never does, and its inputs

    def test_it_only_makes_read_calls(self):
        self.state["threads"][""] = page([thread("a.py", 1, "x")])
        self.failing_build(log_lines("build", "Run mvn verify", "##[error]boom"))
        self.report()
        for call in self.calls():
            self.assertIn(tuple(call[:2]), ALLOWED_CALLS, call)
            self.assertNotIn("-X", call)
            self.assertNotIn("--method", call)
            self.assertFalse(any(arg.startswith("body=") or arg == "--body" for arg in call), call)
            self.assertNotIn("mutation", " ".join(call))

    def test_the_three_ways_to_name_the_pr_give_the_same_report(self):
        self.pr(pr_view())
        reports = [self.report(*ref) for ref in ((f"https://github.com/{OWNER_REPO}/pull/12",),
                                                 (OWNER_REPO, "12"), (f"{OWNER_REPO}#12",))]
        self.assertEqual(reports[0], reports[1])
        self.assertEqual(reports[0], reports[2])
        self.assertEqual((reports[0]["repo"], reports[0]["number"], reports[0]["head"]), (OWNER_REPO, 12, "0123456789"))

    def test_a_workspace_repo_name_resolves_through_origin(self):
        root = self.tmp / "ws"
        (root / ".claude").mkdir(parents=True)
        (root / ".claude" / "toolbox.local.yml").write_text(yaml.safe_dump({
            "version": 1, "features": ["pr"], "workspace": {"name": "pantry", "layout": "single", "default_branch": "main"},
            "user": {"handle": "jdoe", "name": "Jordan"}, "vault": {"path": "vault"},
            "tracker": {"kind": "vault", "key_prefix": "PT"}}))
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "remote", "add", "origin", f"https://github.com/{OWNER_REPO}.git"],
                       check=True)
        self.pr(pr_view(number=7))
        code, out = self.run_script(".", "7", cwd=root, env={**self.env, "TOOLBOX_WORKSPACE": str(root)})
        self.assertEqual((code, out["repo"], out["number"]), (0, OWNER_REPO, 7))

    def test_the_configs_pr_bots_are_skipped_in_conversation_and_marked_on_threads(self):
        root = self.tmp / "ws"
        (root / ".claude").mkdir(parents=True)
        (root / ".claude" / "toolbox.local.yml").write_text(yaml.safe_dump({
            "version": 1, "features": ["pr"], "workspace": {"name": "pantry", "layout": "single", "default_branch": "main"},
            "user": {"handle": "jdoe", "name": "Jordan"}, "vault": {"path": "vault"},
            "tracker": {"kind": "vault", "key_prefix": "PT"}, "pr": {"bots": ["scanner-account"]}}))
        self.state["threads"][""] = page([thread("a.py", 3, "smell", "scanner-account")])
        self.pr(pr_view(comments=[{"author": {"login": "scanner-account"}, "body": "Quality gate passed", "url": "u"},
                                  {"author": {"login": "rita"}, "body": "Please add a test.", "url": "u2"}]))
        code, out = self.run_script(f"https://github.com/{OWNER_REPO}/pull/12", cwd=root,
                                    env={**self.env, "TOOLBOX_WORKSPACE": str(root)})
        self.assertEqual(code, 0, out)
        self.assertEqual([c["author"] for c in out["conversation"]], ["rita"])
        self.assertEqual(out["bots_skipped"], 1)
        self.assertTrue(out["threads"][0]["bot"])
        # without the workspace's config the account is an ordinary user
        out = self.report()
        self.assertEqual([c["author"] for c in out["conversation"]], ["scanner-account", "rita"])

    def test_a_bad_reference_is_exit_2(self):
        for ref in (("not-a-pr",), (OWNER_REPO, "twelve"), (OWNER_REPO, "1", "2")):
            code, out = self.run_script(*ref)
            self.assertEqual(code, 2, ref)
            self.assertIn("error", out)

    def test_a_pr_gh_cant_find_is_exit_3_with_ghs_reason(self):
        code, out = self.run_script(f"https://github.com/{OWNER_REPO}/pull/99")
        self.assertEqual(code, 3)
        self.assertIn("Could not resolve to a PullRequest", out["error"])

    def test_the_output_is_far_smaller_than_the_log_it_was_read_from(self):
        self.failing_build(log_lines("build", "Run mvn verify", *[f"[INFO] building module {n}" for n in range(5000)],
                                     "[ERROR] BUILD FAILURE here"))
        out = json.dumps(self.report())
        self.assertLess(len(out), 3000)


class ErrorLinesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def test_ansi_color_and_timestamps_are_stripped(self):
        lines = ["2026-11-10T10:00:00.1234567Z \x1b[31m[ERROR]\x1b[0m compile failed: Foo.java:[3,1]"]
        self.assertEqual(self.module.error_lines(lines), ["[ERROR] compile failed: Foo.java:[3,1]"])

    def test_javascript_and_python_failures_are_found(self):
        lines = ["npm ERR! code ELIFECYCLE", "ok", "Traceback (most recent call last):",
                 "AssertionError: 1 != 2", "src/a.ts: error TS2345: bad arg"]
        self.assertEqual(len(self.module.error_lines(lines)), 4)

    def test_blank_lines_are_dropped_and_an_empty_log_gives_nothing(self):
        self.assertEqual(self.module.error_lines(["", "  "]), [])


if __name__ == "__main__":
    unittest.main()
