# Ticket Docs

Task records (in the tasks folder the toolbox-config rule names) are read and written only through `tasks.py`, never by
hand-editing their frontmatter: it validates every value and writes atomically. The user deletes task records they no
longer want; never clean them up for them.

The full contract for task records and discussion drafts (frontmatter, keys and slices, titles, acceptance criteria) is
`.claude/toolbox/references/ticket-docs.md` under the workspace root. Read it before writing or changing a record or a
draft.
