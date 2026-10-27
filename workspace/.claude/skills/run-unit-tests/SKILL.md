---
name: run-unit-tests
description: Run the unit tests of one or more areas of a checkout or task worktree, from the config's per-area `unit` command, and report pass/fail briefly with counts and the first failure's error line. Use to run unit tests or check that a change builds.
allowed-tools: Bash(*), Read, Grep, Glob
---

# Run Unit Tests

**Extensions.** Before anything else, run `.claude/toolbox/bin/toolbox.py config extensions run-unit-tests` from the
workspace root (its absolute path is in the toolbox-config rule) and read every file it lists. Each `##` section of an
extension names a heading of this skill: follow a `Replaces` section instead of that heading's text, and an `Adds to`
section after it. If two extensions replace the same heading, stop and ask the user which applies.

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
  (`<worktrees>/<work id>/<repo>` in a `nested` workspace, `<worktrees>/<work id>/<area>` in a `single` one). A bare
  area runs in the workspace root's folder of that name.
- **--test**: run one test class or method. It replaces `{test}` in the area's command; an area whose command has no
  `{test}` fails with that reason instead of running everything.

## Workflow

### Run

```bash
.claude/toolbox/bin/run_tests.py unit <area[=dir]>... [--test <test>] [--label <work id>]
```

Run it in the background (`run_in_background`) and end your turn; the session wakes once, when every area has finished.
Each area runs its `unit` command (the `"*"` area's when it has none) in its folder, with its output in
`$TMPDIR/<label>-<area>-unit.log`. An area with no command is `skipped`, which is neither a pass nor a failure; say so.

### Report

Pass the script's lines on as they are; don't grep the log yourself. Per area:

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
