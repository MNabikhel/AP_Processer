# ap_coder/core/: notes for the 36-hour goal (reader: core)

Repo /home/user/AP_Processer at af9925b. Every core file was read (33 files, 12,429 lines). Line references are
`file:line`. **Verified** means reproduced with a snippet against a scratch database built from the demo
(`ops/demo.load_demo`), sometimes grown with SQL to 3,010 invoices, 20,000 vendors, 5,000 GL accounts and 57k feedback
rows. The scripts are in `scratchpad/exp/`
(`mk.py`, `grow.py`, `plans.py`, `locks.py`, `export_t.py`, `race.py`). The scratch databases were deleted afterwards
to free disk. SQLite here is 3.53.1 and Python is the repo's .venv.

---------------------------------------------------------------------------------------------------------------------

## 1. Map

### The Store package `core/store/` (8,600 lines)
`Store` (`store/__init__.py:117`) is a single class built from 14 mixins, with `SummaryMixin` first so that its
`_migrate` and `_conn` wrap `StoreCore`'s. It has **265 public and 60 private methods**. A test that patches `_now`
patches it in the part that uses it.

| part | lines | what it holds |
|---|---|---|
| `_base.py` | 999 | `SCHEMA_VERSION = 19` (:36) and `_SCHEMA` (:107-249, 30 tables). `StoreCore`: `__init__` (:458), `_before_migrating` (:470, a backup before an upgrade, refuses a newer database), `_migrate` (:490-686, 197 lines), `_reading_as_d89` (:689), `_fill_tax_codes` (:732), `_recompute_vendor_keys` (:768), the merges (:821-897), `session` (:899), `_session_memo` (:919), `_open` (:935), `_conn` (:948). Process-wide caches: `_FEEDBACK`, `_METRICS`, `_FOUND`, `_NOT_FOUND`, `_RESTORING` (:59-75). Queue constants, `fold_text` (the SQL UDF `ap_fold`, :392), `suggestion_kept`, `line_guessed`, `_rescored`. |
| `summary.py` | 766 | D-110 `invoice_summary`, plus `summary_facts`, `summary_codes`, `summary_spend`, `summary_dirty`, `feedback_counts`, and the FTS5 `invoice_search`. Twelve triggers (`SUMMARY_TRIGGERS` :180) mark changed invoices dirty. `SummaryMixin._conn` (:406) recomputes the dirty rows **inside the writer's transaction**. `_summarize` (:441) calls `erp`/`coding` functions per invoice (`_Work.part` :681). `summary_memo` (:507) is a process cache stamped by generation counters in `settings`. `fetch_rows` (:274) returns all rows as one JSON text, to avoid GIL ping-pong. |
| `invoices.py` | 999 | `add_invoice` (:53), `find_by_hash`, `find_duplicates` (:133), the ERP register, directory paging (:222-251), `get_invoice(s)`, `invoice_file` (finds a moved file, :361), park/reject/delete (:410-523), drafts (:529-567), `invoice_columns` (:608), `approve_invoice` (:659), `_line_feedback` (:752), the second approval (`final_approve` :881), `send_back`, `reopen` (:948). |
| `accounts.py` | 492 | GL accounts and cost centers, the audit trail (`_log` :93, `events` :115, `latest_events`), bank accounts, tax setup, settings (`get_setting` :356, `set_setting` :373), approval controls (:391-435), `reference_data` (:441, a process cache stamped by `_REFERENCE_STAMP` :36), `checks_stamp` (:460). |
| `users.py` | 959 | Server-mode accounts: scrypt hashes, lockouts on `login_attempts`, sessions (`start_session` :871, `session_user` :888), two-step TOTP (`check_code` :785), and `carry_accounts` for a restore (:289). |
| `capture.py` | 984 | Compressed capture (:52), supplier profiles and template snapshots, touchless autonomy (`sync_autonomy` :379), supplier and reader outcomes and their scorecards (:640-748), and the page-reader and Azure queues (:750-984). |
| `lessons.py` | 545 | Lessons (`record_lesson` :53, `_withdraw_lessons` :91), `corrections` (:171, 90 lines), the AI reviewer's queue (:274-385), Azure figures, `update_after_check` (:425), `_recheck_duplicates` (:458), `replace_proposal` (:493). |
| `vendors.py` | 546 | Vendor aliases (D-119), `find_vendor` (:140), `vendor_key_for` (:173), the vendor-master import (:215), `vendor_ids` (:379), `vendor_invoices` (:408), `vendor_summaries` (:488). |
| `purchasing.py` | 415 | POs, export batches (`create_export_batch` :226, `undo_export_batch` :396), coding rules, batch totals. |
| `backups.py` + `backup_files.py` | 411 + 771 | D-143 whole-state backups: the database by the SQLite backup API, plus content-addressed files and a per-backup `.files.json` list. Restore (`_restore_from` :354), `exclusive` OS lock (backup_files:78), `snapshot` (:337), `prune` (:569), `restore_files` (:706). |
| `learning.py` | 267 | `feedback_rows` (:34, up to 50,000 rows cached per process), `learning_rows`, history import, `metrics` (:169). |
| `managers.py` | 229 | D-138 manager requests. |
| `ephemeral.py` | 125 | A short-lived key-value table: flash messages, uploads, job leases across workers, upload allowance. |
| `postings.py` | 184 | The JDE postings ledger (a table plus `jde_postings.jsonl`) and the installation id. |
| `hub.py` | 113 | Full-table reads for the finance hub. |
| `samples.py`, `fx.py` | 73, 46 | Demo loaders; FX-rate parsing. |

### The rest of core/
- `schema.py` (517): the strict JSON schema (`build_json_schema` :85, 140 lines) and the Pydantic `InvoiceCoding`,
  `LineItem` and `TaxLine` models, plus `province_code` (:297).
- `memory.py` (404): the learning memory's pure logic: `vendor_key` (lru_cache, :100), `aggregate`/`RowsCache`
  (:173-211), `latest_decision`, `select_examples`, `compare_with_history`, `pair_lines`.
- `audit.py` (514): `diff_coding` (:155), and `describe` (:281, a 198-line if/elif chain) that turns events into
  text.
- `config.py` (372): `Settings.from_env`, LLM, page-reader and engine settings, `normalise_base_url`, and
  `_on_this_network` (:118).
- `paths.py` (410): the data folder, install identity, `find_moved_file` (:345, os.walk) and `try_lock`.
- `ui.py` (411): **HTML building blocks**, used by web/, ai/ and ops/.
- `reference_data.py` (251): `ReferenceTable` and `ReferenceData`, and CSV/JSON loading.
- `authenticator.py` (162): TOTP, and HMAC-SHA256 sealing with `authenticator.key`.
- `envfile.py` (106), `safe.py` (106: CSV formula neutralising, `parse_amount`), `csvio.py` (76), `headers.py` (33).

