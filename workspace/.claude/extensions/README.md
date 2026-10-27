# Extensions

An extension hooks a project's own steps into a generic toolbox skill without forking it: a Sonar scan before
`work-task`'s first review stop, a network check before integration tests, another way to start the services. It is
one markdown doc in this folder, `.claude/extensions/`, kept in the project's repo or linked in by a glue toolbox's
install. The skill it names finds it here; no config lists it. This README is never read as an extension.

## The doc

```markdown
---
type: extension
extends: work-task
---

# Sonar Scan

What this extension is for. Text before the first section is context only.

## add review-checks

Run `/xx-sonar-scan` on each applying area from `worktrees.py changes <work id>`. Its findings join the
Fix/Decline/Ask triage.

## skip verify

The scan covers what the verifier would; verification runs in CI.
```

- `extends:` names one skill; a doc extends only that one.
- Each `##` heading is exactly `## <verb> <hook id>`. The prose under it, `###` subsections included, is the
  instruction: run a skill, then any follow-up. For `skip`, it is the reason.
- The hook ids are the skill's own, in the `## Extension Points` table of its `SKILL.md`
  (`.claude/skills/<skill>/SKILL.md`): when each hook runs, its default, and the verbs it allows.

| Verb | Meaning |
|---|---|
| `add` | Follow this after the hook's own text |
| `replace` | Follow this instead of the hook's text |
| `skip` | Don't run the step; the reason is shown wherever the skill reports its steps, as `⏭️ <step>: skipped by <doc>: <reason>` |

## The hook contract

- An added step that fails stops the skill for the user (a STOP in `work-task`), unless the extension says otherwise.
- Findings from `work-task`'s `review-checks` join step 6's Fix/Decline/Ask triage, beside `/code-review`'s and the
  verifier's.
- Anything a step reports shows in the skill's next report: in `work-task`, the next stop.
- A built-in step (`work-task`'s `format`, `unit-tests`, `verify`, ...) takes `add` and `skip`, never `replace`, since
  replacing it would bypass its record updates.
- At most one doc replaces or skips a hook.

## Hook a skill into `work-task`

1. Write the skill, with the project's prefix (`xx-sonar-scan`), as any skill.
2. Add a doc here with `extends: work-task` and `## add <id>` (`review-checks` for a check beside `/code-review`),
   saying when to run the skill and what to do with what it reports.
3. Run `.claude/toolbox/bin/toolbox.py doctor` from the workspace root. Its `extensions` line fails a mistyped id
   (naming the nearest), a verb the hook doesn't allow, or two docs replacing or skipping one hook.

`toolbox.py config extensions work-task` lists the docs `work-task` reads, in name order. To leave one out without
deleting it (a doc a glue toolbox links in, say), name it in `.claude/toolbox.local.yml`:

```yaml
extensions:
  disabled: [sonar-scan.md]
```

## Making a skill extensible

For toolbox authors. A skill that takes extensions has an `## Extension Points` table, `| Id | Runs | Default | Verbs |`
with one backticked id per row; names each id where it runs (``hook `<id>` ``); and carries this paragraph, word for
word, right after its title, with its own name for `<skill>`:

```markdown
**Extensions.** Before anything else, run `.claude/toolbox/bin/toolbox.py config extensions <skill>` from the workspace
root (its absolute path is in the toolbox-config rule) and read every doc it lists. Each `##` section of a doc is a verb
and one of this skill's hook ids ("Extension Points" below): at that hook, follow an `add` section after the hook's own
text and a `replace` section instead of it; for a `skip` section, don't run the step, and list it wherever this skill
reports its steps as `⏭️ <step>: skipped by <doc>: <reason>`. An added step that fails stops the skill for the user,
unless its doc says otherwise, and what a step reports goes in this skill's next report. If two docs replace or skip
the same hook, stop and ask the user which applies.
```

Rename headings freely; a hook's id never changes once a doc may use it.
