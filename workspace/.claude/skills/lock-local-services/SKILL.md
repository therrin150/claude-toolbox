---
name: lock-local-services
description: Take the workspace's local-services lock so no other session runs its services meanwhile. The user runs it by hand (`--manual`) before running services from the IDE; `start-local-services` takes it itself.
disable-model-invocation: true
---

# Lock Local Services

**Extensions.** Before anything else, run `.claude/toolbox/bin/toolbox.py config extensions lock-local-services` from
the workspace root (its absolute path is in the toolbox-config rule) and read every doc it lists. Each `##` section of a
doc is a verb and one of this skill's hook ids ("Extension Points" below): at that hook, follow an `add` section after
the hook's own text and a `replace` section instead of it; for a `skip` section, don't run the step, and list it
wherever this skill reports its steps as `⏭️ <step>: skipped by <doc>: <reason>`. An added step that fails stops the
skill for the user, unless its doc says otherwise, and what a step reports goes in this skill's next report. If two docs
replace or skip the same hook, stop and ask the user which applies.

One lock per name, shared by Claude and the user: two sessions on the same name wait for each other, and different
names run in parallel. All lock logic lives in one script; never create, edit or delete lock files by hand:

```bash
.claude/toolbox/bin/services_lock.py <command> ...
```

Every command prints JSON. Keep the `token` from a successful acquire: starting, updating, stopping and releasing all
need it. Lock times are the config's `services.agent_lock_minutes` and `services.manual_lock_minutes`.

## Arguments

```
/lock-local-services [name] [--task <work id>] [--worktree <path>] [--ports <p1,p2>] [--manual]
```

- **name**: see "Lock name".
- **--manual**: when the user runs it by hand. A manual lock lasts longer, and is the one a waiting agent takes over
  only once it expires.

## Workflow

### Lock name

Hook `lock-name`. The lock is named after what shares ports. In a `single` workspace, leave the name out: it is the
workspace's name, one lock for the whole stack. In a `nested` workspace, pass the repo's folder name (also for a
worktree of it), so different repos run in parallel.

### Acquire

Hook `acquire`. `services_lock.py acquire [name] --task <work id> --worktree <path> --ports <ports>`, plus `--manual`
when the user runs it by hand. Pass every app and management port the services use: a takeover stops what the entry
lists, and `stop` frees those ports.

| Exit | `result` | Meaning | Do |
|---|---|---|---|
| 0 | `acquired` | The lock is yours | Carry on; keep the token |
| 0 | `taken-over` | The holder's lock had expired, of either kind; its processes were stopped | Carry on, and tell the user: `previous` has the holder's task, worktree and kind, `stopped` what was ended |
| 3 | `held` | Another session or the user holds it | Wait ("Wait for the lock") |

### Wait for the lock

Hook `wait-for-the-lock`. Don't poll in the conversation. Run `services_lock.py wait [name] --max-seconds 540` in the
foreground with a Bash timeout of 600000. Exit 3 means still held: run it again. After 45 minutes of waiting in total,
stop waiting and tell the user whose lock it is (`owner`: task, worktree, expiry).

- Exit 0, `free` or `expired`: acquire again. An expired lock is taken over by that acquire; say so in your report.

A session with nothing else to do may instead run the wait in the background (`run_in_background`) and end its turn;
the session wakes when it exits.

### Rules

- **The lock is not renewed.** One bracket (start services, tests, stop services) must fit inside it. Fix code between
  brackets, never while holding the lock.
- **Never stop for the user while holding the lock.** If you need them mid-run, stop the services and release first
  (`stop-local-services`).
- **Never take over or break a lock any other way.** The script's takeover is the only one, and only for an expired
  lock.
- `services_lock.py status [name]` lists the workspace's locks, `services_lock.py mine --task <work id>` a task's.

## Extension Points

Hooks for extension docs (`.claude/extensions/README.md`). All are `add` only: the script's lock logic is never
replaced.

| Id | Runs | Default | Verbs |
|---|---|---|---|
| `lock-name` | "Lock name" | the workspace's name (`single`) or the repo's (`nested`) | add |
| `acquire` | "Acquire", before `services_lock.py acquire` | the acquire alone | add |
| `wait-for-the-lock` | "Wait for the lock", while another session holds it | the bounded `wait` loop | add |
