---
name: toolbox-doctor
description: Check this workspace's toolbox setup in one pass - the config, the install, the generated rule and rendered files, gh's account, the vault folders and every extension doc's hooks, then the Jira and Slack MCP connections and accounts - and report each check with its fix. Read-only. Use when Allen says "toolbox doctor", "check the toolbox", "is the toolbox set up right", after /setup-workspace, or when a toolbox skill or script behaves as if the setup were wrong.
allowed-tools: Bash(*), Read
---

# Toolbox Doctor

Checks; never fixes. Each failing check is reported with the fix, and Allen decides. Named `toolbox-doctor` because
`/doctor` is Claude Code's own command.

## Workflow

### Script checks

From the workspace root (its absolute path is in the toolbox-config rule):

```bash
.claude/toolbox/bin/toolbox.py doctor
```

It prints JSON with one line per check, each `ok`, `fail`, or `skip` (it needs the config, which failed), then `mcp`:
the checks below. Exit 1 when any check isn't `ok`.

| Check | Fails when | Fix |
|---|---|---|
| `config` | `.claude/toolbox.local.yml` is missing or doesn't match its schema | the errors name each key; edit the config, or run `/setup-workspace` |
| `install` | the installer's preview for this workspace isn't clean, or `machine.yml` doesn't list it | `/install-domain` (it previews first) |
| `rule` | `.claude/rules/toolbox-config.md` is missing or older than the config | `/install-domain` |
| `rendered` | a file rendered from a template (`jira-filer`'s agent, say) is missing, out of date with the config or no longer wanted, or the template names a config key the config lacks | `/install-domain`; for a missing key, add it to the config (the `jira` feature needs `tracker.jira`) |
| `gh` | `gh` isn't signed in, or not as `user.github` (`user.handle` when unset) | `gh auth login` / `gh auth switch`, or set `user.github` to the GitHub login when it isn't the branch prefix |
| `vault` | the vault, tasks or discussions folder is missing | create it, or correct `vault.*` |
| `extensions` | a doc in `.claude/extensions/` lacks `type: extension` or `extends:`, extends a skill that isn't installed or has no Extension Points table, has a `##` heading that isn't `<add\|replace\|skip> <hook id>`, names a hook the skill doesn't have (the nearest is suggested) or a verb the hook doesn't allow, or replaces or skips a hook another doc does; or `extensions.disabled` names a doc that isn't there | fix the doc against the skill's Extension Points table (`.claude/extensions/README.md`), or name it in `extensions.disabled` |

### MCP checks

For each entry of `mcp`, with the tools `mcp__<server>__<tool>` of the entry's `server`:

- **`jira`**: steps 1 to 3 of `.claude/toolbox/references/identity-check.md`, the check every skill that writes to Jira
  runs: connected, `atlassianUserInfo`'s email is the entry's `account`, and `getAccessibleAtlassianResources` lists its
  `cloud_id` for its `site`.
- **`slack`**: the server's tools are in this session (else: not connected), and the channel id resolves:
  `slack_read_channel` on it with a limit of 1 succeeds. Name the channel in the report. The config names no Slack
  account, so there is no account to compare.

These are reads only. A wrong account is a failure even when the tools work: no skill may write as it.

### Report

A table, one row per check (script and MCP): check, status, and for each failure its detail and fix. End with the
overall result. Don't fix anything unless Allen asks.
