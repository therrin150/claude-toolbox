#!/usr/bin/env python3
"""Link every toolbox's Claude Code domains into place on this machine.

Each domain folder mirrors its target folder: folders are created, files are linked one at a time, and skill folders
(anything containing SKILL.md) are linked whole. A per-workspace domain installs into every workspace machine.yml
lists, gated by each workspace's features, with the workspace's generated config rule. Other toolbox repos listed in
machine.yml contribute their domains. Previews by default and changes nothing; pass --apply to make the changes. See
README.md.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required. Install it with: python3 -m pip install -r requirements.txt")

REPO = Path(__file__).resolve().parent
MANIFEST = REPO / "domains.yml"
MACHINE = REPO / "machine.yml"
MACHINE_EXAMPLE = REPO / "machine.example.yml"
STATE = REPO / ".install-state.json"
PLAN = REPO / "install-plan.txt"

# The workspace config library, read for each workspace's features and its generated rule.
CONFIG_LIB = Path(__file__).resolve().parent / "workspace" / ".claude" / "toolbox" / "lib"
PER_WORKSPACE = "per-workspace"
FEATURES_FILE = "features.yml"
# The per-workspace domain that writes each workspace's generated rule.
RULE_DOMAIN = "workspace"
# The feature whose workspaces get their vault's tasks and discussions folders made, when the vault exists. Like any
# folder the installer makes, they are removed only once empty, so a task record is never touched.
TASKS_FEATURE = "tasks"
# The folder in a per-workspace domain that mirrors each workspace's vault (its config's `vault.path`) instead of the
# workspace root. Its files are written, never linked, since Obsidian and synced vaults handle links poorly: rendered
# when they end .tmpl, copied as they are otherwise.
VAULT_DIR = ".vault"

ALWAYS_SKIP = {".DS_Store", "Thumbs.db", "__pycache__", ".gitkeep"}
SKILL_FILES = ("SKILL.md", "skill.md")
FRONTMATTER_NAME = re.compile(r"^name:\s*['\"]?(.+?)['\"]?\s*$", re.MULTILINE)

CONFLICT_REASONS = {
    "real": "a real file or folder is in the way",
    "foreign": "a link to somewhere else is in the way",
    "collision": "more than one source wants this location",
    "missing-source": "the outside source doesn't exist",
    "blocked": "inside a folder that is in conflict",
    "dir-conflict": "a file or link is where a folder should be",
    "not-generated": "a file the installer didn't generate is where a generated file goes",
}


@dataclass
class Domain:
    name: str
    spec: dict
    repo: Path  # the toolbox repo whose domains.yml defines it

    @property
    def per_workspace(self) -> bool:
        return self.spec.get("target") == PER_WORKSPACE

    @property
    def source(self) -> Path:
        return self.repo / self.spec["source"]

    @property
    def marker(self) -> str:
        return f"{self.repo.name}:{self.name}"


@dataclass
class Workspace:
    path: Path
    config: object  # config.Config

    @property
    def key(self) -> str:
        return norm(self.path)


@dataclass
class Link:
    domain: str
    source: Path
    target: Path
    external: bool = False
    workspace: str | None = None  # norm path, for a per-workspace domain's links


@dataclass
class Generated:
    domain: str
    workspace: str
    path: Path
    text: str
    template: Path | None = None  # the source of a rendered or copied file; None for the generated rule


@dataclass
class Scope:
    """Which installed items this run may change: domains, narrowed to some workspaces, minus held ones."""

    domains: set
    workspaces: set | None = None  # None: every workspace
    held: set = field(default_factory=set)  # workspaces whose config is missing or invalid: left as they are

    def covers(self, domain: str, workspace: str | None) -> bool:
        if domain not in self.domains:
            return False
        if workspace is None:
            return True
        return workspace not in self.held and (self.workspaces is None or workspace in self.workspaces)


@dataclass
class Plan:
    create_dirs: list
    create: list
    relink: list
    stale: list
    forget: list
    write_rules: list
    stale_rules: list
    forget_rules: list
    excludes: list
    ok: list
    rules_ok: list
    conflicts: list
    warnings: list
    problems: list
    keep_dirs: set
    domains: list
    workspaces: list
    scope: Scope
    held_generated: list = field(default_factory=list)  # (domain, workspace, path) of templates that failed to render
    rendered: list = field(default_factory=list)  # each rendered file's {path, template, status}, for --json


# ---------------------------------------------------------------------------------------------------------------------
# Paths


def norm(path) -> str:
    """Comparable form of a path: absolute, case-normalized, without the Windows long-path prefix."""
    text = str(path).removeprefix("\\\\?\\")
    return os.path.normcase(os.path.abspath(text))


def link_dest(link: Path) -> str:
    """Where a symlink points, as a comparable path (without following any further links)."""
    raw = os.readlink(link).removeprefix("\\\\?\\")
    if not os.path.isabs(raw):
        raw = os.path.join(link.parent, raw)
    return norm(raw)


def inside(dest: str, roots: set) -> bool:
    return any(dest == root or dest.startswith(root + os.sep) for root in roots)


def expand(value: str, base: Path | None = None) -> Path:
    path = Path(os.path.expandvars(os.path.expanduser(value)))
    if base is not None and not path.is_absolute():
        path = base / path
    return path


def show(path) -> str:
    """Path for display, with the home folder shortened to ~."""
    text, home = str(path), str(Path.home())
    return "~" + text[len(home):] if text == home or text.startswith(home + os.sep) else text


# ---------------------------------------------------------------------------------------------------------------------
# Inputs


def load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_machine() -> dict:
    """machine.yml's domains, toolboxes and workspaces; all empty when there is no machine.yml."""
    raw = load_yaml(MACHINE) if MACHINE.exists() else {}
    return {
        "domains": list(raw.get("domains") or []),
        "toolboxes": [expand(value, REPO).absolute() for value in raw.get("toolboxes") or []],
        "workspaces": [expand(value, REPO).absolute() for value in raw.get("workspaces") or []],
    }


