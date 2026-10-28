# Ticket Docs

Task records and the discussion drafts they come from. The toolbox-config rule names this workspace's tracker, its key
prefix, its tasks folder and its discussions folder; the names below stand for those, never hard-coded ones. Read and
write task records only through `tasks.py` (in the scripts folder the toolbox-config rule names), never by hand-editing
their frontmatter: it validates every value and writes atomically.

The discussion layout around the drafts (folders, `plan.md`, sibling docs) is the global obsidian rule's. The user
deletes task records they no longer want; never clean them up for them.

## Task records

Every task has a task record, always, whatever the tracker: `<tasks>/<KEY>/<KEY>-<slug>.md` in its key's folder when
it has a key, `<tasks>/<slug>.md` when it has none (the `none` tracker). A keyed record from before the key folders,
`<tasks>/<KEY>-<slug>.md`, is still read where it is. The slug is the summary in lowercase with every run of other
characters turned into `-`. A task's **work id** is its key, else that slug; worktrees, branches, sessions and the
publish folder are all named by it.

A ticket worked in more than one limited-scope run gets a **slice** per run: `work-task <KEY> --limited-scope` copies
the ticket's record into its key folder as `<KEY>.<n>-<slug>.md`, titled for the scope, with `slice: <n>` and a
`## Scope` section saying which part of the ticket the run covers. A slice's work id is `<KEY>.<n>`, and slices run in
parallel; its branch carries the ticket's key, with `slice-<n>-` leading the description. The ticket's own record is
the one the bare key names, and it covers whatever the slices haven't. The ticket is done when the user says so.

| Type | What it is | Made by |
|---|---|---|
| `story` | a change to make; a spike is a story whose summary starts `Spike: ` | filing a draft |
| `epic` | a capability its stories build; never worked itself | filing a draft |
| `defect` | an unrelated problem found along the way (one found while testing a task is fixed in that task) | `create-defect` (vault tracker) |
| `task` | work started straight from a discussion's `plan.md` | `context <plan.md>` |

Frontmatter, in this order (an epic has only `type`, `key`, `summary`, `status`, `created`, `legacy_id`, `source` and
`source_doc`):

