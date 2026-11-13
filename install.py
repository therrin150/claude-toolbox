#!/usr/bin/env python3
"""Copy every toolbox's Claude Code files into place on this machine.

A toolbox repo is laid out as feature folders: `core/` (always installs) and `features/<name>/`, each holding a
feature.yml and a `.claude/` tree that mirrors where its files install. A `target: workspace` feature installs into
each workspace that lists it under `features` (core into every workspace, with the workspace's generated config rule),
a `target: global` feature into ~/.claude when machine.yml lists it. A toolbox repo machine.yml lists may instead (or
also) install its toolbox.yml's source folder, which mirrors its target. Every file is installed as a copy (never a
link), so each lands inside the project that uses it; templates are rendered and rules get a generated note. A
manifest (.install-state.json) records each file's source and the hash written, and every target is classified: create, update (still what was written, source changed), unchanged,
remove (no longer wanted, unedited) or conflict (edited on disk, or a file the installer didn't write). A feature folder
may also carry a settings.json fragment: the picked workspace features' fragments are merged into the workspace's
installer-owned .claude/settings.json (a generated file like the rule), and the picked global features' into
~/.claude/settings.json, where the installer owns only the entries the manifest records it adding and merges around the
user's own. Previews by default and changes nothing; pass --apply to make the changes, each file written atomically.
See README.md.
"""

from __future__ import annotations

import argparse
import copy
import fnmatch
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required. Install it with: python3 -m pip install -r requirements.txt")

REPO = Path(__file__).resolve().parent
MANIFEST_NAME = "toolbox.yml"
OLD_MANIFEST_NAME = "domains.yml"
MACHINE = REPO / "machine.yml"
MACHINE_EXAMPLE = REPO / "machine.example.yml"
STATE = REPO / ".install-state.json"
PLAN = REPO / "install-plan.txt"

# The workspace config library, read for each workspace's features and its generated rule.
CONFIG_LIB = Path(__file__).resolve().parent / "core" / "workspace" / ".claude" / "toolbox" / "lib"
PER_WORKSPACE = "per-workspace"
GLOBAL_TARGET = "~/.claude"
# What this repo's own feature folders never install: the toolbox scripts' tests and fixtures, run from this repo.
OWN_IGNORE = [".claude/toolbox/tests"]
# The retired folders this repo used to install: its parts recorded in the manifest under these names are cleaned up.
RETIRED_PARTS = ("global", "workspace")
# The keys a listed toolbox's toolbox.yml may hold: what it installs, and how ship-toolbox-change lands its changes.
TOOLBOX_KEYS = {"source", "target", "prefix", "ignore", "external", "ship", "branch_prefix"}
# A toolbox repo's feature folders: `core` always installs, each folder under `features` when picked. Each holds a
# feature.yml and a `.claude/` tree (and, for a workspace feature, a `.vault/` tree) that mirrors where its files go.
# `core/` is one folder with its own feature.yml, or, to have a global and a workspace core, a `core/global/` and a
# `core/workspace/` folder, each with one.
CORE = "core"
FEATURES_DIR = "features"
FEATURE_FILE = "feature.yml"
SCOPES = ("global", "workspace")
# This repo's own first per-workspace part writes each workspace's generated rule, and its first global part writes the
# pointer to this repo, so a script installed as a copy can find the installer.
POINTER_NAME = "toolbox-repo"
# The feature whose workspaces get their vault's tasks and discussions folders made, when the vault exists. Like any
# folder the installer makes, they are removed only once empty, so a task record is never touched.
TASKS_FEATURE = "tasks"
# The folder in a per-workspace part that mirrors each workspace's vault (its config's `vault.path`) instead of the
# workspace root: rendered when a file ends .tmpl, copied as it is otherwise.
VAULT_DIR = ".vault"
FEATURE_ROOTS = (".claude", VAULT_DIR)  # the only folders of a feature folder that install
# A feature folder's settings fragment, merged (not copied) into the settings file of its scope: a workspace's
# .claude/settings.json, or ~/.claude/settings.json.
SETTINGS_FRAGMENT = "settings.json"
SETTINGS_FILE = "settings.json"
# The rules folder in a per-workspace part. Each rule is written with a generated note, after any frontmatter so
# `paths:` still works.
RULES_DIR = ".claude/rules"
RULE_FRONTMATTER = re.compile(r"\A---\n(?:.*?\n)?---[ \t]*(?:\n|\Z)", re.DOTALL)

# Running an installed script writes __pycache__ beside it, where it used to land in the toolbox repo.
PYCACHE_EXCLUDE = "__pycache__/"

ALWAYS_SKIP = {".DS_Store", "Thumbs.db", "__pycache__", ".gitkeep"}
SKILL_FILES = ("SKILL.md", "skill.md")
FRONTMATTER_NAME = re.compile(r"^name:\s*['\"]?(.+?)['\"]?\s*$", re.MULTILINE)

# How an item was made: copied as it is, copied with a rule's note, copied into a vault, rendered from a template, or
# generated from the config.
COPY, RULE, VAULT, RENDERED, GENERATED = "copy", "rule", "vault", "rendered", "generated"

CONFLICT_REASONS = {
    "edited": "edited on disk since the installer wrote it",
    "unmanaged": "a file the installer didn't write is there",
    "unreadable": "can't be read",
    "folder": "a folder is where a file goes",
    "foreign": "a link to somewhere else is in the way",
    "collision": "more than one source wants this location",
    "missing-source": "the outside source doesn't exist",
    "blocked": "inside a folder that is in conflict",
    "dir-conflict": "a file or link is where a folder should be",
    "edited-stale": "edited on disk, and no longer wanted",
}


@dataclass
class Feature:
    """One feature folder: `core` or a folder under `features/`, with its feature.yml read."""

    name: str
    folder: Path
    target: str  # global or workspace
    description: str
    needs: list
    requires: list


@dataclass
class Part:
    """What one source installs and where: a listed toolbox's source folder, or the feature folders of one scope (global
    or workspace) of a toolbox repo."""

    name: str
    spec: dict
    repo: Path  # the toolbox repo it comes from
    toolbox: str = ""  # the toolbox's name: the repo's folder name, as machine.yml lists it
    features: dict = field(default_factory=dict)  # a feature part's {name: Feature}

    @property
    def per_workspace(self) -> bool:
        return self.spec.get("target") == PER_WORKSPACE

    @property
    def scope(self) -> str | None:
        """`global` or `workspace` for a part made of feature folders; None for a source folder."""
        return self.spec.get("features")

    @property
    def source(self) -> Path:
        return self.repo / self.spec["source"]

    @property
    def marker(self) -> str:
        return f"{self.repo.name}:{self.spec['source']}"


@dataclass
class Workspace:
    path: Path
    config: object  # config.Config

    @property
    def key(self) -> str:
        return norm(self.path)


@dataclass
class Item:
    """One file to install: the bytes it should hold at `target`."""

    part: str
    workspace: str | None  # norm path, for a per-workspace part's files
    target: Path
    data: bytes
    source: Path | None = None  # the file copied or the template rendered; None for a generated file
    kind: str = COPY
    executable: bool = False
    in_skill: bool = False  # inside a skill folder, whose name is checked once for the folder

    @property
    def special(self) -> bool:
        """Whether it is a rule, vault, rendered or generated file: what `toolbox.py doctor` reports as rendered."""
        return self.kind != COPY


@dataclass
class Scope:
    """Which installed items this run may change: parts (by the name recorded with each item), narrowed to some
    workspaces, minus held ones."""

    parts: set
    workspaces: set | None = None  # None: every workspace
    held: set = field(default_factory=set)  # workspaces whose config is missing or invalid: left as they are

    def covers(self, part: str, workspace: str | None) -> bool:
        if part not in self.parts:
            return False
        if workspace is None:
            return True
        return workspace not in self.held and (self.workspaces is None or workspace in self.workspaces)


