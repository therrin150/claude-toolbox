"""local-review-ui command line: open a review in the browser, wait for the reviewer, read and answer comments.

  review.py open [--id ID] [--title T] [--base REF] [--store DIR] [--replace] [--resume-hint TEXT] [--no-browser] REPO...
  review.py wait <id> [--session S] [--until HH:MM|ISO | --minutes N]
  review.py read <id> [--all] [--json] [--peek]
  review.py reply <id> <comment-id> [--body TEXT]                  (body from stdin when --body is omitted)
  review.py note <id> --repo NAME --path FILE --line N [--end-line N] [--side new|old] [--body TEXT]
  review.py status <id>
  review.py list
  review.py forget <id> [--delete-comments]
  review.py prune [--dry-run]
  review.py stop

REPO is `name=path`, a path to a git work tree, or a bare repo name looked up under the configured workspace root
(install.py configure --workspace-root). The review id defaults to the ticket key in the first repo's branch name
(e.g. ABC-123), else the branch name.

open    registers the review, starts the server if it isn't running (or restarts it when its code changed), and
        opens /review/<id> in the default browser. On an existing review it adds the repos to its list and keeps its
        store; a repo name already registered at another path fails, unless --replace replaces the whole list.
        Each repo is registered with its checked-out branch (none for a detached HEAD), which `prune` uses.
        --resume-hint TEXT is what a reviewer pastes into a new session to pick the review back up; the page shows
        it, with a copy button, while no session is listening. A later `open` without it keeps the earlier hint
        (an empty TEXT clears it).
wait    the background waiter: run it with the agent's run_in_background. It exits with one JSON line on stdout:
          {"event": "submitted", ...}   the reviewer clicked "Finish round"; run `read` next
          {"event": "done", ...}        the reviewer clicked "Done reviewing": run `read` next, answer what it
                                        lists, and don't wait again
          {"event": "closed", ...}      the review was already closed with nothing unread; don't wait again
          {"event": "superseded", ...}  another session's waiter took over this review
          {"event": "timeout", ...}     --until or --minutes passed with nothing submitted
        It exits at once with "submitted" or "done" when a finished round hasn't been read yet.
        `open` reopens a closed review.
read    the comments that need a response (open, with the reviewer's message last), as Markdown, and marks the
        latest finished round as read (--peek leaves it unread). --all shows every comment.
reply   adds the agent's reply to a comment's thread. Only the reviewer resolves threads, in the page, so a reply
        never closes one: the reviewer reads it first.
note    opens a thread of the agent's own at a line (--side new is the working copy, old is the base), to ask the
        reviewer something. It belongs to no round and `read` lists it only once the reviewer has replied.
forget  drops one review by id. prune drops every review whose repos are all gone (path missing or not a git work
        tree, or the registered branch deleted), comments and leftover lock files included; --dry-run only reports.

Exit codes: 0 success, 1 failure (message on stderr), 2 bad arguments.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import os
import re
import signal
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

import anchors
import common
import gitdiff

KEY_RE = re.compile(r"[A-Z][A-Z0-9]+-\d+")


def fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


# --- server -----------------------------------------------------------------------------------------------------------

def base_url() -> str:
    return f"http://127.0.0.1:{common.port()}"


def health() -> dict | None:
    """The server's /health, None when nothing listens. Raises when the port belongs to something else."""
    try:
        with urllib.request.urlopen(base_url() + "/health", timeout=1.5) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
        return None
    except ValueError:
        data = {}
    if data.get("name") != common.TOOL:
        raise RuntimeError(f"port {common.port()} is in use by another program; set LOCAL_REVIEW_PORT "
                           f"or run install.py configure --port N")
    return data


def request_shutdown() -> None:
    req = urllib.request.Request(base_url() + "/api/shutdown", data=b"", method="POST")
    try:
        urllib.request.urlopen(req, timeout=3).read()
    except (urllib.error.URLError, OSError):
        pass


def wait_for(predicate, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.2)
    return predicate()


