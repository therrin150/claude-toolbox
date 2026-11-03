---
name: run-unit-tests
description: Run the unit tests of one or more areas of a checkout or task worktree, from the config's per-area `unit` command, and report pass/fail briefly with counts and the first failure's error line. Use to run unit tests or check that a change builds.
allowed-tools: Bash(*), Read, Grep, Glob
---

# Run Unit Tests

**Extensions.** Before anything else, run `.claude/toolbox/bin/toolbox.py config extensions run-unit-tests --apply` from
the workspace root (its absolute path is in the toolbox-config rule). If it lists docs, read each and apply them at this
skill's hooks ("Extension Points" below) as its `apply` text says.

Runs the unit tests of areas, in parallel. It doesn't fix code; the caller decides what to do with a failure (and counts
attempts). Needs no local services and no lock.

## Arguments

```
/run-unit-tests [area[=<dir>]...] [--test <ClassName[#method]>]
```

- **area**: an area of the config (`toolbox.py config show workspace.areas`; in a `nested` workspace, a repo). Several
  run in parallel. If omitted, the areas the change touches: those with changed files in `git status` / the diff against
  the default branch, or every area when unsure.
- **dir**: the area's folder when it isn't the workspace root's: a task's worktree
  (`<worktrees>/<work id>/<repo>` in a `nested` workspace; in a `single` one, the area's folder under
  `<worktrees>/<work id>`, as `worktrees.py changes` prints it). A bare area runs in its folder in the workspace root:
  its `dir` (`workspace.areas.<area>.dir`), else the folder named for it.
- **--test**: run one test class or method. It replaces `{test}` in the area's command; an area whose command has no
  `{test}` fails with that reason instead of running everything. Without it, `{test}` is replaced with nothing.

## Workflow

### Run

Hook `run`.

```bash
.claude/toolbox/bin/run_tests.py unit <area[=dir]>... [--test <test>] [--label <work id>]
```

Run it in the background (`run_in_background`) and end your turn; the session wakes once, when every area has finished.
Each area runs its `unit` command (the `"*"` area's when it has none) in its folder, with its output in
`$TMPDIR/<label>-<area>-unit.log`. An area with no command is `skipped`, which is neither a pass nor a failure; say so.

### Report

Hook `report`. Pass the script's lines on as they are; don't grep the log yourself. Per area:

- `pass`: one line, with the test and module counts when the build was Maven.
- `fail`: each failing test with its first assertion or exception line (Maven), or the command's exit code, its first
  error line and the log's tail (any other build tool).
- `build-failed`, `no-tests`: the build's first error lines and the log path. A build that never ran its tests is not a
  pass. Read the log only when those lines don't name the cause.

### Maven builds

Maven's Surefire reports are read by `.claude/toolbox/bin/summarize_reports.py <dir> --kind surefire [--log <log>]`,
which `run_tests.py` calls, so counts and failures read the same everywhere. Use `verify`, never `install`, in an area's
`unit` command: the reactor resolves the checkout's own modules without `~/.m2`, so nothing the build produces leaks to
another session. Other build tools report through their own command's output.

## Extension Points

Hooks for extension docs (`.claude/extensions/README.md`).

| Id | Runs | Default | Verbs |
|---|---|---|---|
| `run` | "Run" | `run_tests.py unit` with each area's `unit` command | add, replace |
| `report` | "Report", once every area has finished | the script's lines, as they are | add |
