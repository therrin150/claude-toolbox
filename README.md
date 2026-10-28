# Claude Toolbox

Allen's personal Claude Code configuration: the global rules, skills and style guides that apply in every session,
and the generic workflow tools installed into each workspace, configured per workspace by its untracked
`.claude/toolbox.local.yml`. `install.py` is the one installer: it also installs the domains of every other toolbox
repo this machine lists (such as the work toolbox). A project's own conventions stay in its own repo.

## Layout

Each domain folder mirrors the folder it installs into. The installer creates folders, links files one at a time, and
links skill folders (anything containing `SKILL.md`) whole, so edits here take effect immediately. The `workspace`
domain mirrors a workspace root and installs into every workspace `machine.yml` lists.

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
workspace/                       → every listed workspace (per-workspace)
  features.yml                   which paths need which config feature (not installed)
  .claude/toolbox/               scripts (bin/), their library (lib/), references and templates
  .vault/Templates/              Templater templates written into each workspace's vault (feature obsidian)
  .claude/skills/, agents/       generic workflow skills and agents
install.py                       the installer
tests/                           the installer's tests: python3 -m unittest discover -s tests
```

## Naming

Every rule, skill and agent in a domain carries the domain's prefix (see `prefix` in `domains.yml`); global and
workspace items have none. Skill names follow `<prefix>-<system>-<verb>-<object>`, with the system included mainly when
the skill works against another API or tool (`dt` Dynatrace, `gh` GitHub, `mvn` Maven). The installer warns about
names that break these rules.

## Setting Up a Machine

1. Install Python 3 and the installer's one dependency:

   ```bash
   python3 -m pip install -r requirements.txt
   ```

2. Copy `machine.example.yml` to `machine.yml` (untracked) and fill it in:
   - `domains`: the domains to install here, from this repo or any listed toolbox.
   - `toolboxes`: other toolbox repos (each holding a `domains.yml` and its domain folders) whose domains this installer
     installs too.
   - `workspaces`: where the `workspace` domain goes. Each needs a `.claude/toolbox.local.yml`; `/setup-workspace`
     writes it and adds the workspace here.
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
| `install.py --apply` | Creates missing links, writes generated rules, removes stale ones, updates git excludes |
| `install.py --domain <name>` | Acts on one domain only, ignoring `machine.yml`'s domains (repeatable) |
| `install.py --workspace <path>` | Acts on one workspace only, ignoring `machine.yml`'s workspaces (repeatable) |
| `install.py --teardown` | Removes everything this installer installed (add `--apply`); combines with the two above |

- **Running it syncs.** A renamed or deleted file's old link is removed on the next run, and a domain, toolbox or
  workspace dropped from `machine.yml` is uninstalled.
- **Other toolboxes.** Each repo under `toolboxes:` contributes its `domains.yml`, with sources resolved against that
  repo. A domain name defined twice is a problem; the first definition (this repo's, then the toolboxes' in order) wins.
  Links into any listed toolbox count as this installer's own.
- **Per-workspace domains** (`target: per-workspace`) install into every listed workspace. A workspace with no
  `.claude/toolbox.local.yml`, or an invalid one, is a problem line naming `/setup-workspace`, and nothing there is
  installed or removed. Otherwise its `features` gate what installs: the domain's `features.yml` maps each feature to
  paths, a path needs every feature that lists it, and anything unlisted always installs. Dropping a feature removes
  its links on the next sync.
- **Generated rule.** Each workspace gets `.claude/rules/toolbox-config.md`, rendered from its config on every sync. It
  is a real file, recorded as generated, so stale cleanup and teardown remove it; a file there the installer didn't
  write is a conflict.
- **Templates.** In a per-workspace domain, a file whose name ends `.tmpl` is rendered into each workspace at the same
  path without the suffix, instead of being linked: each `{{ dotted.config.key }}` becomes that key's value from the
  workspace's config. It is recorded as generated, like the rule, and `features.yml` gates it by its `.tmpl` name. A
  key the config doesn't have, or one that isn't a single value, is a problem, and the file there is left as it is.
  This is for what a link can't do, such as an agent whose `tools:` names the workspace's MCP server (`jira-filer`).
- **Vault files.** A per-workspace domain's `.vault/` folder mirrors each workspace's vault (its config's
  `vault.path`, inside the workspace or anywhere on disk) instead of the workspace root. Its files are written, never
  linked, since Obsidian and synced vaults handle links poorly: a `.tmpl` file is rendered as above, any other file is
  copied as it is. They are recorded as generated, so a changed source rewrites them, stale cleanup and teardown remove
  them, and an edit made in the vault is overwritten on the next sync. `features.yml` gates them by their `.vault/`
  paths (the `obsidian` feature gates the whole folder). A vault that doesn't exist is a problem. Inside the
  workspace's git repo they are excluded from git like the rest; a vault outside it gets no exclude.
- **It never touches what isn't its own.** A real file, or a link pointing somewhere else, is reported as a conflict and
  left alone.
- **Git targets.** When a target folder or workspace is a git repo, the links (and a workspace's config file and
  generated files) go in that repo's `.git/info/exclude`, so they never show up as untracked files.
- **State.** `.install-state.json` (untracked) records what this machine installed, for stale cleanup and teardown.