def start_server() -> None:
    home = common.home()
    home.mkdir(parents=True, exist_ok=True)
    with open(home / "server.log", "ab") as log:
        common.spawn_detached([sys.executable, str(common.SCRIPTS / "server.py"), "--port", str(common.port())],
                              log=log, cwd=str(home))


def ensure_server() -> dict:
    """A running server with this code's version. Safe when several sessions call it at once: a second server
    fails to bind and exits, and every caller then finds the one that won."""
    current = health()
    wanted = common.version()
    if current and current.get("version") == wanted:
        return current
    if current:
        request_shutdown()
        wait_for(lambda: health() is None, 5)
    start_server()
    if not wait_for(lambda: (health() or {}).get("version") == wanted, 10):
        raise RuntimeError(f"the server didn't start; see {common.home() / 'server.log'}")
    return health()


# --- open -------------------------------------------------------------------------------------------------------------

def resolve_repo(spec: str) -> dict:
    name, sep, raw = spec.partition("=")
    if not sep:
        name, raw = "", spec
    path = Path(raw).expanduser()
    if not path.exists() and not sep and os.sep not in raw and "/" not in raw:
        root = common.load_config().get("workspaceRoot")
        if not root:
            raise ValueError(f"{raw!r} isn't a path and no workspace root is configured "
                             f"(install.py configure --workspace-root DIR)")
        path = Path(root).expanduser() / raw
    if not gitdiff.is_work_tree(str(path)):
        raise ValueError(f"{path} isn't a git work tree")
    top = gitdiff.top_level(str(path.resolve()))
    return {"name": name or Path(top).name, "path": top}


def derive_id(repo: dict) -> str:
    branch = gitdiff.current_branch(repo["path"])
    key = KEY_RE.search(branch)
    if key:
        return key.group(0)
    if branch in ("HEAD", "master", "main"):
        return repo["name"]
    return re.sub(r"[^A-Za-z0-9._-]+", "-", branch).strip("-.")[:80] or repo["name"]


def cmd_open(args) -> int:
    repos = [resolve_repo(spec) for spec in args.repos]
    names = [r["name"] for r in repos]
    if len(set(names)) != len(names):
        return fail(f"two repos share a name ({', '.join(names)}); use name=path to tell them apart")
    review_id = common.check_id(args.id or derive_id(repos[0]))
    base = args.base or common.load_config().get("defaultBase")
    for repo in repos:
        gitdiff.resolve_base(repo["path"], base)  # fail now, not in the browser
        # Each repo keeps the base it was opened with, so a later `open` adding a repo can't change another's.
        repo["base"] = base
        repo["branch"] = gitdiff.branch_name(repo["path"])

    path = common.registration_path(review_id)

    def register(data):
        # Without --store, keep the store the review already has, so its comments stay with it.
        if args.store:
            store = str(Path(args.store).expanduser().resolve())
        else:
            store = data.get("store") or str(common.default_store(review_id))
        for old in data.get("repos") or []:
            old.setdefault("base", data.get("base"))  # from before per-repo bases
        data.pop("base", None)
        merged = repos
        if data.get("repos") and not args.replace:
            # Add to the review rather than replace it, so a second session on the same ticket can't silently drop
            # the first one's repos. A repo name already registered at another path is a clash, not an update.
            new_paths = {r["name"]: r["path"] for r in repos}
            for old in data["repos"]:
                if old["name"] in new_paths and new_paths[old["name"]] != old["path"]:
                    raise ValueError(f"review {review_id} already has {old['name']} at {old['path']}; pass --id for "
                                     f"a separate review, or --replace to replace its repos")
            merged = [r for r in data["repos"] if r["name"] not in new_paths] + repos
        data.update(id=review_id, title=args.title or data.get("title"), repos=merged, store=store,
                    createdAt=data.get("createdAt") or common.now_iso(), updatedAt=common.now_iso())
        if args.resume_hint is not None:
            data["resumeHint"] = args.resume_hint.strip() or None
        return data
    registration = common.update_json(path, {}, register)
    common.store_dir(registration).mkdir(parents=True, exist_ok=True)
    if common.load_comments(registration).get("closedAt"):
        common.update_comments(registration, lambda data: data.update(closedAt=None))

    server = ensure_server()
    url = f"{base_url()}/review/{review_id}"
    opened = False
    if not args.no_browser:
        opened = webbrowser.open(url)
    print(json.dumps({"id": review_id, "url": url, "browserOpened": opened, "repos": registration["repos"],
                      "resumeHint": registration.get("resumeHint"),
                      "store": registration["store"], "server": {"pid": server.get("pid"),
                                                                  "version": server.get("version")}}, indent=2))
    return 0


