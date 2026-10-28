"""Walkthroughs of work-task's script calls, as its phase files prescribe them, and checks that the skill's text names
only headings, files, scripts and commands that exist.

Three fixtures: a single-repo workspace with its vault inside the repo (`vault` tracker, no PRs: approval pushes to the
default branch of a local bare remote), a nested workspace on the `jira` tracker (an issue imported from a saved
response, two repos, PRs through a fake `gh`), and a nested workspace with a shared library (both cycles, no PRs, the
release wait on the fake `gh`'s default-branch run). Each walkthrough reaches both stops, resumes from the task record
in a fresh session at each, and publishes. No MCP calls; `gh` is tests/fake_gh.py.

Run from the repo root:

    python3 -m unittest discover -s workspace/.claude/toolbox/tests
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

TOOLBOX = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
WORKSPACE_DOMAIN = TOOLBOX.parents[1]
SKILL = WORKSPACE_DOMAIN / ".claude" / "skills" / "work-task"
sys.path.insert(0, str(TOOLBOX / "lib"))

import doctor  # noqa: E402
import extensions  # noqa: E402

CLOUD = "00000000-test-4000-8000-000000000001"
# work-task's hooks (SKILL.md "Extension Points"), in order, with the verbs each allows.
HOOKS = {
    "ground-rules": "add", "stop-conditions": "add", "dispatch": "add", "before-first-cycle": "add",
    "plan-the-cycles": "add, replace", "after-implementing": "add", "format": "add, skip", "unit-tests": "add, skip",
    "before-integration-tests": "add", "integration-tests": "add, skip", "blocked-bracket": "add, replace",
    "code-review": "add, skip", "verify": "add, skip", "review-checks": "add", "needs-a-release": "add, replace",
    "before-publishing": "add", "after-the-merge": "add", "before-done": "add",
}
SKILLS = WORKSPACE_DOMAIN / ".claude" / "skills"
README = WORKSPACE_DOMAIN / ".claude" / "extensions" / "README.md"


def sh(cwd: Path, *args: str) -> str:
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def dispatch(fields: dict) -> str:
    """The phase file SKILL.md's Dispatch table sends a session to, from the record's fields."""
    status, stage = fields.get("status"), fields.get("stage")
    if status in (None, "todo") or (status == "in-progress" and stage in ("implementing", "testing")):
        return "work.md"
    if status == "review" and stage == "awaiting-ci" and fields.get("cycle") == "shared-library" \
            and not fields.get("worktrees"):
        return "release"  # merge.md "Wait for the Release"
    if status == "review":
        return {"code-review": "code-review.md", "publish-review": "publish-review.md",
                "awaiting-ci": "merge.md", "awaiting-merge": "merge.md"}[stage]
    if status == "done":
        return "nothing" if fields.get("usage_total") else "merge.md"
    return "ask"


class Walkthrough(unittest.TestCase):
    layout = "single"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.root = self.tmp / "ws"
        self.root.mkdir()
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(f"#!/bin/sh\nexec {sys.executable} {TESTS / 'fake_gh.py'} \"$@\"\n")
        gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
        self.home = self.tmp / "claude"
        self.remotes: dict[str, str] = {}
        self.env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                    "GH_FAKE_STATE": str(self.tmp / "gh.json"), "GH_FAKE_DEFAULT": "main",
                    "CLAUDE_CONFIG_DIR": str(self.home), "TMPDIR": str(self.tmp),
                    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_AUTHOR_NAME": "Tester", "GIT_AUTHOR_EMAIL": "t@example.test",
                    "GIT_COMMITTER_NAME": "Tester", "GIT_COMMITTER_EMAIL": "t@example.test"}
        self.env.pop("TOOLBOX_WORKSPACE", None)
        os.environ.update({k: self.env[k] for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "GIT_AUTHOR_NAME",
                                                    "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME",
                                                    "GIT_COMMITTER_EMAIL")})

    # Construction

    def make_repo(self, path: Path, name: str, files: dict) -> None:
        bare = self.tmp / "remotes" / f"{name}.git"
        sh(self.tmp, "git", "init", "--bare", "-b", "main", str(bare))
        path.mkdir(parents=True, exist_ok=True)
        sh(path, "git", "init", "-b", "main")
        for rel, text in files.items():
            (path / rel).parent.mkdir(parents=True, exist_ok=True)
            (path / rel).write_text(text)
        sh(path, "git", "add", "-A")
        sh(path, "git", "commit", "-m", "initial")
        sh(path, "git", "remote", "add", "origin", str(bare))
        sh(path, "git", "push", "-u", "origin", "main")
        (path / ".git" / "info" / "exclude").write_text(".worktrees/\n.claude/\n")
        self.remotes[f"remotes/{name}"] = str(bare)  # fake_gh's --repo: owner/name from origin's path

    def configure(self, raw: dict) -> None:
        (self.root / ".claude").mkdir(exist_ok=True)
        (self.root / ".claude" / "toolbox.local.yml").write_text(yaml.safe_dump(raw))
        (self.root / ".claude" / "toolbox").symlink_to(TOOLBOX)
        self.env["GH_FAKE_REMOTES"] = json.dumps(self.remotes)

    # Running the scripts, as a session would

    def script(self, name: str, *args: str, cwd: Path | None = None, code: int | None = 0) -> dict:
        proc = subprocess.run([sys.executable, str(self.root / ".claude/toolbox/bin" / name), *args],
                              cwd=cwd or self.root, capture_output=True, text=True, env=self.env, timeout=300)
        if code is not None:
            self.assertEqual(proc.returncode, code, proc.stdout + proc.stderr)
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            return {"text": proc.stdout}

    def tasks(self, *args: str, **kw) -> dict:
        return self.script("tasks.py", *args, **kw)

    def context(self, source: str, session: str) -> dict:
        return self.tasks("context", source, "--session", session)

    def fields(self, wid: str) -> dict:
        return self.tasks("get", wid)["fields"]

    def transcript(self, session: str) -> None:
        folder = self.home / "projects" / "-ws"
        folder.mkdir(parents=True, exist_ok=True)
        line = {"type": "assistant", "requestId": f"r-{session}", "timestamp": "2026-10-27T10:00:00Z",
                "message": {"model": "claude-sonnet-5-5", "usage": {"input_tokens": 100, "output_tokens": 50}}}
        (folder / f"{session}.jsonl").write_text(json.dumps(line) + "\n")

    # Steps every walkthrough shares

    def stop(self, wid: str, publish_dir: Path, name: str, stage: str, diff: str) -> None:
        """Present a stop: set the record and save the report, as code-review.md and publish-review.md say."""
        out = self.tasks("set", wid, "status=review", "owner=jordan", f"stage={stage}")
        self.assertIsNone(out["write_back"])
        (publish_dir / f"{name}.md").write_text(f"## {wid} · {name.replace('-', ' ')}\n\n- **Diff:** {diff}\n")

    def resume(self, wid: str, session: str, phase: str) -> dict:
        """A fresh session: context records it, and the record alone names the phase and the saved stop."""
        out = self.context(wid, session)
        self.assertTrue(out["new_session"])
        self.assertEqual(dispatch(out["fields"]), phase)
        return out

    def finish(self, wid: str, sessions: list[str]) -> None:
        for session in sessions:
            self.transcript(session)
        out = self.script("session_usage.py", "--task", wid)
        self.assertEqual(out["total"]["requests"], len(sessions))
        fields = self.fields(wid)
        self.assertEqual([u["session"] for u in fields["usage"]], sessions)
        self.assertEqual(dispatch(fields), "nothing")


class SingleVaultTest(Walkthrough):
    """One repo, the vault inside it, keys from the vault counter, no PRs."""

    def setUp(self):
        super().setUp()
        plan = "vault/Discussions/2026/10/2026-10-27-shelf-counts/plan.md"
        self.make_repo(self.root, "pantry", {
            "app/src/Shelf.java": "class Shelf {}\n", "web/index.ts": "export {}\n",
            plan: "---\ntype: plan\ntitle: Shelf Counts\n---\n\n# Shelf Counts\n\nAdd a count to each shelf in `app`.\n",
        })
        self.plan = plan
        self.configure({
            "version": 1, "features": ["tasks", "worktrees"],
            "workspace": {"name": "pantry", "layout": "single", "default_branch": "main", "areas": {
                "app": {"format": "touch .formatted", "unit": "test -f .formatted", "files": ["*.java"]},
                "web": {"unit": "true", "files": ["*.ts"]}}},
            "user": {"handle": "jdoe", "name": "Jordan"}, "vault": {"path": "vault"},
            "tracker": {"kind": "vault", "key_prefix": "PT"},
            "pr": {"required": False},
        })

    def test_walkthrough(self):
        # Setup: a plan makes a keyed `task` record whose requirement the session writes.
        out = self.context(self.plan, "s1")
        wid, doc, publish_dir = out["work_id"], Path(out["doc"]), Path(out["publish_dir"])
        self.assertEqual((wid, out["made"], out["needs_requirement"]), ("PT-1", True, True))
        self.assertEqual(dispatch(out["fields"]), "work.md")
        text = doc.read_text()
        text = re.sub(r"(## Description\n\n)<!--.*?-->", r"\1Add a `count` to each shelf in `app`.", text, flags=re.S)
        doc.write_text(text)
        self.assertFalse(self.context(wid, "s1")["needs_requirement"])

        # Work: fresh start, worktree, implement, checks that apply.
        self.tasks("set", wid, "status=in-progress", "owner=claude", "stage=implementing")
        made = self.script("worktrees.py", "create", wid, "--desc", "shelf-counts")
        tree = Path(made["results"][0]["path"])
        self.assertEqual(made["branch"], "jdoe/PT-1-shelf-counts")
        self.tasks("set-list", wid, "worktrees", str(tree))
        (tree / "app/src/Shelf.java").write_text("class Shelf { int count; }\n")
        (tree / "app/README.md").write_text("notes\n")
        changes = self.script("worktrees.py", "changes", wid)
        self.assertEqual(changes["areas"], [f"app={tree / 'app'}"])
        self.assertEqual(changes["skipped"], [])
        diff = changes["checkouts"][0]["diff"]
        self.assertEqual(diff, "2 files, +2 −1")
        # A shell that moved into the worktree finds the workspace's scripts and records the same way.
        self.assertEqual(self.tasks("get", wid, "stage", cwd=tree)["fields"], {"stage": "implementing"})
        self.script("run_tests.py", "format", *changes["areas"], "--label", wid)
        self.tasks("set", wid, "stage=testing")
        tests = self.script("run_tests.py", "unit", *changes["areas"], "--label", wid)
        self.assertIn("app (unit): pass", tests["text"])
        (tree / "app/.formatted").unlink()  # format output isn't part of the change

        # Stop 1, then a fresh session resumes it from the record and the saved report.
        self.stop(wid, publish_dir, "code-review", "code-review", diff)
        self.resume(wid, "s2", "code-review.md")
        self.assertIn(diff, (publish_dir / "code-review.md").read_text())
        self.assertEqual(self.script("worktrees.py", "changes", wid)["checkouts"][0]["diff"], diff)

        # Close out: the drafts go to the publish folder; stop 2; a fresh session resumes it.
        (publish_dir / "commit.txt").write_text("PT-1 add shelf counts\n\nShelves now carry a count.\n")
        self.stop(wid, publish_dir, "publish-review", "publish-review", diff)
        self.resume(wid, "s3", "publish-review.md")

        # Approval: no PRs, so the run pushes straight to the default branch and closes the task.
        out = self.script("publish.py", "run", wid)
        self.assertTrue(out["done"], out)
        self.assertEqual(sh(self.root, "git", "--git-dir", self.remotes["remotes/pantry"], "log", "-1", "--format=%s",
                            "main"), "PT-1 add shelf counts")
        fields = self.fields(wid)
        self.assertEqual((fields["status"], fields["stage"], fields["worktrees"]), ("done", None, []))
        self.assertEqual(dispatch(fields), "merge.md")
        self.assertEqual(self.script("worktrees.py", "list", wid)["results"], [])
        self.assertFalse((self.root / ".worktrees" / wid).exists())
        self.finish(wid, ["s1", "s2", "s3"])


class NestedJiraTest(Walkthrough):
    """A folder of repos, Jira keys imported from a saved issue, PRs, and a merge instruction."""

    def setUp(self):
        super().setUp()
        for name in ("api", "web"):
            self.make_repo(self.root / name, name, {"README.md": f"{name}\n", "src/Main.java": "class Main {}\n",
                                                    ".github/pull_request_template.md": "# Checklist\n- [ ] Tests\n"})
        (self.root / "notes").mkdir()
        self.configure({
            "version": 1, "features": ["tasks", "worktrees", "pr"],
            "workspace": {"name": "fleet", "layout": "nested", "default_branch": "main",
                          "areas": {"*": {"unit": "true", "files": ["*.java"]}}},
            "user": {"handle": "jdoe", "name": "Jordan"}, "vault": {"path": "notes"},
            "tracker": {"kind": "jira", "key_prefix": "ACME", "jira": {
                "mcp_server": "tracker-test", "site": "acme-test.atlassian.net", "cloud_id": CLOUD,
                "project": "ACME", "account": "tester@example.com",
                "fields": {"acceptance_criteria": "customfield_54321"},
                "issue_types": {"story": "User Story", "defect": "Defect"}}},
            "git": {"org": "acme"},
            "pr": {"required": True, "wait_for_ci": True, "announce": {"channel": "C123"}},
        })

    def test_walkthrough(self):
        # Setup: no record yet, so the session imports the saved getJiraIssue response and loads it again.
        out = self.tasks("context", "ACME-12", "--session", "s1", code=1)
        self.assertEqual(out["needs_import"], "ACME-12")
        self.tasks("import", "ACME-12", "--issue", str(TESTS / "fixtures" / "jira" / "issue-story.json"))
        out = self.context("ACME-12", "s1")
        wid, publish_dir = out["work_id"], Path(out["publish_dir"])
        self.assertEqual((wid, out["needs_requirement"], out["fields"]["depends_on"]), ("ACME-12", False, ["ACME-11"]))
        # work.dependencies defaults to isolate: an undone dependency with no record doesn't hold the task up.
        self.assertEqual(self.script("toolbox.py", "config", "show", "work.dependencies"), "isolate")

        self.tasks("set", wid, "status=in-progress", "owner=claude", "stage=implementing")
        made = self.script("worktrees.py", "create", wid, "--repos", "api,web", "--desc", "shelf-counts")
        trees = {r["repo"]: Path(r["path"]) for r in made["results"]}
        self.tasks("set-list", wid, "worktrees", *map(str, trees.values()))
        (trees["api"] / "src/Main.java").write_text("class Main { int count; }\n")
        (trees["web"] / "README.md").write_text("web, with counts\n")
        changes = self.script("worktrees.py", "changes", wid)
        self.assertEqual(changes["areas"], [f"api={trees['api']}"])
        self.assertEqual([(s["area"], s["reason"]) for s in changes["skipped"]],
                         [("web", "no changed file matches *.java")])
        self.tasks("set", wid, "stage=testing")
        self.assertIn("api (unit): pass", self.script("run_tests.py", "unit", *changes["areas"])["text"])

        self.stop(wid, publish_dir, "code-review", "code-review", "1 file, +1 −1")
        self.resume(wid, "s2", "code-review.md")
        for repo in ("api", "web"):
            (publish_dir / f"{repo}.txt").write_text(f"ACME-12 show shelf counts in {repo}\n\nAdds the count.\n")
        (publish_dir / "announce.txt").write_text("Shelf counts are up for review.\n")
        self.stop(wid, publish_dir, "publish-review", "publish-review", "1 file, +1 −1")
        self.resume(wid, "s3", "publish-review.md")

        # Approval: commit, push and open the PRs; the merge waits for the user's instruction.
        out = self.script("publish.py", "run", wid, "--stop-after", "open-pr")
        self.assertEqual(out["stopped_after"], "open-pr")
        self.assertEqual(out["announce"]["channel"], "C123")
        self.tasks("set", wid, "owner=jordan")
        fields = self.resume(wid, "s4", "merge.md")["fields"]
        self.assertEqual((fields["status"], fields["stage"], len(fields["prs"])), ("review", "awaiting-ci", 2))
        state = json.loads((self.tmp / "gh.json").read_text())
        self.assertEqual({p["state"] for p in state["prs"]}, {"OPEN"})

        # The merge instruction: the same run resumes, waits for CI, merges and cleans up.
        out = self.script("publish.py", "run", wid, "--interval", "0", "--grace", "0")
        self.assertTrue(out["done"], out)
        state = json.loads((self.tmp / "gh.json").read_text())
        self.assertEqual({p["state"] for p in state["prs"]}, {"MERGED"})
        for repo in ("api", "web"):
            self.assertEqual(sh(self.root, "git", "--git-dir", self.remotes[f"remotes/{repo}"], "log", "-1",
                                "--format=%s", "main"), f"ACME-12 show shelf counts in {repo}")
        self.assertFalse((self.root / ".worktrees" / wid).exists())
        self.finish(wid, ["s1", "s2", "s3", "s4"])


class NestedSharedLibraryTest(Walkthrough):
    """A library repo and its consumer, no PRs: the shared-library cycle publishes the library, the release wait sees
    the default branch's run of its commit, and the consumers cycle picks up the release and publishes the consumer."""

    def setUp(self):
        super().setUp()
        self.make_repo(self.root / "common", "common", {"src/Lib.java": "class Lib {}\n"})
        self.make_repo(self.root / "api", "api", {"src/Main.java": "class Main {}\n", "deps.txt": ""})
        plan = self.root / "notes/Discussions/2026/10/2026-10-27-shelf-labels/plan.md"
        plan.parent.mkdir(parents=True)
        plan.write_text("---\ntype: plan\ntitle: Shelf Labels\n---\n\n# Shelf Labels\n\nAdd labels to `common`, "
                        "then show them in `api`.\n")
        self.plan = str(plan.relative_to(self.root / "notes"))
        self.configure({
            "version": 1, "features": ["tasks", "worktrees"],
            "workspace": {"name": "fleet", "layout": "nested", "default_branch": "main",
                          "areas": {"*": {"unit": "true"}}},
            "user": {"handle": "jdoe", "name": "Jordan"}, "vault": {"path": "notes"},
            "tracker": {"kind": "vault", "key_prefix": "SL"},
            "pr": {"required": False},
            "shared_libraries": {"names": ["common"], "update": {"command": "echo {libraries} released >> deps.txt"}},
        })

    def cycle(self, wid: str, publish_dir: Path, repo: str, first: str, label: str) -> Path:
        """One cycle from worktree to stop 2, resumed in fresh sessions at both stops; returns its worktree."""
        made = self.script("worktrees.py", "create", wid, "--repos", repo, "--desc", "shelf-labels")
        tree = Path(made["results"][0]["path"])
        self.tasks("set-list", wid, "worktrees", str(tree))
        if repo == "api":
            # Step 2 starts by picking up the release, with the configured command run in the consumer checkout.
            update = self.script("toolbox.py", "config", "show", "shared_libraries.update")
            subprocess.run(update["command"].format(libraries="common"), shell=True, cwd=tree, check=True)
            (tree / "src/Main.java").write_text("class Main { String label; }\n")
        else:
            (tree / "src/Lib.java").write_text("class Lib { String label; }\n")
        changes = self.script("worktrees.py", "changes", wid)
        self.assertEqual(changes["areas"], [f"{repo}={tree}"])
        self.tasks("set", wid, "stage=testing")
        self.assertIn(f"{repo} (unit): pass", self.script("run_tests.py", "unit", *changes["areas"])["text"])
        diff = changes["checkouts"][0]["diff"]
        self.stop(wid, publish_dir, "code-review", "code-review", diff)
        self.assertEqual(self.resume(wid, first, "code-review.md")["fields"]["cycle"], label)
        (publish_dir / f"{repo}.txt").write_text(f"SL-1 shelf labels in {repo}\n\nAdds labels.\n")
        self.stop(wid, publish_dir, "publish-review", "publish-review", diff)
        self.resume(wid, first + "b", "publish-review.md")
        return tree

    def test_walkthrough(self):
        out = self.context(self.plan, "s1")
        wid, doc, publish_dir = out["work_id"], Path(out["doc"]), Path(out["publish_dir"])
        doc.write_text(re.sub(r"(## Description\n\n)<!--.*?-->", r"\1Add labels to `common`, then use them in `api`.",
                              doc.read_text(), flags=re.S))

        # Plan the cycles: the task changes a configured shared library, so two cycles.
        self.assertEqual(self.script("toolbox.py", "config", "show", "shared_libraries")["names"], ["common"])
        self.tasks("set", wid, "status=in-progress", "owner=claude", "stage=implementing", "cycle=shared-library")
        self.cycle(wid, publish_dir, "common", "s2", "shared-library")

        # Approval publishes the library; the task stays open, waiting for the release.
        out = self.script("publish.py", "run", wid)
        self.assertEqual((out["done"], out["next"]), (True, "release"), out)
        common_head = sh(self.root, "git", "--git-dir", self.remotes["remotes/common"], "rev-parse", "main")
        self.assertEqual(json.loads((publish_dir / "merged.json").read_text()), {"common": common_head})
        fields = self.fields(wid)
        self.assertEqual((fields["status"], fields["stage"], fields["owner"], fields["worktrees"]),
                         ("review", "awaiting-ci", "claude", []))

        # A fresh session lands on the release wait, and the watcher sees the default branch's green run.
        self.resume(wid, "s3", "release")
        out = self.script("watch_task.py", "pipeline", wid)
        self.assertEqual([(p["repo"], p["conclusion"]) for p in out["pipelines"]], [("common", "success")])
        self.assertEqual(out["pipelines"][0]["tags"], [])

        # The consumers cycle: fresh worktrees from the released default branch, the update, checks, stops, publish.
        self.tasks("set", wid, "cycle=consumers", "status=in-progress", "owner=claude", "stage=implementing")
        self.assertEqual(dispatch(self.fields(wid)), "work.md")
        tree = self.cycle(wid, publish_dir, "api", "s4", "consumers")
        self.assertEqual((tree / "deps.txt").read_text(), "common released\n")
        out = self.script("publish.py", "run", wid)
        self.assertTrue(out["done"], out)
        self.assertNotIn("next", out)
        self.assertEqual(sh(self.root, "git", "--git-dir", self.remotes["remotes/api"], "show", "main:deps.txt"),
                         "common released")
        fields = self.fields(wid)
        self.assertEqual((fields["status"], fields["stage"], fields["cycle"]), ("done", None, "consumers"))
        self.assertFalse((self.root / ".worktrees" / wid).exists())
        self.finish(wid, ["s1", "s2", "s2b", "s3", "s4", "s4b"])


