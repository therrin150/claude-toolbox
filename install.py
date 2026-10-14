#!/usr/bin/env python3
"""Link this repo's Claude Code domains into place on this machine.

Each domain folder mirrors its target folder: folders are created, files are linked one at a time, and skill folders
(anything containing SKILL.md) are linked whole. Previews by default and changes nothing; pass --apply to make the
changes. See README.md.

Keep this file identical in claude-toolbox and asudweeks-claude-toolbox: a change to one goes in the other too.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
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
}


@dataclass
class Link:
    domain: str
    source: Path
    target: Path
    external: bool = False


@dataclass
class Plan:
    create_dirs: list
    create: list
    relink: list
    stale: list
    forget: list
    excludes: list
    ok: list
    conflicts: list
    warnings: list
    problems: list
    keep_dirs: set
    domains: list
    scope: set


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


def inside_repo(dest: str) -> bool:
    repo = norm(REPO)
    return dest == repo or dest.startswith(repo + os.sep)


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


def load_manifest() -> dict:
    domains = load_yaml(MANIFEST).get("domains") or {}
    if not isinstance(domains, dict) or not domains:
        sys.exit(f"{MANIFEST.name} lists no domains.")
    return domains


def machine_domains() -> list:
    if not MACHINE.exists():
        sys.exit(
            f"No {MACHINE.name} on this machine. Copy {MACHINE_EXAMPLE.name} to {MACHINE.name} and list the domains "
            f"to install here.\n  {MACHINE_EXAMPLE}"
        )
    return list(load_yaml(MACHINE).get("domains") or [])


def load_state() -> dict:
    if STATE.exists():
        with open(STATE, encoding="utf-8") as handle:
            state = json.load(handle)
        state.setdefault("links", {})
        state.setdefault("dirs", {})
        return state
    return {"links": {}, "dirs": {}}


def save_state(state: dict) -> None:
    with open(STATE, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)
        handle.write("\n")


# ---------------------------------------------------------------------------------------------------------------------
# Desired state


def is_skill_dir(path: Path) -> bool:
    return any((path / name).is_file() for name in SKILL_FILES)


def walk(domain: str, src_root: Path, tgt_root: Path, ignore: list, links: list, dirs: list) -> None:
    def visit(src: Path, tgt: Path, rel: str) -> None:
        for entry in sorted(src.iterdir()):
            relpath = f"{rel}/{entry.name}" if rel else entry.name
            if entry.name in ALWAYS_SKIP or any(fnmatch.fnmatch(relpath, pattern) for pattern in ignore):
                continue
            target = tgt / entry.name
            if entry.is_dir() and not is_skill_dir(entry):
                dirs.append((domain, target))
                visit(entry, target, relpath)
            else:
                links.append(Link(domain, entry, target))

    visit(src_root, tgt_root, "")


def desired(manifest: dict, names: list, problems: list):
    links, dirs = [], []
    for name in names:
        spec = manifest[name]
        src_root = REPO / spec["source"]
        tgt_root = expand(spec["target"])
        if not src_root.is_dir():
            problems.append(f"{name}: source folder {show(src_root)} doesn't exist")
            continue
        if not tgt_root.is_dir():
            problems.append(f"{name}: target folder {show(tgt_root)} doesn't exist. Create or clone it first.")
            continue
        walk(name, src_root, tgt_root, spec.get("ignore") or [], links, dirs)
        for external in spec.get("external") or []:
            target = tgt_root / external["target"]
            parent, chain = target.parent, []
            while parent != tgt_root and tgt_root in parent.parents:
                chain.append(parent)
                parent = parent.parent
            dirs.extend((name, folder) for folder in reversed(chain))
            links.append(Link(name, expand(external["source"]), target, external=True))
    unique_dirs = {}
    for domain, folder in dirs:
        unique_dirs.setdefault(norm(folder), (domain, folder))
    return links, list(unique_dirs.values())


# ---------------------------------------------------------------------------------------------------------------------
# Checks


def frontmatter_name(markdown: Path) -> str | None:
    text = markdown.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    match = FRONTMATTER_NAME.search(text[3:] if end == -1 else text[3:end])
    return match.group(1).strip() if match else None


def name_warnings(link: Link, prefix: str | None) -> list:
    if link.external:
        return []
    source = link.source
    parts = source.relative_to(REPO).parts
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
    if prefix and not name.startswith(prefix):
        warnings.append(f"{kind} {where}: missing the domain prefix {prefix!r}")
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


def build_plan(manifest: dict, names: list, scope: set, state: dict) -> Plan:
    problems = []
    links, dirs = desired(manifest, names, problems)
    plan = Plan([], [], [], [], [], [], [], [], [], problems, set(), names, scope)

    blocked = []
    for domain, folder in dirs:
        if folder.is_symlink() or (folder.exists() and not folder.is_dir()):
            plan.conflicts.append(("dir-conflict", folder, None))
            blocked.append(norm(folder))
        elif not folder.exists():
            plan.create_dirs.append((domain, folder))
        plan.keep_dirs.add(norm(folder))

    counts = {}
    for link in links:
        counts[norm(link.target)] = counts.get(norm(link.target), 0) + 1

    for link in links:
        key = norm(link.target)
        plan.warnings += name_warnings(link, manifest[link.domain].get("prefix"))
        if counts[key] > 1:
            plan.conflicts.append(("collision", link.target, link.source))
        elif any(key.startswith(folder + os.sep) for folder in blocked):
            plan.conflicts.append(("blocked", link.target, link.source))
        elif link.target.is_symlink():
            dest = link_dest(link.target)
            if dest == norm(link.source):
                plan.ok.append(link)
            elif inside_repo(dest):
                plan.relink.append(link)
            else:
                plan.conflicts.append(("foreign", link.target, Path(dest)))
        elif link.target.exists():
            plan.conflicts.append(("real", link.target, link.source))
        elif not link.source.exists():
            plan.conflicts.append(("missing-source", link.target, link.source))
        else:
            plan.create.append(link)

    wanted = {norm(link.target) for link in links}
    for key, record in state["links"].items():
        if record["domain"] not in scope or key in wanted:
            continue
        target = Path(record["target"])
        if target.is_symlink() and link_dest(target) == norm(record["source"]):
            plan.stale.append((key, record))
        else:
            plan.forget.append(key)

    # Git excludes: one managed block per toolbox repo and domain in each git target.
    installed = plan.ok + plan.relink + plan.create
    for domain in sorted(scope & set(manifest)):
        found = git_exclude(expand(manifest[domain]["target"]))
        if found is None:
            continue
        top, exclude_file = found
        entries = sorted(
            "/" + Path(os.path.relpath(norm(link.target), norm(top))).as_posix()
            for link in installed
            if link.domain == domain
        )
        current = exclude_file.read_text(encoding="utf-8") if exclude_file.exists() else ""
        updated = render_exclude(current, f"{REPO.name}:{domain}", entries)
        if updated != current:
            plan.excludes.append((exclude_file, updated, len(entries)))
    return plan


# ---------------------------------------------------------------------------------------------------------------------
# Applying


def apply_plan(plan: Plan, state: dict) -> list:
    errors = []
    for domain, folder in plan.create_dirs:
        folder.mkdir(parents=True, exist_ok=True)
        state["dirs"][norm(folder)] = {"path": str(folder), "domain": domain}
    for key, record in plan.stale:
        Path(record["target"]).unlink()
        del state["links"][key]
    for key in plan.forget:
        del state["links"][key]
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
            state["links"][norm(link.target)] = {
                "target": str(link.target),
                "source": str(link.source),
                "domain": link.domain,
            }
    for exclude_file, text, _ in plan.excludes:
        exclude_file.parent.mkdir(parents=True, exist_ok=True)
        exclude_file.write_text(text, encoding="utf-8")
    # Remove folders this installer created that are no longer wanted and are now empty, deepest first.
    for key in sorted(state["dirs"], key=len, reverse=True):
        record = state["dirs"][key]
        folder = Path(record["path"])
        if record["domain"] not in plan.scope or key in plan.keep_dirs:
            continue
        if folder.is_dir() and not folder.is_symlink() and not any(folder.iterdir()):
            folder.rmdir()
        if not folder.exists():
            del state["dirs"][key]
    save_state(state)
    return errors


# ---------------------------------------------------------------------------------------------------------------------
# Reporting


def report(plan: Plan, applied: bool, rerun: str, errors: list) -> None:
    sections = [
        ("Create folders", [show(folder) for _, folder in plan.create_dirs]),
        ("Create links", [f"{show(link.target)} -> {show(link.source)}" for link in plan.create]),
        ("Replace links", [f"{show(link.target)} -> {show(link.source)}" for link in plan.relink]),
        ("Remove stale links", [f"{show(record['target'])}" for _, record in plan.stale]),
        ("Update git excludes", [f"{show(path)} ({count} entries)" for path, _, count in plan.excludes]),
        ("Already in place", [show(link.target) for link in plan.ok]),
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
    mode = "APPLIED" if applied else "PREVIEW"
    header = [
        f"{REPO.name} install plan: {mode}, {datetime.now().isoformat(timespec='seconds')}",
        f"Domains: {', '.join(plan.domains) or '(none)'}",
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
    for title in ("Conflicts, left alone", "Warnings", "Problems", "Errors"):
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="make the changes (default: preview only)")
    parser.add_argument("--teardown", action="store_true", help="remove this repo's links instead of installing")
    parser.add_argument(
        "--domain", action="append", default=[], help="act on this domain only, ignoring machine.yml (repeatable)"
    )
    args = parser.parse_args()

    manifest = load_manifest()
    state = load_state()
    unknown = [name for name in args.domain if name not in manifest]
    if unknown:
        sys.exit(f"Unknown domain(s): {', '.join(unknown)}. {MANIFEST.name} has: {', '.join(manifest)}")

    if args.domain:
        scope = set(args.domain)
    else:
        scope = set(manifest) | {record["domain"] for record in state["links"].values()}
    if args.teardown:
        names = []
    elif args.domain:
        names = list(args.domain)
    else:
        names = machine_domains()
        unknown = [name for name in names if name not in manifest]
        if unknown:
            sys.exit(f"{MACHINE.name} lists unknown domain(s): {', '.join(unknown)}")

    plan = build_plan(manifest, names, scope, state)
    errors = apply_plan(plan, state) if args.apply else []

    rerun = " ".join(
        [Path(sys.executable).name, Path(__file__).name]
        + (["--teardown"] if args.teardown else [])
        + [f"--domain {name}" for name in args.domain]
    )
    report(plan, args.apply, rerun, errors)
    return 1 if plan.conflicts or plan.problems or errors else 0


if __name__ == "__main__":
    sys.exit(main())
