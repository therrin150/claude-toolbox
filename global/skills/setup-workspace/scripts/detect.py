#!/usr/bin/env python3
"""Detect what a workspace's toolbox config can be read from the workspace itself, for /setup-workspace.

Usage:
  detect.py [<workspace>] [--max-repos N]

Prints JSON. `values` maps each dotted config key to {"value", "origin", "from"}, where origin is `detected` (read from
the workspace, git or gh) or `default` (nothing to read, or a default from the layout and tracker). An entry may also
carry:

  ask      why the skill must ask Allen (nothing found, or the table in the plan says always ask); value is a
           suggestion or null
  confirm  the value is a suggestion the skill confirms with Allen before writing
  mcp      the MCP tools the skill reads it with; value is null until it does

Beside `values`: `ask`, `confirm` and `mcp` list those keys; `known_features` the features features.yml gates on;
`repos` has what was read per repo; `gh` whether gh is signed in and as whom; `existing_config` the config file when one
is already there; `toolbox` the paths the skill uses (toolbox.py, install.py, machine.yml, draft.py) and whether
machine.yml lists the workspace.

It runs before anything is installed in the workspace, so it reads only git, gh and files. It changes nothing.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import xml.etree.ElementTree as ElementTree
from collections import Counter
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[4]
CONFIG_FILE = Path(".claude/toolbox.local.yml")
FEATURES = REPO / "workspace" / "features.yml"

BUILD_FILES = ("pom.xml", "build.gradle", "build.gradle.kts", "package.json", "docker-compose.yml", "compose.yml")
SKIP_DIRS = {"node_modules", "target", "build", "dist", "vendor"}
KEY = re.compile(r"\b([A-Z][A-Z0-9]{1,9})-\d+\b")
BRANCH_KEY = re.compile(r"^[^/]+/([A-Za-z][A-Za-z0-9]{1,9})-\d+(?:\b|-)")
# Prefixes that look like keys in commit subjects but are standards and versions.
NOT_KEYS = {"UTF", "SHA", "ISO", "RFC", "CVE", "HTTP", "TLS", "SSL", "JDK", "JSR", "MD", "AES", "RSA", "GPL", "UUID"}
DEPENDENCY_FILES = ("pom.xml", "build.gradle", "build.gradle.kts", "package.json")  # what shared-library detection reads
GRADLE_COORDINATE = re.compile(r"['\"][\w.-]+:([\w.-]+):[^'\"]*['\"]")
GRADLE_ROOT_NAME = re.compile(r"rootProject\.name\s*=\s*['\"]([^'\"]+)['\"]")
GITHUB_URL = re.compile(r"^(?:\w+://)?(?:[^@/]+@)?(?P<host>[^:/]+)[:/](?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$")


def run(*args, cwd: Path | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(list(args), cwd=cwd, capture_output=True, text=True)
    except FileNotFoundError as error:
        return subprocess.CompletedProcess(list(args), 127, "", str(error))


def git(cwd: Path, *args) -> str | None:
    proc = run("git", "-C", str(cwd), *args)
    return proc.stdout.strip() if proc.returncode == 0 else None


def entry(value, origin: str, source: str, **extra) -> dict:
    return {"value": value, "origin": origin, "from": source, **extra}


def tally(counts: dict) -> str:
    return ", ".join(f"{item} x{count}" for item, count in counts.items())


def most_common(items) -> tuple:
    """(the most common item, its count, every count) or (None, 0, {})."""
    counts = Counter(item for item in items if item)
    if not counts:
        return None, 0, {}
    item, count = counts.most_common(1)[0]
    return item, count, dict(counts)


# ---------------------------------------------------------------------------------------------------------------------
# Layout and repos


def find_repos(root: Path) -> tuple[str, list[Path], str]:
    """(layout, repos, how it was decided)."""
    top = git(root, "rev-parse", "--show-toplevel")
    if top and Path(top).resolve() == root:
        return "single", [root], "git rev-parse at the root"
    children = sorted(child for child in root.iterdir()
                      if child.is_dir() and not child.name.startswith(".") and (child / ".git").exists())
    if children:
        return "nested", children, f".git in {len(children)} folder(s) one level down"
    return "single", [], "no git repo at the root or one level down"


def build_files(folder: Path) -> list[str]:
    return [name for name in BUILD_FILES if (folder / name).is_file()]


def commands(folder: Path, files: list[str]) -> dict:
    """Suggested area commands for a folder's build files. Only the obvious ones; the skill confirms them."""
    found = {}
    if "pom.xml" in files:
        found["unit"] = "mvn -q verify" if not (folder / "mvnw").is_file() else "./mvnw -q verify"
    elif {"build.gradle", "build.gradle.kts"} & set(files):
        found["unit"] = "./gradlew test" if (folder / "gradlew").is_file() else "gradle test"
    elif "package.json" in files:
        try:
            scripts = json.loads((folder / "package.json").read_text(encoding="utf-8")).get("scripts") or {}
        except (OSError, ValueError, AttributeError):
            scripts = {}
        if "test" in scripts:
            found["unit"] = "npm test"
        if "format" in scripts:
            found["format"] = "npm run format"
    if {"docker-compose.yml", "compose.yml"} & set(files):
        found["start"] = "docker compose up -d"
        found["stop"] = "docker compose down"
    return found


