# AP Coder: what the code is today, and what stands between it and 400 people

This digest comes from seven readers, each covering one part of the codebase. It was read on `main` at af9925b on 5 Nov 2026,
and the lead re-checked its top claims in the code. The detail, with file:line references, is in the seven notes
beside this file: core, web_backend, web_frontend, ai_capture, coding_erp, ops_scripts and tests_bench.

## The size of it
- **App code:** about 82,000 lines.
  - `web` 17.6k, `core` 12.4k, `capture` 12.0k, `ai` 9.5k, `erp` 9.5k, `bench` 6.9k, `ops` 6.5k, `coding` 6.2k,
    `intake` 1.9k.
  - **Front-end:** 48 templates, 13 JS modules (6.2k lines; `invoice.js` 2.6k, `invoice_viewer.js` 1.7k), 8 CSS files.
    There is no build step.
- **Tests:** 270 files, 73k lines, 4,918 tests; 4,256 run in the default tier.
  - CI: the full suite is about 7 min on Ubuntu, and the default tier about 11 min on Windows.
  - Each CI run has 29 jobs and takes about 25–30 min.
- **Scripts:** 7.9k lines.
- **Docs:** 7k lines across 22 guides, plus `BUGS.md` and `decisions.md` (D-147 is the latest).

## What is already sound (keep it)
- **Server mode exists and was reviewed twice for security** (D-97 to D-111, D-146):
  - accounts with TOTP, sessions, four roles with a default-deny route table (a test walks every route × role);
  - CSRF protection, HTTPS;
  - several web workers plus one background process. Jobs, uploads, flash messages and sessions live in the
    database, not in memory.
- **Money paths:**
  - posting state machine with compare-and-set and lookup-before-resend (no double post);
  - export batches under `BEGIN IMMEDIATE`;
  - credit notes are consistent;
  - approval is compare-and-set on status.
- **Front-end safety:** no XSS found, CSRF via a custom header plus same-origin checks, and the server enforces every
  role (hidden buttons are not the protection).
- **Reading:** French and Quebec invoices read well, and the rule reader is fast (0.03 s).
- **Tests:** strong on tax, checks, capture, roles, security, offline mode and JDE AIS failures. N+1 guards exist.

## The scale targets
- About 25,000 invoices a month: 1,250 a working day, about 300,000 a year.
- 300–400 people at once. At one click every 10–20 s, that is 20–40 requests a second, plus polling.
- **Stage 2 hardware:**
  - a Windows VM with 32 vCPU and 64 GB in IT's data centre runs AP Coder;
  - a Mac Studio runs the models, reached over the network;
  - the Mac mini is the backup.
- **The pilot:** the Mac mini runs both AP Coder and the models (macOS).

## The measured gap
- **One web process serves about 1.6 pages a second** (D-111, the GIL). Workers are capped at 8
  (`web/server.py:223`), which is about 13 requests a second against the 20–40 needed. **Verified.**
- **The only load test** (100 people, 4 cores, 4 workers) gave p50 6.9 s and p95 22 s.
  - Its harness was never committed, and it ran before the latest database speed-ups, so the figures are stale.
  - No test anywhere has more than about 120 invoices.

## Findings by area

### 1. Database (core/)
- **Verdict:** SQLite holds for one VM at this volume if the items below are fixed.
  - **Size after a year:** about 12–20 GB of database. The biggest table is `reader_outcomes`, about 15M rows.
  - **When PostgreSQL is worth it:** for high availability, a second app host or point-in-time recovery, not for
    the volume.
  - **A move to PostgreSQL:** about 95% of the SQL is already in `core/store`. A seam is about 2–4 days; a full
    port is about 2–3 weeks.
- **Bugs (reproduced):**
  - Two managers answering one request at once are both logged (150/150). There is no rowcount check after the
    guarded UPDATE (`managers.py:174`). **Verified.**
  - Every request's `session_user` writes `last_seen` and waits up to 30 s behind any writer (`users.py:912`).
  - Store transactions don't nest: a nested `BEGIN IMMEDIATE` fails at 28 call sites.
  - Building a `Store()` inside an open transaction commits it (latent).
  - **The Mac Studio can't be named:** a company DNS name such as `macstudio.corp.x.com` is refused as a model server
    unless internet access is on. Only bare names, `.local`, `.lan`, `.home` or private IPs pass
    (`config.py:118`). **Verified.**
  - **Missing indexes:** `feedback(invoice_id)`, `invoices(export_batch)`, `events(action, created_at)`,
    `sessions(user_id)` and `ephemeral(expires_at)`. `find_vendor` scans the whole vendor table.
  - After a restore the WAL is as big as the database, so a restore needs about 3× the database free.
