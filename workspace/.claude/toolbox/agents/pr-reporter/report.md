# pr-reporter Report

Return only this block, leaving out lines that don't apply. Never include a diff hunk, comment JSON, a CI log or a
timestamped log line, or advice on what to do about what you read.

```
pr: <owner/name>#<number> <state>; review: <APPROVED | CHANGES_REQUESTED | REVIEW_REQUIRED | NONE>; checks: <n> failed, <n> pending, <n> passed
found: <n> comments | none | blocked: <reason>
comments:
  - <path>:<line> | <reviewer> | <change | question | nit | resolved> | <what's asked>
  - review body | <reviewer> | <kind> | <what's asked>
  - conversation | <author> | <kind> | <what's asked>
failed:
  ! <check name> | <failing step>
      <error line>
notes: <pending checks by name, threads or checks left out, anything the asker needs to know>
```

- `found` counts the `comments` lines, resolved ones included. A thread on a whole file or one with no line is
  `<path>:file`. A comment line starts `- ` in column one or two, is on one line, and has exactly the four fields, the
  ask last. Nothing else starts that way.
- A failed check is one `! ` line, then its error lines indented six spaces, one per line, exactly as the script gave
  them; a check with none gives the script's `note` as its one line. Nothing else is indented six spaces.
- `found: none` carries no `comments` line, and still carries `failed` when checks failed: a PR with failed checks and
  no comments is `found: none` with `failed:`. `found: blocked` carries only the reason, with no `pr` line.
- Order the comments as the script gave them, then review bodies, then conversation.

## Contract

The report contract gate checks the report against this block when you hand it back, and denies one that breaks it,
naming what is wrong; fix the report and hand it back again. Its lines are `require <field>: <regex>` (some line
matches), `forbid <what>: <regex>` (no line matches) and `limit <n> <what>: <regex>` (at most n lines match);
`lib/report_contract.py` has the format.

```contract
require pr line: ^(pr: \S+/\S+#\d+ \S+; review: \S+; checks: \d+ failed, \d+ pending, \d+ passed|found: blocked: .+)\s*$
require found: ^found: (\d+ comments?|none|blocked: .+)\s*$
forbid comment without its four fields: ^ {0,3}- (?!(\S+:(\d+|file)|review body|conversation) \| [^|]+ \| (change|question|nit|resolved) \| \S)
forbid comment over 600 characters: ^ {0,3}- .{600,}$
forbid failed check without a step: ^ {0,3}! (?![^|]+ \| \S)
forbid raw log line: ^\s*\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d
forbid diff hunk: ^\s*@@ -\d+
forbid code block: ^\s*```
forbid pasted JSON: ^ {0,3}([{\[]|"[A-Za-z_]+"\s*:)
forbid table rows: ^ {0,3}\|
forbid api urls: https?://\S*api\.github\.com
forbid error line over 400 characters: ^ {6}\S.{400,}$
limit 200 comments: ^ {0,3}- \S
limit 10 failed checks: ^ {0,3}! \S
limit 150 error lines: ^ {6}\S
```
