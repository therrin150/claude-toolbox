---
name: work-task
description: Work one task end to end in its own worktrees (implement, format, unit and integration tests, code review, independent verification), stopping twice for the user, to review the code and then the commit messages; their approval at the second publishes, and their merge instruction merges and cleans up. The task is a ticket key, a discussion's draft, a discussion's plan.md, or a work id to resume. Resumable from the task record in any session. Use when the user says "/work-task ABC-12", "work ABC-12", "work this plan", or "pick up ABC-12 again".
allowed-tools: Bash(*), Read, Edit, Write, Grep, Glob, Agent, Skill
---

# Work Task

**Extensions.** Before anything else, run `.claude/toolbox/bin/toolbox.py config extensions work-task` from the
workspace root (its absolute path is in the toolbox-config rule) and read every doc it lists. Each `##` section of a doc
is a verb and one of this skill's hook ids ("Extension Points" below): at that hook, follow an `add` section after the
hook's own text and a `replace` section instead of it; for a `skip` section, don't run the step, and list it wherever
this skill reports its steps as `⏭️ <step>: skipped by <doc>: <reason>`. An added step that fails stops the skill for
the user, unless its doc says otherwise, and what a step reports goes in this skill's next report. If two docs replace
or skip the same hook, stop and ask the user which applies.

Drives one task to a merged change, stopping for the user only where a mistake would be hard to undo. The **work id**
names the task everywhere: its ticket key, else the slug of its summary (the toolbox-config rule's tracker line says
which this workspace has). The task's record (`ticket-docs` rule) holds all its progress, so any session can resume it.

It composes the toolbox's skills and scripts named in its phase files; each owns its own mechanics, so follow them
rather than redoing their work inline. The scripts are in `.claude/toolbox/bin/` (`<bin>` below), whose absolute path
the toolbox-config rule names: write that path out in every call, since a shell that moved into a worktree can't use
the relative one, and zsh won't run a command held in a variable. Every script prints JSON.

The body is split by phase, and a session reads only the phase its record names (see "Dispatch"). At the long waits it
ends with a prompt to paste into a new session, so a phase that resumes hours later starts from the record in a small
session instead of rebuilding a large one.

## Arguments

```
/work-task <KEY | draft path | plan.md path>
/work-task <work id>          # a resume
```

- **A ticket key:** its record. Under the `jira` tracker, an issue with no record yet is imported (Setup, step 2).
- **A draft** in a discussion's `tickets/` folder: under the `none` tracker it is copied into a record; under `vault`
  and `jira` it must be filed first (`create-tickets`), and `context` says so.
- **A discussion's `plan.md`:** a `type: task` record whose requirement the session writes from the plan (Setup,
  step 2).

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
  can't be isolated away ("### Needs a release").
- **Waiting.** Never poll or sleep in the conversation. Long runs go in the background and wake the session when they
  exit: test runs, the review waiter (`code-review.md`), `<bin>/watch_task.py prs` once the PRs are open, and
  `<bin>/watch_task.py pipeline` for a shared library's release (`merge.md`). Nothing else watches GitHub or CI.
- **The tracker.** A `tasks.py set` that prints a non-null `write_back` moves the Jira issue too: follow
  `.claude/toolbox/references/jira-write-back.md`. Before the first Jira write, run the identity check
  (`.claude/toolbox/references/identity-check.md`) once per session.
- **Main checkouts.** Never edit, commit or switch branches in one (`git-conventions` rule). A vault inside the repo
  is read and written in the main checkout, never a worktree's copy.
- **Unreleased libraries.** In a workspace with shared libraries, consumers build and test against a released
  version, which is why the shared-library cycle exists ("### Plan the cycles"). Never test an unreleased build of one
  (a branch's, or another checkout's) against local services without the user's go-ahead, and that includes installing
  it into a local build cache so a consumer's build picks it up: parallel sessions share that cache, so one session's
  install silently changes what another's build runs. A checkout's own modules are fine.
- **Extensions' rules** (hook `ground-rules`) hold for the whole task, like these.

## STOP Conditions

STOP (record it, notify, explain in a few lines, wait) when:

- the tracker's issue contradicts the record (someone may have edited it after filing or import; a task with a Jira
  key);
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

