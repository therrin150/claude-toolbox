---
name: publish-change
description: Publish a change - draft its commit message from the diff summary, wait for the user's approval, then commit, push and open the PR through publish.py. Refuses when there is nothing to publish, a checkout is on the default branch, or no message is approved; warns when checks haven't run on the current diff. Use when the user says to commit, publish or open a PR for a change, on its own; `work-task` runs it for its publish stop.
allowed-tools: Bash(*), Read, Write, Grep, Glob, Agent, Skill, ToolSearch, PushNotification
---

# Publish Change

The publish stage, for any session: the commit message drafted for the user's approval, then the commit, push and PR
by `publish.py`. `work-task` runs the drafting and the publishing as two calls (below), with its own stop for the
approval between them; a session working outside `work-task` runs the whole skill. It checks only its own requirements,
never that review, verification or checks ran. A missing check run is a warning the user can accept, not a refusal. It
never merges: `publish.py run --stop-after` ends it before the merge step, and merging is the finish stage.

## Arguments

```
/publish-change <work id> [--checkouts <repo-or-path>[,<repo-or-path>...]] [--draft-only | --approved]
```

- **work id**: the change's record, the one `start-change` printed; it also names the publish folder that holds the
  approved texts. Its worktrees are the change.
- **--checkouts**: publish these repos or worktrees instead (any repo name or path), for a change that isn't a task's
  worktrees. Pass `--no-record` to `publish.py` then, as `gh-create-pull-request` does.
- **--draft-only**, **--approved**: for a driver with a stop of its own for the approval (`work-task`), which calls the
  stage twice. `--draft-only` runs steps 1 to 3, saves each text as drafted (step 4, since the driver presents them and
  the approval comes after), and returns the messages, the announcement and step 2's warnings without waiting or
  publishing. `--approved` is the second call, once the user has approved the saved texts: it runs steps 5 to 7. Without
  either flag the stage does all of it, waiting for the approval itself.

## Steps

1. **Gate it, before drafting.** Run the script from the workspace root, or by the absolute path in the toolbox-config
   rule:

   ```bash
   .claude/toolbox/bin/change.py publish <work id> --draft [--checkouts <a,b>]
   ```

   It exits 1 with `refused` and the reason, naming the checkout, when:
   - a checkout is on the default branch (or detached): a change publishes from its own branch, so say which checkout
     and that `start-change` makes one;
   - there is nothing to publish: no worktree, or no changed file against `origin/<default>`.

   Report the reason in a line and stop. On success it prints each `checkout`, the `messages` (where each approved
   message goes), `announce_file`, `pr_required` and `announce` (whether this publish announces), and any
   `warnings`.
2. **Warn about unrun checks.** A warning says an area's changes have no passing build, unit or integration run since
   the diff last changed (`check-change` runs them, and the script records the results). Tell the user in a line, name
   the areas, and go on: it doesn't stop the publish, and they may approve the message regardless. Never run the checks
   here unasked.
3. **Draft the message** with the `git-draft-commit-message` skill (the Skill tool): the checkout paths, `--task <work
   id>`, and `--announce` when the output's `announce` is true. Don't draft it yourself and don't read the diff: that
   skill takes the summary from `diff-summarizer`. Show the user each message (and the announcement) and wait for their
   approval; redraft on wording changes. Nothing is written until they approve.
4. **Save the approved texts** where the output says: each message to its `messages` path, the announcement to
   `announce_file`, byte for byte as shown. With `--draft-only`, save them as drafted and return (the driver's stop
   holds the approval).
5. **Gate the message.** (`--approved` starts here.) Run the gate again without `--draft`:

   ```bash
   .claude/toolbox/bin/change.py publish <work id> [--checkouts <a,b>]
   ```

   It refuses, naming the checkout, when a message is missing or can't be committed (no subject, no body, a trailer).
   Fix the file, or go back to step 3 when the wording is wrong. Its `warnings` are step 2's, current now.
6. **Publish.** With `pr_required`:

   ```bash
   .claude/toolbox/bin/publish.py run <work id> --stop-after open-pr [--checkouts <a,b> --no-record]
   ```

   It commits each checkout with its message, rebases onto `origin/<default>` when that has new code, pushes and opens
   the PRs. When the output has `announce`, post it as `gh-create-pull-request`'s "Step 4" says; the approval in step 3
   covered it. Without `pr_required`, run it with `--stop-after push`: it commits and rebases onto `origin/<default>`
   when that has new code, and pushes nothing. Putting the commit on the default branch is the finish stage's
   (`publish.py merge`), which refuses a branch that isn't a fast-forward.
7. **Report** the commits and the PR links. A failure reports `stopped_at` and why: exit 2 is a precheck that changed
   nothing (a stray file, a branch that doesn't match `git.branch`), exit 4 with `conflicts` leaves a rebase open to
   resolve with the user, exit 3 is a step that failed part way. Running the same command again resumes.

The next stage is finish (merge and clean up), when the user asks for it.
