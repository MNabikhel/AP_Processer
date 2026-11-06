# Load after phase 3 part 2: 100 people on 100,000 and 300,000 invoices (6 Nov 2026)

The same runs as phase 3's ([phase3-load-100-people.md](phase3-load-100-people.md)), on seeds made again by this code
(schema 27) and again before each 100,000-invoice run: `python -m ap_coder.bench load --users N --duration D --ramp R
--think 10 --workers 4` against the real `serve --server`, the stand-in model, Python 3.12, the 4 cores with nothing
else running, phase 3's reports as the baselines.

**In short: at 100,000 invoices, 100 people meet every target of target 1 for the first time.** At 300,000 they still
do not: the pages' median fell from 19.4 s to 13.8 s and the requests answered rose by a quarter, but the workers are
still asked for more than they give.

| run | requests | pages p50 / p95 / p99 | actions p95 | errors |
|---|---:|---|---:|---|
| 100k, 100 people, 600 s | 14,113 (phase 3: 13,912) | **175 ms / 1.15 s / 2.73 s** (366 ms / 4.81 s / 10.5 s) | **554 ms** (2.82 s) | 0 |
| 100k, 50 people, 300 s | 4,213 | 144 ms / 1.07 s / 3.14 s (148 ms / 1.43 s / 3.27 s) | 268 ms (333 ms) | 0 |
| 100k, 25 people, 300 s | 2,076 | 131 ms / 1.14 s / 3.05 s (144 ms / 1.32 s / 3.34 s) | 206 ms (263 ms) | 0 |
| 300k, 100 people, 600 s | 9,400 (7,489) | 13.8 s / 64.0 s / 95.2 s (19.4 s / 69.5 s / 91.3 s) | 63.4 s (71.8 s) | 58 5xx (27), 0 locked |

- **What moved at 100,000 (100 people, p95):** the queue 3.23 → 0.89 s, a check 2.94 → 0.60 s, an invoice's page
  3.26 → 1.01 s, Import invoices 8.93 → 0.55 s, Vendors 6.85 → 2.74 s, Spend 11.2 → 4.7 s, month-end 21.7 → 7.5 s
  (both still worked out once after each change, while the others asking wait for it).
- **The shorter runs' p99** (3.05-3.14 s at 25 and 50 people, 300 s each) is the first minutes' tail weighing more
  in a run half as long: the 600-second run at 100 people meets it (2.73 s). The gate's two "slower than the baseline"
  routes at 25 and 50 people are `/process/status` (p95 154-249 ms from 22-46 ms, 20-43 asks, median unchanged at
  8-10 ms) and the upload (n = 5-10): a few asks behind a long page in the same worker.
- **Workers:** 0.65-0.69 of a core each at 300,000 (phase 3: 0.74-0.78), 1.44-1.63 GB each after 10 minutes; the
  background process 2.1 GB.

## What still grows with the database: the profile at 300,000

py-spy of the two busiest web workers for 120 s, from the fourth minute (24,326 samples), by page:
month-end 22.2%, an invoice's page 16.8%, a check 15.7%, the queue 14.8%, Sales tax 6.3%, Learning 5.9%, Vendors 4.8%,
search 3.3%. Inside them:

- **Month-end's answer after a change (22%):** `accruals.build` → `_received_not_invoiced` 18.2%, `billing_codings`
  9.2%, `po.positive_lines` 4%: each invoice not yet in the ERP is decoded again whenever anything it depends on changed.
  Shared by every process since D-169, it is worked out once a change, but at 300,000 that is most of a core.
- **A vendor's older lessons (13%):** `_past_memory` read whole (`_past_read` 12.9%). A probe on the 300,000 seed,
  400 checks of the review queue's invoices with no write at all: 380 read the vendor's older lessons whole (3.1M rows,
  115 ms of CPU each). What each worker keeps of them (`PAST_KEPT` 32 vendors, `PAST_ROWS_KEPT` 60,000 rows, B-1719) holds
  22 vendors, while the queue's invoices come from 1,041. D-173's carry forward works for the vendors kept; at
  300,000 most are not.
