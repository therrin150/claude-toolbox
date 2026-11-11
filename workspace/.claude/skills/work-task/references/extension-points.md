# Work Task Extension Points

Read when `config extensions work-task` lists docs, to place each doc's sections. Workspace knowledge (a scan before
the review stop, a network check before integration tests, a wait on another task's release) lives in extension docs
(`.claude/extensions/README.md`), not in this skill. Each hook has a fixed id, named at its step as `hook <id>`; rename
headings freely, never an id. A default in a phase file is the text at the hook.

## Extension Points

| Id | Runs | Default | Verbs |
|---|---|---|---|
| `ground-rules` | SKILL.md "Ground Rules", for the whole task | those rules | add |
| `stop-conditions` | SKILL.md "STOP Conditions" | those conditions | add |
| `dispatch` | SKILL.md "Dispatch": rows for an extension's own stages | that table | add |
| `before-first-cycle` | `work.md` "Before the First Cycle", once on a fresh start: after the record and its context are loaded, before any worktree | nothing | add |
| `plan-the-cycles` | `work.md` "Before the First Cycle", step 6 | that step, and `cycles.md` for a shared library | add, replace |
| `after-implementing` | `work.md` "Each Cycle", after step 2, before format and tests | nothing | add |
| `format` | `work.md` "Each Cycle", step 3 | `run_tests.py format` | add, skip |
| `unit-tests` | `work.md` "Each Cycle", step 4 | `run-unit-tests` | add, skip |
| `before-integration-tests` | `work.md` "Each Cycle", step 5, before the first runner | nothing beyond `start-local-services`' own checks | add |
| `integration-tests` | `work.md` "Each Cycle", step 5 | `services-runner` brackets (SKILL.md "Checks") | add, skip |
| `blocked-bracket` | SKILL.md "Checks", integration tests: a runner's `blocked` result | that bullet's `blocked` text | add, replace |
| `code-review` | `work.md` "Each Cycle", step 6 | `/code-review` | add, skip |
| `verify` | `work.md` "Each Cycle", step 6 | `verify-change` | add, skip |
| `review-checks` | `work.md` "Each Cycle", step 6, beside `/code-review` and `verify-change`; findings join the triage | nothing | add |
| `needs-a-release` | `other-tasks.md` "Needs a Release": the task needs another task's release | that section's text | add, replace |
| `before-publishing` | `merge.md` "Publish", after stop 2's approval, before `publish.py` | nothing | add |
| `after-the-merge` | `merge.md` "Merge", step 3 | that step's text | add |
| `before-done` | `done.md` "Done", after the cleanup (`publish.py` has set `status=done`), before the done line and "Usage" | nothing | add |

Built-in steps (`format`, `unit-tests`, `integration-tests`, `code-review`, `verify`) take `add` and `skip`, never
`replace`, since replacing one would bypass its record updates. A skipped step is listed at both stops beside the
skipped checks (`work.md`, "Which Checks Apply"), and an `add` on a skipped step still runs. What an added step reports
goes on the next stop's Testing list (`code-review.md`), and an added step that fails is a STOP unless its doc says
otherwise.