# --- wait -------------------------------------------------------------------------------------------------------------

def parse_until(value: str) -> dt.datetime:
    if re.fullmatch(r"\d{1,2}:\d{2}", value):
        hour, minute = map(int, value.split(":"))
        now = dt.datetime.now().astimezone()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += dt.timedelta(days=1)
        return target.astimezone(dt.timezone.utc)
    parsed = common.parse_iso(value)
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.astimezone(dt.timezone.utc)


def unread_round(registration: dict) -> dict | None:
    rounds = common.load_comments(registration)["rounds"]
    if rounds and not rounds[-1].get("acknowledgedAt"):
        return rounds[-1]
    return None


def cmd_wait(args) -> int:
    registration = load(args.id)
    session = args.session or os.environ.get("CLAUDE_CODE_SESSION_ID") or f"pid-{os.getppid()}"
    # Microseconds, so two waiters started in the same second still order; the later one wins.
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    until = parse_until(args.until) if args.until else None
    if args.minutes:
        until = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=args.minutes)
    path = common.listener_path(registration)
    alive = common.review_alive(registration)
    me = {"session": session, "pid": os.getpid(), "startedAt": started,
          "until": until.isoformat(timespec="seconds").replace("+00:00", "Z") if until else None}

    def release(*_):
        def clear(data):
            if data.get("pid") == me["pid"] and data.get("startedAt") == started:
                data.clear()
        common.update_json(path, {}, clear, alive=alive)

    def finish(event: dict) -> int:
        print(json.dumps({**event, "id": registration["id"], "session": session}), flush=True)
        return 0

    def claim(data):
        data.clear()
        data.update(me, seenAt=common.now_iso())

    # The handlers only note the signal, and the loop exits between polls. Releasing inside a handler would take
    # listener.json's lock again and deadlock when the signal lands while the heartbeat holds it (flock belongs to
    # the open file, not the process); raising would interrupt a write halfway and leave its temp file behind.
    # Installed before the claim, so the claim's write is covered too.
    stop: list[int] = []
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda signum, _frame: stop.append(signum))

    def pause(seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while not stop and time.monotonic() < deadline:
            time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))

    try:
        common.update_json(path, {}, claim, alive=alive)
        while not stop:
            pending = unread_round(registration)
            if pending:
                data = common.load_comments(registration)
                attention = [c["id"] for c in data["comments"] if common.needs_attention(c)]
                return finish({"event": "done" if pending.get("done") else "submitted", "round": pending["n"],
                               "summary": pending.get("summary"), "needResponse": attention,
                               "next": f"review.py read {registration['id']}"})
            if common.load_comments(registration).get("closedAt"):
                return finish({"event": "closed"})

            def heartbeat(data):
                if data.get("startedAt") and data.get("startedAt") > started and data.get("pid") != me["pid"]:
                    return data.get("session")
                data.update(me, seenAt=common.now_iso())
                return None
            newer = common.update_json(path, {}, heartbeat, alive=alive)
            if newer:
                print(json.dumps({"event": "superseded", "id": registration["id"], "by": newer}), flush=True)
                return 0
            if until and dt.datetime.now(dt.timezone.utc) >= until:
                return finish({"event": "timeout"})
            pause(args.interval)
        return 128 + stop[0]
    finally:
        try:
            release()
        except (OSError, ValueError):
            pass  # the event is already printed; a listener entry left behind goes stale on its own


