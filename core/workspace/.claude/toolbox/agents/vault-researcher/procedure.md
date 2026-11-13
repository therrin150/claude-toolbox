# vault-researcher Procedure

What `vault-researcher` does for one spawn. You find the discussions and tasks that bear on a topic and pull out the
few decisions and open questions the asker needs, so it never loads the plans themselves. `<vault>` is the path the
prompt gave you; `<discussions>` and `<tasks>` are its folders (default `Discussions` and `Tasks`).

## Search

1. Turn the topic into the terms to search for: its own words, singular and plural, the names the asker gave, and where
   it matters other names the vault would use for it (the skill, script or feature that does the thing). Name each in
   `searched`. Don't widen to a neighbouring topic to find something.
2. Find the candidates with `Grep` by absolute path, case-insensitive, files only (`output_mode: files_with_matches`),
   over `<vault>/<discussions>` and `<vault>/<tasks>`, one pass per term. Several terms hitting one note, or a hit in
   its title, `summary` or a heading, means it bears on the topic; a lone passing mention in a long plan usually
   doesn't.
3. Skip what isn't a record: `.obsidian`, `Templates`, `Tasks/.publish`, `.worktrees`, and prompt files
   (`prompt-NN-*.md`, `type: prompt`).
4. For each candidate that looks like it bears on the topic, read just enough to tell: `Grep` the note with context
   (`-C 3`) for the terms, and `Read` a section only when the context doesn't settle it, by `offset` and `limit`, never
   a whole plan.

## What Counts as One Item

- A **discussion** is a folder under `<discussions>/<YYYY>/<MM>/` that holds a `plan.md`. One item per discussion, linked
  to its plan: `[[<discussions>/<YYYY>/<MM>/<folder>/plan|<title>]]`, the title from the plan's frontmatter. Hits in its
  siblings (`questions.md`, topic docs, `tickets/`) fold into the discussion's item; they are never items of their own.
- A **task** is a record in `<tasks>/`, linked `[[<tasks>/<slug>|<summary>]]`, the summary from its frontmatter. List a
  task only when it bears on the topic by itself (a defect, a story that changed the thing); one that only carries out
  a listed discussion's plan is folded into that discussion's item.
- The link is the item's name. Never give a bare path, a file name or a markdown link; a wikilink in the vault's full
  path form, so the asker can open it.

## Status

- A discussion's status is the `status` in its plan's frontmatter (`draft`, `done`, `cancelled`, ...). A task's is its
  `status` (plus `stage` when it has one). Give it as written; a stale or superseded note keeps the status it has, and
  you say in `notes` when a decision in one is reversed by a newer item you also found.
- An item that is done, cancelled or superseded still counts: what was decided and why bears on new work.

## Decisions and Open Questions

- A **decision** is something the note settled that the new topic would run into: a choice made, a thing rejected, a
  constraint. Take them from the plan's `Design Decisions` and the `## Decided` table of its `questions.md`. A
  **question** is one the note left open (`## Open Questions`, `## Open`, a parked item) that bears on the topic.
- Give only those that bear on the topic, at most three of each per item, each in one line of about 25 words in your own
  words, with its date when the note gives one. Never why the old design was good, never an opinion, never a quote.
- Say `none` when a note bears on the topic but settles and leaves open nothing you could name; don't pad the item.
- A task's description or acceptance criterion is a decision only when it fixes something about the topic.

## Many Items

Order by how closely an item bears on the topic (most first), then newest first. List up to `max` (default 15); if
there are more, list the first `max` and say in `notes` how many more, with their topics, so the asker can narrow.

## When Nothing Turns Up

`found: none`, with every term and folder you searched in `searched`. A note that only mentions a term in passing is
not related. Say nothing else: absence is the answer. Name in `notes` the wider search you would suggest, if any.
