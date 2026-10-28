"""Tests for lib/config.py, lib/extensions.py and `toolbox.py config`, against temporary workspaces.

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

import yaml

TOOLBOX = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(TOOLBOX / "lib"))

import config  # noqa: E402
import extensions  # noqa: E402


class Workspace(unittest.TestCase):
    """Each test gets a scratch folder; make() turns part of it into a workspace with a fixture's config."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp)
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(config.ENV_WORKSPACE, None)

    def make(self, fixture: str, name: str = "ws") -> Path:
        root = self.tmp / name
        (root / ".claude").mkdir(parents=True)
        shutil.copy(FIXTURES / f"{fixture}.yml", root / config.CONFIG_PATH)
        return root

    def write(self, raw: dict, name: str = "ws") -> Path:
        root = self.tmp / name
        (root / ".claude").mkdir(parents=True, exist_ok=True)
        (root / config.CONFIG_PATH).write_text(yaml.safe_dump(raw), encoding="utf-8")
        return root

    def raw(self, fixture: str) -> dict:
        return yaml.safe_load((FIXTURES / f"{fixture}.yml").read_text(encoding="utf-8"))

    def errors(self, raw: dict) -> list[str]:
        return config.validate(raw)[1]

    def add_docs(self, root: Path) -> None:
        """Extension docs beside the single fixture's config, whose extensions.disabled names old-release-wait.md."""
        folder = root / extensions.FOLDER
        folder.mkdir(parents=True, exist_ok=True)
        for name, skill in (("release-wait.md", "work-task"), ("old-release-wait.md", "work-task"),
                            ("a-verify-checks.md", "verify-change")):
            (folder / name).write_text(f"---\ntype: extension\nextends: {skill}\n---\n\n## add dispatch\n\nx\n")
        (folder / "README.md").write_text("---\ntype: extension\nextends: work-task\n---\n")  # never a doc
        (folder / "notes.txt").write_text("not markdown")


class LookupTest(Workspace):
    def test_finds_config_at_the_root(self):
        root = self.make("single")
        self.assertEqual(config.find(root), root)

    def test_walks_up_from_a_worktree_two_levels_deep(self):
        root = self.make("single")
        worktree = root / ".worktrees" / "PT-1-add-shelves"
        (worktree / ".claude" / "rules").mkdir(parents=True)  # the worktree's tracked .claude, without the config
        start = worktree / "src" / "main"
        start.mkdir(parents=True)
        self.assertEqual(config.find(start), root)
        self.assertEqual(config.load(start).root, root)

    def test_toolbox_workspace_overrides_the_walk(self):
        root = self.make("single")
        other = self.make("nested", "other")
        os.environ[config.ENV_WORKSPACE] = str(other)
        self.assertEqual(config.find(root), other)

    def test_toolbox_workspace_without_a_config_fails(self):
        os.environ[config.ENV_WORKSPACE] = str(self.tmp)
        with self.assertRaises(config.NotFound) as caught:
            config.find(self.tmp)
        self.assertIn(config.ENV_WORKSPACE, caught.exception.errors[0])

    def test_no_config_found(self):
        start = self.tmp / "nowhere"
        (start / ".claude").mkdir(parents=True)
        with self.assertRaises(config.NotFound) as caught:
            config.load(start)
        self.assertIn("/setup-workspace", caught.exception.errors[0])

    def test_invalid_yaml_names_the_file(self):
        root = self.tmp / "ws"
        (root / ".claude").mkdir(parents=True)
        (root / config.CONFIG_PATH).write_text("version: [1\n", encoding="utf-8")
        with self.assertRaises(config.ConfigError) as caught:
            config.load_workspace(root)
        self.assertEqual(caught.exception.path, root / config.CONFIG_PATH)


