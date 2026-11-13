# Merge Phase

Publish on the user's approval at stop 2, watch the PRs, merge on their instruction and clean up; after a
shared-library cycle, wait for the release and start the consumers cycle. Stages `awaiting-ci` and `awaiting-merge`,
then `status: done`, where `done.md` takes over. `<bin>` is the scripts folder (SKILL.md). The publish and the merge
run through their stage skills (`publish-change`, `finish-change`), which call `publish.py`; its commands print JSON,
with exit codes 0 done, 2 a precheck failed (nothing changed), 3 a step failed part way, 4 halted for the session or
the user, and a run resumes from wherever it stopped, so after fixing a cause, run the same command again.

## Publish

On approval only, and only with nothing `Pending`. First hook `before-publishing` (SKILL.md "Extension Points"):
whatever an extension does before anything is committed. Then run the `publish-change` skill with the Skill tool, args
`<work id> --approved`: the saved messages are the approved ones. It gates them, then commits every worktree with its
message and rebases onto `origin/<default>` when that has new code.

**With `pr.required`:** it also pushes, opens the PRs (appending them to `prs` and setting `status=review` and the
stage) and posts the announcement, verbatim, when the config has `pr.announce`; the approval covered it. Then:

1. `<bin>/tasks.py set <work id> owner=<user>`; the merge is their move.
2. Report the PR links and end with the hand-off block (read `hand-off.md` for it). Don't start a watcher here: it
   belongs to the session that resumes from the pasted prompt, whose wake is cheap. If the user answers here instead,
   go on with "Watch the PRs" in this session.

**Without `pr.required`:** it commits and rebases and pushes nothing. The approval covers the push to the default
branch, so go straight on to "Merge", step 1. Running it again is safe: a commit already made is skipped, so a session
that died between the two resumes at stop 2 and the approval runs both again.

On a failure, report where it stopped (`stopped_at`) and why:

- a refusal for a message that is missing or can't be committed, or exit 2 with `problems` (a message without a body,
  a stray file, a branch that doesn't match `git.branch`): nothing was written. A wording problem goes back to stop 2;
  anything else is a STOP.
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
tests; integration tests as SKILL.md "## Checks" says), draft a follow-up message with
`git-draft-commit-message`, and on their approval commit and push it with `git-commit`. Then start the watcher again:
the push re-runs CI.

Before merging, or if the user says to stop watching, stop the watcher (`TaskStop`).

## Merge

1. On the user's merge instruction, when they merged on GitHub, or, without `pr.required`, right after publishing: the
   `finish-change` skill with the Skill tool, args `<work id>`. It gates the merge, says what will merge, merges by
   `pr.merge` (or pushes straight to the default branch), removes the worktrees, deletes the merged branches, sets
   `status=done` and forgets the task's review. With every PR already merged it runs cleanup only. The user's
   instruction covers a squash that collapses several commits and an `--admin` merge, both of which it names: don't ask
   again, as its "ask once" is for a user who hasn't seen them; with no `pr.required`, the approval at stop 2 is the
   instruction. Its refusals, merging and removing nothing:
    - a PR waiting on a review approval or on CI: start the watcher ("Watch the PRs") and run it again on `ready`.
    - a PR or branch behind the default branch: `<bin>/publish.py push <work id>` rebases it (and pushes the PR's
      branch), then, with `pr.wait_for_ci`, `<bin>/publish.py wait-ci <work id>` in the background (it exits on a
      result), then `finish-change` again. A rebase conflict (`publish.py push` exit 4 with `conflicts`) is handled as
      in "Publish".
    - uncommitted or untracked files in a worktree: STOP; they are the user's to settle.
   An exit 3 is a merge that failed or left a PR open: report it; running it again is safe. The cleanup of a
   `shared-library` cycle leaves the task open (`next: release`).
2. Hook `after-the-merge`. By default: after the shared-library cycle (the cleanup set `status=review
   stage=awaiting-ci owner=claude` and printed `next: release`), "Wait for the Release". Otherwise nothing: read
   `done.md` and follow it.

## Wait for the Release

After the shared-library cycle's merge (`cycles.md`): `review`, `awaiting-ci`, `cycle:
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
