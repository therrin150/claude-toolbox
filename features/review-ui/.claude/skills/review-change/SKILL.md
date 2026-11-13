---
name: review-change
description: Open the local review page for a change's diff and answer the reviewer's line comments until they finish. Refuses when there is nothing to review. Use when the user wants to review a change before it is committed - "let me review it", "review this change", "open the review for X" - on its own; `work-task` runs it for its code-review stop.
allowed-tools: Bash(*), Read, Edit, Write, Grep, Glob, Skill
---

# Review Change

The review stage, for any session: the change's diff in the `local-review-ui` page, with the reviewer's comments
answered here. `work-task`'s code-review stop runs this stage's gate and registration, then adds its own decisions,
report and waiter; a session working outside `work-task` runs the whole skill. It checks only its own requirement, that
there is a diff to review. It never checks that checks or verification ran, and it never commits, pushes or publishes:
finishing the review is not approval (`local-review-ui`, "Finishing Is Not Approval").

## Arguments

```
/review-change <work id> [--no-browser] [--resume-hint <text>]
/review-change --checkouts <repo-or-path>[,<repo-or-path>...]
```

- **work id**: the change's record, the one `start-change` printed. Its worktrees are the change.
- **--checkouts**: any repos or worktrees, for a change with no work id.
- **--no-browser**, **--resume-hint**: for a driver that seeds threads of its own and runs its own waiter
  (`work-task`). Both go to `local-review-ui`'s open. With `--no-browser` this stage ends after step 2: it registers
  the review without opening the tab, prints the URL and returns, and the driver opens the tab and starts the waiter.
  Steps 3 and 4 are then the driver's.

## Steps

1. **Gate it.** Run the script from the workspace root, or by the absolute path in the toolbox-config rule:

   ```bash
   .claude/toolbox/bin/change.py review <work id>
   .claude/toolbox/bin/change.py review --checkouts <a,b>
   ```

   It exits 1 with `refused` and the reason when there is nothing to review: the work id has no worktree, or its
   worktrees have no changed file against `origin/<default>`. Report the reason in a line and stop; don't open a page.
   On success it prints each changed checkout's `repo`, `path`, `branch`, `diff` and `arg` (`<repo>=<path>`).
2. **Open the review** with the `local-review-ui` skill (the Skill tool). With a work id, pass the work id: the skill
   covers the task's worktrees and resumes the same review and comments. With `--checkouts`, pass each `arg`. Pass
   `--no-browser` and `--resume-hint` through when given. Tell the user the review is open, with its URL, and what the
   page's buttons do (`local-review-ui`, "Open"); with `--no-browser`, return the URL to the driver instead.
3. **Wait and answer** as `local-review-ui` says: start its waiter in the background and end your turn; on each wake
   read the comments, change the code or answer, reply on each thread and never resolve one. A round that changed code
   needs its checks re-run (`check-change`).
4. **On "Done reviewing"** (or "looks good" in chat), answer what's left, tell the user the review is closed and wait
   for their next instruction. `local-review-ui` goes on to draft the commit messages after "Done reviewing" for a work
   id; this stage doesn't, because drafting and everything after it is `publish-change`'s, when the user asks for it.
