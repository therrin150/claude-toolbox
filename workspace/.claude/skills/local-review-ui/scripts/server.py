"""The local-review-ui server: one per machine, on 127.0.0.1, serving every registered review.

  server.py [--port N] [--idle-hours 12]

Started by `review.py open` (detached); you rarely run it by hand. It reads registrations from the registry that
review.py writes, reads diffs with gitdiff (never writing to a repo), and reads and writes each review's comments
under the same file lock the CLI uses. It exits after --idle-hours with no requests; the next `open` restarts it.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import anchors
import common
import gitdiff

MAX_BODY = 1_000_000
# Windows runs a .cmd or .bat launcher (the JetBrains Toolbox's idea.cmd, VS Code's code.cmd) through cmd.exe, and
# Python's quoting doesn't escape cmd's metacharacters, so a file named `x&calc&.txt` would run calc.
CMD_UNSAFE = set('"&|<>^%!\r\n')
# The only files the server serves, so a request path never reaches the file system.
ASSET_FILES = {name: common.ASSETS / name for name in ("index.html", "app.js", "app.css", "highlight.js")}
STATIC_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8"}


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class ReviewServer(ThreadingHTTPServer):
    daemon_threads = True
    # SO_REUSEADDR lets a second listener steal the port on Windows; there we ask for exclusive use instead.
    allow_reuse_address = not common.IS_WINDOWS

    def server_bind(self):
        if common.IS_WINDOWS and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def __init__(self, port: int):
        super().__init__(("127.0.0.1", port), Handler)
        self.port = port
        self.version = common.version()
        self.last_request = time.monotonic()
        self.diff_cache: dict = {}
        self.cache_lock = threading.Lock()


def find_repo(registration: dict, name: str) -> dict:
    for repo in registration["repos"]:
        if repo["name"] == name:
            return repo
    raise ApiError(404, f"no repo {name!r} in review {registration['id']}")


repo_base = gitdiff.repo_base


class Handler(BaseHTTPRequestHandler):
    server: ReviewServer
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if os.environ.get("LOCAL_REVIEW_DEBUG"):
            super().log_message(fmt, *args)

    # --- plumbing -----------------------------------------------------------------------------------------------------

    def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if self._content_length() and not self._body_read:
            # The body is still in the socket (an error before _body, or a route that takes none). Close rather
            # than let a keep-alive connection parse those bytes as the next request.
            self.send_header("Connection", "close")
            self.close_connection = True
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, data):
        self._send(status, json.dumps(data).encode("utf-8"), "application/json; charset=utf-8")

    def _allowed_hosts(self) -> set[str]:
        return {f"127.0.0.1:{self.server.port}", f"localhost:{self.server.port}"}

    def _content_length(self) -> int:
        try:
            return max(0, int(self.headers.get("Content-Length") or 0))
        except ValueError:
            return 1  # unparseable: treat as a body we can't consume

    def _check_request(self):
        # A Host check blocks DNS rebinding (a website resolving its own name to 127.0.0.1 to read the diffs).
        if self.headers.get("Host", "") not in self._allowed_hosts():
            raise ApiError(403, "bad host")
        if self.command in ("POST", "PUT", "PATCH", "DELETE"):
            origin = self.headers.get("Origin")
            if origin is not None and origin.split("://", 1)[-1] not in self._allowed_hosts():
                raise ApiError(403, "bad origin")
            if self._content_length() and not self.headers.get("Content-Type", "").startswith("application/json"):
                raise ApiError(415, "JSON only")

    def _body(self) -> dict:
        length = self._content_length()
        if length > MAX_BODY:
            raise ApiError(413, "body too large")
        if not length:
            return {}
        raw = self.rfile.read(length)
        self._body_read = True
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError:
            raise ApiError(400, "invalid JSON")
        if not isinstance(data, dict):
            raise ApiError(400, "expected a JSON object")
        return data

    def _dispatch(self):
        self.server.last_request = time.monotonic()
        self._body_read = False
        url = urlsplit(self.path)
        parts = [unquote(p) for p in url.path.split("/") if p]
        query = {k: v[-1] for k, v in parse_qs(url.query).items()}
        try:
            self._check_request()
            self._route(parts, query)
        except ApiError as e:
            self._json(e.status, {"error": str(e)})
        except gitdiff.GitError as e:
            self._json(500, {"error": f"git: {e}"})
        except ValueError as e:
            self._json(400, {"error": str(e)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            # Anything else (a malformed body field, a file permission) still gets an answer instead of a dropped
            # connection, which the page could only show as "Failed to fetch".
            traceback.print_exc()
            self._json(500, {"error": f"{type(e).__name__}: {e}"})

    do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = _dispatch

    # --- routes -------------------------------------------------------------------------------------------------------

    def _route(self, parts: list[str], query: dict):
        method = self.command if self.command != "HEAD" else "GET"

        if method == "GET" and parts == ["health"]:
            return self._json(200, {"name": common.TOOL, "version": self.server.version, "pid": os.getpid(),
                                    "port": self.server.port})
        if method == "POST" and parts == ["api", "shutdown"]:
            if self.headers.get("Origin") is not None:
                raise ApiError(403, "shutdown is for the CLI")
            self._json(200, {"stopping": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return None
        if method == "GET" and (not parts or (len(parts) == 2 and parts[0] == "review")):
            return self._static("index.html")
        if method == "GET" and len(parts) == 2 and parts[0] == "assets":
            return self._static(parts[1])
        if parts[:2] == ["api", "reviews"]:
            if len(parts) == 2 and method == "GET":
                return self._json(200, [self._summary(r) for r in common.list_registrations()])
            if len(parts) >= 3:
                registration = common.load_registration(parts[2])
                if registration is None:
                    raise ApiError(404, f"no review {parts[2]!r}; run review.py open")
                return self._review_route(method, registration, parts[3:], query)
        raise ApiError(404, "not found")

    def _static(self, name: str):
        path = ASSET_FILES.get(name)
        if path is None or not path.is_file():
            raise ApiError(404, "not found")
        self._send(200, path.read_bytes(), STATIC_TYPES[path.suffix])

    def _summary(self, registration: dict) -> dict:
        try:
            data = common.load_comments(registration)
        except common.CorruptFile as e:
            # One broken review shows its error on its own row instead of hiding every other review.
            data, error = common.empty_comments(), str(e)
        else:
            error = None
        return {"id": registration["id"], "title": registration.get("title"), "error": error,
                "updatedAt": registration.get("updatedAt"),
                "closed": bool(data.get("closedAt")),
                "repos": [r["name"] for r in registration["repos"]], "rounds": len(data["rounds"]),
                "open": sum(1 for c in data["comments"] if c.get("status") == "open")}

    def _review_route(self, method: str, registration: dict, rest: list[str], query: dict):
        if method == "GET" and not rest:
            return self._json(200, self._review(registration))
        if method == "GET" and rest == ["state"]:
            return self._json(200, self._state(registration))
        if method == "GET" and rest == ["files"]:
            return self._json(200, self._files(registration, query))
        if method == "GET" and rest == ["diff"]:
            return self._json(200, self._diff(registration, query))
        if method == "GET" and rest == ["comments"]:
            return self._json(200, {**common.load_comments(registration),
                                    "viewed": common.read_json(common.store_dir(registration) / "viewed.json", {})})
        if method == "POST" and rest == ["comments"]:
            return self._json(201, self._add_comment(registration, self._body()))
        if len(rest) >= 2 and rest[0] == "comments":
            return self._comment_route(method, registration, rest[1], rest[2:])
        if method == "PUT" and rest == ["viewed"]:
            return self._json(200, self._set_viewed(registration, self._body()))
        if method == "POST" and rest == ["open-in-ide"]:
            return self._json(200, self._open_in_ide(registration, self._body()))
        if method == "POST" and rest == ["reopen"]:
            common.update_comments(registration, lambda data: data.update(closedAt=None))
            return self._json(200, {"reopened": True})
        if method == "POST" and rest == ["submit"]:
            return self._json(200, self._submit(registration, self._body()))
        raise ApiError(404, "not found")

    # --- review data --------------------------------------------------------------------------------------------------

    def _review(self, registration: dict) -> dict:
        repos = []
        for repo in registration["repos"]:
            info = {"name": repo["name"], "path": repo["path"]}
            try:
                base_ref, base = repo_base(registration, repo)
                info.update(branch=gitdiff.current_branch(repo["path"]), baseRef=base_ref, mergeBase=base[:10])
            except gitdiff.GitError as e:
                info["error"] = str(e)
            repos.append(info)
        config = common.load_config()
        return {"id": registration["id"], "title": registration.get("title"), "repos": repos,
                "editorUrl": config.get("editorUrl") or common.DEFAULT_EDITOR_URL,
                "editorCommand": bool(config.get("editorCommand")),
                "resumeHint": registration.get("resumeHint"),
                "listener": common.listener_state(registration)}

    def _state(self, registration: dict) -> dict:
        data = common.load_comments(registration)
        prints = {}
        for repo in registration["repos"]:
            try:
                prints[repo["name"]] = gitdiff.fingerprint(repo["path"])
            except OSError:
                prints[repo["name"]] = None
        return {"commentsVersion": data["version"], "round": common.current_round(data),
                "closedAt": data.get("closedAt"),
                "fingerprints": prints, "resumeHint": registration.get("resumeHint"),
                "listener": common.listener_state(registration)}

    def _cached(self, key: tuple, compute):
        with self.server.cache_lock:
            if key in self.server.diff_cache:
                return self.server.diff_cache[key]
        value = compute()
        with self.server.cache_lock:
            if len(self.server.diff_cache) > 400:
                self.server.diff_cache.clear()
            self.server.diff_cache[key] = value
        return value

    def _files(self, registration: dict, query: dict) -> dict:
        repo = find_repo(registration, query.get("repo", ""))
        base_ref, base = repo_base(registration, repo)
        fp = gitdiff.fingerprint(repo["path"])
        files = self._cached(("files", repo["path"], base, fp), lambda: gitdiff.changed_files(repo["path"], base))
        return {"repo": repo["name"], "baseRef": base_ref, "mergeBase": base, "fingerprint": fp, "files": files}

    def _diff(self, registration: dict, query: dict) -> dict:
        repo = find_repo(registration, query.get("repo", ""))
        path = query.get("path", "")
        context = gitdiff.FULL_CONTEXT if query.get("context") == "full" else int(query.get("context") or 3)
        _, base = repo_base(registration, repo)
        fp = gitdiff.fingerprint(repo["path"])
        files = self._cached(("files", repo["path"], base, fp), lambda: gitdiff.changed_files(repo["path"], base))
        entry = next((f for f in files if f["path"] == path), None)
        if entry is None:
            raise ApiError(404, f"{path} has no changes in {repo['name']}")
        diff = self._cached(("diff", repo["path"], base, fp, path, context),
                            lambda: gitdiff.file_diff(repo["path"], base, entry, context))
        comments = [c for c in common.load_comments(registration)["comments"]
                    if c.get("repo") == repo["name"] and c.get("path") == path]
        return self._cached(("anchored", repo["path"], base, fp, path, context, anchors.digest(comments)),
                            lambda: anchors.place(repo["path"], base, entry, diff, comments))

    # --- comments -----------------------------------------------------------------------------------------------------

    @staticmethod
    def _int(body: dict, key: str, default: int = 0) -> int:
        value = body.get(key)
        if value is None or value == "":
            return default
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ApiError(400, f"{key} must be a number")
        try:
            return int(value)
        except ValueError:
            raise ApiError(400, f"{key} must be a number")

    @staticmethod
    def _text(body: dict, key: str = "body") -> str:
        value = body.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ApiError(400, f"{key} is required")
        return value.strip()

    def _add_comment(self, registration: dict, body: dict) -> dict:
        repo = find_repo(registration, body.get("repo", ""))
        side = body.get("side")
        if side not in ("old", "new"):
            raise ApiError(400, "side must be old or new")
        line = self._int(body, "line")
        end_line = self._int(body, "endLine", line)
        if line < 1 or end_line < line:
            raise ApiError(400, "bad line range")
        text = self._text(body)
        path = str(body.get("path") or "")
        around = self._around(registration, repo, path, side, line, end_line)

        def add(data):
            comment = {"id": f"c{data['nextId']}", "round": common.current_round(data), "repo": repo["name"],
                       "path": path, "side": side, "line": line, "endLine": end_line,
                       "lineText": str(body.get("lineText") or ""), "anchorText": str(body.get("anchorText") or ""),
                       **around, "body": text, "author": "user",
                       "createdAt": common.now_iso(), "status": "open", "replies": []}
            data["nextId"] += 1
            data["comments"].append(comment)
            return comment
        return common.update_comments(registration, add)

    @staticmethod
    def _around(registration: dict, repo: dict, path: str, side: str, line: int, end_line: int) -> dict:
        """The lines around a new comment, so it finds its place after the code changes; none when the file can't
        be read, which only costs the comment that help."""
        try:
            _, base = repo_base(registration, repo)
            entry = next((f for f in gitdiff.changed_files(repo["path"], base) if f["path"] == path), None)
            lines = anchors.side_lines(repo["path"], base, path, entry["oldPath"] if entry else path, side)
        except gitdiff.GitError:
            return {}
        return anchors.capture(lines, line, end_line) if lines else {}

    def _comment_route(self, method: str, registration: dict, comment_id: str, rest: list[str]):
        if method == "POST" and rest == ["replies"]:
            text = self._text(self._body())

            def reply(comment, data):
                comment["replies"].append({"author": "user", "body": text, "at": common.now_iso()})
                comment["status"] = "open"
            return self._json(201, self._mutate_comment(registration, comment_id, reply))
        if method == "PATCH" and not rest:
            body = self._body()

            def patch(comment, data):
                if "status" in body:
                    if body["status"] not in ("open", "resolved"):
                        raise ApiError(400, "status must be open or resolved")
                    comment["status"] = body["status"]
                if "body" in body:
                    if comment["replies"]:
                        raise ApiError(409, "a comment with replies can't be edited")
                    comment["body"] = self._text(body)
            return self._json(200, self._mutate_comment(registration, comment_id, patch))
        if method == "DELETE" and not rest:
            def delete(comment, data):
                if comment["replies"]:
                    raise ApiError(409, "a comment with replies can't be deleted; resolve it instead")
                data["comments"].remove(comment)
            self._mutate_comment(registration, comment_id, delete)
            return self._json(200, {"deleted": comment_id})
        raise ApiError(404, "not found")

    def _mutate_comment(self, registration: dict, comment_id: str, change) -> dict:
        def apply(data):
            comment = next((c for c in data["comments"] if c["id"] == comment_id), None)
            if comment is None:
                raise ApiError(404, f"no comment {comment_id}")
            change(comment, data)
            return comment
        return common.update_comments(registration, apply)

    def _set_viewed(self, registration: dict, body: dict) -> dict:
        key = f"{body.get('repo')}:{body.get('path')}"

        def mark(data):
            if body.get("viewed"):
                data[key] = body.get("fingerprint") or True
            else:
                data.pop(key, None)
            return data
        return common.update_json(common.store_dir(registration) / "viewed.json", {}, mark,
                                  alive=common.review_alive(registration))

    def _open_in_ide(self, registration: dict, body: dict) -> dict:
        """Opens the repo as a project with the file at the line, through the configured editorCommand. The command
        comes only from config.json; the browser supplies just which registered repo, file and line."""
        repo = find_repo(registration, body.get("repo", ""))
        # Only a file in the review's own diff, taken from git's list, so the browser can't name an arbitrary path.
        _, base = repo_base(registration, repo)
        fp = gitdiff.fingerprint(repo["path"])
        files = self._cached(("files", repo["path"], base, fp), lambda: gitdiff.changed_files(repo["path"], base))
        entry = next((f for f in files if f["path"] == body.get("path")), None)
        if entry is None:
            raise ApiError(404, "not a changed file in this review")
        root = Path(repo["path"])
        target = root / entry["path"]
        file = str(target) if target.is_file() else None
        command = common.editor_command(str(root), file, max(1, self._int(body, "line", 1)))
        if not command:
            raise ApiError(409, "no editorCommand configured; run install.py configure --editor intellij")
        if common.IS_WINDOWS and command[0].lower().endswith((".cmd", ".bat")):
            if any(CMD_UNSAFE & set(arg) for arg in command[1:]):
                raise ApiError(400, "the path has characters cmd.exe would run as commands; open it in the IDE by hand")
        try:
            common.spawn_detached(command, cwd=str(root))
        except OSError as e:
            raise ApiError(500, f"couldn't start {command[0]}: {e}")
        return {"opened": True, "file": file}

    def _submit(self, registration: dict, body: dict) -> dict:
        summary = body.get("summary")
        summary = summary.strip() if isinstance(summary, str) and summary.strip() else None
        done = bool(body.get("done"))

        def submit(data):
            n = common.current_round(data)
            entry = {"n": n, "submittedAt": common.now_iso(), "summary": summary, "done": done,
                     "comments": [c["id"] for c in data["comments"] if c.get("round") == n],
                     "acknowledgedAt": None}
            data["rounds"].append(entry)
            # Done reviewing closes the review: the agent answers what's left and stops waiting. Not approval.
            if done:
                data["closedAt"] = entry["submittedAt"]
            return entry
        entry = common.update_comments(registration, submit)
        return {"round": entry, "listener": common.listener_state(registration)}


def idle_watch(server: ReviewServer, idle_seconds: float):
    while True:
        time.sleep(60)
        if time.monotonic() - server.last_request > idle_seconds:
            server.shutdown()
            return


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=common.port())
    parser.add_argument("--idle-hours", type=float, default=12)
    args = parser.parse_args()

    try:
        server = ReviewServer(args.port)
    except OSError as e:
        # Another launcher won the race (or something else holds the port); the caller checks /health.
        print(f"cannot bind 127.0.0.1:{args.port}: {e}", file=sys.stderr)
        return 3

    state = {"pid": os.getpid(), "port": args.port, "version": server.version, "startedAt": common.now_iso(),
             "python": sys.executable}
    common.write_json_atomic(common.home() / "server.json", state)
    print(f"{common.TOOL} {server.version} listening on http://127.0.0.1:{args.port}/", flush=True)
    threading.Thread(target=idle_watch, args=(server, args.idle_hours * 3600), daemon=True).start()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        current = common.read_json(common.home() / "server.json", {})
        if current.get("pid") == os.getpid():
            try:
                (common.home() / "server.json").unlink()
            except OSError:
                pass
        print("stopped", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