- **The rest:** `fetch_rows` 32.5% self and JSON decoding 9.6%: rows read again and decoded, by these and by the
  invoice page's analysis (`analyse` 28%, suggestions 17%), Sales tax's return (`taxreturn.build` 3%), the Learning
  page's coding context (5.3%), the duplicate check's numbers (`find_duplicates` 3.3%).


## The report at 100,000 invoices, 100 people

### Load test: 100 people for 600 s on 4 worker(s)

2026-11-06T11:20:02; ramp 60 s, think 10.0 s (median), connections kept 5 s unused; 100000 invoices in /tmp/claude-0/-home-user/b4fef7c4-f9cb-52fc-9da2-47ad67a7ebc2/scratchpad/bench_data/S/s100k; models: ap_coder/bench/fake_model.py, 1-4 s an answer, 2 at once, at http://192.0.2.2:50655/v1; people reach the server at 192.0.2.2. 4 cores, Python 3.12.3, Linux-6.18.44-fc-v242-x86_64-with-glibc2.39.

#### Summary
- Requests: 14113 (21.38/s); 5xx 0, without an answer 0, cut off at the end 0, over 30 s 0; refused (4xx) 165.
- Pages: p50 175 ms, p95 1.15 s, p99 2.73 s (5599); actions (check, approve, park, reject, final approval): p50 79 ms, p95 554 ms (2488). Timed from when each step was due (scheduled start).
- Targets: pages p50 <= 0.3 s met, pages p95 <= 1.5 s met, pages p99 <= 3.0 s met, actions p95 <= 1.0 s met.
- Behind schedule: p50 1 ms, p95 21 ms.
- Harness CPU: 0.148 cores (its event loop 0.147): well under one core, so the figures are the server's. Server started in 3.67 s; its stop took 1.29 s (shutdown).
- Server logs: 0 'database is locked', 0 5xx of 14118 lines, 0 tracebacks.

#### Gate
- Passed.

#### Routes

