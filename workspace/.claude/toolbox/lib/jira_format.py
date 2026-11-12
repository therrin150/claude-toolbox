"""A discussion draft's markdown as the Atlassian Document Format (ADF) Jira takes, so `jira-creator` sends what a script
produced instead of converting prose itself.

`convert_draft(text, ac_field)` reads one draft (or a task record made from one) and returns the issue's fields:

- `description`: an ADF document of the `## Description` section, then (a defect) its `## Steps to Reproduce`,
  `## Expected` and `## Actual` under level-2 headings. With no acceptance-criteria field (`ac_field` empty), the AC
  follow under a level-2 `Acceptance Criteria` heading.
- `acceptance_criteria`: `{"field": <id>, "value": <ADF>}` when `ac_field` is set and the draft has AC, else None.
- `testing_notes_comment`: the `## Testing Notes` as an ADF comment body (a bold `Testing Notes` line, then the notes),
  already serialized as the JSON string the comment tool takes, else None.
- `warnings`: things worth saying that aren't errors (a story with no AC).

The scenario AC house style (a bold scenario name, then one GIVEN / WHEN / THEN / AND / OR clause per line) becomes a
bold name paragraph and a paragraph of the clauses joined by hard breaks. Supported markdown: paragraphs, `###`-`######`
headings, bullet and numbered lists (nested), fenced code blocks, tables, `> [!info|note|warning|tip|success|danger|error]`
callouts (panels hold paragraphs and lists only), horizontal rules, and inline code, bold, italic, strikethrough and
links. Anything else raises `Unsupported` naming the construct and its line, so the caller files nothing: raw HTML,
images, wikilinks, plain blockquotes, a callout title, a level-1 heading, an unclosed code span or code block, a table
row with the wrong number of cells, an unknown `##` section, or AC text that isn't a scenario.

Line numbers in errors are the draft file's. Vault scaffolding is dropped: the frontmatter, the `# Title` and `Plan:`
lines, HTML comments (outside code blocks), trailing rules and a record's `## Log`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import frontmatter

SECTIONS = {"Description", "Acceptance Criteria", "Testing Notes", "Steps to Reproduce", "Expected", "Actual", "Log"}
DEFECT_SECTIONS = ("Steps to Reproduce", "Expected", "Actual")
PANELS = {"info": "info", "note": "note", "warning": "warning", "tip": "success", "success": "success",
          "danger": "error", "error": "error"}
CLAUSE = re.compile(r"^(GIVEN|WHEN|THEN|AND|OR)\b ?(.*)$")
SCENARIO = re.compile(r"^\*\*(Scenario: .+?)\*\*\s*$")
FENCE = re.compile(r"^\s*```\s*([\w+#.-]*)\s*$")
FENCE_CLOSE = re.compile(r"^\s*```\s*$")
HEADING = re.compile(r"^(#{1,6}) +(.+?)\s*#*\s*$")
RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
LIST_ITEM = re.compile(r"^( *)([-*+]|\d+[.)]) +(.*)$")
TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
HTML_LINE = re.compile(r"^\s*</?[A-Za-z!]")
CALLOUT = re.compile(r"^>\s*\[!(\w+)\](.*)$")


class Unsupported(Exception):
    """Markdown the converter can't turn into ADF faithfully: `construct` says what, `line` where (the file's, or 0)."""

    def __init__(self, construct: str, line: int = 0, detail: str = ""):
        self.construct, self.line, self.detail = construct, line, detail
        where = f"line {line}: " if line else ""
        super().__init__(f"{where}unsupported markdown: {construct}" + (f" ({detail})" if detail else ""))


# ---------------------------------------------------------------------------------------------------------------------
# Inline text

INLINE = re.compile(r"""
    (?P<code>(?<!`)(?P<ticks>`+)(?!`)(?P<ctext>.+?)(?<!`)(?P=ticks)(?!`))
  | (?P<image>!\[[^\]]*\]\([^)]*\))
  | (?P<wiki>\[\[[^\]]+\]\])
  | (?P<link>\[(?P<ltext>[^\]]+)\]\((?P<lurl>[^)\s]+)\))
  | (?P<bold>\*\*(?P<btext>.+?)\*\*)
  | (?P<strike>~~(?P<stext>.+?)~~)
  | (?P<em>(?<![\w*])\*(?P<etext>[^\s*](?:.*?[^\s*])?)\*(?![\w*]))
  | (?P<em2>(?<![\w_])_(?P<e2text>[^\s_](?:.*?[^\s_])?)_(?![\w_]))
  | (?P<html></?[A-Za-z][^>`]*>)
