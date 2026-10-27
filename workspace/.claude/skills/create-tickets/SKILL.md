---
name: create-tickets
description: File a discussion's approved drafts as tasks, the "ready" step after propose-tickets. With the vault tracker it gives them keys and moves them into the tasks folder; with the jira tracker the jira-filer agent files them in Jira and the keys it returns are checked and recorded. Use when Allen says "file these", "create these tickets", "these are ready", or names a discussion to file.
---

# Create Tickets

Files the approved drafts in a discussion's `tickets/` folder, so each becomes a task record in the tasks folder with a
key, its `depends_on` turned into keys, the plan's `epic` set and its `## Proposed Stories` rewritten as links. The
toolbox-config rule names the tracker; `tasks.py` (in the scripts folder it names) does every vault write, so never
edit draft or record frontmatter by hand. Every `tasks.py` command prints JSON.

Under the `none` tracker there is nothing to file into: say so, and that `work-task` starts a task straight from its
draft.

## Go-ahead

Filing writes the tracker, so it needs a go-ahead in the current request. An explicit invocation (`/create-tickets`, or
the skill named) is one: show the plan and file. When you inferred that filing was wanted, show the plan and get
agreement first.

## 1. The discussion and its drafts

Use the discussion Allen named (a path, a path in the vault, or the folder's name). Otherwise list discussions whose
`tickets/` folder holds drafts and ask which.

Only drafts with `status: approved` are filed; the rest are reported as skipped. **Don't assess quality.** The drafts
were reviewed in the vault: an instruction to file means they're ready, so file what's there. Stop only for what can't
be filed: a draft with no summary, or a draft `tasks.py` refuses (it says why).

## 2. Vault tracker

```bash
.claude/toolbox/bin/tasks.py file <discussion> --dry-run
.claude/toolbox/bin/tasks.py file <discussion>
```

Show the dry run's keys, summaries, epic and dependencies (and what it skips, with the reason), then run it for real
on the go-ahead. A run that stopped part way is safe to repeat: a draft already holding a key keeps it. Then report.

## 3. Jira tracker

### Plan

```bash
.claude/toolbox/bin/tasks.py file <discussion>
```

With no keys this writes nothing and prints the filing plan: `to_file` (each draft's number, path, issue type,
summary, epic, dependencies, estimate, sprint, labels, components), `keyed_not_moved` (drafts an interrupted run gave a
key), `already_filed`, `skipped`, `no_epic` and the configured `fields`. Show it as a table.

- A story with no epic (`no_epic`) can't be filed: ask which epic, unless Allen has said it goes without one.
- **Sprint.** A row whose `sprint` is a name needs its id: resolve it with `jira-find-current-sprint` (installed with
  the `jira-sprints` feature) and follow its rules for calling skills. Without that skill, ask for the id; never guess
  one. A flagged result needs Allen's pick before anything is filed.
- A `keyed_not_moved` draft was filed by an earlier run: never file it again. Include its key in step 3's `--keys`.

### Identity

Follow `references/identity-check.md` in the toolbox folder (the scripts folder's parent) for the Jira server before
filing anything. Stop on a mismatch.

### File with jira-filer

Spawn the `jira-filer` agent with the discussion, the plan's rows to file and the sprint ids. It owns the field
mechanics (AC field or description, ADF, panels, labels, sprint, estimate) and returns `Filed: NN=KEY ...`.

1. **The epic first.** If `to_file` has an epic draft, ask the agent to file only the epic and return. Record it at
   once (the next step, with just `00=<KEY>`), so the plan holds the epic's key before any story exists. If the epic
   fails, stop: never file stories under no epic.
2. **Then the stories,** in a second call, from a fresh `tasks.py file <discussion>` plan: their epic is now the
   plan's. The agent files them in number order, posts testing notes as comments and links dependencies.

If the agent reports missing Jira tools or the identity check failing, say so; never file any other way.

### Check and record

After each agent call, fetch what it filed yourself, independently of its report: `searchJiraIssuesUsingJql` with
`key in (<keys>)` and fields `summary`, `issuetype`, `parent`, `labels` and `issuelinks`. Save the response, exactly as
returned, to a file in the scratchpad (or the session's temporary folder), then:

```bash
.claude/toolbox/bin/tasks.py file <discussion> --keys 00=ABC-20 01=ABC-21 ... --issues <file> --dry-run
.claude/toolbox/bin/tasks.py file <discussion> --keys 00=ABC-20 01=ABC-21 ... --issues <file>
```

Pass only the drafts that actually filed, plus any `keyed_not_moved`. The script checks every key against the saved
issues (the site, the summary, the issue type, a story's parent epic) and writes nothing if any disagree; it refuses a
key another record holds and a draft that already holds a different key, which is the duplicate-filing guard: never
force past it. On a mismatch, report it and stop: the issues exist in Jira, so the fix is to correct the mapping (or
the issue), never to file again. `warnings` (a missing `Blocks` link or label) don't stop anything: report them, and
ask the agent to fix them if Allen wants.

A filed issue whose key isn't recorded shows as unfiled and invites a duplicate, so if recording fails, say so loudly
and list the keys.

## 4. Report

A table of number, key, summary and record (with each key's URL under `jira`), plus what was skipped and why, the
testing-notes comments and dependency links (`jira`), and any warnings. If some filed and others didn't, say exactly
which; never imply a clean sweep.

## Failure handling

- Partial failure is normal: record the keys that filed before reporting the ones that didn't.
- Never retry a create after an ambiguous error without searching for the summary first: a timeout may have created
  the issue. The agent does this; check it did.
- An interrupted run is resumed by running this skill again: `tasks.py file` picks up drafts that hold a key, and the
  plan shows what's left.

## Notes

- Obsidian may have the files open; writes still land, and it shows them on refocus.
- No git. The vault may live in a repo, but commits are Allen's.