def load_manifests(toolboxes: list, problems: list) -> dict:
    """Every domain this installer knows: this repo's, then each listed toolbox's. The first definition of a name wins."""
    own = load_yaml(MANIFEST).get("domains") or {}
    if not isinstance(own, dict) or not own:
        sys.exit(f"{MANIFEST.name} lists no domains.")
    manifest = {name: Domain(name, spec, REPO) for name, spec in own.items()}
    for toolbox in toolboxes:
        if norm(toolbox) == norm(REPO):
            continue
        file = toolbox / "domains.yml"
        if not file.is_file():
            problems.append(f"toolbox {show(toolbox)}: no domains.yml there")
            continue
        domains = load_yaml(file).get("domains") or {}
        for name, spec in domains.items():
            if name in manifest:
                problems.append(
                    f"domain {name}: defined in both {show(manifest[name].repo)} and {show(toolbox)}; "
                    f"using {show(manifest[name].repo)}'s"
                )
                continue
            manifest[name] = Domain(name, spec, toolbox.resolve())
    return manifest


def load_features(domain: Domain) -> dict:
    """A per-workspace domain's features.yml: {feature: [paths relative to its source]}."""
    file = domain.source / FEATURES_FILE
    if not file.exists():
        return {}
    raw = load_yaml(file)
    if not isinstance(raw, dict) or not all(isinstance(paths, list) for paths in raw.values()):
        sys.exit(f"{show(file)}: must map each feature to a list of paths")
    return {str(feature): [str(path).strip("/") for path in paths] for feature, paths in raw.items()}


def import_config():
    if str(CONFIG_LIB) not in sys.path:
        sys.path.insert(0, str(CONFIG_LIB))
    import config  # noqa: PLC0415 (the workspace config library, imported by path)

    return config


def import_templates():
    import_config()
    import templates  # noqa: PLC0415 (the template renderer, beside the config library)

    return templates


def load_workspaces(paths: list, problems: list):
    """(workspaces with a valid config, keys of those left alone). Each without one gets a problem line."""
    config = import_config()
    active, held, seen = [], set(), set()
    for path in paths:
        key = norm(path)
        if key in seen:
            continue
        seen.add(key)
        if not path.is_dir():
            problems.append(f"workspace {show(path)}: doesn't exist")
            held.add(key)
            continue
        try:
            active.append(Workspace(path, config.load_workspace(path)))
        except config.NotFound:
            problems.append(
                f"workspace {show(path)}: no {config.CONFIG_PATH}; run /setup-workspace there. "
                "Nothing installed or removed there."
            )
            held.add(key)
        except config.ConfigError as error:
            problems.append(
                f"workspace {show(path)}: invalid {config.CONFIG_PATH}, so nothing installed or removed there: "
                + "; ".join(error.errors)
            )
            held.add(key)
    return active, held


def load_state() -> dict:
    state = {}
    if STATE.exists():
        with open(STATE, encoding="utf-8") as handle:
            state = json.load(handle)
    for section in ("links", "dirs", "generated"):
        state.setdefault(section, {})
    return state


def save_state(state: dict) -> None:
    with open(STATE, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)
        handle.write("\n")


# ---------------------------------------------------------------------------------------------------------------------
# Desired state


def is_skill_dir(path: Path) -> bool:
    return any((path / name).is_file() for name in SKILL_FILES)


