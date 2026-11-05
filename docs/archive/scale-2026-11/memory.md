# Phase 2: what makes a web worker grow to 3-7 GB (2026-11-05)

Repo `/home/user/srv` @ 5a88509, the seeded 100k server folder (`bench_data/S/s100k`), opened as `bench/volume.py` does
(`create_app(..., server=True, background=False, processes=4)`, TestClient, real login with TOTP). The scripts
(kept outside the repo): `grow.py` sequential rounds, `concur.py` N people at once, `peak.py` tracemalloc at a
request's peak, `parts.py` the big reads alone, `probe_check.py` the review screen per vendor size.

## Verdict
- **There is no leak.** One person, one request at a time, through the whole load mix 3 times: RSS goes 142 MB
  (signed in) -> 766 MB after round 1 -> 749 -> 748 MB (gc). That is 622 MB after `malloc_trim`. It is flat from
  round 2 on.
- **The GBs come from requests running at once.** Each request in flight holds a big transient (about 110 MB on
  average, up to 2.4 GB). The memory freed afterwards is never given back (pymalloc arenas).
  - RSS rises to a level set by how many requests are in flight, then stays there.
  - 16 at once: 2.0-2.3 GB. 32 at once: 3.5 GB (peak 3.95 GB).
  - A uvicorn worker has anyio's default 40 threads and no limit (`web/server.py:217`, no `limit_concurrency`, no
    thread limiter). Under the load test the requests piled up (p50 27 s), so every worker ran tens of them at once.
    Add one `/activity` or controls report (+1.4-2.4 GB) and 3.4-6.9 GB follows.

## Sequential (grow.py, warm unless said): RSS before -> peak (VmHWM reset per request) -> after
| request | time | peak above level | retained once |
|---|---:|---:|---:|
| GET /review, first (no re-check needed) | 1.7 s | | +193 MB (recurring/vendor_invoice_dates memo, counts, reference) |
| GET /review, first, whole-queue re-check (checks stamp changed) | 25.7 s | +480 MB | +446 MB |
| GET /review warm (any view) | 0.55-0.85 s | +25-45 MB | 0 |
| first invoice open + check | 4.2 s | | +153 MB (the whole learning memory, 50k lessons) |
| invoice open + check, warm | 1.5-5.5 s | 0-+30 MB | 0 |
| big vendor (59,410 lessons) GET / check | 3.2 s / 1.7 s | +27 / +78 MB | 0 |
| /search?q=word | 2.2 s | +16 MB | +68 MB once |
| /search/export.xlsx (approved) | 8.0 s | +30-114 MB | +114 once (openpyxl) |
| /spend | 4.8-5.5 s | **+345 MB** | 0 |
| /exports?fmt=jde | 3.9-4.1 s | **+165-220 MB** | 0 |
| /activity (admin) | 19-20 s | **+1.31-1.42 GB** | +183 once |
| /process | 6.5-7.1 s | 0 | 0 |
| /insights, /sales-tax, /vendors, /learning, /purchase-orders, PNG scale 2 | | 0-+30 MB | |

The big reads alone (`parts.py`, no tracemalloc, RSS):
| read | where | time | RSS peak | rows |
|---|---|---:|---:|---:|
| `controls.build` over a year | `erp/controls.py:64` | 87.6 s | **+2,432 MB** | |
| `invoice_columns(final_output, APPROVED)` | `controls.py:76-84` (one_person) | 10.8 s | **+1,516 MB** | 95,705 |
| `events(limit=1_000_000)` | `controls.py:68` | 5.1 s | **+1,013 MB** | 416,326 |
| `spend.parts(default period)` | `erp/spend.py:81` | 3.1 s | +542 MB | 308,547 |
| `search_rows()` (quick search, exports) | `core/store/summary.py:552` | 1.0 s | +207 MB | 100,000 |
| `feedback_rows()` whole memory | `core/store/learning.py:66-78` | 1.4 s | +144 MB (132 held) | 50,000 |
| `feedback_rows(vendor_name=granite)` | `learning.py:45-54` | 0.55 s | +122 MB | 50,000 |
| `vendor_invoice_dates()` (memo) | `core/store/vendors.py:443` | 0.48 s | +104 MB held | 98,689 |
| queue raw validation/ai/final text | `bulk.py:131`, `review.py:188` | 0.16 s | +86 MB (2x a /review) | 2,722 |

