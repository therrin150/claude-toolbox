# Code Review Phase

Stop 1 of 2 per cycle: the user reviews the code in the local review page and makes the decisions the checks left
them. It ends at "Done reviewing", which is the cue to draft (never to publish), and then stop 2 (`publish-review.md`).
Stage `code-review`. Review comments, decisions and code changes all happen here; the commit messages don't exist yet.

`<review>` stands for `python3 <workspace>/.claude/skills/local-review-ui/scripts/review.py`, with the workspace's
absolute path from the toolbox-config rule: write it out in full in every call, as SKILL.md says of `<bin>`, never in a
shell variable (zsh won't run a command held in one). The review's id is the work id. Without the `review-ui` feature
there is no review page: skip the steps that name `<review>`, and the user answers stop 1 in chat, comments and all.

## Presenting the Stop

Order of the turn, so the report lands after `wrap_up` (text before the call can reach the user cut to a short
summary) and the waiter is running before the turn ends:

1. **Open the review:** `local-review-ui <work id>`, as its Arguments describe: it covers the task's worktrees, titles
   the review `<work id> <summary>`, diffs against the default base, and passes `--resume-hint "/work-task <work id>"`.
   Note the URL it prints. Opening again is safe: the same id reopens the same review with its comments.
2. **Seed the decisions:** one thread per **Ask** finding and per **Decided alone** item that has a code location, on
   its line: `<review> note <work id> --repo <repo> --path <file> --line <n> --body "<the question, with the
   options>"`. Anything with no location stays in the report. Don't seed a decision that already has a thread (a stop
   1 that comes back after a code change at stop 2, or after a release, keeps the earlier threads).
3. **Save the report:** write `code-review.md` to the publish folder: the report below, without the hand-off block
   (the block is time-specific, so a resume writes its own). Set `status=review owner=<user> stage=code-review`.
4. **Start the waiter** in the background (`run_in_background: true`), keeping its task id for `TaskStop`:
   `<review> wait <work id> --session "$CLAUDE_CODE_SESSION_ID" --until <HH:MM>`, the hand-off time in 24-hour local
   time. One call gives both forms, `h:mm` for the block and `HH:MM` for `--until`: `date -v+1H +'%-I:%M %H:%M'`.
5. **Notify** (`PushNotification`), call `wrap_up` with a recap only, then write the report as the closing message,
   ending with the hand-off block (SKILL.md "Hand-offs"). Never write the report before `wrap_up`, and never end the
   turn with a one-line pointer to a report the user can't see.

````markdown
## ABC-12 · code review

**Review:** http://127.0.0.1:47820/review/ABC-12. "Finish round" sends your comments; "Done reviewing" moves on to
the commit messages.

### Decisions

1. **api `ShelfService.java:88`:** <the Ask finding, one or two lines, with the options>. Also a thread on the line.

---

### api

`<worktrees>/ABC-12/api`

- **Changed:** <one line>
- **Diff:** 4 files, +50 −7
- **Testing:**
  - ✅ Format: clean
  - ✅ Unit tests (`<the unit command>`): 412 tests, 0 failed
  - ✅ Integration tests: 38 run, 0 failed (attempt 2; attempt 1 failed `ShelfIT.counts`, fixed)
  - ✅ `/code-review`: 2 findings, 1 fixed, 1 in Decisions
  - ✅ Independent verification: no findings
- **Decided alone:** <only if something was>
- **Declined:** <findings not fixed, one-line reason each, only if any>
- **Overlaps:** <only if changes were carried from another in-flight task>
- **Pending:** <only if something is held back, and what it waits on>

---

<hand-off block>
````

The heading is `<work id> · code review`, with the cycle's name before `code review` when the task runs two
(`ABC-12 · consumers code review`). `Decisions` is left out when the Ask group is empty, and then the stop is a plain code review. Each worktree gets
its own `###` section with a `---` rule before it (a `single` workspace has one, named by the workspace), and lists the
skipped checks for its areas (`work.md`, "Which Checks Apply"). No diffs beyond the `Diff` stats: the page has them,
and the user asks for a file's diff in chat when they want it there.

