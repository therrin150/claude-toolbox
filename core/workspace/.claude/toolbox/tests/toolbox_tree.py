"""Where the toolbox's files are, for the tests.

The repo holds each file in the feature folder that installs it, but its scripts and library call each other as
installed, side by side. So the tests run against an assembled copy: every workspace feature folder's `.claude/` tree
(and `.vault/`) merged into one workspace folder, the way a workspace that picks every feature gets them, and every
global feature's the way `~/.claude` gets them. Built once per test process, in a temporary folder.

    TOOLBOX            the assembled `.claude/toolbox` (bin/, lib/, agents/, references/, templates/)
    CLAUDE             the assembled workspace `.claude`
    WORKSPACE_FOLDER   the assembled workspace folder (holds `.claude` and `.vault`)
    SKILLS             the assembled workspace `.claude/skills`
    GLOBAL_CLAUDE      the assembled global `.claude` (what installs into ~/.claude)
    REPO               the toolbox repo itself
    TESTS              this folder

`feature_path(feature, relpath)` is a file in its feature folder in the repo itself, for a script that finds the repo
from its own location. `features()` is what each workspace feature holds, as `{feature: {paths, needs, requires}}`.
"""

from __future__ import annotations

import atexit
import shutil
import sys
import tempfile
from pathlib import Path

import yaml

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parents[4]
sys.path.insert(0, str(REPO / "core" / "workspace" / ".claude" / "toolbox" / "lib"))

import config  # noqa: E402  (the config library, which also finds the feature folders)

IGNORED = shutil.ignore_patterns("__pycache__", ".DS_Store")
ROOTS = (".claude", ".vault")


def read(folder: Path) -> dict:
    return config.read_feature(yaml.safe_load((folder / config.FEATURE_FILE).read_text(encoding="utf-8")),
                               config.feature_name(REPO, folder))


def folders(target: str) -> list[Path]:
    return [folder for folder in config.feature_folders(REPO) if read(folder)["target"] == target]


def merge(target: str, into: Path) -> None:
    for folder in folders(target):
        for root in ROOTS:
            if (folder / root).is_dir():
                shutil.copytree(folder / root, into / root, dirs_exist_ok=True, ignore=_ignore)


def _ignore(directory: str, names: list) -> set:
    """Everything but each feature's tests folder (the tests are run from the repo, not installed)."""
    skip = set(IGNORED(directory, names))
    if Path(directory).name == "toolbox" and "tests" in names:
        skip.add("tests")
    return skip


_scratch = Path(tempfile.mkdtemp(prefix="toolbox-tree-")).resolve()
atexit.register(shutil.rmtree, _scratch, ignore_errors=True)
WORKSPACE_FOLDER = _scratch / "workspace"
GLOBAL_FOLDER = _scratch / "global"
merge("workspace", WORKSPACE_FOLDER)
merge("global", GLOBAL_FOLDER)

CLAUDE = WORKSPACE_FOLDER / ".claude"
TOOLBOX = CLAUDE / "toolbox"
SKILLS = CLAUDE / "skills"
GLOBAL_CLAUDE = GLOBAL_FOLDER / ".claude"


def feature_path(feature: str, relpath: str) -> Path:
    """A file as it sits in its feature folder in the repo."""
    return REPO / ("core" if feature == "core" else f"features/{feature}") / relpath


def entries(folder: Path) -> list[str]:
    """What a feature folder holds, as a workspace-relative path each: a skill folder is one entry, a `.vault` folder
    is one, and every other file is its own."""
    found = []
    for root in ROOTS:
        base = folder / root
        if not base.is_dir():
            continue
        if root == ".vault":
            found.append(".vault")
            continue
        for path in sorted(base.rglob("*")):
            parts = path.relative_to(folder).parts
            if "__pycache__" in parts or path.name == ".DS_Store" or parts[:3] == (".claude", "toolbox", "tests"):
                continue
            if parts[:2] == (".claude", "skills") and len(parts) > 3:
                continue
            if path.is_dir() and not (parts[:2] == (".claude", "skills") and len(parts) == 3):
                continue
            found.append("/".join(parts))
    return found


def features() -> dict:
    """{feature: {"paths": [...], "needs": [...], "requires": [...]}} for every workspace feature but core."""
    found = {}
    for folder in folders("workspace"):
        spec = read(folder)
        if spec["name"] != config.CORE:
            found[spec["name"]] = {"paths": entries(folder), "needs": spec["needs"], "requires": spec["requires"]}
    return found