## Concurrent (concur.py): one event loop, sync handlers on anyio's pool, as uvicorn runs them
Each person loops over the mix with no think time (work 40, queue 15, search 12, reports 10, look 8,
export_search 3). That makes "in flight" equal to the number of people. 150 s, then the people still under way
finish. Different people and addresses for each run.

| run | RSS plateau | peak | after gc | after malloc_trim | pymalloc arenas / live blocks | scenarios done |
|---|---:|---:|---:|---:|---:|---:|
| 16 at once | 2.2-2.35 GB | 2.48 | 2.15 | 1.88 | | 52 |
| 16 at once (again) | 1.8-2.0 GB | 2.10 | 1.93 | 1.66 | 931 MB / 369 MB (414 MB empty pools) | 50 |
| 16, MALLOC_ARENA_MAX=2 | 1.7-2.1 GB | 2.16 | 1.69 | 1.37 | 1,246 / 309 MB (743 MB empty pools) | 49 |
| **16, thread pool capped at 4** | **1.13-1.38 GB** | 1.42 | 1.33 | 1.19 | 636 / 240 MB | **52** |
| 16, caches dropped every 20 s (as approvals do) | 2.3-2.56 GB | 2.86 | 2.30 | 2.10 | 1,551 / 278 MB (1.1 GB empty pools) | 34 |
| 32 at once | **3.5 GB** | 3.95 | 3.39 | 2.94 | **2,109 / 389 MB (1.46 GB empty pools)** | 63 (by 300 s) |
| 32, PYTHONMALLOC=malloc + ARENA_MAX=2 | 1.8 GB | 1.89 | | | n/a | 32 by 490 s (stopped: slow) |

What these runs show:
- **RSS follows how many requests are in flight**, about 110 MB each: 16 at once gives 2 GB, 32 gives 3.5 GB.
- **Capping the pool halves the memory and loses nothing.** With the pool capped at 4 threads, the same 16 people
  did the same work (52 scenarios against 50) in 1.33 GB instead of 1.9-2.15 GB. One worker is bound by the GIL, so
  40 threads multiply the memory, not the throughput.
- **The memory freed is kept (fragmentation).** At 32 at once, pymalloc holds 2.1 GB of arenas around 389 MB of
  live objects. An arena is only given back when it is entirely empty. A few long-lived objects in each one
  (cache rows, lru entries, `_TO_FIX`) keep it.
  - `MALLOC_ARENA_MAX=2` alone saves about 230 MB.
  - glibc's allocator for everything (`PYTHONMALLOC=malloc`) halves the plateau (1.8 GB against 3.5 GB) but was
    much slower here.
- **Approvals make it worse.** When the caches are dropped every 20 s, as each approval's new stamps make every
  worker read them again, the plateau rises by 300-500 MB and the work done falls by a third.

