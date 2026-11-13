# Claude Toolbox

Allen's personal Claude Code configuration: the global rules, skills and style guides that apply in every session,
and the generic workflow tools and stack conventions installed into each workspace, picked per workspace by the
features in its untracked `.claude/toolbox.local.yml`. `install.py` is the one installer: it also installs every other
toolbox repo this machine lists (such as the work toolbox), each whole. A project's own conventions stay in its own
repo.

## Three Layers

Every session in a workspace (any folder sessions start in that the toolbox installs into) is fed by three layers:

1. **Global** (global features → `~/.claude`): what holds in every session on the machine. The response style, the
   git, shell and Obsidian rules, and the skills that run before a workspace has anything installed. Each is a feature
   of its own that `machine.yml` picks; the global core (the hook dispatcher and the `Read(~/.claude/**)` allow) always
   installs.
2. **Feature-gated workspace items** (workspace features → each workspace's `.claude/`): what's shared but chosen per
   project. Every skill, rule and agent here sits in a feature folder, and installs only into a workspace whose config
   picks it: workflow skills (`git`, `tasks`, `pr`, `verify`, ...) and knowledge skills (conventions for one
   technology, `java` to `bruno`). Only the workspace core installs everywhere: the runtime (`.claude/toolbox/`),
   `toolbox-doctor`, `toolbox-features`, the report contract gates (an agent's report template declares what its report
   must carry and must not; the gates deny a report that breaks it), the `workspace-searcher` agent, and the generated
   `toolbox-config.md` rule.
3. **The project's own files**: its `.claude/toolbox.local.yml` (features and values, untracked), and, checked in, its
   prefixed rules (`.claude/rules/<prefix>-*.md`, only what it does differently) and extension docs
   (`.claude/extensions/<prefix>-<skill>.md`, its own steps inside a shared skill).

Overrides are additions in the project's own files, never edits to shared ones. In order of preference: a config
value; an extension at a skill's extension point; a prefixed project rule or skill that states what differs; and last,
dropping the feature and keeping a project-owned copy.

### Where a New Thing Goes

| You want… | Put it in | Loads |
|---|---|---|
| Something true in every session on the machine | a global feature, `features/rule-<name>/.claude/rules/` | session start, everywhere |
| A procedure reused across projects | `features/<feature>/.claude/skills/<name>/` | Claude judges the description relevant, or `/name` |
| A convention you'd follow in the next project on the same stack | the stack's knowledge skill, in its feature folder (`features/java/`, ...) | Claude judges the description relevant |
| Something only this project does differently (its modules, hosts, vendors, decisions) | the project's `.claude/rules/<prefix>-*.md`, checked in | session start, or a matching file read (`paths:`) |
| A project's own step inside a shared skill | the project's `.claude/extensions/<prefix>-<skill>.md` | read by the skill at its extension point |
| A project's values (build commands, branches, tracker) | `.claude/toolbox.local.yml` | read by the scripts and the generated `toolbox-config.md` |

### Features

| Feature | Installs | Needs | Requires |
|---|---|---|---|
| `git` | `git-commit`, `git-draft-commit-message`, `publish-change` (the publish stage: drafts the commit message for approval, then commits, pushes and opens the PR through `publish.py`, refusing a checkout on the default branch and warning when checks haven't run on the current diff), `git-delete-merged-branches`, the `git-conventions.md` rule, the git write gate, the `diff-summarizer` agent (a read-only Haiku summary of a diff, grouped by change, with its out-of-place lines) and its `diff_summary.py` |  | `worktrees` |
| `verify` | `verify-change`, the `change-verifier` agent |  |  |
| `tests` | `run-unit-tests`, `check-change` (the check stage: gates a change's `build`, `unit`, `format` or `integration` check through `change.py check`, then runs it in a `check-runner`), the `check-runner` agent, the checks and raw output gates (deny builds, tests, `services_lock.py` and build or test logs in the main session) | `workspace.areas` | `worktrees` |
| `worktrees` | `git-worktrees`, the main checkout gate (denies `Edit` and `Write` in a repo's main checkout while a task worktree of it exists). Every stage skill that works on a change's worktrees sits in the feature that supplies the rest of what it needs, which requires this one |  |  |
| `pr` | `gh-create-pull-request`, `gh-merge-pull-request`, `finish-change` (the finish stage: gates a published change through `change.py finish`, then merges its PR, removes its worktrees and marks the record done through `publish.py`, refusing a worktree with uncommitted or untracked files or a PR that isn't approved, green and mergeable), the `pr-reporter` agent (a read-only Haiku report of a PR's review comments, sorted, and its failed checks' error lines) and its `pr_report.py` |  | `worktrees` |
| `tasks` | `work-task` (drives the stage skills in order for a ticket and keeps its record, stops and hand-offs), `start-change` (the start stage: makes or finds a change's record and gives it a branch and worktrees through `change.py start`, refusing a repo that already has one), `investigate` (a read-only production investigation, recorded in a discussion, ending in a defect when there is a fix), `propose-tickets`, `create-tickets`, `create-defect`, the `ticket-docs.md` and `session-types.md` rules (the latter says which entry skill a session starts from how it's opened) | `tracker`, `vault` | `worktrees` |
| `jira` | the `jira-creator` agent (a template; a Haiku agent that files drafts as Jira issues, after `draft_to_jira.py` converts their markdown), the `jira-reader` agent (a template; a read-only Haiku lookup of issues or a JQL that returns only the fields asked for), the Jira write gate (denies the Jira MCP server's create, edit, comment, link, worklog and transition tools in the main session, so a write goes through `jira-creator`) | `tracker.jira` |  |
| `jira-sprints` | `jira-find-current-sprint` | `tracker.jira` |  |
| `dispatcher` | `work-tasks`, `dispatch.py` |  |  |
| `services-lock` | `start-`, `stop-local-services` (user-invoked), `services_lock.py` | `workspace.areas` |  |
| `review-ui` | `local-review-ui`, `review-change` (the review stage: gates a change through `change.py review`, then opens `local-review-ui`) |  | `worktrees` |
| `intellij` | `intellij-open-worktree` |  |  |
| `obsidian` | the vault's Templater templates (`.vault/`), the `vault-researcher` agent (a read-only Haiku search of the vault's discussions and tasks, reported per item as its wikilink, status, and the decisions and open questions that bear on a topic) | `vault`, `tracker` |  |
| `java` | `java-conventions`, `java-testing-conventions` (knowledge) |  |  |
| `spring` | `spring-boot-conventions` (knowledge) |  |  |
| `postgres` | `database-conventions` (knowledge) |  |  |
| `angular` | `angular-conventions` (knowledge) |  |  |
| `playwright` | `playwright-conventions` (knowledge) |  |  |
| `bruno` | `bruno-api-collections` (knowledge) |  |  |

The global features, picked in `machine.yml`'s `features:` and installed into `~/.claude`:

| Feature | Installs |
|---|---|
| `response-style` | `CLAUDE.md`, the response style |
| `personal-styles` | `personal/voice.md`, `ticket-style.md` and `commit-style.md`, read where the skills and rules reference them |
| `rule-git` | the git rule: Git writes need an explicit instruction, no commit trailers, commit voice |
| `rule-obsidian` | the Personal vault and Obsidian formatting; discussions are the discussion skill's |
| `rule-shell` | zsh pitfalls |
| `discussion` | start or extend a design discussion in a vault |
| `setup-workspace` | configure a workspace and install into it (`detect.py`, `draft.py`) |
| `ship-toolbox-change` | ship a change to any toolbox repo (`ship.py`) |
| `sync-toolbox` | re-sync, add or remove a toolbox or workspace, or tear down |

Both cores always install. The global core registers the hook dispatcher (`~/.claude/hooks/run_gates.py`) and allows
reads of `~/.claude/**`; the workspace core is the runtime, `toolbox-doctor`, `toolbox-features`, the report contract
gates and the `workspace-searcher` agent. A feature that sits beside another's stage skill `requires` it: a workspace that picks it
without the other is a problem, and `/setup-workspace` suggests both.

The `workspace-searcher` agent (a read-only Haiku sweep of the workspace's repos that reports each location with a
one-line role, never file contents) is in the workspace core: every workspace gets it.

`/setup-workspace` suggests features from the folder (`.git`, `pom.xml`, `angular.json`, `bruno/`, `.obsidian/`, ...)
and asks only for the config the picked ones need: the `vault` and `tracker` sections are optional, and a workspace
with neither asks no vault or tracker questions.

## Layout

This repo is feature folders: `core/` always installs, and each folder under `features/` installs when picked. The
installer creates folders and copies every file, so each lands inside the project that uses it (skill folders,
anything containing `SKILL.md`, included). An edit here takes effect on the next sync, which `ship-toolbox-change` and
`/sync-toolbox` run.

```
core/
  global/                        → ~/.claude, always
    feature.yml, settings.json   registers the dispatcher; allows Read(~/.claude/**)
    .claude/hooks/               run_gates.py, the one hook dispatcher, and an example gate
  workspace/                     → every listed workspace, always
    feature.yml
    .claude/toolbox/             scripts (bin/), their library (lib/), references, templates, agent docs, and tests/
    .claude/skills/, agents/     toolbox-doctor, toolbox-features, workspace-searcher
    .claude/hooks/gates/         the shared gate helper and the report contract gates
features/<name>/                 → a workspace that lists it, or ~/.claude when machine.yml lists it (target)
  feature.yml                    name, description, target, needs, requires
  settings.json                  optional: what it adds to the settings file of its scope
  .claude/                       mirrors where its files install: skills/, agents/, rules/, hooks/, toolbox/bin/
  .vault/                        mirrors the workspace's vault (feature obsidian)
install.py                       the installer
toolbox.yml                      this repo's manifest (how changes ship), and the reference for other toolboxes' keys
tests/                           the installer's tests: python3 -m unittest discover -s tests
```

The toolbox scripts' tests sit in `core/workspace/.claude/toolbox/tests/` (never installed) and run against an assembled
copy of every feature folder, since the scripts call each other as installed:
`python3 -m unittest discover -s core/workspace/.claude/toolbox/tests`.

## Enabling and Disabling Features

`/toolbox-features` (the workspace core's skill, over `toolbox.py features`) is the way to see and change what is
installed, instead of editing `machine.yml` or `toolbox.local.yml` and syncing by hand:

| Command | Effect |
|---|---|
| `toolbox.py features [--json]` | Every feature of every toolbox: scope, where it's installed, state (see `install.py --features`). Needs no workspace |
| `toolbox.py features enable <feature>... --scope global\|workspace` | Preview: what the sync would create, update and remove. Writes nothing |
| `toolbox.py features disable <feature>... --scope global\|workspace` | The same, plus `concurrent`: what could be running on the files it removes |
| `... --apply` | Writes the config (`machine.yml` for a global feature, the workspace's `toolbox.local.yml` otherwise, keeping its comments and list style) and syncs that scope |
| `disable ... --apply --confirm-concurrent` | A disable is refused without the flag (exit 3, nothing written); the skill passes it only after the user has read the warning |

`concurrent` lists, for a workspace: tasks `in-progress`, task worktrees, held services locks and other sessions started
in the workspace or its worktrees in the last hour; for a global feature: other sessions anywhere in the last hour.
An enable that needs a feature or config that isn't picked, and a disable of a feature another picked one requires, are
refused before anything is written.

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
   - `workspaces`: where the workspace core and features install. Each needs a `.claude/toolbox.local.yml`;
     `/setup-workspace` writes it and adds the workspace here.
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
| `install.py --toolbox <name or path>` | Acts on one toolbox only (`claude-toolbox` means both its global and workspace parts), ignoring the others `machine.yml` lists (repeatable) |
| `install.py --workspace <path>` | Acts on one workspace only, ignoring `machine.yml`'s workspaces (repeatable) |
| `install.py --teardown` | Removes everything this installer installed (add `--apply`); combines with the two above |
| `install.py --features` | Lists every feature of every toolbox as JSON: its scope, each place it installs into and its state there (`in-sync`, `drifted` with counts of edited, outdated and missing files, `pending-install`, `pending-removal`, `off`). Changes nothing; `toolbox.py features` is the readable front end |
| `install.py --json --workspace <path> --enable/--disable <feature>` | Previews the sync as if the workspace's config picked (or dropped) the feature, without writing the config; without `--workspace`, as if `machine.yml` did, for a global feature. Repeatable |

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
  `feature.yml` and a `.claude/` tree that mirrors where its files install:
  `features/tests/.claude/agents/check-runner.md` installs to `<workspace>/.claude/agents/check-runner.md`. A feature's
  `.vault/` tree mirrors the workspace's vault, and `.tmpl` files and `.claude/rules/` rules of a workspace feature
  behave as described below. Nothing else in the folder installs except its `settings.json` fragment, which is merged
  (below). `feature.yml` has:

  | Key | Meaning |
  |---|---|
  | `name` | the folder's name (lowercase words joined by hyphens) |
  | `description` | one line on what it is for |
  | `target` | `workspace`: installs into each workspace whose `features` lists it. `global`: installs into `~/.claude` when `machine.yml`'s `features` lists it |
  | `needs` | workspace config keys it reads, dotted keys or sections (`tracker`, `tracker.jira`, `workspace.areas`); a workspace that picks it without that config is a problem, and nothing there is installed or removed until the config gains it or drops the feature (workspace features only) |
  | `requires` | features that must be picked wherever it is: a workspace picks them (or `machine.yml` does, for a global one), or the workspace is a problem and nothing there is installed or removed; for a global feature, nothing is installed or removed from that toolbox's global features |

  `core/` always installs: one folder with its own `feature.yml` (into every workspace, or into `~/.claude` if its
  `target` is `global`), or, for a global and a workspace core, a `core/global/` and a `core/workspace/` folder, each
  with a `feature.yml` named `core` whose `target` is its folder's name (this repo's layout). A feature can't be named
  `core`. An invalid or missing `feature.yml` is a problem, and nothing is installed or removed from that
  toolbox's feature folders until it's fixed. A listed toolbox with feature folders may leave `source` and `target`
  out of its `toolbox.yml`; one with only one of them is a problem. Two features installing to the same path are a
  collision. Each toolbox's feature folders are two parts, `<toolbox>:workspace` and `<toolbox>:global`, which
  `--toolbox` and `--workspace` select with the rest of the toolbox. Git excludes and the manifest record them like any
  part. A part recorded in the manifest under the retired names `global` and `workspace` (this repo's old folders) is
  cleaned up as the same files install under the new parts, so nothing is removed or rewritten.
- **Settings fragments.** A feature folder may hold a `settings.json` beside its `feature.yml`: a JSON object of what
  the feature adds to the settings file of its scope (its permission allows, its hooks). It is merged, never copied:
  each item of a list (an allow, a hook entry) is one entry, and each other value is one entry. A key the settings
  already set to something else keeps its value, with a warning, and the first of two fragments to set a key wins. A
  fragment that isn't a JSON object is a problem, and the settings file it feeds is left as it is.
  - **Workspace.** The fragments of `core` and each picked workspace feature, across every toolbox, are merged into the
    workspace's `.claude/settings.json`, which the installer owns: it is generated like the rule, recorded in the
    manifest, rewritten on a change, removed when no fragment is left, and a file there it didn't write or that was
    edited is a conflict. Your own entries go in `.claude/settings.local.json`, which the installer never reads or
    writes.
  - **Global.** The fragments of `core` and each global feature `machine.yml` picks are merged into
    `~/.claude/settings.json`, which is yours. The manifest's `settings` section records the entries the installer
    added and the mappings and lists it made, so it merges around your entries and on a dropped feature or `--teardown`
    removes only those (and its empty mappings, and the file if it made it and nothing else is left). A second `--apply`
    changes nothing, and leaves your formatting as it is. An entry you already had is not recorded, so it stays. A file
    that isn't a JSON object is a problem and left alone; a link at the path is written through.
  - **The global core.** `core/global/settings.json` registers `~/.claude/hooks/run_gates.py` as the one `PreToolUse`
    `*` hook (its command skips a missing script, since a hook command that exits 2 blocks the call) and allows
    `Read(~/.claude/**)`, so a session reads a global skill's references and runs its script (called through
    `${CLAUDE_SKILL_DIR}`, with a matching `allowed-tools` entry) without a prompt.
- **Workspaces.** A workspace with no `.claude/toolbox.local.yml`, or an invalid one, is a problem line naming
  `/setup-workspace`, and nothing there is installed or removed. Otherwise the workspace core and the workspace
  features its `features` list install; dropping a feature removes its files on the next sync.
- **Generated rule.** Each workspace gets `.claude/rules/toolbox-config.md`, rendered from its config on every sync. It
  is recorded in the manifest like any file, so stale cleanup and teardown remove it; a file there the installer didn't
  write is a conflict.
- **Workspace rules.** Each rule in a workspace feature's `.claude/rules/` is copied into the workspace with a note
  after its title (and after any frontmatter, so `paths:` keeps working) that it is generated. Like any file, a
  changed source rewrites it, a removed one or a dropped feature removes it, and an edit made in the workspace is a
  conflict.
- **Templates.** In a workspace feature, a file whose name ends `.tmpl` is rendered into each workspace at the same
  path without the suffix, instead of being copied as it is: each `{{ dotted.config.key }}` becomes that key's value
  from the workspace's config. A key the config doesn't have, or one that
  isn't a single value, is a problem, and the file there is left as it is. This is for what a plain copy can't do, such
  as an agent whose `tools:` names the workspace's MCP server (`jira-creator`, `jira-reader`).
- **Vault files.** A workspace feature's `.vault/` folder mirrors each workspace's vault (its config's
  `vault.path`, inside the workspace or anywhere on disk) instead of the workspace root. A `.tmpl` file is rendered as
  above, any other file is copied as it is. A changed source rewrites them, stale cleanup and teardown remove them, and
  an edit made in the vault is a conflict, left alone. A vault that doesn't exist is a problem. Inside the
  workspace's git repo they are excluded from git like the rest; a vault outside it gets no exclude.
- **It never touches what isn't its own.** A file it didn't write, or a link pointing somewhere else, is reported as a
  conflict and left alone.
- **Git targets.** When a target folder or workspace is a git repo, the installed files (and a workspace's config file)
  go in that repo's `.git/info/exclude`, so they never show up as untracked files.
