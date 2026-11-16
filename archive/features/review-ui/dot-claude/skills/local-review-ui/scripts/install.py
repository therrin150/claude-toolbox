"""First-time setup for local-review-ui, built to be run by a Claude session on the user's behalf.

  install.py check                      what's present and what's missing, as JSON (changes nothing)
  install.py configure [--workspace-root DIR] [--port N] [--editor intellij|vscode] [--default-base REF]
                        [--editor-command JSON] [--editor-url TEMPLATE]
  install.py verify                     start the server, confirm it answers, and print its URL

`check` lists every question it couldn't answer itself under "needs", each with the question to ask the user and
the candidates it found, so the session answers what it can and asks only the rest. Settings land in
<LOCAL_REVIEW_HOME>/config.json (default ~/.local-review/config.json).

"Open in IDE" runs editorCommand, a JSON list of arguments with {project}, {file} and {line} placeholders, so the
repo opens as a project (its usual name in the IDE's recent list) with the file at the line. --editor fills it from
the IDE install it finds, e.g. on macOS:
  ["open", "-na", "/Applications/IntelliJ IDEA.app", "--args", "{project}", "--line", "{line}", "{file}"]
Without an editorCommand the button falls back to editorUrl, a URL-scheme link that opens only the file:
  IntelliJ   idea://open?file={path}&line={line}     (the default)
  VS Code    vscode://file/{path}:{line}
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import common

MIN_PYTHON = (3, 9)
EDITORS = {
    "intellij": "idea://open?file={path}&line={line}",
    "vscode": "vscode://file/{path}:{line}",
}
WORKSPACE_GUESSES = ["~/workspace", "~/work", "~/dev", "~/code", "~/src", "~/projects",
                     "~/IdeaProjects", "~/git", "~/repos", "~/source/repos"]


def git_version() -> str | None:
    try:
        result = subprocess.run(["git", "--version"], capture_output=True, text=True)
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def git_repo_count(folder: Path) -> int:
    try:
        return sum(1 for child in folder.iterdir() if (child / ".git").exists())
    except OSError:
        return 0


def toolbox_workspace() -> Path | None:
    """The toolbox workspace this skill is installed in: the nearest folder above the current one holding
    `.claude/toolbox.local.yml`. Only a nested workspace holds its repos side by side."""
    for folder in (Path.cwd().resolve(), *Path.cwd().resolve().parents):
        config = folder / ".claude" / "toolbox.local.yml"
        if config.is_file():
            # Standard library only, so no YAML parser: `layout` is the one key of that name in the config.
            found = re.search(r"^\s+layout:\s*(\w+)", config.read_text(encoding="utf-8"), re.MULTILINE)
            layout = found.group(1) if found else None
            return folder if layout == "nested" else None
    return None


def workspace_candidates() -> list[dict]:
    """Folders holding several git checkouts side by side; the toolbox workspace comes first when it is nested."""
    seen: dict[str, dict] = {}
    workspace = toolbox_workspace()
    if workspace:
        seen[str(workspace)] = {"path": str(workspace), "repos": git_repo_count(workspace),
                                "why": "the toolbox workspace this skill is installed in"}
    for guess in WORKSPACE_GUESSES:
        folder = Path(guess).expanduser()
        if folder.is_dir() and str(folder) not in seen:
            count = git_repo_count(folder)
            if count >= 2:
                seen[str(folder)] = {"path": str(folder), "repos": count, "why": "common workspace location"}
    return list(seen.values())


def detect_editors() -> list[str]:
    found = []
    system = platform.system()
    if system == "Darwin":
        apps = [Path("/Applications"), Path.home() / "Applications"]
        if any(list(folder.glob("IntelliJ IDEA*.app")) for folder in apps if folder.is_dir()):
            found.append("intellij")
    elif system == "Windows":
        roots = [os.environ.get("ProgramFiles", ""), os.environ.get("LOCALAPPDATA", "")]
        for root in filter(None, roots):
            jetbrains = Path(root) / "JetBrains"
            if jetbrains.is_dir() and any("IntelliJ" in p.name or "Toolbox" in p.name for p in jetbrains.iterdir()):
                found.append("intellij")
                break
    else:
        if shutil.which("idea") or shutil.which("idea.sh") or shutil.which("intellij-idea-ultimate"):
            found.append("intellij")
    if shutil.which("code"):
        found.append("vscode")
    return found


def intellij_launcher() -> list[str] | None:
    """How to start IntelliJ with arguments on this OS, or None if it isn't found."""
    system = platform.system()
    if system == "Darwin":
        for folder in (Path("/Applications"), Path.home() / "Applications"):
            apps = sorted(folder.glob("IntelliJ IDEA*.app")) if folder.is_dir() else []
            if apps:
                return ["open", "-na", str(apps[0]), "--args"]
        return None
    if system == "Windows":
        local = os.environ.get("LOCALAPPDATA", "")
        toolbox = Path(local) / "JetBrains" / "Toolbox" / "scripts" / "idea.cmd"
        if local and toolbox.is_file():
            return [str(toolbox)]
        for root in filter(None, [os.environ.get("ProgramFiles", ""), local and str(Path(local) / "Programs")]):
            exes = sorted(Path(root).glob("JetBrains/IntelliJ*/bin/idea64.exe"))
            exes += sorted(Path(root).glob("IntelliJ*/bin/idea64.exe"))
            if exes:
                return [str(exes[-1])]
    for name in ("idea", "idea.sh", "intellij-idea-ultimate", "intellij-idea-community"):
        found = shutil.which(name)
        if found:
            return [found]
    return None


