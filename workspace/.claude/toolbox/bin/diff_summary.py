#!/usr/bin/env python3
"""Digest each checkout's diff against its default branch for the `diff-summarizer` agent, so the session that asked
never reads the diff itself. The full diff goes to a file; what's printed is the per-file counts, the mechanical sweeps
the diff holds, and the lines that look out of place.

  diff_summary.py <checkout>... [--base origin/<default>] [--out <file>]

Per checkout (a path, or a name under the workspace root or its worktrees folder; a folder of worktrees stands for the
checkouts inside it): `git fetch origin`, then the working tree against its merge base with --base (default
origin/<default branch>), so committed and uncommitted changes both count and untracked files are diffed as new files.
The index is never touched. The diff is `verify_inputs.checkout_diff`'s, the same one `change-verifier` reads.

files    each changed file: `status` (A, M, D, R), `path`, `from` for a rename, `added` and `removed` line counts.
         At most 300 are listed; `files_total` is the real count.
sweeps   a change that repeats across at least three modified files (new and deleted files are left out, their lines
         being the file itself), so the agent reports it as one line, with its `files`:
         `rename` (one word or version replaced by another on a line, the same pair in each file, with an `example` line), `edit` (the same line replaced
         by the same line), `add` or `remove` (the same line added or removed alone: an import, a version).
         Biggest first, at most 25.
flags    added lines and files that look out of place: a debug print, a leftover TODO, a conflict marker, a stray file
         such as `.DS_Store` or `*.orig`. `kind`, `file`, `line` (in the new file) and `text`. At most 60.
diff     the file holding every checkout's full diff, for the agent to Read in parts.

Prints one JSON object; exit 0, or 1 when a checkout's diff couldn't be produced (`failed` says which).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "lib")]

import config  # noqa: E402
import verify_inputs  # noqa: E402

MIN_SWEEP_FILES = 3
MAX_FILES, MAX_SWEEPS, MAX_SWEEP_FILES, MAX_FLAGS, TEXT_CHARS = 300, 25, 40, 60, 160

FILE_HEADER = re.compile(r"^diff --git a/(.*) b/(.*)$")
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)")
TOKEN = re.compile(r"\d+(?:\.\d+)+|\w+|\W")
LINE_FLAGS = [
    ("debug print", re.compile(r"System\.(out|err)\.print|\.printStackTrace\(\)|\bconsole\.(log|debug)\(|^\s*debugger;"
                               r"|\bbreakpoint\(\)|\bpdb\.set_trace\(|^\s*import pdb\b")),
    ("leftover marker", re.compile(r"\b(TODO|FIXME|XXX|HACK)\b")),
    ("conflict marker", re.compile(r"^(<{7}|>{7})( |$)|^={7}$")),
]
STRAY_FILE = re.compile(r"(^|/)(\.DS_Store|Thumbs\.db|\.idea/.*|.*\.(orig|rej|bak|swp|log)|.*~)$")


def parse_diff(text: str) -> list[dict]:
    """The files of a unified diff: status, path, from, added and removed counts, and for each its changed lines as
    blocks of (removed lines, added lines with their new line numbers)."""
    files: list[dict] = []
    current: dict | None = None
    block: tuple[list[str], list[tuple[int, str]]] | None = None
    new_line = 0
    in_hunk = False
    for line in text.splitlines():
        header = FILE_HEADER.match(line)
        if header:  # never a content line, which always starts with a space, + or -
            current = {"status": "M", "path": header.group(2), "from": None, "added": 0, "removed": 0, "blocks": []}
            files.append(current)
            block, in_hunk = None, False
            continue
        if current is None:
            continue
        if HUNK.match(line):
            new_line, block, in_hunk = int(HUNK.match(line).group(1)), None, True
        elif not in_hunk:  # the file's own header lines: modes, renames, `---`/`+++`, `Binary files differ`
            if line.startswith("new file mode"):
                current["status"] = "A"
            elif line.startswith("deleted file mode"):
                current["status"] = "D"
            elif line.startswith("rename from "):
                current["status"], current["from"] = "R", line[len("rename from "):]
        elif line.startswith("+"):
            current["added"] += 1
            if block is None:
                block = ([], [])
                current["blocks"].append(block)
            block[1].append((new_line, line[1:]))
            new_line += 1
        elif line.startswith("-"):
            current["removed"] += 1
            if block is None or block[1]:
                block = ([], [])
                current["blocks"].append(block)
            block[0].append(line[1:])
        else:
            block = None
            if line.startswith(" "):
                new_line += 1
    return files


def signature(removed: str, added: str) -> tuple:
    """What a replaced line says in a form that repeats across files: one word swapped for another, wherever it occurs
    on the line, is a rename."""
    before, after = TOKEN.findall(removed), TOKEN.findall(added)
    if len(before) == len(after):
        differing = {(a, b) for a, b in zip(before, after) if a != b}
        if len(differing) == 1:
            old, new = next(iter(differing))
            if re.fullmatch(r"\w+(?:\.\w+)*", old) and re.fullmatch(r"\w+(?:\.\w+)*", new):
                return ("rename", old, new)
    return ("edit", removed.strip()[:TEXT_CHARS], added.strip()[:TEXT_CHARS])


def sweeps(files: list[dict]) -> list[dict]:
    """Changes that repeat across at least MIN_SWEEP_FILES files, biggest first."""
    seen: dict[tuple, set[str]] = defaultdict(set)
    example: dict[tuple, str] = {}
    for f in files:
        if f["status"] in ("A", "D"):  # a new or deleted file's lines are the file, not a change repeated in it
            continue
        for removed, added in f["blocks"]:
            if removed and len(removed) == len(added):
                for old, (_, new) in zip(removed, added):
                    if old.strip() and new.strip():
                        sig = signature(old, new)
                        seen[sig].add(f["path"])
                        example.setdefault(sig, f"{old.strip()[:TEXT_CHARS]} -> {new.strip()[:TEXT_CHARS]}")
            elif removed and not added:
                for old in removed:
                    if old.strip():
                        seen[("remove", old.strip()[:TEXT_CHARS])].add(f["path"])
            elif added and not removed:
                for _, new in added:
                    if new.strip():
                        seen[("add", new.strip()[:TEXT_CHARS])].add(f["path"])
    found = [{"kind": sig[0], "change": " -> ".join(sig[1:]) if sig[0] in ("rename", "edit") else sig[1],
              "files_total": len(paths), "files": sorted(paths)[:MAX_SWEEP_FILES],
              **({"example": example[sig]} if sig[0] == "rename" else {})}
             for sig, paths in seen.items() if len(paths) >= MIN_SWEEP_FILES]
    found.sort(key=lambda s: (-s["files_total"], s["change"]))
    return found[:MAX_SWEEPS]


def flags(files: list[dict]) -> list[dict]:
    out = []
    for f in files:
        if f["status"] != "D" and STRAY_FILE.search(f["path"]):
            out.append({"kind": "stray file", "file": f["path"], "line": None, "text": f["path"]})
        for _, added in f["blocks"]:
            for number, text in added:
                for kind, pattern in LINE_FLAGS:
                    if pattern.search(text):
                        out.append({"kind": kind, "file": f["path"], "line": number, "text": text.strip()[:TEXT_CHARS]})
                        break
    return out


def digest(checkout: dict) -> dict:
    files = parse_diff(checkout["diff"])
    found = flags(files)
    return {
        "name": checkout["name"], "path": checkout["path"], "base": checkout["base"],
        "added": sum(f["added"] for f in files), "removed": sum(f["removed"] for f in files),
        "files_total": len(files),
        "files": [{k: f[k] for k in ("status", "path", "from", "added", "removed") if k != "from" or f["from"]}
                  for f in files[:MAX_FILES]],
        "sweeps": sweeps(files),
        "flags": found[:MAX_FLAGS], "flags_total": len(found),
    }


def build(cfg: config.Config, checkouts: list[str], base: str | None, out: Path | None) -> tuple[dict, int]:
    base = base or f"origin/{cfg.default_branch}"
    paths = [p for c in checkouts for p in verify_inputs.resolve(cfg, c)]
    diffs, failed = [], []
    for path in paths:
        try:
            diffs.append(verify_inputs.checkout_diff(path, base))
        except verify_inputs.Failed as e:
            failed.append({"checkout": str(path), "error": str(e)})
    label = "-".join(p.name for p in paths)
    out = out or Path(os.environ.get("TMPDIR") or tempfile.gettempdir()) / f"diff-summary-{label}.diff"
    out.write_text("".join(f"# {c['name']}\n\n{c['diff']}\n\n" for c in diffs), encoding="utf-8")
    return {"diff": str(out), "checkouts": [digest(c) for c in diffs], "failed": failed}, 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("checkouts", nargs="+")
    parser.add_argument("--base", help="what the diff is against; default origin/<default branch>")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    try:
        summary, code = build(config.load(), args.checkouts, args.base, args.out)
    except (config.ConfigError, verify_inputs.Failed) as e:
        sys.exit(str(e))
    print(json.dumps(summary, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
