"""Shared paths, config, locking and JSON storage for local-review-ui. Standard library only; macOS, Linux and Windows.

State lives under LOCAL_REVIEW_HOME (default ~/.local-review):

  config.json                 port, workspaceRoot, defaultBase, editorCommand, editorUrl (install.py configure)
  server.json, server.log     the running server's pid, port and version, and its output
  registry/<id>.json          one review: its repos (path, base, branch), resume hint and store directory (review.py open)
  reviews/<id>/               the default store: comments.json, viewed.json, listener.json and their .lock files
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path

TOOL = "local-review-ui"
DEFAULT_PORT = 47820
DEFAULT_EDITOR_URL = "idea://open?file={path}&line={line}"

SCRIPTS = Path(__file__).resolve().parent
SKILL_DIR = SCRIPTS.parent
ASSETS = SKILL_DIR / "assets"

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")

IS_WINDOWS = os.name == "nt"
if IS_WINDOWS:
    import msvcrt
else:
    import fcntl


def spawn_detached(command: list[str], log=None, cwd: str | None = None) -> None:
    """Start a process that outlives this one and never holds the caller's stdout open."""
    import subprocess
    # Popen's cwd doesn't update PWD, so the child would inherit the launching shell's, which may since be deleted
    # (a removed worktree). JetBrains' launcher changes to $PWD on start and fails outright when it's gone.
    env = dict(os.environ, PWD=os.path.abspath(cwd or os.getcwd()))
    env.pop("OLDPWD", None)
    kwargs = {"env": env}
    if IS_WINDOWS:
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log or subprocess.DEVNULL,
                     stderr=subprocess.STDOUT if log else subprocess.DEVNULL, cwd=cwd, close_fds=True, **kwargs)


def editor_command(project: str, file: str | None, line: int) -> list[str] | None:
    """The configured "Open in IDE" command with its placeholders filled, or None when none is configured.
    Without a file (a deleted one), the file and line arguments are dropped so the project still opens."""
    template = load_config().get("editorCommand")
    if not template:
        return None
    command = []
    skip_next = False
    for i, arg in enumerate(template):
        if skip_next:
            skip_next = False
            continue
        if file is None and "{file}" in arg:
            continue
        if file is None and i + 1 < len(template) and "{line}" in template[i + 1] and arg.startswith("-"):
            skip_next = True
            continue
        if file is None and "{line}" in arg:
            continue
        command.append(arg.replace("{project}", project).replace("{file}", file or "").replace("{line}", str(line)))
    return command


def home() -> Path:
    return Path(os.environ.get("LOCAL_REVIEW_HOME") or Path.home() / ".local-review")


def load_config() -> dict:
    return read_json(home() / "config.json", {})


def port() -> int:
    value = os.environ.get("LOCAL_REVIEW_PORT") or load_config().get("port") or DEFAULT_PORT
    return int(value)


