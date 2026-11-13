# pr-reporter Procedure

What `pr-reporter` does for one spawn. A script fetches the PR's review threads, reviews and failed checks through `gh`
and trims them; you sort the comments and report. The comments' JSON (diff hunks, reactions, urls) and a failed check's
log stay in the script, so the asker reads a page, not a dump.

## Fetch

Run once, with the PR as given:

```
<bin>/pr_report.py <PR url>
<bin>/pr_report.py <repo> <number>
```

It prints one JSON object. Exit 2 (bad input) or 3 (`gh` failed: no such PR, no access, no network) prints `error`:
report `found: blocked: <that error, one line>` and stop. Never retry more than once, and never fall back to a `gh`
command of your own.

What it gives: `repo`, `number`, `state`, `review_decision`, `reviews` (`states` per reviewer, `bodies` with text),
`threads` (each: `path`, `line`, `reviewer`, `state`, `body`, `replies`, `reply_count`), `conversation` (comments on the
PR itself), `checks` (`failed` each with `name`, `step`, `errors` and `note`; `pending`; `passed`), and `more_threads`,
`more_failed` and `bots_skipped` when it left some out.

## Sort Each Comment

A thread is one comment, whatever its replies. Give it one `kind`:

| Kind | When |
|---|---|
| `resolved` | the thread's `state` is `resolved` or `outdated`. Say which in the ask: `(resolved)` or `(outdated: a later commit changed these lines)`. An outdated thread whose replies say it is not fixed (the reviewer wrote "still", "not yet", "this is still wrong") is `change` instead, and the ask says so |
| `change` | an open thread that asks for something to be different: do, rename, remove, add, fix, a requested test or a change of approach |
| `question` | an open thread that asks why or whether and wants an answer, not an edit ("why not X?", "is this safe?"). A question phrased as a request ("can you rename this?") is a `change` |
| `nit` | an open thread the reviewer marks minor (`nit`, `optional`, `minor`, `take it or leave it`, a pure style or wording remark) |

When a reply by someone other than the reviewer says it's done or answers the question and the reviewer hasn't answered
back, still give the thread the kind its own text and `state` give it, and put what the reply says at the end of the ask
(`replied: done in abc123`); you can't tell whether it's true, and the asker can.

Write the ask in your own words in one short line: what the reviewer wants, not a quote of their whole comment. Keep a
name, a number or an exact string they gave. A thread whose body has several unrelated asks is one line with the asks
separated by `;`. Keep the script's order (oldest thread first).

Review `bodies` and `conversation` are comments with no file and line. List one only when it asks for something or
asks a question; skip an approval with no ask (`LGTM`, `looks good`) and thanks. Give its location as `review body`
(a review's summary, whose `state` you add to the ask when it is CHANGES_REQUESTED) or `conversation`.

## Failed Checks

For each of `checks.failed`: its `name`, its failing `step` (`unknown` when the script found none) and its `errors`
exactly as given, one line each, never re-quoting more of a log than that. A check with a `note` and no `errors` (not a
GitHub Actions job, a log that couldn't be fetched) says so in one line instead of lines. Report `pending` checks by name
in `notes`; `passed` is only a count.

## Counts and Limits

`review_decision` goes on the `pr` line. If the script says `more_threads` or `more_failed`, say how many in `notes`.
Never report a thread, check or error line the script didn't give you.

## When There Is Nothing

A PR with no open threads, no comments with an ask and no failed checks is `found: none`, with the `pr` line saying its
state and review decision. That is a finding: say nothing else except `notes` for pending checks.
