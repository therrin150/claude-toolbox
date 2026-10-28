---
name: sync-toolbox
description: Sync the Claude toolboxes on this machine - re-sync everything after pulling or editing a toolbox, add or remove a toolbox repo or a workspace in machine.yml and sync, or tear everything down. Previews first and applies only after Allen approves. Use when Allen says "sync my toolbox", "re-link my skills", "install the <name> toolbox", "remove the <name> toolbox", "stop installing into <workspace>", "tear down my toolbox", or after adding, renaming or deleting rules, skills or agents in a toolbox repo.
---

# Sync Toolbox

Wraps `install.py` in `claude-toolbox`, the one installer. It always installs `claude-toolbox`'s own two folders,
`global/` into `~/.claude` and `workspace/` into every workspace its `machine.yml` lists under `workspaces:`, and
installs every other toolbox repo listed under `toolboxes:` whole: each holds a `toolbox.yml` naming its one `source`
folder and the `target` folder it mirrors. It previews by default and changes nothing until `--apply`. Removing a
toolbox or workspace from `machine.yml` and syncing removes what the installer put there: its links, generated files,
the folders it created for them, and its git-exclude entries. Real files and links the installer didn't make are never
touched, and neither is a workspace's `.claude/toolbox.local.yml`.

## Arguments

```
/sync-toolbox [--add-toolbox <path> | --remove-toolbox <path> | --remove-workspace <path> | --teardown]
```

- **None**: re-sync everything against the existing `machine.yml`.
- **--add-toolbox <path>**: start installing another toolbox repo.
- **--remove-toolbox <path>**: stop installing one.
- **--remove-workspace <path>**: stop installing into a workspace. A workspace joins through `/setup-workspace`, which
  writes its `.claude/toolbox.local.yml` and adds it to `workspaces:`.
- **--teardown**: remove everything the installer installed.

## Workflow

### 1. Find the installer

The installer is the `claude-toolbox` folder this skill is linked from: resolve `~/.claude/skills/sync-toolbox` and go
up three levels (today `~/_personal/claude-toolbox`). Use `python3` to run it, or `py -3` on Windows. If `import yaml`
fails, show `python3 -m pip install -r requirements.txt` and stop.

If there's no `machine.yml`, create it from `machine.example.yml`. Keep `machine.yml`'s original text to restore on
decline.

### 2. Change `machine.yml` (only with an argument)

Edit the text, keeping its comments.

- **Add a toolbox**: the folder must hold a `toolbox.yml` with `source` and `target` (the header of `claude-toolbox`'s
  `toolbox.yml` lists every key). If it has a `domains.yml` instead, say it must be renamed and reshaped first and
  stop. Add the path, written with `~`, under `toolboxes:`.
- **Remove a toolbox or a workspace**: delete its line. If it isn't listed, still run the preview: links from an
  earlier install may remain.

To act on one toolbox or workspace without editing `machine.yml`, `install.py --toolbox <name or path>` and
`--workspace <path>` narrow a run (with `--teardown`, they remove only that one), but the next full sync puts back
whatever `machine.yml` still lists.

### 3. Preview

Run `install.py` from the installer's folder, or `install.py --teardown` for a teardown. A teardown leaves
`machine.yml` as it is, so a sync with no argument reinstalls everything later.

### 4. Present the preview

Show the counts, then every generated-rule change, conflict, warning and problem verbatim, plus the full plan's path
(`install-plan.txt`). Don't list the individual links unless asked.

- **Conflicts** are left alone by the installer. Explain each: a real file in the way usually means content not yet
  moved into the toolbox; a link pointing elsewhere is usually an old link from before the toolbox. Never delete or move
  either without Allen saying so.
- **Problems** include a workspace with no `.claude/toolbox.local.yml` or an invalid one (nothing there is installed or
  removed until `/setup-workspace` runs there or the config is fixed), a toolbox whose manifest is missing or still
  `domains.yml` (nothing of it is installed or removed until it's renamed), and a `machine.yml` that still has
  `domains:` (remove the key: it is no longer read).
- **Warnings** flag names that break the naming rules (a toolbox's `prefix`, `name:` matching the folder), features a
  workspace's config names that no `features.yml` mentions, and `features.yml` paths that don't exist. Mention them;
  they don't block applying.

Ask Allen to approve applying. On decline, restore `machine.yml` if step 2 changed it.

### 5. Apply and report

Run the same command with `--apply`. Report the counts, any errors, and the record's path. Sessions already open don't
see new rules or skills; a new session at the toolbox's target folder or the workspace does.