def gated(relpath: str, features: dict, have: list) -> bool:
    """Whether features.yml ties this path, or a folder above it, to a feature the workspace doesn't have."""
    for feature, paths in features.items():
        if feature in have:
            continue
        if any(relpath == path or relpath.startswith(path + "/") for path in paths):
            return True
    return False


def skipper(per_workspace: bool, features: dict, have: list):
    if not per_workspace:
        return lambda relpath: False
    return lambda relpath: relpath in (FEATURES_FILE, VAULT_DIR) or gated(relpath, features, have)


def walk(domain: str, src_root: Path, tgt_root: Path, ignore: list, skip, workspace, links: list, dirs: list,
         templates: list | None = None) -> None:
    """Collect a domain's links and folders. With `templates` (a per-workspace domain), each .tmpl file goes there as
    (source, target without the suffix) instead of being linked."""
    suffix = import_templates().SUFFIX if templates is not None else None

    def visit(src: Path, tgt: Path, rel: str) -> None:
        for entry in sorted(src.iterdir()):
            relpath = f"{rel}/{entry.name}" if rel else entry.name
            if entry.name in ALWAYS_SKIP or any(fnmatch.fnmatch(relpath, pattern) for pattern in ignore):
                continue
            if skip(relpath):
                continue
            target = tgt / entry.name
            if entry.is_dir() and not is_skill_dir(entry):
                dirs.append((domain, workspace, target))
                visit(entry, target, relpath)
            elif suffix and entry.is_file() and entry.name.endswith(suffix) and entry.name != suffix:
                templates.append((entry, tgt / entry.name[: -len(suffix)]))
            else:
                links.append(Link(domain, entry, target, workspace=workspace))

    visit(src_root, tgt_root, "")


def parents_within(path: Path, root: Path) -> list:
    """The folders between root and path, outermost first."""
    chain, parent = [], path.parent
    while parent != root and root in parent.parents:
        chain.append(parent)
        parent = parent.parent
    return list(reversed(chain))


def render_template(domain: str, ws: Workspace, template: Path, path: Path, generated: list, held: list,
                    problems: list) -> None:
    """Render one template with a workspace's config into `generated`, or report why it can't be."""
    templates = import_templates()
    try:
        text = templates.render(template.read_text(encoding="utf-8"), ws.config)
    except (OSError, UnicodeDecodeError, templates.TemplateError) as error:
        reasons = error.errors if isinstance(error, templates.TemplateError) else [str(error)]
        problems.append(
            f"workspace {show(ws.path)}: can't render {show(path)} from {template.name}, so it is left as it is: "
            + "; ".join(reasons)
        )
        held.append((domain, ws.key, path))
        return
    generated.append(Generated(domain, ws.key, path, text, template))


def vault_files(domain: str, src_root: Path, ws: Workspace, ignore: list, skip, dirs: list, generated: list,
                held: list, problems: list) -> None:
    """Collect the domain's vault folder for one workspace: each file rendered or copied into the workspace's vault, at
    its path below the folder. A vault that doesn't exist is a problem, and nothing is written there."""
    source = src_root / VAULT_DIR
    if not source.is_dir():
        return
    found = []

    def visit(src: Path, rel: str) -> None:
        for entry in sorted(src.iterdir()):
            relpath = f"{rel}/{entry.name}"
            if entry.name in ALWAYS_SKIP or any(fnmatch.fnmatch(relpath, pattern) for pattern in ignore):
                continue
            if skip(relpath):
                continue
            if entry.is_dir():
                visit(entry, relpath)
            else:
                found.append((entry, Path(relpath).relative_to(VAULT_DIR)))

    visit(source, VAULT_DIR)
    vault = ws.config.vault_dir
    if found and not vault.is_dir():
        problems.append(f"workspace {show(ws.path)}: vault {show(vault)} doesn't exist, so nothing is written there")
        return
    suffix = import_templates().SUFFIX
    for entry, rel in found:
        target = vault / rel
        dirs.extend((domain, ws.key, folder) for folder in parents_within(target, vault))
        if entry.name.endswith(suffix) and entry.name != suffix:
            render_template(domain, ws, entry, target.with_name(entry.name[: -len(suffix)]), generated, held, problems)
            continue
        try:
            text = entry.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            problems.append(f"workspace {show(ws.path)}: can't copy {entry.name} into the vault: {error}")
            held.append((domain, ws.key, target))
            continue
        generated.append(Generated(domain, ws.key, target, text, entry))


