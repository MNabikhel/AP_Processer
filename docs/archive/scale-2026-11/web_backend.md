# Web backend (`ap_coder/web/`, 43 files, ~17,600 lines): notes for the 36-hour goal

Read on `main` at af9925b. Every `.py` in `ap_coder/web/` and `pages/` was read. `qrcodegen.py` (909 lines) is the vendored
Nayuki QR library: skimmed only. Also read: `ap_coder/__main__.py`, `ops/cli.py` `cmd_serve`, and the store code each
finding touches (`core/store/_base.py` sessions, `users.py` sessions, `invoices.py` approve and drafts, `accounts.py`
checks stamp, `coding/bulk.py` issues_today, `ai/local_llm.py` status cache).

**Verified** means reproduced with a TestClient snippet. The snippets are in
`scratchpad/verify/` (v1 to v11, plus `common.py`). Each runs on its own temp database;
they need the repo's `.venv`. **Read** means found by reading the code and not run. **Suspected** means it needs a test.

Note for the lead: `ap_coder/bench/coding.py` shows as modified in `git status` (01:12 and 01:26 UTC). I didn't edit
it, and none of my commands write there. My only file edits were in the scratchpad.

---

## 1. Map

### The core modules
| File | What it does |
|---|---|
| `app.py` (376 lines) | `create_app` (210-376) builds the app: middleware, static mounts, the routers in `pages.MORE_MODULES` and `pages.PAGES`, and `/`, `/healthz`, `/shutdown`, `/jobs/{name}`, plus the error handlers. Also here: `CheckedStaticFiles` (62-71, `no-cache`); `DailyBackup` (74-124, a backup thread per process); `BackupCheck` middleware (127-139); `EnvSettings` (147-192, re-reads the .env when its stat or `os.environ` changes); `start_background_work` (195-207: page reader, Azure check, JDE sync). |
| `server.py` (532) | `serve` runs a single uvicorn process (226-268). `_serve_workers` (283-319) runs the uvicorn `Multiprocess` supervisor with N workers through the `worker_app` factory (412-425), plus one `serve --background-work` subprocess (`BackgroundWork` 448-488, `run_background` 491-532). Stopping works through a stop file and `_Supervisor` (358-405). TLS is done by uvicorn itself (`uvicorn_options` 136-146, ciphers 133). Keep-alive is 75 s (206), the graceful stop 25 s (198). `default_workers` = min(cpu, 8) (221-223). |
| `core.py` (620) | Shared page helpers. `render` (580-620): each page counts the rail, the approvals today and the demo count, takes the flash, and on review/process reads the reading status. `one_connection` and `page_router` (141-184): plain handlers get one SQLite connection per request; async ones get nothing. Also flash messages kept in the DB (339-411), `local_url`, `stale_page`/`page_version` (454-472), `jde_shown`, `can`. |
| `security.py` (407) | `LocalOnly`: Host check, the custom header and Origin check for POSTs (136-182). `SecurityHeaders`: CSP and HSTS on the server (190-225). `limit_body` (260-289). `AccessLog` (366-407): one line per request, and a `downloaded` event for each download. |
| `identity.py` (397) | The `Identity` middleware. Laptop or network mode uses a signed name cookie (`/who`, 260-307). Server mode (309-365) looks up the session (`store.session_user`, in the threadpool), applies the role rule (`roles.need_for`), the must-change-password redirect and the body limit, and wraps `send` in `_no_store` for anything that needs a login. |
| `roles.py` (246) | `RULES` (58-178): one table from (method, path) to what the route needs. Default-deny: no rule means ADMIN (`need_for` 208-229). `can` (235-246) caches needs per path, up to 4,096. `JOB_NEEDS` (181). |
| `access.py` (62) | `is_admin`, `may_lift_hold`, `person_key`/`key_of` (whose drafts). |
| `jobs.py` (207) | Background `Job` threads. With several workers, a job is shared through DB rows: `claim_job`, `renew_job` every second (`RENEW`, 38) and `finish_job`. |
| `files.py` (304) | `download()`; uploaded spreadsheets kept in the DB ephemeral table (`keep_upload` 84); pandas readers with size and column caps. |
| `document.py` (404) | Page sizes and PNGs (pymupdf / PIL) with a per-process LRU of 96 MB (36, 118-155, 181-202); `page_words`, with an OCR fallback (261-289). |
| `errors.py` (115) | `FriendlyErrors` middleware: turns an exception into an error page or JSON, logged to `~/.ap_coder/logs/dashboard-errors.log`. |
| `invoice_view.py` (2298) | The review screen's model: `Draft` (253-355); `analyse` (1725-1844), which runs the full `finalise_coding` plus many store reads on every check; `grid`, `reader_state`, `reviewer_state`; the grid actions. |
| `charts.py`, `minimd.py`, `qrcodegen.py` | SVG charts, a small Markdown renderer, the QR library. |
| `__init__.py`, `pages/__init__.py` | Docs; `PAGES` (rail order), `MORE_MODULES`. |

