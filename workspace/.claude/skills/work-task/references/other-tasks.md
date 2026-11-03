# Working Around Other Tasks

Read when the task depends on another that hasn't merged, under `work.dependencies: isolate`: from a `depends_on` key,
or from the code (it won't compile, or would break, without the other task's change). There are two cases.

## Shares a Repo

**The other task's change is code in a repo this task also changes.** Carry the minimum of it in this task's own
branch, so this task builds and tests on its own (delete the class the other task deletes, if this task's change needs
it gone, say). Don't take on the rest of the other task. Name every carried change on the `Overlaps:` line at stop 1
(`code-review.md`). Whichever change merges second settles the overlap like any merge conflict; identical changes
usually merge cleanly.

## Needs a Release

**This task needs something the other task will publish** (a library version, an image, a schema in a shared
registry) that doesn't exist yet. Hook `needs-a-release`, which an extension may replace with a workspace's own
release wait. By default:

Isolation can't supply it, so do and test everything that doesn't need it, and hold back the rest. At both stops, add a
`Pending:` line naming what's held back and what it waits on; the user can review and ask for changes, but approval
doesn't publish. After presenting stop 2, STOP with `work_note` naming what it waits on. When the user says it's out,
clear the note, set `status=in-progress stage=testing`, apply what was held back, format, re-run the checks that apply
(`work.md`, steps 3–5), and return to stop 1 with only what changed; stop 2 follows with fresh drafts, and its approval
publishes as usual.
