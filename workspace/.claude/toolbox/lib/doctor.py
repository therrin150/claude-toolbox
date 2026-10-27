"""`toolbox.py doctor`: the checks a script can make of a workspace's toolbox setup.

Each check returns {"check", "status", "detail"}, status `ok`, `fail` or `skip` (it needs a check that failed):

  config      .claude/toolbox.local.yml parses and matches its version's schema
  install     the installer's preview for this workspace is clean, and machine.yml lists the workspace
  rule        .claude/rules/toolbox-config.md is what the config renders today
  rendered    every file the installer renders from a template (`.tmpl`) is there and what the config renders today
  gh          gh is signed in, as the GitHub account user.handle names
  vault       the vault, tasks and discussions folders exist
  extensions  every listed extension doc parses, and every skill heading it names is in that skill's SKILL.md

What needs an MCP tool (the Jira account and cloud id, the Slack channel) is the toolbox-doctor skill's: `mcp_checks()`
lists what it should check. The installer is the claude-toolbox repo's install.py, found from this file's resolved
location; TOOLBOX_INSTALLER names another (for tests).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import config
import extensions

ENV_INSTALLER = "TOOLBOX_INSTALLER"
FENCE = re.compile(r"^\s*(```|~~~)")


def result(check: str, status: str, detail) -> dict:
    return {"check": check, "status": status, "detail": detail}


def installer() -> Path:
    override = os.environ.get(ENV_INSTALLER)
    return Path(override) if override else Path(__file__).resolve().parents[4] / "install.py"


# ---------------------------------------------------------------------------------------------------------------------
# Checks


_previews: dict[str, dict | str] = {}


def install_preview(cfg: config.Config) -> dict | str:
    """The installer's --json preview of this workspace, or why there is none; run once per doctor run."""
    key = str(cfg.root)
    if key not in _previews:
        script = installer()
        if not script.is_file():
            _previews[key] = f"no installer at {script}"
        else:
            proc = subprocess.run([sys.executable, str(script), "--workspace", key, "--json"],
                                  capture_output=True, text=True)
            try:
                _previews[key] = json.loads(proc.stdout)
            except ValueError:
                _previews[key] = (proc.stderr or proc.stdout).strip() or f"{script} printed no JSON"
    return _previews[key]


def check_install(cfg: config.Config) -> dict:
    script = installer()
    preview = install_preview(cfg)
    if isinstance(preview, str):
        return result("install", "fail", preview)
    pending = {title: items for title, items in preview["sections"].items()
               if items and title not in ("Already in place", "Warnings")}
    problems = []
    if not preview["listed"]:
        problems.append(f"machine.yml doesn't list {cfg.root}, so the next full sync removes what's installed here")
    if pending:
        problems.append(f"preview isn't clean; run {script} --workspace {cfg.root} to see it")
    if problems:
        return result("install", "fail", {"problems": problems, "pending": pending})
    in_place = len(preview["sections"].get("Already in place", []))
    return result("install", "ok", f"{in_place} item(s) in place, nothing to change")


def check_rule(cfg: config.Config) -> dict:
    path = cfg.rule_path
    if not path.is_file():
        return result("rule", "fail", f"{path} doesn't exist; sync the toolbox")
    if path.read_text(encoding="utf-8") != config.render_rule(cfg):
        return result("rule", "fail", f"{path} is stale: the config has changed since the last sync; sync the toolbox")
    return result("rule", "ok", str(path))


RENDERED_FIXES = {
    "new": "doesn't exist",
    "changed": "is out of date with the config",
    "replaces a link": "is still a link from before it was rendered",
    "stale": "is no longer wanted (its feature is off)",
}


def check_rendered(cfg: config.Config) -> dict:
    preview = install_preview(cfg)
    if isinstance(preview, str):
        return result("rendered", "fail", preview)
    files = preview.get("rendered", [])
    problems = [f"{item['path']} {RENDERED_FIXES.get(item['status'], item['status'])}; sync the toolbox"
                for item in files if item["status"] != "ok" and item["template"]]
    # A template that can't render (an unknown key, say) has no template in its item; the installer's problem says why.
    problems += [line for line in preview["sections"].get("Problems", []) if "can't render" in line]
    if problems:
        return result("rendered", "fail", problems)
    return result("rendered", "ok", f"{len(files)} file(s) current" if files else "none rendered")


