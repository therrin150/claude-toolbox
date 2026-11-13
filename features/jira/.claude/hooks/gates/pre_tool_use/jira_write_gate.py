"""The Jira write gate: the main session never creates, edits, comments on, links, logs work on or transitions a Jira
issue by calling the MCP server directly.

`jira-creator` converts a draft's markdown first, runs the identity check and checks what it filed, and keeps each
request and response out of the main session's context. Denied in the main session: the Jira MCP server's write tools,
whatever the server is called (`mcp__<server>__<tool>`):

  createJiraIssue, editJiraIssue, addCommentToJiraIssue, addOrEditJiraIssueComment, addWorklogToJiraIssue,
  createIssueLink, transitionJiraIssue, executeWrite (the v2 server's generic write, which can do any of them)

Allowed: every other tool of the server (`getJiraIssue`, `searchJiraIssuesUsingJql`, `getTransitionsForJiraIssue`,
`discover`, `executeRead`, ...), and Confluence's own write tools (`createConfluencePage`, ...), which are not Jira's.
The v2 server's generic `executeWrite` is denied whatever product it targets, since the gate can't tell; a Confluence
write on v2 goes through a subagent too.

A subagent is never denied (MAIN_ONLY), so `jira-creator` makes the same calls. The message names `jira-creator`.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

TOOLS = {"*"}  # the server's name is the workspace's choice, so the tool is matched by its last segment
MAIN_ONLY = True

WRITES = {
    "createJiraIssue",
    "editJiraIssue",
    "addCommentToJiraIssue",
    "addOrEditJiraIssueComment",
    "addWorklogToJiraIssue",
    "createIssueLink",
    "transitionJiraIssue",
    "executeWrite",
}


def check(event: dict) -> str | None:
    tool = event.get("tool_name")
    if not isinstance(tool, str) or not tool.startswith("mcp__"):
        return None
    server, _, name = tool[len("mcp__"):].partition("__")
    if not server or name not in WRITES:
        return None
    return (
        f"`{name}` writes to Jira, and a Jira write goes through `jira-creator`, not a direct call in the main "
        f"session: it converts the markdown, checks the signed-in account and what it wrote, and keeps the request "
        f"out of your context. Spawn `jira-creator` with the issue key (or the filing plan) and the change asked. "
        f"Reads (`getJiraIssue`, `searchJiraIssuesUsingJql`) are unaffected."
    )
