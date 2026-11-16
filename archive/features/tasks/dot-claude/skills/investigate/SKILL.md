---
name: investigate
description: Start any production investigation (an alert, a support ticket, a slowdown or a report) and capture what's found in a discussion. Read-only; ends, when there's a fix to make, in a defect. Use when the user opens with "this alert fired", "why is X restricted", "look into this production problem", "investigate ABC-12" or "this slowdown alert fired, let's look".
allowed-tools: Bash(*), Read, Edit, Write, Grep, Glob, Agent, Skill
---

# Investigate

**Extensions.** Before anything else, run `.claude/toolbox/bin/toolbox.py config extensions investigate --apply` from
the workspace root (its absolute path is in the toolbox-config rule). If it lists docs, read each and apply them at this
skill's hooks ("Extension Points" below) as its `apply` text says.

An investigation finds out what happened and why, and writes it down. It changes nothing: no fix, no re-run that writes,
no comment posted, until the user says so. The record is a discussion, so a later session (or a later alert of the same
kind) starts from what this one found.

It starts from the user's own words: the alert, the ticket key, the dashboard or the report. Ask only for what you can't
find yourself (a link, a time window), never to confirm a title.

## 1. Open the discussion

Follow the `discussion` skill's layout: one problem, one dated folder. Name the slug for the symptom ("rms-sls-slowdown",
"dgs-8038474-no-access"), not the cause.

- First look for an earlier investigation of it: spawn `vault-researcher` with the symptom and the subject (a service, a
  DGS, an alert name; search the vault's discussions and tasks yourself when the workspace doesn't have the agent).
  Extend a discussion that covers the same problem; start a new one otherwise. Say which, with its link.
- `plan.md` gets a Problem section (what fired or was reported, when, who by) before any digging. Everything found
  after that goes under the plan's investigation trail, in the order it was found; evidence too long for the plan
  (a query's result, a timeline) goes in a sibling `<topic>.md`.

## 2. Read the subject

| The subject is | Read it with |
|---|---|
| A ticket key (`ABC-12`) | the `jira-reader` agent when the tracker is `jira` (the fields asked for, not the raw issue); the task record under `tasks.py` otherwise |
| An alert, a dashboard or a report | the link or text the user gave; the observability tools the workspace has (for Dynatrace, its knowledge skill) |
| A service or repo | `workspace-searcher` for where it is defined and who calls it |

State the claim in one line ("service X's p95 doubled at 14:10; the alert says dependency Y") before going on, and put it
in the plan's Problem.

hook `subject-steps`. A project that knows more about a kind of subject adds its steps here (a ticket about one record,
a service of its own). If an extension doc applies to this subject, follow it for the subject's steps and carry on at
"3. Find the cause" with what it found; with none, go straight on.

## 3. Find the cause

Work from evidence, read-only:

1. List what could explain it, then rule each out or in by looking, not by reasoning. Say what you checked and what it
   showed, including what ruled a cause out.
2. Check the timing: when it started, what changed just before (a deploy, a config or dependency change, a spike in
   input), and whether the pattern repeats.
3. Prefer one cheap check over a long chain of inferences; when two checks would settle it, run both before concluding.
4. Never write to production, re-evaluate, re-run, restart or comment to "see if it helps". If the next step does,
   stop and ask.

Append each finding to the plan's investigation trail as you go, so an interrupted session loses nothing.

## 4. Record the findings

Update `plan.md`: the cause (or "not found" and what's left), the evidence for each claim, what was ruled out, and what
isn't known. Keep claims to what the evidence showed. A guess is written as one.

Report the same in chat: cause, evidence, the fix if there is one, and the discussion's link. No more.

## 5. End

Hook `end`. Pick what the findings call for, and propose it; don't do it:

| The findings show | Then |
|---|---|
| A fix to make (a bug, a missing guard, a bad config) | `create-defect` with the symptom, the cause and the evidence; under `jira`, `create-defect` says how to draft it for `create-tickets`. Ask first when the user didn't ask for a defect |
| Work that needs designing before anyone fixes it | continue in the `discussion` skill from this folder: its findings are the design's input |
| A correction to data or a re-run that writes | say exactly what and where, and wait for the user's go-ahead |
| Nothing wrong, or not found | say so; leave the discussion's `status` as it is and name what would reopen it |

A defect found along the way that is unrelated to the investigated problem is a separate `create-defect`, not part of
this one's findings.

## Extension Points

Hooks for extension docs (`.claude/extensions/README.md`).

| Id | Runs | Default | Verbs |
|---|---|---|---|
| `subject-steps` | "2. Read the subject": a project's steps for its own kinds of subject | none: the generic steps | add |
| `end` | "5. End": a project's own follow-ups beyond the table | none | add |
