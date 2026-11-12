"""Report contracts: what an agent's report must carry and must not, read from the agent's own report template.

An agent's template, `<toolbox>/agents/<agent>/report.md`, may hold one fenced block tagged `contract`. Its lines:

    require <field>: <regex>        some line of the report matches (a required field)
    forbid <what>: <regex>          no line of the report matches
    limit <n> <what>: <regex>       at most n lines of the report match (a raw dump over a size)

Blank lines and lines starting with `#` are comments. A regex is Python's, matched per line with `re.search`. A template
with no contract block (or none at all) leaves the agent's report unchecked.

The report contract gates are the callers, each loading this file by path and calling `check_report`:
`hooks/gates/pre_tool_use/report_contract_gate.py` for an agent that hands its report back through `SubagentHandback`,
and `hooks/gates/subagent_stop/report_contract_gate.py` for one that returns it as text.

- check_report(agent_type, report, toolbox) is the denial message for a report that breaks its agent's contract, or
  None.
- handed_back(transcript_path) is whether the subagent's transcript holds a `SubagentHandback` call, None if it can't
  be read.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True

BLOCK = re.compile(r"^```contract[ \t]*\n(.*?)^```[ \t]*$", re.DOTALL | re.MULTILINE)
RULE = re.compile(r"^(require|forbid|limit[ \t]+(\d+))[ \t]+([^:]+?)[ \t]*:[ \t]*(.+?)[ \t]*$")
AGENT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
HANDBACK_CALL = re.compile(r'"name"\s*:\s*"SubagentHandback"')
SNIPPET = 80


class Rule:
    """One contract line: kind (require, forbid or limit), what it is called, its compiled regex, and a limit's n.
    Plain classes, not dataclasses: the gates load this file by path, without registering it in sys.modules."""

    def __init__(self, kind: str, what: str, pattern: re.Pattern, limit: int = 0) -> None:
        self.kind, self.what, self.pattern, self.limit = kind, what, pattern, limit


class Contract:
    def __init__(self, agent: str, template: Path) -> None:
        self.agent, self.template, self.rules = agent, template, []


def parse(agent: str, template: Path, text: str) -> Contract | None:
    """The contract in a template's text, or None when it declares none. A line that isn't a rule or whose regex
    doesn't compile raises ValueError, so a broken template is reported (the dispatcher skips the gate) and never
    silently unchecked."""
    blocks = BLOCK.findall(text)
    if not blocks:
        return None
    contract = Contract(agent, template)
    for block in blocks:
        for number, line in enumerate(block.splitlines(), 1):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            match = RULE.match(line.strip())
            if not match:
                raise ValueError(f"{template}: contract line {number} isn't `require|forbid|limit <n> <what>: <regex>`")
            word, count, what, regex = match.groups()
            try:
                pattern = re.compile(regex)
            except re.error as error:
                raise ValueError(f"{template}: contract line {number}: bad regex: {error}") from error
            contract.rules.append(Rule(word.split()[0], what, pattern, int(count or 0)))
    return contract


def load(agent_type: str, toolbox: Path) -> Contract | None:
    """The contract of the named agent, or None: an empty or odd agent name (the harness's own agents, a plugin's
    `plugin:agent`), no template, or a template without a contract."""
    if not isinstance(agent_type, str) or not AGENT_NAME.match(agent_type):
        return None
    template = toolbox / "agents" / agent_type / "report.md"
    try:
        text = template.read_text(encoding="utf-8")
    except OSError:
        return None
    return parse(agent_type, template, text)


def problems(contract: Contract, report: str) -> list:
    """What the report gets wrong, one line each, naming the field or the offending text."""
    lines = report.splitlines()
    found = []
    for rule in contract.rules:
        hits = [line for line in lines if rule.pattern.search(line)]
        if rule.kind == "require" and not hits:
            found.append(f"missing {rule.what}")
        elif rule.kind == "forbid" and hits:
            found.append(f"{rule.what}: {snippet(rule.pattern.search(hits[0]).group(0).strip() or hits[0])}")
        elif rule.kind == "limit" and len(hits) > rule.limit:
            found.append(f"{rule.what}: {len(hits)} lines, at most {rule.limit}")
    return found


def snippet(text: str) -> str:
    text = " ".join(text.split())
    return "`" + (text if len(text) <= SNIPPET else text[:SNIPPET - 1] + "…") + "`"


def check_report(agent_type: str, report: object, toolbox: Path) -> str | None:
    """The denial message for a report that breaks the agent's contract, or None (no contract, or it holds)."""
    if not isinstance(report, str):
        return None
    contract = load(agent_type, toolbox)
    if contract is None:
        return None
    found = problems(contract, report)
    if not found:
        return None
    listed = "\n".join(f"- {item}" for item in found)
    return (
        f"Your report breaks the `{agent_type}` report contract:\n{listed}\n"
        f"Fix the report to the template in {contract.template} (no log paths, raw output or dumps; only the fields "
        f"it lists) and hand it back again."
    )


def handed_back(transcript_path: object) -> bool | None:
    """Whether the subagent's transcript holds a `SubagentHandback` call (so its report went through that tool, and
    the closing text isn't the report); None when the transcript can't be read."""
    if not isinstance(transcript_path, str) or not transcript_path:
        return None
    try:
        text = Path(transcript_path).expanduser().read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return bool(HANDBACK_CALL.search(text))
