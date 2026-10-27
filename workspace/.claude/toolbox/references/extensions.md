# Extensions

An extension is a glue doc that changes a generic skill for one workspace without forking it. A project's knowledge
(a release cycle, a dependency rule, a VPN check) lives in the extension, in the work toolbox or the project's own repo,
and the generic skill stays the same everywhere.

## The doc

An extension is a markdown file made of sections. Each section's `##` heading names one heading of a generic skill,
exactly as the skill writes it, with its `#`s, and says what the section does to it:

| Heading | Effect |
|---|---|
| `## Replaces work-task's "### Needs a release"` | The section is followed instead of that heading's text |
| `## Adds to work-task's "### Before integration tests"` | The section is followed as well, after that heading's own text |

```markdown
---
type: extension
---

# Shared-Library Cycle

What this extension is for, in a sentence or two. Text before the first section is context only.

## Replaces work-task's "### Needs a release"

Wait for the library's release, then bump the services that depend on it to the released version, never a SNAPSHOT.

## Adds to work-task's "### Before integration tests"

Check that the network the integration tests need is reachable.
```

- Every `##` heading is a section heading; deeper headings (`###`) inside a section belong to it.
- A replaced heading takes its subsections with it: the section replaces everything down to the skill's next heading
  of the same or a higher level.
- One extension may change several skills; each section names its own.
- Two extensions replacing the same heading is a conflict: the skill stops and asks.
- `toolbox.py doctor` checks that every named skill heading still exists, so a renamed heading fails loudly instead of
  silently dropping the extension.

## Listing extensions

The workspace config lists extensions per skill, by path relative to the workspace root:

```yaml
extensions:
  work-task: [.claude/extensions/shared-library-cycle.md]
  verify-change: [.claude/extensions/shared-library-cycle.md, .claude/extensions/vpn-check.md]
```

A skill with no entry has no extensions. The installer links a toolbox's extension docs into the workspace like any
other file, so the listed paths stay inside the workspace.

## In a generic skill

A generic skill that supports extensions says so in this paragraph, word for word, right after its title, with its own
name in place of `<skill>`:

```markdown
**Extensions.** Before anything else, run `.claude/toolbox/bin/toolbox.py config extensions <skill>` from the
workspace root (its absolute path is in the toolbox-config rule) and read every file it lists. Each `##` section of an
extension names a heading of this skill: follow a `Replaces` section instead of that heading's text, and an `Adds to`
section after it. If two extensions replace the same heading, stop and ask the user which applies.
```

Only headings in the skill's own `SKILL.md` can be extended. A skill that wants a step extended gives it a heading of
its own, and doesn't rename a heading without checking the extensions that name it.
