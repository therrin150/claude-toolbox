# Local Review UI: Options, Commands and Notes

Read by `local-review-ui` when it needs more than its SKILL.md.

## Options

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

## Other Commands

| Command | Does |
|---|---|
| `review.py status <id>` | The review's repos, rounds, comment counts, listener and server state |
| `review.py read <id> --all` | Every comment, resolved ones too (`--peek` leaves the round unread, `--json` for JSON) |
| `review.py list` | Every registered review |
| `review.py forget <id> [--delete-comments]` | Drop a review once its change is published (`--delete-comments` removes only the tool's own files and round snapshots from its store; its `.lock` files and folder stay) |
| `review.py prune [--dry-run]` | After a merge: forget, comments and snapshots included, every review whose repos are all gone (the path is missing or not a git work tree, or the branch `open` recorded for it was deleted), and remove its `.lock` files and empty store folder. Skips a review a session is listening on; a review registered without branches is judged on paths alone. Prints `pruned` and `kept`, each with a reason; `--dry-run` changes nothing |
| `review.py stop` | Stop the server (the next `open` starts it again) |

## Notes

- The server never writes to a repo: it reads diffs with git's optional locks off and reads untracked files from disk.
- "Finish round" snapshots each repo's working tree (untracked files included) in a scratch index, with the git
  objects kept in the review's store, not the repo. The page's "Changes since round N" picker then diffs that snapshot
  against the working tree now, so the reviewer sees only what changed after that round. When master was merged or
  rebased into a repo since then, its view falls back to all changes and says so.
- Comments live outside every repo, under `~/.local-review/reviews/<work id>/` by default (`open --store DIR` to
  change), so they're never committed and survive a new session.
- A session that resumes later picks up a finished round: `wait` exits at once with `submitted` when a finished round
  hasn't been read.
