# Claude Toolbox

Allen's personal Claude Code configuration: the global rules, skills and style guides that apply in every session.
The work domain lives separately in `fs-eng/asudweeks-claude-toolbox`. Single-repo personal projects (`meal-planner`,
`story-forge`) keep their Claude config in their own repos.

## Layout

Each domain folder mirrors the folder it installs into. The installer creates folders, links files one at a time, and
links skill folders (anything containing `SKILL.md`) whole, so edits here take effect immediately.

```
global/                          → ~/.claude
  CLAUDE.md                      response style
  rules/git.md                   Git writes need Allen's explicit instruction; no commit trailers; commit voice
  rules/obsidian.md              the Personal vault and Obsidian formatting
  personal/voice.md              Allen's writing voice (read where referenced)
  personal/ticket-style.md       Allen's Jira ticket style (read where referenced)
  personal/commit-style.md       Allen's commit message style (read where referenced)
  skills/install-domain/         install a domain on this machine, or re-sync every toolbox
  skills/remove-domain/          uninstall a domain, or tear down a toolbox
  skills/add-domain/             create a new domain in a toolbox
```

## Naming

Every rule, skill and agent in a domain carries the domain's prefix (see `prefix` in `domains.yml`); global items have
none. Skill names follow `<prefix>-<system>-<verb>-<object>`, with the system included mainly when the skill works
against another API or tool (`dt` Dynatrace, `gh` GitHub, `mvn` Maven). The installer warns about names that break
these rules.

## Setting Up a Machine

1. Install Python 3 and the installer's one dependency:

   ```bash
   python3 -m pip install -r requirements.txt
   ```

2. Copy `machine.example.yml` to `machine.yml` (untracked) and list the domains to install on this machine.
3. Preview, then apply:

   ```bash
   python3 install.py
   python3 install.py --apply
   ```

On Windows, turn on Developer Mode first so Python can create symlinks without admin rights.

## Installer

| Command | Effect |
|---|---|
| `install.py` | Preview: writes the full plan to `install-plan.txt` and changes nothing |
| `install.py --apply` | Creates missing links, removes stale ones, updates git excludes |
| `install.py --domain <name>` | Acts on one domain only, ignoring `machine.yml` (repeatable) |
| `install.py --teardown` | Removes this repo's links (add `--apply`) |

- **Running it syncs.** A renamed or deleted file's old link is removed on the next run, and a domain dropped from
  `machine.yml` is uninstalled.
- **It never touches what isn't its own.** A real file, or a link pointing somewhere else, is reported as a conflict and
  left alone.
- **Git targets.** When a target folder is a git repo, the links go in that repo's `.git/info/exclude`, so they never
  show up as untracked files.
- **State.** `.install-state.json` (untracked) records what this machine installed, for stale-link cleanup and
  teardown.

`install.py` is kept identical in `claude-toolbox` and `asudweeks-claude-toolbox`. A change to one goes in the other
too.
