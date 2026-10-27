#!/usr/bin/env python3
"""Turn detect.py's values and Allen's answers into a workspace's toolbox config, for /setup-workspace.

Usage:
  draft.py render <detect.json> [--mcp KEY=VALUE]... [--set KEY=VALUE]... [--drop KEY]... [--write [--force]]
  draft.py add-workspace <workspace> [--dry-run]

render prints JSON: `yaml` (the config, each value commented with its origin: detected, default or asked), `valid`,
`errors` (the schema's), `missing` (keys still without a value) and `unanswered` (keys detect.py said to ask that no
--set answered). --mcp gives a value the skill read through an MCP tool (origin detected); --set gives Allen's answer
(origin asked); --drop removes a key and everything under it (`--drop pr.announce` for no announcement). VALUE is read
as YAML, so `true`, `[a, b]` and `{unit: make test}` work. Keys that don't apply to the tracker are left out:
`tracker.jira.*` unless it is jira, `tracker.key_prefix` unless it is vault. --write writes
`<workspace>/.claude/toolbox.local.yml` only when the config is valid with nothing missing or unanswered, and won't
replace an existing one without --force.

add-workspace adds the workspace to machine.yml's `workspaces:` (creating machine.yml from machine.example.yml when
there is none), keeping the file's other text. Prints {"machine", "added", "already"}.

Exit codes: 0 ok, 1 not valid or not written, 2 bad arguments.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "workspace" / ".claude" / "toolbox" / "lib"))

import config  # noqa: E402

OK, INVALID, USAGE = 0, 1, 2
MACHINE = REPO / "machine.yml"
MACHINE_EXAMPLE = REPO / "machine.example.yml"
HEADER = """\
# This workspace's toolbox config, written by /setup-workspace. Never tracked; credentials never go here.
# Each value's comment says where it came from: detected (read from the workspace, git, gh or an MCP tool), default,
# or asked. Every key and its default: claude-toolbox/workspace/.claude/toolbox/templates/toolbox.local.example.yml.
# Check it with `.claude/toolbox/bin/toolbox.py config validate`; re-sync the toolbox after changing it.
"""


def parse_pair(text: str) -> tuple[str, object]:
    key, sep, value = text.partition("=")
    if not sep or not key:
        raise argparse.ArgumentTypeError(f"{text!r}: use KEY=VALUE")
    return key.strip(), yaml.safe_load(value) if value.strip() else None


def applies(key: str, kind: str) -> bool:
    if key.startswith("tracker.jira."):
        return kind == "jira"
    if key == "tracker.key_prefix":
        return kind == "vault"
    return True


def merge(detected: dict, mcp: list, answers: list, drops: list) -> dict:
    values = {key: dict(value) for key, value in detected.items()}
    for key, value in mcp:
        values[key] = {**values.get(key, {}), "value": value, "origin": "detected", "from": "MCP"}
    for key, value in answers:
        values[key] = {**values.get(key, {}), "value": value, "origin": "asked", "from": "Allen", "answered": True}
    for drop in drops:
        for key in [key for key in values if key == drop or key.startswith(drop + ".")]:
            del values[key]
    kind = (values.get("tracker.kind") or {}).get("value")
    return {key: value for key, value in values.items() if applies(key, kind)}


def nest(values: dict) -> dict:
    tree: dict = {"version": config.VERSION}
    for key, item in values.items():
        if item.get("value") is None:
            continue
        *parents, leaf = key.split(".")
        node = tree
        for part in parents:
            node = node.setdefault(part, {})
        node[leaf] = item["value"]
    return tree


def flow(key: str, value) -> str:
    text = yaml.safe_dump({key: value}, default_flow_style=True, sort_keys=False, width=10**9).strip()
    return text[1:-1]


def comment(item: dict) -> str:
    if item.get("value") is None:
        why = item.get("ask") or ("read with " + ", ".join(item["mcp"]) if item.get("mcp") else "needs a value")
        return f"  # asked, not answered yet: {why}"
    origin, source = item["origin"], item.get("from")
    return f"  # {origin}" + (f": {source}" if source and source not in (origin, "Allen") else "")


def render_yaml(values: dict) -> str:
    lines = [HEADER, "version: 1"]

    def walk(spec, path: str, indent: str) -> None:
        for name, sub in spec.fields.items():
            key = f"{path}.{name}" if path else name
            if key == "version":
                continue
            if key in values:
                item = values[key]
                value = item.get("value")
                if isinstance(value, dict) and value and all(isinstance(v, dict) for v in value.values()):
                    lines.append(f"{indent}{name}:{comment(item)}")
                    lines.extend(f"{indent}  {flow(child, sub_value)}" for child, sub_value in value.items())
                elif value is None:
                    lines.append(f"{indent}{name}:{comment(item)}")
                else:
                    lines.append(f"{indent}{flow(name, value)}{comment(item)}")
            elif isinstance(sub, config.Section) and any(k.startswith(key + ".") for k in values):
                if not indent:
                    lines.append("")
                lines.append(f"{indent}{name}:")
                walk(sub, key, indent + "  ")

    walk(config.SCHEMA, "", "")
    return "\n".join(lines) + "\n"


def render(args) -> int:
    report = json.loads(Path(args.detect).read_text(encoding="utf-8"))
    root = Path(report["workspace"])
    values = merge(report["values"], args.mcp, args.set, args.drop)
    text = render_yaml(values)
    _, errors = config.validate(nest(values))
    missing = [key for key, item in values.items() if item.get("value") is None]
    unanswered = [key for key, item in values.items() if "ask" in item and not item.get("answered")]
    result = {"workspace": str(root), "yaml": text, "valid": not errors, "errors": errors, "missing": missing,
              "unanswered": unanswered, "written": None}
    code = OK if not errors and not missing else INVALID
    if args.write:
        target = root / config.CONFIG_PATH
        if errors or missing or unanswered:
            result["error"] = "not written: fix the errors and answer every missing and unanswered key first"
            code = INVALID
        elif target.exists() and not args.force:
            result["error"] = f"not written: {target} exists; pass --force to replace it"
            code = INVALID
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            result["written"] = str(target)
    print(json.dumps(result, indent=2))
    return code


def add_workspace(args) -> int:
    root = Path(args.workspace).expanduser().resolve()
    machine = Path(os.environ.get("TOOLBOX_MACHINE") or MACHINE)
    if not machine.exists():
        if not args.dry_run:
            shutil.copyfile(MACHINE_EXAMPLE, machine)
        text = MACHINE_EXAMPLE.read_text(encoding="utf-8")
    else:
        text = machine.read_text(encoding="utf-8")
    listed = (yaml.safe_load(text) or {}).get("workspaces") or []
    if any(Path(str(item)).expanduser().resolve() == root for item in listed):
        print(json.dumps({"machine": str(machine), "added": None, "already": True}))
        return OK
    home = str(Path.home())
    shown = "~" + str(root)[len(home):] if str(root).startswith(home + os.sep) else str(root)
    updated = insert_workspace(text, shown)
    if not args.dry_run:
        machine.write_text(updated, encoding="utf-8")
    print(json.dumps({"machine": str(machine), "added": shown, "already": False, "dry_run": args.dry_run}))
    return OK


def insert_workspace(text: str, item: str) -> str:
    """machine.yml's text with `item` appended to `workspaces:`, leaving every other line as it is."""
    lines = text.splitlines()
    index = next((i for i, line in enumerate(lines) if line.split("#")[0].rstrip().startswith("workspaces:")), None)
    if index is None:
        return text.rstrip("\n") + f"\nworkspaces:\n  - {item}\n"
    head = lines[index].split("#")[0].rstrip()
    if head.endswith("[]"):
        lines[index] = "workspaces:"
        lines.insert(index + 1, f"  - {item}")
    else:
        end = index + 1
        while end < len(lines) and lines[end].lstrip().startswith("- "):
            end += 1
        lines.insert(end, f"  - {item}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="draft.py", description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    draft = commands.add_parser("render", help="render, check and optionally write the config")
    draft.add_argument("detect", help="detect.py's JSON output, saved to a file")
    draft.add_argument("--mcp", action="append", default=[], type=parse_pair, metavar="KEY=VALUE")
    draft.add_argument("--set", action="append", default=[], type=parse_pair, metavar="KEY=VALUE")
    draft.add_argument("--drop", action="append", default=[], metavar="KEY")
    draft.add_argument("--write", action="store_true")
    draft.add_argument("--force", action="store_true")
    add = commands.add_parser("add-workspace", help="list the workspace in machine.yml")
    add.add_argument("workspace")
    add.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    return render(args) if args.command == "render" else add_workspace(args)


if __name__ == "__main__":
    sys.exit(main())
