# Work Phase

From the task record to stop 1, the code review (`code-review.md`). Status `in-progress`, stages `implementing` and
`testing`; a resume re-checks the cycle's `worktrees` against `worktrees.py list` and picks up where they stand. The
commit messages are drafted later, after "Done reviewing" (`code-review.md`, "Close Out the Review"). `<bin>` is the
scripts folder (SKILL.md). At each `hook <id>`, follow the extension docs at that hook, if Setup found any
(`extension-points.md` places them).

## Before the First Cycle

1. **Read the context** that Setup named, in order of authority:
    - `plan`, if any: how and why. The source of truth when docs disagree.
    - `questions`, if any: decisions. STOP if something the task needs is still open.
    - sibling docs the record or plan link to (line-level detail);
    - the record itself: what to build (frozen at filing, or at import; for a task started from a plan, the requirement
      Setup wrote from it). A slice's `## Scope` limits it: only that part of the requirement is this run's, and the
      rest of the ticket is out of scope here. A ticket's own record with done slices in `other_runs` covers what
      they didn't: read each one's Scope and PRs to see what's already there.
2. **Check the tracker** (a task with a Jira key; any other skips this): read the issue through `jira-reader`
   (set `tracker-check`, `.claude/toolbox/references/jira-reads.md`), unless Setup just imported it, and check it still
   matches the record; STOP if not.
3. **Check the requirement against the code.** Read the code the task names. If it already does what the task asks
   (another change got there first, other than a done slice of this ticket), STOP: say what's there and which change
   made it, and ask whether to close the task, do something else instead, or go on. Never quietly substitute a
   different change.
4. **Find prose dependencies.** A tracker records hard blockers as links, but a description may name one in prose
   ("don't start until ABC-9 is validated", "after ABC-9"). Add each key it names that way to the record:
   `<bin>/tasks.py set-list <work id> depends_on <the keys already there> <the new ones>`. A key that's only mentioned
   (background, a related change) isn't one.
5. **Check dependencies.** For each `depends_on` key: its record's status (`<bin>/tasks.py get <key> status`), or,
   under `jira` with no record, the issue's status category (`jira-reader`, set `triage`). If one isn't done, or the code shows the task needs
   another that hasn't merged: with `work.dependencies: block` (`<bin>/toolbox.py config show work.dependencies`),
   STOP; with `isolate`, read `other-tasks.md` before planning.
6. **Plan the cycles** (hook `plan-the-cycles`). List the areas the task changes (in a `nested` workspace, its repos)
   from the record, the plan and the code, then read the shared libraries: `<bin>/toolbox.py config show
   shared_libraries`. **One cycle** when `names` is empty or the task changes none of them (a task that changes only the
   consumers of a library that's already released included): every area worked and published together, as "Each
   Cycle" describes; a `single` workspace has one worktree whatever the areas. No `cycle` field. **Two cycles** when it
   changes a shared library: read `cycles.md` and follow it.
7. **Start:** on a fresh start, `<bin>/tasks.py set <work id> status=in-progress owner=claude stage=implementing`.
8. **Extensions** (hook `before-first-cycle`): on a fresh start only, before any worktree.

## Which Checks Apply

```bash
<bin>/worktrees.py changes <work id>
```

It reads each worktree's whole change against its branch point (committed, uncommitted and untracked, without touching
the index) and prints, per worktree, `diff` (the stops' Diff line) and `files`, and overall `areas`, the areas whose
checks apply as `<area>=<dir>` (the ones `check-change` hands its runner), and `skipped`, the areas with changes but
none their config's `files` globs count (a doc-only change to a repo whose checks look at code). Run it again whenever a round
changes code. Per applying area:

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

1. **Worktrees:** the `start-change` skill with the Skill tool, args `<work id> [--repos <a,b>] [--desc <kebab-desc>]`
   for this cycle's repos (`--repos` in a `nested` workspace only). It branches from the latest `origin/<default>` (so a
   consumers cycle starts after the shared library merged), creates each worktree and records them in the task's
   `worktrees`. With a key the description is required; without one, leave it off and the branch is named by the work
   id. A `refused` or `failed` repo is a STOP.
2. **Implement** the change in the worktrees, following the workspace's rules, including anything carried from another
   in-flight task (`other-tasks.md`). In a consumers cycle, first pick up the release in each consumer worktree, as
   `cycles.md` step 3 says (read it, if this session hasn't). Then hook `after-implementing`, before any check.
3. **Format** (hook `format`) every applying area that has a `format` command, before the first test run, so the
   tests, `/code-review`, the verifier and the user all see formatted code: `check-change` in `format` mode, as SKILL.md
   "## Checks" says. A formatter that rewrites whole files brings unrelated
   lines into the diff; that's accepted. A failure (the formatter couldn't parse a file) is an attempt: fix and run it
   again. It runs again on the areas touched by every later fix.
4. **Unit tests** (hook `unit-tests`): set `stage=testing`, then `check-change` in `unit` mode, as SKILL.md
   "## Checks" says (it covers every applying area), and end the turn until it wakes you.
   Fix and re-run the failing areas only; at most three attempts per area, then STOP. After a fix, format again before
   the re-run.
5. **Integration tests** (hook `integration-tests`), for each applying area with an `integration` command, when the
   workspace has the `services-lock` feature. First hook `before-integration-tests`: by default nothing to check
   beyond what `start-local-services` checks itself. When a check an extension adds there fails (a network that must
   be up, say), STOP and ask the user to fix it, and run `check-change` when they say it's done.

   Run the bracket as SKILL.md "## Checks" says: `check-change` in `integration` mode, never by hand.
6. **Review:** `/code-review` on the cycle's change (hook `code-review`), then the `verify-change` skill with the Skill
   tool, args `<work id> <worktree>...` (hook `verify`), then the checks extensions add beside them (hook
   `review-checks`). Sort every finding from all of them into one of three groups before changing anything:
    - **Fix:** clearly right, inside the task's scope, and no behavior change beyond what the task asks for.
    - **Decline:** clearly wrong or not applicable (a false positive, already handled, outside the code changed, a bump
      of a dependency the config's `verify.ignore_bumps` names). You must be able to say why in one line.
    - **Ask:** anything else. If you hesitate at all (scope, judgment, a behavior change, a "probably"), it goes here.

   Apply the **Fix** group together, format the touched areas, and re-run their unit tests (step 4) and, when a fix
   reaches integration-tested code, the integration tests (step 5, run as SKILL.md "## Checks" says), so the rebuild
   happens once. Don't stop for the **Ask** group: it becomes stop 1's Decisions, next to the code it's about. Never
   decline a finding silently: declined ones appear on stop 1's `Declined` line.
7. **Stop 1:** read `code-review.md` and follow it. Nothing is drafted yet.
