---
name: setup-workspace
description: Configure a workspace for the toolbox and install it there in one pass - detect what the workspace, git, gh and the session's MCP tools can tell, ask Allen only the rest, write .claude/toolbox.local.yml on approval, add the workspace to machine.yml, preview and apply the install, and finish with toolbox-doctor. Use when Allen says "set up this workspace", "set up the toolbox here", "/setup-workspace", or a toolbox script says to run it.
---

# Setup Workspace

Global, because it runs in a workspace that has nothing of the toolbox installed yet. It writes one untracked file in
the workspace, `.claude/toolbox.local.yml`, and one line in `claude-toolbox`'s `machine.yml`; the installer does the
rest. Nothing is written or applied without Allen's approval.

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
the MCP tools it names (step 3). `toolbox` gives the paths the later steps use: `draft_py`, `installer`, `toolbox_py`,
`machine`, and whether `machine.yml` already lists the workspace.

If `existing_config` is set, the workspace already has a config: show it and ask Allen whether to replace it (step 6
then passes `--force`) or stop and run `/toolbox-doctor` instead.

### 3. Read what the MCP tools know

Only the session can call MCP tools; the script can't. Use the servers the session has, by their tool names'
`mcp__<server>__` prefix, and record each value as `--mcp KEY=VALUE` for step 5. When `tracker.kind` would be `jira`
(an Atlassian server in `.mcp.json` or in this session, or ticket keys in the commits):

| Value | How |
|---|---|
| `tracker.jira.mcp_server` | the session's Atlassian server name, when `.mcp.json` didn't name one |
| `tracker.jira.site`, `cloud_id` | `getAccessibleAtlassianResources`: the site's host (`<site>.atlassian.net`) and its `id`. More than one site: ask which |
| `tracker.jira.account` | `atlassianUserInfo`: the signed-in account's email |
| `tracker.jira.fields` | the `discover` tool's field listing, or on a server without it `getJiraIssueTypeMetaWithFields` for the project's story type (`requiredFieldsOnly: false`), matched by name: "Acceptance Criteria" → `acceptance_criteria`, "Sprint" → `sprint`. No match: ask, or leave the field out |
| `tracker.jira.project` | detected from key prefixes; with none, ask, and check the key exists in the site's projects |

With no Atlassian tools in the session, ask for the site, cloud id and account, or let Allen pick another tracker.

The pull request announcement is always asked: the channel's **name**, resolved to its id with
`slack_search_channels`, as `--mcp pr.announce.channel=<id>`, with `pr.announce.mcp_server` set to the session's Slack
server when it isn't `slack`. If Allen wants no announcement, `--drop pr.announce`.

### 4. Ask the rest

Ask everything in one message: each `ask` key with its reason and any suggestion or `candidates` (a vault found
outside the workspace, such as `~/Notes/Work`, is a fine answer for `vault.path`; suggest the `obsidian` feature when
it has an `.obsidian/` folder), the announcement channel, and the
`confirm` values: `tracker.kind`, `features` (offer `known_features`, saying what each installs), and
`work` (session model, dispatch, WIP limits), and `shared_libraries.names` when detected (a task changing one runs
`work-task`'s shared-library cycle, then its consumers cycle). Ask nothing detected and nothing the table below doesn't name; Allen can
correct any value when he sees the draft.

| Value | Asked when |
|---|---|
| `pr.required`, `review_required`, `wait_for_ci` | no branch protection found, or `gh` not signed in |
| `user.handle`, `user.name` | nothing found |
| `vault.path` | no `.obsidian/` folder, or more than one |
| `tracker.kind` | always confirmed |
| Jira `site`, `cloud_id`, `account`, `project`, `fields` | more than one site, no keys found, no field match, or no Atlassian tools |
| `pr.announce` | always |
| `features`, `work` | always confirmed |
| `shared_libraries` | `names` confirmed when build files show a repo (or area) others build against, then `update` asked (`--set 'shared_libraries.update={command: ...}'` or `{skill: ...}`); `names` asked when some repos have no build file detection reads. No libraries: `--drop shared_libraries` |

### 5. Draft and present

```bash
python3 <draft_py> render <scratchpad>/detect.json --mcp KEY=VALUE... --set KEY=VALUE... [--drop KEY...]
```

`--set` carries Allen's answers (origin `asked`); a value is read as YAML (`true`, `[tasks, pr]`, `{unit: make
test}`). Keys the tracker doesn't use drop out by themselves. The JSON has the config as `yaml`, each value commented
with its origin, plus `errors`, `missing` and `unanswered`. Fix what those name (ask again if needed) and re-render until
all three are empty.

Present the `yaml` verbatim, then ask Allen to approve writing it. An edit he asks for is one more `--set` or `--drop`
and a re-render, never a hand edit of the YAML.

### 6. Write and list the workspace

On approval, run the same render with `--write` (and `--force` when replacing a config Allen agreed to replace). It
writes `<workspace>/.claude/toolbox.local.yml` only when the config is valid with nothing missing or unanswered. Then:

```bash
python3 <draft_py> add-workspace <workspace>
```

It adds the workspace to `machine.yml`'s `workspaces:` (copying `machine.example.yml` when there is none), keeping the
rest of the file. On a later decline, remove that line again.

### 7. Preview and apply the install

```bash
python3 <installer> --workspace <workspace>
```

Present it as `/sync-toolbox` does: the counts, every generated-rule change, conflict, warning and problem verbatim,
and `install-plan.txt`'s path. A real file where a link goes is usually a copy from before the toolbox: never delete or
move it without Allen saying so. On approval, run it again with `--apply` and report the counts and any errors.

### 8. Toolbox doctor

```bash
cd <workspace> && .claude/toolbox/bin/toolbox.py doctor
```

Then follow the installed `toolbox-doctor` skill (`<workspace>/.claude/skills/toolbox-doctor/SKILL.md`; this session
hasn't loaded it, so read it) for the MCP checks and the report. A failing check is reported with its fix; don't fix anything unasked.

### 9. Report

The config's path, what was installed, the toolbox-doctor results, and that a new session at the workspace root sees
the new skills and rules (this one doesn't). Losing the config costs one re-run of this skill.
