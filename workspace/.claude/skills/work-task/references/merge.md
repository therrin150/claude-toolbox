# Merge Phase

Publish on the user's approval at stop 2, watch the PRs, merge on their instruction, clean up, and report usage; after a
shared-library cycle, wait for the release and start the consumers cycle. Stages `awaiting-ci` and `awaiting-merge`,
then `status: done`. `<bin>` is the scripts folder (SKILL.md). Every
`publish.py` command prints JSON; its exit codes are 0 done, 2 a precheck failed (nothing changed), 3 a step failed part
way, 4 halted for the session or the user. A run resumes from wherever it stopped, so after fixing a cause, run the
same command again.

## Publish

On approval only, and only with nothing `Pending`. First hook `before-publishing` (SKILL.md "Extension Points"):
whatever an extension does before anything is committed.

**With `pr.required`:** `<bin>/publish.py run <work id> --stop-after open-pr`. It commits every worktree with its
approved message, rebases onto `origin/<default>` when that has new code, pushes, opens the PRs (appending them to
`prs` and setting `status=review` and the stage), and prints `announce` when the config has `pr.announce`. Then:

1. Post the announcement as `gh-create-pull-request`'s "Step 4" says, verbatim; the approval covered it.
2. `<bin>/tasks.py set <work id> owner=<user>`; the merge is their move.
3. Report the PR links and end with the hand-off block (SKILL.md "Hand-offs"). Don't start a watcher here: it belongs
   to the session that resumes from the pasted prompt, whose wake is cheap. If the user answers here instead, go on
   with "Watch the PRs" in this session.

**Without `pr.required`:** `<bin>/publish.py run <work id>`. It commits, rebases onto `origin/<default>` when needed,
pushes the branch to the default branch (a fast-forward), removes the worktrees and the branch, and sets
`status=done`. Then go on to "Merge", step 2.

On a failure, report where it stopped (`stopped_at`) and why:

