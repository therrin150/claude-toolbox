# local-review-ui: Getting Started

Instructions for Claude, to set this tool up for its user and to work on it. How to use it in a session is in
[SKILL.md](SKILL.md).

## What It Is

A local, GitHub-style review page for changes that aren't published yet: a file list, red/green diffs with line
numbers, and line comments that a Claude session reads and answers. One server per machine serves every review, each
in its own browser tab. It is Python's standard library plus one static page: no `pip` or `npm` installs, no
third-party code, nothing loaded from the internet.

| File | Role |
|---|---|
| `SKILL.md` | How a session opens a review, waits for the reviewer, and answers the comments |
| `scripts/review.py` | The CLI a session runs: `open`, `wait`, `read`, `reply`, `note`, `status`, `list`, `forget`, `prune`, `stop` |
| `scripts/server.py` | The server; `review.py open` starts it detached, and it exits after 12 idle hours |
| `scripts/install.py` | First-time setup: `check`, `configure`, `verify` |
| `scripts/common.py` | Paths, config, cross-platform file locks, JSON storage |
| `scripts/gitdiff.py` | Read-only git diff helpers |
| `scripts/anchors.py` | Where each comment sits in a file's current diff, and the context lines that show it |
| `assets/` | The page: `index.html`, `app.js`, `app.css`, and an in-house syntax highlighter, `highlight.js` |

## Requirements

- Python 3.9 or newer, and git, on `PATH`.
- macOS, Linux or Windows. The Python command is `python3` on macOS and Linux and `py -3` (or `python`) on Windows;
  use whichever step 1 of "Getting Started" finds working, everywhere below.

## Getting Started

Act as the go-between: run the setup script, answer what you can find out yourself, and ask the user only for what
you can't. Ask everything you need in one message.

1. **Find Python.** The setup script is Python, so it can't report a missing Python itself. Try each command until
   one prints 3.9 or newer: `python3 --version`, then `python --version`, then (Windows) `py -3 --version`. Watch for
   two stand-ins that aren't Python:
   - On Windows, `python` and `python3` can be App Execution Aliases that open the Microsoft Store (they print
     nothing, or a Store message, and exit with an error).
   - On macOS, `/usr/bin/python3` without the Command Line Tools pops up an install dialog instead of running.
     Installing the Command Line Tools (`xcode-select --install`) gives a Python new enough for this tool.

   With none working, tell the user, give the install for their OS, and stop until it's done:

   | OS | Install |
   |---|---|
   | macOS | `xcode-select --install`, or `brew install python`, or the python.org installer |
   | Windows | `winget install Python.Python.3.12`, or the python.org installer with "Add python.exe to PATH" ticked; then open a new terminal (and a new Claude Code session) so `PATH` updates |
   | Linux | The distribution's package (`sudo apt install python3`, `sudo dnf install python3`) |

   Found but older than 3.9: same, as an upgrade.
2. **Check.** Run `<python> scripts/install.py check` from this folder, with the command step 1 found. It changes
   nothing and prints JSON: `python`, `git`, `port`, `editorsFound`, and `needs`, the open questions.
3. **Resolve `needs`.**
   - `git`: tell the user it's missing from `PATH` and how to install it for their OS. Stop until it's fixed.
   - `workspaceRoot` (optional): the folder holding their repo checkouts side by side, so reviews can name repos by
     folder name. If one candidate clearly holds their repos (the toolbox workspace of a nested layout, or most of
     the repos they work on), propose it and ask them to confirm; otherwise list the candidates and ask.
   - `port`: another program holds the default port (`47820`). Pick the next free one and tell them.
   - `editor`: which IDE "Open in IDE" opens the repo in, as a project with the file at the line. When only one
     candidate was found, use it without asking.
4. **Configure.** `python3 scripts/install.py configure --workspace-root <dir> [--port N] [--editor intellij|vscode]`.
   Settings go to `~/.local-review/config.json`.
5. **Verify.** `python3 scripts/install.py verify` starts the server and prints its URL. Open it in the browser: the
   index page lists reviews (none yet).
6. **Tell the user** in a few lines: setup is done; in a session, `/local-review-ui <repos>` opens a review; they
   comment by clicking a line number (shift-click for a range); "Finish review" sends the comments back to the
   session, which answers them and then waits. Finishing never counts as approval to commit or publish.

