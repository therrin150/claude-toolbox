"""Extension docs: glue that replaces or adds to a generic skill's sections. See references/extensions.md.

parse() splits an extension doc into its sections, each led by a `## Replaces <skill>'s "<heading>"` or
`## Adds to <skill>'s "<heading>"` line. The doctor check uses it to confirm every named heading still exists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SECTION = re.compile(r"""^## (?P<verb>Replaces|Adds to) (?P<skill>[a-z0-9-]+)'s "(?P<heading>#{1,6} [^"]+)"\s*$""")
LEVEL_TWO = re.compile(r"^## ")
FENCE = re.compile(r"^\s*(```|~~~)")


@dataclass(frozen=True)
class ExtensionSection:
    verb: str  # "Replaces" or "Adds to"
    skill: str
    heading: str  # the skill's heading as written, with its leading #s
    body: str
    line: int  # 1-based line of the section's own heading


def parse(text: str) -> tuple[list[ExtensionSection], list[str]]:
    """The doc's sections, and a problem line for each `## ` heading that isn't a Replaces or Adds to."""
    sections: list[ExtensionSection] = []
    problems: list[str] = []
    current: dict | None = None
    body: list[str] = []
    fenced = False

    def close():
        if current:
            sections.append(ExtensionSection(**current, body="\n".join(body).strip()))

    for number, line in enumerate(text.splitlines(), start=1):
        if FENCE.match(line):
            fenced = not fenced
        if not fenced and LEVEL_TWO.match(line):
            close()
            body = []
            match = SECTION.match(line)
            if match:
                current = {**match.groupdict(), "line": number}
            else:
                current = None
                problems.append(f"line {number}: {line.strip()!r} is not a Replaces or Adds to heading")
            continue
        if current:
            body.append(line)
    close()
    return sections, problems