---------------------------------------------------------------------------------------------------------------------

## 2. Data flow: connections, transactions, migrations, state

- **A connection per call, or one per request.** `_open()` (_base:935) runs `sqlite3.connect(path, timeout=30)`,
  sets the `sqlite3.Row` factory, registers `ap_fold`, and runs `PRAGMA journal_mode=WAL` on every open. If the file
  is missing, it creates and migrates a new database on the spot (:936-945); see risk R9.
  - Outside a session, every store call opens and closes its own connection: **0.74 ms per open (verified)**.
    `get_setting` costs 0.78 ms outside a session and 0.008 ms inside one.
  - `session()` (:899) keeps one connection per thread (`threading.local` `_sessions.held`, :447) in a `_Held`
    object with a depth and a memo.
  - The web wraps every plain handler in a session (`web/core.py:141-151`, `page_router`). The pipeline, bulk
    approval and exports open their own.
- **Transactions.** The connection uses Python's legacy implicit mode: a deferred `BEGIN` is issued just before the
  first DML.
  - `_conn()` (:948) commits at the outermost exit and rolls back on an exception. A store call made inside another
    store call **joins** the outer transaction.
  - 28 call sites issue `conn.execute("BEGIN IMMEDIATE")` explicitly (approvals, exports, queues, users). These
    cannot run inside an open transaction (see R4).
  - Reads in autocommit release their snapshot at once, so a read-then-write sequence is not atomic unless it is
    wrapped in BEGIN IMMEDIATE (see R1).
- **The summary sits inside every write.** `SummaryMixin._conn` (summary:406-417) checks `conn.in_transaction` after
  the caller's work. It then runs `_summary_fresh`: `SELECT 1 FROM summary_dirty` and `_refresh_summary` in chunks of
  400. That recomputes each dirty invoice in Python (`_summary_row`: tax facts, insights, spend parts, queue facts)
  **while the write lock is held**.
  - Readers call `_summary_fresh` too. If a foreign connection left rows dirty, the reader turns into a writer.
- **Migrations** run on every `Store()` (`StoreCore.__init__` :458):
  1. `_before_migrating`: `database_schema()`. A newer database raises `DatabaseTooNew`. An older one is copied to
     `backups/…-before-update-from-schema-N.db` and its files are snapshotted.
  2. `executescript(_SCHEMA)`.
  3. `_migrate`: about 20 `PRAGMA table_info` probes with `ALTER TABLE ADD COLUMN`, version-gated data rewrites, the
     `VENDOR_KEY_RULES` and `FEEDBACK_CLEANUP` one-offs, and `_reading_as_d89`.
  4. `_capture_migrate`.
  5. `_summary_migrate`: a full rebuild when `SUMMARY_RULES/VENDOR_KEY_RULES` changed (summary:367-399).
  - All of this runs in one transaction. **When nothing is due, Store() does not take the write lock (verified: 0.00 s
    while another connection held BEGIN IMMEDIATE).**
  - In server mode the parent process (`ops/cli.py:394`) and `server_update.migrate` (`ops/server_update.py:272`)
    migrate before the workers fork.
- **Where state lives.**
  - The database file, its `-wal` and `-shm`.
  - The data folder: `invoices/`, `emails/`, `reference/`, `authenticator.key`, `jde_postings.jsonl`,
    `jde_installation_id`, `.env`, `backups/` (with `files/<2>/<sha>`), and the markers `<db>.restoring` and
    `backups/.making-*`.
  - `~/.ap_coder/settings.json` (the data-folder pointer and the installs).
  - **Process memory**, all validated by stamps read from the database:
    - `_REFERENCE` (accounts:33), keyed by a content stamp.
    - `_FEEDBACK` and `_METRICS` (_base:72-75), keyed by the max id, count and newest row.
    - `_MEMO`, `_COMPUTING` and `_FTS` (summary:231-236).
    - `_FOUND` and `_NOT_FOUND` (time-based).
    - `memory._AGGREGATED` (`RowsCache`) and the `lru_cache`s.
    - `users._DUMMY`, and `UsersMixin.session_idle/absolute` (class attributes set at start).

---------------------------------------------------------------------------------------------------------------------

## 3. Scale and concurrency

### 3a. Write locking (one global writer)
- **Verified with `locks.py`:**
  - Plain reads, summary reads and `get_setting` do not wait for a writer.
  - `session_user()` waits as long as any writer holds the lock (3.05 s in a 3 s test), and up to 30 s.
  - `log_event` waits likewise.
- `session_user` runs on **every authenticated request** and writes `last_seen` once a minute
  (users:912-916, `SEEN_EVERY = 60`). With 400 users that is about 7 writes a second, and each one makes a request
  wait behind the slowest writer. Its `except OperationalError: pass` only fires after the full 30 s wait.
- **No PRAGMA tuning at all** (grep finds no `synchronous`, `busy_timeout`, `wal_autocheckpoint`,
  `journal_size_limit`, `cache_size`, `mmap_size`, `foreign_keys`, `optimize`, `ANALYZE` or `VACUUM` anywhere in
  ap_coder/).
  - WAL with the default `synchronous=FULL` fsyncs on every commit.
  - Each new connection gets a cold 2 MB page cache and re-parses the schema (about 70 tables, indexes and triggers).
  - The planner has no statistics.
- **Each approval makes about 25-30 separate write transactions.**
  1. `approve_invoice`.
  2. `capture/workflow.learn_from_approval` (:500): `record_reader_outcomes`, `remember_vendor_alias`,
     `record_outcomes` (plus `sync_autonomy`), `save_supplier_profile` (BEGIN IMMEDIATE), `remember_line_texts`,
     `forget_if_withdrawn` (BEGIN IMMEDIATE).
  3. `ai/lessons.py:132`, which calls `record_lesson` **once per situation**. That is about 15 header fields plus 2
     per distinct line, so about 20 commits.
  - Each one is a lock acquire and an fsync.

### 3b. Long write transactions (everyone else waits, up to 30 s, then fails)
- `create_export_batch` (purchasing:226): one UPDATE and one event per invoice, then the summary recompute of every
  invoice in the batch, all under BEGIN IMMEDIATE.
  - **Verified: 602 invoices held the lock 0.77 s (1.29 ms per invoice) on demo-sized invoices.**
  - A day's batch is 1,250 invoices, and real invoices are 2-4 times larger, so expect 3-6 s.
  - `undo_export_batch` takes 0.46 s per 602, and it also full-scans `invoices` (see 3e).
- **Restore** (`backups._restore_from:354`):
  1. A full safety backup.
  2. `source.backup(target)` into the live database. The write lock is held for the whole copy, and Python's
     `backup()` retries BUSY forever.
  3. `_migrate`, `carry_accounts`, `carry_postings`, and `repoint_invoices` (reads every invoice row).
  - At 15 GB this is minutes with every writer blocked.
