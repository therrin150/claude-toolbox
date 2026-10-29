#!/usr/bin/env python3
"""Publish a task once Allen approved it: commit every worktree with its approved message, push, open the PRs, wait for
CI, merge, and clean up. Everything after "approve", so a session spends no tokens on git and GitHub mechanics.

  publish.py commit   <work id> [options]    commit each worktree with its approved message
  publish.py push     <work id> [options]    rebase onto origin/<default> when it has new code commits, then push
  publish.py open-pr  <work id> [options]    open a PR per repo from its template; print the announcement to post
  publish.py wait-ci  <work id> [options]    block until every PR's checks have a result
  publish.py merge    <work id> [options]    merge the PRs by `pr.merge` (or straight to the default branch)
  publish.py cleanup  <work id> [options]    remove the worktrees, delete the merged branches, close out the record
  publish.py run      <work id> [options]    the whole sequence, resumable from wherever it stopped

<work id> names the task: its ticket key, or a slug. The checkouts are the task's worktrees (see worktrees.py), or
--checkouts PATH,PATH (any repo name or path, for use outside a task). Options every step takes:
  --repos a,b          only these repos of the task
  --dry-run            run the step's read-only checks and print them; change nothing
  --no-record          never write the task record (a change with no record)
  --publish-dir D      default: <vault>/<tasks>/.publish/<work id>, where the approved texts live
  --message-file F | --message-dir D   the commit message(s): one for every repo, or a folder of <repo>.txt
                       (default: <publish dir>/<repo>.txt, or commit.txt in a single-repo workspace)
  --announce-file F    the approved announcement (default: <publish dir>/announce.txt)

A commit message is the subject, a blank line, then the body, committed byte for byte with `git commit -F`. The subject
becomes the PR title, from the oldest commit on the branch, and the body the PR description.

What each step does, and what makes it stop:
  commit    precheck every repo first (not on the default branch, branch name matches `git.branch`, no operation in
            progress, a message with a subject, a body and no trailer, no uncommitted files on a branch whose PR is
            merged); any problem halts the batch with nothing changed. Then per repo `git add -A` and `git commit -F`,
            checking the tree is clean, the subject is the message's, and no trailer crept in.
  push      per repo, `git rebase origin/<default>` when that has new code commits (commits touching only the vault,
            when the vault is inside the repo, don't count). A conflict stops the batch (exit 4, `conflicts`) with the
            rebase left in progress for the session to resolve, `git rebase --continue`, re-test, and run again. A
            branch already on the remote that the rebase rewrote is pushed with --force-with-lease against the sha
            seen; nothing else is ever forced. Without `pr.required`, nothing is pushed here: `merge` pushes the default
            branch. A repo whose PR is merged (and none open) is skipped, never rebased or pushed: rebasing onto its
            squash commit and pushing would recreate the branch GitHub deleted. Commits after the merge fail the
            precheck; they belong on a new branch.
  open-pr   (`pr.required` only) per repo, in order: skip a repo whose PR is merged; look the branch up with
            `--state all` and reuse an open PR, else create one. Title: the oldest non-merge commit's subject. Body:
            the template, with everything above `# Checklist` replaced by the commits' bodies and every `- [ ]` ticked
            (the ticket-number box stays unticked when neither the branch nor a subject carries a key); with no
            `# Checklist`, the commits' bodies then the template; with no template, the commits' bodies. Ready for
            review, not a draft. With `pr.announce`, prints `announce`: the approved text, the channel and the MCP
            server, for the session to post (a script can't call an MCP tool). Records `prs`, `status: review` and
            `stage` through tasks.py.
  wait-ci   (`pr.wait_for_ci` only) poll each PR's checks until all are green (exit 0), any failed (exit 3) or
            --timeout seconds pass (exit 4). Run it in the background; it exits on a result, so nothing polls in the
            conversation. A PR with no checks at all after --grace seconds counts as green.
  merge     (`pr.required`) per PR: open, mergeable, checks green (with `pr.wait_for_ci`), approved (with
            `pr.review_required`), local tree clean and pushed; any blocked PR halts the batch with nothing merged. A PR
            behind the default branch is blocked: run `push` to rebase it, then `wait-ci`. A squash that collapses
            several commits halts (exit 4, `needs_confirmation`) unless --confirm-squash. Then `gh pr merge --<pr.merge>
            --delete-branch` per PR, re-queried after: a merge that left the PR open is a failure. --admin only for a
            PR whose precheck shows `admin_merge`: approved, mergeable, up to date, no unresolved review thread, every
            reported check green, and BLOCKED only by required checks that never ran because every file the PR
            changes is in their `pull_request` workflow's path filter (`paths-ignore`, say a docs-only change).
            Without `pr.required`, pushes HEAD to the default branch (it must be a fast-forward) and deletes the branch,
            noting each repo's pushed commit in the publish folder's `merged.json` ({repo: sha}), where
            `watch_task.py pipeline` finds the commits whose default-branch runs release them.
  cleanup   per repo that merged: `worktrees.py remove`, then delete the local branch (branches.py's PR check), then
            `status: done`, an empty `stage` and empty `worktrees` through tasks.py; when the record's `cycle` is
            `shared-library`, `status: review`, `stage: awaiting-ci` and `owner: claude` instead (the task waits for
            the release, then runs its consumers cycle), with `next: release` in the output. A worktree with
            uncommitted files is refused, and the task stays open.
  run       preflight (every precheck above and the texts they need, before anything is written), then commit, push,
            open-pr, wait-ci, merge, cleanup. The first failure halts with `stopped_at`. Every step looks at what
            already happened (a clean tree, a pushed branch, an open PR, a merged PR), so running it again resumes.
            With `pr.required` and every PR merged (on GitHub, say), it runs cleanup only, with `skipped`.
            --stop-after <step> ends the run after that step with `stopped_after` (exit 0): work-task publishes with
            `--stop-after open-pr` and runs it again, without the flag, on the user's merge instruction.

Every command prints one JSON object. Exit codes: 0 done, 2 a precheck failed (nothing changed), 3 a step failed
part-way (or the task record couldn't be written after the work succeeded), 4 halted for the session or Allen
(conflicts, a squash to confirm, CI timed out, review still needed).

The task record is written through tasks.py (`<scripts>/tasks.py`, called as a subprocess) when the workspace has the
`tasks` feature: `append <id> prs <urls>`, `set <id> status=... stage=...`, `set-list <id> worktrees`; cleanup reads
`get <id> cycle` first.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import branches  # noqa: E402
import config  # noqa: E402
import gitgh  # noqa: E402
import worktrees  # noqa: E402
from gitgh import (HALTED, OK, PRECHECK_FAILED, STEP_FAILED, TICKET_KEY, TRAILER, Checkout,  # noqa: E402
                   Failed, branch, commits, emit, gh, gh_json, git, operation_in_progress, prs_for_branch, remote_sha,
                   status_lines)

TEMPLATE = Path(".github/pull_request_template.md")
UNTICKED = re.compile(r"^(\s*)- \[ \](.*)$", re.MULTILINE)
TICKET_BOX = re.compile(r"\bticket (number|key)\b", re.IGNORECASE)
BARE_KEY = re.compile(rf"^\s*{TICKET_KEY.pattern}\s*$")
SUSPICIOUS_DIRS = ("target/", "build/", ".idea/", "node_modules/")
SUSPICIOUS_NAME = re.compile(r"tmp|probe|scratch|copy|\.orig$|\.bak$|~$", re.IGNORECASE)
TEST_DIR = re.compile(r"(^|/)(tests?|__tests__|src/test)/")
GREEN = {"SUCCESS", "NEUTRAL", "SKIPPED"}
SYNC_STATES = {"BEHIND", "DIRTY"}
OK_STATES = {"CLEAN", "HAS_HOOKS", "UNSTABLE"}
PR_FIELDS = "number,url,title,state,reviewDecision,mergeable,mergeStateStatus,statusCheckRollup,mergeCommit"


class Context:
    """What every step needs: the config, the task's checkouts, where its approved texts live, and the options."""

    def __init__(self, cfg: config.Config, wid: str, args: argparse.Namespace):
        self.cfg, self.wid, self.args = cfg, wid, args
        self.dry_run = bool(getattr(args, "dry_run", False))
        self.record = cfg.has("tasks") and not getattr(args, "no_record", False)
        self.publish_dir = (Path(args.publish_dir).expanduser() if getattr(args, "publish_dir", None)
                            else cfg.tasks_dir / ".publish" / wid)
        self.checkouts = self._checkouts()

    def _checkouts(self) -> list[Checkout]:
        a = self.args
        if getattr(a, "checkouts", None):
            found = gitgh.resolve_all(self.cfg, [c.strip() for c in a.checkouts.split(",") if c.strip()])
        else:
            found = gitgh.task_worktrees(self.cfg, self.wid)
            if not found:
                raise Failed(f"no worktrees for {self.wid} under {self.cfg.worktrees_dir}")
        if getattr(a, "repos", None):
            wanted = {r.strip() for r in a.repos.split(",") if r.strip()}
            missing = wanted - {c.name for c in found}
            if missing:
                raise Failed(f"{self.wid} has no worktree for: {', '.join(sorted(missing))}")
            found = [c for c in found if c.name in wanted]
        return found

    def message_path(self, co: Checkout) -> Path:
        a = self.args
        if getattr(a, "message_file", None):
            return Path(a.message_file).expanduser()
        base = Path(a.message_dir).expanduser() if getattr(a, "message_dir", None) else self.publish_dir
        return base / (f"{co.name}.txt" if self.cfg.nested else "commit.txt")

    def announce_path(self) -> Path:
        a = self.args
        return Path(a.announce_file).expanduser() if getattr(a, "announce_file", None) else \
            self.publish_dir / "announce.txt"

    def base(self) -> str:
        return f"origin/{self.cfg.default_branch}"


