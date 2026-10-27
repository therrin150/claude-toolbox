"""Tests for `toolbox.py doctor`, on a temporary workspace installed by a temporary copy of the installer (never this
machine's machine.yml or install state), with a fake `gh` (fake_gh_api.py).

Run from the repo root:

    python3 -m unittest discover -s workspace/.claude/toolbox/tests -p test_doctor.py
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
CONFIG = Path(".claude/toolbox.local.yml")


class DoctorTest(unittest.TestCase):
    """A toolbox (install.py copied, its workspace domain linked to this repo's) and one single-repo workspace with a
    `none` tracker, installed and clean."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)

        self.toolbox = self.tmp / "toolbox"
        self.toolbox.mkdir()
        shutil.copy(REPO / "install.py", self.toolbox / "install.py")
        (self.toolbox / "workspace").symlink_to(REPO / "workspace")
        (self.toolbox / "domains.yml").write_text(yaml.safe_dump({"domains": {
            "workspace": {"source": "workspace", "target": "per-workspace", "ignore": [".claude/toolbox/tests"]},
        }}))

        self.ws = self.tmp / "ws"
        for folder in ("vault/Tasks", "vault/Discussions"):
            (self.ws / folder).mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.ws)], check=True)
        (self.toolbox / "machine.yml").write_text(yaml.safe_dump({"domains": [], "workspaces": [str(self.ws)]}))
        self.config = {
            "version": 1,
            "workspace": {"name": "ws", "layout": "single"},
            "user": {"handle": "jdoe", "name": "Jordan"},
            "vault": {"path": "vault"},
            "tracker": {"kind": "none"},
        }
        self.write_config()
        self.install()

        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(f"#!/bin/sh\nexec {sys.executable} {TESTS / 'fake_gh_api.py'} \"$@\"\n")
        gh.chmod(0o755)
        self.env = {
            **{k: v for k, v in os.environ.items() if not k.startswith(("GH_", "TOOLBOX_"))},
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "TOOLBOX_INSTALLER": str(self.toolbox / "install.py"),
            "GH_FAKE_LOGIN": "jdoe",
        }

    def write_config(self) -> None:
        (self.ws / CONFIG).parent.mkdir(parents=True, exist_ok=True)
        (self.ws / CONFIG).write_text(yaml.safe_dump(self.config))

    def install(self) -> None:
        proc = subprocess.run([sys.executable, str(self.toolbox / "install.py"), "--apply"], capture_output=True,
                              text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def doctor(self, **env) -> tuple[int, dict, str]:
        script = self.ws / ".claude" / "toolbox" / "bin" / "toolbox.py"
        self.assertTrue(script.is_symlink())
        proc = subprocess.run([sys.executable, str(script), "doctor"], cwd=self.ws, capture_output=True, text=True,
                              env={**self.env, **env})
        out = json.loads(proc.stdout)
        return proc.returncode, {check["check"]: check for check in out["checks"]}, proc.stdout

    def test_clean_workspace(self):
        (self.toolbox / "install-plan.txt").unlink()  # setUp's --apply wrote one
        code, checks, text = self.doctor()
        self.assertEqual(code, 0, text)
        self.assertEqual({name: check["status"] for name, check in checks.items()}, {
            "config": "ok", "install": "ok", "rule": "ok", "rendered": "ok", "gh": "ok", "vault": "ok",
            "extensions": "ok"})
        self.assertEqual(checks["rendered"]["detail"], "none rendered")
        lines = text.splitlines()
        self.assertTrue(all(line.startswith('{"check": ') for line in lines[1:8]))  # one line per check
        self.assertFalse((self.toolbox / "install-plan.txt").exists())  # --json writes no plan file

    def test_stale_generated_rule(self):
        self.config["workspace"]["name"] = "renamed"
        self.write_config()
        code, checks, _ = self.doctor()
        self.assertEqual(code, 1)
        self.assertEqual(checks["rule"]["status"], "fail")
        self.assertIn("stale", checks["rule"]["detail"])
        self.assertEqual(checks["install"]["status"], "fail")
        self.assertIn("Write generated rules", checks["install"]["detail"]["pending"])
        self.install()
        code, checks, _ = self.doctor()
        self.assertEqual(code, 0)

    def test_extension_anchor_gone(self):
        doc = self.ws / ".claude" / "extensions" / "release-wait.md"
        doc.parent.mkdir(parents=True)
        doc.write_text("# Release Wait\n\n"
                       "## Adds to run-unit-tests's \"### Run\"\n\nWait for the release first.\n\n"
                       "## Replaces run-unit-tests's \"### Gone\"\n\nThis heading was renamed away.\n")
        self.config["extensions"] = {"run-unit-tests": [".claude/extensions/release-wait.md"]}
        self.write_config()
        self.install()
        code, checks, _ = self.doctor()
        self.assertEqual(code, 1)
        self.assertEqual(checks["extensions"]["status"], "fail")
        problems = checks["extensions"]["detail"]
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("line 7: run-unit-tests has no heading '### Gone'", problems[0])

    def test_extension_for_an_unlisted_skill(self):
        doc = self.ws / ".claude" / "extensions" / "x.md"
        doc.parent.mkdir(parents=True)
        doc.write_text("## Adds to run-unit-tests's \"### Run\"\n\nx\n")
        self.config["extensions"] = {"stop-local-services": [".claude/extensions/x.md"]}
        self.write_config()
        self.install()
        _, checks, _ = self.doctor()
        self.assertIn("extensions.run-unit-tests doesn't list", checks["extensions"]["detail"][0])

    def use_jira(self, server: str) -> None:
        self.config["features"] = ["jira"]
        self.config["tracker"] = {"kind": "jira", "key_prefix": "ABC", "jira": {
            "mcp_server": server, "site": "example.atlassian.net", "cloud_id": "00000000-0000-0000-0000-000000000000",
            "project": "ABC", "account": "jdoe@example.com"}}
        self.write_config()

    def test_jira_filer_is_rendered_with_the_configured_server(self):
        agent = self.ws / ".claude" / "agents" / "jira-filer.md"
        jira_tools = ["atlassianUserInfo", "getAccessibleAtlassianResources", "getJiraIssue",
                      "searchJiraIssuesUsingJql", "createJiraIssue", "editJiraIssue", "addOrEditJiraIssueComment",
                      "discover", "executeRead", "executeWrite"]
        self.assertFalse(agent.exists())  # no jira feature yet
        for server in ("atlassian", "atlassian-work"):
            self.use_jira(server)
            proc = subprocess.run([sys.executable, str(self.toolbox / "install.py")], capture_output=True, text=True)
            plan = (self.toolbox / "install-plan.txt").read_text()
            status = "new" if server == "atlassian" else "changed"
            self.assertIn(f"  {agent} ({status}, rendered from jira-filer.md.tmpl)\n", plan, proc.stdout)
            self.install()
            text = agent.read_text()
            self.assertFalse(agent.is_symlink())
            self.assertNotIn("{{", text)
            tools = next(line for line in text.splitlines() if line.startswith("tools: "))
            self.assertEqual(tools.removeprefix("tools: ").split(", "),
                             ["Read", "Bash"] + [f"mcp__{server}__{tool}" for tool in jira_tools])
            self.assertIn("project `ABC` on `example.atlassian.net`", text)
            code, checks, out = self.doctor()
            self.assertEqual(code, 0, out)
            self.assertEqual(checks["rendered"]["detail"], "1 file(s) current")

    def test_rendered_file_missing_or_out_of_date(self):
        agent = self.ws / ".claude" / "agents" / "jira-filer.md"
        self.use_jira("atlassian")
        self.install()
        self.use_jira("atlassian-work")
        code, checks, _ = self.doctor()
        self.assertEqual(code, 1)
        self.assertEqual(checks["rendered"]["status"], "fail")
        self.assertEqual(checks["rendered"]["detail"],
                         [f"{agent} is out of date with the config; sync the toolbox"])
        agent.unlink()
        _, checks, _ = self.doctor()
        self.assertEqual(checks["rendered"]["detail"], [f"{agent} doesn't exist; sync the toolbox"])
        self.install()
        _, checks, _ = self.doctor()
        self.assertEqual(checks["rendered"]["status"], "ok")

        self.config["features"] = []
        self.write_config()
        _, checks, _ = self.doctor()
        self.assertEqual(checks["rendered"]["detail"],
                         [f"{agent} is no longer wanted (its feature is off); sync the toolbox"])

    def test_rendered_file_whose_config_key_is_missing(self):
        self.config["features"] = ["jira"]
        self.write_config()
        _, checks, _ = self.doctor()
        self.assertEqual(checks["rendered"]["status"], "fail")
        self.assertIn("unknown config key 'tracker.jira.", checks["rendered"]["detail"][0])

    def test_gh_signed_in_as_someone_else(self):
        _, checks, _ = self.doctor(GH_FAKE_LOGIN="someone")
        self.assertEqual(checks["gh"]["status"], "fail")
        self.assertIn("signed in as someone", checks["gh"]["detail"])

    def test_missing_vault_folder_and_unlisted_workspace(self):
        (self.ws / "vault" / "Discussions").rmdir()
        (self.toolbox / "machine.yml").write_text(yaml.safe_dump({"domains": [], "workspaces": []}))
        _, checks, _ = self.doctor()
        self.assertEqual(checks["vault"]["status"], "fail")
        self.assertIn("discussions folder", checks["vault"]["detail"][0])
        self.assertIn("machine.yml doesn't list", checks["install"]["detail"]["problems"][0])

    def test_invalid_config_skips_the_rest(self):
        (self.ws / CONFIG).write_text("version: 1\n")
        code, checks, _ = self.doctor()
        self.assertEqual(code, 1)
        self.assertEqual(checks["config"]["status"], "fail")
        self.assertEqual({checks[name]["status"] for name in checks if name != "config"}, {"skip"})

    def test_mcp_checks_for_the_skill(self):
        self.config["tracker"] = {"kind": "jira", "jira": {
            "site": "example.atlassian.net", "cloud_id": "0000", "project": "ABC", "account": "jdoe@example.com"}}
        self.config["pr"] = {"announce": {"channel": "C00000000"}}
        self.write_config()
        script = self.ws / ".claude" / "toolbox" / "bin" / "toolbox.py"
        out = json.loads(subprocess.run([sys.executable, str(script), "doctor"], cwd=self.ws, capture_output=True,
                                        text=True, env=self.env).stdout)
        self.assertEqual(out["mcp"], [
            {"check": "jira", "server": "atlassian", "account": "jdoe@example.com", "cloud_id": "0000",
             "site": "example.atlassian.net"},
            {"check": "slack", "server": "slack", "channel": "C00000000"},
        ])


if __name__ == "__main__":
    unittest.main()