""", re.X)


def text_node(text: str, marks: list[dict]) -> dict:
    node = {"type": "text", "text": text}
    if marks:
        node["marks"] = [dict(mark) for mark in marks]
    return node


def inline(text: str, line: int, marks: list[dict] | None = None) -> list[dict]:
    """ADF text nodes for a run of inline markdown. Adjacent runs with the same marks are merged."""
    marks = marks or []
    nodes: list[dict] = []

    def plain(chunk: str) -> None:
        for token, construct in (("`", "unclosed code span"), ("**", "unclosed bold"), ("~~", "unclosed strikethrough")):
            if token in chunk:
                raise Unsupported(construct, line, chunk.strip()[:60])
        if chunk:
            nodes.append(text_node(chunk, marks))

    pos = 0
    for match in INLINE.finditer(text):
        plain(text[pos:match.start()])
        pos = match.end()
        kind = match.lastgroup
        if kind == "code":
            # ADF's code mark excludes the other marks except a link.
            keep = [mark for mark in marks if mark["type"] == "link"]
            code = match.group("ctext")
            if len(match.group("ticks")) > 1 and code.startswith(" ") and code.endswith(" ") and code.strip():
                code = code[1:-1]
            nodes.append(text_node(code, [*keep, {"type": "code"}]))
        elif kind == "image":
            raise Unsupported("image", line, match.group())
        elif kind == "wiki":
            raise Unsupported("Obsidian wikilink", line, match.group())
        elif kind == "html":
            raise Unsupported("inline HTML or an unbackticked <...>", line, match.group())
        elif kind == "link":
            nodes.extend(inline(match.group("ltext"), line, [*marks, {"type": "link", "attrs": {"href": match.group("lurl")}}]))
        elif kind == "bold":
            nodes.extend(inline(match.group("btext"), line, [*marks, {"type": "strong"}]))
        elif kind == "strike":
            nodes.extend(inline(match.group("stext"), line, [*marks, {"type": "strike"}]))
        else:
            nodes.extend(inline(match.group("etext") or match.group("e2text"), line, [*marks, {"type": "em"}]))
    plain(text[pos:])

    merged: list[dict] = []
    for node in nodes:
        if merged and merged[-1].get("marks") == node.get("marks"):
            merged[-1]["text"] += node["text"]
        else:
            merged.append(node)
    return merged


def paragraph(text: str, line: int) -> dict:
    return {"type": "paragraph", "content": inline(text, line)}


# ---------------------------------------------------------------------------------------------------------------------
# Blocks


@dataclass
class Line:
    number: int
    text: str


def starts_block(text: str, nxt: str | None) -> bool:
    """Whether a line begins something other than a paragraph, ending the paragraph above it."""
    return bool(FENCE.match(text) or HEADING.match(text) or LIST_ITEM.match(text) or text.startswith(">")
                or RULE.match(text) or HTML_LINE.match(text)
                or (text.lstrip().startswith("|") and nxt is not None and TABLE_SEPARATOR.match(nxt)))


def split_cells(row: str) -> list[str]:
    """A table row's cells: split on `|` outside backticks, ignoring the outer pipes and `\\|` escapes."""
    row = row.strip()
    cells, current, in_code, i = [], "", False, 0
    while i < len(row):
        char = row[i]
        if char == "\\" and row[i + 1:i + 2] == "|":
            current += "|"
            i += 2
            continue
        if char == "`":
            in_code = not in_code
        if char == "|" and not in_code:
            cells.append(current)
            current = ""
        else:
            current += char
        i += 1
    cells.append(current)
    if row.startswith("|"):
        cells = cells[1:]
    if row.endswith("|") and not row.endswith("\\|") and cells:
        cells = cells[:-1]
    return [cell.strip() for cell in cells]