- **Cost (measured):**
  - Every process reloads the whole learning memory after every approval: about 1.7 s and 50–105 MB.
  - `vendor_ids` / `supplier_key_for`: about 55 ms each at 20k vendors.
  - One approval makes 25–30 commits.
  - Each request opens a fresh connection with a cold cache, and no PRAGMAs are tuned beyond WAL.
- **Cleanliness:**
  - `core` imports upward 54 times (from `coding`, `capture`, `ai`, `erp`, `ops`).
  - `core/ui.py` is HTML.
  - Names clash: `_forget_learning`, `ACCOUNT_TABLES`, `_tax_signature`.
  - The three work queues are near-copies; `waiting_manager_map` is dead.
  - `audit.describe` (198 lines) and `_migrate` (197) are the longest functions.

### 2. Web backend (web/)
- **GET `/review` re-checks and rewrites the whole queue** after a small master-data edit: saving vendor notes,
  "Add new vendor", or the nightly JDE sync bumping `masters_version`. That is 4.6 s and 650 writes at 650 queued,
  and 41 s at 3,000. The morning's first viewer in each worker pays it. **Verified** (the stamp is
  `accounts.py:74`).
- **GET `/process` reads and hashes every invoice file ever received**, about 12 GB per view after a month at volume.
  `save_upload` re-reads files with the same name.
- **Async handlers block the event loop:** fill-lines, apply-rules, `exports/create` and spreadsheet imports. While
  one runs, its worker answers nothing.
- **Imports run inside a web worker, one at a time for the whole server**, OCR included. Their queue is in memory and
  lost on restart.
- **Polling:**
  - job polls return 57 KB every 0.8 s, and the job row is rewritten every second;
  - polls keep idle sessions alive (2 h observed against the 30-min idle limit).
- **Activity:** its CSV is silently capped at 5,000 events, and the page searches only the newest 5,000 (a few hours
  at 400 people).
- **Drafts:** with several workers, `/check` reads every person's drafts. Drafts never expire, and bulk approve skips
  invoices with anyone's draft.
- **The model-server status probe** (1.5 s × 2) runs on page requests every 10–30 s per worker, with no single-flight.
- **No limits** on expensive endpoints: 20 back-to-back 50k-row exports all succeed, and the thread pool is not sized.
- **Page images:** each worker keeps its own 96 MB render cache, and server mode sends them `no-store` (D-109).

### 3. Front-end
- **Static files** are revalidated on every view (`no-cache`, because modules import without `?v=`): 6–11 extra
  requests per view, each one logged.
- **No compression.** An invoice page is about 100 KB, and the icon font is 373 KB.
- **Every invoice open runs the edit check a second time** (167–580 ms against a page of 20–98 ms) and writes to the
  database.
- **Big inline lists:**
  - the invoice HTML embeds the whole vendor list and the chart of accounts twice: about 0.8 MB per view at 3,000
    vendors;
  - Accounts → Coding rules prints the full chart per rule row, about 16 MB at 200 × 1,500.
- **Polling never pauses** on a hidden tab. `pollJob` retries every 800 ms forever, loops on the login page after
  expiry, and there are no fetch timeouts.
- **Unbounded lists:** Failed/rejected, Parked, Second approval, bulk candidates (approved in one synchronous
  request), Vendors, POs, Exports-ready and Users. The queue computes every pending invoice on each load.
- **No presence or claim:** everyone's Next goes to the same invoices.
  - A stale screen gets a toast on every edit and stays editable.
  - Two tabs silently overwrite each other's edits.
  - The data for "Ann has edits here" already exists (`invoice_drafts`).
- **Bugs (confirmed in code):**
  1. Queue sort, filter and search are lost when an invoice opens.
  2. The queue loads twice on the way back.
  3. On session expiry, approve goes to `/login` with no return address, and edits are lost.
  4. The network-error wording tells server users to "start it again with the shortcut".
  5. The draft clock is 24-hour.
- **Cleanliness:**
  - `InvoiceViewer.build` is 202 lines.
  - Streamlit-era leftovers remain in the viewer.
  - There are four "is typing" helpers, four ways to send a request and three pollers.
  - Repeated template blocks should be macros.

