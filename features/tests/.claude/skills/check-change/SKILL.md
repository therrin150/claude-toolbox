---
name: check-change
description: Run a change's checks (build, unit, format or integration) through a check-runner agent, so the output stays out of this session. Refuses when the change has no changes to check, or, for integration, while the services lock is held elsewhere. Use after editing code in a task's worktree - "run the unit tests", "check this change", "run the integration tests".
allowed-tools: Bash(*), Read, Agent
---

# Check Change

The check stage, for any session: one check of the areas a change touches, run by a `check-runner` agent. The main
session never runs `run_checks.py`, `run_tests.py`, `services_lock.py` or an area's own `build`, `unit`, `format` or
`integration` command (a hook denies them), and never reads a build, test or service log. It checks only its own
requirements, never that an earlier stage ran or that a later one will.

## Arguments

```
/check-change <work id> <mode>[,<mode>...] [<test>...]
```

- **work id**: the change's record, the one `start-change` printed. Its worktrees are the change.
- **mode**: `build`, `unit`, `format` or `integration`. Several comma-separated (`format,unit,integration`) are one run by
  one runner, in the order given; it stops at the first mode that does not pass. Use that instead of spawning a runner
  per mode.
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

   Report the reason in a line and stop; don't spawn a runner. On success it prints the `areas` to check as
   `<area>=<dir>`, the `skipped` ones, each checkout's `diff` and `bin`, the scripts folder.
2. **Spawn the runner** with `Agent` (`subagent_type: check-runner`, `run_in_background: false`), passing the mode(s),
   the `areas`, `bin`, the work id and, when only some need re-running, the test. One runner runs all its areas. For
   `integration` in a nested workspace, spawn one runner per repo (each has its own lock), one at a time when two
   repos' services may share a port.

   **Wait for the report in this same turn, then act on it (step 3).** The foreground call returns the report. If the
   host returned before it (it ran the agent in the background anyway), do not end your turn: block until the report
   arrives with `Monitor` (an until-loop on the agent's output file for its final report, or for the file to stop
   growing and the agent to be listed done), a foreground wait of at most ten minutes per call. Nothing wakes a session
   that ends its turn here, so ending it stalls the task.
3. **Act on its report**, `pass`, `fail` or `blocked`, with the counts and each failure's test, assertion, expected and
   actual (with several modes, each line says which mode):
   - `pass`: say so per area, and which areas were `skipped` and why.
   - `fail`: report each failure. Fixing it and counting attempts is the caller's call; this stage runs the check
     once. A caller that fixes and re-runs (`work-task`) counts the attempts: at most three per area, then ask the user.
   - `blocked`: say why. It is never a pass and never skippable: the caller decides (`work-task` STOPs; see its
     "Checks"). A lock held elsewhere isn't a failure; a port held outside the lock is a block, with the holder named.
   - `lock: NOT RELEASED`, in any result: stop and tell the user.
   - A takeover in `notes`: the runner took over an expired lock; tell the user if it was theirs (a manual one).

Don't edit the worktree while a runner runs. If the runner itself misbehaves, the fix goes in its agent file or its
docs (`.claude/toolbox/agents/check-runner/`), never a by-hand run.
