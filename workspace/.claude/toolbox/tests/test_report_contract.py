"""Tests for the report contract gates (`report_contract_gate.py` in workspace/.claude/hooks/gates/pre_tool_use/ and
subagent_stop/) and lib/report_contract.py.

The gates run through the real dispatcher on hook input, the way Claude Code does, against a temporary workspace whose
gate folders and `.claude/toolbox/lib` link the sources one file at a time and whose `.claude/toolbox/agents` holds the
shipped check-runner template beside agents of the test's own.

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

REPO = Path(__file__).resolve().parents[4]
WORKSPACE_SRC = REPO / "workspace" / ".claude"
DISPATCHER = REPO / "global" / "hooks" / "run_gates.py"
LIB = WORKSPACE_SRC / "toolbox" / "lib"
AGENTS = WORKSPACE_SRC / "toolbox" / "agents"

GOOD = """result: fail
mode: unit
api: fail (112 run, 1 failed, 41s)
failures:
  - api OrderServiceTest#rejectsEmptyCart: expected exception
      expected: IllegalArgumentException   actual: no exception
      at OrderServiceTest.java:58, OrderService.java:31
notes: waited 12s for a lock held by another session
"""

PLAIN = "# plain Report\n\nReturn what you found.\n"
BROKEN = "# broken Report\n\n```contract\nrequire result ^result:\n```\n"
BAD_REGEX = "# badre Report\n\n```contract\nrequire result: ^(result\n```\n"
OTHER = "# other Report\n\n```contract\nrequire verdict: ^verdict: (ok|no)$\nforbid TODO: TODO\n```\n"


def load_lib():
    spec = importlib.util.spec_from_file_location("report_contract_under_test", LIB / "report_contract.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ws = self.tmp / "ws"
        self.toolbox = self.ws / ".claude" / "toolbox"
        for event in ("pre_tool_use", "subagent_stop"):
            folder = self.ws / ".claude" / "hooks" / "gates" / event
            folder.mkdir(parents=True)
            for src in (WORKSPACE_SRC / "hooks" / "gates" / event).glob("report_contract_gate.py"):
                (folder / src.name).symlink_to(src)
        (self.toolbox / "agents").mkdir(parents=True)
        (self.toolbox / "lib").symlink_to(LIB)
        (self.toolbox / "agents" / "check-runner").symlink_to(AGENTS / "check-runner")
        for name, text in (("plain", PLAIN), ("broken", BROKEN), ("badre", BAD_REGEX), ("other", OTHER)):
            (self.toolbox / "agents" / name).mkdir()
            (self.toolbox / "agents" / name / "report.md").write_text(text, encoding="utf-8")
        (self.ws / "api").mkdir()
        self.hooks = self.tmp / "home" / ".claude" / "hooks"
        self.hooks.mkdir(parents=True)
        shutil.copy(DISPATCHER, self.hooks / "run_gates.py")
        self.env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}

    def dispatch(self, event: dict):
        event = {"cwd": str(self.ws / "api"), **event}
        return subprocess.run([sys.executable, str(self.hooks / "run_gates.py")], input=json.dumps(event),
                              capture_output=True, text=True, timeout=30, env=self.env)

    def handback(self, report, agent: str = "check-runner") -> str | None:
        """The denial reason for this handback, or None when it goes through."""
        result = self.dispatch({"hook_event_name": "PreToolUse", "tool_name": "SubagentHandback",
                                "tool_input": {"message": report}, "agent_type": agent, "agent_id": "a1"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        if not result.stdout:
            return None
        out = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(out["permissionDecision"], "deny")
        return out["permissionDecisionReason"]

    def transcript(self, *, handed_back: bool) -> Path:
        path = self.tmp / "agent.jsonl"
        lines = [{"type": "assistant", "message": {"content": [{"type": "text", "text": "running"}]}}]
        if handed_back:
            lines.append({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "SubagentHandback", "input": {"message": "x"}}]}})
        path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")
        return path

    def stop(self, text, agent: str = "check-runner", transcript: object = "unset", **extra) -> str | None:
        """The block reason for this SubagentStop, or None when the agent may stop."""
        if transcript == "unset":
            transcript = self.transcript(handed_back=False)
        event = {"hook_event_name": "SubagentStop", "agent_type": agent, "agent_id": "a1",
                 "last_assistant_message": text, "agent_transcript_path": str(transcript),
                 "stop_hook_active": False, **extra}
        result = self.dispatch(event)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        if not result.stdout:
            return None
        out = json.loads(result.stdout)
        self.assertEqual(out["decision"], "block")
        return out["reason"]


class HandbackGateTest(ContractTest):
    def test_a_good_report_goes_through(self) -> None:
        self.assertIsNone(self.handback(GOOD))

    def test_a_report_with_a_target_path_is_denied_naming_it_and_the_corrected_one_goes_through(self) -> None:
        bad = GOOD + "notes: see api/target/surefire-reports/OrderServiceTest.txt\n"
        reason = self.handback(bad)
        self.assertIn("log path", reason)
        self.assertIn("api/target/surefire-reports/OrderServiceTest.txt", reason)
        self.assertIn("check-runner", reason)
        self.assertIsNone(self.handback(GOOD))

    def test_a_report_with_a_log_file_is_denied_naming_it(self) -> None:
        reason = self.handback(GOOD + "notes: started api, output in /tmp/api-start.log\n")
        self.assertIn("log file", reason)
        self.assertIn("/tmp/api-start.log", reason)

    def test_a_bare_log_name_and_a_log_call_are_not_log_files(self) -> None:
        text = GOOD + "notes: Logger.log(...) was expected; build.log4j set\n"
        self.assertIsNone(self.handback(text))

    def test_a_report_without_its_result_line_is_denied_naming_the_field(self) -> None:
        reason = self.handback(GOOD.replace("result: fail\n", ""))
        self.assertIn("missing result", reason)
        self.assertNotIn("missing mode", reason)

    def test_a_result_that_is_not_one_of_the_values_counts_as_missing(self) -> None:
        self.assertIn("missing result", self.handback(GOOD.replace("result: fail", "result: mostly ok")))

    def test_every_problem_is_named_at_once(self) -> None:
        reason = self.handback("api: pass\nnotes: /w/api/target/x.log\n")
        for word in ("missing result", "missing mode", "log path", "log file"):
            self.assertIn(word, reason)

    def test_a_raw_stack_dump_over_the_limit_is_denied(self) -> None:
        def dump(n: int) -> str:
            return "".join(f"    at com.acme.Service.m{i}(Service.java:{i})\n" for i in range(n))

        self.assertIn("stack frame lines: 26 lines, at most 25", self.handback(GOOD + dump(26)))
        self.assertIsNone(self.handback(GOOD + dump(25)))

    def test_an_agent_without_a_contract_goes_through_unchanged(self) -> None:
        bad = "no fields at all, see target/x.log\n"
        self.assertIsNone(self.handback(bad, agent="plain"))  # a template with no contract block
        self.assertIsNone(self.handback(bad, agent="no-template"))  # no template at all
        self.assertIsNone(self.handback(bad, agent="my-plugin:reviewer"))  # a plugin's agent
        self.assertIsNone(self.handback(bad, agent="../../etc"))  # never a path out of agents/
        self.assertIsNone(self.handback(bad, agent=""))

    def test_each_agent_is_held_to_its_own_template(self) -> None:
        self.assertIn("missing verdict", self.handback("result: pass\nmode: unit\n", agent="other"))
        self.assertIsNone(self.handback("verdict: ok\n", agent="other"))
        self.assertIn("TODO", self.handback("verdict: ok\nTODO later\n", agent="other"))

    def test_a_message_that_is_not_text_goes_through(self) -> None:
        self.assertIsNone(self.handback(None))
        self.assertIsNone(self.handback({"a": 1}))

    def test_a_broken_contract_fails_open_and_names_itself_on_stderr(self) -> None:
        for agent in ("broken", "badre"):
            result = self.dispatch({"hook_event_name": "PreToolUse", "tool_name": "SubagentHandback",
                                    "tool_input": {"message": "anything"}, "agent_type": agent})
            self.assertEqual((result.returncode, result.stdout), (0, ""))
            self.assertIn("gate pre_tool_use/report_contract_gate", result.stderr)

    def test_other_tools_are_not_looked_at(self) -> None:
        result = self.dispatch({"hook_event_name": "PreToolUse", "tool_name": "Bash",
                                "tool_input": {"command": "ls"}, "agent_type": "check-runner"})
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))


class StopGateTest(ContractTest):
    def test_a_good_report_may_stop(self) -> None:
        self.assertIsNone(self.stop(GOOD))

    def test_a_report_with_a_log_path_is_blocked_naming_it(self) -> None:
        reason = self.stop(GOOD + "notes: /repo/api/target/failsafe-reports/it.txt\n")
        self.assertIn("log path", reason)
        self.assertIn("api/target/failsafe-reports/it.txt", reason)

    def test_a_report_missing_a_field_is_blocked_naming_it(self) -> None:
        self.assertIn("missing result", self.stop(GOOD.replace("result: fail\n", "")))

    def test_an_agent_without_a_contract_may_stop_whatever_it_says(self) -> None:
        for agent in ("plain", "no-template", ""):
            self.assertIsNone(self.stop("target/x.log", agent=agent))

    def test_an_agent_that_handed_back_is_left_to_the_handback_gate(self) -> None:
        done = self.transcript(handed_back=True)
        self.assertIsNone(self.stop("Done. Report delivered.", transcript=done))
        self.assertIsNone(self.stop("/repo/target/x.log", transcript=done))

    def test_an_unreadable_or_missing_transcript_stays_out_of_the_way(self) -> None:
        self.assertIsNone(self.stop("no fields", transcript=self.tmp / "gone.jsonl"))
        self.assertIsNone(self.stop("no fields", transcript=""))
        event = {"hook_event_name": "SubagentStop", "agent_type": "check-runner", "last_assistant_message": "no fields"}
        self.assertEqual(self.dispatch(event).stdout, "")

    def test_a_stop_that_continues_from_a_block_is_not_blocked_again(self) -> None:
        self.assertIsNone(self.stop("no fields", stop_hook_active=True))

    def test_a_message_that_is_not_text_may_stop(self) -> None:
        self.assertIsNone(self.stop(None))

    def test_a_broken_contract_fails_open_and_names_itself_on_stderr(self) -> None:
        path = self.transcript(handed_back=False)
        result = self.dispatch({"hook_event_name": "SubagentStop", "agent_type": "broken",
                                "last_assistant_message": "x", "agent_transcript_path": str(path)})
        self.assertEqual((result.returncode, result.stdout), (0, ""))
        self.assertIn("gate subagent_stop/report_contract_gate", result.stderr)


class ShippedTemplateTest(unittest.TestCase):
    """The check-runner template that ships declares a contract its own example report satisfies."""

    def setUp(self) -> None:
        self.lib = load_lib()
        self.template = (AGENTS / "check-runner" / "report.md").read_text(encoding="utf-8")
        self.contract = self.lib.parse("check-runner", AGENTS / "check-runner" / "report.md", self.template)

    def test_it_declares_a_contract(self) -> None:
        self.assertIsNotNone(self.contract)
        kinds = {(rule.kind, rule.what) for rule in self.contract.rules}
        self.assertIn(("require", "result"), kinds)
        self.assertIn(("forbid", "log path"), kinds)

    def test_the_templates_example_block_is_not_itself_a_report_that_breaks_it_apart_from_placeholders(self) -> None:
        example = self.template.split("```")[1]
        found = self.lib.problems(self.contract, example)
        self.assertEqual(found, ["missing result", "missing mode"])  # `result: pass | fail | blocked` is the pattern

    def test_a_filled_report_with_every_line_kind_passes(self) -> None:
        report = (
            "result: fail\nmode: integration\napi: fail (40 run, 2 failed, 90s)\n"
            "web: skipped (0 run, 0 failed, 0s) no tests\n"
            "failures:\n  - api CartIT#total: expected 3\n      expected: 3   actual: 2\n"
            "      at CartIT.java:40, Cart.java:12\n"
            "services: started api (ports 8080)\nlock: released\nnotes: waited 4s on a lock held by another session\n"
        )
        self.assertEqual(self.lib.problems(self.contract, report), [])

    def test_paths_that_are_not_logs_pass(self) -> None:
        report = "result: pass\nmode: build\napi: pass\nnotes: built in api/src/main/java/Target.java, retargeted\n"
        self.assertEqual(self.lib.problems(self.contract, report), [])


class ParseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.lib = load_lib()
        self.path = Path("report.md")

    def parse(self, text: str):
        return self.lib.parse("a", self.path, text)

    def test_no_block_is_no_contract(self) -> None:
        self.assertIsNone(self.parse("# A Report\n\nno block\n"))
        self.assertIsNone(self.parse("```\nrequire x: y\n```\n"))  # an untagged fence is just an example

    def test_comments_and_blank_lines_are_skipped_and_several_blocks_add_up(self) -> None:
        contract = self.parse(
            "```contract\n# a note\n\nrequire a: ^a$\n```\ntext\n```contract\nforbid b: b\nlimit 3 c: c\n```\n")
        self.assertEqual([(r.kind, r.what, r.limit) for r in contract.rules],
                         [("require", "a", 0), ("forbid", "b", 0), ("limit", "c", 3)])

    def test_a_regex_may_hold_colons(self) -> None:
        contract = self.parse("```contract\nrequire the result: ^result: (pass|fail)$\n```\n")
        self.assertEqual(contract.rules[0].what, "the result")
        self.assertTrue(contract.rules[0].pattern.search("result: pass"))

    def test_a_bad_line_or_regex_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.parse("```contract\nrequire result ^x\n```\n")
        with self.assertRaises(ValueError):
            self.parse("```contract\nrequire result: ^(x\n```\n")
        with self.assertRaises(ValueError):
            self.parse("```contract\nlimit many dumps: x\n```\n")

    def test_handed_back_reads_the_transcript(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        plain = tmp / "a.jsonl"
        plain.write_text('{"text": "I will use SubagentHandback later"}\n', encoding="utf-8")
        used = tmp / "b.jsonl"
        used.write_text('{"type":"tool_use","name":"SubagentHandback","input":{}}\n', encoding="utf-8")
        self.assertIs(self.lib.handed_back(str(plain)), False)
        self.assertIs(self.lib.handed_back(str(used)), True)
        self.assertIsNone(self.lib.handed_back(str(tmp / "none.jsonl")))
        self.assertIsNone(self.lib.handed_back(None))


if __name__ == "__main__":
    unittest.main()
