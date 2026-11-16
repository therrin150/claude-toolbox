# diff-summarizer Report

Return only this block, leaving out lines that don't apply. Never include a diff hunk, the digest's JSON, why a change was
made, or advice on what to do about what you read.

```
found: <n> repos | none | blocked: <reason>
<repo>: +<added> -<removed>, <n> files, against <base>
  - <what the diff changes> | <files it touches>
  ! <what looks out of place> | <file[:line]>
notes: <flags or files left out, a checkout that failed, anything the asker needs to know>
```

- One `<repo>` line per checkout, then its changes (`  - `, two spaces), then its out-of-place lines (`  ! `). A repo with
  no changes still gets its line, with `+0 -0, 0 files`.
- A change or out-of-place line is on one line, with exactly two fields: the text, then `|` and the files. Nothing else
  starts with `  - ` or `  ! `.
- Changes go biggest first (a sweep over many files before a one-line fix), in each repo.
- `found: none` carries no repo lines. `found: blocked` carries only the reason.

## Contract

The report contract gate checks the report against this block when you hand it back, and denies one that breaks it,
naming what is wrong; fix the report and hand it back again. Its lines are `require <field>: <regex>` (some line
matches), `forbid <what>: <regex>` (no line matches) and `limit <n> <what>: <regex>` (at most n lines match);
`lib/report_contract.py` has the format.

```contract
require found: ^found: (\d+ repos?|none|blocked: .+)\s*$
forbid line that is no repo, change, flag or note: ^(?!found:|notes:|\S[^:]*: \+\d+ -\d+, \d+ files?, against \S+\s*$|  [-!] \S)\S|^ (?! [-!] \S)\s*\S|^   +\S
forbid change or flag without its files: ^  [-!] (?![^|]+ \| \S)
forbid line over 400 characters: ^.{400,}$
forbid why: (?i)\b(because|in order to|so that|the reason|presumably|probably|intended to|meant to|aims to|to fix|to support)\b
forbid diff hunk: ^\s*@@ -\d+
forbid diff header: ^\s*(diff --git|index [0-9a-f]+\.\.|(\+\+\+|---) [ab/])
forbid code block: ^\s*```
forbid pasted JSON: ^\s*([{\[]|"[A-Za-z_]+"\s*:)
forbid table rows: ^\s*\|
limit 150 change lines: ^  - \S
limit 60 out of place lines: ^  ! \S
```
