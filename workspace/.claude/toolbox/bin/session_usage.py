#!/usr/bin/env python3
"""Total the token usage of one or more Claude Code sessions from their transcripts, subagents included.

Reads ~/.claude/projects/*/<session-id>.jsonl and <session-id>/subagents/*.jsonl and counts each API request once.

    session_usage.py [--session <id>]... [--weekly-percent P --weekly-resets-at ISO]
    session_usage.py --task <work id | record path> [--dry-run] [--weekly-percent P --weekly-resets-at ISO]
    session_usage.py --discussion <folder> [--record <work id | record path>] [--session <id>]... [--dry-run]
    session_usage.py [--session <id>]... | --task <work id> --top-results [N]

--session defaults to $CLAUDE_CODE_SESSION_ID, i.e. the calling session, and may be repeated. --task reads the ids
from a task record's `sessions` instead (work-task's usage step) and writes the record's `usage` (one entry per
session: its totals per model and its `time`) and `usage_total` (tokens, cost, `time`, and weekly_percent when the
weekly flags are given), under the tasks lock; --dry-run writes nothing. The caveats that apply go under the markdown
tables. Transcripts are read from $CLAUDE_CONFIG_DIR/projects, else ~/.claude/projects. Prints JSON:
  sessions        per session, totals per model
  time            per session and in total, seconds of Claude's time, your time and their sum (see Time)
  by_model        all sessions together, per model, with each model's API-equivalent cost
  total           every token and the cost summed across models
  weekly          the estimated share of a week's plan limit, when --weekly-percent and --weekly-resets-at are given
  markdown        the same totals as a table to show as is, with its caveats under it
  top_results     with --top-results, the ranking below (rows of session, tool, tokens, requests, product)

--top-results [N] (default 20) ranks the main sessions' largest tool results by tokens times the requests each stayed
in context for, to show what to delegate. A result stays in context for every request after it until a compaction;
cache reads are most of the tokens, so a big result costs once per request. Rows name the tool, and for Bash the
command's first two words after any leading `cd <dir> &&`. Subagent transcripts are left out. Result tokens are
estimated from text length (4 chars a token, 1,600 an image), since transcripts don't record them. With --task it
writes nothing to the record.

--discussion <folder> (a path, relative to the vault, or the folder's name) reports a discussion's planning time instead:
its sessions are every session whose transcript wrote into the folder (Edit, Write), plus any named with --session or
by the record's earlier roll-up, each split by turn as a task's are (below). Prints JSON with `time` (per session and in
total), `missing_sessions` and `markdown`; no tokens or cost. --record writes the time to that record's `planning_time`
({claude, user, total, sessions: [{session, claude, user}]}), under the tasks lock: create-tickets passes the epic, or
the single story or defect. A story's `usage_total` is rewritten whole when it's done, so planning time has its own
field. Claude Code deletes transcripts after 30 days, so a session that is named but has none is listed in
`missing_sessions`; where the record's `planning_time` already held its time, that time is kept, else it's left out.
A session no one names whose transcript is gone can't be known, so the total may run low.

Time is worked out from the transcripts' event timestamps, with no stopwatch, so a session left idle for hours stops at
its last event. A turn starts with your prompt, or with a notification (a finished background task, a subagent's
hand-back), and ends at its last event, subagents' events included. Claude's time is each turn's span. Your time is the
gap from a turn's end to your next prompt, capped at 10 minutes; before a turn a notification started it is zero, so
waiting on CI adds nothing to either. With --task, a task's sessions are the record's `sessions` plus any session that
wrote to its worktrees (`<workspace.worktrees>/<work id>`) or record, or named it in a toolbox script call such as
tasks.py. A session that touched several tasks is split by turn: a turn belongs to the task it wrote to or named, else
to the previous turn's task; turns before the first one go to the first task touched; a turn that touched two is split
evenly. Only the record's own `sessions` count toward tokens and cost; the other sessions add time only.

Cost is the API list price of the tokens (rates below, from the claude-api skill, 2026-10). A subscription doesn't bill
per token, so it's a yardstick for comparing runs, not a charge.

The weekly estimate assumes the plan limit tracks API-equivalent cost. It divides the weekly percent used so far by the
API-equivalent cost of every local Claude Code session since the window opened (resets-at minus 7 days), which gives
percent per dollar, and multiplies that by the task's cost. Usage this machine can't see (claude.ai chat, other
machines) inflates the rate, so the estimate runs high; early in a window the whole-number percent makes it coarse.

Subagent output tokens are a lower bound. A subagent transcript often records a request's usage as it starts
streaming, before the output is counted. The parent's Agent result carries the final request's real usage for a
foreground subagent, and that replaces the final request's count; a background subagent's requests stay low. Input and
cache counts are known when a request starts, so they are exact.

Claude Haiku 5.5 bills a request whose prompt passes 100K tokens at a higher rate; the table prices every Haiku
request at the base rate, so such requests are under-counted.
"""

