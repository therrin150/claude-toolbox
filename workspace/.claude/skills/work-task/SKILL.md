---
name: work-task
description: Work one task end to end in its own worktrees, from implementation through review, publish and merge, resumable in any session. Use for "/work-task ABC-12", "work ABC-12", "work this plan", "pick up ABC-12 again".
allowed-tools: Bash(*), Read, Edit, Write, Grep, Glob, Agent, Skill
---

# Work Task

**Extensions.** Before anything else, run `.claude/toolbox/bin/toolbox.py config extensions work-task --apply` from the
workspace root (its absolute path is in the toolbox-config rule). If it lists docs, read each and apply them at this
skill's hooks ("Extension Points" below) as its `apply` text says.

Drives one task to a merged change: implement, format, unit and integration tests, code review and independent
verification, then two stops for the user, to review the code and then the commit messages. Their approval at the
second publishes, and their merge instruction merges and cleans up. The **work id** names the task everywhere: its
ticket key, else the slug of its summary (the toolbox-config rule's tracker line says which this workspace has). The
task's record holds all its progress, so any session can resume it.

It composes the toolbox's skills and scripts named in its phase files; each owns its own mechanics, so follow them
rather than redoing their work inline. The scripts are in `.claude/toolbox/bin/` (`<bin>` below), whose absolute path
the toolbox-config rule names: write that path out in every call, since a shell that moved into a worktree can't use
the relative one. Every script prints JSON.

The body is split by phase, and a session reads only the phase its record names ("Dispatch"). At the long waits it
ends with a prompt to paste into a new session (`references/hand-off.md`), so a phase that resumes hours later starts
from the record in a small session instead of rebuilding a large one.

## Arguments

```
/work-task <KEY | draft path | plan.md path>
/work-task <KEY> --limited-scope <instructions>
/work-task <work id>          # a resume
```

- **A ticket key:** its record. Under the `jira` tracker, an issue with no record yet is imported (Setup, step 2).
- **A draft** in a discussion's `tickets/` folder: under the `none` tracker it is copied into a record; under `vault`
  and `jira` it must be filed first (`create-tickets`), and `context` says so.
- **A discussion's `plan.md`:** a `type: task` record whose requirement the session writes from the plan (Setup,
  step 2).
- **`--limited-scope` after a key:** this run covers only the part of the ticket the instructions after the flag name.
  It makes a **slice**, a copy of the ticket's record in the ticket's key folder with its own work id, `<KEY>.<n>`, and
  a `## Scope` section (Setup, step 2). Only the flag limits the scope: instructions or approvals given with a key and
  no flag never narrow it. A bare key names the ticket's own record, which covers whatever the done slices haven't.
  A slice resumes by its work id (`/work-task ABC-12.2`).

One task per session. Parallel tasks are parallel sessions, each launched at the workspace root, never inside a
worktree (the workspace's skills and rules load only from there). `work-tasks` launches a batch.

## Ground Rules

- **Stops.** Only these: the code review stop (stop 1) and the publish review stop (stop 2), each once per cycle; with
  `pr.required`, the wait for the user's merge instruction; and the STOP conditions below. Each stop sends a desktop
  notification (`PushNotification`).
- **Git writes.** Commit, push, PRs and the announcement happen ONLY after the user approves at stop 2. With
  `pr.required`, merging happens ONLY on their merge instruction; without it, the approval covers the push to the
  default branch too. "Done reviewing" at stop 1 is the cue to draft the messages, never to publish. Nothing else in
  this flow writes to git history or a remote.
- **Three attempts.** Per area, each check (format, unit tests, integration tests) gets at most three attempts. After
  the third failure, STOP and discuss; don't keep looping.
- **Isolation.** Each task works in its own worktrees. With `work.dependencies: isolate` (the default), a `depends_on`
  task that isn't done, or a dependency found in the code, never holds it up: overlaps settle at merge time like any
  conflict (`references/other-tasks.md`). With `block`, it is a STOP. Either way, a release that doesn't exist yet
  can't be isolated away (`other-tasks.md`, "Needs a Release").
- **Waiting.** Never poll or sleep in the conversation. Long runs go in the background and wake the session when they
  exit: test runs, the review waiter (`code-review.md`), `<bin>/watch_task.py prs` once the PRs are open, and
  `<bin>/watch_task.py pipeline` for a shared library's release (`merge.md`). Nothing else watches GitHub or CI.
- **The tracker.** A `tasks.py set` that prints a non-null `write_back` moves the Jira issue too: follow
  `.claude/toolbox/references/jira-write-back.md` (a `jira-creator` spawn; the main session never writes to Jira).
- **Unreleased libraries.** In a workspace with shared libraries, consumers build and test against a released
  version, which is why the shared-library cycle exists (`references/cycles.md`). Never test an unreleased build of one
  (a branch's, or another checkout's) against local services without the user's go-ahead, and that includes installing
  it into a local build cache so a consumer's build picks it up: parallel sessions share that cache, so one session's
  install silently changes what another's build runs. A checkout's own modules are fine.
- **Extensions' rules** (hook `ground-rules`) hold for the whole task, like these.

## STOP Conditions

STOP (record it, notify, explain in a few lines, wait) when:

- the tracker's issue contradicts the record (someone may have edited it after filing or import; a task with a Jira
  key);