- **Migrations on a big database:**
  - `_summary_migrate` rebuild: **verified 0.42 ms per invoice (demo size)**, so 300k invoices is 2-10 minutes in one
    transaction.
  - `_recompute_vendor_keys` (:768): loops in Python over every invoices, feedback, PO, vendor, erp_invoices and
    lessons row, one UPDATE per changed row.
  - `_fill_tax_codes`, `_reading_as_d89`: whole-table Python loops.
  - None of them reports progress.
- `_replay_without` (capture:206) runs `replay_template`, which re-reads captures and PDFs, inside BEGIN
  IMMEDIATE on every reject, reopen or send-back of a supplier whose template changed since.
- Imports run as one transaction each, which is fine at the sizes involved: `import_vendor_master` with 20k vendors
  (a row each), `import_purchase_orders`, `import_history`, `import_accounts`.

### 3c. N+1 patterns
- `record_lesson` loop: about 20 transactions per approval (above).
- `sync_autonomy()` with no key (capture:379): for **every** supplier profile, `supplier_stats` makes 2 queries and
  then a transaction per move. This runs on `set_touchless(on)`. With thousands of suppliers it is thousands of
  queries.
- `_recheck_duplicates` (lessons:458): `find_duplicates`, then a BEGIN IMMEDIATE and another `find_duplicates` per
  duplicate.
- `delete_empty_batches` (purchasing:329): a full-scan query per batch.
- `vendor_key_for` (vendors:173) calls `find_vendor` (which calls `_aliased`), then `_aliased` again: 3-4 queries,
  each a table scan of vendors (see 3e).
- `supplier_key_for` (capture:111) calls `vendor_ids()` (the whole vendor table) on **every** call.
  - **Verified: 52 ms per call with 20k vendors.**
  - It is called from `capture/workflow.py:53, :180, :535, :733`, i.e. for every invoice read and every approval.
- `approve_invoice` runs `_line_feedback`, which calls `list_accounts("cost_centers")` (invoices:765) and reads the
  whole cost-center list only to test `bool(...)`.
- Outside core but caused by a missing store method: `web/pages/vendors.py:315` builds `vendor_summaries()` for all
  vendors (a GROUP BY over all feedback) to show one vendor's page.

### 3d. Indexes, and the queries that use them
EXPLAIN QUERY PLAN was run on 3,010 invoices (`plans.py`).

Present:
- **invoices**: PK; `invoices_status(status)`; `invoices_vendor(vendor_key)`; `invoices_po(po_key)`;
  `invoices_sha(file_sha256)`; `invoices_original_sha`; `invoices_total(grand_total)`.
  - Used by `find_by_hash`, `find_duplicates`, `_vendor_invoices`, `po_invoices`, `duplicates_elsewhere`, `parked`,
    `credit_note_ids` and `invoice_drafts`.
- **events**: `events_invoice(invoice_id)`; `events_action(action, invoice_id)`.
  - Used by `latest_events`, `latest_approval_id`, `_generation` and the `summary_memo` stamp (a covering count).
- **feedback**: `feedback_vendor`, `feedback_outcome`, `feedback_vendor_outcome`.
- **supplier_outcomes**: `(supplier_key, at)` and `(invoice_id)`.
- **reader_outcomes**: `(invoice_id)` and `(reader, field)`.
- **lessons**: `situation` UNIQUE, and `(vendor_key, kind)`. **lesson_approvals**: PK `(invoice_id, situation)` and
  `(situation)`.
- **The three queues**: `(status, created_at)`.
- **login_attempts**: `(username, at)` and `(ip, at)`.
- **summary**: 18 indexes (summary:142-172).

**Missing, verified as full scans:**

| query | where | plan |
|---|---|---|
| `invoices WHERE export_batch = ?` | `undo_export_batch` purchasing:412-413, `delete_empty_batches` :334 | SCAN invoices. `export_batch` is stored *after* the JSON columns, so the scan walks the overflow chain of every row (it reads the whole table). 2.2 ms at 21 MB; about 10 GB at year 1. |
| events JOIN invoices on `export_batch` | `posting_refusal` users:532 (each JDE post) | SEARCH events by `action IN ('approved','final_approved')` (every approval ever), then a PK lookup and an overflow read per invoice. |
| `feedback WHERE invoice_id = ?` | `_forget_learning` invoices:476 (reject, reopen, send-back, withdraw), `delete_invoice` :517, `_keep_approved_coding` learning:213, `delete_feedback` :154, `_fill_tax_codes` :743 | **SCAN feedback**: 3.2 ms at 57k rows, so about 70 ms at 1.2M, inside write transactions. |
| `vendors WHERE vendor_key = ? OR substr(vendor_key,1,?) = ?` | `find_vendor` vendors:154-157 | **SCAN vendors**, because the `substr` defeats the PK index. 1.5 ms with 20k vendors, 3-4 times per `vendor_key_for`. |
| `sessions WHERE user_id = ?`; `created_at <= ? OR last_seen < ?` | users:423, 481, 509, 878, 955 | SCAN sessions (a small table; fine at 400 users). |
| `ephemeral WHERE expires_at < ?`; `token = ? AND kind LIKE …` | ephemeral:22, :59 | SCAN ephemeral (a small table). |
| `events WHERE action=? AND created_at >= ?` | `touchless_summary` capture:476-494, `approval_controls` accounts:399, `vouched_for` users:550, `_temporary_expired` users:658 | Reads every event of that action, then filters by date (`processed` is 300k a year). An index on `(action, created_at)` would fix it. |
| `events ORDER BY id DESC LIMIT 1000` filtered by action | `events()` accounts:115 | Sorts every event of the action in a temp B-tree (the Activity page). |
| `manager_requests WHERE status = ?` | managers:137, 151 | SCAN (small). |
| `vendors WHERE in_master …` | `has_vendor_master`, `vendor_choices` | SCAN vendors. |
| `feedback WHERE tax_source='picked' ORDER BY id DESC LIMIT` | `corrections` lessons:204-219 | SCAN feedback backwards, with a correlated `lower(description)` subquery per row. |
| `invoice_summary WHERE instr(column, ?)` | `summary_containing` with fewer than 3 characters or no FTS5 | SCAN (by design). |

### 3e. Full-table reads still on request paths
At 3,010 invoices, measured, with a linear extrapolation to 300k:

| call | 3,010 invoices | 300k invoices |
|---|---|---|
| `list_invoices()` with no status | 45 ms | about 4.5 s |
| `search_rows()` with no ids | 25 ms | about 2.5 s |
| `_vendor_invoice_dates` | 15 ms | about 1.5 s per process (`summary_memo`, recomputed whenever the "dates" topic changes, i.e. on each new invoice) |
| `vendor_summaries()` (57k feedback) | 177 ms | seconds; the feedback GROUP BY is **not** memoized; called by the Vendors, vendor and Statements pages |
| `metrics()` (57k feedback) | 151 ms | about 3 s each time a lesson is added; called by the **Settings page** (`web/pages/settings.py:465`) for one count `line_metrics()` already gives |

