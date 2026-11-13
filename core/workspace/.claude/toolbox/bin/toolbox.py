#!/usr/bin/env python3
"""The toolbox's own commands for a workspace. Finds the workspace from the current directory (see lib/config.py).

Commands:
  config show [dotted.key]     The config with defaults applied, or one value, as JSON
  config validate              {"ok": true, "config": <path>} or {"ok": false, "errors": [...]}
  config render                The generated rule's text (.claude/rules/toolbox-config.md)
  config extensions <skill>    The extension docs in .claude/extensions/ that extend a skill (minus
                               extensions.disabled), in name order, as absolute paths in a JSON list. With --apply,
                               {"docs": [...], "apply": <how a skill applies them>}, "apply" only when docs exist
  doctor                       Check the workspace's toolbox setup (see lib/doctor.py): JSON, one line per check, and
                               the MCP checks the toolbox-doctor skill makes; exit 1 when any check isn't ok
  features [--json]            Every feature of every toolbox: its scope, where it's installed and whether the install
                               has drifted from its source (see lib/features.py). Needs no workspace config
  features enable|disable <feature>... --scope global|workspace [--apply] [--confirm-concurrent]
                               Preview adding or dropping features in machine.yml (global) or this workspace's config:
                               what the sync would create, update and remove, and for a disable what could be running on
                               those files (in-progress tasks, task worktrees, held services locks, other sessions).
                               Nothing is written until --apply, which writes the config and syncs; a disable also needs
                               --confirm-concurrent, passed only once the user has read the warning

Exit codes: 0 ok, 1 no config or an invalid one (the errors print as JSON), 2 bad arguments or an unknown key, 3 a
disable applied without --confirm-concurrent (nothing was written), 4 the sync after --apply reported a problem.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import config  # noqa: E402
import doctor  # noqa: E402
import features  # noqa: E402

OK, INVALID, USAGE, REFUSED, SYNC_FAILED = 0, 1, 2, 3, 4

# How a skill applies its extension docs, printed by `config extensions --apply` only when it has some, so the skills
# carry one line instead of this text.
APPLY = (
    "Each `##` section of a doc is a verb and one of the skill's hook ids (its \"Extension Points\" table): at that "
    "hook, follow an `add` section after the hook's own text and a `replace` section instead of it; for a `skip` "
    "section, don't run the step, and list it wherever the skill reports its steps as "
    "`⏭️ <step>: skipped by <doc>: <reason>`. An added step that fails stops the skill for the user, unless "
    "its doc says otherwise, and what a step reports goes in the skill's next report. If two docs replace or skip the "
    "same hook, stop and ask the user which applies."
)


def emit(value) -> None:
    print(json.dumps(value, indent=2, default=str, ensure_ascii=False))


def run_features(args) -> int:
    try:
        if args.action is None:
            listed = features.listing()
            if args.json:
                emit(listed)
            else:
                sys.stdout.write(features.render_listing(listed))
            return OK
        code, out = features.run(args.action, args.names, args.scope, args.apply,
                                 getattr(args, "confirm_concurrent", False))
        emit(out)
        return code
    except features.FeaturesError as error:
        emit({"ok": False, "errors": [str(error)]})
        return USAGE
    except config.ConfigError as error:
        emit({"ok": False, "config": str(error.path) if error.path else None, "errors": error.errors})
        return INVALID


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="toolbox.py", description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    config_parser = commands.add_parser("config", help="read, check and render the workspace config")
    actions = config_parser.add_subparsers(dest="action", required=True)
    show = actions.add_parser("show", help="print the config, or one dotted key, as JSON")
    show.add_argument("key", nargs="?")
    actions.add_parser("validate", help="check the config against the schema")
    actions.add_parser("render", help="print the generated rule")
    extensions = actions.add_parser("extensions", help="list the extension docs that extend a skill")
    extensions.add_argument("skill")
    extensions.add_argument("--apply", action="store_true", help="also print how the skill applies the docs")
    commands.add_parser("doctor", help="check the workspace's toolbox setup")
    feature_parser = commands.add_parser("features", help="list, enable and disable features")
    feature_parser.add_argument("--json", action="store_true", help="print the listing as JSON")
    changes = feature_parser.add_subparsers(dest="action")
    for verb in ("enable", "disable"):
        change = changes.add_parser(verb, help=f"{verb} features in a scope's config")
        change.add_argument("names", nargs="+", metavar="feature")
        change.add_argument("--scope", required=True, choices=("global", "workspace"))
        change.add_argument("--apply", action="store_true", help="write the config and sync (default: preview only)")
        if verb == "disable":
            change.add_argument("--confirm-concurrent", action="store_true",
                                help="the user has read what a disable could affect (required with --apply)")
    args = parser.parse_args(argv)

    if args.command == "features":
        return run_features(args)

    if args.command == "doctor":
        checks, mcp, workspace = doctor.run()
        sys.stdout.write(doctor.render(checks, mcp, workspace))
        return OK if all(check["status"] == "ok" for check in checks) else INVALID

    try:
        loaded = config.load()
    except config.ConfigError as error:
        emit({"ok": False, "config": str(error.path) if error.path else None, "errors": error.errors})
        return INVALID

    if args.action == "validate":
        emit({"ok": True, "config": str(loaded.path)})
    elif args.action == "show":
        if not args.key:
            emit(loaded.data)
            return OK
        try:
            emit(loaded.get(args.key))
        except KeyError as error:
            emit({"ok": False, "errors": [f"{error.args[0]}: no such key"]})
            return USAGE
    elif args.action == "render":
        sys.stdout.write(config.render_rule(loaded))
    elif args.action == "extensions":
        docs = [str(path) for path in loaded.extensions(args.skill)]
        emit(({"docs": docs, "apply": APPLY} if docs else {"docs": docs}) if args.apply else docs)
    return OK


if __name__ == "__main__":
    sys.exit(main())