# ---------------------------------------------------------------------------------------------------------------------
# The task record


def record(ctx: Context, *command: str) -> str | None:
    """Run a tasks.py command for this task. Returns the error text on failure, else None. Nothing when the workspace
    has no `tasks` feature, or with --no-record or --dry-run."""
    if not ctx.record or ctx.dry_run:
        return None
    script = ctx.cfg.scripts_dir / "tasks.py"
    cmd = [sys.executable, str(script), command[0], ctx.wid, *command[1:]]
    try:
        proc = subprocess.run(cmd, cwd=ctx.cfg.root, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              timeout=120, env={**gitgh.env(), config.ENV_WORKSPACE: str(ctx.cfg.root)})
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"{' '.join(command)}: {e}"
    if proc.returncode != 0:
        return f"{' '.join(command)}: {(proc.stderr or proc.stdout).strip()}"
    return None


def record_field(ctx: Context, name: str):
    """One field of the task record through tasks.py `get`, or None (no record, no tasks feature, or a failed read)."""
    if not ctx.record:
        return None
    cmd = [sys.executable, str(ctx.cfg.scripts_dir / "tasks.py"), "get", ctx.wid, name]
    try:
        proc = subprocess.run(cmd, cwd=ctx.cfg.root, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              timeout=120, env={**gitgh.env(), config.ENV_WORKSPACE: str(ctx.cfg.root)})
        return (json.loads(proc.stdout).get("fields") or {}).get(name) if proc.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError, AttributeError):
        return None


def note_merge(ctx: Context, repo: str, sha: str) -> None:
    """Add a repo's commit on the default branch to the publish folder's merged.json, for watch_task.py pipeline."""
    if not ctx.record or ctx.dry_run:
        return
    path = ctx.publish_dir / "merged.json"
    try:
        merged = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        merged = {}
    merged[repo] = sha
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")


def record_all(ctx: Context, out: dict, *commands: tuple[str, ...]) -> bool:
    """Run record commands in order, noting failures under `record_errors`. True when all succeeded."""
    errors = [e for e in (record(ctx, *c) for c in commands) if e]
    if errors:
        out.setdefault("record_errors", []).extend(errors)
    return not errors


def record_code(out: dict) -> int:
    """A step that finished its work but couldn't write the task record still fails, so the session says so."""
    return STEP_FAILED if out.get("record_errors") else OK


# ---------------------------------------------------------------------------------------------------------------------
# A PR merged on GitHub


def merged_pr(ctx: Context, co: Checkout, name: str) -> dict | None:
    """The branch's merged PR when it has no open one (`pr.required` only). GitHub already has that change, so commit,
    push and open-pr leave the repo alone: rebasing a squash-merged branch onto its squash commit and pushing it
    recreates the branch GitHub deleted."""
    if not ctx.cfg.pr["required"] or not name:
        return None
    prs = prs_for_branch(co.gh_repo, name)
    if any(p["state"] == "OPEN" for p in prs):
        return None
    merged = [p for p in prs if p["state"] == "MERGED"]
    return max(merged, key=lambda p: p["number"]) if merged else None


def after_merge(ctx: Context, co: Checkout, pr: dict) -> bool:
    """HEAD has commits that neither the merged PR nor the default branch has: work that belongs on a new branch."""
    head = git(co.path, "rev-parse", "HEAD")
    if head == pr["headRefOid"]:
        return False
    git(co.path, "fetch", "origin", "--prune", timeout=90)
    git(co.path, "fetch", "origin", f"refs/pull/{pr['number']}/head", check=False, timeout=90)
    return not (gitgh.git_ok(co.path, "merge-base", "--is-ancestor", head, pr["headRefOid"])
                or gitgh.git_ok(co.path, "merge-base", "--is-ancestor", head, ctx.base()))