The skill itself is already installed in the workspace by the toolbox installer (feature `review-ui`); a new session
is needed to see it if it was installed after this one started. If you came here from
`SKILL.md` because a review was asked for, go back and carry on with its workflow: the user is waiting for it.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `port N is in use by another program` | `install.py configure --port <other>`, or set `LOCAL_REVIEW_PORT` |
| `the server didn't start` | Read `~/.local-review/server.log` |
| The page shows "No session listening" | The session's waiter isn't running: the session ended or timed out. Finishing still records the round; in the session, run `review.py read <id>` or start `wait` again. When the review was opened with `--resume-hint`, the page shows that prompt with a copy button |
| The page says "Server offline" | `review.py open` (or `install.py verify`) starts it again; open tabs reconnect |
| The browser didn't open | Use the `url` from `review.py open`'s output |
| "Open in IDE" does nothing | `configure --editor intellij` (or `vscode`), or pass the launch command with `--editor-command '["<ide>", "{project}", "--line", "{line}", "{file}"]'` |
| "Open in IDE" opens a lone file, not the project | No `editorCommand` is set, so it fell back to the URL scheme; run `configure --editor <name>` |
| Two checkouts share a folder name | Name them: `review.py open main=<path> wt=<path>` |

## Security and Privacy

- The server binds to `127.0.0.1` only, rejects any request whose `Host` isn't that address and port (which blocks
  DNS rebinding), and rejects writes from any other web page's `Origin`.
- It makes no network calls and loads nothing from the internet; the page talks only to the server that served it.
- "Open in IDE" runs only the `editorCommand` in `config.json`; the page supplies just which registered repo, file
  (checked to be inside that repo) and line.
- It never writes to a repo: git runs with `GIT_OPTIONAL_LOCKS=0`, and untracked files are read from disk rather than
  marked with `git add -N`.

## State

Everything lives under `~/.local-review/` (override with `LOCAL_REVIEW_HOME`):

```
config.json            port, workspaceRoot, defaultBase, editorCommand, editorUrl
server.json            the running server's pid, port and version
server.log             server output
registry/<id>.json     each review: its repos, resume hint and store folder
reviews/<id>/          default store: comments.json, viewed.json, listener.json (+ .lock files)
```

Each repo in a registration records its `path`, its `base` and the `branch` it was on when `open` ran (`null` for a
detached HEAD). Registrations from before branches were recorded have no `branch` key; `prune` judges those on the path
alone, and nothing else reads the field. A comment's `author` is `user`, or `claude` for a thread the agent opened with
`note`, which has `round: null` because it belongs to no round.

A comment keeps the text of its line (`anchorText`) and up to three lines either side of its range (`before`,
`after`), captured from the file when it's made. The server places it against the file as it is now
(`anchors.locate`): at its own line, else where the line moved ("Moved"), else at the closest surviving line, flagged
"Outdated" with its original text. A line with no letters or digits (`}`, `*/`) never moves on its own text alone.
`/diff` returns the placements and widens the hunks to show every placed comment, so one outside the diff's context
shows inline. Comments from before `before`/`after` were kept fall back to their old line number when their text is
gone.

The server and every CLI process take the same file lock before writing (`fcntl` on macOS and Linux, `msvcrt` on
Windows) and write atomically, so a browser click and a session's reply can't overwrite each other. A writer also
checks, under its lock, that the review's registration still exists, so one that loaded it before a `forget` or `prune`
writes nothing instead of recreating the store. `prune` takes the registry lock and the store's locks before it deletes
a review's files, and removes the `.lock` files only after releasing them; a process that waited on a lock file
that was then removed finds the path now names another file and locks that one (`common.locked`). `prune` keeps a review
when git can't say whether a repo is still a work tree (unsafe ownership, a broken config), rather than reading that as
"gone".

## Working on the Tool

- The server's version is a hash of `anchors.py`, `common.py`, `gitdiff.py` and `server.py`. After changing them, the
  next `review.py open` replaces the running server; open tabs reconnect. Changes to `assets/` need only a page reload.
- Test in isolation so you don't disturb the user's reviews:
  `LOCAL_REVIEW_HOME=<temp dir> LOCAL_REVIEW_PORT=47899 python3 scripts/review.py open --no-browser <repo>`.
- Keep it dependency-free: standard library only in `scripts/`, no frameworks or CDN links in `assets/`.
