"""Tests for bin/session_usage.py, against hand-written transcripts in a scratch CLAUDE_CONFIG_DIR and a task record in
a scratch workspace.

Run from the repo root:

    python3 -m unittest discover -s workspace/.claude/toolbox/tests
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

TOOLBOX = Path(__file__).resolve().parents[1]
USAGE = TOOLBOX / "bin" / "session_usage.py"
TASKS = TOOLBOX / "bin" / "tasks.py"
sys.path.insert(0, str(TOOLBOX / "lib"))

import frontmatter  # noqa: E402


def assistant(request: str, model: str, output: int, cache_read: int = 0, when: str = "2026-10-27T10:00:00Z") -> dict:
    return {"type": "assistant", "requestId": request, "timestamp": when,
            "message": {"id": f"msg-{request}", "model": model,
                        "usage": {"input_tokens": 10, "cache_read_input_tokens": cache_read, "output_tokens": output,
                                  "cache_creation_input_tokens": 100,
                                  "cache_creation": {"ephemeral_1h_input_tokens": 100}}}}


class UsageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.home = self.tmp / "claude"
        self.project = self.home / "projects" / "-ws"
        self.project.mkdir(parents=True)
        self.root = self.tmp / "ws"
        (self.root / ".claude").mkdir(parents=True)
        raw = {"version": 1, "features": ["tasks"], "workspace": {"name": "pantry", "layout": "single"},
               "user": {"handle": "jdoe", "name": "Jordan"}, "vault": {"path": "vault"},
               "tracker": {"kind": "vault", "key_prefix": "PT"}}
        (self.root / ".claude" / "toolbox.local.yml").write_text(yaml.safe_dump(raw))
        self.env = {**os.environ, "CLAUDE_CONFIG_DIR": str(self.home), "TOOLBOX_WORKSPACE": str(self.root)}
        self.env.pop("CLAUDE_CODE_SESSION_ID", None)

    def transcript(self, session: str, lines: list[dict], agents: dict | None = None) -> None:
        (self.project / f"{session}.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
        for agent, agent_lines in (agents or {}).items():
            folder = self.project / session / "subagents"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"agent-{agent}.jsonl").write_text("".join(json.dumps(x) + "\n" for x in agent_lines))

    def run_usage(self, *args: str, ok: bool = True) -> dict | str:
        proc = subprocess.run([sys.executable, str(USAGE), *args], capture_output=True, text=True, env=self.env,
                              cwd=self.root)
        if not ok:
            self.assertNotEqual(proc.returncode, 0)
            return proc.stderr
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def record(self, sessions: list[str]) -> Path:
        out = subprocess.run([sys.executable, str(TASKS), "create-defect", "--summary", "Shelf Count Wrong"],
                             capture_output=True, text=True, env=self.env, cwd=self.root, check=True)
        path = Path(json.loads(out.stdout)["doc"])
        for session in sessions:
            subprocess.run([sys.executable, str(TASKS), "append", "PT-1", "sessions", session], env=self.env,
                           cwd=self.root, check=True, capture_output=True)
        return path

    def test_counts_each_request_once_with_subagents(self):
        # A request spans several lines; the one with the most output wins.
        self.transcript("s1", [assistant("r1", "claude-opus-5-5", 5), assistant("r1", "claude-opus-5-5", 40),
                               assistant("r2", "claude-opus-5-5", 60)],
                        {"a1": [assistant("r3", "claude-haiku-5-5", 7)]})
        out = self.run_usage("--session", "s1")
        opus = out["by_model"]["claude-opus-5-5"]
        self.assertEqual((opus["requests"], opus["output"], opus["cache_write_1h"]), (2, 100, 200))
        self.assertEqual(out["by_model"]["claude-haiku-5-5"]["requests"], 1)
        self.assertEqual(out["total"]["requests"], 3)
        self.assertIn("Subagent output tokens are a lower bound", out["markdown"])
        self.assertIsNone(out["record"])

    def test_task_writes_usage_to_the_record(self):
        self.transcript("s1", [assistant("r1", "claude-opus-5-5", 1000)])
        self.transcript("s2", [assistant("r2", "claude-sonnet-5-5", 2000), assistant("r3", "mystery-model", 1)])
        path = self.record(["s1", "s2"])
        out = self.run_usage("--task", "PT-1", "--dry-run")
        self.assertEqual(frontmatter.fields(path)["usage"], [])
        out = self.run_usage("--task", "PT-1")
        self.assertEqual(out["record"], str(path))
        fields = frontmatter.fields(path)
        self.assertEqual([u["session"] for u in fields["usage"]], ["s1", "s2"])
        self.assertEqual(fields["usage"][0]["claude-opus-5-5"]["output"], 1000)
        self.assertEqual(fields["usage_total"], {"tokens": out["total"]["tokens"],
                                                 "cost": round(out["total"]["cost"], 2)})
        self.assertIn("Across 2 sessions.", out["markdown"])
        self.assertIn("Not priced, so left out of the cost: mystery-model.", out["markdown"])

    def test_weekly_estimate(self):
        self.transcript("s1", [assistant("r1", "claude-opus-5-5", 1000)])
        self.transcript("other", [assistant("r9", "claude-opus-5-5", 3000)])
        out = self.run_usage("--session", "s1", "--weekly-percent", "8", "--weekly-resets-at", "2026-10-30T00:00:00Z")
        weekly = out["weekly"]
        self.assertEqual(weekly["window_start"], "2026-10-23T00:00:00Z")
        self.assertAlmostEqual(weekly["estimate_percent"], round(8 / weekly["local_cost"] * out["total"]["cost"], 2),
                               places=1)
        self.assertIn("Weekly limit", out["markdown"])

    def test_top_results_ranks_by_tokens_times_requests(self):
        def call(request, tool_id, name, tool_input):
            line = assistant(request, "claude-opus-5-5", 1)
            line["message"]["content"] = [{"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}]
            return line

        def result(tool_id, content):
            return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tool_id,
                                                              "content": content}]}}

        lines = [
            call("r1", "t1", "Bash", {"command": "git diff --stat HEAD"}), result("t1", "x" * 4000),  # 1,000 tokens
            call("r2", "t2", "Read", {"file_path": "/a"}), result("t2", [{"type": "text", "text": "y" * 8000}]),
            call("r3", "t3", "Bash", {"command": "git diff master"}), result("t3", "z" * 400),  # 100 tokens
            {"type": "system", "subtype": "compact_boundary"},
            call("r4", "t4", "Read", {"file_path": "/b"}), result("t4", "w" * 40000),  # 10,000 tokens, 1 later request
            assistant("r5", "claude-opus-5-5", 1),
        ]
        # A sidechain record and a subagent's own results must not count.
        side = call("rs", "ts", "Bash", {"command": "ls"})
        side["isSidechain"] = True
        self.transcript("s1", lines + [side],
                        {"a1": [call("ra", "ta", "Grep", {}), result("ta", "q" * 400000)]})
        out = self.run_usage("--session", "s1", "--top-results", "3")
        rows = out["top_results"]
        # t1 stayed in context for r2 and r3, t2 for r3; the compaction clears t3, so it has none.
        self.assertEqual([(r["tool"], r["tokens"], r["requests"], r["product"]) for r in rows],
                         [("Read", 10000, 1, 10000), ("Read", 2000, 1, 2000), ("Bash git diff", 1000, 2, 2000)])
        self.assertIn("| 1 | Read | 10,000 | 1 | 10,000 |", out["markdown"])
        self.assertIn("| 3 | Bash git diff | 1,000 | 2 | 2,000 |", out["markdown"])
        self.assertEqual(len(self.run_usage("--session", "s1", "--top-results")["top_results"]), 4)
        self.assertIsNone(self.run_usage("--session", "s1")["top_results"])

    def test_top_results_label_skips_leading_cd_and_env(self):
        spec = importlib.util.spec_from_file_location("session_usage_under_test", USAGE)
        session_usage = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(session_usage)
        label = session_usage.tool_label
        self.assertEqual(label("Bash", {"command": "cd ~/x/y && git status -s"}), "Bash git status")
        self.assertEqual(label("Bash", {"command": "cd /a; cd /b && grep -rn x ."}), "Bash grep -rn")
        self.assertEqual(label("Bash", {"command": "FOO=1 BAR=2 mvn test"}), "Bash mvn test")
        self.assertEqual(label("Bash", {"command": "git diff HEAD"}), "Bash git diff")
        self.assertEqual(label("Bash", {"command": 'cd "dir with spaces" && ls -la'}), "Bash ls -la")
        self.assertEqual(label("Bash", {"command": "B=.claude/bin && $B/tasks.py queue"}), "Bash $B/tasks.py queue")
        self.assertEqual(label("Bash", {"command": "cd foo"}), "Bash cd foo")
        self.assertEqual(label("Read", {"file_path": "/a"}), "Read")

    def test_top_results_with_task_ranks_the_records_sessions_and_leaves_it_alone(self):
        def session(request, tool_id, chars):
            line = assistant(request, "claude-opus-5-5", 1)
            line["message"]["content"] = [{"type": "tool_use", "id": tool_id, "name": "Grep", "input": {}}]
            return [line, {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": tool_id, "content": "x" * chars}]}},
                assistant(request + "b", "claude-opus-5-5", 1)]
        self.transcript("s1", session("r1", "t1", 400))
        self.transcript("s2", session("r2", "t2", 4000))
        path = self.record(["s1", "s2"])
        before = path.read_text()
        out = self.run_usage("--task", "PT-1", "--top-results", "20")
        self.assertEqual([(r["session"], r["tokens"], r["requests"]) for r in out["top_results"]],
                         [("s2", 1000, 1), ("s1", 100, 1)])
        self.assertIsNone(out["record"])
        self.assertEqual(path.read_text(), before)

    def test_errors(self):
        self.transcript("s1", [assistant("r1", "claude-opus-5-5", 1)])
        self.assertIn("not a positive number", self.run_usage("--session", "s1", "--top-results", "0", ok=False))
        self.assertIn("no --session", self.run_usage(ok=False))
        self.assertIn("no transcript for session nope", self.run_usage("--session", "nope", ok=False))
        self.record([])
        self.assertIn("no sessions in", self.run_usage("--task", "PT-1", ok=False))
        self.assertIn("PT-7", self.run_usage("--task", "PT-7", ok=False))


if __name__ == "__main__":
    unittest.main()