def _pom_parts(folder: Path) -> tuple[set[str], set[str]]:
    """(artifactIds a folder's poms publish, artifactIds they depend on or inherit from), its own and its modules'."""
    own, uses = set(), set()
    for pom in [folder / "pom.xml", *sorted(folder.glob("*/pom.xml"))]:
        try:
            root = ElementTree.parse(pom).getroot()
        except (OSError, ElementTree.ParseError):
            continue
        for element in root.iter():
            element.tag = element.tag.rsplit("}", 1)[-1]  # drop the Maven namespace
        artifact = root.findtext("artifactId")
        if artifact:
            own.add(artifact.strip())
        for path in ("parent/artifactId", ".//dependency/artifactId"):
            uses.update(found.text.strip() for found in root.findall(path) if found.text)
    return own, uses


def library_parts(folder: Path) -> tuple[set[str], set[str]] | None:
    """(names a folder's build publishes, names of what it builds against), from Maven, Gradle and npm build files;
    None when it has none of them, so detection can't tell."""
    files = [name for name in DEPENDENCY_FILES if (folder / name).is_file()]
    if not files:
        return None
    own, uses = _pom_parts(folder) if "pom.xml" in files else (set(), set())
    for gradle in [name for name in files if name.startswith("build.gradle")]:
        own.add(folder.name)
        for settings in ("settings.gradle", "settings.gradle.kts"):
            if (folder / settings).is_file():
                own.update(GRADLE_ROOT_NAME.findall((folder / settings).read_text(encoding="utf-8", errors="replace")))
        uses.update(GRADLE_COORDINATE.findall((folder / gradle).read_text(encoding="utf-8", errors="replace")))
    if "package.json" in files:
        try:
            package = json.loads((folder / "package.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            package = {}
        if isinstance(package, dict):
            if isinstance(package.get("name"), str):
                own.add(package["name"])
            for kind in ("dependencies", "devDependencies", "peerDependencies"):
                uses.update(package.get(kind) or {})
    return own, uses - own


def builds_together(root: Path) -> bool:
    """A single repo whose root build aggregates its folders (Maven modules, npm workspaces, Gradle settings): its
    areas build against each other's source, never a release."""
    pom = root / "pom.xml"
    if pom.is_file() and "<modules>" in pom.read_text(encoding="utf-8", errors="replace"):
        return True
    package = root / "package.json"
    if package.is_file() and '"workspaces"' in package.read_text(encoding="utf-8", errors="replace"):
        return True
    return (root / "settings.gradle").is_file() or (root / "settings.gradle.kts").is_file()


def shared_libraries(units: dict[str, Path]) -> dict[str, dict]:
    """detect()'s shared_libraries entries for a workspace's repos or areas ({name: folder}): the ones another builds
    against, by build file. Asked when some can't be read; nothing with fewer than two, or none found among readable."""
    if len(units) < 2:
        return {}
    parts = {name: library_parts(folder) for name, folder in units.items()}
    consumers = {name: sorted(other for other, theirs in parts.items() if other != name and theirs and ours
                              and ours[0] & theirs[1])
                 for name, ours in parts.items()}
    found = {name: users for name, users in consumers.items() if users}
    if found:
        how = "; ".join(f"{', '.join(users)} build against {name}" for name, users in sorted(found.items()))
        return {
            "shared_libraries.names": entry(sorted(found), "detected", f"build files: {how}", confirm=True),
            "shared_libraries.update": entry(None, "default", "asked", ask=(
                f"how a consumer checkout picks up a new release of {', '.join(sorted(found))}: a command run in it "
                "({libraries} is the released names), or a skill, as {command: ...} or {skill: ...}")),
        }
    unread = sorted(name for name, part in parts.items() if part is None)
    if unread:
        return {"shared_libraries.names": entry([], "default", "no Maven, Gradle or npm build file in "
                                                + ", ".join(unread), ask=(
            f"whether any of {', '.join(sorted(units))} is a library the others build against as a released version "
            "(then also how a consumer picks up a release)"))}
    return {}


def origin_url(repo: Path) -> dict | None:
    url = git(repo, "remote", "get-url", "origin")
    if not url:
        return None
    match = GITHUB_URL.match(url)
    return {"url": url, **(match.groupdict() if match else {"host": None, "owner": None, "repo": None})}


def default_branch(repo: Path) -> str | None:
    ref = git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    return ref.split("/", 1)[1] if ref and "/" in ref else None


def remote_branches(repo: Path) -> list[tuple[str, str]]:
    """(branch name without origin/, tip author email) for every origin branch."""
    out = git(repo, "for-each-ref", "--format=%(refname:short)%09%(authoremail)", "refs/remotes/origin")
    rows = []
    for line in (out or "").splitlines():
        name, _, email = line.partition("\t")
        if name in ("origin", "origin/HEAD") or not name.startswith("origin/"):
            continue
        rows.append((name.removeprefix("origin/"), email.strip("<>").lower()))
    return rows


def commit_subjects(repo: Path, limit: int = 200) -> list[str]:
    out = git(repo, "log", "--all", f"-n{limit}", "--format=%s")
    return (out or "").splitlines()


# ---------------------------------------------------------------------------------------------------------------------
# gh


def gh_status() -> dict:
    auth = run("gh", "auth", "status")
    if auth.returncode != 0:
        reason = "gh isn't installed" if auth.returncode == 127 else "gh isn't signed in"
        return {"signed_in": False, "login": None, "reason": reason}
    user = run("gh", "api", "user", "--jq", ".login")
    login = user.stdout.strip() if user.returncode == 0 else None
    return {"signed_in": True, "login": login or None}


def protection(owner: str, name: str, branch: str) -> dict:
    """{"protected": bool, "required", "review_required", "wait_for_ci", "from"}, or {"error": ...}."""
    proc = run("gh", "api", f"repos/{owner}/{name}/branches/{branch}/protection")
    if proc.returncode == 0:
        data = json.loads(proc.stdout or "{}")
        reviews = data.get("required_pull_request_reviews")
        checks = data.get("required_status_checks") or {}
        return {
            "protected": True,
            "required": reviews is not None,
            "review_required": bool(reviews and reviews.get("required_approving_review_count", 0) >= 1),
            "wait_for_ci": bool(checks.get("contexts") or checks.get("checks")),
            "from": "branch protection",
        }
    if "404" not in proc.stderr and "Not Found" not in proc.stderr and "not protected" not in proc.stderr:
        return {"error": (proc.stderr.strip().splitlines() or ["gh api failed"])[-1]}
    # No classic protection: a ruleset may still cover the branch.
    rules = run("gh", "api", f"repos/{owner}/{name}/rules/branches/{branch}")
    if rules.returncode != 0:
        return {"protected": False, "from": "branch protection"}
    by_type = {rule.get("type"): rule.get("parameters") or {} for rule in json.loads(rules.stdout or "[]")}
    if "pull_request" not in by_type and "required_status_checks" not in by_type:
        return {"protected": False, "from": "branch protection and rulesets"}
    return {
        "protected": True,
        "required": "pull_request" in by_type,
        "review_required": by_type.get("pull_request", {}).get("required_approving_review_count", 0) >= 1,
        "wait_for_ci": bool(by_type.get("required_status_checks", {}).get("required_status_checks")),
        "from": "rulesets",
    }


# ---------------------------------------------------------------------------------------------------------------------
# Files


def find_vaults(root: Path) -> list[str]:
    """Folders holding `.obsidian/`, at the root or up to two levels down, relative to the root."""
    found = []

    def visit(folder: Path, depth: int) -> None:
        if (folder / ".obsidian").is_dir():
            found.append(folder.relative_to(root).as_posix() or ".")
            return
        if depth == 2:
            return
        for child in sorted(folder.iterdir()):
            if child.is_dir() and not child.is_symlink() and not child.name.startswith(".") \
                    and child.name not in SKIP_DIRS:
                visit(child, depth + 1)

    visit(root, 0)
    return found


def mcp_servers(root: Path) -> dict:
    """{"atlassian": name or None, "slack": name or None, "all": [names]} from the root's .mcp.json."""
    file = root / ".mcp.json"
    try:
        servers = (json.loads(file.read_text(encoding="utf-8")).get("mcpServers") or {}) if file.is_file() else {}
    except (OSError, ValueError, AttributeError):
        servers = {}

    def pick(*words):
        for name, spec in servers.items():
            text = (name + " " + json.dumps(spec)).lower()
            if any(word in text for word in words):
                return name
        return None

    return {"atlassian": pick("atlassian", "jira"), "slack": pick("slack"), "all": sorted(servers)}


def known_features() -> list[str]:
    try:
        return sorted((yaml.safe_load(FEATURES.read_text(encoding="utf-8")) or {}).keys())
    except OSError:
        return []


def machine_listed(root: Path) -> bool:
    machine = REPO / "machine.yml"
    if not machine.is_file():
        return False
    listed = (yaml.safe_load(machine.read_text(encoding="utf-8")) or {}).get("workspaces") or []
    return any(Path(str(item)).expanduser().resolve() == root for item in listed)


# ---------------------------------------------------------------------------------------------------------------------


def detect(root: Path, max_repos: int) -> dict:
    values: dict[str, dict] = {}
    layout, repos, layout_from = find_repos(root)
    values["workspace.name"] = entry(root.name.lstrip("_").lower() or root.name, "detected", "the folder's name")
    values["workspace.layout"] = entry(layout, "detected" if repos else "default", layout_from)

    repo_info = []
    for repo in repos:
        files = build_files(repo)
        repo_info.append({
            "path": str(repo), "origin": origin_url(repo), "default_branch": default_branch(repo),
            "build_files": files, "branches": remote_branches(repo), "subjects": commit_subjects(repo),
        })

    # Areas: a single repo's build folders one level down; a nested workspace's repos are discovered, so only a
    # shared "*" entry when every repo builds the same way.
    areas = {}
    if layout == "single":
        for child in sorted(root.iterdir()):
            if child.is_dir() and not child.name.startswith(".") and child.name not in SKIP_DIRS:
                files = build_files(child)
                if files:
                    areas[child.name] = commands(child, files)
        values["workspace.areas"] = entry(areas, "detected" if areas else "default",
                                          "build files one level down" if areas else "no build files one level down")
    else:
        shared = [json.dumps(commands(Path(info["path"]), info["build_files"]), sort_keys=True) for info in repo_info]
        if shared and len(set(shared)) == 1 and shared[0] != "{}":
            areas = {"*": json.loads(shared[0])}
        values["workspace.areas"] = entry(areas, "detected" if areas else "default",
                                          "every repo has the same build files" if areas
                                          else "repos are discovered; none listed")

    branch, _, branch_counts = most_common(info["default_branch"] for info in repo_info)
    values["workspace.default_branch"] = (
        entry(branch, "detected", f"origin/HEAD ({tally(branch_counts)})") if branch
        else entry("main", "default", "no origin/HEAD"))
    values["workspace.worktrees"] = entry(".worktrees", "detected" if (root / ".worktrees").is_dir() else "default",
                                          "the folder exists" if (root / ".worktrees").is_dir() else "default")

    owner, _, owner_counts = most_common((info["origin"] or {}).get("owner") for info in repo_info)
    host, _, _ = most_common((info["origin"] or {}).get("host") for info in repo_info)
    values["git.org"] = (entry(owner, "detected", f"git remote get-url origin ({tally(owner_counts)})") if owner
                         else entry(None, "default", "no origin remote", ask="no origin remote to read the org from"))
    values["git.branch"] = entry("{handle}/{key}-{slug}", "default", "default")

    # Branch protection, through gh, on up to max_repos repos.
    gh = gh_status()
    checked = []
    if gh["signed_in"] and branch:
        for info in repo_info[:max_repos]:
            origin = info["origin"] or {}
            if origin.get("owner") and origin.get("repo") and "github" in (origin.get("host") or ""):
                result = protection(origin["owner"], origin["repo"], info["default_branch"] or branch)
                info["protection"] = result
                checked.append(result)
    protected = [result for result in checked if result.get("protected")]
    if protected:
        rules, count, counts = most_common(
            (result["required"], result["review_required"], result["wait_for_ci"]) for result in protected)
        how = f"{protected[0]['from']} on {branch}, {count} of {len(checked)} repo(s) checked"
        for key, value in zip(("pr.required", "pr.review_required", "pr.wait_for_ci"), rules):
            values[key] = entry(value, "detected", how)
    else:
        reason = (gh.get("reason") if not gh["signed_in"]
                  else "no GitHub origin to check" if not checked
                  else f"no branch protection on {branch} in {len(checked)} repo(s) checked")
        for key, value in (("pr.required", True), ("pr.review_required", False), ("pr.wait_for_ci", True)):
            values[key] = entry(value, "default", "default", ask=reason)
    values["pr.merge"] = entry("squash", "default", "default")

    # The user.
    email = (git(repos[0] if repos else root, "config", "user.email") or "").lower()
    prefixes = [name.split("/", 1)[0] for info in repo_info for name, author in info["branches"]
                if "/" in name and email and author == email]
    handle, count, _ = most_common(prefixes)
    if handle:
        values["user.handle"] = entry(handle, "detected", f"prefix of {count} of your remote branches")
    elif gh.get("login"):
        values["user.handle"] = entry(gh["login"], "detected", "gh api user")
    else:
        values["user.handle"] = entry(None, "default", "nothing found",
                                      ask="no branches of yours with a prefix, and gh isn't signed in")
    full_name = git(repos[0] if repos else root, "config", "user.name") or ""
    values["user.name"] = (entry(full_name.split()[0], "detected", "git config user.name") if full_name.strip()
                           else entry(None, "default", "nothing found", ask="no git user.name"))

    # The vault.
    vaults = find_vaults(root)
    if len(vaults) == 1:
        values["vault.path"] = entry(vaults[0], "detected", ".obsidian folder")
    else:
        why = (f"{len(vaults)} .obsidian folders: {', '.join(vaults)}" if vaults
               else "no .obsidian folder in the workspace, two levels deep")
        values["vault.path"] = entry(None, "default", "nothing found", ask=why, candidates=vaults)
    values["vault.tasks"] = entry("Tasks", "default", "default")
    values["vault.discussions"] = entry("Discussions", "default", "default")

    # The tracker.
    servers = mcp_servers(root)
    keys = [match for info in repo_info for subject in info["subjects"] for match in KEY.findall(subject)]
    keys += [match.group(1).upper() for info in repo_info for name, _ in info["branches"]
             if (match := BRANCH_KEY.match(name))]
    prefix, count, prefix_counts = most_common(key for key in keys if key not in NOT_KEYS)
    if servers["atlassian"]:
        kind, why = "jira", f"Atlassian server {servers['atlassian']!r} in .mcp.json"
    elif prefix:
        kind, why = "vault", f"{prefix}-n keys in commits and branches, and no Atlassian server in .mcp.json"
    elif vaults:
        kind, why = "vault", "a vault and no ticket keys"
    else:
        kind, why = "none", "no vault, no ticket keys and no Atlassian server"
    values["tracker.kind"] = entry(kind, "detected" if servers["atlassian"] or prefix else "default", why,
                                   confirm=True)
    if prefix:
        how = f"most common key prefix in recent commits and branches ({tally(prefix_counts)})"
        values["tracker.key_prefix"] = entry(prefix, "detected", how)
        values["tracker.jira.project"] = entry(prefix, "detected", how)
    else:
        values["tracker.key_prefix"] = entry(None, "default", "nothing found", ask="no ticket keys found")
        values["tracker.jira.project"] = entry(None, "default", "nothing found", ask="no ticket keys found")
    values["tracker.jira.mcp_server"] = (
        entry(servers["atlassian"], "detected", ".mcp.json") if servers["atlassian"]
        else entry("atlassian", "default", "default; or the session's Atlassian server"))
    values["tracker.jira.site"] = entry(None, "default", "MCP", mcp=["getAccessibleAtlassianResources"],
                                        ask_when="more than one site")
    values["tracker.jira.cloud_id"] = entry(None, "default", "MCP", mcp=["getAccessibleAtlassianResources"])
    values["tracker.jira.account"] = entry(None, "default", "MCP", mcp=["atlassianUserInfo"])
    values["tracker.jira.fields"] = entry(None, "default", "MCP", mcp=["discover"],
                                          match=["Acceptance Criteria", "Sprint"], ask_when="no match")
    values["tracker.jira.write_back"] = entry("none", "default", "default")

    # The pull request announcement: always asked, as a channel name the skill resolves to an id.
    values["pr.announce.mcp_server"] = (entry(servers["slack"], "detected", ".mcp.json") if servers["slack"]
                                        else entry("slack", "default", "default; or the session's Slack server"))
    values["pr.announce.channel"] = entry(None, "default", "asked", mcp=["slack_search_channels"],
                                          ask="which channel announces new pull requests, if any")

    # Features and work: defaults from the layout and tracker, always confirmed.
    has_github = "github" in (host or "")
    intellij = (root / ".idea").is_dir() or any((Path(info["path"]) / ".idea").is_dir() for info in repo_info)
    compose = any("start" in value for value in areas.values()) or any(
        {"docker-compose.yml", "compose.yml"} & set(info["build_files"]) for info in repo_info)
    features = ["tasks", "worktrees"] + (["pr"] if has_github else []) + (["services-lock"] if compose else []) \
        + (["intellij"] if intellij else []) + (["jira"] if kind == "jira" else [])
    values["features"] = entry(features, "default", "from the layout and tracker", confirm=True)
    values["work.session_model"] = entry("sonnet", "default", "default", confirm=True)
    values["work.dispatch"] = entry("direct", "default", "default", confirm=True)
    owner_value = (values["user.name"]["value"] or "you").lower()
    values["work.wip"] = entry({"claude": 5, owner_value: 3}, "default", "default", confirm=True)

    # Shared libraries: a repo (or area) another builds against, read from build files.
    if layout == "nested":
        values.update(shared_libraries({Path(info["path"]).name: Path(info["path"]) for info in repo_info}))
    elif not builds_together(root):
        values.update(shared_libraries({name: root / name for name in areas}))

    for info in repo_info:  # the raw lists were for detection only
        info["branches"] = len(info.pop("branches"))
        info["subjects"] = len(info.pop("subjects"))
    existing = root / CONFIG_FILE
    return {
        "workspace": str(root),
        "values": values,
        "ask": [key for key, value in values.items() if "ask" in value],
        "confirm": [key for key, value in values.items() if value.get("confirm")],
        "mcp": [key for key, value in values.items() if "mcp" in value],
        "repos": repo_info,
        "gh": gh,
        "mcp_json": servers["all"],
        "known_features": known_features(),
        "existing_config": str(existing) if existing.is_file() else None,
        "toolbox": {
            "repo": str(REPO),
            "toolbox_py": str(REPO / "workspace" / ".claude" / "toolbox" / "bin" / "toolbox.py"),
            "installer": str(REPO / "install.py"),
            "draft_py": str(Path(__file__).resolve().parent / "draft.py"),
            "machine": str(REPO / "machine.yml"),
            "listed": machine_listed(root),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="detect.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("workspace", nargs="?", default=".")
    parser.add_argument("--max-repos", type=int, default=10, help="repos whose branch protection is checked")
    args = parser.parse_args(argv)
    root = Path(args.workspace).expanduser().resolve()
    if not root.is_dir():
        print(json.dumps({"ok": False, "error": f"{root} isn't a folder"}))
        return 1
    print(json.dumps(detect(root, args.max_repos), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