def parse_list(lines: list[Line], start: int) -> tuple[dict, int]:
    """The list starting at lines[start], as an ADF list node, and the index after it."""
    first = LIST_ITEM.match(lines[start].text)
    base = len(first.group(1))
    ordered = first.group(2)[0].isdigit()
    items: list[dict] = []
    i = start
    while i < len(lines):
        match = LIST_ITEM.match(lines[i].text)
        if not match or len(match.group(1)) != base or match.group(2)[0].isdigit() != ordered:
            break
        number, parts, nested = lines[i].number, [match.group(3).strip()], []
        i += 1
        while i < len(lines):
            text = lines[i].text
            if not text.strip():
                # A blank line continues the list only when the next line is indented or another item.
                j = i
                while j < len(lines) and not lines[j].text.strip():
                    j += 1
                if j < len(lines) and (LIST_ITEM.match(lines[j].text) or lines[j].text.startswith("  ")):
                    i = j
                    continue
                break
            indent = len(text) - len(text.lstrip(" "))
            if indent <= base:
                break
            if LIST_ITEM.match(text):
                node, i = parse_list(lines, i)
                nested.append(node)
            elif text.lstrip().startswith(("```", "|", ">", "#")):
                raise Unsupported("code block, table, quote or heading inside a list item", lines[i].number,
                                  text.strip()[:60])
            elif nested:
                raise Unsupported("text after a nested list inside a list item", lines[i].number, text.strip()[:60])
            else:
                parts.append(text.strip())
                i += 1
        content = [paragraph(" ".join(parts), number), *nested]
        items.append({"type": "listItem", "content": content})
    return {"type": "orderedList" if ordered else "bulletList", "content": items}, i


def parse_blocks(lines: list[Line], in_panel: bool = False) -> list[dict]:
    """ADF block nodes for a section's lines."""
    blocks: list[dict] = []
    i = 0
    while i < len(lines):
        text, number = lines[i].text, lines[i].number
        nxt = lines[i + 1].text if i + 1 < len(lines) else None
        if not text.strip():
            i += 1
            continue
        fence = FENCE.match(text)
        if fence:
            if in_panel:
                raise Unsupported("code block inside a callout (panels hold paragraphs and lists only)", number)
            body, i = [], i + 1
            while i < len(lines) and not FENCE_CLOSE.match(lines[i].text):
                body.append(lines[i].text)
                i += 1
            if i >= len(lines):
                raise Unsupported("unclosed code block", number)
            i += 1
            node: dict = {"type": "codeBlock"}
            if fence.group(1):
                node["attrs"] = {"language": fence.group(1)}
            if any(body):
                node["content"] = [{"type": "text", "text": "\n".join(body)}]
            blocks.append(node)
            continue
        if text.startswith(">"):
            callout = CALLOUT.match(text)
            if not callout:
                raise Unsupported("blockquote (only > [!info] style callouts are supported)", number)
            if in_panel:
                raise Unsupported("callout inside a callout", number)
            title = callout.group(2).strip()
            kind = callout.group(1).lower()
            if kind not in PANELS:
                raise Unsupported(f"callout type [!{callout.group(1)}]", number, "one of " + ", ".join(PANELS))
            body = []
            i += 1
            while i < len(lines) and lines[i].text.startswith(">"):
                body.append(Line(lines[i].number, re.sub(r"^> ?", "", lines[i].text)))
                i += 1
            content = parse_blocks(body, in_panel=True)
            if title:  # ADF panels have no title: it becomes a bold first paragraph
                content.insert(0, {"type": "paragraph", "content": [
                    *({**node, "marks": [*node.get("marks", []), {"type": "strong"}]} if "code" not in
                      [m["type"] for m in node.get("marks", [])] else node for node in inline(title, number))]})
            if not content:
                raise Unsupported("empty callout", number)
            blocks.append({"type": "panel", "attrs": {"panelType": PANELS[kind]}, "content": content})
            continue
        heading = HEADING.match(text)
        if heading:
            level = len(heading.group(1))
            if level < 3 or in_panel:
                raise Unsupported(f"level-{level} heading" + (" inside a callout" if in_panel else ""), number,
                                  text.strip()[:60])
            blocks.append({"type": "heading", "attrs": {"level": level}, "content": inline(heading.group(2), number)})
            i += 1
            continue
        if RULE.match(text):
            if in_panel:
                raise Unsupported("horizontal rule inside a callout", number)
            blocks.append({"type": "rule"})
            i += 1
            continue
        if LIST_ITEM.match(text):
            node, i = parse_list(lines, i)
            blocks.append(node)
            continue
        if text.lstrip().startswith("|") and nxt is not None and TABLE_SEPARATOR.match(nxt):
            if in_panel:
                raise Unsupported("table inside a callout (panels hold paragraphs and lists only)", number)
            header = split_cells(text)
            rows, i = [], i + 2
            while i < len(lines) and lines[i].text.strip().startswith("|"):
                cells = split_cells(lines[i].text)
                if len(cells) != len(header):
                    raise Unsupported(f"table row with {len(cells)} cells under a header of {len(header)}",
                                      lines[i].number, lines[i].text.strip()[:60])
                rows.append((lines[i].number, cells))
                i += 1

            def row(kind: str, cells: list[str], at: int) -> dict:
                return {"type": "tableRow", "content": [
                    {"type": kind, "content": [paragraph(cell, at)]} for cell in cells]}

            blocks.append({"type": "table", "attrs": {"isNumberColumnEnabled": False, "layout": "default"},
                           "content": [row("tableHeader", header, number), *(row("tableCell", c, n) for n, c in rows)]})
            continue
        if HTML_LINE.match(text):
            raise Unsupported("HTML", number, text.strip()[:60])
        parts = [text.strip()]
        i += 1
        while i < len(lines) and lines[i].text.strip() and not starts_block(
                lines[i].text, lines[i + 1].text if i + 1 < len(lines) else None):
            parts.append(lines[i].text.strip())
            i += 1
        blocks.append(paragraph(" ".join(parts), number))
    return blocks