### Route groups (196 routes)
- **Review screen** `pages/invoice.py` (1301): GET `/review/{id}`, `page/{n}.png`, `words`, `reader` (the poll), `approved.pdf`. POST `check`, `approve`, `undo-approve`, `park`, `manager`, `reject`, `note`, `teach`, `split`, `apply-rules`, `po-coding`, `suggestion`, `fill-lines`, `vendor`, `read-pages`, `review-again`, `start-over`, `resume`, `restore`, `correct`. Drafts live in `app.state`, or are re-read from the DB on each request when there are several workers (93-230).
- **Queue** `pages/review.py` (689): GET `/review` with tabs; POST `bulk-approve`, `sample-gl`, `{id}/delete|reopen|retry|unpark|final-approve|send-back`. Module-level caches `_TO_FIX` and `_PAY_FROM` (128-129).
- **Import** `pages/process.py` (1032): GET `/process`, `/process/status` (the poll); POST `upload`, `folder`, `open-folder`, inbox actions, `/demo/load|remove`. The import job body is `process_files` (571-623). `ImportQueue` (740-862) is in memory. The per-account upload allowance is at 94-117.
- **Directory** `pages/search.py`: `/search` (SQL paging, 100 rows), CSV and XLSX exports of up to 50,000 rows, presets, `approved.pdf`, `unpark`.
- **Exports** `pages/exports.py` (681): the page; `create` (async); batch downloads (built in memory); approved PDFs ZIP; undo; the custom layout; the ERP register upload and import; the duplicate audit; JDE API dry-run, post, release and retry (jobs).
- **Master data**: `accounts.py` (GL, cost centers, tax, bank, rules, policy; upload/import/save mostly async), `vendors.py`, `purchase_orders.py`, `statements.py`.
- **Analytics**: `insights.py`, `spend.py` (+ `/spend/data.xlsx`, every invoice), `month_end.py`, `sales_tax.py`, `activity.py` (+ events.csv and the controls report), `learning.py` (coding, suppliers, lessons, readers, history upload/teach, training.zip).
- **Settings**: `settings.py` (review, data and backups, restore, about), `settings_reading.py` (LM Studio status, self-test, load model, AI reviewer, Azure), `settings_automation.py`, `settings_jde.py` (incl. the AIS test and sync job), `settings_hub.py`, `azure.py`.
- **Accounts**: `login.py` (password, then TOTP; `/logout`; `/account/password`; end other sessions), `users.py` (Admin user management; temporary passwords in memory, or sealed in the DB), `who.py` (the office-network name), `manager.py` (Manager role, D-138), `help.py` (help pages, report a problem, a cached setup report).

### How uvicorn is started
- `python -m ap_coder serve` → `ops/cli.py:347 cmd_serve` → `server.serve`. `--server` sets `AP_SERVER_MODE=1`, needs TLS (`tls_refusal`) unless `--insecure-http`, and sets the host to 0.0.0.0. `--proxy <ip>` turns on `proxy_headers` and `forwarded_allow_ips`.
- Workers come from `--workers`, else `AP_SERVER_WORKERS` (CLI default 1, `cli.py:385-390`); the service uses `server.json` or `default_workers()` = min(cpu, 8). If there is more than one, `_serve_workers` runs uvicorn's supervisor on one bound socket with spawned workers (`factory=True`, `timeout_worker_healthcheck=30`), plus the background process.
- Not set: `limit_concurrency`, `limit_max_requests` (workers are never recycled), backlog (default 2048), the size of the anyio thread pool (default 40 per worker), and gzip.

---

## 2. Request lifecycle

### Middleware order (outermost first), verified with `app.user_middleware` (v1, v4)
`ServerErrorMiddleware` (Starlette) → **AccessLog** (server only) → **SecurityHeaders** → **LocalOnly** → **StoreContext** →
**Identity** → **FriendlyErrors** → **BackupCheck** (only where `background=True`, so not in server workers) →
`ExceptionMiddleware` → router. They are added at `app.py:266-278`; the last one added runs first.
- `LocalOnly` builds a dict of the headers per request and checks Host, the proxy, and the header and Origin on POSTs.
- `Identity`, server mode: `roles.need_for` walks the ~200 leaf routes, matching regexes, for every request
  (`roles.py:219-229`; routing then does it again). It skips the session lookup only for `/static/`, `/fonts/` and `/assets/`.
- `AccessLog` writes the log line synchronously, on the event loop, in `finally` (`security.py:402`). A download
  event is written in the threadpool.

### The session lookup on each request (server mode)
- `identity.py:320-323`: `await run_in_threadpool(store.session_user, token)`. This opens its own SQLite connection,
  separate from the handler's (verified, v4: 2 connections opened per request). `users.py:888-920`: a SELECT that joins
  sessions and users; `last_seen` is updated at most once a minute (`SEEN_EVERY`), a write.
- Any request extends the idle timeout, including **automatic polls** (see §6).

### The database connection on each request
- `page_router()` wraps every plain `def` handler that takes `request` in `one_connection` (`core.py:141-184`): one
  thread-local connection (`_base.py:899-917`), opened with `sqlite3.connect(timeout=30)` and
  `PRAGMA journal_mode=WAL` on **every** open (`_base.py:934-946`). There is no pool: the connection is closed at the
  end of each request. Steady state is 1 connection per request on the laptop and 2 in server mode (v1, v4).
- **Async handlers get no shared connection**: each store call opens and closes its own (`_base.py:949-982`).
- `issues_today` and the bulk approve open nested sessions; that's fine.

### Sync and async handlers, and the thread pool
- 70 async defs in `web/`. Many do **blocking DB, pandas or file work on the event loop**. **Verified** (v2: Store
  `_conn` calls made while an asyncio loop runs in that thread):
  - `/review/{id}/fill-lines`: 4/4 store calls on the event loop.
  - `/review/{id}/apply-rules`: 39/39, which includes the whole `view.analyse`, i.e. `finalise_coding` (`invoice.py:1128-1133, 1170-1176`).
  - `/exports/create`: 2/2. The whole batch, with JDE validation of every invoice ticked, runs inside
    `with store.session()` on the loop (`exports.py:365-370`); the comment says that is safe because nothing is awaited.
    It is safe for the connection, but it blocks the worker.
  - `/accounts/rules`: 4/4.
  - `/review/{id}/check` correctly uses `run_in_threadpool` (0/37 on the loop).
- **Read, same pattern**: `split`, `po-coding`, `suggestion` (`invoice.py:1145-1201`, through `_posted_in_review`/`_coded`).
  Every upload and import: `accounts.py:294-525`, `vendors.py:339-377`, `purchase_orders.py` `po_upload`/`po_import`,
  `exports.py:516-549` and `statements.py:136`, which parse up to 50 MB with pandas (`files.receive_sheet` → `read_sheet`).
  `learning.py:620-670`: history upload and teach parse an AP history of up to 50 MB, JSON-encode every row into the DB,
  then import it, all on the loop. Also `settings.py:186` `save_review` (writes the .env), `settings_hub.py:69`, `manager.py:125` `record` (writes a file), `exports.py:493/506`, and
  `process.py:866-872` (`upload_refusal` is a DB write on the loop before the threadpool hand-off).
