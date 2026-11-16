# A New Record

Read from SKILL.md's Setup, step 2, when `context` prints `needs_import`, `needs_scope` or `needs_requirement`. Before
writing a record's sections, read `.claude/toolbox/references/ticket-docs.md` ("Task records" and "Acceptance
Criteria") for their layout; fields change only through `<bin>/tasks.py`.

## `needs_import`

Exit 1, `jira` tracker: no record has this key. Read the issue through `jira-reader` in save mode
(`.claude/toolbox/references/jira-reads.md`, set `import`, `model: sonnet`), with
`save: <the session scratchpad>/<KEY>-issue.json`. If its report says `ac: missing`, STOP (there is no record yet, so
nothing to set). Otherwise run `<bin>/tasks.py import <KEY> --issue <file>`. If it says the file isn't JSON, spawn the
reader once more and import again; a second failure is a STOP. Then run `context` again (with `--limited-scope` again,
for a slice) and read the new record: if its description doesn't say what to change, STOP (the record stays; the user
deletes one they don't want).

## `needs_scope`

A new slice's `## Scope` is empty. Write it with `Edit`, from the user's instructions: the part of the ticket this run
covers, in the terms of its description and acceptance criteria, and nothing they didn't say. Instructions that don't
change what's covered (approvals, preferences) don't go in it.

## `needs_requirement`

The record's `## Description` is empty, as it always is right after a plan's record is made. Read the `plan` (and the
sibling docs it links), then write the record's `## Description` (what to change, and in which areas or repos) and
`## Acceptance Criteria` (how to tell it's done) with `Edit`, in the plan's terms, nothing it doesn't say. The plan
stays the source of truth. If it doesn't say what to change and how to tell it's done, or holds several tasks' worth of
change, leave the record as it is and STOP.
