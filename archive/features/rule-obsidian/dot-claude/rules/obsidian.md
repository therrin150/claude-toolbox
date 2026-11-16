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

A discussion is my unit of design work: one problem, one dated folder in the vault's discussions folder. "Start a
discussion", "create a discussion plan", "capture this in the vault" and "write this up" all mean create or extend one,
following the `discussion` skill, which owns the layout. The vault is the record; memory only points at it.
