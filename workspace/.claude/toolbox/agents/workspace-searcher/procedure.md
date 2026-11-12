# workspace-searcher Procedure

What `workspace-searcher` does for one spawn. You read to find places and describe each in a line; the asker reads the
code itself when it needs more. `<root>` is the path the prompt gave you.

## Sweep

1. Turn the question into the names to search for: the identifier itself, and where it matters its other spellings (the
   bean or config key a class is wired by, its constant, a string the code looks it up by). Name each spelling you
   searched in `searched`.
2. Find the repos: `Glob` `<root>/*/.git/HEAD` (a repo's `.git` is a folder, and `Glob` matches files), limited by the
   prompt's `repos` and `exclude`. Search each with `Grep` by absolute path, case-sensitive, and `Glob` to find files by
   name when the question is about a file.
3. Skip what isn't the workspace's own code: `<root>/.worktrees` (a worktree copies a repo, so every hit would appear
   twice), `.git`, `target`, `node_modules`, `build`, `dist`, generated sources, and any path the prompt excludes.
4. Take each role from the hit and its surroundings: run `Grep` with two lines of context (`-C 2`) so one call shows
   many hits with the code around them. `Read` a few lines of a file only when the context doesn't say what the code
   does with the thing, never the whole file and never one `Read` per hit by default: a sweep has a turn limit.

## What Counts as One Location

- One entry per use site: a call, a construction, an injection, a subclass, a reference in config or a test. Give the
  line of the use, not of the enclosing method.
- Several hits in one method are one entry; the same use repeated in a file is one entry per method.
- An `import` is never a location of its own: it is folded into the file's use sites. A file whose only hit is an
  import, a comment or Javadoc gets one entry with the role `import only`, `comment` or `Javadoc`.
- A test is a location, marked `(test)` in its role, so the asker can tell production callers from test callers.
- The definition of the thing searched for is a location with the role `defines it`.

## The Role

The role is what this code does with the thing, in one line of at most about 15 words, in your words: `reads it to
decide whether to re-evaluate`, `wires it as a bean`, `evicts it on a permission change`. It is never a quote, never why
the code does it, and never an opinion of it. If you can't tell what the code does with it, say `use not clear from the
hit`; a guess isn't a role.

## Many Hits

List up to `max` locations (default 100), in the order repo, then path. If there are more, list the first `max` and say
in `notes` how many more there are and in which repos, so the asker can narrow the next question. If a name matches so
much that it can't be one thing (a common word), stop and report that in `notes` instead of listing it.

## When Nothing Turns Up

`found: none`, with every spelling and repo you searched in `searched`. Say nothing else: absence is the answer.
Never widen the question yourself to find something; name in `notes` the wider search you would suggest.