- While one of these runs, **every** request to that worker stalls, static files and session lookups included.
- The thread pool is not sized: anyio's default of 40 tokens per worker. Long sync requests hold tokens: the 50k-row
  XLSX export, `spend/data.xlsx`, approved-PDF ZIPs, `load_model` (an LM Studio model load), the JDE AIS test (30 s
  per request, `jde_ais.py:67`), and `backup_in_background`, which joins for 5 s.

### In-process state that breaks, or costs, with several workers
| State | Where | With N workers |
|---|---|---|
| Drafts | `invoice.py:93-138` | Handled: when `processes>1` the holder is `request.state`, so **every request re-reads ALL drafts in the DB** (`store.invoice_drafts()`, JSON-decoded). Verified (v11): with 1,000 others' drafts, POST /check goes from 46 to 90 ms; the cost grows linearly. |
| `_TO_FIX`, `_PAY_FROM` | `review.py:128-129` | Correct (keyed by a hash of the content) but **unbounded**, per process; one entry for every invoice ever queued. |
| Page PNG LRU (96 MB) and `_sizes_cached` | `document.py:36,71,202` | Per worker: N × 96 MB, and a low hit rate with round-robin routing. |
| `ImportQueue` | `process.py:740-862` | Per process; closures are lost on restart. Partly published (`set_job_waiting`); a 2-second `_watch` thread per worker. |
| Jobs | `jobs.py` | Shared via DB rows. The job's thread runs in **the web worker that started it** (the import pipeline, OCR included). The row is rewritten every second while it runs. |
| `local_llm._cache` | `ai/local_llm.py:39` | Per process; no single-flight (see §3). "Check again" (`settings_reading.py:416-432`) and `forget_setup_report` clear only the worker that answered. |
| `DailyBackup` | `app.py:262` | Each worker has one; manual backups run in that worker's thread. The cross-process lock `backup_files.exclusive` prevents two at once (good). |
| `EnvSettings` / `os.environ.update` | `app.py:147`, `settings.py:96-110` | Handled by re-reading the .env's stat (B-1404). It does a `stat()` and builds a `frozenset(os.environ)` on every `get_settings`. |
| Temporary passwords | `users.py:41-94` | Sealed in the DB when shared: OK. |
| Upload allowance | `process.py:90-117` | DB-backed when a store is given (always on the server): OK. |
| `roles` caches | `app.state._leaf_routes/_needs` | Per process; the cache is keyed by full path including invoice ids, and the whole dict is dropped at 4,096 entries. Minor. |

---

## 3. Scale

### The slowest pages (measured on this 4-core box, TestClient, warm unless marked)
- **GET /review after any vendor-master change: a full re-validation of the queue inside a GET. Verified (v8, v9).**
  `review_page` → `bulk.issues_today` (`review.py:398`, `coding/bulk.py:109-160`) re-runs `finalise_coding` for every
  invoice in review whose stored checks carry an old `checks_stamp`, then **writes each one back** (`save_recheck`).
  The stamp includes `masters_version`, which is bumped by any `vendor_added` / `vendor_updated` / `pos_imported` /
  `po_status` event (`accounts.py:68-80, 105-106`).
  Measured: one **"Add new vendor" click on a review screen**, or **saving a vendor's notes** on /vendors, makes the next
  /review take **4.6 s and 650 writes** at a 650-invoice queue (warm: 0.06 s). Cold at 1,000 in review it is 20.6 s
  (v7); `scripts/ci/perf_timing.py --invoices 3000` gives **/review 41.6 s**. It costs about 11 ms per invoice, all CPU.
  Approve, park, note and bulk don't trigger it (v9). The JDE nightly sync of suppliers and POs would also bump it.
  Several first viewers on different workers all do the same work at once (no single-flight).
- **/review/{id}**: about 0.7 to 0.9 s cold (imports), 23 to 50 ms warm with a small queue; 86 statements on one connection.
  `navigation()` (`invoice.py:274-301`) loads the **whole review queue**, and `attention()` for show=attention|ready,
  on every invoice view **and on every action** (`next_url` 304-322 in approve, park, reject, manager).
  `reading_status` is computed twice: `invoice.py:524`, then again in `render` (`core.py:587`).
- **POST /review/{id}/check**: 20 to 160 ms, fired 450 ms after typing stops (`invoice.js:163-169`). Each one is a full
  `analyse` (finalise_coding, duplicates, vendor findings, PO match, JDE view) plus an UPDATE (`refresh_confidence`,
  only if changed) and a draft UPSERT when edited; 21 KB of JSON and re-rendered HTML sections back.
- **GET /process**: hashes **every file in the invoices folder**. Verified (v3): 300 processed files gives 300 whole-file
  reads (`folder_files` → `not_processed` → `store.find_by_hash`, which reads the file and SHA-256s it;
  `process.py:661-676`, `core/store/invoices.py:122-131`). Processed files **stay** in `invoices/` (`source_path`). At
  25k invoices a month × about 0.5 MB, every Import page view reads roughly 12 GB after a month. `POST /process/folder`
  does it twice (`not_processed` + `split_new`, 938-941).
- **save_upload** (`process.py:158-168`): to choose a name, it reads every earlier file with the same stem (`invoice.pdf`,
  `invoice_1.pdf`...) whole. Verified (v3): the 40th "invoice.pdf" reads 39 files. This is O(n²) over time for common
  attachment names.
- **/exports** (`exports.py:237-342`): every ready invoice (no paging); JDE validation of all of them when the format
  is JDE (`jde_check`, with `prior_records`); every batch ever made (the template loops over `batches`); layout preview.