def desired(manifest: dict, names: list, workspaces: list, problems: list, warnings: list, held: list | None = None):
    """(links, folders, generated files). A template that fails to render is a problem, and its target goes in `held`
    as (domain, workspace, path): neither written nor removed."""
    links, dirs, generated = [], [], []
    held = [] if held is None else held
    by_key = {ws.key: ws for ws in workspaces}
    for name in names:
        domain = manifest[name]
        spec = domain.spec
        src_root = domain.source
        if not src_root.is_dir():
            problems.append(f"{name}: source folder {show(src_root)} doesn't exist")
            continue
        if domain.per_workspace:
            features = load_features(domain)
            for feature, paths in features.items():
                warnings.extend(
                    f"{name}/{FEATURES_FILE}: {feature}: {path} doesn't exist"
                    for path in paths
                    if not (src_root / path).exists()
                )
            roots = [(ws.key, ws.path, ws.config.features) for ws in workspaces]
        else:
            features = {}
            tgt_root = expand(spec["target"])
            if not tgt_root.is_dir():
                problems.append(f"{name}: target folder {show(tgt_root)} doesn't exist. Create or clone it first.")
                continue
            roots = [(None, tgt_root, None)]
        for workspace, tgt_root, have in roots:
            skip = skipper(domain.per_workspace, features, have)
            found = [] if domain.per_workspace else None
            walk(name, src_root, tgt_root, spec.get("ignore") or [], skip, workspace, links, dirs, found)
            for template, path in found or []:
                render_template(name, by_key[workspace], template, path, generated, held, problems)
            if domain.per_workspace:
                vault_files(name, src_root, by_key[workspace], spec.get("ignore") or [], skip, dirs, generated, held,
                            problems)
            for external in spec.get("external") or []:
                if skip(external["target"]):
                    continue
                target = tgt_root / external["target"]
                dirs.extend((name, workspace, folder) for folder in parents_within(target, tgt_root))
                links.append(Link(name, expand(external["source"]), target, external=True, workspace=workspace))

    if RULE_DOMAIN in names and manifest[RULE_DOMAIN].per_workspace and workspaces:
        config = import_config()
        for ws in workspaces:
            path = ws.path / config.RULE_PATH
            dirs.extend((RULE_DOMAIN, ws.key, folder) for folder in parents_within(path, ws.path))
            generated.append(Generated(RULE_DOMAIN, ws.key, path, config.render_rule(ws.config)))
            if TASKS_FEATURE in ws.config.features:
                vault = ws.config.vault_dir
                if not vault.is_dir():
                    problem = (f"workspace {show(ws.path)}: vault {show(vault)} doesn't exist, so nothing is written "
                               "there")
                    if problem not in problems:  # vault_files may have said so already
                        problems.append(problem)
                    continue
                for folder in (ws.config.tasks_dir, ws.config.discussions_dir):
                    dirs.extend((RULE_DOMAIN, ws.key, d) for d in [*parents_within(folder, vault), folder])

    unique_dirs = {}
    for domain, workspace, folder in dirs:
        unique_dirs.setdefault(norm(folder), (domain, workspace, folder))
    return links, list(unique_dirs.values()), generated


# ---------------------------------------------------------------------------------------------------------------------
# Checks


def frontmatter_name(markdown: Path) -> str | None:
    text = markdown.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    match = FRONTMATTER_NAME.search(text[3:] if end == -1 else text[3:end])
    return match.group(1).strip() if match else None


def name_warnings(link: Link, domain: Domain) -> list:
    if link.external:
        return []
    source = link.source
    parts = source.relative_to(domain.repo).parts
    if source.is_dir():
        kind, name = "skill", source.name
        markdown = next(source / file for file in SKILL_FILES if (source / file).is_file())
    elif source.suffix == ".md" and source.parent.name == "agents":
        kind, name, markdown = "agent", source.stem, source
    elif source.suffix == ".md" and "rules" in parts:
        kind, name, markdown = "rule", source.stem, None
    else:
        return []
    warnings, where = [], "/".join(parts)
    if markdown is not None:
        declared = frontmatter_name(markdown)
        if declared != name:
            warnings.append(f"{kind} {where}: frontmatter name is {declared!r}, but its name on disk is {name!r}")
    prefix = domain.spec.get("prefix")
    if prefix and not name.startswith(prefix):
        warnings.append(f"{kind} {where}: missing the domain prefix {prefix!r}")
    return warnings


def feature_warnings(manifest: dict, names: list, workspaces: list) -> list:
    """Features a workspace's config names that no features.yml it installs from mentions."""
    known = set()
    for name in names:
        if manifest[name].per_workspace:
            known |= set(load_features(manifest[name]))
    return [
        f"workspace {show(ws.path)}: feature {feature!r} isn't in any {FEATURES_FILE}, so it gates nothing"
        for ws in workspaces
        for feature in ws.config.features
        if feature not in known
    ]


