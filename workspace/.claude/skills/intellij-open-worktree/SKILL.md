---
name: intellij-open-worktree
description: Open a task's worktrees in IntelliJ IDEA, one project window per repo. Use when asked to "open it in IntelliJ" or `/intellij-open-worktree <work id> [repo...]`.
allowed-tools: Bash(*)
---

# Open Worktree in IntelliJ

**Extensions.** Before anything else, run `.claude/toolbox/bin/toolbox.py config extensions intellij-open-worktree` from
the workspace root (its absolute path is in the toolbox-config rule) and read every file it lists. Each `##` section of
an extension names a heading of this skill: follow a `Replaces` section instead of that heading's text, and an `Adds to`
section after it. If two extensions replace the same heading, stop and ask the user which applies.

Opens a task's worktree folders in IntelliJ, the way JetBrains' own `idea` command-line launcher does on macOS:

```bash
open -na "IntelliJ IDEA.app" --args <path>
```

IntelliJ's "Open project in" setting (new window, this window, or ask) decides where it lands. If the app is installed
under another name (`IntelliJ IDEA CE.app`, say), use that name.

## Arguments

```
/intellij-open-worktree <work id> [repo...]
/intellij-open-worktree <repo>...
```

The second form is for work with no task (a quick fix made in the main checkout). It applies when no worktree folder
named by the first argument exists but a repo folder of that name does; every argument is then a repo.

## Workflow

### Resolve the repos

The ones named, or every repo with a worktree for the task when none are. Where worktrees live is the config's
`workspace.worktrees` (`toolbox.py config show workspace.worktrees`, relative to the workspace root):

- **`nested` workspace**: one worktree per repo, `<worktrees>/<work id>/<repo>`. With no repo named, every folder under
  `<worktrees>/<work id>/`.
- **`single` workspace**: one worktree for the whole task, `<worktrees>/<work id>`, so one window; a repo named is a
  folder inside it, opened as its own project.

### Resolve each path

The repo's worktree if it exists; otherwise the main checkout (the workspace root, or in a `nested` workspace its repo
folder). The intent is to open the checked-out code for review, whether or not it is technically a worktree. Note in the
report which repos fell back. Neither exists: report it and skip that repo. Don't create a worktree.

### Open

Run the command once per repo, then report what opened in one line.
