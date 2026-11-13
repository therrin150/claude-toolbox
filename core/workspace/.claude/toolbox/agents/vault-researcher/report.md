# vault-researcher Report

Return only this block, leaving out lines that don't apply. Never include a plan's text beyond one line per decision or
question: no excerpt, no frontmatter, no table, no code block, no advice on what to decide.

```
found: <n> items | none | blocked: <reason>
searched: <each term searched> in <each folder searched>
items:
  - [[<vault path of the note>|<title>]] - discussion|task, <status>
    decisions: <one line each, separated by "; ", or none>
    open: <one line each, separated by "; ", or none>
notes: <what the asker needs to know about the search: items over the limit, a decision a newer item reverses, a wider search you'd suggest>
```

- An item's first line starts `- `, then the wikilink in the vault's full path form (a discussion's links to its
  `plan`), then `discussion` or `task` and the status as written in the note. Nothing else starts with `- `.
- `decisions` and `open` are the item's two continuation lines, indented under it, each at most about 100 words.
- `found: none` carries no `items`; `found: blocked` carries only the reason.
- Order the items by how closely they bear on the topic, then newest first.

## Contract

The report contract gate checks the report against this block when you hand it back, and denies one that breaks it,
naming what is wrong; fix the report and hand it back again. Its lines are `require <field>: <regex>` (some line
matches), `forbid <what>: <regex>` (no line matches) and `limit <n> <what>: <regex>` (at most n lines match);
`lib/report_contract.py` has the format.

```contract
require found: ^found: (\d+ items?|none|blocked: .+)\s*$
require searched: ^searched: .+
forbid code block: ^\s*```
forbid item that isn't a wikilink with its kind and status: ^\s*- (?!\[\[[^\]|]+(\|[^\]]+)?\]\] - (discussion|task), \S)
forbid item line over 300 characters: ^\s*- \[\[.{298,}$
forbid decisions or open line over 800 characters: ^\s+(decisions|open): .{790,}$
forbid frontmatter lines: ^(---|(type|slug|epic|created|source_doc): )
forbid table rows: ^\s*\|
limit 30 items: ^\s*- \[\[
```
