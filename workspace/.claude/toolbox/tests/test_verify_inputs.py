"""Tests for bin/verify_inputs.py, against temporary git repos: a nested workspace and a single one, each with a renamed
public function and its callers.

Run from the repo root:

    python3 -m unittest discover -s workspace/.claude/toolbox/tests
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TOOLBOX = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLBOX / "lib"))
sys.path.insert(0, str(TOOLBOX / "bin"))

import config  # noqa: E402
import verify_inputs  # noqa: E402

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.com"}


def run(cwd: Path, *args: str) -> str:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, env=dict(os.environ, **GIT_ENV))
    assert result.returncode == 0, f"{' '.join(args)}: {result.stderr}"
    return result.stdout


def make_repo(root: Path, name: str, files: dict[str, str]) -> Path:
    """A clone of a new bare origin, with `files` pushed to its `main` branch."""
    origin = root / "origins" / f"{name}.git"
    origin.mkdir(parents=True)
    run(origin, "git", "init", "--bare", "--initial-branch=main")
    clone = root / name
    clone.parent.mkdir(parents=True, exist_ok=True)
    run(root, "git", "clone", "--quiet", str(origin), str(clone))
    run(clone, "git", "checkout", "--quiet", "-B", "main")
    commit(clone, files, "initial")
    run(clone, "git", "push", "--quiet", "-u", "origin", "main")
    return clone


def commit(repo: Path, files: dict[str, str], message: str) -> None:
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
    run(repo, "git", "add", "-A")
    run(repo, "git", "commit", "--quiet", "-m", message)


def write_config(root: Path, layout: str, extra: str = "") -> config.Config:
    (root / ".claude").mkdir(parents=True, exist_ok=True)
    (root / ".claude" / "toolbox.local.yml").write_text(
        f"version: 1\nworkspace:\n  name: demo\n  layout: {layout}\n  default_branch: main\n{extra}"
        "user:\n  handle: jdoe\n  name: Jordan\nvault:\n  path: vault\n"
        "tracker:\n  kind: vault\n  key_prefix: DM\n", encoding="utf-8")
    return config.load_workspace(root)


class VerifyInputs(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp)
        patcher = mock.patch.dict(os.environ, {"TMPDIR": str(self.tmp / "out")})
        patcher.start()
        self.addCleanup(patcher.stop)
        (self.tmp / "out").mkdir()

    def record(self, cfg: config.Config, work_id: str, body: str, header: str = "") -> Path:
        path = cfg.tasks_dir / f"{work_id}-rename-fetch.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\ntype: story\nkey: {work_id}\n{header}---\n\n{body}\n", encoding="utf-8")
        return path

    def build(self, cfg, checkouts, names, **kwargs):
        summary, code = verify_inputs.build(cfg, checkouts, names, kwargs.get("task"), kwargs.get("base"), None)
        return summary, code, Path(summary["file"]).read_text(encoding="utf-8")

    # A nested workspace: `service` renames fetchAll to loadAll; `client` still calls fetchAll on its main branch.

    def nested(self, extra: str = "") -> tuple[config.Config, Path]:
        root = self.tmp / "ws"
        service = make_repo(root, "service", {"src/Repo.java": "class Repo { List fetchAll() { return null; } }\n"})
        make_repo(root, "client", {"src/Use.java": "class Use { void go(Repo r) { r.fetchAll(); } }\n",
                                   "src/Other.java": "// fetchAllItems is a different name\n"})
        make_repo(root, "library", {"README.md": "no callers here\n"})
        cfg = write_config(root, "nested", extra)
        run(service, "git", "checkout", "--quiet", "-b", "jdoe/DM-1-rename")
        commit(service, {"src/Repo.java": "class Repo { List loadAll() { return null; } }\n"}, "rename")
        (service / "NOTES.md").write_text("untracked note about loadAll\n", encoding="utf-8")
        return cfg, service

    def test_nested_sweeps_every_repo_on_its_fetched_default_branch(self):
        cfg, service = self.nested()
        summary, code, text = self.build(cfg, [str(service)], ["fetchAll"])
        self.assertEqual(code, 0)
        self.assertEqual(summary["checkouts"], {"service": 2})
        self.assertEqual(summary["not_searched"], {})
        # Two hits: the old definition on service's main (the branch's rename isn't there), and client's caller.
        self.assertEqual(summary["hits"], {"fetchAll": 2})
        self.assertIn("client:src/Use.java:1:class Use", text)
        self.assertIn("service:src/Repo.java:1:class Repo { List fetchAll()", text)
        self.assertNotIn("fetchAllItems is", text.split("## Caller sweep")[1])  # -w: a longer name is no hit
        self.assertIn("-class Repo { List fetchAll()", text)
        self.assertIn("+class Repo { List loadAll()", text)
        self.assertIn("NOTES.md (untracked)", text)

    def test_sweep_ignores_a_stale_local_branch_and_the_working_tree(self):
        cfg, service = self.nested()
        client = cfg.root / "client"
        # A caller merged to origin after the client's local main was last updated.
        other = self.tmp / "other-clone"
        run(self.tmp, "git", "clone", "--quiet", str(self.tmp / "ws" / "origins" / "client.git"), str(other))
        commit(other, {"src/New.java": "class New { void go(Repo r) { r.fetchAll(); } }\n"}, "merged elsewhere")
        run(other, "git", "push", "--quiet", "origin", "main")
        # An uncommitted caller in the client's working tree that is not on origin.
        (client / "src" / "Draft.java").write_text("r.fetchAll();\n", encoding="utf-8")
        summary, _, text = self.build(cfg, [str(service)], ["fetchAll"])
        self.assertIn("client:src/New.java", text)
        self.assertNotIn("Draft.java", text)
        self.assertEqual(summary["hits"], {"fetchAll": 3})

    def test_a_worktree_folder_stands_for_the_checkouts_inside_it(self):
        cfg, service = self.nested()
        folder = cfg.worktrees_dir / "DM-1"
        folder.mkdir(parents=True)
        run(service, "git", "worktree", "add", "--quiet", str(folder / "service"), "-b", "jdoe/DM-1-wt")
        summary, _, _ = self.build(cfg, ["DM-1"], ["loadAll"])
        self.assertEqual(list(summary["checkouts"]), ["service"])

    def test_requirement_from_the_task_record_and_scope_exceptions(self):
        cfg, service = self.nested("verify:\n  ignore_bumps: [shared-lib, team-*]\n")
        plan = cfg.vault_dir / "Discussions" / "d" / "plan.md"
        plan.parent.mkdir(parents=True)
        plan.write_text("# Plan\n", encoding="utf-8")
        self.record(cfg, "DM-1", "Rename fetchAll to loadAll.\n\nGIVEN a caller WHEN it lists THEN loadAll is used",
                    "source: \"[[Discussions/d/plan|Plan]]\"\n")
        summary, _, text = self.build(cfg, [str(service)], ["fetchAll"], task="DM-1")
        self.assertTrue(summary["file"].endswith("verify-inputs-DM-1.md"))
        self.assertIn("## Requirement", text)
        self.assertIn("Rename fetchAll to loadAll.", text)
        self.assertIn(f"Design (read only if needed): `{plan}`", text)
        self.assertIn("`shared-lib`, `team-*`", text)
        self.assertNotIn("type: story", text)  # frontmatter stays out of the requirement

    def test_no_scope_exceptions_section_by_default(self):
        cfg, service = self.nested()
        _, _, text = self.build(cfg, [str(service)], ["fetchAll"])
        self.assertNotIn("Scope exceptions", text)
        self.assertNotIn("## Requirement", text)

    def test_ignore_bumps_defaults_to_empty_and_must_be_a_list_of_names(self):
        cfg, _ = self.nested()
        self.assertEqual(cfg.get("verify.ignore_bumps"), [])
        with self.assertRaisesRegex(config.ConfigError, r"verify\.ignore_bumps: must be a list"):
            write_config(cfg.root, "nested", "verify:\n  ignore_bumps: shared-lib\n")

    def test_missing_task_record_fails_with_a_hint(self):
        cfg, service = self.nested()
        with self.assertRaisesRegex(verify_inputs.Failed, "no task record for DM-9"):
            self.build(cfg, [str(service)], ["fetchAll"], task="DM-9")

    def test_unknown_checkout_fails(self):
        cfg, _ = self.nested()
        with self.assertRaisesRegex(verify_inputs.Failed, "isn't a git checkout"):
            self.build(cfg, ["nowhere"], ["fetchAll"])

    # A single workspace: one repo with `api` and `web` areas; docs/ is outside both.

    def single(self) -> tuple[config.Config, Path]:
        root = self.tmp / "app"
        repo = make_repo(self.tmp, "app", {
            "api/Service.java": "class Service { List fetchAll() { return null; } }\n",
            "web/client.ts": "service.fetchAll();\n",
            "docs/api.md": "fetchAll is documented here\n",
        })
        self.assertEqual(repo, root)
        cfg = write_config(root, "single", "  areas:\n    api: {unit: mvn test}\n    web: {unit: npm test}\n")
        run(repo, "git", "checkout", "--quiet", "-b", "jdoe/DM-2-rename")
        commit(repo, {"api/Service.java": "class Service { List loadAll() { return null; } }\n"}, "rename")
        return cfg, repo

    def test_single_sweeps_the_repos_areas_only(self):
        cfg, repo = self.single()
        summary, code, text = self.build(cfg, [str(repo)], ["fetchAll"])
        self.assertEqual(code, 0)
        self.assertEqual(summary["hits"], {"fetchAll": 2})
        self.assertIn("demo:api/Service.java:1:", text)
        self.assertIn("demo:web/client.ts:1:service.fetchAll();", text)
        self.assertNotIn("docs/api.md", text.split("## Caller sweep")[1])

    def test_single_with_no_areas_sweeps_the_whole_repo(self):
        cfg, repo = self.single()
        cfg = write_config(repo, "single")
        summary, _, text = self.build(cfg, [str(repo)], ["fetchAll"])
        self.assertEqual(summary["hits"], {"fetchAll": 3})
        self.assertIn("docs/api.md", text)

    def test_cli_prints_the_summary(self):
        cfg, service = self.nested()
        result = subprocess.run([sys.executable, str(TOOLBOX / "bin" / "verify_inputs.py"), str(service),
                                 "--names", "fetchAll, loadAll", "--out", str(self.tmp / "inputs.md")],
                                cwd=cfg.root, capture_output=True, text=True,
                                env={k: v for k, v in os.environ.items() if k != config.ENV_WORKSPACE})
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(set(summary["hits"]), {"fetchAll", "loadAll"})
        self.assertTrue((self.tmp / "inputs.md").is_file())


if __name__ == "__main__":
    unittest.main()
