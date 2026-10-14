---
name: install-domain
description: Install a Claude toolbox domain on this machine (add it to machine.yml and sync), or with no argument re-sync every toolbox after pulling changes. Previews first and applies only after Allen approves. Use when Allen says "install the <domain> domain", "sync my toolbox", "re-link my skills", or after adding, renaming or deleting rules, skills or agents in a toolbox repo.
---

# Install Domain

Wraps each toolbox repo's `install.py`. The installer previews by default and changes nothing until `--apply`.

## Arguments

```
/install-domain [domain]
```

- **domain**: a domain name from a toolbox's `domains.yml` (e.g. `global`, `rights-management`). Omit it to re-sync
  every toolbox against its existing `machine.yml`.

## Workflow

### 1. Find the toolboxes

A toolbox is any folder at `~/*/*/` containing both `domains.yml` and `install.py` (today
`~/_personal/claude-toolbox` and, on the work Mac, `~/_workspace/asudweeks-claude-toolbox`). Use `python3` to run the
installer, or `py -3` on Windows. If `import yaml` fails, show `python3 -m pip install -r requirements.txt` and stop.

### 2. With a domain

1. Find the toolbox whose `domains.yml` lists the domain. If none does, list the domains that exist and stop.
2. If that toolbox has no `machine.yml`, create it from `machine.example.yml` with only this domain listed.
   Otherwise add the domain to its `domains:` list if it's missing. Keep the original text to restore on decline.
3. Run `install.py` (preview) from the toolbox folder.

### 3. Without a domain

Run `install.py` (preview) in every toolbox that has a `machine.yml`. Report any toolbox without one; don't create it.

### 4. Present the preview

Per toolbox, show the counts, then every conflict, warning and problem verbatim, plus the full plan's path
(`install-plan.txt`). Don't list the individual links unless asked.

- **Conflicts** are left alone by the installer. Explain each: a real file in the way usually means content not yet
  moved into the toolbox; a link pointing elsewhere is usually an old link from before the toolbox. Never delete or move
  either without Allen saying so.
- **Warnings** flag names that break the naming rules (prefix, `name:` matching the folder). Mention them; they don't
  block applying.

Ask Allen to approve applying. On decline, restore `machine.yml` if step 2 changed it.

### 5. Apply and report

Run `install.py --apply` in the same toolboxes. Report the counts, any errors, and the record's path. Sessions
already open don't see new rules or skills; a new session at the domain's target folder does.
