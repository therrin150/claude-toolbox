"""The draft-to-ADF converter behind jira-creator: lib/jira_format.py and bin/draft_to_jira.py.

Run from the repo root:

    python3 -m unittest discover -s core/workspace/.claude/toolbox/tests
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from toolbox_tree import TOOLBOX  # noqa: E402
sys.path.insert(0, str(TOOLBOX / "lib"))

import jira_format  # noqa: E402

SCRIPT = TOOLBOX / "bin" / "draft_to_jira.py"
FRONT = "---\ntype: story\nsummary: Add Shelf Labels\nstatus: draft\n---\n"


def draft(description: str = "Context and a directive.", ac: str | None = None, notes: str | None = None,
          front: str = FRONT) -> str:
    text = f"{front}\n# Add Shelf Labels\n\nPlan: [[Discussions/x/plan|x]]\n\n## Description\n\n<!-- budget -->\n\n{description}\n"
    if ac is not None:
        text += f"\n## Acceptance Criteria\n\n{ac}\n"
    if notes is not None:
        text += f"\n## Testing Notes\n\n{notes}\n"
    return text


SCENARIOS = """**Scenario: Shelf Already Full**
GIVEN a shelf holding its maximum number of items
WHEN an item is added to that shelf
THEN a `400` response is returned with a message naming the full shelf
AND the item is NOT added

