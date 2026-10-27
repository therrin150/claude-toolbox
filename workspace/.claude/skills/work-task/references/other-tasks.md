# Working Around Other Tasks

Read when the task depends on another that hasn't landed, under `work.dependencies: isolate`: from a `depends_on` key,
or from the code (it won't compile, or would break, without the other task's change). There are two cases.

## Shares a Repo

**The other task's change is code in a repo this task also changes.** Carry the minimum of it in this task's own
branch, so this task builds and tests on its own (delete the class the other task deletes, if this task's change needs
it gone, say). Don't take on the rest of the other task. Name every carried change on the `Overlaps:` line at stop 1
(`code-review.md`). Whichever change merges second settles the overlap like any merge conflict; identical changes
usually merge cleanly.

## Needs a Release

**This task needs something the other task will publish** (a library version, an image, a schema in a shared
registry) that doesn't exist yet. Follow SKILL.md "### Needs a release", which an extension may replace with a
workspace's own release wait.
