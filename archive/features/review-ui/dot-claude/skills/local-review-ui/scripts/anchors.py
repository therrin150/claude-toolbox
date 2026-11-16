"""Where each comment sits in a file's current diff, and the extra context lines that show it.

A comment keeps the text of the line it was left on (`anchorText`) and up to CONTEXT lines either side of its range
(`before`, `after`). Against the file as it is now, a comment is placed:

1. at its own line, when the text there still matches;
2. else at the nearest line with the same text (the code moved), preferring one whose neighbours still match;
3. else, its line being gone or rewritten, at the closest surviving line to where it was, flagged outdated.

A line with no letters or digits (`}`, `*/`, a blank) says too little on its own, so it only counts as the same line
when a neighbour of substance matches too; otherwise a comment on a deleted `*/` would hop to the next Javadoc's.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import common
import gitdiff

CONTEXT = 3
REACH = 200


def source_lines(text: str) -> list[str]:
    """A file's lines as the page numbers them (split on \\n only, a trailing newline ends the last line)."""
    if not text:
        return []
    lines = text.split("\n")
    if text.endswith("\n"):
        lines.pop()
    return [line.rstrip("\r") for line in lines]


def side_lines(repo: str, base: str, path: str, old_path: str, side: str) -> list[str] | None:
    """The file's lines on one side: the working copy for new, the file at the base for old. None when that side has
    no such file."""
    if side == "old":
        try:
            return source_lines(gitdiff.file_at(repo, base, old_path))
        except gitdiff.GitError:
            return None
    try:
        target = common.inside(Path(repo), path)
        # Bytes, not read_text: text mode would turn a lone \r into a line break, which git (and so the page) doesn't.
        return source_lines(target.read_bytes().decode("utf-8", "replace"))
    except (OSError, ValueError):
        return None


def capture(lines: list[str], line: int, end: int) -> dict:
    """The lines around a range, stored with a comment so it can find its place again after the code changes."""
    return {"before": lines[max(0, line - 1 - CONTEXT):max(0, line - 1)], "after": lines[end:end + CONTEXT]}


def _substantial(text: str) -> bool:
    return any(ch.isalnum() for ch in text)


def _at(lines: list[str], n: int) -> str | None:
    return lines[n - 1] if 1 <= n <= len(lines) else None


def _before_score(lines: list[str], before: list[str], first: int) -> int:
    """How many substantial lines of `before` still sit right above line `first`."""
    return sum(1 for k, text in enumerate(reversed(before)) if _substantial(text) and _at(lines, first - 1 - k) == text)


def _after_score(lines: list[str], after: list[str], start: int) -> int:
    """How many substantial lines of `after` still sit from line `start` down."""
    return sum(1 for k, text in enumerate(after) if _substantial(text) and _at(lines, start + k) == text)


def _best(candidates, score, end: int) -> int | None:
    """The candidate with the highest score above zero, the nearest to `end` on a tie."""
    best = None
    for n in candidates:
        s = score(n)
        if s > 0 and (best is None or (s, -abs(n - end)) > best[0]):
            best = ((s, -abs(n - end)), n)
    return best[1] if best else None


def locate(lines: list[str] | None, comment: dict) -> dict | None:
    """The line a comment sits at now, `moved` when the code moved, `outdated` when its line is gone or rewritten.
    None when the side has no file to place it in."""
    if not lines:
        return None
    end = comment["endLine"]
    span = end - (comment.get("line") or end)
    want = comment.get("anchorText")
    before = comment.get("before") or []
    after = comment.get("after") or []
    has_context = any(_substantial(t) for t in before + after)

    def context(n: int) -> int:
        return _before_score(lines, before, n - span) + _after_score(lines, after, n + 1)

    def same(n: int) -> bool:
        if _at(lines, n) != want:
            return False
        if _substantial(want):
            return True
        # Without stored context (a comment from before it was kept), a bare `}` may stay put but never move.
        return context(n) > 0 if has_context else n == end

    if want is None:
        if end <= len(lines):
            return {"line": end, "moved": False, "outdated": False}
    elif same(end):
        return {"line": end, "moved": False, "outdated": False}
    else:
        near = range(max(1, end - REACH), min(len(lines), end + REACH) + 1)
        matches = [n for n in near if n != end and same(n)]
        if matches:
            if has_context:
                # Neighbours that still match beat distance: the same `return result;` can appear twice nearby.
                at = max(matches, key=lambda n: (context(n), -abs(n - end)))
            else:
                at = min(matches, key=lambda n: abs(n - end))
            return {"line": at, "moved": True, "outdated": False}

    # Gone or rewritten: the gap between the surviving context above and below is where it was. Its last line (a
    # rewrite), or the first line below it (a deletion), is the closest surviving line.
    near = range(max(1, end - REACH), min(len(lines) + 1, end + REACH) + 1)
    below = _best(near, lambda n: _after_score(lines, after, n), end + 1)
    above = _best(near, lambda n: _before_score(lines, before, n), end - span)
    if below is not None and above is not None and above <= below:
        at = below - 1 if below > above else below
    elif below is not None:
        at = below
    elif above is not None:
        at = above + span
    else:
        at = end
    return {"line": max(1, min(at, len(lines))), "moved": False, "outdated": True}


