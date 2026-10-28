"""Tests for the global setup-workspace skill's scripts, detect.py and draft.py, against temporary workspaces built from
local bare repos, with a fake `gh` (fake_gh_api.py). Nothing touches a network or this machine's machine.yml.

Run from the repo root:

    python3 -m unittest discover -s workspace/.claude/toolbox/tests -p test_setup_workspace.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parents[3]
SCRIPTS = REPO / "global" / "skills" / "setup-workspace" / "scripts"
sys.path.insert(0, str(TESTS.parent / "lib"))

import config  # noqa: E402

PROTECTED = {"required_pull_request_reviews": {"required_approving_review_count": 1},
             "required_status_checks": {"contexts": ["build"]}}


class Fixture:
    """Temporary origins, workspaces and a fake gh on PATH, with git's global and system config shut out."""

    def __init__(self, test: unittest.TestCase):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        test.addCleanup(shutil.rmtree, self.tmp, True)
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(f"#!/bin/sh\nexec {sys.executable} {TESTS / 'fake_gh_api.py'} \"$@\"\n")
        gh.chmod(0o755)
        self.env = {
            **{k: v for k, v in os.environ.items() if not k.startswith(("GH_", "GIT_", "TOOLBOX_"))},
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
        }

    def git(self, cwd: Path, *args, email="jdoe@example.com") -> None:
        subprocess.run(["git", "-c", f"user.email={email}", "-c", "user.name=Test", *args], cwd=cwd, check=True,
                       capture_output=True, env=self.env)

    def origin(self, name: str, default: str, subjects: list[str], branches: dict, files: dict) -> Path:
        """A bare repo with commits on the default branch and the given branches ({name: author email})."""
        bare = self.tmp / "origins" / f"{name}.git"
        seed = self.tmp / "seeds" / name
        bare.parent.mkdir(exist_ok=True)
        seed.mkdir(parents=True)
        self.git(self.tmp, "init", "-q", "--bare", "-b", default, str(bare))
        self.git(seed, "init", "-q", "-b", default)
        for path, text in files.items():
            (seed / path).parent.mkdir(parents=True, exist_ok=True)
            (seed / path).write_text(text)
        self.git(seed, "add", "-A")
        for subject in subjects:
            self.git(seed, "commit", "-q", "--allow-empty", "-m", subject)
        self.git(seed, "remote", "add", "origin", str(bare))
        self.git(seed, "push", "-q", "origin", default)
        for branch, email in branches.items():
            self.git(seed, "checkout", "-q", "-b", branch, default)
            self.git(seed, "commit", "-q", "--allow-empty", "-m", f"work on {branch}", email=email)
            self.git(seed, "push", "-q", "origin", branch)
        return bare

    def clone(self, bare: Path, dest: Path, url: str) -> Path:
        self.git(self.tmp, "clone", "-q", str(bare), str(dest))
        self.git(dest, "remote", "set-url", "origin", url)
        self.git(dest, "config", "user.email", "jdoe@example.com")
        self.git(dest, "config", "user.name", "Jordan Doe")
        return dest

    def run(self, script: str, *args, gh_login=None, protection=None) -> tuple[int, dict]:
        env = dict(self.env)
        if gh_login:
            env["GH_FAKE_LOGIN"] = gh_login
        env["GH_FAKE_PROTECTION"] = json.dumps(protection or {})
        proc = subprocess.run([sys.executable, str(SCRIPTS / script), *args], capture_output=True, text=True, env=env)
        assert proc.stdout, proc.stderr
        return proc.returncode, json.loads(proc.stdout)


def nested(fx: Fixture) -> Path:
    """`fleet`: two Maven repos in example-org, an Atlassian server in .mcp.json, FLT keys, no vault inside."""
    ws = fx.tmp / "fleet"
    ws.mkdir()
    for name in ("alpha", "beta"):
        bare = fx.origin(name, "main", ["FLT-10 Start", "FLT-12 Add thing", "Bump to UTF-8 everywhere"],
                         {"jdoe/FLT-12-add-thing": "jdoe@example.com", "jdoe/FLT-15-fix-it": "jdoe@example.com",
                          "other/ABC-1-theirs": "other@example.com"},
                         {"pom.xml": "<project/>\n"})
        fx.clone(bare, ws / name, f"git@github.com:example-org/{name}.git")
    server = {"type": "http", "url": "https://mcp.atlassian.com/v1/sse"}
    (ws / ".mcp.json").write_text(json.dumps({"mcpServers": {"atlassian": server}}))
    return ws


