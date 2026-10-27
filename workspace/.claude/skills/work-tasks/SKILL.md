---
name: work-tasks
description: Start or resume several tasks at once, one work-task session each. With the vault tracker, report what waits on the user and any services lock held, resume tasks handed back to Claude that have no live session, and start new ones from the queue within the WIP limits; with Jira or no tracker, start the tasks the user names (keys, task records, story docs, plans). Use when the user says "/work-tasks", "/work-tasks ABC-12 13", "work these tasks", "what's next", or hands over a batch of keys or doc paths.
allowed-tools: Bash(*), Read, Write, ToolSearch, mcp__ccd_session__start_session, mcp__ccd_session_mgmt__list_sessions
---

# Work Tasks

Fans tasks out to parallel sessions. It only launches them: each session runs `work-task` on its own, with its own
stops, worktrees and progress in its task record. Nothing here touches git, a tracker or a record.

The script `<bin>/dispatch.py plan` (`<bin>` is `.claude/toolbox/bin/`; the toolbox-config rule names its absolute
path, so write that out) does the bookkeeping: it reads the queue or the named sources and prints every session's title
and prompt as JSON. This skill lists sessions, runs it, starts what it says to, and reports.

## Arguments

```
/work-tasks                        # vault tracker: the queue
/work-tasks <task> [task...]       # any tracker: these tasks
/work-tasks ... --dry-run          # report what would start; start nothing
```

A task is a key, a task record's path, a story doc (a draft in a discussion's `tickets/` folder) or a discussion's
`plan.md`, as `work-task` takes them, separated by spaces or commas. A bare number takes the workspace's key prefix
(`4643` → `ABC-4643`). Under the `jira` and `none` trackers there is no queue: with no tasks named, ask for them and
stop.

## Where it runs

Sessions start in this session's folder, so run this from the workspace root (check `pwd` against the workspace path
in the toolbox-config rule; anywhere else, say so and stop), since the workspace's skills and rules load only there.

## Workflow

1. **Load the tools** in one `ToolSearch` call: `select:mcp__ccd_session__start_session,
   mcp__ccd_session_mgmt__list_sessions`, plus `mcp__ccd_session__dismiss_task` with chips and, under the `jira` tracker, `mcp__<server>__getJiraIssue` (server from
   the toolbox-config rule). Skip `start_session` with `--dry-run`.
2. **List the sessions.** `list_sessions` with `limit: 100` (archived excluded), and write the result as JSON to the
   session scratchpad as `sessions.json`. `work-task` names its session `<work id> <summary>` as its first step, so
   that prefix is how a task's live session is found.
3. **Plan.** From the workspace root:
   `<bin>/dispatch.py plan [<task> ...] --sessions <scratchpad>/sessions.json`. On an error, report it and stop.
   - **A named Jira key with no record** comes back with `summary: null`: fetch it (`getJiraIssue`, the rule's cloud
     id, fields `summary` and `status`), skip and report a key that doesn't exist or whose status category is done,
     and run the plan again with `--summary KEY=<summary>` for each one fetched. Don't stop the batch for one bad key.
   - Under the `vault` tracker with no tasks named, also run `<bin>/services_lock.py status` when the workspace has the
     `services-lock` feature, for the report's lock line.
4. **Start** each entry of `launch`, in order, unless `--dry-run`. `start_session` with:

   | Field | Value |
   |---|---|
   | `initiation` | the plan's `initiation`: `user_asked` for `work.dispatch: direct` (the user ran this command), `own_initiative` for `chips` (one card per task; the user starts each with a click) |
   | `title` | the entry's `title`, as is |
   | `prompt` | the entry's `prompt` (`/work-task <ref>`), nothing else: the record holds everything the session needs |
   | `background` | `Runs work-task on <work id>: <summary>. It resumes from the task record on its own.` (at least 80 characters; pad with the task's state when it is short) |
   | `context` | `fresh` |
   | `model` | the plan's `model` (`work.session_model`) |
   | `use_worktree` | `false`: `work-task` makes its own worktrees |

   The plan limits a chips batch to three per turn, the most the app shows; the rest are `next`. When the user says
   "next", run the plan again with `--offered` and the work ids already offered, and offer the next three.
5. **Report** in a short table, one row per entry that isn't `done` or `skipped`: work id, summary, and
   `waiting on you` / `resumed` / `started` / `card offered` (chips) / `already running` (link its session as
   `[its title](#<id>)` with its `local_…` id) / `blocked by …` / `held by WIP` / `next`. With `--dry-run`, say that
   nothing started and use `would resume` / `would start`. Then, in the same order:
   - what's waiting on the user, oldest first, with each `stage` and `work_note`, and any lock held;
   - skipped and done entries with their reason, one line each;
   - under the queue, `held` (the WIP reason) when it is set.

   Add the caveat that applies: tasks that use the same local stack take turns on its services lock, and tasks that
   change a shared library may share local build state (an `extensions` doc says more when the workspace has one).

## The queue (vault tracker)

`tasks.py queue` decides, and `dispatch.py` turns it into groups:

| Group | Tasks | Becomes |
|---|---|---|
| `waiting` | owner is the user, not done, oldest first | reported only; never answered or started here |
| `resume` | owner `claude`, `in-progress`, no live session | a session on `/work-task <work id>` |
| `start` | `todo`, owner `claude`, dependencies done, in backlog order, while Claude has fewer than `work.wip.claude` in progress and the user owns fewer than `work.wip.<user>` open tasks | a session on `/work-task <work id>` |
| `held` | ready, but the WIP limit holds it | reported |
| `blocked` | `todo` with a dependency not done | reported with its blockers |
| `stopped` | parked by the user | reported with `work_note` |

At the user's limit nothing new starts, but `resume` still runs: it is Claude's work handed back, not new work. Named
tasks ignore the limits, because the user chose them.

## After Launch

Each started session reports back here when it ends a turn. Relay it in one line (`<work id>: <what it's waiting on>`);
the task's own state is in its record, not here. Never answer a task session's stop for the user. Withdraw a card they
no longer want with `mcp__ccd_session__dismiss_task` and its `task_id`.