class ValidateTest(Workspace):
    def test_fixtures_are_valid(self):
        for fixture in ("single", "nested"):
            with self.subTest(fixture):
                self.assertEqual(self.errors(self.raw(fixture)), [])

    def test_bad_version(self):
        for version, expected in ((2, "version: must be 1; got 2"), ("1", "version: must be 1; got '1'"),
                                  (True, "version: must be 1; got True"), (None, "version: required")):
            with self.subTest(version):
                raw = {**self.raw("single"), "version": version}
                self.assertEqual(self.errors(raw), [expected])

    def test_unknown_key_names_its_dotted_path(self):
        raw = self.raw("single")
        raw["workspace"]["layuot"] = "single"
        raw["pr"]["announce"] = {"channel": "C1", "emoji": "tada"}
        raw["colour"] = "blue"
        self.assertEqual(sorted(self.errors(raw)), [
            "colour: unknown key", "pr.announce.emoji: unknown key", "workspace.layuot: unknown key"])

    def test_unknown_area_command(self):
        raw = self.raw("single")
        raw["workspace"]["areas"]["pantry-web"]["lint"] = "npm run lint"
        self.assertEqual(self.errors(raw), ["workspace.areas.pantry-web.lint: unknown key"])

    def test_wrong_types(self):
        raw = self.raw("single")
        raw["pr"]["required"] = "yes"
        raw["services"] = {"agent_lock_minutes": True}
        raw["features"] = ["tasks", 3]
        raw["extensions"] = {"disabled": "one.md"}
        self.assertEqual(sorted(self.errors(raw)), [
            "extensions.disabled: must be a list",
            "features[1]: must be a string",
            "pr.required: must be true or false, got 'yes'",
            "services.agent_lock_minutes: must be an integer, got True",
        ])

    def test_choices(self):
        raw = self.raw("single")
        raw["workspace"]["layout"] = "flat"
        self.assertEqual(self.errors(raw), ["workspace.layout: must be one of single, nested; got 'flat'"])

    def test_missing_required_keys(self):
        raw = self.raw("single")
        del raw["user"]["handle"]
        del raw["vault"]
        self.assertEqual(sorted(self.errors(raw)), ["user.handle: required", "vault.path: required"])

    def test_tracker_needs(self):
        raw = self.raw("single")
        raw["tracker"] = {"kind": "jira"}
        self.assertEqual(self.errors(raw), ["tracker.jira: required when tracker.kind is jira"])
        raw["tracker"] = {"kind": "vault"}
        self.assertEqual(self.errors(raw), ["tracker.key_prefix: required when tracker.kind is vault"])
        raw["tracker"] = {"kind": "jira", "jira": {"site": "example.atlassian.net"}}
        self.assertEqual(sorted(self.errors(raw)), [
            "tracker.jira.account: required", "tracker.jira.cloud_id: required", "tracker.jira.project: required"])

    def test_branch_placeholders(self):
        raw = self.raw("single")
        raw["git"] = {"branch": "{handle}/{ticket}"}
        self.assertEqual(self.errors(raw), [
            "git.branch: unknown placeholder {ticket}; use {handle}, {key} or {slug}",
            "git.branch: must contain {slug}",
        ])

    def test_paths_must_be_relative(self):
        raw = self.raw("single")
        raw["workspace"]["worktrees"] = "~/worktrees"
        raw["extensions"] = {"disabled": ["../extension.md"]}
        self.assertEqual(sorted(self.errors(raw)), [
            "extensions.disabled[0]: must be a doc's file name in .claude/extensions/, like sonar-scan.md; got "
            "'../extension.md'",
            "workspace.worktrees: must be relative to the workspace root; got '~/worktrees'",
        ])

    def test_the_old_per_skill_listing_is_refused(self):
        raw = self.raw("single")
        raw["extensions"] = {"work-task": [".claude/extensions/release-wait.md"]}
        self.assertEqual(self.errors(raw), ["extensions.work-task: unknown key"])

    def test_wip_owners(self):
        raw = self.raw("single")
        raw["work"] = {"wip": {"sam": 2}}
        self.assertEqual(self.errors(raw), ["work.wip.sam: unknown owner; use claude or jordan"])

    def test_defaults(self):
        data, _ = config.validate(self.raw("single"))
        self.assertEqual(data["vault"]["tasks"], "Tasks")
        self.assertEqual(data["git"]["branch"], "{handle}/{key}-{slug}")
        self.assertEqual(data["pr"]["merge"], "squash")
        self.assertIsNone(data["pr"]["announce"])
        self.assertIsNone(data["tracker"]["jira"])
        self.assertEqual(data["services"], {"agent_lock_minutes": 30, "manual_lock_minutes": 60})
        self.assertEqual(data["work"]["wip"], {"claude": 5, "jordan": 3})
        self.assertIsNone(data["workspace"]["areas"]["pantry-web"]["start"])
        self.assertEqual(data["workspace"]["areas"]["pantry-web"]["files"], [])
        self.assertEqual(data["pr"]["bots"], [])
        self.assertEqual(data["work"]["dependencies"], "isolate")

    def test_work_task_keys(self):
        raw = self.raw("single")
        raw["workspace"]["areas"]["pantry-web"]["files"] = "*.ts"
        raw["pr"]["bots"] = ["scanner", 4]
        raw["work"] = {"dependencies": "wait"}
        self.assertEqual(sorted(self.errors(raw)), [
            "pr.bots[1]: must be a string",
            "work.dependencies: must be one of isolate, block; got 'wait'",
            "workspace.areas.pantry-web.files: must be a list",
        ])


    def test_shared_libraries(self):
        raw = self.raw("nested")
        self.assertEqual(config.validate(raw)[0]["shared_libraries"], {"names": [], "update": None})
        raw["shared_libraries"] = {"names": ["common"]}
        self.assertEqual(self.errors(raw), ["shared_libraries.update: required when shared_libraries.names lists any "
                                            "(how consumers pick up a release)"])
        raw["shared_libraries"]["update"] = {"command": "make bump", "skill": "bump-deps"}
        self.assertEqual(self.errors(raw), ["shared_libraries.update: give command or skill, not both"])
        raw["shared_libraries"] = {"names": ["common", "common"], "update": {"command": "bump {libraries} {version}"}}
        self.assertEqual(sorted(self.errors(raw)), [
            "shared_libraries.names: lists a name twice",
            "shared_libraries.update.command: unknown placeholder {version}; use {libraries}",
        ])
        raw["shared_libraries"] = {"names": ["common"], "update": {"skill": "bump-deps"}}
        self.assertEqual(self.errors(raw), [])
        single = self.raw("single")
        single["shared_libraries"] = {"names": ["pantry-web", "pantry-lib"], "update": {"command": "npm update"}}
        self.assertEqual(self.errors(single), ["shared_libraries.names: 'pantry-lib' isn't an area under "
                                               "workspace.areas (a single workspace's shared libraries are its areas)"])

    def test_extra_fields(self):
        raw = self.raw("single")
        raw["work"] = {"extra_fields": {"pending_release": "text", "risk": ["low", "high"]}}
        self.assertEqual(self.errors(raw), [])
        raw["work"] = {"extra_fields": {"Pending": "text", "stage": "text", "size": "number", "risk": [],
                                        "level": ["a", "a", ""], "note": None}}
        self.assertEqual(sorted(self.errors(raw)), [
            "work.extra_fields.Pending: a field name is lowercase letters, digits and _, like pending_release",
            "work.extra_fields.level: lists a value twice",
            "work.extra_fields.level[2]: must be a non-empty string",
            "work.extra_fields.note: must be text or a list of allowed values",
            "work.extra_fields.risk: an empty list allows nothing; list the values, or use text",
            "work.extra_fields.size: must be text or a list of allowed values; got 'number'",
            "work.extra_fields.stage: a built-in task-record field; pick another name",
        ])
        raw["work"] = {"extra_fields": {"flag": 3}}
        self.assertEqual(self.errors(raw), ["work.extra_fields.flag: must be a string or a list, got 3"])


