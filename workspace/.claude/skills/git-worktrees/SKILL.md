---
name: git-worktrees
description: Create, list or remove a task's git worktrees, one per repo (nested workspace) or one per task (single-repo workspace), from the latest origin default branch. The work id is the ticket key, else a slug of the summary; the branch comes from the config's branch format. Create resumes an existing branch; remove refuses a worktree with uncommitted files and keeps the branches. Use when starting, resuming or cleaning up a task or a change with no ticket; work-task calls it.
allowed-tools: Bash(*), Read
---

# Worktrees

Gives a task (or a change with no ticket) its own checkout of each repo it touches, so parallel tasks, and Allen's own
work in the main checkouts, never share a working tree. Everything is keyed by the **work id**: the ticket key when
there is one, else a kebab-case slug of the summary or title. The worktrees live under the config's
`workspace.worktrees` folder: `<work id>/<repo>` in a nested workspace, `<work id>` in a single-repo one. All the git
work is in one script, run from the workspace root or by the absolute path in the toolbox-config rule:

```bash
.claude/toolbox/bin/worktrees.py create <id> [--repos <a,b>] [--desc <kebab-desc>]
.claude/toolbox/bin/worktrees.py remove <id> [--repos <a,b>]
.claude/toolbox/bin/worktrees.py list [id]
```

It never checks out, stashes or changes anything in a main checkout, never deletes a branch and never forces.

## Arguments

```
/git-worktrees create <KEY> <kebab-description> [repo...]
/git-worktrees create <slug> [--desc <kebab-description>] [repo...]
/git-worktrees remove <id> [repo...]
/git-worktrees list [id]
```

- **id**: the work id, a ticket key or a lowercase kebab-case slug, never a path.
- **kebab-description**: the branch's description. The branch is the config's `git.branch` filled in (the toolbox-config
  rule shows it): with a key, the key and the description (required); with a slug, the description (`--desc`) or the
  id itself. Pass `--branch <full-name>` to the script to reuse an existing branch name exactly.
- **repo...**: repo names, for a nested workspace (required to create; none means every worktree on remove). A
  single-repo workspace has one repo and takes none.

## Create

1. **Create:** run the script with every repo in one call.
2. **Report** each repo's `status`, `path` and `head`:

   | `status` | Meaning |
   |---|---|
   | `created` | New branch from the latest `origin/<default>`, with no upstream (the first push sets it) |
   | `attached` | The local branch already existed and wasn't checked out anywhere; the worktree uses it as is |
   | `from-remote` | Only `origin` had the branch; it's checked out tracking `origin/<branch>` |
   | `exists` | The worktree is already set up on this branch |
   | `failed` | The fetch failed, the path is on another branch, or the branch is checked out elsewhere (see `reason`) |

3. **Don't fix failures by force.** A branch checked out in the main checkout has to be switched there by Allen.

A fresh worktree starts cold: build output and gitignored local files (`.env`) aren't copied from the main checkout.

## Remove

1. **Look first:** `worktrees.py list <id>`. Show Allen any worktree with commits that aren't on its remote branch
   (`remote_branch: false` with `ahead_of_default` above 0). Removing a worktree keeps its branch, so nothing is lost,
   but unpushed work is worth mentioning.
2. **Remove:** run the script. It refuses, per repo, when the worktree has uncommitted or untracked files (it lists
   them) or its folder is no longer a worktree. The other repos are still removed, and the `<id>` folder goes once it's
   empty.
3. **Report** each repo's `status` (`removed`, `refused` with the reason, or `absent`).

Never remove a refused worktree another way (`git worktree remove --force`, deleting the folder). Uncommitted files in
a worktree exist nowhere else.

Branches stay. To delete a task's branches once its PRs have merged, run `git-delete-merged-branches`; it skips a
branch that's still checked out in a worktree, so remove the worktree first.

## List

`worktrees.py list [id]` shows every task worktree (or one task's) with its id, branch, dirty file count, commits ahead
of `origin/<default>`, and whether the remote branch exists.
