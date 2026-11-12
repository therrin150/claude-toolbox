---
name: setup-workspace
description: Configure a workspace for the toolbox and install into it: detect what it can, ask the rest, write .claude/toolbox.local.yml on approval, install, then check it with toolbox-doctor. Invoked as /setup-workspace.
disable-model-invocation: true
---

# Setup Workspace

Global, because it runs in a workspace that has nothing of the toolbox installed yet. It writes one untracked file in
the workspace, `.claude/toolbox.local.yml`, and one line in `claude-toolbox`'s `machine.yml`; the installer does the
rest. Nothing is written or applied without the user's approval.

## Arguments

```
/setup-workspace [workspace]
```

- **workspace**: the workspace root. Default: the session's working directory. A workspace is either one repo
  (`single`) or a folder of repos (`nested`); sessions launch at its root.

## Workflow

### 1. Find the scripts

The scripts are beside this file: resolve `~/.claude/skills/setup-workspace` (today
`~/_personal/claude-toolbox/global/skills/setup-workspace`) and use its `scripts/`. Run them with `python3` (`py -3` on
Windows). If `import yaml` fails, show `python3 -m pip install -r requirements.txt` (in `claude-toolbox`) and stop.

### 2. Detect

```bash
python3 <scripts>/detect.py <workspace> > <scratchpad>/detect.json
```

It changes nothing. Its JSON has `values`: each dotted config key with its `value`, its `origin` (`detected` or
`default`) and `from` (where it was read). Entries marked `ask` must be asked, `confirm` confirmed, and `mcp` read with
the MCP tools it names (step 4). `values.features` is the features the folder suggests, with what showed each in
`signals`; `feature_needs` names the config each feature needs. `toolbox` gives the paths the later steps use:
`draft_py`, `installer`, `toolbox_py`, `machine`, and whether `machine.yml` already lists the workspace.

If `existing_config` is set, the workspace already has a config: show it and ask the user whether to replace it (step 7
then passes `--force`) or stop and run `/toolbox-doctor` instead.

### 3. Pick the features

Features come first, since they decide what else is asked. Present the suggested features with what showed each
(`signals`), and offer the rest of `known_features`, saying what each installs:

| Feature | Installs | Suggested by | Needs |
|---|---|---|---|
| `git` | `git-commit`, `git-draft-commit-message`, `git-delete-merged-branches`, the git conventions rule | `.git` | |
| `verify` | `verify-change` and its `change-verifier` agent | `.git` | |
| `tests` | `run-unit-tests`, the `check-runner` agent | `.git` | `workspace.areas` |
| `worktrees` | `git-worktrees` | `.git` | |
| `pr` | `gh-create-pull-request`, `gh-merge-pull-request`, the `pr-reporter` agent | `.git` with a GitHub origin | |
| `tasks` | `work-task`, `propose-tickets`, `create-tickets`, `create-defect`, the ticket-docs rule | an Atlassian server | `tracker`, `vault` |
| `jira` | the `jira-creator` and `jira-reader` agents | an Atlassian server | `tracker.jira` |
| `jira-sprints` | `jira-find-current-sprint` | | `tracker.jira` |
| `dispatcher` | `work-tasks` | | |
| `services-lock` | `start-`, `stop-local-services` (user-invoked) | a compose file | `workspace.areas` |
| `review-ui` | `local-review-ui` | | |
| `intellij` | `intellij-open-worktree` | `.idea/` | |
| `obsidian` | the vault's Templater templates, the `vault-researcher` agent | `.obsidian/` | `vault`, `tracker` |
| `java` | `java-conventions`, `java-testing-conventions` (knowledge) | `pom.xml` or `*.java` | |
| `spring` | `spring-boot-conventions` (knowledge) | `spring-boot` in a `pom.xml` | |
| `postgres` | `database-conventions` (knowledge) | `db/migration/`, `*.sql`, or `postgres` in a compose file | |
| `angular` | `angular-conventions` (knowledge) | `angular.json` | |
| `playwright` | `playwright-conventions` (knowledge) | `playwright.config.*` | |
| `bruno` | `bruno-api-collections` (knowledge) | `bruno/` or `bruno.json` | |

Wait for the user's pick. It is `--set 'features=[...]'` in every later render.

### 4. Read what the MCP tools know

Only the session can call MCP tools; the script can't. Use the servers the session has, by their tool names'
`mcp__<server>__` prefix, and record each value as `--mcp KEY=VALUE` for step 6. Read Jira only when a picked feature
needs the tracker (`tasks`, `jira`, `jira-sprints`, `obsidian`) and `tracker.kind` would be `jira` (an Atlassian
server in `.mcp.json` or in this session, or ticket keys in the commits):

