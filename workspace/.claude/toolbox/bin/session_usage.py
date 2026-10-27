#!/usr/bin/env python3
"""Total the token usage of one or more Claude Code sessions from their transcripts, subagents included.

Reads ~/.claude/projects/*/<session-id>.jsonl and <session-id>/subagents/*.jsonl and counts each API request once.

    session_usage.py [--session <id>]... [--weekly-percent P --weekly-resets-at ISO]
    session_usage.py --task <work id | record path> [--dry-run] [--weekly-percent P --weekly-resets-at ISO]

--session defaults to $CLAUDE_CODE_SESSION_ID, i.e. the calling session, and may be repeated. --task reads the ids
from a task record's `sessions` instead (work-task's usage step) and writes the record's `usage` (one entry per
session, its totals per model) and `usage_total` (tokens, cost, and weekly_percent when the weekly flags are given),
under the tasks lock; --dry-run writes nothing. The caveats that apply go under the markdown table. Transcripts are
read from $CLAUDE_CONFIG_DIR/projects, else ~/.claude/projects. Prints JSON:
  sessions        per session, totals per model (what work-task writes to the record's `usage`)
  by_model        all sessions together, per model, with each model's API-equivalent cost
  total           every token and the cost summed across models
  weekly          the estimated share of a week's plan limit, when --weekly-percent and --weekly-resets-at are given
  markdown        the same totals as a table to show as is, with its caveats under it

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


def caveats(n_sessions, subagents, unpriced, weekly):
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
    return notes


def task_store():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
    import config
    import records
    return records.Store(config.load())


def write_record(store, record, per_session, total, weekly, dry_run):
    """usage: one entry per session with its per-model totals; usage_total: tokens, cost, weekly_percent."""
    import frontmatter
    usage = [{"session": sid, **{model: {k: t[k] for k in USAGE_ORDER} for model, t in totals.items()}}
             for sid, totals in per_session.items()]
    summary = {"tokens": total["tokens"], "cost": round(total["cost"], 2)}
    if weekly:
        summary["weekly_percent"] = weekly["estimate_percent"]
    with store.lock(dry_run):
        frontmatter.update(record.path, {"usage": usage, "usage_total": summary}, dry_run)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", action="append")
    parser.add_argument("--task")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--weekly-percent", type=float)
    parser.add_argument("--weekly-resets-at")
    args = parser.parse_args()
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

    per_session, by_model, subagents = {}, new_totals(), 0
    for sid in ids:
        hits = glob.glob(os.path.join(PROJECTS, "*", f"{sid}.jsonl"))
        if not hits:
            sys.exit(f"no transcript for session {sid}")
        per_session[sid] = session_totals(hits[0])
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

    if record:
        write_record(store, record, per_session, total, weekly, args.dry_run)
    table = markdown(by_model, total, weekly)
    notes = caveats(len(ids), subagents, unpriced, weekly)
    print(json.dumps({
        "sessions": per_session,
        "by_model": by_model,
        "total": total,
        "unpriced_models": unpriced,
        "weekly": weekly,
        "record": str(record.path) if record else None,
        "dry_run": args.dry_run,
        "markdown": table + "\n\n" + "\n".join(notes) if notes else table,
    }, indent=2))


if __name__ == "__main__":
    main()
