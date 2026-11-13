"""Tests for the checks, raw output, git write, main checkout and Jira write gates
(core/ and features/*/.claude/hooks/gates/pre_tool_use/).

Most tests run the real dispatcher on hook input, the way Claude Code does, against a temporary workspace whose
`.claude/hooks/gates/pre_tool_use/` links the gates one file at a time (as the installer does) and whose
`.claude/toolbox/lib/` links the config library, so the gates find the workspace's area commands.

Run from the repo root:

    python3 -m unittest discover -s core/workspace/.claude/toolbox/tests
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

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from toolbox_tree import CLAUDE as WORKSPACE_SRC, REPO  # noqa: E402
GATES_SRC = WORKSPACE_SRC / "hooks" / "gates" / "pre_tool_use"
DISPATCHER = REPO / "core" / "global" / ".claude" / "hooks" / "run_gates.py"
CONFIG_LIB = WORKSPACE_SRC / "toolbox" / "lib"

CONFIG = {
    "version": 1,
    "features": ["tests"],
    "workspace": {
        "name": "ws",
        "layout": "single",
        "default_branch": "master",
        "areas": {
            "api": {
                "unit": "make verify TEST={test}",
                "format": "python3 ~/tools/fmt.py . && python3 ~/tools/imports.py .",
                "integration": "cd *-validation && make it",
                "start": "docker compose up -d",
            },
            "*": {"build": "make assemble"},
        },
    },
    "user": {"handle": "jdoe", "name": "Jordan"},
    "vault": {"path": "vault"},
    "tracker": {"kind": "vault", "key_prefix": "PT"},
    "pr": {"required": False},
}


class GateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ws = self.tmp / "ws"
        gates = self.ws / ".claude" / "hooks" / "gates" / "pre_tool_use"
        gates.mkdir(parents=True)
        for src in GATES_SRC.glob("*.py"):
            (gates / src.name).symlink_to(src)
        toolbox = self.ws / ".claude" / "toolbox"
        toolbox.mkdir(parents=True)
        (toolbox / "lib").symlink_to(CONFIG_LIB)
        (self.ws / ".claude" / "toolbox.local.yml").write_text(yaml.safe_dump(CONFIG), encoding="utf-8")
        (self.ws / "api").mkdir()
        self.hooks = self.tmp / "home" / ".claude" / "hooks"  # the global dispatcher, with no global gates
        self.hooks.mkdir(parents=True)
        shutil.copy(DISPATCHER, self.hooks / "run_gates.py")
        self.env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_PROJECT_DIR", "TOOLBOX_WORKSPACE")}

    def run_gate(self, tool: str, tool_input: dict, **extra) -> str | None:
        """The denial message the dispatcher gives this call from the workspace, or None."""
        event = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input,
                 "cwd": str(self.ws / "api"), **extra}
        result = subprocess.run([sys.executable, str(self.hooks / "run_gates.py")], input=json.dumps(event),
                                capture_output=True, text=True, timeout=30, env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        if not result.stdout:
            return None
        out = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(out["permissionDecision"], "deny")
        return out["permissionDecisionReason"]

    def bash(self, command: str, **extra) -> str | None:
        return self.run_gate("Bash", {"command": command}, **extra)

    def assertDenied(self, message: str | None, mode: str | None = None) -> None:
        self.assertIsNotNone(message)
        self.assertIn("check-runner", message)
        if mode:
            self.assertIn(f"mode `{mode}`", message)

    def assertDeniedRaw(self, message: str | None) -> None:
        self.assertIsNotNone(message)
        self.assertIn("check-runner", message)
        self.assertIn("raw", message)


class ChecksGateTest(GateTest):
    def test_raw_maven_names_the_unit_mode(self) -> None:
        self.assertDenied(self.bash("mvn -q test"), "unit")

    def test_other_raw_tools(self) -> None:
        self.assertDenied(self.bash("./mvnw -B verify"), "unit")
        self.assertDenied(self.bash("mvn clean install -DskipTests"), "build")
        self.assertDenied(self.bash("mvn verify -Pvalidation"), "integration")
        self.assertDenied(self.bash("./gradlew spotlessApply"), "format")
        self.assertDenied(self.bash("gradle test"), "unit")
        self.assertDenied(self.bash("npm test"), "unit")
        self.assertDenied(self.bash("npm run test -- --watch=false"), "unit")
        self.assertDenied(self.bash("yarn test"), "unit")

    def test_found_inside_a_pipeline_a_chain_a_shell_and_an_env_prefix(self) -> None:
        self.assertDenied(self.bash("cd api && mvn -q test | tail -20"))
        self.assertDenied(self.bash("echo start; mvn test"))
        self.assertDenied(self.bash('bash -lc "cd api && mvn test"'))
        self.assertDenied(self.bash("JAVA_HOME=/x time mvn test"))
        self.assertDenied(self.bash("nice -n 10 mvn test"))
        self.assertDenied(self.bash("sudo -u build mvn test"))
        self.assertDenied(self.bash("echo hi\nmvn test"))

    def test_run_checks_and_services_lock(self) -> None:
        self.assertDenied(self.bash("/ws/.claude/toolbox/bin/run_checks.py unit api=api --task T"), "unit")
        self.assertDenied(self.bash("python3 /ws/.claude/toolbox/bin/run_checks.py format api=api"), "format")
        self.assertDenied(self.bash("/ws/.claude/toolbox/bin/run_tests.py unit api=api --label T"), "unit")
        self.assertDenied(self.bash("/ws/.claude/toolbox/bin/run_tests.py integration api=api"), "integration")
        self.assertDenied(self.bash("/ws/.claude/toolbox/bin/services_lock.py acquire --task T"), "integration")
        self.assertDenied(self.bash("python3 -I services_lock.py start api --token t"), "integration")

    def test_other_services_lock_commands_pass(self) -> None:
        for sub in ("status", "mine --task T", "release --token t", "stop --token t", "wait"):
            self.assertIsNone(self.bash(f"/ws/.claude/toolbox/bin/services_lock.py {sub}"), sub)

    def test_the_configs_area_commands(self) -> None:
        self.assertDenied(self.bash("make verify TEST=FooTest"), "unit")
        self.assertDenied(self.bash("make assemble"), "build")
        self.assertDenied(self.bash("make it"), "integration")
        home = os.path.expanduser("~")
        self.assertDenied(self.bash(f"python3 {home}/tools/fmt.py ."), "format")
        self.assertDenied(self.bash("python3 ~/tools/imports.py ."), "format")

    def test_area_command_setup_steps_and_start_commands_do_not_match(self) -> None:
        self.assertIsNone(self.bash("cd api && ls"))
        self.assertIsNone(self.bash("docker compose up -d"))
        self.assertIsNone(self.bash("make clean"))
        self.assertIsNone(self.bash("python3 ~/tools/other.py ."))

    def test_unrelated_commands_and_mentions_pass(self) -> None:
        for command in ("git status", "ls target", 'git log --grep="fix mvn test; npm test"', "echo mvn test",
                        "grep -rn mvn README.md", "cat pom.xml", "python3 -m unittest", "npm install",
                        "npm run build", "mvnx test", "cat build.gradle"):
            self.assertIsNone(self.bash(command), command)

    def test_a_subagent_runs_the_same_check(self) -> None:
        for command in ("python3 /ws/.claude/toolbox/bin/run_checks.py unit api=api", "mvn -q test", "make assemble",
                        "/ws/.claude/toolbox/bin/services_lock.py acquire"):
            self.assertIsNone(self.bash(command, agent_id="a1", agent_type="check-runner"), command)

    def test_other_tools_pass(self) -> None:
        self.assertIsNone(self.run_gate("Read", {"command": "mvn test", "file_path": "pom.xml"}))

    def test_without_a_config_the_fixed_shapes_still_deny(self) -> None:
        (self.ws / ".claude" / "toolbox.local.yml").unlink()
        self.assertDenied(self.bash("mvn test"), "unit")
        self.assertIsNone(self.bash("make verify"))

    def test_a_broken_config_does_not_break_the_gate(self) -> None:
        (self.ws / ".claude" / "toolbox.local.yml").write_text("version: [unclosed", encoding="utf-8")
        self.assertDenied(self.bash("mvn test"), "unit")
        self.assertIsNone(self.bash("git status"))

    def test_an_unbalanced_quote_still_finds_the_command(self) -> None:
        self.assertDenied(self.bash('mvn test -Dx="oops'), "unit")


class RawOutputGateTest(GateTest):
    def test_reading_a_surefire_report_is_denied(self) -> None:
        for path in ("api/target/surefire-reports/FooTest.txt", "/w/api/target/failsafe-reports/TEST-x.xml",
                     "api/build/test-results/test/TEST-x.xml"):
            self.assertDeniedRaw(self.run_gate("Read", {"file_path": path}))

    def test_reading_logs_is_denied(self) -> None:
        for path in ("/var/folders/x/T/ws-api-start.log", "api/target/app.log", "api/build.log",
                     "/Users/a/.claude/locks/ws/ws/out.log", "api/logs/app.log", "mvn-output.log",
                     "/var/folders/x/T/ws-api-unit.log", "/var/folders/x/T/ws-api-integration.log"):
            self.assertDeniedRaw(self.run_gate("Read", {"file_path": path}))

    def test_grep_paths_and_globs(self) -> None:
        self.assertDeniedRaw(self.run_gate("Grep", {"pattern": "FAIL", "path": "api/target/surefire-reports"}))
        self.assertDeniedRaw(self.run_gate("Grep", {"pattern": "FAIL", "glob": "**/target/surefire-reports/*.txt"}))
        self.assertDeniedRaw(self.run_gate("Grep", {"pattern": "ERROR", "path": "/tmp/ws-api-start.log"}))

    def test_bash_readers(self) -> None:
        self.assertDeniedRaw(self.bash("cat api/target/surefire-reports/FooTest.txt"))
        self.assertDeniedRaw(self.bash("tail -n 50 /tmp/ws-api-start.log"))
        self.assertDeniedRaw(self.bash("cd api && grep -rn FAIL target/failsafe-reports/*.txt | head"))
        self.assertDeniedRaw(self.bash("tail -f ~/.claude/locks/ws/ws/out.log"))

    def test_other_files_and_commands_pass(self) -> None:
        for path in ("api/src/main/java/Foo.java", "api/target/classes/Foo.class", "README.md", "/tmp/notes.log",
                     "api/docs/log.md", "api/surefire-reports.md"):
            self.assertIsNone(self.run_gate("Read", {"file_path": path}), path)
        self.assertIsNone(self.run_gate("Grep", {"pattern": "x", "path": "api/src"}))
        for command in ("grep -rn build.log src/", "rg -n 'ws-api-unit.log' docs",
                        "ls api/target/surefire-reports", "cat README.md", "git log -- api/target",
                        "echo target/surefire-reports/x.txt"):
            self.assertIsNone(self.bash(command), command)

    def test_a_subagent_reads_it(self) -> None:
        self.assertIsNone(self.run_gate("Read", {"file_path": "api/target/surefire-reports/FooTest.txt"},
                                        agent_id="a1", agent_type="check-runner"))
        self.assertIsNone(self.bash("cat api/target/surefire-reports/FooTest.txt", agent_type="check-runner"))


class GitWriteGateTest(GateTest):
    def assertDeniedGit(self, message: str | None, what: str, skill: str = "git-commit") -> None:
        self.assertIsNotNone(message, what)
        self.assertIn(f"`{what}`", message)
        self.assertIn("publish", message)
        self.assertIn(skill, message)

    def test_direct_commit_is_denied_naming_the_publish_step(self) -> None:
        self.assertDeniedGit(self.bash('git commit -m "x"'), "git commit")
        self.assertDeniedGit(self.bash("git commit --amend --no-edit"), "git commit")

    def test_each_write_is_denied(self) -> None:
        self.assertDeniedGit(self.bash("git push origin HEAD"), "git push")
        self.assertDeniedGit(self.bash("git push --force-with-lease"), "git push")
        self.assertDeniedGit(self.bash("git rebase origin/master"), "git rebase")
        self.assertDeniedGit(self.bash("git reset --hard HEAD~1"), "git reset --hard")
        self.assertDeniedGit(self.bash("git merge feature"), "git merge", "gh-merge-pull-request")
        self.assertDeniedGit(self.bash("gh pr merge 12 --squash"), "gh pr merge", "gh-merge-pull-request")

    def test_options_before_the_subcommand_dont_hide_it(self) -> None:
        self.assertDeniedGit(self.bash("git -C api commit -m x"), "git commit")
        self.assertDeniedGit(self.bash("git -c user.name=x -c core.editor=true push"), "git push")
        self.assertDeniedGit(self.bash("git --no-pager --git-dir=/r/.git merge x"), "git merge", "gh-merge-pull-request")
        self.assertDeniedGit(self.bash("gh -R fs-eng/api pr merge 3"), "gh pr merge", "gh-merge-pull-request")
        self.assertDeniedGit(self.bash("/usr/bin/git commit -m x"), "git commit")

    def test_found_inside_a_pipeline_a_chain_a_shell_and_an_env_prefix(self) -> None:
        self.assertDeniedGit(self.bash("git add -A && git commit -m x"), "git commit")
        self.assertDeniedGit(self.bash("cd api; git push"), "git push")
        self.assertDeniedGit(self.bash('bash -lc "cd api && git commit -m x"'), "git commit")
        self.assertDeniedGit(self.bash("GIT_AUTHOR_NAME=x git commit -m x"), "git commit")
        self.assertDeniedGit(self.bash("echo $(git commit -m x)"), "git commit")
        self.assertDeniedGit(self.bash("git status\ngit push"), "git push")

    def test_publish_and_ship_scripts_run(self) -> None:
        for command in ("/ws/.claude/toolbox/bin/publish.py commit T --message-file m.txt",
                        "python3 /ws/.claude/toolbox/bin/publish.py push T",
                        "/ws/.claude/toolbox/bin/publish.py merge T --confirm-squash",
                        "python3 ~/.claude/skills/ship-toolbox-change/scripts/ship.py run --message-file m.txt",
                        "cd /ws && .claude/toolbox/bin/publish.py run T"):
            self.assertIsNone(self.bash(command), command)

    def test_read_only_and_other_git_commands_run(self) -> None:
        for command in ("git status", "git diff --stat", "git log --oneline -5", "git show HEAD", "git add -A",
                        "git branch --show-current", "git fetch origin", "git stash list", "git reset HEAD file",
                        "git reset --soft HEAD~1", "git checkout -b x", "git -C api status", "git rev-parse HEAD",
                        "gh pr view 3", "gh pr list", "gh pr checks 3", "gh -R fs-eng/api pr status",
                        "git worktree list", "git config --get user.name", "git"):
            self.assertIsNone(self.bash(command), command)

    def test_the_words_inside_a_quoted_argument_dont_count(self) -> None:
        for command in ('echo "git commit"', "grep -rn 'git push' docs", 'printf "%s" "gh pr merge"',
                        'git log --grep="git commit"', "cat git-commit/SKILL.md", "ls git_commit"):
            self.assertIsNone(self.bash(command), command)

    def test_a_conflicted_rebase_can_be_finished_or_abandoned(self) -> None:
        for flag in ("--continue", "--abort", "--skip", "--quit"):
            self.assertIsNone(self.bash(f"git rebase {flag}"), flag)
        for flag in ("--abort", "--quit"):
            self.assertIsNone(self.bash(f"git merge {flag}"), flag)
        self.assertDeniedGit(self.bash("git merge --continue"), "git merge", "gh-merge-pull-request")
        self.assertDeniedGit(self.bash("git rebase --continue && git push"), "git push")

    def test_a_subagent_runs_them(self) -> None:
        self.assertIsNone(self.bash("git commit -m x", agent_type="general-purpose"))
        self.assertIsNone(self.bash("gh pr merge 1", agent_type="general-purpose"))


class MainCheckoutGateTest(GateTest):
    """The main checkout gate, against a nested workspace of real repos: `api` has a task worktree, `web` has none."""

    def git(self, cwd: Path, *args: str) -> None:
        subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True,
                       capture_output=True)

    def setUp(self) -> None:
        super().setUp()
        config = {**CONFIG, "features": ["worktrees"], "workspace": {**CONFIG["workspace"], "layout": "nested",
                                                                      "worktrees": ".worktrees"}}
        (self.ws / ".claude" / "toolbox.local.yml").write_text(yaml.safe_dump(config), encoding="utf-8")
        (self.ws / "api").rmdir()
        for repo in ("api", "web"):
            (self.ws / repo / "src").mkdir(parents=True)
            self.git(self.ws, "init", "-q", repo)
            (self.ws / repo / "src" / "a.txt").write_text("x", encoding="utf-8")
            self.git(self.ws / repo, "add", "-A")
            self.git(self.ws / repo, "commit", "-q", "-m", "init")
        self.worktree = self.ws / ".worktrees" / "T-1" / "api"
        self.worktree.parent.mkdir(parents=True)
        self.git(self.ws / "api", "worktree", "add", "-q", "-b", "t-1", str(self.worktree))
        (self.ws / "vault").mkdir()

    def edit(self, path: Path | str, tool: str = "Edit", **extra) -> str | None:
        return self.run_gate(tool, {"file_path": str(path)}, **extra)

    def test_a_main_checkout_edit_with_a_worktree_is_denied_naming_the_worktree_path(self) -> None:
        for tool in ("Edit", "Write"):
            message = self.edit(self.ws / "api" / "src" / "a.txt", tool)
            self.assertIsNotNone(message, tool)
            self.assertIn(str(self.worktree.resolve() / "src" / "a.txt"), message)

    def test_a_new_file_in_the_main_checkout_is_denied_too(self) -> None:
        self.assertIsNotNone(self.edit(self.ws / "api" / "src" / "new" / "B.java", "Write"))

    def test_a_relative_path_resolves_against_the_sessions_directory(self) -> None:
        self.assertIsNotNone(self.run_gate("Edit", {"file_path": "src/a.txt"}, cwd=str(self.ws / "api")))

    def test_a_repo_with_no_worktree_goes_ahead(self) -> None:
        self.assertIsNone(self.edit(self.ws / "web" / "src" / "a.txt"))

    def test_the_worktree_itself_goes_ahead(self) -> None:
        self.assertIsNone(self.edit(self.worktree / "src" / "a.txt"))

    def test_the_vault_and_files_outside_every_repo_go_ahead(self) -> None:
        self.assertIsNone(self.edit(self.ws / "vault" / "Tasks" / "T-1.md", "Write"))
        self.assertIsNone(self.edit(self.ws / ".claude" / "notes.md", "Write"))
        self.assertIsNone(self.edit(self.ws / "README.md", "Write"))
        self.assertIsNone(self.edit(self.tmp / "elsewhere.txt", "Write"))

    def test_a_vault_inside_a_repo_is_written_in_the_main_checkout(self) -> None:
        config = yaml.safe_load((self.ws / ".claude" / "toolbox.local.yml").read_text(encoding="utf-8"))
        config["vault"]["path"] = "api/vault"
        (self.ws / ".claude" / "toolbox.local.yml").write_text(yaml.safe_dump(config), encoding="utf-8")
        self.assertIsNone(self.edit(self.ws / "api" / "vault" / "Tasks" / "T-1.md", "Write"))
        self.assertIsNotNone(self.edit(self.ws / "api" / "src" / "a.txt"))

    def test_a_removed_worktree_lifts_the_gate(self) -> None:
        self.git(self.ws / "api", "worktree", "remove", str(self.worktree))
        self.assertIsNone(self.edit(self.ws / "api" / "src" / "a.txt"))

    def test_a_worktree_outside_the_worktrees_folder_is_not_a_task_worktree(self) -> None:
        self.git(self.ws / "web", "worktree", "add", "-q", "-b", "scratch", str(self.tmp / "scratch"))
        self.assertIsNone(self.edit(self.ws / "web" / "src" / "a.txt"))

    def test_every_worktree_is_named(self) -> None:
        other = self.ws / ".worktrees" / "T-2" / "api"
        other.parent.mkdir(parents=True)
        self.git(self.ws / "api", "worktree", "add", "-q", "-b", "t-2", str(other))
        message = self.edit(self.ws / "api" / "src" / "a.txt")
        self.assertIn(str(self.worktree.resolve()), message)
        self.assertIn(str(other.resolve()), message)

    def test_a_single_repo_workspace_gates_its_root(self) -> None:
        config = yaml.safe_load((self.ws / ".claude" / "toolbox.local.yml").read_text(encoding="utf-8"))
        config["workspace"]["layout"] = "single"
        (self.ws / ".claude" / "toolbox.local.yml").write_text(yaml.safe_dump(config), encoding="utf-8")
        self.git(self.ws, "init", "-q", ".")
        (self.ws / "src").mkdir()
        (self.ws / "src" / "a.txt").write_text("x", encoding="utf-8")
        self.git(self.ws, "add", "src")
        self.git(self.ws, "commit", "-q", "-m", "init")
        self.assertIsNone(self.edit(self.ws / "src" / "a.txt"))
        wt = self.ws / ".worktrees" / "T-9"
        self.git(self.ws, "worktree", "add", "-q", "-b", "t-9", str(wt))
        message = self.edit(self.ws / "src" / "a.txt")
        self.assertIn(str(wt.resolve() / "src" / "a.txt"), message)
        self.assertIsNone(self.edit(wt / "src" / "a.txt"))
        self.assertIsNone(self.edit(self.tmp / "elsewhere.txt", "Write"))  # outside the root: no crash, no denial

    def test_a_subagent_edits_it(self) -> None:
        self.assertIsNone(self.edit(self.ws / "api" / "src" / "a.txt", agent_id="a1", agent_type="general-purpose"))

    def test_without_a_config_or_with_a_broken_one_the_edit_goes_ahead(self) -> None:
        (self.ws / ".claude" / "toolbox.local.yml").write_text("version: [unclosed", encoding="utf-8")
        self.assertIsNone(self.edit(self.ws / "api" / "src" / "a.txt"))
        (self.ws / ".claude" / "toolbox.local.yml").unlink()
        self.assertIsNone(self.edit(self.ws / "api" / "src" / "a.txt"))

    def test_other_tools_and_empty_inputs_pass(self) -> None:
        self.assertIsNone(self.run_gate("Read", {"file_path": str(self.ws / "api" / "src" / "a.txt")}))
        self.assertIsNone(self.run_gate("Edit", {}))
        self.assertIsNone(self.run_gate("Write", {"file_path": ""}))
        self.assertIsNone(self.bash("git status"))


class JiraWriteGateTest(GateTest):
    """The Jira write gate: the server's write tools are denied in the main session, whatever the server is called."""

    WRITES = ("createJiraIssue", "editJiraIssue", "addCommentToJiraIssue", "addOrEditJiraIssueComment",
              "addWorklogToJiraIssue", "createIssueLink", "transitionJiraIssue", "executeWrite")
    READS = ("getTransitionsForJiraIssue", "getIssueLinkTypes",
             "atlassianUserInfo", "getAccessibleAtlassianResources", "discover", "executeRead",
             "getJiraIssueTypeMetaWithFields", "lookupJiraAccountId", "search", "fetch")

    def jira(self, name: str, server: str = "atlassian", **extra) -> str | None:
        return self.run_gate(f"mcp__{server}__{name}", {"cloudId": "c", "issueIdOrKey": "ABC-1"}, **extra)

    def test_a_main_session_create_is_denied_naming_jira_creator(self) -> None:
        message = self.jira("createJiraIssue")
        self.assertIsNotNone(message)
        self.assertIn("`createJiraIssue`", message)
        self.assertIn("`jira-creator`", message)

    def test_every_write_tool_is_denied(self) -> None:
        for name in self.WRITES:
            message = self.jira(name)
            self.assertIsNotNone(message, name)
            self.assertIn("`jira-creator`", message, name)

    def test_jira_creator_makes_the_same_call(self) -> None:
        for name in self.WRITES:
            self.assertIsNone(self.jira(name, agent_id="a1", agent_type="jira-creator"), name)

    def test_any_subagent_goes_ahead(self) -> None:
        self.assertIsNone(self.jira("editJiraIssue", agent_id="a1", agent_type="general-purpose"))

    def test_a_main_session_read_goes_ahead(self) -> None:
        for name in self.READS:
            self.assertIsNone(self.jira(name), name)

    def test_the_servers_name_is_the_workspaces_choice(self) -> None:
        for server in ("atlassian-work", "claude_ai_Atlassian", "a"):
            self.assertIsNotNone(self.jira("transitionJiraIssue", server), server)
            self.assertIsNone(self.jira("getTransitionsForJiraIssue", server), server)

    def test_it_needs_no_config(self) -> None:
        (self.ws / ".claude" / "toolbox.local.yml").unlink()
        self.assertIsNotNone(self.jira("createJiraIssue"))
        self.assertIsNone(self.jira("getTransitionsForJiraIssue"))

    def test_other_tools_pass(self) -> None:
        self.assertIsNone(self.jira("createConfluencePage"))
        self.assertIsNone(self.jira("updateConfluencePage"))
        self.assertIsNone(self.jira("slack_send_message", "slack"))
        self.assertIsNone(self.run_gate("Bash", {"command": "echo createJiraIssue"}))
        self.assertIsNone(self.run_gate("createJiraIssue", {}))  # not an MCP tool
        self.assertIsNone(self.run_gate("mcp__createJiraIssue", {}))  # no server segment
        self.assertIsNone(self.run_gate("mcp__atlassian__createJiraIssueDraft", {}))  # a longer name is another tool