- **/vendors** (`vendors.py:204-297`): every vendor drawn, plus `recurring.detected` and `vendor_workload` on each view;
  `save_vendor` reads **all** vendor summaries to find one (315). Same pattern: `purchase_orders._known` (296).
- **/learning?tab=suppliers** (`learning.py:358-415`): N+1, with `supplier_stats` + `supplier_outcomes(limit=500)` per
  supplier profile.
- **/activity** (`activity.py:144-245`): reads the newest 5,000 events (`EVENTS_READ`, 51) and JSON-decodes them, plus
  `approved_as_coded` and `controls.build` per view. At 400 users, 5,000 events is a few hours of work (see the bug in §7).
- **/spend**: reads every approved line of the last 12 months into Python per view (`spend.parts`).
  **/spend/data.xlsx**: "every invoice" (`erp/spend.py:196`) built in memory with openpyxl. Read-only can trigger it.
- **/search export.xlsx/csv**: up to 50,000 rows built in memory (`search.py:140-172`).
- **Bulk approval**: the queue page draws every candidate and posts them all (`review.html:53-70`); the POST
  re-validates each one in one request (`review.py:527-568`).

### Rendering and paging
- The queue draws 50 rows (`QUEUE_PAGE`), but `?limit=` can go up to 5,000 (`review.py:392`), and the page **reads and
  checks the whole queue every time** (`list_invoices(statuses=(REVIEW, PARKED, PENDING))`, `issues_today`, `attention`,
  `sort_for_bulk`, `today_pills` with `recurring.detected`).
- The directory is SQL-paged (OFFSET), fine. Exports, vendors, POs, batches, the bulk dialog and the activity people
  list are not paged.
- Jinja `auto_reload` is left at True: a stat per template load. There is no gzip: the queue is 75-162 KB of HTML, the
  invoice 86 KB, a check 21 KB of JSON.

### Polling the front-end does
| Endpoint | Interval | Payload | Server cost |
|---|---|---|---|
| `/process/status` or `/jobs/{name}` (`pollJob`, `app.js:109-124`) | **0.8 s**, while a job runs; anyone who opens /process while *any* import runs polls (the job is global) | Up to 500 log lines: **57 KB** each (verified, v5) | `snapshot()` reads the job row from the DB with several workers, plus `jobs_waiting`. |
| `/review/{id}/reader` (`invoice.js:1278-1297`) | 20 s (`PAGE_READER_POLL_SECONDS`) while OvisOCR2 has it queued or reading | 0.3 KB | `reading_status` (many DB reads, maybe HTTP to the model server) + `reader_state` + a render. |
| `/settings/reading/self-test` | `TEST_POLL_SECONDS` while a test runs | small | DB read. |
- None of them pauses when the tab is hidden; there is no backoff and no ETag.
- While a job runs with several workers, `Job._renew` **rewrites the whole snapshot to SQLite every second**. Verified
  (v5): 4 writes and 229 KB in 5 s.

### A slow model server (the Mac Studio over the network)
- `local_llm.check_server` (`ai/local_llm.py:314-343`): the timeout is 1.5 s per HTTP call (`DETECT_TIMEOUT`, which
  assumes "a server on this computer"), with 2 calls per check (models, then the LM Studio listing). The cache lasts 30 s
  when up and 10 s when down, **per process, with no single-flight**: when it expires, every concurrent request in that
  worker that asks fetches it again.
