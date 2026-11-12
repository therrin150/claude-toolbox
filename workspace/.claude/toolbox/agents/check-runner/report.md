# check-runner Report

Return only this block, leaving out lines that don't apply. Never include a log path, a raw log line beyond the ones the
script printed, or advice on what to fix: the session that spawned you decides that.

```
result: pass | fail | blocked
mode: build | unit | format | integration
<area>: pass | fail | blocked | skipped (<tests> run, <failed> failed, <seconds>s) [reason]
failures:
  - <area> <Class#method or test name>: <first assertion line>
      expected: <expected>   actual: <actual>
      at <first frame>, <own-code frames>
  - <area> <what failed>: <reason or first error line>
services: started <area> (ports <p>), ... | start failed: <area>: <cause> | not started   (integration only)
lock: released | NOT RELEASED, token <token>: <why>   (integration only)
notes: <one line per judgment call: a lock wait (how long, whose lock), a retry, a takeover (previous_kind, previous)>
```

- `result` is the worst of the areas: `fail`, then `blocked`, then `pass`; every area `skipped` is `pass` with each area
  line saying `skipped`, never a pass of its own.
- `pass`: every area passed or was skipped.
- `fail`: a code problem: a test failed, the code didn't compile or format, an area ran no tests, or a service failed to
  start because of the checkout's code or config.
- `blocked`: something outside the code: the lock, a container runtime, a port held outside the lock, a missing
  `start` or `integration` command, or anything you couldn't judge. Say what is needed.
- One `failures` entry per failing test, from the script's `failures` (its `test`, `assertion`, `expected`, `actual` and
  trimmed `stack`); for a build, format or start failure with none, its `reason`, `build_errors` or `problem`, and at
  most the last three `tail` lines when the cause isn't in them. More than 20 failures: list the first 20 and say how
  many more.
- A `takeover` the script reports goes in `notes`, so the session can tell the user their manual lock was taken over.

## Contract

The report contract gate checks the report against this block when you hand it back, and denies one that breaks it,
naming what is wrong; fix the report and hand it back again. Its lines are `require <field>: <regex>` (some line
matches), `forbid <what>: <regex>` (no line matches) and `limit <n> <what>: <regex>` (at most n lines match);
`lib/report_contract.py` has the format.

```contract
require result: ^result: (pass|fail|blocked)\s*$
require mode: ^mode: (build|unit|format|integration)\s*$
forbid log path: (?:^|[\s("'`=:])(?:[\w.~/-]*/)?target/[\w./-]+
forbid log file: [\w/~-]+\.log(?=$|[\s:,;)'"`])
limit 25 stack frame lines: ^\s*at [\w$.<>/]+\(
```