def digest(comments: list[dict]) -> str:
    """Changes when a comment is added, removed or re-anchored, not on a reply, so a diff's placement can be cached."""
    keys = [[c["id"], c["side"], c.get("line"), c["endLine"], c.get("anchorText"), c.get("before"), c.get("after")]
            for c in comments]
    return hashlib.sha1(json.dumps(keys).encode()).hexdigest()[:12]


def _next(hunk: dict, key: str, start: str) -> int:
    """The line number on one side just past a hunk."""
    numbers = [line[key] for line in hunk["lines"] if line[key] is not None]
    return numbers[-1] + 1 if numbers else hunk[start] + 1


def _first(hunk: dict, key: str, start: str) -> int:
    numbers = [line[key] for line in hunk["lines"] if line[key] is not None]
    return numbers[0] if numbers else hunk[start] + 1


def widen(hunks: list[dict], new_lines: list[str], ranges: list[tuple[int, int]]) -> list[dict]:
    """The hunks plus the unchanged new-side lines in `ranges` that they don't show, joined into any hunk they touch,
    so a comment outside the diff's context shows with a few lines around it."""
    if not hunks or not ranges:
        return hunks
    shown = {line["n"] for hunk in hunks for line in hunk["lines"] if line["n"] is not None}
    extra = sorted({n for first, last in ranges for n in range(max(1, first), min(len(new_lines), last) + 1)} - shown)
    if not extra:
        return hunks
    ordered = sorted(hunks, key=lambda h: _first(h, "n", "newStart"))

    def old_number(n: int) -> int:
        # Between hunks nothing changed, so both sides advance together from the last hunk above.
        delta = 0
        for hunk in ordered:
            if _first(hunk, "n", "newStart") > n:
                break
            delta = _next(hunk, "o", "oldStart") - _next(hunk, "n", "newStart")
        return n + delta

    pieces = [{"oldStart": old_number(n), "newStart": n, "section": "",
               "lines": [{"t": "ctx", "o": old_number(n), "n": n, "s": new_lines[n - 1]}]} for n in extra]
    merged: list[dict] = []
    for hunk in sorted(ordered + pieces, key=lambda h: _first(h, "n", "newStart")):
        last = merged[-1] if merged else None
        if (last and _next(last, "n", "newStart") == _first(hunk, "n", "newStart")
                and _next(last, "o", "oldStart") == _first(hunk, "o", "oldStart")):
            last["lines"] = last["lines"] + hunk["lines"]
            last["section"] = last["section"] or hunk["section"]
        else:
            merged.append(dict(hunk))
    return merged


def place(repo: str, base: str, entry: dict, diff: dict, comments: list[dict]) -> dict:
    """The diff with `anchors` (comment id to its placement, None when it can't be placed) and its hunks widened to
    show every placed comment."""
    anchors: dict[str, dict | None] = {c["id"]: None for c in comments}
    result = dict(diff, anchors=anchors)
    if diff["binary"] or diff["truncated"] or not diff["hunks"] or not comments:
        return result
    lines = {"old": None, "new": None}
    for side in ("old", "new"):
        if any(c["side"] == side for c in comments):
            lines[side] = side_lines(repo, base, entry["path"], entry["oldPath"], side)

    ranges = []
    shown_old = {line["o"] for hunk in diff["hunks"] for line in hunk["lines"] if line["o"] is not None}
    for c in comments:
        at = locate(lines[c["side"]], c)
        if at is None:
            continue
        anchors[c["id"]] = at
        first = at["line"] - (0 if at["outdated"] else c["endLine"] - (c.get("line") or c["endLine"]))
        if c["side"] == "new":
            ranges.append((first - CONTEXT, at["line"] + CONTEXT))
        elif at["line"] not in shown_old:
            # An unchanged old line outside the hunks: show it through its new-side twin.
            new_at = _old_to_new(diff["hunks"], at["line"])
            ranges.append((new_at - CONTEXT, new_at + CONTEXT))
    if lines["new"] is None and ranges:
        lines["new"] = side_lines(repo, base, entry["path"], entry["oldPath"], "new")
    if lines["new"] is not None:
        result["hunks"] = widen(diff["hunks"], lines["new"], ranges)
    return result


def _old_to_new(hunks: list[dict], o: int) -> int:
    delta = 0
    for hunk in sorted(hunks, key=lambda h: _first(h, "o", "oldStart")):
        if _first(hunk, "o", "oldStart") > o:
            break
        delta = _next(hunk, "n", "newStart") - _next(hunk, "o", "oldStart")
    return o + delta
