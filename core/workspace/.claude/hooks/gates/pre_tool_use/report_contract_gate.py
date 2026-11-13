"""The report contract gate, for a subagent that hands its report back: the report must keep its template's contract.

A subagent that runs with the `SubagentHandback` tool (auto mode, Claude Code v2.1.271 or later) delivers its report
through that tool, and the report is the call's `message`. The gate reads the agent's contract from its report template
(`.claude/toolbox/agents/<agent>/report.md`, the block tagged `contract`; `lib/report_contract.py` describes it) and
denies a report that misses a required field or carries something forbidden, such as a log path or a raw stack dump,
with a message naming what is wrong. Claude Code tells the agent its report wasn't delivered, and the agent hands back a
corrected one (up to three times).

An agent whose template declares no contract, and any agent_type with no template (the harness's own agents, a
plugin's), goes through unchanged. An agent that returns its report as text is checked at `SubagentStop` instead
(`gates/subagent_stop/report_contract_gate.py`).

The call comes from the subagent, so the gate is not MAIN_ONLY.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True

TOOLS = {"SubagentHandback"}
MAIN_ONLY = False

_TOOLBOX = Path(os.path.abspath(__file__)).parents[3] / "toolbox"
_spec = importlib.util.spec_from_file_location("_gate_report_contract", _TOOLBOX / "lib" / "report_contract.py")
contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(contract)


def check(event: dict) -> str | None:
    message = (event.get("tool_input") or {}).get("message")
    return contract.check_report(event.get("agent_type"), message, _TOOLBOX)
