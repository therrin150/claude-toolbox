---
name: check-change
description: Run a change's checks (build, unit, format or integration) through `run_checks.py`, which prints one trimmed JSON result so the raw output stays out of this session. Refuses when the change has no changes to check, or, for integration, while the services lock is held elsewhere. Use after editing code in a task's worktree - "run the unit tests", "check this change", "run the integration tests".
allowed-tools: Bash(*), Read
---

# Check Change

The check stage, for any session: one check of the areas a change touches, run by this session through `run_checks.py`,
in the foreground. The session never runs an area's own `build`, `unit`, `format` or `integration` command, or raw
`mvn`, `gradle` or `npm test` (a hook denies them), and never reads a build, test or service log: the script's JSON
carries everything. It checks only its own
requirements, never that an earlier stage ran or that a later one will.

## Arguments

```
/check-change <work id> <mode>[,<mode>...] [<test>...]
```

- **work id**: the change's record, the one `start-change` printed. Its worktrees are the change.
- **mode**: `build`, `unit`, `format` or `integration`. Several comma-separated (`format,unit,integration`) run one after another, in the order
  given, stopping at the first mode that does not pass (later ones are `not run`).
- **test**: only these tests, to re-run what failed. `work-task` runs every check through this stage and counts the
  attempts itself.

## Steps

1. **Gate it.** Run the script from the workspace root, or by the absolute path in the toolbox-config rule:

   ```bash
   .claude/toolbox/bin/change.py check <work id> <mode>
   ```

   It exits 1 with `refused` and the reason when:
   - there is no change to check: the id has no worktree, the worktree has no changed file, or the changes touch
     only areas whose `files` globs the checks ignore;
   - for `integration`, an area's `prepare` command fails (the script runs it first, from the worktree: env files,
     shared containers, a database; the refusal quotes its last lines), or the services lock is held (it names the
     holder). A lock the user holds by hand is theirs: tell
     them, don't wait on it silently.

   Report the reason in a line and stop. On success it prints the `areas` to check as `<area>=<dir>`, the `skipped`
   ones, each checkout's `diff` and `bin`, the scripts folder.
2. **Run it**, one mode at a time, alone, in the foreground:

   ```bash
   <bin>/run_checks.py <mode> <area=dir>... [--test <test>] [--skip-tests] [--task <work id>] \
     [--ports <p1,p2>] [--health-url <url>]...
   ```

   Use a Bash `timeout` of 600000 (ten minutes) and never `run_in_background`; if the harness moves it to the
   background anyway, wait for it with `Monitor` (an until-loop on its output file, at most ten minutes per call) and
   never start it a second time. It prints one JSON object (`result`, `areas` with counts and `failures`, and for
   `integration` the `services` and the `lock`) and exits 0 on pass, 1 on fail, 3 on blocked. It prints no log path
   and you never look for one. For `integration` in a nested workspace run one repo at a time (each has its own lock).

   **Integration with extensions.** Before `integration`, check whether the workspace starts its services some other
   way: `<bin>/toolbox.py config extensions start-local-services --apply`. With no docs listed, run `run_checks.py` as
   above. With docs listed, an extension changes how services start, so `run_checks.py integration` can't: do the
   bracket by hand as `.claude/toolbox/references/services-bracket.md` says.
3. **Act on the JSON**: `pass`, `fail` or `blocked`, with the counts and each failure's test, assertion, expected and
   actual (with several modes, each line says which mode). Pass failures on as the script gave them:
   - `pass`: say so per area, and which areas were `skipped` and why.
   - `fail`: report each failure. Fixing it and counting attempts is the caller's call; this stage runs the check
     once. A caller that fixes and re-runs (`work-task`) counts the attempts: at most three per area, then ask the user.
     A compile error or failed build (`build_errors`) is a `fail`, with the first errors as the failure; so is a Spring
     context that won't start, with the exception line. A `fail` whose cause looks outside the code (a timeout with no
     error, a port that frees up a moment later) may be re-run once, naming the retry; never re-run a `fail` with test
     failures. A run that ran no tests is never a pass.
   - `blocked`: say why. It is never a pass and never skippable: the caller decides (`work-task` STOPs; see its
     "Checks"). A lock held elsewhere isn't a failure; a port held outside the lock is a block: name the port and its
     holder (`lsof -nP -iTCP:<port> -sTCP:LISTEN`, or `docker ps --filter publish=<port> --format '{{.Names}}'`;
     read-only, one call each). Never kill what holds it.
   - `lock` not released, in any result: stop and tell the user.
   - A takeover of an expired lock: tell the user if it was theirs (a manual one).

Don't edit the worktree while a check runs. `format` may rewrite files in the checkout; that is its job. `build` without
`--skip-tests` runs the area's unit tests too. Never run `services_lock.py acquire` or `start`, or an area's
`integration` command, outside `run_checks.py integration` or the by-hand bracket.
