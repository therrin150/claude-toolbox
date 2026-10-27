"""Extension docs: a project's steps hooked into a generic skill. See .claude/extensions/README.md.

An extension doc lives in the workspace's `.claude/extensions/` folder. Its frontmatter says `type: extension` and
`extends: <skill>`; each `##` section is `## <add|replace|skip> <hook id>`, the prose under it the instruction (for
`skip`, the reason). A skill declares its hook ids in an `## Extension Points` table (id, when it runs, its default,
the verbs it allows), so its headings can be renamed freely.

- parse() reads a doc's frontmatter and sections; points() reads a skill's Extension Points table.
- docs() lists the folder's docs (never its README); discover() picks those extending one skill, minus disabled ones.
- The doctor check validates every doc against the table of the skill it extends.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

FOLDER = Path(".claude/extensions")
README = "README.md"
VERBS = ("add", "replace", "skip")
SECTION = re.compile(r"^## (add|replace|skip) ([a-z0-9-]+)$")
SKILL_NAME = re.compile(r"^[a-z0-9-]+$")
POINTS_HEADING = "## Extension Points"
POINTS_COLUMNS = ["Id", "Runs", "Default", "Verbs"]
POINT_ID = re.compile(r"^`([a-z0-9-]+)`$")
LEVEL_TWO = re.compile(r"^## ")
FENCE = re.compile(r"^\s*(```|~~~)")
CELL = re.compile(r"(?<!\\)\|")


@dataclass(frozen=True)
class Section:
    verb: str  # add, replace or skip
    hook: str  # the skill's hook id
    body: str  # the instruction, or for skip the reason
    line: int  # 1-based line of the section's own heading


@dataclass(frozen=True)
class Doc:
    path: Path
    extends: str | None  # None when the frontmatter doesn't name one skill
    sections: list[Section]
    problems: list[str]  # each led by its line, when it has one

    @property
    def name(self) -> str:
        return self.path.name


@dataclass(frozen=True)
class Point:
    id: str
    runs: str
    default: str
    verbs: tuple[str, ...]


def _lines_outside_fences(text: str):
    """(1-based line number, line, fenced) for each line; a fence line itself counts as fenced."""
    fenced = False
    for number, line in enumerate(text.splitlines(), start=1):
        if FENCE.match(line):
            fenced = not fenced
            yield number, line, True
            continue
        yield number, line, fenced


def _frontmatter(text: str) -> tuple[dict | None, int, str | None]:
    """(fields, the line the body starts on, a problem). Fields are None when there's no readable frontmatter."""
    lines = text.splitlines()
    if not lines or lines[0].rstrip() != "---":
        return None, 1, "no frontmatter; start the doc with `type: extension` and `extends: <skill>` between --- lines"
    for index, line in enumerate(lines[1:], start=1):
        if line.rstrip() == "---":
            try:
                fields = yaml.safe_load("\n".join(lines[1:index])) or {}
            except yaml.YAMLError as error:
                return None, index + 2, f"frontmatter isn't YAML: {error}".splitlines()[0]
            if not isinstance(fields, dict):
                return None, index + 2, "frontmatter isn't a mapping"
            return fields, index + 2, None
    return None, 1, "frontmatter never closes"


