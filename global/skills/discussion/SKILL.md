---
name: discussion
description: Start or extend a design discussion in an Obsidian vault - one problem per dated folder, a plan.md design record, optional questions.md and topic docs. Use when the user opens a design, planning or workflow-optimization session ("let's design X", "how should we handle X", "plan X", "think through X", a change to the toolbox itself) or says "start a discussion", "create a discussion plan", "capture this in the vault" or "write this up".
---

# Discussion

A discussion is the user's unit of design work: one problem, one folder, kept as the record of what was found and
decided. Most discussions never produce tickets; tickets are an optional last step (`propose-tickets`), not the
purpose.

It starts from the user's own words, with no slash command: their description of the problem names the folder's slug,
and the first step is to look for an existing discussion on it. Ask only when the problem itself is unclear, never to
confirm a title.

The vault comes from the global obsidian rule: the one the session's own rules name, else the Personal vault. The
discussions folder is the one the toolbox-config rule names, in a workspace that has one; else `Discussions/` at the
vault root. Notes are never hard-wrapped (obsidian rule).

## Layout

```
<discussions>/<YYYY>/<MM>/<YYYY-MM-DD>-<slug>/
├── plan.md          always
├── questions.md     optional: decision log plus parked questions
├── <topic>.md       optional: one reference doc per sub-topic
└── tickets/         story and epic drafts, only when tickets are proposed
```

- The date is the day the discussion starts, and the year and month folders come from it. `<slug>` is the title in
  lowercase with every run of other characters turned into `-`.
- Before creating one, list the discussions folder for an existing discussion on the same problem and extend it
  instead. One problem per folder; split genuinely unrelated efforts into two.
- Wikilinks use the full vault path, including year and month:
  `[[Discussions/2026/10/2026-10-20-pantry-shelves/plan|Pantry Shelves]]`.

## plan.md

The living design record: problem, decisions with their reasons and rejected alternatives, the investigation trail,
implementation detail, open questions and out of scope.

- Frontmatter is exactly `type: plan`, `title`, `slug`, `epic`, `created` and `status` (`draft` on creation). Vault
  dashboards run Dataview over these keys, so a missing or renamed key silently drops the discussion from them.
- Sections, in order: Problem, Design Decisions, Implementation Detail, Dependencies, Proposed Epic, Proposed Stories,
  Open Questions, Out of Scope. In a workspace, the vault's `Templates/discussion-plan.md` (Templater, which the toolbox
  installer writes from the workspace's config) and the toolbox's `templates/plan.md` both make this shape. A
  discussion that proposes no tickets may leave the two Proposed sections empty and add its own headings.
- Keep it current. When a decision reverses or the effort is cancelled, update the plan and its `status` in the same
  session, so the vault never reads as if the old decision still holds.

## Sibling docs

- Anything too long for the plan (an investigation, a measurement, a checklist, a sub-design) gets its own
  `<topic>.md` with `type: reference` and `created:` frontmatter. Its first line under the title links back:
  `Back to [[<discussion path>/plan|plan]].` The plan links to each sibling where it is relevant.
- `questions.md` holds a `## Decided` table (`| Date | Decision |`, newest last) and the open or parked questions. Once
  it exists, the plan's `## Open Questions` points to it rather than duplicating it.
- Files that start a session (`prompt-NN-*.md`, "Carry out <path>") are `type: prompt`, never `type: task`, so they
  never mix with task records.

## tickets/

Drafts follow the workspace's `.claude/toolbox/references/ticket-docs.md` and are owned by `/propose-tickets`
(drafting) and `/create-tickets` (filing). Never write or rename a draft outside those skills, except `create-defect`,
which under the `jira` tracker writes a defect draft into a discussion's `tickets/` for `create-tickets` to file. An
empty `tickets/` folder is normal: the Templater template makes it.

## Memory and the vault

The vault is the record; memory only points at it. When a discussion matters across sessions, a memory file names the
discussion folder and says why it matters. Decisions, findings and their reasons go in the discussion, not in memory.

## Vault templates

A workspace's `Templates/discussion-plan.md` and `Templates/scripts/` are written by the toolbox installer (the
`obsidian` feature), and the next sync overwrites any edit made in the vault. Change them in the toolbox's
`workspace/.vault/` instead.
