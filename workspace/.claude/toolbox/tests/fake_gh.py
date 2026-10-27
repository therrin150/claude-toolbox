#!/usr/bin/env python3
"""A stand-in for `gh` in the git and publish tests: pull requests live in a JSON file, and "GitHub" is the local bare
repo each test made. Only the calls the toolbox scripts make are supported.

Environment (set by the tests):
  GH_FAKE_STATE     path to the JSON state: {"next": 1, "prs": [...], "calls": [...], "ci": {...}}
  GH_FAKE_REMOTES   JSON map of `--repo` value -> the bare repo's path
  GH_FAKE_DEFAULT   the default branch's name
  GH_FAKE_GREEN     a new PR's checks are already green
  GH_FAKE_CREATE_FAILS  a number of `pr create` calls that fail (without creating anything) before one works

A PR's `statusCheckRollup`, `reviewDecision` and `mergeStateStatus` are plain fields a test edits in the state file.
`ci` maps a PR number to a list of rollups: each `pr view` of it moves to the next one, and the last sticks.

`run list --commit <sha>` reports one green `release` push run on the default branch for any commit the bare repo's
default branch holds (none otherwise), unless `runs` in the state maps the sha to a list of runs; `run view` reports
it, and `run watch` returns at once.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

STATE = Path(os.environ["GH_FAKE_STATE"])
REMOTES = json.loads(os.environ["GH_FAKE_REMOTES"])
DEFAULT = os.environ.get("GH_FAKE_DEFAULT", "main")
GREEN = [{"name": "build", "status": "COMPLETED", "conclusion": "SUCCESS"}]


def load() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {"next": 1, "prs": [], "calls": [], "ci": {}}


def save(state: dict) -> None:
    STATE.write_text(json.dumps(state, indent=2))


def bare(repo: str) -> str:
    return REMOTES[repo]


def ref_sha(repo: str, branch: str) -> str | None:
    proc = subprocess.run(["git", "--git-dir", bare(repo), "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
                          capture_output=True, text=True)
    return proc.stdout.strip() or None


def option(args: list[str], name: str, default=None):
    return args[args.index(name) + 1] if name in args else default


def project(pr: dict, fields: str) -> dict:
    return {f: pr.get(f) for f in fields.split(",")}


def find(state: dict, repo: str, ref: str) -> dict | None:
    for pr in state["prs"]:
        if pr["repo"] == repo and ref in (str(pr["number"]), pr["head"], pr["url"]):
            return pr
    return None


def run_command(state: dict, verb: str, rest: list[str]) -> int:
    repo = option(rest, "--repo")
    if verb == "list":
        sha = option(rest, "--commit")
        runs = state.get("runs", {}).get(sha)
        if runs is None:
            held = subprocess.run(["git", "--git-dir", bare(repo), "merge-base", "--is-ancestor", sha, DEFAULT],
                                  capture_output=True).returncode == 0
            runs = [{"databaseId": 1, "workflowName": "release", "event": "push", "headBranch": DEFAULT,
                     "status": "completed", "conclusion": "success", "url": f"https://example.test/{repo}/runs/1",
                     "createdAt": "2026-10-27T10:00:00Z", "number": 1, "attempt": 1}] if held else []
        save(state)
        print(json.dumps(runs))
        return 0
    if verb == "view":
        found = state.get("run_views", {}).get(rest[0]) or {
            "status": "completed", "conclusion": "success", "url": f"https://example.test/{repo}/runs/{rest[0]}",
            "number": 1, "attempt": 1, "workflowName": "release"}
        save(state)
        print(json.dumps(found))
        return 0
    save(state)
    return 0 if verb == "watch" else 1


def main() -> int:
    args = sys.argv[1:]
    state = load()
    state["calls"].append(args)
    if args[:1] == ["api"]:
        # gh api -X DELETE repos/<owner>/<repo>/git/refs/heads/<branch>
        path = args[-1]
        owner_repo, _, branch = path.removeprefix("repos/").partition("/git/refs/heads/")
        subprocess.run(["git", "--git-dir", bare(owner_repo), "update-ref", "-d", f"refs/heads/{branch}"], check=True)
        save(state)
        return 0
    if args[:1] == ["run"]:
        return run_command(state, args[1], args[2:])
    if args[:1] != ["pr"]:
        save(state)
        print("unsupported", args, file=sys.stderr)
        return 1
    verb, rest = args[1], args[2:]
    repo = option(rest, "--repo")
    if verb == "list":
        head, wanted = option(rest, "--head"), option(rest, "--state", "open")
        found = [p for p in state["prs"] if p["repo"] == repo and p["head"] == head
                 and (wanted == "all" or p["state"].lower() == wanted)]
        for pr in found:
            pr["headRefOid"] = ref_sha(repo, pr["head"]) or pr["headRefOid"]
        save(state)
        print(json.dumps([project(p, option(rest, "--json")) for p in found]))
        return 0
    if verb == "create":
        failures = int(os.environ.get("GH_FAKE_CREATE_FAILS", "0"))
        counted = state.setdefault("create_attempts", 0)
        state["create_attempts"] = counted + 1
        if counted < failures:
            save(state)
            print("HTTP 502", file=sys.stderr)
            return 1
        number = state["next"]
        state["next"] += 1
        head = option(rest, "--head")
        state["prs"].append({
            "number": number, "repo": repo, "head": head, "base": option(rest, "--base"),
            "url": f"https://example.test/{repo}/pull/{number}", "state": "OPEN",
            "title": option(rest, "--title"),
            "body": Path(option(rest, "--body-file")).read_text() if "--body-file" in rest
            else option(rest, "--body"),
            "reviewer": option(rest, "--reviewer"), "headRefOid": ref_sha(repo, head),
            "reviewDecision": "", "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN",
            "statusCheckRollup": GREEN if os.environ.get("GH_FAKE_GREEN") else [], "mergeCommit": None,
        })
        save(state)
        print(state["prs"][-1]["url"])
        return 0
    pr = find(state, repo, rest[0])
    if pr is None:
        print("no pull requests found", file=sys.stderr)
        return 1
    if verb == "view":
        sequence = state["ci"].get(str(pr["number"]))
        if sequence:
            pr["statusCheckRollup"] = sequence[0]
            if len(sequence) > 1:
                sequence.pop(0)
        pr["headRefOid"] = ref_sha(repo, pr["head"]) or pr["headRefOid"]
        save(state)
        print(json.dumps(project(pr, option(rest, "--json"))))
        return 0
    if verb == "merge":
        if pr["state"] != "OPEN":
            print("already merged", file=sys.stderr)
            return 1
        head = ref_sha(repo, pr["head"])
        git = ["git", "--git-dir", bare(repo)]
        subprocess.run([*git, "update-ref", f"refs/heads/{DEFAULT}", head], check=True)
        pr.update(state="MERGED", headRefOid=head, mergeCommit={"oid": head})
        if "--delete-branch" in rest:
            subprocess.run([*git, "update-ref", "-d", f"refs/heads/{pr['head']}"], check=True)
        pr["merge_flag"] = [a for a in rest if a in ("--squash", "--merge", "--rebase")]
        save(state)
        return 0
    save(state)
    return 1


if __name__ == "__main__":
    sys.exit(main())
