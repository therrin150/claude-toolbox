# workspace-searcher Report

Return only this block, leaving out lines that don't apply. Never include file contents beyond the one-line role of a
location: no code block, no stack trace, no copied lines, no advice on what to change.

```
found: <n> locations in <m> repos | none | blocked: <reason>
searched: <each spelling searched> in <each repo swept>
locations:
  - <path relative to the root>:<line> - <one-line role>
notes: <what the asker needs to know about the sweep: locations over the limit and where, a spelling you didn't search, a wider search you'd suggest>
```

- Paths are relative to the root the prompt gave, so the first folder is the repo. Order by repo, then path.
- A test's role ends `(test)`; a definition's role is `defines it`.
- `found: none` carries no `locations`; `found: blocked` carries only the reason.
- One line per location, with no continuation lines: a role that needs more than about 15 words is two things, so list
  two locations or say `use not clear from the hit`.

## Contract

The report contract gate checks the report against this block when you hand it back, and denies one that breaks it,
naming what is wrong; fix the report and hand it back again. Its lines are `require <field>: <regex>` (some line
matches), `forbid <what>: <regex>` (no line matches) and `limit <n> <what>: <regex>` (at most n lines match);
`lib/report_contract.py` has the format.

```contract
require found: ^found: (\d+ locations? in \d+ repos?|none|blocked: .+)\s*$
require searched: ^searched: .+
forbid code block: ^\s*```
forbid location without a role: ^\s*- \S+:\d+\s*$
forbid location line over 260 characters: ^(?=\s*- \S+:\d+ - ).{260,}$
forbid file contents on a line of their own: ^\s*\d+[:|]\s
limit 150 locations: ^\s*- \S+:\d+ - 
```