| route | n | p50 | p95 | p99 | max | server p95 | statuses | refused | errors |
|---|---:|---:|---:|---:|---:|---:|---|---:|---:|
| GET /review | 2781 | 191 ms | 893 ms | 1.60 s | 4.92 s | 808 ms | 200: 2781 | 0 | 0 |
| POST /review/{id}/check | 2047 | 83 ms | 599 ms | 1.06 s | 3.99 s | 490 ms | 200: 1882, 409: 165 | 165 | 0 |
| GET /review/{id}/page/{n}.png | 1745 | 17 ms | 88 ms | 233 ms | 1.01 s | 50 ms | 200: 1745 | 0 | 0 |
| GET /static/{file} | 1615 | 12 ms | 59 ms | 116 ms | 498 ms | 0 ms | 200: 1615 | 0 | 0 |
| GET /review/{id} | 1287 | 173 ms | 1.01 s | 2.60 s | 4.66 s | 914 ms | 200: 1287 | 0 | 0 |
| GET /review/vendors.json | 856 | 26 ms | 102 ms | 224 ms | 1.42 s | 37 ms | 200: 856 | 0 | 0 |
| GET /search | 623 | 106 ms | 540 ms | 972 ms | 1.45 s | 508 ms | 200: 623 | 0 | 0 |
| GET /downloads/{id} | 497 | 19 ms | 133 ms | 501 ms | 782 ms | 66 ms | 200: 497 | 0 | 0 |
| GET /process/status | 375 | 17 ms | 224 ms | 533 ms | 635 ms | 166 ms | 200: 375 | 0 | 0 |
| POST /review/{id}/park | 313 | 74 ms | 410 ms | 916 ms | 1.22 s | 368 ms | 200: 313 | 0 | 0 |
| GET /assets/{file} | 200 | 20 ms | 50 ms | 56 ms | 83 ms | 0 ms | 200: 200 | 0 | 0 |
| GET /fonts/{file} | 200 | 3 ms | 12 ms | 21 ms | 189 ms | 0 ms | 200: 200 | 0 | 0 |
| GET /process | 176 | 98 ms | 553 ms | 1.44 s | 2.35 s | 500 ms | 200: 176 | 0 | 0 |
| GET /exports | 160 | 522 ms | 1.25 s | 1.85 s | 2.10 s | 1.24 s | 200: 160 | 0 | 0 |
| POST /review/{id}/final-approve | 113 | 36 ms | 366 ms | 533 ms | 560 ms | 129 ms | 200: 113 | 0 | 0 |
| GET /login | 100 | 8 ms | 53 ms | 1.29 s | 1.90 s | 27 ms | 200: 100 | 0 | 0 |
| GET /login/code | 100 | 49 ms | 88 ms | 204 ms | 209 ms | 26 ms | 200: 100 | 0 | 0 |
| POST /login | 100 | 64 ms | 493 ms | 607 ms | 626 ms | 477 ms | 200: 100 | 0 | 0 |
| POST /login/code | 100 | 14 ms | 290 ms | 510 ms | 557 ms | 142 ms | 200: 100 | 0 | 0 |
| GET /search/export.csv | 91 | 17 ms | 263 ms | 558 ms | 558 ms | 248 ms | 200: 91 | 0 | 0 |
| GET /search/export.csv?job={id} | 91 | 81 ms | 368 ms | 1.40 s | 1.40 s | - | 200: 91 | 0 | 0 |
| POST /process/upload | 88 | 40 ms | 312 ms | 849 ms | 849 ms | 285 ms | 200: 88 | 0 | 0 |
| GET /insights | 64 | 33 ms | 2.87 s | 5.36 s | 5.36 s | 2.86 s | 200: 64 | 0 | 0 |
| GET /learning | 59 | 315 ms | 2.04 s | 2.88 s | 2.88 s | 2.04 s | 200: 59 | 0 | 0 |
| GET /month-end | 57 | 457 ms | 7.52 s | 8.16 s | 8.16 s | 7.50 s | 200: 57 | 0 | 0 |
| GET /spend | 47 | 56 ms | 4.74 s | 5.09 s | 5.09 s | 4.73 s | 200: 47 | 0 | 0 |
| GET /vendors | 45 | 1.74 s | 2.74 s | 3.28 s | 3.28 s | 2.73 s | 200: 45 | 0 | 0 |
| GET /sales-tax | 39 | 1.34 s | 2.62 s | 2.81 s | 2.81 s | 2.52 s | 200: 39 | 0 | 0 |
| GET /purchase-orders | 32 | 139 ms | 660 ms | 738 ms | 738 ms | 563 ms | 200: 32 | 0 | 0 |
| GET /search/export.xlsx | 32 | 14 ms | 99 ms | 546 ms | 546 ms | 70 ms | 200: 32 | 0 | 0 |
| GET /search/export.xlsx?job={id} | 32 | 58 ms | 331 ms | 426 ms | 426 ms | - | 200: 32 | 0 | 0 |
| POST /review/{id}/reject | 15 | 86 ms | 818 ms | 818 ms | 818 ms | 786 ms | 200: 15 | 0 | 0 |
| GET /activity | 11 | 232 ms | 357 ms | 357 ms | 357 ms | 351 ms | 200: 11 | 0 | 0 |
| GET /admin/users | 11 | 50 ms | 752 ms | 752 ms | 752 ms | 747 ms | 200: 11 | 0 | 0 |
| GET /settings | 7 | 26 ms | 143 ms | 143 ms | 143 ms | 135 ms | 200: 7 | 0 | 0 |
| GET /review/{id}/reader | 2 | 15 ms | 26 ms | 26 ms | 26 ms | 19 ms | 200: 2 | 0 | 0 |
| POST /exports/create | 2 | 54 ms | 701 ms | 701 ms | 701 ms | 54 ms | 200: 2 | 0 | 0 |

