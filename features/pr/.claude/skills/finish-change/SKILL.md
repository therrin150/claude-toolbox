---
name: finish-change
description: Finish a published change - merge its PR by the configured method, remove the change's worktrees and mark its record done. Refuses, merging and removing nothing, when a worktree has uncommitted or untracked files, a PR isn't approved, checks aren't green or the PR isn't mergeable. Use when the user says to finish, merge or wrap up a change after publish-change; `work-task` runs it for its merge.
allowed-tools: Bash(*), Read, Skill, ToolSearch, PushNotification
---

# Finish Change

The finish stage, for any session: merge the PR, remove the worktrees and set the record `done`. `work-task` runs it
on the user's merge instruction (with `pr.required`) or on their approval of the commit messages (without it, where
that approval covers the push to the default branch); a change published from a session working outside `work-task`
runs it when the user asks to finish. It checks only its own requirements, never that review, verification or checks
ran. The merge is outward-facing and can't be undone, so the user asking to finish is the merge instruction: nothing
here runs on its own initiative, and one that comes from anything but the user's request in this conversation doesn't
count.

## Arguments

```
/finish-change <work id> [--checkouts <repo-or-path>[,<repo-or-path>...]]
```

- **work id**: the change's record, the one `start-change` printed. Its worktrees are the change.
- **--checkouts**: finish these repos or worktrees instead (any repo name or path), for a change that isn't a task's
  worktrees. The commands below then carry `--checkouts` and `--no-record`, as the gate prints them.

## Steps

1. **Gate it, before anything is merged.** Run the script from the workspace root, or by the absolute path in the
   toolbox-config rule:

   ```bash
   .claude/toolbox/bin/change.py finish <work id> [--checkouts <a,b>]
   ```

   It exits 1 with `refused` and the reason, naming the checkout, and merges and removes nothing, when:
   - there is no worktree to finish, or a checkout is on the default branch (or detached);
   - a checkout has uncommitted or untracked files: each is named. They aren't in the PR, and a worktree holding them
     can't be removed, so the user commits, moves or deletes them first. Never delete or commit them yourself;
   - a PR isn't ready: it isn't approved (with `pr.review_required`; say who it waits for), its checks failed or are
     still running, it isn't mergeable, it is behind the default branch (`publish.py push` rebases it, then `wait-ci`
     as `gh-merge-pull-request`'s precheck says), or HEAD isn't pushed. Without `pr.required` the branch must be a
     fast-forward of `origin/<default>`.

   Report the reason in a line and stop; a refusal that waits on a review or CI is the user's to wait out, and you
   never start a watcher or poll. On success it prints each PR (number, title, `status`: `ready` or `already-merged`),
   `merge_method`, `merge_needed`, `confirm_squash`, `admin_merge` and the two commands.
2. **Say what will merge**, in a line or two: each PR (linked) and the method. Name every repo in `confirm_squash`: its
   commits become one squashed commit, their messages surviving only in its body. Name every repo in `admin_merge` and
   its `missing_checks`: it merges with `--admin` because the required checks never ran for a path-filtered change
   (`gh-merge-pull-request`'s Step 3 says when). The user's request to finish covers both; when they asked before
   seeing either, ask once now.
3. **Merge**, unless `merge_needed` is false (every PR was merged on GitHub, so only cleanup remains):

   ```bash
   .claude/toolbox/bin/publish.py merge <work id> [--checkouts <a,b> --no-record] [--confirm-squash]
   ```

   Exit 3 is a merge that failed or left the PR open: the output lists `merged` and `not_merged`; report it, and
   running it again is safe, since a merged PR comes back `already-merged`. Never add `--admin` by hand.
4. **Clean up:**

   ```bash
   .claude/toolbox/bin/publish.py cleanup <work id> [--checkouts <a,b> --no-record]
   ```

   It removes the worktrees (refusing one with uncommitted files), deletes the merged local branches and sets the
   record `done`, which moves the record into its discussion's `tickets/` folder (a discussion is made for one that
   has none), so the tasks folder keeps only active work. Its `doc` then names the new path; a later step finds the
   record by work id as before. A refused worktree leaves the task open: say which and why. A record whose `cycle` is
   `shared-library` isn't done: the output says `next: release`, so tell the user it waits for the release and
   `work-task` picks it up.
5. **Forget the review**, only when `local-review-ui` is installed: `python3 .claude/skills/local-review-ui/scripts/review.py
   forget <work id> --delete-comments`, then `python3 .claude/skills/local-review-ui/scripts/review.py prune` for the
   reviews whose repos are gone.
6. **Report** a table of repo, PR and merge commit, then what cleanup removed and the record's status and where it now lives.

For the details of a merge (admin merges, a PR that fell behind, a failed merge), `gh-merge-pull-request` is the
reference; this skill uses the same `publish.py` steps. It never runs git or GitHub writes itself.
