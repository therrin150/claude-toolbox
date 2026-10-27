---
name: install-domain
description: Install a Claude toolbox domain on this machine (add it to machine.yml and sync), or with no argument re-sync every toolbox and workspace after pulling changes. Previews first and applies only after Allen approves. Use when Allen says "install the <domain> domain", "sync my toolbox", "re-link my skills", or after adding, renaming or deleting rules, skills or agents in a toolbox repo.
---

# Install Domain

Wraps `install.py` in `claude-toolbox`, the one installer: it installs this repo's domains, the domains of every other
toolbox repo its `machine.yml` lists under `toolboxes:`, and the per-workspace `workspace` domain into every workspace
listed under `workspaces:`. It previews by default and changes nothing until `--apply`.

## Arguments

```
/install-domain [domain]
```

- **domain**: a domain name from any toolbox's `domains.yml` (e.g. `global`). Omit it to re-sync everything against
  the existing `machine.yml`.

## Workflow

### 1. Find the installer

The installer is the `claude-toolbox` folder this skill is linked from: resolve `~/.claude/skills/install-domain` and
go up three levels (today `~/_personal/claude-toolbox`). Other toolboxes are the folders its `machine.yml` lists under
`toolboxes:`; they hold only a `domains.yml` and their domain folders. Use `python3` to run the installer, or `py -3` on
Windows. If `import yaml` fails, show `python3 -m pip install -r requirements.txt` and stop.

If there's no `machine.yml`, create it from `machine.example.yml` (with the domain's entry, when one is given).

### 2. With a domain

Keep `machine.yml`'s original text to restore on decline.

1. Find the toolbox whose `domains.yml` defines the domain: `claude-toolbox` or a listed toolbox. If none does, look for
   other folders at `~/*/*/` with a `domains.yml`; if one defines it, add that folder to `toolboxes:`. If still none,
   list the domains that exist and stop.
2. A per-workspace domain (`target: per-workspace`) needs no `domains:` entry: it installs into every listed workspace.
   A workspace joins through `/setup-workspace`, which writes its `.claude/toolbox.local.yml` and adds it to
   `workspaces:`. For any other domain, add it to `domains:` if it's missing.
3. Run `install.py` (preview) from the installer's folder.

### 3. Without a domain

Run `install.py` (preview). It covers every listed toolbox and workspace.

### 4. Present the preview

Show the counts, then every generated-rule change, conflict, warning and problem verbatim, plus the full plan's path
(`install-plan.txt`). Don't list the individual links unless asked.

- **Conflicts** are left alone by the installer. Explain each: a real file in the way usually means content not yet
  moved into the toolbox; a link pointing elsewhere is usually an old link from before the toolbox. Never delete or move
  either without Allen saying so.
- **Problems** include a workspace with no `.claude/toolbox.local.yml` or an invalid one: nothing there is installed or
  removed until `/setup-workspace` runs there or the config is fixed.
- **Warnings** flag names that break the naming rules (prefix, `name:` matching the folder), features a workspace's
  config names that no `features.yml` mentions, and `features.yml` paths that don't exist. Mention them; they don't
  block applying.

Ask Allen to approve applying. On decline, restore `machine.yml` if step 1 or 2 changed it.

### 5. Apply and report

Run `install.py --apply`. Report the counts, any errors, and the record's path. Sessions already open don't see new
rules or skills; a new session at the domain's target folder or workspace does.