Other whole-table reads:
- `feedback_rows()` **cold** (the whole learning memory, 50k rows): **1.33 s and 50 MB held per process (verified)**,
  plus `aggregate()` at 0.42 s.
  - The stamp changes with every approval (new feedback rows).
  - The review screen calls `learning_rows` → `feedback_rows()` (`web/invoice_view.py:1422`).
  - So after every approval, each of the 9 processes pays about 1.7 s on its next review page or invoice read.
  - At about 2-3 approvals a minute (25k/month, 8-hour days), this happens all day.
- `hub_invoice_rows()` (hub:20): every invoice with ai_output, final_output and meta **decoded in memory**, on each
  hub run. At 300k invoices × 14 KB of JSON that is several GB of Python objects (an out-of-memory risk).
  `hub_feedback_rows` and `hub_outcome_rows` are also full reads.
- `invoice_columns()` with no ids (invoices:608): every invoice's JSON. Callers: `erp/dupaudit.py:109`,
  `erp/spend.py:212`, `erp/controls.py:79`.
- `_exported_totals` (purchasing:363): every `exported` event JSON-decoded in Python (memoized per process).
- Reader scorecards (capture:680-748): GROUP BYs over all of `reader_outcomes`, about 15M rows a year.
- `release_page_reads` and `release_verifications`: small.
- `repoint_invoices` (backup_files:163): every invoice row on each restore.

### 3f. Growth over a year at 25,000 invoices a month (300k)
Rows per invoice come from the code. Byte sizes come from the measured demo rows, scaled to the D-110 docstring's
"about 14 KB of JSON an invoice" and the "about 7 KB" of capture JSON.

| table | rows a year | about |
|---|---|---|
| invoices | 300k | 25-35 KB each (JSON 14 KB, extraction_md 3-10 KB) → **7.5-10 GB** |
| invoice_capture | 300k | 2-3 KB zlib → 0.6-0.9 GB |
| summary tables + FTS | 300k summary, about 1M codes, about 450k spend | 1.5 KB an invoice → 0.5 GB |
| events | 2-3M (6-10 per invoice) + about 0.4M logins and logouts | about 1 GB |
| reader_outcomes | **about 15M** (15 fields × 2-4 readers + fused) | about 2-2.5 GB with indexes |
| supplier_outcomes | about 4.5M | about 0.7 GB |
| lesson_approvals | about 6M (about 20 situations per approval) | about 0.8 GB |
| feedback | about 1.2M (4 lines) | about 0.4 GB with 4 indexes |
| line_texts | about 1.2M | about 0.2 GB |
| page_reads, verifications, azure_checks | ≤ 300k each | verifications.result_json can be KBs |
| login_attempts | about 200k (no purge of `ok=1` rows) | small |
| lessons | grows with vendors × fields × patterns | small |

- **Database total: about 12-20 GB at the end of year 1, growing linearly.** Nothing is retained, archived or
  vacuumed (no `auto_vacuum`, no `VACUUM`), so deletes leave free pages.
- **Backups:**
  - The daily backup is a full copy (`backup_to` backups:55) plus a full SHA-256 of the copy (`snapshot` →
    `_sha_size`).
  - 14 dailies are kept, plus the same 14 in the second folder (`copy_backup` copies the whole database every day,
    e.g. over SMB).
  - Labelled backups (`-before-update-…`, `-before-restore`, `-manual`) are **never pruned** (DAILY regex,
    backups:36).
  - At 15 GB that is about 420 GB of database copies, plus each server update's copy kept forever.
  - Invoice files (0.5 MB each, docs/SERVER.md:47) come to about 150 GB live, 150 GB in `backups/files` and 150 GB
    in the second folder.
  - **Plan for about 1 TB a year.**
- The `.files.json` list per backup has an entry per file (about 200 B, indent=1). At 300k files that is about 60 MB
  per list, and about 1 GB across the 14 dailies.
  - `prune()` (backup_files:569) json-loads **every** list on each daily run.
  - `snapshot()` stats every file of `invoices/` (os.walk). On Windows NTFS that is slow (about 50-100 µs a stat, so
    30-100 s at 300k-1M files).
- **Restore needs about 3× the database size free (verified):** after a restore the WAL is as large as the database
  (27.8 MB WAL for 27.6 MB) and cannot shrink while readers are open. On top of that come the safety backup and the
  database itself. There is no `journal_size_limit`, so the WAL file is never truncated.

### 3g. Sessions and users at 400 people
- The `users` table (about 400 rows) is fine.
- `sessions` holds one row per login (a new token each time). Expired rows are purged on each `start_session` by a
  full scan. A few thousand rows at most, so fine.
- `login_attempts`: `_known`, `_count_try` and `_account_paused_until` are about 6 indexed queries per login. `ok=1`
  rows are never purged. Fine for years.
- **The real cost is `session_user`'s once-a-minute `UPDATE sessions`** on the request path: about 7 writes a
  second at 400 users, each fsynced and each waiting behind any long writer (see 3a). Each `authenticate` and
  `check_code` is also a BEGIN IMMEDIATE.
- scrypt with N=2^14 takes about 16 MB and about 50-100 ms of CPU per login. That is fine, but a login burst at 9:00
  (400 logins) occupies the GIL of whichever worker handles it.

### 3h. What assumes one user or one process
1. **Per-process caches multiply work and memory.** There are 8 web workers and 1 background process
   (`web/server.py:221-299`).
   - `feedback_rows`: 50 MB each, re-read 9 times after every approval.
   - `summary_memo`: each worker recomputes the same 300k-row value after each change. The `_COMPUTING` lock only
     dedupes within one process.
   - `_REFERENCE`, and `memory._AGGREGATED` (rebuilt per process).
2. **Read-modify-write without BEGIN IMMEDIATE (lost updates and double events)**, verified in R1 for the managers:
   - `save_vendor` (vendors:193 reads `before` outside the transaction), `add_vendor_here` (notes concatenation),
     `set_tax_treatment`, `save_bank_accounts`.
   - `record_lesson` (`confirmed` from a stale read), `set_supplier_state`, `reopen` (`was`).
   - Fine for one person on a laptop; racy with 400 people.
3. `envfile.write_env` (:63) is a non-atomic `write_text`, so two workers saving Settings at once lose an update and
   a crash truncates `.env`.
4. `postings.append` (:94) appends to `jde_postings.jsonl` from any worker. Append is not atomic across processes on
   Windows (suspected interleaving).
