---
name: unlock-local-services
description: Release the workspace's local-services lock, an agent's by its token or the user's manual lock by hand.
disable-model-invocation: true
---

# Unlock Local Services

**Extensions.** Before anything else, run `.claude/toolbox/bin/toolbox.py config extensions unlock-local-services` from
the workspace root (its absolute path is in the toolbox-config rule) and read every file it lists. Each `##` section of
an extension names a heading of this skill: follow a `Replaces` section instead of that heading's text, and an `Adds to`
section after it. If two extensions replace the same heading, stop and ask the user which applies.

Releases the lock taken by `lock-local-services`. It doesn't stop services: `stop-local-services` stops them and then
releases in one step, which is what agents normally use.

## Arguments

```
/unlock-local-services [name] [--token <token>]
```

**name** is as in `lock-local-services` ("Lock name").

## Workflow

### Agent release

`.claude/toolbox/bin/services_lock.py release [name] --token <token>`.

- `released`: done.
- `not-held`: nothing to release. Report it; it usually means the lock expired and was taken over and released.
- `not-owner` (exit 5): another session took the lock over after yours expired. Release nothing, stop nothing, and
  report it to the user, including the current `owner`.

### Manual release

When the user runs this by hand for their own manual lock, look it up first (`services_lock.py status [name]`). If it's
manual, release it with `release [name] --force`. If it's an agent's lock, show them the owner (task, worktree, expiry)
and use `--force` only if they confirm.

Never use `--force` on your own initiative.