# ---------------------------------------------------------------------------------------------------------------------
# commit


def check_message(path: Path) -> tuple[str | None, list[str]]:
    """The subject, and what's wrong with the message."""
    if not path.is_file():
        return None, [f"no commit message at {path}"]
    text = path.read_text()
    subject, _, body = text.strip().partition("\n")
    problems = []
    if not subject.strip():
        problems.append(f"{path.name}: no subject line")
    if not body.strip():
        problems.append(f"{path.name}: no body; it becomes the PR description, so ask Allen for one")
    if TRAILER.search(text):
        problems.append(f"{path.name}: carries a trailer or attribution line; remove it")
    return subject.strip(), problems


def group_files(lines: list[str]) -> dict:
    groups = {"modified": [], "added": [], "deleted": [], "renamed": [], "untracked": []}
    for line in lines:
        code, name = line[:2], line[3:]
        if code == "??":
            groups["untracked"].append(name)
        elif "R" in code:
            groups["renamed"].append(name)
        elif "D" in code:
            groups["deleted"].append(name)
        elif "A" in code:
            groups["added"].append(name)
        else:
            groups["modified"].append(name)
    return {k: v for k, v in groups.items() if v}


def suspicious(groups: dict) -> list[str]:
    found = []
    for kind in ("added", "untracked", "modified"):
        for name in groups.get(kind, []):
            new = kind != "modified"
            if any(f"/{d}" in f"/{name}" for d in SUSPICIOUS_DIRS) or SUSPICIOUS_NAME.search(Path(name).name) \
                    or (new and TEST_DIR.search(name)):
                found.append(name)
    return found


def branch_problems(ctx: Context, name: str) -> list[str]:
    if not name:
        return ["detached HEAD"]
    if name == ctx.cfg.default_branch:
        return [f"on {name}; a task never commits to the default branch"]
    if not any(p.match(name) for p in gitgh.branch_patterns(ctx.cfg)):
        return [f"branch {name} doesn't match git.branch ({ctx.cfg.get('git.branch')})"]
    return []


def commit_precheck_one(ctx: Context, co: Checkout) -> dict:
    msg = ctx.message_path(co)
    result = {"repo": co.name, "path": str(co.path), "message": str(msg), "problems": []}
    problems = result["problems"]
    name = branch(co.path)
    result["branch"] = name
    ops = operation_in_progress(co.path)
    if ops:
        problems.append(f"in progress: {', '.join(ops)}; resolve it first")
    else:
        problems += branch_problems(ctx, name)
    lines = status_lines(co.path)
    if lines:
        result["action"] = "commit"
        result["files"] = group_files(lines)
        result["suspicious"] = suspicious(result["files"])
        staged = [line[3:] for line in lines if line[0] not in " ?"]
        if staged:
            result["staged"] = staged
        pr = merged_pr(ctx, co, name)
        if pr:
            problems.append(f"uncommitted files on a branch whose PR #{pr['number']} is merged; they belong on a new "
                            "branch")
    else:
        pushed = remote_sha(co.path, name) if name else None
        since = f"{pushed or ctx.base()}..HEAD"
        ahead = int(git(co.path, "rev-list", "--count", since, check=False) or 0) if name else 0
        result["action"] = "committed" if ahead else "clean"
        if ahead and (TRAILER.search(git(co.path, "log", "--format=%B", since))
                      or git(co.path, "log", "--format=%(trailers)", since)):
            problems.append("a commit on the branch carries a trailer or attribution line; fix it by hand first")
    if result["action"] == "commit":
        subject, message_problems = check_message(msg)
        problems += message_problems
        key = TICKET_KEY.search(name or "")
        if subject and key and not subject.startswith(key.group()):
            result["warning"] = f"subject doesn't start with the branch's key {key.group()}"
    return result


def verify_commit(co: Checkout, subject: str) -> None:
    if status_lines(co.path):
        raise Failed(f"{co.name}: tree not clean after the commit")
    if git(co.path, "log", "-1", "--format=%s") != subject:
        raise Failed(f"{co.name}: HEAD's subject isn't the message's subject")
    if git(co.path, "log", "-1", "--format=%(trailers)") or TRAILER.search(git(co.path, "log", "-1", "--format=%B")):
        raise Failed(f"{co.name}: the commit carries a trailer or attribution line (a hook or template added it); "
                     "not pushing. Fix it by hand before it reaches a PR")


def step_commit(ctx: Context) -> tuple[dict, int]:
    checks = [commit_precheck_one(ctx, co) for co in ctx.checkouts]
    out = {"precheck": checks, "committed": []}
    if any(c["problems"] for c in checks):
        out["error"] = "precheck failed; nothing was staged or committed"
        return out, PRECHECK_FAILED
    if ctx.dry_run:
        return {**out, "dry_run": True}, OK
    by_name = {co.name: co for co in ctx.checkouts}
    for check in checks:
        if check["action"] != "commit":
            continue
        co, msg = by_name[check["repo"]], Path(check["message"])
        try:
            git(co.path, "add", "-A")
            git(co.path, "commit", "-F", str(msg))
            verify_commit(co, msg.read_text().strip().partition("\n")[0].strip())
        except Failed as e:
            # The index stays staged so Allen can inspect it; earlier commits stand.
            out.update(error=str(e), stopped_at=co.name)
            return out, STEP_FAILED
        out["committed"].append({"repo": co.name, "sha": git(co.path, "rev-parse", "--short", "HEAD"),
                                 "subject": git(co.path, "log", "-1", "--format=%s"),
                                 "files": sum(len(v) for v in check["files"].values())})
    return out, OK


# ---------------------------------------------------------------------------------------------------------------------
# push


def vault_prefix(ctx: Context, co: Checkout) -> str | None:
    """The vault's folder relative to the repo, when the vault lives inside it (a single-repo workspace's `vault/`)."""
    try:
        return str(ctx.cfg.vault_dir.resolve().relative_to(co.main.resolve()))
    except ValueError:
        return None


def code_commits_behind(ctx: Context, co: Checkout) -> list[str]:
    """Subjects of commits on origin/<default> that HEAD lacks and that touch more than the vault."""
    prefix = vault_prefix(ctx, co)
    found = []
    for sha in git(co.path, "rev-list", "--no-merges", "--reverse", f"HEAD..{ctx.base()}").split():
        files = git(co.path, "diff-tree", "--no-commit-id", "--name-only", "-r", sha).splitlines()
        if prefix is None or any(not (f == prefix or f.startswith(prefix + "/")) for f in files):
            found.append(git(co.path, "log", "-1", "--format=%s", sha))
    return found