5. Time is naive local time, `dt.datetime.now().isoformat()` (`_base._now` :291), with mixed epoch floats in
   sessions and login_attempts. This assumes one machine and one time zone (see section 8).
6. Nothing guards against the data folder sitting on a network share or mapped drive. SQLite WAL needs shared memory
   on one host, so two machines on one share risk corruption (suspected; grep found no UNC check for the database).
7. `_open()` silently creates a new empty database when the file is missing (_base:936). That suits a laptop, but on
   a server, after a mis-step by IT, users would write into an empty database.

### 3i. How hard a move to PostgreSQL (or SQL Server) would be, and where the seam goes
**SQLite-specific SQL** (grep counts: in core / elsewhere):
- JSON:
  - `json_extract` 24/2, `json_each` 9/7 (used for every IN list, `_in_list` _base:53), `json_valid` 11/1,
    `json_set` 3/1, `json_group_array`/`json_object` 5/9.
  - `fetch_rows`' `printf('%!.17g')`, `typeof()` and `hex()` transport (summary:274).
- `group_concat` 7 (the reference and check stamps).
- Conflict handling: `INSERT OR IGNORE` 4/1, `INSERT OR REPLACE` 4, `UPDATE OR IGNORE` 4 (no PG equivalent),
  `ON CONFLICT … excluded` 27/2 (portable).
- `rowid` 20 (lesson_approvals ordering and `corrections`, the merges, FTS rowid, `_recompute_vendor_keys` on
  erp_invoices).
- `BEGIN IMMEDIATE` 28 (in PG: `SELECT … FOR UPDATE` or SERIALIZABLE).
- Schema introspection: `PRAGMA` 29/2 (all migrations probe `table_info`), `sqlite_master` 8, `executescript` 3/1.
- 12 SQLite-syntax triggers (summary), and an FTS5 trigram virtual table (PG: `pg_trgm`).
- The `ap_fold` Python UDF (PG: `unaccent`).
- Behaviour that differs:
  - Boolean `SUM(x = 'y')` (8).
  - Bare-column `MAX(id)` (capture:703).
  - `LIKE` is case-insensitive in SQLite (14 uses, e.g. `detail LIKE '%"bulk": true%'`).
  - `COLLATE NOCASE`, `substr` on ISO date text (7), and 31 `created_at >= ?` comparisons on text.
  - `CAST(value AS INTEGER) + 1` on TEXT settings.
- Driver details: `lastrowid` 5 (use RETURNING), `sqlite3.Row` 8, the `?` placeholders in every query,
  `total_changes` and `PRAGMA data_version` (`_session_memo`), the `conn.backup()` backups and restores.
- Typeless columns: `summary_codes.code`, `invoice_capture.name_status`/`credit_note`.

**Where the SQL lives:** about 95% is in `core/store/`. The leaks:
- `store._conn()` is used outside core in `erp/jde_post.py:165` and `erp/jde_sync.py:70, :111`.
- Raw SQL passes through `summary_query`, `summary_fetch`, `summary_rows` and `directory_rows` from about 12 modules:
  `erp/directory.py`, `search.py`, `spend.py`, `insights.py`, `accruals.py`, `hub/*`, `coding/taxreturn.py`,
  `coding/bulk.py`, `web/pages/process.py`. That is 26 call sites and 36 SQL string literals outside core.

**The seam:**
1. Make `Store` the only owner of SQL: move the erp SQL into store methods or a `store/reports.py` part, and remove
   `_conn()` from erp.
2. Put a thin `Db` dialect object behind `StoreCore._open`/`_conn` with: `in_list(col)`, `json_get(col, path)`,
   `upsert(...)`, `insert_ignore`, `begin_write()` (which also composes, see R4), `returning_id`, `now()`, and
   `has_column`/`add_column`.
3. Replace the PRAGMA-probe migrations with versioned migration steps.
4. Make the summary refresh a function the dialect can call (PG: the same Python, triggers in plpgsql or app-side
   dirty marking).

**Effort:** the seam and the cleanup are **M** (2-4 days). A working PG backend is **L** (2-3 weeks including the
summary, FTS, backups by `pg_dump`, tests on both).
- **The reason to do it is not throughput at 25k a month.** SQLite holds 20 GB and about 50 small writes a second
  if the transactions are short.
- The reasons are high availability (a second app host, failover), point-in-time recovery, no single-file lock, and
  monitoring. The current bottleneck is Python CPU and the per-process caches (D-111: "about 1.6 requests a second"
  per process), not SQLite.

---------------------------------------------------------------------------------------------------------------------

## 4. Efficiency hotspots (with evidence)

Times are from the 20k-vendor, 57k-feedback scratch database unless noted.
1. **`feedback_rows()` cold read:** 1,325 ms, 50 MB, plus `aggregate` 424 ms, re-read after every approval in every
   process (learning:34-71; the review screen at `web/invoice_view.py:1422`). Fix: an incremental read
   (`id > last`, and deletions via a counter in `feedback_counts`), or a vendor-scoped read plus a shared
   pre-aggregated table.
2. **`vendor_ids()`:** 57.6 ms per call with 20k vendors. `supplier_key_for` takes 52 ms per call (capture:117), and
   it is called on every read and approval. Fix: memoize on `masters_version` (already counted in
   `count_masters_change`, accounts:73).
3. **`find_vendor` / `vendor_key_for`:** SCAN vendors, 1.46 ms, 3-4 lookups per call. Fix: a PK seek plus a range
   seek `vendor_key >= 'k #' AND vendor_key < 'k #￿'`, and pass `_aliased`'s result through once.
4. **`_REFERENCE_STAMP` and `_CHECKS_STAMP` group_concat every GL account and cost center** on each
   `reference_data()` and `checks_stamp()`: 2.3 ms and 3.0 ms with 5k GL and 2k CC (accounts:36-56). Each invoice
   and many pages call them, several times. Fix: a `reference_version` counter bumped by the writers (as
   `masters_version` is).
5. **A connection per request**, plus a cold page cache, schema parse, `PRAGMA journal_mode` and UDF registration on
   each open (0.74 ms, with 2 MB of cache thrown away). Fix: a per-thread connection pool with `cache_size`,
   `mmap_size`, `synchronous=NORMAL` and `temp_store`.
6. **Writes on the request path:** `session_user` (users:912), `/review/{id}/check` → `refresh_confidence` plus
   a draft upsert (`web/pages/invoice.py:786-788`). Each write fires the summary trigger (a recompute of that
   invoice) and an fsync.
7. **Approvals:** about 20 lesson transactions (ai/lessons.py:132), and `list_accounts("cost_centers")` per approval
   (invoices:765). `approve_invoice` measured 37 ms outside a session and 9 ms inside one.