- Callers in the request path: `render` on review and process (`core.py:587`), `invoice_page`, the `/reader` poll,
  `accuracy_note` (the queue's KPIs), `lm_studio_step` (process and welcome), `settings_reading`. With the model server
  down or slow, a share of page loads stall 1.5 to 3 s every 10 s in each worker.
- Handlers that wait synchronously on remote systems: `load_model` (minutes), the JDE `test_api` (30 s per request),
  `check_again`.

### What multi-worker needs (it exists, D-111, so this is what remains)
1. Take CPU work out of web workers. The **import pipeline, OCR included, runs as a thread in the web worker** that got
   the upload (`process.py:571-623`, started via `jobs.start`), and **only one import runs server-wide**: job "process"
   is claimed in the DB (`jobs.py:91`) and the others queue in memory. At 1,250 invoices a day from about 300 clerks,
   that is one serial pipeline in one web process.
2. Remove the per-request read of all drafts; share or bound the caches; single-flight the model status (the background
   process probes, and workers read the result from the DB).
3. Raise the default above `min(cpu, 8)` for the 32-vCPU VM; that is configurable already (`server.json`).
4. SQLite write paths per request: session `last_seen` (1/min/user), the draft UPSERT and `refresh_confidence` on
   checks, the job renew at 1 Hz, `save_recheck` bursts inside GETs, flash and ephemeral writes, events. The busy timeout
   is 30 s (`_base.py:937`).

### Rate limits
- Only two: login throttling (per username and per address, in the store) and the upload allowance (120 uploads or
  2 GB per hour per account, `process.py:85-117`).
- No other limit. Verified (v4): 20 back-to-back `/search/export.xlsx` from one Clerk all got 200.
- No per-user concurrency cap on page PNGs at scale 3 (up to 6,000 px), `/words` (OCR on scans), the spend workbook,
  ZIPs, the duplicate audit or the controls report.

### File uploads (memory and temp files)
- Starlette's multipart parser spools each file past 1 MB to the OS temp folder. `BODY_LIMIT` is 260 MB in server mode
  (`security.py:233`); the limit before login is 64 KB.
- `upload_refusal` runs **after** FastAPI has received and spooled the whole multipart body (`process.py:866-874`):
  a 250 MB upload over the hourly allowance is received in full, then refused. Read.
- Each file is then read whole into memory, up to 50 MB (100 MB for emails), one at a time (`_read_upload` 878-883), and
  written to `invoices/`.
- Spreadsheets: up to 50 MB parsed with pandas, then the **whole file is stored as a blob in SQLite** (`files.keep_upload`)
  and re-parsed on every GET of the import page (`read_upload` in GET handlers). `learning.keep_upload` (179) stores
  JSON of every row instead, a different format.
- `posted_form` allows 200,000 fields (`core.py:200`) for any POST from someone logged in.

### Serving PDFs and images
- Pages are drawn on demand by pymupdf or PIL (`document.py:181-246`) with a 96 MB per-process LRU. Measured cold
  (v2): scale 1 took 651 ms (first, with imports), scale 2 105 ms, scale 3 222 ms; warm in the same worker, 6 ms.
- The route sets `private, max-age=86400` with a `?v=` version (`invoice.py:709`), but **server mode forces
  `no-store` on everything that needs a login** (`identity.py:237-247, 361`). Verified (v4): the PNG gets
  `cache-control: no-store`. So every open of an invoice downloads its page images again and, with N workers, often
  draws them again. This is on purpose (D-109: page images not kept by the browser).
- `approved.pdf` and the batch ZIPs are built in memory per request; `inbox_file`, `reply_file` and backups use
  `FileResponse`, which streams.

### Caching headers for static files
- `/static`: `Cache-Control: no-cache` (`app.py:62-71`), so every use is revalidated with a 304. Verified (v4). Each page
  view sends a conditional request per script and stylesheet through the whole middleware stack, with an AccessLog line
  for each. `static_version` is in the template URLs, but `app.js` and `invoice_viewer.js` are imported without it
  (the docstring says why).
- `/fonts` and `/assets`: StaticFiles defaults (ETag and Last-Modified, no Cache-Control).

---

## 4. Efficiency hotspots (with evidence)
1. **The queue re-check inside a GET** after any vendor or PO change: 11 ms per queued invoice plus a write each
   (v8, v9; `bulk.py:123-160`, `accounts.py:68-106`). Over-broad: vendor *notes* bump `masters_version`.
2. **GET /process hashes every invoice file** (v3; `process.py:661-676`).
3. **Blocking work on the event loop** in async handlers (v2). One import or JDE batch freezes the worker.
4. **The import pipeline runs in a web worker, one at a time server-wide** (`process.py:571-623, 829-862`; `jobs.py:91`).
5. **All drafts read per request** with several workers (v11; `invoice.py:101-128`).
6. **No connection reuse**: every request opens 1 to 2 SQLite connections, with `PRAGMA journal_mode=WAL` each time
   (v1, v4; `_base.py:934-946`; `identity.py:323`). Async handlers open one per store call.
7. **Job polling**: 57 KB every 0.8 s per watching tab, plus a 1 Hz write of the whole snapshot (v5).
8. **Model status without single-flight**, with a 1.5 s timeout ×2 on the request path (`local_llm.py:314-343`).
9. **`navigation()` loads the whole queue** on every invoice view and action (`invoice.py:274-322`).
10. **No browser caching of page PNGs in server mode**, and a per-worker render cache (v4).
11. **Unpaged lists**: exports (ready list and batches), vendors, POs, bulk candidates; Activity reads 5,000 events;
    /spend reads 12 months of lines.
12. Per-request micro-costs: `need_for` walks the routes linearly (`roles.py:219`); `EnvSettings._stamp` does a
    `frozenset(os.environ)` and a stat per call (`app.py:162-170`); Jinja auto_reload stats; the AccessLog write on the
    event loop; `render` re-counts the rail (status counts, ready-to-export, approved today, demo count).
13. O(n) lookups to find one row: `vendors.py:315`, `purchase_orders.py:296`.
14. `save_upload` reads same-name files whole (v3).

For context: D-111's load test reached 4.3 requests/s with 4 workers at 100 people (`docs/SERVER.md:50-60`), about
1 request/s per worker. 400 people clicking every 10 s is about 40 requests/s: roughly 10 times today's throughput per
core, or 40 workers.

---

## 5. Cleanliness
- **Long handlers and helpers**:
  - `invoice._approve`: 99 lines (`invoice.py:836-934`). It checks the "why" questions, approves, learns, explains
    lessons, writes 4 kinds of flash and counts the queue. The learning and messages belong in `capture/workflow` or
    `coding`.
  - `exports.exports_page`: 104 lines (237-342).
  - `vendors.vendors_page`: 94 (204-297).
  - `review.review_page`: 79 (379-457).
  - `month_end_page`: 78 (`month_end.py:52`).
  - `purchase_orders.po_detail`: 77 (91).
  - `spend_page`: 71.
  - `purchase_orders_page`: 66.
  - `invoice_page`: 63 (519-581).
  - `review.attention`: 61; `activity_page`: 58.
  - `learning.coding_context`: 92 (261); `suppliers_context`: 55.
  - `invoice_view.analyse`: 120 (1725-1844).
  - `app.create_app`: 167.
- **Business logic in route modules**:
  - `process.process_files` (the import job itself, `process.py:571-623`) and `split_new`/`not_processed`. The queue
    retry imports it from the page module (`review.py:629`).
  - `review.attention`/`_to_fix` (queue readiness rules, `review.py:132-225`): they duplicate what `bulk.py` and
    `invoice_view.analyse` decide; the comment says "the queue asks the same question".
  - `review.delete_invoice`, which moves files (`review.py:582-599`).
  - `invoice.add_vendor`, which saves JDE AN8 overrides (614-649).
  - `exports.jde_left_out`/`jde_file`/`jde_check` (105-152).
  - `settings._approval_part` etc.: validation of settings (323-375).
  - `upload_refusal` (`process.py:94-117`).
  - `vendors.vendor_detail` and `ask_vendor`.
- **Duplication and inconsistent patterns**:
  - Two `changed_meanwhile` with different signatures (`invoice.py:329`, `review.py:515`).
  - Two upload-waiting stores: `files.keep_upload` (bytes) and `learning.keep_upload` (JSON rows) (`learning.py:179-195`).
  - The upload, column-pick and import flow is repeated in accounts (gl, cc, bank), vendors master, POs, ERP register,
    statements and learning history, each a slightly different async handler.
  - Refusals mix `HTTPException(409/404)` raised from POST handlers (`_in_review`, `_reference`; `invoice.py:246-251,
    491-495`) with `refuse()` JSON.
  - Role checks: the literal `"admin"` (`invoice.py:951`) vs `access.ADMIN` vs `users.ADMIN`; Manager checks are done
    by hand per route (`manager.py:40-60`) and also in `RULES`.
  - Async vs sync is chosen per handler with no rule. Some read the form with `await` and then do sync work (blocking),
    some hand off with `run_in_threadpool`, some use `with store.session()` on the loop (`exports.py:369`), some take
    `Form()` parameters sync.
  - Explicit `@one_connection` on handlers that `page_router` wraps already (`invoice.py:518`, `review.py:378`,
    `exports.py:238/412/436/458/469`). Harmless, but confusing.
  - `stale_page` is skipped when no `version` is posted (`core.py:466-472`), so it is only a UI guard.
- **GETs that write**:
  - `/review` (`save_recheck` bursts); `take_flash` deletes.
  - `jde_shown` sets a setting (`core.py:502-503`).
  - `bank_version` creates a secret on the first view (`accounts.py:255-257`).
  - `session_user` updates `last_seen`.

---

## 6. Security under many users
| Area | Finding | Status |
|---|---|---|
| CSRF | A POST needs `X-AP-Coder: 1`, plus Origin and Sec-Fetch-Site checks (`security.py:120-129, 176-181`); the session cookie is `__Host-`, HttpOnly, SameSite=Strict; JDE ZIP downloads are POST only (D-135). | Verified (v4): no header gives 403; a cross-site Origin gives 403. Good. |
| Default deny | `need_for` returns ADMIN for any route without a rule (`roles.py:208-229`); `tests/web/test_roles.py` enforces a rule per route. Mounts are open only for /static, /fonts, /assets. | Read. Good. |
| IDOR | There is no data partitioning among AP roles: Read-only, Clerk and Approver see every invoice, by design. A Manager is limited to its own requests (`manager.py:49-55`), and `RANK[MANAGER]=0` keeps it off READ routes. Drafts are keyed per account; presets per owner. `/process/status` shows any clerk the global import's log lines (file and vendor names): acceptable for AP. | Read. Nothing found beyond the design. |
| Idle timeout defeated by polls | Any request refreshes `last_seen`, including the automatic `/review/{id}/reader` poll (20 s) and job polls (0.8 s). | **Verified (v10)**: a tab left on an invoice waiting for OvisOCR2 stayed logged in for 2 hours of no input (idle limit 30 min) and still opened /review. With 400 users this is the usual case; the 12 h cap still applies. |
| Session expiry mid-form | The 401 JSON on a POST carries `redirect: "/login"` without `next` (`identity.py:330`). `invoice.js` `check()` only toasts errors and doesn't follow; edits made after expiry are not kept as drafts; Approve then sends the person to /login, then to /review, not back to the invoice. | Read (JS and Python). Suspected lost edits. |
| Session handling at scale | Tokens are stored hashed; a new token after TOTP; end other sessions; logout clears every duplicate cookie. Each request costs a SELECT on its own connection, plus a write at most once a minute per session. scrypt at n=2^14 is about 16 MB and 50-100 ms per login try, in the threadpool: 400 morning logins are fine. | Read. |
| Rate limits and DoS by logged-in users | Only login and upload are limited. A Read-only user can repeatedly request 50k-row XLSX exports, `/spend/data.xlsx` (every invoice), scale-3 page renders and `/words` OCR. | Verified (v4) for exports; the rest read. |
| Trusting the client | Approve rebuilds and re-checks the coding server-side (`analyse`), the status is guarded by `UPDATE ... WHERE status=REVIEW` (`invoices.py:688-713`), final approval posts the approval id it was shown (B-654), batches re-check that invoices are approved and unexported. Things taken from the form: `next_id` (redirect only), `override_reason`, `taught` boxes (sanitised), and the optional `version` stamp (skippable, so only a UI guard). `upload_refusal` uses `UploadFile.size`, measured by the server. | Read. OK. |
| Proxy and headers | X-Forwarded-For is believed only from `--proxy` (`server.py:136-146`); a loopback forward from an unnamed proxy is refused (`security.py:102-117, 167-174`). The Host allow-list is `AP_ALLOWED_HOSTS`. CSP has no inline scripts. HSTS 1 year. | Read. Good. |
| Network (laptop-shared) mode | `/who` names are self-asserted, with no password. Anyone on the LAN can approve under any name. The CLI warns about this (`cli.py:382-385`). | Read. The target must use server mode only. |
| Errors | The traceback goes to the server's log; users see a summary with folders stripped (`errors.py:264-283`). | Read. OK. |

---

## 7. Possible bugs
**Verified**
1. **The Activity CSV is silently capped at 5,000 events.** Its docstring says "all of them" (`activity.py:239-245`,
   `EVENTS_READ=5000`). With 5,100 events in the DB the CSV has 5,000 rows (v6). The page's filters (person, search) and
   the People list also only see the newest 5,000. At 400 users that is a few hours: an audit export is incomplete.
2. **Polls keep idle sessions alive** past the 30-minute idle limit (v10). See §6.
3. **GET /review writes hundreds of rows and stalls for seconds** after a vendor *notes* save, an "Add new vendor" from
   the review screen, or a vendor hold change (v8, v9). Notes can't change any check, so the invalidation is too broad
   (`MASTERS_CHANGES` includes `vendor_updated`).
4. **Blocking DB and analysis work on the event loop** in async handlers (v2).
5. **GET /process reads every invoice file** (v3); **`save_upload` reads same-name files** (v3).
6. **Server mode disables browser caching of page PNGs** (v4), despite the route's own `max-age` (intended, D-109,
   but expensive).
