---
name: remove-domain
description: Uninstall a Claude toolbox domain, a whole toolbox, or a workspace from this machine (remove it from machine.yml and sync), or tear down everything. Previews first and applies only after Allen approves. Use when Allen says "remove the <domain> domain", "uninstall <domain>", "stop installing into <workspace>", or "tear down my toolbox".
---

# Remove Domain

Wraps `install.py` in `claude-toolbox`, the one installer. Removing a domain, toolbox or workspace from its
`machine.yml` and re-running the installer removes the links it installed there, the generated rules, the folders it
created for them, and its git-exclude entries. Real files and links the installer didn't create are never touched, and
neither is a workspace's `.claude/toolbox.local.yml`.

## Arguments

```
/remove-domain <domain | --toolbox <path> | --workspace <path> | --all>
```

- **domain**: the domain to uninstall from this machine.
- **--toolbox <path>**: stop installing another toolbox repo's domains.
- **--workspace <path>**: stop installing the per-workspace domain into a workspace.
- **--all**: tear down everything the installer installed.

## Workflow

1. **Find the installer**: resolve `~/.claude/skills/remove-domain` and go up three levels (today
   `~/_personal/claude-toolbox`). Use `python3`, or `py -3` on Windows. Keep `machine.yml`'s original text to restore
   on decline.
2. **One domain**: remove it from `machine.yml`'s `domains:`, then run `install.py` (preview). If it isn't listed, still
   run the preview: links from an earlier install may remain. A per-workspace domain can't be dropped this way; remove
   its workspaces instead, or use `install.py --domain <name> --teardown`.
3. **A toolbox**: remove it from `toolboxes:` and its domains from `domains:` (an unlisted toolbox's domains are
   unknown, so the installer refuses a `domains:` entry for one), then run the preview.
4. **A workspace**: remove it from `workspaces:`, then run the preview. To act on it alone without editing
   `machine.yml`, run `install.py --workspace <path> --teardown`, but the next full sync reinstalls it.
5. **Everything**: run `install.py --teardown` (preview). `machine.yml` stays as it is, so `/install-domain` with no
   argument reinstalls everything later.
6. **Present the preview**: the counts, every conflict, warning and problem verbatim, and the full plan's path
   (`install-plan.txt`). Ask Allen to approve. On decline, restore `machine.yml`.
7. **Apply** with the same command plus `--apply`, and report the counts, any errors, and the record's path.