class AccessorTest(Workspace):
    def test_single(self):
        root = self.make("single")
        loaded = config.load(root)
        self.assertFalse(loaded.nested)
        self.assertEqual(loaded.scripts_dir, root / ".claude/toolbox/bin")
        self.assertEqual(loaded.tasks_dir, root / "vault/Tasks")
        self.assertEqual(loaded.worktrees_dir, root / ".worktrees")
        self.assertEqual(loaded.owner, "jordan")
        self.assertEqual(loaded.key_prefix, "PT")
        self.assertIsNone(loaded.jira)
        self.assertEqual(loaded.areas(), ["pantry-app", "pantry-web"])
        self.assertEqual(loaded.branch("add-shelves", "PT-4"), "jdoe/PT-4-add-shelves")
        self.assertEqual(loaded.branch("add-shelves"), "jdoe/add-shelves")
        self.assertEqual(loaded.extensions("work-task"), [])  # no .claude/extensions/ yet
        self.add_docs(root)
        self.assertEqual(loaded.disabled_extensions, ["old-release-wait.md"])
        self.assertEqual(loaded.extensions("work-task"), [root / ".claude/extensions/release-wait.md"])
        self.assertEqual(loaded.extensions("verify-change"), [root / ".claude/extensions/a-verify-checks.md"])
        self.assertEqual(loaded.extensions("run-unit-tests"), [])
        self.assertTrue(loaded.has("pr"))
        self.assertFalse(loaded.has("services-lock"))
        self.assertEqual(loaded.get("pr.announce"), None)
        with self.assertRaises(KeyError) as caught:
            loaded.get("pr.annouce.channel")
        self.assertEqual(caught.exception.args[0], "pr.annouce")

    def test_nested(self):
        root = self.make("nested")
        loaded = config.load(root)
        self.assertTrue(loaded.nested)
        self.assertEqual(loaded.vault_dir, Path.home() / "Notes/Work")
        self.assertEqual(loaded.key_prefix, "FLT")
        self.assertEqual(loaded.jira["mcp_server"], "atlassian")
        self.assertEqual(loaded.data["work"]["wip"], {"jordan": 2, "claude": 5})

    def test_star_area_is_the_fallback(self):
        root = self.make("nested")
        for repo in ("billing", "search"):
            (root / repo / ".git").mkdir(parents=True)
        (root / "notes").mkdir()
        (root / ".worktrees" / "FLT-2" / ".git").mkdir(parents=True)
        loaded = config.load(root)
        self.assertEqual(loaded.areas(), ["billing", "search"])
        self.assertEqual(loaded.command("billing", "unit"), "make unit-fast")
        self.assertEqual(loaded.command("billing", "format"), "make format")
        self.assertEqual(loaded.command("search", "integration"), "make it")
        self.assertIsNone(loaded.command("search", "start"))
        with self.assertRaises(ValueError):
            loaded.command("search", "lint")

    def test_area_files_fall_back_to_the_star_area(self):
        root = self.make("nested")
        raw = yaml.safe_load((root / config.CONFIG_PATH).read_text())
        raw["workspace"]["areas"]["*"]["files"] = ["*.java", "pom.xml"]
        raw["workspace"]["areas"]["billing"] = {"files": ["src/*"]}
        (root / config.CONFIG_PATH).write_text(yaml.safe_dump(raw))
        loaded = config.load(root)
        self.assertEqual(loaded.area_files("billing"), ["src/*"])
        self.assertEqual(loaded.area_files("search"), ["*.java", "pom.xml"])
        self.assertEqual(config.load(self.make("single", "other")).area_files("pantry-app"), [])

    def test_no_star_area(self):
        loaded = config.load(self.make("single"))
        self.assertEqual(loaded.command("pantry-app", "start"), "docker compose up -d")
        self.assertIsNone(loaded.command("pantry-web", "format"))


