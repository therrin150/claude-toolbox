# jira-creator Procedure

What `jira-creator` does for one spawn. A script converts each draft's markdown to the issue format; you send what it
produced, check the result and report. `<root>` is the workspace path the prompt gave; `<scripts>` is
`<root>/.claude/toolbox/bin`.

## 1. Identity check

Before the first write, follow `<root>/.claude/toolbox/references/identity-check.md`. Stop on a mismatch: write
nothing and say so under `Problems`.

## 2. Convert every draft first

For a filing plan, before creating anything, run the converter once over every draft in the plan (the epic's too):

```bash
<scripts>/draft_to_jira.py <draft path>... --ac-field <fields.acceptance_criteria>
```

Pass `--ac-field` with the id from `config show tracker.jira` (`fields.acceptance_criteria`); leave it off when the
config has no such field, and the converter puts the AC in the description instead. It prints one JSON line.

- `"ok": false`: some draft holds markdown it can't convert. **Create nothing, from this draft or any other.** Report
  `Filed: none` and, under `Problems`, each failure's `message` as printed (it names the construct and the draft's
  line). Never fix the draft, convert it by hand or file the others without it: the user fixes the draft and runs
  create-tickets again.
- `"ok": true`: each `drafts[].file` is a JSON file holding that draft's fields. `Read` it when you file that draft.
  `warnings` (a story with no AC) go under `Problems` and don't stop anything; a spike has none.

## 3. Filing from a plan

The plan's rows come from `tasks.py file`. File them in the order given: the epic first, then the stories by number,
so foundation stories get the lower keys. **If the epic fails, stop**: never file stories under no epic. When the
caller asks for the epic alone, file it, return its key and stop.

For each draft, `Read` its converted file and call `createJiraIssue` with:

| Field | What to send |
|---|---|
| `projectKey`, `issueTypeName` | the configured project; the row's `issue_type` |
| `summary` | the file's `summary`, verbatim |
| `description` | the file's `description` as given, with `contentFormat: "adf"` |
| `parent` (in the call) | the row's epic key; an epic has none. For "the epic draft's key", the key you just got for draft `00` |
| acceptance criteria | when the file's `acceptance_criteria` isn't null: `additional_fields` `{"<its field>": <its value>}`, the ADF document as given. When null, the AC are already in the description |
| labels | `additional_fields` `{"labels": [...]}` from the row, always |
| components | `additional_fields` `{"components": [{"name": "..."}]}` from the row's names |
| sprint | only with a sprint id from the caller: `additional_fields` `{"<fields.sprint>": <the bare integer id>}`, never the name or an array. If the create rejects it, create without it and set it afterwards with `editJiraIssue` |
| estimate | only when the row has one: `additional_fields` `{"timetracking": {"originalEstimate": "<it>"}}`. If the create screen lacks it (`getJiraIssueTypeMetaWithFields`, directly or through `executeRead`), set it after creating with `editJiraIssue` and say so |
| assignee | the signed-in user (`assignee_account_id` from `atlassianUserInfo`), unless the caller says otherwise |

Never change the converted content: if it looks wrong to you, send it anyway and say what looked wrong under
`Problems`.

**Testing notes.** When the file's `testing_notes_comment` isn't null, post it right after the issue is created with
`addCommentToJiraIssue` (or `addOrEditJiraIssueComment`): `commentBody` is that string exactly, `contentFormat:
"adf"`. If the comment fails, the issue still counts as filed: report the missing comment. Never re-create an issue to
retry a comment.

**Dependencies.** Once every issue in the run exists, link each story to what it depends on (the row's `depends_on`:
draft numbers you just filed, or keys) with a `Blocks` link: the dependency as `inwardIssue`, the dependent story as
`outwardIssue` (`createIssueLink`, or `createJiraIssueLink` through `executeWrite`). Check the link type's name with
`getIssueLinkTypes` (or `listJiraIssueLinkTypes` through `executeRead`) first if `Blocks` is rejected. Atlassian's
server comes in two shapes: one has a tool per operation, the other runs the rest (issue links, link types,
create-screen fields) by name through `executeRead` / `executeWrite`, with `discover` to find a name rather than
guessing one. Use whichever this server has.

**Errors.** Never retry a create after an ambiguous error (a timeout, a 5xx) without first searching the project for
the summary with `searchJiraIssuesUsingJql`: it may have been created. Partial success is normal: file what you can,
then report exactly what did and didn't file.

## 4. Check what you filed

Before reporting, fetch each new issue with `getJiraIssue` (`fields`: `summary`, `issuetype`, `parent`, `labels`,
`issuelinks`, plus the description and the acceptance-criteria field, with `responseContentFormat: "markdown"`;
nothing else) and confirm its summary, type and parent are the plan's, that its description isn't empty, and that its
AC (in the AC field, or under an `Acceptance Criteria` heading in the description) hold as many `**Scenario:` lines as
the draft's converted AC have scenarios (count the paragraphs of the converted AC whose text starts `Scenario:`). A
mismatch goes under `Problems` with the key; never create a second issue to fix it.

When the report is composed, remove the folder the converter printed as `out` (`rm -r <out>`): it holds only the
converted copies and nothing else uses it.

## 5. Editing an issue

For a change to an existing issue (a field, its AC, a link, a comment), fetch it first with `getJiraIssue`. Change
only what you were asked. A description or AC the caller wants rewritten comes as a draft-shaped file the caller wrote
and approved: convert it with the script as in step 2 and send the result, keeping every other part of the issue as it
is (reads flatten panels, so check the rendered HTML with `expand: "renderedFields"` for any `class="panel"` before
sending a replacement description, and say if the caller's file would lose one). Never replace existing AC unless the
caller passed the replacement as approved; otherwise return the current AC and ask the caller whether to append,
replace or leave it.

## 6. Worklogs

Not yet. When time goes to Jira, a script will compute each item's total and start timestamp and this agent will write
the worklog with `addWorklogToJiraIssue`, so the Jira write gate holds. Until then, a request to log work is reported
under `Problems` as not supported yet, and nothing is written.

## 7. Transitions

For a move to another status (the caller gives the issue key and the Jira status name, `to`), after the identity
check: list the issue's transitions (`getTransitionsForJiraIssue`, or `listJiraIssueTransitions` through `executeRead`;
`discover` names the operation when neither exists) and pick the one whose target status is `to`, ignoring case. If the
issue is already in `to`, change nothing (the report's `Problems` is `none`). If no transition leads there, never guess
another: change nothing and report the issue's current status and `to` under `Problems`. Otherwise call
`transitionJiraIssue` with the cloud id and that transition's id, and nothing else (no fields, no comment unless the
caller asked). A failed call goes under `Problems`.