#### Server processes

| process | CPU s | mean cores | peak cores | RSS MB first → last (peak) |
|---|---:|---:|---:|---|
| model | 0.9 | 0.0 | 0.02 | 22.1 → 22.4 (30.5) |
| server | 1.0 | 0.0 | 0.01 | 60.5 → 60.5 (60.5) |
| background | 207.2 | 0.31 | 1.28 | 200.0 → 458.1 (458.1) |
| server child | 82.7 | 0.13 | 1.0 | 131.6 → 327.5 (327.6) |
| server child | 75.0 | 0.11 | 0.98 | 131.9 → 327.3 (327.4) |
| resource tracker | 0.0 | 0.0 | 0.0 | 13.3 → 13.3 (13.3) |
| worker 1 | 317.4 | 0.48 | 1.6 | 125.7 → 890.6 (909.3) |
| worker 2 | 290.3 | 0.44 | 1.22 | 128.5 → 968.5 (968.5) |
| worker 3 | 349.1 | 0.53 | 1.38 | 125.8 → 1019.0 (1019.0) |
| worker 4 | 279.1 | 0.42 | 1.24 | 124.2 → 932.7 (932.7) |

#### Over time (a row a minute)

| from | requests/s | errors | p95 (worst 10 s) |
|---:|---:|---:|---:|
| 0 min | 33.6 | 0 | 410 ms |
| 1 min | 28.1 | 0 | 1.59 s |
| 2 min | 23.1 | 0 | 821 ms |
| 3 min | 20.9 | 0 | 729 ms |
| 4 min | 19.6 | 0 | 1.18 s |
| 5 min | 19.7 | 0 | 1.14 s |
| 6 min | 20.1 | 0 | 1.21 s |
| 7 min | 16.8 | 0 | 1.31 s |
| 8 min | 18.1 | 0 | 817 ms |
| 9 min | 16.7 | 0 | 709 ms |
| 10 min | 18.4 | 0 | 1.09 s |
| 11 min | 4.0 | 0 | 2.20 s |

#### Correctness after the run

- Approved twice: none; finally approved twice: none; exported twice: none.
- Approved without an approval event: none.
- Summary against a recomputation (2558 invoices: every one the run touched, and a sample): the same.
- Logins paused: none.
- Events in the run: {'downloaded': 123, 'exported': 57, 'final_approved': 109, 'import_run': 88, 'login': 100, 'page_reader_tested': 1, 'parked': 289, 'processed': 88, 'rejected': 15}; invoices now: {'approved': 95800, 'failed': 298, 'parked': 440, 'rejected': 1026, 'review': 2524}; waiting for OvisOCR2 at the end: 49.

#### What people did

- approvals not open: parked instead: 269
- export batches (jde): 2
- final approvals: 113
- invoices acted on by someone else meanwhile (409): 165
- invoices left with a draft: 33
- logins: 100
- parked: 313
- rejected: 15
- search exports downloaded: 123
- second approvals not theirs to give: 16
- static from the cache: 60389
- uploads: 88
- scenario admin: 50 done, 0 stopped by an error
- scenario batch: 119 done, 0 stopped by an error
- scenario bulk: 95 done, 0 stopped by an error
- scenario export_search: 123 done, 0 stopped by an error
- scenario look: 304 done, 0 stopped by an error
- scenario queue: 582 done, 0 stopped by an error
- scenario reports: 361 done, 0 stopped by an error
- scenario search: 497 done, 0 stopped by an error
- scenario second: 251 done, 0 stopped by an error
- scenario upload: 88 done, 0 stopped by an error
- scenario work: 1286 done, 0 stopped by an error

## The report at 300,000 invoices, 100 people

### Load test: 100 people for 600 s on 4 worker(s)