def version() -> str:
    """A hash of the server's code, so a launcher can tell a stale server from a current one."""
    digest = hashlib.sha256()
    for name in ("anchors.py", "common.py", "gitdiff.py", "server.py"):
        digest.update((SCRIPTS / name).read_bytes())
    return digest.hexdigest()[:12]


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_iso(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def check_id(review_id: str) -> str:
    if not ID_RE.match(review_id or ""):
        raise ValueError(f"invalid review id {review_id!r}: use letters, digits, '.', '_' or '-' (max 80)")
    return review_id


# --- JSON files -------------------------------------------------------------------------------------------------------

class CorruptFile(ValueError):
    pass


def read_json(path: Path, default):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as e:
        # Our writes are atomic, so this is a hand edit or a truncated disk write. Fail rather than return the
        # default: update_json would write it back and wipe every comment.
        raise CorruptFile(f"{path} isn't valid JSON ({e}); fix or remove it") from None


def write_json_atomic(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
        # On Windows os.replace fails while another process has the target open; readers hold it only briefly.
        for attempt in range(20):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if not IS_WINDOWS or attempt == 19:
                    raise
                time.sleep(0.05)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@contextlib.contextmanager
def locked(path: Path):
    """An exclusive lock on <path>.lock, shared by the server and every CLI process."""
    lock_path = path.with_name(path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if IS_WINDOWS:
        with open(lock_path, "a+b") as handle:
            handle.seek(0)
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    # LK_LOCK gives up after about 10 seconds; keep waiting like flock does.
                    continue
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    while True:
        handle = open(lock_path, "a+b")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            # `prune` deletes lock files, so the one we waited on may no longer be the file at this path (a
            # newcomer would lock the new one, and both would run). Take the lock again on whatever is there now.
            try:
                now = os.stat(lock_path)
            except FileNotFoundError:
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                continue
            held = os.fstat(handle.fileno())
            if (now.st_ino, now.st_dev) != (held.st_ino, held.st_dev):
                continue
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            return
        finally:
            handle.close()


def update_json(path: Path, default, mutate, alive=None):
    """Read, mutate and atomically rewrite a JSON file under its lock. Returns mutate's result. With `alive`, a
    callable checked under the lock, nothing is written when it returns false (see review_alive)."""
    # Checked before the lock too: taking it creates the lock file and its folder, which a pruned review must not
    # get back. The check under the lock closes the window where the review goes between the two.
    if alive is not None and not alive():
        raise ValueError("the review was forgotten or pruned")
    with locked(path):
        if alive is not None and not alive():
            raise ValueError("the review was forgotten or pruned")
        data = read_json(path, default)
        result = mutate(data)
        write_json_atomic(path, data)
        return result


# --- Reviews ----------------------------------------------------------------------------------------------------------

def inside(base: Path, name: str) -> Path:
    """base/name, refusing anything that resolves outside base. Ids are validated too; this is the backstop."""
    root = os.path.realpath(base)
    full = os.path.realpath(os.path.join(root, name))
    if not full.startswith(root + os.sep):
        raise ValueError(f"{name!r} is outside {root}")
    return Path(full)


def registration_path(review_id: str) -> Path:
    return inside(home() / "registry", f"{check_id(review_id)}.json")


def load_registration(review_id: str) -> dict | None:
    return read_json(registration_path(review_id), None)


def list_registrations() -> list[dict]:
    folder = home() / "registry"
    if not folder.is_dir():
        return []
    found = []
    for path in sorted(folder.glob("*.json")):
        try:
            found.append(read_json(path, None))
        except CorruptFile:
            continue  # one broken registration shouldn't hide the rest; opening it reports the error
    return [reg for reg in found if reg]


def review_alive(registration: dict):
    """For update_json: false once the review's registration is gone, so a writer that loaded it before a `forget` or
    `prune` can't recreate the store they just removed."""
    return lambda: registration_path(registration["id"]).exists()


def default_store(review_id: str) -> Path:
    return inside(home() / "reviews", check_id(review_id))


def store_dir(registration: dict) -> Path:
    return Path(registration["store"])


def empty_comments() -> dict:
    return {"version": 0, "nextId": 1, "rounds": [], "comments": []}


STORE_FILES = ("comments.json", "viewed.json", "listener.json")


def comments_path(registration: dict) -> Path:
    return store_dir(registration) / "comments.json"


def load_comments(registration: dict) -> dict:
    return read_json(comments_path(registration), empty_comments())


def update_comments(registration: dict, mutate):
    def wrapped(data):
        for key, value in empty_comments().items():
            data.setdefault(key, value)
        result = mutate(data)
        data["version"] += 1
        return result
    return update_json(comments_path(registration), empty_comments(), wrapped, alive=review_alive(registration))


def current_round(data: dict) -> int:
    return len(data["rounds"]) + 1


def needs_attention(comment: dict) -> bool:
    """Open, and the reviewer spoke last: the agent owes it a fix or a reply. A note the agent opened has no reviewer
    message until the reviewer replies."""
    if comment.get("status") != "open":
        return False
    replies = comment.get("replies") or []
    last = replies[-1].get("author") if replies else comment.get("author", "user")
    return last != "claude"


def listener_path(registration: dict) -> Path:
    return store_dir(registration) / "listener.json"


LISTENER_FRESH_SECONDS = 15


def listener_state(registration: dict) -> dict | None:
    listener = read_json(listener_path(registration), None)
    if not listener:
        return None
    seen = parse_iso(listener.get("seenAt"))
    age = (dt.datetime.now(dt.timezone.utc) - seen).total_seconds() if seen else None
    listener["fresh"] = age is not None and age <= LISTENER_FRESH_SECONDS
    return listener
