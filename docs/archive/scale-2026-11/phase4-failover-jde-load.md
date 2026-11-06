# Phase 4 measured: the model machine's failover, JD Edwards at volume, and nothing worse (6 Nov 2026)

On the integration branch at de9ab79 (phase 4 with its four reviews' fixes), the 4 cores with nothing else
running, Python 3.12 with both OCR models.

## Failover to the backup model machine (target 3)

`python -m ap_coder.bench day --n 40 --scanned 0.5 --workers 2 --backup --reviewer all --drill <kill|hang> --drill-for
60`: two stand-in model servers (the primary and the backup), the drill on the primary after its third page read, the
AI reviewer on every invoice.

| drill | to the backup | back to the primary | reads lost / done twice / stored twice | failures |
|---|---:|---:|---|---:|
| the primary killed for 60 s | 1 s after the drill began (1 s after the first unanswered request) | 15 s after it ended | 0 / 0 / 0 | 0 |
| the primary hung for 60 s | 31 s after the drill began (1 s after the first unanswered request) | 36 s after it ended | 0 / 0 / 0 | 0 |

- Each drill's 40 invoices all reached the review queue, read and reviewed; `GET /review` p95 during the drop 75-88 ms.
- The hang's 31 s is the bench's scaled call limits (a review gives up at 30 s there). At the app's own limits, with
  two reviews holding both chat slots, a hang moves the calls in 46 s (the review-held test of D-180's minute cap);
  the fix agent's drill at the app's limits measured 20.9 s.
- Also drilled during the work (D-180): slow ×100 for 90 s → the backup in 29-31 s; a swapped chat model: no move,
  the new model trusted after it read the test invoice's header (4-8 s).

## JD Edwards at volume (target 4), against the AIS stand-in

`python -m tests.erp.test_jde_sync_volume` (30,000 suppliers, 600,000 vouchers; "longest write wait": how long a tiny
write from another connection, every 20 ms, waited for the lock):

| run | took | longest write wait | read | register right |
|---|---:|---:|---|---|
| first sync | 51.2 s | 0.054 s | 30,000 suppliers, 600,000 vouchers (1,260 pages) | yes |
| next night (50 suppliers changed, 1,000 vouchers new, 50 voided) | 5.0 s | 0.039 s | 50 suppliers, 28,568 vouchers; 30 voided removed | yes |
| weekly whole read, AIS down at the vouchers' page 600 | 28.5 s | 0.054 s | stopped, kept | — |
| the next sync, resuming | 25.9 s | 0.079 s | the 602 pages left; 20 older voids removed | yes |

The 500-voucher run (`tests/erp/test_jde_post_runs.py::test_500_vouchers_post_in_one_run_through_slow_and_late_answers_none_twice`:
every answer after 20 ms, one in ten late past the wait (half made by JDE, half never), one in twenty refused):
approved and batched 13.7 s, dry run 3.6 s, **one go-ahead run of 38.8 s: 474 posted, 25 refused, 1 unknown; the
queue after it posted the last: 475 posted, 25 refused, 525 sends, 75 lookups, none made twice.** On the code before
phase 4 the same run stops at the first late voucher and queues the rest. The duplicate check beside a
20,000-voucher supplier: right, about 0.1 s a check (`tests/coding/test_vendors.py::test_the_duplicate_check_stays_right_and_quick_beside_a_supplier_with_20000_vouchers`).

## Nothing worse (target 8)

- `scripts/same_behaviour.py --ref origin/main`: approve and read the same; tax and pipeline different, as D-183
  intends: half up where the float rounded half even (`every line (250.50) × 15% = 37.57` → `37.58`; a PST spread
  `250.42` → `250.43`), and the Tax check's total row showing the sum TOTAL_MISMATCH checks (the tax total).
- The accuracy benches (`coding --realistic`, `run --quick`): byte for byte the same (D-183).
- **Load, 100 people on 100,000 invoices, 600 s.** The first run had a stall in its second minute: month-end's first
  answer on a fresh seed (9 s of CPU alone, on main's code as on this one) took over 30 s under load, and the workers
  waiting for it worked it out again themselves (pages up to 55 s, p99 3.49 s, 8 requests over 30 s). The second
  run, on a seed made again, had none: **p50 129 ms, p95 636 ms, p99 1.89 s, actions p95 296 ms, 0 errors, 0 over
  30 s** (phase 3 part 3: 136 ms, 673 ms, 1.84 s, 348 ms). The cascade is B-1914, fixed after both runs: a process
  waits for an answer another is still working out (its hold renewed) rather than work it out twice.

## The reports


### The day bench, drill kill

#### Server day: a morning's drop through the company server

40 invoices (50% scans, seed 1) delivered by the invoices folder and the Process page to `serve --server --workers 2`; the fake model: 8-20 s a page, 8-20 s a chat answer, 2 slot(s), AP_LLM_PARALLEL 2, time x0.05; AI reviewer: all. 4 cores, Python 3.12.3, Linux. Wall time 2.5 min.

| | measured | at full latency (estimate) |
|---|---:|---:|
| invoices in the review queue | 40 of 40 | |
| all of them in after | 2.0 min | 6.8 min |
| arrival, invoices an hour | 1,175 | 352 |
| page reader: queued / skipped (clean digital) / read / failed / left | 40 / 0 / 40 / 0 / 0 | |
| page reader drained after (from the drop) | 2.3 min | 15.7 min |
| page reader, invoices an hour | 1,210 | |
| AI reviewer: queued / done / failed / left | 40 / 40 / 0 / 0 | |
| AI reviewer, reviews an hour (first one after) | 1,210 (23 s) | |
| AI reviewer: reviews done while the page reader had invoices in line | 36 | |
| AI reviewer: an invoice's wait for it once read, median / max | 1 s / 16 s | |
| model time an invoice | | 57.0 s |
| invoices the model machine serves in a 10-hour day | | 1,263 |
| GET /review p50 / p95, idle | 12 ms / 16 ms | |
| GET /review p50 / p95 / max, during the drop | 20 ms / 88 ms / 127 ms (48 probes, 0 errors) | |
| failures (imports, reads, reviews, probes, locked, tracebacks) | 0 | |

CPU (seconds over the run; cores = seconds / wall time):

| process | n | CPU s | busiest | cores |
|---|---:|---:|---:|---:|
| server | 1 | 0.2 | 0.2 | 0.0 |
| background | 1 | 62.2 | 62.2 | 0.42 |
| capture | 2 | 209.0 | 104.5 | 1.42 |
| other | 1 | 0.0 | 0.0 | 0.0 |
| web worker | 2 | 3.0 | 2.1 | 0.02 |
| the bench and its fake model | 1 | 7.1 | | |

Over time (seconds from the drop):

| s | in the queue | page reads waiting / reading / done | reviews waiting / done | model busy / waiting |
|---:|---:|---:|---:|---:|
| 2 | 2 | 2 / 0 / 0 | 2 / 0 | 1 / 0 |
| 14 | 3 | 3 / 0 / 0 | 3 / 0 | 1 / 0 |
| 26 | 6 | 3 / 2 / 1 | 5 / 1 | 2 / 1 |
| 38 | 12 | 4 / 1 / 7 | 6 / 5 | 0 / 0 |
| 50 | 17 | 4 / 1 / 12 | 6 / 10 | 0 / 0 |
| 62 | 20 | 0 / 2 / 18 | 3 / 17 | 0 / 0 |
| 75 | 23 | 2 / 0 / 21 | 2 / 21 | 0 / 0 |
| 87 | 28 | 7 / 0 / 21 | 7 / 21 | 0 / 0 |
| 99 | 36 | 13 / 1 / 22 | 14 / 22 | 0 / 0 |
| 111 | 38 | 10 / 0 / 28 | 10 / 26 | 2 / 0 |
| 123 | 39 | 5 / 1 / 33 | 6 / 32 | 0 / 0 |
| 135 | 40 | 1 / 2 / 37 | 3 / 37 | 0 / 0 |
| 147 | 40 | 0 / 0 / 40 | 0 / 40 | 0 / 0 |

Page reader self-test: ath-maas_ovisocr2 passed (9 of 9 fields).

##### Drill: kill

- from 28.2 s to 88.6 s after the drop; requests not answered in it: {'review dropped': 1, 'models dropped': 7, 'chat dropped': 11}; the app saw it after 0 s
- recovery (the first page read after it ended): 15 s; a model answer took 0.65 s before it, - s in it (median, as measured)
- reads postponed 0 (cut off up to 0 time(s) each), lost 0 (failed or never read: none), pages read twice 0
- failover: the backup's first answer 1 s after the drill began (1 s after the first request the primary didn't answer; by kind {'page': 1.8, 'suggest': 8.7, 'header': 2.3, 'review': 1.3}); back on the primary 15 s after the drill ended ({'page': 15.3, 'suggest': 20.4, 'header': 16.8, 'review': 15.9}); answered meanwhile {'primary': {'page': 4, 'review': 3, 'header': 3, 'suggest': 1}, 'backup': {'review': 22, 'page': 23, 'header': 21, 'suggest': 27}}
- readings or reviews stored twice: 0

Notes: at full latency is an estimate. Arrival adds back the chat model's answers the import waited for (GL suggestions), each at most the arrival's limit and the capture processes waiting side by side; the page reader its pages, header and review answers divided by AP_LLM_PARALLEL; the work on this computer (OCR, the pipeline) is as measured. The app's own timers are real time: a minute's pause after a failed read, 30 s when nothing waits, the model status kept 10-30 s, the breaker's hang limit, hold and probes (D-180).

### The day bench, drill hang

#### Server day: a morning's drop through the company server

40 invoices (50% scans, seed 1) delivered by the invoices folder and the Process page to `serve --server --workers 2`; the fake model: 8-20 s a page, 8-20 s a chat answer, 2 slot(s), AP_LLM_PARALLEL 2, time x0.05; AI reviewer: all. 4 cores, Python 3.12.3, Linux. Wall time 2.6 min.

| | measured | at full latency (estimate) |
|---|---:|---:|
| invoices in the review queue | 40 of 40 | |
| all of them in after | 2.4 min | 7.5 min |
| arrival, invoices an hour | 1,018 | 319 |
| page reader: queued / skipped (clean digital) / read / failed / left | 40 / 0 / 40 / 0 / 0 | |
| page reader drained after (from the drop) | 2.4 min | 15.7 min |
| page reader, invoices an hour | 1,152 | |
| AI reviewer: queued / done / failed / left | 40 / 40 / 0 / 0 | |
| AI reviewer, reviews an hour (first one after) | 1,143 (22 s) | |
| AI reviewer: reviews done while the page reader had invoices in line | 39 | |
| AI reviewer: an invoice's wait for it once read, median / max | 1 s / 31 s | |
| model time an invoice | | 57.3 s |
| invoices the model machine serves in a 10-hour day | | 1,256 |
| GET /review p50 / p95, idle | 12 ms / 15 ms | |
| GET /review p50 / p95 / max, during the drop | 18 ms / 75 ms / 79 ms (50 probes, 0 errors) | |
| failures (imports, reads, reviews, probes, locked, tracebacks) | 0 | |

CPU (seconds over the run; cores = seconds / wall time):

| process | n | CPU s | busiest | cores |
|---|---:|---:|---:|---:|
| server | 1 | 0.2 | 0.2 | 0.0 |
| background | 1 | 63.0 | 63.0 | 0.41 |
| capture | 2 | 204.4 | 106.3 | 1.33 |
| other | 1 | 0.0 | 0.0 | 0.0 |
| web worker | 2 | 2.9 | 1.8 | 0.02 |
| the bench and its fake model | 1 | 7.0 | | |

Over time (seconds from the drop):

| s | in the queue | page reads waiting / reading / done | reviews waiting / done | model busy / waiting |
|---:|---:|---:|---:|---:|
| 2 | 2 | 2 / 0 / 0 | 2 / 0 | 1 / 0 |
| 14 | 3 | 3 / 0 / 0 | 3 / 0 | 1 / 0 |
| 26 | 8 | 5 / 2 / 1 | 7 / 1 | 2 / 1 |
| 38 | 10 | 6 / 1 / 3 | 7 / 2 | 2 / 1 |
| 50 | 10 | 6 / 1 / 3 | 7 / 2 | 2 / 1 |
| 62 | 12 | 5 / 2 / 5 | 7 / 5 | 1 / 0 |
| 74 | 18 | 6 / 2 / 10 | 8 / 10 | 0 / 0 |
| 87 | 21 | 5 / 1 / 15 | 6 / 14 | 1 / 0 |
| 99 | 27 | 5 / 2 / 20 | 7 / 20 | 0 / 0 |
| 111 | 31 | 4 / 1 / 26 | 5 / 25 | 0 / 0 |
| 123 | 36 | 3 / 1 / 32 | 4 / 31 | 1 / 0 |
| 135 | 38 | 0 / 2 / 36 | 2 / 36 | 1 / 0 |
| 147 | 40 | 0 / 1 / 39 | 1 / 39 | 0 / 0 |
| 153 | 40 | 0 / 0 / 40 | 0 / 40 | 0 / 0 |

Page reader self-test: ath-maas_ovisocr2 passed (9 of 9 fields).

##### Drill: hang

- from 28.2 s to 88.6 s after the drop; requests not answered in it: {'review hung': 1, 'page hung': 1, 'suggest hung': 1, 'chat hung': 2}; the app saw it after 30 s
- recovery (the first page read after it ended): 36 s; a model answer took 0.66 s before it, - s in it (median, as measured)
- reads postponed 0 (cut off up to 0 time(s) each), lost 0 (failed or never read: none), pages read twice 0
- hung requests 5, given up by AP Coder 4 (the rest dropped when LM Studio restarted); the longest held 45 s
- failover: the backup's first answer 31 s after the drill began (1 s after the first request the primary didn't answer; by kind {'page': 31.5, 'suggest': 35.7, 'header': 32.1, 'review': 31.1}); back on the primary 36 s after the drill ended ({'page': 36.3, 'suggest': 41.1, 'header': 36.3, 'review': 37.5}); answered meanwhile {'primary': {'page': 3, 'header': 2, 'review': 2, 'suggest': 2}, 'backup': {'review': 30, 'page': 32, 'header': 29, 'suggest': 27}}
- readings or reviews stored twice: 0

Notes: at full latency is an estimate. Arrival adds back the chat model's answers the import waited for (GL suggestions), each at most the arrival's limit and the capture processes waiting side by side; the page reader its pages, header and review answers divided by AP_LLM_PARALLEL; the work on this computer (OCR, the pipeline) is as measured. The app's own timers are real time: a minute's pause after a failed read, 30 s when nothing waits, the model status kept 10-30 s, the breaker's hang limit, hold and probes (D-180).

### The load, 100 people on 100,000 invoices (the second run)

#### Load test: 100 people for 600 s on 4 worker(s)

2026-11-06T19:48:56; ramp 60 s, think 10.0 s (median), connections kept 5 s unused; 100000 invoices in /tmp/claude-0/-home-user/b4fef7c4-f9cb-52fc-9da2-47ad67a7ebc2/scratchpad/bench_data/S/s100k; models: ap_coder/bench/fake_model.py, 1-4 s an answer, 2 at once, at http://192.0.2.2:44153/v1; people reach the server at 192.0.2.2. 4 cores, Python 3.12.3, Linux-6.18.44-fc-v242-x86_64-with-glibc2.39.

##### Summary
- Requests: 14100 (21.36/s); 5xx 0, without an answer 0, cut off at the end 0, over 30 s 0; refused (4xx) 151.
- Pages: p50 129 ms, p95 636 ms, p99 1.89 s (5543); actions (check, approve, park, reject, final approval): p50 50 ms, p95 296 ms (2596). Timed from when each step was due (scheduled start).
- Targets: pages p50 <= 0.3 s met, pages p95 <= 1.5 s met, pages p99 <= 3.0 s met, actions p95 <= 1.0 s met.
- Behind schedule: p50 1 ms, p95 12 ms.
- Harness CPU: 0.143 cores (its event loop 0.141): well under one core, so the figures are the server's. Server started in 3.07 s; its stop took 1.02 s (shutdown).
- Server logs: 0 'database is locked', 0 5xx of 14105 lines, 0 tracebacks.

##### Gate
- GET /process/status: p95 0.233 s, baseline 0.135 s (1.7x)
- POST /login/code: p95 0.306 s, baseline 0.131 s (2.3x)

##### Routes

| route | n | p50 | p95 | p99 | max | server p95 | statuses | refused | errors |
|---|---:|---:|---:|---:|---:|---:|---|---:|---:|
| GET /review | 2800 | 161 ms | 550 ms | 1.12 s | 6.09 s | 519 ms | 200: 2800 | 0 | 0 |
| POST /review/{id}/check | 2139 | 54 ms | 302 ms | 845 ms | 2.55 s | 238 ms | 200: 1988, 409: 151 | 151 | 0 |
| GET /review/{id}/page/{n}.png | 1724 | 14 ms | 63 ms | 219 ms | 883 ms | 35 ms | 200: 1724 | 0 | 0 |
| GET /static/{file} | 1590 | 9 ms | 52 ms | 87 ms | 297 ms | 0 ms | 200: 1590 | 0 | 0 |
| GET /review/{id} | 1301 | 100 ms | 472 ms | 1.08 s | 3.48 s | 447 ms | 200: 1301 | 0 | 0 |
| GET /review/vendors.json | 878 | 23 ms | 84 ms | 163 ms | 711 ms | 25 ms | 200: 878 | 0 | 0 |
| GET /search | 591 | 91 ms | 384 ms | 673 ms | 960 ms | 337 ms | 200: 591 | 0 | 0 |
| GET /downloads/{id} | 494 | 17 ms | 138 ms | 358 ms | 555 ms | 61 ms | 200: 494 | 0 | 0 |
| GET /process/status | 326 | 13 ms | 233 ms | 570 ms | 1.23 s | 113 ms | 200: 326 | 0 | 0 |
| POST /review/{id}/park | 323 | 23 ms | 303 ms | 685 ms | 925 ms | 176 ms | 200: 323 | 0 | 0 |
| GET /assets/{file} | 200 | 42 ms | 51 ms | 58 ms | 80 ms | 0 ms | 200: 200 | 0 | 0 |
| GET /fonts/{file} | 200 | 3 ms | 13 ms | 19 ms | 32 ms | 1 ms | 200: 200 | 0 | 0 |
| GET /process | 177 | 87 ms | 295 ms | 1.24 s | 1.63 s | 212 ms | 200: 177 | 0 | 0 |
| GET /exports | 136 | 422 ms | 900 ms | 1.33 s | 1.89 s | 888 ms | 200: 136 | 0 | 0 |
| POST /review/{id}/final-approve | 116 | 21 ms | 247 ms | 354 ms | 603 ms | 183 ms | 200: 116 | 0 | 0 |
| GET /login | 100 | 7 ms | 59 ms | 1.75 s | 2.36 s | 21 ms | 200: 100 | 0 | 0 |
| GET /login/code | 100 | 47 ms | 79 ms | 90 ms | 104 ms | 19 ms | 200: 100 | 0 | 0 |
| POST /login | 100 | 63 ms | 332 ms | 907 ms | 1.24 s | 325 ms | 200: 100 | 0 | 0 |
| POST /login/code | 100 | 13 ms | 306 ms | 752 ms | 827 ms | 196 ms | 200: 100 | 0 | 0 |
| GET /search/export.csv | 91 | 16 ms | 236 ms | 835 ms | 835 ms | 160 ms | 200: 91 | 0 | 0 |
| GET /search/export.csv?job={id} | 91 | 57 ms | 250 ms | 532 ms | 532 ms | - | 200: 91 | 0 | 0 |
| POST /process/upload | 88 | 28 ms | 297 ms | 1.14 s | 1.14 s | 283 ms | 200: 88 | 0 | 0 |
| GET /insights | 58 | 23 ms | 2.61 s | 3.48 s | 3.48 s | 2.61 s | 200: 58 | 0 | 0 |
| GET /learning | 51 | 59 ms | 1.16 s | 2.08 s | 2.08 s | 1.16 s | 200: 51 | 0 | 0 |
| GET /month-end | 51 | 310 ms | 4.43 s | 8.58 s | 8.58 s | 4.39 s | 200: 51 | 0 | 0 |
| GET /spend | 46 | 34 ms | 4.13 s | 4.92 s | 4.92 s | 4.13 s | 200: 46 | 0 | 0 |
| GET /search/export.xlsx | 36 | 12 ms | 356 ms | 764 ms | 764 ms | 145 ms | 200: 36 | 0 | 0 |
| GET /search/export.xlsx?job={id} | 36 | 60 ms | 138 ms | 457 ms | 457 ms | - | 200: 36 | 0 | 0 |
| GET /sales-tax | 35 | 40 ms | 1.32 s | 1.50 s | 1.50 s | 1.28 s | 200: 35 | 0 | 0 |
| GET /vendors | 35 | 1.55 s | 2.93 s | 3.34 s | 3.34 s | 2.93 s | 200: 35 | 0 | 0 |
| GET /purchase-orders | 30 | 131 ms | 308 ms | 429 ms | 429 ms | 303 ms | 200: 30 | 0 | 0 |
| POST /review/{id}/reject | 18 | 28 ms | 174 ms | 174 ms | 174 ms | 157 ms | 200: 18 | 0 | 0 |
| GET /admin/users | 12 | 34 ms | 98 ms | 98 ms | 98 ms | 84 ms | 200: 12 | 0 | 0 |
| GET /activity | 10 | 188 ms | 617 ms | 617 ms | 617 ms | 568 ms | 200: 10 | 0 | 0 |
| GET /settings | 10 | 14 ms | 120 ms | 120 ms | 120 ms | 103 ms | 200: 10 | 0 | 0 |
| GET /review/{id}/reader | 5 | 13 ms | 15 ms | 15 ms | 15 ms | 10 ms | 200: 5 | 0 | 0 |
| POST /exports/create | 2 | 85 ms | 93 ms | 93 ms | 93 ms | 78 ms | 200: 2 | 0 | 0 |

##### Server processes

| process | CPU s | mean cores | peak cores | RSS MB first → last (peak) |
|---|---:|---:|---:|---|
| model | 1.0 | 0.0 | 0.04 | 22.4 → 22.7 (30.8) |
| server | 1.0 | 0.0 | 0.01 | 61.8 → 61.8 (61.8) |
| background | 193.9 | 0.29 | 1.23 | 209.2 → 470.8 (470.8) |
| server child | 82.4 | 0.13 | 0.99 | 133.5 → 330.8 (331.8) |
| server child | 69.6 | 0.11 | 0.99 | 134.0 → 331.8 (331.8) |
| resource tracker | 0.0 | 0.0 | 0.0 | 13.5 → 13.5 (13.5) |
| worker 1 | 203.5 | 0.31 | 1.33 | 129.6 → 820.9 (820.9) |
| worker 2 | 216.9 | 0.33 | 1.25 | 129.6 → 817.9 (817.9) |
| worker 3 | 251.9 | 0.38 | 1.27 | 126.6 → 840.8 (877.6) |
| worker 4 | 300.4 | 0.46 | 1.45 | 126.5 → 933.2 (933.3) |

##### Over time (a row a minute)

| from | requests/s | errors | p95 (worst 10 s) |
|---:|---:|---:|---:|
| 0 min | 33.6 | 0 | 298 ms |
| 1 min | 28.0 | 0 | 1.21 s |
| 2 min | 22.6 | 0 | 1.16 s |
| 3 min | 20.1 | 0 | 538 ms |
| 4 min | 19.2 | 0 | 852 ms |
| 5 min | 19.4 | 0 | 883 ms |
| 6 min | 18.3 | 0 | 494 ms |
| 7 min | 18.7 | 0 | 686 ms |
| 8 min | 18.3 | 0 | 713 ms |
| 9 min | 17.5 | 0 | 675 ms |
| 10 min | 19.4 | 0 | 563 ms |
| 11 min | 1.0 | 0 | 112 ms |

##### Correctness after the run

- Approved twice: none; finally approved twice: none; exported twice: none.
- Approved without an approval event: none.
- Summary against a recomputation (2569 invoices: every one the run touched, and a sample): the same.
- Logins paused: none.
- Events in the run: {'downloaded': 127, 'exported': 57, 'final_approved': 109, 'import_run': 88, 'login': 100, 'logout': 4, 'page_reader_tested': 1, 'parked': 298, 'processed': 88, 'rejected': 18}; invoices now: {'approved': 95800, 'failed': 298, 'parked': 448, 'rejected': 1029, 'review': 2513}; waiting for OvisOCR2 at the end: 47.

##### What people did

- approvals not open: parked instead: 273
- export batches (jde): 2
- final approvals: 116
- invoices acted on by someone else meanwhile (409): 151
- invoices left with a draft: 39
- logins: 100
- parked: 323
- rejected: 18
- search exports downloaded: 127
- second approvals not theirs to give: 8
- static from the cache: 59870
- uploads: 88
- scenario admin: 57 done, 0 stopped by an error
- scenario batch: 90 done, 0 stopped by an error
- scenario bulk: 107 done, 0 stopped by an error
- scenario export_search: 127 done, 0 stopped by an error
- scenario look: 312 done, 0 stopped by an error
- scenario queue: 567 done, 0 stopped by an error
- scenario reports: 325 done, 0 stopped by an error
- scenario search: 460 done, 0 stopped by an error
- scenario second: 249 done, 0 stopped by an error
- scenario upload: 88 done, 0 stopped by an error
- scenario work: 1295 done, 0 stopped by an error
