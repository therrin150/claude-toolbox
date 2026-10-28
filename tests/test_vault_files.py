"""Tests for install.py's vault files (a per-workspace domain's `.vault/` folder, written into each workspace's vault)
and for the toolbox's own vault templates, run under a stand-in for Templater and filed with tasks.py.

Run from the repo root:

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_install import CONFIG, REPO, InstallerTest, install, write  # noqa: E402

TEMPLATE = Path("Templates/discussion-plan.md")
SCRIPT = Path("Templates/scripts/picker.js")
TOOLBOX = REPO / "workspace" / ".claude" / "toolbox"
TASKS = TOOLBOX / "bin" / "tasks.py"
FAKE_TEMPLATER = Path(__file__).resolve().parent / "fake_templater.js"
VAULT_SOURCE = REPO / "workspace" / ".vault"
TODAY = datetime.date.today().isoformat()


def vault_config(name: str, features: list, vault: str = "vault", tracker: dict | None = None) -> str:
    return yaml.safe_dump({
        "version": 1,
        "features": features,
        "workspace": {"name": name, "layout": "single"},
        "user": {"handle": "tester", "name": "Tester"},
        "vault": {"path": vault, "discussions": "Notes/Discussions"},
        "tracker": tracker or {"kind": "vault", "key_prefix": "PT"},
    })


class VaultFiles(InstallerTest):
    """The base fixture, plus a `.vault/` folder gated on `obsidian`: a template and a plain script. ws1's vault is
    outside it, ws2's (a git repo) inside it, both with `obsidian`."""

    def setUp(self):
        super().setUp()
        self.source = self.repo / "workspace"
        write(self.source / ".vault" / "Templates" / "discussion-plan.md.tmpl",
              'const DIR = "{{ vault.discussions }}";\n')
        write(self.source / ".vault" / "Templates" / "scripts" / "picker.js", "module.exports = () => 1;\n")
        write(self.source / "features.yml", yaml.safe_dump({
            "services-lock": [".claude/skills/lock-local-services"],
            "obsidian": [".vault"],
        }))
        self.vault1 = self.tmp / "elsewhere" / "Notes"
        self.vault1.mkdir(parents=True)
        self.vault2 = self.ws2 / "vault"
        self.vault2.mkdir()
        write(self.ws1 / CONFIG, vault_config("one", ["obsidian"], str(self.vault1)))
        write(self.ws2 / CONFIG, vault_config("two", ["obsidian"]))

    def test_written_into_each_vault(self):
        code, plan = self.run_install()
        self.assertEqual(code, 0, plan)
        written = self.section(plan, "Write generated rules")
        self.assertIn(f"{self.vault1 / TEMPLATE} (new, rendered from discussion-plan.md.tmpl)", written)
        self.assertIn(f"{self.vault1 / SCRIPT} (new, copied from picker.js)", written)
        self.assertIn(f"{self.vault2 / TEMPLATE} (new, rendered from discussion-plan.md.tmpl)", written)
        self.assertIn(str(self.vault1 / "Templates"), self.section(plan, "Create folders"))
        self.assertFalse(any(".vault" in line for line in self.section(plan, "Create links")))

        code, plan = self.run_install("--apply")
        self.assertEqual(code, 0, plan)
        self.assertEqual((self.vault1 / TEMPLATE).read_text(), 'const DIR = "Notes/Discussions";\n')
        self.assertFalse((self.vault1 / SCRIPT).is_symlink())
        self.assertEqual((self.vault1 / SCRIPT).read_text(), "module.exports = () => 1;\n")
        self.assertFalse((self.ws1 / ".vault").exists())
        self.assertFalse((self.ws1 / "Templates").exists())
        record = self.state()["generated"][install.norm(self.vault1 / SCRIPT)]
        self.assertEqual(record["template"], str(self.source / ".vault" / "Templates" / "scripts" / "picker.js"))

        exclude = (self.ws2 / ".git" / "info" / "exclude").read_text(encoding="utf-8")
        self.assertIn("\n/vault/Templates/discussion-plan.md\n", exclude)
        self.assertIn("\n/vault/Templates/scripts/picker.js\n", exclude)
        self.assertNotIn("..", exclude)
        status = subprocess.run(["git", "-C", str(self.ws2), "status", "--porcelain"], capture_output=True,
                                text=True, check=True).stdout
        self.assertEqual(status, "")

        _, plan = self.run_install()
        self.assertEqual(self.section(plan, "Write generated rules"), [])

    def test_a_changed_source_or_vault_edit_is_rewritten(self):
        self.run_install("--apply")
        write(self.source / ".vault" / "Templates" / "scripts" / "picker.js", "module.exports = () => 2;\n")
        write(self.vault2 / TEMPLATE, "edited in the vault\n")
        _, plan = self.run_install()
        written = self.section(plan, "Write generated rules")
        self.assertIn(f"{self.vault1 / SCRIPT} (changed, copied from picker.js)", written)
        self.assertIn(f"{self.vault2 / TEMPLATE} (changed, rendered from discussion-plan.md.tmpl)", written)
        self.run_install("--apply")
        self.assertEqual((self.vault1 / SCRIPT).read_text(), "module.exports = () => 2;\n")
        self.assertEqual((self.vault2 / TEMPLATE).read_text(), 'const DIR = "Notes/Discussions";\n')

    def test_feature_gate_and_teardown(self):
        self.run_install("--apply")
        write(self.ws1 / CONFIG, vault_config("one", [], str(self.vault1)))
        _, plan = self.run_install()
        self.assertEqual(sorted(self.section(plan, "Remove generated rules")),
                         sorted([str(self.vault1 / TEMPLATE), str(self.vault1 / SCRIPT)]))
        self.run_install("--apply")
        self.assertFalse((self.vault1 / "Templates").exists())  # folders the installer made go when empty
        self.assertTrue(self.vault1.is_dir())
        self.assertTrue((self.vault2 / SCRIPT).is_file())

        self.run_install("--teardown", "--apply")
        self.assertFalse((self.vault2 / "Templates").exists())
        self.assertTrue(self.vault2.is_dir())
        self.assertEqual(self.state()["generated"], {})

    def test_existing_vault_folders_are_kept(self):
        write(self.vault1 / "Templates" / "Mine.md", "# Mine\n")
        self.run_install("--apply")
        self.run_install("--teardown", "--apply")
        self.assertEqual(sorted(p.name for p in (self.vault1 / "Templates").iterdir()), ["Mine.md"])

    def test_a_missing_vault_is_a_problem(self):
        shutil.rmtree(self.vault1)
        code, plan = self.run_install()
        self.assertEqual(code, 1)
        self.assertIn(f"workspace {self.ws1}: vault {self.vault1} doesn't exist, so nothing is written there",
                      self.section(plan, "Problems"))
        self.assertIn(f"{self.vault2 / TEMPLATE} (new, rendered from discussion-plan.md.tmpl)",
                      self.section(plan, "Write generated rules"))

    def test_a_file_already_there_is_adopted_only_when_identical(self):
        write(self.vault1 / SCRIPT, "module.exports = () => 1;\n")
        write(self.vault1 / TEMPLATE, "# A hand-made template\n")
        code, plan = self.run_install("--apply")
        self.assertEqual(code, 1)
        self.assertEqual(self.section(plan, "Conflicts, left alone"), [
            f"[not-generated] {self.vault1 / TEMPLATE}: a file the installer didn't generate is where a generated "
            "file goes"])
        self.assertIn(install.norm(self.vault1 / SCRIPT), self.state()["generated"])
        self.assertEqual((self.vault1 / TEMPLATE).read_text(), "# A hand-made template\n")

    def test_tasks_feature_makes_the_tasks_and_discussions_folders(self):
        write(self.ws1 / CONFIG, vault_config("one", ["tasks"], str(self.vault1)))
        _, plan = self.run_install()
        made = self.section(plan, "Create folders")
        for folder in ("Tasks", "Notes", "Notes/Discussions"):
            self.assertIn(str(self.vault1 / folder), made)
        self.assertNotIn(str(self.vault2 / "Tasks"), made)  # ws2 has no `tasks` feature
        self.run_install("--apply")
        self.assertTrue((self.vault1 / "Notes" / "Discussions").is_dir())
        write(self.vault1 / "Tasks" / "PT-1-shelf-labels.md", "---\ntype: story\n---\n")

        # Dropping the feature, or a teardown, removes only the folders left empty: never a task record.
        write(self.ws1 / CONFIG, vault_config("one", [], str(self.vault1)))
        self.run_install("--apply")
        self.assertFalse((self.vault1 / "Notes").exists())
        self.assertTrue((self.vault1 / "Tasks" / "PT-1-shelf-labels.md").is_file())
        write(self.ws1 / CONFIG, vault_config("one", ["tasks"], str(self.vault1)))
        self.run_install("--apply")
        self.run_install("--teardown", "--apply")
        self.assertTrue((self.vault1 / "Tasks" / "PT-1-shelf-labels.md").is_file())

    def test_tasks_feature_with_a_missing_vault_is_a_problem(self):
        write(self.ws1 / CONFIG, vault_config("one", ["tasks", "obsidian"], str(self.vault1)))
        shutil.rmtree(self.vault1)
        code, plan = self.run_install()
        self.assertEqual(code, 1)
        problem = f"workspace {self.ws1}: vault {self.vault1} doesn't exist, so nothing is written there"
        self.assertEqual(self.section(plan, "Problems").count(problem), 1)

    def test_the_vault_folder_is_never_linked_into_the_workspace(self):
        write(self.ws1 / CONFIG, vault_config("one", [], str(self.vault1)))
        _, plan = self.run_install("--apply")
        self.assertFalse(any(".vault" in line for line in plan.splitlines()))


