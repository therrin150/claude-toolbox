---
name: gh-create-pull-request
description: Open GitHub PRs for one or more pushed repos or task worktrees from their commits, using each repo's PR template, then announce the batch in the configured channel. Rebases onto the latest default branch first. Use after git-commit, when Allen says to open a PR.
allowed-tools: Bash(*), Read, Write, Grep, Glob, ToolSearch, PushNotification
---

# Pull Request

Open PRs for committed work and announce them. The only git write besides the push is `git rebase` onto the latest
`origin/<default>` when it has new code commits. Never an amend, a branch switch or a stash, and a force-push only
as `--force-with-lease` against the exact sha seen, only after that rebase rewrote a branch the remote already had.

The mechanics are `publish.py`'s `push` and `open-pr` steps, which print JSON (run from the workspace root, or by the
absolute path in the toolbox-config rule):

```bash
.claude/toolbox/bin/publish.py push <id> --checkouts <paths> --no-record
.claude/toolbox/bin/publish.py open-pr <id> --checkouts <paths> --announce-file <F> --no-record [--reviewer R]
```

`<id>` is the task's work id. A checkout is a repo name or a worktree path; for a task's own worktrees, drop
`--checkouts` and `--no-record` so the task record gets the PR URLs. The script's docstring has every check, the title
and body rules, and the exit codes.

**This skill needs `pr.required: true`.** With `pr.required: false` there are no PRs: changes go straight to the
default branch through `publish.py merge`.

## Invocation

```
/gh-create-pull-request                      # derive the repos from this conversation's work
/gh-create-pull-request <repo>
/gh-create-pull-request <repo-a> <repo-b>
/gh-create-pull-request <repo> --reviewer <github-login-or-org/team>
```

- **No repo named:** pick the repos from the work done in this conversation and say which and why before anything
  else. If that's ambiguous, ask.
- **Reviewer:** none by default. `--reviewer` asks one login or `<org>/<team-slug>` for every PR in the batch.

## Step 1: Push and precheck

Run `publish.py push`. A `conflicts` result (exit 4) leaves the rebase in progress: notify (`PushNotification`),
report the files and the commits master brought (`brought_in`), and stop; the session resolves it,
runs `git rebase --continue`, re-tests, and runs again. A rebase that went through is reported with what it brought in.

Then `publish.py open-pr --dry-run`. Any `problems` in any repo halt the whole batch: report them, open nothing, post
nothing. The announcement covers the batch as a unit, so a partial batch must never be announced. The common ones: the
branch isn't pushed, a PR is already open (its URL is reported), or the announcement text is missing.

## Step 2: The announcement, and wait

**Called by `work-task`:** it publishes through `publish.py run`, with the announcement Allen approved at its publish
review stop, so nothing here waits again.

When the config has `pr.announce`, draft one sentence for the whole batch per `git-draft-commit-message`'s
"The Announcement", show it with the PR titles from the precheck, and stop. Revise until Allen approves. **Approving
the sentence authorizes both the PRs and the post**; no second confirmation. Write it to a file in the session
scratchpad. With no `pr.announce`, show the PR titles and wait for Allen's go-ahead instead.

## Step 3: Open

Run `publish.py open-pr` with `--announce-file`. It opens the PRs, ready for review, not drafts. Act on its result:

- **A failed create** (exit 3) stops the batch: the output lists the PRs that exist and the repos `not_opened`. Ask
  before posting anything. Re-running is safe: every create looks the branch up with `--state all` first and reuses an
  open PR, so it never opens one twice.
- **`reviewer_error`** on a PR: the PR stands; say the review request needs adding by hand.
- **`record_errors`:** the PRs exist but the task record wasn't updated; say so and give the failing command.

The PR body is the template with everything above `# Checklist` replaced by the commits' bodies and every box ticked;
a change with no ticket key leaves the "ticket number" box unticked. Say so in the report. A repo with no template gets
the commits' bodies alone.

Run no build and no tests; the PR's CI builds the merged result.

## Step 4: Post the announcement

When the output has `announce`, post it with the Slack send-message tool of the `announce.mcp_server` server
(`mcp__<server>__slack_send_message`; load it with `ToolSearch` if it is deferred), verbatim: `text` to
`announce.channel`. For a batch, reply in its thread (`thread_ts` = the parent's `ts`, no broadcast) with
`thread_text`. If the channel id errors, search for it by name with the server's channel search and tell Allen.

If the post fails on auth, ask Allen to refresh the Slack MCP auth and retry once. Otherwise, or if it still fails,
print the message text, the thread text and the channel so he can post it himself.

## Report

The PR URLs, the commits per repo, the post's link, and each repo's rebase when it brought anything in. If the run
halted, report only what failed and what Allen needs to do.