### tracemalloc in flight (16 at once, 6 frames; traced 514 MB at 55 s, 584 MB at 344 s)
At 55 s, the queue's reads:
```
+175.3 MB +2394868  web/pages/review.py:398 <- coding/bulk.py:120 <- coding/bulk.py:136   (json.loads of every queue validation)
+125.4 MB   +54444  web/pages/review.py:399 <- review.py:188 <- core/store/invoices.py:625 <- summary.py:290 (raw text)
+105.5 MB   +70772  coding/bulk.py:120 <- bulk.py:131 <- invoices.py:625 <- summary.py:290                (raw text)
 +22.8 MB      +26  review.py:399 <- review.py:188 <- invoices.py:625                                   (fetch_rows' JSON text)
 +14.9 MB  +297502  review.py:388 <- invoices.py:318 <- summary.py:290                                   (list_invoices)
```
At 344 s, the learning memory, read by several threads at once (no single flight):
```
+303.2 MB +4999952  web/invoice_view.py:1422 <- core/store/learning.py:78 <- learning.py:66 <- summary.py:290
 +62.5 MB      +26  web/pages/invoice.py:548 <- invoice_view.py:1818 <- 1422 <- learning.py:78  (the 50k rows' JSON text, 2-3 at once)
 +22.8 MB  +394834  core/store/vendors.py:443 <- summary.py:533 (vendor_invoice_dates memo)
 +21.3 MB  +380217  invoice.py:548 <- invoice_view.py:1803 <- core/store/vendors.py:385
 +15.9 MB  +308989  pages/search.py:103 <- erp/directory.py:339 <- erp/search.py:209
```
The first invoice opened (peak.py): one vendor's lessons were held 3 times at once in one check, about 38-61 MB each
(`invoice.py:785 <- invoice_view.py:1760 <- ai/pipeline.py:164`, `invoice_view.py:1818 <- 1422`,
`invoice.py:792 <- invoice_view.py:709 <- learning.py:54`). The check's own writes change `total_changes`, which
invalidates `_session_memo`; the suggestions' RowsCache also keeps the previous rows.

## The causes, ranked by MB under the load mix
1. **Unbounded concurrency in each worker.** About 110 MB per request in flight; anyio's 40 threads (`web/server.py:217`
   sets no limit). 16 at once: 2.0-2.3 GB; 32 at once: 3.5-3.9 GB. Scales with requests in flight, which rise as
   latency rises (a spiral).
   - **Fix:** cap the thread limiter in the lifespan (`anyio.to_thread.current_default_thread_limiter().total_tokens
     = 4-6`), plus uvicorn `limit_concurrency` to refuse with 503 past a backlog.
   - **Measured:** 2.0-2.3 GB -> 1.33 GB, same throughput.
2. **The learning memory (`core/store/learning.py:66-78`).**
   - The whole memory (50k lessons) is 132 MB held per process, and 145 MB at its peak while it is read: the dicts
     plus a 60 MB JSON text (`summary.py:289`).
   - It has no single flight: every thread that misses reads its own copy, 303 MB in flight above. It misses again
     after every new lesson (every approval) in every worker.
   - **Fix:** single flight, as `summary_memo`'s `_COMPUTING` does; a refresh that reads only the new lessons
     (`id > last`); or build the index once, in the background process.
   - The vendor's own lessons (`learning.py:45-54`) are read on every GET and every check, up to 50,000 rows and
     +122 MB for the biggest vendor. On average 5,335 rows an invoice of the queue (6.6% read 50,000).
     `vendor_accounts` (`invoice_view.py:709`) only needs the 6 GL accounts used most: a `GROUP BY final_gl` in SQL.
     Or keep an LRU of vendors across requests, keyed by the feedback stamp.
3. **GET /review, the busiest route.**
   - Each request reads the raw validation, ai_output and final_output text of all 2,722 queue invoices twice:
     `coding/bulk.py:131` (`issues_today`) and `web/pages/review.py:188` (`attention`), +86 MB each.
   - It decodes every validation (`bulk.py:136`) only to reach `recheck.issues`.
   - `_to_fix` caches by a hash of the text, so the text is still read in full every time.
   - When the checks stamp changes (any vendor or PO change, `masters_version`), the first request re-checks the
     whole queue and writes every invoice: 25.7 s and +446 MB, once in each worker that gets there first.
   - **Fix:**
     - Keep the queue's facts (to pick / errors / warned, the re-check findings) in `invoice_summary`.
     - Or read `json_extract(validation, '$.recheck')` and a cheap row version in SQL.
     - Single-flight the re-check, or move it to the background process.
