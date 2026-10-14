---
name: remove-domain
description: Uninstall a Claude toolbox domain from this machine (remove it from machine.yml and sync), or tear down a whole toolbox. Previews first and applies only after Allen approves. Use when Allen says "remove the <domain> domain", "uninstall <domain>", or "tear down my toolbox".
---

# Remove Domain

Wraps each toolbox repo's `install.py`. Removing a domain from `machine.yml` and re-running the installer removes that
domain's links, the folders the installer created for them, and its git-exclude entries. Real files and links the
installer didn't create are never touched.

## Arguments

```
/remove-domain <domain | --all <toolbox>>
```

- **domain**: the domain to uninstall from this machine.
- **--all <toolbox>**: tear down every domain a toolbox installed (e.g. `--all claude-toolbox`).

## Workflow

1. **Find the toolboxes**: folders at `~/*/*/` containing both `domains.yml` and `install.py`. Use `python3`, or
   `py -3` on Windows.
2. **One domain**: find the toolbox whose `domains.yml` lists it, remove it from that toolbox's `machine.yml` (keep the
   original text to restore on decline), then run `install.py` (preview). If it isn't in `machine.yml`, still run the
   preview: links from an earlier install may remain.
3. **Whole toolbox**: run `install.py --teardown` (preview) in that toolbox. `machine.yml` stays as it is, so
   `/install-domain` with no argument reinstalls everything later.
4. **Present the preview**: the counts, every conflict, warning and problem verbatim, and the full plan's path
   (`install-plan.txt`). Ask Allen to approve. On decline, restore `machine.yml`.
5. **Apply** with the same command plus `--apply`, and report the counts, any errors, and the record's path.
