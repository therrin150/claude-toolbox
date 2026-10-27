---
name: start-local-services
description: Start a checkout's local services (the config's per-area start commands) from the main checkout or a task's worktree, under the workspace's local-services lock, and report PIDs, ports and the lock token `stop-local-services` needs.
allowed-tools: Bash(*), Read, Grep, Glob
---

# Start Local Services

**Extensions.** Before anything else, run `.claude/toolbox/bin/toolbox.py config extensions start-local-services` from
the workspace root (its absolute path is in the toolbox-config rule) and read every file it lists. Each `##` section of
an extension names a heading of this skill: follow a `Replaces` section instead of that heading's text, and an `Adds to`
section after it. If two extensions replace the same heading, stop and ask the user which applies.

Every run holds the lock (`lock-local-services`) from before the first service starts until `stop-local-services` stops
them, so two sessions never run the same services at once. The commands to run are the config's, per area:
`workspace.areas.<area>.start` and `.stop` (the `"*"` area's when an area has none). A workspace whose services start
some other way replaces "Start the services" in an extension; the lock steps around it stay.

## Arguments

```
/start-local-services [area | checkout-path] [--task <work id>] [--ports <p1,p2>] [--health-url <url>]...
```

- **area** or **checkout-path**: an area of the config, or the path of a task's worktree. If omitted, infer it from the
  current directory or the conversation.
- **--task**: the work id the run belongs to, recorded in the lock. A task's session always passes it.
- **--ports**: every port the services listen on, app and management. If omitted, take them from the project's own
  docs or compose file. If nothing names them, start without: the run then waits for the command to return or stay up
  a moment, which proves little, so say so in the report.
- **--health-url**: a URL that must answer before the services count as up.

## Workflow

### Locate the checkout

- **Checkout**: a worktree path when one is given; otherwise the workspace root, or the current directory when it is
  itself the target checkout. If it can't be determined, ask.
- **Areas to start**: in a `nested` workspace, the one repo the checkout is (its folder name, which a worktree keeps).
  In a `single` workspace, the named area, or else every area with a `start` command (`toolbox.py config show
  workspace.areas`). An area's directory is its folder under the checkout.
- **Lock name**: as in `lock-local-services` ("Lock name").

If no area has a `start` command, report that `workspace.areas.<area>.start` isn't configured and stop.

### Preflight

Nothing by default. A workspace adds its checks here (a container runtime that must be up, a dependency that must not be
a snapshot) in an extension. A failed check stops the run before the lock is taken.

### Take the lock

`.claude/toolbox/bin/services_lock.py acquire [name] --task <work id> --worktree <checkout> --ports <ports>`. Keep the
`token`. If it is held, wait exactly as `lock-local-services` describes ("Wait for the lock"); start nothing until you
hold it. Once you hold it, the clock is running: don't stop for the user until `stop-local-services` has released it.

### Prepare the checkout

Nothing by default. A workspace adds what must happen inside the lock before the services start (re-installing the
checkout's own modules so the services run its code, say). If it fails, release
(`services_lock.py release [name] --token <token>`), report the error and stop.

### Start the services

For each area to start, from the area's directory:

```bash
.claude/toolbox/bin/services_lock.py start [name] --token <token> --area <area> --checkout <area directory> \
  --ports <ports> [--health-url <url>]... --wait-seconds 90
```

It runs the area's `start` command detached (its output in `$TMPDIR/<name>-<area>-start.log`), records the PID, the
ports and the area's `stop` command in the lock, and waits for every port to accept connections and every URL to answer.

| Exit | `result` | Do |
|---|---|---|
| 0 | `started` | Next area, then "Report" |
| 6 | `failed` or `timeout` | Show `log_tail`, then `stop-local-services` (it stops what was started and releases), and report the failure |
| 5 | `not-owner` | Your lock was taken over. Start nothing more; report it to the user |

A port that is already in use is something outside the lock (usually the user, running it from their IDE): release the
lock, report the port and the PID that holds it (`lsof -nP -iTCP:<port> -sTCP:LISTEN`), and stop. Never kill it.

### Wait for health

`start` has waited for the ports and URLs it was given. If the services need more (a seeder, a migration), check it here
and, on failure, show the last 20 lines of the log, then `stop-local-services`.

### Report

```
Local services started
======================

Checkout: <checkout>
Lock:     <name>, token <token>, expires <expires>

Started:
  <area> (PID <pid>, ports <ports>) - UP, log <path>

To stop these services and release the lock:
  /stop-local-services [name] --token <token>
```
