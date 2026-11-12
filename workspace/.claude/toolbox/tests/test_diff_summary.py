"""Tests for bin/diff_summary.py, against temporary git repos: a rename across callers, a stray debug line, an untracked
file, a file move and a clean checkout.

Run from the repo root:

    python3 -m unittest discover -s workspace/.claude/toolbox/tests
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TOOLBOX = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLBOX / "lib"))
sys.path.insert(0, str(TOOLBOX / "bin"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
import diff_summary  # noqa: E402
from test_verify_inputs import commit, make_repo, run, write_config  # noqa: E402

CALLERS = 30


def caller(n: int, name: str) -> str:
    return f"class Use{n} {{\n  void go() {{\n    {name} shelf = new {name}();\n    shelf.count();\n  }}\n}}\n"


class DiffSummary(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp)
        patcher = mock.patch.dict(os.environ, {"TMPDIR": str(self.tmp / "out")})
        patcher.start()
        self.addCleanup(patcher.stop)
        (self.tmp / "out").mkdir()

    def single(self, files: dict[str, str]) -> tuple[config.Config, Path]:
        root = self.tmp / "ws"
        repo = make_repo(root, "demo", files)
        run(repo, "git", "checkout", "--quiet", "-b", "jdoe/work")
        return write_config(root, "nested"), repo

    def summarize(self, cfg: config.Config, *checkouts: str) -> tuple[dict, int]:
        return diff_summary.build(cfg, list(checkouts), None, None)

    def only(self, summary: dict) -> dict:
        self.assertEqual(len(summary["checkouts"]), 1)
        return summary["checkouts"][0]

    # AC: Rename Across Many Files

    def renamed_across_callers(self) -> tuple[config.Config, Path]:
        files = {f"src/Use{n}.java": caller(n, "Shelf") for n in range(CALLERS)}
        files["src/Shelf.java"] = "class Shelf {\n  int count() { return items.size(); }\n}\n"
        cfg, repo = self.single(files)
        changed = {f"src/Use{n}.java": caller(n, "Rack") for n in range(CALLERS)}
        changed["src/Shelf.java"] = "class Rack {\n  int count() { return items.size() + spare; }\n}\n"
        commit(repo, changed, "rename and fix")
        return cfg, repo

    def test_a_rename_across_thirty_callers_is_one_sweep_naming_the_files(self):
        cfg, repo = self.renamed_across_callers()
        summary, code = self.summarize(cfg, str(repo))
        self.assertEqual(code, 0)
        sweeps = [s for s in self.only(summary)["sweeps"] if s["kind"] == "rename"]
        # The thirty callers and the class declaration itself.
        self.assertEqual([(s["change"], s["files_total"]) for s in sweeps], [("Shelf -> Rack", CALLERS + 1)])
        self.assertIn("src/Use7.java", sweeps[0]["files"])

    def test_the_logic_change_in_one_file_is_not_a_sweep(self):
        cfg, repo = self.renamed_across_callers()
        digest = self.only(self.summarize(cfg, str(repo))[0])
        self.assertFalse([s for s in digest["sweeps"] if "spare" in s["change"]])
        shelf = next(f for f in digest["files"] if f["path"] == "src/Shelf.java")
        self.assertEqual((shelf["added"], shelf["removed"]), (2, 2))

    def test_counts_add_up_and_the_full_diff_goes_to_a_file(self):
        cfg, repo = self.renamed_across_callers()
        summary, _ = self.summarize(cfg, str(repo))
        digest = self.only(summary)
        self.assertEqual(digest["files_total"], CALLERS + 1)
        self.assertEqual(digest["added"], sum(f["added"] for f in digest["files"]))
        self.assertEqual(digest["removed"], sum(f["removed"] for f in digest["files"]))
        self.assertEqual(digest["base"], "origin/main")
        text = Path(summary["diff"]).read_text(encoding="utf-8")
        self.assertIn("# demo", text)
        self.assertIn("+class Rack {", text)

    def test_the_digest_is_small_next_to_the_diff(self):
        cfg, repo = self.renamed_across_callers()
        summary, _ = self.summarize(cfg, str(repo))
        self.assertLess(len(json.dumps(summary["checkouts"][0]["sweeps"])), 1500)

    def test_the_same_import_added_to_many_files_is_an_add_sweep(self):
        files = {f"src/A{n}.java": "package a;\nclass A {}\n" for n in range(4)}
        cfg, repo = self.single(files)
        commit(repo, {p: "package a;\nimport java.util.List;\nclass A {}\n" for p in files}, "imports")
        sweeps = self.only(self.summarize(cfg, str(repo))[0])["sweeps"]
        self.assertEqual([(s["kind"], s["change"], s["files_total"]) for s in sweeps],
                         [("add", "import java.util.List;", 4)])

    def test_a_version_bump_in_three_poms_is_one_edit_sweep(self):
        files = {f"m{n}/pom.xml": "<version>1.2.3</version>\n" for n in range(3)}
        cfg, repo = self.single(files)
        commit(repo, {p: "<version>1.3.0</version>\n" for p in files}, "bump")
        sweeps = self.only(self.summarize(cfg, str(repo))[0])["sweeps"]
        # A version is one word, so this is a rename of 1.2.3 to 1.3.0, with the line it happened on.
        self.assertEqual([(s["kind"], s["change"], s["files_total"]) for s in sweeps],
                         [("rename", "1.2.3 -> 1.3.0", 3)])
        self.assertEqual(sweeps[0]["example"], "<version>1.2.3</version> -> <version>1.3.0</version>")

    def test_a_patch_bump_in_three_poms_keeps_its_version_not_the_last_digit(self):
        files = {f"m{n}/pom.xml": "<version>1.2.3</version>\n" for n in range(3)}
        cfg, repo = self.single(files)
        commit(repo, {p: "<version>1.2.4</version>\n" for p in files}, "patch bump")
        sweeps = self.only(self.summarize(cfg, str(repo))[0])["sweeps"]
        self.assertEqual([(s["change"], s["files_total"]) for s in sweeps], [("1.2.3 -> 1.2.4", 3)])

    def test_lines_that_look_like_diff_headers_inside_a_hunk_are_counted(self):
        cfg, repo = self.single({"SKILL.md": "---\nname: x\n---\n-- sql comment\n++ plus\n"})
        commit(repo, {"SKILL.md": "---\nname: y\n---\n"}, "edit")
        files = self.only(self.summarize(cfg, str(repo))[0])["files"]
        self.assertEqual((files[0]["added"], files[0]["removed"]), (1, 3))
        run(repo, "git", "rm", "--quiet", "SKILL.md")
        run(repo, "git", "commit", "--quiet", "-m", "delete")
        files = self.only(self.summarize(cfg, str(repo))[0])["files"]
        self.assertEqual((files[0]["status"], files[0]["added"], files[0]["removed"]), ("D", 0, 5))

    def test_the_same_lines_in_new_files_are_not_a_sweep(self):
        cfg, repo = self.single({"README.md": "hi\n"})
        commit(repo, {f"src/New{n}.py": "from __future__ import annotations\nimport os\n" for n in range(4)}, "new")
        self.assertEqual(self.only(self.summarize(cfg, str(repo))[0])["sweeps"], [])

    def test_two_files_changing_alike_is_not_yet_a_sweep(self):
        files = {f"src/A{n}.java": "class A { int x; }\n" for n in range(2)}
        cfg, repo = self.single(files)
        commit(repo, {p: "class A { int y; }\n" for p in files}, "rename field")
        self.assertEqual(self.only(self.summarize(cfg, str(repo))[0])["sweeps"], [])

    # AC: Stray Debug Line

    def test_an_added_system_out_println_is_flagged_with_its_line(self):
        cfg, repo = self.single({"src/Shelf.java": "class Shelf {\n  int count() { return 1; }\n}\n"})
        commit(repo, {"src/Shelf.java": "class Shelf {\n  int count() {\n    System.out.println(\"here\");\n"
                                        "    return 1;\n  }\n}\n"}, "debug")
        flags = self.only(self.summarize(cfg, str(repo))[0])["flags"]
        self.assertEqual([(f["kind"], f["file"], f["line"]) for f in flags], [("debug print", "src/Shelf.java", 3)])
        self.assertIn("System.out.println", flags[0]["text"])

    def test_a_leftover_todo_a_conflict_marker_and_other_debug_calls_are_flagged(self):
        cfg, repo = self.single({"a.js": "x()\n", "b.py": "y()\n", "c.java": "z();\n"})
        commit(repo, {"a.js": "x()\nconsole.log(1)\n// TODO remove\n", "b.py": "y()\nbreakpoint()\n",
                      "c.java": "z();\ne.printStackTrace();\n<<<<<<< HEAD\n"}, "stuff")
        kinds = sorted((f["file"], f["kind"]) for f in self.only(self.summarize(cfg, str(repo))[0])["flags"])
        self.assertEqual(kinds, [("a.js", "debug print"), ("a.js", "leftover marker"), ("b.py", "debug print"),
                                 ("c.java", "conflict marker"), ("c.java", "debug print")])

    def test_a_removed_debug_line_or_one_that_was_already_there_is_not_flagged(self):
        cfg, repo = self.single({"a.java": "a();\nSystem.out.println(1);\nb();\n"})
        commit(repo, {"a.java": "a();\nSystem.out.println(1);\nb();\nc();\n"}, "add c")
        self.assertEqual(self.only(self.summarize(cfg, str(repo))[0])["flags"], [])
        commit(repo, {"a.java": "a();\nb();\nc();\n"}, "drop it")
        self.assertEqual(self.only(self.summarize(cfg, str(repo))[0])["flags"], [])

    def test_stray_files_are_flagged_and_an_untracked_one_is_still_in_the_diff(self):
        cfg, repo = self.single({"README.md": "hi\n"})
        (repo / "src").mkdir()
        (repo / "src" / "Real.java.orig").write_text("old\n", encoding="utf-8")
        (repo / ".DS_Store").write_text("x", encoding="utf-8")
        (repo / "notes.md").write_text("fine\n", encoding="utf-8")
        digest = self.only(self.summarize(cfg, str(repo))[0])
        self.assertEqual(sorted(f["file"] for f in digest["flags"] if f["kind"] == "stray file"),
                         [".DS_Store", "src/Real.java.orig"])
        self.assertEqual(digest["files_total"], 3)

    def test_flags_over_the_cap_are_counted(self):
        cfg, repo = self.single({"a.py": "x = 1\n"})
        commit(repo, {"a.py": "x = 1\n" + "breakpoint()\n" * 70}, "many")
        digest = self.only(self.summarize(cfg, str(repo))[0])
        self.assertEqual((len(digest["flags"]), digest["flags_total"]), (60, 70))

    # Files, renames, empty diffs, several checkouts.

    def test_a_moved_file_is_one_rename_with_its_old_path(self):
        body = "".join(f"line {n}\n" for n in range(20))
        cfg, repo = self.single({"old/Thing.txt": body})
        (repo / "new").mkdir()
        run(repo, "git", "mv", "old/Thing.txt", "new/Thing.txt")
        run(repo, "git", "commit", "--quiet", "-m", "move")
        files = self.only(self.summarize(cfg, str(repo))[0])["files"]
        self.assertEqual(files, [{"status": "R", "path": "new/Thing.txt", "from": "old/Thing.txt", "added": 0,
                                  "removed": 0}])

    def test_added_and_deleted_files_have_their_status_and_counts(self):
        cfg, repo = self.single({"gone.txt": "a\nb\n"})
        run(repo, "git", "rm", "--quiet", "gone.txt")
        commit(repo, {"fresh.txt": "x\ny\nz\n"}, "swap")
        files = {f["path"]: f for f in self.only(self.summarize(cfg, str(repo))[0])["files"]}
        self.assertEqual((files["gone.txt"]["status"], files["gone.txt"]["removed"]), ("D", 2))
        self.assertEqual((files["fresh.txt"]["status"], files["fresh.txt"]["added"]), ("A", 3))

    def test_a_checkout_with_no_changes_is_all_zero(self):
        cfg, repo = self.single({"a.txt": "a\n"})
        digest = self.only(self.summarize(cfg, str(repo))[0])
        self.assertEqual((digest["added"], digest["removed"], digest["files_total"], digest["files"]), (0, 0, 0, []))

    def test_several_checkouts_each_get_their_own_digest(self):
        root = self.tmp / "ws"
        first = make_repo(root, "first", {"a.txt": "a\n"})
        second = make_repo(root, "second", {"b.txt": "b\n"})
        cfg = write_config(root, "nested")
        commit(first, {"a.txt": "a\nmore\n"}, "one")
        commit(second, {"b.txt": "b\nmore\nmore2\n"}, "two")
        summary, code = self.summarize(cfg, "first", "second")
        self.assertEqual(code, 0)
        self.assertEqual({c["name"]: c["added"] for c in summary["checkouts"]}, {"first": 1, "second": 2})

    def test_a_checkout_that_cant_be_diffed_is_reported_and_the_rest_still_are(self):
        cfg, repo = self.single({"a.txt": "a\n"})
        commit(repo, {"a.txt": "a\nb\n"}, "change")
        summary, code = self.summarize(cfg, str(repo))
        self.assertEqual(code, 0)
        run(repo, "git", "remote", "set-url", "origin", str(self.tmp / "missing.git"))
        summary, code = self.summarize(cfg, str(repo))
        self.assertEqual(code, 1)
        self.assertEqual(summary["checkouts"], [])
        self.assertEqual(summary["failed"][0]["checkout"], str(repo))

    def test_a_name_that_is_no_checkout_is_an_error(self):
        cfg, _ = self.single({"a.txt": "a\n"})
        with self.assertRaises(diff_summary.verify_inputs.Failed):
            self.summarize(cfg, "nowhere")

    def test_base_can_be_overridden(self):
        cfg, repo = self.single({"a.txt": "a\n"})
        commit(repo, {"a.txt": "a\nb\n"}, "one")
        run(repo, "git", "tag", "mid")
        commit(repo, {"a.txt": "a\nb\nc\n"}, "two")
        summary, _ = diff_summary.build(cfg, [str(repo)], "mid", None)
        self.assertEqual(self.only(summary)["added"], 1)

    def test_the_script_runs_and_prints_json(self):
        cfg, repo = self.single({"a.txt": "a\n"})
        commit(repo, {"a.txt": "a\nb\n"}, "one")
        result = subprocess.run([sys.executable, str(TOOLBOX / "bin" / "diff_summary.py"), str(repo)],
                                cwd=cfg.root, capture_output=True, text=True, env=os.environ)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["checkouts"][0]["added"], 1)


class Parse(unittest.TestCase):
    def test_signature_is_a_rename_only_when_one_word_differs(self):
        self.assertEqual(diff_summary.signature("Shelf s = new Shelf();", "Rack s = new Shelf();"),
                         ("rename", "Shelf", "Rack"))
        self.assertEqual(diff_summary.signature("a(1)", "a(2)"), ("rename", "1", "2"))
        self.assertEqual(diff_summary.signature("a(1, 1)", "a(2, 2)"), ("rename", "1", "2"))
        self.assertEqual(diff_summary.signature("a(1)", "b(2)")[0], "edit")
        self.assertEqual(diff_summary.signature("a(x)", "a(x, y)")[0], "edit")

    def test_binary_and_mode_only_changes_have_no_lines(self):
        text = "diff --git a/x.png b/x.png\nBinary files a/x.png and b/x.png differ\n"
        self.assertEqual([(f["path"], f["added"], f["removed"]) for f in diff_summary.parse_diff(text)],
                         [("x.png", 0, 0)])


if __name__ == "__main__":
    unittest.main()
