# diff-summarizer Procedure

What `diff-summarizer` does for one spawn. A script diffs each checkout against its default branch and digests the
result: per-file counts, the changes that repeat across files, and the lines that look out of place. The full diff stays
in a file, so the asker reads a page, not a diff.

## Digest

Run once, with every checkout the prompt names:

```
<bin>/diff_summary.py <checkout>... [--base <base>]
```

It prints one JSON object. A non-zero exit with no JSON (the config or a checkout can't be found) prints the reason:
report `found: blocked: <that reason, one line>` and stop. Exit 1 with JSON means some checkout failed: report the rest
and put each `failed` checkout and its `error` in `notes`. Never run `git` yourself.

What it gives: `diff` (the file holding every full diff, each under `# <checkout>`) and, per checkout, `name`, `base`,
`added`, `removed`, `files_total`, `files` (`status` A, M, D or R, `path`, `from` for a rename, counts), `sweeps`
(each a change repeated across three or more files: `kind`, `change`, `files_total`, `files`), and `flags` (`kind`,
`file`, `line`, `text`; `flags_total` when it left some out).

## Group the Changes

Report changes, not files: a 40-file rename is one line. Work in this order, per checkout:

1. **Sweeps.** Each entry in `sweeps` is one line: `rename OldName to NewName` (`rename`), `replace <old line> with
   <new line>` (`edit`), `add <line>` or `remove <line>` (`add`, `remove`), and the files it touches. A `rename` carries
   an `example` line: when the swapped words are numbers or a version, say what it changes from the example (`bump
   jackson 2.15.2 to 2.16.0`), not the bare words. Several sweeps over the same files that are plainly one change (a
   rename and the import line that follows it) are one line.
2. **Everything else.** Read the diff file for the files no sweep explains (`Read` it in parts, from the line of
   `# <checkout>` on) and write one line per change: a new class, a changed condition, a removed method, a new config
   key, a new test, a renamed file (`R`, with its `from`). Files that change the same way are one line; a file with
   unrelated changes is a line per change. Tests and docs are changes too: say what they cover or describe.
3. **Out of place.** Every `flags` entry becomes a `!` line, whatever the diff around it does: a debug print, a leftover
   `TODO`, a conflict marker, a stray file. Add what you notice yourself and the script can't: a file that has nothing
   to do with the rest of the change, a commented-out block, a hard-coded path, credentials or a secret. When there are
   more than 60 flags, say how many in `notes`.

A change line says what the diff does, in a plain verb phrase naming the thing changed (`add retry to FetchClient`,
`remove the unused cache field from Shelf`, `bump jackson 2.15.2 to 2.16.0`). It never says why. Don't write `to fix`,
`so that`, `because`, `in order to`, `intended`, or a guess at the purpose; if the diff doesn't show something, leave it
out. Never quote a hunk.

## Focus

When the prompt gives a **focus** file, run the same digest, then report only that file: its `repo` line (counts for the
whole checkout, as given), then one `-` line per change in it, down to a single condition or a single added method, from
the diff file, and its `!` lines. The sweeps don't apply; a change that repeats within the file is still one line.

## Say Where

Each line ends with the files it touches: the paths, or for more than five, the count and the folder or pattern that
covers them (`31 files under src/main/java`). A sweep's `files` list tells you the folder; its `files_total` the count.

## Counts

The `repo` line carries the digest's `added`, `removed` and `files_total` and its `base`, as given: never add, subtract
or estimate them yourself.

## When There Is Nothing

A diff with no changes (every checkout's `files_total` is 0) is `found: none`, with nothing else except `notes`.