2026-11-06T11:04:30; ramp 60 s, think 10.0 s (median), connections kept 5 s unused; 300000 invoices in /tmp/claude-0/-home-user/b4fef7c4-f9cb-52fc-9da2-47ad67a7ebc2/scratchpad/bench_data/S/s300k; models: ap_coder/bench/fake_model.py, 1-4 s an answer, 2 at once, at http://192.0.2.2:50917/v1; people reach the server at 192.0.2.2. 4 cores, Python 3.12.3, Linux-6.18.44-fc-v242-x86_64-with-glibc2.39.

#### Summary
- Requests: 9400 (14.24/s); 5xx 58, without an answer 11, cut off at the end 0, over 30 s 1773; refused (4xx) 92.
- Pages: p50 13.83 s, p95 64.02 s, p99 95.21 s (3342); actions (check, approve, park, reject, final approval): p50 27.51 s, p95 63.40 s (1396). Timed from when each step was due (scheduled start).
- Targets: pages p50 <= 0.3 s NOT met, pages p95 <= 1.5 s NOT met, pages p99 <= 3.0 s NOT met, actions p95 <= 1.0 s NOT met.
- Behind schedule: p50 9.25 s, p95 51.02 s.
- Harness CPU: 0.086 cores (its event loop 0.084): well under one core, so the figures are the server's. Server started in 3.04 s; its stop took 12.75 s (shutdown).
- Server logs: 0 'database is locked', 58 5xx of 9405 lines, 0 tracebacks.

#### Gate
- errors: 58 5xx, 11 without an answer, 0 cut off at the end, 1773 over 30 s
- server logs: 0 'database is locked', 58 5xx

#### Routes

