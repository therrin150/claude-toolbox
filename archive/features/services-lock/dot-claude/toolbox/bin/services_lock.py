#!/usr/bin/env python3
"""The local-services lock: one lock per name, so parallel sessions and the user never run the same stack at once.

A lock is a folder, ~/.claude/locks/<workspace name>/<name>/, created atomically with mkdir; its owner.json holds the
token, the kind (agent or manual), the task, the ports and PIDs of what the holder started, and the commands that stop
it. <name> defaults to the workspace name (one stack for the whole workspace); a nested workspace passes a repo's name
to lock per repo. Lock times come from the config's services.agent_lock_minutes and manual_lock_minutes.

Ports, PIDs and stop commands belong to the lock entry: `stop` and a takeover end only what the entry lists, and skip a
port another lock lists. Every command prints one JSON object.

Commands and exit codes:
  acquire [name] [--task K] [--worktree P] [--ports 3001,3002] [--manual] [--ttl-min N]
      0 acquired, or took over an expired lock of either kind (stopped the holder's processes; `notify` is true, so
      tell the user), 3 held and not expired
  wait [name] [--max-seconds 540]
      Polls until the lock is free or expired (0; acquire next), or --max-seconds pass (3, still held). Run it in the
      foreground in bounded steps, or in the background; 0 means no limit.
  start [name] --token T --area A --checkout P [--ports 3001] [--health-url U] [--wait-seconds 90]
      Runs the area's `start` command in the checkout, detached, records its PID and the area's `stop` command in the
      entry, and waits for the ports to listen and the health URLs to answer. 0 started, 5 not the owner,
      6 failed or timed out (nothing is stopped; call `stop`), 2 no start command for the area
  update [name] --token T [--pids 1,2] [--ports 3001] [--stop-cmd C --stop-cwd D]
      Adds to the entry. 0 updated, 5 not the owner
  stop [name] --token T
      Runs the entry's stop commands, ends its PIDs and what listens on its ports, releases. 0 stopped, 5 not the owner
  release [name] (--token T | --force)
      0 released or not held, 5 not the owner. --force is for the user's own manual lock
  status [name] | mine --task K
      0

Tests point TOOLBOX_LOCKS at a temporary folder.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import config  # noqa: E402

OK, USAGE, HELD, NOT_OWNER, START_FAILED = 0, 2, 3, 5, 6
ENV_LOCKS = "TOOLBOX_LOCKS"
POLL_SECONDS = 5
GRACE_SECONDS = 15
STOP_COMMAND_SECONDS = 120
LOG_TAIL = 20


class State:
    """What a run needs from the config: where this workspace's locks live, and how long each kind lasts."""

    def __init__(self, cfg: config.Config):
        self.cfg = cfg
        base = Path(os.environ.get(ENV_LOCKS) or Path.home() / ".claude" / "locks")
        self.root = base / cfg.name
        self.agent_minutes = cfg.get("services.agent_lock_minutes")
        self.manual_minutes = cfg.get("services.manual_lock_minutes")

    def path(self, name: str) -> Path:
        return self.root / name


def now() -> datetime:
    return datetime.now(timezone.utc)


def emit(code: int, **fields) -> int:
    print(json.dumps(fields, indent=2, sort_keys=True, default=str))
    return code


def ints(text: str | None) -> list[int]:
    return [int(part) for part in text.split(",") if part.strip()] if text else []


# ---------------------------------------------------------------------------------------------------------------------
# Lock files


