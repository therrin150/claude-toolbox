"""`toolbox.py features`: which features every toolbox has, where each is installed and whether it has drifted, and
enabling or disabling them in `machine.yml` (global) or a workspace's `.claude/toolbox.local.yml`.

The installer knows the features, so this module asks it: `install.py --features` lists them with their state,
`install.py --json --enable/--disable` previews a change to the config without writing it, and `install.py --apply`
syncs once the config is written. What is added here is the config edit (a text edit that keeps comments and
formatting) and, for a `disable`, the list of what could be running on the files it removes.

A disable can pull files out from under a concurrent session, so `concurrent()` lists what could be affected: tasks
`in-progress`, task worktrees, held services locks and other sessions started in the workspace within the last hour.
The command applies a disable only with `--confirm-concurrent`, which the skill passes after the user has read it.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

import config
import doctor

ENV_LOCKS = "TOOLBOX_LOCKS"  # as services_lock.py reads it
ENV_SESSION = "CLAUDE_CODE_SESSION_ID"
SESSION_WINDOW_MINUTES = 60
SHOWN_SECTIONS = ("Create folders", "Replace links", "Create", "Update", "Remove", "Merge settings",
                  "Update git excludes", "Conflicts, left alone", "Warnings", "Problems", "Errors")


class FeaturesError(Exception):
    """Something the user must fix; the message says what."""


# ---------------------------------------------------------------------------------------------------------------------
# The installer


def installer() -> Path:
    script = doctor.installer()
    if not script.is_file():
        raise FeaturesError(f"no installer at {script}; run the installer once (install.py --apply) so "
                            f"~/.claude/toolbox-repo points to the toolbox repo")
    return script


def run_installer(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(installer()), *args], capture_output=True, text=True)


def machine_file() -> Path:
    return installer().parent / "machine.yml"


def listing(workspace: Path | None = None) -> dict:
    """The installer's `--features` JSON: {"features": [...], "problems": [...]}."""
    proc = run_installer("--features", *(["--workspace", str(workspace)] if workspace else []))
    try:
        return json.loads(proc.stdout)
    except ValueError:
        raise FeaturesError((proc.stderr or proc.stdout).strip() or "the installer printed nothing") from None


def drift_text(drift: dict) -> str:
    return ", ".join(f"{count} {kind}" for kind, count in drift.items() if count)


