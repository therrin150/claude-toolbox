"""A workspace's toolbox config: finding `.claude/toolbox.local.yml`, validating it, and rendering the generated rule.

Every toolbox script and the installer read the config through this module.

- find() walks up from the current directory to the first folder holding `.claude/toolbox.local.yml`, as git finds
  `.git`, so a script works from the workspace root and from inside a worktree. A `.claude/` folder without the file
  (a worktree's tracked one) doesn't stop the walk. TOOLBOX_WORKSPACE names the workspace root instead.
- load() finds and reads the config; load_workspace() reads a known workspace's, for the installer.
- validate() checks a parsed config against SCHEMA (`version: 1`) and applies defaults. Unknown keys, wrong types and
  missing required keys fail with the key's dotted path, all at once.
- render_rule() writes `.claude/rules/toolbox-config.md`: the few values a session needs to act, loaded every session.

Scripts import it by path from their own resolved location:

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
"""

from __future__ import annotations

import copy
import os
import re
import string
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml

VERSION = 1
CONFIG_PATH = Path(".claude/toolbox.local.yml")
RULE_PATH = Path(".claude/rules/toolbox-config.md")
TOOLBOX_PATH = Path(".claude/toolbox")
ENV_WORKSPACE = "TOOLBOX_WORKSPACE"

AREA_COMMANDS = ("format", "unit", "integration", "start", "stop")
ANY_AREA = "*"
BRANCH_FIELDS = ("handle", "key", "slug")
KEY_PREFIX = re.compile(r"^[A-Z][A-Z0-9]*$")
JIRA_TYPES = ("story", "epic", "defect")
JIRA_STATUSES = ("todo", "in-progress", "review", "done", "stopped")
LIBRARY_FIELDS = ("libraries",)  # placeholders shared_libraries.update.command may hold
# The task-record fields tasks.py owns (records.py's FIELDS, plus `cycle`); an extra field can't take one's name.
RECORD_FIELDS = ("type", "key", "slice", "summary", "epic", "status", "owner", "stage", "cycle", "depends_on",
                 "created", "found_in", "legacy_id", "source", "source_doc", "worktrees", "sessions", "prs", "work_note", "usage",
                 "usage_total")
FIELD_NAME = re.compile(r"[a-z][a-z0-9_]*")


class ConfigError(Exception):
    """The config is missing or invalid. `errors` holds one line per problem, each led by the key's dotted path."""

    def __init__(self, errors: list[str], path: Path | None = None):
        self.errors = errors
        self.path = path
        where = f"{path}: " if path else ""
        super().__init__(where + "; ".join(errors))


class NotFound(ConfigError):
    """No workspace config above the starting folder."""


# ---------------------------------------------------------------------------------------------------------------------
# Schema


@dataclass(frozen=True)
class Value:
    kind: type | tuple
    default: Any = None
    required: bool = False
    choices: tuple = ()


@dataclass(frozen=True)
class Section:
    fields: dict
    optional: bool = False  # absent means None; present, its required keys apply


@dataclass(frozen=True)
class MapOf:
    value: Any  # any spec; keys are free-form names


@dataclass(frozen=True)
class ListOf:
    kind: type


# `files`: globs (fnmatch, against the path from the area's folder) for the changed files that make the area's checks
# apply; with none, any changed file in the area does.
AREA = Section({**{name: Value(str) for name in AREA_COMMANDS}, "files": ListOf(str)})

