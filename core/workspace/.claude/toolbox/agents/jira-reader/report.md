# jira-reader Report

Return only this block, leaving out lines that don't apply. Never include an issue's JSON, rendered HTML, a comment nobody
asked for, a changelog, a field that wasn't asked for, or advice on what to do about what you read.

```
found: <n> issues | none | blocked: <reason>
fields: <the fields asked for, in the order each issue line gives them>
query: <the JQL, when one was run>
saved: <the absolute path of the file written, in save mode>
issues:
  - <KEY> | <value of field 1> | <value of field 2> | ...
missing: <KEY>: <why it couldn't be read>, ...
notes: <what the asker needs to know: issues over the limit, a custom field's id, a JQL that may miss what was meant>
```

- An issue's line starts `- `, then its key, then one value per field in `fields` order, each after ` | `. A value is
  plain text on the one line, `none` when the field is empty. Nothing else starts with `- `.
- In save mode `saved` is the path and each issue line is `- <KEY> | <issue type> | <status> | ac: present|missing`
  (`ac` says whether the acceptance criteria are filled in, from the field id the prompt gave, else the description's
  own `Acceptance Criteria` heading); no field's value is reported.
- A value that spans lines is one line, joined with ` ¶ `, and carries its whole content; a `|` in it is written `\|`.
- `found: none` carries no `issues`; `found: blocked` carries only the reason.
- Order the issues as given (keys) or as Jira returned them (JQL).

## Contract

The report contract gate checks the report against this block when you hand it back, and denies one that breaks it,
naming what is wrong; fix the report and hand it back again. Its lines are `require <field>: <regex>` (some line
matches), `forbid <what>: <regex>` (no line matches) and `limit <n> <what>: <regex>` (at most n lines match);
`lib/report_contract.py` has the format.

```contract
require found: ^found: (\d+ issues?|none|blocked: .+)\s*$
forbid issue that isn't a key and its values: ^\s*- (?![A-Z][A-Z0-9]*-\d+( \| |\s*$))
forbid saved path that isn't absolute: ^saved: (?!/\S+\.json\s*$)
forbid code block: ^\s*```
forbid pasted JSON: ^\s*([{\[]|"[A-Za-z_]+"\s*:)
forbid rendered HTML: </?(p|div|span|br|ul|ol|li|table|tr|td|strong|em|h[1-6]|pre|code)\b[^>]*>
forbid API urls: https?://\S*/rest/api
forbid table rows: ^\s*\|
limit 200 issues: ^\s*- [A-Z][A-Z0-9]*-\d+
```