7. **The job poll is 57 KB every 0.8 s, and the renew rewrites the whole snapshot every second** (v5).
8. **Each /check reads every draft in the DB with several workers** (v11).

**Suspected or read**
9. The upload allowance is checked only after the whole body is received (`process.py:866-874`).
10. Edits are lost when the session expires mid-form (§6).
11. `invoice_page` computes `reading_status` twice (`invoice.py:524`, `core.py:587`).
12. `_TO_FIX`/`_PAY_FROM` grow without bound per worker (`review.py:128-129`).
13. "Check again" and `forget_setup_report` clear only one worker's cache; others show the old LM Studio status for up
    to 30 s, so a clicked "Check again" can look like it did nothing on the next page, served by another worker.
14. A restart during an import: the in-memory `ImportQueue` is lost (`process.py:734-738` says so); the files sit in
    `invoices/` until someone presses "Process the folder", which then hashes the whole folder.
15. Approve while the page reader applies a new reading: `_approve` uses `inv` read at the start for learning
    (`_line_feedback` with the old `ai_output`), while the page worker may update `ai_output` before the guarded UPDATE.
    The status guard holds; what is learned may compare against a stale AI output. Needs a test with a thread.
16. Bulk approval skips any invoice with **anyone's** draft (`review.py:548-553`). Drafts never expire, only end with
    approve, reject, delete, reopen or start over, so drafts left by people who walked away block bulk approval of
    those invoices indefinitely.