def push_precheck_one(ctx: Context, co: Checkout) -> dict:
    result = {"repo": co.name, "path": str(co.path), "problems": []}
    problems = result["problems"]
    name = branch(co.path)
    result["branch"] = name
    ops = operation_in_progress(co.path)
    if ops:
        problems.append(f"in progress: {', '.join(ops)}; resolve it, `git rebase --continue` if it is a rebase, "
                        "and run again")
        return result
    problems += branch_problems(ctx, name)
    if status_lines(co.path):
        problems.append("working tree not clean; commit first")
    pr = merged_pr(ctx, co, name)
    if pr:
        result["merged_pr"] = pr["number"]
        if after_merge(ctx, co, pr):
            problems.append(f"commits after PR #{pr['number']} merged; they belong on a new branch")
    return result


def step_push(ctx: Context) -> tuple[dict, int]:
    checks = [push_precheck_one(ctx, co) for co in ctx.checkouts]
    out = {"precheck": checks, "rebased": [], "pushed": []}
    if any(c["problems"] for c in checks):
        out["error"] = "precheck failed; nothing was rebased or pushed"
        return out, PRECHECK_FAILED
    merged = {c["repo"]: c["merged_pr"] for c in checks if "merged_pr" in c}
    if merged:
        out["skipped"] = [{"repo": r, "merged_pr": n} for r, n in sorted(merged.items())]
    todo = [co for co in ctx.checkouts if co.name not in merged]
    for co in todo:
        git(co.path, "fetch", "origin", "--prune", timeout=90)
    behind = {co.name: code_commits_behind(ctx, co) for co in todo}
    if ctx.dry_run:
        return {**out, "dry_run": True, "would_rebase": {k: v for k, v in behind.items() if v}}, OK
    for co in todo:
        name = branch(co.path)
        seen = remote_sha(co.path, name)
        before = git(co.path, "rev-parse", "HEAD")
        if behind[co.name]:
            proc = gitgh.run(["git", "rebase", ctx.base()], co.path, gitgh.GIT_TIMEOUT, check=False)
            if proc.returncode != 0:
                files = git(co.path, "diff", "--name-only", "--diff-filter=U", check=False).splitlines()
                out.update(conflicts=[{"repo": co.name, "path": str(co.path), "files": files,
                                       "brought_in": behind[co.name]}],
                           stopped_at=co.name,
                           halted="the rebase is in progress; resolve the conflicts, `git rebase --continue`, "
                                  "re-test, and run again")
                return out, HALTED
            out["rebased"].append({"repo": co.name, "brought_in": behind[co.name]})
        if not ctx.cfg.pr["required"]:
            continue  # merge pushes the default branch
        head = git(co.path, "rev-parse", "HEAD")
        if seen == head:
            continue
        args = ["push", "-u", "origin", name]
        if seen and head != before:
            # The rebase rewrote a branch the remote already had: replace exactly what was seen, nothing newer.
            args = ["push", "-u", f"--force-with-lease={name}:{seen}", "origin", name]
        try:
            git(co.path, *args, timeout=90)
            if git(co.path, "rev-parse", "HEAD") != remote_sha(co.path, name):
                raise Failed(f"{co.name}: HEAD isn't origin/{name} after the push")
        except Failed as e:
            out.update(error=str(e), stopped_at=co.name)
            return out, STEP_FAILED
        out["pushed"].append({"repo": co.name, "branch": name})
    return out, OK


# ---------------------------------------------------------------------------------------------------------------------
# open-pr


def open_pr_for(co: Checkout, name: str) -> list[dict]:
    """The branch's open PR, looked up with --state all like every lookup, then filtered here."""
    return [p for p in prs_for_branch(co.gh_repo, name) if p["state"] == "OPEN"]


def description(own: list[dict]) -> str:
    paragraphs = []
    for c in own:
        lines = [line for line in c["body"].splitlines() if not TRAILER.search(line) and not BARE_KEY.match(line)]
        text = "\n".join(lines).strip()
        if text:
            paragraphs.append(re.sub(r"\n{3,}", "\n\n", text))
    return "\n\n".join(paragraphs)


def has_ticket(name: str, own: list[dict]) -> bool:
    """A key leads the branch's description or a commit subject; a token like SHA-256 elsewhere in a subject isn't."""
    return bool(re.search(r"/" + TICKET_KEY.pattern + r"(-|$)", name)) or \
        any(re.match(TICKET_KEY.pattern, c["subject"]) for c in own)


def pr_body(co: Checkout, desc: str, ticket: bool = True) -> str:
    template_path = co.path / TEMPLATE
    if not template_path.is_file():
        return desc
    template = template_path.read_text()
    head, sep, checklist = template.partition("# Checklist")
    if not sep:
        return f"{desc}\n\n{template}".strip() + "\n"
    # With no ticket, the ticket box stays unticked, so the PR doesn't claim a key the change doesn't have.
    checklist = UNTICKED.sub(lambda m: m.group(0) if not ticket and TICKET_BOX.search(m.group(0))
                             else f"{m.group(1)}- [x]{m.group(2)}", checklist)
    return f"{desc}\n\n{sep}{checklist}"


def read_announcement(path: Path) -> str:
    text = path.read_text().strip() if path.is_file() else ""
    if not text or "\n" in text:
        raise Failed(f"{path}: expected one announcement sentence on one line")
    return text


def announcement(cfg: config.Config, created: list[dict], sentence: str) -> dict:
    prs = sorted(created, key=lambda p: p["repo"])
    announce = cfg.announce
    base = {"mcp_server": announce["mcp_server"], "channel": announce["channel"]}
    if len(prs) == 1:
        return {**base, "text": f"{prs[0]['url']} - {sentence}"}
    return {**base, "text": f"{len(prs)} pull requests (in-thread) - {sentence}",
            "thread_text": "\n".join(f"- {p['repo']} - {p['url']}" for p in prs)}


def pr_precheck_one(ctx: Context, co: Checkout, resume: bool, pushed: bool) -> dict:
    result = {"repo": co.name, "path": str(co.path), "problems": []}
    problems = result["problems"]
    name = branch(co.path)
    result["branch"] = name
    problems += branch_problems(ctx, name)
    if pushed:
        pr = merged_pr(ctx, co, name)
        if pr:
            return {**result, "merged_pr": pr["number"], "url": pr["url"]}
    template = co.path / TEMPLATE
    if template.is_file() and "# Checklist" not in template.read_text():
        result["note"] = f"{TEMPLATE} has no '# Checklist' heading: the description goes above the template"
    if pushed:
        if status_lines(co.path):
            problems.append("working tree not clean")
        git(co.path, "fetch", "origin", "--prune", timeout=90)
        if remote_sha(co.path, name) != git(co.path, "rev-parse", "HEAD"):
            problems.append(f"HEAD isn't pushed to origin/{name}; push first")
        own = commits(co.path, ctx.base())
        if not own:
            problems.append(f"no commits ahead of {ctx.base()}")
        else:
            result["title"] = own[0]["subject"]
            result["commits"] = len(own)
    if name:
        existing = open_pr_for(co, name)
        if existing:
            result["open_pr"] = existing[0]["url"]
            if not resume:
                problems.append(f"a PR is already open: {existing[0]['url']}")
    return result