| route | n | p50 | p95 | p99 | max | server p95 | statuses | refused | errors |
|---|---:|---:|---:|---:|---:|---:|---|---:|---:|
| GET /review | 1625 | 12.86 s | 58.79 s | 76.16 s | 115.35 s | 24.04 s | 200: 1625 | 0 | 464 |
| GET /static/{file} | 1523 | 13 ms | 72 ms | 202 ms | 1.13 s | 2 ms | 200: 1523 | 0 | 0 |
| POST /review/{id}/check | 1119 | 27.95 s | 63.40 s | 79.15 s | 116.96 s | 25.50 s | 200: 1028, 409: 91 | 91 | 522 |
| GET /review/{id}/page/{n}.png | 1047 | 2.59 s | 21.08 s | 58.25 s | 75.48 s | 21.11 s | 200: 1047 | 0 | 28 |
| GET /review/{id} | 764 | 16.50 s | 62.93 s | 78.60 s | 106.39 s | 33.24 s | 200: 764 | 0 | 238 |
| GET /process/status | 634 | 979 ms | 18.95 s | 44.54 s | 75.48 s | 18.94 s | 200: 634 | 0 | 12 |
| GET /review/vendors.json | 453 | 2.70 s | 22.15 s | 59.92 s | 76.09 s | 22.10 s | 200: 453 | 0 | 11 |
| GET /search | 316 | 18.64 s | 62.47 s | 81.30 s | 116.18 s | 24.19 s | 200: 316 | 0 | 106 |
| GET /assets/{file} | 200 | 28 ms | 52 ms | 55 ms | 65 ms | 0 ms | 200: 200 | 0 | 0 |
| GET /fonts/{file} | 200 | 3 ms | 13 ms | 24 ms | 38 ms | 0 ms | 200: 200 | 0 | 0 |
| GET /downloads/{id} | 164 | 807 ms | 17.70 s | 33.71 s | 44.23 s | 17.67 s | 200: 164 | 0 | 4 |
| POST /review/{id}/park | 157 | 25.58 s | 61.68 s | 78.95 s | 113.00 s | 19.74 s | 200: 157 | 0 | 71 |
| GET /exports | 123 | 12.62 s | 71.45 s | 84.48 s | 123.67 s | 38.26 s | 200: 100, 503: 23 | 0 | 65 |
| POST /review/{id}/final-approve | 111 | 24.27 s | 65.58 s | 82.21 s | 100.15 s | 20.18 s | 200: 111 | 0 | 45 |
| GET /process | 108 | 8.46 s | 55.33 s | 67.66 s | 76.76 s | 28.52 s | 200: 108 | 0 | 17 |
| GET /login | 100 | 8 ms | 418 ms | 2.24 s | 2.84 s | 39 ms | 200: 100 | 0 | 0 |
| GET /login/code | 100 | 48 ms | 71 ms | 305 ms | 320 ms | 19 ms | 200: 100 | 0 | 0 |
| POST /login | 100 | 90 ms | 609 ms | 784 ms | 914 ms | 601 ms | 200: 100 | 0 | 0 |
| POST /login/code | 100 | 49 ms | 251 ms | 795 ms | 999 ms | 223 ms | 200: 100 | 0 | 0 |
| GET /search/export.csv | 56 | 18.23 s | 62.02 s | 91.17 s | 91.17 s | 171.57 s | 200: 56 | 0 | 17 |
| POST /process/upload | 54 | 15.86 s | 57.52 s | 101.96 s | 101.96 s | 25.23 s | 200: 54 | 0 | 17 |
| GET /search/export.csv?job={id} | 51 | 31.41 s | 245.39 s | 380.36 s | 380.36 s | - | 200: 51 | 0 | 27 |
| POST /exports/create | 46 | 16.31 s | 52.72 s | 70.98 s | 70.98 s | 33.16 s | 200: 39, 400: 1, 503: 6 | 1 | 22 |
| GET /month-end | 31 | 89.36 s | 160.58 s | 165.60 s | 165.60 s | 234.81 s | 200: 19, 503: 5 | 0 | 36 |
| GET /vendors | 29 | 28.55 s | 65.37 s | 65.52 s | 65.52 s | 33.67 s | 200: 29 | 0 | 12 |
| GET /insights | 28 | 32.14 s | 99.18 s | 101.27 s | 101.27 s | 52.76 s | 200: 23, 503: 5 | 0 | 21 |
| GET /spend | 28 | 57.29 s | 99.62 s | 105.10 s | 105.10 s | 66.86 s | 200: 20, 503: 8 | 0 | 31 |
| GET /sales-tax | 27 | 34.22 s | 104.97 s | 110.78 s | 110.78 s | 51.76 s | 200: 20, 503: 7 | 0 | 24 |
| GET /learning | 26 | 35.97 s | 126.26 s | 127.65 s | 127.65 s | 161.81 s | 200: 22 | 0 | 16 |
| GET /purchase-orders | 18 | 27.53 s | 64.02 s | 64.02 s | 64.02 s | 61.47 s | 200: 18 | 0 | 9 |
| GET /exports/batches/{id}/download | 14 | 15.92 s | 68.02 s | 68.02 s | 68.02 s | 32.88 s | 200: 12, 503: 2 | 0 | 7 |
| GET /search/export.xlsx | 10 | 5.85 s | 33.74 s | 33.74 s | 33.74 s | 188.39 s | 200: 10 | 0 | 1 |
| GET /search/export.xlsx?job={id} | 10 | 29.48 s | 303.53 s | 303.53 s | 303.53 s | - | 200: 10 | 0 | 5 |
| POST /review/{id}/reject | 9 | 29.82 s | 50.77 s | 50.77 s | 50.77 s | 29.48 s | 200: 9 | 0 | 4 |
| GET /settings | 7 | 14.43 s | 58.14 s | 58.14 s | 58.14 s | 11.77 s | 200: 7 | 0 | 3 |
| GET /activity | 6 | 30.14 s | 71.60 s | 71.60 s | 71.60 s | 38.06 s | 200: 4, 503: 2 | 0 | 6 |
| GET /admin/users | 6 | 7.88 s | 88.24 s | 88.24 s | 88.24 s | 36.86 s | 200: 6 | 0 | 1 |

