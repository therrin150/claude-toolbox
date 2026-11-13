"""The jira-reader write gate: `jira-reader` writes one file, the JSON a `save` request names, and only in the
session's scratchpad.

Its `Write` tool exists for that one job (procedure.md, "Save"). The agent is told the path in its prompt, but a prompt
is not a boundary, so this gate denies a `Write` by `jira-reader` to anything but a `.json` file under the
`scratchpad_dir` the hook input carries.

Every other caller is untouched: the main session's and other agents' writes are other gates' business.
"""

from __future__ import annotations

import os
import sys

sys.dont_write_bytecode = True

TOOLS = {"Write"}
MAIN_ONLY = False
AGENT = "jira-reader"


def check(event: dict) -> str | None:
    if event.get("agent_type") != AGENT:
        return None
    scratch = event.get("scratchpad_dir")
    path = (event.get("tool_input") or {}).get("file_path")
    if not isinstance(path, str) or not path:
        return "`jira-reader` writes only the JSON file its `save` path names; this write has no path."
    if not isinstance(scratch, str) or not scratch:
        return "`jira-reader` may write only in the session's scratchpad, and this session has none."
    real, root = os.path.realpath(path), os.path.realpath(scratch)
    if not path.endswith(".json") or os.path.commonpath([real, root]) != root:
        return (f"`jira-reader` writes only a `.json` file in the session's scratchpad ({scratch}); {path} is not one. "
                "Report `found: blocked` if the prompt's `save` path is somewhere else.")
    return None