def pr_preflight_problems(ctx: Context) -> list[str]:
    """What open-pr needs besides the pushed branch: the approved announcement when `pr.announce` is configured."""
    if ctx.cfg.announce and not getattr(ctx.args, "no_announce", False):
        try:
            read_announcement(ctx.announce_path())
        except (Failed, OSError) as e:
            return [f"announcement: {e} (or pass --no-announce)"]
    return []


def create_pr(ctx: Context, co: Checkout, reviewer: str | None) -> dict:
    name = branch(co.path)
    result = {"repo": co.name, "branch": name}
    existing = open_pr_for(co, name)
    if existing:
        return {**result, "status": "exists", "url": existing[0]["url"]}
    own = commits(co.path, ctx.base())
    title = own[0]["subject"]
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
        f.write(pr_body(co, description(own), has_ticket(name, own)))
        body_file = f.name
    args = ["pr", "create", "--repo", co.gh_repo, "--base", ctx.cfg.default_branch, "--head", name, "--title", title,
            "--body-file", body_file]
    if reviewer:
        args += ["--reviewer", reviewer]
    try:
        url = gh(*args).splitlines()[-1]
        result.update(status="created", url=url, title=title)
    except Failed as e:
        # A timeout or a failed review request may still have opened the PR: look before calling it failed.
        found = open_pr_for(co, name)
        if not found:
            raise Failed(f"{co.name}: {e}") from None
        result.update(status="created", url=found[0]["url"], title=title, reviewer_error=str(e))
    finally:
        Path(body_file).unlink(missing_ok=True)
    return result


def step_open_pr(ctx: Context) -> tuple[dict, int]:
    if not ctx.cfg.pr["required"]:
        return {"skipped": "pr.required is false: nothing to open; merge pushes the default branch"}, OK
    checks = [pr_precheck_one(ctx, co, resume=True, pushed=True) for co in ctx.checkouts]
    out = {"precheck": checks, "prs": []}
    problems = [f"{c['repo']}: {p}" for c in checks for p in c["problems"]] + pr_preflight_problems(ctx)
    if problems:
        return {**out, "problems": problems, "error": "precheck failed; nothing was opened"}, PRECHECK_FAILED
    merged = {c["repo"]: c for c in checks if "merged_pr" in c}
    if len(merged) == len(checks):
        return {**out, "skipped": "every PR is merged; nothing to open"}, OK
    if ctx.dry_run:
        return {**out, "dry_run": True}, OK
    for co in ctx.checkouts:
        if co.name in merged:
            out["prs"].append({"repo": co.name, "status": "merged", "url": merged[co.name]["url"]})
            continue
        try:
            out["prs"].append(create_pr(ctx, co, getattr(ctx.args, "reviewer", None)))
        except Failed as e:
            out.update(stopped_at=co.name, error=str(e),
                       not_opened=[c.name for c in ctx.checkouts if c.name not in {p["repo"] for p in out["prs"]}])
            return out, STEP_FAILED
    created = [p for p in out["prs"] if p["status"] == "created"]
    if ctx.cfg.announce and not getattr(ctx.args, "no_announce", False):
        if created or getattr(ctx.args, "announce_existing", False):
            out["announce"] = announcement(ctx.cfg, [p for p in out["prs"] if p["status"] != "merged"],
                                           read_announcement(ctx.announce_path()))
        else:
            out["announce"] = None
            out["note"] = "every PR was already open, so nothing new to announce (--announce-existing forces it)"
    stage = "awaiting-ci" if ctx.cfg.pr["wait_for_ci"] else "awaiting-merge"
    record_all(ctx, out, ("append", "prs", *[p["url"] for p in out["prs"] if p["status"] != "merged"]),
               ("set", "status=review", f"stage={stage}"))
    return out, record_code(out)


# ---------------------------------------------------------------------------------------------------------------------
# wait-ci


def check_state(item: dict) -> str:
    """pass, pending or fail, for one entry of a PR's statusCheckRollup (a check run or a commit status)."""
    if "state" in item and "status" not in item:
        state = item["state"]
        return "pass" if state in GREEN else "pending" if state in ("PENDING", "EXPECTED") else "fail"
    if item.get("status") != "COMPLETED":
        return "pending"
    return "pass" if item.get("conclusion") in GREEN else "fail"


def check_name(item: dict) -> str:
    return item.get("name") or item.get("context") or "check"


def ci_state(rollup: list[dict]) -> tuple[str, list[str]]:
    """none, pending, fail or pass for a PR, and the names of the failing checks."""
    if not rollup:
        return "none", []
    states = [(check_state(i), check_name(i)) for i in rollup]
    failing = [f"{n}: {i.get('conclusion') or i.get('state')}" for (s, n), i in zip(states, rollup) if s == "fail"]
    if failing:
        return "fail", failing
    return ("pending" if any(s == "pending" for s, _ in states) else "pass"), []


def step_wait_ci(ctx: Context) -> tuple[dict, int]:
    if not (ctx.cfg.pr["required"] and ctx.cfg.pr["wait_for_ci"]):
        return {"skipped": "pr.required or pr.wait_for_ci is false: nothing to wait for"}, OK
    a = ctx.args
    prs = {}
    for co in ctx.checkouts:
        found = open_pr_for(co, branch(co.path))
        if not found:
            merged = [p for p in prs_for_branch(co.gh_repo, branch(co.path)) if p["state"] == "MERGED"]
            if merged:
                continue
            return {"error": f"{co.name}: no open PR for {branch(co.path)}; run open-pr first"}, PRECHECK_FAILED
        prs[co.name] = (co, found[0]["number"])
    out = {"checks": {}, "waited": 0}
    if ctx.dry_run:
        return {**out, "dry_run": True, "prs": {k: v[1] for k, v in prs.items()}}, OK
    interval = getattr(a, "interval", 30)
    timeout, grace = getattr(a, "timeout", 3600), getattr(a, "grace", 60)
    started = time.monotonic()
    while True:
        pending = []
        for name, (co, number) in prs.items():
            pr = gh_json("pr", "view", str(number), "--repo", co.gh_repo, "--json", "statusCheckRollup,state")
            state, failing = ci_state(pr.get("statusCheckRollup"))
            waited = time.monotonic() - started
            if state == "none" and waited >= grace:
                state = "pass"
                out["checks"][name] = {"number": number, "state": "no-checks"}
                continue
            if state in ("none", "pending"):
                pending.append(name)
                continue
            out["checks"][name] = {"number": number, "state": "green" if state == "pass" else "failed",
                                   **({"failing": failing} if failing else {})}
            if state == "fail":
                out.update(error=f"{name}: checks failed: {'; '.join(failing)}", waited=round(waited))
                return out, STEP_FAILED
        out["waited"] = round(time.monotonic() - started)
        if not pending:
            return out, OK
        if time.monotonic() - started >= timeout:
            out.update(halted=f"checks still pending after {timeout}s: {', '.join(pending)}", pending=pending)
            return out, HALTED
        time.sleep(interval)


