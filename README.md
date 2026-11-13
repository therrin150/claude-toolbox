# Claude Toolbox

Allen's personal Claude Code configuration: the global rules, skills and style guides that apply in every session,
and the generic workflow tools and stack conventions installed into each workspace, picked per workspace by the
features in its untracked `.claude/toolbox.local.yml`. `install.py` is the one installer: it also installs every other
toolbox repo this machine lists (such as the work toolbox), each whole. A project's own conventions stay in its own
repo.

## Three Layers

Every session in a workspace (any folder sessions start in that the toolbox installs into) is fed by three layers:

1. **Global** (`global/` → `~/.claude`): what holds in every session on the machine. The response style, the git,
   shell and Obsidian rules, and the skills that run before a workspace has anything installed.
2. **Feature-gated workspace items** (`workspace/` → each workspace's `.claude/`): what's shared but chosen per
   project. Every skill, rule and agent here sits behind a feature in `workspace/features.yml`, and installs only into
   a workspace whose config picks it: workflow skills (`git`, `tasks`, `pr`, `verify`, ...) and knowledge skills
   (Allen's conventions for one technology, `java` to `bruno`). Only the runtime (`.claude/toolbox/`), `toolbox-doctor`,
   the report contract gates (an agent's report template declares what its report must carry and must not; the gates
   deny a report that breaks it) and the generated `toolbox-config.md` rule install everywhere.
3. **The project's own files**: its `.claude/toolbox.local.yml` (features and values, untracked), and, checked in, its
   prefixed rules (`.claude/rules/<prefix>-*.md`, only what it does differently) and extension docs
   (`.claude/extensions/<prefix>-<skill>.md`, its own steps inside a shared skill).

Overrides are additions in the project's own files, never edits to shared ones. In order of preference: a config
value; an extension at a skill's extension point; a prefixed project rule or skill that states what differs; and last,
dropping the feature and keeping a project-owned copy.

### Where a New Thing Goes

| You want… | Put it in | Loads |
|---|---|---|
| Something true in every session on the machine | `global/rules/` | session start, everywhere |
| A procedure reused across projects | `workspace/.claude/skills/<name>/`, listed under a feature | Claude judges the description relevant, or `/name` |
| A convention you'd follow in the next project on the same stack | the stack's knowledge skill in `workspace/.claude/skills/`, under its feature | Claude judges the description relevant |
| Something only this project does differently (its modules, hosts, vendors, decisions) | the project's `.claude/rules/<prefix>-*.md`, checked in | session start, or a matching file read (`paths:`) |
| A project's own step inside a shared skill | the project's `.claude/extensions/<prefix>-<skill>.md` | read by the skill at its extension point |
| A project's values (build commands, branches, tracker) | `.claude/toolbox.local.yml` | read by the scripts and the generated `toolbox-config.md` |

### Features

| Feature | Installs | Needs |
|---|---|---|
| `git` | `git-commit`, `git-draft-commit-message`, `publish-change` (the publish stage, also under `worktrees`: drafts the commit message for approval, then commits, pushes and opens the PR through `publish.py`, refusing a checkout on the default branch and warning when checks haven't run on the current diff), `git-delete-merged-branches`, the `git-conventions.md` rule, the `diff-summarizer` agent (a read-only Haiku summary of a diff, grouped by change, with its out-of-place lines) and its `diff_summary.py` | |
| `verify` | `verify-change`, the `change-verifier` agent | |
| `tests` | `run-unit-tests`, `check-change` (the check stage: gates a change's `build`, `unit`, `format` or `integration` check through `change.py check`, then runs it in a `check-runner`), the `check-runner` agent, the checks and raw output gates (deny builds, tests, `services_lock.py` and build or test logs in the main session) | `workspace.areas` |
| `worktrees` | `git-worktrees`, `check-change` (also under `tests`), `review-change` (the review stage, also under `review-ui`: gates a change through `change.py review`, then opens `local-review-ui`), `publish-change` (also under `git`), `finish-change` (the finish stage, also under `pr`: gates a published change through `change.py finish`, then merges its PR, removes its worktrees and marks the record done through `publish.py`, refusing a worktree with uncommitted or untracked files or a PR that isn't approved, green and mergeable), `start-change` (the start stage, also under `tasks`: makes or finds a change's record and gives it a branch and worktrees through `change.py start`, refusing a repo that already has one), the main checkout gate (denies `Edit` and `Write` in a repo's main checkout while a task worktree of it exists) | |
| `pr` | `gh-create-pull-request`, `gh-merge-pull-request`, `finish-change` (also under `worktrees`), the `pr-reporter` agent (a read-only Haiku report of a PR's review comments, sorted, and its failed checks' error lines) and its `pr_report.py` | |
| `tasks` | `work-task`, `start-change` (also under `worktrees`), `investigate` (a read-only production investigation, recorded in a discussion, ending in a defect when there is a fix), `propose-tickets`, `create-tickets`, `create-defect`, the `ticket-docs.md` and `session-types.md` rules (the latter says which entry skill a session starts from how it's opened) | `tracker`, `vault` |
| `jira` | the `jira-creator` agent (a template; a Haiku agent that files drafts as Jira issues, after `draft_to_jira.py` converts their markdown), the `jira-reader` agent (a template; a read-only Haiku lookup of issues or a JQL that returns only the fields asked for), the Jira write gate (denies the Jira MCP server's create, edit, comment, link, worklog and transition tools in the main session, so a write goes through `jira-creator`) | `tracker.jira` |
| `jira-sprints` | `jira-find-current-sprint` | `tracker.jira` |
| `dispatcher` | `work-tasks`, `dispatch.py` | |
| `services-lock` | `start-`, `stop-local-services` (user-invoked), `services_lock.py` | `workspace.areas` |
| `review-ui` | `local-review-ui`, `review-change` (also under `worktrees`) | |
| `intellij` | `intellij-open-worktree` | |
| `obsidian` | the vault's Templater templates (`.vault/`), the `vault-researcher` agent (a read-only Haiku search of the vault's discussions and tasks, reported per item as its wikilink, status, and the decisions and open questions that bear on a topic) | `vault`, `tracker` |
| `java` | `java-conventions`, `java-testing-conventions` (knowledge) | |
| `spring` | `spring-boot-conventions` (knowledge) | |
| `postgres` | `database-conventions` (knowledge) | |
| `angular` | `angular-conventions` (knowledge) | |
| `playwright` | `playwright-conventions` (knowledge) | |
| `bruno` | `bruno-api-collections` (knowledge) | |

The `workspace-searcher` agent (a read-only Haiku sweep of the workspace's repos that reports each location with a
one-line role, never file contents) belongs to no feature: every workspace gets it.

`/setup-workspace` suggests features from the folder (`.git`, `pom.xml`, `angular.json`, `bruno/`, `.obsidian/`, ...)
and asks only for the config the picked ones need: the `vault` and `tracker` sections are optional, and a workspace
with neither asks no vault or tracker questions.

## Layout

This repo installs two folders, both always: `global/` mirrors `~/.claude`, and `workspace/` mirrors a workspace root
and installs into every workspace `machine.yml` lists. The installer creates folders, links files one at a time, and
links skill folders (anything containing `SKILL.md`) whole, so edits here take effect immediately. The workspace
folder's rules are the exception: they are copied, and an edit takes effect on the next sync.

```
global/                          → ~/.claude
  CLAUDE.md                      response style
  rules/git.md                   Git writes need Allen's explicit instruction; no commit trailers; commit voice
  rules/obsidian.md              the Personal vault and Obsidian formatting; discussions are the discussion skill's
  rules/shell.md                 zsh pitfalls
  personal/voice.md              Allen's writing voice (read where referenced)
  personal/ticket-style.md       Allen's Jira ticket style (read where referenced)
  personal/commit-style.md       Allen's commit message style (read where referenced)
  skills/sync-toolbox/           re-sync, add or remove a toolbox or workspace, or tear down
  skills/setup-workspace/        configure a workspace and install into it
  skills/ship-toolbox-change/    ship a change to any toolbox repo
  skills/discussion/             start or extend a design discussion in a vault
workspace/                       → every listed workspace (per-workspace)
  features.yml                   which paths need which feature, and which config each feature needs (not installed)
  .claude/toolbox/               scripts (bin/), their library (lib/), references and templates
  .vault/Templates/              Templater templates written into each workspace's vault (feature obsidian)
  .claude/rules/                 workspace rules, copied into each workspace (not linked)
  .claude/skills/, agents/       generic workflow skills and agents, and the stacks' knowledge skills
install.py                       the installer; its two folders are fixed there
toolbox.yml                      this repo's manifest (how changes ship), and the reference for other toolboxes' keys
tests/                           the installer's tests: python3 -m unittest discover -s tests
```

## Naming

Every rule, skill and agent in another toolbox carries that toolbox's prefix (see `prefix` in its `toolbox.yml`); this
repo's global and workspace items have none. Skill names follow `<prefix>-<system>-<verb>-<object>`, with the system
included mainly when the skill works against another API or tool (`dt` Dynatrace, `gh` GitHub, `mvn` Maven). The
installer warns about names that break these rules.

## Setting Up a Machine

1. Install Python 3 and the installer's one dependency:

   ```bash
   python3 -m pip install -r requirements.txt
   ```

2. Copy `machine.example.yml` to `machine.yml` (untracked) and fill it in:
   - `toolboxes`: other toolbox repos to install, each holding a `toolbox.yml` and its source folder.
   - `workspaces`: where the `workspace` folder goes. Each needs a `.claude/toolbox.local.yml`; `/setup-workspace`
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
| `install.py --toolbox <name or path>` | Acts on one toolbox only (`claude-toolbox` means both its folders), ignoring the others `machine.yml` lists (repeatable) |
| `install.py --workspace <path>` | Acts on one workspace only, ignoring `machine.yml`'s workspaces (repeatable) |
| `install.py --teardown` | Removes everything this installer installed (add `--apply`); combines with the two above |

- **Running it syncs.** A renamed or deleted file's old link is removed on the next run, and a toolbox or workspace
  dropped from `machine.yml` is uninstalled.
- **Other toolboxes.** Each repo under `toolboxes:` installs whole, as its `toolbox.yml` says: one `source` folder in
  that repo mirroring one `target` folder, with an optional `prefix`, `ignore` and `external` links (this repo's
  `toolbox.yml` describes each key). It is known by its folder's name; a name taken twice is a problem, and the first
  wins. Links into any listed toolbox count as this installer's own. A toolbox still holding the retired `domains.yml`,
  or a `toolbox.yml` with `domains:` in it, is a problem, and nothing it installed is touched until it's reshaped. A
  `machine.yml` that still has `domains:` is a problem too: the key is no longer read.
- **The workspace folder** installs into every listed workspace. A workspace with no `.claude/toolbox.local.yml`, or an
  invalid one, is a problem line naming `/setup-workspace`, and nothing there is installed or removed. Otherwise its
  `features` gate what installs: `workspace/features.yml` maps each feature to paths, a path needs every feature that
  lists it, and anything unlisted always installs. Dropping a feature removes its links on the next sync. A feature
  may also list the config it `needs` (`tasks` needs `tracker` and `vault`); a workspace that picks it without that
  config is a problem, and nothing there is installed or removed until the config gains it or drops the feature.
- **Generated rule.** Each workspace gets `.claude/rules/toolbox-config.md`, rendered from its config on every sync. It
  is a real file, recorded as generated, so stale cleanup and teardown remove it; a file there the installer didn't
  write is a conflict.
- **Workspace rules.** Claude Code never loads a project rule whose link resolves outside the project, so each rule in
  `workspace/.claude/rules/` is copied into the workspace instead of linked, with a note after its title (and after
  any frontmatter, so `paths:` keeps working) that it is generated and edits are lost. Like the generated rule, it is
  recorded as generated: a changed source rewrites it, a removed one or a dropped feature removes it, and the link an
  earlier sync made there is replaced. Other toolboxes' rules are still linked.
- **Templates.** In the workspace folder, a file whose name ends `.tmpl` is rendered into each workspace at the same
  path without the suffix, instead of being linked: each `{{ dotted.config.key }}` becomes that key's value from the
  workspace's config. It is recorded as generated, like the rule, and `features.yml` gates it by its `.tmpl` name. A
  key the config doesn't have, or one that isn't a single value, is a problem, and the file there is left as it is.
  This is for what a link can't do, such as an agent whose `tools:` names the workspace's MCP server (`jira-creator`,
  `jira-reader`).
- **Vault files.** The workspace folder's `.vault/` folder mirrors each workspace's vault (its config's
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
