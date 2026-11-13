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
| `tasks` | `work-task` (drives the stage skills in order for a ticket and keeps its record, stops and hand-offs), `start-change` (also under `worktrees`), `investigate` (a read-only production investigation, recorded in a discussion, ending in a defect when there is a fix), `propose-tickets`, `create-tickets`, `create-defect`, the `ticket-docs.md` and `session-types.md` rules (the latter says which entry skill a session starts from how it's opened) | `tracker`, `vault` |
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
and installs into every workspace `machine.yml` lists. The installer creates folders and copies every file, so each
lands inside the project that uses it (skill folders, anything containing `SKILL.md`, included). An edit here takes
effect on the next sync, which `ship-toolbox-change` and `/sync-toolbox` run.

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
  .claude/rules/                 workspace rules, copied with a generated note
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

## Installer

| Command | Effect |
|---|---|
| `install.py` | Preview: writes the full plan to `install-plan.txt` and changes nothing |
| `install.py --apply` | Creates and updates copies, removes stale ones, updates the manifest and git excludes |
| `install.py --toolbox <name or path>` | Acts on one toolbox only (`claude-toolbox` means both its folders), ignoring the others `machine.yml` lists (repeatable) |
| `install.py --workspace <path>` | Acts on one workspace only, ignoring `machine.yml`'s workspaces (repeatable) |
| `install.py --teardown` | Removes everything this installer installed (add `--apply`); combines with the two above |

- **Copies and a manifest.** Every installed file is a copy, written atomically (a temp file beside it, then a
  rename), with the source's executable bit. `.install-state.json` (untracked) is the manifest: for each file, its
  source and the hash written. Every target is classified against it:

  | Class | Meaning |
  |---|---|
  | `create` | nothing there |
  | `update` | on disk is what was written (or a link of this installer's), and the source changed |
  | `unchanged` | on disk is what the source gives |
  | `remove` | no longer wanted (the source is gone, the feature dropped, the toolbox unlisted), and unedited |
  | `conflict` | edited on disk, no longer wanted but edited, a file the installer didn't write, a link to somewhere else, or a folder in the way: left alone |

  A second `--apply` reports every target `unchanged`. A conflict is resolved by hand: copy the edit into the toolbox,
  or delete the file so the next sync writes the toolbox's copy. A file that is already exactly what would be written
  is adopted, not a conflict. The first apply replaces links an earlier installer made into a toolbox (file links and
  skill-folder links) with copies; state from before the manifest has no hashes, so its generated files are rewritten
  as they always were.
- **Running it syncs.** A renamed or deleted file's old copy is removed on the next run, and a toolbox or workspace
  dropped from `machine.yml` is uninstalled.
- **Finding the repo.** A script installed as a copy sits outside the repo, so the installer also writes the repo's
  path to `~/.claude/toolbox-repo`; `ship.py`, `/setup-workspace` and `toolbox.py doctor` read it to find `install.py`
  and `machine.yml`.
- **Other toolboxes.** Each repo under `toolboxes:` installs whole, as its `toolbox.yml` says: one `source` folder in
  that repo mirroring one `target` folder, with an optional `prefix`, `ignore` and `external` files (this repo's
  `toolbox.yml` describes each key). It is known by its folder's name; a name taken twice is a problem, and the first
  wins. Links into any listed toolbox count as this installer's own, and are replaced by copies. A toolbox still
  holding the retired `domains.yml`, or a `toolbox.yml` with `domains:` in it, is a problem, and nothing it installed
  is touched until it's reshaped. A `machine.yml` that still has `domains:` is a problem too: the key is no longer
  read.
- **Feature folders.** A toolbox repo (this one or any listed) may hold `core/` and `features/<name>/`, each a
  `feature.yml` and a `.claude/` tree that mirrors where its files install: `features/tests/.claude/agents/
  check-runner.md` installs to `<workspace>/.claude/agents/check-runner.md`. A feature's `.vault/` tree mirrors the
  workspace's vault, and `.tmpl` files and `.claude/rules/` rules behave as in the workspace folder below. Nothing
  else in the folder installs. `feature.yml` has:

  | Key | Meaning |
  |---|---|
  | `name` | the folder's name (lowercase words joined by hyphens) |
  | `description` | one line on what it is for |
  | `target` | `workspace`: installs into each workspace whose `features` lists it. `global`: installs into `~/.claude` when `machine.yml`'s `features` lists it |
  | `needs` | workspace config keys it reads, as in `features.yml` (workspace features only) |
  | `requires` | features that must be picked wherever it is: a workspace picks them (or `machine.yml` does, for a global one), or the workspace is a problem and nothing there is installed or removed; for a global feature, nothing is installed or removed from that toolbox's global features |

  `core/` always installs (into every workspace, or into `~/.claude` if its `target` is `global`); a feature can't be
  named `core`. An invalid or missing `feature.yml` is a problem, and nothing is installed or removed from that
  toolbox's feature folders until it's fixed. A listed toolbox with feature folders may leave `source` and `target`
  out of its `toolbox.yml`; one with only one of them is a problem. Two features installing to the same path are a
  collision. Each toolbox's feature folders are two parts, `<toolbox>:workspace` and `<toolbox>:global`, which
  `--toolbox` and `--workspace` select with the rest of the toolbox. Git excludes and the manifest record them like any
  part. The older `workspace/` folder and `features.yml` path lists still install, for the layout that predates
  feature folders.
- **The workspace folder** installs into every listed workspace. A workspace with no `.claude/toolbox.local.yml`, or an
  invalid one, is a problem line naming `/setup-workspace`, and nothing there is installed or removed. Otherwise its
  `features` gate what installs: `workspace/features.yml` maps each feature to paths, a path needs every feature that
  lists it, and anything unlisted always installs. Dropping a feature removes its files on the next sync. A feature
  may also list the config it `needs` (`tasks` needs `tracker` and `vault`); a workspace that picks it without that
  config is a problem, and nothing there is installed or removed until the config gains it or drops the feature.
- **Generated rule.** Each workspace gets `.claude/rules/toolbox-config.md`, rendered from its config on every sync. It
  is recorded in the manifest like any file, so stale cleanup and teardown remove it; a file there the installer didn't
  write is a conflict.
- **Workspace rules.** Each rule in `workspace/.claude/rules/` is copied into the workspace with a note after its
  title (and after any frontmatter, so `paths:` keeps working) that it is generated. Like any file, a changed source
  rewrites it, a removed one or a dropped feature removes it, and an edit made in the workspace is a conflict.
- **Templates.** In the workspace folder, a file whose name ends `.tmpl` is rendered into each workspace at the same
  path without the suffix, instead of being copied as it is: each `{{ dotted.config.key }}` becomes that key's value
  from the workspace's config. `features.yml` gates it by its `.tmpl` name. A key the config doesn't have, or one that
  isn't a single value, is a problem, and the file there is left as it is. This is for what a plain copy can't do, such
  as an agent whose `tools:` names the workspace's MCP server (`jira-creator`, `jira-reader`).
- **Vault files.** The workspace folder's `.vault/` folder mirrors each workspace's vault (its config's
  `vault.path`, inside the workspace or anywhere on disk) instead of the workspace root. A `.tmpl` file is rendered as
  above, any other file is copied as it is. A changed source rewrites them, stale cleanup and teardown remove them, and
  an edit made in the vault is a conflict, left alone. `features.yml` gates them by their `.vault/`
  paths (the `obsidian` feature gates the whole folder). A vault that doesn't exist is a problem. Inside the
  workspace's git repo they are excluded from git like the rest; a vault outside it gets no exclude.
- **It never touches what isn't its own.** A file it didn't write, or a link pointing somewhere else, is reported as a
  conflict and left alone.
- **Git targets.** When a target folder or workspace is a git repo, the installed files (and a workspace's config file)
  go in that repo's `.git/info/exclude`, so they never show up as untracked files.
