# The By-Hand Bracket

For an `integration` check in a workspace whose extensions change how services start (`procedure.md`, "Integration").
`run_checks.py integration` can't apply them; you do, around the area's integration tests. One bracket: take the lock,
start the services, run the tests, stop the services, release the lock. It's also the procedure the user's
`/start-local-services` and `/stop-local-services` follow.

Paths: the workspace root is `<bin>/../../..`. Where this document or `start-local-services/SKILL.md` writes a path
starting `.claude/`, read it under that root, and run `<bin>/services_lock.py` and `<bin>/toolbox.py` by their absolute
paths: your shell may be in a worktree, and you may not have the toolbox-config rule that names the root.

All lock logic lives in one script; never create, edit or delete lock files by hand. Every command prints JSON, and a
successful `acquire` gives a `token` that `start`, `update`, `stop` and `release` all need.

```bash
<bin>/services_lock.py <command> [name] ...
```

Lock times are the config's `services.agent_lock_minutes` and `services.manual_lock_minutes`.

## 1. The Lock

**Name.** The lock is named after what shares ports. In a `single` workspace leave the name out (the workspace's name,
one lock for the whole stack); in a `nested` one pass the repo's folder name, also for a worktree of it, so different
repos run in parallel.

**Acquire.** `services_lock.py acquire [name] --task <work id> --worktree <checkout> --ports <ports>`. Pass every app
and management port the services use: a takeover stops what the entry lists, and `stop` frees those ports.

| Exit | `result` | Meaning | Do |
|---|---|---|---|
| 0 | `acquired` | The lock is yours | Carry on; keep the token |
| 0 | `taken-over` | The holder's lock had expired, of either kind; its processes were stopped | Carry on; put `previous_kind` and `previous` in `notes` |
| 3 | `held` | Another session or the user holds it | Wait |

**Wait.** Run `services_lock.py wait [name] --max-seconds 540` in the foreground with a Bash timeout of 600000, and
again while it exits 3 (still held). Never in the background. After 45 minutes of waiting in total, return `blocked`
with the holder (`owner`: task, worktree, expiry). On exit 0 (`free` or `expired`), acquire again; an expired lock is
taken over by that acquire.

**Rules.**

- The lock is not renewed. One bracket must fit inside it. Fix code between brackets, never while holding it.
- Never stop for the user while holding the lock. If you need them, stop the services and release first, and return
  `blocked`.
- Never take over or break a lock any other way. `services_lock.py status [name]` lists the locks, `mine --task <work
  id>` a task's.

## 2. Start the Services

Read `<root>/.claude/skills/start-local-services/SKILL.md` and follow it from "Locate the checkout" to "Report", with
`--task <work id>`; its extensions (`toolbox.py config extensions start-local-services --apply`) apply to you. Its lock
steps are this document's section 1. Where it says to ask the user, report to them, or stop, stop the bracket cleanly
(section 4) and return `blocked` with the reason: you can't reach the user, your caller can.

- **A start that fails for a reason outside the code** (an app that times out on health with no error, a port that frees
  up a moment later): stop everything, release, and retry the whole start once. Name the retry in `notes`.
- **Some services up, some not:** never run tests against a partial start. Stop everything and release. If the failed
  service's `log_tail` shows a code or config error (exception, missing property), return `fail` with its cause;
  otherwise retry once, then `blocked`.
- **A service that exits within seconds with no error:** re-check the start command and environment it was given, retry
  once; if it fails the same way, return `blocked`.
- **A port held by something outside your lock** (usually the user, in their IDE): release with
  `services_lock.py release [name] --token <token>` (never `stop`, which ends what the lock lists), clean up only the
  PIDs your own start launched, and return `blocked` with the port and its PID.

## 3. Run the Integration Tests

Check the bracket first: `services_lock.py status [name]` must show your token as `owner.token`, at least 5 minutes
before `owner.expires`, and every port the lock lists must accept connections (`nc -z localhost <port>`). If not,
section 4 and `blocked`.

```bash
<bin>/run_tests.py integration <area=dir>... [--test <test>] [--label <work id>]
```

Foreground, with a Bash timeout of the time left on the lock. Its lines are the result; don't read its log. `pass` is
the only pass; `skipped` (no `integration` command) is never one. Leave the services running until section 4.

## 4. Stop and Release

Always, for any outcome in sections 2 and 3, before you return:

```bash
<bin>/services_lock.py stop [name] --token <token>
```

It checks the token, runs the stop commands the lock recorded, ends the PIDs it lists and what listens on its ports
(SIGTERM, then SIGKILL after 15 seconds), and releases the lock. It touches nothing the lock doesn't list.

- `stopped`: report what ended. A non-empty `still_listening` goes in `notes` as needing attention.
- `not-owner` (exit 5): your lock expired and another session took it over; nothing was stopped and what runs on those
  ports is now theirs. Report it, with the current `owner`.

Then `services_lock.py status [name]`: if it still carries your token, report `lock: NOT RELEASED`.

## By Hand, for the User

The user may take a lock before running services from their IDE: `services_lock.py acquire [name] --manual --ports
<ports>`. A manual lock lasts longer, and a waiting agent takes it over only once it expires. They release it with
`services_lock.py release [name] --force`, after looking at it with `status`; never use `--force` on your own
initiative, and never on an agent's lock without the user's confirmation. To end services by PID or port at the user's
request, send SIGTERM to each given PID or port's listener (`lsof -ti tcp:<port> -sTCP:LISTEN`), wait up to 15 seconds,
then SIGKILL what remains, after checking `services_lock.py status`: if a lock lists that port, show the user its owner
and kill only if they confirm. Killing never releases a lock.