# ---------------------------------------------------------------------------------------------------------------------
# merge


def view(co: Checkout, ref: str, fields: str = PR_FIELDS) -> dict:
    """The PR, re-queried while GitHub is still computing mergeability (UNKNOWN)."""
    for attempt in range(4):
        pr = gh_json("pr", "view", ref, "--repo", co.gh_repo, "--json", fields)
        if "mergeable" not in fields or pr.get("state") != "OPEN" or \
                "UNKNOWN" not in (pr.get("mergeable"), pr.get("mergeStateStatus")):
            return pr
        time.sleep(3 * (attempt + 1))
    return pr


def filter_regex(pattern: str) -> re.Pattern | None:
    """A GitHub Actions path filter pattern as a regex: `**` crosses folders, `*` doesn't. None for `?`, `+` or `[...]`,
    whose GitHub meanings this doesn't model."""
    if re.search(r"[?+\[\]]", pattern):
        return None
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(out + r"\Z")


def filter_skips(trigger, files: list[str]) -> bool:
    """Whether a `pull_request` trigger's `paths-ignore` or `paths` filter leaves out every file, so the workflow never
    runs. False with no filter, or one this can't read."""
    if not isinstance(trigger, dict) or ("paths" in trigger) == ("paths-ignore" in trigger):
        return False
    patterns = trigger.get("paths-ignore") or trigger.get("paths")
    if not isinstance(patterns, list) or not patterns or not files:
        return False
    compiled = [(p.startswith("!"), filter_regex(p.removeprefix("!"))) for p in patterns]
    if any(regex is None for _, regex in compiled):
        return False

    def matched(path: str) -> bool:  # the last pattern that matches wins; a `!` one negates
        hit = False
        for negated, regex in compiled:
            if regex.match(path):
                hit = not negated
        return hit

    if "paths-ignore" in trigger:
        return all(matched(f) for f in files)
    return not any(matched(f) for f in files)


def path_skipped_jobs(co: Checkout, files: list[str]) -> set[str]:
    """Job ids and names of the PR's workflows (at HEAD) whose `pull_request` path filter leaves out every file."""
    jobs = set()
    for path in git(co.path, "ls-tree", "--name-only", "HEAD", ".github/workflows/").splitlines():
        if not path.endswith((".yml", ".yaml")):
            continue
        try:
            workflow = yaml.safe_load(git(co.path, "show", f"HEAD:{path}")) or {}
        except yaml.YAMLError:
            continue
        on = workflow.get("on", workflow.get(True))  # YAML 1.1 reads a bare `on:` key as True
        if isinstance(on, dict) and filter_skips(on.get("pull_request"), files):
            for job_id, job in (workflow.get("jobs") or {}).items():
                jobs.update({job_id, *([job["name"]] if isinstance(job, dict) and job.get("name") else [])})
    return jobs


def required_checks(co: Checkout, base: str) -> set[str] | None:
    """The status checks `base` requires, by branch protection and rulesets. None when either can't be read."""
    try:
        found = set((gh_json("api", f"repos/{co.gh_repo}/branches/{base}/protection/required_status_checks")
                     or {}).get("contexts") or [])
    except Failed as e:
        if "not protected" not in str(e).lower():
            return None
        found = set()
    try:
        rules = gh_json("api", f"repos/{co.gh_repo}/rules/branches/{base}") or []
    except Failed:
        return None
    for rule in rules:
        if rule.get("type") == "required_status_checks":
            found.update(c["context"] for c in (rule.get("parameters") or {}).get("required_status_checks") or [])
    return found


