# Phase 2 part 2: where a web worker's CPU goes (2026-11-05)

Repo `/home/user/int`, the seeded 100k server folder (`bench_data/S/s100k`: 100,000 invoices, 20,000 vendors in the
master, 2,708 in review, 421k lessons, JD Edwards in use). The app is opened as one web worker of the server runs it
(`create_app(server=True, background=False, processes=4)` plus the `Recycle` middleware), signed in by the real login
(password, then the authenticator's code). CPU is process CPU (`time.process_time()`: every thread, the threadpool's
included; nothing else runs in the process: 0.06 ms of CPU in 3 idle seconds). The fixed costs were measured through
real uvicorn (h11, asyncio: no httptools or uvloop here), with the server process's own CPU from `/proc`. Scripts and
outputs: this folder (list at the end). **m** = measured, **e** = estimated.

## Verdict
- **At 100 people the load asks for 4.5 times the machine.**
  - With instant answers, the harness's people ask for 5,318 requests a minute (88.6/s, model of `bench/load.py`).
  - Answering them costs about **1,085 core-seconds a minute (18 cores)**; the machine has 4 (240 core-s), the web
    workers about 3.4 of them.
  - The run answered 18.3/s, a fifth of what was asked: the rest queued. Hence p50 23 s and p95 84 s.
- **Two routes are 82% of it**, and neither is slow because of the database's size alone:
  - `POST /review/{id}/check`: 410 a minute, **1.29 s** each under load (0.64 s alone). 49%.
  - `GET /review/{id}`: 200 a minute, **1.78 s** (1.13 s alone, 1.66 s the first time). 33%.
  - Most of it is work done again on every request that gives the same answer each time:
    - the vendor master turned into a dict of 20,000 vendors twice (`vendor_ids`);
    - the held-vendor check's SQL over the approved invoices of the vendors on hold, twice (`approved_numbers`);
    - the 20,000-vendor picker list rebuilt and put in every page as 3.3 MB of JSON (`vendor_options`);
    - the GL suggestions' index of 50,000 lessons, rebuilt whenever the worker's previous request was for another
      vendor, which under load is nearly always (`suggest._MEMORY` keeps two lists of rows).
- **Measured: the four memos plus the vendor list taken out cut the two routes 6-7 times.**
  - Check 1,295 -> 191 ms; invoice page 1,781 -> 252 ms (four vendors in turn, as under load).
  - With the queue's checks kept too, the whole demand falls to **261 core-s a minute (4.4 cores)**.
  - That is still 1.8 times what 4 cores give at 60% busy; the reports and the queue are next.
- **The fixed costs are not the problem.**
  - A request through uvicorn costs 0.35 ms (h11 and asyncio). A static file costs 1.0 ms (304) to 1.7 ms (200).
  - A signed-in request adds about 1 ms for its session. 1.9 ms for `/healthz`, 4.2 ms for `/process/status`.
  - All together about 7 core-s a minute: 0.7% of the demand.
  - The static files' 20-57 s waits in the run are queueing, not CPU: Starlette's `StaticFiles` takes one of the
    worker's 4 threads for each file, behind 1-2 s pages.
- **Wall time is CPU time on every route** (the same within a few %, 15% at most): the requests wait on nothing but
  the processor. A page above 0.3 s of CPU misses the p50 target even on an idle server, and two of them in one worker
  (one GIL) take twice as long.

## The budget (cpu_demand.py, cpu_budget.py)
`cpu_demand.py` replays load.py's people with instant answers, for 300 simulated hours per role:
- the role mix and the scenario weights; think times lognormal around 10 s, a third of it between steps;
- the browser cache: `/static` is revalidated on every page (`no-cache`, a 304); fonts and assets are fetched once a
  day; page images once per address;
- what this seed does: every approval is closed, so it is parked (as in the run, 45 of 45); no bulk candidates; a
  quarter of the invoices have a second page.

| | 100 people, 4 cores (4 workers) | 400 people, 32 vCPU (28 workers) |
|---|---:|---:|
| requests asked a minute | 5,318 (88.6/s; 75% static 304s) | 21,315 (355/s) |
| core-s a minute, as is | 1,085 (18.1 cores) | 4,354 (72.6 cores) |
| core-s a minute, with the fixes tried | 261 (4.4 cores) | 1,051 (17.5 cores) |
| capacity at 60% busy (one core a worker at most: the GIL) | 144 | 1,008 |
| mean CPU a request may cost at 60% | **27 ms** (as is 204 ms) | 47 ms |
| demand / capacity: as is, fixed | 7.5x, 1.8x | 4.3x, 1.04x |