class RenderTest(Workspace):
    def test_single(self):
        root = self.make("single")
        rule = config.render_rule(config.load(root))
        self.assertLess(len(rule.splitlines()), 40)
        self.assertIn("edits here are lost", rule.splitlines()[2])
        for expected in (
            f"`pantry` at `{root}`, one repo; default branch `master`",
            f"**Scripts:** `{root}/.claude/toolbox/bin`",
            f"**Vault:** `{root}/vault`; task records in `Tasks/`",
            "**Task owners:** `claude` or `jordan`",
            "**Tracker:** `vault`. The task record is the ticket; keys are `PT-<n>`",
            "**Branches:** `jdoe/{key}-{slug}`, dropping",
            "**Pull requests:** optional; a change may go straight to `master`. An opened one is squash-merged with "
            "no review required, once CI passes.",
            "**Slack:** no pull request announcements.",
            "**Features:** `tasks`, `worktrees`, `pr`.",
        ):
            self.assertIn(expected, rule)
        self.assertNotIn("mcp__", rule)

    def test_nested(self):
        root = self.make("nested")
        rule = config.render_rule(config.load(root))
        self.assertLess(len(rule.splitlines()), 40)
        for expected in (
            f"`fleet` at `{root}`, a folder of repos in GitHub org `example-org`; default branch `master`",
            f"**Vault:** `{Path.home()}/Notes/Work`",
            "**Tracker:** `jira`, project `FLT` on `example.atlassian.net` (cloud id "
            "`00000000-0000-0000-0000-000000000000`), keys `FLT-<n>`. Writes back nothing.",
            "**Jira tools:** `mcp__atlassian__<tool>`. Before any write, confirm the signed-in account is "
            "`jdoe@example.com`",
            "**Pull requests:** every change goes through one, squash-merged after an approving review, once CI "
            "passes.",
            "**Slack:** announce each new pull request in channel `C00000000`, with `mcp__slack__<tool>`.",
        ):
            self.assertIn(expected, rule)

    def test_shared_libraries_and_extra_fields_lines_only_when_set(self):
        root = self.make("nested")
        rule = config.render_rule(config.load(root))
        self.assertNotIn("Shared libraries", rule)
        self.assertNotIn("Extra task fields", rule)
        raw = self.raw("nested")
        raw["shared_libraries"] = {"names": ["common"], "update": {"skill": "bump-deps"}}
        raw["work"]["extra_fields"] = {"pending_release": "text", "risk": ["low", "high"]}
        rule = config.render_rule(config.load(self.write(raw, "other")))
        self.assertIn("- **Shared libraries:** `common`. A task changing one runs `work-task`'s shared-library cycle, "
                      "then its consumers cycle; consumers pick up a release with the `bump-deps` skill on each "
                      "consumer checkout.", rule)
        self.assertIn("- **Extra task fields:** `pending_release` (text); `risk` (`low`, `high`).", rule)
        loaded = config.load(root / ".." / "other")
        self.assertEqual((loaded.shared_libraries, loaded.library_update), (["common"], {"skill": "bump-deps"}))

    def test_workspace_argument_moves_the_paths(self):
        loaded = config.load(self.make("single"))
        elsewhere = self.tmp / "elsewhere"
        self.assertIn(f"**Scripts:** `{elsewhere}/.claude/toolbox/bin`", config.render_rule(loaded, elsewhere))