SCHEMA = Section({
    "version": Value(int, required=True),
    "features": ListOf(str),
    "workspace": Section({
        "name": Value(str, required=True),
        "layout": Value(str, required=True, choices=("single", "nested")),
        "default_branch": Value(str, "master"),
        "worktrees": Value(str, ".worktrees"),
        "areas": MapOf(AREA),
    }),
    "user": Section({
        "handle": Value(str, required=True),
        "name": Value(str, required=True),
        "github": Value(str),
    }),
    "vault": Section({
        "path": Value(str, required=True),
        "tasks": Value(str, "Tasks"),
        "discussions": Value(str, "Discussions"),
    }),
    "tracker": Section({
        "kind": Value(str, required=True, choices=("vault", "jira", "none")),
        "key_prefix": Value(str),
        "jira": Section({
            "mcp_server": Value(str, "atlassian"),
            "site": Value(str, required=True),
            "cloud_id": Value(str, required=True),
            "project": Value(str, required=True),
            "account": Value(str, required=True),
            "fields": MapOf(Value(str)),
            "write_back": Value(str, "none", choices=("none", "transitions")),
            "statuses": MapOf(Value(str)),     # task status -> Jira status name, for write_back: transitions
            "issue_types": MapOf(Value(str)),  # story, epic, defect -> Jira issue type name
            "labels": ListOf(str),             # labels every filed issue gets
            "estimates": Value(bool, False),   # file a draft's `estimate` as the issue's original estimate
            "board": Value(int),               # the board whose sprints jira-find-current-sprint reads
        }, optional=True),
    }),
    "git": Section({
        "branch": Value(str, "{handle}/{key}-{slug}"),
        "org": Value(str),
    }),
    "pr": Section({
        "required": Value(bool, True),
        "review_required": Value(bool, False),
        "merge": Value(str, "squash", choices=("squash", "merge", "rebase")),
        "wait_for_ci": Value(bool, True),
        "bots": ListOf(str),  # logins whose PR comments the watcher ignores, beyond GitHub apps and *[bot]
        "announce": Section({
            "mcp_server": Value(str, "slack"),
            "channel": Value(str, required=True),
        }, optional=True),
    }),
    "services": Section({
        "agent_lock_minutes": Value(int, 30),
        "manual_lock_minutes": Value(int, 60),
    }),
    "work": Section({
        "session_model": Value(str, "sonnet"),
        "dispatch": Value(str, "direct", choices=("direct", "chips")),
        "wip": MapOf(Value(int)),  # per owner; defaults filled in by _check_values
        "stages": ListOf(str),  # task `stage` values beyond the generic ones, such as awaiting-release
        # A task whose depends_on aren't done: `isolate` works it anyway (overlaps settle at merge), `block` stops
        "dependencies": Value(str, "isolate", choices=("isolate", "block")),
        # Task-record fields beyond the built-in ones: name -> a list of allowed values, or `text`
        "extra_fields": MapOf(Value((str, list))),
    }),
    # Repos (nested) or areas (single) that others build against: a task changing one runs work-task's
    # shared-library cycle, then its consumers cycle. Empty: one cycle.
    "shared_libraries": Section({
        "names": ListOf(str),
        "update": Section({  # how a consumer picks up a release, run in each consumer checkout: one of
            "command": Value(str),
            "skill": Value(str),
        }, optional=True),
    }),
    "verify": Section({
        "ignore_bumps": ListOf(str),  # dependency names whose version bumps verify-change doesn't count as scope creep
    }),
    # Extension docs are found in .claude/extensions/ (lib/extensions.py); `disabled` names the ones to leave out
    "extensions": Section({
        "disabled": ListOf(str),  # doc file names in .claude/extensions/, `.md` optional
    }),
})

DEFAULT_WIP = {"claude": 5, "user": 3}


def _join(path: str, key) -> str:
    return f"{path}.{key}" if path else str(key)


def _kind_name(kind) -> str:
    kinds = kind if isinstance(kind, tuple) else (kind,)
    names = {str: "a string", int: "an integer", bool: "true or false", dict: "a mapping", list: "a list"}
    return " or ".join(names.get(k, k.__name__) for k in kinds)


def _is(value, kind) -> bool:
    # YAML's true/false are ints to Python; only a bool field takes them.
    if isinstance(value, bool) and kind is not bool:
        return False
    return isinstance(value, kind)


