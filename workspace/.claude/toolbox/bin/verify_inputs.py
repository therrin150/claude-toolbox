#!/usr/bin/env python3
"""Write change-verifier's inputs to one file: the requirement, each checkout's diff and the caller sweep. The script
behind verify-change step 1; the session still picks the names to sweep.

  verify_inputs.py <checkout>... --names a,b,... [--task <work id | task record path>] [--base origin/<default>]
                   [--out <file>]

Per checkout (a path, or a name under the workspace root or its worktrees folder; a folder of worktrees, such as a
nested task's, stands for the checkouts inside it): `git fetch origin`, then the changed files and the full diff of the
working tree against its merge base with --base, so committed and uncommitted changes both count. Untracked files are
diffed as new files; the index is never touched.

The caller sweep runs `git grep -n -w -F` for every name on a freshly fetched `origin/<default branch>`, never the
working tree or the local branch: a main checkout sits on whatever branch was last used, and a stale local branch
misses merged code. A nested workspace sweeps every repo in it (fetched in parallel); a single one sweeps its own repo,
limited to its areas' folders when it lists any. Hits are `repo:path:line:text`, capped per name.

--task adds the requirement: the task record's body and its design doc's path (its `source_doc`, else its `source`).
Without it, the session passes the task in the verifier's prompt. Dependencies in `verify.ignore_bumps` are listed so
the verifier doesn't flag their version bumps.

The file goes to --out (default $TMPDIR/verify-inputs-<work id or repos>.md). Prints a JSON summary: the file, per
checkout its changed-file count, per name its hit count, and any repo that couldn't be fetched or searched. Exit 0, or
1 when a checkout's diff couldn't be produced.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import config  # noqa: E402

# High enough for any real name; a name that hits more is too generic to judge hit by hit.
MAX_HITS_PER_NAME = 1000
MAX_TEXT = 200
FETCH_TIMEOUT = 180
WIKILINK = re.compile(r"\[\[([^\]|#]+)")


class Failed(Exception):
    pass


def git(path: Path, *args: str, check: bool = True, timeout: int = 60) -> str:
    try:
        result = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, timeout=timeout,
                                env=dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0"))
    except subprocess.TimeoutExpired:
        raise Failed(f"git {' '.join(args[:2])} in {path} timed out") from None
    if check and result.returncode != 0:
        raise Failed(f"git {' '.join(args[:2])} in {path}: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout


def resolve(cfg: config.Config, name: str) -> list[Path]:
    """The checkouts a name stands for: a path, else a name under the workspace root or the worktrees folder. A folder
    that isn't a checkout itself but holds some (a nested task's worktrees) stands for those."""
    candidates = [Path(name).expanduser()]
    if not Path(name).expanduser().is_absolute():
        candidates += [cfg.root / name, cfg.worktrees_dir / name]
    for candidate in candidates:
        if candidate.is_dir():
            candidate = candidate.resolve()
            if (candidate / ".git").exists():
                return [candidate]
            inside = sorted(child for child in candidate.iterdir() if child.is_dir() and (child / ".git").exists())
            if inside:
                return inside
    raise Failed(f"{name} isn't a git checkout, or a folder of them, under {cfg.root}")


def checkout_diff(path: Path, base: str) -> dict:
    git(path, "fetch", "--quiet", "origin", timeout=FETCH_TIMEOUT)
    merge_base = git(path, "merge-base", base, "HEAD").strip()
    status = git(path, "diff", "--name-status", merge_base)
    diff = git(path, "diff", merge_base)
    untracked = [f for f in git(path, "ls-files", "--others", "--exclude-standard").splitlines() if f]
    for f in untracked:
        status += f"\nA\t{f} (untracked)"
        # --no-index exits 1 when the files differ, which they always do here.
        diff += "\n" + git(path, "diff", "--no-index", "--", "/dev/null", f, check=False)
    return {"name": path.name, "path": str(path), "base": base, "merge_base": merge_base,
            "files": [line for line in status.splitlines() if line.strip()], "diff": diff.strip()}


def sweep_targets(cfg: config.Config) -> list[tuple[str, Path, list[str]]]:
    """(label, repo path, pathspecs) for every repo the caller sweep searches: a nested workspace's repos, whole; a
    single workspace's own repo, limited to its areas' folders when it lists any."""
    if cfg.nested:
        return [(name, cfg.root / name, []) for name in cfg.areas()]
    return [(cfg.name, cfg.root, [name for name in cfg.areas() if (cfg.root / name).is_dir()])]


def sweep_repo(label: str, path: Path, pathspecs: list[str], names: list[str], ref: str) -> dict:
    if not (path / ".git").exists():
        return {"repo": label, "skipped": "not cloned"}
    try:
        git(path, "fetch", "--quiet", "origin", timeout=FETCH_TIMEOUT)
        args = ["grep", "-n", "-w", "-F", "-I"] + [x for n in names for x in ("-e", n)] + [f"origin/{ref}"]
        out = git(path, *args, *(["--", *pathspecs] if pathspecs else []), check=False)
    except Failed as e:
        return {"repo": label, "error": str(e)}
    hits: dict[str, list[str]] = {n: [] for n in names}
    for line in out.splitlines():
        hit = line.partition(":")[2]  # origin/<branch>:path:line:text -> path:line:text
        text = hit.split(":", 2)[-1]
        for n in names:
            if re.search(rf"(?<!\w){re.escape(n)}(?!\w)", text):
                hits[n].append(f"{label}:{hit[:MAX_TEXT]}")
    return {"repo": label, "hits": hits}


def split_record(text: str) -> tuple[dict, str]:
    """A task record's frontmatter and body."""
    import yaml
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            try:
                return yaml.safe_load(text[4:end]) or {}, text[end + 4:].strip()
            except yaml.YAMLError:
                pass
    return {}, text.strip()


