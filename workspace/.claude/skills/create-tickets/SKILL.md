---
name: create-tickets
description: File a discussion's approved drafts as tasks, the "ready" step after propose-tickets. With the vault tracker it gives them keys and moves them into the tasks folder; with the jira tracker the jira-filer agent files them in Jira and the keys it returns are checked and recorded; drafts that never go to a tracker are filed keyless, as slug-named records. Use when the user says "file these", "create these tickets", "these are ready", names a discussion to file, or says the drafts are vault-only.
---

# Create Tickets

Files the approved drafts in a discussion's `tickets/` folder, so each becomes a task record in the tasks folder with a
key, its `depends_on` turned into keys, the plan's `epic` set and its `## Proposed Stories` rewritten as links. The
toolbox-config rule names the tracker; `tasks.py` (in the scripts folder it names) does every vault write, so never
edit draft or record frontmatter by hand. Every `tasks.py` command prints JSON.

Under the `none` tracker there is nothing to file into: say so, and that `work-task` starts a task straight from its
draft, or that the drafts can be filed keyless (below).

**Vault-only drafts.** When the user says the drafts are vault-only (work that never goes to the tracker, such as this
toolbox's own), or the discussion's plan says so, file them keyless under any tracker: go to step 2a, whatever the
toolbox-config rule's tracker. Offer it when the plan's `## Proposed Epic` says the work never goes to Jira. Don't
choose it for the user otherwise.

## Go-ahead

Filing writes the tracker, so it needs a go-ahead in the current request. An explicit invocation (`/create-tickets`, or
the skill named) is one: show the plan and file. When you inferred that filing was wanted, show the plan and get
agreement first.

## 1. The discussion and its drafts

Use the discussion the user named (a path, a path in the vault, or the folder's name). Otherwise list discussions whose
`tickets/` folder holds drafts and ask which. An older discussion may keep drafts in `jira-tickets/` instead, from
before this draft contract: they are history and never filed from there; an unfiled one is redrafted with
`propose-tickets`.

Only drafts with `status: approved` are filed; the rest are reported as skipped. **Don't assess quality.** The drafts
were reviewed in the vault: an instruction to file means they're ready, so file what's there. Stop only for what can't
be filed: a draft with no summary, or a draft `tasks.py` refuses (it says why).

## 2. Vault tracker

```bash
.claude/toolbox/bin/tasks.py file <discussion> --dry-run
.claude/toolbox/bin/tasks.py file <discussion>
```

Show the dry run's keys, summaries, epic and dependencies (and what it skips, with the reason), then run it for real
on the go-ahead. A run that stopped part way is safe to repeat: a draft already holding a key keeps it. Then step 4.

## 2a. Keyless (vault-only drafts, any tracker)

```bash
.claude/toolbox/bin/tasks.py file <discussion> --keyless --dry-run
.claude/toolbox/bin/tasks.py file <discussion> --keyless
```

Each approved draft becomes `<tasks>/<slug>.md` with an empty `key`; draft numbers in `depends_on` become slugs, links
are repaired and the plan's `## Proposed Stories` is rewritten. It touches nothing in Jira. Show the dry run's
work ids, summaries and dependencies (and what it skips, with the reason), then run it for real on the go-ahead. An
epic draft is refused: the discussion folder groups keyless stories, so drop the epic draft or file it keyed. A run
that stopped part way is safe to repeat. Then step 4; the work ids are slugs, and `work-task <slug>` starts one.

## 3. Jira tracker

### Plan

```bash
.claude/toolbox/bin/tasks.py file <discussion>
```

With no keys this writes nothing and prints the filing plan: `to_file` (each draft's number, path, issue type,
summary, epic, dependencies, estimate, sprint, labels, components), `keyed_not_moved` (drafts an interrupted run gave a
key), `already_filed`, `skipped`, `no_epic` and the configured `fields`. Show it as a table.

- A story with no epic (`no_epic`) can't be filed: ask which epic, unless the user has said it goes without one.
- **Sprint.** A row whose `sprint` is a name needs its id: resolve it with `jira-find-current-sprint` (installed with
  the `jira-sprints` feature) and follow its rules for calling skills. Without that skill, ask for the id; never guess
  one. A flagged result needs the user's pick before anything is filed.
- A `keyed_not_moved` draft was filed by an earlier run: never file it again. Include its key in step 3's `--keys`.

### File with jira-filer

Spawn the `jira-filer` agent (it runs the identity check before its first write) with the discussion, the plan's rows to
file and the sprint ids. It owns the field mechanics (AC field or description, ADF, panels, labels, sprint, estimate)
and returns `Filed: NN=KEY ...`.

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
ask the agent to fix them if the user wants.

A filed issue whose key isn't recorded shows as unfiled and invites a duplicate, so if recording fails, say so loudly
and list the keys.

## 4. Planning time

Once filing is done (step 2, 2a or 3), total the discussion's planning time and record it. Record it on the epic's
record; with no epic, on the one story or defect filed; with several stories and no epic, leave `--record` off, show the
total and record nothing. Use the work id from the filing output.

```bash
.claude/toolbox/bin/session_usage.py --discussion <discussion> --record <work id>
```

Show its `markdown` as is: each session's Claude, your and total time, with its caveats. It writes `planning_time` on
the record, never `usage` or `usage_total`, and a re-run recomputes the whole total. It names any session whose
transcript is gone (Claude Code deletes them after 30 days) and keeps that session's earlier time when the record
already held it. To count a session whose writes it can't find, pass `--session <id>`.

## 5. Report

The planning time from step 4 and the record it went on, then a table of number, key, summary and record (with each
key's URL under `jira`), plus what was skipped and why, the testing-notes comments and dependency links (`jira`), and
any warnings. If some filed and others didn't, say exactly which; never imply a clean sweep.

## Failure handling

- Partial failure is normal: record the keys that filed before reporting the ones that didn't.
- Never retry a create after an ambiguous error without searching for the summary first: a timeout may have created
  the issue. The agent does this; check it did.
- An interrupted run is resumed by running this skill again: `tasks.py file` picks up drafts that hold a key, and the
  plan shows what's left.

## Notes

- Obsidian may have the files open; writes still land, and it shows them on refocus.
- No git. The vault may live in a repo, but commits are the user's.