def parse(text: str, path: Path | str = "extension.md") -> Doc:
    """A doc's skill and sections, with a problem line for anything malformed."""
    path = Path(path)
    problems: list[str] = []
    fields, start, problem = _frontmatter(text)
    extends = None
    if problem:
        problems.append(problem)
    else:
        if fields.get("type") != "extension":
            problems.append(f"frontmatter: type must be extension; got {fields.get('type')!r}")
        value = fields.get("extends")
        if value is None:
            problems.append("frontmatter: no extends; name the one skill this doc extends")
        elif not isinstance(value, str) or not SKILL_NAME.match(value):
            problems.append(f"frontmatter: extends must be one skill's name, like work-task; got {value!r}")
        elif fields.get("type") == "extension":  # a doc of another type is never read as an extension
            extends = value

    sections: list[Section] = []
    current: dict | None = None
    body: list[str] = []
    seen: dict[tuple[str, str], int] = {}

    def close():
        if not current:
            return
        text_ = "\n".join(body).strip()
        where = f"line {current['line']}"
        if not text_:
            what = "the reason" if current["verb"] == "skip" else "what to do"
            problems.append(f"{where}: `## {current['verb']} {current['hook']}` is empty; say {what}")
        key = (current["verb"], current["hook"])
        if key in seen:
            problems.append(f"{where}: `## {current['verb']} {current['hook']}` repeats line {seen[key]}; "
                            "put both in one section")
        else:
            seen[key] = current["line"]
        sections.append(Section(**current, body=text_))

    for number, line, fenced in _lines_outside_fences(text):
        if number < start:
            continue
        if not fenced and LEVEL_TWO.match(line):
            close()
            body = []
            match = SECTION.match(line.rstrip())
            if match:
                current = {"verb": match.group(1), "hook": match.group(2), "line": number}
            else:
                current = None
                problems.append(f"line {number}: {line.strip()!r} isn't `## <add|replace|skip> <hook id>`")
            continue
        if current:
            body.append(line)
    close()
    return Doc(path=path, extends=extends, sections=sections, problems=problems)


def load(path: Path) -> Doc:
    return parse(path.read_text(encoding="utf-8"), path)


def points(text: str) -> tuple[list[Point], list[str]]:
    """A skill's hook ids from its `## Extension Points` table, and a problem line for anything malformed.

    No Extension Points section gives ([], []): the skill supports no extensions.
    """
    found: list[Point] = []
    problems: list[str] = []
    inside = header = False
    for number, line, fenced in _lines_outside_fences(text):
        if fenced:
            continue
        if LEVEL_TWO.match(line):
            if inside:
                break
            inside = line.rstrip() == POINTS_HEADING
            continue
        if not inside:
            continue
        stripped = line.strip()
        if not stripped.startswith("|"):
            if header and found:
                break  # the table ended; prose and ### defaults follow
            continue
        cells = [cell.strip() for cell in CELL.split(stripped.strip("|"))]
        if not header:
            header = True
            if cells != POINTS_COLUMNS:
                problems.append(f"line {number}: the table's columns must be {' | '.join(POINTS_COLUMNS)}")
                break
            continue
        if all(set(cell) <= set("-: ") for cell in cells):
            continue  # the separator row
        if len(cells) != len(POINTS_COLUMNS):
            problems.append(f"line {number}: a row has {len(cells)} cells, not {len(POINTS_COLUMNS)}")
            continue
        match = POINT_ID.match(cells[0])
        verbs = tuple(verb.strip() for verb in cells[3].split(","))
        if not match:
            problems.append(f"line {number}: {cells[0]!r} isn't a backticked id of lowercase words joined by -")
        elif any(verb not in VERBS for verb in verbs):
            problems.append(f"line {number}: verbs must be from {', '.join(VERBS)}; got {cells[3]!r}")
        elif match.group(1) in {point.id for point in found}:
            problems.append(f"line {number}: hook {match.group(1)!r} is declared twice")
        else:
            found.append(Point(id=match.group(1), runs=cells[1], default=cells[2], verbs=verbs))
    if inside and not found and not problems:
        problems.append(f"{POINTS_HEADING} has no table of hooks")
    return found, problems


def docs(root: Path) -> list[Path]:
    """Every extension doc in a workspace's `.claude/extensions/`, in name order; never the README."""
    folder = Path(root) / FOLDER
    if not folder.is_dir():
        return []
    return sorted(path for path in folder.glob("*.md") if path.name != README and path.is_file())


def doc_name(entry: str) -> str:
    """A `extensions.disabled` entry as a file name: `sonar-scan` and `sonar-scan.md` are the same doc."""
    return entry if entry.endswith(".md") else entry + ".md"


def discover(root: Path, skill: str, disabled: list[str] = ()) -> list[Path]:
    """The docs extending a skill, in name order, minus those `extensions.disabled` names."""
    off = {doc_name(entry) for entry in disabled}
    return [path for path in docs(root) if path.name not in off and load(path).extends == skill]