def read_owner(path: Path) -> dict | None:
    try:
        return json.loads((path / "owner.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def write_owner(path: Path, owner: dict) -> None:
    temp = path / "owner.json.tmp"
    temp.write_text(json.dumps(owner, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path / "owner.json")


def kind(owner: dict | None) -> str:
    return "manual" if owner and owner.get("manual") else "agent"


def expired(state: State, path: Path, owner: dict | None) -> bool:
    if owner and owner.get("expires"):
        return now() >= datetime.fromisoformat(owner["expires"])
    # owner.json not written yet (another process is mid-acquire) or unreadable: judge by the folder's age.
    try:
        age = now() - datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except FileNotFoundError:
        return True
    return age > timedelta(minutes=state.agent_minutes)


def all_locks(state: State) -> list[dict]:
    if not state.root.exists():
        return []
    locks = []
    for path in sorted(state.root.iterdir()):
        if not path.is_dir() or ".stale-" in path.name:
            continue
        owner = read_owner(path)
        locks.append({"name": path.name, "kind": kind(owner), "expired": expired(state, path, owner), "owner": owner})
    return locks


def ports_of_others(state: State, name: str) -> set[int]:
    """Ports another lock's entry lists: not this lock's to free."""
    others = (lock["owner"] or {} for lock in all_locks(state) if lock["name"] != name)
    return {port for owner in others for port in owner.get("ports", [])}


# ---------------------------------------------------------------------------------------------------------------------
# Processes


def listeners(port: int) -> list[int]:
    try:
        result = subprocess.run(["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"], capture_output=True, text=True)
    except FileNotFoundError:
        return []
    return [int(pid) for pid in result.stdout.split()]


def proc_start(pid: int) -> str | None:
    """The process's start time as `ps` prints it, or None when it isn't running. A reused PID has another one."""
    result = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True)
    return result.stdout.strip() or None


def signal_process(pid: int, sig: int) -> None:
    try:
        # A process we started is its own group leader, so its children go with it.
        if os.getpgid(pid) == pid:
            os.killpg(pid, sig)
        else:
            os.kill(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def run_stop_commands(owner: dict) -> list[dict]:
    ran = []
    for stop in owner.get("stops", []):
        entry = {"command": stop["command"], "cwd": stop.get("cwd")}
        try:
            result = subprocess.run(stop["command"], shell=True, cwd=stop.get("cwd") or None, capture_output=True,
                                    text=True, timeout=STOP_COMMAND_SECONDS, stdin=subprocess.DEVNULL)
            entry["exit"] = result.returncode
            if result.returncode:
                entry["output"] = (result.stdout + result.stderr).strip().splitlines()[-LOG_TAIL:]
        except subprocess.TimeoutExpired:
            entry["exit"] = "timeout"
        except OSError as error:
            entry["exit"] = str(error)
        ran.append(entry)
    return ran


def stop_entry(state: State, name: str, owner: dict) -> dict:
    """End what the entry lists, and nothing else: its stop commands, its PIDs, what listens on its ports.

    SIGTERM first, SIGKILL after the grace period. A port another lock lists is skipped, and a PID whose start time
    has changed is a reused one.
    """
    stopped = run_stop_commands(owner)
    pids = {}
    for pid in owner.get("pids", []):
        recorded = (owner.get("pid_starts") or {}).get(str(pid))
        if recorded and proc_start(pid) == recorded:
            pids[pid] = recorded
    others = ports_of_others(state, name)
    ports = [port for port in owner.get("ports", []) if port not in others]
    skipped = [port for port in owner.get("ports", []) if port in others]
    on_ports = {port: listeners(port) for port in ports}
    on_ports = {port: found for port, found in on_ports.items() if found}

    for pid in pids:
        signal_process(pid, signal.SIGTERM)
    for found in on_ports.values():
        for pid in found:
            signal_process(pid, signal.SIGTERM)

    deadline = time.time() + GRACE_SECONDS
    while time.time() < deadline and (any(proc_start(pid) == start for pid, start in pids.items())
                                      or any(listeners(port) for port in on_ports)):
        time.sleep(0.5)
    for pid, start in pids.items():
        if proc_start(pid) == start:
            signal_process(pid, signal.SIGKILL)
    for port in on_ports:
        for pid in listeners(port):
            signal_process(pid, signal.SIGKILL)

    still = {port: listeners(port) for port in ports if listeners(port)}
    return {"stop_commands": stopped, "pids": sorted(pids), "ports": on_ports, "skipped_ports": skipped,
            "still_listening": still}


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            return response.status < 400
    except urllib.error.HTTPError as error:
        return error.code < 400
    except (urllib.error.URLError, OSError):
        return False


# ---------------------------------------------------------------------------------------------------------------------
# Commands


def acquire(state: State, args) -> int:
    state.root.mkdir(parents=True, exist_ok=True)
    path = state.path(args.name)
    for _ in range(5):
        try:
            path.mkdir()
        except FileExistsError:
            owner = read_owner(path)
            if not expired(state, path, owner):
                return emit(HELD, result="held", name=args.name, kind=kind(owner), owner=owner)
            # Expired, of either kind: rename it away atomically, so only one waiting session wins the takeover.
            stale = path.with_name(f"{path.name}.stale-{secrets.token_hex(4)}")
            try:
                path.rename(stale)
            except FileNotFoundError:
                continue
            stopped = stop_entry(state, args.name, owner or {})
            shutil.rmtree(stale, ignore_errors=True)
            try:
                path.mkdir()
            except FileExistsError:
                continue
            return _claim(state, args, path, result="taken-over", previous=owner, previous_kind=kind(owner),
                          stopped=stopped, notify=True,
                          note="the previous holder's lock had expired and its processes were stopped; "
                               f"tell {state.cfg.get('user.name')}")
        else:
            return _claim(state, args, path, result="acquired")
    return emit(HELD, result="held", name=args.name, owner=read_owner(path), note="lost repeated races; try again")


def _claim(state: State, args, path: Path, **extra) -> int:
    minutes = args.ttl_min or (state.manual_minutes if args.manual else state.agent_minutes)
    acquired = now()
    owner = {
        "token": secrets.token_hex(8),
        "name": args.name,
        "task": args.task,
        "worktree": args.worktree,
        "ports": ints(args.ports),
        "pids": [],
        "pid_starts": {},
        "stops": [],
        "manual": args.manual,
        "acquired": acquired.isoformat(timespec="seconds"),
        "expires": (acquired + timedelta(minutes=minutes)).isoformat(timespec="seconds"),
    }
    write_owner(path, owner)
    return emit(OK, owner=owner, kind=kind(owner), **extra)


def wait(state: State, args) -> int:
    path = state.path(args.name)
    deadline = time.monotonic() + args.max_seconds if args.max_seconds else None
    while True:
        owner = read_owner(path)
        if not path.exists():
            return emit(OK, result="free", name=args.name)
        if expired(state, path, owner):
            return emit(OK, result="expired", name=args.name, kind=kind(owner), owner=owner,
                        note="acquire takes it over and stops its processes; tell the user it ran out"
                        if owner else None)
        if deadline and time.monotonic() >= deadline:
            return emit(HELD, result="held", name=args.name, kind=kind(owner), owner=owner)
        time.sleep(args.poll_seconds)


def owned(state: State, args) -> tuple[Path, dict | None, bool]:
    path = state.path(args.name)
    owner = read_owner(path)
    return path, owner, bool(owner) and owner.get("token") == args.token


def not_owner(args, owner) -> int:
    return emit(NOT_OWNER, result="not-owner", name=args.name, owner=owner, note="changed nothing")


def _add(owner: dict, pids: list[int], ports: list[int], stop: dict | None) -> None:
    for pid in pids:
        if pid not in owner["pids"]:
            owner["pids"].append(pid)
        started = proc_start(pid)
        if started:
            owner.setdefault("pid_starts", {})[str(pid)] = started
    owner["ports"] = sorted(set(owner["ports"]) | set(ports))
    if stop and stop not in owner.setdefault("stops", []):
        owner["stops"].append(stop)


def update(state: State, args) -> int:
    path, owner, mine = owned(state, args)
    if not mine:
        return not_owner(args, owner)
    stop = {"command": args.stop_cmd, "cwd": args.stop_cwd} if args.stop_cmd else None
    _add(owner, ints(args.pids), ints(args.ports), stop)
    if args.worktree:
        owner["worktree"] = args.worktree
    write_owner(path, owner)
    return emit(OK, result="updated", owner=owner)


def start(state: State, args) -> int:
    path, owner, mine = owned(state, args)
    if not mine:
        return not_owner(args, owner)
    command = state.cfg.command(args.area, "start")
    if not command:
        return emit(USAGE, result="no-command", area=args.area,
                    note=f"workspace.areas.{args.area}.start isn't set in the config")
    checkout = Path(args.checkout).expanduser().resolve()
    if not checkout.is_dir():
        return emit(USAGE, result="no-checkout", checkout=str(checkout))
    log = Path(args.log) if args.log else Path(os.environ.get("TMPDIR") or tempfile.gettempdir()) / \
        f"{args.name}-{args.area}-start.log"
    with open(log, "w") as out:
        process = subprocess.Popen(command, shell=True, cwd=checkout, stdout=out, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, start_new_session=True)
    stop_command = state.cfg.command(args.area, "stop")
    _add(owner, [process.pid], ints(args.ports), {"command": stop_command, "cwd": str(checkout)}
         if stop_command else None)
    write_owner(path, owner)

    ports, urls = ints(args.ports) or owner["ports"], args.health_url or []
    began = time.monotonic()
    deadline = began + args.wait_seconds
    result, code = "timeout", START_FAILED
    while time.monotonic() < deadline:
        exit_code = process.poll()
        if exit_code not in (None, 0):
            result = "failed"
            break
        if ports or urls:
            ready = all(port_open(port) for port in ports) and all(healthy(url) for url in urls)
        else:
            # Nothing to wait on: a command that returned is up (`docker compose up -d`); one still running after a
            # moment is a server in the foreground of its own process.
            ready = exit_code == 0 or time.monotonic() - began >= 2
        if ready:
            result, code = "started", OK
            break
        time.sleep(0.5)
    tail = log.read_text(errors="replace").splitlines()[-LOG_TAIL:] if code else []
    return emit(code, result=result, name=args.name, area=args.area, pid=process.pid, ports=ports, log=str(log),
                exit_code=process.poll(), log_tail=tail)


def stop(state: State, args) -> int:
    path, owner, mine = owned(state, args)
    if not mine:
        return not_owner(args, owner)
    stopped = stop_entry(state, args.name, owner)
    shutil.rmtree(path, ignore_errors=True)
    return emit(OK, result="stopped", name=args.name, **stopped)


def release(state: State, args) -> int:
    path = state.path(args.name)
    owner = read_owner(path)
    if not path.exists():
        return emit(OK, result="not-held", name=args.name)
    if not args.force and not (owner and owner.get("token") == args.token):
        return not_owner(args, owner)
    shutil.rmtree(path, ignore_errors=True)
    return emit(OK, result="released", name=args.name, previous=owner)


def status(state: State, args) -> int:
    locks = all_locks(state)
    if args.name:
        locks = [lock for lock in locks if lock["name"] == args.name]
    return emit(OK, workspace=state.cfg.name, locks=locks)


def mine(state: State, args) -> int:
    return emit(OK, locks=[lock for lock in all_locks(state) if (lock["owner"] or {}).get("task") == args.task])


# ---------------------------------------------------------------------------------------------------------------------


def parser_for(default_name: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    def command(name, run, default=default_name):
        sub = commands.add_parser(name)
        sub.add_argument("name", nargs="?", default=default)
        sub.set_defaults(run=run)
        return sub

    sub = command("acquire", acquire)
    sub.add_argument("--task")
    sub.add_argument("--worktree")
    sub.add_argument("--ports")
    sub.add_argument("--manual", action="store_true")
    sub.add_argument("--ttl-min", type=float)

    sub = command("wait", wait)
    sub.add_argument("--max-seconds", type=int, default=540)
    sub.add_argument("--poll-seconds", type=float, default=POLL_SECONDS)

    sub = command("update", update)
    sub.add_argument("--token", required=True)
    sub.add_argument("--pids")
    sub.add_argument("--ports")
    sub.add_argument("--worktree")
    sub.add_argument("--stop-cmd")
    sub.add_argument("--stop-cwd")

    sub = command("start", start)
    sub.add_argument("--token", required=True)
    sub.add_argument("--area", required=True)
    sub.add_argument("--checkout", required=True)
    sub.add_argument("--ports")
    sub.add_argument("--health-url", action="append")
    sub.add_argument("--wait-seconds", type=float, default=90)
    sub.add_argument("--log")

    sub = command("stop", stop)
    sub.add_argument("--token", required=True)

    sub = command("release", release)
    group = sub.add_mutually_exclusive_group(required=True)
    group.add_argument("--token")
    group.add_argument("--force", action="store_true")

    command("status", status, default=None)

    sub = commands.add_parser("mine")
    sub.add_argument("--task", required=True)
    sub.set_defaults(run=mine, name=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        cfg = config.load()
    except config.ConfigError as error:
        return emit(1, ok=False, config=str(error.path) if error.path else None, errors=error.errors)
    state = State(cfg)
    args = parser_for(cfg.name).parse_args(argv)
    return args.run(state, args)


if __name__ == "__main__":
    sys.exit(main())
