---
name: ship-toolbox-change
description: Ship a change to any Claude toolbox repo end to end. Commits (drafting the message), then lands it by PR or straight on the default branch as the toolbox's domains.yml says (ship - pr or direct), cleans up, and previews the installer. Use when Allen says to commit, PR, ship or merge a toolbox change; git-commit and the gh-* skills are for workspace repos.
allowed-tools: Bash(*), Read, Write, Grep, Glob
---

# Ship Toolbox Change

A toolbox repo (a folder with a `domains.yml`) holds Allen's rules, skills and agents, and changes to it are his own
work, so invoking this skill **is** the instruction to commit, push and land the change. How it lands is the toolbox's
own choice, the top-level `ship` key of its `domains.yml`:

| `ship:` | The change |
|---|---|
| `pr` (the default) | goes on a branch, through a PR that is merged with a merge commit (so the local default branch fast-forwards and the branch deletes cleanly), and the branch is deleted |
| `direct` | is committed, rebased onto the default branch if it moved, and pushed straight to it |

A toolbox's `branch_prefix` key (default `toolbox`) names a new branch in `pr` mode. None of the workspace process
applies: no reviewer, template or announcement, and the commit message needs no approval.

| Workspace repos (`git-commit`, `gh-create-pull-request`, `gh-merge-pull-request`) | A toolbox repo |
|---|---|
| Allen supplies or approves the commit message | Draft it and commit. Invoking this skill is the instruction to commit, push and land |
| Branch from `git.branch` | `<branch_prefix>/<desc>`, no ticket |
| Merge method from `pr.merge` | Merge commit (`pr`), or a push (`direct`) |

Everything runs through one script, which prints one JSON object:

```bash
python3 ~/.claude/skills/ship-toolbox-change/scripts/ship.py <precheck | run | cleanup> [--repo-dir <toolbox>] ...
```

**Which toolbox.** `--repo-dir` defaults to the repository the current directory is in. When that isn't a toolbox,
ask which one: `claude-toolbox` itself, or one of the other toolboxes listed under `toolboxes:` in its `machine.yml`.

**From a worktree.** Pass `--repo-dir <worktree>` to every command, and run that worktree's own `ship.py` when the
change is to `ship.py` itself. The branch is the worktree's. Cleanup removes the worktree (refused while it holds
uncommitted or untracked files) instead of switching branches, fast-forwards the main checkout's default branch when
it's on it, and runs the install preview in the main checkout, which is what the installed links point at.

## 1. Precheck

`ship.py precheck` is read-only and says what `run` will do:

| Field | Meaning |
|---|---|
| `mode`, `base` | The toolbox's `ship` value and its default branch |
| `branch`, `new_branch` | `pr` mode: `new_branch: true` when HEAD is the default branch, or a branch whose PR has merged (and none is open). The change then goes on a fresh branch |
| `uncommitted` | Files `run` will stage with `git add -A` and commit |
| `commits_ahead` | Commits already on the branch that will ship with it |
| `open_pr` | An open PR that `run` will reuse instead of opening one |
| `problems` | A detached HEAD, or nothing to ship. Report and stop |

**Read `uncommitted` before going on.** Everything listed gets committed, so stop and ask about anything that doesn't
belong to the change: scratch files, a local file such as `machine.yml`, anything that looks like a secret.
`.gitignore` already covers the installer's state and plan files.

## 2. Draft the commit message (only when `uncommitted` isn't empty)

Follow `~/.claude/personal/commit-style.md` for what the message says and how it sounds, with this repo's mechanics: no
ticket key (a toolbox has no tickets), and a body is required (it becomes the PR description in `pr` mode). The script
refuses a trailer.

Write it to a file in the session scratchpad: the subject line, a blank line, then the body. Don't wait for approval.
Show the message in the report.

## 3. Run

```bash
python3 ~/.claude/skills/ship-toolbox-change/scripts/ship.py run --message-file <file> [--branch <name>] [--title "<title>"]
```

`pr` mode:

1. **Branch,** when `new_branch`. The name is `--branch`, else `<branch_prefix>/<subject as kebab-case>`. From the
   default branch it starts at HEAD, so unpushed local commits come along; from a merged branch it starts at
   `origin/<default>`. Uncommitted changes ride along. Git refuses, and nothing changes, if one would be overwritten.
2. **Commit:** `git add -A`, then commit with exactly the message file.
3. **Push** with `-u`, never forced.
4. **PR:** reuses the open one, else opens one against the default branch. The title is the oldest commit's subject,
   unless `--title` is passed (pass it when the branch carries several commits and the oldest subject doesn't describe
   the whole change). The body is the commit body, or a list of every commit's subject and body when there are several.
5. **Merge:** `gh pr merge --merge --match-head-commit <HEAD>`.
6. **Cleanup:** deletes the remote branch, switches to the default branch and fast-forwards it, and deletes the local
   branch with `git branch -d`, which refuses an unmerged branch. Nothing is force-deleted or reset.

`direct` mode:

1. **Commit** on the current branch with exactly the message file.
2. **Rebase** onto `origin/<default>` when it moved. A conflict stops the run (exit 3) with the rebase in progress.
3. **Push** `HEAD` to the default branch, never forced.
4. **Cleanup:** fast-forwards the local default branch; from another branch, switches to it and deletes that branch.

Both end with the **install preview**: `install.py` in preview mode in the main checkout, or in `claude-toolbox` for a
glue toolbox with no installer of its own, returning any pending sections.

If a step fails (exit 3), the output names the steps that ran. Don't re-run `run` blindly:

- An open PR is reused, but a merged one isn't.
- If the commit or push already happened, `uncommitted` is empty on the next precheck.

Finish by hand, or with `ship.py cleanup --branch <branch>` once the PR shows merged.

## Report

- The commit message and the files committed (when there was a commit)
- The branch, the PR URL and title, and the merge commit (`pr`), or the commit pushed (`direct`)
- What cleanup did
- The install preview: when it has pending changes (new or renamed skills, removed files), list them and ask whether to
  apply (`/install-domain`, or `python3 install.py --apply` in the repo). When it's empty, say the links are already in
  sync.
