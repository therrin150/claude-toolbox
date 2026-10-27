---
name: git-draft-commit-message
description: Draft commit messages for Allen to approve, one per repo, from the diff and the task, in his commit style; optionally the one-sentence announcement for the PR batch. Drafts only; never commits.
allowed-tools: Bash(*), Read, Grep, Glob
---

# Draft Commit Message

**Extensions.** Before anything else, run `.claude/toolbox/bin/toolbox.py config extensions git-draft-commit-message`
from the workspace root (its absolute path is in the toolbox-config rule) and read every file it lists. Each `##`
section of an extension names a heading of this skill: follow a `Replaces` section instead of that heading's text, and
an `Adds to` section after it. If two extensions replace the same heading, stop and ask the user which applies.

Drafts only. Committing is `git-commit`, with the message Allen approves.

## Arguments

```
/git-draft-commit-message <checkout...> [--task <work id>] [--announce]
```

- **checkout...**: task worktree paths or repo names of the workspace.
- **--task**: the task's work id: its ticket key, or, for a change with no ticket, the slug that names its record.
  Without it, take the key from the branch name (the config's `git.branch`); a branch with no key means the change has
  no ticket.
- **--announce**: also draft the one-sentence announcement for the batch of PRs these commits will become.

## Inputs

- Per checkout: the diff against `origin/<default branch>` (committed and uncommitted) and its file list.
- The task record, for what the change is for (`.claude/toolbox/bin/tasks.py get <work id>`, when the workspace has the
  `tasks` feature). The diff decides what the message may claim.
- The style: `~/.claude/personal/commit-style.md` (which pulls in `voice.md`), plus the "Slack" section of
  `~/.claude/personal/voice.md` for `--announce`. Read both from disk on every run; when a file is missing, write plain,
  short, lowercase-after-the-key messages and say the style file wasn't found.

## The Message

`commit-style.md` decides what the subject and body say, how long they run and how they sound. The skill owns only the
mechanics:

- **One message per repo.** Repos in a batch get the same message only when the change in each is the same (a version
  bump across consumers, say).
- **Subject:** the ticket key first when the task has one (`<KEY> <phrase>`); with no ticket, the phrase alone, with no
  placeholder in the key's place. It becomes the PR title verbatim.
- **Body:** always present, since it becomes the PR description and a subject-only commit leaves that empty. Each
  paragraph is a single unwrapped line, with a blank line between paragraphs.

## The Announcement (`--announce`)

One sentence for the whole batch, not one per PR, written to `voice.md`'s "Slack" rules, which own its length, wording
and tone. This is the sentence `gh-create-pull-request` posts to the `pr.announce` channel. When the config has no
`pr.announce`, there is nothing to announce: say so and skip it.

## Output

Per repo, the subject and body in a code block so Allen can copy them, then the announcement if asked. Flag anything in
a diff the message doesn't cover (an unrelated file, a stray debug line) instead of quietly leaving it out. When the
task has a publish folder (`Tasks/.publish/<work id>/`), offer to save the approved texts there as `<repo>.txt` (or
`commit.txt` in a single-repo workspace) and `announce.txt`, which is where `publish.py` reads them.
