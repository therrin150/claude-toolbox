#!/usr/bin/env python3
"""The toolbox's own commands for a workspace. Finds the workspace from the current directory (see lib/config.py).

Commands:
  config show [dotted.key]     The config with defaults applied, or one value, as JSON
  config validate              {"ok": true, "config": <path>} or {"ok": false, "errors": [...]}
  config render                The generated rule's text (.claude/rules/toolbox-config.md)
  config extensions <skill>    The extension docs in .claude/extensions/ that extend a skill (minus
                               extensions.disabled), in name order, as absolute paths in a JSON list
  doctor                       Check the workspace's toolbox setup (see lib/doctor.py): JSON, one line per check, and
                               the MCP checks the toolbox-doctor skill makes; exit 1 when any check isn't ok

Exit codes: 0 ok, 1 no config or an invalid one (the errors print as JSON), 2 bad arguments or an unknown key.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import config  # noqa: E402
import doctor  # noqa: E402

OK, INVALID, USAGE = 0, 1, 2


def emit(value) -> None:
    print(json.dumps(value, indent=2, default=str))


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
    commands.add_parser("doctor", help="check the workspace's toolbox setup")
    args = parser.parse_args(argv)

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
        emit([str(path) for path in loaded.extensions(args.skill)])
    return OK


if __name__ == "__main__":
    sys.exit(main())