**Scenario: Shelf Has Room**
GIVEN an empty shelf
WHEN an item is added
THEN a `201` response is returned"""


def convert(text: str, ac_field: str | None = "customfield_1") -> dict:
    return jira_format.convert_draft(text, ac_field)


def blocks(text: str, **kw) -> list[dict]:
    return convert(text, **kw)["description"]["content"]


def text_of(node) -> str:
    if isinstance(node, list):
        return "".join(text_of(n) for n in node)
    if node.get("type") == "text":
        return node["text"]
    if node.get("type") == "hardBreak":
        return "\n"
    return text_of(node.get("content", []))


class InlineTest(unittest.TestCase):
    def runs(self, text: str) -> list[tuple[str, list[str]]]:
        return [(n["text"], [m["type"] for m in n.get("marks", [])]) for n in jira_format.inline(text, 1)]

    def test_marks(self):
        self.assertEqual(self.runs("a `b` **c** *d* ~~e~~ _f_"), [
            ("a ", []), ("b", ["code"]), (" ", []), ("c", ["strong"]), (" ", []), ("d", ["em"]), (" ", []),
            ("e", ["strike"]), (" ", []), ("f", ["em"])])

    def test_code_keeps_stars_underscores_and_double_backticks(self):
        self.assertEqual(self.runs("`a*b` and snake_case_name and `` `x` ``"), [
            ("a*b", ["code"]), (" and snake_case_name and ", []), ("`x`", ["code"])])

    def test_bold_around_code_leaves_the_code_unbold(self):
        self.assertEqual(self.runs("**see `x` now**"), [("see ", ["strong"]), ("x", ["code"]), (" now", ["strong"])])

    def test_link(self):
        node = jira_format.inline("[docs](https://example.com/a)", 1)[0]
        self.assertEqual(node["marks"], [{"type": "link", "attrs": {"href": "https://example.com/a"}}])

    def test_a_lone_star_or_underscore_is_text(self):
        self.assertEqual(self.runs("2 * 3 and a_b"), [("2 * 3 and a_b", [])])

    def test_unsupported_inline(self):
        for text, construct in (("see [[Tasks/x]]", "wikilink"), ("![a](b.png)", "image"), ("a <b>x</b>", "HTML"),
                                ("a `b", "unclosed code span"), ("a **b", "unclosed bold"), ("a ~~b", "unclosed strike")):
            with self.assertRaisesRegex(jira_format.Unsupported, construct):
                jira_format.inline(text, 7)


class BlockTest(unittest.TestCase):
    def test_paragraphs_join_hard_wrapped_lines(self):
        nodes = blocks(draft("one two\nthree\n\nfour"))
        self.assertEqual([text_of(n) for n in nodes], ["one two three", "four"])

    def test_headings_lists_and_nested_lists(self):
        nodes = blocks(draft("### Steps\n\n1. one\n2. two\n   - nested a\n   - nested b\n3. three\n\n- x\n- y"))
        self.assertEqual([n["type"] for n in nodes], ["heading", "orderedList", "bulletList"])
        self.assertEqual(nodes[0]["attrs"]["level"], 3)
        second = nodes[1]["content"][1]["content"]
        self.assertEqual([c["type"] for c in second], ["paragraph", "bulletList"])
        self.assertEqual(len(second[1]["content"]), 2)

    def test_code_block_with_a_language_and_a_comment_inside_it_kept(self):
        nodes = blocks(draft("```json\n{\"a\": 1}\n<!-- keep -->\n\n## not a section\n```"))
        self.assertEqual(nodes[0]["type"], "codeBlock")
        self.assertEqual(nodes[0]["attrs"], {"language": "json"})
        self.assertEqual(nodes[0]["content"][0]["text"], '{"a": 1}\n<!-- keep -->\n\n## not a section')

    def test_table(self):
        table = blocks(draft("| Flag | Default |\n| --- | --- |\n| `a|b` | `true` |\n| c | d |"))[0]
        self.assertEqual(table["type"], "table")
        rows = table["content"]
        self.assertEqual([c["type"] for c in rows[0]["content"]], ["tableHeader", "tableHeader"])
        self.assertEqual(text_of(rows[1]), "a|btrue")
        self.assertEqual(len(rows), 3)

    def test_callouts_become_panels_and_a_title_a_bold_first_paragraph(self):
        nodes = blocks(draft("> [!warning]\n> Careful here.\n\n> [!info] Deploy order\n> First.\n>\n> - a\n> - b"))
        self.assertEqual([(n["type"], n["attrs"]["panelType"]) for n in nodes], [("panel", "warning"), ("panel", "info")])
        title = nodes[1]["content"][0]
        self.assertEqual(text_of(title), "Deploy order")
        self.assertEqual(title["content"][0]["marks"], [{"type": "strong"}])
        self.assertEqual([c["type"] for c in nodes[1]["content"]], ["paragraph", "paragraph", "bulletList"])

    def test_tip_and_danger_map_to_jira_panel_types(self):
        nodes = blocks(draft("> [!tip]\n> a\n\n> [!danger]\n> b"))
        self.assertEqual([n["attrs"]["panelType"] for n in nodes], ["success", "error"])

    def test_rule_between_blocks_and_trailing_rule_dropped(self):
        nodes = blocks(draft("one\n\n---\n\ntwo\n\n---"))
        self.assertEqual([n["type"] for n in nodes], ["paragraph", "rule", "paragraph"])

    def test_scaffolding_dropped(self):
        text = draft("Body.") + "\n## Log\n\n- 2026-11-12: a note with <b>html</b>\n"
        self.assertEqual([text_of(n) for n in blocks(text)], ["Body."])


class AcceptanceTest(unittest.TestCase):
    def test_scenarios_as_a_bold_name_and_a_paragraph_of_hard_broken_clauses(self):
        ac = convert(draft(ac=SCENARIOS))["acceptance_criteria"]
        self.assertEqual(ac["field"], "customfield_1")
        content = ac["value"]["content"]
        self.assertEqual(ac["value"]["type"], "doc")
        self.assertEqual(len(content), 4)
        self.assertEqual(content[0], {"type": "paragraph", "content": [
            {"type": "text", "text": "Scenario: Shelf Already Full", "marks": [{"type": "strong"}]}]})
        clauses = content[1]["content"]
        self.assertEqual([n["type"] for n in clauses].count("hardBreak"), 3)
        self.assertEqual(clauses[0], {"type": "text", "text": "GIVEN a shelf holding its maximum number of items"})
        self.assertIn({"type": "text", "text": "400", "marks": [{"type": "code"}]}, clauses)
        self.assertEqual(text_of(content[1]).splitlines()[0], "GIVEN a shelf holding its maximum number of items")

    def test_with_no_ac_field_the_ac_follow_the_description_under_a_heading(self):
        result = convert(draft(ac=SCENARIOS), ac_field=None)
        self.assertIsNone(result["acceptance_criteria"])
        content = result["description"]["content"]
        heading = next(i for i, n in enumerate(content) if n["type"] == "heading")
        self.assertEqual(text_of(content[heading]), "Acceptance Criteria")
        self.assertEqual(content[heading]["attrs"]["level"], 2)
        self.assertEqual(len(content[heading + 1:]), 4)

    def test_with_an_ac_field_the_description_has_no_ac(self):
        self.assertEqual([n["type"] for n in blocks(draft(ac=SCENARIOS))], ["paragraph"])

    def test_a_blank_line_after_the_name_and_trailing_double_spaces_are_fine(self):
        ac = "**Scenario: A**\n\nGIVEN one  \nWHEN two  \nTHEN three"
        content = convert(draft(ac=ac))["acceptance_criteria"]["value"]["content"]
        self.assertEqual(text_of(content[1]), "GIVEN one\nWHEN two\nTHEN three")

    def test_a_statement_before_and_bullets_after_the_scenarios_are_kept(self):
        ac = "Verified by review.\n\n" + SCENARIOS + "\n\n- Every consumer builds.\n- The reactor lists 14 modules."
        content = convert(draft(ac=ac))["acceptance_criteria"]["value"]["content"]
        self.assertEqual([n["type"] for n in content], ["paragraph", "paragraph", "paragraph", "paragraph", "paragraph",
                                                        "bulletList"])
        self.assertEqual(text_of(content[0]), "Verified by review.")

    def test_a_brief_statement_is_a_bullet_list(self):
        content = convert(draft(ac="The response is a 400."))["acceptance_criteria"]["value"]["content"]
        self.assertEqual([n["type"] for n in content], ["bulletList"])
        content = convert(draft(ac="- one\n- two"))["acceptance_criteria"]["value"]["content"]
        self.assertEqual(len(content[0]["content"]), 2)

    def test_scenario_problems(self):
        for ac, construct in (
                ("**Scenario: A**\nGIVEN x\nSOMETIMES y", "doesn't start GIVEN"),
                ("**Scenario: A**", "no GIVEN"),
                ("**Scenario: `x` thing**\nGIVEN a", "backticks or emphasis in a scenario name"),
                ("**Scenario A**\nGIVEN a", "scenario name"),
                ("**Scenario: A**\nGIVEN a\n\nWHEN b", "clauses outside a scenario"),
                ("**Scenario: A**\ngiven a\nwhen b", "doesn't start GIVEN")):
            with self.assertRaisesRegex(jira_format.Unsupported, construct, msg=ac):
                convert(draft(ac=ac))

    def test_testing_notes_become_a_comment_body_string(self):
        comment = convert(draft(notes="- Delete the test issues.\n- Use `RMGT`."))["testing_notes_comment"]
        self.assertIsInstance(comment, str)
        doc = json.loads(comment)
        self.assertEqual(doc["type"], "doc")
        self.assertEqual(text_of(doc["content"][0]), "Testing Notes")
        self.assertEqual(doc["content"][0]["content"][0]["marks"], [{"type": "strong"}])
        self.assertEqual(doc["content"][1]["type"], "bulletList")

    def test_no_testing_notes_no_comment(self):
        self.assertIsNone(convert(draft())["testing_notes_comment"])
        self.assertIsNone(convert(draft(notes=""))["testing_notes_comment"])


class DraftTest(unittest.TestCase):
    def test_summary_type_and_fields(self):
        result = convert(draft(ac=SCENARIOS))
        self.assertEqual((result["type"], result["summary"], result["warnings"]), ("story", "Add Shelf Labels", []))

    def test_a_story_without_ac_warns_but_a_spike_does_not(self):
        self.assertEqual(len(convert(draft())["warnings"]), 1)
        spike = FRONT.replace("Add Shelf Labels", "'Spike: Does It Hold'")
        self.assertEqual(convert(draft(front=spike))["warnings"], [])

    def test_a_defect_keeps_its_sections_in_the_description_under_headings(self):
        front = FRONT.replace("story", "defect")
        text = draft(front=front, ac="The count is right.") + (
            "\n## Steps to Reproduce\n\n1. Add one.\n\n## Expected\n\nOne.\n\n## Actual\n\nTwo.\n")
        content = convert(text)["description"]["content"]
        self.assertEqual([text_of(n) for n in content if n["type"] == "heading"],
                         ["Steps to Reproduce", "Expected", "Actual"])
        self.assertEqual([n["type"] for n in content], ["paragraph", "heading", "orderedList", "heading", "paragraph",
                                                        "heading", "paragraph"])

    def test_an_epic_is_a_description_only(self):
        epic = FRONT.replace("story", "epic")
        self.assertEqual(convert(draft(front=epic))["acceptance_criteria"], None)
        with self.assertRaisesRegex(jira_format.Unsupported, "epic"):
            convert(draft(front=epic, ac="- no"))

    def test_a_task_record_made_from_a_draft_converts_too(self):
        front = "---\ntype: story\nkey: RMGT-1\nsummary: Add Shelf Labels\nsessions: []\n---\n"
        text = draft(ac=SCENARIOS, front=front).replace("Plan:", "Jira: [RMGT-1](https://x/browse/RMGT-1)\n\nPlan:")
        self.assertEqual(convert(text)["summary"], "Add Shelf Labels")

    def test_structural_problems(self):
        for text, construct in (
                (draft().replace("## Description", "## Details"), "section `## Details`"),
                (draft().replace("## Description\n", "## Description\n\nx\n\n## Description\n"), "twice"),
                (draft("").replace("<!-- budget -->", ""), "no `## Description`"),
                (draft().replace("type: story", "type: spike"), "type `spike`"),
                (draft().replace("summary: Add Shelf Labels\n", ""), "no summary"),
                ("---\ntype: story\nsummary: x\n---\nstray text\n\n## Description\n\nx\n", "text before the first"),
                (draft("```\nnever closed"), "unclosed code block")):
            with self.assertRaisesRegex(jira_format.Unsupported, construct, msg=construct):
                convert(text)

    def test_unsupported_blocks_name_the_construct_and_the_files_line(self):
        cases = (
            ("<div>x</div>", "HTML"),
            ("> a plain quote", "blockquote"),
            ("> [!bogus]\n> x", r"callout type \[!bogus\]"),
            ("> [!info]\n> ```\n> code\n> ```", "code block inside a callout"),
            ("> [!info]\n> | a |\n> | --- |\n> | b |", "table inside a callout"),
            ("# Top heading", "level-1 heading"),
            ("| a | b |\n| --- | --- |\n| 1 | 2 | 3 |", "table row with 3 cells under a header of 2"),
            ("text with ![img](a.png)", "image"),
            ("- item\n  ```\n  code\n  ```", "inside a list item"),
            ("- item\n  | a | b |\n  | --- | --- |", "inside a list item"))
        for body, construct in cases:
            text = draft(body)
            with self.assertRaisesRegex(jira_format.Unsupported, construct, msg=body) as caught:
                convert(text)
            line = caught.exception.line
            self.assertGreater(line, 0, body)
            self.assertIn(text.splitlines()[line - 1].strip(), body.splitlines() + [l.strip() for l in body.splitlines()], body)


class ScriptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def run_script(self, *args: str):
        proc = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)
        return proc.returncode, json.loads(proc.stdout), proc.stderr

    def write(self, name: str, text: str) -> Path:
        path = self.dir / name
        path.write_text(text)
        return path

    def test_converts_each_draft_to_a_json_file(self):
        a = self.write("01-add-shelf-labels.md", draft(ac=SCENARIOS, notes="- one"))
        b = self.write("02-other.md", draft(ac=SCENARIOS).replace("Add Shelf Labels", "Other"))
        out = self.dir / "out"
        code, summary, _ = self.run_script(str(a), str(b), "--ac-field", "customfield_9", "--out", str(out))
        self.assertEqual(code, 0)
        self.assertTrue(summary["ok"])
        self.assertEqual([d["file"] for d in summary["drafts"]], [str(out / "01-add-shelf-labels.json"), str(out / "02-other.json")])
        first = json.loads((out / "01-add-shelf-labels.json").read_text())
        self.assertEqual(first["acceptance_criteria"]["field"], "customfield_9")
        self.assertIsInstance(first["testing_notes_comment"], str)
        self.assertEqual(json.loads((out / "02-other.json").read_text())["summary"], "Other")

    def test_without_an_ac_field_the_ac_are_in_the_description(self):
        a = self.write("01-x.md", draft(ac=SCENARIOS))
        code, summary, _ = self.run_script(str(a), "--out", str(self.dir / "o"))
        self.assertEqual(code, 0)
        result = json.loads(Path(summary["drafts"][0]["file"]).read_text())
        self.assertIsNone(result["acceptance_criteria"])
        self.assertIn("Scenario: Shelf Already Full", text_of(result["description"]["content"]))

    def test_default_out_is_a_new_temporary_folder(self):
        a = self.write("01-x.md", draft())
        code, summary, _ = self.run_script(str(a))
        self.assertEqual(code, 0)
        self.assertTrue(Path(summary["out"]).is_dir())
        self.addCleanup(lambda: [p.unlink() for p in Path(summary["out"]).iterdir()] or Path(summary["out"]).rmdir())

    def test_a_conversion_problem_fails_naming_the_construct_and_writes_nothing(self):
        good = self.write("01-good.md", draft(ac=SCENARIOS))
        bad = self.write("02-bad.md", draft("fine\n\n<details>x</details>", ac=SCENARIOS))
        out = self.dir / "out"
        code, summary, stderr = self.run_script(str(good), str(bad), "--out", str(out))
        self.assertEqual(code, 1)
        self.assertFalse(summary["ok"])
        self.assertEqual(len(summary["failed"]), 1)
        failure = summary["failed"][0]
        self.assertEqual((failure["draft"], failure["construct"]), (str(bad), "HTML"))
        self.assertGreater(failure["line"], 0)
        self.assertIn("unsupported markdown: HTML", failure["message"])
        self.assertIn("02-bad.md", stderr)
        self.assertFalse(out.exists())

    def test_every_failing_draft_is_named(self):
        one = self.write("01-a.md", draft("![x](y.png)"))
        two = self.write("02-b.md", draft("see [[Tasks/z]]"))
        code, summary, _ = self.run_script(str(one), str(two))
        self.assertEqual(code, 1)
        self.assertEqual([f["construct"] for f in summary["failed"]], ["image", "Obsidian wikilink"])

    def test_an_unreadable_draft_fails(self):
        no_front = self.write("01-a.md", "no frontmatter\n")
        code, summary, _ = self.run_script(str(no_front), str(self.dir / "missing.md"))
        self.assertEqual(code, 1)
        self.assertEqual([f["construct"] for f in summary["failed"]], ["unreadable draft", "unreadable draft"])


if __name__ == "__main__":
    unittest.main()
