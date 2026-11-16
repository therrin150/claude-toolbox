---
name: sync-toolbox
description: Sync the Claude toolboxes on this machine: after pulling or editing a toolbox, adding or removing a toolbox or workspace in machine.yml, or tearing it all down. Previews, then applies on approval. Use for "sync my toolbox", "refresh my skills".
---

# Sync Toolbox

Wraps `install.py` in `claude-toolbox`, the one installer. It installs the feature folders of `claude-toolbox` and of
each toolbox repo listed under `toolboxes:` that has them: `core/` always (into `~/.claude` for its global core, and
every workspace its `machine.yml` lists under `workspaces:` for its workspace core), a global feature into `~/.claude`
when `machine.yml`'s `features:` lists it, and a workspace feature into each workspace whose config lists it. A listed
toolbox with a `toolbox.yml` naming a `source` folder and the `target` folder it mirrors installs that folder whole too.
It previews by default and changes nothing until `--apply`. Removing a
toolbox or workspace from `machine.yml` and syncing removes what the installer put there: its copies, the folders it
created for them, and its git-exclude entries. Every installed file is a copy recorded in a manifest with the hash
written, so a file edited on disk is a conflict and left alone, and so is any file the installer didn't write. A
workspace's `.claude/toolbox.local.yml` is never touched.

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

The installer is the `claude-toolbox` folder named in `~/.claude/toolbox-repo`;
the installer writes that file on every sync. If it's missing (a machine not yet synced since copies), ask where the
toolbox is cloned. Use `python3` to run it, or `py -3` on Windows. If `import yaml` fails, show
`python3 -m pip install -r requirements.txt` and stop.

If there's no `machine.yml`, create it from `machine.example.yml`. Keep `machine.yml`'s original text to restore on
decline.

### 2. Change `machine.yml` (only with an argument)

Edit the text, keeping its comments.

- **Add a toolbox**: the folder must hold a `toolbox.yml` with `source` and `target` (the header of `claude-toolbox`'s
  `toolbox.yml` lists every key). If it has a `domains.yml` instead, say it must be renamed and reshaped first and
  stop. Add the path, written with `~`, under `toolboxes:`.
- **Remove a toolbox or a workspace**: delete its line. If it isn't listed, still run the preview: files from an
  earlier install may remain.

To act on one toolbox or workspace without editing `machine.yml`, `install.py --toolbox <name or path>` and
`--workspace <path>` narrow a run (with `--teardown`, they remove only that one), but the next full sync puts back
whatever `machine.yml` still lists.

### 3. Preview

Run `install.py` from the installer's folder, or `install.py --teardown` for a teardown. A teardown leaves
`machine.yml` as it is, so a sync with no argument reinstalls everything later.

### 4. Present the preview

Show the counts, then every conflict, warning and problem verbatim (and the Create, Update and Remove items when the
summary lists them), plus the full plan's path (`install-plan.txt`). Don't list the individual files unless asked.

- **Conflicts** are left alone by the installer. Explain each: `edited` is a copy changed in place, whose edit belongs
  in the toolbox (or is thrown away by deleting the file, which the next sync recreates); `unmanaged` is a file the
  installer never wrote, usually content not yet moved into the toolbox; `foreign` is a link pointing elsewhere. Never
  delete or move any of them without the user saying so.
- **Problems** include a workspace with no `.claude/toolbox.local.yml` or an invalid one (nothing there is installed or
  removed until `/setup-workspace` runs there or the config is fixed), a toolbox whose manifest is missing or still
  `domains.yml` (nothing of it is installed or removed until it's renamed), and a `machine.yml` that still has
  `domains:` (remove the key: it is no longer read).
- **Warnings** flag names that break the naming rules (a toolbox's `prefix`, `name:` matching the folder), features a
  workspace's config names that no workspace feature folder is named for. Mention them;
  they don't block applying.

Ask the user to approve applying. On decline, restore `machine.yml` if step 2 changed it.

### 5. Apply and report

Run the same command with `--apply`. Report the counts, any errors, and the record's path. Sessions already open don't
see new rules or skills; a new session at the toolbox's target folder or the workspace does.
