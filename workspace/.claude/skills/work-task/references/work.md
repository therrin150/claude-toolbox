# Work Phase

From the task record to stop 1, the code review (`code-review.md`). Status `in-progress`, stages `implementing` and
`testing`; a resume re-checks the cycle's `worktrees` against `worktrees.py list` and picks up where they stand. The
commit messages are drafted later, after "Done reviewing" (`code-review.md`, "Close Out the Review"). `<bin>` is the
scripts folder (SKILL.md). At each `hook <id>`, follow the extensions at that hook (SKILL.md "Extension Points").

## Before the First Cycle

1. **Read the context** that Setup named, in order of authority:
    - `plan`, if any: how and why. The source of truth when docs disagree.
    - `questions`, if any: decisions. STOP if something the task needs is still open.
    - sibling docs the record or plan link to (line-level detail);
    - the record itself: what to build (frozen at filing, or at import; for a task started from a plan, the requirement
      Setup wrote from it).
2. **Check the tracker** (a task with a Jira key; any other skips this): fetch the issue (`getJiraIssue`, the rule's
   cloud id), unless Setup just fetched it to import, and check it still matches the record; STOP if not.
3. **Check dependencies.** For each `depends_on` key: its record's status (`<bin>/tasks.py get <key> status`), or,
   under `jira` with no record, the issue's status category. If one isn't done, or the code shows the task needs
   another that hasn't landed: with `work.dependencies: block` (`<bin>/toolbox.py config show work.dependencies`),
   STOP; with `isolate`, read `other-tasks.md` before planning.
4. **Plan the cycles** (hook `plan-the-cycles`): SKILL.md "### Plan the cycles".
5. **Start:** on a fresh start, `<bin>/tasks.py set <work id> status=in-progress owner=claude stage=implementing`.
6. **Extensions** (hook `before-first-cycle`): on a fresh start only, before any worktree.

## Which Checks Apply

```bash
<bin>/worktrees.py changes <work id>
```

It reads each worktree's whole change against its branch point (committed, uncommitted and untracked, without touching
the index) and prints, per worktree, `diff` (the stops' Diff line) and `files`, and overall `areas`, the areas whose
checks apply as `<area>=<dir>`, ready for `run_tests.py`, and `skipped`, the areas with changes but none their config's
`files` globs count (a doc-only change to a repo whose checks look at code). Run it again whenever a round changes
code. Per applying area:

| Check | Runs when | Else, at the stops |
|---|---|---|
| Format | the area has a `format` command | `⏭️ Format: skipped, no format command` |
| Unit tests | the area has a `unit` command | `⏭️ Unit tests: skipped, no unit command` |
| Integration tests | the area has an `integration` command and the workspace the `services-lock` feature | `⏭️ Integration tests: skipped, <no integration command / no local services>` |

An area in `skipped` lists all three as `⏭️ <check>: skipped, no changes its checks look at`. `/code-review` and
`verify-change` always run, unless an extension skips them. A step an extension skips (`format`, `unit-tests`,
`integration-tests`, `code-review`, `verify`) is listed as `⏭️ <step>: skipped by <doc>: <reason>`. List every skipped
check at both stops.

## Each Cycle

1. **Worktrees:** `<bin>/worktrees.py create <work id> [--repos <a,b>] [--desc <kebab-desc>]` for this cycle's repos
   (`--repos` in a `nested` workspace only), from the latest `origin/<default>` (so a consumers cycle starts after the
   shared library merged), as `git-worktrees`'s "Create" describes. With a key the description is required; without one, leave it off and the branch is named by the work
   id. Record them: `<bin>/tasks.py set-list <work id> worktrees <path>...`. A `failed` repo is a STOP.
2. **Implement** the change in the worktrees, following the workspace's rules, including anything carried from another
   in-flight task (`other-tasks.md`). In a consumers cycle, first pick up the release in each consumer worktree, as
   SKILL.md "### Plan the cycles" says. Then hook `after-implementing`, before any check.
3. **Format** (hook `format`) every applying area that has a `format` command, before the first test run, so the
   tests, `/code-review`, the verifier and the user all see formatted code:
   `<bin>/run_tests.py format <area=dir>... --label <work id>`. A formatter that rewrites whole files brings unrelated
   lines into the diff; that's accepted. A failure (the formatter couldn't parse a file) is an attempt: fix and run it
   again. It runs again on the areas touched by every later fix.
4. **Unit tests** (hook `unit-tests`): set `stage=testing`, then `run-unit-tests` on every applying area in one
   background run (`<bin>/run_tests.py unit <area=dir>... --label <work id>`), and end the turn until it wakes you.
   Fix and re-run the failing areas only; at most three attempts per area, then STOP. After a fix, format again before
   the re-run.
5. **Integration tests** (hook `integration-tests`), for each applying area with an `integration` command, when the
   workspace has the `services-lock` feature. First hook `before-integration-tests`, by default
   SKILL.md "### Before integration tests".

   Each attempt is one lock bracket (start the services, run the tests, stop the services), run by the
   `services-runner` agent so the bracket's many mechanical steps stay out of this session's context. Spawn it with
   `Agent` (`subagent_type: services-runner`, `run_in_background: true`), passing the worktree path, the work id, the
   areas, and a test when only some need re-running; then end your turn. In a `nested` workspace, spawn one runner per
   repo (each repo has its own lock), but locks don't see port overlaps: when two repos' services may share a port, run
   them one at a time. Act on each runner's `result`:
    - `pass`: done with that area.
    - `fail`: an attempt. Fix the code, format, re-run its unit tests, then spawn a new runner. At most three attempts
      per area, then STOP.
    - `blocked`: hook `blocked-bracket` (by default SKILL.md "### A blocked bracket").
    - `lock: NOT RELEASED`, in any result: STOP.
    - A takeover in `notes`: notify the user that their manual lock was taken over.

   Don't edit a worktree while its runner is running. If the runner itself misbehaves, the fix goes in its agent file
   (`services-runner`), never a by-hand bracket.
6. **Review:** `/code-review` on the cycle's change (hook `code-review`), then `verify-change <work id> <worktree>...`
   (hook `verify`), then the checks extensions add beside them (hook `review-checks`). Sort every finding from all of
   them into one of three groups before changing anything:
    - **Fix:** clearly right, inside the task's scope, and no behavior change beyond what the task asks for.
    - **Decline:** clearly wrong or not applicable (a false positive, already handled, outside the code changed, a bump
      of a dependency the config's `verify.ignore_bumps` names). You must be able to say why in one line.
    - **Ask:** anything else. If you hesitate at all (scope, judgment, a behavior change, a "probably"), it goes here.

   Apply the **Fix** group together, format the touched areas, and re-run their unit tests (step 4) and, when a fix
   reaches integration-tested code, the integration tests (step 5), so the rebuild happens once. Don't stop for the
   **Ask** group: it becomes stop 1's Decisions, next to the code it's about. Never decline a finding silently:
   declined ones appear on stop 1's `Declined` line.
7. **Stop 1:** read `code-review.md` and follow it. Nothing is drafted yet.