4. **`/activity` and the controls report (`erp/controls.py:64-98`).**
   - `events(limit=1_000_000)` decodes all 416k events: +1.0 GB.
   - `invoice_columns(final_output, APPROVED)` decodes 95.7k codings: +1.5 GB.
   - Together +1.3-1.4 GB for the page, +2.4 GB for a year's report. Linear in events and approvals.
   - **Fix:** the period filter and the needed fields in SQL (`WHERE created_at >= ? AND created_at < ?`,
     `json_extract`), and the one-person check as a query.
5. **`/spend` (`erp/spend.py:81`).** 308k `summary_spend` parts become Python objects: +345-542 MB.
   **Fix:** totals with GROUP BY in SQL.
6. **Search and exports.**
   - `search_rows()` reads the whole directory for each search: +207 MB.
   - `/search/export.xlsx` builds the workbook in memory with openpyxl (`pages/search.py:159`): +100-250 MB.
   - `/exports?fmt=jde`: +165-220 MB.
   - **Fix:** SQL or FTS paging; `Workbook(write_only=True)`; streamed CSV.
7. **`fetch_rows` itself (`core/store/summary.py:289`).** Its one JSON text sits beside the decoded rows, so a big
   read peaks at about 1.6 times its rows. SQLite's own copy, outside tracemalloc, comes on top.
   **Fix:** read big results in chunks of ids.
8. **Allocator retention.** pymalloc keeps what the peaks free: 414 MB to 1.46 GB of empty pools in the runs above.
   - **Fix:** fewer transient objects (1-7).
   - Safety net: recycle a worker past a memory ceiling or a number of requests (the service already runs several).
   - `PYTHONMALLOC=malloc` halves the plateau but costs CPU: measure it before using it.
9. **Bounded caches held in every worker.** About 600 MB a worker after warm-up, 8 times over with 8 workers:
   - the learning memory, 132 MB;
   - `vendor_invoice_dates` and the `recurring` memo, about 104 MB (`vendors.py:443`, `recurring.py:106`);
   - the PNG cache, up to 96 MB (`web/document.py:36`; small in these runs);
   - `RowsCache` / `_MEMORY` (`ai/suggest.py:118`), the lru_caches (`core/memory.py:92,127`, 131,072 entries each),
     `_TO_FIX` / `_PAY_FROM` (`review.py:128`).
   - **Fix:** keep the shared values in the database (summary tables), or have the background process build them.

## Fixes in order of value
1. Cap each worker's thread pool to a few threads (and set `limit_concurrency`). Measured: half the RSS, same
   throughput. One line in the lifespan.
2. Single flight for `feedback_rows()`, and stop reading the vendor's lessons in full on every request (SQL
   aggregates, or an LRU keyed by the stamp).
3. Make GET /review read small columns: the queue's facts in the summary, the re-check in SQL or in the background.
4. `controls.build` and `/activity` filtered in SQL. This removes the single 1.4-2.4 GB peaks.
5. `/spend` totals in SQL; xlsx in write-only mode; search paging in SQL.
6. Recycle workers past a memory ceiling, as a safety net.

## Notes
- tracemalloc undercounts RSS a lot: fragmentation, SQLite's buffers, and its own overhead (peak.py's RSS figures
  under tracemalloc are not usable; only its attributions are).
- The very first run here sat more than 600 s in its first GET /review (killed): the whole-queue re-check. The next
  one took 25.7 s.
- **What the runs wrote to the data folder:**
  - logins, their sessions and access logs;
  - the opening checks (drafts, confidence) on queue invoices;
  - the whole-queue re-check stored once;
  - two runs started together signed in the same people and refused each other's codes: logins from 10.0.0.5 were
    paused for 15 minutes (it ends by itself).
  No approvals; the `--recheck` patch, which would have written nothing, was not used.
