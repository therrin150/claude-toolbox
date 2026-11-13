# Hand-offs

Read at both review stops and once the PRs are open. The user is often gone for hours there, and coming back rewrites
this session's whole context to cache. So each of those waits ends with a prompt they can paste into a new session,
which resumes from the record. Stop 1's waiter runs `--until` the same hand-off time (`code-review.md`), so this session
wakes only while its cache is warm; past it the review page shows the prompt (`--resume-hint`) and a pasted session
takes over. A session resumed at stop 1 ends with no block: its waiter has no deadline, and its context is small.

1. **End the stop's report** (the closing message after `wrap_up`, never text before it, which the app can cut) with
   this block, `h:mm` being now plus the one-hour cache TTL (`date -v+1H +%-I:%M` on macOS):

   ````markdown
   **If you're not back by h:mm**

   This session's prompt cache expires then, and resuming here rewrites the whole context to cache. To pick up from
   the saved task record instead, paste this into a new session started at the workspace root:

   ```
   /work-task <work id>
   ```
   ````

2. **When the user answers here instead,** first run `<bin>/tasks.py get <work id> sessions`. If its last entry isn't
   `$CLAUDE_CODE_SESSION_ID`, the task has moved to a pasted session: say so in one line and do nothing else.
   Otherwise go on here: within the hour this is the cheap path, and it keeps the implementation context for review
   fixes.
