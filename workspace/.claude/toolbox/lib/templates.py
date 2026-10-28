"""Templated files: a file in the toolbox's workspace folder whose name ends `.tmpl` is rendered into each workspace,
to the same path without the suffix, instead of being linked.

A placeholder is a dotted config key in double braces, `{{ tracker.jira.mcp_server }}`, replaced by that key's value
in the workspace's `.claude/toolbox.local.yml` (defaults included). Only single values fill a placeholder: text, a
number or a boolean (`true` / `false`). There is no other syntax: no defaults, filters or conditions. A key the config
doesn't have, a value that is a list, a map or unset, and anything else in double braces are errors, and the installer
reports them as problems rather than writing the file.
"""

from __future__ import annotations

import re

SUFFIX = ".tmpl"
PLACEHOLDER = re.compile(r"\{\{(.*?)\}\}")
KEY = re.compile(r"^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*$")


class TemplateError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def rendered_name(name: str) -> str:
    """A template's file name without its suffix."""
    return name[: -len(SUFFIX)]


def _text(value) -> str | None:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)):
        return str(value)
    return None


def render(text: str, config) -> str:
    """The template text with every placeholder filled from the config. TemplateError lists every bad placeholder."""
    errors = []

    def fill(match: re.Match) -> str:
        key = match.group(1).strip()
        if not KEY.match(key):
            errors.append(f"{match.group(0)!r} isn't a placeholder: put one dotted config key in double braces")
            return match.group(0)
        try:
            value = config.get(key)
        except KeyError:
            errors.append(f"unknown config key {key!r}")
            return match.group(0)
        filled = _text(value)
        if filled is None:
            what = "unset" if value is None else f"a {type(value).__name__}, not a single value"
            errors.append(f"config key {key!r} is {what}")
            return match.group(0)
        return filled

    result = PLACEHOLDER.sub(fill, text)
    if errors:
        raise TemplateError(list(dict.fromkeys(errors)))
    return result