The **Testing** list names every check that actually ran, in order, with its result in a few words: counts, attempts,
what failed and how it was fixed. A step an extension added, at any hook, that reported something gets a line of its
own, by its name (`✅ Sonar scan: no new issues`), and a step an extension skipped is listed as `⏭️ <step>: skipped
by <doc>: <reason>`. Never list one as passing without having run it in this cycle against the code as it
stands: after any fix, re-run the unit tests (and integration tests, if they apply) before presenting. Leave out
`Decided alone`, `Declined`, `Overlaps` and `Pending` when there's nothing to say.

The **Diff** line is `diff` from `<bin>/worktrees.py changes <work id>`: the worktree's whole change against its branch
point, committed, uncommitted and untracked alike. Don't open the app's diff pane: it reads the session's working
directory, which can't see the worktrees.

## On Each Wake or Answer

Check first that the task is still this session's (SKILL.md "Hand-offs", step 2), then:

| Event | Do |
|---|---|
| `submitted` | `<review> read <work id>`. Per comment, change the code or answer. If code changed: format the touched areas and run their unit tests only (`run-unit-tests`, one background run; a failure is an attempt, three then STOP). Then reply on each thread with what changed and the test result (`<review> reply`; never resolve a thread, the user does). One line per comment in chat. Then start the waiter again, with a fresh hand-off time, and end with the short hand-off block |
| The user answers in chat | `TaskStop` the waiter, apply the answer like a comment, reply on the matching threads, start the waiter again |
| `done`, or "looks good" in chat | Answer any comment left (as `submitted`); ask in chat about any decision still unanswered (`<review> read <work id> --all --peek`: a thread "opened by Claude" with no reply) and wait for the answer, applied like a comment; then "Close Out the Review" |
| `closed` | The review was already closed with nothing unread (a session resumed after "Done reviewing" that an earlier session had read): go on to "Close Out the Review" |
| `superseded` | Say the work moved to another session, in one line, and stop |
| `timeout` | End the turn quietly: the hand-off block is already on screen |

A round that changes code leaves integration tests for "Close Out the Review": the services lock and a bracket are too
heavy for comment-sized changes. If a round changes the diff enough that a Pending or Overlaps line, or the Testing
list, is wrong, fix those in `code-review.md` too.

A short hand-off block after a round: "If you're not back by h:mm, paste `/work-task <work id>` into a new session
started at the workspace root", with the new time and the waiter's matching `--until` (each wake refreshes the cache,
so the deadline moves with it).

## Close Out the Review

Reached on "Done reviewing" or "looks good". Nothing here commits, pushes or publishes: drafting isn't a git write.

1. **Format** the areas changed since the last run (`work.md` step 3). A no-op when nothing changed; if it changed a
   file, re-run that area's unit tests.
2. **Integration tests** (`work.md` step 5) for each area that has them and whose code changed since its last pass,
   once, for all the rounds together; skip when none did. A failure is an attempt (three, then STOP); fix, format,
   re-run the unit tests, then the integration tests again.
3. **Draft:** `git-draft-commit-message <worktree>... --task <work id>`, plus `--announce` when the config has
   `pr.announce` and `pr.required`. Write each message to the publish folder, `commit.txt` in a `single` workspace or
   `<repo>.txt` per repo in a `nested` one, and the announcement to `announce.txt`; `publish.py` commits and posts
   those files byte for byte.
4. **Stop 2:** read `publish-review.md` and follow it. If steps 1–2 changed code after the user reviewed it, say so on
   that worktree's `Changed since review` line there.

## Resuming

A session that starts at `code-review` (a pasted prompt, or the user back after the hand-off) re-presents the stop:

1. Run `<bin>/worktrees.py changes <work id>`. If a worktree's `diff` differs from `code-review.md`'s, someone changed
   it since: say which, and re-run the unit tests (and integration tests, if they apply) before presenting. Without a
   `code-review.md`, rebuild the report: re-run the checks that apply for its Testing list.
2. `local-review-ui <work id>` opens (reopens) the review.
3. Start the waiter with no `--until`: a new session's context is small, so a wake is cheap. A finished round it
   hasn't read makes it exit at once, and that wake handles the round (table above) right after the report.
4. Present `code-review.md`, after `wrap_up` as above, with no hand-off block.
