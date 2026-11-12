"""Tests for hooks/run_gates.py: denial, MAIN_ONLY, fail-open, name order, removal mid-session, linked install.

Each test copies the dispatcher beside a temporary gates folder and runs it the way Claude Code does: hook input as
JSON on stdin.

Run from the repo root:

    python3 -m unittest discover -s workspace/.claude/toolbox/tests
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[2] / "hooks"
SCRIPT = HOOKS / "run_gates.py"

BLOCK_ECHO = '''
TOOLS = {"Bash"}
MAIN_ONLY = True

def check(event):
    if event["tool_input"].get("command") == "echo blocked":
        return "blocked by test gate; use the test agent"
    return None
'''

RAISES = '''
TOOLS = {"Bash"}
MAIN_ONLY = False

def check(event):
    raise RuntimeError("boom")
'''


def bash(command: str, **extra) -> dict:
    return {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}, **extra}


class RunGatesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.hooks = self.tmp / "hooks"
        self.folder = self.hooks / "gates" / "pre_tool_use"
        self.folder.mkdir(parents=True)
        shutil.copy(SCRIPT, self.hooks / "run_gates.py")

    def gate(self, name: str, source: str) -> Path:
        path = self.folder / f"{name}.py"
        path.write_text(source)
        return path

    def run_gates(self, event, script: Path | None = None, raw: str | None = None):
        return subprocess.run(
            [sys.executable, str(script or self.hooks / "run_gates.py")],
            input=raw if raw is not None else json.dumps(event),
            capture_output=True, text=True, timeout=30,
        )

    def denial(self, result) -> str | None:
        self.assertEqual(result.returncode, 0, result.stderr)
        if not result.stdout:
            return None
        out = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "PreToolUse")
        self.assertEqual(out["permissionDecision"], "deny")
        return out["permissionDecisionReason"]

    def test_gate_denies_in_the_main_session(self) -> None:
        self.gate("block_echo", BLOCK_ECHO)
        result = self.run_gates(bash("echo blocked"))
        self.assertEqual(self.denial(result), "blocked by test gate; use the test agent")

    def test_other_calls_and_tools_pass(self) -> None:
        self.gate("block_echo", BLOCK_ECHO)
        self.assertIsNone(self.denial(self.run_gates(bash("echo fine"))))
        other = {"hook_event_name": "PreToolUse", "tool_name": "Read", "tool_input": {"command": "echo blocked"}}
        self.assertIsNone(self.denial(self.run_gates(other)))

    def test_same_call_from_an_agent_runs(self) -> None:
        self.gate("block_echo", BLOCK_ECHO)
        result = self.run_gates(bash("echo blocked", agent_id="a1", agent_type="check-runner"))
        self.assertIsNone(self.denial(result))

    def test_gate_without_main_only_applies_to_agents_too(self) -> None:
        self.gate("block_echo", BLOCK_ECHO.replace("MAIN_ONLY = True", "MAIN_ONLY = False"))
        result = self.run_gates(bash("echo blocked", agent_type="check-runner"))
        self.assertIsNotNone(self.denial(result))

    def test_broken_gate_fails_open_and_names_itself_on_stderr(self) -> None:
        self.gate("a_broken", RAISES)
        result = self.run_gates(bash("echo hi"))
        self.assertIsNone(self.denial(result))
        self.assertIn("gate pre_tool_use/a_broken: RuntimeError: boom", result.stderr)

    def test_broken_gate_does_not_hide_a_later_denial(self) -> None:
        self.gate("a_broken", RAISES)
        self.gate("b_block", BLOCK_ECHO)
        result = self.run_gates(bash("echo blocked"))
        self.assertIsNotNone(self.denial(result))
        self.assertIn("a_broken", result.stderr)

    def test_gate_that_fails_to_load_or_returns_junk_is_skipped(self) -> None:
        self.gate("a_syntax", "def check(:\n")
        self.gate("b_missing", "MAIN_ONLY = True\n")
        self.gate("c_junk", 'TOOLS = {"Bash"}\nMAIN_ONLY = False\ndef check(event):\n    return 3\n')
        result = self.run_gates(bash("echo hi"))
        self.assertIsNone(self.denial(result))
        for name in ("a_syntax", "b_missing", "c_junk"):
            self.assertIn(f"gate pre_tool_use/{name}:", result.stderr)

    def test_first_denial_in_name_order_wins(self) -> None:
        deny = 'TOOLS = {{"Bash"}}\nMAIN_ONLY = False\ndef check(event):\n    return "{}"\n'
        self.gate("b_second", deny.format("second"))
        self.gate("a_first", deny.format("first"))
        self.assertEqual(self.denial(self.run_gates(bash("x"))), "first")

    def test_gate_removed_mid_session_no_longer_runs_and_shows_no_error(self) -> None:
        path = self.gate("block_echo", BLOCK_ECHO)
        self.assertIsNotNone(self.denial(self.run_gates(bash("echo blocked"))))
        path.unlink()
        result = self.run_gates(bash("echo blocked"))
        self.assertIsNone(self.denial(result))
        self.assertEqual(result.stderr, "")

    def test_underscore_files_are_not_gates_and_no_bytecode_is_written(self) -> None:
        self.gate("_helper", BLOCK_ECHO)
        self.gate("block_echo", BLOCK_ECHO)
        self.assertIsNotNone(self.denial(self.run_gates(bash("echo blocked"))))
        self.gate("block_echo", "TOOLS = set()\nMAIN_ONLY = True\ndef check(event):\n    return None\n")
        self.assertIsNone(self.denial(self.run_gates(bash("echo blocked"))))
        self.assertEqual(list(self.hooks.rglob("__pycache__")), [])

    def test_nothing_to_run_exits_clean(self) -> None:
        shutil.rmtree(self.hooks / "gates")
        for raw in (json.dumps(bash("x")), "", "not json", "[1]"):
            result = self.run_gates(None, raw=raw)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""), raw)
        other = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {}}
        self.assertEqual(self.run_gates(other).stdout, "")

    def test_events_read_their_own_folder(self) -> None:
        post = self.hooks / "gates" / "post_tool_use"
        post.mkdir()
        (post / "g.py").write_text('TOOLS = {"Bash"}\nMAIN_ONLY = False\ndef check(event):\n    return "post"\n')
        self.gate("block_echo", BLOCK_ECHO)
        event = bash("echo blocked", hook_event_name="PostToolUse")
        out = json.loads(self.run_gates(event).stdout)
        self.assertEqual(out, {"decision": "block", "reason": "post"})

    def test_bare_string_tools_is_a_broken_gate(self) -> None:
        self.gate("a_str", BLOCK_ECHO.replace('{"Bash"}', '"Bash Read"'))
        result = self.run_gates(bash("echo blocked"))
        self.assertIsNone(self.denial(result))
        self.assertIn("gate pre_tool_use/a_str: TypeError", result.stderr)

    def test_gate_failing_with_file_not_found_still_warns(self) -> None:
        source = BLOCK_ECHO.replace('return "blocked by test gate; use the test agent"', 'open("/no/such/file")')
        self.gate("a_missing_file", source)
        result = self.run_gates(bash("echo blocked"))
        self.assertIsNone(self.denial(result))
        self.assertIn("gate pre_tool_use/a_missing_file: FileNotFoundError", result.stderr)

    def test_linked_dispatcher_finds_gates_beside_the_link(self) -> None:
        """The installer links files one at a time: gates sit beside the link, not beside its target."""
        workspace = self.tmp / "workspace" / ".claude" / "hooks"
        (workspace / "gates" / "pre_tool_use").mkdir(parents=True)
        link = workspace / "run_gates.py"
        link.symlink_to(SCRIPT)
        (workspace / "gates" / "pre_tool_use" / "block_echo.py").write_text(BLOCK_ECHO)
        self.assertIsNotNone(self.denial(self.run_gates(bash("echo blocked"), script=link)))

    def test_shipped_example_gate_is_a_no_op(self) -> None:
        shutil.copytree(HOOKS / "gates", self.hooks / "gates", dirs_exist_ok=True)
        result = self.run_gates(bash("echo blocked"))
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))


if __name__ == "__main__":
    unittest.main()
