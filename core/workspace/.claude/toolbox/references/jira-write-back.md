# Jira Write-Back

With `tracker.jira.write_back: transitions`, a task's status change moves its Jira issue too. `tasks.py set` can't call
Jira, so it records the intended move in its output and the session has `jira-creator` make the call:

```json
"write_back": {"tool": "mcp__<server>__transitionJiraIssue", "cloud_id": "<id>", "key": "ABC-12",
               "status": "review", "to": "In Review", "how": "references/jira-write-back.md"}
```

`write_back` is `null` when there is nothing to do: the config says `none`, the status didn't change, the task has no
key, it's an epic, or the config names no Jira status for the new one (by default only `in-progress`, `review` and
`done` move the issue; `tracker.jira.statuses` changes or adds to that).

## Steps

The Jira write gate denies the main session a direct `transitionJiraIssue`, so the move goes through `jira-creator`.

1. Spawn `jira-creator` with the workspace root, the `write_back` block's `key`, `to` and `cloud_id`, and "move it to
   `to`". It runs the identity check and follows its procedure's "Transitions" step: it picks the transition whose
   target status is `to`, ignoring case, does nothing if the issue is already there, and never guesses another.
2. Read its report. A `Problems` line (no transition leads to `to`, or the call failed) is logged on the record
   (`tasks.py log <id> "Jira: <the problem>"`) and told to the user. The task record keeps its new status either way,
   since the record is the source of truth for the workflow.
