"""The report contract gate, for a subagent that returns its report as text: it must keep its template's contract.

When a subagent stops, the gate checks its closing text (`last_assistant_message`) against the contract in its report
template (`.claude/toolbox/agents/<agent>/report.md`, the block tagged `contract`; `lib/report_contract.py` describes
it). A report that misses a required field or carries something forbidden, such as a log path or a raw stack dump, is
blocked with a message naming what is wrong, which keeps the agent running so it fixes the report before the parent
gets it.

Skipped, so the report goes through unchanged:

- an agent whose template declares no contract, and any agent_type with no template (the harness's own agents);
- an agent that handed its report back through `SubagentHandback` (its transcript holds the call): the report is that
  call's message, checked by `gates/pre_tool_use/report_contract_gate.py`, and the closing text is not the report. A
  transcript that can't be read counts as unknown, and the gate stays out of the way;
- a stop that continues from an earlier block (`stop_hook_active`), so a report that can't be fixed never traps the
  agent in a loop.

TOOLS is `*` because SubagentStop has no tool. The call comes from the subagent, so the gate is not MAIN_ONLY.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True

TOOLS = {"*"}
MAIN_ONLY = False

_TOOLBOX = Path(os.path.abspath(__file__)).parents[3] / "toolbox"
_spec = importlib.util.spec_from_file_location("_gate_report_contract", _TOOLBOX / "lib" / "report_contract.py")
contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(contract)


def check(event: dict) -> str | None:
    if event.get("stop_hook_active"):
        return None
    agent_type = event.get("agent_type")
    if contract.load(agent_type, _TOOLBOX) is None:
        return None
    if contract.handed_back(event.get("agent_transcript_path")) is not False:
        return None
    return contract.check_report(agent_type, event.get("last_assistant_message"), _TOOLBOX)
