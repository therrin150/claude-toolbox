# Jira Reads

Every Jira read in a session goes through the `jira-reader` agent, never a direct `getJiraIssue` or
`searchJiraIssuesUsingJql` call: the server's response carries every field, avatars and a rendered copy of the
description, and all of it lands in the session's context. The Jira read gate denies those two tools in the main session;
a subagent (the reader itself, `jira-creator`) is not denied.

## The Call

Spawn `jira-reader` with a prompt of these lines, and nothing else:

```
root: <the workspace's absolute path, from the toolbox-config rule>
issues: <keys, comma-separated>        (or)  jql: <the JQL>
fields: <the fields wanted, in the order wanted: a set from below>
max: <issues to list for a JQL>        (optional, default 50)
save: <absolute path of a .json file in the session scratchpad>   (optional: save mode)
```

The `fields` are the caller's to define: the reader returns exactly those, each in full, and nothing else. Name the
acceptance-criteria and sprint fields with their ids (`<bin>/toolbox.py config show tracker.jira.fields`:
`acceptance_criteria`, `sprint`), as in `acceptance criteria (field id customfield_10085)`.

- **Line mode** (the default, Haiku): the report lists one line per issue, `- <KEY> | <value> | <value> ...`, one value
  per field in the order asked. A multi-line value is joined with ` ¶ `, comments with ` ¶¶ `, a `|` inside a value is
  `\|`, and an empty field is `none`. A key Jira can't read is under `missing`.
- **Save mode** (`save:`, spawn with `model: sonnet`): the reader writes the issues, as `tasks.py` reads them
  (`import --issue`, `file --issues`), to that file, and reports the path and, per issue, its type, status and
  `ac: present|missing`. You never read the file; `tasks.py` does. If `tasks.py` says the file isn't JSON, spawn the
  reader once more with the same prompt and run `tasks.py` again; a second failure is a STOP: report it.

## Field Sets

| Set | Fields | Mode | Used by |
|---|---|---|---|
| `tracker-check` | summary, status, description, acceptance criteria | line | `work-task`, before the first cycle |
| `import` | summary, issuetype, status, parent, issuelinks, labels, description, acceptance criteria | save | `work-task`, `needs_import` |
| `triage` | summary, status category | line | `work-tasks` (a key with no record); `work-task` (a dependency with no record) |
| `filed` | summary, issuetype, parent, labels, issuelinks | save, with a `jql:` | `create-tickets`, after filing |
| `epics` | summary, status | line, with a `jql:` | `propose-tickets` |
| `sprints` | sprints (the sprint field's id) | line, with a `jql:` | `jira-find-current-sprint` |

A skill that needs a field no set has adds it to the prompt and to this table.
