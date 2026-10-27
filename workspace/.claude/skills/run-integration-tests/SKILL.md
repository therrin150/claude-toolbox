---
name: run-integration-tests
description: Run an area's integration tests (the config's per-area `integration` command) against the services `start-local-services` is running, inside its lock bracket, and report pass/fail briefly.
allowed-tools: Bash(*), Read, Grep, Glob
disable-model-invocation: true
---

# Run Integration Tests

**Extensions.** Before anything else, run `.claude/toolbox/bin/toolbox.py config extensions run-integration-tests` from
the workspace root (its absolute path is in the toolbox-config rule) and read every doc it lists. Each `##` section of a
doc is a verb and one of this skill's hook ids ("Extension Points" below): at that hook, follow an `add` section after
the hook's own text and a `replace` section instead of it; for a `skip` section, don't run the step, and list it
wherever this skill reports its steps as `⏭️ <step>: skipped by <doc>: <reason>`. An added step that fails stops the
skill for the user, unless its doc says otherwise, and what a step reports goes in this skill's next report. If two docs
replace or skip the same hook, stop and ask the user which applies.

Runs inside a lock bracket: `start-local-services` (locks) → this skill → `stop-local-services` (unlocks). It
neither starts nor stops services, and it doesn't fix code; the caller decides what to do with a failure (and counts
attempts).

## Arguments

```
/run-integration-tests <area[=<dir>]>... --token <token> [--name <lock name>] [--test <ClassName[#method]>]
```

- **area[=dir]**: as in `run-unit-tests`; the same checkout `start-local-services` started.
- **--token**: the lock token from `start-local-services`. **--name** is its lock name, left out in a `single`
  workspace.
- **--test**: run one test; it replaces `{test}` in the area's command.

## Workflow

### Check the bracket

Hook `check-the-bracket`.

```bash
.claude/toolbox/bin/services_lock.py status [name]
```

- The lock's `owner.token` must equal your token. If it doesn't, or the lock is gone, stop and report: what is running
  on those ports may not be yours.
- Note `owner.expires`. If fewer than 5 minutes remain, don't start a run that can't finish; report it and let the
  caller stop and restart the bracket.
- Confirm the lock's ports accept connections (`nc -z localhost <port>`, or the health URL `start-local-services` was
  given). If one doesn't, report it and stop.

### Run and report

Hook `run-and-report`.

```bash
.claude/toolbox/bin/run_tests.py integration <area[=dir]>... [--test <test>] [--label <work id>]
```

Set the Bash timeout to the time left on the lock, not the default, and run it in the foreground (a subagent can't wake
from the background). Each area runs its `integration` command (the `"*"` area's when it has none).

Pass the script's lines on as they are; don't grep the log yourself. `pass` is the only pass:

- `fail` lists each failing test with its first assertion or exception line, or the exit code, first error line and the
  log's tail for a build tool other than Maven.
- `build-failed`, `no-tests` (a compile error, no tests found, a failure before the tests): the first error lines and
  the log path. A run that never reached the tests is not a pass.
- `skipped`: the area has no `integration` command. Report it as not run, never as a pass.

### Maven builds

Failsafe's reports are read first, Surefire's when failsafe found none (modules that name their integration tests
`*Test` run them in Surefire's phase). If both have no tests, the module has no runnable integration tests: report that,
not a pass.

Leave the services running. The caller stops them with `stop-local-services [name] --token <token>`.

## Extension Points

Hooks for extension docs (`.claude/extensions/README.md`).

| Id | Runs | Default | Verbs |
|---|---|---|---|
| `check-the-bracket` | "Check the bracket", before any test runs | the lock's token, time left and ports | add |
| `run-and-report` | "Run and report" | `run_tests.py integration` with each area's `integration` command | add, replace |
