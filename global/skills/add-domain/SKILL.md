---
name: add-domain
description: Create a new domain in a Claude toolbox repo — the domains.yml entry, its source folder, and the README layout — then hand off to /install-domain. Use when Allen says "add a domain for <project>", "set up a new domain", or starts a new team or role whose workspace spans many repos and needs shared rules, skills and agents.
---

# Add Domain

A domain holds what no single repo owns: global assets, or the shared assets of a workspace that spans many repos
(like `~/_workspace`). A single-repo project keeps its Claude config in its own repo instead.

Creates the domain; it doesn't install it. `/install-domain <name>` does that afterwards. Never commits.

## Arguments

```
/add-domain [name] [prefix] [target]
```

Ask for anything missing:

- **name**: kebab-case, e.g. `rights-management`.
- **prefix**: two or three lowercase letters plus a hyphen, e.g. `rm-`. Every rule, skill and agent in the domain
  carries it.
- **target**: the folder the domain mirrors, where Allen launches its sessions, e.g. `~/_workspace`.

## Workflow

### 1. Choose the toolbox

Toolboxes are folders at `~/*/*/` containing both `domains.yml` and `install.py`. Work domains go in the work toolbox
(today `~/_workspace/asudweeks-claude-toolbox`, which stays with the job); everything else goes in
`~/_personal/claude-toolbox`. Ask if it isn't obvious.

### 2. Check before creating

Stop and explain if any of these fail:

- The name isn't already a domain in any toolbox, and the prefix isn't used by any domain in any toolbox.
- No other domain in any toolbox already targets the same folder.
- The target folder exists. If it doesn't, ask whether to create it.

Then check whether the target is a git repo that already tracks its own Claude config (`git ls-files` for
`CLAUDE.md`, `CLAUDE.local.md` or anything under `.claude/`). If it does, tell Allen before going on. That project
already versions its config with its own content, so it almost certainly doesn't need a domain. If he still wants one,
the domain must not ship a file the project tracks (a domain `CLAUDE.md` would conflict with the project's), and
`CLAUDE.local.md` is the usual place for instructions that are Allen's rather than the project's.

### 3. Create

- **Source folder**: `<name>/` at the toolbox root, next to the existing domains. Create it with only a `.gitkeep`;
  content comes later.
- **`domains.yml`**: append the entry by editing the text (keep the comments), matching the existing entries:

  ```yaml
    <name>:
      source: <source folder>
      target: <target, written with ~>
      prefix: <prefix>
  ```

- **`README.md`**: add the domain to the Layout block.

### 4. Hand off

Run the installer's preview from the toolbox to confirm the new entry parses (`python3 install.py`, or `py -3` on
Windows). Report what was created, and that `/install-domain <name>` installs it on this machine. Remind Allen that the
toolbox changes are uncommitted.