def is_generated(text: str, expected: str) -> bool:
    """Whether a file on disk is a generated rule: its title and note paragraphs match the rendered text's."""
    return text.split("\n\n", 2)[:2] == expected.split("\n\n", 2)[:2]


def git_exclude(root: Path):
    """(repo top level, exclude file) when root is inside a git work tree, else None."""
    def git(*args):
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()

    try:
        return Path(git("rev-parse", "--show-toplevel")), expand(git("rev-parse", "--git-path", "info/exclude"), root)
    except (OSError, subprocess.CalledProcessError):
        return None


def render_exclude(text: str, marker: str, entries: list) -> str:
    begin, end = f"# >>> {marker} (managed by install.py)", f"# <<< {marker}"
    kept, skipping = [], False
    for line in text.splitlines():
        if line == begin:
            skipping = True
        elif line == end:
            skipping = False
        elif not skipping:
            kept.append(line)
    if entries:
        kept += [begin, *entries, end]
    return "\n".join(kept) + "\n" if kept else ""


# ---------------------------------------------------------------------------------------------------------------------
# Planning


def own_link(path: Path, state: dict, scope: Scope) -> bool:
    """Whether a link is one this installer made, still pointing where it did, and in this run's scope."""
    entry = state["links"].get(norm(path))
    return bool(entry) and scope.covers(entry["domain"], entry.get("workspace")) \
        and link_dest(path) == norm(entry["source"])


def generated_status(gen: Generated, counts: dict, blocked: list, state: dict, scope: Scope) -> str:
    """`ok`, a write status (`new`, `changed`, `replaces a link`), or a conflict kind."""
    key = norm(gen.path)
    if counts[key] > 1:
        return "collision"
    if any(key.startswith(folder + os.sep) for folder in blocked):
        return "blocked"
    if gen.path.is_file() and not gen.path.is_symlink():
        current = gen.path.read_text(encoding="utf-8", errors="replace")
        # An unrecorded rendered file is adopted only when it is exactly what this sync would write.
        adoptable = current == gen.text if gen.template else is_generated(current, gen.text)
        if key not in state["generated"] and not adoptable:
            return "not-generated"
        return "ok" if current == gen.text else "changed"
    if gen.path.is_symlink() and own_link(gen.path, state, scope):
        return "replaces a link"  # a file that used to be linked: the link goes as stale before the write
    if gen.path.is_symlink() or gen.path.exists():
        return "not-generated"
    return "new"


def build_plan(manifest: dict, names: list, scope: Scope, state: dict, workspaces: list, problems: list) -> Plan:
    warnings, held = [], []
    links, dirs, generated = desired(manifest, names, workspaces, problems, warnings, held)
    plan = Plan(
        [], [], [], [], [], [], [], [], [], [], [], [], warnings, problems, set(), names,
        [ws.path for ws in workspaces], scope, held,
    )
    owned = {norm(REPO)} | {norm(domain.repo) for domain in manifest.values()}

    blocked = []
    for domain, workspace, folder in dirs:
        if folder.is_symlink() or (folder.exists() and not folder.is_dir()):
            plan.conflicts.append(("dir-conflict", folder, None))
            blocked.append(norm(folder))
        elif not folder.exists():
            plan.create_dirs.append((domain, workspace, folder))
        plan.keep_dirs.add(norm(folder))

    counts = {}
    for target in [link.target for link in links] + [gen.path for gen in generated] + [path for _, _, path in held]:
        counts[norm(target)] = counts.get(norm(target), 0) + 1

    for link in links:
        key = norm(link.target)
        plan.warnings += name_warnings(link, manifest[link.domain])
        if counts[key] > 1:
            plan.conflicts.append(("collision", link.target, link.source))
        elif any(key.startswith(folder + os.sep) for folder in blocked):
            plan.conflicts.append(("blocked", link.target, link.source))
        elif link.target.is_symlink():
            dest = link_dest(link.target)
            if dest == norm(link.source):
                plan.ok.append(link)
            elif inside(dest, owned):
                plan.relink.append(link)
            else:
                plan.conflicts.append(("foreign", link.target, Path(dest)))
        elif link.target.exists():
            plan.conflicts.append(("real", link.target, link.source))
        elif not link.source.exists():
            plan.conflicts.append(("missing-source", link.target, link.source))
        else:
            plan.create.append(link)

    for gen in generated:
        status = generated_status(gen, counts, blocked, state, scope)
        if status == "ok":
            plan.rules_ok.append(gen)
        elif status in CONFLICT_REASONS:
            plan.conflicts.append((status, gen.path, None))
        else:
            plan.write_rules.append((gen, status))
        if gen.template:
            shown = f"conflict: {CONFLICT_REASONS[status]}" if status in CONFLICT_REASONS else status
            plan.rendered.append({"path": str(gen.path), "template": str(gen.template), "status": shown})
    for _, _, path in held:
        plan.rendered.append({"path": str(path), "template": None, "status": "can't render; see Problems"})

    wanted = {norm(link.target) for link in links}
    for key, record in state["links"].items():
        if not scope.covers(record["domain"], record.get("workspace")) or key in wanted:
            continue
        target = Path(record["target"])
        if target.is_symlink() and link_dest(target) == norm(record["source"]):
            plan.stale.append((key, record))
        else:
            plan.forget.append(key)

    wanted_rules = {norm(gen.path) for gen in generated} | {norm(path) for _, _, path in held}
    for key, record in state["generated"].items():
        if not scope.covers(record["domain"], record["workspace"]) or key in wanted_rules:
            continue
        path = Path(record["path"])
        if path.is_file() and not path.is_symlink():
            plan.stale_rules.append((key, record))
            if record.get("template"):
                plan.rendered.append({"path": record["path"], "template": record["template"], "status": "stale"})
        else:
            plan.forget_rules.append(key)

    plan.excludes = plan_excludes(manifest, scope, state, workspaces, plan)
    plan.warnings += feature_warnings(manifest, names, workspaces)
    plan.warnings = list(dict.fromkeys(plan.warnings))
    return plan