# --- comments ---------------------------------------------------------------------------------------------------------

def load(review_id: str) -> dict:
    registration = common.load_registration(review_id)
    if registration is None:
        raise ValueError(f"no review {review_id!r}; run review.py open first (review.py list shows them)")
    return registration


def repo_path(registration: dict, name: str) -> str:
    return next((r["path"] for r in registration["repos"] if r["name"] == name), "")


def format_comment(registration: dict, c: dict) -> str:
    lines = c["line"] if c["line"] == c["endLine"] else f"{c['line']}-{c['endLine']}"
    side = "new" if c["side"] == "new" else "old (deleted side)"
    absolute = str(Path(repo_path(registration, c["repo"])) / c["path"])
    note = c.get("author") == "claude"
    when = "opened by Claude" if note else f"round {c['round']}"
    out = [f"### {c['id']} · {c['repo']} · `{c['path']}:{lines}` ({side}) · {c['status']} · {when}",
           f"File: `{absolute}`"]
    if c.get("lineText"):
        out.append("```\n" + c["lineText"] + "\n```")
    out.append(f"**Claude (opened this thread):** {c['body']}" if note else f"**Reviewer:** {c['body']}")
    for reply in c.get("replies") or []:
        who = "Claude" if reply.get("author") == "claude" else "Reviewer"
        out.append(f"**{who}:** {reply['body']}")
    return "\n\n".join(out)


def cmd_read(args) -> int:
    registration = load(args.id)
    def unread(d: dict) -> dict | None:
        return d["rounds"][-1] if d["rounds"] and not d["rounds"][-1].get("acknowledgedAt") else None

    data = common.load_comments(registration)
    pending = unread(data)
    if pending and not args.peek:
        # Ack and print from the same locked snapshot, so a round finished after the first read is either shown
        # here or left unread for the next wait, never acked unseen.
        def ack(d):
            round_ = unread(d)
            if round_:
                round_["acknowledgedAt"] = common.now_iso()
            return d, round_
        data, pending = common.update_comments(registration, ack)

    chosen = data["comments"] if args.all else [c for c in data["comments"] if common.needs_attention(c)]
    if args.json:
        print(json.dumps({"id": registration["id"], "round": pending, "comments": chosen}, indent=2))
        return 0

    head = f"# Review {registration['id']}"
    if data["rounds"]:
        last = data["rounds"][-1]
        head += f" · round {last['n']} finished {last['submittedAt']}"
    out = [head]
    if data.get("closedAt"):
        out.append("**The reviewer is done reviewing:** answer anything listed below, then stop waiting. "
                   "This is not approval to commit or publish.")
    if pending and pending.get("summary"):
        out.append(f"**Reviewer's summary:** {pending['summary']}")
    if not chosen:
        out.append("No comments need a response." if not args.all else "No comments.")
    out += [format_comment(registration, c) for c in chosen]
    print("\n\n".join(out))
    return 0


def mutate_comment(registration: dict, comment_id: str, change) -> dict:
    def apply(data):
        comment = next((c for c in data["comments"] if c["id"] == comment_id), None)
        if comment is None:
            raise ValueError(f"no comment {comment_id} in review {registration['id']}")
        change(comment)
        return comment
    return common.update_comments(registration, apply)


def cmd_reply(args) -> int:
    registration = load(args.id)
    body = args.body if args.body is not None else sys.stdin.read()
    if not body.strip():
        return fail("empty reply")

    def reply(comment):
        comment.setdefault("replies", []).append({"author": "claude", "body": body.strip(), "at": common.now_iso()})
    comment = mutate_comment(registration, args.comment, reply)
    print(json.dumps({"id": comment["id"], "status": comment["status"], "replies": len(comment["replies"])}))
    return 0


