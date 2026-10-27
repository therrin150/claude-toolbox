---
name: stop-local-services
description: Stop the services a `start-local-services` run started and release its lock (by token), or kill processes by PID or port on the user's request.
allowed-tools: Bash(*)
---

# Stop Local Services

**Extensions.** Before anything else, run `.claude/toolbox/bin/toolbox.py config extensions stop-local-services` from
the workspace root (its absolute path is in the toolbox-config rule) and read every doc it lists. Each `##` section of a
doc is a verb and one of this skill's hook ids ("Extension Points" below): at that hook, follow an `add` section after
the hook's own text and a `replace` section instead of it; for a `skip` section, don't run the step, and list it
wherever this skill reports its steps as `⏭️ <step>: skipped by <doc>: <reason>`. An added step that fails stops the
skill for the user, unless its doc says otherwise, and what a step reports goes in this skill's next report. If two docs
replace or skip the same hook, stop and ask the user which applies.

Two modes: stopping a run by its lock, the normal case, and killing by PID or port on the user's request. Both need an
explicit target; never scan or guess.

## Arguments

```
/stop-local-services [name] --token <token>        # a run started by start-local-services
/stop-local-services --pid <pid>... | --port <port>...     # on the user's request
```

A token selects the first mode; `--pid` and `--port` the second. **name** is as in `lock-local-services` ("Lock name").

## Workflow

### Stop and release

Hook `stop-and-release`.

```bash
.claude/toolbox/bin/services_lock.py stop [name] --token <token>
```

The script checks the token, runs the stop commands the lock recorded (the areas' `stop` commands), ends the PIDs it
lists and whatever listens on its ports (SIGTERM, then SIGKILL after 15 seconds), and releases the lock. It touches
nothing the lock doesn't list, and skips a port another lock lists.

- `stopped`: report what ended. If `still_listening` isn't empty, report those ports and PIDs as needing attention. A
  `stop_commands` entry with a non-zero `exit` is worth a line too.
- `not-owner` (exit 5): your lock expired and another session took it over, so what runs on those ports is now theirs.
  Nothing was stopped. Report it to the user with the current `owner`.

Agents always use this mode for services they started.

### Stop by PID or port

Before killing by `--port`, check the locks (`services_lock.py status`). If a lock lists that port, the listener belongs
to that session's run: show the user the owner (task, worktree, expiry) and kill it only if they confirm. Killing by
port never releases a lock.

Otherwise send SIGTERM to each given PID or port's listener (`lsof -ti tcp:<port> -sTCP:LISTEN`), wait up to 15 seconds,
then SIGKILL what remains, and report what ended.

## Extension Points

Hooks for extension docs (`.claude/extensions/README.md`).

| Id | Runs | Default | Verbs |
|---|---|---|---|
| `stop-and-release` | "Stop and release": a run's stop, by its token | `services_lock.py stop` | add, replace |

A `replace stop-and-release` still releases the lock afterwards (`services_lock.py release [name] --token <token>`),
and stops only what the lock lists.
