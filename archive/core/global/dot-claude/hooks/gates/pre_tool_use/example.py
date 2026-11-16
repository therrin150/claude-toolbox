"""The no-op example gate: copy it to write a PreToolUse gate. run_gates.py describes the contract.

An empty TOOLS matches no tool, so this file never runs `check`. A real gate lists the tools it looks at, and its
denial message names the agent or skill to use instead, so the session doesn't hunt for a workaround.
"""

TOOLS: set[str] = set()  # e.g. {"Bash"}
MAIN_ONLY = True  # skip calls from a subagent (the input has an agent_type)


def check(event: dict) -> str | None:
    """Return a denial message, or None to let the call go ahead. event is the hook input (tool_name, tool_input, ...)."""
    return None
