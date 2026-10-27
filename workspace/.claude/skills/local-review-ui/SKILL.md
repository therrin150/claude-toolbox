---
name: local-review-ui
description: Open a local, GitHub-style review UI in the browser for the uncommitted or unpushed changes of a task's worktrees (by work id) or of any repos or paths. The reviewer leaves line comments and finishes each round in the page, which wakes this session to answer them. Use when the user wants to review changes locally before committing or opening a PR ("let me review it", "open the review UI", "/local-review-ui <work id>").
allowed-tools: Bash(*), Read, Edit, Write, Grep, Glob
---

# Local Review UI

A local diff viewer with line comments, for reviewing a change before it's committed or published. One small server
per machine (Python standard library, bound to `127.0.0.1`) serves every review; each review gets its own browser tab
with a repo dropdown, a file list, red/green diffs with line numbers, and comment threads. Comments come back to this
session through `review.py`, and this session's replies show up in the reviewer's tab.

The scripts are in this skill's folder, `.claude/skills/local-review-ui` under the workspace root (`<skill-dir>`
below; the workspace's absolute path is in the toolbox-config rule). Run them with `python3` on macOS and Linux and
`py -3` (or `python`) on Windows.

## First Use

Before step 1, check for `~/.local-review/config.json` (`%USERPROFILE%\.local-review\config.json` on Windows; or
under `LOCAL_REVIEW_HOME` when that's set). If it doesn't exist, this machine hasn't been set up: read
[setup.md](setup.md) in this folder, follow its "Getting Started" with the user, then continue here. Do the same if
`review.py open` fails on configuration (no workspace root, port in use), or when no Python command works.

## Arguments

```
/local-review-ui <work id | repo-or-path...> [--id ID] [--base REF] [--resume-hint TEXT]
```

- **work id**: a task's id (its key, or the slug of a keyless task) in a workspace with the `tasks` and `worktrees`
  features. The review covers the task's worktrees, `.claude/toolbox/bin/worktrees.py list <work id>`, one path each.
  The review id is the work id and the title is `<work id> <summary>` (`.claude/toolbox/bin/tasks.py get <work id>
  summary`, passed as `--title`), so the same work id resumes the review and its comments. A work id with no
  worktrees is an error: say so and stop.
- **repo-or-path**: a path to a git checkout or worktree, a repo's folder name under the configured workspace root, or
  `name=path` to choose the name shown in the dropdown (needed when two checkouts share a folder name). With none, use
  the repos this session changed; if that's unclear, ask.
- **--id**: the review's id. The default is the ticket key in the first repo's branch name, else the branch name. The
  same id reopens the same review and its comments, and adds any new repos to it. A repo name the review already has
  at another path fails: pass a different `--id` when another session reviews the same ticket separately (two sessions
  on one id share its comments and take turns listening), or `--replace` to swap the review's repo list.
- **--base**: the ref to diff against. The default is `origin`'s default branch, and the diff is the whole change since
  the branch point: committed, staged, unstaged and untracked. Passing a ref such as `HEAD` shows only the part after
  it, so leave it out unless the user asks.
- **--resume-hint**: the prompt the reviewer pastes into a new session to pick this review back up. While no session
  is listening, the page shows it with a copy button in its warnings (the header, and the toasts after "Finish round",
  "Done reviewing" and "Reopen review"). It's kept with the review, so a later `open` without it keeps the earlier
  one; `--resume-hint ''` clears it. With a work id the default is `/work-task <work id>` when the workspace has the
  `tasks` feature; without a hint the warnings say to tell your session.

## Workflow

### 1. Open

```bash
python3 <skill-dir>/scripts/review.py open <repo-or-path>... [--id ID] [--base REF] [--title TITLE] [--resume-hint TEXT]
```

It prints JSON with the `id` and `url` and opens the tab. Tell the user in one line that the review is open (give
the URL in case the browser didn't open) and that "Finish review" brings the comments back here.

### 2. Wait

Start the waiter in the background (the Bash tool's `run_in_background: true`), then end your turn. Don't poll or
sleep.

```bash
python3 <skill-dir>/scripts/review.py wait <id> [--session "$CLAUDE_CODE_SESSION_ID"] [--until HH:MM | --minutes N]
```

It exits with one JSON line when there's something to do, and its exit wakes this session.

A session that hands the review off (a task's code-review stop, say) sets a deadline at the hand-off time, now plus
the one-hour cache TTL (`--until` takes 24-hour local time, e.g. `--until "$(date -v+1H +%H:%M)"` on macOS), because
past it this session's cache is cold. The waiter then ends with `timeout` and the page shows "No session listening"
with the resume prompt. A session that resumes from that prompt runs the waiter with no deadline, and a finished round
it hasn't read exits at once. Standalone, with no hand-off, use no deadline.

### 3. On Wake

| `event` | Do |
|---|---|
| `submitted` | The reviewer clicked "Finish round". Step 4, then wait again. |
| `done` | The reviewer clicked "Done reviewing". Step 4, but don't start the waiter again: tell the user the review is closed. For a work id, go on to drafting the commit messages (`git-draft-commit-message`) and present them for approval; otherwise wait for the user's instructions. |
| `closed` | The review was already closed and nothing new came in. Don't wait again. |
| `superseded` | Another session is now listening for this review. Say so in one line and stop. |
| `timeout` | Only with `--until` or `--minutes`. End your turn quietly. |

A closed review reopens when the reviewer clicks "Reopen review" in the page or when a session runs `open` for it
again; start the waiter again then. "Looks good" typed in chat counts as "Done reviewing". Drafting is not a git
write, so it keeps "Finishing Is Not Approval": nothing commits, pushes or publishes on the click. Inside `work-task`,
its code-review step says what comes next and replaces the `done` and `timeout` handling here.

### 4. Answer the Comments

```bash
python3 <skill-dir>/scripts/review.py read <id>
```

It prints every comment that needs a response (open, with the reviewer's message last) with its repo, file, line,
the code it was left on and the thread so far, plus the reviewer's optional summary. Reading marks the round as read.

For each comment, do what it asks when that's clear: make the change, or answer the question. When you disagree or
it's ambiguous, don't guess; say so in the reply. Then reply on the thread:

```bash
python3 <skill-dir>/scripts/review.py reply <id> <comment-id> \
  --body "Renamed to findAllByCustomer in OrderService.java:150."
```

Never resolve a thread, even when you made the change: the reviewer resolves it in the page once they've read your
reply, so it isn't lost. Keep replies short and specific: what changed and where, or why not. They render `code`,
**bold** and fenced blocks. For a long or multi-line body, omit `--body` and pipe it on stdin.

If you changed code, re-run the checks the change touches (its build and tests), as you would for any change. Inside
`work-task`, its code-review step says which.

To raise a question or decision of your own at a line, open a thread yourself:

```bash
python3 <skill-dir>/scripts/review.py note <id> --repo <name> --path <file> --line <n> [--end-line <n>] \
  [--side new|old] --body "Keep the retry here, or move it into the client?"
```

`--path` is relative to the repo, `--side new` (the default) is the working copy and `--side old` the base; `--body`
works like `reply`'s (stdin when omitted). The page shows it like any thread, marked "Opened by Claude". It belongs to
no round, and `read` lists it only once the reviewer has replied, so answer it like any other comment then. The
reviewer resolves it; you never do. A line outside the diff's context is fine: the page widens the diff to show it.
The output warns when the file has no changes in the review, so the note sits under "Comments on files no longer
changed".

Then give the user a short summary in chat (one line per comment: what you did). After `submitted`, start the waiter
again (step 2); after `done`, don't. Then wait for the user's next instruction.

With no comments that need a response, say in one line that the round finished (or the review closed) with no
comments, start the waiter again only after `submitted`, and wait.

### Finishing Is Not Approval

"Finish round" means "I'm done with this round of comments"; "Done reviewing" means "I'm done reviewing". Never treat
either, with or without comments, as approval to commit, push, open or merge a PR, deploy, or take any other next
step. Those happen only when the user asks for them in the conversation.

## Other Commands

| Command | Does |
|---|---|
| `review.py status <id>` | The review's repos, rounds, comment counts, listener and server state |
| `review.py read <id> --all` | Every comment, resolved ones too (`--peek` leaves the round unread, `--json` for JSON) |
| `review.py list` | Every registered review |
| `review.py forget <id> [--delete-comments]` | Drop a review once its change is published (`--delete-comments` removes only the tool's own files from its store; its `.lock` files and folder stay) |
| `review.py prune [--dry-run]` | After a merge: forget, comments included, every review whose repos are all gone (the path is missing or not a git work tree, or the branch `open` recorded for it was deleted), and remove its `.lock` files and empty store folder. Skips a review a session is listening on; a review registered without branches is judged on paths alone. Prints `pruned` and `kept`, each with a reason; `--dry-run` changes nothing |
| `review.py stop` | Stop the server (the next `open` starts it again) |

## Notes

- The server never writes to a repo: it reads diffs with git's optional locks off and reads untracked files from disk.
- Comments live outside every repo, under `~/.local-review/reviews/<work id>/` by default (`open --store DIR` to
  change), so they're never committed and survive a new session.
- A session that resumes later picks up a finished round: `wait` exits at once with `submitted` when a finished round
  hasn't been read.