8. **`metrics()` on the Settings page** for one number (`web/pages/settings.py:465`), and **`vendor_summaries()`**
   computed for all vendors to show one (`web/pages/vendors.py:315`).
9. **`summary_memo` values over every invoice** (`vendor_invoice_dates`, `recurring`) are invalidated by every new
   invoice and recomputed per process. `MEMO_WAIT = 600` s (summary:234) lets requests queue up to 10 minutes
   behind a slow compute, which can exhaust the threadpool (suspected).
10. **`ReferenceTable.get` and `.codes` are linear** (reference_data:117-124). `.codes` builds a new list per call,
    and `get` is called per line in `coding/validation.py:162, 181` and `web/pages/invoice.py:1193-1214`. Small but
    free to fix.
11. **Backups:** a full copy and full SHA-256 per day, a full copy to the second folder, `prune` loading every list,
    and `snapshot` statting every file (3f).
12. **`invoice_file` / `find_moved_file`:** an os.walk of all of `invoices/` per missing file (paths:345;
    invoices:390). After a data-folder move, a list page of 50 missing files means 50 walks of 300k files. The
    30 s `_NOT_FOUND` cache has no size cap.

---------------------------------------------------------------------------------------------------------------------

## 5. Cleanliness

**Long functions** (ast count of lines):

| function | lines |
|---|---|
| `audit.describe` (audit:281) | 198 |
| `StoreCore._migrate` (_base:490) | 197 |
| `schema.build_json_schema` (schema:85) | 140 |
| `LessonsMixin.corrections` (lessons:171) | 90 |
| `_line_feedback` (invoices:752) | 85 |
| `approve_invoice` (invoices:659) | 77 |
| `import_accounts` (accounts:164) | 77 |
| `import_vendor_master` (vendors:215) | 74 |
| `add_invoice` (invoices:53) | 68 |
| `Settings.from_env` (config:273) | 59 |
| `_restore_from` (backups:354) | 58 |
| `change_password` | 55 |
| `import_purchase_orders` | 55 |
| `replace_proposal` | 53 |
| `_Work.part` | 52 |
| `snapshot` | 52 |
| `_recompute_vendor_keys` | 51 |
| `_replay_without` | 50 |

**Duplication:**
- **Three near-identical queues** (`page_reads`, `azure_checks`, `verifications`), each with queue, next (BEGIN
  IMMEDIATE, select, update), finish, release, counts and stale handling (capture:750-984, lessons:274-385). A
  generic queue helper would save about 150 lines.
- The column-add migration pattern repeats about 20 times in `_migrate` (:501-677), while helpers already exist:
  `users.add_columns` (:261) and `postings.ensure` (:49).
- Accent and case folding exists 5 times: `_base.fold_text` :392, `memory._fold` :79, `headers.header_key` :195,
  `users.fold_username` :132, and the normalisation inside `schema.province_code`.
- "The latest approval event" is queried in 4 places: `latest_approval_id`, `final_approve` :903, `_generation`,
  and `_replay_without` :232.
- `_Work.part("po")` re-parses JSON that `_Work.__getitem__` has already decoded (summary:719).

**Dead code:**
- `ManagersMixin.waiting_manager_map` (managers:147) has no caller.
- Others are referenced only once: `layout_sources`, `supplier_templates_count`, `page_read_pages_average`,
  `fused_kind_scorecard`, `recently_approved`, `summary_fetch`, `has_other_vendors`.

**Confusing names (same name, different things):**
- `_base._forget_learning(path)` (:78) drops process caches. `InvoicesMixin._forget_learning(conn, id)`
  (invoices:470) deletes learning rows.
- `_base.ACCOUNT_TABLES` (:40, GL and CC) versus `users.ACCOUNT_TABLES` (:271, users, login_attempts, sessions).
- `_base._tax_signature` (:991, type and amount) versus `audit._tax_signature` (:132, five fields).
- `users.LOCKED` (a message) versus `postings.LOCKED` (a status tuple); `users.UNKNOWN` ("?") versus
  `postings.UNKNOWN` ("unknown").
- `Store.session()` (a database connection) versus `sessions` and `session_user` (logins).
- `_Held` (a connection) versus `backup_files.Held` (backup contents).
- The store part `store/capture.py` versus the `ap_coder/capture` package.
- The learning memory is `feedback`, as distinct from `lessons` (another table): `core/memory.py`,
  `store/learning.py`, `store/lessons.py` and `ai/lessons.py` overlap.

**Layering violations:**
- `core` is not a base layer. It has 54 upward imports (most of them lazy, to dodge cycles):
  - `coding` (about 30): tax, terms, po, vendors, line_tax, validation, bulk, taxreturn, banks, rules.
  - `capture` (about 12): supplier, workflow, confidence, types.
  - `ai` (4): offline_coder, lessons, history, page_worker.
  - `erp` (4, in `summary._Work`): spend, search, insights, accruals, the last via a *private* `_bill`.
  - `ops` (3): `ops.service.folder_refusal` (backups:171, 380) and `ops.help` (audit:266).
- The database layer computes report facts by calling business code inside its transactions (D-110). That couples a
  backend change to the whole app.
- `core/ui.py` is HTML presentation that belongs in `web/`.
- `core/memory.py` is learning logic (belongs in `ai/` or `coding/`).

---------------------------------------------------------------------------------------------------------------------

## 6. Possible bugs and risks

