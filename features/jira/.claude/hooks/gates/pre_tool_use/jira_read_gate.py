"""The Jira read gate: the main session never reads a Jira issue or runs a JQL search by calling the MCP server directly.

The server's response carries every field of every issue, avatars and a rendered copy of the description, and all of it
lands in the main session's context. `jira-reader` takes only the fields asked for and returns them, one compact line per
issue (or, in save mode, as a file for `tasks.py`). Denied in the main session, whatever the server is called
(`mcp__<server>__<tool>`):

  getJiraIssue, searchJiraIssuesUsingJql

Allowed: every other tool of the server (`getTransitionsForJiraIssue`, `atlassianUserInfo`, `discover`, `executeRead`,
...), which return little, and the Jira write gate's own list is its business.

A subagent is never denied (MAIN_ONLY), so `jira-reader` and `jira-creator` make the same calls. The message names
`jira-reader` and `.claude/toolbox/references/jira-reads.md`, which says how to call it.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

TOOLS = {"*"}  # the server's name is the workspace's choice, so the tool is matched by its last segment
MAIN_ONLY = True

READS = {"getJiraIssue", "searchJiraIssuesUsingJql"}


def check(event: dict) -> str | None:
    tool = event.get("tool_name")
    if not isinstance(tool, str) or not tool.startswith("mcp__"):
        return None
    server, _, name = tool[len("mcp__"):].partition("__")
    if not server or name not in READS:
        return None
    return (
        f"`{name}` reads Jira, and a Jira read goes through the `jira-reader` agent, not a direct call in the main "
        f"session: the server's response carries every field of every issue, and the agent returns only the fields "
        f"you ask for. Spawn `jira-reader` with `root` (the workspace's absolute path), `issues` or `jql`, and the "
        f"`fields` you need; `.claude/toolbox/references/jira-reads.md` has the prompt and the field sets, and save "
        f"mode (`save`, `model: sonnet`) for a file `tasks.py` reads."
    )
