# Archive

Abandoned on 2026-11-15 and moved here on 2026-11-16 (`git mv`, so history follows): the installer-based Claude
toolbox. It grew from a handful of rules (2026-10-14) to 28 features, 17 scripts, 9 hooks and 9 agents in a month.
The 2026-11-14 MealBox runs of `/work-task` finished 2 of 9 tasks without help and hit hook denials in 8 of 9; the
gates, the agent layer, the 20 KB skill with its references, and installed copies that drifted from source were most of
the friction.

Why and what replaced it: the vault discussion `Discussions/2026/11/2026-11-15-minimal-claude-toolset` (`plan.md`,
`feedback-synthesis.md`, `current-footprint.md`). The live setup is plain files edited in place: `~/.claude` (response
style, git, shell and Obsidian rules, voice and commit style), `~/_workspace/.claude` and `~/_workspace/CLAUDE.md`, and
MealBox's tracked `.claude/` and `CLAUDE.md`. Tag `pre-strip-2026-11-15` is the last state before the strip.

Nothing here is installed or read by a session. Each feature folder's `.claude/` tree is renamed `dot-claude/` so a
session opened in this repo never discovers an archived skill, rule or agent.

| Item | What it was | Why it went |
|---|---|---|
| `install.py`, `toolbox.yml`, `machine.example.yml`, `requirements.txt`, `tests/` | The installer: copies feature folders into `~/.claude` and each workspace, with a manifest and drift conflicts | Installed copies drifted from source; edits now happen in place |
| `core/global`, `core/workspace` | What every machine and workspace got: the gate dispatcher and settings | The gates are gone |
| `features/setup-workspace`, `sync-toolbox`, `ship-toolbox-change` | Skills to configure, sync and ship the toolbox | They went with the installer |
| `features/tasks` | Task records, `tasks.py`, `work-task`, `start-change`, `investigate`, `propose-tickets`, `create-tickets`, `create-defect` | Replaced by a short per-project `/work-task`, `/tickets` and slash-only `/investigate`; Jira or vault notes hold state |
| `features/worktrees`, `git`, `pr` | Worktree, commit-draft, publish, PR and merge skills, `publish.py`, `pr_report.py`, the git and main-checkout gates | The commit-message drafting, PR opening and merge steps live inside `/work-task` |
| `features/tests`, `services-lock` | `run_checks.py`, `run_tests.py`, `services_lock.py`, the checks and raw-output gates, start and stop services | Plain commands in each project's `CLAUDE.md`; one session per story needs no lock |
| `features/verify` | `verify-change` and the `change-verifier` agent | Became `/verify` with the verifier prompt inline |
| `features/review-ui` | `local-review-ui` and `review-change` (a local review page, about 3,700 lines) | Review moved to GitHub draft PRs |
| `features/dispatcher` | `work-tasks`, `dispatch.py`, `watch_task.py` for unattended parallel runs | One session per story, driven by Allen |
| `features/jira`, `jira-sprints` | `jira-creator` and `jira-reader` agents, Jira read and write gates, `jira-find-current-sprint` | The Jira MCP with `responseFields` is enough; the sprint skill now lives in `_workspace/.claude` |
| `features/intellij` | `intellij-open-worktree` | Not worth a skill |
| `features/obsidian` | Templater templates and the `vault-researcher` agent | The vault templates are now the vault's own; Explore covers searching |
| `features/discussion` | The general `discussion` skill | Each project keeps its own copy naming its vault |
| `features/angular`, `bruno`, `java`, `playwright`, `postgres`, `spring` | Convention knowledge skills | Rules only (`mb-*`, `themis/*`) |
| `features/response-style`, `personal-styles`, `rule-git`, `rule-obsidian`, `rule-shell` | The global `CLAUDE.md`, voice, ticket and commit styles, and the git, Obsidian and shell rules | Still in use, now hand-edited in `~/.claude`; these are the original sources |
| `toolbox-README.md` | The toolbox's README | Describes the installer |
