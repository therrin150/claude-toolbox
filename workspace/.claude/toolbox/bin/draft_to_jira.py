#!/usr/bin/env python3
"""Convert discussion drafts' markdown to the Atlassian Document Format Jira takes: the script behind `jira-creator`.
The agent runs it before it creates any issue, so what it sends is what this produced.

  draft_to_jira.py <draft>... [--ac-field customfield_<n>] [--out <folder>]

Each draft (or task record made from one) becomes `<folder>/<draft file name>.json` (default: a new temporary folder)
holding `type`, `summary`, `description` (an ADF document), `acceptance_criteria` (`{"field", "value"}` with
--ac-field, else the AC sit in the description under an `Acceptance Criteria` heading and this is null),
`testing_notes_comment` (the comment body, an ADF document already serialized as the string the comment tool takes, or
null) and `warnings`. Pass the description and the AC with `contentFormat: "adf"`.

Prints one JSON line: `{"ok": true, "out": <folder>, "drafts": [{"draft", "file", "type", "summary", "warnings"}]}`
and exits 0. If any draft holds markdown it can't convert (lib/jira_format.py lists what is supported), it writes
nothing, prints `{"ok": false, "failed": [{"draft", "line", "construct", "message"}]}` naming the unsupported
construct and its line in each failing draft, and exits 1: file no issue, from this draft or any other in the run.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import frontmatter  # noqa: E402
import jira_format  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("drafts", nargs="+", type=Path)
    parser.add_argument("--ac-field", help="the Jira field id acceptance criteria go in; without it they go in the description")
    parser.add_argument("--out", type=Path, help="folder for the converted files (default: a new temporary folder)")
    args = parser.parse_args(argv)

    converted, failed = [], []
    for draft in args.drafts:
        try:
            converted.append((draft, jira_format.convert_draft(draft.read_text(encoding="utf-8"), args.ac_field)))
        except jira_format.Unsupported as problem:
            failed.append({"draft": str(draft), "line": problem.line, "construct": problem.construct,
                           "message": f"{draft}: {problem}"})
        except (frontmatter.FrontmatterError, OSError) as problem:
            failed.append({"draft": str(draft), "line": 0, "construct": "unreadable draft", "message": f"{draft}: {problem}"})
    if failed:
        print(json.dumps({"ok": False, "failed": failed}))
        for problem in failed:
            print(problem["message"], file=sys.stderr)
        return 1

    out = args.out or Path(tempfile.mkdtemp(prefix="jira-drafts-"))
    out.mkdir(parents=True, exist_ok=True)
    drafts = []
    for draft, result in converted:
        target = out / f"{draft.stem}.json"
        target.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        drafts.append({"draft": str(draft), "file": str(target), "type": result["type"], "summary": result["summary"],
                       "warnings": result["warnings"]})
    print(json.dumps({"ok": True, "out": str(out), "drafts": drafts}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