- the code already does what the task asks (`work.md`, "Before the First Cycle");
- the task isn't ready to work: the record (or, for a plan, the plan) doesn't say what to change and how to tell it's
  done, or a plan holds several tasks' worth of change with nothing saying which to work (ask what's missing, or
  whether to file drafts for it);
- something the task needs is still open in the discussion's `questions.md` or the plan's open questions;
- a `depends_on` task isn't done and the config's `work.dependencies` is `block`;
- the task is `in-progress` at `implementing` or `testing` and another session started it (ask whether to take over);
- a third attempt of a check fails in any area;
- testing would need an unreleased build of a shared library against local services (Ground Rules);
- a lock, worktree, git, publish or watcher step reports something unexpected (`not-owner`, `refused`, `failed`,
  conflicts) that its skill or script says to report;
- a step an extension added fails, unless its doc says otherwise;
- an extension's own condition holds (hook `stop-conditions`).

To record a STOP: `<bin>/tasks.py set <work id> owner=<user> "work_note=<the question, in a line>"` and `<bin>/tasks.py
log <work id> "STOP: <the question>"` (the owner value is in the toolbox-config rule). Status and stage stay as they
are, so a resume lands back here. On the user's answer, log it (`tasks.py log <work id> "<answer, in a line>"`), set
`owner=claude work_note=` and go on. `status: stopped` is only for a task the user parks, with the reason in
`work_note`.

## Setup (every session)

0. **Update the default branch** before anything reads the record or checks `depends_on`, so a dependency merged in
   another session is seen as merged: in the workspace root (every repo, in a `nested` workspace), `git pull --ff-only`
   on the default branch (the toolbox-config rule names it). A failure (diverged, or local changes in the way) is a
   STOP; never merge, rebase or stash to get past it. Skip it only in a session resumed at a review stop or a wait.
1. **Load the tools** in one `ToolSearch` call, `select:` with `PushNotification`, `TaskStop`,
   `mcp__ccd_session_mgmt__set_session_title` and `mcp__ccd_session_mgmt__get_usage`, plus, under the `jira` tracker,
   `mcp__<server>__getJiraIssue` and, with `pr.announce`, `mcp__<server>__slack_send_message` and `slack_read_channel`
   (servers from the toolbox-config rule). A name that doesn't load, search for by its last part.
2. **Load the task** with one call, from the workspace root:
   `<bin>/tasks.py context <argument> --session "$CLAUDE_CODE_SESSION_ID"`, plus `--limited-scope "<title>"` for a
   `--limited-scope` start, the title being the scope in 2-6 words (Title Case, no key) that you write from the
   instructions.
   It finds or makes the record and prints its path (`doc`), `work_id`, `key`, `summary`, its `fields` as they were
   before this call, the `plan` and `questions` paths, `publish_dir`, the task's `worktrees`, `needs_requirement` and
   `new_session`, and appends this session to `sessions`, which the usage step counts. Never work out the work id
   yourself; use `work_id` wherever these files say `<work id>`. A keyed task's output also lists the ticket's
   `other_runs` (its own record and its slices, with status, stage and PRs) and the `unfinished_runs` among them.
   Slices run in parallel without asking: each has its own work id, worktrees and branch (`slice-<n>-` leads its
   description).

   When it prints `needs_import` (exit 1: a Jira key with no record), `needs_scope` or `needs_requirement`, read
   `references/new-record.md` and follow it before going on.
3. **Name the session** `<work id> <summary>` (`set_session_title` with `session_id: "self"`), e.g. `ABC-12 Add Shelf
   Labels`, or `shelf-labels Shelf Labels` with no key. `work-tasks` finds running tasks by the `<work id> ` prefix.
4. **Check who's on it.** When `new_session` is true and the task is `in-progress` at `implementing` or `testing`,
   another session may still be working: STOP and ask whether to take over. A new session at a review stop or a wait
   is a hand-off: go on without asking. A run of the whole ticket overlaps every slice: when the ticket's own record
   starts fresh (`todo`) while `unfinished_runs` names a slice, or a new slice starts while it names the ticket's own
   record (the bare key), STOP and ask whether to go on alongside.
5. **Dispatch** (below): read that phase's file and follow it. Read another phase file only when the one you're in
   says to.

## Dispatch