def _check(spec, value, path: str, errors: list[str]):
    """Return `value` with defaults applied, appending a line to `errors` for each problem."""
    if isinstance(spec, Section):
        if value is None:
            if spec.optional:
                return None
            value = {}
        if not isinstance(value, dict):
            errors.append(f"{path}: must be {_kind_name(dict)}")
            return None
        errors.extend(f"{_join(path, key)}: unknown key" for key in value if key not in spec.fields)
        return {key: _check(sub, value.get(key), _join(path, key), errors) for key, sub in spec.fields.items()}
    if isinstance(spec, MapOf):
        if value is None:
            return {}
        if not isinstance(value, dict):
            errors.append(f"{path}: must be {_kind_name(dict)}")
            return {}
        return {str(key): _check(spec.value, item, _join(path, key), errors) for key, item in value.items()}
    if isinstance(spec, ListOf):
        if value is None:
            return []
        if not isinstance(value, list):
            errors.append(f"{path}: must be {_kind_name(list)}")
            return []
        for index, item in enumerate(value):
            if not _is(item, spec.kind):
                errors.append(f"{path}[{index}]: must be {_kind_name(spec.kind)}")
        return list(value)
    if value is None:
        if spec.required:
            errors.append(f"{path}: required")
        return copy.deepcopy(spec.default)
    if not _is(value, spec.kind):
        errors.append(f"{path}: must be {_kind_name(spec.kind)}, got {value!r}")
        return copy.deepcopy(spec.default)
    if spec.choices and value not in spec.choices:
        errors.append(f"{path}: must be one of {', '.join(spec.choices)}; got {value!r}")
    return value


