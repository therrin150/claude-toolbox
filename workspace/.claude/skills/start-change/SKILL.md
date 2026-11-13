---
name: start-change
description: Start a change in its own branch and worktrees, from a task record or an ad-hoc summary (a keyless record is made for it). Refuses when the record already has a worktree for that repo. Use before editing code - "start a change for X", "make a branch for this fix" - or to give a record its worktree; `work-task` runs it for each cycle.
allowed-tools: Bash(*), Read
---

# Start Change

The first stage of a change, for any session: the record that holds its state, a branch, and a worktree per repo, so
nothing is edited in a main checkout. `work-task` runs it for each cycle of a ticket; a session working outside
`work-task` (an ad-hoc fix, a change started from a plan) runs it directly. It checks only its own requirement, that the
record has no worktree for the repo yet. It never checks that anything ran before it.

All the work is in one script. Run it from the workspace root, or by the absolute path in the toolbox-config rule (a
shell that moved into a worktree can't use the relative one):

```bash
.claude/toolbox/bin/change.py start <work id | record path> [--repos <a,b>] [--desc <kebab-description>]
.claude/toolbox/bin/change.py start --summary "<what the change does>" [--repos <a,b>] [--description "<text>"]
```

## Arguments

- **A record** (`<work id>` or its path): an existing task record, keyed or keyless. A Jira key with no record yet
  isn't imported here: run `work-task`'s Setup for it first (`tasks.py context <KEY>`).
- **`--summary`**: an ad-hoc change. The script finds the keyless record whose slug the summary makes, or makes one
  (`<tasks>/<slug>.md`, a `task` whose Description is `--description`, else the summary). Write the summary as a
  short title ("Fix Shelf Count Rounding"); it names the record, the work id and the branch.
- **`--repos`**: the repos to branch, for a nested workspace (required there; a single-repo workspace takes none).
- **`--desc`**: the branch's kebab-case description. Required for a record with a key; a keyless one is named by its
  work id.

## Steps

1. **Run the script.** It makes or finds the record, then, per repo, creates the branch from the latest
   `origin/<default>` and its worktree under the config's `workspace.worktrees` (as `git-worktrees` describes), adds
   the worktrees to the record's `worktrees` and, for a record still `todo`, sets it `in-progress`, stage
   `implementing`, owner `claude`.
2. **Report** the `work_id`, whether the record was `made`, the `branch` and each repo's `status` and `path`.
3. **A `refused` repo** already has a worktree for this record: the script names it. Say so and work in that
   worktree, or ask the user; never remove it, recreate it by hand or pass it on as new. Repos that weren't refused
   are still created. A record that is `done` is refused whole: start a new change under a new summary. Arguments are
   checked before anything is written, so a failed call leaves no record behind.
4. **A `failed` repo** (the fetch failed, the branch is checked out elsewhere) is reported with its `reason`; don't
   fix it by force.

Edit only in the worktrees from here on. The next stage is `check-change`.