The 4-core server at 100 people is the binding case. These targets bring it to 137 core-s (95% of 144); 400 people on
28 workers would then use 547 of 1,008:

| route | as is | must be | factor |
|---|---:|---:|---:|
| check | 1,290 ms | 100 ms | 13x |
| invoice page | 1,780 ms | 150 ms | 12x |
| queue and its tabs | 335-485 ms | 80 ms | 4-6x |
| page images | 34-128 ms | 8 ms | 4-16x |
| exports page | 800 ms | 150 ms | 5x |
| search | 184 ms | 100 ms | 2x |
| each report | 1.5-6.5 s | 1 s | 2-6x |

Two cautions:
- A worker gives at most one core. The run's workers were unbalanced: two at 0.34 cores, two at 1.0 (load report,
  "Server processes"). The connections of 25 people stay with the worker that took them (keep-alive), so 4 cores
  gave about 2.7 (cause **e**).
- The background process took 0.63 cores in the run.

## Per route (100 people; CPU as under load, warm; wall alone)
The table is `budget_100.md` (`python cpu_budget.py 100 4 4`).
- CPU and wall: medians of 3-7 warm requests (5-10 for the cheap ones).
- Under load, the check and the invoice page are measured with four vendors' invoices in turn
  (`cpu_interleave.py`). Their alone figures are in the wall column.
- n/min: cpu_demand.py. The fixed column is with the fixes tried below.

