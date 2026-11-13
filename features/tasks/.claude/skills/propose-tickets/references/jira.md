# Jira

Read by `propose-tickets` under the `jira` tracker only.

## Open epics

Search the configured project for open epics with `searchJiraIssuesUsingJql`, requesting only `summary` and `status`:

```
project = <PROJECT> AND issuetype = <epic issue type> AND statusCategory != Done ORDER BY updated DESC
```

## Draft fields

Only when the user asks for them; filing sends them to Jira, and records don't keep them:

- `estimate` (`1d`)
- `sprint`: a sprint's name. Resolve "this sprint" or "next sprint" with `jira-find-current-sprint` and write the name,
  never the word.
- `labels` beyond the configured ones
- `components` (names)