def plan_excludes(manifest: dict, scope: Scope, state: dict, workspaces: list, plan: Plan) -> list:
    """One managed block per toolbox, domain and git target: the domain's links and generated files (the rule and
    rendered templates, including any left in place because they failed to render), plus, for the rule domain, each
    workspace's config file."""
    installed = plan.ok + plan.relink + plan.create
    written = [(gen.domain, gen.workspace, gen.path) for gen in plan.rules_ok + [gen for gen, _ in plan.write_rules]]
    written += [(domain, workspace, path) for domain, workspace, path in plan.held_generated if path.is_file()]
    active = {ws.key: ws for ws in workspaces}

    roots = []  # (domain, workspace key or None, root folder)
    for name in sorted(scope.domains & set(manifest)):
        domain = manifest[name]
        if not domain.per_workspace:
            roots.append((name, None, expand(domain.spec["target"])))
            continue
        keys = set(active) | {
            record["workspace"]
            for section in ("links", "generated")
            for record in state[section].values()
            if record["domain"] == name and record.get("workspace") and scope.covers(name, record["workspace"])
        }
        roots += [(name, key, active[key].path if key in active else Path(key)) for key in sorted(keys)]

    config = import_config() if any(workspace for _, workspace, _ in roots) else None
    excludes = []
    for name, workspace, root in roots:
        found = git_exclude(root) if root.is_dir() else None
        if found is None:
            continue
        top, exclude_file = found

        def entry(path) -> str:
            return "/" + Path(os.path.relpath(norm(path), norm(top))).as_posix()

        # A vault outside the workspace's repo (work's lives elsewhere on disk) has nothing to exclude here.
        entries = {entry(link.target) for link in installed if link.domain == name and link.workspace == workspace}
        entries |= {entry(path) for domain, key, path in written
                    if domain == name and key == workspace and inside(norm(path), {norm(top)})}
        if name == RULE_DOMAIN and workspace in active:
            entries.add(entry(root / config.CONFIG_PATH))
        current = exclude_file.read_text(encoding="utf-8") if exclude_file.exists() else ""
        updated = render_exclude(current, manifest[name].marker, sorted(entries))
        if updated != current:
            excludes.append((exclude_file, updated, len(entries)))
    return excludes


# ---------------------------------------------------------------------------------------------------------------------
# Applying


def record(fields: dict, workspace: str | None) -> dict:
    return {**fields, "workspace": workspace} if workspace else fields