17. `/fonts/<missing>` in server mode renders the full error page and opens 4 connections (v4). Trivial.

---

## 8. Test gaps (`tests/web/`, 51 files)
- No test that **async handlers do no blocking store or file work on the loop**. A meta test could run every POST
  route with a loop-detector like v2.
- No **performance budget** in CI. `scripts/ci/perf_timing.py` is a stopwatch, not a gate (and it shows /review at 41 s
  for 3,000 invoices). The **load test behind D-111 is not in the repo**.
- Nothing covers **/review after a masters change** (re-check cost and writes in a GET).
- Nothing covers **GET /process with many processed files** (hash count) or `save_upload` with common names.
- Nothing covers **Activity CSV completeness** beyond `EVENTS_READ`.
- No test that **automatic polls don't extend the idle timeout**. `test_server_hardening.py:716` tests only the config.
- No **real-concurrency** tests: two threads approving the same invoice; approve vs the page-reader apply; two workers
  re-checking the queue at once; SQLite `database is locked` behaviour with 8+ writer processes.
  `test_server_scale.py` covers shared jobs, drafts and passwords through in-process apps with `processes>1`, and one
  real start and stop.
- No test of **session expiry in the middle of the review form** (JS follows the 401?).
- No test of the **model server down or slow** on page latency (the fake LM Studio exists: `scripts/ci/fake_lm_studio.py`).
- No test of **job snapshot size** or poll payload limits.
- No test that the **thread pool and limits** are configured.

---

## 9. Scenarios people may not have thought of
1. **Two people on the same invoice.** There is no presence or lock. Both build drafts; the first approval wins and the
   second gets "changed meanwhile" with their work lost. With one shared queue sorted by priority and 300 clerks, "Next"
   sends many people to the same invoices. Needs soft locks or assignment ("Clara is on this one") and a "next" that
   skips invoices someone opened in the last few minutes.
2. **Approving while someone re-processes.** Retry is FAILED-only (good). `read-pages` and the AI reviewer re-read in the
   background while someone edits; the race is in §7.15. "Add new vendor" by one clerk re-checks the whole queue for the
   next viewer (§7.3).
3. **Session expiry mid-form**: §6. **The reverse**: polling keeps unattended screens logged in (v10).
4. **400 people polling at once**: 40 open import tabs during a long import mean 50 requests/s of 57 KB `/process/status`
   (about 2.8 MB/s) plus a 1 Hz DB write. 400 invoice tabs waiting for OvisOCR2 mean 20 requests/s of `/reader`, each
   doing `reading_status`. If OvisOCR2 has a backlog, which is likely at 1,250 a day, many screens are in "waiting".
5. **A slow model server**: the 1.5 s × 2 status probe on page paths every 10 to 30 s per worker, with no single-flight;
   GL suggestions and the verifier in the import job (inside a web worker) wait on the Mac Studio, holding the only
   import slot. Failover to the Mac mini is not handled in the web layer (one `base_url`).
6. **A restart during an upload**: Starlette's temp spool files vanish (fine). Files already saved stay in `invoices/`;
   the in-memory queue of waiting imports is lost; a running import's job row goes stale after 30 s, with the invoices
   done so far kept. Nobody is told that the queued files were never read.
7. **The morning rush**: 400 logins (scrypt, fine) plus the first /review of the day after the nightly JDE sync bumped
   `masters_version`, so the first viewer in each worker re-checks the whole queue (20-40 s each, in parallel, writing).
8. **A restore on one worker** while others serve (`settings.py:520-555`): the store swaps the DB, every session ends;
   other workers' in-memory caches (PNG LRU by path and mtime, `_TO_FIX` by hash) are fine, but requests in flight
   during the swap may fail. Needs a test.
9. **An Admin uploads a year of AP history (50 MB)** on `/learning/history/upload`: the worker's event loop is blocked
   for tens of seconds, and the row JSON is stored as one SQLite blob.
10. **Month-end**: 20 people open /exports, /month-end, /sales-tax and the 50k-row exports together. Every one is
    computed live and unpaged; nothing is cached or rate limited.
11. **A Windows VM**: there is no uvloop; TLS is in Python per worker unless IIS terminates it; spawned workers each
    re-import pandas, pymupdf and OCR libraries (memory: SERVER.md measured 2 to 5 GB for 4 workers).

---

