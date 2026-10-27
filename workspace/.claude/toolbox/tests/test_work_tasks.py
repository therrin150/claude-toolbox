"""Walkthroughs of work-tasks: what `bin/dispatch.py plan` says the skill starts, resumes and reports, against a vault
fixture with tasks in every state (both WIP limits), and against named sources under each tracker (session titles and
prompts, nothing started and nothing written). Also checks that the skill's text names only scripts and commands that
exist and that it installs under the `dispatcher` feature.

Run from the repo root:

    python3 -m unittest discover -s workspace/.claude/toolbox/tests
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import yaml

TOOLBOX = Path(__file__).resolve().parents[1]
WORKSPACE_DOMAIN = TOOLBOX.parents[1]
SKILL = WORKSPACE_DOMAIN / ".claude" / "skills" / "work-tasks"
DISPATCH = TOOLBOX / "bin" / "dispatch.py"
DISCUSSION = "Discussions/2026/10/2026-10-27-shelf-counts"


def note(fields: dict, body: str = "") -> str:
    return "---\n" + yaml.safe_dump(fields, sort_keys=False) + "---\n\n" + textwrap.dedent(body).lstrip("\n")


class Fixture(unittest.TestCase):
    tracker = "vault"
    extra: dict = {}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.root = self.tmp / "ws"
        (self.root / ".claude").mkdir(parents=True)
        raw = {"version": 1, "features": ["tasks", "dispatcher"],
               "workspace": {"name": "pantry", "layout": "single"},
               "user": {"handle": "jdoe", "name": "Jordan"}, "vault": {"path": "vault"},
               "tracker": {"kind": self.tracker}}
        if self.tracker == "vault":
            raw["tracker"]["key_prefix"] = "PT"
        if self.tracker == "jira":
            raw["tracker"].update(key_prefix="PT", jira={
                "site": "example.atlassian.net", "cloud_id": "0000", "project": "PT", "account": "j@example.com"})
        raw.update(self.extra)
        (self.root / ".claude" / "toolbox.local.yml").write_text(yaml.safe_dump(raw), encoding="utf-8")
        self.vault = self.root / "vault"
        self.env = {k: v for k, v in os.environ.items() if k != "TOOLBOX_WORKSPACE"}

    def write(self, rel: str, fields: dict, body: str = "") -> Path:
        path = self.vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(note(fields, body), encoding="utf-8")
        return path

    def record(self, key: str, **fields) -> Path:
        fields = {"type": "story", "key": key, "summary": f"Task {key}", "status": "todo", "owner": "claude",
                  "depends_on": [], "created": f"2026-10-{int(key.split('-')[1]):02d}", **fields}
        slug = re.sub(r"[^a-z0-9]+", "-", fields["summary"].lower()).strip("-")
        return self.write(f"Tasks/{key}-{slug}.md", fields, "# x\n\n## Log\n")

    def sessions(self, *titles: str, archived: tuple = ()) -> str:
        rows = [{"sessionId": f"local_{n}", "title": title, "archived": title in archived}
                for n, title in enumerate(titles)]
        path = self.tmp / "sessions.json"
        path.write_text(json.dumps(rows))
        return str(path)

    def plan(self, *args: str, code: int = 0) -> dict:
        proc = subprocess.run([sys.executable, str(DISPATCH), "plan", *args], cwd=self.root, capture_output=True,
                              text=True, env=self.env)
        self.assertEqual(proc.returncode, code, proc.stdout + proc.stderr)
        return json.loads(proc.stdout)

    def groups(self, out: dict) -> dict:
        found: dict = {}
        for item in out["entries"]:
            found.setdefault(item["group"], []).append(item["work_id"])
        return found

    def snapshot(self) -> dict:
        return {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}


class QueueTest(Fixture):
    """The vault fixture: one task in every state, claude's limit 3 and the user's 2."""

    extra = {"work": {"wip": {"claude": 3, "jordan": 2}}}

    def setUp(self):
        super().setUp()
        self.record("PT-1", type="epic")
        self.record("PT-2", status="done")
        self.record("PT-3", status="in-progress", stage="testing")
        self.record("PT-4", status="in-progress", stage="implementing")
        self.record("PT-5", owner="jordan", status="review", stage="code-review", work_note="Check the diff")
        self.record("PT-6", depends_on=["PT-3"])
        for key in ("PT-7", "PT-8", "PT-9"):
            self.record(key)
        self.record("PT-10", status="stopped", work_note="Waiting on a design")
        self.live = self.sessions("PT-3 Task PT-3")
        self.before = self.snapshot()

    def test_every_group_and_both_limits(self):
        out = self.plan("--sessions", self.live)
        self.assertEqual(self.groups(out), {
            "waiting": ["PT-5"], "running": ["PT-3"], "resume": ["PT-4"], "start": ["PT-7"],
            "held": ["PT-8", "PT-9"], "blocked": ["PT-6"], "stopped": ["PT-10"]})
        # Claude has 2 in progress of 3: one new start. The user has 1 of 2: not at the limit.
        self.assertEqual((out["load"], out["capacity"], out["mode"]), ({"claude": 2, "jordan": 1}, 1, "queue"))
        self.assertEqual([i["work_id"] for i in out["launch"]], ["PT-4", "PT-7"])
        self.assertEqual([i["prompt"] for i in out["launch"]], ["/work-task PT-4", "/work-task PT-7"])
        self.assertEqual([i["title"] for i in out["launch"]], ["PT-4 Task PT-4", "PT-7 Task PT-7"])
        waiting = out["waiting_on_user"][0]
        self.assertEqual((waiting["work_id"], waiting["stage"], waiting["work_note"]),
                         ("PT-5", "code-review", "Check the diff"))
        blocked = next(i for i in out["entries"] if i["group"] == "blocked")
        self.assertIn("PT-3 (in-progress)", blocked["reason"])
        self.assertEqual(self.snapshot(), self.before)

    def test_user_limit_stops_new_starts_but_not_resumes(self):
        self.record("PT-11", owner="jordan")
        out = self.plan("--sessions", self.live)
        self.assertEqual(out["capacity"], 0)
        self.assertIn("nothing new starts", out["held"])
        self.assertEqual([i["work_id"] for i in out["launch"]], ["PT-4"])
        self.assertEqual(self.groups(out)["held"], ["PT-7", "PT-8", "PT-9"])
        self.assertEqual(sorted(self.groups(out)["waiting"]), ["PT-11", "PT-5"])

    def test_claude_limit_holds_starts(self):
        for key in ("PT-12", "PT-13"):
            self.record(key, status="in-progress")
        out = self.plan("--sessions", self.live)
        self.assertEqual(out["capacity"], 0)
        self.assertEqual(sorted(i["work_id"] for i in out["launch"]), ["PT-12", "PT-13", "PT-4"])
        self.assertTrue(all(i["group"] == "resume" for i in out["launch"]))
        self.assertIn("limit of 3", out["held"])

    def test_a_live_session_is_matched_by_the_whole_work_id_and_never_when_archived(self):
        sessions = self.sessions("PT-30 Another Task", "PT-3 Task PT-3", "PT-4 Task PT-4", archived=("PT-4 Task PT-4",))
        out = self.plan("--sessions", sessions)
        self.assertEqual(self.groups(out)["running"], ["PT-3"])
        self.assertIn("PT-4", [i["work_id"] for i in out["launch"]])
        running = next(i for i in out["entries"] if i["group"] == "running")
        self.assertEqual(running["session"], {"id": "local_1", "title": "PT-3 Task PT-3"})

    def test_a_ready_task_with_a_live_session_is_not_started_twice(self):
        out = self.plan("--sessions", self.sessions("PT-7 Task PT-7"))
        self.assertEqual(self.groups(out)["running"], ["PT-7"])
        self.assertNotIn("PT-7", [i["work_id"] for i in out["launch"]])

    def test_direct_starts_the_user_asked_way(self):
        out = self.plan("--sessions", self.live)
        self.assertEqual((out["dispatch"], out["initiation"], out["model"], out["cwd"]),
                         ("direct", "user_asked", "sonnet", str(self.root)))

    def test_no_sessions_file_means_nothing_is_running(self):
        out = self.plan()
        self.assertEqual([i["work_id"] for i in out["launch"]], ["PT-3", "PT-4", "PT-7"])
        self.assertNotIn("running", self.groups(out))


class ChipsTest(Fixture):
    extra = {"work": {"dispatch": "chips", "session_model": "haiku", "wip": {"claude": 6, "jordan": 3}}}

    def setUp(self):
        super().setUp()
        self.record("PT-1", status="in-progress")
        for n in range(2, 7):
            self.record(f"PT-{n}")

    def test_three_cards_a_turn_then_the_next_three(self):
        out = self.plan()
        self.assertEqual((out["initiation"], out["model"]), ("own_initiative", "haiku"))
        self.assertEqual([i["work_id"] for i in out["launch"]], ["PT-1", "PT-2", "PT-3"])
        self.assertEqual(self.groups(out)["next"], ["PT-4", "PT-5", "PT-6"])
        offered = self.plan("--offered", "PT-1", "PT-2", "PT-3")
        self.assertEqual([i["work_id"] for i in offered["launch"]], ["PT-4", "PT-5", "PT-6"])
        self.assertEqual(self.groups(offered)["offered"], ["PT-1", "PT-2", "PT-3"])

    def test_limits_can_be_changed_per_call(self):
        self.assertEqual(len(self.plan("--no-limit")["launch"]), 6)
        self.assertEqual(len(self.plan("--limit", "1")["launch"]), 1)


class NamedVaultTest(Fixture):
    """Named sources under the vault tracker: titles and prompts, no WIP limits, nothing started or written."""

    extra = {"work": {"wip": {"claude": 1, "jordan": 1}}}

    def setUp(self):
        super().setUp()
        self.record("PT-1", type="epic")
        self.record("PT-2", status="done")
        self.record("PT-3", summary="Add Shelf Labels")
        self.record("PT-4", status="review", owner="jordan", stage="publish-review")
        self.record("PT-5", summary="Count Every Item On A Shelf And Show The Running Total On The Shelf Screen")
        self.write(f"{DISCUSSION}/plan.md", {"type": "plan", "title": "Shelf Counts"}, "# Shelf Counts\n")
        self.before = self.snapshot()

    def test_titles_prompts_groups_and_no_writes(self):
        out = self.plan("3", "PT-4,PT-5", "PT-2", "PT-1", "PT-99", "PT-3", f"{DISCUSSION}/plan.md",
                        "--sessions", self.sessions("PT-5 Count Every Item"))
        by_source = {i.get("source"): i for i in out["entries"]}
        self.assertEqual(out["mode"], "named")
        self.assertEqual([i["title"] for i in out["launch"]], ["PT-3 Add Shelf Labels", "PT-4 Task PT-4"])
        self.assertEqual([i["prompt"] for i in out["launch"]], ["/work-task PT-3", "/work-task PT-4"])
        self.assertEqual(by_source["PT-4"]["reason"], "review, publish-review")
        self.assertEqual(by_source["PT-5"]["group"], "running")
        self.assertEqual(by_source["PT-2"]["group"], "done")
        self.assertIn("epic", by_source["PT-1"]["reason"])
        self.assertIn("no task record", by_source["PT-99"]["reason"])
        plan = by_source[f"{DISCUSSION}/plan.md"]
        self.assertEqual((plan["group"], plan["work_id"]), ("skipped", None))
        self.assertIn("key is assigned", plan["reason"])
        self.assertEqual(len(out["entries"]), 7)  # PT-3 named twice counts once
        self.assertEqual(self.snapshot(), self.before)

    def test_a_long_title_is_cut_to_sixty_characters(self):
        item = self.plan("PT-5")["launch"][0]
        self.assertIn(len(item["title"]), (59, 60))  # a cut that ends on a space drops it
        self.assertTrue(item["title"].startswith("PT-5 Count Every Item"))

    def test_a_task_record_path_and_a_bare_number(self):
        path = self.vault / "Tasks" / "PT-3-add-shelf-labels.md"
        out = self.plan(str(path), "3")
        self.assertEqual([(i["work_id"], i["prompt"]) for i in out["launch"]], [("PT-3", "/work-task PT-3")])


class NamedNoneTest(Fixture):
    tracker = "none"

    def setUp(self):
        super().setUp()
        self.plan_path = self.write(f"{DISCUSSION}/plan.md", {"type": "plan", "title": "Shelf Counts"}, "# Plan\n")
        self.draft = self.write(f"{DISCUSSION}/tickets/01-add-labels.md",
                                {"type": "story", "summary": "Add Shelf Labels", "status": "approved",
                                 "owner": "claude", "depends_on": []}, "# Add Shelf Labels\n")
        self.write("Tasks/export-shelves.md", {"type": "task", "key": None, "summary": "Export Shelves",
                                               "status": "in-progress", "owner": "claude", "stage": "testing"})
        self.before = self.snapshot()

    def test_named_sources_get_slug_work_ids_and_path_prompts(self):
        out = self.plan(str(self.plan_path), str(self.draft), "export-shelves")
        launch = [(i["title"], i["prompt"]) for i in out["launch"]]
        self.assertEqual(launch, [
            ("shelf-counts Shelf Counts", f"/work-task {self.plan_path}"),
            ("add-shelf-labels Add Shelf Labels", f"/work-task {self.draft}"),
            ("export-shelves Export Shelves", "/work-task export-shelves")])
        self.assertEqual(self.snapshot(), self.before)

    def test_no_queue_and_no_bare_numbers(self):
        self.assertIn("name the tasks", self.plan(code=1)["error"])
        self.assertIn("needs a key prefix", self.plan("12", code=1)["error"])

    def test_a_running_slug_is_found_by_its_title_prefix(self):
        out = self.plan("export-shelves", "--sessions", self.sessions("export-shelves Export Shelves"))
        self.assertEqual((self.groups(out), out["launch"]), ({"running": ["export-shelves"]}, []))


class NamedJiraTest(Fixture):
    tracker = "jira"

    def setUp(self):
        super().setUp()
        self.record("PT-7", summary="Add Shelf Labels")

    def test_a_key_with_no_record_takes_the_summary_the_session_fetched(self):
        out = self.plan("PT-7", "PT-12", "13")
        self.assertEqual([(i["title"], i["summary"]) for i in out["launch"]],
                         [("PT-7 Add Shelf Labels", "Add Shelf Labels"), ("PT-12", None), ("PT-13", None)])
        out = self.plan("PT-7", "PT-12", "--summary", "PT-12=Count Shelf Items")
        self.assertEqual([(i["title"], i["prompt"]) for i in out["launch"]],
                         [("PT-7 Add Shelf Labels", "/work-task PT-7"), ("PT-12 Count Shelf Items", "/work-task PT-12")])

    def test_another_projects_key_is_skipped_with_the_reason(self):
        out = self.plan("OTHER-1", "PT-7")
        self.assertEqual(self.groups(out), {"skipped": [None], "start": ["PT-7"]})
        self.assertIn("not a PT key", out["entries"][0]["reason"])

    def test_no_queue(self):
        self.assertIn("name the tasks", self.plan(code=1)["error"])


class SkillTextTest(unittest.TestCase):
    def setUp(self):
        self.skill = (SKILL / "SKILL.md").read_text()

    def test_every_script_command_exists(self):
        calls = set(re.findall(r"<bin>/([a-z_]+\.py) ([a-z][a-z-]*)", self.skill))
        self.assertIn(("dispatch.py", "plan"), calls)
        for script, command in sorted(calls):
            self.assertTrue((TOOLBOX / "bin" / script).is_file(), script)
            if script == "dispatch.py":
                proc = subprocess.run([sys.executable, str(TOOLBOX / "bin" / script), command, "--help"],
                                      capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0, f"{script} {command}: {proc.stderr}")
            else:
                self.assertIn(f'"{command}"', (TOOLBOX / "bin" / script).read_text(), f"{script} {command}")

    def test_every_flag_the_skill_passes_exists(self):
        help_text = subprocess.run([sys.executable, str(DISPATCH), "plan", "--help"], capture_output=True,
                                   text=True).stdout
        for flag in set(re.findall(r"dispatch\.py plan[^\n]*?(--[a-z-]+)", self.skill)) | {"--offered", "--summary",
                                                                                          "--sessions"}:
            self.assertIn(flag, help_text, flag)

    def test_installed_under_the_dispatcher_feature(self):
        features = yaml.safe_load((WORKSPACE_DOMAIN / "features.yml").read_text())
        self.assertEqual(features["dispatcher"], [".claude/skills/work-tasks", ".claude/toolbox/bin/dispatch.py"])
        for path in features["dispatcher"]:
            self.assertTrue((WORKSPACE_DOMAIN / path).exists(), path)

    def test_the_session_fields_the_skill_sets_are_the_plans(self):
        for text in ("`work.dispatch: direct`", "`own_initiative`", "`user_asked`", "`use_worktree`", "`false`",
                     "`fresh`", "--dry-run", "work.session_model"):
            self.assertIn(text, self.skill)


if __name__ == "__main__":
    unittest.main()