def repo_relative(repo: dict, raw: str) -> str:
    """The path as the page names it: relative to the repo, with forward slashes."""
    root = Path(repo["path"]).resolve()
    path = Path(raw)
    if path.is_absolute():
        try:
            path = Path(os.path.realpath(path)).relative_to(root)
        except ValueError:
            raise ValueError(f"{raw} isn't inside {repo['name']} ({root})") from None
    relative = path.as_posix()
    if relative in ("", ".") or ".." in path.parts:
        raise ValueError(f"--path must be a file inside {repo['name']}, relative to its root")
    return relative


def cmd_note(args) -> int:
    registration = load(args.id)
    repo = next((r for r in registration["repos"] if r["name"] == args.repo), None)
    if repo is None:
        names = ", ".join(r["name"] for r in registration["repos"])
        return fail(f"no repo {args.repo!r} in review {args.id} (its repos: {names})")
    end = args.end_line if args.end_line is not None else args.line
    if args.line < 1 or end < args.line:
        return fail("bad line range")
    body = args.body if args.body is not None else sys.stdin.read()
    if not body.strip():
        return fail("empty note")
    path = repo_relative(repo, args.path)
    _, base = gitdiff.repo_base(registration, repo)
    entry = next((f for f in gitdiff.changed_files(repo["path"], base) if f["path"] == path), None)

    # The same text the page captures for a reviewer's comment, so the note re-anchors and goes outdated alike:
    # the new side is the working copy, the old side the file at the review's base.
    if args.side == "old":
        text = gitdiff.file_at(repo["path"], base, entry["oldPath"] if entry else path)
    else:
        target = common.inside(Path(repo["path"]), path)
        if not target.is_file():
            return fail(f"{path} isn't a file in {repo['name']}'s working copy; use --side old for a deleted line")
        # Bytes, not read_text: text mode would turn a lone \r into a line break, which git (and so the page) doesn't.
        text = target.read_bytes().decode("utf-8", "replace")
    lines = anchors.source_lines(text)
    if end > len(lines):
        return fail(f"{path} has {len(lines)} lines on the {args.side} side; --line/--end-line reach {end}")

    def add(data):
        note = {"id": f"c{data['nextId']}", "round": None, "repo": repo["name"], "path": path, "side": args.side,
                "line": args.line, "endLine": end, "lineText": "\n".join(lines[args.line - 1:end]),
                "anchorText": lines[end - 1], **anchors.capture(lines, args.line, end), "body": body.strip(),
                "author": "claude", "createdAt": common.now_iso(), "status": "open", "replies": []}
        data["nextId"] += 1
        data["comments"].append(note)
        return note
    note = common.update_comments(registration, add)
    result = {"id": note["id"], "repo": repo["name"], "path": path, "side": args.side, "line": args.line,
              "endLine": end}
    if entry is None:
        result["warning"] = (f"{path} has no changes in this review; the page lists the note under "
                             f"'Comments on files no longer changed'")
    print(json.dumps(result))
    return 0


def cmd_status(args) -> int:
    registration = load(args.id)
    data = common.load_comments(registration)
    try:
        server = health()
    except RuntimeError as e:
        server = {"error": str(e)}
    print(json.dumps({
        "id": registration["id"], "url": f"{base_url()}/review/{registration['id']}",
        "repos": registration["repos"], "store": registration["store"],
        "round": common.current_round(data), "rounds": data["rounds"], "closedAt": data.get("closedAt"),
        "comments": {"total": len(data["comments"]),
                     "open": sum(1 for c in data["comments"] if c["status"] == "open"),
                     "needResponse": sum(1 for c in data["comments"] if common.needs_attention(c))},
        "listener": common.listener_state(registration), "server": server}, indent=2))
    return 0