def _check_values(data: dict, errors: list[str]) -> None:
    """Rules that span keys, or that a type alone can't state. Fills the per-owner WIP defaults."""
    tracker = data["tracker"]
    if tracker["kind"] == "jira" and tracker["jira"] is None:
        errors.append("tracker.jira: required when tracker.kind is jira")
    if tracker["kind"] == "vault" and not tracker["key_prefix"]:
        errors.append("tracker.key_prefix: required when tracker.kind is vault")
    if isinstance(tracker["key_prefix"], str) and not KEY_PREFIX.match(tracker["key_prefix"]):
        errors.append(f"tracker.key_prefix: must be capital letters and digits, like MB; got {tracker['key_prefix']!r}")
    jira = tracker["jira"]
    if jira and isinstance(jira["project"], str) and not KEY_PREFIX.match(jira["project"]):
        errors.append(f"tracker.jira.project: must be a Jira project key, like ABC; got {jira['project']!r}")
    if jira:
        for name, allowed in (("issue_types", JIRA_TYPES), ("statuses", JIRA_STATUSES)):
            for key in sorted(set(jira[name]) - set(allowed)):
                errors.append(f"tracker.jira.{name}.{key}: unknown; use {', '.join(allowed)}")

    branch = data["git"]["branch"]
    if isinstance(branch, str):
        try:
            fields = {name for _, name, _, _ in string.Formatter().parse(branch) if name is not None}
        except ValueError as error:
            errors.append(f"git.branch: {error}")
        else:
            for name in sorted(fields - set(BRANCH_FIELDS)):
                errors.append(f"git.branch: unknown placeholder {{{name}}}; use {{handle}}, {{key}} or {{slug}}")
            if "slug" not in fields:
                errors.append("git.branch: must contain {slug}")

    worktrees = data["workspace"]["worktrees"]
    if isinstance(worktrees, str) and _absolute(worktrees):
        errors.append(f"workspace.worktrees: must be relative to the workspace root; got {worktrees!r}")
    for index, doc in enumerate(data["extensions"]["disabled"]):
        if isinstance(doc, str) and ("/" in doc or not doc.strip()):
            errors.append(f"extensions.disabled[{index}]: must be a doc's file name in .claude/extensions/, like "
                          f"sonar-scan.md; got {doc!r}")

    name = data["user"]["name"]
    if isinstance(name, str):
        owner = name.lower()
        if owner == "claude":
            errors.append("user.name: can't be Claude, which is the other task owner")
        wip = data["work"]["wip"]
        for key in wip:
            if key not in ("claude", owner):
                errors.append(f"work.wip.{key}: unknown owner; use claude or {owner}")
        wip.setdefault("claude", DEFAULT_WIP["claude"])
        wip.setdefault(owner, DEFAULT_WIP["user"])

    for index, stage in enumerate(data["work"]["stages"]):
        if isinstance(stage, str) and not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", stage):
            errors.append(f"work.stages[{index}]: must be lowercase words joined by -, like awaiting-release; "
                          f"got {stage!r}")

    for name, allowed in data["work"]["extra_fields"].items():
        where = f"work.extra_fields.{name}"
        if not FIELD_NAME.fullmatch(name):
            errors.append(f"{where}: a field name is lowercase letters, digits and _, like pending_release")
        elif name in RECORD_FIELDS:
            errors.append(f"{where}: a built-in task-record field; pick another name")
        if allowed is None:
            errors.append(f"{where}: must be text or a list of allowed values")
        if isinstance(allowed, str) and allowed != "text":
            errors.append(f"{where}: must be text or a list of allowed values; got {allowed!r}")
        if isinstance(allowed, list):
            if not allowed:
                errors.append(f"{where}: an empty list allows nothing; list the values, or use text")
            for index, value in enumerate(allowed):
                if not isinstance(value, str) or not value.strip():
                    errors.append(f"{where}[{index}]: must be a non-empty string")
            if len(set(map(str, allowed))) != len(allowed):
                errors.append(f"{where}: lists a value twice")

    shared = data["shared_libraries"]
    names = shared["names"]
    for index, name in enumerate(names):
        if isinstance(name, str) and not name.strip():
            errors.append(f"shared_libraries.names[{index}]: must not be empty")
    if len(set(names)) != len(names):
        errors.append("shared_libraries.names: lists a name twice")
    if data["workspace"]["layout"] == "single":
        listed = set(data["workspace"]["areas"]) - {ANY_AREA}
        for name in names:
            if isinstance(name, str) and name.strip() and name not in listed:
                errors.append(f"shared_libraries.names: {name!r} isn't an area under workspace.areas (a single "
                              "workspace's shared libraries are its areas)")
    update = shared["update"]
    if names and update is None:
        errors.append("shared_libraries.update: required when shared_libraries.names lists any (how consumers "
                      "pick up a release)")
    if update is not None and (update["command"] is None) == (update["skill"] is None):
        errors.append("shared_libraries.update: give command or skill, not both")
    if update is not None and isinstance(update["command"], str):
        try:
            fields = {name for _, name, _, _ in string.Formatter().parse(update["command"]) if name is not None}
        except ValueError as error:
            errors.append(f"shared_libraries.update.command: {error}")
        else:
            for name in sorted(fields - set(LIBRARY_FIELDS)):
                errors.append(f"shared_libraries.update.command: unknown placeholder {{{name}}}; use {{libraries}}")


def _absolute(path: str) -> bool:
    return path.startswith("~") or Path(path).is_absolute()


def validate(raw) -> tuple[dict, list[str]]:
    """Check a parsed config. Returns it with defaults applied, and the problems found (empty when valid)."""
    if raw is None:
        return {}, ["config is empty"]
    if not isinstance(raw, dict):
        return {}, ["config must be a mapping"]
    version = raw.get("version")
    if version is None:
        return {}, ["version: required"]
    if version != VERSION or isinstance(version, bool):
        return {}, [f"version: must be {VERSION}; got {version!r}"]
    errors: list[str] = []
    data = _check(SCHEMA, raw, "", errors)
    if not errors:
        _check_values(data, errors)
    return data, errors


# ---------------------------------------------------------------------------------------------------------------------
# Lookup


