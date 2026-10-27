---
name: git-delete-merged-branches
description: Delete local branches carrying the user's handle across the workspace repos whose PRs have merged, after showing the list. Use for branch cleanup after gh-merge-pull-request.
allowed-tools: Bash(*), Read, Grep, Glob
---

# Local Branch Cleanup

Delete local feature branches whose work has landed. Only `git branch -D` on local `<user.handle>/*` branches that pass
verification: never a remote branch, the default branch, someone else's branch or a checked-out one, and never a
checkout, stash, merge or reset.

The mechanics are one script, which prints JSON (run from the workspace root, or by the absolute path in the
toolbox-config rule):

```bash
.claude/toolbox/bin/branches.py scan [repo...] [--except repo...] --out <plan.json>
.claude/toolbox/bin/branches.py delete --plan <plan.json>
```

With no repos named, `scan` covers every repo of the workspace.

## Invocation

```
/git-delete-merged-branches                          # every repo of the workspace (the default)
/git-delete-merged-branches --except <repo>          # every repo but these
/git-delete-merged-branches <repo-a> <repo-b>        # only these repos
```

## Why the PR decides

Squash and rebase merges leave every branch commit unreachable from the default branch, so `git branch -d`,
`git cherry` and tree comparison all say "unmerged". The scan checks the PR instead: a branch is eligible only when its
PR is `MERGED` and its local tip is the PR's head or behind it. Behind is fine (another checkout rebased or updated
the branch, and every local commit is still in what merged); ahead or diverged means local commits in no PR, which
would be lost. Without `pr.required` there is no PR, so a branch is eligible when its tip is already in
`origin/<default>`. Everything else is kept, and reported with its reason:

| Reason                | Meaning                                                         |
|-----------------------|-----------------------------------------------------------------|
| No PR                 | Never opened a PR: possibly unfinished work                     |
| PR open               | Still in review                                                 |
| PR closed, not merged | Abandoned or superseded: Allen decides                          |
| Local tip ahead of PR | Local commits beyond what merged (ahead of or diverged from it) |
| Checked out           | The current branch, or checked out in a worktree                |
| gh error              | Couldn't verify: never delete on an unverified answer           |

A branch checked out in a task worktree is kept until the worktree goes: `git-worktrees remove <id>`, then re-run.

## Step 1: Scan, show the list, and wait

Run `scan --out <scratchpad>/branches.json`. Show one summary: per repo, the branches to delete and the branches kept
with their reasons. List eligible branches in full under ~20; beyond that, summarize by repo. Mark each eligible branch
that's `behind` its PR head, with the count.

Then **stop and wait for approval.** One approval covers the batch. A deleted local branch is recoverable only through
the reflog, so this gate matters.

**Called by `publish.py cleanup`:** it deletes only the task's own branch after its PR merged on Allen's instruction.
Nothing here waits.

## Step 2: Delete

Run `delete --plan <the same file>`. It re-checks each branch (still at the scanned SHA, not checked out) and skips any
that changed, with the reason.

## Report

The branches deleted per repo, then every remaining branch with the user's handle and its reason for being kept. If
nothing was eligible, say so in one line.