class ExampleTest(unittest.TestCase):
    def test_example_lists_every_schema_key(self):
        example = yaml.safe_load((TOOLBOX / "templates/toolbox.local.example.yml").read_text(encoding="utf-8"))
        self.assertEqual(keys_in(example, config.SCHEMA), schema_keys(config.SCHEMA))


def schema_keys(spec, path: str = "") -> set[str]:
    """Every key path the schema allows; a free-form map's names collapse to `*`."""
    found = set()
    if isinstance(spec, config.Section):
        for key, sub in spec.fields.items():
            found.add(config._join(path, key))
            found |= schema_keys(sub, config._join(path, key))
    elif isinstance(spec, config.MapOf):
        found |= schema_keys(spec.value, f"{path}.*")
    return found


def keys_in(raw, spec, path: str = "") -> set[str]:
    """Every key path present in a config, collapsed the same way."""
    found = set()
    if isinstance(spec, config.Section) and isinstance(raw, dict):
        for key, value in raw.items():
            found.add(config._join(path, key))
            found |= keys_in(value, spec.fields.get(key), config._join(path, key))
    elif isinstance(spec, config.MapOf) and isinstance(raw, dict):
        for value in raw.values():
            found |= keys_in(value, spec.value, f"{path}.*")
    return found


class ExtensionsTest(Workspace):
    def test_parse(self):
        text = "\n".join([
            "---", "type: extension", "extends: work-task", "---", "", "# Release Wait", "", "Context.", "",
            "## add review-checks", "", "Run the scan.", "", "### Detail", "More.", "",
            "```markdown", "## Not a section", "```", "",
            "## replace needs-a-release", "Wait for the release.",
            "## skip verify", "CI verifies.",
            "## Notes", "Stray.",
            "## add Review-Checks", "Uppercase.",
            "## replace plan-the-cycles", "",
            "## add review-checks", "Again.",
        ])
        doc = extensions.parse(text, "release-wait.md")
        self.assertEqual(doc.extends, "work-task")
        self.assertEqual(doc.name, "release-wait.md")
        self.assertEqual([(s.verb, s.hook, s.line) for s in doc.sections], [
            ("add", "review-checks", 10), ("replace", "needs-a-release", 21), ("skip", "verify", 23),
            ("replace", "plan-the-cycles", 29), ("add", "review-checks", 31)])
        self.assertIn("### Detail", doc.sections[0].body)
        self.assertIn("## Not a section", doc.sections[0].body)
        self.assertEqual(doc.sections[2].body, "CI verifies.")
        self.assertEqual(doc.problems, [
            "line 25: '## Notes' isn't `## <add|replace|skip> <hook id>`",
            "line 27: '## add Review-Checks' isn't `## <add|replace|skip> <hook id>`",
            "line 29: `## replace plan-the-cycles` is empty; say what to do",
            "line 31: `## add review-checks` repeats line 10; put both in one section",
        ])

    def test_frontmatter(self):
        cases = {
            "## add x\n\ny\n": ["no frontmatter; start the doc with `type: extension` and `extends: <skill>` "
                                 "between --- lines"],
            "---\ntype: extension\n## add x\n\ny\n": ["frontmatter never closes"],
            "---\ntype: note\nextends: work-task\n---\n": ["frontmatter: type must be extension; got 'note'"],
            "---\ntype: extension\n---\n": ["frontmatter: no extends; name the one skill this doc extends"],
            "---\ntype: extension\nextends: [work-task, verify-change]\n---\n": [
                "frontmatter: extends must be one skill's name, like work-task; got ['work-task', 'verify-change']"],
        }
        for text, problems in cases.items():
            with self.subTest(text):
                doc = extensions.parse(text)
                self.assertEqual(doc.problems, problems)
                self.assertIsNone(doc.extends)
        # A `##` line in the frontmatter's YAML isn't a section.
        doc = extensions.parse("---\ntype: extension\nextends: work-task\n---\n\n## skip format\n\nNo formatter.\n")
        self.assertEqual((doc.problems, [(s.verb, s.hook, s.line) for s in doc.sections]),
                         ([], [("skip", "format", 6)]))

    def test_points(self):
        text = "\n".join([
            "# Skill", "", "## Steps", "", "| Id | Runs | Default | Verbs |", "|---|---|---|---|",
            "| `not-this` | a | b | add |", "", "## Extension Points", "", "Intro.", "",
            "| Id | Runs | Default | Verbs |", "|---|---|---|---|",
            "| `format` | step 3 | `run_tests.py format` | add, skip |",
            "| `plan-the-cycles` | step 4, with a \\| pipe | \"### Plan the cycles\" | add, replace |",
            "| `format` | again | x | add |",
            "| Bad Id | x | y | add |",
            "| `odd` | x | y | add, remove |",
            "| `short` | x | add |",
            "", "Prose after the table.", "", "| `later` | x | y | add |", "",
            "### Plan the cycles", "", "Default text.",
        ])
        points, problems = extensions.points(text)
        self.assertEqual([(p.id, p.verbs) for p in points],
                         [("format", ("add", "skip")), ("plan-the-cycles", ("add", "replace"))])
        self.assertEqual(points[0].default, "`run_tests.py format`")
        self.assertEqual(problems, [
            "line 17: hook 'format' is declared twice",
            "line 18: 'Bad Id' isn't a backticked id of lowercase words joined by -",
            "line 19: verbs must be from add, replace, skip; got 'add, remove'",
            "line 20: a row has 3 cells, not 4",
        ])
        self.assertEqual(extensions.points("# Skill\n\n## Workflow\n"), ([], []))
        self.assertEqual(extensions.points("## Extension Points\n\nNone yet.\n"),
                         ([], ["## Extension Points has no table of hooks"]))
        self.assertEqual(extensions.points("## Extension Points\n\n| Id | When |\n|---|---|\n")[1],
                         ["line 3: the table's columns must be Id | Runs | Default | Verbs"])

    def test_docs_and_discover(self):
        root = self.make("single")
        self.assertEqual(extensions.docs(root), [])
        self.add_docs(root)
        folder = root / extensions.FOLDER
        self.assertEqual([p.name for p in extensions.docs(root)],
                         ["a-verify-checks.md", "old-release-wait.md", "release-wait.md"])
        self.assertEqual(extensions.discover(root, "work-task"),
                         [folder / "old-release-wait.md", folder / "release-wait.md"])
        for disabled in (["old-release-wait"], ["old-release-wait.md"]):
            self.assertEqual(extensions.discover(root, "work-task", disabled), [folder / "release-wait.md"])
        (folder / "broken.md").write_text("## add dispatch\n\nno frontmatter\n")
        self.assertEqual(extensions.discover(root, "work-task", ["old-release-wait"]), [folder / "release-wait.md"])


