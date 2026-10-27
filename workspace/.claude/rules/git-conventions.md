# Git Conventions

The global git rule covers Git writes (only on Allen's instruction) and trailers. These add what the toolbox's git and
GitHub scripts rely on. The toolbox-config rule gives this workspace's branch format, default branch, worktrees folder
and PR policy; the values below are never hard-coded.

## Branches

A task's branch is the config's `git.branch` filled in, and the key and its separator drop out when the task has no
ticket. With the format `{handle}/{key}-{slug}`:

- `tester/ABC-12-recipe-scaling` for a task with the key `ABC-12`.
- `tester/recipe-scaling` for a task with no ticket.

The slug is kebab-case: lowercase letters, digits and single hyphens. `worktrees.py` builds the name, and `publish.py`
refuses a branch that doesn't match it.

## Worktrees

Work for a task happens in its worktrees, under the config's `workspace.worktrees` folder: `<work id>/<repo>` in a
nested workspace, `<work id>` in a single-repo one. Never edit, commit or switch branches in a main checkout while a
task's worktree exists; Allen works in the main checkouts.

A worktree holds a copy of the repo as of the branch point. A vault kept inside the repo is read and written in the main
checkout, never the worktree's stale copy, which would land vault edits on the task branch.

## Commit messages

- The **subject becomes the PR title verbatim**, taken from the *oldest* commit on the branch, so it must read as a
  title. It leads with the ticket key when the branch has one (`ABC-12 recipe scaling`) and has none otherwise.
- The **body becomes the PR description**. A subject-only commit leaves it empty, so every message has at least one body
  paragraph, each written as a single unwrapped line.
- One message per repo. The approved messages are committed byte for byte, from the task's publish folder
  (`<tasks folder>/.publish/<work id>/`).
- Commits made by `publish.py` stage everything in the worktree (`git add -A`), so a worktree holds only the task's
  files: no build output, IDE files or scratch files.

## Pull requests

When the PR policy says a PR is required, every change goes through one; otherwise a change may go straight to the
default branch. Either way:

- The default branch is never committed to by a task, and a task never force-pushes. The one exception is
  `--force-with-lease` against the exact sha seen, after `publish.py push` rebased a branch the remote already had.
- A task branch is rebased onto the latest `origin/<default>`, never merged into. Commits on the default branch that
  touch only a vault inside the repo don't force a rebase.
- Merging never uses `--admin`: a merge refused by branch protection is the protection working.

## Cleanup

Removing a worktree keeps its branch, and a worktree with uncommitted or untracked files is never removed. A local
branch is deleted only after its PR is verified merged with that tip: squash and rebase merges leave a merged branch's
commits unreachable from the default branch, so `git branch -d` calls it unmerged.