def check_gh(cfg: config.Config) -> dict:
    try:
        auth = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
    except FileNotFoundError:
        return result("gh", "fail", "gh isn't installed")
    if auth.returncode != 0:
        return result("gh", "fail", "gh isn't signed in; run `gh auth login`")
    user = subprocess.run(["gh", "api", "user", "--jq", ".login"], capture_output=True, text=True)
    login = user.stdout.strip()
    if user.returncode != 0 or not login:
        return result("gh", "fail", "gh api user failed: " + (user.stderr.strip() or "no login"))
    if login.lower() != cfg.handle.lower():
        return result("gh", "fail", f"gh is signed in as {login}, but user.handle is {cfg.handle}")
    return result("gh", "ok", f"signed in as {login}")


def check_vault(cfg: config.Config) -> dict:
    folders = {"vault": cfg.vault_dir, "tasks": cfg.tasks_dir, "discussions": cfg.discussions_dir}
    absent = [f"{name} folder {path} doesn't exist" for name, path in folders.items() if not path.is_dir()]
    if absent:
        return result("vault", "fail", absent)
    return result("vault", "ok", str(cfg.vault_dir))


def headings(path: Path) -> set[str]:
    """A markdown file's headings, as written with their #s, outside fenced code."""
    found, fenced = set(), False
    for line in path.read_text(encoding="utf-8").splitlines():
        if FENCE.match(line):
            fenced = not fenced
        elif not fenced and re.match(r"^#{1,6} ", line):
            found.add(line.rstrip())
    return found


def check_extensions(cfg: config.Config) -> dict:
    listed = cfg.data["extensions"]
    if not any(listed.values()):
        return result("extensions", "ok", "none listed")
    problems, count = [], 0
    skills: dict[str, set | None] = {}
    for skill, docs in listed.items():
        for doc in docs:
            path = cfg.root / doc
            if not path.is_file():
                problems.append(f"extensions.{skill}: {doc} doesn't exist")
                continue
            sections, parse_problems = extensions.parse(path.read_text(encoding="utf-8"))
            problems.extend(f"{doc}: {problem}" for problem in parse_problems)
            for section in sections:
                count += 1
                where = f"{doc} line {section.line}"
                if section.skill not in listed or doc not in listed[section.skill]:
                    problems.append(f"{where}: changes {section.skill}, but extensions.{section.skill} doesn't list "
                                    f"{doc}, so {section.skill} never reads it")
                if section.skill not in skills:
                    skill_md = cfg.root / ".claude" / "skills" / section.skill / "SKILL.md"
                    skills[section.skill] = headings(skill_md) if skill_md.is_file() else None
                found = skills[section.skill]
                if found is None:
                    problems.append(f"{where}: {section.skill} isn't installed in this workspace")
                elif section.heading not in found:
                    problems.append(f"{where}: {section.skill} has no heading {section.heading!r}")
    if problems:
        return result("extensions", "fail", problems)
    return result("extensions", "ok", f"{count} section(s), every heading found")


# ---------------------------------------------------------------------------------------------------------------------


def mcp_checks(cfg: config.Config) -> list[dict]:
    """What the toolbox-doctor skill checks with MCP tools; see references/identity-check.md."""
    checks = []
    jira = cfg.jira
    if jira:
        checks.append({"check": "jira", "server": jira["mcp_server"], "account": jira["account"],
                       "cloud_id": jira["cloud_id"], "site": jira["site"]})
    announce = cfg.announce
    if announce:
        checks.append({"check": "slack", "server": announce["mcp_server"], "channel": announce["channel"]})
    return checks


def run() -> tuple[list[dict], list[dict], str | None]:
    """(checks, MCP checks for the skill, the workspace root or None)."""
    names = ("install", "rule", "rendered", "gh", "vault", "extensions")
    try:
        cfg = config.load()
    except config.ConfigError as error:
        where = str(error.path) if error.path else None
        checks = [result("config", "fail", {"config": where, "errors": error.errors})]
        return checks + [result(name, "skip", "needs a valid config") for name in names], [], None
    checks = [result("config", "ok", str(cfg.path))]
    _previews.clear()
    for check in (check_install, check_rule, check_rendered, check_gh, check_vault, check_extensions):
        try:
            checks.append(check(cfg))
        except Exception as error:  # a broken check is a failed check, not a crashed doctor
            checks.append(result(check.__name__.removeprefix("check_"), "fail", f"{type(error).__name__}: {error}"))
    return checks, mcp_checks(cfg), str(cfg.root)


def render(checks: list[dict], mcp: list[dict], workspace: str | None) -> str:
    """JSON with one line per check."""
    ok = all(check["status"] == "ok" for check in checks)
    lines = [json.dumps(check) for check in checks]
    return (f'{{"ok": {json.dumps(ok)}, "workspace": {json.dumps(workspace)}, "checks": [\n'
            + ",\n".join(lines) + f'\n], "mcp": {json.dumps(mcp)}}}\n')
