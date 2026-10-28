"""Tests for install.py's templates: `.tmpl` files in the workspace folder, rendered into each workspace from its
config instead of linked.

Run from the repo root:

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_install import CONFIG, InstallerTest, install, write  # noqa: E402  (test_install puts the repo on the path)

AGENT = Path(".claude/agents/filer.md")
TEMPLATE = ("---\nname: filer\ntools: Read, mcp__{{ tracker.jira.mcp_server }}__getJiraIssue\n---\n\n"
            "Files in {{ tracker.jira.project }}.\n")


def jira_config(name: str, server: str, features: list) -> str:
    return yaml.safe_dump({
        "version": 1,
        "features": features,
        "workspace": {"name": name, "layout": "single"},
        "user": {"handle": "tester", "name": "Tester"},
        "vault": {"path": "vault"},
        "tracker": {"kind": "jira", "key_prefix": "ABC", "jira": {
            "mcp_server": server, "site": "example.atlassian.net", "cloud_id": "00000000-0000-0000-0000-000000000000",
            "project": "ABC", "account": "tester@example.com",
        }},
    })


class Templates(InstallerTest):
    """The base fixture's toolbox, plus a template gated on `jira`. ws1 is on server `atlassian`, ws2 (a git repo) on
    `atlassian-personal`, both with `jira`."""

    def setUp(self):
        super().setUp()
        self.source = self.repo / "workspace"
        write(self.source / ".claude" / "agents" / "filer.md.tmpl", TEMPLATE)
        write(self.source / "features.yml", yaml.safe_dump({
            "services-lock": [".claude/skills/lock-local-services"],
            "jira": [".claude/agents/filer.md.tmpl"],
        }))
        write(self.ws1 / CONFIG, jira_config("one", "atlassian", ["jira"]))
        write(self.ws2 / CONFIG, jira_config("two", "atlassian-personal", ["jira"]))

    def preview_json(self, *args) -> dict:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            install.main(["--json", *args])
        return json.loads(out.getvalue())

    def text(self, ws: Path) -> str:
        return (ws / AGENT).read_text(encoding="utf-8")

    def test_renders_each_workspaces_server(self):
        code, plan = self.run_install()
        self.assertEqual(code, 0, plan)
        written = self.section(plan, "Write generated rules")
        self.assertIn(f"{self.ws1 / AGENT} (new, rendered from filer.md.tmpl)", written)
        self.assertIn(f"{self.ws2 / AGENT} (new, rendered from filer.md.tmpl)", written)
        self.assertFalse(any("filer.md" in line for line in self.section(plan, "Create links")))
        self.assertFalse((self.ws1 / AGENT).exists())

        code, plan = self.run_install("--apply")
        self.assertEqual(code, 0, plan)
        self.assertFalse((self.ws1 / AGENT).is_symlink())
        self.assertIn("tools: Read, mcp__atlassian__getJiraIssue\n", self.text(self.ws1))
        self.assertIn("tools: Read, mcp__atlassian-personal__getJiraIssue\n", self.text(self.ws2))
        self.assertIn("Files in ABC.", self.text(self.ws1))
        self.assertFalse((self.ws1 / ".claude" / "agents" / "filer.md.tmpl").exists())
        record = self.state()["generated"][install.norm(self.ws1 / AGENT)]
        self.assertEqual(record["template"], str(self.source / ".claude" / "agents" / "filer.md.tmpl"))

        exclude = (self.ws2 / ".git" / "info" / "exclude").read_text(encoding="utf-8")
        self.assertIn("\n/.claude/agents/filer.md\n", exclude)
        self.assertIn("\n/.claude/rules/toolbox-config.md\n", exclude)
        status = subprocess.run(["git", "-C", str(self.ws2), "status", "--porcelain"], capture_output=True,
                                text=True, check=True).stdout
        self.assertEqual(status, "")

        _, plan = self.run_install()
        self.assertEqual(self.section(plan, "Write generated rules"), [])
        self.assertIn(f"{self.ws1 / AGENT} (rendered)", self.section(plan, "Already in place"))

    def test_config_change_rewrites(self):
        self.run_install("--apply")
        write(self.ws1 / CONFIG, jira_config("one", "jira-work", ["jira"]))
        _, plan = self.run_install()
        self.assertIn(f"{self.ws1 / AGENT} (changed, rendered from filer.md.tmpl)",
                      self.section(plan, "Write generated rules"))
        self.assertEqual(self.preview_json("--workspace", str(self.ws1))["rendered"], [{
            "path": str(self.ws1 / AGENT), "template": str(self.source / ".claude/agents/filer.md.tmpl"),
            "status": "changed"}])
        self.run_install("--apply")
        self.assertIn("mcp__jira-work__getJiraIssue", self.text(self.ws1))

    def test_feature_gate(self):
        write(self.ws1 / CONFIG, jira_config("one", "atlassian", []))
        self.run_install("--apply")
        self.assertFalse((self.ws1 / AGENT).exists())
        self.assertTrue((self.ws2 / AGENT).is_file())

        write(self.ws2 / CONFIG, jira_config("two", "atlassian-personal", []))
        _, plan = self.run_install()
        self.assertEqual(self.section(plan, "Remove generated rules"), [str(self.ws2 / AGENT)])
        self.assertEqual(self.preview_json("--workspace", str(self.ws2))["rendered"][0]["status"], "stale")
        self.run_install("--apply")
        self.assertFalse((self.ws2 / AGENT).exists())
        self.assertTrue((self.ws2 / ".claude" / "rules" / "toolbox-config.md").is_file())
        self.assertNotIn("filer.md", (self.ws2 / ".git" / "info" / "exclude").read_text(encoding="utf-8"))

    def test_stale_when_the_template_goes_and_teardown(self):
        self.run_install("--apply")
        (self.source / ".claude" / "agents" / "filer.md.tmpl").unlink()
        _, plan = self.run_install()
        self.assertEqual(sorted(self.section(plan, "Remove generated rules")),
                         sorted([str(self.ws1 / AGENT), str(self.ws2 / AGENT)]))
        self.run_install("--apply")
        self.assertFalse((self.ws1 / AGENT).exists())

        write(self.source / ".claude" / "agents" / "filer.md.tmpl", TEMPLATE)
        self.run_install("--apply")
        self.assertTrue((self.ws1 / AGENT).is_file())
        self.run_install("--teardown", "--apply")
        self.assertFalse((self.ws1 / AGENT).exists())
        self.assertFalse((self.ws2 / AGENT).exists())
        self.assertEqual(self.state()["generated"], {})

    def test_unknown_placeholder_fails_the_preview_and_keeps_the_file(self):
        self.run_install("--apply")
        before = self.text(self.ws1)
        bad = TEMPLATE + "{{ tracker.jira.nope }} {{ bad key }}\n"
        write(self.source / ".claude" / "agents" / "filer.md.tmpl", bad)
        code, plan = self.run_install("--apply")
        self.assertEqual(code, 1)
        problems = self.section(plan, "Problems")
        self.assertTrue(any("can't render" in line and "unknown config key 'tracker.jira.nope'" in line
                            and "isn't a placeholder" in line for line in problems), problems)
        self.assertEqual(self.section(plan, "Remove generated rules"), [])
        self.assertEqual(self.text(self.ws1), before)
        self.assertIn(install.norm(self.ws1 / AGENT), self.state()["generated"])
        self.assertIn("\n/.claude/agents/filer.md\n", (self.ws2 / ".git" / "info" / "exclude").read_text())

    def test_a_feature_without_its_config_section_is_a_problem(self):
        write(self.ws1 / CONFIG, yaml.safe_dump({
            **yaml.safe_load(jira_config("one", "atlassian", ["jira"])), "tracker": {"kind": "none"}}))
        code, plan = self.run_install()
        self.assertEqual(code, 1)
        self.assertTrue(any(f"workspace {self.ws1}: can't render" in line
                            and "unknown config key 'tracker.jira.mcp_server'" in line
                            for line in self.section(plan, "Problems")))

    def test_a_previously_linked_file_is_replaced(self):
        # Before templates, the agent was linked; that link goes and the rendered file takes its place.
        plain = write(self.source / ".claude" / "agents" / "filer.md", "---\nname: filer\n---\n")
        (self.source / ".claude" / "agents" / "filer.md.tmpl").rename(self.tmp / "filer.md.tmpl")
        self.run_install("--apply")
        self.assertTrue((self.ws1 / AGENT).is_symlink())

        plain.unlink()
        (self.tmp / "filer.md.tmpl").rename(self.source / ".claude" / "agents" / "filer.md.tmpl")
        _, plan = self.run_install()
        self.assertIn(str(self.ws1 / AGENT), self.section(plan, "Remove stale links"))
        self.assertIn(f"{self.ws1 / AGENT} (replaces a link, rendered from filer.md.tmpl)",
                      self.section(plan, "Write generated rules"))
        code, plan = self.run_install("--apply")
        self.assertEqual(code, 0, plan)
        self.assertFalse((self.ws1 / AGENT).is_symlink())
        self.assertIn("mcp__atlassian__getJiraIssue", self.text(self.ws1))
        self.assertNotIn(install.norm(self.ws1 / AGENT), self.state()["links"])

    def test_a_file_the_installer_didnt_write_is_a_conflict_unless_identical(self):
        write(self.ws1 / AGENT, "# Mine\n")
        code, plan = self.run_install("--apply")
        self.assertEqual(code, 1)
        self.assertIn(f"[not-generated] {self.ws1 / AGENT}: a file the installer didn't generate is where a "
                      "generated file goes", self.section(plan, "Conflicts, left alone"))
        self.assertEqual(self.text(self.ws1), "# Mine\n")

        rendered = self.text(self.ws2)
        write(self.ws1 / AGENT, rendered.replace("atlassian-personal", "atlassian"))
        code, plan = self.run_install("--apply")
        self.assertEqual(code, 0, plan)
        self.assertIn(install.norm(self.ws1 / AGENT), self.state()["generated"])

    def test_a_link_conflict_is_reported_in_json(self):
        os.makedirs(self.ws1 / AGENT.parent, exist_ok=True)
        os.symlink(self.tmp, self.ws1 / AGENT)
        statuses = {item["path"]: item["status"] for item in self.preview_json()["rendered"]}
        self.assertTrue(statuses[str(self.ws1 / AGENT)].startswith("conflict: "))
        self.assertEqual(statuses[str(self.ws2 / AGENT)], "new")


if __name__ == "__main__":
    unittest.main()
