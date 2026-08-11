---
name: db-migration
description: This skill should be used when the user asks to "add a migration", "add a column", "change the schema", "add a table", "add an index", or any change to the messages/outbound_jobs Postgres schema in db/migrations/.
---

# Adding a database migration

Add a new numbered SQL file to `db/migrations/`, following the existing
convention — there is no ORM or migration framework (no Alembic); a small
custom runner (`app/tracking/db.py::run_migrations()`) applies whatever
hasn't run yet, in filename order, at startup of `app`, `worker`, and
`poller` alike.

## Steps

1. **Determine the next number.** Files are `NNNN_description.sql`
   (four-digit, zero-padded), applied in filename-sort order. Find the
   current highest with:
   ```
   ls db/migrations/ | sort | tail -1
   ```
   Use the next integer, a short `snake_case` description of the change
   (e.g. `0008_add_partner_notes.sql`).

2. **Write idempotent SQL.** Every existing migration uses guarded DDL —
   `ADD COLUMN IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`,
   `DROP INDEX IF EXISTS` before recreating, `CREATE TABLE IF NOT EXISTS`.
   This matters because the runner only skips a file once it's recorded
   in `schema_migrations`; guarded DDL means a partially-applied or
   manually-reapplied file can't fail on "already exists". Look at
   `db/migrations/0004_unique_refnum.sql` and `0007_messages_processed.sql`
   for the pattern.

3. **No down-migration.** This project doesn't have a rollback mechanism
   — migrations are forward-only. If a change needs to be reverted,
   write a new forward migration that undoes it, don't edit or delete a
   past file.

4. **Explain non-obvious changes in a header comment**, the way
   `0004_unique_refnum.sql` does — it explains *why* a non-unique index
   wasn't sufficient (a real race condition it closes), not just what the
   SQL does. A bare `ALTER TABLE ... ADD COLUMN` doesn't need this; a
   change driven by a race condition, a NAESB spec requirement, or a
   non-obvious constraint choice does.

5. **Update the code that reads/writes the changed columns.** New columns
   or tables are inert until `app/tracking/models.py` (dataclasses like
   `MessageRecord`/`OutboundJob`) and `app/tracking/repository.py`
   (`MessageTracker`/`OutboundJobRepository` query/insert/update SQL) are
   updated to use them. Check both files for every table this migration
   touches.

6. **Add or extend a test.** `tests/test_tracking_repository.py` is
   `@pytest.mark.integration` (real Postgres via `testcontainers`) and is
   where schema-dependent repository behavior gets covered — run
   `pytest -m integration` (requires Docker) to verify a new migration
   actually applies cleanly and the updated repository code works against
   it.

7. **Mention it in `docs/PLAN.md`'s Database section** if it's a
   structural change worth a reader knowing about (new table, changed
   semantics) — that section briefly summarizes what each migration file
   added, in one place, so a reader doesn't have to open every SQL file
   to understand the schema's evolution. A trivial index-only migration
   usually doesn't need a mention there.
