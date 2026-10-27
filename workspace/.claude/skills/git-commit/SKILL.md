---
name: git-commit
description: Commit and push the working-tree changes in one or more repos or task worktrees with a message Allen supplied or approved, after showing what will be staged. Use when Allen says to commit; work-task publishes through the same script.
allowed-tools: Bash(*), Read, Write, Grep, Glob
---

# Commit Changes

**Extensions.** Before anything else, run `.claude/toolbox/bin/toolbox.py config extensions git-commit` from the
workspace root (its absolute path is in the toolbox-config rule) and read every file it lists. Each `##` section of an
extension names a heading of this skill: follow a `Replaces` section instead of that heading's text, and an `Adds to`
section after it. If two extensions replace the same heading, stop and ask the user which applies.

Commit finished work in one or more checkouts and push the branches. Invoking this skill **is** the explicit
instruction to commit *and push* that the global git rule's "Git Writes" section requires; approving the preview
covers both, with no second confirmation before the push. It never opens PRs (`gh-create-pull-request`), and never
force-pushes, amends, resets, switches branches or tags. No trailers: see the global git rule. The script refuses a
message that carries one and checks the commit after.

The mechanics are `publish.py`'s `commit` and `push` steps, which print JSON (run from the workspace root, or by the
absolute path in the toolbox-config rule):

```bash
.claude/toolbox/bin/publish.py commit <id> --dry-run --checkouts <paths> --message-file <F> --no-record
.claude/toolbox/bin/publish.py commit <id> --checkouts <paths> --message-file <F> --no-record
.claude/toolbox/bin/publish.py push <id> --checkouts <paths> --no-record
```

`<id>` is the task's work id: the ticket key on the branch, else a slug of its description. A checkout is a repo name of
the workspace or a worktree path. `--message-dir <D>` (a `<repo>.txt` per repo, or `commit.txt` in a single-repo
workspace) replaces `--message-file` when repos get different messages. For a task's own worktrees, drop `--checkouts`
and `--no-record`; `publish.py`'s docstring has every check and exit code.

## Invocation

```
/git-commit "KEY-123 Do The Thing"                  # derive the checkouts from this conversation
/git-commit <repo> "KEY-123 Do The Thing"
/git-commit <repo-a> <repo-b> "KEY-123 ..."         # one shared message across a batch
/git-commit --no-push <repo> "KEY-123 ..."          # commit only: run the commit step and stop
/git-commit <repo> "Do the thing"                   # no ticket: the branch has a description only
```

The branch follows `git.branch`. The subject leads with the ticket key only when the branch has one (the script warns
if it doesn't); with no ticket there is no key to lead with.

With no checkout named, pick them from the work done in this conversation and say which and why before anything else.
If that's ambiguous, ask.

## The Message

The message is Allen's, verbatim: subject, blank line, body. Write it to a file in the session scratchpad. Don't edit
it. If something looks wrong (a typo in the key, a branch with a key whose subject lacks it, a subject the diff
doesn't support), say so and wait. A subject-only message gets a request for a body, never an invented one;
`git-draft-commit-message` drafts one for Allen to approve.

Two rules exist because `gh-create-pull-request` builds the PR from these commits:

- The **subject becomes the PR title verbatim**, from the *oldest* commit on the branch, so a second commit on a branch
  won't change the title.
- The **body becomes the PR description**.

When one message covers a batch, check it's true of every repo. If a repo's diff holds something the message doesn't
describe (a dependency bump, a rename, a test), say so before committing, so Allen can extend the message or give that
repo its own.

## Step 1: Precheck

Run the `--dry-run` form. Any `problems` in any repo halt the whole batch: report them and change nothing, because
`gh-create-pull-request` treats the batch as a unit. A repo whose `action` is `clean` is reported and dropped, not a
failure; `committed` means it was committed earlier and only needs a push; say so.

## Step 2: Show what would be committed, and wait

Per repo, list the files the precheck grouped (modified, added, deleted, renamed, untracked): paths, not the diff. Call
out each `suspicious` path and say why it may not belong: build output, IDE files, scratch names (`tmp`, `probe`,
`scratch`, `copy`, `.orig`, `.bak`), a new file under a test folder the conversation didn't create. List any `staged`
files too: they were in the index before this run.

Then **stop and wait for approval.** One approval covers the whole batch, commit and push.

**Called by `work-task`:** it publishes through `publish.py run`. Allen's approval at its publish review stop covers
the repos, messages and changes, so nothing here waits again.

## Step 3: Commit and push

Run `publish.py commit`, then `publish.py push` (skip the push for `--no-push`). Per repo, alphabetically, the commit
step stages everything listed (`git add -A`), commits with the message file, and checks the tree is clean, the subject
matches and no trailer crept in (a hook or commit template can add one; if so it stops before pushing, and you say so
plainly). The push step first rebases onto the latest `origin/<default>` when that has new code commits, then pushes.

- A rejected commit (a hook) leaves the index staged for Allen to inspect. Report the hook's output verbatim.
- A rebase `conflicts` result (exit 4) leaves the rebase in progress. Resolve it, `git rebase --continue`, re-run the
  tests the task needs, and run the push step again.
- A rejected push means the remote has commits this branch doesn't. Report it; never force, fetch, merge or retry on
  your own.

## Report

One table: repo, short SHA, subject, files committed, pushed. Then anything dropped or left to finish by hand.