### 4. Reading and AI (ai/, capture/)
- **A scan costs 15.5–16.7 s; a digital PDF 0.3 s.**
  - PNG round-trips cost 6.6 of the 14.6 s profiled, more than the OCR (3.7 s).
  - The OCR cache is keyed by the freshly drawn image, so a warm re-read still costs 8.5 s.
  - A scan is read up to 5 times in its life.
- **Imports block on the chat model:** an import job makes chat calls on arrival (`pipeline.py:628`), each with a
  600 s timeout and no circuit breaker. A hung model stalls everyone's imports.
- **OCR runs on threads, never processes.** It stalls the worker's other threads by up to 0.9 s, and three threads are
  only 17% faster than one.
- **Queues:**
  - the page-reader and reviewer queues are in SQLite (FIFO);
  - the reviewer runs only when the reader is idle, so it starves under a backlog;
  - there is no heartbeat on reads in progress (after 2 h another reader takes the invoice);
  - the wait estimate ignores `llm_parallel`.
- **The model server:** one URL each for chat and page reading.
  - No failover to the Mac mini and no circuit breaker; a timeout never marks the server down.
  - The page reader's self-test is recorded per model name, not per server.
  - A swapped chat model is never qualified before it can approve held touchless invoices.
- **Caches:**
  - `.cache/page_reader`, `model_reading` and `model_accounts` are never pruned;
  - the OCR cache keeps 4,000 files, about 1.5 days at volume, so a backlog means OCR is redone.
- **Bugs:**
  - `supplier.parse_amount` reads "0.125" as 125 and drops the minus of "$-113.00" (reproduced; template learning
    only).
  - PyMuPDF is used from several threads without a lock (N-14).
- **The totals solver's worst case is 1.3 s.**

### 5. Tax checks and JD Edwards (coding/, erp/)
- **The nightly JDE sync reads everything:** 730 days of vouchers and every supplier, with no `changed_since`. It
  stops at 200 pages × 500 = 100,000 rows. **Verified** (`jde_ais.py:718`).
  - At volume, about 600k vouchers, it fails every night and the duplicate register goes stale.
  - The supplier sync is about 52 s in one write transaction, with no index on `vendors.erp_id`.
- **One supplier can switch the duplicate check off for everyone.** One big supplier (more than 40 pages of vouchers)
  makes the live check answer "JDE not reached" for every supplier for a minute. A real duplicate was missed
  (reproduced). Every check signs in and out of AIS afresh.
- **Posting stops at the first late voucher** and queues the rest for 5 minutes. With R04110ZA run per voucher, that
  is about 40 h for 500 vouchers, and probably one pending JDE batch per voucher.
- **The controls report** reads the newest 1M events and filters by date in Python, so older periods come out
  silently empty (reproduced, a sibling of B-1251).
- **Hot spots (measured):**
  - `held_vendor` 2.8 s with 3,000 held vendors (every review render and bulk approval);
  - `account_for` 17 s per 500-invoice batch with a 20k mapping;
  - `jde.load` 50 ms and `vendor_ids()` 160 ms per check;
  - duplicate audit 4.8 s for one vendor.
- **Tax math:** `round()` on floats gives 0.12 where half-up gives 0.13. The Tax check and the validation checks use
  different tolerances, and the two cent-splitters put the odd cent on different lines.
- **Fiscal periods are missing everywhere.** October invoices posted in November would be refused, and the FY2026 DV
  may refuse every G/L date from 1 Nov 2026 on.
- **Holds placed in JDE after approval are not re-checked** before posting. The default pay status `A` may override
  the supplier's hold (confirm with the JDE team).
- **Z-table keys (EDBT/EDTN) collide** across two installations or after a restore.
- **Voided vouchers** never leave the register.
- **Accruals** count exported-but-unposted API vouchers as already in the ERP.
- **One company per installation:** POs are keyed by number only, and multi-company is not supported.
- **The finance hub** re-reads and fingerprints everything every run.

### 6. Operations (ops/, intake/, scripts/, CI)
- **The Windows service** is a Task Scheduler task that runs at boot and every 5 min. It starts the host
  (`service run`), which keeps `serve --server` running with backoff. The host itself runs anywhere; only
  install/start/stop/status and `process_image` are Windows-only.
- **macOS needs new work:**
  - a LaunchDaemon (`UserName`, `KeepAlive`, restart on a crash only) and a SIGTERM handler;
  - a stable `current` symlink;
  - an LM Studio daemon;
  - a launchd backend for `server-update`: today it doesn't stop or start anything on a Mac.
  - With FileVault on, an unattended reboot stops at the unlock screen (a decision for IT).
