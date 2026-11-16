---
name: database-conventions
description: Allen's Postgres and Flyway conventions - timestamped migration names and folders, SQL style, named constraints and indexes, UUIDv7 ids, column types (varchar lengths, enum types, no arrays), table and column naming, UTC timestamps, never editing an applied migration. Read before planning a table, writing a migration or SQL, or reviewing either.
user-invocable: false
---

# Database Conventions

Allen's conventions for Postgres and Flyway in his own projects. A project's `<prefix>-database-patterns` rule adds its
roles, tenancy model and anything it decides differently; where the two disagree, the project's rule wins.

## Flyway Versioned Migrations

- **Location**: the application module's `src/main/resources/db/migration/{YYYY}/{MM}/`.
- **Naming**: `V{YYYY}.{MM}.{DD}.{HH}.{mm}__{description}.sql`, e.g. `V2026.10.24.13.50__create_recipe_table.sql`.
- Timestamps, not sequence numbers, so migrations written in parallel worktrees never collide.
- Flyway runs at app startup as the schema-owning role; the app's runtime role can't change the schema.

## Migration Practices

- **Never modify** a migration after it has run anywhere (locally counts once committed). Flyway checksums applied
  files and refuses to start if one changes. Fix mistakes with a new migration.
- **Forward-only.** Flyway's free edition has no undo; restore from backup for a true rollback.
- Postgres wraps DDL in the migration's transaction, so a failed migration leaves nothing half-applied.
- **Tables are planned, not pre-built.** A table's design lives in its epic's discussion until a story implements it,
  and the migration is written by the first story that needs the table, never up front.
- **Every foreign key gets an index** unless a primary key or unique index already starts with that column, plus columns
  used in `WHERE` clauses for common queries.

## SQL Style

- **Keywords, types and built-in functions uppercase** (`CREATE TABLE`, `NOT NULL`, `UUID`, `VARCHAR(64)`,
  `TIMESTAMPTZ`, `NOW()`), identifiers lowercase `snake_case`. Our own functions stay lowercase, so they stand apart.
- **Primary keys are named for the table**: `person.person_id`, not `person.id`. Joins say `USING (person_id)` and no
  column is ambiguous. The entity field stays `id`, mapped with `@Column(name = "person_id")`; a foreign key keeps the
  referenced name (`account.person_id`).
- **Name every key, declared after the columns**, never inline: `person_id UUID NOT NULL`, then
  `CONSTRAINT pk_person PRIMARY KEY (person_id)`. Names are `pk_<table>`, `fk_<table>_<column>`, `uk_<table>_<columns>`;
  for a composite too long to spell, describe it (`uk_meal_plan_slot_position`).
- **Name every index `idx_<table>_<columns>`** (`idx_invitation_person_id`), so a key's constraint (`pk_`, `fk_`, `uk_`)
  and its index never share a prefix.
- **No comments** in migrations; the purpose lives in the decision or discussion note.

## Tables and Columns

- Singular `snake_case` tables (`recipe`, `meal_plan`), foreign keys `<entity>_id`, booleans `is_<adjective>`.
- **Full names, never abbreviations**: `invitation`, not `invite`. The UI can be casual.
- Avoid SQL reserved words (`group`, `user`, `order`); pick a full-name alternative when the table is designed.
- Related tables share their prefix so they sort together (`party`, `party_membership`).
- **Timestamps are UTC.** `timestamptz` columns map to `Instant`, `hibernate.jdbc.time_zone` is `UTC`, the API sends
  ISO 8601 with `Z`, and clients format in local time. No server-side zone conversion.
- **`date` columns end in `_on`, `timestamptz` columns in `_at`** (`served_on`, `created_at`).
- **Money is `bigint` microdollars** (`cost_microdollars`); the API speaks dollars.
- **`position` is `int`.** Every row created on its own has `created_at`; child rows a parent's save rewrites by
  position don't.

## Ids

- **Every primary key is a UUIDv7, `<table>_id UUID`, and every foreign key to it is `UUID`.** That one id is what the
  API, client routes and every link use; there are no separate public ids. Join tables keep composite keys, and tables
  keyed by something else keep it.
- **Java makes ids** through a `@UuidV7` Hibernate generator, at persist, so a new entity's id is null until then. It
  keeps an id already set, so a client can make UUIDv7s for what it creates offline.
- **Prompts to an LLM never carry UUIDs** (about 20 tokens each): number list entries from 1 and map the numbers back.

## Column Types

- **Strings are `varchar(n)`.** `n` is 16, 32 or a multiple of 64, unless a value is a fixed length by definition
  (an email is 254). Titles and names are 64. The API checks the column's limit with `@Size` and returns `400`, the
  client sets the same `maxlength`, and the column is the backstop. `text` only for long prose with no natural limit
  (descriptions, notes, comments).
- **Fixed lists are Postgres `ENUM` types, not `text` + `check`.** One type per Java enum, named as the enum in
  `snake_case` (`GroceryCategory` → `grocery_category`), values exactly the Java constant names, declared in display
  order (Postgres sorts by declaration order).
- **No array columns.** A set of values is a join table with a primary key on both columns
  (`recipe_meal (recipe_id, meal)`), mapped as an `@ElementCollection`.
- **Adding an enum value**: `ALTER TYPE x ADD VALUE 'NEW' AFTER 'OLD'`; data using it goes in a later migration, since
  the value can't be used in the transaction that adds it. **Renaming**: `ALTER TYPE x RENAME VALUE`. **Removing or
  reordering**: migrate the rows, create `x_new`, `ALTER COLUMN … TYPE x_new USING c::text::x_new` on each column, drop
  `x`, rename `x_new` to `x`.
- `CHECK` constraints stay for rules that aren't a list (kebab-case names, `servings > 0`, "exactly one of" pairs).
