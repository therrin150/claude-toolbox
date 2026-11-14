# Unattended Run

`/work-task <KEY> --unattended`: the user is away and approved, in advance, skipping stop 1 and stop 2 and committing
and pushing once the checks and reviews pass. Every check and review still runs; only the two stops go. Nothing else
is relaxed: the `STOP Conditions` hold, a failed third attempt or a `blocked` check still ends the run with the task
stopped, and a session never decides for itself that it is unattended.

## Flow

1. **Work and check** as `work.md` says, through step 4 (format, tests and review fixes). The **Ask** findings are not stops: decide each
   yourself, taking the smallest change inside the task's scope or declining with a one-line reason, and `tasks.py log
   <work id> "decided: <call>, because <reason>"` each so the user can find them. A decision that would widen the
   scope, change a public contract or touch another task's files is a STOP instead.
2. **Save the review report** to the publish folder as `code-review.md` (the stop 1 format, no hand-off block, its
   `Decisions` section listing what you decided and why). No review page, waiter or notification.
3. **Draft and publish:** the `publish-change` skill, args `<work id> --draft-only`, then `<work id> --approved` right
   after: the flag is the approval. Follow `merge.md` "Publish" and "Merge" as for an approved stop 2. With
   `pr.required`, the PRs are opened and the run ends there with `owner=<user>`: merging still needs the user.
   Publish through the stage skills only, never by hand. A rebase conflict (`publish.py push` exit 4) is resolved only
   when it is mechanical (formatting, imports, adjacent lines), re-running the checks it touches; otherwise STOP.
4. **Denied commands:** if a permission prompt or classifier denies a publish step, STOP at once, naming the exact
   command, so the user (or an orchestrating session they authorized) can run it. Don't look for another route.
5. **Done:** `done.md`. The final message is the report: the commit hash, every decision from step 1, anything declined,
   and the real state of each check (nothing is "not verified": that would have been a STOP).
