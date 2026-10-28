#!/usr/bin/env python3
"""Ship a change to a Claude toolbox repo end to end: branch and commit when needed, get it onto the default branch,
clean up, and preview the installer. How it gets there is the toolbox's own choice, the top-level `ship` key of its
`domains.yml`:

  ship: pr       branch, push, open a PR, merge it (a merge commit), delete the branch. The default.
  ship: direct   commit, rebase onto origin/<default> when it moved, push straight to the default branch.

`branch_prefix` (top-level, default `toolbox`) names a new branch in `pr` mode: <prefix>/<subject as kebab-case>.
Nothing else applies: no reviewers, no PR template, no announcement, no approval to wait for.

  ship.py precheck                              read-only; what run would do: mode, branch, commit, files, PR
  ship.py run [--message-file F] [--branch B] [--title T]
  ship.py cleanup --branch B                    cleanup only, for a branch whose PR is already merged

Every command takes --repo-dir (default: the repository the current directory is in). It must hold a `domains.yml`, and
may be a linked worktree: ship from there, and cleanup removes the worktree (refused while it has uncommitted or
untracked files) instead of switching branches. The install preview always runs in the main checkout, which is what the
installed links point at.

`run` needs --message-file (subject line, blank line, body) whenever there are uncommitted changes. In `pr` mode a new
branch is created from origin/<default> when HEAD is the default branch or a branch whose PR has merged; its name is
--branch, else <prefix>/<subject as kebab-case>. On any other branch, the commit goes on that branch. In `direct` mode
the commit goes on the current branch, which is rebased onto origin/<default> and pushed to it.

Every command prints one JSON object. Exit 0 on success, 2 when a precheck fails (nothing was changed), 3 when a step
after that fails (the output says which steps ran).

Never force-pushes, never amends, and adds no trailers to the message. Cleanup deletes the local branch with
`git branch -d` only (refuses an unmerged branch) and fast-forwards the local default branch; nothing is
force-deleted or reset.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

GH_TIMEOUT = 90
ENV = {"GIT_TERMINAL_PROMPT": "0", "GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1"}
TRAILER = re.compile(r"^(co-authored-by|signed-off-by):|🤖", re.IGNORECASE | re.MULTILINE)
# The claude-toolbox this script ships from (global/skills/ship-toolbox-change/scripts/), whose installer also installs
# the glue toolboxes its machine.yml lists, which have no installer of their own.
CLAUDE_TOOLBOX = Path(__file__).resolve().parents[4]


class StepFailed(Exception):
    pass


class Ship:
    """One toolbox checkout to ship from: its main checkout, default branch, GitHub repo and ship mode."""

    def __init__(self, repo_dir: Path):
        self.dir = repo_dir.resolve()
        if not (self.dir / ".git").exists():
            raise StepFailed(f"{self.dir} is not a git checkout")
        self.main = Path(self.git("rev-parse", "--path-format=absolute", "--git-common-dir")).parent
        manifest = self.main / "domains.yml"
        if not manifest.is_file():
            raise StepFailed(f"{self.main} has no domains.yml: not a toolbox repo")
        data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        self.mode = data.get("ship", "pr")
        if self.mode not in ("pr", "direct"):
            raise StepFailed(f"domains.yml: ship must be pr or direct; got {self.mode!r}")
        self.prefix = str(data.get("branch_prefix", "toolbox")).strip("/")
        self.base = self.default_branch()
        self.gh_repo = self.github_repo()

    # Running things

    def run(self, cmd: list[str], check: bool = True, timeout: int | None = None, cwd: Path | None = None) -> str:
        # stdin closed so gh can never sit on an interactive prompt.
        proc = subprocess.run(cmd, cwd=cwd or self.dir, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              timeout=timeout, env={**os.environ, **ENV})
        if check and proc.returncode != 0:
            raise StepFailed(f"{' '.join(cmd)}: {(proc.stderr or proc.stdout).strip()}")
        return proc.stdout.strip()

    def git(self, *args: str, check: bool = True, cwd: Path | None = None) -> str:
        return self.run(["git", *args], check=check, cwd=cwd)

    def gh(self, *args: str, check: bool = True) -> str:
        return self.run(["gh", *args], check=check, timeout=GH_TIMEOUT, cwd=Path.home())

    def default_branch(self) -> str:
        ref = self.git("symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD", check=False)
        if ref:
            return ref.removeprefix("origin/")
        for name in ("main", "master"):
            if self.git("rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{name}", check=False):
                return name
        raise StepFailed("can't tell the default branch: origin/HEAD isn't set and there is no origin/main or "
                         "origin/master")

    def github_repo(self) -> str:
        url = self.git("remote", "get-url", "origin")
        match = re.search(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?/?$", url)
        if not match:
            raise StepFailed(f"can't read an owner and repo from origin's URL {url}")
        return f"{match.group(1)}/{match.group(2)}"

    def prs(self, branch: str, state: str) -> list[dict]:
        return json.loads(self.gh("pr", "list", "--repo", self.gh_repo, "--head", branch, "--state", state,
                                  "--json", "number,url") or "[]")

    # precheck

    def precheck(self) -> dict:
        """Works out what run would do. Only a detached HEAD or nothing to ship is a problem here; a missing commit
        message is checked by run."""
        self.git("fetch", "origin", "--prune")
        branch = self.git("branch", "--show-current")
        changes = [line for line in self.git("status", "--porcelain").splitlines() if line]
        problems = []
        if not branch:
            problems.append(f"detached HEAD; switch to {self.base} or a branch")

        open_pr, new_branch = None, False
        if self.mode == "pr":
            if branch == self.base:
                new_branch = True
            elif branch:
                open_prs = self.prs(branch, "open")
                open_pr = open_prs[0] if open_prs else None
                # A merged PR and no open one means the branch is done: new work goes on a fresh branch.
                new_branch = not open_pr and bool(self.prs(branch, "merged"))

        # A merged branch's commits are already in; on the default branch, local commits not yet on origin go along.
        ahead = [] if new_branch and branch != self.base else [
            line.split(" ", 1) for line in
            self.git("log", "--reverse", "--format=%H %s", f"origin/{self.base}..HEAD").splitlines() if line]
        if not changes and not ahead and not open_pr and branch:
            problems.append(f"nothing to ship: no uncommitted changes and no commits ahead of origin/{self.base}")

        head = self.git("rev-parse", "HEAD")
        remote = self.git("rev-parse", "--verify", "--quiet", f"origin/{branch}", check=False) \
            if branch and not new_branch else ""
        return {
            "mode": self.mode,
            "base": self.base,
            "branch": branch,
            "new_branch": new_branch,
            "uncommitted": changes,
            "commits_ahead": [{"sha": c[0][:7], "subject": c[1]} for c in ahead],
            "needs_push": bool(changes) or new_branch or remote != head,
            "open_pr": open_pr,
            "problems": problems,
        }

    # branch, commit, push

    @staticmethod
    def slug(subject: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", subject.lower()).strip("-")[:40].rstrip("-")

    @staticmethod
    def read_message(path: str) -> tuple[str, str]:
        text = Path(path).read_text().strip()
        subject, _, body = text.partition("\n")
        if not subject.strip():
            raise StepFailed("the commit message has no subject line")
        if TRAILER.search(text):
            raise StepFailed("the commit message carries a trailer; remove it")
        return subject.strip(), body.strip()

    def branch_and_commit(self, check: dict, message_file: str | None, branch_name: str | None) -> dict:
        done = {}
        subject = None
        if check["uncommitted"]:
            if not message_file:
                raise StepFailed("there are uncommitted changes; pass --message-file")
            subject, _ = self.read_message(message_file)
        branch = check["branch"]
        if check["new_branch"]:
            branch = branch_name or f"{self.prefix}/{self.slug(subject or 'toolbox-change')}"
            if self.git("rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False):
                raise StepFailed(f"branch {branch} already exists; pass --branch")
            # From the default branch, start at HEAD so unpushed local commits come along; from a merged branch, start
            # at origin/<default>. Uncommitted changes ride along; git refuses (and nothing changes) if one would be
            # overwritten.
            start = "HEAD" if check["branch"] == self.base else f"origin/{self.base}"
            self.git("switch", "-c", branch, start)
            done["created_branch"] = branch
        if check["uncommitted"]:
            self.git("add", "-A")
            done["committed_files"] = self.git("diff", "--cached", "--name-status").splitlines()
            self.git("commit", "-F", message_file)
            done["commit"] = self.git("rev-parse", "--short", "HEAD")
        done["branch"] = branch
        return done

    def push_branch(self, branch: str) -> dict:
        remote = self.git("rev-parse", "--verify", "--quiet", f"origin/{branch}", check=False)
        if remote != self.git("rev-parse", "HEAD"):
            self.git("push", "-u", "origin", branch)
            return {"pushed": True}
        return {}

    # pr mode: PR and merge

    def pr_text(self, title: str | None) -> tuple[str, str]:
        shas = self.git("log", "--reverse", "--format=%H", f"origin/{self.base}..HEAD").splitlines()
        if not shas:
            raise StepFailed(f"no commits ahead of origin/{self.base}")
        subjects = [self.git("log", "-1", "--format=%s", s) for s in shas]
        bodies = [self.git("log", "-1", "--format=%b", s).strip() for s in shas]
        if len(shas) == 1:
            return title or subjects[0], bodies[0]
        # Several commits: the title is the oldest subject (or --title), and the body lists every commit so the merge
        # commit's PR still says what landed.
        lines = []
        for subject, body in zip(subjects, bodies):
            lines.append(f"- {subject}")
            lines.extend(f"  {line}" if line else "" for line in body.splitlines())
        return title or subjects[0], "\n".join(lines)

    def open_pr(self, branch: str, title: str | None) -> dict:
        pr_title, body = self.pr_text(title)
        url = self.gh("pr", "create", "--repo", self.gh_repo, "--base", self.base, "--head", branch, "--title",
                      pr_title, "--body", body)
        return {"url": url.splitlines()[-1], "title": pr_title, "created": True}

    def merge_pr(self, pr_url: str, head: str) -> str:
        # A merge commit: the branch's commits stay reachable from the default branch, so the local one fast-forwards
        # and `git branch -d` recognizes the branch as merged. --match-head-commit refuses if the branch moved.
        self.gh("pr", "merge", pr_url, "--repo", self.gh_repo, "--merge", "--match-head-commit", head)
        state = json.loads(self.gh("pr", "view", pr_url, "--repo", self.gh_repo, "--json", "state,mergeCommit"))
        if state.get("state") != "MERGED":
            raise StepFailed(f"PR state is {state.get('state')} after merge")
        return state["mergeCommit"]["oid"][:7]

    # direct mode: straight to the default branch

    def push_to_default(self) -> dict:
        done = {}
        self.git("fetch", "origin", "--prune")
        behind = self.git("rev-list", "--count", f"HEAD..origin/{self.base}")
        if behind != "0":
            try:
                self.git("rebase", f"origin/{self.base}")
            except StepFailed as e:
                raise StepFailed(f"rebase onto origin/{self.base} stopped: {e}. Resolve it with "
                                 "`git rebase --continue` or `git rebase --abort`, then run again") from None
            done["rebased_onto"] = self.git("rev-parse", "--short", f"origin/{self.base}")
        self.git("push", "origin", f"HEAD:refs/heads/{self.base}")
        done["pushed_to"] = self.base
        done["merge_commit"] = self.git("rev-parse", "--short", "HEAD")
        return done

    # cleanup

    def cleanup(self, branch: str) -> dict:
        done = {}
        self.git("fetch", "origin", "--prune")
        if self.mode == "pr" and self.git("ls-remote", "--heads", "origin", branch):
            self.gh("api", "-X", "DELETE", f"repos/{self.gh_repo}/git/refs/heads/{branch}")
            done["remote_branch_deleted"] = True
        if self.main.resolve() != self.dir:
            return {**done, **self.cleanup_worktree(branch)}
        if branch == self.base:
            self.git("merge", "--ff-only", f"origin/{self.base}")
            done["base_at"] = self.git("rev-parse", "--short", "HEAD")
            done["install_preview"] = self.install_preview()
            return done
        # Untracked files ride along a switch safely (git refuses if one would be overwritten); tracked changes don't.
        if self.git("status", "--porcelain", "--untracked-files=no"):
            raise StepFailed("tracked files have uncommitted changes; not switching branches")
        if self.git("branch", "--show-current") != self.base:
            self.git("switch", self.base)
        self.git("merge", "--ff-only", f"origin/{self.base}")
        done["base_at"] = self.git("rev-parse", "--short", "HEAD")
        if self.git("rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False):
            self.git("branch", "-d", branch)
            done["local_branch_deleted"] = True
        done["install_preview"] = self.install_preview()
        return done

    def cleanup_worktree(self, branch: str) -> dict:
        """Cleanup when shipping from a linked worktree: the default branch is checked out in the main checkout, so
        remove the worktree instead of switching, fast-forward the main checkout's default branch if it's on it, then
        delete the merged branch there."""
        done = {}
        # `git worktree remove` refuses a worktree with uncommitted or untracked files; nothing is forced.
        self.git("worktree", "remove", str(self.dir), cwd=self.main)
        done["worktree_removed"] = str(self.dir)
        if self.git("branch", "--show-current", cwd=self.main) == self.base:
            self.git("merge", "--ff-only", f"origin/{self.base}", cwd=self.main)
            done["base_at"] = self.git("rev-parse", "--short", "HEAD", cwd=self.main)
        else:
            done["base_not_updated"] = "the main checkout isn't on the default branch"
        if self.git("rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False, cwd=self.main):
            # -d checks the branch is merged into the main checkout's HEAD (the default branch, just fast-forwarded).
            self.git("branch", "-d", branch, cwd=self.main)
            done["local_branch_deleted"] = True
        done["install_preview"] = self.install_preview()
        return done

    def install_preview(self) -> dict:
        """Runs the installer in preview mode and returns the sections with pending changes: the main checkout's own,
        else (a glue toolbox) claude-toolbox's. Applying stays with install-domain, which asks first."""
        root = self.main if (self.main / "install.py").exists() else CLAUDE_TOOLBOX
        self.run([sys.executable, "install.py"], cwd=root)
        plan = (root / "install-plan.txt").read_text().splitlines()
        pending, section = {}, None
        for line in plan:
            if line.startswith("== "):
                section = line[3:].rsplit(" (", 1)[0]
                continue
            if section and section != "Already in place" and line.strip():
                pending.setdefault(section, []).append(line.strip())
        return pending


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo-dir", type=Path, default=Path.cwd(),
                        help="the checkout to ship from: a toolbox's main checkout or a linked worktree of it")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("precheck", parents=[common])
    r = sub.add_parser("run", parents=[common])
    r.add_argument("--message-file", help="commit message: subject line, blank line, body")
    r.add_argument("--branch", help="name for a new branch (default <branch_prefix>/<subject>)")
    r.add_argument("--title", help="PR title (default: the oldest commit's subject)")
    c = sub.add_parser("cleanup", parents=[common])
    c.add_argument("--branch", required=True)
    args = parser.parse_args(argv)

    out: dict = {}
    try:
        ship = Ship(args.repo_dir.expanduser())
        if args.cmd == "cleanup":
            out["cleanup"] = ship.cleanup(args.branch)
            print(json.dumps(out))
            return 0
        check = ship.precheck()
        out["precheck"] = check
        if not check["problems"] and args.cmd == "run" and check["uncommitted"] and not args.message_file:
            check["problems"].append("there are uncommitted changes; pass --message-file")
        if check["problems"] or args.cmd == "precheck":
            print(json.dumps(out))
            return 2 if check["problems"] else 0
    except (StepFailed, subprocess.TimeoutExpired) as e:
        out["error"] = str(e)
        print(json.dumps(out))
        return 2

    try:
        out["commit"] = ship.branch_and_commit(check, args.message_file, args.branch)
        branch = out["commit"]["branch"]
        if ship.mode == "direct":
            out["push"] = ship.push_to_default()
        else:
            out["commit"].update(ship.push_branch(branch))
            out["pr"] = {**check["open_pr"], "created": False} if check["open_pr"] else ship.open_pr(branch, args.title)
            out["merge_commit"] = ship.merge_pr(out["pr"]["url"], ship.git("rev-parse", "HEAD"))
        out["cleanup"] = ship.cleanup(branch)
    except (StepFailed, subprocess.TimeoutExpired) as e:
        out["error"] = str(e)
        print(json.dumps(out))
        return 3
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
