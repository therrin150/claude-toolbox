---
name: verify-change
description: Independently verify a change against its task. A verifier agent sees only the requirement and the diff and reports missed requirements, out-of-scope changes, broken callers and missing tests. Use after implementing a task, or before reviewing any hand-made change.
allowed-tools: Bash(*), Read, Grep, Glob, Agent
---

# Verify Change

**Extensions.** Before anything else, run `.claude/toolbox/bin/toolbox.py config extensions verify-change` from the
workspace root (its absolute path is in the toolbox-config rule) and read every doc it lists. Each `##` section of a doc
is a verb and one of this skill's hook ids ("Extension Points" below): at that hook, follow an `add` section after the
hook's own text and a `replace` section instead of it; for a `skip` section, don't run the step, and list it wherever
this skill reports its steps as `⏭️ <step>: skipped by <doc>: <reason>`. An added step that fails stops the skill for
the user, unless its doc says otherwise, and what a step reports goes in this skill's next report. If two docs replace
or skip the same hook, stop and ask the user which applies.

A second pair of eyes that didn't watch the work happen. Its value comes from what it **doesn't** know: it gets the
requirement and the diff, and has to judge the change on its own.

## Arguments

```
/verify-change <work id | task-text> <checkout...> [--base <ref>]
```

- **work id | task-text**: a task's work id (a ticket key, or the slug of a keyless task) with a task record, or, without
  one, the task in a sentence or two.
- **checkout...**: the repos changed, as worktree paths, repo names in the workspace, or a task's worktrees folder
  under the workspace's worktrees folder (it stands for the checkouts inside it).
- **--base**: what the diff is against. Default `origin/<default branch>`, from the config.

## Workflow

### 1. Gather the inputs (and nothing else)

Pick the names to sweep: every public class, method, field, endpoint, property, queue or message shape the diff
removes, renames or changes. Then write the inputs to a file, from the workspace root:

```bash
.claude/toolbox/bin/verify_inputs.py <checkout>... --names <a,b,...> [--task <work id>] [--base <ref>]
```

It fetches, writes each checkout's changed files and full diff (committed and uncommitted), the caller sweep
(`git grep -n -w -F` on the freshly fetched default branch of every repo in a nested workspace, or of the workspace's
areas in a single one) and, with `--task`, the task record's requirement and its design doc's path to one file. It
lists the dependencies whose version bumps the config's `verify.ignore_bumps` excuses, and prints the file's path and
the hit counts. Don't read the file yourself.

Never sweep the main checkouts' working trees or local branches. They sit on whatever branch was last used, and a stale
local branch is normal, so they miss merged code. The script searches only the freshly fetched default branch.

Don't include your own summary of the change, your reasoning, the decisions you made, or what you think might be wrong.
That's the whole point.

### 2. Spawn the verifier

Hook `spawn-the-verifier`. Use the `Agent` tool (`subagent_type: change-verifier`, `run_in_background: false`). The
prompt is only the inputs file's path, plus the task text when there's no task record, plus any extra checks an
extension adds here. The agent reads the file itself; it can't run git, so everything it needs from git is in that file.
Some hosts run every subagent in the background whatever the flag says: if the call returns before the verifier's
report, end the turn and go on to "Report" when it arrives. Never poll for it.

### 3. Report

Pass the verifier's findings through as they are: no rewording, no softening, no extra commentary. If you disagree with
a finding, add one line saying so and why, under it. Don't fix anything here; the caller decides.

## Extension Points

Hooks for extension docs (`.claude/extensions/README.md`). Only checks join the verifier's prompt: never a summary of
the change, which would cost the verifier its independence.

| Id | Runs | Default | Verbs |
|---|---|---|---|
| `spawn-the-verifier` | "2. Spawn the verifier": extra checks for the verifier's prompt | the inputs file's path, plus the task text without a record | add |