def unresolved_threads(co: Checkout, number: int) -> int | None:
    owner, name = co.gh_repo.split("/")
    query = ("query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){pullRequest(number:$n)"
             "{reviewThreads(first:100){nodes{isResolved}}}}}")
    try:
        data = gh_json("api", "graphql", "-f", f"query={query}", "-F", f"o={owner}", "-F", f"r={name}",
                       "-F", f"n={number}")
    except Failed:
        return None
    threads = data["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
    return sum(not t["isResolved"] for t in threads)


def path_filtered(ctx: Context, co: Checkout, pr: dict) -> dict | None:
    """When an approved, mergeable, up-to-date PR is BLOCKED only because required checks never ran, every file it
    changes being in their workflows' path filter (`paths-ignore`, say a docs-only change): the checks never reported
    and the files. `merge` merges such a PR with --admin; nothing else ever gets --admin."""
    if pr.get("reviewDecision") != "APPROVED" or pr.get("mergeable") != "MERGEABLE":
        return None
    if not gitgh.git_ok(co.path, "merge-base", "--is-ancestor", ctx.base(), "HEAD"):
        return None
    required = required_checks(co, ctx.cfg.default_branch)
    missing = sorted((required or set()) - {check_name(i) for i in pr.get("statusCheckRollup") or []})
    if not missing:
        return None
    files = git(co.path, "diff", "--name-only", "--no-renames", f"{ctx.base()}...HEAD").splitlines()
    jobs = path_skipped_jobs(co, files)
    if any(c not in jobs and c.split(" / ")[0] not in jobs for c in missing):
        return None
    if unresolved_threads(co, pr["number"]) != 0:
        return None
    return {"missing_checks": missing, "files": files}


def merge_precheck_one(ctx: Context, co: Checkout) -> dict:
    name = branch(co.path)
    result = {"repo": co.name, "path": str(co.path), "branch": name, "problems": []}
    problems = result["problems"]
    if not name or name == ctx.cfg.default_branch:
        problems.append("not on a task branch")
        return {**result, "status": "blocked"}
    if not ctx.cfg.pr["required"]:
        return direct_precheck(ctx, co, result)
    found = prs_for_branch(co.gh_repo, name)
    if not found:
        problems.append("no PR for the branch; run open-pr first")
        return {**result, "status": "blocked"}
    pr = view(co, str(sorted(found, key=lambda p: p["number"])[-1]["number"]))
    result.update(number=pr.get("number"), url=pr.get("url"), title=pr.get("title"))
    if pr.get("state") == "MERGED":
        return {**result, "status": "already-merged", "merge_commit": (pr.get("mergeCommit") or {}).get("oid")}
    if pr.get("state") != "OPEN":
        problems.append(f"PR is {pr.get('state')}")
        return {**result, "status": "blocked"}
    if ctx.cfg.pr["review_required"] and pr.get("reviewDecision") != "APPROVED":
        problems.append(f"review: {pr.get('reviewDecision') or 'NONE'}")
        result["waiting_for"] = "review"
    if pr.get("mergeStateStatus") in SYNC_STATES or pr.get("mergeable") == "CONFLICTING":
        problems.append(f"behind or conflicting with {ctx.cfg.default_branch}: run push to rebase it, then wait-ci")
    else:
        if pr.get("mergeable") != "MERGEABLE":
            problems.append(f"mergeable: {pr.get('mergeable')}")
        if pr.get("mergeStateStatus") not in OK_STATES:
            problems.append(f"merge state: {pr.get('mergeStateStatus')}")
    state, failing = ci_state(pr.get("statusCheckRollup"))
    if ctx.cfg.pr["wait_for_ci"] and state in ("fail", "pending"):
        problems.append(f"checks {'failed: ' + '; '.join(failing) if failing else 'still running'}")
    git(co.path, "fetch", "origin", "--prune", timeout=90)
    dirty = status_lines(co.path)
    if dirty:
        problems.append(f"uncommitted files, not in the PR: {', '.join(line[3:] for line in dirty)}")
    if remote_sha(co.path, name) != git(co.path, "rev-parse", "HEAD"):
        problems.append(f"HEAD isn't pushed to origin/{name}: unpushed work isn't in the PR")
    own = commits(co.path, ctx.base())
    result.update(commits=[c["subject"] for c in own])
    if len(own) > 1 and ctx.cfg.pr["merge"] == "squash":
        result["squash_collapses"] = True
    if problems == ["merge state: BLOCKED"] and state in ("pass", "none"):
        skipped = path_filtered(ctx, co, pr)
        if skipped:
            problems.clear()
            result["admin_merge"] = skipped
    result["status"] = "blocked" if problems else "ready"
    return result


def direct_precheck(ctx: Context, co: Checkout, result: dict) -> dict:
    """Without `pr.required`: HEAD must be clean, and a fast-forward of origin/<default>."""
    problems = result["problems"]
    git(co.path, "fetch", "origin", "--prune", timeout=90)
    if status_lines(co.path):
        problems.append("working tree not clean; commit first")
    head = git(co.path, "rev-parse", "HEAD")
    base = git(co.path, "rev-parse", ctx.base())
    if gitgh.git_ok(co.path, "merge-base", "--is-ancestor", head, base):
        return {**result, "status": "already-merged", "merge_commit": head[:7]}
    if not gitgh.git_ok(co.path, "merge-base", "--is-ancestor", base, head):
        problems.append(f"not a fast-forward of {ctx.base()}: run push to rebase it")
    result["commits"] = [c["subject"] for c in commits(co.path, ctx.base())]
    result["status"] = "blocked" if problems else "ready"
    return result


def merge_one(ctx: Context, co: Checkout, check: dict) -> dict:
    if not ctx.cfg.pr["required"]:
        name = branch(co.path)
        git(co.path, "push", "origin", f"HEAD:refs/heads/{ctx.cfg.default_branch}", timeout=90)
        git(co.path, "fetch", "origin", "--prune", timeout=90)
        if git(co.path, "rev-parse", "HEAD") != remote_sha(co.path, ctx.cfg.default_branch):
            raise Failed(f"{co.name}: origin/{ctx.cfg.default_branch} isn't HEAD after the push")
        note_merge(ctx, co.name, git(co.path, "rev-parse", "HEAD"))
        deleted = gitgh.git_ok(co.path, "push", "origin", "--delete", name)
        return {"repo": co.name, "merge_commit": git(co.path, "rev-parse", "--short", "HEAD"),
                "remote_branch_deleted": deleted}
    method = ctx.cfg.pr["merge"]
    args = ["pr", "merge", str(check["number"]), "--repo", co.gh_repo, f"--{method}", "--delete-branch",
            *(["--admin"] if check.get("admin_merge") else [])]
    error = None
    try:
        gh(*args)
    except Failed as e:
        error = str(e)  # may have merged anyway (a timeout): the re-query decides
    pr = view(co, str(check["number"]), "state,mergeCommit")
    if pr.get("state") != "MERGED":
        raise Failed(f"{co.name}: PR #{check['number']} is {pr.get('state')} after merge" +
                     (f": {error}" if error else " although gh reported success"))
    return {"repo": co.name, "number": check["number"], "url": check["url"],
            "merge_commit": (pr.get("mergeCommit") or {}).get("oid", "")[:7], "remote_branch_deleted": True,
            **({"admin": True} if check.get("admin_merge") else {})}


def step_merge(ctx: Context) -> tuple[dict, int]:
    checks = [merge_precheck_one(ctx, co) for co in ctx.checkouts]
    out = {"precheck": checks, "merged": []}
    blocked = [c for c in checks if c["status"] == "blocked"]
    if blocked:
        out["error"] = "precheck failed; nothing was merged"
        waiting = [c["repo"] for c in blocked if c.get("waiting_for") == "review"]
        if waiting and all(c.get("waiting_for") == "review" and len(c["problems"]) == 1 for c in blocked):
            return {**out, "halted": f"waiting for review approval: {', '.join(waiting)}"}, HALTED
        return out, PRECHECK_FAILED
    collapses = [c for c in checks if c["status"] == "ready" and c.get("squash_collapses")]
    if collapses and not getattr(ctx.args, "confirm_squash", False):
        out["needs_confirmation"] = [{"repo": c["repo"], "number": c["number"], "commits": c["commits"]}
                                     for c in collapses]
        out["halted"] = "these squashes collapse several commits into one; show Allen, then run again with " \
                        "--confirm-squash"
        return out, HALTED
    if ctx.dry_run:
        return {**out, "dry_run": True}, OK
    by_name = {co.name: co for co in ctx.checkouts}
    for check in checks:
        if check["status"] != "ready":
            continue
        try:
            out["merged"].append(merge_one(ctx, by_name[check["repo"]], check))
        except Failed as e:
            out.update(stopped_at=check["repo"], error=str(e),
                       not_merged=[c["repo"] for c in checks if c["status"] == "ready"
                                   and c["repo"] not in {m["repo"] for m in out["merged"]}])
            return out, STEP_FAILED
    return out, OK


# ---------------------------------------------------------------------------------------------------------------------
# cleanup


def is_merged(ctx: Context, co: Checkout, name: str) -> bool:
    if not ctx.cfg.pr["required"]:
        return gitgh.git_ok(co.path, "merge-base", "--is-ancestor", "HEAD", ctx.base())
    return any(p["state"] == "MERGED" for p in prs_for_branch(co.gh_repo, name))


def step_cleanup(ctx: Context) -> tuple[dict, int]:
    out = {"worktrees": [], "branches": [], "warnings": []}
    names = {}
    for co in ctx.checkouts:
        git(co.path, "fetch", "origin", "--prune", timeout=90, check=False)
        names[co.name] = branch(co.path)
    unmerged = [r for r, n in names.items() if not is_merged(ctx, next(c for c in ctx.checkouts if c.name == r), n)]
    if unmerged:
        return {**out, "error": f"not merged yet: {', '.join(sorted(unmerged))}; nothing was removed"}, PRECHECK_FAILED
    if ctx.dry_run:
        return {**out, "dry_run": True, "would_remove": sorted(names)}, OK
    mains = {co.name: co.main for co in ctx.checkouts}
    if getattr(ctx.args, "checkouts", None):
        results = [worktrees.remove_path(co.path, co.name, co.main) for co in ctx.checkouts]
        for folder in {co.path.parent for co in ctx.checkouts}:
            if folder.is_dir() and folder.parent == ctx.cfg.worktrees_dir and not any(folder.iterdir()):
                folder.rmdir()
    else:
        results = worktrees.remove(ctx.cfg, ctx.wid, sorted(names))
    out["worktrees"] = results
    gone = {r["repo"] for r in results if r["status"] in ("removed", "absent")}
    for repo, name in sorted(names.items()):
        if repo not in gone:
            continue
        plan = branches.scan(ctx.cfg, [repo], name)
        deleted, code = branches.delete(plan, name)
        out["branches"] += deleted["deleted"]
        out["warnings"] += [f"{k['repo']}: {k['branch']} kept ({k['keep']})" for k in plan["kept"]]
        out["warnings"] += [f"{s['repo']}: {s['branch']} skipped ({s['reason']})" for s in deleted["skipped"]]
        if code != OK:
            return {**out, "error": deleted["error"]}, STEP_FAILED
        # Best effort: a default branch checked out in the main checkout can't be fast-forwarded by refspec.
        gitgh.run(["git", "fetch", "origin", f"{ctx.cfg.default_branch}:{ctx.cfg.default_branch}"], mains[repo], 90,
                  check=False)
    refused = [r for r in results if r["status"] == "refused"]
    if refused:
        out["error"] = "worktree removal refused for " + ", ".join(sorted(r["repo"] for r in refused)) + \
            "; the others are cleaned up and the task stays open. Clear what it names, then run again"
        return out, STEP_FAILED
    if record_field(ctx, "cycle") == "shared-library":
        out["next"] = "release"
        ok = record_all(ctx, out, ("set-list", "worktrees"),
                        ("set", "status=review", "stage=awaiting-ci", "owner=claude"))
    else:
        ok = record_all(ctx, out, ("set-list", "worktrees"), ("set", "status=done", "stage="))
    if not ok:
        out["error"] = "cleaned up, but the task record wasn't updated"
    return out, record_code(out)


# ---------------------------------------------------------------------------------------------------------------------
# run


def step_run(ctx: Context) -> tuple[dict, int]:
    out = {"work_id": ctx.wid, "steps": {}}
    if ctx.cfg.pr["required"] and all(merged_pr(ctx, co, branch(co.path)) for co in ctx.checkouts):
        # Merged on GitHub: everything before cleanup already happened there.
        out["skipped"] = "every PR is merged; cleanup only"
        return run_steps(ctx, out, [("cleanup", step_cleanup)])
    checks = [commit_precheck_one(ctx, co) for co in ctx.checkouts]
    pr_checks = [pr_precheck_one(ctx, co, resume=True, pushed=False) for co in ctx.checkouts] \
        if ctx.cfg.pr["required"] else []
    problems = [f"{c['repo']}: {p}" for c in (*checks, *pr_checks) for p in c["problems"]]
    if ctx.cfg.pr["required"]:
        problems += pr_preflight_problems(ctx)
    if problems:
        return {**out, "precheck": checks, "problems": problems,
                "error": "preflight failed; nothing was committed, pushed or opened"}, PRECHECK_FAILED
    if ctx.dry_run:
        return {**out, "precheck": checks, "dry_run": True}, OK
    return run_steps(ctx, out, [("commit", step_commit), ("push", step_push), ("open-pr", step_open_pr),
                                ("wait-ci", step_wait_ci), ("merge", step_merge), ("cleanup", step_cleanup)])


def run_steps(ctx: Context, out: dict, steps: list) -> tuple[dict, int]:
    for name, fn in steps:
        result, code = fn(ctx)
        out["steps"][name] = result
        if "announce" in result:
            out["announce"] = result["announce"]
        if code != OK:
            out.update(stopped_at=name, error=result.get("error") or result.get("halted"))
            return out, code
        if name == getattr(ctx.args, "stop_after", None):
            out["stopped_after"] = name
            return out, OK
    out["done"] = True
    if "next" in out["steps"].get("cleanup", {}):
        out["next"] = out["steps"]["cleanup"]["next"]
    return out, OK


STEPS = {"commit": step_commit, "push": step_push, "open-pr": step_open_pr, "wait-ci": step_wait_ci,
         "merge": step_merge, "cleanup": step_cleanup, "run": step_run}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in STEPS:
        p = sub.add_parser(name)
        p.add_argument("work_id", help="the task's ticket key, or a slug")
        p.add_argument("--repos", help="only these repos of the task")
        p.add_argument("--checkouts", help="comma-separated repo names or paths, instead of the task's worktrees")
        p.add_argument("--dry-run", action="store_true", help="run the read-only checks and print them")
        p.add_argument("--no-record", action="store_true", help="never write the task record")
        p.add_argument("--publish-dir")
        if name in ("commit", "run"):
            msg = p.add_mutually_exclusive_group()
            msg.add_argument("--message-file")
            msg.add_argument("--message-dir")
        if name in ("open-pr", "run"):
            p.add_argument("--announce-file")
            p.add_argument("--no-announce", action="store_true", help="skip the announcement (none was approved)")
            p.add_argument("--announce-existing", action="store_true",
                           help="print the announcement even when every PR was already open")
            p.add_argument("--reviewer", help="a login or org/team to request review from, for every PR")
        if name in ("wait-ci", "run"):
            p.add_argument("--interval", type=float, default=30, help="seconds between polls")
            p.add_argument("--timeout", type=float, default=3600, help="seconds before giving up (exit 4)")
            p.add_argument("--grace", type=float, default=60, help="seconds with no checks before calling it green")
        if name in ("merge", "run"):
            p.add_argument("--confirm-squash", action="store_true",
                           help="Allen has seen the squashes that collapse several commits")
        if name == "run":
            p.add_argument("--stop-after", choices=[s for s in STEPS if s != "run"],
                           help="end the run after this step")
    args = parser.parse_args(argv)
    try:
        cfg = config.load()
        gitgh.check_work_id(args.work_id)
        ctx = Context(cfg, args.work_id, args)
        return emit(*STEPS[args.cmd](ctx))
    except (Failed, config.ConfigError) as e:
        return emit({"error": str(e)}, PRECHECK_FAILED)


if __name__ == "__main__":
    sys.exit(main())
