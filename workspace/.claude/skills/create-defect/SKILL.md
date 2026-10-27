---
name: create-defect
description: Write a defect straight into the tasks folder with the next key, from Allen's or Claude's description of an unrelated problem found along the way. Vault tracker only. Use when Allen says "file a defect", "log a bug", "make a defect for this", or a session finds a problem outside its task.
---

# Create Defect

**Extensions.** Before anything else, run `.claude/toolbox/bin/toolbox.py config extensions create-defect` from the
workspace root (its absolute path is in the toolbox-config rule) and read every file it lists. Each `##` section of an
extension names a heading of this skill: follow a `Replaces` section instead of that heading's text, and an `Adds to`
section after it. If two extensions replace the same heading, stop and ask the user which applies.

A defect is an unrelated problem found along the way. One found while testing a task is fixed in that task, not filed.
Everything else starts as a discussion draft (`propose-tickets`); a defect is the one record written straight into the
tasks folder.

**Vault tracker only.** The toolbox-config rule names the tracker. Under `jira`, draft the defect in a discussion's
`tickets/` folder (`type: defect`) and file it with `create-tickets`, or ask the `jira-filer` agent to file it directly;
under `none` there is no tracker to file it in. Say which and stop.

## 1. Gather

From the description you were given, fill in what a session needs to reproduce and fix it:

| Option | Holds |
|---|---|
| `--summary` | the symptom, in Title Case, per the ticket-docs rule (`Shelf Count Wrong After Move`) |
| `--description` | what was seen, where and by whom, the evidence, then cause and fix as far as they're known |
| `--steps` | numbered steps to reproduce, when it isn't obvious |
| `--expected`, `--actual` | what should happen, and what does |
| `--acceptance` | usually brief: the expected result at the relevant repro step |
| `--found-in` | where it was found, when known: a key, a version, a page |
| `--owner` | `claude` (the default), or Allen's owner value for work only he can do |
| `--epic`, `--depends-on` | only when they're known |

Ask only for what's missing to reproduce it. Don't investigate the cause beyond what you already know: that's the
defect's work.

## 2. Write

```bash
.claude/toolbox/bin/tasks.py create-defect --summary "..." --description "..." --steps "..." --expected "..." \
  --actual "..." --acceptance "..." [--found-in ...] [--owner ...] [--epic ...] [--depends-on KEY ...] --dry-run
```

Show Allen the dry run's key and summary when he didn't ask for the defect himself; then run it without `--dry-run`.
It takes the next key under the tasks lock, so parallel sessions never share one.

## 3. Report

The key, the summary and the record's path. Nothing else changes: the defect waits in the queue like any task.