def render_listing(data: dict) -> str:
    """One line per feature and place it installs into: feature, scope, toolbox, where, state (with the drift)."""
    rows = []
    for feature in sorted(data["features"], key=lambda f: (f["scope"] != "global", f["name"], f["toolbox"])):
        for place in feature["installs"]:
            state = place["state"]
            if state == "drifted":
                state += f" ({drift_text(place['drift'])})"
            rows.append((feature["name"] + ("*" if feature["always"] else ""), feature["scope"], feature["toolbox"],
                         place["where"], state))
    header = ("FEATURE", "SCOPE", "TOOLBOX", "WHERE", "STATE")
    widths = [max(len(row[i]) for row in [header, *rows]) for i in range(len(header))]
    lines = ["  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip() for row in [header, *rows]]
    lines.append("")
    lines.append("* always installed. States: in-sync, drifted (edited: changed on disk; outdated: the source has "
                 "changed, a sync updates it; missing), pending-install and pending-removal (a sync does it), off.")
    lines.extend(f"problem: {problem}" for problem in data["problems"])
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------------------------------------------------
# The config edit

FEATURES_LINE = re.compile(r"^features:[ \t]*(.*?)[ \t]*$")
ITEM = re.compile(r"^(\s*)-\s+(.*?)\s*(#.*)?$")


def split_comment(text: str) -> tuple[str, str]:
    """(value, comment) of what follows `key:` on a line: the comment starts at a `#` that begins the text or follows
    whitespace."""
    match = re.search(r"(^|\s)#", text)
    if not match:
        return text.strip(), ""
    return text[:match.start()].strip(), text[match.start():].strip()


def edit_features(text: str, enable: list[str], disable: list[str]) -> str:
    """`text` (a YAML file) with `enable`d names added to its top-level `features` list and `disable`d ones dropped,
    keeping every comment and the list's style (one-line `[a, b]`, or a block of `- a` lines). The caller has already
    dropped names that need no change. A file with no `features` key gets one at the end."""
    lines = text.split("\n")
    at = next((i for i, line in enumerate(lines) if FEATURES_LINE.match(line)), None)
    if at is None:
        body = text if not text or text.endswith("\n") else text + "\n"
        return body + "features:\n" + "".join(f"  - {name}\n" for name in enable)
    value, comment = split_comment(FEATURES_LINE.match(lines[at]).group(1))
    if value.startswith("["):
        if not value.endswith("]"):
            raise FeaturesError("`features:` is a flow list that spans lines; write it on one line or as a block list "
                                "of `- name` lines, then run this again")
        current = [name.strip().strip("'\"") for name in value[1:-1].split(",") if name.strip()]
        names = [name for name in current if name not in disable] + [name for name in enable if name not in current]
        lines[at] = f"features: [{', '.join(names)}]" + (f"  {comment}" if comment else "")
    elif value in ("", "null", "~"):
        items, i = [], at + 1  # the block: item lines, with any blank or comment lines between them
        while i < len(lines) and (ITEM.match(lines[i]) or not lines[i].strip() or lines[i].lstrip().startswith("#")):
            if ITEM.match(lines[i]):
                items.append(i)
            i += 1
        indent = ITEM.match(lines[items[0]]).group(1) if items else "  "
        drop = [i for i in items if ITEM.match(lines[i]).group(2).strip("'\"") in disable]
        lines[(items[-1] if items else at) + 1:(items[-1] if items else at) + 1] = [f"{indent}- {n}" for n in enable]
        for i in reversed(drop):
            del lines[i]
        if value:
            lines[at] = "features:" + (f"  {comment}" if comment else "")
    else:
        raise FeaturesError(f"`features:` is `{value}`; write it as a list")
    return "\n".join(lines)


def write_atomic(path: Path, text: str) -> None:
    handle, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(text)
        os.chmod(temp, path.stat().st_mode & 0o777)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def picked_in(path: Path) -> list[str]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else None
    value = (raw or {}).get("features") if isinstance(raw, dict) else None
    return [str(name) for name in value] if isinstance(value, list) else []


# ---------------------------------------------------------------------------------------------------------------------
# What a disable could pull files out from under


def project_folders(root: Path | None, worktrees: Path | None) -> list[Path]:
    """Claude Code's transcript folders for the sessions started in a workspace (or in one of its worktrees), or for
    every project when `root` is None. A folder is named for the working directory with each character that isn't a
    letter or digit turned into `-`."""
    base = Path.home() / ".claude" / "projects"
    if not base.is_dir():
        return []
    if root is None:
        return [folder for folder in base.iterdir() if folder.is_dir()]
    name = re.sub(r"[^A-Za-z0-9]", "-", str(root))
    under = re.sub(r"[^A-Za-z0-9]", "-", str(worktrees)) if worktrees else None
    return [folder for folder in base.iterdir() if folder.is_dir() and (
        folder.name == name or (under and (folder.name == under or folder.name.startswith(under + "-"))))]


def other_sessions(root: Path | None, worktrees: Path | None) -> list[dict]:
    """Sessions with a transcript written in the last hour, other than this one."""
    own, now, found = os.environ.get(ENV_SESSION), datetime.now().timestamp(), []
    for folder in project_folders(root, worktrees):
        for transcript in folder.glob("*.jsonl"):
            age = (now - transcript.stat().st_mtime) / 60
            if age <= SESSION_WINDOW_MINUTES and transcript.stem != own:
                found.append({"session": transcript.stem, "project": folder.name, "minutes_ago": round(age)})
    return sorted(found, key=lambda session: session["minutes_ago"])


def held_locks(cfg: config.Config) -> list[dict]:
    base = Path(os.environ.get(ENV_LOCKS) or Path.home() / ".claude" / "locks") / cfg.name
    held = []
    for folder in sorted(base.iterdir()) if base.is_dir() else []:
        if not folder.is_dir() or ".stale-" in folder.name:
            continue
        try:
            owner = json.loads((folder / "owner.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            owner = {}
        expires = owner.get("expires")
        if expires and datetime.fromisoformat(expires) <= datetime.now(timezone.utc):
            continue
        held.append({"name": folder.name, "task": owner.get("task"), "manual": bool(owner.get("manual"))})
    return held


def in_progress_tasks(cfg: config.Config) -> tuple[list[dict], list[str]]:
    """(the tasks `in-progress`, what couldn't be checked). A workspace with no vault has no task records, so nothing to
    check; a vault that can't be scanned, or a record whose frontmatter can't be read, is named, so the warning never
    says "no tasks" about records it didn't see."""
    if cfg.vault_dir is None:
        return [], []
    import records  # noqa: PLC0415 (only a workspace with a vault has task records)

    try:
        scan = records.Store(cfg).scan()
    except records.TaskError as error:
        return [], [str(error)]
    tasks = [{"task": record.work_id, "summary": record.summary} for record in scan.records
             if record.status == "in-progress"]
    return tasks, [f"{path.name}: {why}" for path, why in scan.unreadable]


def concurrent(cfg: config.Config | None) -> dict:
    """What a disable could affect. For a workspace: its in-progress tasks, task worktrees, held services locks and
    other sessions started in it or its worktrees. For the global scope (`cfg` None): other sessions anywhere, since
    every workspace's sessions read `~/.claude`."""
    if cfg is None:
        return {"sessions": other_sessions(None, None)}
    base = cfg.worktrees_dir
    tasks, unchecked = in_progress_tasks(cfg)
    return {
        "tasks": tasks,
        "tasks_not_checked": unchecked,
        "worktrees": sorted(p.name for p in base.iterdir() if p.is_dir()) if base.is_dir() else [],
        "locks": held_locks(cfg),
        "sessions": other_sessions(cfg.root, base),
    }


# ---------------------------------------------------------------------------------------------------------------------
# Enable and disable


def plan_change(action: str, names: list[str], scope: str, listed: dict, cfg: config.Config | None) -> dict:
    """Check `names` against the listing and the scope's current picks. Returns {"change": [...], "same": [...]}: the
    names the config will gain or lose, and those it already has (or lacks). Raises FeaturesError for an unknown
    name, one of the other scope, one a picked feature requires (disable) or one that requires a feature the scope
    doesn't pick, or whose config the workspace lacks (enable)."""
    by_name = {}
    for feature in listed["features"]:
        if not feature["always"]:
            by_name.setdefault(feature["name"], []).append(feature)
    unknown = [name for name in names if name not in by_name]
    if unknown:
        raise FeaturesError(f"no such feature: {', '.join(unknown)}. Run `toolbox.py features` to list them")
    wrong = [name for name in names if scope not in {f["scope"] for f in by_name[name]}]
    if wrong:
        other = "workspace" if scope == "global" else "global"
        raise FeaturesError(f"{', '.join(wrong)}: a {other} feature, not a {scope} one (use --scope {other})")
    picked = picked_in(machine_file()) if cfg is None else cfg.features
    change = [n for n in dict.fromkeys(names) if (n not in picked if action == "enable" else n in picked)]
    same = [n for n in dict.fromkeys(names) if n not in change]
    after = [n for n in picked if n not in change] if action == "disable" else [*picked, *change]
    requires = {f["name"]: f["requires"] for f in listed["features"] if f["scope"] == scope}
    if action == "enable":
        needs = {f["name"]: f["needs"] for f in listed["features"] if f["scope"] == scope}
        lacking = {n: [r for r in requires.get(n, []) if r not in after and r not in ("core",)] for n in change}
        lacking = {n: needed for n, needed in lacking.items() if needed}
        if lacking:
            raise FeaturesError("; ".join(
                f"{n} requires {', '.join(needed)}: enable {'them' if len(needed) > 1 else 'it'} too"
                for n, needed in lacking.items()))
        if cfg is not None:
            unmet = {n: [key for key in needs.get(n, []) if not cfg.is_set(key)] for n in change}
            unmet = {n: keys for n, keys in unmet.items() if keys}
            if unmet:
                raise FeaturesError("; ".join(f"{n} needs {', '.join(keys)}, which this workspace's config doesn't "
                                              "set (add it, or run /setup-workspace)" for n, keys in unmet.items()))
    else:
        needed_by = {n: [p for p in after if n in requires.get(p, [])] for n in change}
        needed_by = {n: by for n, by in needed_by.items() if by}
        if needed_by:
            raise FeaturesError("; ".join(f"{n} is required by {', '.join(by)}: disable them too"
                                          for n, by in needed_by.items()))
    return {"change": change, "same": same}


def installer_args(scope: str, root: Path | None, listed: dict, names: list[str]) -> list[str]:
    """The installer's arguments that act on the scope: a workspace's folder, or the toolboxes the global features
    belong to."""
    if scope == "workspace":
        return ["--workspace", str(root)]
    toolboxes = {f["toolbox"] for f in listed["features"] if f["scope"] == "global" and f["name"] in names}
    return [arg for toolbox in sorted(toolboxes) for arg in ("--toolbox", toolbox)]


def sections_of(preview: dict, scope: str) -> dict:
    """The preview's sections that have items, the unchanged ones as a count only. A workspace preview runs without
    the global parts, so it can't see the global features machine.yml picks and warns that they install nothing: those
    warnings are dropped."""
    sections = {title: items for title, items in preview["sections"].items() if title in SHOWN_SECTIONS and items}
    if scope == "workspace" and "Warnings" in sections:
        sections["Warnings"] = [warning for warning in sections["Warnings"]
                                if not warning.startswith("machine.yml: feature ")]
        if not sections["Warnings"]:
            del sections["Warnings"]
    return {"clean": preview["clean"], **sections, "Unchanged": len(preview["sections"].get("Unchanged", []))}


def run(action: str, names: list[str], scope: str, apply: bool, confirm: bool) -> tuple[int, dict]:
    """Preview or apply an enable or disable. Returns (exit code, JSON to print): 0 ok, 3 refused, 4 the installer
    reported a conflict, problem or error after the config was written."""
    cfg = config.load() if scope == "workspace" else None
    listed = listing()
    target = cfg.path if cfg else machine_file()
    plan = plan_change(action, names, scope, listed, cfg)
    out = {"action": action, "scope": scope, "config": str(target), "features": plan["change"],
           "already": plan["same"], "applied": False}
    if not plan["change"]:
        out["note"] = f"nothing to {action}: every feature already is that way"
        return 0, out

    extra = installer_args(scope, cfg.root if cfg else None, listed, plan["change"])
    flags = [arg for name in plan["change"] for arg in (f"--{action}", name)]
    proc = run_installer("--json", *extra, *flags)
    try:
        out["preview"] = sections_of(json.loads(proc.stdout), scope)
    except ValueError:
        raise FeaturesError("the installer's preview failed: " + (proc.stderr or proc.stdout).strip()) from None
    if action == "disable":
        out["concurrent"] = concurrent(cfg)
    if not apply:
        out["next"] = (f"run again with --apply{' --confirm-concurrent' if action == 'disable' else ''} to write "
                       f"{target} and sync")
        return 0, out
    if action == "disable" and not confirm:
        out["refused"] = ("a disable removes files other sessions may be using; nothing was written or removed. Show "
                          "the user `concurrent`, and run again with --confirm-concurrent once they have read it")
        return 3, out

    text = target.read_text(encoding="utf-8") if target.is_file() else ""
    enabling, disabling = (plan["change"], []) if action == "enable" else ([], plan["change"])
    new = edit_features(text, enabling, disabling)
    expected = [n for n in picked_in(target) if n not in plan["change"]] if action == "disable" \
        else [*picked_in(target), *plan["change"]]
    with tempfile.TemporaryDirectory() as scratch:
        probe = Path(scratch) / "probe.yml"
        probe.write_text(new, encoding="utf-8")
        result = picked_in(probe)
    if result != expected:
        raise FeaturesError(f"couldn't edit {target} safely (the list would read {result}, not {expected}); edit "
                            "its `features` by hand")
    write_atomic(target, new)
    sync = run_installer("--apply", *extra)
    out["applied"] = True
    out["installer"] = {"exit": sync.returncode, "output": sync.stdout.strip().splitlines(),
                        "errors": sync.stderr.strip()}
    return (0 if sync.returncode == 0 else 4), out
