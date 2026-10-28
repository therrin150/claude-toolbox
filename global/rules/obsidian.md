# Obsidian

## Personal Vault

My **Personal** vault is iCloud-synced and also syncs to my iPhone. On macOS it lives at:

```
~/Library/Mobile Documents/iCloud~md~obsidian/Documents/Personal
```

Use it when I ask for a "personal" note, vault or workspace, and as the default when the session's own rules name no
other vault. A project that is itself a vault (it has an `.obsidian/` folder) uses that vault for its own notes. Do not
create Obsidian notes anywhere a rule or I haven't named. When in doubt about which vault I mean, ask.

Obsidian rewrites a vault's `.obsidian/` folder while it runs: never edit plugin settings with it open; ask me to quit
it first.

## Formatting

Never hard-wrap prose in Obsidian notes. Write each paragraph as a single line and let the editor soft-wrap it.
Obsidian's "Readable line length" setting controls the display measure, so a newline at a fixed column lands
mid-sentence and renders ragged, and editing a sentence forces a manual re-flow of every line after it.

Intentional line breaks stay one per line: table rows, list items, code blocks, callout body lines, and GIVEN / WHEN /
THEN / AND / OR acceptance-criteria clauses.

This applies to notes in Obsidian vaults only. Keep markdown inside repositories hard-wrapped at 120.

## Discussions

The same in every vault. A discussion is my unit of design work: one problem, one folder, kept as the record of what
we found and decided. "Start a discussion", "create a discussion plan", "capture this in the vault" and "write this up"
all mean create or extend one. Most discussions never produce tickets; tickets are an optional last step, not the
purpose.

The discussions folder is the one the toolbox-config rule names, in a workspace that has one; else `Discussions/` at
the vault root.

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

### plan.md

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

### Sibling docs

- Anything too long for the plan (an investigation, a measurement, a checklist, a sub-design) gets its own
  `<topic>.md` with `type: reference` and `created:` frontmatter. Its first line under the title links back:
  `Back to [[<discussion path>/plan|plan]].` The plan links to each sibling where it is relevant.
- `questions.md` holds a `## Decided` table (`| Date | Decision |`, newest last) and the open or parked questions. Once
  it exists, the plan's `## Open Questions` points to it rather than duplicating it.
- Files that start a session (`prompt-NN-*.md`, "Carry out <path>") are `type: prompt`, never `type: task`, so they
  never mix with task records.

### tickets/

Drafts follow a workspace's `ticket-docs` rule and are owned by `/propose-tickets` (drafting) and `/create-tickets`
(filing). Never write or rename a draft outside those skills. An empty `tickets/` folder is normal: the Templater
template makes it.

### Memory and the vault

The vault is the record; memory only points at it. When a discussion matters across sessions, a memory file names the
discussion folder and says why it matters. Decisions, findings and their reasons go in the discussion, not in memory.

### Vault templates

A workspace's `Templates/discussion-plan.md` and `Templates/scripts/` are written by the toolbox installer (the
`obsidian` feature), and the next sync overwrites any edit made in the vault. Change them in the toolbox's
`workspace/.vault/` instead.
