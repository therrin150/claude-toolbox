# Git Conventions

These always hold, on top of the global git rule:

- Never edit, commit or switch branches in a main checkout while a task's worktree exists: the user works there. A
  vault inside a repo is read and written in the main checkout, never a worktree's stale copy.
- Never force-push (the one exception is `publish.py`'s `--force-with-lease`), merge with `--admin`, or merge the
  default branch into a task branch: rebase onto `origin/<default>` instead.
- Never remove a worktree that has uncommitted or untracked files, and never delete a branch by hand (`git branch -D`).

Branch names, commit messages, PRs and cleanup follow `.claude/toolbox/references/git-conventions.md` under the
workspace root. Read it before writing a commit message or creating, pushing, merging or deleting a task's branch.