def scenario(chunk: list[Line]) -> list[dict]:
    """One scenario's lines: a bold name paragraph and a paragraph of its clauses joined by hard breaks."""
    name = SCENARIO.match(chunk[0].text.strip())
    if not name:
        raise Unsupported("scenario name that isn't `**Scenario: Name**` on its own line", chunk[0].number,
                          chunk[0].text.strip()[:60])
    if "`" in name.group(1) or "*" in name.group(1):
        raise Unsupported("backticks or emphasis in a scenario name", chunk[0].number, name.group(1))
    clauses: list[dict] = []
    for line in chunk[1:]:
        clause = CLAUSE.match(line.text.strip())
        if not clause:
            raise Unsupported("scenario line that doesn't start GIVEN, WHEN, THEN, AND or OR", line.number,
                              line.text.strip()[:60])
        if clauses:
            clauses.append({"type": "hardBreak"})
        clauses.extend(inline(f"{clause.group(1)} {clause.group(2)}".rstrip(), line.number))
    if not clauses:
        raise Unsupported("scenario with no GIVEN / WHEN / THEN clauses", chunk[0].number)
    return [{"type": "paragraph", "content": [text_node(name.group(1), [{"type": "strong"}])]},
            {"type": "paragraph", "content": clauses}]


def parse_acceptance(lines: list[Line]) -> list[dict]:
    """AC as ADF nodes. Blank-line-separated chunks that start `**Scenario` are scenarios (a bold name paragraph and a
    paragraph of clauses); any other chunk (a statement before the scenarios, bullets after them) is kept as it is. With
    no scenario at all, the AC are a brief statement: a bullet list of paragraphs."""
    # A scenario is its name line and the clause lines after it (a blank line may separate the name from its first
    # clause); any other run of lines up to a blank line is a chunk of plain statements or bullets.
    chunks: list[list[Line]] = []
    current: list[Line] = []
    for line in lines:
        text = line.text.strip()
        if text.startswith("**Scenario"):
            if current:
                chunks.append(current)
            current = [line]
        elif not text:
            if current and not (current[0].text.lstrip().startswith("**Scenario") and len(current) == 1):
                chunks.append(current)
                current = []
        else:
            current.append(line)
    if current:
        chunks.append(current)
    if not chunks:
        return []
    is_scenario = [chunk[0].text.lstrip().startswith("**Scenario") for chunk in chunks]
    nodes: list[dict] = []
    for chunk, scenario_chunk in zip(chunks, is_scenario):
        if scenario_chunk:
            nodes.extend(scenario(chunk))
            continue
        if CLAUSE.match(chunk[0].text.strip()):
            raise Unsupported("GIVEN / WHEN / THEN clauses outside a scenario (a blank line inside one?)",
                              chunk[0].number, chunk[0].text.strip()[:60])
        nodes.extend(parse_blocks(chunk))
    if any(is_scenario):
        return nodes
    items: list[dict] = []
    for block in nodes:
        if block["type"] == "paragraph":
            items.append({"type": "listItem", "content": [block]})
        elif block["type"] == "bulletList":
            items.extend(block["content"])
        else:
            raise Unsupported(f"{block['type']} in a brief-statement AC (use scenarios or plain bullets)", lines[0].number)
    return [{"type": "bulletList", "content": items}]


