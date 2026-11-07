# PostgreSQL: the seam, and the plan for a port

AP Coder keeps everything in one SQLite file (`ap_coder.db` in the data folder). That holds a year of a company's
invoices on one server (12 to 20 GB, tens of small writes a second at the peak). PostgreSQL is worth it for what one
file can't give: high availability (a second app host, failover), point-in-time recovery, and monitoring of its own;
not for the volume. This page is the inventory a move would work through: each construct that is SQLite's own, how
many times AP Coder uses it and where, what PostgreSQL has for it, what the seam covers already, and the plan with
its effort. The port itself is not done (D-190): no PostgreSQL driver is a dependency, and nothing here changes how
AP Coder runs.

The counts are re-run with `python scripts/sql_inventory.py` (`--markdown` gives the table below, `--json` every file's
count). They are of `ap_coder/` on 7 November 2026, after the seam went in.

## What is in place

- **The dialect** (`ap_coder/core/store/dialect.py`): the constructs below that are SQLite's own, behind names, each
  saying what PostgreSQL has instead. The store's shared helpers use them, so each has one place:
  - `in_list(column)`: a list of any length bound as one JSON value (`json_each`), behind `_base._in_list`, which
    about 40 queries call.
  - `NOW_DAYS`, `NOW_SECONDS`: the database's clock (`julianday('now')`), behind the queues' staleness test
    (`_base._STALE`, every queue's "is this read still under way") and the short-lived values' clock
    (`ephemeral._DB_NOW`).
  - `begin_write(conn)`: the write lock, joining the transaction under way when there is one; used where the store
    already joined (bulk approval's jobs, the report jobs, the export batch's undo, a review screen's draft).
  - `columns`, `add_column`, `table_names`, `busy_timeout`, `set_busy_timeout`, `data_version`, `settle`: the
    `PRAGMA`s and `sqlite_master`, used by the migrations, `database_schema`, `briefly`, the session's memo and every
    connection's opening; `page_size`, `journal_mode`, `checkpoint`: the database file's own, for server-update's
    copy of it made ahead (D-196), which a PostgreSQL server replaces with its own backup (`pg_basebackup`).
  - `upsert`, `insert_or_replace`, `insert_or_ignore`, `update_or_ignore`, `json_get`, `json_valid`, `like`,
    `nocase`, `group_concat`, `ROWID`, `PARAM`: the rest of the vocabulary, used by the migrations and the vendor
    keys' recount, and there for new code.
- **Numbered migrations** (`ap_coder/core/store/migrations.py`): `STEPS`, one small function per schema version
  (`MIGRATIONS = {2: _v2_vendor_keys, 3: _v3_export_batch, ...}`) and, where they always ran, the named steps that
  look whatever the version. A port starts PostgreSQL at the latest schema and carries on the numbering: step 28 and
  after are written once, with `dialect.columns` and `add_column`, for both.
- **Migrated equals fresh** (`tests/core/test_migrations.py`): the database each release made, from PR #1 (schema 2,
  the oldest an update starts from) to PR #99 (schema 27), and the versions on the way that no release had (20 to
  26): 22 databases, each made by that release's own code (`scripts/old_schemas.py`, kept as SQL in
  `tests/fixtures/old_schemas/`). Each is brought up to date and compared with a new database: tables, columns
  (type, NOT NULL, default, key), foreign keys, indexes, triggers and views. One difference is left, listed with why
  it is harmless (the AI reviewer's queue's columns in another order). It found B-1975 (an index a database at 24 never
  got). The same databases, seeded with invoices and lessons, are `scripts/same_behaviour.py`'s `migrate` probe.
- **No new SQLite-only SQL** (`tests/meta/test_dialect.py`): for each construct the dialect names, its uses outside
  `dialect.py` may fall, never rise. Its allowances are today's counts (the "Uses" column below, less the dialect's
  own); lower them as uses move behind the seam.
- **SQL in the store only** (another phase 7 item, under way beside this one): the SQL of `erp/` and `ops/` moved
  into store methods brings the "outside `core/store`" figures below down to the benches' own (`bench/`).

## The inventory

"Uses" counts each construct as written in the text of the string literals that read as SQL, an f-string's text
without its `{...}` (a helper used by fifty queries counts once, where it is written); a driver construct
(`lastrowid`, `executescript`...) is counted in the code. In brackets: the uses outside `core/store` (`erp/`, `ops/`,
`bench/`...). "The seam": the name in `dialect.py` that covers it, when there is one.

