#!/usr/bin/env python3
"""Read and write a workspace's task records. Finds the workspace from the current directory (see lib/config.py).

Every command prints JSON. Every command that writes takes --dry-run, which prints what it would do and writes
nothing. A command the workspace's tracker doesn't do fails with the reason.

<ref> is a work id (a key, or a keyless record's slug) or a task record's path. <source> is also a key, a draft in a
discussion's tickets/ folder, or a discussion's plan.md; paths may be relative to the vault.

Any tracker:
  context <source> [--session <id>]   Find or make the task record; print what a session needs. --session appends
                                      the id to `sessions` and reports `new_session`
  work-id <source>                    The work id, key and summary a source has or would get; writes nothing
  get <ref> [field ...]               The record's frontmatter, or some fields of it
  set <ref> field=value ...           Set status, owner, stage, work_note, cycle (shared-library or consumers,
                                      only with shared_libraries configured) or a field the config's
                                      work.extra_fields declares; an empty value clears it
  append <ref> <field> <value> ...    Add to worktrees, sessions, prs or depends_on, skipping values already there
  set-list <ref> <field> [<value> ...]  Replace one of those lists; no values empties it
  log <ref> <text>                    Append a dated entry to the record's `## Log`

jira tracker:
  import <key> --issue <file>         Make a record from a saved getJiraIssue response
  file <discussion>                   The filing plan: the drafts to file in Jira, in order; writes nothing
  file <discussion> --keys NN=KEY ... --issues <file>
                                      Check the keys Jira gave against a saved search response for them, write
                                      them into the drafts and move those into the tasks folder; repair links

vault tracker:
  file <discussion>                   Key every approved draft and move it into the tasks folder; repair links
  next-key [--count <n>]              Hand out keys from the counter, under the tasks lock
  create-defect --summary <text> ...  Write a defect record straight into the tasks folder
  queue                               Waiting on the user, resumable, startable under work.wip, blocked
  epic-status [<key>]                 Each epic's status, from its stories
  check                               Unknown keys, cycles, done depending on undone; exit 1 on any problem

Exit codes: 0 ok, 1 failed (the reason prints as JSON), 2 bad arguments.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import board  # noqa: E402
import config  # noqa: E402
import frontmatter  # noqa: E402
import records  # noqa: E402
import trackers  # noqa: E402

OK, FAILED, USAGE = 0, 1, 2
WRITES = {"context", "set", "append", "set-list", "log", "import", "file", "next-key", "create-defect"}


def emit(value) -> None:
    print(json.dumps(value, indent=2, default=str))


def pairs(values: list[str]) -> dict:
    """`field=value ...`, or `field value` the way sessions sometimes guess it."""
    if len(values) == 2 and "=" not in values[0]:
        values = [f"{values[0]}={values[1]}"]
    out = {}
    for pair in values:
        name, sep, value = pair.partition("=")
        if not sep:
            raise records.TaskError(f"expected field=value, got {pair!r}")
        out[name.strip()] = value
    return out


def list_args(args) -> tuple[str, list[str]]:
    """`field value ...`, or `field=value value ...` the way set takes it."""
    name, sep, first = args.field.partition("=")
    return (name.strip(), ([first] if first else []) + args.values) if sep else (name, args.values)


def run(args, store: records.Store, tracker: trackers.Tracker):
    command = args.command
    dry = getattr(args, "dry_run", False)

    def done(out: dict) -> dict:
        if dry:
            out["dry_run"] = True
        return out

    if command == "context":
        record, made_from = tracker.find_or_make(args.source, dry)
        return store.context(record, made_from, tracker, args.session, dry)
    if command == "work-id":
        return tracker.work_id(args.source)
    if command == "get":
        record = store.find(args.ref)
        store.check_names(record, args.fields)
        fields = {name: record.fields.get(name) for name in args.fields} if args.fields else record.fields
        return {"doc": str(record.path), "work_id": record.work_id,
                "publish_dir": str(store.publish_dir(record.work_id)), "fields": fields}
    if command == "set":
        record = store.find(args.ref)
        values = store.set_fields(record, pairs(args.pairs), dry)
        return done({"doc": str(record.path), "set": values, "write_back": tracker.write_back(record, values)})
    if command == "append":
        record = store.find(args.ref)
        name, values = list_args(args)
        if not values:
            raise records.TaskError("append needs at least one value")
        return done({"doc": str(record.path), "field": name, "added": store.append(record, name, values, dry)})
    if command == "set-list":
        record = store.find(args.ref)
        name, values = list_args(args)
        return done({"doc": str(record.path), "field": name, "values": store.set_list(record, name, values, dry)})
    if command == "log":
        record = store.find(args.ref)
        return done({"doc": str(record.path), "entry": store.log(record, " ".join(args.text), dry)})
    if command == "import":
        return tracker.import_issue(args.key, Path(args.issue).expanduser(), dry)
    if command == "file":
        issues = Path(args.issues).expanduser() if args.issues else None
        return tracker.file(args.discussion, dry, args.keys, issues)
    if command == "next-key":
        if args.count < 1:
            raise records.TaskError("next-key: --count must be at least 1")
        return done(tracker.next_keys(args.count, dry))
    if command == "create-defect":
        values = {name: getattr(args, name) for name in ("summary", "description", "steps", "expected", "actual",
                                                         "acceptance", "found_in", "owner", "epic")}
        values["depends_on"] = args.depends_on or []
        return tracker.create_defect(values, dry)
    if command == "queue":
        return board.queue(store)
    if command == "epic-status":
        return board.epic_status(store, args.key)
    if command == "check":
        return board.check(store)
    raise records.TaskError(f"{command}: unknown command")


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="tasks.py", description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = top.add_subparsers(dest="command", required=True, metavar="<command>")

    def add(name: str, help_text: str) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=help_text)
        if name in WRITES:
            sub.add_argument("--dry-run", action="store_true", help="print what would change; write nothing")
        return sub

    sub = add("context", "find or make a task record; print what a session needs; record the session")
    sub.add_argument("source", help="key, work id, record path, draft path or plan.md path")
    sub.add_argument("--session", help="session id to append to sessions")
    sub = add("work-id", "the work id a source has or would get; writes nothing")
    sub.add_argument("source", help="key, work id, record path, draft path or plan.md path")
    sub = add("get", "a record's frontmatter, or some fields of it")
    sub.add_argument("ref", help="work id or record path")
    sub.add_argument("fields", nargs="*")
    sub = add("set", "set status, owner, stage, work_note, cycle or a declared extra field")
    sub.add_argument("ref", help="work id or record path")
    sub.add_argument("pairs", nargs="+", metavar="field=value")
    for name, text in (("append", "add to a list field"), ("set-list", "replace a list field")):
        sub = add(name, text)
        sub.add_argument("ref", help="work id or record path")
        sub.add_argument("field", help="worktrees, sessions, prs or depends_on (or field=value)")
        sub.add_argument("values", nargs="*")
    sub = add("log", "append a dated entry to the record's ## Log")
    sub.add_argument("ref", help="work id or record path")
    sub.add_argument("text", nargs="+")
    sub = add("import", "make a record from a saved Jira issue response (jira tracker)")
    sub.add_argument("key")
    sub.add_argument("--issue", required=True, help="the saved issue response, as JSON")
    sub = add("file", "key a discussion's approved drafts and move them into the tasks folder")
    sub.add_argument("discussion", help="discussion folder: a path, relative to the vault, or its name")
    sub.add_argument("--keys", nargs="+", metavar="NN=KEY", help="jira: the key Jira gave each draft number")
    sub.add_argument("--issues", help="jira: the saved searchJiraIssuesUsingJql response for those keys")
    sub = add("next-key", "hand out keys from the counter (vault tracker)")
    sub.add_argument("--count", type=int, default=1)
    sub = add("create-defect", "write a defect record into the tasks folder (vault tracker)")
    sub.add_argument("--summary", required=True, help="the symptom, in Title Case")
    sub.add_argument("--description", help="symptom, evidence, cause and fix, as known")
    sub.add_argument("--steps", help="steps to reproduce")
    sub.add_argument("--expected")
    sub.add_argument("--actual")
    sub.add_argument("--acceptance", help="acceptance criteria")
    sub.add_argument("--found-in", dest="found_in", help="where it was found: a key, a version, a page")
    sub.add_argument("--owner", help="claude (default) or the user's owner value")
    sub.add_argument("--epic", help="the epic's key")
    sub.add_argument("--depends-on", dest="depends_on", nargs="*", metavar="KEY")
    add("queue", "the dispatcher's queue (vault tracker)")
    sub = add("epic-status", "each epic's status from its stories (vault tracker)")
    sub.add_argument("key", nargs="?")
    add("check", "check every record's links and fields (vault tracker)")
    return top


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        loaded = config.load()
    except config.ConfigError as error:
        emit({"ok": False, "config": str(error.path) if error.path else None, "errors": error.errors})
        return FAILED
    store = records.Store(loaded)
    try:
        tracker = trackers.get(store)
        tracker.require(args.command)
        plan_only = args.command == "file" and tracker.name == "jira" and not args.keys  # the filing plan
        with store.lock(dry_run=args.command not in WRITES or args.dry_run or plan_only):
            out = run(args, store, tracker)
    except (records.TaskError, frontmatter.FrontmatterError) as error:
        failed = {"ok": False, "error": str(error)}
        if getattr(error, "key", None):
            failed["needs_import"] = error.key
        emit(failed)
        return FAILED
    emit(out)
    return FAILED if args.command == "check" and not out["ok"] else OK


if __name__ == "__main__":
    sys.exit(main())