def cmd_list(args) -> int:
    rows = []
    for registration in common.list_registrations():
        try:
            data = common.load_comments(registration)
        except common.CorruptFile as e:
            rows.append({"id": registration["id"], "error": str(e)})
            continue
        rows.append({"id": registration["id"], "repos": [r["name"] for r in registration["repos"]],
                     "updatedAt": registration.get("updatedAt"), "rounds": len(data["rounds"]),
                     "needResponse": sum(1 for c in data["comments"] if common.needs_attention(c))})
    print(json.dumps(rows, indent=2))
    return 0


def cmd_forget(args) -> int:
    registration = load(args.id)
    store = common.store_dir(registration)
    common.registration_path(args.id).unlink()
    removed = []
    if args.delete_comments:
        # Only the files this tool writes: a custom --store may be a folder that holds other things. The .lock files
        # stay: a server request or waiter may hold one, and a new lock file would let its next writer in alongside.
        for name in common.STORE_FILES:
            path = store / name
            if path.is_file():
                path.unlink()
                removed.append(str(path))
    print(json.dumps({"forgot": args.id, "deleted": removed}))
    return 0


def same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def repo_gone(repo: dict) -> str | None:
    """Why a registered repo no longer exists, None while it does. A review registered without a branch (before
    branches were recorded, or on a detached HEAD) is judged on its path alone."""
    path = repo["path"]
    if not Path(path).is_dir():
        return "path no longer exists"
    # Raises GitError when git can't say (unsafe ownership, a broken config): prune keeps the review then.
    # A folder left behind inside some other repo is a work tree too, but it isn't the repo that was registered.
    if not gitdiff.work_tree_state(path) or not same_path(gitdiff.top_level(path), path):
        return "not a git work tree"
    branch = repo.get("branch")
    if branch and not gitdiff.branch_exists(path, branch):
        return f"branch {branch} no longer exists"
    return None


def prune_verdict(registration: dict) -> tuple[bool, str]:
    """(prune it, why). Never prunes a review a session is listening on."""
    try:
        listener = common.listener_state(registration)
    except common.CorruptFile as e:
        return False, f"kept: {e}"
    if listener and listener.get("fresh"):
        return False, f"a session is listening (session {listener.get('session')})"
    repos = registration.get("repos") or []
    if not repos:
        return False, "no repos registered"
    try:
        gone = [(r["name"], repo_gone(r)) for r in repos]
    except gitdiff.GitError as e:
        return False, f"kept: {e}"
    alive = [name for name, why in gone if why is None]
    if alive:
        return False, f"still exists: {', '.join(alive)}"
    return True, "; ".join(f"{name}: {why}" for name, why in gone)


def prune_paths(registration: dict) -> dict:
    """Everything prune removes for one review, in the order it removes them."""
    store = common.store_dir(registration)
    review_id = registration["id"]
    return {"registration": common.registration_path(review_id),
            "data": [store / name for name in common.STORE_FILES],
            "locks": [store / f"{name}.lock" for name in common.STORE_FILES],
            "registryLock": common.registration_path(review_id).with_name(f"{review_id}.json.lock"),
            "store": store}


def prune_one(registration: dict) -> tuple[bool, str, list[str]]:
    """Forget one review and its leftovers, under the review's own locks. Returns (pruned, why, removed paths).
    The verdict is taken again once the locks are held, so a review that was reopened, or got a listener, since the
    first look is left alone, and no writer is mid-write while its files go."""
    review_id = registration["id"]
    removed: list[str] = []

    def remove(path: Path, best_effort: bool = False) -> None:
        try:
            path.unlink()
            removed.append(str(path))
        except FileNotFoundError:
            pass
        except OSError:
            if not best_effort:
                raise

    # The registry lock first, then the store's, the order `open` and the server never invert. A writer that loaded
    # the registration earlier finds it gone once it gets its lock (common.review_alive) and writes nothing.
    with common.locked(common.registration_path(review_id)):
        current = common.read_json(common.registration_path(review_id), None)
        if current is None:
            return False, "already forgotten", []
        # From what is registered now: a concurrent `open --store` may have moved the store since the first look.
        paths = prune_paths(current)
        with contextlib.ExitStack() as stack:
            for name in common.STORE_FILES:
                stack.enter_context(common.locked(common.store_dir(current) / name))
            prune, why = prune_verdict(current)
            if not prune:
                return False, why, []
            remove(paths["registration"])
            for path in paths["data"]:
                remove(path)
    # The lock files go once released: Windows can't delete a file another handle holds open (then it stays, which
    # is harmless). Nothing can be writing by now, since the registration is gone; a process still waiting on one
    # finds it replaced and retries (common.locked).
    for path in paths["locks"] + [paths["registryLock"]]:
        remove(path, best_effort=True)
    try:
        paths["store"].rmdir()  # only when empty: a custom --store may hold other things
        removed.append(str(paths["store"]))
    except OSError:
        pass
    return True, why, removed


