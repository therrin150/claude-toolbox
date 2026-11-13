# Two Cycles

Read from `work.md` step 6 when the task changes a shared library (`<bin>/toolbox.py config show shared_libraries`,
`names`; `update` is how a consumer picks up a release), and again at a consumers cycle's start. A task that changes no
shared library, or only the consumers of one that's already released, runs one cycle and never reads this.

The task runs two cycles, because its consumers must build against a released version, not a branch (SKILL.md, Ground
Rules, "Unreleased libraries"):

1. **Shared library:** the libraries the task changes, with their own worktrees, checks, both stops, publish and merge.
   Its checks are the libraries' own; consumers are tested in the next cycle, against the release.
2. **The release:** after the merge, wait for the default branch's CI run of the merge commit, which publishes it
   (`merge.md`, "Wait for the Release").
3. **Consumers:** the areas or repos that need the new release (the plan names them, else the code shows them: those
   the task also changes, and those whose build pins the library), in fresh worktrees from the latest
   `origin/<default>`. Step 2 of "Each Cycle" starts by picking up the release in each consumer checkout: with
   `update.command`, run it there (`{libraries}` is the released libraries' names, space-separated); with
   `update.skill`, run that skill on the checkout; with `update.path`, read that `SKILL.md` and follow it on the
   checkout (a skill this workspace doesn't install, such as a team's). Then the usual checks, stops and publish.

Say which cycles the task runs, and set `<bin>/tasks.py set <work id> cycle=shared-library`; each later cycle's start
sets `cycle` again (`merge.md` sets `cycle=consumers`). A task that needs another task's release, not its own, is
`other-tasks.md`, "Needs a Release".
