"""YAML frontmatter in markdown notes: reading it, and changing fields without disturbing the rest of the note.

A note with frontmatter starts with a `---` line, then YAML, then a closing `---` line, then the body. Updates are
line-based: a field's line and its indented or `- ` continuation lines are replaced, or the field is appended, so every
other field keeps the formatting it had. Every write checks that YAML can read the result back, and replaces the file
atomically, so a crash never leaves a half-written note.
"""

from __future__ import annotations

import datetime
import os
import re
import tempfile
from pathlib import Path

import yaml

FENCE = "---\n"
CLOSE = re.compile(r"^---[ \t]*(?:\n|\Z)", re.M)


class FrontmatterError(Exception):
    """A note has no frontmatter, or frontmatter YAML can't read."""


def split(text: str, where: Path | str = "note") -> tuple[str, str]:
    """(frontmatter text, body) of a note's text. The frontmatter text ends with a newline, or is empty."""
    if not text.startswith(FENCE):
        raise FrontmatterError(f"{where}: no frontmatter")
    end = CLOSE.search(text, len(FENCE))
    if end is None:
        raise FrontmatterError(f"{where}: frontmatter never closes")
    return text[len(FENCE):end.start()], text[end.end():]


def parse(text: str, where: Path | str = "note") -> tuple[dict, str, str]:
    """(fields, frontmatter text, body) of a note's text."""
    fm, body = split(text, where)
    try:
        fields = yaml.safe_load(fm) if fm.strip() else {}
    except yaml.YAMLError as error:
        raise FrontmatterError(f"{where}: frontmatter isn't valid YAML: {error}") from None
    if not isinstance(fields, dict):
        raise FrontmatterError(f"{where}: frontmatter isn't a mapping")
    return fields, fm, body


def read(path: Path) -> tuple[dict, str]:
    """(fields, body) of the note at `path`."""
    fields, _, body = parse(Path(path).read_text(encoding="utf-8"), path)
    return fields, body


def fields(path: Path) -> dict:
    return read(path)[0]


def compose(fm: str, body: str) -> str:
    if fm and not fm.endswith("\n"):
        fm += "\n"
    return FENCE + fm + FENCE + body


# ---------------------------------------------------------------------------------------------------------------------
# Rendering


def render_value(value) -> str:
    """What follows `field:` on a frontmatter line: empty for no value, ` <scalar>`, ` []`, or indented block lines.

    Strings are quoted only when YAML needs it, so paths and URLs stay bare while `true`, `12` or `a: b` are quoted.
    Lists of scalars become block lists; mappings and lists of mappings become indented block YAML.
    """
    if value is None or value == "":
        return ""
    if isinstance(value, (datetime.date, datetime.datetime)):
        return " " + value.isoformat()
    if isinstance(value, (list, tuple)):
        if not value:
            return " []"
        if all(not isinstance(item, (dict, list, tuple)) for item in value):
            return "".join(f"\n  -{render_value(item) or ' null'}" for item in value)
        return "\n" + _indent(yaml.safe_dump(list(value), default_flow_style=False, sort_keys=False,
                                             allow_unicode=True, width=10**9))
    if isinstance(value, dict):
        if not value:
            return " {}"
        return "\n" + _indent(yaml.safe_dump(value, default_flow_style=False, sort_keys=False, allow_unicode=True,
                                             width=10**9))
    dumped = yaml.safe_dump(value, width=10**9, allow_unicode=True)
    return " " + dumped.removesuffix("\n...\n").strip()


def _indent(text: str) -> str:
    return "\n".join("  " + line for line in text.rstrip("\n").split("\n"))


def render(values: dict) -> str:
    """Frontmatter text for `values`, in their order."""
    return "".join(f"{field}:{render_value(value)}\n" for field, value in values.items())


# ---------------------------------------------------------------------------------------------------------------------
# Updating


def replace_field(fm: str, field: str, rendered: str) -> str:
    """Replace `field:` and its indented or `- ` continuation lines with `field:<rendered>`, or append it."""
    lines = fm.split("\n")
    out, i, found = [], 0, False
    while i < len(lines):
        if re.match(rf"^{re.escape(field)}:(\s|$)", lines[i]):
            found = True
            out.append(f"{field}:{rendered}")
            i += 1
            while i < len(lines) and lines[i] and (lines[i][0] in " \t" or lines[i].startswith("- ")):
                i += 1
            continue
        out.append(lines[i])
        i += 1
    if not found:
        while out and out[-1] == "":
            out.pop()
        out += [f"{field}:{rendered}", ""]
    return "\n".join(out)


def updated(text: str, values: dict, where: Path | str = "note") -> str:
    """A note's text with `values` (field -> Python value) written into its frontmatter."""
    _, fm, body = parse(text, where)
    for field, value in values.items():
        fm = replace_field(fm, field, render_value(value))
    check(fm, where)
    return compose(fm, body)


def check(fm: str, where: Path | str = "note") -> None:
    """Never write frontmatter YAML can't read back."""
    try:
        loaded = yaml.safe_load(fm) if fm.strip() else {}
    except yaml.YAMLError as error:
        raise FrontmatterError(f"{where}: refusing to write frontmatter YAML can't read: {error}") from None
    if not isinstance(loaded, dict):
        raise FrontmatterError(f"{where}: refusing to write frontmatter that isn't a mapping")


def update(path: Path, values: dict, dry_run: bool = False) -> str:
    """Write `values` into the frontmatter of the note at `path`. Returns the new text."""
    path = Path(path)
    text = updated(path.read_text(encoding="utf-8"), values, path)
    if not dry_run:
        write_atomic(path, text)
    return text


def write_atomic(path: Path, text: str) -> None:
    """Write `text` to `path` through a temporary file beside it, so readers see the old note or the new one."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