def cmd_prune(args) -> int:
    pruned, kept = [], []
    for registration in common.list_registrations():
        review_id = registration["id"]
        prune, why = prune_verdict(registration)
        if not prune:
            kept.append({"id": review_id, "reason": why})
        elif args.dry_run:
            paths = prune_paths(registration)
            files = [paths["registration"], *paths["data"], *paths["locks"], paths["registryLock"]]
            would = [str(p) for p in files if p.exists()]
            store = paths["store"]
            if store.is_dir() and all(child in files for child in store.iterdir()):
                would.append(str(store))
            pruned.append({"id": review_id, "reason": why, "deleted": would})
        else:
            done, why, removed = prune_one(registration)
            (pruned if done else kept).append({"id": review_id, "reason": why, **({"deleted": removed} if done else {})})
    print(json.dumps({"dryRun": args.dry_run, "pruned": pruned, "kept": kept}, indent=2))
    return 0


def cmd_stop(args) -> int:
    try:
        running = health()
    except RuntimeError as e:
        return fail(str(e))
    if not running:
        print(json.dumps({"stopped": False, "reason": "not running"}))
        return 0
    request_shutdown()
    print(json.dumps({"stopped": wait_for(lambda: health() is None, 5)}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("open")
    p.add_argument("repos", nargs="+")
    p.add_argument("--id")
    p.add_argument("--title")
    p.add_argument("--base")
    p.add_argument("--store")
    p.add_argument("--replace", action="store_true")
    p.add_argument("--resume-hint")
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(func=cmd_open)

    p = sub.add_parser("wait")
    p.add_argument("id")
    p.add_argument("--session")
    p.add_argument("--until")
    p.add_argument("--minutes", type=float)
    p.add_argument("--interval", type=float, default=2)
    p.set_defaults(func=cmd_wait)

    p = sub.add_parser("read")
    p.add_argument("id")
    p.add_argument("--all", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--peek", action="store_true")
    p.set_defaults(func=cmd_read)

    p = sub.add_parser("reply")
    p.add_argument("id")
    p.add_argument("comment")
    p.add_argument("--body")
    p.set_defaults(func=cmd_reply)

    p = sub.add_parser("note")
    p.add_argument("id")
    p.add_argument("--repo", required=True)
    p.add_argument("--path", required=True)
    p.add_argument("--line", type=int, required=True)
    p.add_argument("--end-line", type=int)
    p.add_argument("--side", choices=("new", "old"), default="new")
    p.add_argument("--body")
    p.set_defaults(func=cmd_note)

    p = sub.add_parser("prune")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_prune)

    for name, func in (("status", cmd_status), ("forget", cmd_forget)):
        p = sub.add_parser(name)
        p.add_argument("id")
        if name == "forget":
            p.add_argument("--delete-comments", action="store_true")
        p.set_defaults(func=func)
    sub.add_parser("list").set_defaults(func=cmd_list)
    sub.add_parser("stop").set_defaults(func=cmd_stop)

    args = parser.parse_args()
    try:
        return args.func(args)
    except (ValueError, RuntimeError, gitdiff.GitError) as e:
        return fail(str(e))


if __name__ == "__main__":
    sys.exit(main())
