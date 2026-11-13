# Identity Check

Before any write through an MCP server (creating, editing, transitioning, linking or commenting on an issue, posting a
message), confirm the server is signed in as the account this workspace's config names. A server signed in to the
wrong account writes into the wrong site or workspace, and nothing in the write itself would catch it. A script can't
call an MCP tool, so this is the session's or the agent's step, once per session or agent run, before the first write.

| Server | Config | Check |
|---|---|---|
| Jira (`tracker.jira.mcp_server`) | `tracker.jira.account`, `tracker.jira.cloud_id`, `tracker.jira.site` | `atlassianUserInfo`: its email is the account. `getAccessibleAtlassianResources`: it lists the cloud id, for the site |
| Slack (`pr.announce.mcp_server`) | `pr.announce.channel` (no account: a channel id belongs to one Slack workspace) | `slack_read_channel` on the channel with a limit of 1 succeeds |

The toolbox-config rule names the server, the account and the cloud id; `toolbox.py config show tracker.jira` prints
the rest. Tool names are `mcp__<server>__<tool>`.

## Steps

1. Call the check's tool (Jira: the user-info tool; Slack: `slack_read_channel`). If the server isn't connected, or
   the tool isn't available, stop: say which server and that it isn't connected. For Slack, a failed read means the
   server is signed in to another workspace or can't see the channel: stop before posting and say so. Steps 2 and 3
   are Jira's.
2. Compare its account with the configured one, ignoring case. On a mismatch, stop before any write and report both:
   "Jira is signed in as `<found>`, but this workspace files as `<configured>`. Sign the `<server>` server in to
   `<configured>`, then run this again." Never fall back to another server, site or account.
3. For Jira, also check that the configured cloud id is among the accessible resources, and pass it on every call.
4. Reads before the check are fine. Once it passes, it holds for the rest of the session or agent run, unless a call
   fails with an authentication error, which means checking again.