#### Server processes

| process | CPU s | mean cores | peak cores | RSS MB first → last (peak) |
|---|---:|---:|---:|---|
| model | 1.0 | 0.0 | 0.02 | 22.5 → 18.5 (31.0) |
| server | 0.9 | 0.0 | 0.01 | 61.3 → 53.7 (61.3) |
| server child | 155.0 | 0.21 | 1.0 | 133.5 → 185.5 (294.0) |
| background | 228.8 | 0.32 | 1.17 | 211.0 → 2102.9 (2102.9) |
| server child | 145.1 | 0.2 | 0.99 | 133.4 → 183.0 (294.0) |
| resource tracker | 0.0 | 0.0 | 0.0 | 13.5 → 11.9 (13.5) |
| worker 1 | 466.1 | 0.65 | 1.54 | 128.5 → 1442.8 (1563.9) |
| worker 2 | 466.3 | 0.65 | 1.44 | 125.5 → 1521.6 (1632.8) |
| worker 3 | 487.1 | 0.68 | 1.76 | 128.2 → 1442.1 (1582.2) |
| worker 4 | 498.1 | 0.69 | 1.62 | 125.7 → 1438.2 (1444.7) |

#### Over time (a row a minute)

| from | requests/s | errors | p95 (worst 10 s) |
|---:|---:|---:|---:|
| 0 min | 33.3 | 0 | 661 ms |
| 1 min | 20.7 | 9 | 38.30 s |
| 2 min | 9.5 | 12 | 120.01 s |
| 3 min | 6.3 | 13 | 135.81 s |
| 4 min | 8.1 | 13 | 106.39 s |
| 5 min | 14.5 | 1 | 57.69 s |
| 6 min | 12.2 | 1 | 66.32 s |
| 7 min | 11.8 | 3 | 62.78 s |
| 8 min | 10.7 | 9 | 75.64 s |
| 9 min | 9.9 | 5 | 71.94 s |
| 10 min | 12.9 | 0 | 67.09 s |
| 11 min | 7.2 | 3 | 177.65 s |

#### Correctness after the run

- Approved twice: none; finally approved twice: none; exported twice: none.
- Approved without an approval event: none.
- Summary against a recomputation (3459 invoices: every one the run touched, and a sample): the same.
- Logins paused: none.
- Events in the run: {'downloaded': 73, 'exported': 1250, 'final_approved': 98, 'import_run': 54, 'login': 100, 'page_reader_tested': 1, 'parked': 148, 'processed': 54, 'rejected': 9}; invoices now: {'approved': 293357, 'failed': 890, 'parked': 271, 'rejected': 2860, 'review': 2676}; waiting for OvisOCR2 at the end: 34.

#### What people did

- approvals not open: parked instead: 137
- export batches (csv): 37
- export batches (jde): 2
- export batches refused: 1
- final approvals: 111
- invoices acted on by someone else meanwhile (409): 91
- invoices left with a draft: 21
- logins: 100
- parked: 157
- rejected: 9
- schedule restarted (over a minute behind): 325
- search exports downloaded: 61
- search exports still being made when left: 5
- second approvals not theirs to give: 10
- static from the cache: 34324
- uploads: 54
- scenario admin: 24 done, 3 stopped by an error
- scenario batch: 37 done, 26 stopped by an error
- scenario bulk: 55 done, 0 stopped by an error
- scenario export_search: 66 done, 0 stopped by an error
- scenario look: 189 done, 0 stopped by an error
- scenario queue: 306 done, 0 stopped by an error
- scenario reports: 158 done, 40 stopped by an error
- scenario search: 248 done, 0 stopped by an error
- scenario second: 136 done, 0 stopped by an error
- scenario upload: 54 done, 0 stopped by an error
- scenario work: 671 done, 0 stopped by an error
