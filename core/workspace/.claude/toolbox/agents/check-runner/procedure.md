# check-runner Procedure

What `check-runner` does for one spawn. The script does the running and the extraction; you judge what it can't and fill
`report.md`. `<bin>` is the path the prompt gave you.

## Run the Check

```bash
<bin>/run_checks.py <mode> <area=dir>... [--test <test>] [--skip-tests] [--task <work id>] \
  [--ports <p1,p2>] [--health-url <url>]...
```

Run it in the foreground with a Bash timeout of 600000 (10 minutes), alone, one call. If the harness moves it to the
background anyway, wait for it to finish; never start it a second time. It prints one JSON object (`result`, `areas`
with counts and `failures`, and for `integration` the `services` and the `lock`) and exits 0 on pass, 1 on fail, 3 on
blocked. It never prints a log path and you never look for one: everything the report needs is in the JSON.

Fill the report from the JSON as `report.md` says. Pass failures on as the script gave them; don't regroup, reword or
shorten an assertion.

## What the Script Can't Classify

Judge these, from the JSON's `reason`, `build_errors`, `problem` and `tail` alone:

- **A compile error or failed build** (`build_errors`): `fail`, with the first errors as the failure.
- **A Spring context that won't start, a missing property, an exception before any test:** `fail` with the exception
  line, when it is the checkout's code or config.
- **A port already in use, or a service that fails the same way twice with nothing in the checkout to blame:**
  `blocked`. The script never kills what holds a port, and neither do you.
- **A run that ran no tests, or an area with no command:** the script says `fail` or `skipped`; keep its word. A run
  that never reached its tests is never a pass.
- **Anything you don't understand:** `blocked` with what you saw. A guess isn't an answer.

A `fail` whose cause looks outside the code (a timeout with no error, a port that frees up a moment later) may be re-run
once; name the retry in `notes`. Never re-run a `fail` with test failures.

## Integration

`integration` is `run_checks.py`'s lock bracket: acquire, start the area's services, run its integration tests, stop,
release. It always stops and releases, whatever happens. Before running it, check whether the workspace starts its
services some other way:

```bash
<bin>/toolbox.py config extensions start-local-services --apply
```

- **No docs listed:** run it as above. Its `blocked` answers are real: the services lock is held by someone else
  (`holder`), `start` or `integration` isn't set for an area, or the `services-lock` feature isn't installed.
  A lock another checkout holds is `blocked`, not an attempt; say whose it is.
- **Docs listed:** an extension changes how this workspace starts services (the config has no `start` command for it),
  so `run_checks.py integration` can't. Do the bracket by hand: `bracket.md`.

Never run `services_lock.py acquire` or `start`, or an area's `integration` command, outside one of those two ways.

## Other Modes

`build`, `unit` and `format` need no services and no lock. `format` may rewrite files in the checkout; that is its job.
`build` without `--skip-tests` runs the area's unit tests too.
