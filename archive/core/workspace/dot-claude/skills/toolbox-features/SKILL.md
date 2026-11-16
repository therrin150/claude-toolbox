---
name: toolbox-features
description: List the toolbox's features (scope, where each is installed, whether it has drifted from its source), and enable or disable features globally (machine.yml) or in this workspace (its toolbox.local.yml), previewing the sync first and warning what a disable could pull out from under a running session. Use for "which features are installed", "enable the tests feature", "turn off X".
allowed-tools: Bash(*), Read
---

# Toolbox Features

Wraps `toolbox.py features`. It edits the config (`machine.yml` for a `global` feature, this workspace's
`.claude/toolbox.local.yml` for a `workspace` one, keeping comments) and syncs through the installer. Never edit those
files by hand for this.

From the workspace root (its absolute path is in the toolbox-config rule):

## List

```bash
.claude/toolbox/bin/toolbox.py features
```

One line per feature and place it installs into (`~/.claude` or a workspace), with its state: `in-sync`; `drifted`
(`edited`: changed on disk, the sync leaves it alone and reports a conflict; `outdated`: the source changed, a sync
updates it; `missing`); `pending-install` and `pending-removal` (a sync does it); `off`. Show it as a table, or only the
rows the user asked about. Don't fix drift unless asked: `/sync-toolbox` does.

## Enable or Disable

1. **Preview** (writes nothing). Features are space-separated, and the scope is `global` or `workspace`:

   ```bash
   .claude/toolbox/bin/toolbox.py features enable <feature>... --scope <global|workspace>
   .claude/toolbox/bin/toolbox.py features disable <feature>... --scope <global|workspace>
   ```

   It prints JSON: `features` it will change, `already` (nothing to do), the sync's `preview` (Create, Update, Remove,
   Conflicts, Problems; Unchanged is a count) and, for a disable, `concurrent`. A feature that doesn't exist, is of the
   other scope, requires one that isn't picked (enable) or is required by a picked one (disable), or needs config the
   workspace lacks, is an error with exit 2: report it, and offer the fix it names.
2. **Show the user** the files to create, update and remove, and every Conflict and Problem verbatim. For a disable,
   show `concurrent` too and say plainly that removing files can break a session using them: each task `in-progress`,
   each task worktree, each held services lock and each other session started in this workspace in the last hour
   (`minutes_ago`). Say when a list is empty. `tasks_not_checked` names task records or a vault the script couldn't
   read: say the task list may be incomplete.
3. **Wait for the user's go-ahead in chat.** For a disable the go-ahead is the acknowledgment of that warning.
4. **Apply**, the same command plus `--apply`; for a disable also `--confirm-concurrent`, which you pass only after
   step 3, never on the first call and never on your own judgment (the script refuses a disable without it, exit 3,
   and writes nothing):

   ```bash
   .claude/toolbox/bin/toolbox.py features disable <feature>... --scope <scope> --apply --confirm-concurrent
   ```

   It writes the config, then syncs just that scope. Exit 4: the config is written but the sync reported a conflict, a
   problem or an error (`installer.output`); report it, don't undo the config. Sessions already open don't see new or
   removed skills and rules.

Enabling a workspace feature that needs config (`tasks` needs `tracker` and `vault`) is refused until the config has it:
`/setup-workspace` adds it.