def editor_command_for(editor: str) -> list[str] | None:
    if editor == "intellij":
        launcher = intellij_launcher()
        return launcher + ["{project}", "--line", "{line}", "{file}"] if launcher else None
    if editor == "vscode":
        code = shutil.which("code")
        return [code, "{project}", "-g", "{file}:{line}"] if code else None
    return None


def port_state(port: int) -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        if probe.connect_ex(("127.0.0.1", port)) != 0:
            return "free"
    sys.path.insert(0, str(common.SCRIPTS))
    import review
    try:
        return "local-review-ui" if review.health() else "free"
    except RuntimeError:
        return "other-program"


def cmd_check(args) -> int:
    config = common.load_config()
    py_ok = sys.version_info >= MIN_PYTHON
    git = git_version()
    port = common.port()
    editors = detect_editors()
    needs = []

    if not py_ok:
        needs.append({"item": "python", "question": f"Python {'.'.join(map(str, MIN_PYTHON))}+ is required; "
                                                   f"this is {platform.python_version()}. Install a newer Python?"})
    if not git:
        needs.append({"item": "git", "question": "git isn't on PATH. Install git, or add it to PATH?"})
    candidates = workspace_candidates()
    if not config.get("workspaceRoot"):
        needs.append({"item": "workspaceRoot",
                      "question": "Which folder holds your repo checkouts side by side? Reviews can then name repos "
                                  "by folder name. (Optional: full paths always work.)",
                      "candidates": candidates})
    state = port_state(port)
    if state == "other-program":
        needs.append({"item": "port", "question": f"Port {port} is taken by another program. Pick another "
                                                  f"(e.g. {port + 1})?"})
    if not config.get("editorCommand"):
        options = [{"editor": e, "command": editor_command_for(e)} for e in editors]
        options = [o for o in options if o["command"]]
        needs.append({"item": "editor",
                      "question": "Which IDE should 'Open in IDE' open the repo in? (configure --editor <name>; "
                                  "with one option, use it without asking)",
                      "candidates": options})

    print(json.dumps({
        "os": platform.system(),
        "python": {"version": platform.python_version(), "executable": sys.executable, "ok": py_ok},
        "git": {"version": git, "ok": bool(git)},
        "home": str(common.home()),
        "config": config,
        "port": {"value": port, "state": state},
        "editorsFound": editors,
        "skill": {"folder": str(common.SKILL_DIR)},
        "needs": needs,
        "ready": py_ok and bool(git) and state != "other-program",
    }, indent=2))
    return 0


def cmd_configure(args) -> int:
    changes = {}
    if args.workspace_root:
        root = Path(args.workspace_root).expanduser().resolve()
        if not root.is_dir():
            print(f"{root} isn't a folder", file=sys.stderr)
            return 1
        changes["workspaceRoot"] = str(root)
    if args.port:
        changes["port"] = args.port
    if args.editor:
        command = editor_command_for(args.editor)
        if not command:
            print(f"couldn't find {args.editor} on this machine; pass --editor-command instead", file=sys.stderr)
            return 1
        changes["editorCommand"] = command
        changes["editorUrl"] = EDITORS[args.editor]
    if args.editor_command:
        command = json.loads(args.editor_command)
        if not isinstance(command, list) or not all(isinstance(a, str) for a in command):
            print("--editor-command must be a JSON list of strings", file=sys.stderr)
            return 1
        changes["editorCommand"] = command
    if args.editor_url:
        changes["editorUrl"] = EDITORS.get(args.editor_url, args.editor_url)
    if args.default_base:
        changes["defaultBase"] = args.default_base

    def apply(data):
        data.update(changes)
        return data
    config = common.update_json(common.home() / "config.json", {}, apply)
    print(json.dumps(config, indent=2))
    return 0


def cmd_verify(args) -> int:
    sys.path.insert(0, str(common.SCRIPTS))
    import review
    try:
        server = review.ensure_server()
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "url": f"{review.base_url()}/", "server": server}, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check").set_defaults(func=cmd_check)
    p = sub.add_parser("configure")
    p.add_argument("--workspace-root")
    p.add_argument("--port", type=int)
    p.add_argument("--editor", choices=sorted(EDITORS), help="detect the IDE and set editorCommand and editorUrl")
    p.add_argument("--editor-command", help='JSON list with {project}, {file} and {line}')
    p.add_argument("--editor-url", help="intellij, vscode, or a template with {path} and {line}")
    p.add_argument("--default-base")
    p.set_defaults(func=cmd_configure)
    sub.add_parser("verify").set_defaults(func=cmd_verify)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