def find(start: Path | None = None) -> Path:
    """The workspace root: TOOLBOX_WORKSPACE, else the nearest folder at or above `start` holding the config."""
    override = os.environ.get(ENV_WORKSPACE)
    if override:
        root = Path(override).expanduser().resolve()
        if not (root / CONFIG_PATH).is_file():
            raise NotFound([f"{ENV_WORKSPACE} is {override}, which has no {CONFIG_PATH}"])
        return root
    here = Path(start or Path.cwd()).resolve()
    for folder in (here, *here.parents):
        if (folder / CONFIG_PATH).is_file():
            return folder
    raise NotFound([f"no {CONFIG_PATH} in {here} or any folder above it; run /setup-workspace, or set {ENV_WORKSPACE}"])


def load(start: Path | None = None) -> Config:
    """Find the workspace above `start` (default: the current directory) and load its config."""
    return load_workspace(find(start))


def load_workspace(root: Path) -> Config:
    """Load and validate the config of the workspace at `root`, with no walk and no override."""
    root = Path(root).expanduser().resolve()
    path = root / CONFIG_PATH
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise NotFound([f"{path} doesn't exist"]) from None
    except yaml.YAMLError as error:
        raise ConfigError([f"not valid YAML: {error}"], path) from None
    data, errors = validate(raw)
    if errors:
        raise ConfigError(errors, path)
    return Config(root, data)


# ---------------------------------------------------------------------------------------------------------------------
# Accessors


