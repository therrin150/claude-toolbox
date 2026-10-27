# Jira Write-Back

With `tracker.jira.write_back: transitions`, a task's status change moves its Jira issue too. `tasks.py set` can't call
Jira, so it records the intended move in its output and the session makes the call:

```json
"write_back": {"tool": "mcp__<server>__transitionJiraIssue", "cloud_id": "<id>", "key": "ABC-12",
               "status": "review", "to": "In Review", "how": "references/jira-write-back.md"}
```

`write_back` is `null` when there is nothing to do: the config says `none`, the status didn't change, the task has no
key, it's an epic, or the config names no Jira status for the new one (by default only `in-progress`, `review` and
`done` move the issue; `tracker.jira.statuses` changes or adds to that).

## Steps

1. Run the identity check (`references/identity-check.md`) if this session hasn't yet.
2. List the issue's transitions (`getTransitionsForJiraIssue`, or `listJiraIssueTransitions` through `executeRead`;
   `discover` names the operation when neither exists) and pick the one whose target status is `to`, ignoring case.
3. If the issue is already in `to`, do nothing. If no transition leads there, don't guess another: log it on the record
   (`tasks.py log <id> "Jira: no transition from <current> to <to>"`) and tell the user.
4. Otherwise transition it, passing the cloud id. A failed call is reported and logged the same way; the task record
   keeps its new status either way, since the record is the source of truth for the workflow.