def requirement(cfg: config.Config, task: str) -> dict:
    """The task record named by a path or a work id (`Tasks/<work id>-<slug>.md`), with its design doc's path."""
    record, work_id = Path(task).expanduser(), task
    if record.is_file():
        work_id = record.stem
    else:
        found = sorted(cfg.tasks_dir.glob(f"{task}-*.md")) + sorted(cfg.tasks_dir.glob(f"{task}.md"))
        if not found:
            raise Failed(f"no task record for {task} in {cfg.tasks_dir}; pass the task in the verifier's prompt instead")
        record = found[0]
    fields, body = split_record(record.read_text(encoding="utf-8"))
    plan = None
    source_doc = fields.get("source_doc")
    if isinstance(source_doc, str):
        plan = Path(source_doc).expanduser()
        plan = plan if plan.is_absolute() else cfg.vault_dir / plan
    elif isinstance(fields.get("source"), str):
        link = WIKILINK.search(fields["source"])
        if link:
            plan = cfg.vault_dir / f"{link.group(1).strip()}.md"
    return {"record": str(record), "work_id": work_id,
            "plan": str(plan) if plan and plan.is_file() else None, "body": body}


def render(req: dict | None, checkouts: list[dict], names: list[str], sweep: list[dict], ref: str,
           ignore_bumps: list[str]) -> str:
    out = ["# Verifier inputs", ""]
    if req:
        out += ["## Requirement", "", f"Task record: `{req['record']}`"]
        if req["plan"]:
            out.append(f"Design (read only if needed): `{req['plan']}`")
        out += ["", req["body"], ""]
    if ignore_bumps:
        out += ["## Scope exceptions", "",
                "Version bumps of these dependencies are made on purpose and are not out of scope (a name ending in `*` "
                f"is a prefix): {', '.join(f'`{b}`' for b in ignore_bumps)}.", ""]
    for c in checkouts:
        out += [f"## Diff: {c['name']}", "", f"Checkout: `{c['path']}` (against {c['base']}, merge base "
                f"{c['merge_base'][:10]})", "", "Changed files:", "", *[f"- `{f}`" for f in c["files"]], "",
                "```diff", c["diff"], "```", ""]
    out += ["## Caller sweep", "",
            f"`git grep -n -w -F` on each repo's freshly fetched origin/{ref}. Names searched: {', '.join(names)}.", ""]
    skipped = [f"{s['repo']} ({s.get('skipped') or s.get('error')})" for s in sweep if "hits" not in s]
    if skipped:
        out += [f"Not searched: {'; '.join(skipped)}", ""]
    for n in names:
        hits = [h for s in sweep for h in s.get("hits", {}).get(n, [])]
        out += [f"### `{n}`: {len(hits)} hit(s)", ""]
        shown = hits[:MAX_HITS_PER_NAME]
        out += ["```", *shown, "```"] if shown else ["None."]
        if len(hits) > len(shown):
            out.append(f"({len(hits) - len(shown)} more not shown)")
        out.append("")
    return "\n".join(out)


def build(cfg: config.Config, checkouts: list[str], names: list[str], task: str | None, base: str | None,
          out: Path | None) -> tuple[dict, int]:
    ref = cfg.default_branch
    base = base or f"origin/{ref}"
    paths = [p for c in checkouts for p in resolve(cfg, c)]
    diffs, failed = [], []
    for path in paths:
        try:
            diffs.append(checkout_diff(path, base))
        except Failed as e:
            failed.append({"checkout": str(path), "error": str(e)})
    req = requirement(cfg, task) if task else None
    targets = sweep_targets(cfg)
    with ThreadPoolExecutor(max_workers=8) as pool:
        sweep = list(pool.map(lambda t: sweep_repo(t[0], t[1], t[2], names, ref), targets))
    ignore = cfg.get("verify.ignore_bumps")

    label = req["work_id"] if req else "-".join(p.name for p in paths)
    out = out or Path(os.environ.get("TMPDIR") or tempfile.gettempdir()) / f"verify-inputs-{label}.md"
    out.write_text(render(req, diffs, names, sweep, ref, ignore), encoding="utf-8")
    summary = {
        "file": str(out),
        "checkouts": {c["name"]: len(c["files"]) for c in diffs},
        "hits": {n: sum(len(s.get("hits", {}).get(n, [])) for s in sweep) for n in names},
        "not_searched": {s["repo"]: s.get("skipped") or s.get("error") for s in sweep if "hits" not in s},
        "failed": failed,
    }
    return summary, 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("checkouts", nargs="+")
    parser.add_argument("--names", required=True, help="comma-separated public names to sweep for")
    parser.add_argument("--task", help="a work id or a task record's path")
    parser.add_argument("--base", help="what the diff is against; default origin/<default branch>")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    names = list(dict.fromkeys(n.strip() for n in args.names.split(",") if n.strip()))
    if not names:
        sys.exit("--names needs at least one name")
    try:
        cfg = config.load()
        summary, code = build(cfg, args.checkouts, names, args.task, args.base, args.out)
    except config.ConfigError as e:
        sys.exit(str(e))
    except Failed as e:
        sys.exit(str(e))
    print(json.dumps(summary, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