- **Bugs (reproduced):**
  - **A stale `host.json` keeps the service down.** If it names a PID now used by another process, the host exits and
    the service stays down after a reboot (`service.py:625`). **Verified.**
  - **`server-update` trusts `server.json`'s task name,** which the service account can write: the repro stopped and
    started Windows' Defrag task.
  - **A scheduled `watch` and the Process button can store the same file twice.** There is no shared lock and no
    unique file hash.
  - **A power cut during an update** leaves the service stopped, with no record of the half-done update.
- **Intake cost grows with every invoice ever received:** each check reads and hashes the whole flat invoices folder
  2–3 times. The email triage log is read whole on every Import page view.
- **Backups:**
  - about 150 GB a year of files, plus a second copy of each in the backup store on the same disk (about 1 TB a year
    in total);
  - every update makes two full database copies that are never pruned;
  - no backup time window and no free-space check anywhere.
- **Update downtime** is about 10–25 min: the self-check runs twice, and the venv is built after the stop. It could be
  about 3 min.
- **Monitoring:**
  - `/healthz` only says "up";
  - nothing checks disk, certificate expiry, backup age, the background process or queue depth;
  - nothing writes to the Event Log or syslog;
  - there is no business time zone: "today" follows the server clock.
- **CI:** 29 jobs.
  - The offline bundle is built 12 times per run.
  - Only 3 of 13 jobs have time limits.
  - Missing: a test that really starts the task, an end-to-end `server-update`, a macOS service test and a load test.
- **Dependencies:** minimum versions only. `docs/tested-packages.txt` isn't used, so bundles aren't reproducible.
  - PyMuPDF is AGPL and needs a licence look.
  - A server-only bundle would be about 200 MB against 1 GB+.

### 7. Tests and benchmarks
- **Concurrency** is mostly simulated with monkeypatch and sleeps.
  - Server mode has one real 2-worker test, which only checks `/healthz`.
  - Nothing covers a slow, hung or networked model server.
  - Migrations use faked old schemas: real ones reach only schema 15, and columns were added at 19 without a version
    bump.
- **Existing tools:**
  - `scripts/ci/perf_timing.py`: `/review` 0.12 s, an invoice 0.05 s, bulk approve of 100 3.1 s, at 2,100 invoices;
  - `tests/offline_flow.py`, a seed for user flows;
  - three different fake LM Studios.
- **Test health:**
  - at least 15 tests over 0.5 s are missing from the slow list;
  - there are 44 sleeps;
  - no per-test timeout, and CI's Tests job has no limit.
- **Raw SQL and copies:** 81 test files write SQL directly, and `store_api.txt` pins 279 old Store names. Helpers are
  copied around: `_gt` 13×, the `store` fixture 36×, `PAGE` 21×, `demo_db` 11×. 64 files import from other test files.
- **Disk:** pytest keeps 3 runs of temp folders; 1,323 tests left 332 MB. That matters on this machine.
- **Doc disagreement on the default tier's time:** "a few minutes" in CLAUDE.md, "6 to 9 minutes" in HANDOFF.md,
  10.8 min on Windows.

## Loose ends from before
- **The Mac mini service** (above). **The real-model run on the Mac mini** needs the hardware; it's in stage 0's burn-in.
- **The kit's docs describe the old plan:** `HANDOFF.md`, `SERVER.md` and `PILOT_PLAN.md` still mention a Windows laptop
  or NVIDIA machine and "more districts". The hand-over guide tells the company's team to fix them.
- **Job cost coding** (job, cost code, cost type) is not built. KNOWN_LIMITS says stage 0 builds it.
- **PR #92** (docs fix plus release tags pilot10/handoff10) is open on the work branch.
- **A stray change, cause unknown:** an old copy of `next_time` (calling the deleted `_ratio`) appeared twice in
  `ap_coder/bench/coding.py` (01:12 and 01:26 UTC). None of the readers made it, and it was reverted both times. Watch
  for it; ruff and a default-tier test catch it.

## What this environment can and can't prove
- **Can:**
  - a 4-core Linux sandbox: per-request cost, throughput per core, and a load test against a real multi-worker server;
  - a fake model server with delays and failures;
  - the Windows and macOS CI runners, for the service backends.
- **Can't:** the 32-vCPU VM, a real Mac Studio or Mac mini, a real JDE or GPU speeds.
- **The rule:** the targets are set per core and proven here; the VM's figures are a projection, re-measured on the
  real server in stage 2.
- **Disk:** about 2 GB free, so test temp folders must be kept small.