def apply_plan(plan: Plan, state: dict) -> list:
    errors = []
    for domain, workspace, folder in plan.create_dirs:
        folder.mkdir(parents=True, exist_ok=True)
        state["dirs"][norm(folder)] = record({"path": str(folder), "domain": domain}, workspace)
    for key, entry in plan.stale:
        Path(entry["target"]).unlink()
        del state["links"][key]
    for key in plan.forget:
        del state["links"][key]
    for key, entry in plan.stale_rules:
        Path(entry["path"]).unlink()
        del state["generated"][key]
    for key in plan.forget_rules:
        del state["generated"][key]
    for link in plan.relink + plan.create:
        try:
            if link.target.is_symlink():
                link.target.unlink()
            os.symlink(link.source, link.target, target_is_directory=link.source.is_dir())
        except OSError as error:
            hint = " (on Windows, turn on Developer Mode)" if os.name == "nt" else ""
            errors.append(f"{show(link.target)}: {error}{hint}")
            continue
    for link in plan.ok + plan.relink + plan.create:
        if link.target.is_symlink():
            state["links"][norm(link.target)] = record(
                {"target": str(link.target), "source": str(link.source), "domain": link.domain}, link.workspace
            )
    for gen, _ in plan.write_rules:
        try:
            gen.path.parent.mkdir(parents=True, exist_ok=True)
            gen.path.write_text(gen.text, encoding="utf-8")
        except OSError as error:
            errors.append(f"{show(gen.path)}: {error}")
            continue
    for gen in plan.rules_ok + [gen for gen, _ in plan.write_rules]:
        if gen.path.is_file():
            state["generated"][norm(gen.path)] = {
                "path": str(gen.path), "domain": gen.domain, "workspace": gen.workspace,
                **({"template": str(gen.template)} if gen.template else {}),
            }
    for exclude_file, text, _ in plan.excludes:
        exclude_file.parent.mkdir(parents=True, exist_ok=True)
        exclude_file.write_text(text, encoding="utf-8")
    # Remove folders this installer created that are no longer wanted and are now empty, deepest first.
    for key in sorted(state["dirs"], key=len, reverse=True):
        entry = state["dirs"][key]
        folder = Path(entry["path"])
        if not plan.scope.covers(entry["domain"], entry.get("workspace")) or key in plan.keep_dirs:
            continue
        if folder.is_dir() and not folder.is_symlink() and not any(folder.iterdir()):
            folder.rmdir()
        if not folder.exists():
            del state["dirs"][key]
    save_state(state)
    return errors


# ---------------------------------------------------------------------------------------------------------------------
# Reporting


def rendered_from(gen: Generated) -> str:
    if not gen.template:
        return ""
    verb = "rendered" if gen.template.name.endswith(import_templates().SUFFIX) else "copied"
    return f", {verb} from {gen.template.name}"


def plan_sections(plan: Plan, errors: list) -> list:
    return [
        ("Create folders", [show(folder) for _, _, folder in plan.create_dirs]),
        ("Create links", [f"{show(link.target)} -> {show(link.source)}" for link in plan.create]),
        ("Replace links", [f"{show(link.target)} -> {show(link.source)}" for link in plan.relink]),
        ("Remove stale links", [f"{show(entry['target'])}" for _, entry in plan.stale]),
        (
            "Write generated rules",
            [f"{show(gen.path)} ({status}{rendered_from(gen)})" for gen, status in plan.write_rules],
        ),
        ("Remove generated rules", [show(entry["path"]) for _, entry in plan.stale_rules]),
        ("Update git excludes", [f"{show(path)} ({count} entries)" for path, _, count in plan.excludes]),
        (
            "Already in place",
            [show(link.target) for link in plan.ok]
            + [f"{show(gen.path)} ({'rendered' if gen.template else 'generated'})" for gen in plan.rules_ok],
        ),
        (
            "Conflicts, left alone",
            [
                f"[{kind}] {show(target)}: {CONFLICT_REASONS[kind]}"
                + ("" if other is None else f" ({'points to' if kind == 'foreign' else 'source'}: {show(other)})")
                for kind, target, other in plan.conflicts
            ],
        ),
        ("Warnings", plan.warnings),
        ("Problems", plan.problems),
        ("Errors", errors),
    ]


# Sections that mean the preview would change something, or can't.
PENDING = ("Create folders", "Create links", "Replace links", "Remove stale links", "Write generated rules",
           "Remove generated rules", "Update git excludes", "Conflicts, left alone", "Problems", "Errors")


def report_json(plan: Plan, listed: bool) -> None:
    """The preview as JSON, for `toolbox.py doctor`: each section's items, whether anything is pending, whether
    machine.yml lists every workspace acted on, and each rendered file's status (`ok`, `new`, `changed`, `replaces a
    link`, `stale`, a conflict, or can't render). Writes no plan file."""
    sections = dict(plan_sections(plan, []))
    print(json.dumps({
        "clean": not any(sections[title] for title in PENDING),
        "listed": listed,
        "workspaces": [str(path) for path in plan.workspaces],
        "sections": sections,
        "rendered": plan.rendered,
    }, indent=2))