import argparse
import bisect
import glob
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

FIELDS = ("input", "cache_write_5m", "cache_write_1h", "cache_read", "output", "requests")
TOKEN_FIELDS = FIELDS[:-1]

# USD per million tokens: input, cache write 5m, cache write 1h, cache read, output.
PRICES = {
    "claude-fable-5-1": (10.00, 12.50, 20.00, 0.25, 50.00),
    "claude-opus-5-5": (4.00, 5.00, 8.00, 0.20, 20.00),
    "claude-sonnet-5-5": (2.00, 2.50, 4.00, 0.20, 10.00),
    "claude-haiku-5-5": (0.10, 0.125, 0.20, 0.01, 0.50),
    "claude-opus-5": (5.00, 6.25, 10.00, 0.50, 25.00),
    "claude-sonnet-5": (2.00, 2.50, 4.00, 0.20, 10.00),
    "claude-haiku-4-5": (1.00, 1.25, 2.00, 0.10, 5.00),
}
DATE_SUFFIX = re.compile(r"-\d{8}$")
USAGE_ORDER = ("requests", "input", "cache_write_5m", "cache_write_1h", "cache_read", "output")
# Transcripts don't record a tool result's size, so --top-results estimates it from the result's text.
CHARS_PER_TOKEN = 4
# What a Bash command starts with that says nothing about it: `cd <dir> &&` and `NAME=value` words.
LEADING_SETUP = re.compile(
    r"""^(?:(?:cd\s+(?:"[^"]*"|'[^']*'|\S+)\s*(?:&&|;)|[A-Za-z_]\w*=(?:"[^"]*"|'[^']*'|\S*)(?:\s*(?:&&|;)|\s+))\s*)+""")
IMAGE_TOKENS = 1600

# Time: what counts as an event, which user records start a turn by themselves, and the cap on one gap of your time.
EVENT_TYPES = ("assistant", "user", "system", "attachment")
AUTOMATED_ORIGINS = ("task-notification", "peer")
INTERRUPT_MARKER = "[Request interrupted"
USER_TIME_CAP = 600
# Toolbox scripts whose call names a task, and the calls among them that write to it.
SCRIPT_CALL = re.compile(r"\b(?:tasks|publish|worktrees|session_usage)\.py\b")
WRITING_CALL = re.compile(
    r"\btasks\.py\s+(?:set|set-list|append|log)\b|\bpublish\.py\b|\bworktrees\.py\s+(?:create|remove)\b")
# Claude Code writes the attachments that come with a prompt (a resume's tool list, skill mentions) a few milliseconds
# before the prompt record; one this close ahead of a turn's start belongs to that turn, not to the idle time before it.
ATTACHMENT_LEAD = 1.0
# A record this much older than the file's latest so far is stale (an idle-compaction summary), not an event.
STALE_SECONDS = 60
WRITE_PATH_KEYS = {"Edit": "file_path", "MultiEdit": "file_path", "Write": "file_path", "NotebookEdit": "notebook_path"}

PROJECTS = os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"), "projects")


def read_records(path):
    with open(path) as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                yield rec


def requests(path, since=None):
    """One (model, usage) per API request, in order. A request spans several lines, one per content block."""
    found = {}
    for rec in read_records(path):
        msg = rec.get("message")
        usage = msg.get("usage") if isinstance(msg, dict) else None
        if rec.get("type") != "assistant" or not usage:
            continue
        if since and rec.get("timestamp", "") < since:
            continue
        key = rec.get("requestId") or msg.get("id")
        if key not in found or usage.get("output_tokens", 0) > found[key][1].get("output_tokens", 0):
            found[key] = (msg.get("model", "unknown"), usage)
    return list(found.values())