| Value | How |
|---|---|
| `tracker.jira.mcp_server` | the session's Atlassian server name, when `.mcp.json` didn't name one |
| `tracker.jira.site`, `cloud_id` | `getAccessibleAtlassianResources`: the site's host (`<site>.atlassian.net`) and its `id`. More than one site: ask which |
| `tracker.jira.account` | `atlassianUserInfo`: the signed-in account's email |
| `tracker.jira.fields` | the `discover` tool's field listing, or on a server without it `getJiraIssueTypeMetaWithFields` for the project's story type (`requiredFieldsOnly: false`), matched by name: "Acceptance Criteria" → `acceptance_criteria`, "Sprint" → `sprint`. No match: ask, or leave the field out |
| `tracker.jira.project` | detected from key prefixes; with none, ask, and check the key exists in the site's projects |

With no Atlassian tools in the session, ask for the site, cloud id and account, or let the user pick another tracker.

With `pr` picked, the pull request announcement is asked: the channel's **name**, resolved to its id with
`slack_search_channels`, as `--mcp pr.announce.channel=<id>`, with `pr.announce.mcp_server` set to the session's Slack
server when it isn't `slack`. If the user wants no announcement, `--drop pr.announce`.

### 5. Ask the rest

Render once with the picked features (step 6's command, without `--write`): what the features don't use drops out,
so its `missing`, `unanswered` and `unmet` are what's left to ask. Ask it all in one message: each key with detect's
reason and any suggestion or `candidates` (a vault found outside the workspace, such as `~/Notes/Work`, is a fine
answer for `vault.path`), each `unmet` need with the feature that needs it, and the `confirm` values still in the
draft. Ask nothing detected and nothing the table below doesn't name; the user can correct any value when they see
the draft.

| Value | Asked when |
|---|---|
| `pr.required`, `review_required`, `wait_for_ci` | no branch protection found, or `gh` not signed in |
| `user.handle`, `user.name` | nothing found |
| `workspace.areas` | `tests` or `services-lock` picked and no build files found (`unmet`) |
| `vault.path` | `tasks`, `obsidian` or a `vault` tracker picked, and no `.obsidian/` folder or more than one |
| `tracker.kind` | confirmed when `tasks`, `jira`, `jira-sprints` or `obsidian` is picked |
| Jira `site`, `cloud_id`, `account`, `project`, `fields` | `tracker.kind` is `jira` and more than one site, no keys found, no field match, or no Atlassian tools |
| `pr.announce` | `pr` picked |
| `work` (session model, dispatch, WIP limits) | confirmed when `tasks` is picked |
| `shared_libraries` | `tasks` picked: `names` confirmed when build files show a repo (or area) others build against (a task changing one runs `work-task`'s shared-library cycle, then its consumers cycle), then `update` asked (`--set 'shared_libraries.update={command: ...}'`, `{skill: ...}`, or `{path: ~/.../SKILL.md}` for a skill file outside the workspace); `names` asked when some repos have no build file detection reads. No libraries: `--drop shared_libraries` |

### 6. Draft and present

```bash
python3 <draft_py> render <scratchpad>/detect.json --set 'features=[...]' --mcp KEY=VALUE... --set KEY=VALUE... [--drop KEY...]
```

`--set` carries the user's answers (origin `asked`); a value is read as YAML (`true`, `[git, pr]`, `{unit: make
test}`). Keys the picked features or the tracker don't use drop out by themselves: no `vault` or `tracker` unless a
feature needs it. The JSON has the config as `yaml`, each value commented with its origin, plus `errors`, `missing`,
`unanswered` and `unmet` (a picked feature's needs nothing gives). Fix what those name (ask again if needed) and
re-render until all four are empty.

Present the `yaml` verbatim, then ask the user to approve writing it. An edit they ask for is one more `--set` or
`--drop` and a re-render, never a hand edit of the YAML.

### 7. Write and list the workspace

On approval, run the same render with `--write` (and `--force` when replacing a config the user agreed to replace). It
writes `<workspace>/.claude/toolbox.local.yml` only when the config is valid with nothing missing, unanswered or unmet.
Then:

```bash
python3 <draft_py> add-workspace <workspace>
```

It adds the workspace to `machine.yml`'s `workspaces:` (copying `machine.example.yml` when there is none), keeping the
rest of the file. On a later decline, remove that line again.

### 8. Preview and apply the install

```bash
python3 <installer> --workspace <workspace>
```

Present it as `/sync-toolbox` does: the counts, every generated-rule change, conflict, warning and problem verbatim,
and `install-plan.txt`'s path. A real file where a link goes is usually a copy from before the toolbox: never delete or
move it without the user saying so. On approval, run it again with `--apply` and report the counts and any errors.

### 9. Toolbox doctor

```bash
cd <workspace> && .claude/toolbox/bin/toolbox.py doctor
```

Then follow the installed `toolbox-doctor` skill (`<workspace>/.claude/skills/toolbox-doctor/SKILL.md`; this session
hasn't loaded it, so read it) for the MCP checks and the report. A failing check is reported with its fix; don't fix
anything unasked.

### 10. Report

The config's path, what was installed, the toolbox-doctor results, and that a new session at the workspace root sees
the new skills and rules (this one doesn't). Losing the config costs one re-run of this skill.
