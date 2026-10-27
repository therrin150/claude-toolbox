# Publish Review Phase

Stop 2 of 2 per cycle: the user approves the commit messages and the announcement, drafted from the final code, and
the final testing summary. Their approval publishes (`merge.md`). Stage `publish-review`. It is for wording only: a
code change asked for here goes back through the checks and both stops (below).

## Presenting the Stop

Same turn order as `code-review.md`, so the report lands after `wrap_up`: write `publish-review.md` to the publish
folder (the report below without the hand-off block), set `status=review owner=<user> stage=publish-review`, notify
(`PushNotification`), call `wrap_up` with a recap only, then write the report as the closing message, ending with the
hand-off block (SKILL.md "Hand-offs"). Present it as ordinary Markdown, NOT inside a code block, so headings, bold and
lists render. Only each commit message goes in a code block, because its exact text is what the user approves. Never
end the turn with a one-line pointer to a report they can't see.

````markdown
## ABC-12 · publish review

---

### api

`<worktrees>/ABC-12/api`

- **Diff:** 4 files, +50 −7
- **Testing:**
  - ✅ Format: clean
  - ✅ Unit tests (`<the unit command>`): 412 tests, 0 failed
  - ✅ Integration tests: 38 run, 0 failed
  - ✅ `/code-review`: 2 findings, 1 fixed, 1 decided at the review
  - ✅ Independent verification: no findings
- **Changed since review:** <only if the close-out changed code after the user reviewed it, and what>
- **Pending:** <only if something is held back, and what it waits on>
- **Commit:**
  ```
  ABC-12 short lowercase phrase

  Body, as drafted.
  ```
  (With no key the subject is the phrase alone.)

---

**Announcement:** <one sentence, only with `pr.announce`>

**Publishing:** <one line from the PR policy: "commit, push and open the PRs; merging waits for your instruction", or
"commit and push straight to `<default>`, then clean up">

Wording changes stay here; a code change goes back through the checks and both stops.

<hand-off block>
````

Per worktree: path, `Diff` (from `worktrees.py changes`), the **final** `Testing` list, `Changed since review` and
`Pending` when they apply, and the commit message. No `Changed` or `Decided alone` lines: the user saw those at stop 1.
The Testing list names every check that ran against the final code, in order, with its result in a few words, and lists
one that didn't run as skipped with the reason (`work.md`, "Which Checks Apply"). Never list one as passing without
having run it in this cycle against the final code. Each worktree gets its own `###` section, and a `---` rule goes
before every worktree section and before the announcement.

## The User's Answer

Check first that the task is still this session's (SKILL.md "Hand-offs", step 2), then:

- **Wording:** redraft the message or the announcement, keep the publish folder's files in step with their wording,
  and present stop 2 again.
- **A code change:** say in one line that it goes back through the checks and both stops, set `status=in-progress
  owner=claude stage=implementing`, and go back to `work.md`'s "Each Cycle" step 2 (Implement) for the affected areas,
  then on through format, unit tests, integration tests, `/code-review` and the verifier, stop 1 and stop 2 with fresh
  drafts. The review id and its comments carry over, so stop 1 shows the earlier threads.
- **Approval,** with nothing `Pending`: read `merge.md` and publish.
- **Approval with something `Pending`:** nothing publishes until it's out (SKILL.md "### Needs a release"); say so in
  one line.

## Resuming

A session that starts at `publish-review` runs `<bin>/worktrees.py changes <work id>` first: if a worktree's `diff`
differs from the saved report's, someone changed it since, so say which and re-run the unit tests (and integration
tests, if they apply) before presenting. Then it presents `publish-review.md` as saved. Without one, rebuild the
report: re-run the checks that apply for its Testing list, and take the commit messages and the announcement from the
publish folder. Then end with a new hand-off block, as above.