def final_agent_usage(transcript):
    """agentId -> the usage of its final request, from the parent's Agent tool results."""
    finals = {}
    for rec in read_records(transcript):
        result = rec.get("toolUseResult")
        if isinstance(result, dict) and result.get("agentId") and result.get("usage"):
            finals[result["agentId"]] = result["usage"]
    return finals


def result_tokens(content):
    """Estimated tokens of one tool result: its text at CHARS_PER_TOKEN, plus a flat IMAGE_TOKENS per image."""
    if isinstance(content, str):
        return round(len(content) / CHARS_PER_TOKEN)
    chars = images = 0
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            chars += len(block.get("text", ""))
        elif block.get("type") == "image":
            images += 1
    return round(chars / CHARS_PER_TOKEN) + images * IMAGE_TOKENS


def tool_label(name, tool_input):
    """The tool, and for Bash its command's first two words, e.g. `Bash git status`."""
    if name == "Bash":
        command = LEADING_SETUP.sub("", str((tool_input or {}).get("command", "")).strip())
        return " ".join(["Bash", *command.split()[:2]])
    return name


def tool_results(transcript):
    """The main session's tool results as dicts: label, tokens, and the later requests each stayed in context for.

    A result stays in context for every API request after it until the conversation is compacted. Subagent
    transcripts are not read, so a subagent's own results never appear; the Agent call's result does."""
    uses, results, order = {}, [], []  # order: ("request", key) | ("result", index) | ("compact", None)
    seen = set()
    for rec in read_records(transcript):
        if rec.get("isSidechain"):
            continue
        msg = rec.get("message")
        kind = rec.get("type")
        if kind == "system" and rec.get("subtype") == "compact_boundary":
            order.append(("compact", None))
        elif kind == "assistant" and isinstance(msg, dict) and msg.get("usage"):
            key = rec.get("requestId") or msg.get("id")
            if key not in seen:
                seen.add(key)
                order.append(("request", key))
            for block in msg.get("content") if isinstance(msg.get("content"), list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    uses[block.get("id")] = (block.get("name", "unknown"), block.get("input"))
        elif kind == "user" and isinstance(msg, dict) and isinstance(msg.get("content"), list):
            for block in msg["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    name, tool_input = uses.get(block.get("tool_use_id"), ("unknown", None))
                    results.append({"tool": tool_label(name, tool_input),
                                    "tokens": result_tokens(block.get("content"))})
                    order.append(("result", len(results) - 1))
    # Walk backwards, counting requests since the next compaction; a result's count is the tally at its position.
    later = 0
    for kind, ref in reversed(order):
        if kind == "request":
            later += 1
        elif kind == "compact":
            later = 0
        else:
            results[ref]["requests"] = later
    return results


def top_results(per_session_results, limit):
    """Rows ordered by tokens x requests, largest first; ties by tokens then the order found."""
    rows = [{"session": sid, **r, "product": r["tokens"] * r["requests"]}
            for sid, results in per_session_results.items() for r in results]
    rows.sort(key=lambda r: (-r["product"], -r["tokens"]))
    return rows[:limit]


def top_results_markdown(rows):
    out = ["| # | Tool | Result tokens | Later requests | Tokens x requests |", "|--:|---|---:|---:|---:|"]
    for i, r in enumerate(rows, 1):
        out.append(f"| {i} | {r['tool']} | {fmt(r['tokens'])} | {fmt(r['requests'])} | {fmt(r['product'])} |")
    return "\n".join(out)


def timestamp(rec):
    """A record's timestamp in epoch seconds, or None when it has none."""
    try:
        return datetime.fromisoformat(rec["timestamp"].replace("Z", "+00:00")).timestamp()
    except (KeyError, ValueError, AttributeError):
        return None


def turn_start(rec):
    """'prompt' for your prompt, 'notification' for a record that starts a turn on its own (a finished background task,
    a subagent's hand-back), None for anything else, tool results and skill text included."""
    if rec.get("type") != "user" or rec.get("isSidechain") or rec.get("isCompactSummary"):
        return None
    msg = rec.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    blocks = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
    if any(b.get("type") == "tool_result" for b in blocks):
        return None
    origin = rec.get("origin")
    kind = origin.get("kind") if isinstance(origin, dict) else origin
    text = content if isinstance(content, str) else next(
        (b.get("text", "") for b in blocks if b.get("type") == "text"), "")
    if kind in AUTOMATED_ORIGINS or text.lstrip().startswith("<task-notification>"):
        return "notification"
    if rec.get("isMeta") or text.startswith(INTERRUPT_MARKER):
        return None
    return "prompt" if kind in (None, "human") else "notification"


def session_events(transcript):
    """Every event of a session, subagents' included, as (epoch seconds, record) in time order."""
    events = []
    for path in [transcript, *subagent_transcripts(transcript)]:
        latest = 0.0
        for rec in read_records(path):
            when = timestamp(rec)
            if when is None or rec.get("type") not in EVENT_TYPES:
                continue
            # A compaction summary written while you were idle keeps an older timestamp than the prompt that
            # triggered it; counting it would stretch the previous turn across the idle time.
            if when < latest - STALE_SECONDS or rec.get("isCompactSummary"):
                continue
            latest = max(latest, when)
            events.append((when, rec))
    events.sort(key=lambda event: event[0])
    return events


def build_turns(events, touches=None):
    """The session's turns: start, end (its last event), kind, and the items `touches(record)` names for its events.

    Events before the first turn start belong to no turn."""
    marks = [(when, kind) for when, rec in events if (kind := turn_start(rec))]
    turns = [{"start": when, "end": when, "kind": kind, "items": set()} for when, kind in marks]
    begins = [when for when, _ in marks]
    for when, rec in events:
        lead = ATTACHMENT_LEAD if rec.get("type") == "attachment" else 0.0
        i = bisect.bisect_right(begins, when + lead) - 1
        if i >= 0:
            turns[i]["end"] = max(turns[i]["end"], when)
            if touches:
                turns[i]["items"] |= touches(rec)
    return turns


def turn_shares(turns):
    """Per turn, {item: share of it}, or None for a session that touched no item. A turn belongs to the item it
    touched, else the previous turn's; leading turns to the first item touched; a turn touching two splits evenly."""
    current = next((t["items"] for t in turns if t["items"]), None)
    if current is None:
        return [None] * len(turns)
    shares = []
    for t in turns:
        current = t["items"] or current
        shares.append({item: 1 / len(current) for item in current})
    return shares


def session_time(turns, item=None):
    """(Claude's seconds, your seconds): Claude's is each turn's span; yours is the gap after it up to your next prompt,
    capped at USER_TIME_CAP, and nothing when a notification starts the next turn. With `item`, only its share of each
    turn (the whole session when it touched no item)."""
    shares = turn_shares(turns) if item else [None] * len(turns)
    claude = user = 0.0
    for i, t in enumerate(turns):
        share = 1.0 if shares[i] is None else shares[i].get(item, 0.0)
        claude += share * (t["end"] - t["start"])
        nxt = turns[i + 1] if i + 1 < len(turns) else None
        if nxt and nxt["kind"] == "prompt":
            user += share * min(USER_TIME_CAP, max(0.0, nxt["start"] - t["end"]))
    return round(claude), round(user)


class Items:
    """The workspace's task records, to tell which one a tool call wrote to or named."""

    def __init__(self, store):
        self.record_paths, self.prefixes, names = {}, [], []
        for r in store.records():
            self.record_paths[str(r.path)] = r.work_id
            names.append(r.work_id)
            for folder in [str(store.cfg.worktrees_dir / r.work_id), *map(str, r.fields.get("worktrees") or [])]:
                self.prefixes.append((folder.rstrip("/"), r.work_id))
        names.sort(key=len, reverse=True)
        self.names = re.compile(r"(?<![\w.-])(" + "|".join(map(re.escape, names)) + r")(?![\w-])") if names else None

    def touched(self, rec, writes_only=False):
        """Work ids a record's tool calls wrote to (Edit and Write on a record or inside its worktrees) or named in a
        call of a toolbox script (tasks.py, publish.py ...); with `writes_only`, only script calls that write."""
        msg = rec.get("message")
        blocks = msg.get("content") if rec.get("type") == "assistant" and isinstance(msg, dict) else None
        found = set()
        for block in blocks if isinstance(blocks, list) else []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            tool_input = block.get("input") or {}
            key = WRITE_PATH_KEYS.get(block.get("name"))
            if key and isinstance(tool_input.get(key), str):
                path = tool_input[key]
                if path in self.record_paths:
                    found.add(self.record_paths[path])
                found.update(work_id for folder, work_id in self.prefixes
                             if path == folder or path.startswith(folder + "/"))
            elif block.get("name") == "Bash" and self.names:
                command = str(tool_input.get("command", ""))
                if (WRITING_CALL if writes_only else SCRIPT_CALL).search(command):
                    found.update(self.names.findall(command))
        return found


def mentions(path, needle):
    """Whether a file has `needle` (bytes) on any line, read a line at a time."""
    with open(path, "rb") as f:
        return any(needle in line for line in f)


def related_sessions(record, items, known):
    """Sessions outside `known` that wrote to the task: {session id: turns}, oldest first. Scans every transcript
    changed since the record was created, skipping at once any that never mention the work id. Only writes count
    here (Edit and Write on the record or its worktrees, tasks.py set and the like); naming the task in a read-only
    call doesn't make a session its own."""
    try:
        floor = datetime.fromisoformat(str(record.fields.get("created"))).timestamp() - 86400
    except ValueError:
        floor = 0
    needle = record.work_id.encode()
    found = {}
    for path in glob.glob(os.path.join(PROJECTS, "*", "*.jsonl")):
        sid = os.path.basename(path)[: -len(".jsonl")]
        if sid in known or os.path.getmtime(path) < floor:
            continue
        if not any(mentions(p, needle) for p in [path, *subagent_transcripts(path)]):
            continue
        events = session_events(path)
        wrote = build_turns(events, lambda rec: items.touched(rec, writes_only=True))
        if any(record.work_id in t["items"] for t in wrote):
            found[sid] = build_turns(events, items.touched)
    return dict(sorted(found.items(), key=lambda kv: kv[1][0]["start"]))


def write_paths(rec):
    """The file paths a record's Write, Edit and similar tool calls wrote to."""
    msg = rec.get("message")
    blocks = msg.get("content") if rec.get("type") == "assistant" and isinstance(msg, dict) else None
    for block in blocks if isinstance(blocks, list) else []:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            key = WRITE_PATH_KEYS.get(block.get("name"))
            path = (block.get("input") or {}).get(key) if key else None
            if isinstance(path, str):
                yield path


def discussion_turns(folder, items, floor, named):
    """{session id: turns} for every session that wrote into the discussion folder, oldest first, then any `named`
    session that has a transcript. A turn's items are the work ids it wrote to or named, plus the folder when it wrote
    into it, so a session that also worked a task is split by turn like a task's sessions. Scans every transcript
    changed since `floor`, skipping at once any that never mention the folder's name."""
    marker, needle = str(folder), folder.name.encode()

    def inside(path):  # the folder is resolved, so a path written through a symlink is too
        return path == marker or path.startswith(marker + "/")

    def touches(rec):
        found = items.touched(rec)
        if any(inside(os.path.realpath(path)) for path in write_paths(rec)):
            found.add(marker)
        return found

    found = {}
    for path in glob.glob(os.path.join(PROJECTS, "*", "*.jsonl")):
        sid = os.path.basename(path)[: -len(".jsonl")]
        if sid not in named and (os.path.getmtime(path) < floor
                                 or not any(mentions(p, needle) for p in [path, *subagent_transcripts(path)])):
            continue
        turns = build_turns(session_events(path), touches)
        if sid in named or any(marker in t["items"] for t in turns):
            found[sid] = turns
    return dict(sorted(found.items(), key=lambda kv: kv[1][0]["start"] if kv[1] else 0))


def duration(seconds):
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m {secs:02d}s" if minutes else f"{secs}s"


def time_markdown(per_session, total):
    rows = ["| Session | Claude | You | Total |", "|---|---:|---:|---:|"]
    for sid, t in per_session.items():
        rows.append(f"| {sid[:8]} | {duration(t['claude'])} | {duration(t['user'])} | {duration(t['total'])} |")
    rows.append(f"| **Total** | **{duration(total['claude'])}** | **{duration(total['user'])}** "
                f"| **{duration(total['total'])}** |")
    return "\n".join(rows)


def with_total(claude, user):
    return {"claude": claude, "user": user, "total": claude + user}


def new_totals():
    return defaultdict(lambda: dict.fromkeys(FIELDS, 0))


def add(totals, model, usage):
    created = usage.get("cache_creation") or {}
    write_1h = created.get("ephemeral_1h_input_tokens", 0)
    write_5m = created.get("ephemeral_5m_input_tokens", usage.get("cache_creation_input_tokens", 0) - write_1h)
    t = totals[model]
    t["input"] += usage.get("input_tokens", 0)
    t["cache_write_5m"] += write_5m
    t["cache_write_1h"] += write_1h
    t["cache_read"] += usage.get("cache_read_input_tokens", 0)
    t["output"] += usage.get("output_tokens", 0)
    t["requests"] += 1


def merge(into, totals):
    for model, t in totals.items():
        for k in FIELDS:
            into[model][k] += t[k]


def cost(model, t):
    rates = PRICES.get(DATE_SUFFIX.sub("", model))
    if rates is None:
        return None
    return sum(t[k] * rate for k, rate in zip(TOKEN_FIELDS, rates)) / 1_000_000


def session_totals(transcript, since=None):
    totals = new_totals()
    for model, usage in requests(transcript, since):
        add(totals, model, usage)
    finals = final_agent_usage(transcript)
    for path in subagent_transcripts(transcript):
        reqs = requests(path, since)
        final = finals.get(os.path.basename(path)[len("agent-"): -len(".jsonl")])
        if reqs and final and reqs[-1][1].get("cache_read_input_tokens") == final.get("cache_read_input_tokens"):
            reqs[-1] = (reqs[-1][0], final)
        for model, usage in reqs:
            add(totals, model, usage)
    return totals


def subagent_transcripts(transcript):
    return sorted(glob.glob(os.path.join(transcript[: -len(".jsonl")], "subagents", "agent-*.jsonl")))


def total_cost(totals):
    costs = [cost(model, t) for model, t in totals.items()]
    return sum(c for c in costs if c is not None)


def local_cost_since(since):
    """API-equivalent cost of every local session's requests since `since` (ISO, UTC)."""
    cutoff = datetime.fromisoformat(since.replace("Z", "+00:00")).timestamp()
    totals = new_totals()
    for transcript in glob.glob(os.path.join(PROJECTS, "*", "*.jsonl")):
        if os.path.getmtime(transcript) >= cutoff:
            merge(totals, session_totals(transcript, since))
    return total_cost(totals)


def fmt(n):
    return f"{n:,}"


def markdown(by_model, total, weekly):
    rows = ["| Model | Requests | Input | Cache write 5m | Cache write 1h | Cache read | Output | Tokens | Cost |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for model, t in by_model.items():
        c = t["cost"]
        rows.append(f"| {model} | {fmt(t['requests'])} | " + " | ".join(fmt(t[k]) for k in TOKEN_FIELDS)
                    + f" | {fmt(t['tokens'])} | {'?' if c is None else f'${c:,.2f}'} |")
    rows.append(f"| **Total** | {fmt(total['requests'])} | " + " | ".join(fmt(total[k]) for k in TOKEN_FIELDS)
                + f" | **{fmt(total['tokens'])}** | **${total['cost']:,.2f}** |")
    if weekly:
        rows.append("")
        rows.append(f"Weekly limit: ≈{weekly['estimate_percent']:.1f}% of a week "
                    f"({weekly['percent_used']}% used so far this week by ${weekly['local_cost']:,.2f} of local sessions)")
    return "\n".join(rows)


def caveats(n_sessions, subagents, unpriced, weekly, timed=False, extra_sessions=0):
    notes = []
    if n_sessions > 1:
        notes.append(f"Across {n_sessions} sessions.")
    if subagents:
        notes.append("Subagent output tokens are a lower bound: a subagent's transcript often records a request's "
                     "usage before its output is counted.")
    if unpriced:
        notes.append(f"Not priced, so left out of the cost: {', '.join(unpriced)}.")
    if weekly:
        notes.append("The weekly figure is an estimate that runs high: it can't see usage outside this machine's "
                     "Claude Code sessions.")
    if timed:
        notes.append(f"Time comes from transcript timestamps: your time is each gap before a prompt, up to "
                     f"{USER_TIME_CAP // 60} minutes, and a wait for a notification counts for neither of you.")
    if extra_sessions:
        notes.append(f"Time also counts {extra_sessions} session{'s' if extra_sessions > 1 else ''} that wrote to the "
                     "task but aren't in its `sessions`; they add no tokens or cost.")
    return notes


def task_store():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
    import config
    import records
    return records.Store(config.load())


def write_record(store, record, per_session, total, weekly, times, dry_run):
    """usage: one entry per session with its per-model totals and time ({claude, user} seconds); usage_total: tokens,
    cost, weekly_percent and time ({claude, user, total}). A session counted for time only has just its time."""
    import frontmatter
    session_times, total_time = times
    usage = []
    for sid in dict.fromkeys([*per_session, *session_times]):
        entry = {"session": sid}
        entry.update({model: {k: t[k] for k in USAGE_ORDER} for model, t in per_session.get(sid, {}).items()})
        entry["time"] = {k: session_times[sid][k] for k in ("claude", "user")}
        usage.append(entry)
    summary = {"tokens": total["tokens"], "cost": round(total["cost"], 2), "time": total_time}
    if weekly:
        summary["weekly_percent"] = weekly["estimate_percent"]
    with store.lock(dry_run):
        frontmatter.update(record.path, {"usage": usage, "usage_total": summary}, dry_run)


def session_times(paths, record, store):
    """({session id: {claude, user, total}} in seconds, the same summed). Without a record each session counts whole;
    with one, only the task's share of each session, plus any session outside `paths` that wrote to it."""
    items = Items(store) if record else None
    item = record.work_id if record else None
    turns = {sid: build_turns(session_events(path), items.touched if items else None) for sid, path in paths.items()}
    if record:
        turns.update(related_sessions(record, items, set(paths)))
    per_session = {sid: with_total(*session_time(t, item)) for sid, t in turns.items()}
    total = with_total(sum(t["claude"] for t in per_session.values()), sum(t["user"] for t in per_session.values()))
    return per_session, total


def positive_int(text):
    n = int(text)
    if n < 1:
        raise argparse.ArgumentTypeError(f"{text} is not a positive number")
    return n


def planning_time(record):
    """{session id: {claude, user}} a record's `planning_time` already holds."""
    held = (record.fields.get("planning_time") or {}) if record else {}
    sessions = held.get("sessions") if isinstance(held, dict) else None
    return {e["session"]: {"claude": e.get("claude", 0), "user": e.get("user", 0)}
            for e in sessions or [] if isinstance(e, dict) and e.get("session")}


def write_planning_time(store, record, per_session, total, dry_run):
    import frontmatter
    value = {**total, "sessions": [{"session": sid, "claude": t["claude"], "user": t["user"]}
                                   for sid, t in per_session.items()]}
    with store.lock(dry_run):
        frontmatter.update(record.path, {"planning_time": value}, dry_run)


def discussion_report(args):
    """A discussion's planning time: every session that wrote into its folder, plus the named ones."""
    store = task_store()
    import frontmatter
    try:
        folder = store.resolve_discussion(args.discussion)
        record = store.find(args.record) if args.record else None
        plan = folder / "plan.md"
        created = frontmatter.fields(plan).get("created") if plan.is_file() else None
        try:
            floor = datetime.fromisoformat(str(created)).timestamp() - 86400
        except ValueError:
            floor = 0
        held = planning_time(record)
        named = dict.fromkeys([*held, *(args.session or [])])
        turns = discussion_turns(folder, Items(store), floor, set(named))
    except Exception as e:  # records.TaskError, config.ConfigError, frontmatter.FrontmatterError
        sys.exit(str(e))
    per_session = {sid: with_total(*session_time(t, str(folder))) for sid, t in turns.items()}
    missing = [sid for sid in named if sid not in turns]
    for sid in missing:  # the record's earlier roll-up still has what their transcripts no longer can
        if sid in held:
            per_session[sid] = with_total(held[sid]["claude"], held[sid]["user"])
    total = with_total(sum(t["claude"] for t in per_session.values()), sum(t["user"] for t in per_session.values()))
    written = bool(record and per_session)
    if written:
        write_planning_time(store, record, per_session, total, args.dry_run)
    notes = []
    if not per_session:
        notes.append(f"No session wrote into {folder}, so there is no time to report.")
    else:
        notes += caveats(len(per_session), 0, [], None, True)
    if missing:
        kept = [sid for sid in missing if sid in held]
        notes.append(f"{len(missing)} named session{'s' if len(missing) > 1 else ''} no longer "
                     f"{'has' if len(missing) == 1 else 'have'} a transcript (Claude Code deletes them after 30 days): "
                     f"{', '.join(missing)}. Their time is kept from the record's earlier roll-up where it has it "
                     f"({len(kept)} of {len(missing)}); the total runs low by the rest.")
    table = time_markdown(per_session, total) if per_session else ""
    print(json.dumps({
        "discussion": str(folder),
        "time": {"sessions": per_session, "total": total},
        "missing_sessions": missing,
        "record": str(record.path) if written else None,
        "dry_run": args.dry_run,
        "markdown": "\n\n".join(part for part in (table, "\n".join(notes)) if part),
    }, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", action="append")
    parser.add_argument("--task")
    parser.add_argument("--discussion")
    parser.add_argument("--record")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--top-results", nargs="?", type=positive_int, const=20, metavar="N")
    parser.add_argument("--weekly-percent", type=float)
    parser.add_argument("--weekly-resets-at")
    args = parser.parse_args()
    if args.record and not args.discussion:
        parser.error("--record goes with --discussion")
    if args.discussion:
        if args.task or args.top_results is not None or args.weekly_percent is not None or args.weekly_resets_at:
            parser.error("--discussion takes only --record, --session and --dry-run")
        return discussion_report(args)
    store = record = None
    if args.task:
        store = task_store()
        try:
            record = store.find(args.task)
        except Exception as e:  # records.TaskError, config.ConfigError
            sys.exit(str(e))
        ids = record.fields.get("sessions") or []
        if not ids:
            sys.exit(f"no sessions in {record.path}")
    else:
        ids = args.session or [os.environ.get("CLAUDE_CODE_SESSION_ID")]
    if not all(ids):
        sys.exit("no --session and no CLAUDE_CODE_SESSION_ID")

    per_session, by_model, subagents, results, paths = {}, new_totals(), 0, {}, {}
    for sid in ids:
        hits = glob.glob(os.path.join(PROJECTS, "*", f"{sid}.jsonl"))
        if not hits:
            sys.exit(f"no transcript for session {sid}")
        paths[sid] = hits[0]
        per_session[sid] = session_totals(hits[0])
        if args.top_results is not None:
            results[sid] = tool_results(hits[0])
        subagents += len(subagent_transcripts(hits[0]))
        merge(by_model, per_session[sid])

    for model, t in by_model.items():
        t["tokens"] = sum(t[k] for k in TOKEN_FIELDS)
        t["cost"] = cost(model, t)
    total = {k: sum(t[k] for t in by_model.values()) for k in FIELDS + ("tokens",)}
    total["cost"] = sum(t["cost"] for t in by_model.values() if t["cost"] is not None)
    unpriced = [m for m, t in by_model.items() if t["cost"] is None]

    weekly = None
    if args.weekly_percent is not None and args.weekly_resets_at:
        resets = datetime.fromisoformat(args.weekly_resets_at.replace("Z", "+00:00"))
        since = (resets - timedelta(days=7)).isoformat().replace("+00:00", "Z")
        local = local_cost_since(since)
        if local > 0:
            weekly = {"percent_used": args.weekly_percent, "window_start": since, "local_cost": round(local, 2),
                      "estimate_percent": round(args.weekly_percent / local * total["cost"], 2)}

    times = None
    if args.top_results is None:
        times = session_times(paths, record, store)
    if record and args.top_results is None:
        write_record(store, record, per_session, total, weekly, times, args.dry_run)
    table = markdown(by_model, total, weekly)
    if times:
        table += "\n\n" + time_markdown(*times)
    notes = caveats(len(ids), subagents, unpriced, weekly, bool(times), len(set(times[0]) - set(ids)) if times else 0)
    ranking = None
    if args.top_results is not None:
        ranking = top_results(results, args.top_results)
        table += "\n\nLargest main-session tool results (tokens estimated from text length):\n\n"
        table += top_results_markdown(ranking)
    print(json.dumps({
        "sessions": per_session,
        "by_model": by_model,
        "total": total,
        "unpriced_models": unpriced,
        "weekly": weekly,
        "time": {"sessions": times[0], "total": times[1]} if times else None,
        "top_results": ranking,
        "record": str(record.path) if record and args.top_results is None else None,
        "dry_run": args.dry_run,
        "markdown": table + "\n\n" + "\n".join(notes) if notes else table,
    }, indent=2))


if __name__ == "__main__":
    main()