1. **Load the tools** in one `ToolSearch` call, `select:` with `PushNotification`, `TaskStop`,
   `mcp__ccd_session_mgmt__set_session_title` and `mcp__ccd_session_mgmt__get_usage`, plus, under the `jira` tracker,
   `mcp__<server>__getJiraIssue` and, with `pr.announce`, `mcp__<server>__slack_send_message` (servers from the
   toolbox-config rule). A name that doesn't load, search for by its last part.
2. **Load the task** with one call (a Jira key with no record adds an import and a second call, below), from the
   workspace root:
   `<bin>/tasks.py context <argument> --session "$CLAUDE_CODE_SESSION_ID"`.
   It finds or makes the record and prints its path (`doc`), `work_id`, `key`, `summary`, its `fields` as they were
   before this call, the `plan` and `questions` paths, `publish_dir`, the task's `worktrees`, `needs_requirement` and
   `new_session`, and appends this session to `sessions`, which the usage step counts. Never work out the work id
   yourself; use `work_id` wherever these files say `<work id>`.

   **`needs_import`** (exit 1, `jira` tracker): no record has this key. Fetch the issue (`getJiraIssue` with the
   rule's cloud id; fields `summary`, `description`, `issuetype`, `parent`, `issuelinks`, `status`, `labels`, plus
   the acceptance-criteria field id from `<bin>/toolbox.py config show tracker.jira.fields`, when there is one). If it
   has no acceptance criteria, or its description doesn't say what to change, STOP (there is no record yet, so
   nothing to set). Otherwise save the response as JSON to the session scratchpad as `<KEY>-issue.json`, run
   `<bin>/tasks.py import <KEY> --issue <file>`, and run `context` again.

   **`needs_requirement`**: the record's `## Description` is empty, as it always is right after a plan's record is
   made. Read the `plan` (and the sibling docs it links), then write the record's `## Description` (what to change,
   and in which areas or repos) and `## Acceptance Criteria` (how to tell it's done) with `Edit`, in the plan's terms,
   nothing it doesn't say. The plan stays the source of truth. If it doesn't say what to change and how to tell it's
   done, or holds several tasks' worth of change, leave the record as it is and STOP.
3. **Name the session** `<work id> <summary>` (`set_session_title` with `session_id: "self"`), e.g. `ABC-12 Add Shelf
   Labels`, or `shelf-labels Shelf Labels` with no key. `work-tasks` finds running tasks by the `<work id> ` prefix.
4. **Check who's on it.** When `new_session` is true and the task is `in-progress` at `implementing` or `testing`,
   another session may still be working: STOP and ask whether to take over. A new session at a review stop or a wait
   is a hand-off: go on without asking.
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
| `done`, no `usage_total` | `references/merge.md` | "Done" (the final sweep), then "Usage" |
| `done`, `usage_total` set | nothing | say in one line that the task is done, with its `usage_total.cost` |
| `stopped` | nothing yet | show `work_note` and ask the user; their answer names the phase |

A `work_note` with `owner` the user's (a STOP still open) is shown and asked about first, whatever the row. A stage
this table doesn't list comes from an extension: its rows (hook `dispatch`) say what to read and where to start. Paths
are relative to this skill's folder, `.claude/skills/work-task/`.

## Hand-offs

At both review stops and once the PRs are open, the user is often gone for hours, and coming back rewrites this
session's whole context to cache. So each of those waits ends with a prompt they can paste into a new session, which
resumes from the record. Stop 1's waiter runs `--until` the same hand-off time (`code-review.md`), so this session
wakes only while its cache is warm; past it the review page shows the prompt (`--resume-hint`) and a pasted session
takes over. A session resumed at stop 1 ends with no block: its waiter has no deadline, and its context is small.

1. **End the stop's report** (the closing message after `wrap_up`, never text before it, which the app can cut) with
   this block, `h:mm` being now plus the one-hour cache TTL (`date -v+1H +%-I:%M` on macOS):

   ````markdown
   **If you're not back by h:mm**

   This session's prompt cache expires then, and resuming here rewrites the whole context to cache. To pick up from
   the saved task record instead, paste this into a new session started at the workspace root:

   ```
   /work-task <work id>
   ```
   ````

2. **When the user answers here instead,** first run `<bin>/tasks.py get <work id> sessions`. If its last entry isn't
   `$CLAUDE_CODE_SESSION_ID`, the task has moved to a pasted session: say so in one line and do nothing else.
   Otherwise go on here: within the hour this is the cheap path, and it keeps the implementation context for review
   fixes.

## Progress

Record progress in the task record at every step, so another session can resume, and always through
`<bin>/tasks.py`, never by hand (`ticket-docs` rule). `owner` is whose move it is: `claude` while this session works,
the user's value at a stop or a wait on them.

| When | Write |
|---|---|
| A fresh start | `set <work id> status=in-progress owner=claude stage=implementing` |
| Two cycles planned, and each cycle's start | `set <work id> cycle=<shared-library or consumers>` |
| Worktrees made | `set-list <work id> worktrees <path>...` (the current cycle's) |
| Tests start | `set <work id> stage=testing` |
| Stop 1 | `set <work id> status=review owner=<user> stage=code-review` |
| Stop 2 | `set <work id> status=review owner=<user> stage=publish-review` |
| A code change asked for at stop 2 | `set <work id> status=in-progress owner=claude stage=implementing` |
| Published | `publish.py` appends `prs` and sets `status=review` and `stage` (`awaiting-ci`, else `awaiting-merge`); then `set <work id> owner=<user>` |
| Merged | `publish.py` sets `status=done`, clears `stage` and empties `worktrees`; after the shared-library cycle, `status=review stage=awaiting-ci owner=claude` instead |
| Released | `set <work id> cycle=consumers status=in-progress owner=claude stage=implementing` |
| A STOP, its answer | STOP Conditions |
| Done | `session_usage.py --task` writes `usage` and `usage_total` |

The publish folder (`publish_dir`, `<tasks>/.publish/<work id>/`) holds what doesn't fit frontmatter:
`commit.txt` (a single-repo workspace) or `<repo>.txt` per repo, the approved commit messages, and `announce.txt`, the
announcement, all read by `publish.py` byte for byte; plus `code-review.md` and `publish-review.md`, the two stops as
last presented, without their hand-off blocks.

What each phase resumes from, with nothing from an earlier session:

| Phase | Reads |
|---|---|
| work | the record, `plan`, `questions`, `cycle`, and `worktrees` (checked against `worktrees.py list <work id>`) |
| review stops | `cycle`, `worktrees`, and the publish folder's `code-review.md`, `publish-review.md` and messages |
| merge | `cycle`, `prs` (`watch_task.py` reads them), `worktrees` |
| release wait | `prs`, or the publish folder's `merged.json` without PRs (`watch_task.py pipeline` reads them) |

## Extension Points

Workspace knowledge (a scan before the review stop, a network check before integration tests, a wait on another
task's release) lives in extension docs (`.claude/extensions/README.md`), not here. Each hook has a fixed id, named at
its step as `hook <id>`; rename headings freely, never an id. The `###` sections below are the longer defaults.

| Id | Runs | Default | Verbs |
|---|---|---|---|
| `ground-rules` | "Ground Rules" above, for the whole task | those rules | add |
| `stop-conditions` | "STOP Conditions" above | those conditions | add |
| `dispatch` | "Dispatch" above: rows for an extension's own stages | that table | add |
| `before-first-cycle` | `work.md` "Before the First Cycle", once on a fresh start: after the record and its context are loaded, before any worktree | nothing | add |
| `plan-the-cycles` | `work.md` "Before the First Cycle", step 4 | "### Plan the cycles" | add, replace |
| `after-implementing` | `work.md` "Each Cycle", after step 2, before format and tests | nothing | add |
| `format` | `work.md` "Each Cycle", step 3 | `run_tests.py format` | add, skip |
| `unit-tests` | `work.md` "Each Cycle", step 4 | `run-unit-tests` | add, skip |
| `before-integration-tests` | `work.md` "Each Cycle", step 5, before the first runner | "### Before integration tests" | add |
| `integration-tests` | `work.md` "Each Cycle", step 5 | `services-runner` brackets | add, skip |
| `blocked-bracket` | `work.md` "Each Cycle", step 5: a runner's `blocked` result | "### A blocked bracket" | add, replace |
| `code-review` | `work.md` "Each Cycle", step 6 | `/code-review` | add, skip |
| `verify` | `work.md` "Each Cycle", step 6 | `verify-change` | add, skip |
| `review-checks` | `work.md` "Each Cycle", step 6, beside `/code-review` and `verify-change`; findings join the triage | nothing | add |
| `needs-a-release` | `other-tasks.md` "Needs a Release": the task needs another task's release | "### Needs a release" | add, replace |
| `before-publishing` | `merge.md` "Publish", after stop 2's approval, before `publish.py` | nothing | add |
| `after-the-merge` | `merge.md` "Merge", step 3 | "### After the merge" | add |
| `before-done` | `merge.md` "Done", after the cleanup (`publish.py` has set `status=done`), before the done line and "Usage" | nothing | add |

Built-in steps (`format`, `unit-tests`, `integration-tests`, `code-review`, `verify`) take `add` and `skip`, never
`replace`, since replacing one would bypass its record updates. A skipped step is listed at both stops beside the
skipped checks (`work.md`, "Which Checks Apply"), and an `add` on a skipped step still runs. What an added step reports
goes on the next stop's Testing list (`code-review.md`), and an added step that fails is a STOP unless its doc says
otherwise.

### Plan the cycles

List the areas the task changes (in a `nested` workspace, its repos) from the record, the plan and the code, then read
the shared libraries: `<bin>/toolbox.py config show shared_libraries` (`names`, and `update`: how a consumer picks up a
release).

**One cycle** when `names` is empty or the task changes none of them: every area worked and published together, as
"Each Cycle" describes; a `single` workspace has one worktree whatever the areas. No `cycle` field.

**Two cycles** when it changes a shared library, because its consumers must build against a released version, not a
branch (Ground Rules, "Unreleased libraries"):

1. **Shared library:** the libraries the task changes, with their own worktrees, checks, both stops, publish and merge.
   Its checks are the libraries' own; consumers are tested in the next cycle, against the release.
2. **The release:** after the merge, wait for the default branch's CI run of the merge commit, which publishes it
   ("### After the merge").
3. **Consumers:** the areas or repos that need the new release (the plan names them, else the code shows them: those
   the task also changes, and those whose build pins the library), in fresh worktrees from the latest
   `origin/<default>`. Step 2 of "Each Cycle" starts by picking up the release in each consumer checkout: with
   `update.command`, run it there (`{libraries}` is the released libraries' names, space-separated); with
   `update.skill`, run that skill on the checkout. Then the usual checks, stops and publish.

Say which cycles the task runs, and set `<bin>/tasks.py set <work id> cycle=shared-library`. A task that changes only
the consumers of a library that's already released runs one cycle. A task that needs another task's release, not its
own, is "### Needs a release".

### Before integration tests

Nothing to check beyond what `start-local-services` checks itself. An extension adds a check the tests need (a network
that must be up, say); when one fails, STOP and ask the user to fix it, and spawn the runners when they say it's done.

### A blocked bracket

A runner's `blocked` result is not an attempt. If its reason is a STOP condition or something you can't clear
yourself, STOP. If it took over the user's expired manual lock (`notes`), notify them and go on.

### Needs a release

The task needs something another task will publish that doesn't exist yet (a version bump to a release not out, say).
Isolation can't supply it, so: do and test everything that doesn't need it, and hold back the rest. At both stops, add
a `Pending:` line naming what's held back and what it waits on; the user can review and ask for changes, but approval
doesn't publish. After presenting stop 2, STOP with `work_note` naming what it waits on. When the user says it's out,
clear the note, set `status=in-progress stage=testing`, apply what was held back, format, re-run the checks that
apply (`work.md`, steps 3–5), and return to stop 1 with only what changed; stop 2 follows with fresh drafts, and its
approval publishes as usual.

### After the merge

After the shared-library cycle (`publish.py` set `stage=awaiting-ci` and printed `next: release`): "Wait for the
Release" (`merge.md`). Otherwise nothing: go on to "Done" (`merge.md`).