@dataclass
class Merge:
    """The merge of global features' settings fragments into one settings file the user also owns."""

    key: str  # norm path of the file, as the manifest keys it
    part: str
    path: Path  # as listed
    real: Path  # where it is written: the path, or what a link at the path points to
    data: bytes | None  # the text to write; None when the file is already right
    created: bool  # the file doesn't exist yet
    delete: bool  # the file was made by the installer and nothing is left in it
    added: list  # entries it owns once applied: [{"path": [...], "item": x} | {"path": [...], "set": x}]
    containers: list  # paths of mappings and lists it made, removed again once empty
    file_created: bool  # it made the file, so it removes the file when nothing is left
    adds: int = 0
    removes: int = 0

    @property
    def changes(self) -> bool:
        return self.data is not None or self.delete

    @property
    def record(self) -> dict | None:
        """The manifest entry, or None when it owns nothing there."""
        if self.delete or not (self.added or self.containers):
            return None
        return {"path": str(self.path), "part": self.part, "added": self.added, "created": self.containers,
                "file_created": self.file_created}


@dataclass
class Plan:
    create_dirs: list  # (part, workspace, folder)
    replace_dirs: list  # (part, workspace, folder): a link to one of our folders, replaced by a real folder
    create: list  # Items with nothing on disk
    update: list  # (Item, detail): still what was written, or a link of ours; rewritten
    unchanged: list  # Items already what they should be
    edited: list  # Items edited on disk: conflicts that are ours
    remove: list  # (key, manifest entry): no longer wanted, unedited
    forget: list  # manifest keys whose file is gone or no longer ours
    excludes: list
    conflicts: list
    warnings: list
    problems: list
    keep_dirs: set
    parts: list
    dirs: list  # every folder wanted, as (part, workspace, folder)
    known: set  # every part name a toolbox has, held ones included
    workspaces: list
    scope: Scope
    held_generated: list = field(default_factory=list)  # (part, workspace, path) of files that failed to read or render
    rendered: list = field(default_factory=list)  # each special file's {path, template, status}, for --json
    merges: list = field(default_factory=list)  # Merges into settings files the user also owns


# ---------------------------------------------------------------------------------------------------------------------
# Paths and files


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


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def disk_digest(path: Path) -> str | None:
    try:
        return digest(path.read_bytes())
    except OSError:
        return None


def is_executable(path: Path) -> bool:
    """Whether the file has an executable bit. Never on Windows, where there is none to keep."""
    return os.name != "nt" and path.stat().st_mode & 0o111 != 0


