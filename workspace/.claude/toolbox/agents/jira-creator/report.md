# jira-creator Report

End with this block, exactly, leaving out lines that don't apply; then any detail. Never paste an issue's JSON or
ADF, a converted file's contents, an API URL or a log path.

```
Filed: 00=ABC-20 01=ABC-21 02=ABC-22
Not filed: 03 (the create failed: <error>)
Comments: 01 posted
Links: ABC-21 blocks ABC-22
Problems: none
```

- `Filed` lists only issues that exist in Jira, as `<draft number>=<key>`; `Filed: none` when nothing was created
  (including a converter failure). Each key's browse URL is `https://<site>/browse/<KEY>`.
- A converter failure is `Filed: none` and one `Problems:` entry per failing draft, the converter's message as
  printed.
- For an edit, `Filed` is `none` and `Problems` says what was changed (or `none` when the change was made).

## Contract

The report contract gate checks the report against this block when you hand it back, and denies one that breaks it,
naming what is wrong; fix the report and hand it back again. Its lines are `require <field>: <regex>` (some line
matches), `forbid <what>: <regex>` (no line matches) and `limit <n> <what>: <regex>` (at most n lines match);
`lib/report_contract.py` has the format.

```contract
require filed: ^Filed: (none|\d+=[A-Z][A-Z0-9]*-\d+( \d+=[A-Z][A-Z0-9]*-\d+)*)\s*$
require problems: ^Problems: .+
forbid pasted JSON: ^\s*([{\[]|"[A-Za-z_]+"\s*:)
forbid API urls: https?://\S*/rest/api
forbid code block: ^\s*```
forbid ADF: "type"\s*:\s*"(doc|paragraph|panel)"
```
