---
name: gh-merge-pull-request
description: Merge one or more approved PRs by the configured method and delete their remote branches, halting the whole batch if any PR isn't approved, green or mergeable. Without pr.required, merges the branch straight into the default branch. Use when Allen says to merge; runs after gh-create-pull-request.
allowed-tools: Bash(*), Read, Grep, Glob, PushNotification
---

# Merge Pull Request

Merge approved PRs and report what landed. Merging is outward-facing and can't be undone once the commit is on the
default branch, so invoking this skill authorizes the merge **only after Step 2's table has been approved**. The merge
step writes nothing locally; local branches and worktrees are removed by `publish.py cleanup` (Step 4).

The mechanics are `publish.py`'s `merge` and `cleanup` steps, which print JSON (run from the workspace root, or by the
absolute path in the toolbox-config rule):

```bash
.claude/toolbox/bin/publish.py merge <id> --dry-run --checkouts <paths> --no-record
.claude/toolbox/bin/publish.py merge <id> --checkouts <paths> --no-record [--confirm-squash]
.claude/toolbox/bin/publish.py cleanup <id> [--checkouts <paths> --no-record]
```

`<id>` is the task's work id. A checkout is a repo name or a worktree path. Each PR is found by its branch, and `gh`
runs from outside the repo with `--repo`: inside it, `--delete-branch` would try to switch and delete the local branch,
which in a task worktree collides with the default branch checked out in the main checkout. The script's docstring has
every check and exit code. The method is the config's `pr.merge`; the toolbox-config rule shows it.

## Invocation

```
/gh-merge-pull-request                       # derive the PRs from this conversation's work
/gh-merge-pull-request <repo>
/gh-merge-pull-request <repo-a> <repo-b>     # a batch
```

- **No repo named:** pick the PRs from this conversation's work, normally the batch `gh-create-pull-request` just
  opened, and say which and why before anything else. If that's ambiguous, ask.

## Step 1: Precheck

Run the `--dry-run` form. Any PR `blocked` halts the whole batch with nothing merged: half the fleet on the new
behavior is the worst outcome. Report each problem:

- **Behind the default branch:** run `publish.py push` to rebase and push it, then `publish.py wait-ci` in the
  background (`run_in_background`), and merge once it is green. Don't poll CI.
- **Waiting for review:** with `pr.review_required`, an unapproved PR stops the batch (exit 4). Report who it waits for.
- **Uncommitted or unpushed work** in a checkout isn't in the PR, and merging strands it on a branch about to be
  deleted; point at `git-commit`.

A PR `already-merged` is reported and dropped.

**Don't review the PR title, description or commit messages.** They were settled when the PR was opened and reviewed.
Don't flag wording, a missing key or a mismatch with the diff, and don't offer `gh pr edit`.

## Step 2: Show what will be merged, and wait

One table: repo, PR number, title, commit count, merge method, and that the remote branch goes. Under it, name every PR
with `squash_collapses`: its commits become one squashed commit on the default branch, their messages surviving only in
its body. That's nearly always still right; Allen just needs to know first, and the batch needs `--confirm-squash`.
Then name every PR with `admin_merge` and its `missing_checks`: it merges with `--admin` (Step 3).

Then **stop and wait for approval.** One approval covers the batch.

**Called by `work-task`:** it merges through `publish.py run`. Allen's merge instruction is the approval, except for a
squash that collapses several commits: that halts for him to see first.

## Step 3: Merge

Run `publish.py merge` with the same arguments.

- **Exit 3:** a merge failed or left the PR open; the output lists `merged` and `not_merged`. Report it. Re-running is
  safe: merged PRs come back `already-merged`.
- `--admin` only where the precheck set `admin_merge`, which `merge` passes itself; never add it by hand. That is a PR
  approved, mergeable, up to date, with no unresolved review thread and every reported check green, blocked only by
  required checks that will never report: every file it changes is in their `pull_request` workflow's path filter
  (`paths-ignore`, say a docs-only change). Allen's approval covers it. Any other merge blocked by branch protection is
  the protection working.
- With `pr.required: false` there is no PR: the branch must be a fast-forward of `origin/<default>`, which `merge`
  pushes to the default branch and then deletes the branch.

## Step 4: Clean up

Run `publish.py cleanup`. It removes the task's worktrees (refusing one with uncommitted files), deletes the merged
local branches after verifying each tip against its PR, and marks the task record done when the workspace keeps one.
Say which worktrees were refused and why; the task stays open until they are cleared.

## Report

A table: repo, PR number, merge commit. Then say the remote branches were deleted, and what cleanup removed.