def report(plan: Plan, applied: bool, rerun: str, errors: list) -> None:
    sections = plan_sections(plan, errors)
    mode = "APPLIED" if applied else "PREVIEW"
    header = [
        f"{REPO.name} install plan: {mode}, {datetime.now().isoformat(timespec='seconds')}",
        f"Domains: {', '.join(plan.domains) or '(none)'}",
        f"Workspaces: {', '.join(show(path) for path in plan.workspaces) or '(none)'}",
    ]
    with open(PLAN, "w", encoding="utf-8") as handle:
        handle.write("\n".join(header) + "\n")
        for title, items in sections:
            handle.write(f"\n== {title} ({len(items)})\n")
            handle.writelines(f"  {item}\n" for item in items)

    print("\n".join(header) + "\n")
    for title, items in sections:
        if items or title in ("Create links", "Conflicts, left alone"):
            print(f"  {title}: {len(items)}")
    for title in ("Write generated rules", "Conflicts, left alone", "Warnings", "Problems", "Errors"):
        items = dict(sections)[title]
        if items:
            print(f"\n{title}:")
            print("\n".join(f"  {item}" for item in items))

    bar = "-" * 72
    print(f"\n{bar}")
    if applied:
        print("APPLIED.")
        print(f"Full record: {PLAN}")
    else:
        print("PREVIEW ONLY: nothing was changed.")
        print(f"Full plan:   {PLAN}")
        print(f"To apply:    {rerun} --apply")
    print(bar)


# ---------------------------------------------------------------------------------------------------------------------


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="make the changes (default: preview only)")
    parser.add_argument("--teardown", action="store_true", help="remove what this installer installed instead")
    parser.add_argument(
        "--domain", action="append", default=[], help="act on this domain only, ignoring machine.yml (repeatable)"
    )
    parser.add_argument(
        "--workspace",
        action="append",
        default=[],
        help="act on this workspace only, ignoring machine.yml's workspaces (repeatable)",
    )
    parser.add_argument("--json", action="store_true", help="print the preview as JSON and write no plan file")
    args = parser.parse_args(argv)
    if args.json and args.apply:
        sys.exit("--json previews only; leave out --apply")

    machine = load_machine()
    problems = []
    manifest = load_manifests(machine["toolboxes"], problems)
    state = load_state()
    unknown = [name for name in args.domain if name not in manifest]
    if unknown:
        sys.exit(f"Unknown domain(s): {', '.join(unknown)}. Known domains: {', '.join(manifest)}")
    per_workspace = [name for name, domain in manifest.items() if domain.per_workspace]

    if args.workspace:
        chosen = [name for name in args.domain if name in per_workspace] if args.domain else per_workspace
        if not chosen:
            sys.exit(f"--workspace acts on per-workspace domains only: {', '.join(per_workspace) or '(none)'}")
    else:
        chosen = list(args.domain) or None

    if chosen is not None:
        scope_domains = set(chosen)
    else:
        scope_domains = set(manifest) | {
            entry["domain"] for section in ("links", "dirs", "generated") for entry in state[section].values()
        }
    if args.teardown:
        names = []
    elif chosen is not None:
        names = chosen
    else:
        if not MACHINE.exists():
            sys.exit(
                f"No {MACHINE.name} on this machine. Copy {MACHINE_EXAMPLE.name} to {MACHINE.name} and list the "
                f"domains to install here.\n  {MACHINE_EXAMPLE}"
            )
        names = list(dict.fromkeys(machine["domains"]))
        unknown = [name for name in names if name not in manifest]
        if unknown:
            sys.exit(f"{MACHINE.name} lists unknown domain(s): {', '.join(unknown)}")
        names += [name for name in per_workspace if name not in names]

    paths = [expand(value).absolute() for value in args.workspace] if args.workspace else machine["workspaces"]
    workspaces, held = [], set()
    if any(name in per_workspace for name in names):
        workspaces, held = load_workspaces(paths, problems)
    listed = {norm(path) for path in machine["workspaces"]}
    warnings = [
        f"workspace {show(path)}: not in {MACHINE.name}, so the next full sync removes what this run installs there"
        for path in paths
        if args.workspace and not args.teardown and norm(path) not in listed
    ]
    scope = Scope(scope_domains, {norm(path) for path in paths} if args.workspace else None, held)

    plan = build_plan(manifest, names, scope, state, workspaces, problems)
    plan.warnings = warnings + plan.warnings
    if args.json:
        report_json(plan, all(norm(path) in listed for path in paths))
        return 1 if plan.conflicts or plan.problems else 0
    errors = apply_plan(plan, state) if args.apply else []

    rerun = " ".join(
        [Path(sys.executable).name, Path(__file__).name]
        + (["--teardown"] if args.teardown else [])
        + [f"--domain {name}" for name in args.domain]
        + [f"--workspace {value}" for value in args.workspace]
    )
    report(plan, args.apply, rerun, errors)
    return 1 if plan.conflicts or plan.problems or errors else 0


if __name__ == "__main__":
    sys.exit(main())
