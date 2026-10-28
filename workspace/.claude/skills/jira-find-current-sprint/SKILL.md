---
name: jira-find-current-sprint
description: Resolve the current sprint, the next sprint, or a sprint by name to its Jira name and id from live data, never by date math. Use when Allen says "this sprint", "next sprint" or names one, and when another skill needs a sprint id. Read-only in Jira.
---

# Find Current Sprint

Resolves a sprint reference to its name, id, state and dates. It never moves an issue or touches a sprint; the only
thing it writes is its own cache. Other skills call this rather than working sprints out themselves.

```
/jira-find-current-sprint                  # the active sprint
/jira-find-current-sprint refresh          # the active sprint, skipping the cache
/jira-find-current-sprint next             # the earliest future sprint
/jira-find-current-sprint "<sprint name>"  # a sprint by name, to its id
```

## Config

The toolbox-config rule names the Jira server and cloud id; `toolbox.py config show tracker.jira` gives the rest:

| Key | Use |
|---|---|
| `project` | the project whose issues are searched |
| `fields.sprint` | the sprint field's id: read as an array of sprint objects, written as the bare integer id |
| `board` | optional: only sprints with this `boardId` count. Without it, sprints from more than one board are a stop |

With no `fields.sprint`, stop: the field's id goes in the config (`/setup-workspace` finds it by name with the
`discover` tool, or `getJiraIssueTypeMetaWithFields`).

Sprints are read off the issues in them, through `searchJiraIssuesUsingJql`, because the Atlassian connector has no
board or sprint API. So a sprint with no issues in it can't be found.

**Never derive a sprint from its name or from today's date.** A name's date may be its start or its end, its padding
and labels drift, and sprints close hours after they end. Jira's `state` (`active`, `future`, `closed`) is the only
reliable answer.

## Procedure

Every query requests only `fields: ["<sprint field>"]`, with the cloud id; the default field set is far larger and
isn't needed. From the results, collect the distinct sprint objects (only `board`'s, when it's set), then pick by
`state` as below. Get the current time with `date -u +%Y-%m-%dT%H:%M:%SZ` to compare against `endDate`.

### Current (the default)

Check the cache first (see **Cache**). Query Jira only on a miss, or with `refresh`:

```
project = <project> AND sprint in openSprints() ORDER BY created DESC
```

`maxResults: 10`. Pick the sprint with `state == "active"`.

- **Exactly one, and now is before its `endDate`:** that's the answer.
- **Exactly one, but now is past its `endDate`:** it's planning day: the sprint has ended but nobody has closed it, and
  Allen usually means the one being planned. Resolve **next** too and return both, flagged as ambiguous.
- **None:** the gap between one sprint closing and the next starting. Resolve **next** and return it, flagged.
- **More than one:** stop and list them, with their boards. Don't pick one.

### Next

```
project = <project> AND sprint in futureSprints() ORDER BY created DESC
```

`maxResults: 50`. Of the `state == "future"` sprints, pick the earliest: a future sprint has no dates until it starts,
so order by the date in its name when every name has one (parsed loosely, padded or not), else by id, the lower id
winning a tie. This is the one place a name is read, and only to order sprints Jira already says are future.

Nothing back means the next sprint holds no issues yet: ask for its name or id. Don't guess an id.

### By name

```
project = <project> AND sprint = "<name>"
```

`maxResults: 1`. Take the sprint object whose `name` equals the given name exactly. A name Jira doesn't know fails the
query ("does not exist for the field 'sprint'"); a sprint with no issues returns nothing. Either way, report it and ask
for the id.

### Cache

The current sprint changes once a sprint, so it's cached per workspace at
`~/.claude/cache/<workspace name>/current-sprint.json` (the workspace name is in the toolbox-config rule):

```json
{"name": "<name>", "id": 123, "startDate": "<iso>", "endDate": "<iso>", "resolvedAt": "<iso>"}
```

- **Hit:** the file parses and today's UTC date (`date -u +%Y-%m-%d`) is before the date part of `endDate`. Answer from
  it. On the end date itself the cache is stale by design: that's planning day, and the rules above need live data.
- **Miss:** anything else. Query Jira.
- **Write** only an unflagged current result (exactly one active sprint, now before its `endDate`), with the current
  time as `resolvedAt`, making the folder if needed. Never cache a flagged, **next** or **by name** result.

If Jira rejects a cached id (a sprint closed early, say), run again with `refresh`.

## Output

One line per sprint, for Allen and the calling skill alike:

```
Current sprint: <name> (id 123, 2026-10-05 → 2026-10-19, active)
```

Add `(cached)` when the answer came from the cache. In a flagged case, add the next sprint on its own line and say in a
few words why it's ambiguous (past its end date, or no active sprint).

## For calling skills

- **The vault gets the name** (a draft's `sprint: <name>`). Never write `current` or `next`: those drift, so a draft
  filed two weeks after it was written would land in the wrong sprint.
- **Jira gets the id,** in the sprint field as a bare integer: not the name, not an array. If a create call rejects
  it, create the issue without it and set it afterwards with `editJiraIssue`.
- **A flagged result needs Allen's pick** before anything is written, in the vault or in Jira.