class JiraReadGateTest(GateTest):
    """The Jira read gate: an issue read or JQL search is denied in the main session, whatever the server is called."""

    READS = ("getJiraIssue", "searchJiraIssuesUsingJql")
    OTHERS = ("getTransitionsForJiraIssue", "getIssueLinkTypes", "atlassianUserInfo", "getAccessibleAtlassianResources",
              "discover", "executeRead", "lookupJiraAccountId", "listJiraIssueComments", "search", "fetch")

    def jira(self, name: str, server: str = "atlassian", **extra) -> str | None:
        return self.run_gate(f"mcp__{server}__{name}", {"cloudId": "c", "issueIdOrKey": "ABC-1"}, **extra)

    def test_a_main_session_read_is_denied_naming_jira_reader_and_the_doc(self) -> None:
        for name in self.READS:
            message = self.jira(name)
            self.assertIsNotNone(message, name)
            self.assertIn(f"`{name}`", message)
            self.assertIn("`jira-reader`", message, name)
            self.assertIn(".claude/toolbox/references/jira-reads.md", message, name)

    def test_any_subagent_goes_ahead(self) -> None:
        for name in self.READS:
            self.assertIsNone(self.jira(name, agent_id="a1", agent_type="jira-reader"), name)
            self.assertIsNone(self.jira(name, agent_id="a2", agent_type="jira-creator"), name)
            self.assertIsNone(self.jira(name, agent_id="a3", agent_type="general-purpose"), name)

    def test_the_servers_other_tools_pass(self) -> None:
        for name in self.OTHERS:
            self.assertIsNone(self.jira(name), name)

    def test_the_servers_name_is_the_workspaces_choice(self) -> None:
        for server in ("atlassian-work", "claude_ai_Atlassian", "a"):
            self.assertIsNotNone(self.jira("getJiraIssue", server), server)
            self.assertIsNone(self.jira("getTransitionsForJiraIssue", server), server)

    def test_it_needs_no_config(self) -> None:
        (self.ws / ".claude" / "toolbox.local.yml").unlink()
        self.assertIsNotNone(self.jira("searchJiraIssuesUsingJql"))

    def test_other_tools_pass(self) -> None:
        self.assertIsNone(self.run_gate("Bash", {"command": "echo getJiraIssue"}))
        self.assertIsNone(self.run_gate("getJiraIssue", {}))  # not an MCP tool
        self.assertIsNone(self.run_gate("mcp__getJiraIssue", {}))  # no server segment
        self.assertIsNone(self.run_gate("mcp__atlassian__getJiraIssueTypeMetaWithFields", {}))  # a longer name is another tool