## 10. Recommended goal items (web backend)
| # | Item | Benefit | Size | How to prove it |
|---|---|---|---|---|
| 1 | **Nothing blocking on the event loop**: convert the 60-odd async POST handlers to sync, or hand off with `run_in_threadpool` after reading the form; add a meta test that runs each POST route with a loop-detector (v2) and fails on any store call made on the loop. | One import, export or JDE batch no longer freezes every request in its worker. | M | Meta test green. In a load run, p95 of other pages unchanged while an Admin imports a 50 MB sheet. |
| 2 | **Queue re-checks out of GET /review**: bump the checks stamp only for changes that matter (not vendor notes); re-check stale invoices in the background process, single-flight via a DB lease; GET reads stored results only. | /review stays at about 100 ms after master-data edits; no write bursts in GETs. | M | Test: after "Add new vendor" and a vendor-notes save at 5,000 queued, /review ≤ 300 ms and 0 `save_recheck` calls in the GET. perf_timing /review ≤ 0.5 s at 3,000. |
| 3 | **Stop hashing the invoices folder**: track processed files by (name, size, mtime) or move processed files into dated subfolders or a hash-named store; `save_upload` names by hash or uuid. | GET /process and folder pick-up stay O(new files). | S/M | Test: 30,000 files in the folder → GET /process reads 0 files and takes < 200 ms. |
| 4 | **Imports off the web workers and in parallel**: a persistent import queue table; a pool of reader processes (background); per-user progress; the queue survives a restart. | 1,250 invoices a day without one serial pipeline in a web process; web latency independent of OCR. | L | Throughput test: 200 files uploaded by 5 users at once are processed in parallel; web p95 unchanged during it; restart mid-queue resumes. |
| 5 | **Cheaper polling and true idle timeout**: incremental job snapshots (`?since=`, ≤ 2 KB), backoff and pause when the tab is hidden, renew writes only the progress; poll endpoints don't refresh `last_seen`. | Removes 50+ requests/s of 57 KB answers and a 1 Hz DB write; idle sessions end at 30 min as D-109 intends. | S | Test like v10 shows logout at 30 min with the poll running; v5 payload < 2 KB; writes during a job ≤ 0.2/s. |
| 6 | **Per-request overhead**: reuse SQLite connections per thread (a small pool, WAL pragma once); the session lookup shares the handler's connection; cache `need_for` by route; gzip HTML and JSON; Jinja `auto_reload=False` in production. | Fewer file opens and fsync-risky closes; faster small requests. | M | v1/v4: 0 new connections per request in steady state; p50 of small pages −30% in the load test. |
| 7 | **Drafts with several workers**: read only the asker's drafts, and draft ids for bulk; expire abandoned drafts (e.g. 7 days) or let bulk ignore drafts older than N hours. | /check flat as drafts grow; bulk not blocked by walk-aways. | S | v11 at 5,000 drafts: /check within 10% of the 0-draft time. |
| 8 | **Model-server status off the request path**: the background process probes the Mac Studio (and the Mac mini as backup) and writes the status to the DB; workers read it; network-sized timeouts; failover. | No 1.5-3 s stalls when the model server is slow or down; one place to fail over. | M | Load run with the fake LM Studio blackholed: page p95 unchanged; failover test switches to the backup. |
| 9 | **Paging and limits**: the exports ready list and batches, vendors, POs, bulk candidates (a cap, chunked approval); Activity filtered in SQL with a streaming CSV of all matching events (fixes §7.1); heavy downloads (spend workbook, 50k exports, ZIPs) as jobs written to temp files and streamed. | Bounded page sizes and memory; complete audit exports. | M | Activity CSV rows = DB rows for 100k events; page HTML ≤ 300 KB at 10× data; memory flat during exports. |
| 10 | **Rate and concurrency limits**: per-user token bucket in the DB for exports, renders at scale ≥ 2, `/words`, reports; a sized thread pool; uvicorn `limit_concurrency` per worker; `limit_max_requests` for recycling. | One user can't starve 399 others. | S/M | Test: the 6th concurrent export from one user gets 429; another user's p95 is unchanged. |
| 11 | **Page images**: pre-render page 1-2 PNGs at import into a shared disk cache keyed by file sha and scale; serve with ETag and 304 (and, if security agrees, `private, max-age` with the versioned URL in server mode). | Invoice opens drop the 100-650 ms render and repeated downloads; no 8 × 96 MB caches. | M | Opening an invoice twice: the second time 304s or a cache hit in any worker; CPU per invoice view −50%. |
| 12 | **A load test in the repo** (an httpx/asyncio or locust script plus the D-111 mix) and perf gates in CI (perf_timing with budgets). Target for the VM: 400 users at one click per 10 s (40 requests/s) with p50 < 300 ms, p95 < 1.5 s, 0 errors, on 32 vCPU with 16+ workers and the background pool. | Makes "serves 300-400 people" measurable and keeps it true. | M | The script and its report; the CI job fails when a budget is exceeded. |
| 13 | **Soft locks and presence on the review screen**: "X is editing this" from the last /check per person; "Next" skips invoices opened by others in the last 5 minutes. | Fewer collisions and less lost work with hundreds of clerks on one queue. | M | A two-client test sees the banner; the load-test collision rate (409 "changed meanwhile") drops. |
| 14 | **Clean-up for the above**: move `_approve`'s learning and messages into `capture/workflow`, `process_files` into `ai`/`intake`, queue readiness into `coding/bulk`; one upload-sheet flow (drop the second `keep_upload`); one `changed_meanwhile`; role constants; replace the O(n) lookups (`vendors.py:315`, `purchase_orders.py:296`). | Smaller handlers, logic testable outside HTTP. | S/M | `scripts/same_behaviour.py` identical; tests green; the handlers cited drop under 40 lines. |
| 15 | **Worker sizing and SQLite contention** on the VM: set workers to about 16-24 once OCR is out; measure `database is locked` and write latency with 16 writers; cut per-request writes (session `last_seen` batching, draft UPSERT only when changed). Decide on SQLite vs Postgres from that data. | Knows whether SQLite holds at 400 users before the pilot grows. | M | A contention test of 16 processes × mixed writes: p99 write < 100 ms, 0 lock errors. |