class CliTest(Workspace):
    def run_cli(self, cwd: Path, *args: str) -> subprocess.CompletedProcess:
        env = {key: value for key, value in os.environ.items() if key != config.ENV_WORKSPACE}
        return subprocess.run([sys.executable, str(TOOLBOX / "bin/toolbox.py"), "config", *args], cwd=cwd, env=env,
                              capture_output=True, text=True, timeout=30)

    def test_render_is_under_forty_lines_for_each_fixture(self):
        for fixture in ("single", "nested"):
            with self.subTest(fixture):
                result = self.run_cli(self.make(fixture, fixture), "render")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertLess(len(result.stdout.splitlines()), 40)
                self.assertTrue(result.stdout.startswith("# Toolbox Config\n"))

    def test_show_and_validate(self):
        root = self.make("nested")
        self.assertEqual(json.loads(self.run_cli(root, "show", "tracker.jira.project").stdout), "FLT")
        self.assertEqual(json.loads(self.run_cli(root, "show").stdout)["version"], 1)
        self.assertEqual(json.loads(self.run_cli(root, "validate").stdout),
                         {"ok": True, "config": str(root / config.CONFIG_PATH)})
        missing = self.run_cli(root, "show", "tracker.jira.nothing")
        self.assertEqual(missing.returncode, 2)
        self.assertEqual(json.loads(missing.stdout)["errors"], ["tracker.jira.nothing: no such key"])

    def test_extensions(self):
        root = self.make("single")
        self.assertEqual(json.loads(self.run_cli(root, "extensions", "work-task").stdout), [])
        self.add_docs(root)
        self.assertEqual(json.loads(self.run_cli(root, "extensions", "work-task").stdout),
                         [str(root / ".claude/extensions/release-wait.md")])
        self.assertEqual(json.loads(self.run_cli(root, "extensions", "git-commit").stdout), [])

    def test_render_points_at_the_extensions_folder(self):
        root = self.make("single")
        rule = self.run_cli(root, "render").stdout
        self.assertIn(f"- **Extensions:** a project's steps hooked into these skills, as docs in "
                      f"`{root / '.claude/extensions'}`; its `README.md` says how to write one.\n", rule)

    def test_invalid_config(self):
        raw = yaml.safe_load((FIXTURES / "single.yml").read_text(encoding="utf-8"))
        raw["workspace"]["layuot"] = "single"
        root = self.write(raw)
        result = self.run_cli(root / ".claude", "validate")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["errors"], ["workspace.layuot: unknown key"])

    def test_no_config(self):
        result = self.run_cli(self.tmp, "render")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(json.loads(result.stdout)["ok"])


if __name__ == "__main__":
    unittest.main()