def fields_of(text: str) -> list:
    """A note's frontmatter keys, in order, read line by line (the toolbox's templates hold placeholders YAML can't
    parse)."""
    block = text.split("---\n")[1]
    return [line.split(":")[0] for line in block.splitlines() if re.match(r"^[a-z_]+:", line)]


def headings(text: str) -> list:
    return re.findall(r"^## (.+)$", text, re.MULTILINE)


@unittest.skipUnless(shutil.which("node"), "node isn't installed")
class DiscussionTemplate(unittest.TestCase):
    """The toolbox's discussion template, rendered from a workspace's config, run under fake_templater.js in a
    scratch vault, and the discussion it makes filed by tasks.py as create-tickets would."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.ws = self.tmp / "ws"
        self.vault = self.ws / "vault"
        self.use({"kind": "vault", "key_prefix": "PT"})

    def use(self, tracker: dict) -> None:
        write(self.ws / CONFIG, vault_config("pantry", ["tasks", "obsidian"], tracker=tracker))
        sys.path.insert(0, str(TOOLBOX / "lib"))
        import config  # noqa: PLC0415
        import templates  # noqa: PLC0415

        loaded = config.load_workspace(self.ws)
        source = VAULT_SOURCE / "Templates" / "discussion-plan.md.tmpl"
        write(self.vault / TEMPLATE, templates.render(source.read_text(encoding="utf-8"), loaded))
        scripts = self.vault / "Templates" / "scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        shutil.copy(VAULT_SOURCE / "Templates" / "scripts" / "epicSuggester.js", scripts / "epicSuggester.js")

    def run_template(self, *answers) -> dict:
        answer_file = self.tmp / "answers.json"
        answer_file.write_text(json.dumps(list(answers)), encoding="utf-8")
        proc = subprocess.run(["node", str(FAKE_TEMPLATER), str(self.vault / TEMPLATE), str(self.vault),
                               str(answer_file), TODAY], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(result["answers_left"], 0, result)
        return result

    def discussion(self, slug: str) -> Path:
        year, month = TODAY.split("-")[:2]
        return self.vault / "Notes" / "Discussions" / year / month / f"{TODAY}-{slug}"

    def run_tasks(self, *args) -> dict:
        proc = subprocess.run([sys.executable, str(TASKS), *args], cwd=self.ws, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return json.loads(proc.stdout)

    def test_matches_the_toolbox_plan_template(self):
        self.run_template("Pantry Shelves", "none")
        plan = (self.discussion("pantry-shelves") / "plan.md").read_text(encoding="utf-8")
        toolbox_plan = (TOOLBOX / "templates" / "plan.md").read_text(encoding="utf-8")
        self.assertEqual(fields_of(plan), fields_of(toolbox_plan))
        self.assertEqual(headings(plan), headings(toolbox_plan))
        fields = yaml.safe_load(plan.split("---\n")[1])
        self.assertEqual(fields, {"type": "plan", "title": "Pantry Shelves", "slug": "pantry-shelves", "epic": None,
                                  "created": datetime.date.fromisoformat(TODAY), "status": "draft"})
        self.assertIn("## Proposed Epic\n\nNone.\n", plan)
        self.assertTrue((self.discussion("pantry-shelves") / "tickets").is_dir())

    def test_a_stubbed_epic_is_filed_by_tasks_py(self):
        result = self.run_template("Pantry Shelves", "new", None)
        folder = self.discussion("pantry-shelves")
        self.assertEqual(result["moved_to"], str((folder / "plan").relative_to(self.vault)))
        stub = folder / "tickets" / "00-pantry-shelves.md"
        plan = (folder / "plan.md").read_text(encoding="utf-8")
        self.assertIn(f"[[{stub.relative_to(self.vault).with_suffix('')}|Pantry Shelves]]: stubbed", plan)

        # What propose-tickets and Allen's approval add, then create-tickets' filing.
        stub.write_text(stub.read_text(encoding="utf-8").replace("status: draft", "status: approved"),
                        encoding="utf-8")
        write(folder / "tickets" / "01-add-shelf-labels.md", "---\ntype: story\nsummary: Add Shelf Labels\nepic:\n"
              "owner: claude\ndepends_on: []\nstatus: approved\n---\n\n# Add Shelf Labels\n\n## Description\n\nText.\n")
        discussion = str(folder.relative_to(self.vault))
        self.run_tasks("file", discussion, "--dry-run")
        self.run_tasks("file", discussion)
        plan = (folder / "plan.md").read_text(encoding="utf-8")
        self.assertIn("epic: PT-1\n", plan)
        self.assertIn("Filed as task records:", plan)
        self.assertNotIn("```dataview", plan)
        story = self.run_tasks("get", "PT-2")
        self.assertEqual(story["fields"]["epic"], "PT-1")

    def test_an_existing_epic_record_is_offered(self):
        write(self.vault / "Tasks" / "PT-7-pantry-shelves.md",
              "---\ntype: epic\nkey: PT-7\nsummary: Pantry Shelves\nstatus: todo\n---\n\n# Pantry Shelves\n")
        write(self.vault / "Tasks" / "PT-3-old-epic.md",
              "---\ntype: epic\nkey: PT-3\nsummary: Old Epic\nstatus: done\n---\n\n# Old Epic\n")
        self.run_template("Shelf Labels", "existing", "PT-7")
        plan = (self.discussion("shelf-labels") / "plan.md").read_text(encoding="utf-8")
        self.assertIn("epic: PT-7\n", plan)
        self.assertIn("[[Tasks/PT-7-pantry-shelves|PT-7]]: existing epic.", plan)

        # A done epic isn't offered: the suggester refuses it.
        answers = self.tmp / "answers.json"
        answers.write_text(json.dumps(["Other", "existing", "PT-3"]), encoding="utf-8")
        proc = subprocess.run(["node", str(FAKE_TEMPLATER), str(self.vault / TEMPLATE), str(self.vault),
                               str(answers), TODAY], capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("isn't offered", proc.stderr)

    def test_with_no_epic_records_the_key_is_typed(self):
        self.run_template("Shelf Labels", "existing", "ABC-40")
        plan = (self.discussion("shelf-labels") / "plan.md").read_text(encoding="utf-8")
        self.assertIn("epic: ABC-40\n", plan)
        self.assertIn("ABC-40: existing epic.", plan)

    def test_the_none_tracker_asks_no_epic(self):
        self.use({"kind": "none"})
        self.run_template("Shelf Labels")
        plan = (self.discussion("shelf-labels") / "plan.md").read_text(encoding="utf-8")
        self.assertIn("## Proposed Epic\n\nNone.\n", plan)

    def test_renders_under_every_tracker(self):
        self.use({"kind": "jira", "jira": {"site": "example.atlassian.net", "cloud_id": "0000", "project": "PT",
                                           "account": "tester@example.com"}})
        text = (self.vault / TEMPLATE).read_text(encoding="utf-8")
        self.assertIn('const TRACKER = "jira";', text)
        self.assertIn('const DISCUSSIONS = "Notes/Discussions";', text)
        self.assertNotIn("{{", text)

    def test_cancelling_writes_nothing(self):
        result = self.run_template(None)
        self.assertIsNone(result["output"])
        self.assertFalse((self.vault / "Notes").exists())


if __name__ == "__main__":
    unittest.main()