@dataclass(frozen=True)
class Config:
    root: Path
    data: dict

    def get(self, dotted: str):
        """The value at a dotted key, such as `pr.announce.channel`. KeyError names the first missing part."""
        value, walked = self.data, []
        for part in dotted.split("."):
            walked.append(part)
            if not isinstance(value, dict) or part not in value:
                raise KeyError(".".join(walked))
            value = value[part]
        return value

    # Paths

    @property
    def path(self) -> Path:
        return self.root / CONFIG_PATH

    @property
    def rule_path(self) -> Path:
        return self.root / RULE_PATH

    @property
    def toolbox_dir(self) -> Path:
        return self.root / TOOLBOX_PATH

    @property
    def scripts_dir(self) -> Path:
        return self.toolbox_dir / "bin"

    @property
    def worktrees_dir(self) -> Path:
        return self.root / self.data["workspace"]["worktrees"]

    @property
    def vault_dir(self) -> Path:
        return self.root / Path(self.data["vault"]["path"]).expanduser()

    @property
    def tasks_dir(self) -> Path:
        return self.vault_dir / self.data["vault"]["tasks"]

    @property
    def discussions_dir(self) -> Path:
        return self.vault_dir / self.data["vault"]["discussions"]

    # Workspace

    @property
    def name(self) -> str:
        return self.data["workspace"]["name"]

    @property
    def nested(self) -> bool:
        return self.data["workspace"]["layout"] == "nested"

    @property
    def default_branch(self) -> str:
        return self.data["workspace"]["default_branch"]

    @property
    def features(self) -> list[str]:
        return list(self.data["features"])

    def has(self, feature: str) -> bool:
        return feature in self.data["features"]

    def areas(self) -> list[str]:
        """Areas with their own build: the listed ones, plus, in a nested workspace, every repo one level down."""
        names = {name for name in self.data["workspace"]["areas"] if name != ANY_AREA}
        if self.nested:
            names.update(child.name for child in self.root.iterdir()
                         if child.is_dir() and not child.name.startswith(".") and (child / ".git").exists())
        return sorted(names)

    def area_files(self, area: str) -> list[str]:
        """The globs that make an area's checks apply, falling back to the `"*"` area's; empty means any file."""
        areas = self.data["workspace"]["areas"]
        own = (areas.get(area) or {}).get("files")
        return list(own) if own else list((areas.get(ANY_AREA) or {}).get("files") or [])

    def command(self, area: str, kind: str) -> str | None:
        """An area's command of one kind (`unit`, `start`, ...), falling back to the `"*"` area's, else None."""
        if kind not in AREA_COMMANDS:
            raise ValueError(f"unknown command kind {kind!r}; use one of {', '.join(AREA_COMMANDS)}")
        areas = self.data["workspace"]["areas"]
        own = (areas.get(area) or {}).get(kind)
        return own if own is not None else (areas.get(ANY_AREA) or {}).get(kind)

    # People and tracker

    @property
    def handle(self) -> str:
        return self.data["user"]["handle"]

    @property
    def github(self) -> str:
        """The GitHub account gh should be signed in as: user.github, else the handle."""
        return self.data["user"].get("github") or self.handle

    @property
    def owner(self) -> str:
        """The user's value for a task record's `owner`; the other is `claude`."""
        return self.data["user"]["name"].lower()

    @property
    def tracker(self) -> str:
        return self.data["tracker"]["kind"]

    @property
    def key_prefix(self) -> str | None:
        """Ticket keys' prefix: the vault counter's, or the Jira project's. None under the `none` tracker."""
        tracker = self.data["tracker"]
        if tracker["kind"] == "jira":
            return tracker["key_prefix"] or tracker["jira"]["project"]
        return tracker["key_prefix"] if tracker["kind"] == "vault" else None

    @property
    def jira(self) -> dict | None:
        return self.data["tracker"]["jira"] if self.tracker == "jira" else None

    # Git and PRs

    def branch(self, slug: str, key: str | None = None) -> str:
        """A task's branch name from `git.branch`, dropping the key and its separator when the task has none."""
        pattern = self.data["git"]["branch"]
        if not key:
            pattern = pattern.replace("{key}-", "").replace("-{key}", "").replace("{key}", "")
        return pattern.format(handle=self.handle, key=key or "", slug=slug)

    @property
    def pr(self) -> dict:
        return self.data["pr"]

    @property
    def announce(self) -> dict | None:
        return self.data["pr"]["announce"]

    # Tasks

    @property
    def shared_libraries(self) -> list[str]:
        """The repos (nested) or areas (single) other repos or areas build against; empty means one cycle."""
        return list(self.data["shared_libraries"]["names"])

    @property
    def library_update(self) -> dict | None:
        """How a consumer picks up a release: {"command": ...} or {"skill": ...}; None with no shared libraries."""
        update = self.data["shared_libraries"]["update"]
        if not self.shared_libraries or not update:
            return None
        return {kind: value for kind, value in update.items() if value is not None}

    @property
    def extra_fields(self) -> dict:
        """Task-record fields the config declares: name -> a list of allowed values, or "text"."""
        return dict(self.data["work"]["extra_fields"])

    # Extensions

    @property
    def disabled_extensions(self) -> list[str]:
        """The extension docs `extensions.disabled` leaves out, as file names."""
        import extensions
        return [extensions.doc_name(entry) for entry in self.data["extensions"]["disabled"]]

    def extensions(self, skill: str) -> list[Path]:
        """The extension docs in .claude/extensions/ that extend a skill, in name order, minus the disabled ones, as
        absolute paths. See .claude/extensions/README.md."""
        import extensions
        return extensions.discover(self.root, skill, self.disabled_extensions)


# ---------------------------------------------------------------------------------------------------------------------
# The generated rule

MERGE_TEXT = {"squash": "squash-merged", "merge": "merged with a merge commit", "rebase": "rebase-merged"}


