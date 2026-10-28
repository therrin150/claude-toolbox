---
name: propose-tickets
description: Capture a design discussion as a vault discussion folder, a plan.md holding the full design record plus one draft per proposed story (and an optional epic) in its tickets/ folder, for review before create-tickets files them. Use when Allen says "propose tickets", "draft stories for this", "capture this", "write this up", or wants a discussion broken into stories.
---

# Propose Tickets

Turns a discussion into a discussion folder in the vault: a plan and a draft per story. The output is a review
surface. Nothing is filed and no tracker is written; `create-tickets` does that once Allen approves the drafts.

The ticket-docs rule owns the draft contract (frontmatter, numbering, titles, acceptance criteria); this skill owns the
workflow. The toolbox-config rule names the vault, the discussions folder and the tracker.

## Style guide

Before writing any draft, read `~/.claude/personal/ticket-style.md` and the `~/.claude/personal/voice.md` it refers to,
when they exist. They govern titles, description shape and length, voice, acceptance criteria and panels, over the
ticket-docs rule's defaults. If they don't exist, say once, without blocking, that a style guide at that path lets
tickets be written in Allen's voice, and use the rule.

## Output

```
<discussions>/<YYYY>/<MM>/<YYYY-MM-DD>-<slug>/
├── plan.md
└── tickets/
    ├── 00-<epic-slug>.md       only when the discussion proposes a new epic
    ├── 01-<story-slug>.md
    └── 02-<story-slug>.md
```

- The folder's name, its reuse and full-path wikilinks follow the global obsidian rule's Discussions section; the
  date is today. Make the year and month folders if they don't exist.
- Drafts are numbered in dependency order, foundation first; `00` is reserved for a new epic, at most one. Never put a
  key in a draft's name or frontmatter, and never renumber a draft another doc refers to: filing does the keys.

## Plan versus drafts

| | `plan.md` | `tickets/*.md` |
|---|---|---|
| For | Allen, and whoever resumes the work | Whoever implements the task, and Allen verifying it |
| Holds | Problem, decisions and their reasons, rejected alternatives, the investigation trail, full implementation detail, dependencies, open questions, out of scope | What the work needs to start and to be verified: context, a directive, the detail to act on, AC, optional testing notes |
| Names, endpoints, tables, payloads, config keys | All of them: the record | The subset a story needs |
| Open questions | Yes (or a sibling `questions.md`) | Never |
| Kept current | Yes, as the work evolves | No, frozen once filed |

**A draft stands on its own.** Under the `jira` tracker it becomes an issue nobody can follow back to the vault, and
under any tracker a session works from the task record alone. So a draft's Description, AC and testing notes never
refer to the plan or depend on it; the `Plan:` line is scaffolding. Draw from the plan what the work needs to begin,
and present anything the plan hasn't settled as a suggestion ("may look something like this").

**Size.** A story is one small, testable unit: one behavior that can be verified on its own, usually in one to three
AC scenarios. Split a story that bundles behaviors which could each ship and be tested alone. Flag any story that
can't carry what it needs within the style guide's description budget, needs more than five scenarios, or would take
more than about a day, and propose a split.

**Steps only Allen can do** (an account to create, a secret to set, a decision to make) become their own drafts,
`owner: <Allen's owner value>`, which the stories needing them list in `depends_on`.

## Workflow

### 1. Settle the epic: the one question you must ask

Always ask; never infer the epic.

- **An existing epic:** list the candidates and let Allen pick. With the `jira` tracker, search the configured project
  for open epics (`searchJiraIssuesUsingJql`, `project = <PROJECT> AND issuetype = <epic issue type> AND
  statusCategory != Done ORDER BY updated DESC`, requesting only `summary` and `status`). With the `vault` tracker,
  `tasks.py epic-status` lists them. Put the key in the plan's `epic` and in every story's `epic`.
- **A new epic:** when nothing fits, ask whether to propose one. Write `tickets/00-<epic-slug>.md` (`type: epic`) and
  leave the plan's and the stories' `epic` empty: filing gives the epic its key first and fills it in.
- **No epic:** fine when Allen says so, and under the `none` tracker.

An epic draft has a single `## Description` of plain paragraphs at initiative altitude (what is wrong or missing across
the effort, and what is true once every story ships), no AC.

Everything else takes its default silently, unless Allen says otherwise in the same request.

### 2. Agree the breakdown before writing files

List the stories as one-line summaries in dependency order, with the epic, and agree the split first. Rewriting a list
is much cheaper than rewriting files.

### 3. Write plan.md

Start from the toolbox's `templates/plan.md` (in the scripts folder's parent) and keep its frontmatter keys and section
order. Fill `## Implementation Detail` generously: entry points, packages and classes, endpoints, tables and
migrations, UI components and routes, config keys, sequencing. `## Proposed Stories` lists the drafts by number until
filing rewrites it with links to the filed tasks. Open questions go in `## Open Questions` or a sibling
`questions.md`, never in a draft.

### 4. Write one draft per story

Frontmatter per the ticket-docs rule's draft contract, with `status: draft`. The body is the matching template's body
(`templates/story.md`, `epic.md` or `defect.md`), minus its comments and the `## Log` section, which filing adds:

```
# <summary>

Plan: [[<discussion>/plan|<discussion folder name>]]

## Description
## Acceptance Criteria
## Testing Notes          optional: leave the heading out when there are none
```

- **Testing notes:** 1-3 bullets only when testing needs more than the AC imply (test-data steps, a query and what it
  should show, log lines to watch). Most stories have none. Under the `jira` tracker they become a comment on the issue.
- **Panels** are Obsidian callouts (`> [!info]`, `> [!warning]`); under `jira` they become Jira panels.
- **Spikes** are stories whose summary starts `Spike: `: the description ends with the question to answer, and there
  is no AC section.
- **Jira fields**, under the `jira` tracker only and only when Allen asks for them: `estimate` (`1d`), `sprint` (a
  sprint's name: resolve "this sprint" or "next sprint" with `jira-find-current-sprint` and write the name, never the
  word), `labels` beyond the configured ones, and `components` (names).

### 5. Style pass

Before reporting, re-read `~/.claude/personal/ticket-style.md` and `voice.md` from disk (or the ticket-docs rule
without them) and check every draft against them rule by rule: titles, description shape and budget, voice,
formatting, panels, AC, testing notes. Fix violations in place. A mechanical fix never alters text quoted from a
source (a log line, an error message, javadoc).

### 6. Report

The discussion folder, the story count, and each summary with its number, marking those with testing notes and any
flagged as too large. Say that nothing has been filed, that each draft needs `status: approved` before it can be, and
that `create-tickets` is next.