| Construct | Uses (outside `core/store`) | Where (the most) | PostgreSQL | The seam (`dialect`) |
|---|---|---|---|---|
| `?` placeholders | 1576 (179) | core/store/invoices.py 156, core/store/capture.py 146, core/store/users.py 129, bench/seed.py 119, 34 more files | `%s` (psycopg); one translation where the connection is wrapped | `PARAM` |
| `?1` numbered placeholders (among those above) | 16 (0) | core/store/vendors.py 10, core/store/register.py 4, core/store/learning.py 2 | `%(name)s`, or the value bound again | - |
| `INSERT OR IGNORE` | 13 (4) | core/store/summary.py 4, bench/seed.py 3, core/store/folder.py 2, bench/load.py 1, 3 more files | `INSERT ... ON CONFLICT DO NOTHING` (unique keys only: a NULL in a NOT NULL column fails there) | `insert_or_ignore` |
| `INSERT OR REPLACE` | 5 (0) | core/store/capture.py 1, core/store/dialect.py 1, core/store/ephemeral.py 1, core/store/lessons.py 1, 1 more files | an upsert of every column (the row is kept, not deleted and inserted) | `insert_or_replace` |
| `UPDATE OR IGNORE` | 1 (0) | core/store/dialect.py 1 | none: `WHERE NOT EXISTS` the row it would collide with, or a savepoint | `update_or_ignore` |
| `ON CONFLICT ... DO` (an upsert) | 42 (0) | core/store/summary.py 6, core/store/register.py 5, core/store/accounts.py 4, core/store/capture.py 4, 14 more files | the same (portable) | `upsert` |
| `json_each` (lists bound as one value) | 44 (11) | core/store/purchasing.py 7, core/store/postings.py 5, core/store/vendors.py 4, bench/load.py 3, 16 more files | `jsonb_array_elements_text(%s::jsonb)` or `= ANY(%s)` | `in_list` (`_base._in_list`) |
| `json_extract` | 42 (7) | core/store/summary.py 9, erp/controls.py 6, core/store/folder.py 5, core/store/capture.py 4, 10 more files | `(x)::jsonb #>> '{key}'` | `json_get` |
| `json_valid` | 16 (3) | core/store/postings.py 3, core/store/summary.py 3, core/store/lessons.py 2, erp/controls.py 2, 6 more files | `pg_input_is_valid(x, 'jsonb')` (16 and newer) | `json_valid` |
| other JSON (`json_set`, `json_object`, `json_group_array`, `json_array`) | 32 (11) | erp/controls.py 10, core/store/summary.py 7, core/store/purchasing.py 4, core/store/review_queue.py 3, 7 more files | `jsonb_set`, `jsonb_build_object`, `jsonb_agg`, `jsonb_build_array` | - |
| `julianday('now')` (the database's clock) | 8 (0) | core/store/imports.py 4, core/store/lessons.py 2, core/store/page_queue.py 2 | `EXTRACT(EPOCH FROM statement_timestamp())` | `NOW_DAYS`, `NOW_SECONDS` |
| other date functions (`julianday`, `date`, `datetime`, `unixepoch`, `strftime`) | 0 (0) | - | `::date`, `::timestamp`, `EXTRACT`, `to_char` | - |
| `group_concat` | 7 (0) | core/store/accounts.py 6, core/store/summary.py 1 | `string_agg(x::text, sep)` | `group_concat` |
| `LIKE` (A to Z without case in SQLite) | 16 (2) | core/store/ephemeral.py 4, core/store/users.py 2, bench/seed.py 1, core/store/accounts.py 1, 8 more files | `ILIKE` (PostgreSQL's LIKE minds the case) | `like` |
| `GLOB` | 3 (1) | core/store/register.py 2, erp/controls.py 1 | `LIKE` or `~` (a regular expression) | - |
| `COLLATE NOCASE` | 2 (0) | core/store/capture.py 1, core/store/suppliers.py 1 | `lower(x)` or a case-insensitive collation | `nocase` |
| `rowid` | 21 (1) | core/store/lessons.py 8, core/store/vendors.py 4, core/store/summary.py 3, core/store/folder.py 2, 3 more files | none that lasts: a key column of its own (`GENERATED ... AS IDENTITY`) | `ROWID` |
| `AUTOINCREMENT` | 18 (0) | core/store/_base.py 17, core/store/migrations.py 1 | `GENERATED ALWAYS AS IDENTITY` | - |
| `BEGIN IMMEDIATE` (the write lock) | 1 (0) | core/store/dialect.py 1 | the transaction psycopg opens, and `pg_advisory_xact_lock` or `SELECT ... FOR UPDATE` | `begin_write` |
| `PRAGMA table_info` | 10 (1) | core/store/postings.py 3, core/store/users.py 3, bench/seed.py 1, core/store/backups.py 1, 2 more files | `information_schema.columns` | `columns`, `add_column` |
| other `PRAGMA`s | 19 (5) | core/store/dialect.py 8, bench/seed.py 5, core/store/users.py 3, core/store/backups.py 2, 1 more files | `SET` (`lock_timeout`...), or none needed | `settle`, `busy_timeout`, `set_busy_timeout`, `data_version` |
| `sqlite_master` | 12 (2) | core/store/backups.py 3, core/store/summary.py 3, core/store/users.py 2, bench/seed.py 1, 3 more files | `information_schema.tables`, `pg_indexes` | `table_names` |
| triggers | 19 (0) | core/store/summary.py 19 | the same in PL/pgSQL (`CREATE FUNCTION ... RETURNS trigger`) | - |
| full-text index (FTS5) | 8 (1) | core/store/summary.py 5, core/store/register.py 2, erp/search.py 1 | `pg_trgm` or `tsvector` | - |
| `ap_fold` (the store's own SQL function) | 2 (2) | erp/directory.py 2 | `lower(unaccent(x))` (the `unaccent` extension) | - |
| SQLite's other functions (`printf`, `typeof`, `hex`, `char`, `instr`, `ifnull`, `iif`, `total`) | 18 (4) | core/store/accounts.py 6, core/store/summary.py 5, core/store/capture.py 2, erp/insights.py 2, 2 more files | `format`, `pg_typeof`, `encode(x, 'hex')`, `chr`, `strpos`, `COALESCE`, `CASE`, `COALESCE(SUM(x), 0)` | - |
| `executescript` | 4 (0) | core/store/_base.py 2, core/store/backups.py 1, core/store/hub.py 1 | `execute` of each statement | - |
| `lastrowid` | 8 (0) | core/store/users.py 2, core/store/bulk_jobs.py 1, core/store/imports.py 1, core/store/invoices.py 1, 3 more files | `INSERT ... RETURNING id` | - |
| `total_changes` | 1 (0) | core/store/_base.py 1 | none: a counter of the session's own writes | - |
| `in_transaction` | 14 (0) | core/store/learning.py 4, core/store/summary.py 3, core/store/_base.py 1, core/store/backups.py 1, 5 more files | `conn.info.transaction_status` | `begin_write` |
| `create_function` (a Python SQL function) | 1 (0) | core/store/_base.py 1 | an SQL function in the database | - |
| `backup` (the database copied whole) | 3 (0) | core/store/backups.py 3 | `pg_dump` / `pg_basebackup` | - |
| `sqlite3` in the code (its `connect`, `Row`, errors; type hints left out) | 74 (24) | core/store/backups.py 23, bench/seed.py 7, core/store/summary.py 7, core/store/_base.py 6, 16 more files | psycopg's `connect`, `rows.dict_row`, `errors.*` | - |

Headline (counted again at phase 8's end): about 1,580 `?` placeholders (one translation where the connection is made,
not a rewrite), and some 390 uses of SQLite's own SQL to rewrite or to check: the JSON functions (about 130, the most
work), the write lock (one place now, `begin_write`), the summary's triggers (19) and the two full-text indexes,
`rowid`, the conflict forms, the `PRAGMA`s and `sqlite_master` (41, the file's own for server-update's copy among them).
About 86% of it is in `core/store` (the counts outside it are in brackets above).

## What behaves differently (not found by a pattern)

- **Types.** SQLite keeps any value in any column. A few columns have no type (`summary_codes.code`,
  `invoice_capture.name_status` and `credit_note`); money is `REAL`; dates and times are ISO text compared as text
  (`created_at >= ?`, about 30 such comparisons), which PostgreSQL does the same on `text` columns: keep them `text` at
  first, `timestamptz` later. Truth values are integers: `SUM(x = 'y')` (about 10) becomes `COUNT(*) FILTER (WHERE x =
  'y')`.
- **The case of letters.** SQLite's `LIKE` ignores the case of A to Z, PostgreSQL's does not (`ILIKE`). `ap_fold`, the
  store's own SQL function (accents and case folded, `_base.fold_text`), becomes `lower(unaccent(x))` there.
- **A column beside an aggregate.** SQLite returns the row of `MAX(...)` for a bare column next to it (once, in
  `capture.py`); PostgreSQL refuses it: `DISTINCT ON` or a window function.
- **Transactions.** Python's sqlite3 opens a transaction just before the first change, not before a read, and leaves
  table changes (`CREATE`, `ALTER`) outside one. `StoreCore._conn` nests a call inside another's transaction
  (`_Held.depth`). psycopg opens a transaction at the first statement, a read too: reads then hold a snapshot until
  the commit, so the store's connections read in autocommit and write in `begin_write`'s transaction. 29 sites still
  write `BEGIN IMMEDIATE` themselves and fail when called inside a transaction (the digest's R4): turned into
  `begin_write`, they join it, and the port has its lock in one place.
- **What changed since a moment ago.** `_session_memo` compares `total_changes` and `PRAGMA data_version`; PostgreSQL
  has neither: a counter row the writes move (a trigger), or `LISTEN`/`NOTIFY`.
- **The summary** (`summary.py`, D-110): 19 triggers in SQLite's syntax mark the invoices to work out again
  (`summary_dirty`), and an FTS5 trigram index serves the quick search. In PostgreSQL: the same triggers in PL/pgSQL
  (or the store marks the invoices itself), and `pg_trgm`. Its JSON transport of rows (`fetch_rows`:
  `json_group_array`, `printf('%!.17g')`, `typeof`, `hex`) becomes `json_agg(row_to_json(...))`.
- **Keys.** `AUTOINCREMENT` and `lastrowid` become `GENERATED ALWAYS AS IDENTITY` and `RETURNING id`. Tables read in
  the order of their `rowid` or by it (the ERP register, the lessons' approvals, the vendors, the short-lived values,
  the supplier tables in the vendor keys' recount) need a key column of their own first: PostgreSQL's `ctid` moves.
  The search index's `rowid` is the invoice's id: a column of its own there.
- **Conflicts.** `INSERT OR REPLACE` deletes the row it conflicts with and inserts a new one; an upsert keeps it. The
  same where nothing reads the row's id and no second unique column conflicts: each of the 4 to check. `UPDATE OR
  IGNORE` (the vendor keys' recount, once per change of the matching rules) has no equivalent: a `WHERE NOT EXISTS`
  or a savepoint.
- **Backups and updates.** `conn.backup()` (the backups, a restore, the copy before an update, server-update's
  safety copy) becomes `pg_dump`/`pg_restore`; a restore is a new database, not a file put back; the backup's files
  (`backup_files.py`) stay as they are.
- **Numbered placeholders** (`?1`, 16 uses) become named ones (`%(seq)s`).

## The plan for a port

In order, with the effort for one developer who knows the code (days of work):

| # | Step | Days |
|---|---|---|
| 1 | Before the port: the SQL of `erp/` and `ops/` in the store (phase 7, under way); the 29 `BEGIN IMMEDIATE` sites through `begin_write` (R4); a key column for each table read by `rowid`; a type for each typeless column (each a numbered migration, so SQLite gets them too) | 2 |
| 2 | A connection layer behind `StoreCore._open`: psycopg with `dict_row`, `?` turned into `%s` (and `?1` into named), the errors mapped (`sqlite3.IntegrityError` to a unique violation, "database is locked" to a lock timeout), the database chosen by one setting (a URL), SQLite the default | 2 |
| 3 | The dialect's PostgreSQL side: each name of `dialect.py` given its PostgreSQL form (the docstrings say which), picked with the connection | 1 |
| 4 | The schema: `_SCHEMA` in PostgreSQL's types (identity keys, partial indexes as they are), made at the latest version; the numbered steps from 28 on for both | 2 |
| 5 | The SQL left: the JSON functions (about 130), the conflict forms (19), `LIKE`/`GLOB`/`COLLATE NOCASE`/`ap_fold` (23), the clock (8), `group_concat` and SQLite's other functions (24), the truth values and the bare column | 4 |
| 6 | The summary's triggers and the quick search's index (`pg_trgm`) | 2 |
| 7 | Backups, restore, the copy before an update, `server-update`, `doctor` and `/readyz` on PostgreSQL | 2 |
| 8 | A one-off copy of a SQLite database into PostgreSQL (every table, in key order), checked by counts and by the summary worked out again | 1 |
| 9 | The tests on both: the suite against a PostgreSQL service in CI, migrated-equals-fresh on PostgreSQL, `same_behaviour.py`'s probes with each database | 3 |

About 19 days in all, 17 for the port itself (steps 2 to 9): three to four weeks with reviews and a load run on the
real server, against the digest's estimate of two to three weeks (its counts were lower). Step 1 is worth doing
on SQLite anyway (R4, real keys), and is where to start; the laptop stays on SQLite in every case (one file, offline,
nothing to install).
