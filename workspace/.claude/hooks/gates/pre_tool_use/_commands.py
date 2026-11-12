"""Helpers the PreToolUse gates share (not a gate: the dispatcher skips files starting with `_`).

A gate loads this by path (importlib, beside itself), since the dispatcher loads gates one file at a time and
`gates/pre_tool_use/` is not on sys.path.

- simple_commands(command) splits a Bash command line into its simple commands, each a list of words with leading
  `VAR=value` assignments and wrappers (`env`, `time`, `sudo`, ...) dropped, and `bash -c "..."` payloads split too. A
  quoted string stays one word, so `git commit -m "fix; mvn test"` is one `git` command, never an `mvn` one.
- area_commands(event) is the workspace config's `build`, `unit`, `format` and `integration` commands as
  [(kind, command)], from the config found above the event's directory; empty when there is none or it can't be read.
- script_args(words) is a command's script basename and the arguments after it, looking through `python3 script.py`.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shlex
import sys
from pathlib import Path

sys.dont_write_bytecode = True

WRAPPERS = {"env", "time", "nice", "nohup", "sudo", "command", "exec", "builtin", "caffeinate"}
WRAPPER_OPTION_ARGS = {"nice": {"-n"}, "sudo": {"-u", "-g", "-C", "-h", "-p"}}  # options that take a word of their own
SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
SETUP = {"cd", "echo", "export", "set", "source", ".", "true", ":", "printf", "pushd", "popd", "test", "["}
KINDS = ("build", "unit", "format", "integration")
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
PUNCTUATION = ";()<>|&\n"
MAX_DEPTH = 4


def words(command: str) -> list[str]:
    """The command line's words, shell operators as words of their own (`;`, `&&`, `|`, a newline)."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=PUNCTUATION)
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    try:
        return list(lexer)
    except ValueError:  # an unbalanced quote: fall back to splitting on whitespace
        return re.sub(r"([;&|()\n])", r" \1 ", command).split()


def is_operator(word: str) -> bool:
    return bool(word) and all(c in PUNCTUATION for c in word) and "<" not in word and ">" not in word


def simple_commands(command: str, depth: int = 0) -> list[list[str]]:
    """The simple commands of a Bash command line, each as its words (see the module doc)."""
    found: list[list[str]] = []
    current: list[str] = []
    for word in [*words(command), ";"]:
        if not is_operator(word):
            current.append(word)
            continue
        if current:
            found.append(current)
        current = []
    result = []
    for simple in found:
        simple = strip_prefix(simple)
        if not simple:
            continue
        result.append(simple)
        if depth < MAX_DEPTH and os.path.basename(simple[0]) in SHELLS:
            for i, word in enumerate(simple[1:-1], 1):
                if word.startswith("-") and not word.startswith("--") and "c" in word and i + 1 < len(simple):
                    result.extend(simple_commands(simple[i + 1], depth + 1))
                    break
    return result


def strip_prefix(simple: list[str]) -> list[str]:
    """Drop leading `VAR=value` words and wrappers, with the options (and their arguments) after a wrapper."""
    i = 0
    wrapper = None
    while i < len(simple):
        word = simple[i]
        if ASSIGNMENT.match(word):
            i += 1
        elif word in WRAPPERS:
            wrapper = word
            i += 1
        elif wrapper and word.startswith("-"):
            i += 2 if word in WRAPPER_OPTION_ARGS.get(wrapper, ()) else 1
        else:
            break
    return simple[i:]


def script_args(simple: list[str]) -> tuple[str, list[str]]:
    """The basename of the program or script a command runs and the words after it: `python3 -I a/run_checks.py unit`
    is ("run_checks.py", ["unit"])."""
    name = os.path.basename(simple[0])
    rest = simple[1:]
    if re.fullmatch(r"python[0-9.]*", name):
        while rest and rest[0].startswith("-"):
            rest = rest[1:]
        if rest:
            return os.path.basename(rest[0]), rest[1:]
    return name, rest


def expand(word: str) -> str:
    return os.path.expanduser(word)


def area_commands(event: dict) -> list[tuple[str, str]]:
    """Every distinct (kind, command) the config sets for an area, kinds in KINDS; [] when no config can be read."""
    start = event.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR")
    lib = Path(os.path.abspath(__file__)).parents[3] / "toolbox" / "lib" / "config.py"
    if not start or not lib.is_file():
        return []
    try:
        spec = importlib.util.spec_from_file_location("_gate_toolbox_config", lib)
        config = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = config  # its dataclasses look their module up here
        spec.loader.exec_module(config)
        loaded = config.load(Path(start))
        found = []
        for area in [*loaded.areas(), config.ANY_AREA]:
            for kind in KINDS:
                command = loaded.command(area, kind)
                if command and (kind, command) not in found:
                    found.append((kind, command))
        return found
    except Exception:
        return []


def matches_area_command(simple: list[str], template: str) -> bool:
    """Whether a command runs the start of one of the config's command templates. The template is split like any
    command line; each of its commands is compared on the words before its first `{placeholder}`, and setup commands
    (`cd dir`, `export X=1`) never match, so `cd` alone doesn't deny every `cd`."""
    call = [expand(w) for w in simple]
    for part in simple_commands(template):
        if os.path.basename(part[0]) in SETUP:
            continue
        literal = []
        for word in part:
            if "{" in word:
                break
            literal.append(expand(word))
        if literal and call[:len(literal)] == literal:
            return True
    return False