# ---------------------------------------------------------------------------------------------------------------------
# Drafts


def sections(body: str, first_line: int) -> dict[str, list[Line]]:
    """The draft body's `##` sections, comments and scaffolding removed, line numbers kept."""
    found: dict[str, list[Line]] = {}
    current: list[Line] | None = None
    in_fence = in_comment = False
    for offset, raw in enumerate(body.split("\n")):
        number = first_line + offset
        text = raw.rstrip()
        if in_fence:
            if FENCE_CLOSE.match(text):
                in_fence = False
        else:
            if in_comment:
                if "-->" in text:
                    in_comment, text = False, text.split("-->", 1)[1]
                else:
                    continue
            while "<!--" in text:
                head, rest = text.split("<!--", 1)
                if "-->" in rest:
                    text = head + rest.split("-->", 1)[1]
                else:
                    in_comment, text = True, head
            if FENCE.match(text):
                in_fence = True
        if not in_fence and text.startswith("## "):
            name = text[3:].strip()
            if name not in SECTIONS:
                raise Unsupported(f"section `## {name}`", number, "expected " + ", ".join(sorted(SECTIONS - {'Log'})))
            if name in found:
                raise Unsupported(f"section `## {name}` twice", number)
            current = found[name] = []
            continue
        if current is None:
            if text.strip() and not text.startswith("# ") and not text.startswith(("Plan:", "Jira:", "Design:")):
                raise Unsupported("text before the first `##` section", number, text.strip()[:60])
            continue
        current.append(Line(number, text))
    if in_fence:
        raise Unsupported("unclosed code block", 0)
    for lines in found.values():
        while lines and (not lines[-1].text.strip() or RULE.match(lines[-1].text)):
            lines.pop()
    return found


def convert_draft(text: str, ac_field: str | None = None) -> dict:
    """The issue fields for one draft's text. Raises `Unsupported`, or `frontmatter.FrontmatterError` for no
    frontmatter."""
    fields, _, body = frontmatter.parse(text)
    first_line = text.count("\n", 0, len(text) - len(body)) + 1
    kind = fields.get("type")
    if kind not in ("story", "epic", "defect"):
        raise Unsupported(f"type `{kind}` in the frontmatter", 0, "expected story, epic or defect")
    summary = str(fields.get("summary") or "").strip()
    if not summary:
        raise Unsupported("draft with no summary in the frontmatter")
    parts = sections(body, first_line)
    parts.pop("Log", None)
    if not any(line.text.strip() for line in parts.get("Description", [])):
        raise Unsupported("draft with no `## Description`")
    if kind != "defect":
        for name in DEFECT_SECTIONS:
            if name in parts and any(line.text.strip() for line in parts[name]):
                raise Unsupported(f"section `## {name}` in a {kind} (defects only)", parts[name][0].number if parts[name] else 0)
    warnings: list[str] = []

    description = parse_blocks(parts["Description"])
    for name in DEFECT_SECTIONS:
        if name in parts and any(line.text.strip() for line in parts[name]):
            description += [{"type": "heading", "attrs": {"level": 2}, "content": [text_node(name, [])]},
                            *parse_blocks(parts[name])]

    acceptance = parse_acceptance(parts.get("Acceptance Criteria", []))
    if kind == "epic" and acceptance:
        raise Unsupported("acceptance criteria on an epic (epics have none)")
    if kind == "story" and not acceptance and not summary.startswith("Spike: "):
        warnings.append("a story with no Acceptance Criteria (only a spike has none)")
    ac_value = None
    if acceptance and ac_field:
        ac_value = {"field": ac_field, "value": {"version": 1, "type": "doc", "content": acceptance}}
    elif acceptance:
        description += [{"type": "heading", "attrs": {"level": 2}, "content": [text_node("Acceptance Criteria", [])]},
                        *acceptance]

    comment = None
    notes = parse_blocks(parts.get("Testing Notes", []))
    if notes:
        doc = {"version": 1, "type": "doc", "content": [
            {"type": "paragraph", "content": [text_node("Testing Notes", [{"type": "strong"}])]}, *notes]}
        comment = json.dumps(doc, ensure_ascii=False)

    return {"type": kind, "summary": summary,
            "description": {"version": 1, "type": "doc", "content": description},
            "acceptance_criteria": ac_value, "testing_notes_comment": comment, "warnings": warnings}
