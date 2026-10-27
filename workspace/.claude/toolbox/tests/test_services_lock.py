"""Tests for bin/services_lock.py: acquire, wait, expiry, takeover of each kind, tokens, and a start/stop round trip.

Each test gets a temporary workspace and lock folder, and harmless fake commands (`sleep`, `python3 -m http.server`).

Run from the repo root:

    python3 -m unittest discover -s workspace/.claude/toolbox/tests
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

TOOLBOX = Path(__file__).resolve().parents[1]
SCRIPT = TOOLBOX / "bin" / "services_lock.py"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class LockCase(unittest.TestCase):
    """A workspace named `stack` with a lock folder of its own; run() calls the script in it."""

    areas: dict = {}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = self.tmp / "ws"
        (self.root / ".claude").mkdir(parents=True)
        self.locks = self.tmp / "locks"
        self.started: list[int] = []
        self.addCleanup(self.kill_started)
        self.write_config(self.areas)

    def write_config(self, areas: dict, **services) -> None:
        raw = {
            "version": 1,
            "workspace": {"name": "stack", "layout": "single", "areas": areas},
            "user": {"handle": "jdoe", "name": "Jordan"},
            "vault": {"path": "vault"},
            "tracker": {"kind": "none"},
            "services": services or {"agent_lock_minutes": 30, "manual_lock_minutes": 60},
        }
        (self.root / ".claude" / "toolbox.local.yml").write_text(yaml.safe_dump(raw), encoding="utf-8")

    def run_script(self, *args: str) -> tuple[int, dict]:
        env = {**os.environ, "TOOLBOX_WORKSPACE": str(self.root), "TOOLBOX_LOCKS": str(self.locks)}
        result = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=env,
                                cwd=self.tmp, timeout=60)
        try:
            return result.returncode, json.loads(result.stdout)
        except json.JSONDecodeError:
            self.fail(f"not JSON ({result.returncode}): {result.stdout!r} {result.stderr!r}")

    def spawn_sleep(self) -> int:
        """A detached `sleep`, so it is nobody's child and a kill leaves no zombie."""
        out = subprocess.run(["sh", "-c", "sleep 300 >/dev/null 2>&1 & echo $!"], capture_output=True, text=True)
        pid = int(out.stdout)
        self.started.append(pid)
        return pid

    def spawn_server(self, port: int) -> int:
        out = subprocess.run(["sh", "-c", f"python3 -m http.server {port} --bind 127.0.0.1 >/dev/null 2>&1 & echo $!"],
                             capture_output=True, text=True)
        pid = int(out.stdout)
        self.started.append(pid)
        deadline = time.time() + 10
        while not listening(port) and time.time() < deadline:
            time.sleep(0.1)
        self.assertTrue(listening(port))
        return pid

    def kill_started(self):
        for pid in self.started:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def lock_file(self, name: str = "stack") -> Path:
        return self.locks / "stack" / name / "owner.json"

    def expire(self, name: str = "stack") -> None:
        path = self.lock_file(name)
        owner = json.loads(path.read_text())
        owner["expires"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds")
        path.write_text(json.dumps(owner))


class AcquireTest(LockCase):
    def test_acquire_then_held(self):
        code, first = self.run_script("acquire", "--task", "T-1", "--ports", "3001,3002")
        self.assertEqual((code, first["result"], first["kind"]), (0, "acquired", "agent"))
        self.assertEqual(first["owner"]["ports"], [3001, 3002])
        code, second = self.run_script("acquire", "--task", "T-2")
        self.assertEqual((code, second["result"]), (3, "held"))
        self.assertEqual(second["owner"]["token"], first["owner"]["token"])

    def test_default_name_is_the_workspace_and_a_name_locks_separately(self):
        self.run_script("acquire")
        self.assertTrue(self.lock_file("stack").exists())
        code, other = self.run_script("acquire", "repo-a")
        self.assertEqual((code, other["result"]), (0, "acquired"))
        _, listing = self.run_script("status")
        self.assertEqual([lock["name"] for lock in listing["locks"]], ["repo-a", "stack"])

    def test_lock_times_come_from_config(self):
        self.write_config({}, agent_lock_minutes=7, manual_lock_minutes=11)
        for flags, minutes in (([], 7), (["--manual"], 11)):
            self.run_script("release", "--force")
            _, got = self.run_script("acquire", *flags)
            owner = got["owner"]
            span = datetime.fromisoformat(owner["expires"]) - datetime.fromisoformat(owner["acquired"])
            self.assertEqual(span, timedelta(minutes=minutes))
            self.assertEqual(got["kind"], "manual" if flags else "agent")

    def test_mine_lists_a_tasks_locks(self):
        self.run_script("acquire", "a", "--task", "T-1")
        self.run_script("acquire", "b", "--task", "T-2")
        _, got = self.run_script("mine", "--task", "T-2")
        self.assertEqual([lock["name"] for lock in got["locks"]], ["b"])


class WaitTest(LockCase):
    def test_free_returns_at_once(self):
        code, got = self.run_script("wait")
        self.assertEqual((code, got["result"]), (0, "free"))

    def test_held_gives_up_after_max_seconds(self):
        self.run_script("acquire")
        started = time.monotonic()
        code, got = self.run_script("wait", "--max-seconds", "1", "--poll-seconds", "0.2")
        self.assertEqual((code, got["result"]), (3, "held"))
        self.assertLess(time.monotonic() - started, 10)

    def test_expired_agent_lock_returns_expired(self):
        self.run_script("acquire")
        self.expire()
        code, got = self.run_script("wait", "--max-seconds", "5", "--poll-seconds", "0.2")
        self.assertEqual((code, got["result"], got["kind"]), (0, "expired", "agent"))

    def test_expired_manual_lock_returns_expired_too(self):
        self.run_script("acquire", "--manual")
        self.expire()
        code, got = self.run_script("wait", "--max-seconds", "5", "--poll-seconds", "0.2")
        self.assertEqual((code, got["result"], got["kind"]), (0, "expired", "manual"))

    def test_wakes_when_the_holder_releases(self):
        _, held = self.run_script("acquire")
        env = {**os.environ, "TOOLBOX_WORKSPACE": str(self.root), "TOOLBOX_LOCKS": str(self.locks)}
        waiter = subprocess.Popen([sys.executable, str(SCRIPT), "wait", "--max-seconds", "30", "--poll-seconds", "0.2"],
                                  stdout=subprocess.PIPE, text=True, env=env)
        time.sleep(0.6)
        self.run_script("release", "--token", held["owner"]["token"])
        out, _ = waiter.communicate(timeout=20)
        self.assertEqual((waiter.returncode, json.loads(out)["result"]), (0, "free"))


class TakeoverTest(LockCase):
    def holder(self, *flags: str) -> tuple[dict, int, int, int]:
        """A lock whose holder runs a server on a port and a sleep, both recorded in the entry."""
        port = free_port()
        _, got = self.run_script("acquire", "--task", "T-1", *flags)
        token = got["owner"]["token"]
        server, sleeper = self.spawn_server(port), self.spawn_sleep()
        code, _ = self.run_script("update", "--token", token, "--ports", str(port), "--pids", f"{server},{sleeper}")
        self.assertEqual(code, 0)
        return got["owner"], port, server, sleeper

    def check_takeover(self, kind: str, flags: tuple[str, ...]):
        owner, port, server, sleeper = self.holder(*flags)
        self.expire()
        code, got = self.run_script("acquire", "--task", "T-2")
        self.assertEqual((code, got["result"], got["previous_kind"]), (0, "taken-over", kind))
        self.assertTrue(got["notify"])
        self.assertEqual(got["previous"]["token"], owner["token"])
        self.assertNotEqual(got["owner"]["token"], owner["token"])
        self.assertEqual(sorted(got["stopped"]["pids"]), sorted([server, sleeper]))
        deadline = time.time() + 10
        while (listening(port) or alive(server) or alive(sleeper)) and time.time() < deadline:
            time.sleep(0.2)
        self.assertFalse(listening(port))
        self.assertFalse(alive(sleeper))

    def test_takes_over_an_expired_agent_lock(self):
        self.check_takeover("agent", ())

    def test_takes_over_an_expired_manual_lock(self):
        self.check_takeover("manual", ("--manual",))

    def test_does_not_take_over_a_live_lock(self):
        owner, port, *_ = self.holder()
        code, got = self.run_script("acquire")
        self.assertEqual((code, got["result"]), (3, "held"))
        self.assertTrue(listening(port))

    def test_takeover_stops_only_the_holders_entry(self):
        # The new holder asks for the old holder's port; that must not widen what the takeover kills.
        owner, port, *_ = self.holder()
        bystander = free_port()
        self.spawn_server(bystander)
        self.expire()
        self.run_script("acquire", "--ports", str(bystander))
        self.assertTrue(listening(bystander))

    def test_takeover_runs_the_holders_stop_command(self):
        marker = self.tmp / "stopped"
        _, got = self.run_script("acquire")
        self.run_script("update", "--token", got["owner"]["token"], "--stop-cmd", f"touch {marker}",
                        "--stop-cwd", str(self.tmp))
        self.expire()
        self.run_script("acquire")
        self.assertTrue(marker.exists())


class ReleaseAndStopTest(LockCase):
    def test_release_by_the_wrong_token_changes_nothing(self):
        _, got = self.run_script("acquire")
        code, refused = self.run_script("release", "--token", "not-the-token")
        self.assertEqual((code, refused["result"]), (5, "not-owner"))
        self.assertEqual(refused["owner"]["token"], got["owner"]["token"])
        self.assertTrue(self.lock_file().exists())

    def test_release_by_token_then_not_held(self):
        _, got = self.run_script("acquire")
        code, released = self.run_script("release", "--token", got["owner"]["token"])
        self.assertEqual((code, released["result"]), (0, "released"))
        code, again = self.run_script("release", "--token", got["owner"]["token"])
        self.assertEqual((code, again["result"]), (0, "not-held"))

    def test_force_releases_without_a_token(self):
        self.run_script("acquire", "--manual")
        code, released = self.run_script("release", "--force")
        self.assertEqual((code, released["result"]), (0, "released"))

    def test_stop_by_the_wrong_token_kills_nothing(self):
        port = free_port()
        self.spawn_server(port)
        _, got = self.run_script("acquire", "--ports", str(port))
        code, refused = self.run_script("stop", "--token", "wrong")
        self.assertEqual((code, refused["result"]), (5, "not-owner"))
        self.assertTrue(listening(port))
        self.assertTrue(self.lock_file().exists())

    def test_stop_ends_the_entrys_port_and_pids_then_releases(self):
        port = free_port()
        server, sleeper = self.spawn_server(port), self.spawn_sleep()
        _, got = self.run_script("acquire", "--ports", str(port))
        token = got["owner"]["token"]
        self.run_script("update", "--token", token, "--pids", str(sleeper))
        code, stopped = self.run_script("stop", "--token", token)
        self.assertEqual((code, stopped["result"], stopped["still_listening"]), (0, "stopped", {}))
        self.assertEqual(stopped["pids"], [sleeper])
        deadline = time.time() + 10
        while (listening(port) or alive(sleeper)) and time.time() < deadline:
            time.sleep(0.2)
        self.assertFalse(listening(port))
        self.assertFalse(alive(sleeper))
        self.assertFalse(self.lock_file().exists())

    def test_stop_skips_a_port_another_lock_owns(self):
        port = free_port()
        self.spawn_server(port)
        _, mine = self.run_script("acquire", "repo-a", "--ports", str(port))
        self.run_script("acquire", "repo-b", "--ports", str(port))
        code, stopped = self.run_script("stop", "repo-a", "--token", mine["owner"]["token"])
        self.assertEqual((code, stopped["skipped_ports"]), (0, [port]))
        self.assertTrue(listening(port))
        self.assertFalse(self.lock_file("repo-a").exists())
        self.assertTrue(self.lock_file("repo-b").exists())

    def test_stop_leaves_a_reused_pid_alone(self):
        sleeper = self.spawn_sleep()
        _, got = self.run_script("acquire")
        token = got["owner"]["token"]
        self.run_script("update", "--token", token, "--pids", str(sleeper))
        owner = json.loads(self.lock_file().read_text())
        owner["pid_starts"][str(sleeper)] = "Mon Jan  1 00:00:00 2001"
        self.lock_file().write_text(json.dumps(owner))
        code, stopped = self.run_script("stop", "--token", token)
        self.assertEqual((code, stopped["pids"]), (0, []))
        self.assertTrue(alive(sleeper))


class StartStopRoundTripTest(LockCase):
    def setUp(self):
        self.port = free_port()
        self.areas = {"app": {
            "start": f"python3 -m http.server {self.port} --bind 127.0.0.1",
            "stop": "touch stopped.flag",
        }, "broken": {"start": "echo boom; exit 3"}, "quick": {"start": "touch started.flag"}}
        super().setUp()
        self.checkout = self.tmp / "checkout"
        self.checkout.mkdir()

    def test_round_trip_with_fake_commands(self):
        _, got = self.run_script("acquire", "--task", "T-1", "--ports", str(self.port))
        token = got["owner"]["token"]

        code, started = self.run_script("start", "--token", token, "--area", "app", "--checkout", str(self.checkout),
                                        "--health-url", f"http://127.0.0.1:{self.port}/", "--wait-seconds", "30")
        self.assertEqual((code, started["result"]), (0, "started"))
        self.started.append(started["pid"])
        self.assertTrue(listening(self.port))
        owner = json.loads(self.lock_file().read_text())
        self.assertIn(started["pid"], owner["pids"])
        self.assertEqual(owner["stops"], [{"command": "touch stopped.flag", "cwd": str(self.checkout)}])

        _, listing = self.run_script("status")
        self.assertEqual(listing["locks"][0]["owner"]["token"], token)

        code, stopped = self.run_script("stop", "--token", token)
        self.assertEqual((code, stopped["result"], stopped["still_listening"]), (0, "stopped", {}))
        self.assertEqual(stopped["stop_commands"][0]["exit"], 0)
        self.assertTrue((self.checkout / "stopped.flag").exists())
        deadline = time.time() + 10
        while listening(self.port) and time.time() < deadline:
            time.sleep(0.2)
        self.assertFalse(listening(self.port))
        self.assertFalse(self.lock_file().exists())

    def test_start_reports_a_command_that_fails(self):
        _, got = self.run_script("acquire")
        code, started = self.run_script("start", "--token", got["owner"]["token"], "--area", "broken",
                                        "--checkout", str(self.checkout), "--wait-seconds", "20")
        self.assertEqual((code, started["result"], started["exit_code"]), (6, "failed", 3))
        self.assertIn("boom", started["log_tail"])
        self.assertTrue(self.lock_file().exists(), "start never releases; the caller stops")

    def test_start_times_out_when_the_port_never_opens(self):
        _, got = self.run_script("acquire")
        code, started = self.run_script("start", "--token", got["owner"]["token"], "--area", "quick",
                                        "--checkout", str(self.checkout), "--ports", str(free_port()),
                                        "--wait-seconds", "2")
        self.assertEqual((code, started["result"]), (6, "timeout"))

    def test_a_command_that_returns_with_nothing_to_wait_on_is_started(self):
        _, got = self.run_script("acquire")
        code, started = self.run_script("start", "--token", got["owner"]["token"], "--area", "quick",
                                        "--checkout", str(self.checkout))
        self.assertEqual((code, started["result"]), (0, "started"))
        self.assertTrue((self.checkout / "started.flag").exists())

    def test_start_without_a_command_or_the_token_does_nothing(self):
        _, got = self.run_script("acquire")
        code, refused = self.run_script("start", "--token", "wrong", "--area", "app", "--checkout",
                                        str(self.checkout))
        self.assertEqual((code, refused["result"]), (5, "not-owner"))
        code, missing = self.run_script("start", "--token", got["owner"]["token"], "--area", "nope",
                                        "--checkout", str(self.checkout))
        self.assertEqual((code, missing["result"]), (2, "no-command"))


class NoConfigTest(LockCase):
    def test_a_workspace_without_config_fails_with_the_reason(self):
        (self.root / ".claude" / "toolbox.local.yml").unlink()
        env = {k: v for k, v in os.environ.items() if k != "TOOLBOX_WORKSPACE"}
        result = subprocess.run([sys.executable, str(SCRIPT), "status"], capture_output=True, text=True, env=env,
                                cwd=self.tmp)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(json.loads(result.stdout)["ok"])


if __name__ == "__main__":
    unittest.main()
