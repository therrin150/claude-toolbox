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

    def write_doc(self, name: str, skill: str | None, *sections: str, type_: str | None = "extension") -> Path:
        """An extension doc: frontmatter, a title on line 6, then each `<verb> <id>` section, the first on line 8."""
        front = ([f"type: {type_}"] if type_ else []) + ([f"extends: {skill}"] if skill else [])
        while len(front) < 2:
            front.append("note: padding")  # keep the title on line 6
        lines = ["---", *front, "---", "", "# Doc", ""]
        for section in sections:
            lines += [f"## {section}", "", "Do the thing.", ""]
        path = self.ws / ".claude" / "extensions" / name
        path.write_text("\n".join(lines))
        return path

    def config_extensions(self, skill: str) -> list[str]:
        script = self.ws / ".claude" / "toolbox" / "bin" / "toolbox.py"
        proc = subprocess.run([sys.executable, str(script), "config", "extensions", skill], cwd=self.ws,
                              capture_output=True, text=True, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_readme_is_installed_and_never_a_doc(self):
        readme = self.ws / ".claude" / "extensions" / "README.md"
        self.assertTrue(readme.is_symlink())
        self.assertEqual(readme.resolve(), REPO / "workspace" / ".claude" / "extensions" / "README.md")
        self.assertIn(str(readme), (self.toolbox / "install-plan.txt").read_text())
        self.assertIn("/.claude/extensions/README.md", (self.ws / ".git" / "info" / "exclude").read_text())
        self.assertLess(len(readme.read_text().splitlines()), 100)  # a one-screen guide
        self.assertEqual(self.config_extensions("work-task"), [])
        _, checks, _ = self.doctor()
        self.assertEqual(checks["extensions"], {"check": "extensions", "status": "ok", "detail": "none"})
        rule = (self.ws / ".claude" / "rules" / "toolbox-config.md").read_text()
        self.assertIn(f"`{self.ws / '.claude/extensions'}`; its `README.md` says how to write one.", rule)

    def test_an_added_check_and_a_skipped_step(self):
        self.config["features"] = ["tasks"]
        self.write_config()
        self.install()
        scan = self.write_doc("sonar-scan.md", "work-task", "add review-checks")
        skip = self.write_doc("no-verify.md", "work-task", "skip verify")
        self.assertEqual(self.config_extensions("work-task"), [str(skip), str(scan)])
        self.assertEqual(self.config_extensions("verify-change"), [])
        code, checks, out = self.doctor()
        self.assertEqual(code, 0, out)
        self.assertEqual(checks["extensions"]["detail"], "2 doc(s), 2 section(s), every hook allowed")

        self.write_doc("sonar-scan.md", "work-task", "add review-check")
        code, checks, _ = self.doctor()
        self.assertEqual(code, 1)
        self.assertEqual(checks["extensions"]["detail"],
                         ["sonar-scan.md line 8: work-task has no hook 'review-check'; did you mean 'review-checks'?"])

        self.config["extensions"] = {"disabled": ["sonar-scan"]}  # a disabled doc is checked for form only
        self.write_config()
        self.install()
        self.assertEqual(self.config_extensions("work-task"), [str(skip)])
        code, checks, out = self.doctor()
        self.assertEqual(code, 0, out)
        self.assertEqual(checks["extensions"]["detail"], "2 doc(s), 1 disabled, 1 section(s), every hook allowed")

    def test_every_extension_failure(self):
        self.config["features"] = ["tasks"]  # work-task installed; start-local-services (services-lock) isn't
        self.config["extensions"] = {"disabled": ["ghost.md", "disabled-broken"]}
        self.write_config()
        self.install()
        self.write_doc("a-replace.md", "work-task", "replace needs-a-release")
        self.write_doc("b-replace.md", "work-task", "replace needs-a-release", "replace format")
        self.write_doc("bad-heading.md", "work-task", "Run the scan")
        self.write_doc("disabled-broken.md", "start-local-services", "nope")
        self.write_doc("no-extends.md", None, "add dispatch")
        self.write_doc("no-table.md", "git-commit", "add run")
        self.write_doc("no-type.md", "work-task", "add dispatch", type_=None)
        self.write_doc("typo.md", "work-task", "add review-check")
        self.write_doc("uninstalled.md", "start-local-services", "add preflight")
        code, checks, _ = self.doctor()
        self.assertEqual(code, 1)
        problems = checks["extensions"]["detail"]
        self.assertEqual(problems[:2], [
            "extensions.disabled: ghost.md isn't in .claude/extensions/",
            "b-replace.md line 12: work-task's hook 'format' allows add, skip, not replace"])
        self.assertTrue(problems[2].startswith("bad-heading.md: line 8: ") and "isn't `## <add|replace|skip> <hook id>`"
                        in problems[2], problems[2])
        self.assertEqual(problems[3:], [
            "disabled-broken.md: line 8: '## nope' isn't `## <add|replace|skip> <hook id>`",
            "no-extends.md: frontmatter: no extends; name the one skill this doc extends",
            "no-table.md: git-commit has no Extension Points table, so it can't be extended",
            "no-type.md: frontmatter: type must be extension; got None",
            "typo.md line 8: work-task has no hook 'review-check'; did you mean 'review-checks'?",
            "uninstalled.md: extends start-local-services, which isn't installed in this workspace (disable the doc "
            "with extensions.disabled if that's intended)",
            "work-task's hook 'needs-a-release' is replaced or skipped more than once: a-replace.md line 8 (replace), "
            "b-replace.md line 8 (replace); keep one, or disable a doc with extensions.disabled",
        ])

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