def render_rule(config: Config, workspace: Path | None = None) -> str:
    """The text of `.claude/rules/toolbox-config.md` for a workspace (default: the config's own root)."""
    if workspace is not None:
        config = replace(config, root=Path(workspace).expanduser().resolve())
    data = config.data
    shape = "a folder of repos" if config.nested else "one repo"
    org = f" in GitHub org `{data['git']['org']}`" if data["git"]["org"] else ""
    lines = [
        "# Toolbox Config",
        "",
        f"Generated from `{CONFIG_PATH}` on every toolbox sync; edits here are lost. Change the config and sync.",
        "",
        f"- **Workspace:** `{config.name}` at `{config.root}`, {shape}{org}; default branch `{config.default_branch}`.",
        f"- **Scripts:** `{config.scripts_dir}`. Call them by this absolute path, which works from a worktree too.",
        f"- **Other config:** `{config.scripts_dir / 'toolbox.py'} config show [dotted.key]` prints it as JSON.",
        f"- **Worktrees:** `{config.worktrees_dir}`.",
        f"- **Vault:** `{config.vault_dir}`; task records in `{data['vault']['tasks']}/`, discussions in "
        f"`{data['vault']['discussions']}/`.",
        f"- **Task owners:** `claude` or `{config.owner}`.",
        *_tracker_lines(config),
        f"- **Branches:** `{data['git']['branch'].replace('{handle}', config.handle)}`, dropping the key and its "
        "separator when the task has none.",
        f"- **Pull requests:** {_pr_text(config)}",
        f"- **Slack:** {_slack_text(config)}",
        *_task_lines(config),
        f"- **Features:** {', '.join(f'`{f}`' for f in config.features) or 'none'}.",
        f"- **Extensions:** a project's steps hooked into these skills, as docs in "
        f"`{config.root / '.claude/extensions'}`; its `README.md` says how to write one.",
    ]
    return "\n".join(lines) + "\n"


def _task_lines(config: Config) -> list[str]:
    lines = []
    if config.shared_libraries:
        update = config.library_update
        how = (f"`{update['command']}` in each consumer checkout" if "command" in update
               else f"the `{update['skill']}` skill on each consumer checkout")
        names = ", ".join(f"`{name}`" for name in config.shared_libraries)
        lines.append(f"- **Shared libraries:** {names}. A task changing one runs `work-task`'s shared-library cycle, "
                     f"then its consumers cycle; consumers pick up a release with {how}.")
    if config.extra_fields:
        fields = "; ".join(f"`{name}` ({'text' if allowed == 'text' else ', '.join(f'`{v}`' for v in allowed)})"
                           for name, allowed in config.extra_fields.items())
        lines.append(f"- **Extra task fields:** {fields}. Set and read them with `tasks.py set` and `get`.")
    return lines


def _tracker_lines(config: Config) -> list[str]:
    if config.tracker == "vault":
        return [f"- **Tracker:** `vault`. The task record is the ticket; keys are `{config.key_prefix}-<n>` from a "
                "local counter."]
    if config.tracker == "none":
        return ["- **Tracker:** `none`. No ticket keys; a task is named by a slug of its summary or plan title."]
    jira = config.jira
    write_back = "status transitions" if jira["write_back"] == "transitions" else "nothing"
    return [
        f"- **Tracker:** `jira`, project `{jira['project']}` on `{jira['site']}` (cloud id `{jira['cloud_id']}`), "
        f"keys `{config.key_prefix}-<n>`. Writes back {write_back}.",
        f"- **Jira tools:** `mcp__{jira['mcp_server']}__<tool>`. Before any write, confirm the signed-in account "
        f"is `{jira['account']}`; stop if not.",
    ]


def _pr_text(config: Config) -> str:
    pr = config.pr
    merge = MERGE_TEXT[pr["merge"]]
    review = "after an approving review" if pr["review_required"] else "with no review required"
    ci = "once CI passes" if pr["wait_for_ci"] else "without waiting for CI"
    if pr["required"]:
        return f"every change goes through one, {merge} {review}, {ci}."
    return (f"optional; a change may go straight to `{config.default_branch}`. An opened one is {merge} {review}, "
            f"{ci}.")


def _slack_text(config: Config) -> str:
    announce = config.announce
    if not announce:
        return "no pull request announcements."
    return (f"announce each new pull request in channel `{announce['channel']}`, with "
            f"`mcp__{announce['mcp_server']}__<tool>`.")