class SkillTextTest(unittest.TestCase):
    """The skill names only headings, files, scripts and subcommands that exist."""

    def setUp(self):
        self.skill = (SKILL / "SKILL.md").read_text()
        self.texts = {p.name: p.read_text() for p in [SKILL / "SKILL.md", *sorted((SKILL / "references").glob("*.md"))]}

    def test_hooks_are_declared_and_each_is_named_where_it_runs(self):
        points, problems = extensions.points(self.skill)
        self.assertEqual(problems, [])
        self.assertEqual({p.id: ", ".join(p.verbs) for p in points}, HOOKS)
        self.assertEqual([p.id for p in points], list(HOOKS))
        for point in points:
            named = re.search(r"`([a-z-]+\.md)`", point.runs)
            where = named.group(1) if named else "SKILL.md"
            self.assertRegex(" ".join(self.texts[where].split()), rf"[Hh]ook `{point.id}`", f"{point.id} in {where}")
            self.assertNotIn("replace", point.verbs if point.id in ("format", "unit-tests", "integration-tests",
                                                                      "code-review", "verify") else ())

    def test_an_extension_using_every_hook_parses(self):
        text = "---\ntype: extension\nextends: work-task\n---\n\n" + "\n\n".join(
            f"## {verb} {hook}\n\nMore." for hook, verbs in HOOKS.items() for verb in verbs.split(", "))
        doc = extensions.parse(text)
        self.assertEqual((doc.extends, doc.problems), ("work-task", []))
        self.assertEqual(len(doc.sections), sum(len(v.split(", ")) for v in HOOKS.values()))

    def test_cross_references_resolve(self):
        found = doctor.headings(SKILL / "SKILL.md")
        for name, text in self.texts.items():
            for heading in re.findall(r'SKILL\.md "(#+ [^"]+)"', text):
                self.assertIn(heading, found, f"{name} names {heading!r}")
            for ref in re.findall(r"\.claude/toolbox/references/([a-z-]+\.md)", text):
                self.assertTrue((TOOLBOX / "references" / ref).is_file(), f"{name} names {ref}")
            for ref in re.findall(r"`([a-z-]+\.md)`[^`]*?\b(?:read|Read|follow)", text):
                self.assertTrue((SKILL / "references" / ref).is_file(), f"{name} says to read {ref}")
        named = set(re.findall(r"(?<!toolbox/)references/([a-z-]+\.md)", self.skill))
        self.assertEqual(named, {p.name for p in (SKILL / "references").glob("*.md")})

    def test_every_script_command_exists(self):
        calls = set()
        for text in self.texts.values():
            calls |= set(re.findall(r"<bin>/([a-z_]+\.py) ([a-z][a-z-]*)", text))
            calls |= {(s, "") for s in re.findall(r"<bin>/([a-z_]+\.py)", text)}
        self.assertIn(("worktrees.py", "changes"), calls)
        for script, command in sorted(calls):
            path = TOOLBOX / "bin" / script
            self.assertTrue(path.is_file(), script)
            if command:
                proc = subprocess.run([sys.executable, str(path), command, "--help"], capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0, f"{script} {command}: {proc.stderr}")
        for script, flag in (("publish.py", "--stop-after"), ("session_usage.py", "--task")):
            args = ["run", "--help"] if script == "publish.py" else ["--help"]
            out = subprocess.run([sys.executable, str(TOOLBOX / "bin" / script), *args], capture_output=True,
                                 text=True).stdout
            self.assertIn(flag, out)

    def test_every_script_runs_by_its_path(self):
        # Skills call each script as <bin>/<name>.py, never through python3, so each needs its shebang and exec bit.
        for path in sorted((TOOLBOX / "bin").glob("*.py")):
            self.assertTrue(path.read_text().startswith("#!/usr/bin/env python3\n"), path.name)
            self.assertTrue(os.access(path, os.X_OK), f"{path.name} isn't executable")

    def test_installed_under_the_tasks_feature(self):
        features = yaml.safe_load((WORKSPACE_DOMAIN / "features.yml").read_text())
        self.assertIn(".claude/skills/work-task", features["tasks"])

    def test_the_release_wait_is_dispatched_and_described(self):
        self.assertIn("`cycle: shared-library` and no `worktrees` | `references/merge.md` | \"Wait for the Release\"",
                      self.skill)
        self.assertIn("## Wait for the Release", self.texts["merge.md"])
        self.assertIn("<bin>/watch_task.py pipeline <work id>", self.texts["merge.md"])

    def test_dispatch_covers_every_generic_stage(self):
        import records
        for stage in records.STAGES:
            self.assertIn(f"`{stage}`", self.skill, stage)