def single(fx: Fixture) -> Path:
    """`pantry`: one repo on master with a Gradle app (and compose file), an npm web app and a vault; PT keys."""
    bare = fx.origin("pantry", "master", ["PT-1 Start", "PT-3 Add shelf"],
                     {"jdoe/PT-3-add-shelf": "jdoe@example.com"},
                     {"pantry-app/build.gradle": "", "pantry-app/gradlew": "", "pantry-app/docker-compose.yml": "",
                      "pantry-web/package.json": json.dumps({"scripts": {"test": "vitest"}}),
                      "vault/.obsidian/app.json": "{}"})
    return fx.clone(bare, fx.tmp / "pantry", "https://github.com/example-org/pantry.git")


def value(report: dict, key: str):
    return report["values"][key]["value"]


class DetectTest(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture(self)

    def test_nested(self):
        ws = nested(self.fx)
        protection = {f"example-org/{name}/main": PROTECTED for name in ("alpha", "beta")}
        code, report = self.fx.run("detect.py", str(ws), gh_login="jdoe", protection=protection)
        self.assertEqual(code, 0)
        self.assertEqual(value(report, "workspace.layout"), "nested")
        self.assertEqual(value(report, "workspace.default_branch"), "main")
        self.assertEqual(value(report, "workspace.areas"), {"*": {"unit": "mvn -q verify"}})
        self.assertEqual(value(report, "git.org"), "example-org")
        self.assertEqual((value(report, "pr.required"), value(report, "pr.review_required"),
                          value(report, "pr.wait_for_ci")), (True, True, True))
        self.assertEqual(report["values"]["pr.required"]["origin"], "detected")
        self.assertEqual(value(report, "user.handle"), "jdoe")
        self.assertEqual(value(report, "user.name"), "Jordan")
        self.assertEqual(value(report, "tracker.kind"), "jira")
        self.assertEqual(value(report, "tracker.jira.project"), "FLT")
        self.assertEqual(value(report, "tracker.jira.mcp_server"), "atlassian")
        self.assertIsNone(value(report, "vault.path"))
        self.assertEqual(sorted(report["ask"]), ["pr.announce.channel", "vault.path"])
        self.assertIn("tracker.kind", report["confirm"])
        self.assertIn("tracker.jira.cloud_id", report["mcp"])
        self.assertIn("jira", value(report, "features"))
        self.assertIn("pr", value(report, "features"))
        self.assertTrue(all(item["origin"] in ("detected", "default") for item in report["values"].values()))
        self.assertIsNone(report["existing_config"])

    def test_single_without_gh(self):
        ws = single(self.fx)
        code, report = self.fx.run("detect.py", str(ws))
        self.assertEqual(code, 0)
        self.assertEqual(value(report, "workspace.layout"), "single")
        self.assertEqual(value(report, "workspace.default_branch"), "master")
        self.assertEqual(value(report, "workspace.areas"), {
            "pantry-app": {"unit": "./gradlew test", "start": "docker compose up -d", "stop": "docker compose down"},
            "pantry-web": {"unit": "npm test"},
        })
        self.assertEqual(value(report, "vault.path"), "vault")
        self.assertEqual(value(report, "user.handle"), "jdoe")  # from branches, without gh
        self.assertEqual(value(report, "tracker.kind"), "vault")
        self.assertEqual(value(report, "tracker.key_prefix"), "PT")
        self.assertFalse(report["gh"]["signed_in"])
        for key in ("pr.required", "pr.review_required", "pr.wait_for_ci"):
            self.assertEqual(report["values"][key]["ask"], "gh isn't signed in")
        self.assertIn("services-lock", value(report, "features"))

    def test_unprotected_branch_is_asked(self):
        ws = single(self.fx)
        _, report = self.fx.run("detect.py", str(ws), gh_login="jdoe")
        self.assertIn("no branch protection on master", report["values"]["pr.required"]["ask"])

    def test_no_shared_libraries_asks_nothing(self):
        _, report = self.fx.run("detect.py", str(nested(self.fx)))
        self.assertFalse([key for key in report["values"] if key.startswith("shared_libraries")])
        _, report = self.fx.run("detect.py", str(single(self.fx)))
        self.assertFalse([key for key in report["values"] if key.startswith("shared_libraries")])

    def test_a_repo_others_build_against_is_a_shared_library(self):
        ws = self.fx.tmp / "libs"
        ws.mkdir()
        poms = {
            "common": "<project xmlns='http://maven.apache.org/POM/4.0.0'><artifactId>common-core</artifactId>"
                      "</project>",
            "orders": "<project xmlns='http://maven.apache.org/POM/4.0.0'><artifactId>orders</artifactId>"
                      "<dependencies><dependency><artifactId>common-core</artifactId></dependency></dependencies>"
                      "</project>",
        }
        files = {"common": {"pom.xml": poms["common"]}, "orders": {"pom.xml": poms["orders"]},
                 "web": {"package.json": json.dumps({"name": "web", "dependencies": {"left-pad": "1"}})},
                 "docs": {"README.md": "docs\n"}}
        for name, content in files.items():
            bare = self.fx.origin(name, "main", ["Start"], {}, content)
            self.fx.clone(bare, ws / name, f"git@github.com:example-org/{name}.git")
        _, report = self.fx.run("detect.py", str(ws))
        names = report["values"]["shared_libraries.names"]
        self.assertEqual((names["value"], names["origin"], names.get("confirm")), (["common"], "detected", True))
        self.assertIn("orders build against common", names["from"])
        self.assertIsNone(value(report, "shared_libraries.update"))
        self.assertIn("shared_libraries.update", report["ask"])

        # With nothing found among the readable repos, but one detection can't read, it's asked.
        (ws / "orders" / "pom.xml").write_text("<project><artifactId>orders</artifactId></project>")
        _, report = self.fx.run("detect.py", str(ws))
        self.assertEqual(value(report, "shared_libraries.names"), [])
        self.assertIn("no Maven, Gradle or npm build file in docs", report["values"]["shared_libraries.names"]["from"])
        self.assertIn("shared_libraries.names", report["ask"])

    def test_shared_libraries_draft_validates(self):
        ws = self.fx.tmp / "libs"
        ws.mkdir()
        for name, pom in (("common", "<project><artifactId>common</artifactId></project>"),
                          ("orders", "<project><parent><artifactId>common</artifactId></parent></project>")):
            self.fx.clone(self.fx.origin(name, "main", ["Start"], {}, {"pom.xml": pom}), ws / name,
                          f"git@github.com:example-org/{name}.git")
        _, report = self.fx.run("detect.py", str(ws))
        path = self.fx.tmp / "detect.json"
        path.write_text(json.dumps(report))
        code, draft = self.fx.run("draft.py", "render", str(path), "--set", "shared_libraries.update={skill: bump-deps}")
        self.assertEqual([e for e in draft["errors"] if e.startswith("shared_libraries")], [])
        self.assertFalse([key for key in draft["unanswered"] + draft["missing"] if key.startswith("shared_libraries")])
        self.assertIn("names: [common]  # detected", draft["yaml"])
        self.assertIn("update: {skill: bump-deps}  # asked", draft["yaml"])


class DraftTest(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture(self)

    def detect(self, ws: Path, **kwargs) -> Path:
        _, report = self.fx.run("detect.py", str(ws), **kwargs)
        path = self.fx.tmp / "detect.json"
        path.write_text(json.dumps(report))
        return path

    def test_nested_leaves_only_the_asked_values(self):
        """The prompt's done-when: from a copy of the nested fixture, with the MCP values the skill reads, the draft
        lacks only what the plan says to ask; once answered it validates and is written."""
        ws = nested(self.fx)
        protection = {f"example-org/{name}/main": PROTECTED for name in ("alpha", "beta")}
        report = self.detect(ws, gh_login="jdoe", protection=protection)
        mcp = ["--mcp", "tracker.jira.site=example.atlassian.net",
               "--mcp", "tracker.jira.cloud_id=00000000-0000-0000-0000-000000000000",
               "--mcp", "tracker.jira.account=jdoe@example.com",
               "--mcp", "tracker.jira.fields={acceptance_criteria: customfield_10001, sprint: customfield_10002}"]

        code, draft = self.fx.run("draft.py", "render", str(report), *mcp, "--write")
        self.assertEqual(code, 1)
        self.assertEqual(sorted(draft["missing"]), ["pr.announce.channel", "vault.path"])
        self.assertEqual(sorted(draft["unanswered"]), ["pr.announce.channel", "vault.path"])
        self.assertIsNone(draft["written"])
        self.assertIn("# asked, not answered yet", draft["yaml"])
        self.assertNotIn("key_prefix", draft["yaml"])  # jira: the project gives the prefix

        code, draft = self.fx.run("draft.py", "render", str(report), *mcp, "--set", "vault.path=~/Notes/Work",
                                  "--set", "pr.announce.channel=C00000000", "--write")
        self.assertEqual(code, 0, draft)
        self.assertTrue(draft["valid"])
        self.assertEqual(draft["written"], str(ws / config.CONFIG_PATH))
        cfg = config.load_workspace(ws)
        self.assertEqual(cfg.tracker, "jira")
        self.assertEqual(cfg.jira["project"], "FLT")
        self.assertEqual(cfg.announce["channel"], "C00000000")
        self.assertEqual(cfg.areas(), ["alpha", "beta"])
        self.assertEqual(cfg.command("alpha", "unit"), "mvn -q verify")
        text = (ws / config.CONFIG_PATH).read_text()
        self.assertIn("default_branch: main  # detected: origin/HEAD", text)
        self.assertIn("path: ~/Notes/Work  # asked", text)
        self.assertIn("site: example.atlassian.net  # detected: MCP", text)

        code, draft = self.fx.run("draft.py", "render", str(report), *mcp, "--set", "vault.path=~/Notes/Work",
                                  "--set", "pr.announce.channel=C00000000", "--write")
        self.assertEqual(code, 1)
        self.assertIn("--force", draft["error"])

    def test_single_with_no_announcement(self):
        ws = single(self.fx)
        report = self.detect(ws)
        answers = ["--set", "pr.required=false", "--set", "pr.review_required=false", "--set", "pr.wait_for_ci=true",
                   "--drop", "pr.announce"]
        code, draft = self.fx.run("draft.py", "render", str(report), *answers, "--write")
        self.assertEqual(code, 0, draft)
        cfg = config.load_workspace(ws)
        self.assertEqual(cfg.tracker, "vault")
        self.assertEqual(cfg.key_prefix, "PT")
        self.assertIsNone(cfg.announce)
        self.assertFalse(cfg.pr["required"])
        self.assertNotIn("tracker.jira", json.dumps(draft["unanswered"] + draft["missing"]))
        self.assertNotIn("jira:", (ws / config.CONFIG_PATH).read_text())

    def test_add_workspace_keeps_the_file(self):
        machine = self.fx.tmp / "machine.yml"
        machine.write_text("# mine\ntoolboxes: []\nworkspaces: []  # where it goes\n")
        env_backup = self.fx.env
        self.fx.env = {**env_backup, "TOOLBOX_MACHINE": str(machine)}
        ws = self.fx.tmp / "one"
        ws.mkdir()
        _, out = self.fx.run("draft.py", "add-workspace", str(ws))
        self.assertTrue(out["added"])
        _, out = self.fx.run("draft.py", "add-workspace", str(ws))
        self.assertTrue(out["already"])
        other = self.fx.tmp / "two"
        other.mkdir()
        self.fx.run("draft.py", "add-workspace", str(other))
        text = machine.read_text()
        self.assertTrue(text.startswith("# mine\n"))
        self.assertEqual(yaml.safe_load(text)["workspaces"], [str(ws), str(other)])


if __name__ == "__main__":
    unittest.main()