def write_atomic(path: Path, data: bytes, executable: bool, mode: int | None = None) -> None:
    """Write a file so it is never seen half-written: a temp file beside it, then a rename over it (which replaces a
    link at the path rather than following it). `mode` overrides the executable bit's 755 or 644."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".install")
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        os.chmod(temp, mode if mode is not None else 0o755 if executable else 0o644)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------------------------------------------------------------
# Inputs


def load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_machine() -> dict:
    """machine.yml's toolboxes, workspaces and global features, all empty when there is no machine.yml, and whether it
    still has the retired `domains:` key. `features` is the global features picked, as a list of names."""
    raw = load_yaml(MACHINE) if MACHINE.exists() else {}
    features = raw.get("features") or []
    return {
        "toolboxes": [expand(value, REPO).absolute() for value in raw.get("toolboxes") or []],
        "workspaces": [expand(value, REPO).absolute() for value in raw.get("workspaces") or []],
        "features": [str(name) for name in features] if isinstance(features, list) else [],
        "features_ok": isinstance(features, list),
        "domains": "domains" in raw,
    }


def manifest_errors(spec, has_features: bool = False) -> list:
    """What's wrong with a listed toolbox's manifest. A toolbox with feature folders may leave out `source` and `target`
    (it installs only those), but gives both or neither."""
    if not isinstance(spec, dict):
        return [f"{MANIFEST_NAME} must be a mapping"]
    if "domains" in spec:
        return [f"its {MANIFEST_NAME} still has `domains:`; move its one entry's keys (source, target, prefix, ignore, "
                "external) to the top level"]
    keys = ("source", "target")
    optional = has_features and not any(key in spec for key in keys)
    errors = [] if optional else \
        [f"{MANIFEST_NAME} needs `{key}`" for key in keys if not isinstance(spec.get(key), str)]
    unknown = sorted(set(spec) - TOOLBOX_KEYS)
    if unknown:
        errors.append(f"{MANIFEST_NAME} has unknown key(s): {', '.join(unknown)}")
    return errors


def has_feature_folders(repo: Path) -> bool:
    return (repo / CORE).is_dir() or (repo / FEATURES_DIR).is_dir()


def discover_features(repo: Path) -> tuple[list, list]:
    """([Feature], errors) for a toolbox repo's core and each folder under `features/`. A folder without a readable,
    valid feature.yml is an error."""
    config = import_config()
    found, errors = [], []
    for folder in config.feature_folders(repo, frozenset(ALWAYS_SKIP)):
        where = f"{folder.relative_to(repo).as_posix()}/{FEATURE_FILE}"
        name = config.feature_name(repo, folder)
        if folder.parent.name == FEATURES_DIR and folder.name == CORE:
            errors.append(f"{where}: `{CORE}` is the name of the always-installed folder, {CORE}/")
            continue
        if not (folder / FEATURE_FILE).is_file():
            errors.append(f"{where}: missing")
            continue
        try:
            spec = config.read_feature(load_yaml(folder / FEATURE_FILE), name)
        except (ValueError, yaml.YAMLError) as error:
            errors.append(f"{where}: {error}")
            continue
        if folder.parent == repo / CORE and spec["target"] != folder.name:
            errors.append(f"{where}: a feature in {CORE}/{folder.name}/ must have `target: {folder.name}`")
            continue
        found.append(Feature(name, folder, spec["target"], spec["description"], spec["needs"], spec["requires"]))
    return found, errors


def feature_parts(toolbox: str, repo: Path, spec: dict, problems: list) -> tuple[dict, set]:
    """(the toolbox repo's feature parts, names to hold). One part per scope that has a feature; an error in any
    feature.yml is a problem and holds both, so nothing it installed is removed until it's fixed."""
    names = {scope: f"{toolbox}:{scope}" for scope in SCOPES}
    found, errors = discover_features(repo)
    if errors:
        problems.append(f"toolbox {show(repo)}: " + "; ".join(errors) + ". Nothing installed or removed from its "
                        "feature folders until it's fixed.")
        return {}, set(names.values())
    parts = {}
    for scope in SCOPES:
        chosen = {feature.name: feature for feature in found if feature.target == scope}
        if chosen:
            part_spec = {
                "features": scope,
                "source": f"features-{scope}",  # not a folder: it names the part's managed git-exclude block
                "target": PER_WORKSPACE if scope == "workspace" else GLOBAL_TARGET,
                **{key: spec[key] for key in ("ignore", "prefix") if key in spec},
            }
            parts[names[scope]] = Part(names[scope], part_spec, repo, toolbox, chosen)
    return parts, set()


def load_parts(toolboxes: list, problems: list) -> tuple[dict, set]:
    """(every part this installer knows, the names of parts to leave as they are). The parts are this repo's feature
    folders' (one part per scope), then, for each listed toolbox, its source folder (named after it) and its feature
    folders'; a name taken twice is a problem, and the first wins. A toolbox whose manifest can't be read in the new
    shape is a problem, and everything it installed, under its name or its old domains' names, is held: neither
    installed nor removed until it's fixed."""
    parts, taken, held = {}, {REPO.name: REPO}, set()
    own_features, own_held = feature_parts(REPO.name, REPO, {"ignore": OWN_IGNORE}, problems)
    parts.update(own_features)
    held |= own_held
    for toolbox in toolboxes:
        if norm(toolbox) == norm(REPO):
            continue
        name, file, old = toolbox.name, toolbox / MANIFEST_NAME, toolbox / OLD_MANIFEST_NAME
        if not file.is_file():
            if old.is_file():
                problems.append(
                    f"toolbox {show(toolbox)}: rename its {OLD_MANIFEST_NAME} to {MANIFEST_NAME}, with its one "
                    "domain's keys (source, target, prefix, ignore, external) at the top level. Nothing installed or "
                    "removed from it until then."
                )
                old_domains = load_yaml(old).get("domains")
                held |= {name, *(old_domains if isinstance(old_domains, dict) else {})}
            else:
                problems.append(f"toolbox {show(toolbox)}: no {MANIFEST_NAME} there")
            continue
        spec = load_yaml(file)
        errors = manifest_errors(spec, has_feature_folders(toolbox))
        if errors:
            problems.append(f"toolbox {show(toolbox)}: " + "; ".join(errors) + ". Nothing installed or removed from it "
                            "until it's fixed.")
            old_domains = spec.get("domains") if isinstance(spec, dict) else None
            held |= {name, *(f"{name}:{scope}" for scope in SCOPES),
                     *(old_domains if isinstance(old_domains, dict) else {})}
            continue
        if name in taken:
            problems.append(f"toolbox {show(toolbox)}: the name {name} is taken by {show(taken[name])}; skipping it")
            continue
        taken[name] = toolbox
        repo = toolbox.resolve()
        if "source" in spec:
            parts[name] = Part(name, spec, repo, name)
        features, hold = feature_parts(name, repo, spec, problems)
        parts.update(features)
        held |= hold
    return parts, held


def hold_unmet_needs(parts: dict, names: list, workspaces: list, held: set, problems: list) -> list:
    """The workspaces whose picked features have the config they need. Each of the others gets a problem line and
    joins `held`: nothing is installed or removed there until its config gains the sections or drops the features."""
    config = import_config()
    needs = {}
    for name in names:
        if parts[name].per_workspace:
            for feature in parts[name].features.values():
                needs.setdefault(feature.name, []).extend(
                    key for key in feature.needs if key not in needs.get(feature.name, []))
    active = []
    for ws in workspaces:
        unmet = ws.config.unmet_needs(needs, always=(CORE,))
        if not unmet:
            active.append(ws)
            continue
        lacking = "; ".join(f"{feature} needs {', '.join(keys)}" for feature, keys in unmet.items())
        problems.append(
            f"workspace {show(ws.path)}: its features need config its {config.CONFIG_PATH} doesn't set ({lacking}), "
            "so nothing installed or removed there. Add it (/setup-workspace), or drop the feature."
        )
        held.add(ws.key)
    return active


def hold_unmet_requires(parts: dict, names: list, workspaces: list, picks: list, held: set, problems: list) -> list:
    """The workspaces that pick every feature their picked features require (a global feature picked in machine.yml
    counts, and core always does). Each of the others gets a problem line and joins `held`: nothing is installed or
    removed there until it picks them or drops the features."""
    requires = {}
    for name in names:
        if parts[name].scope == "workspace":
            for feature in parts[name].features.values():
                requires.setdefault(feature.name, []).extend(feature.requires)
    active = []
    for ws in workspaces:
        picked = {CORE, *picks, *ws.config.features}
        lacking = {feature: [need for need in dict.fromkeys(needed) if need not in picked]
                   for feature, needed in requires.items() if feature in picked}
        lacking = {feature: needed for feature, needed in lacking.items() if needed}
        if not lacking:
            active.append(ws)
            continue
        text = "; ".join(f"{feature} requires {', '.join(needed)}" for feature, needed in lacking.items())
        problems.append(
            f"workspace {show(ws.path)}: its picked features require features it doesn't pick ({text}), so nothing "
            "installed or removed there. Add them to its features (a global feature to machine.yml's), or drop the "
            "features that require them."
        )
        held.add(ws.key)
    return active


def hold_unmet_global_requires(parts: dict, names: list, picks: list, problems: list) -> set:
    """The names of feature parts of global features whose picked features require one machine.yml doesn't pick. Each
    gets a problem line; nothing is installed or removed from it until machine.yml picks them or drops the features."""
    picked, held = {CORE, *picks}, set()
    for name in names:
        if parts[name].scope != "global":
            continue
        lacking = {feature.name: [need for need in feature.requires if need not in picked]
                   for feature in parts[name].features.values() if feature.name in picked}
        lacking = {feature: needed for feature, needed in lacking.items() if needed}
        if lacking:
            text = "; ".join(f"{feature} requires {', '.join(needed)}" for feature, needed in lacking.items())
            problems.append(
                f"toolbox {name.split(':')[0]}: its picked global features require features {MACHINE.name} doesn't "
                f"pick ({text}), so nothing installed or removed from them. Add them to {MACHINE.name}'s features, or "
                "drop the features that require them."
            )
            held.add(name)
    return held


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
    """The manifest: `files` (each installed file's source and the hash written) and `dirs` (the folders made). State
    from before the manifest had `links` and `generated` sections; they become `files` entries without a hash, which
    the next apply replaces by copies and records."""
    state = {}
    if STATE.exists():
        with open(STATE, encoding="utf-8") as handle:
            state = json.load(handle)
    for section in ("files", "dirs", "links", "generated", "settings"):
        state.setdefault(section, {})
        for entry in state[section].values():
            if "domain" in entry:  # recorded before toolboxes replaced domains
                entry["part"] = entry.pop("domain")
    for key, entry in state.pop("links").items():
        state["files"].setdefault(key, {
            **{name: entry[name] for name in ("part", "workspace") if name in entry},
            "path": entry["target"], "source": entry["source"], "hash": None, "link": True,
        })
    for key, entry in state.pop("generated").items():
        state["files"].setdefault(key, {
            **{name: entry[name] for name in ("part", "workspace") if name in entry},
            "path": entry["path"], "source": entry.get("template"), "hash": None, "special": True,
        })
    return state


def save_state(state: dict) -> None:
    handle, temp = tempfile.mkstemp(dir=STATE.parent, prefix=f".{STATE.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump({"dirs": state["dirs"], "files": state["files"], "settings": state["settings"]}, out, indent=2,
                      sort_keys=True)
            out.write("\n")
        os.replace(temp, STATE)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------------------------------------------------------------
# Settings fragments
#
# A settings fragment is a JSON object. Its entries are what it adds to a settings file: each item of a list (a
# permission allow, a hook entry) is one entry, `{"path": [...], "item": value}`, and each other value is one entry,
# `{"path": [...], "set": value}`. Merging adds the entries the file lacks, in the fragment's order, and never changes
# a value the file already has: a list item the file holds already, or a key it already sets to the same value, adds
# nothing; a key it sets to something else keeps the file's value (a conflict, reported as a warning).


def canon(value) -> str:
    return json.dumps(value, sort_keys=True)


def fragment_entries(doc: dict, path: list = ()) -> list:
    """A fragment's entries, in file order. An empty mapping or list holds none."""
    entries = []
    for key, value in doc.items():
        here = [*path, key]
        if isinstance(value, dict):
            entries += fragment_entries(value, here)
        elif isinstance(value, list):
            entries += [{"path": here, "item": item} for item in value]
        else:
            entries.append({"path": here, "set": value})
    return entries


def find(doc: dict, path: list) -> tuple[str, object]:
    """(`found`, the value at path), (`missing`, None), or (`blocked`, None) when something above it isn't a mapping."""
    node = doc
    for key in path:
        if not isinstance(node, dict):
            return "blocked", None
        if key not in node:
            return "missing", None
        node = node[key]
    return "found", node


def entry_state(doc: dict, entry: dict) -> str:
    """`present` (the file has it), `absent` (it can be added) or `conflict` (the file has something else there)."""
    kind, node = find(doc, entry["path"])
    if kind == "blocked":
        return "conflict"
    if "item" in entry:
        if kind == "missing":
            return "absent"
        if not isinstance(node, list):
            return "conflict"
        return "present" if any(canon(item) == canon(entry["item"]) for item in node) else "absent"
    if kind == "missing":
        return "absent"
    return "present" if canon(node) == canon(entry["set"]) else "conflict"


def add_entry(doc: dict, entry: dict, containers: list) -> None:
    """Add an entry, making the mappings and the list above it as needed and noting each in `containers`."""
    path, node = entry["path"], doc
    for index, key in enumerate(path[:-1]):
        if key not in node:
            node[key] = {}
            containers.append(path[: index + 1])
        node = node[key]
    last = path[-1]
    if "item" in entry:
        if last not in node:
            node[last] = []
            containers.append(list(path))
        node[last].append(copy.deepcopy(entry["item"]))
    else:
        node[last] = copy.deepcopy(entry["set"])


def remove_entry(doc: dict, entry: dict) -> None:
    """Remove an entry the installer added, if it is still there as it added it."""
    kind, node = find(doc, entry["path"])
    if kind != "found":
        return
    if "item" in entry:
        if isinstance(node, list):
            for index, item in enumerate(node):
                if canon(item) == canon(entry["item"]):
                    del node[index]
                    return
    elif canon(node) == canon(entry["set"]):
        del find(doc, entry["path"][:-1])[1][entry["path"][-1]]


def prune(doc: dict, containers: list) -> list:
    """Remove the containers the installer made that are now empty; the paths of those that are left."""
    kept = []
    for path in sorted(containers, key=len, reverse=True):
        kind, node = find(doc, path)
        if kind != "found":
            continue
        if isinstance(node, (dict, list)) and not node:
            del find(doc, path[:-1])[1][path[-1]]
        else:
            kept.append(path)
    return sorted(kept)


def merge_settings(doc: dict, wanted: list, recorded: list, containers: list) -> tuple[dict, list, list, list]:
    """(the merged settings, the entries the installer owns in it, the containers it made, conflicting entries).
    `recorded` are the entries a past run added and `wanted` are the entries now: a recorded one no longer wanted is
    removed, a wanted one the file lacks is added, and everything else, the file's own entries above all, stays where it
    is."""
    doc = copy.deepcopy(doc)
    wanted = list({canon(entry): entry for entry in wanted}.values())
    wanted_keys = {canon(entry) for entry in wanted}
    recorded_keys = {canon(entry) for entry in recorded}
    for entry in recorded:
        if canon(entry) not in wanted_keys:
            remove_entry(doc, entry)
    containers = [list(path) for path in containers]
    ours, conflicts = [], []
    for entry in wanted:
        status = entry_state(doc, entry)
        if status == "conflict":
            conflicts.append(entry)
        elif status == "absent":
            add_entry(doc, entry, containers)
            ours.append(entry)
        elif canon(entry) in recorded_keys:
            ours.append(entry)
    return doc, ours, prune(doc, containers), conflicts


def conflict_text(entries: list) -> str:
    keys = dict.fromkeys(".".join(map(str, entry["path"])) for entry in entries)
    return f"{', '.join(keys)} already set to something else, so the fragment's value isn't used"


def dump_settings(doc: dict) -> bytes:
    return (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def read_fragment(feature: Feature) -> dict | None:
    """A feature folder's settings fragment, or None when it has none. ValueError says why it can't be used."""
    file = feature.folder / SETTINGS_FRAGMENT
    if not file.is_file():
        return None
    try:
        doc = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"{show(file)}: {error}") from error
    if not isinstance(doc, dict):
        raise ValueError(f"{show(file)}: must be a JSON object")
    return doc


def settings_owner(parts: dict, scope: str) -> str | None:
    """The part a scope's settings file is recorded under: the first feature part of that scope, this repo's first."""
    return next((name for name, part in parts.items() if part.scope == scope), None)


def fragments_for(parts: dict, held_parts: set, scope: str, picked: set, problems: list,
                  cache: dict) -> tuple[list, bool]:
    """(the settings fragments of `core` and each picked feature, across every feature part of the scope that isn't
    held; whether every one could be read). An unreadable one is a problem, said once through `cache`."""
    docs, ok = [], True
    for name, part in parts.items():
        if part.scope != scope or name in held_parts:
            continue
        for feature in part.features.values():
            if feature.name != CORE and feature.name not in picked:
                continue
            if feature.folder not in cache:
                try:
                    cache[feature.folder] = read_fragment(feature)
                except ValueError as error:
                    problems.append(f"{error}. The settings file it feeds is left as it is until it's fixed.")
                    cache[feature.folder] = False
            doc = cache[feature.folder]
            if doc is False:
                ok = False
            elif doc:
                docs.append(doc)
    return docs, ok


def plan_merges(parts: dict, names: list, scope: Scope, state: dict, picks: list, held_parts: set, problems: list,
                warnings: list) -> list:
    """The Merges into ~/.claude/settings.json: of the picked global features' fragments, and of the entries the
    manifest records adding for a part this run covers, which are removed when no longer wanted (a feature dropped, a
    teardown). The file is the user's, so a file that isn't a JSON object is a problem and left as it is."""
    owner = settings_owner(parts, "global")
    target, wanted, held = None, [], set()
    if owner in names:
        target = expand(parts[owner].spec["target"]) / SETTINGS_FILE
        if target.parent.is_dir():
            docs, ok = fragments_for(parts, held_parts, "global", set(picks), problems, {})
            wanted = [entry for doc in docs for entry in fragment_entries(doc)]
            if not ok:
                held.add(norm(target))
        else:
            target = None
    records = {key: record for key, record in state["settings"].items() if scope.covers(record["part"], None)}
    keys = {key for key in records if key not in held}
    if target is not None and norm(target) not in held:
        keys.add(norm(target))
    merges = []
    for key in sorted(keys):
        record = records.get(key)
        path = Path(record["path"]) if record else target
        real = path.resolve() if path.is_symlink() else path
        before, text = {}, None
        if real.exists():
            try:
                text = real.read_text(encoding="utf-8")
                before = json.loads(text)
                if not isinstance(before, dict):
                    raise ValueError("not a JSON object")
            except (OSError, ValueError) as error:
                problems.append(f"{show(path)}: can't merge settings into it, so it is left as it is: {error}")
                continue
        recorded = record["added"] if record else []
        entries = wanted if target is not None and key == norm(target) else []
        after, ours, containers, conflicts = merge_settings(before, entries, recorded,
                                                            record["created"] if record else [])
        if conflicts:
            warnings.append(f"{show(path)}: {conflict_text(conflicts)}")
        ours_keys = {canon(entry) for entry in ours}
        recorded_keys = {canon(entry) for entry in recorded}
        file_created = bool(record and record.get("file_created")) or (text is None and bool(ours))
        delete = file_created and text is not None and not after
        changed = canon(after) != canon(before)
        merges.append(Merge(
            key, record["part"] if record else owner, path, real,
            dump_settings(after) if changed and not delete and (after or text is not None) else None,
            text is None, delete, ours, containers, file_created,
            adds=sum(canon(entry) not in recorded_keys for entry in ours),
            removes=sum(entry_state(before, entry) == "present" and entry_state(after, entry) == "absent"
                        for entry in recorded if canon(entry) not in ours_keys),
        ))
    return merges


def merged_fragments(docs: list) -> tuple[dict, list]:
    """(one settings document holding every fragment, conflicting entries). The first fragment to set a key wins."""
    entries = [entry for doc in docs for entry in fragment_entries(doc)]
    doc, _, _, conflicts = merge_settings({}, entries, [], [])
    return doc, conflicts


# ---------------------------------------------------------------------------------------------------------------------
# Desired state


def own_part(parts: dict, names: list, per_workspace: bool) -> str | None:
    """The first of this repo's own parts in `names` that installs into every workspace (`per_workspace`) or into
    ~/.claude: the part that records the generated rule, or the pointer to this repo."""
    return next((name for name in names if parts[name].toolbox == REPO.name
                 and parts[name].per_workspace == per_workspace), None)


def is_skill_dir(path: Path) -> bool:
    return any((path / name).is_file() for name in SKILL_FILES)


def read_source(part: str, workspace: str | None, source: Path, target: Path, items: list, held: list,
                problems: list, **fields) -> None:
    """Add the file at `source` as an item for `target`, or report why it can't be read."""
    try:
        data = source.read_bytes()
        executable = is_executable(source)
    except OSError as error:
        problems.append(f"{show(source)}: can't read, so {show(target)} is left as it is: {error}")
        held.append((part, workspace, target))
        return
    items.append(Item(part, workspace, target, data, source, executable=executable, **fields))


def walk(part: str, src_root: Path, tgt_root: Path, ignore: list, skip, workspace, items: list, dirs: list,
         skills: list, held: list, problems: list, templates: list | None = None, rules: list | None = None,
         rel: str = "") -> None:
    """Collect a part's files, folders and skill folders. With `templates` and `rules` (a per-workspace part), each
    .tmpl file goes in `templates` as (source, target without the suffix), and each other rule in its rules folder goes
    in `rules` as (source, target), to be rendered or noted instead of copied as they are. `rel` is where src_root sits
    within the part's mirror (`.claude` for a feature folder's `.claude/` tree), for the ignore globs and rules
    folder."""
    suffix = import_templates().SUFFIX if templates is not None else None

    def visit(src: Path, tgt: Path, rel: str, in_skill: bool) -> None:
        for entry in sorted(src.iterdir()):
            relpath = f"{rel}/{entry.name}" if rel else entry.name
            if entry.name in ALWAYS_SKIP or any(fnmatch.fnmatch(relpath, pattern) for pattern in ignore):
                continue
            if skip(relpath):
                continue
            target = tgt / entry.name
            if entry.is_dir():
                dirs.append((part, workspace, target))
                skill = in_skill or is_skill_dir(entry)
                if skill and not in_skill:
                    skills.append((part, entry))
                visit(entry, target, relpath, skill)
            elif suffix and entry.is_file() and entry.name.endswith(suffix) and entry.name != suffix:
                templates.append((entry, tgt / entry.name[: -len(suffix)]))
            elif rules is not None and entry.is_file() and entry.suffix == ".md" \
                    and relpath.startswith(RULES_DIR + "/"):
                rules.append((entry, target))
            else:
                read_source(part, workspace, entry, target, items, held, problems, in_skill=in_skill)

    visit(src_root, tgt_root, rel, False)


def parents_within(path: Path, root: Path) -> list:
    """The folders between root and path, outermost first."""
    chain, parent = [], path.parent
    while parent != root and root in parent.parents:
        chain.append(parent)
        parent = parent.parent
    return list(reversed(chain))


def render_template(part: str, ws: Workspace, template: Path, path: Path, items: list, held: list,
                    problems: list) -> None:
    """Render one template with a workspace's config into `items`, or report why it can't be."""
    templates = import_templates()
    try:
        text = templates.render(template.read_text(encoding="utf-8"), ws.config)
        executable = is_executable(template)
    except (OSError, UnicodeDecodeError, templates.TemplateError) as error:
        reasons = error.errors if isinstance(error, templates.TemplateError) else [str(error)]
        problems.append(
            f"workspace {show(ws.path)}: can't render {show(path)} from {template.name}, so it is left as it is: "
            + "; ".join(reasons)
        )
        held.append((part, ws.key, path))
        return
    items.append(Item(part, ws.key, path, text.encode("utf-8"), template, RENDERED, executable))


def noted_rule(text: str, source: Path) -> str:
    """A rule's text with the generated note where toolbox-config.md has its note: after the frontmatter and title."""
    note = f"Generated from `{show(source)}` on every toolbox sync; edits here are lost. Change the source and sync."
    match = RULE_FRONTMATTER.match(text)
    head = text[: match.end()].rstrip("\n") + "\n\n" if match else ""
    body = text[match.end() if match else 0:].lstrip("\n")
    title = ""
    if body.startswith("# "):
        title, _, body = body.partition("\n")
        title, body = title + "\n\n", body.lstrip("\n")
    return head + title + note + "\n" + ("\n" + body if body else "")


def copy_rule(part: str, ws: Workspace, source: Path, path: Path, items: list, held: list, problems: list) -> None:
    """Copy one rule, with its generated note, into `items`, or report why it can't be."""
    try:
        text = source.read_text(encoding="utf-8")
        executable = is_executable(source)
    except (OSError, UnicodeDecodeError) as error:
        problems.append(f"workspace {show(ws.path)}: can't copy {show(path)} from {source.name}, so it is left as it "
                        f"is: {error}")
        held.append((part, ws.key, path))
        return
    items.append(Item(part, ws.key, path, noted_rule(text, source).encode("utf-8"), source, RULE, executable))


def vault_files(part: str, src_root: Path, ws: Workspace, ignore: list, skip, dirs: list, items: list,
                held: list, problems: list) -> None:
    """Collect the part's vault folder for one workspace: each file rendered or copied into the workspace's vault, at
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
    if found and vault is None:
        problems.append(f"workspace {show(ws.path)}: no vault in its config, so the vault files aren't written")
        return
    if found and not vault.is_dir():
        problems.append(f"workspace {show(ws.path)}: vault {show(vault)} doesn't exist, so nothing is written there")
        return
    suffix = import_templates().SUFFIX
    for entry, rel in found:
        target = vault / rel
        dirs.extend((part, ws.key, folder) for folder in parents_within(target, vault))
        if entry.name.endswith(suffix) and entry.name != suffix:
            render_template(part, ws, entry, target.with_name(entry.name[: -len(suffix)]), items, held, problems)
            continue
        read_source(part, ws.key, entry, target, items, held, problems, kind=VAULT)


def install_feature(name: str, feature: Feature, root: Path, ws: Workspace | None, ignore: list, items: list,
                    dirs: list, skills: list, held: list, problems: list) -> None:
    """Collect one feature folder's files: its `.claude/` tree into `root` (a workspace's .claude, or ~/.claude), and,
    for a workspace feature (`ws`), its `.vault/` tree into the workspace's vault. Templates and rules are rendered and
    noted as in any per-workspace part."""
    tree = feature.folder / ".claude"
    key = ws.key if ws else None
    if tree.is_dir():
        found = [] if ws else None
        rules = [] if ws else None
        walk(name, tree, root, ignore, lambda relpath: False, key, items, dirs, skills, held, problems, found, rules,
             ".claude")
        for template, path in found or []:
            render_template(name, ws, template, path, items, held, problems)
        for source, path in rules or []:
            copy_rule(name, ws, source, path, items, held, problems)
    if ws:
        vault_files(name, feature.folder, ws, ignore, lambda relpath: False, dirs, items, held, problems)


def desired_features(name: str, part: Part, workspaces: list, picks: list, problems: list, warnings: list, items: list,
                     dirs: list, skills: list, held: list) -> None:
    """Collect the files of a feature part: for each workspace, core and each feature its config lists; for the global
    scope, core and each feature `picks` (machine.yml's) names."""
    ignore = part.spec.get("ignore") or []
    if part.scope == "workspace":
        schema_has = import_config().schema_has
        for feature in part.features.values():
            where = (feature.folder / FEATURE_FILE).relative_to(part.repo).as_posix()
            warnings.extend(f"{part.toolbox}/{where}: needs {key}, which isn't a config key"
                            for key in feature.needs if not schema_has(key))
        for ws in workspaces:
            for feature in part.features.values():
                if feature.name == CORE or feature.name in ws.config.features:
                    install_feature(name, feature, ws.path / ".claude", ws, ignore, items, dirs, skills, held, problems)
        return
    target = expand(part.spec["target"])
    if not target.is_dir():
        problems.append(f"{name}: target folder {show(target)} doesn't exist. Create or clone it first.")
        return
    for feature in part.features.values():
        if feature.name == CORE or feature.name in picks:
            install_feature(name, feature, target, None, ignore, items, dirs, skills, held, problems)


def desired(parts: dict, names: list, workspaces: list, problems: list, warnings: list, held: list | None = None,
            missing: list | None = None, picks: list = (), held_parts: set = frozenset()):
    """(items, folders, skill folders). A file that can't be read or a template that fails to render is a problem, and
    its target goes in `held` as (part, workspace, path): neither written nor removed. An outside source that doesn't
    exist goes in `missing` as (part, workspace, target, source)."""
    items, dirs, skills = [], [], []
    held = [] if held is None else held
    missing = [] if missing is None else missing
    for name in names:
        part = parts[name]
        spec = part.spec
        if part.scope:
            desired_features(name, part, workspaces, picks, problems, warnings, items, dirs, skills, held)
            continue
        src_root = part.source
        if not src_root.is_dir():
            problems.append(f"{name}: source folder {show(src_root)} doesn't exist")
            continue
        tgt_root = expand(spec["target"])
        if not tgt_root.is_dir():
            problems.append(f"{name}: target folder {show(tgt_root)} doesn't exist. Create or clone it first.")
            continue
        walk(name, src_root, tgt_root, spec.get("ignore") or [], lambda relpath: False, None, items, dirs, skills, held,
             problems)
        for external in spec.get("external") or []:
            target = tgt_root / external["target"]
            source = expand(external["source"])
            dirs.extend((name, None, folder) for folder in parents_within(target, tgt_root))
            if source.is_dir():
                dirs.append((name, None, target))
                walk(name, source, target, [], lambda relpath: False, None, items, dirs, skills, held, problems)
            elif source.exists():
                read_source(name, None, source, target, items, held, problems)
            else:
                missing.append((name, None, target, source))
                held.append((name, None, target))

    rule_part = own_part(parts, names, True)
    if rule_part and workspaces:
        config = import_config()
        for ws in workspaces:
            path = ws.path / config.RULE_PATH
            dirs.extend((rule_part, ws.key, folder) for folder in parents_within(path, ws.path))
            items.append(Item(rule_part, ws.key, path, config.render_rule(ws.config).encode("utf-8"), None, GENERATED))
            if TASKS_FEATURE in ws.config.features and ws.config.vault_dir is not None:
                vault = ws.config.vault_dir
                if not vault.is_dir():
                    problem = (f"workspace {show(ws.path)}: vault {show(vault)} doesn't exist, so nothing is written "
                               "there")
                    if problem not in problems:  # vault_files may have said so already
                        problems.append(problem)
                    continue
                for folder in (ws.config.tasks_dir, ws.config.discussions_dir):
                    dirs.extend((rule_part, ws.key, d) for d in [*parents_within(folder, vault), folder])

    owner = settings_owner(parts, "workspace")
    if owner in names:
        cache = {}
        for ws in workspaces:
            docs, ok = fragments_for(parts, held_parts, "workspace", set(ws.config.features), problems, cache)
            path = ws.path / ".claude" / SETTINGS_FILE
            if not ok:
                held.append((owner, ws.key, path))
                continue
            doc, conflicts = merged_fragments(docs)
            if conflicts:
                warnings.append(f"workspace {show(ws.path)}: {conflict_text(conflicts)}")
            if doc:
                dirs.extend((owner, ws.key, folder) for folder in parents_within(path, ws.path))
                items.append(Item(owner, ws.key, path, dump_settings(doc), None, GENERATED))

    pointer_part = own_part(parts, names, False)
    if pointer_part:
        target = expand(parts[pointer_part].spec["target"])
        if target.is_dir():
            items.append(Item(pointer_part, None, target / POINTER_NAME, f"{REPO}\n".encode("utf-8"), None, GENERATED))

    unique_dirs = {}
    for part, workspace, folder in dirs:
        unique_dirs.setdefault(norm(folder), (part, workspace, folder))
    return items, list(unique_dirs.values()), skills


# ---------------------------------------------------------------------------------------------------------------------
# Checks


def frontmatter_name(markdown: Path) -> str | None:
    text = markdown.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    match = FRONTMATTER_NAME.search(text[3:] if end == -1 else text[3:end])
    return match.group(1).strip() if match else None


def name_warnings(source: Path, part: Part) -> list:
    """Warnings for one skill folder, agent or rule file of a toolbox: its frontmatter name and the toolbox's prefix."""
    if not source.is_relative_to(part.repo):
        return []
    parts = source.relative_to(part.repo).parts
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
    prefix = part.spec.get("prefix")
    if prefix and not name.startswith(prefix):
        warnings.append(f"{kind} {where}: missing the toolbox's prefix {prefix!r}")
    return warnings


def feature_warnings(parts: dict, names: list, workspaces: list, picks: list = ()) -> list:
    """Features a workspace's config names that no workspace feature folder it installs from is named for (or that is a
    global feature, picked in machine.yml instead), and global features machine.yml picks that no global feature
    folder is named for."""
    known, global_known = set(), set()
    for name in names:
        if parts[name].scope == "global":
            global_known |= set(parts[name].features)
        elif parts[name].per_workspace:
            known |= set(parts[name].features)
    warnings = []
    for ws in workspaces:
        for feature in ws.config.features:
            if feature in known:
                continue
            if feature in global_known:
                warnings.append(f"workspace {show(ws.path)}: feature {feature!r} is a global feature, picked in "
                                f"{MACHINE.name}, so listing it here does nothing")
            else:
                warnings.append(f"workspace {show(ws.path)}: feature {feature!r} isn't a feature folder of any "
                                "toolbox, so it gates nothing")
    for feature in picks:
        if feature not in global_known:
            kind = "a workspace feature, listed in a workspace's config" if feature in known else \
                "a feature of any global feature folder it installs from"
            warnings.append(f"{MACHINE.name}: feature {feature!r} isn't {kind}, so it installs nothing")
    return warnings


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


def own_link(path: Path, key: str, source: Path | None, state: dict, owned: set) -> bool:
    """Whether a link at `path` is one of ours: into a toolbox repo, to the source this file would be copied from, or
    recorded in the manifest as a link this installer made."""
    dest = link_dest(path)
    entry = state["files"].get(key)
    return inside(dest, owned) or (source is not None and dest == norm(source)) \
        or bool(entry and entry.get("link") and dest == norm(entry["source"]))


def classify(item: Item, counts: dict, blocked: list, replacing: list, state: dict, owned: set) -> tuple[str, str]:
    """(status, detail): `create`, `update`, `unchanged`, or a conflict kind. The detail says why an update happens
    (`replaces a link`, `changed`), and where a foreign link points."""
    key = norm(item.target)
    path = item.target
    if counts[key] > 1:
        return "collision", ""
    if any(key.startswith(folder + os.sep) for folder in blocked):
        return "blocked", ""
    if any(key.startswith(folder + os.sep) for folder in replacing):
        return "create", ""  # its folder is one of our links, and becomes a real folder before this is written
    if path.is_symlink():
        if own_link(path, key, item.source, state, owned):
            return "update", "replaces a link"
        return "foreign", link_dest(path)
    if not path.exists():
        return "create", ""
    if not path.is_file():
        return "folder", ""
    disk = disk_digest(path)
    if disk is None:
        return "unreadable", ""
    entry = state["files"].get(key)
    if disk == digest(item.data):
        return ("unchanged", "") if is_executable(path) == item.executable or os.name == "nt" else ("update", "changed")
    if entry is None or entry.get("link"):  # never written by this installer: a real file replaced a link of ours
        return "unmanaged", ""
    if entry.get("hash") in (None, disk):  # still what was written (or written before hashes were kept)
        return "update", "changed"
    return "edited", ""


def build_plan(parts: dict, names: list, scope: Scope, state: dict, workspaces: list, problems: list,
               known: set = frozenset(), picks: list = ()) -> Plan:
    warnings, held, missing = [], [], []
    items, dirs, skills = desired(parts, names, workspaces, problems, warnings, held, missing, picks, known)
    plan = Plan(
        [], [], [], [], [], [], [], [], [], [], warnings, problems, set(), names, dirs, set(parts) | known,
        [ws.path for ws in workspaces], scope, held,
    )
    owned = {norm(REPO)} | {norm(part.repo) for part in parts.values()}

    blocked, replacing = [], []
    for part, workspace, folder in dirs:
        key = norm(folder)
        if any(key.startswith(link + os.sep) for link in replacing):
            plan.create_dirs.append((part, workspace, folder))  # beneath a link that is replaced first
        elif folder.is_symlink():
            entry = state["files"].get(key)
            dest = link_dest(folder)
            if inside(dest, owned) or (entry and entry.get("link") and dest == norm(entry["source"])):
                plan.create_dirs.append((part, workspace, folder))
                plan.replace_dirs.append((part, workspace, folder))
                replacing.append(key)
            else:
                plan.conflicts.append(("dir-conflict", folder, None))
                blocked.append(key)
        elif folder.exists() and not folder.is_dir():
            plan.conflicts.append(("dir-conflict", folder, None))
            blocked.append(key)
        elif not folder.exists():
            plan.create_dirs.append((part, workspace, folder))
        plan.keep_dirs.add(key)

    counts = {}
    for target in [item.target for item in items] + [path for _, _, path in held]:
        counts[norm(target)] = counts.get(norm(target), 0) + 1

    for part_name, folder in skills:
        plan.warnings += name_warnings(folder, parts[part_name])
    for item in items:
        if item.part in parts and not item.special and not item.in_skill and item.source is not None:
            plan.warnings += name_warnings(item.source, parts[item.part])
        status, detail = classify(item, counts, blocked, replacing, state, owned)
        shown = status
        if status == "unchanged":
            plan.unchanged.append(item)
            shown = "ok"
        elif status == "create":
            plan.create.append(item)
            shown = "new"
        elif status == "update":
            plan.update.append((item, detail))
            shown = detail
        else:
            if status == "edited":
                plan.edited.append(item)
            other = Path(detail) if status == "foreign" else item.source
            plan.conflicts.append((status, item.target, other))
            shown = f"conflict: {CONFLICT_REASONS[status]}"
        if item.special and item.source is not None:
            plan.rendered.append({"path": str(item.target), "template": str(item.source), "status": shown})
    for part, workspace, target, source in missing:
        plan.conflicts.append(("missing-source", target, source))
    for _, _, path in held:
        if not any(path == target for _, _, target, _ in missing):
            plan.rendered.append({"path": str(path), "template": None, "status": "can't render; see Problems"})

    wanted = {norm(item.target) for item in items} | {norm(path) for _, _, path in held} \
        | {norm(folder) for _, _, folder in dirs}
    for key, entry in state["files"].items():
        if not scope.covers(entry["part"], entry.get("workspace")) or key in wanted:
            continue
        path = Path(entry["path"])
        if path.is_symlink():
            if entry.get("link") and link_dest(path) == norm(entry["source"]):
                plan.remove.append((key, entry))
            else:
                plan.forget.append(key)
        elif path.is_file() and entry.get("link"):
            plan.forget.append(key)  # a real file replaced the link; it was never ours to remove
        elif path.is_file():
            disk = disk_digest(path)
            if entry["hash"] is None or disk == entry["hash"]:
                plan.remove.append((key, entry))
                if entry.get("special") and entry.get("source"):
                    plan.rendered.append({"path": entry["path"], "template": entry["source"], "status": "stale"})
            else:
                plan.conflicts.append(("edited-stale", path, None))
        else:
            plan.forget.append(key)

    plan.merges = plan_merges(parts, names, scope, state, picks, known, problems, warnings)
    plan.excludes = plan_excludes(parts, scope, state, workspaces, plan)
    plan.warnings += feature_warnings(parts, names, workspaces, picks)
    plan.warnings = list(dict.fromkeys(plan.warnings))
    return plan


def plan_excludes(parts: dict, scope: Scope, state: dict, workspaces: list, plan: Plan) -> list:
    """One managed block per toolbox, part and git target: the part's installed files (including any left in place
    because they were edited or failed to render), plus, for the rule part, each workspace's config file."""
    installed = [(item.part, item.workspace, item.target)
                 for item in plan.unchanged + plan.create + [item for item, _ in plan.update] + plan.edited]
    installed += [(part, workspace, path) for part, workspace, path in plan.held_generated if path.is_file()]
    active = {ws.key: ws for ws in workspaces}

    roots = []  # (part, workspace key or None, root folder, marker)
    for name in sorted(scope.parts & set(parts)):
        part = parts[name]
        if not part.per_workspace:
            roots.append((name, None, expand(part.spec["target"]), part.marker))
            continue
        keys = set(active) | {
            record["workspace"]
            for record in state["files"].values()
            if record["part"] == name and record.get("workspace") and scope.covers(name, record["workspace"])
        }
        roots += [(name, key, active[key].path if key in active else Path(key), part.marker)
                  for key in sorted(keys)]
    # The parts this repo's retired folders were recorded under hold no files now: their exclude blocks are emptied.
    for name in RETIRED_PARTS:
        if name in parts or name not in scope.parts:
            continue
        marker = f"{REPO.name}:{name}"
        keys = {record["workspace"] for record in state["files"].values()
                if record["part"] == name and record.get("workspace") and scope.covers(name, record["workspace"])}
        roots += [(name, key, active[key].path if key in active else Path(key), marker) for key in sorted(keys)]
        if any(record["part"] == name and not record.get("workspace") for record in state["files"].values()):
            roots.append((name, None, expand(GLOBAL_TARGET), marker))

    config = import_config() if any(workspace for _, workspace, _, _ in roots) else None
    rule_part = own_part(parts, list(parts), True)
    texts = {}  # exclude file: [its text with every block so far, entries across them]: parts share a file
    for name, workspace, root, marker in roots:
        found = git_exclude(root) if root.is_dir() else None
        if found is None:
            continue
        top, exclude_file = found

        def entry(path) -> str:
            return "/" + Path(os.path.relpath(norm(path), norm(top))).as_posix()

        # A vault outside the workspace's repo (work's lives elsewhere on disk) has nothing to exclude here.
        entries = {entry(path) for part, key, path in installed
                   if part == name and key == workspace and inside(norm(path), {norm(top)})}
        if name == rule_part and workspace in active:
            entries.add(entry(root / config.CONFIG_PATH))
        if entries:
            entries.add(PYCACHE_EXCLUDE)
        if exclude_file not in texts:
            texts[exclude_file] = [exclude_file.read_text(encoding="utf-8") if exclude_file.exists() else "", 0]
        texts[exclude_file][0] = render_exclude(texts[exclude_file][0], marker, sorted(entries))
        texts[exclude_file][1] += len(entries)
    return [
        (file, updated, count) for file, (updated, count) in texts.items()
        if updated != (file.read_text(encoding="utf-8") if file.exists() else "")
    ]


# ---------------------------------------------------------------------------------------------------------------------
# Applying


def record(fields: dict, workspace: str | None) -> dict:
    return {**fields, "workspace": workspace} if workspace else fields


def apply_plan(plan: Plan, state: dict) -> list:
    errors = []
    replaced = {norm(folder) for _, _, folder in plan.replace_dirs}
    failed = []  # folders that couldn't be made: nothing is written beneath them, or through a link still there
    for part, workspace, folder in plan.create_dirs:
        if any(norm(folder).startswith(bad + os.sep) for bad in failed):
            continue
        try:
            if norm(folder) in replaced:
                folder.unlink()  # the link goes before its folder is made, so nothing is ever written through it
                state["files"].pop(norm(folder), None)
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            errors.append(f"{show(folder)}: {error}")
            failed.append(norm(folder))
            continue
        state["dirs"][norm(folder)] = record({"path": str(folder), "part": part}, workspace)
    # A folder recorded under a name no toolbox has any more (a retired domain's) belongs to the part that wants it now.
    for part, workspace, folder in plan.dirs:
        entry = state["dirs"].get(norm(folder))
        if entry and entry["part"] not in plan.known:
            state["dirs"][norm(folder)] = record({"path": entry["path"], "part": part}, workspace)
    for key, entry in plan.remove:
        try:
            Path(entry["path"]).unlink()
        except OSError as error:
            errors.append(f"{show(entry['path'])}: {error}")
            continue
        del state["files"][key]
    for key in plan.forget:
        del state["files"][key]
    for item in plan.create + [item for item, _ in plan.update]:
        if any(norm(item.target).startswith(bad + os.sep) for bad in failed):
            continue
        try:
            write_atomic(item.target, item.data, item.executable)
        except OSError as error:
            errors.append(f"{show(item.target)}: {error}")
    for item in plan.unchanged + plan.create + [item for item, _ in plan.update]:
        if item.target.is_file() and not item.target.is_symlink() and disk_digest(item.target) == digest(item.data):
            state["files"][norm(item.target)] = record({
                "path": str(item.target), "part": item.part, "source": str(item.source) if item.source else None,
                "hash": digest(item.data), **({"special": True} if item.special else {}),
            }, item.workspace)
    for merge in plan.merges:
        try:
            if merge.delete:
                merge.real.unlink(missing_ok=True)
            elif merge.data is not None:
                mode = stat.S_IMODE(merge.real.stat().st_mode) if merge.real.exists() else 0o644
                write_atomic(merge.real, merge.data, False, mode)
        except OSError as error:
            errors.append(f"{show(merge.path)}: {error}")
            continue
        if merge.record:
            state["settings"][merge.key] = merge.record
        else:
            state["settings"].pop(merge.key, None)
    for exclude_file, text, _ in plan.excludes:
        exclude_file.parent.mkdir(parents=True, exist_ok=True)
        exclude_file.write_text(text, encoding="utf-8")
    # Remove folders this installer created that are no longer wanted and are now empty, deepest first.
    for key in sorted(state["dirs"], key=len, reverse=True):
        entry = state["dirs"][key]
        folder = Path(entry["path"])
        if not plan.scope.covers(entry["part"], entry.get("workspace")) or key in plan.keep_dirs:
            continue
        if folder.is_dir() and not folder.is_symlink():
            for cache in folder.glob("__pycache__"):  # left by running an installed script; never ours to keep
                if cache.is_dir() and not cache.is_symlink():
                    shutil.rmtree(cache, ignore_errors=True)
        if folder.is_dir() and not folder.is_symlink() and not any(folder.iterdir()):
            folder.rmdir()
        if not folder.exists():
            del state["dirs"][key]
    save_state(state)
    return errors


# ---------------------------------------------------------------------------------------------------------------------
# Reporting


def made_by(item: Item) -> str:
    """`generated` (from the config), `rendered` (from a template) or `copied`."""
    return item.kind if item.kind in (GENERATED, RENDERED) else "copied"


def made_from(item: Item) -> str:
    return f", {made_by(item)} from {item.source.name}" if item.source else ""


def toolbox_of(part: str) -> str:
    """The toolbox a part belongs to: a listed toolbox's source part is named for it, its feature parts `name:scope`."""
    return part.split(":")[0]


def describe_toolboxes(names: list, known: set = frozenset()) -> str:
    """The toolboxes a run installs, with this repo's parts named when the run installs only some of them."""
    own = [name for name in names if toolbox_of(name) == REPO.name]
    every = {name for name in known if toolbox_of(name) == REPO.name}
    if own and every <= set(own):
        shown = [REPO.name]
    elif own:
        shown = [f"{REPO.name} ({', '.join(dict.fromkeys(name.split(':')[-1] for name in own))})"]
    else:
        shown = []
    return ", ".join(shown + list(dict.fromkeys(toolbox_of(name) for name in names if name not in own)))


def merge_line(merge: Merge) -> str:
    if merge.delete:
        return f"{show(merge.path)} (removes the file, which only the installer's entries were in)"
    verb = "new file" if merge.created else "merge"
    return f"{show(merge.path)} ({verb}: +{merge.adds} -{merge.removes} entries)"


def plan_sections(plan: Plan, errors: list) -> list:
    replaced = {norm(folder) for _, _, folder in plan.replace_dirs}
    return [
        ("Create folders", [show(folder) for _, _, folder in plan.create_dirs if norm(folder) not in replaced]),
        ("Replace links", [show(folder) for _, _, folder in plan.replace_dirs]),
        ("Create", [f"{show(item.target)} (new{made_from(item)})" for item in plan.create]),
        ("Update", [f"{show(item.target)} ({detail}{made_from(item)})" for item, detail in plan.update]),
        ("Remove", [show(entry["path"]) for _, entry in plan.remove]),
        ("Merge settings", [merge_line(merge) for merge in plan.merges if merge.changes]),
        ("Update git excludes", [f"{show(path)} ({count} entries)" for path, _, count in plan.excludes]),
        ("Unchanged", [f"{show(item.target)} ({made_by(item)})" for item in plan.unchanged]
         + [f"{show(merge.path)} (merged settings)" for merge in plan.merges if not merge.changes and merge.added]),
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
PENDING = ("Create folders", "Replace links", "Create", "Update", "Remove", "Merge settings", "Update git excludes",
           "Conflicts, left alone", "Problems", "Errors")
# Sections the summary on screen lists item by item when they are short; the plan file always lists every one.
LISTED = ("Create", "Update", "Remove", "Merge settings", "Conflicts, left alone", "Warnings", "Problems", "Errors")
LISTED_MOST = 20


def report_json(plan: Plan, listed: bool) -> None:
    """The preview as JSON, for `toolbox.py doctor`: each section's items, whether anything is pending, whether
    machine.yml lists every workspace acted on, and each rendered, vault-copied or rule file's status (`ok`, `new`,
    `changed`, `replaces a link`, `stale`, a conflict, or can't render). Writes no plan file."""
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
        f"Toolboxes: {describe_toolboxes(plan.parts, plan.known) or '(none)'}",
        f"Workspaces: {', '.join(show(path) for path in plan.workspaces) or '(none)'}",
    ]
    with open(PLAN, "w", encoding="utf-8") as handle:
        handle.write("\n".join(header) + "\n")
        for title, items in sections:
            handle.write(f"\n== {title} ({len(items)})\n")
            handle.writelines(f"  {item}\n" for item in items)

    print("\n".join(header) + "\n")
    for title, items in sections:
        if items or title in ("Create", "Conflicts, left alone"):
            print(f"  {title}: {len(items)}")
    for title in LISTED:
        items = dict(sections)[title]
        if items and (len(items) <= LISTED_MOST or title not in ("Create", "Update", "Remove")):
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


def select(values: list, parts: dict, held: set) -> list:
    """The parts the --toolbox values name: a toolbox's folder name or path. This repo stands for both its folders."""
    toolboxes = {}  # folder name or path: part names
    for name, part in parts.items():
        for key in (REPO.name, norm(REPO)) if part.toolbox == REPO.name else (part.toolbox, norm(part.repo)):
            toolboxes.setdefault(key, []).append(name)
    chosen = []
    for value in values:
        names = toolboxes.get(value) or toolboxes.get(norm(expand(value).absolute()))
        if names is None:
            known = ", ".join(dict.fromkeys([REPO.name] + [part.toolbox for part in parts.values()]))
            fixing = value in map(toolbox_of, held)
            reason = "its manifest needs fixing first; run install.py to see why" if fixing else \
                f"not a listed toolbox. Known toolboxes: {known}"
            sys.exit(f"--toolbox {value}: {reason}")
        chosen += [name for name in names if name not in chosen]
    return chosen


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="make the changes (default: preview only)")
    parser.add_argument("--teardown", action="store_true", help="remove what this installer installed instead")
    parser.add_argument(
        "--toolbox",
        action="append",
        default=[],
        help="act on this toolbox only, by folder name or path, ignoring the others machine.yml lists (repeatable)",
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
    if machine["domains"]:
        problems.append(
            f"{MACHINE.name}: remove `domains:`, which is no longer read. Every toolbox under `toolboxes:` installs "
            f"whole, and {REPO.name}'s core always installs and its global features are picked by `features:`."
        )
    if not machine["features_ok"]:
        problems.append(f"{MACHINE.name}: `features:` must be a list of global feature names")
    picks = machine["features"]
    parts, held_parts = load_parts(machine["toolboxes"], problems)
    state = load_state()
    per_workspace = [name for name, part in parts.items() if part.per_workspace]
    chosen = select(args.toolbox, parts, held_parts) if args.toolbox else None

    if args.workspace:
        chosen = [name for name in chosen if name in per_workspace] if chosen is not None else per_workspace
        if not chosen:
            sys.exit(f"--workspace acts only on what installs into every workspace: {REPO.name}'s "
                     f"{', '.join(per_workspace) or '(none)'}")

    if args.teardown:
        names = []
    elif chosen is not None:
        names = chosen
    else:
        if not MACHINE.exists():
            sys.exit(
                f"No {MACHINE.name} on this machine. Copy {MACHINE_EXAMPLE.name} to {MACHINE.name} and list the "
                f"toolboxes and workspaces to install here.\n  {MACHINE_EXAMPLE}"
            )
        names = list(parts)

    held_parts |= hold_unmet_global_requires(parts, names, picks, problems)
    names = [name for name in names if name not in held_parts]
    if chosen is not None:
        scope_parts = set(chosen)
    else:
        scope_parts = set(parts) | {entry["part"] for section in ("files", "dirs", "settings")
                                  for entry in state[section].values()}
    scope_parts -= held_parts

    paths = [expand(value).absolute() for value in args.workspace] if args.workspace else machine["workspaces"]
    workspaces, held = [], set()
    if any(name in per_workspace for name in names):
        workspaces, held = load_workspaces(paths, problems)
        workspaces = hold_unmet_needs(parts, names, workspaces, held, problems)
        workspaces = hold_unmet_requires(parts, names, workspaces, picks, held, problems)
    listed = {norm(path) for path in machine["workspaces"]}
    warnings = [
        f"workspace {show(path)}: not in {MACHINE.name}, so the next full sync removes what this run installs there"
        for path in paths
        if args.workspace and not args.teardown and norm(path) not in listed
    ]
    scope = Scope(scope_parts, {norm(path) for path in paths} if args.workspace else None, held)

    plan = build_plan(parts, names, scope, state, workspaces, problems, held_parts, picks)
    plan.warnings = warnings + plan.warnings
    if args.json:
        report_json(plan, all(norm(path) in listed for path in paths))
        return 1 if plan.conflicts or plan.problems else 0
    errors = apply_plan(plan, state) if args.apply else []

    rerun = " ".join(
        [Path(sys.executable).name, Path(__file__).name]
        + (["--teardown"] if args.teardown else [])
        + [f"--toolbox {value}" for value in args.toolbox]
        + [f"--workspace {value}" for value in args.workspace]
    )
    report(plan, args.apply, rerun, errors)
    return 1 if plan.conflicts or plan.problems or errors else 0


if __name__ == "__main__":
    sys.exit(main())