| route | n/min | CPU ms | wall ms alone | SQL | bytes | core-s/min | share | fixed: CPU ms | fixed: core-s/min |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| POST /review/{id}/check | 409.9 | 1,290 m | 640 | 64 | 40,000 | 528.8 | 48.7% | 191 m | 78.3 |
| GET /review/{id} | 200.6 | 1,780 m | 1,130 | 100 | 3,615,000 | 357.1 | 32.9% | 252 m | 50.6 |
| GET /review | 124.1 | 335 m | 341 | 43 | 85,000 | 41.6 | 3.8% | 125 m | 15.5 |
| GET /month-end | 3.4 | 6,490 m | 6,699 | 229 | 1,473,000 | 22.0 | 2.0% | 6,490 | 22.0 |
| GET /review?tab=second | 40.8 | 416 m | 439 | 39 | 112,000 | 17.0 | 1.6% | 180 e | 7.3 |
| GET /exports | 19.2 | 800 e (117-202 warm, 1,081 first m) | 800 | 39 | 165,000 | 15.4 | 1.4% | 150 e | 2.9 |
| GET /spend | 3.4 | 3,923 m | 3,952 | 37 | 35,900 | 13.3 | 1.2% | 3,923 | 13.3 |
| GET /search/export.xlsx | 3.1 | 2,886 m | 2,926 | 126 | 375,000 | 8.8 | 0.8% | 2,886 | 8.8 |
| GET /search (load.py's 8 queries) | 44.8 | 184 m (55-364) | 190 | 24-284 | 150,000 | 8.3 | 0.8% | 184 | 8.3 |
| GET /sales-tax | 3.4 | 2,319 m | 2,342 | 26 | **11,092,000** | 7.9 | 0.7% | 2,319 | 7.9 |
| POST /exports/create | 7.8 | 1,000 e | | | | 7.8 | 0.7% | 1,000 e | 7.8 |
| GET /review/{id}/page/{n}.png | 225.4 | 34 m (40 drawn, 8 kept) | 41 | 4 | 72,000 | 7.7 | 0.7% | 8 m | 1.8 |
| GET /learning | 3.8 | 1,519 m | 1,529 | 37 | 122,000 | 5.7 | 0.5% | 1,519 | 5.7 |
| GET /insights | 3.2 | 1,784 m | 1,804 | 39 | 39,000 | 5.7 | 0.5% | 1,784 | 5.7 |
| GET .../page/{n}.png?scale=2 | 40.0 | 128 m | 129 | 4 | 160,000 | 5.1 | 0.5% | 8 m | 0.3 |
| GET /process | 11.5 | 410 m (first 4,087) | 416 | 53 | 15,600 | 4.7 | 0.4% | 410 | 4.7 |
| GET /review?tab=approved | 9.5 | 485 m | 492 | 42 | 44,000 | 4.6 | 0.4% | 250 e | 2.4 |
| POST /review/{id}/park | 78.4 | 57 m | 60 | 30 | 200 | 4.5 | 0.4% | 57 | 4.5 |
| GET /search/export.csv | 6.1 | 700 e (218-1,978 m) | 700 | 126-161 | 1.4-10.4 MB | 4.3 | 0.4% | 700 | 4.3 |
| GET /static/{file} (304) | 4,007.0 | 1.0 m (uvicorn) | 1.8 | 0 | 0 | 4.0 | 0.4% | 0.35 m | 1.4 |
| GET /review?tab=parked | 9.6 | 339 m | 345 | 38 | 127,000 | 3.2 | 0.3% | 130 e | 1.2 |
| POST /review/{id}/final-approve | 18.7 | 100 e | | | | 1.9 | 0.2% | 100 e | 1.9 |
| GET /vendors | 3.6 | 478 m | 479 | 29 | 1,765,000 | 1.7 | 0.2% | 478 | 1.7 |
| GET /review?tab=other | 3.5 | 363 m | 368 | 39 | 991,000 | 1.3 | 0.1% | 150 e | 0.5 |
| GET /exports/batches/{id}/download | 4.0 | 200 e | | | | 0.8 | 0.1% | 200 e | 0.8 |
| GET /purchase-orders | 2.6 | 272 m | 273 | 17 | 2,034,000 | 0.7 | 0.1% | 272 | 0.7 |
| POST /process/upload | 5.8 | 100 e (web side) | | | | 0.6 | 0.1% | 100 e | 0.6 |
| the other 6 (status polls 4.2 ms m, reject, /activity 166 m, /settings 21 m, /admin/users 51 m, 200s of static) | 24.9 | | | | | 0.6 | | | 0.6 |
| **all** | **5,318** | | | | | **1,085** | 100% | | **261** |

Not in steady state, logins: 100 in the 60 s ramp.
- `GET /login` 5-8 ms, `GET /login/code` 5-9 ms, `POST /login/code` 7-9 ms (33 statements).
- `POST /login` 44-60 ms: scrypt at n=2^14, as designed.
- 100 logins cost about 7 core-s in the ramp.

The same costs times the run's own counts give about 1,525 core-s. The workers used 1,983 s (load report), so the
measured costs explain three quarters of the run. The rest is **e**: the first request of each worker (the learning
memory, `/process` 4 s, memos) and four threads sharing one GIL.

## Fixed costs (cpu_uvicorn_probe.py, real uvicorn, one keep-alive connection; uvicorn.json)
| request | server CPU ms |
|---|---:|
| a static file from memory on the event loop (scratch `MemoryStatic`: no thread, no middleware) | 0.35 (304 or 200) |
| `StaticFiles` alone, bare Starlette: 304 / 200 (49 KB) / 373 KB font | 0.76 / 1.51 / 2.44 |
| the worker's app: static 304 / 200 (also after the big pages: 1.03 / 1.80) | 0.98 / 1.61 |
| `/healthz` (session looked up: a new SQLite connection, 2 statements) | 1.93 |
| a route this Clerk may not use (403: session, `roles.need_for` matching 198 routes, refusal) | 2.70 |
| `GET /process/status` (signed in, a poll) | 4.18 |
| `GET /review/{id}/page/{n}.png` from the worker's picture cache (in process, TestClient: about 2 ms of its own) | 8 |
| `GET /review/{id}`, 3.6 MB, through uvicorn (sending it adds nothing measurable) | 1,020-1,490 |

Where the fixed cost goes:
- uvicorn's floor (h11, asyncio): 0.35 ms.
- `StaticFiles`: +0.4 ms for a 304 (a thread to stat the file), +1.2 ms for a 200.
- The middleware stack: +0.2-0.3 ms (the access-log line, security headers, the host check, `Identity`,
  `FriendlyErrors`, `Recycle`).
- The session of a signed-in request: about +1 ms (a new connection, opened and closed, 0.9 ms of it).
- Matching the routes twice (`roles.need_for` and the router, 198 routes each): about 0.5 ms.
- Not worth a fix for the CPU. Static files from memory are worth it for the waits: they never take a thread.

## What the profiles say (cpu_profile.py: the app on one thread, the threadpool inlined; cpu_pstats.py)
Top 5 by core-seconds: the check, the invoice page, the queue (with its tabs), month-end, spend.
Read the `profile_<route>.txt` and `pstats_<route>.txt` files. Figures are ms per request under the profiler (about
1.6 times slower).

### POST /review/{id}/check (1,126 ms profiled; `pstats_check.txt`)
- `analyse` takes 1,098 ms of it.
  - **`Store.vendor_ids()` twice: 603 ms** (`core/store/vendors.py:392`).
    - Called by `finalise_coding`'s JD Edwards voucher findings (`ai/pipeline.py:209`) and by `analyse`'s supplier
      number (`web/invoice_view.py:1796`).
    - Each call reads the 20,000 vendors with an ERP id and normalises each GST number (608k `isdigit` calls) into a
      dict of names, GST and BN keys. The answer is the same until the vendor master changes.
  - **`approved_numbers` twice: 293 ms** (`core/store/vendors.py:426`).
    - Called through `moved_off_hold` and `held_vendor` (`coding/vendors.py:491,521`): the vendor-on-hold check runs
      once for the vendor as read and once for the vendor on screen.
    - Each call is SQL with `json_extract` over the approved invoices of every vendor on hold: 340 ms of `fetchall`.
      The answer is the same until an invoice of a held vendor is approved.
  - **`suggestions` 114 ms**: `suggest_gl` for each line (stems and similarity against the vendor's lessons).
- The sections drawn (`_render_live`) take 3 ms. The checks themselves are small.

### GET /review/{id} (2,208 ms profiled; `pstats_invoice.txt`)
- `analyse`: 1,065 ms, the same as the check above.
- **`vendor_options`: 931 ms** (`web/pages/invoice.py:610`).
  - `vendor_choices()` reads all 20,000 vendors with `fetch_rows`: 343 ms.
  - Then `memory.vendor_key()` for each vendor: 491 ms (19k unicode folds and regexes).
  - Its `tojson` takes 50 ms and puts **3.3 MB in every page**, of the 3.6 MB it weighs.
- `navigation`: 54 ms. `list_invoices(REVIEW)` reads the 2,708 queue rows for Previous and Next.
- Jinja rendering: 85 ms in all.
- The first time an invoice is opened in a worker, and whenever the worker's previous request was for another vendor,
  the GL suggestions' index is built again (`pstats_invoice_first_fixed.txt`): `suggest._memory` 742 ms.
  - `RowsCache._fingerprint` hashes 51k rows: 298 ms.
  - `_build_memory` builds the patterns and the word index of 51k lessons: 425 ms.
  - Why it rebuilds: `learning_rows` hands over the shared 50k memory plus the vendor's older lessons, a new list for
    each vendor. `_past_memory` keeps those lessons for 2 vendors (`core/store/learning.py:248`), and
    `suggest._MEMORY` keeps 2 lists (`ai/suggest.py:118`). Under load, people on different vendors take turns in each
    worker.
  - Measured (`cpu_interleave.py`):

    | four vendors in turn | check | page |
    |---|---:|---:|
    | as is, alone | 517 ms | 1,192 ms |
    | as is, in turn | 1,295 ms | 1,781 ms |
    | with the four fixes, alone | 99 ms | 165 ms |
    | with the four fixes, in turn | 526 ms | 603 ms |

- The few vendors with about 50,000 lessons of their own cost 1.1-1.6 s a page even with every fix. Granite Logistics
  Contractors has 59,410; by the memory study, 6.6% of queue invoices read 50,000. (**m**, one in
  `interleave_more.json`.) Their lessons are read in full on each
  request.

### GET /review (499 ms profiled; `pstats_queue.txt`; same handler for every tab)
- **`queue_checks` 300 ms** (`coding/bulk.py:218`, called at `web/pages/review.py:366` for every tab).
  - `_today` takes 199 ms: one `json.loads` of all 2,708 invoices' findings, and a dict for each finding.
  - `_queue_rows` takes 63 ms (the summary's queue columns, a `computed()` for each).
  - The answer changes only when an invoice's summary or the checks stamp changes.
- `list_invoices` 65 ms; `attention` 50 ms (`_to_fix` for each invoice); `queue_rows` 28 ms (the 50 shown); Jinja
  24 ms; `all_vendor_terms` 21 ms.
- Left after keeping `queue_checks` (`pstats_queue_fixed.txt`, 171 ms profiled, 125 ms plain): `list_invoices` 54,
  `attention` 44, `queue_rows` 26, render 22, `all_vendor_terms` 19.

### GET /month-end (8,146 ms profiled; `pstats_month_end.txt`)
- `accruals.build` 7,824 ms, of which `_received_not_invoiced` 6,739 ms (`erp/accruals.py:68`).
  - `purchasing.billing_invoices` is called 69,446 times: 3,367 ms.
  - `json.loads` of 6,186 codings: 1,647 ms.
  - `po.positive_lines` and `billed_on` (Python loops over PO lines times invoices): 1,465-1,278 ms.
- `_not_in_erp` 984 ms; SQL `execute` 1,798 ms in all.
- The page is 1.5 MB.

### GET /spend (4,371 ms profiled; `pstats_spend.txt`)
- `spend.figures` 4,018 ms: `summary_fetch` SQL 2.2 s (`execute` own time), then Python over 57k parts.
  - `json.loads` of 57k amount lists: 445 ms.
  - `add` 310 ms; `_in_cad` 141 ms.

### Smaller ones
- **Page image** (`pstats_png*`): drawn 40 ms, of which pymupdf's `get_pixmap` and PNG encoding are 33 ms; 96 ms at
  scale 2. From the worker's 96 MB cache: 8 ms. That cache is per worker, so with 4 workers, and parks moving the top
  of the queue, most images are drawn (80% assumed, **e**).
- **Search**: 55-364 ms. `?q=word` runs 212-284 statements (`cpu_search_sql.py`). 252 of them are FTS5's own
  segment lookups (`SELECT pgno FROM ..._idx WHERE segid=?`): the `invoice_search` index is in many segments. Merging
  them (FTS5 `optimize`, after the seed and now and then) should cut `?q=` searches (**e**, not tried: it would write
  to the 100k database).
- **Static 304**: under the profiler, almost all of it is Starlette's `FileResponse` and `StaticFiles` lookup (a
  thread hop).

## The fixes, ranked by core-seconds a minute saved at 100 people
How the savings were measured:
- Tried as scratch patches in the probe process (`cpu_fixes.py`, `cpu_fixes2.py`, `cpu_first_open.py --fixed`,
  `cpu_interleave.py --fixed / --more`). Nothing was changed in the repo.
- A saving is (CPU before - after) x n/min. Single fixes are measured warm and alone (a lower bound).
- The ladder under load (four vendors in turn):
  - as is: check 1,295 ms, page 1,781 ms (888 core-s/min);
  - with fixes 1-2, 4-5: 526 / 603 (337);
  - adding 3: 191 / 252 (129).

1. **Keep the held-vendor check's approved numbers** (`approved_numbers`, two reads in each check and page).
   - As a `summary_memo` on approved invoices, or as columns in `invoice_summary`.
   - **m**: check 629 -> 323 ms, page 1,093 -> 832 ms: **-178 core-s/min (16%)**.
2. **Keep `vendor_ids()` per process**, keyed on the vendors table (a stamp, as `summary_memo` does).
   - **m**: check -118 ms, page -209 ms: **-90 core-s/min (8%)**.
   - Fixes 1 and 2 together: check 629 -> 102, page 1,093 -> 564: **-322 core-s/min (30%)**.
3. **Keep the GL suggestions' index and the vendor's older lessons for about 32 vendors**, not 2.
   - Today `RowsCache(slots=2)` at `ai/suggest.py:118` and `while len(_PAST_MEMORY) > 2` at
     `core/store/learning.py:248`.
   - Better: build the shared 50k-lesson index once per lessons stamp, and add each vendor's extra lessons as a small
     index of its own.
   - **m** (in turn, on top of 1, 2, 4, 5): check 526 -> 191 ms, page 603 -> 252 ms: **-208 core-s/min (19%)**.
4. **Take the vendor list out of the invoice page.**
   - Today `vendor_options` builds a 3.3 MB `#vendor-data` in every page.
   - Instead: one `/vendors.json?v=<stamp>` the browser keeps (`immutable`), built once per change.
   - **m**: page 1,093 -> 696 ms (-397): **-80 core-s/min (7%)**.
   - It also sends 3.3 MB less per invoice opened: 11 MB/s at 100 people, 44 MB/s at 400.
5. **Keep the queue's checks per stamp** (`queue_checks` for every `/review` and tab).
   - Keyed on the summary's generation and `checks_stamp`. Or keep the queue's facts (to fix, warned) in
     `invoice_summary`, as memory.md said.
   - **m**: /review 335 -> 125 ms. With the tabs (**e**, the same 210 ms): **-39 core-s/min (4%)**.
6. **Reports from memos or the summary.**
   - month-end 6.5 s, spend 3.9 s, sales-tax 2.3 s (and 11 MB of HTML), insights 1.8 s, learning 1.5 s: 55 core-s/min
     together (5% now; 21% of what is left after 1-5).
   - Fix: a `summary_memo` per topic, so each worker works them out once per change; or totals in SQL (`GROUP BY`);
     or built by the background process.
   - **e**: -45 core-s/min. Also stream `/search/export.*` from SQL (13 core-s/min).
7. **Read `list_invoices(REVIEW)` once per change.**
   - It runs in the invoice page's `navigation`, in every park's `next_url`, and in the queue: 50-65 ms each
     (**m** in the profiles).
   - **e**: -20 core-s/min.
8. **Keep the exports page per batch stamp.** First after a new batch 1,081 ms against 117-202 ms warm (**m**);
   batches come 8 a minute. **e**: -12 core-s/min.
9. **Draw page images once, into a shared cache on disk** (or at intake, in the background process).
   - **m**: drawn 40 ms (scale 2: 128 ms) against 8 ms read from a file through the same route:
     **-10.7 core-s/min (1%)**.
   - Also, no drawing in the request.
10. **Lighter suggestions in the check.** `suggest_gl` is 105-140 ms of the 190 ms left in a check (**m**,
    `pstats_check_fixed.txt`). Work out only the lines whose description or vendor changed since the last check of
    that screen, or keep the result per (vendor, description). **e**: -30 core-s/min.
11. **Static files from memory, on the event loop**, before the middlewares, with the same headers.
    - **m**: 1.0 -> 0.35 ms: **-2.6 core-s/min (0.2%)**.
    - Not a CPU fix. It takes the 4,000 a minute off the worker's 4 threads, so they no longer wait behind 1-2 s pages
      (the run's `/static` p95 19.6 s, `/fonts` 56.7 s).
    - Fonts and assets could also be cached with `immutable` (versioned), not the 10%-of-age heuristic.

With fixes 1-5 the demand is 261 core-s/min (**m**/**e** as in the table). Fixes 6-10 bring it to about 140 (**e**),
the 4-core budget at 60%. Two more things matter for latency:
- Each worker is one GIL, so the load must spread over the workers: in the run, 2 workers did 3 times the work of
  the other 2.
- The big vendors' pages (1-1.6 s even fixed) need their own fix: `vendor_lessons` and `learning_rows` read all of a
  vendor's lessons on each request.

## Notes
- Noise: the same warm route varies by about 30% from run to run (`/review` 265-590 ms), more after the heap has grown.
  Medians are given; the ranking does not depend on it.
- The probe's "first" figures in `routes.json` were taken with the SQL tracer on (statement counts), which inflates
  them. Use `first_open.json` for first opens: page 1,662 ms first against 1,134 ms again; checks 628-648 ms.
- A park does not empty the caches (`after_write.json`): each route's next request after a park and unpark costs what
  it did before, within the noise. Park 53-63 ms, unpark 11-12 ms.
- Approvals were not run on the 100k data. This seed's load approves nothing anyway (every Approve is closed).
  `POST /approve`, `final-approve`, `exports/create`, `upload`, `reject` and the batch download are estimated (**e**).
- **Written to the data folder:**
  - about 30 logins and their sessions, and the access-log lines;
  - the opening checks, drafts and confidence of about 45 invoices opened or checked;
  - 3 parks, each undone at once (3 `parked` and 3 `unparked` events, invoices 45-47th of the queue then).
  - No approval, export or upload.
- Re-run:
  - `cpu_probe_routes.py` (about 25 min) gives `routes.json` and `page_static.json`.
  - `cpu_demand.py 100` and `cpu_demand.py 400`, then `cpu_budget.py 100 4 4` and `cpu_budget.py 400 32 28`.
  - `cpu_uvicorn_probe.py` and `cpu_uvicorn_invoice.py`.
  - `cpu_profile.py [--fixed] invoice check queue png png_hit static middleware search invoice_first month_end spend`,
    then `cpu_pstats.py NAME N`.
  - `cpu_fixes.py`, `cpu_fixes2.py`, `cpu_first_open.py [--fixed]`, `cpu_interleave.py [--fixed|--more]`,
    `cpu_after_write.py` (parks and unparks).
  - Smaller checks: `cpu_testclient_floor.py` (the TestClient's own 2 ms a request), `cpu_idle_check.py`,
    `cpu_search_sql.py`, `cpu_explore.py`.
  - Run with `/home/user/AP_Processer/.venv/bin/python` from this folder (`cpu_common.py` puts `/home/user/int` on
    the path).