class JiraReaderWriteGateTest(GateTest):
    """The jira-reader write gate: the reader writes only a `.json` file in the session's scratchpad."""

    def write(self, path: str, agent_type: str | None = "jira-reader", scratch: str | None = "/s/scratch") -> str | None:
        extra = {"agent_type": agent_type, "agent_id": "a1"} if agent_type else {}
        if scratch is not None:
            extra["scratchpad_dir"] = scratch
        return self.run_gate("Write", {"file_path": path, "content": "{}"}, **extra)

    def test_a_json_file_in_the_scratchpad_goes_ahead(self) -> None:
        self.assertIsNone(self.write("/s/scratch/ABC-1-issue.json"))
        self.assertIsNone(self.write("/s/scratch/sub/x.json"))

    def test_anywhere_else_is_denied_naming_the_scratchpad(self) -> None:
        for path in ("/s/other/x.json", "/s/scratch/x.txt", "/s/scratch/../other/x.json", "/s/scratch-2/x.json",
                     str(Path.home() / "x.json"), "relative.json"):
            message = self.write(path)
            self.assertIsNotNone(message, path)
            self.assertIn("scratchpad", message, path)

    def test_a_symlink_out_of_the_scratchpad_is_denied(self) -> None:
        scratch = self.tmp / "scratch"
        scratch.mkdir()
        (scratch / "out").symlink_to(self.tmp / "elsewhere")
        self.assertIsNotNone(self.write(str(scratch / "out" / "x.json"), scratch=str(scratch)))

    def test_no_scratchpad_or_no_path_is_denied(self) -> None:
        self.assertIsNotNone(self.write("/s/scratch/x.json", scratch=None))
        self.assertIsNotNone(self.run_gate("Write", {}, agent_type="jira-reader", agent_id="a1", scratchpad_dir="/s"))

    def test_everyone_else_is_untouched(self) -> None:
        self.assertIsNone(self.write("/anywhere/x.txt", agent_type=None))
        self.assertIsNone(self.write("/anywhere/x.txt", agent_type="jira-creator"))
        self.assertIsNone(self.write("/anywhere/x.txt", agent_type="general-purpose"))

    def test_other_tools_by_the_reader_pass(self) -> None:
        self.assertIsNone(self.run_gate("Read", {"file_path": "/anywhere/x.md"}, agent_type="jira-reader", agent_id="a1"))


if __name__ == "__main__":
    unittest.main()
