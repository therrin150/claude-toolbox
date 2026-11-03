# A New Record

Read from SKILL.md's Setup, step 2, when `context` prints `needs_import`, `needs_scope` or `needs_requirement`. Before
writing a record's sections, read `.claude/toolbox/references/ticket-docs.md` ("Task records" and "Acceptance
Criteria") for their layout; fields change only through `<bin>/tasks.py`.

## `needs_import`

Exit 1, `jira` tracker: no record has this key. Fetch the issue (`getJiraIssue` with the rule's cloud id,
`responseContentFormat: "markdown"`; fields `summary`, `description`, `issuetype`, `parent`, `issuelinks`, `status`,
plus the acceptance-criteria field id from `<bin>/toolbox.py config show tracker.jira.fields`, when there is one). If
it has no acceptance criteria, or its description doesn't say what to change, STOP (there is no record yet, so nothing
to set). Otherwise write to the session scratchpad, as `<KEY>-issue.json`, only what `import` reads, copying each value
exactly (a slip here becomes the record):

```json
{"context": {"cloudId": "<the response's>"}, "key": "<KEY>", "webUrl": "<its URL>", "fields": {
  "summary": "...", "issuetype": {"name": "..."}, "status": {"statusCategory": {"key": "..."}},
  "parent": {"key": "...", "fields": {"issuetype": {"name": "..."}}},
  "issuelinks": [{"type": {"name": "Blocks"}, "inwardIssue": {"key": "..."}}],
  "description": "<markdown>", "<acceptance-criteria field id>": "<markdown, or the ADF object as returned>"}}
```

Leave out `parent` with no parent, and keep only `Blocks` links whose `inwardIssue` is set. Then run
`<bin>/tasks.py import <KEY> --issue <file>`, and run `context` again (with `--limited-scope` again, for a slice).

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
