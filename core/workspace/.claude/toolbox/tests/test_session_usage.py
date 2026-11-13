"""Tests for bin/session_usage.py, against hand-written transcripts in a scratch CLAUDE_CONFIG_DIR and a task record in
a scratch workspace.

Run from the repo root:

    python3 -m unittest discover -s core/workspace/.claude/toolbox/tests
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
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from toolbox_tree import TOOLBOX  # noqa: E402
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


BASE = datetime(2026, 10, 27, 10, 0, 0, tzinfo=timezone.utc)


def at(seconds: float) -> str:
    return (BASE + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def prompt(seconds: float) -> dict:
    return {"type": "user", "origin": {"kind": "human"}, "timestamp": at(seconds),
            "message": {"role": "user", "content": "go on"}}


def notification(seconds: float) -> dict:
    return {"type": "user", "origin": {"kind": "task-notification"}, "timestamp": at(seconds),
            "message": {"role": "user", "content": "<task-notification>CI finished</task-notification>"}}


def event(seconds: float, *calls: tuple[str, dict]) -> dict:
    """An assistant record at `seconds`, calling the given (tool, input) pairs."""
    line = assistant(f"r{seconds}", "claude-opus-5-5", 1, when=at(seconds))
    line["message"]["content"] = [{"type": "tool_use", "id": f"t{seconds}-{i}", "name": name, "input": tool_input}
                                  for i, (name, tool_input) in enumerate(calls)]
    return line


def turns(*spec: tuple[float, float, list]) -> list[dict]:
    """One turn per (start, end, tool calls): your prompt at start, an assistant event at end."""
    return [line for start, end, calls in spec for line in (prompt(start), event(end, *calls))]


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
                                                 "cost": round(out["total"]["cost"], 2),
                                                 "time": {"claude": 0, "user": 0, "total": 0}})
        self.assertEqual(fields["usage"][0]["time"], {"claude": 0, "user": 0})
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

    def seconds(self, out: dict, session: str) -> tuple[int, int, int]:
        t = out["time"]["sessions"][session]
        return t["claude"], t["user"], t["total"]

    def test_time_of_a_task_worked_in_two_sessions(self):
        # s1 is left idle past the hour before its last turn; s2 is a later session.
        self.transcript("s1", turns((0, 60, []), (4060, 4090, [])))
        self.transcript("s2", turns((10000, 10300, [])))
        path = self.record(["s1", "s2"])
        out = self.run_usage("--task", "PT-1")
        # s1: Claude 60 + 30; your gap of 4,000 s is capped at 10 minutes; nothing after its last event.
        self.assertEqual(self.seconds(out, "s1"), (90, 600, 690))
        self.assertEqual(self.seconds(out, "s2"), (300, 0, 300))
        self.assertEqual(out["time"]["total"], {"claude": 390, "user": 600, "total": 990})
        self.assertIn("| s1 | 1m 30s | 10m 00s | 11m 30s |", out["markdown"])
        self.assertIn("| s2 | 5m 00s | 0s | 5m 00s |", out["markdown"])
        self.assertIn("| **Total** | **6m 30s** | **10m 00s** | **16m 30s** |", out["markdown"])
        fields = frontmatter.fields(path)
        self.assertEqual([u["time"] for u in fields["usage"]],
                         [{"claude": 90, "user": 600}, {"claude": 300, "user": 0}])
        self.assertEqual(fields["usage_total"]["time"], {"claude": 390, "user": 600, "total": 990})

    def test_long_gap_before_a_prompt_adds_ten_minutes(self):
        self.transcript("s1", turns((0, 10, []), (10 + 25 * 60, 10 + 25 * 60 + 10, [])))
        self.record(["s1"])
        self.assertEqual(self.seconds(self.run_usage("--task", "PT-1"), "s1"), (20, 600, 620))

    def test_waiting_on_ci_adds_nothing(self):
        # The turn ends waiting on CI; the notification starts the next one an hour later.
        lines = [prompt(0), event(100), notification(3700), event(3750), prompt(3780), event(3790)]
        self.transcript("s1", lines)
        self.record(["s1"])
        # Claude: 100 + 50 + 10. Yours: only the 30 s between the notification turn and your next prompt.
        self.assertEqual(self.seconds(self.run_usage("--task", "PT-1"), "s1"), (160, 30, 190))

    def test_only_prompts_and_notifications_start_turns(self):
        tool_result = {"type": "user", "timestamp": at(5), "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t", "content": "ok"}]}}
        skill_text = {"type": "user", "isMeta": True, "timestamp": at(6),
                      "message": {"content": [{"type": "text", "text": "Base directory for this skill: x"}]}}
        interrupt = {"type": "user", "timestamp": at(20),
                     "message": {"content": [{"type": "text", "text": "[Request interrupted by user for tool use]"}]}}
        hand_back = {"type": "user", "isMeta": True, "origin": {"kind": "peer"}, "timestamp": at(500),
                     "message": {"content": "Another Claude session sent a message:"}}
        # Records with no timestamp or an unrelated type (diagnostics) never count either.
        noise = [{"type": "last-prompt"}, {"type": "api-request", "timestamp": at(900)}]
        self.transcript("s1", [prompt(0), tool_result, skill_text, event(10), interrupt, prompt(30), event(40),
                               hand_back, event(520), *noise])
        self.assertEqual(self.seconds(self.run_usage("--session", "s1"), "s1"), (20 + 10 + 20, 10, 60))

    def test_a_compaction_summary_with_a_stale_timestamp_adds_no_time(self):
        # Written at idle-compaction time with an old timestamp, though it sits in the file after the prompt that
        # triggered the compaction.
        summary = {"type": "user", "isMeta": True, "isCompactSummary": True, "timestamp": at(500),
                   "message": {"content": "This session is being continued from a previous conversation"}}
        stale = {"type": "user", "isMeta": True, "timestamp": at(510),
                 "message": {"content": [{"type": "text", "text": "This session is being continued"}]}}
        self.transcript("s1", [prompt(0), event(10), prompt(4000), event(4010), stale, summary, event(4020)])
        self.assertEqual(self.seconds(self.run_usage("--session", "s1"), "s1"), (10 + 20, 600, 630))

    def test_subagent_events_extend_the_turn(self):
        self.transcript("s1", turns((0, 10, []), (100, 110, [])),
                        {"a1": [event(40), event(75)]})
        # The subagent's work runs to 75 s, so the first turn ends there and your gap shrinks to 25 s.
        self.assertEqual(self.seconds(self.run_usage("--session", "s1"), "s1"), (75 + 10, 25, 110))

    def test_an_attachment_written_just_before_a_prompt_starts_no_idle_time(self):
        # A resume's attachment lands a millisecond ahead of the prompt that triggered it: it isn't the old turn's.
        late = {"type": "attachment", "timestamp": (BASE + timedelta(seconds=499.999)).isoformat(),
                "attachment": {"type": "deferred_tools_delta"}}
        self.transcript("s1", turns((0, 10, [])) + [late] + turns((500, 530, [])))
        out = self.run_usage("--session", "s1")
        self.assertEqual(out["time"]["sessions"]["s1"], {"claude": 10 + 30, "user": 490, "total": 530})

    def test_session_without_a_task_counts_whole_and_top_results_skip_time(self):
        self.transcript("s1", turns((0, 10, [])))
        self.assertEqual(self.run_usage("--session", "s1")["time"]["total"], {"claude": 10, "user": 0, "total": 10})
        self.assertIsNone(self.run_usage("--session", "s1", "--top-results")["time"])

    def second_record(self) -> None:
        subprocess.run([sys.executable, str(TASKS), "create-defect", "--summary", "Jar Label Smudged"],
                       capture_output=True, text=True, env=self.env, cwd=self.root, check=True)

    def test_a_session_spanning_two_tasks_is_split_by_turn(self):
        wt = self.root / ".worktrees"
        edit_1 = ("Edit", {"file_path": str(wt / "PT-1" / "a.py")})
        edit_2 = ("Edit", {"file_path": str(wt / "PT-2" / "b.py")})
        named_1 = ("Bash", {"command": "/x/.claude/toolbox/bin/tasks.py context PT-1 --session s1"})
        named_2 = ("Bash", {"command": "/x/.claude/toolbox/bin/tasks.py set PT-2 status=in-progress"})
        self.transcript("s1", turns(
            (0, 5, []),                   # before any item: goes to the first one touched, PT-1
            (15, 25, [named_1]),          # PT-1
            (45, 65, []),                 # touches nothing: the previous turn's item, PT-1
            (85, 115, [edit_2]),          # PT-2
            (145, 155, [edit_1, named_2]),  # both: split evenly
        ))
        self.record(["s1"])
        self.second_record()
        out = self.run_usage("--task", "PT-1", "--dry-run")
        # Gaps after each turn: 10, 20, 20, 30. PT-1: turns 1-3 whole, half of 5.
        self.assertEqual(self.seconds(out, "s1"), (40, 50, 90))
        subprocess.run([sys.executable, str(TASKS), "append", "PT-2", "sessions", "s1"], env=self.env, cwd=self.root,
                       check=True, capture_output=True)
        out = self.run_usage("--task", "PT-2", "--dry-run")
        self.assertEqual(self.seconds(out, "s1"), (30 + 5, 30, 65))

    def test_sessions_that_wrote_to_the_task_add_time_only(self):
        wt = self.root / ".worktrees"
        write = ("Write", {"file_path": str(wt / "PT-1" / "a.py")})
        self.transcript("s1", [assistant("r1", "claude-opus-5-5", 10, when=at(0))] + turns((0, 20, [])))
        # s2 isn't in the record's sessions but wrote into the task's worktree; s3 only read it; s4 is somewhere else.
        self.transcript("s2", [assistant("r2", "claude-opus-5-5", 999, when=at(0))] + turns((100, 160, [write])))
        self.transcript("s3", turns((200, 260, [("Read", {"file_path": str(wt / "PT-1" / "a.py")}),
                                                ("Bash", {"command": "cat PT-1.md"})])))
        self.transcript("s4", turns((300, 360, [("Write", {"file_path": str(wt / "PT-9" / "a.py")})])))
        # s5 only named the task in a read-only call; s6 recorded progress with tasks.py set.
        self.transcript("s5", turns((400, 460, [("Bash", {"command": "tasks.py context PT-1"})])))
        self.transcript("s6", turns((500, 520, [("Bash", {"command": "tasks.py set PT-1 owner=claude"})])))
        path = self.record(["s1"])
        out = self.run_usage("--task", "PT-1")
        self.assertEqual(list(out["time"]["sessions"]), ["s1", "s2", "s6"])
        self.assertEqual(self.seconds(out, "s2"), (60, 0, 60))
        self.assertEqual(out["time"]["total"], {"claude": 100, "user": 0, "total": 100})
        # Tokens and cost stay with the record's own sessions.
        self.assertEqual(out["by_model"]["claude-opus-5-5"]["output"], 10 + 1)
        fields = frontmatter.fields(path)
        self.assertEqual(fields["usage"][1], {"session": "s2", "time": {"claude": 60, "user": 0}})
        self.assertEqual(fields["usage"][0]["time"], {"claude": 20, "user": 0})
        self.assertEqual(fields["usage_total"]["time"], {"claude": 100, "user": 0, "total": 100})
        self.assertIn("Time also counts 2 sessions that wrote to the task", out["markdown"])

    def discussion(self, name: str = "2026-10-20-pantry-shelves") -> Path:
        folder = self.root / "vault" / "Discussions" / "2026" / "10" / name
        (folder / "tickets").mkdir(parents=True)
        (folder / "plan.md").write_text("---\ntype: plan\ncreated: 2026-10-20\n---\n\n# Pantry Shelves\n")
        return folder

    def epic(self) -> Path:
        """A filed epic's record, as `tasks.py file` leaves it, from the template."""
        path = self.root / "vault" / "Tasks" / "PT-1" / "PT-1-pantry-shelves.md"
        path.parent.mkdir(parents=True)
        text = (TOOLBOX / "templates" / "epic.md").read_text()
        for name, value in {"key": "PT-1", "summary": "Pantry Shelves", "created": "2026-10-20", "legacy_id": "",
                            "source": "", "source_doc": ""}.items():
            text = text.replace("{{" + name + "}}", value)
        path.write_text(text)
        return path

    def test_discussion_time_is_the_sessions_that_wrote_into_the_folder(self):
        folder = self.discussion()
        plan = ("Edit", {"file_path": str(folder / "plan.md")})
        draft = ("Write", {"file_path": str(folder / "tickets" / "01-shelves.md")})
        self.transcript("s1", turns((0, 30, [plan]), (50, 80, [])))
        self.transcript("s2", turns((1000, 1100, [draft])))
        self.transcript("s3", turns((2000, 2100, [("Read", {"file_path": str(folder / "plan.md")})])))
        self.transcript("s4", turns((3000, 3100, [("Write", {"file_path": str(folder) + "-other/plan.md"})])))
        out = self.run_usage("--discussion", str(folder))
        # s1: the second turn follows a write with no other item, so it counts; gap 20s. s3 only read, s4 is a sibling.
        self.assertEqual(list(out["time"]["sessions"]), ["s1", "s2"])
        self.assertEqual(out["time"]["sessions"]["s1"], {"claude": 60, "user": 20, "total": 80})
        self.assertEqual(out["time"]["total"], {"claude": 160, "user": 20, "total": 180})
        self.assertEqual(out["missing_sessions"], [])
        self.assertIsNone(out["record"])
        self.assertIn("Across 2 sessions.", out["markdown"])
        # Found by the folder's name too.
        self.assertEqual(self.run_usage("--discussion", "2026-10-20-pantry-shelves")["time"]["total"],
                         out["time"]["total"])

    def test_a_session_that_also_worked_a_task_counts_only_its_discussion_turns(self):
        folder = self.discussion()
        self.record([])
        wt = self.root / ".worktrees"
        plan = ("Edit", {"file_path": str(folder / "plan.md")})
        task = ("Edit", {"file_path": str(wt / "PT-1" / "a.py")})
        self.transcript("s1", turns((0, 10, [plan]), (30, 50, []), (70, 100, [task]), (130, 170, [])))
        out = self.run_usage("--discussion", str(folder))
        # Turns 1 and 2 are the discussion's (2 follows a write with no other item); 3 and 4 are PT-1's.
        self.assertEqual(out["time"]["sessions"]["s1"], {"claude": 10 + 20, "user": 20 + 20, "total": 70})

    def test_discussion_time_is_recorded_on_the_record_and_survives_expired_transcripts(self):
        folder = self.discussion()
        path = self.epic()
        plan = ("Edit", {"file_path": str(folder / "plan.md")})
        self.transcript("s1", turns((0, 60, [plan]), (100, 120, [])))
        self.transcript("s2", turns((600, 700, [plan])))
        out = self.run_usage("--discussion", str(folder), "--record", "PT-1", "--dry-run")
        self.assertIsNone(frontmatter.fields(path).get("planning_time"))
        self.assertTrue(out["dry_run"])
        out = self.run_usage("--discussion", str(folder), "--record", "PT-1")
        self.assertEqual(out["record"], str(path))
        held = frontmatter.fields(path)["planning_time"]
        self.assertEqual(held, {"claude": 180, "user": 40, "total": 220, "sessions": [
            {"session": "s1", "claude": 80, "user": 40}, {"session": "s2", "claude": 100, "user": 0}]})
        # s1's transcript expires; the roll-up is run again, and by now s3 has written too.
        (self.project / "s1.jsonl").unlink()
        self.transcript("s3", turns((900, 930, [plan])))
        out = self.run_usage("--discussion", str(folder), "--record", "PT-1")
        self.assertEqual(out["missing_sessions"], ["s1"])
        self.assertEqual(out["time"]["total"], {"claude": 80 + 100 + 30, "user": 40, "total": 250})
        self.assertIn("1 named session no longer has a transcript", out["markdown"])
        self.assertIn("(1 of 1)", out["markdown"])
        self.assertEqual([e["session"] for e in frontmatter.fields(path)["planning_time"]["sessions"]],
                         ["s2", "s3", "s1"])

    def test_a_named_session_without_a_transcript_and_no_earlier_time_is_reported(self):
        folder = self.discussion()
        self.transcript("s1", turns((0, 60, [("Edit", {"file_path": str(folder / "plan.md")})])))
        out = self.run_usage("--discussion", str(folder), "--session", "old")
        self.assertEqual(out["missing_sessions"], ["old"])
        self.assertEqual(list(out["time"]["sessions"]), ["s1"])
        self.assertIn("(0 of 1)", out["markdown"])

    def test_discussion_with_no_sessions_writes_nothing(self):
        folder = self.discussion()
        path = self.epic()
        self.transcript("s1", turns((0, 60, [])))
        out = self.run_usage("--discussion", str(folder), "--record", "PT-1")
        self.assertEqual(out["time"]["total"], {"claude": 0, "user": 0, "total": 0})
        self.assertIsNone(out["record"])
        self.assertIsNone(frontmatter.fields(path).get("planning_time"))
        self.assertIn("No session wrote into", out["markdown"])

    def test_a_discussion_reached_through_a_symlink_is_found(self):
        folder = self.discussion()
        link = self.tmp / "link"
        link.symlink_to(self.root / "vault")
        via_link = link / "Discussions" / "2026" / "10" / folder.name / "plan.md"
        self.transcript("s1", turns((0, 60, [("Edit", {"file_path": str(via_link)})])))
        self.assertEqual(self.run_usage("--discussion", str(folder))["time"]["total"]["claude"], 60)

    def test_discussion_errors(self):
        self.assertIn("no such discussion", self.run_usage("--discussion", "nope", ok=False))
        folder = self.discussion()
        self.assertIn("PT-7", self.run_usage("--discussion", str(folder), "--record", "PT-7", ok=False))
        self.assertIn("--record goes with --discussion", self.run_usage("--record", "PT-1", ok=False))
        self.assertIn("takes only", self.run_usage("--discussion", str(folder), "--task", "PT-1", ok=False))

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