| `status`, `stage` | Read | Start at |
|---|---|---|
| no record before this call, or `todo` | `references/work.md` | the beginning |
| `in-progress`, `implementing` or `testing` | `references/work.md` | "Before the First Cycle" step 1, then the first step of "Each Cycle" not yet done (for the record's `cycle`, when it has one) |
| `review`, `code-review` | `references/code-review.md` | "Resuming": reopen the review, re-present the saved stop 1 |
| `review`, `publish-review` | `references/publish-review.md` | "Resuming": re-present the saved stop 2 |
| `review`, `awaiting-ci`, `cycle: shared-library` and no `worktrees` | `references/merge.md` | "Wait for the Release" |
| `review`, `awaiting-ci` or `awaiting-merge` | `references/merge.md` | "Watch the PRs" |
| `done`, no `usage_total` | `references/done.md` | "Done" (the final sweep), then "Usage" |
| `done`, `usage_total` set | nothing | say in one line that the task is done, with its `usage_total.cost` |
| `stopped` | nothing yet | show `work_note` and ask the user; their answer names the phase |

A `work_note` with `owner` the user's (a STOP still open) is shown and asked about first, whatever the row. A stage
this table doesn't list comes from an extension: its rows (hook `dispatch`) say what to read and where to start. Paths
are relative to this skill's folder, `.claude/skills/work-task/`. Read them with the Read tool, whole or by `offset`
and `limit`, never through Bash (`sed`, `awk`): the folder is a symlink out of the workspace, and a Bash read Claude
Code can't prove read-only asks the user for write access there.

## Checks

Every place that runs the checks follows this, in whatever phase and session it is: a session resumed at a review
stop reads this file but not `work.md`, so the delegation lives here.

- **Who runs them:** a `check-runner` agent, for every check (format, unit tests, integration tests, and a build when a
  step needs one), so the output and the integration bracket's many mechanical steps stay out of this session's
  context. This session never runs `<bin>/run_checks.py`, `<bin>/run_tests.py`, `<bin>/services_lock.py`, an area's
  `format`, `unit`, `build` or `integration` command, `start-local-services`, `stop-local-services` or
  `run-unit-tests` itself, in any phase, and never reads a build, test or service log.
- **Spawn it** with `Agent` (`subagent_type: check-runner`, `run_in_background: true`), passing the mode (`format`,
  `unit`, `build` or `integration`), the applying areas as `<area>=<dir>` (from `worktrees.py changes`), the absolute
  path of `<bin>`, the work id, and a test when only some need re-running; then end your turn. One runner per mode runs
  all its areas. For `integration` in a `nested` workspace, spawn one runner per repo (each repo has its own lock), but
  locks don't see port overlaps: when two repos' services may share a port, run them one at a time.
- **Its report** gives `pass`, `fail` or `blocked`, the counts, and each failure's test, assertion, expected/actual and
  trimmed stack, with no log path. Act on each report's `result`:
    - `pass`: done with that area.
    - `fail`: an attempt. Fix the code, format, re-run its unit tests, then spawn a new runner for the check that
      failed. At most three attempts per area, then STOP.
    - `blocked`: hook `blocked-bracket`. By default: not an attempt. If its reason is a STOP condition or something
      you can't clear yourself, STOP. If it took over the user's expired manual lock (`notes`), notify them and go on.
    - `lock: NOT RELEASED`, in any result: STOP.
    - A takeover in `notes`: notify the user that their manual lock was taken over.

  Don't edit a worktree while its runner is running. If the runner itself misbehaves, the fix goes in its agent file
  (`check-runner`) or its docs (`<bin>/../agents/check-runner/`), never a by-hand run.

Which areas need which check, and the hooks around them, are in `work.md` "Which Checks Apply" and steps 3–5; the places
that re-run them later (the review stops, a change after the PRs are open, a release wait) say "the Checks" and mean this
section.

## Progress

Record progress in the task record at every step, so another session can resume, always through `<bin>/tasks.py`
(`set`, `set-list`, `log`) or the scripts that write it (`publish.py`, `session_usage.py`); each phase file names its
writes where they happen. `owner` is whose move it is: `claude` while this session works, the user's value at a stop or
a wait on them. A phase resumes from the record alone: its fields (`cycle`, `worktrees`, `prs`), the `plan` and
`questions`, and the publish folder (`publish_dir`, `<tasks>/.publish/<work id>/`). That folder holds the approved
commit messages (`commit.txt`, or `<repo>.txt` per repo) and `announce.txt`, which `publish.py` reads byte for byte;
`code-review.md` and `publish-review.md`, the two stops as last presented, without their hand-off blocks; and
`merged.json`, what a merge without PRs pushed.

## Extension Points

The hook ids, where each runs, its default and the verbs it allows are in `references/extension-points.md`. Read it
only when the Extensions step listed docs. Each hook is named at its step as `hook <id>`.