- exit 2: the `problems` (a message without a body, a stray file, a branch that doesn't match `git.branch`). Nothing
  was written. A wording problem goes back to stop 2; anything else is a STOP.
- exit 4 with `conflicts`: the rebase is left open in the worktree. STOP and show the conflicting files. On the
  user's go-ahead, resolve them, `git rebase --continue`, re-run the checks the resolution touches, and run the same
  command again.
- exit 3: a step failed part way (a push refused, `gh` down). Report the error; a STOP unless re-running is clearly
  safe (a timeout).

## Watch the PRs

`awaiting-ci` or `awaiting-merge`. Open on the PRs: `<bin>/watch_task.py status <work id>`, then one line per PR (repo
and number linked, review decision, checks, merge state) and any comments or reviews with text, each as author,
`path:line` for an inline one, and the text.

- `overall: ready`: notify and ask "approved and green, merge?".
- `overall: merged` (the user merged on GitHub): "Merge" for the cleanup; its run sees every PR merged and runs
  cleanup only.
- Otherwise start the watcher with `run_in_background: true`: `<bin>/watch_task.py prs <work id>`, and end your turn.
  It exits on the first change and wakes the session; act on each of its `events`, then start it again unless the PRs
  are merged:
    - `ready`: notify and ask "approved and green, merge?". After a rebase during "Merge", the user's merge instruction
      already covers it: run "Merge" again without asking.
    - `comments`, `changes-requested`: notify and show them as above. The user decides what to change.
    - `ci-failed`: notify with the failing checks and the PR link. The user decides whether it's a fix or a re-run.
    - `merged`: only when the output's `overall` is `merged` (every PR is), run "Merge" for the cleanup; its run
      cleans up only. While any PR is still open, say which merged and start the watcher again: merging the rest
      needs their instruction, and the run skips the merged ones. `closed`: STOP.
    - `timeout`: start it again without a message.

A change the user asks for after the PRs are open: make it in the worktree, format and re-run what it touches (unit
tests; integration tests per `work.md` step 5, reading only that step), draft a follow-up message with
`git-draft-commit-message`, and on their approval commit and push it with `git-commit`. Then start the watcher again:
the push re-runs CI.

Before merging, or if the user says to stop watching, stop the watcher (`TaskStop`).

## Merge

1. On the user's merge instruction, or when they merged on GitHub: `<bin>/publish.py run <work id>`. Every step
   already done is skipped; it rebases a PR that fell behind the default branch and pushes it, waits for CI (with
   `pr.wait_for_ci`), merges by `pr.merge`, removes the worktrees, deletes the merged branches, and sets
   `status=done`. With every PR already merged it runs cleanup only (`skipped` in its output); a merged PR is never
   rebased or pushed. If its output shows a `push` or `open-pr` step for a merged PR, STOP: that is a `publish.py`
   bug. Run it in the background when CI may take a while (it exits on a result). Exit 4 is a halt, not a failure:
    - `needs_confirmation`: squashes that collapse several commits. Show the user, then run again with
      `--confirm-squash`.
    - waiting for a review approval, or CI timed out: start the watcher ("Watch the PRs") and run again on `ready`.
    - `conflicts`: as in "Publish".

   A PR whose merge precheck shows `admin_merge` (every file it changes is path-filtered out of the workflows behind
   its required checks, so they never report) merges with `--admin`; the user's merge instruction covers it. Name it
   and its `missing_checks` in the report. A refused worktree removal (uncommitted files) is a STOP. Report its
   `warnings`.
2. With the `review-ui` feature, forget the task's review and its comments, and prune reviews whose repos are gone:
   `python3 .claude/skills/local-review-ui/scripts/review.py forget <work id> --delete-comments`, then `... prune`.
3. Hook `after-the-merge`: by default SKILL.md "### After the merge".

## Wait for the Release

After the shared-library cycle's merge (SKILL.md "### Plan the cycles"): `review`, `awaiting-ci`, `cycle:
shared-library` and no `worktrees`. Start `<bin>/watch_task.py pipeline <work id>` with `run_in_background: true` and
end your turn. It finds the default branch's runs of each merge commit (the merged PRs', or without PRs the commits
`publish.py` pushed) and watches them; a green run means the release is published. A PR merged with `admin` may
publish no release (the default branch's build likely filters the same paths): if no run carries `tags`, STOP and ask
whether the consumers cycle still applies.

- Exit 0: notify, naming the release (each pipeline's `tags`, a build number, say, when there are any), and start the
  consumers cycle without asking: it has its own stops, and nothing in it writes to git before stop 2's approval.
  `<bin>/tasks.py set <work id> cycle=consumers status=in-progress owner=claude stage=implementing`, then read
  `work.md`: "Before the First Cycle" step 1 (this session may not have read the plan; its other steps are done), then
  "Each Cycle" for the consumers. This session's context is small, so no hand-off until stop 1.
- Exit 3: notify with its `error` (and the run's URL, when a run failed). When the user says it's been re-run (or the
  workflow dispatched on the same commit), start the watcher again.
- Exit 2: STOP (a PR not merged, or nothing recorded to watch).

## Done

`<worktrees>/<work id>/` must be gone, leftovers included: an IDE can write into a folder after its worktree was
removed. `<bin>/worktrees.py list <work id>` lists nothing once it is; if the folder is still there, run
`<bin>/worktrees.py remove <work id>` once more and report anything it refuses. Then hook `before-done`, and say the
task is done in one line.

## Usage

Last thing, once the task is done: read the `Weekly · all models` window from `get_usage` (leave the weekly flags off
unless `plan.status` is `ok`), run
`<bin>/session_usage.py --task <work id> --weekly-percent <percentUsed> --weekly-resets-at <resetsAt>`,
and show its `markdown` as is. It writes `usage` and `usage_total` to the record and carries its own caveats.