| # | what | status |
|---|---|---|
| R1 | **`answer_manager_request` double answer** (managers:174-192): the SELECT runs in autocommit, then `UPDATE … WHERE status='waiting'` without a rowcount check, then the invoice update and the event. Two managers or browsers answering at once both "win": the audit trail records both `manager_confirmed` and `manager_sent_back`, and the request keeps one of them. | **Verified: 150 of 150** simultaneous answer pairs (`race.py`) logged two answers. Fix: BEGIN IMMEDIATE or a rowcount check; a test is needed. |
| R2 | `session_user` blocks every page for as long as any write transaction runs (up to 30 s), and its "best effort" `except` only fires after the full timeout (users:912-916). | **Verified** (3.05 s wait in `locks.py`). |
| R3 | Building a `Store()` on a thread that holds a session for the same database **commits the open transaction** (`executescript` issues COMMIT, _base:465). A rollback afterwards leaves a half-done change. | **Verified** (the outer write survived its rollback). Latent: no current caller found. |
| R4 | Transactions do not compose: the 28 `BEGIN IMMEDIATE` sites raise `cannot start a transaction within a transaction` when called inside an open write (e.g. `forget_if_withdrawn` within `with store._conn()`). Any batching of an approval's 25-30 transactions into one hits this. | **Verified.** |
| R5 | `_on_this_network` (config:118-134) refuses a dotted corporate name for the model server (`macstudio.corp.example.com`, `macstudio.ad.acme.ca` give False) unless `AP_ALLOW_INTERNET=1`. Only IPs, bare names and `.local/.lan/.home` names pass. The target puts models on a Mac Studio reached by `AP_LLM_BASE_URL` (`ai/local_llm.refused_address` :103). `.local` names also resolve via mDNS on each call (`_resolved`, getaddrinfo), which can take seconds on Windows. | **Verified** (False and True table in the session). |
| R6 | Missing `feedback(invoice_id)` index: every reject, reopen, send-back or delete scans all of feedback inside its write transaction. | **Verified** plan and timing. |
| R7 | Missing `invoices(export_batch)`: `undo_export_batch`, `delete_empty_batches` and `posting_refusal` read the whole invoices table, overflow pages included. | **Verified** plan. |
| R8 | After a restore the WAL is the size of the database and stays so (no `journal_size_limit`); a restore needs about 3× the database size free. | **Verified** WAL size; "stays" is documented SQLite behaviour. |
| R9 | `_open` recreates an empty database if the file is missing (_base:936-945; test `test_a_deleted_database_is_recreated_not_broken`). On a server, a moved or deleted file means users keep working in an empty database. | Suspected risk (by design for the laptop). |
| R10 | A restore while users work: writes made after the safety backup and before the overwrite are lost without a trace; every session is deleted; requests hit 30 s lock waits and then `database is locked` during the copy. There is no maintenance mode. | Suspected (from the code). |
| R11 | Labelled backups are never pruned, and every server update adds a full copy (backups:36, `drop_old` dailies only). | Verified by reading. |
| R12 | `hub_invoice_rows()` loads every invoice's decoded JSON into memory (an out-of-memory risk at 300k). | Suspected (by size). |
| R13 | No `PRAGMA foreign_keys`: `invoice_capture`'s `ON DELETE CASCADE` (_base:162) is decorative, and cleanup relies on manual deletes in `delete_invoice`. | Verified by grep. |
| R14 | Naive local timestamps compared as text (31 sites). The DST fall-back hour repeats, which affects ordering by `at` in `supplier_outcomes`, `lesson_approvals` and `_withdraw_lessons` (`ORDER BY at DESC, rowid`), and the `STALE_READING_HOURS` checks. A database moved between machines in different zones mixes zones. | Suspected. |
| R15 | `envfile.write_env` is non-atomic (a crash truncates `.env` with its secrets; two workers lose an update). | Verified by reading. |
| R16 | `authenticator.load_key` uses `os.open(…, 0o600)`, which sets no ACL on Windows (the key is readable per the folder's ACL), and `os.link` on some file systems raises OSError rather than FileExistsError. | Suspected (Windows target). |
| R17 | `_NOT_FOUND` (_base:62) grows without a cap; `_FOUND` is capped at 50k. | Verified by reading (minor). |
| R18 | Other read-modify-write races listed in 3h.2 (`save_vendor`, `add_vendor_here`, `record_lesson.confirmed`, `set_supplier_state`). | Suspected. |
| R19 | Per-process `_FEEDBACK` after a restore done by *another* process: the stamp (max id, count, newest row) could coincide in pathological cases; only the restoring process calls `_forget_learning`. | Suspected (low). |

---------------------------------------------------------------------------------------------------------------------

## 7. Test gaps (tests/core, 4,142 lines over 23 files)

Covered well:
- Sessions joining transactions (`test_store_session.py`), export races (`test_store_races.py`), some index checks
  (`test_store_perf.py:105`: by file, total, event).
- Migration backup and refuse (`test_update_database.py`), backups in depth (`test_backups.py`, 728 lines), users
  and TOTP, managers' happy paths.
- The summary is tested in `tests/erp/test_invoice_summary.py`.

Missing:
- Concurrent `answer_manager_request` (R1). There are no multi-thread tests besides exports.
- A write lock held while requests run: `session_user` latency, and a `log_event` timeout
  (`tests/web/test_server_scale.py:612` only fakes an immediate OperationalError).
- Index guards for `export_batch`, `feedback.invoice_id`, `find_vendor` (extend `test_the_lookups_by_file_total_and
  _event_use_an_index`).
- Scale budgets: `vendor_ids`/`supplier_key_for` with 20k vendors, `feedback_rows` invalidation cost, `metrics` on
  the Settings page, `reference_data` stamps with 5k accounts.
- Migration on a large database (time and lock), and a migration while another process holds connections.
- Disk full on a database write (only backups and restores are tested for ENOSPC).
- Nested BEGIN IMMEDIATE (R4) and Store-in-session (R3).
- Time zone and DST ordering.
- The WAL size after a restore.
- `hub_*` memory at volume.
- `_on_this_network` for corporate FQDNs (in config; tests live elsewhere).

---------------------------------------------------------------------------------------------------------------------

## 8. Scenarios people may not have thought of

- **Two server processes on one database:**
  - Two services, or one left running during a manual update: the queues claim work by BEGIN IMMEDIATE plus a
    reader id (safe); backups use `exclusive` (safe).
  - An *older* process still running against a migrated schema is not detected after start-up.
  - Two **machines** (the Mac mini pilot and the VM) on one share would break WAL (3h.6).
- **A backup during heavy writes:** `backup_to` is one `backup(-1)` step, so it is a consistent snapshot and does
  not block writers. But the WAL cannot checkpoint past it, so it grows for the whole copy (minutes at 15 GB).
  `_sha_size` and `copy_backup` add more minutes of I/O on the database disk.
- **A restore while 400 people are logged in:** R10. Every session ends, drafts are replaced by the backup's, and
  work made between the safety backup and the copy is lost. Lock waits turn into 500s. Workers' `summary_memo`
  values are dropped by the new `summary_id`.
- **A migration on a big database:** a full summary rebuild or a vendor-key recompute takes minutes inside
  `Store()`. Downtime equals that time plus the before-update copy (full copy and SHA, plus a files snapshot). There
  is no progress output; the update window must allow for it.
- **Clock or time zone:**
  - The VM in UTC versus users in Eastern: due dates (`dt.date.today()`), the day boundary of `azure_pages_on`,
    greetings, and "today" in reports all follow the server clock.
  - DST repeats an hour.
  - Sessions use epoch time (robust); everything else uses naive local text.
- **Disk full:**
  - Database writes raise `database or disk is full` as 500s.
  - The WAL cannot grow, so every user is stuck.
  - Daily backups on the same volume eat about 14× the database (3f).
  - There is no free-space check before a backup or a restore (restore needs about 3×).
- **The model server over the network** (Mac Studio): an FQDN is refused (R5), and each `.local` lookup costs an
  mDNS query.
- **One big vendor** (a fuel or telecom supplier with 5,000 invoices a year):
  - `_vendor_invoices` reads all of its rows plus JSON per review page (vendors:413).
  - `find_duplicates` normalises all of its invoice numbers in Python (invoices:147-161).
  - `supplier_stats` sorts its outcomes.
- **Learning memory past 50,000 lessons:** a year of approvals gives about 1.2M feedback rows, so `feedback_rows()`
  holds only the newest 50k. Older vendors fall back to `learning_rows` adding their own rows. That is fine, but the
  50k cap means the "whole memory" read is always at its maximum cost.
- **A power cut on the VM:** WAL with `synchronous=FULL` is durable. If someone later sets NORMAL for speed, the last
  transactions can be lost; decide deliberately.

---------------------------------------------------------------------------------------------------------------------

## 9. Recommended goal items for core

Each item gives its benefit, a size (S ≤ half a day, M 1-2 days, L 3+ days) and how to prove it.

1. **Fix the manager double-answer race (R1)** with BEGIN IMMEDIATE and a rowcount check. Benefit: a correct audit
   trail. S. Proof: the threaded test from `race.py` gives 0 of 150 doubles; add a line to docs/BUGS.md.
2. **Add the missing indexes:** `invoices(export_batch)`, `feedback(invoice_id)`, `events(action, created_at)`,
   `sessions(user_id)`, `ephemeral(expires_at)`; rewrite `find_vendor` to range-seek. Benefit: no full scans in
   write transactions. S. Proof: EXPLAIN QUERY PLAN guards in `test_store_perf.py`; `same_behaviour.py`.
3. **Connection setup and PRAGMAs:** `synchronous=NORMAL` (decide deliberately, D-NN), `cache_size` (for example
   64 MB), `mmap_size`, `journal_size_limit`, `wal_autocheckpoint`, `PRAGMA optimize` on close or daily, and
   `foreign_keys` once orphans are checked. Optionally a per-thread connection pool. Benefit: fewer fsyncs, a warm
   cache, bounded WAL. M. Proof: the load test (`tests/web/test_server_scale` or a bench) shows p50/p95 before and
   after; the WAL size after a restore is bounded.
4. **Take writes off the request path:** `session_user` writes `last_seen` with a short busy timeout (for example a
   0.2 s connection-level `busy_timeout`) or in memory, flushed in batches. Benefit: pages never wait 30 s behind a
   writer. S. Proof: the `locks.py`-style test: `session_user` returns in under 0.3 s while a writer holds the lock.
5. **Composable transactions plus one transaction per approval:** a `begin_write(conn)` helper that does BEGIN
   IMMEDIATE only when no transaction is open (fixes R4); `record_lessons(rows)`; and wrap `learn_from_approval` in
   one session and transaction. Benefit: an approval goes from about 25-30 commits to 1-3 and cannot be half-learned.
   M. Proof: count commits (`total_changes` or a wrapper) in a test; approvals per second in a bench.
6. **Stop the per-approval whole-memory reload:** incremental `feedback_rows` (read `id > last`, track deletes) or
   a vendor-scoped read on the review screen; memoize `vendor_ids` on `masters_version`; a `reference_version`
   counter instead of group_concat stamps. Benefit: removes about 1.7 s per process per approval, 52 ms per
   `supplier_key_for`, and 2-3 ms per stamp. M. Proof: the timings in section 4 re-measured at 20k vendors and 50k
   lessons; tests that count reads (as `test_store_perf` does).
7. **Cheaper page-level reads:** `line_metrics()` on Settings, a per-vendor `vendor_summary(key)`, a memoized
   feedback GROUP BY in `vendor_summaries`, streaming or keyset reads for `hub_invoice_rows`. S-M. Proof: page
   timing at 300k synthetic invoices; peak memory of a hub run.
8. **Shorter long transactions:**
   - `create_export_batch` and `undo` set-based (one UPDATE with `json_each`, events by `executemany`).
   - Chunk the summary refresh for large batches (commit per chunk where safe).
   - Migrations print progress.
   - Benefit: the lock is held for under 1 s for a 1,250-invoice batch. M. Proof: `export_t.py`-style timing;
     `same_behaviour.py`.
9. **Retention and backup sizing:**
   - Prune labelled backups (keep the last N before-update copies).
   - Check free space before a backup or restore (refuse below 3× the database size).
   - A retention or archival policy for `reader_outcomes`, `supplier_outcomes` and `events` (for example, aggregate
     after 12 months).
   - A weekly VACUUM or `auto_vacuum=INCREMENTAL` decision.
   - Benefit: a predictable disk (about 1 TB a year otherwise). M. Proof: a synthetic one-year growth run (scaled);
     a backups test for pruning and the free-space refusal.
10. **Allow the model host by name on a company network (R5)**, e.g. an Admin-set allow-list (`AP_LLM_HOSTS`) or
    accepting names that resolve to private addresses (cache the resolution). Benefit: the Mac Studio and Mac mini
    work by DNS name without opening the internet. S. Proof: a unit test of `_on_this_network`/`refused_address`
    with a corporate FQDN; the offline tests still pass.
11. **The PostgreSQL seam (do not port yet):**
    - Forbid `store._conn()` outside core (move the `jde_post`/`jde_sync` SQL into the store) and move the erp raw
      SQL behind store methods (`store/reports.py`).
    - Add a small dialect layer for `in_list`, `json_get`, `upsert`/`insert_ignore`, `begin_write` and `add_column`,
      and replace the PRAGMA-probe migrations with numbered steps.
    - Benefit: a later PG or SQL Server port becomes about 2-3 weeks instead of open-ended, and gives HA when
      needed. M (seam) / L (port). Proof: a meta test (`tests/meta/test_code_rules.py`) that no module outside
      `core/store` imports sqlite3 or calls `_conn`; all tests green.
12. **Server safety rails:** refuse to create a new database silently in server mode (R9); maintenance mode during
    a restore (writes refused with a clear page, R10); detect a data folder on a network share; atomic
    `write_env`. S-M. Proof: tests per rail.
13. **Cleanups (when touching the files anyway):**
    - Rename the clashing names (`_forget_learning`, `ACCOUNT_TABLES`, `_tax_signature`).
    - Drop `waiting_manager_map`.
    - A generic queue helper for the three queues.
    - Use the `add_column` helper in `_migrate`.
    - Move `core/ui.py` to `web/` and `ops.service.folder_refusal` out of the store's dependencies.
    - Benefit: a smaller surface for the seam. M. Proof: `same_behaviour.py` and the tests.

**On "does SQLite hold up":** yes, for one VM at 25k invoices a month, *if* items 2-8 are done. The data is
12-20 GB a year and the peak is tens of small writes a second. The limits that will bite first are:
- Python and per-process CPU and memory: whole-memory reloads, full-table memos, about 1.6 requests a second per
  process (D-111).
- Long write transactions behind one global lock.
- Backups and disk sizing.

PostgreSQL becomes necessary for HA, a second app host, or PITR, not for this volume. Build the seam now (item 11)
so that choice stays cheap.