| Field | Holds |
|---|---|
| `type` | `story`, `epic`, `defect` or `task` |
| `key` | the ticket key; empty with no tracker keys |
| `summary` | the title (see Titles) |
| `epic` | the epic's key, if any |
| `status` | `todo`, `in-progress`, `review`, `done`, or `stopped` (parked; the reason in `work_note`). An epic's status comes from its stories (`epic-status`) |
| `owner` | `claude`, or the user's owner value: whose move it is |
| `stage` | the workflow's finer step while in progress: `implementing`, `testing`, `code-review`, `publish-review`, `awaiting-merge`, `awaiting-ci`, plus any the config's `work.stages` adds (an extension's `awaiting-release`) |
| `depends_on` | keys of tasks that must be `done` before this one starts: hard blockers only |
| `created` | the date the record was made |
| `found_in` | defects only: where it was found (a key, a version, a page), when known |
| `legacy_id` | the key the task had before a renumbering, kept for good; never rewritten |
| `source` | a wikilink to the discussion's plan |
| `source_doc` | the vault-relative path of the draft or plan the record was made from |
| `worktrees`, `sessions`, `prs` | lists the workflow keeps: worktree paths, session ids, PR URLs |
| `work_note` | one line: why a task is stopped, or what it waits on |
| `usage`, `usage_total` | token usage per session and in total, written whole at done |

Two more kinds of field come after these, added when first set:

- **`cycle`**: `shared-library` or `consumers`, only on a task that changes one of the config's `shared_libraries`
  and so runs `work-task`'s two cycles; a task with one cycle has none, and `tasks.py set` refuses it in a workspace
  with no shared libraries. A resume reads it to know which cycle it is in.
- **Extra fields** the config declares under `work.extra_fields`, for what only some workspaces track (an
  extension's, usually): each a list of allowed values, or `text` for one line. `tasks.py set` and `get` take them
  and check their values; the toolbox-config rule lists this workspace's, and a field no config declares is refused.

The body is the draft's (Description, Acceptance Criteria, Testing Notes; a defect's Description, Steps to Reproduce,
Expected, Actual and Acceptance Criteria), ending in `## Log`: dated entries (`- YYYY-MM-DD: text`, from `tasks.py
log`) for STOPs and their answers, and for hand-offs of steps only the user can do. Review and testing feedback stays in
the review tool and the session, not the log. The templates in the toolbox's `templates/` folder are exactly what the
script writes.

- **Ownership flips at hand-offs.** A STOP is `owner: <user>` with the question in the log; the review stop is
  `status: review` with the user as owner; work only the user can do is its own story, owned by them from filing, and
  blocks the stories that need it through their `depends_on`.
- **The publish folder**, `<tasks>/.publish/<work id>/`, holds the approved commit messages, the announcement text and
  each stop's report as last presented, so a fresh session can re-present a stop.
- **Epics carry no `depends_on`.** Blocking stays story to story, so stories in different epics run in parallel.

## Discussion drafts

Everything but a defect starts as a draft in a discussion's `tickets/` folder, beside its `plan.md`, named
`NN-<slug>.md`: `00` is the epic, if the discussion proposes one, and the rest number the stories in filing and
dependency order. Draft frontmatter:

| Field | Holds |
|---|---|
| `type` | `story`, `epic` or `defect` |
| `summary` | the title |
| `epic` | the epic's key, when it is filed already; else empty, and filing fills in the discussion's epic |
| `owner` | `claude` (the default) or the user's owner value |
| `depends_on` | draft numbers within this discussion (`[1, 2]`) or keys outside it |
| `status` | `draft` while being written, `approved` once the user agrees it; only `approved` drafts are filed |
| `reserved_key` | epics only, optional: a key promised before filing, which the epic gets instead of the next one |
| `legacy_id` | only when migrating old tickets |
| `estimate`, `sprint`, `labels`, `components` | `jira` tracker only, optional: what filing sends to Jira (a sprint by name; labels beyond the configured ones). Records don't keep them |

With the `vault` tracker, `tasks.py file <discussion>` is the "ready" step: it gives each approved draft a key (an
epic's `reserved_key`, else the next from the counter), turns draft numbers in `depends_on` into keys, writes the record
frontmatter into the draft and then moves it into the tasks folder, repairs wikilinks to the moved drafts, sets the
plan's `epic`, and rewrites the plan's `## Proposed Stories` as links to the filed tasks. A run that stops part way is
safe to repeat: a draft that already holds a `key` keeps it. With the `jira` tracker, the `jira-filer` agent files the
drafts in Jira and `tasks.py file <discussion> --keys NN=KEY ... --issues <file>` records the keys it returned, after
checking them against a saved search for those issues, and then moves the drafts the same way; `context <KEY>` on an
issue no discussion filed says to `import` it from a saved `getJiraIssue` response. With the `none` tracker, `context
<draft>` copies the draft into a record and the draft stays where it is.

## Titles

Title Case, about 25-45 characters, no articles or filler prepositions. A story is an imperative verb and its object
(`Add Shelf Labels`); a defect names the symptom (`Shelf Count Wrong After Move`); an epic is a bare noun naming the
capability (`Pantry Shelves`); a spike is `Spike: ` and the question.

## Acceptance Criteria

Read `~/.claude/personal/ticket-style.md` and `~/.claude/personal/voice.md` when they exist: they override the defaults
here for titles, descriptions and AC. Then:

- **GIVEN / WHEN / THEN by default**, 1-5 scenarios: the happy path, the invalid-state path, and the variants that
  behave differently. A bold scenario name, then one clause per line, the keywords in uppercase and not bold:

  ```
  **Scenario: Shelf Already Full**
  GIVEN a shelf holding its maximum number of items
  WHEN an item is added to that shelf
  THEN a 400 response is returned with a message naming the full shelf
  AND the item is NOT added
  ```

- **Assert only what the user can observe** from outside the running app: responses and their status codes, the UI,
  database rows, log lines, messages sent or NOT sent. Never internals (method return values, class behavior), except
  for a library-only change no running app exposes, which says it is verified by review and unit tests.
- **Keep GIVEN to the conditions that matter.** Long data setup goes in Testing Notes.
- **Field names, enum values and paths in backticks**, never in the scenario name. Name conditions explicitly ("a
  shelf owned by another household", not "someone else's shelf").
- **A brief statement instead** only for a truly single-path requirement; a defect's AC is usually one: the expected
  result at the relevant repro step. Epics and spikes have no AC.
- **Testing Notes** are optional, 1-3 bullets, only when testing needs more than the AC implies (test-data steps, a
  query and what it should show).
