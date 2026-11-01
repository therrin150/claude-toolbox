---
paths:
  - "**/*.sql"
---

# SQL Conventions

- **Keywords, types and built-in functions uppercase** (`CREATE TABLE`, `NOT NULL`, `UUID`, `VARCHAR(64)`,
  `TIMESTAMPTZ`, `NOW()`), identifiers lowercase `snake_case`. Our own functions stay lowercase, so they stand apart.
- **Primary keys are named for the table**: `person.person_id`, not `person.id`. Joins say `USING (person_id)` and no
  column is ambiguous. The entity field stays `id`, mapped with `@Column(name = "person_id")`.
- **Name every key, declared after the columns**: `person_id UUID NOT NULL`, then `CONSTRAINT pk_person PRIMARY KEY (person_id)`.
  Names are `pk_<table>`, `fk_<table>_<column>`, `uk_<table>_<columns>`; for a composite too long to spell, describe it.
- **Name every index `idx_<table>_<columns>`** (`idx_invitation_person_id`), so a key's constraint (`pk_`, `fk_`, `uk_`) and
  its index never share a prefix.
- **Timestamps are `timestamptz` and UTC** everywhere, whatever the host zone; clients convert to local time.
- **No comments** that repeat what the table or a decision note already says.