class ExtensionPointsTest(unittest.TestCase):
    """Every skill that takes extensions declares its hooks and carries the README's paragraph; no other does."""

    def test_every_extensible_skill(self):
        readme = README.read_text()
        paragraph = re.search(r"```markdown\n(\*\*Extensions\.\*\*.*?)\n```", readme, re.S).group(1)
        flat = " ".join(paragraph.split())
        extensible = []
        for skill_md in sorted(SKILLS.glob("*/SKILL.md")):
            name = skill_md.parent.name
            text = skill_md.read_text()
            points, problems = extensions.points(text)
            has_paragraph = "**Extensions.**" in text
            with self.subTest(name):
                self.assertEqual(problems, [])
                self.assertEqual(bool(points), has_paragraph, "a table needs the paragraph, and the paragraph a table")
                if not points:
                    continue
                extensible.append(name)
                self.assertIn(flat.replace("<skill>", name), " ".join(text.split()))
                if name != "work-task":  # work-task's hooks are named in its phase files (SkillTextTest)
                    for point in points:
                        self.assertRegex(text, rf"[Hh]ook `{point.id}`")
        self.assertEqual(extensible, ["intellij-open-worktree", "lock-local-services", "run-integration-tests",
                                      "run-unit-tests", "start-local-services", "stop-local-services",
                                      "verify-change", "work-task"])

    def test_no_old_format_heading_is_left(self):
        old = re.compile(r"(Replaces|Adds to) [a-z0-9-]+'s \"")
        repo = WORKSPACE_DOMAIN.parent
        for path in repo.rglob("*"):
            if ".git" in path.parts or not path.is_file() or path.suffix not in (".md", ".py", ".yml", ".tmpl"):
                continue
            if path == Path(__file__).resolve():
                continue
            with self.subTest(str(path.relative_to(repo))):
                self.assertIsNone(old.search(path.read_text(errors="replace")))


if __name__ == "__main__":
    unittest.main()
