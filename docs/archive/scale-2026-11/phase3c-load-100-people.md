# Load after phase 3 part 3: 100 people on 100,000 and 300,000 invoices (6 Nov 2026)

The same runs as part 2's ([phase3b-load-100-people.md](phase3b-load-100-people.md)), on seeds made again by this code
(schema 27): 100 people for 600 s against the real `serve --server --workers 4`, the stand-in model, Python 3.12, the 4
cores with nothing else running, part 2's reports as the baselines.

**In short: at 100,000 invoices every target of target 1 is met with room to spare; at 300,000 the pages' median fell
again, from 13.8 s to 4.2 s, and the server answered 45% more, but it is still far from the targets.**

| run | requests | pages p50 / p95 / p99 | actions p95 | errors |
|---|---:|---|---:|---|
| 100k, 100 people | 14,108 (part 2: 14,113) | **136 ms / 673 ms / 1.84 s** (175 ms / 1.15 s / 2.73 s) | **348 ms** (554 ms) | 0 |
| 300k, 100 people | 13,658 (9,400) | 4.17 s / 46.1 s / 66.2 s (13.8 s / 64.0 s / 95.2 s) | 47.4 s (63.4 s) | 96 503s (58), 0 locked |

- **At 100,000 (p95):** the queue 893 → 576 ms, a check 599 → 358 ms, an invoice's page 1.01 s → 524 ms, search 540 →
  460 ms, month-end 7.5 → 4.75 s, Spend 4.7 → 4.4 s (both still worked out by the first asker after a change).
- **At 300,000:** the workers did 0.60-0.67 of a core each (not saturated) and reached 1.65-1.77 GB; the 96 503s are the heavy
  lane full (the exports page 45, Insights 17, month-end 12, Spend 10, Sales tax 9, the rest 3). The reports wait longest: month-end p50 33 s, Spend 32 s, Sales tax
  17 s, Learning 8.7 s, Vendors 15 s.

## What still grows with the database: the profile at 300,000

py-spy of the two busiest web workers for 120 s from the fourth minute (19,104 samples), by page: the queue 25%,
month-end 15.5%, a check 13.9%, an invoice's page 9.5%, search 7.7%, Vendors 7.4%, Insights 3.2%, Learning 3.1%, Sales
tax 2.4%. Inside them:

- **The queue (25%):** `summary_rows` 8.2% and `list_invoices` 6.9%, `attention` 5.9%, `queue_checks` 4.8%: its lists
  and checks are worked out again after each change, and at 300,000 the queue's invoices (about 2,700) and its vendors
  (about 1,000) are each bigger.
- **Month-end (15.5%):** `billing_lines` 10.3% (D-177: each vendor's row read and decoded, `billing_moment` 4.8%) and
  `keep_answer` 4.9% (writing the biggest vendors' rows back, up to 7 MB each): the per-invoice lines no longer decode
  every invoice, but each vendor's whole row is still read and written as one.
- **Search (7.7%):** `erp_hits` 4.6%: the quick search still scores every ERP register row (600,000 at 300k).
- **Vendors (7.4%):** `vendor_workload` 4.2%, each worker's own.
- **Still everywhere:** `fetch_rows` 30.5% inclusive: rows read and made into dicts.


## The report at 100,000 invoices, 100 people

### Load test: 100 people for 600 s on 4 worker(s)

2026-11-06T14:25:57; ramp 60 s, think 10.0 s (median), connections kept 5 s unused; 100000 invoices in /tmp/claude-0/-home-user/b4fef7c4-f9cb-52fc-9da2-47ad67a7ebc2/scratchpad/bench_data/S/s100k; models: ap_coder/bench/fake_model.py, 1-4 s an answer, 2 at once, at http://192.0.2.2:37277/v1; people reach the server at 192.0.2.2. 4 cores, Python 3.12.3, Linux-6.18.44-fc-v242-x86_64-with-glibc2.39.

#### Summary
- Requests: 14108 (21.38/s); 5xx 0, without an answer 0, cut off at the end 0, over 30 s 0; refused (4xx) 144.
- Pages: p50 136 ms, p95 673 ms, p99 1.84 s (5609); actions (check, approve, park, reject, final approval): p50 53 ms, p95 348 ms (2425). Timed from when each step was due (scheduled start).
- Targets: pages p50 <= 0.3 s met, pages p95 <= 1.5 s met, pages p99 <= 3.0 s met, actions p95 <= 1.0 s met.
- Behind schedule: p50 1 ms, p95 16 ms.
- Harness CPU: 0.147 cores (its event loop 0.145): well under one core, so the figures are the server's. Server started in 3.04 s; its stop took 1.11 s (shutdown).
- Server logs: 0 'database is locked', 0 5xx of 14113 lines, 0 tracebacks.

#### Gate
- GET /activity: p95 0.732 s, baseline 0.357 s (2.1x)
- GET /search/export.xlsx: p95 0.239 s, baseline 0.099 s (2.4x)

#### Routes

| route | n | p50 | p95 | p99 | max | server p95 | statuses | refused | errors |
|---|---:|---:|---:|---:|---:|---:|---|---:|---:|
| GET /review | 2782 | 164 ms | 576 ms | 1.11 s | 8.87 s | 541 ms | 200: 2782 | 0 | 0 |
| POST /review/{id}/check | 1980 | 59 ms | 358 ms | 760 ms | 2.43 s | 304 ms | 200: 1836, 409: 144 | 144 | 0 |
| GET /review/{id}/page/{n}.png | 1731 | 15 ms | 76 ms | 229 ms | 1.06 s | 40 ms | 200: 1731 | 0 | 0 |
| GET /static/{file} | 1605 | 9 ms | 52 ms | 66 ms | 288 ms | 0 ms | 200: 1605 | 0 | 0 |
| GET /review/{id} | 1288 | 104 ms | 524 ms | 1.16 s | 3.93 s | 480 ms | 200: 1288 | 0 | 0 |
| GET /review/vendors.json | 845 | 25 ms | 86 ms | 202 ms | 771 ms | 24 ms | 200: 845 | 0 | 0 |
| GET /search | 641 | 91 ms | 460 ms | 814 ms | 3.63 s | 414 ms | 200: 641 | 0 | 0 |
| GET /downloads/{id} | 551 | 16 ms | 133 ms | 438 ms | 1.07 s | 70 ms | 200: 551 | 0 | 0 |
| GET /process/status | 377 | 13 ms | 135 ms | 280 ms | 484 ms | 72 ms | 200: 377 | 0 | 0 |
| POST /review/{id}/park | 304 | 25 ms | 253 ms | 480 ms | 604 ms | 180 ms | 200: 304 | 0 | 0 |
| GET /assets/{file} | 200 | 31 ms | 49 ms | 53 ms | 74 ms | 0 ms | 200: 200 | 0 | 0 |
| GET /fonts/{file} | 200 | 3 ms | 12 ms | 116 ms | 157 ms | 0 ms | 200: 200 | 0 | 0 |
| GET /process | 197 | 91 ms | 414 ms | 1.18 s | 1.61 s | 354 ms | 200: 197 | 0 | 0 |
| GET /exports | 157 | 457 ms | 994 ms | 1.88 s | 1.90 s | 989 ms | 200: 157 | 0 | 0 |
| POST /review/{id}/final-approve | 120 | 21 ms | 164 ms | 457 ms | 540 ms | 119 ms | 200: 120 | 0 | 0 |
| GET /login | 100 | 7 ms | 41 ms | 1.18 s | 1.79 s | 17 ms | 200: 100 | 0 | 0 |
| GET /login/code | 100 | 48 ms | 62 ms | 103 ms | 342 ms | 16 ms | 200: 100 | 0 | 0 |
| POST /login | 100 | 62 ms | 273 ms | 489 ms | 501 ms | 247 ms | 200: 100 | 0 | 0 |
| POST /login/code | 100 | 12 ms | 131 ms | 497 ms | 650 ms | 100 ms | 200: 100 | 0 | 0 |
| POST /process/upload | 98 | 34 ms | 384 ms | 1.04 s | 1.04 s | 287 ms | 200: 98 | 0 | 0 |
| GET /search/export.csv | 88 | 16 ms | 211 ms | 578 ms | 578 ms | 253 ms | 200: 88 | 0 | 0 |
| GET /search/export.csv?job={id} | 88 | 60 ms | 414 ms | 603 ms | 603 ms | - | 200: 88 | 0 | 0 |
| GET /spend | 57 | 38 ms | 4.38 s | 4.79 s | 4.79 s | 4.36 s | 200: 57 | 0 | 0 |
| GET /month-end | 53 | 332 ms | 4.75 s | 11.24 s | 11.24 s | 4.74 s | 200: 53 | 0 | 0 |
| GET /insights | 52 | 48 ms | 1.87 s | 2.97 s | 2.97 s | 1.86 s | 200: 52 | 0 | 0 |
| GET /learning | 51 | 66 ms | 1.01 s | 1.84 s | 1.84 s | 1.01 s | 200: 51 | 0 | 0 |
| GET /search/export.xlsx | 43 | 15 ms | 239 ms | 623 ms | 623 ms | 83 ms | 200: 43 | 0 | 0 |
| GET /search/export.xlsx?job={id} | 43 | 57 ms | 162 ms | 329 ms | 329 ms | - | 200: 43 | 0 | 0 |
| GET /vendors | 41 | 1.58 s | 2.19 s | 2.77 s | 2.77 s | 2.18 s | 200: 41 | 0 | 0 |
| GET /sales-tax | 36 | 64 ms | 1.65 s | 1.76 s | 1.76 s | 1.64 s | 200: 36 | 0 | 0 |
| GET /purchase-orders | 25 | 121 ms | 580 ms | 638 ms | 638 ms | 574 ms | 200: 25 | 0 | 0 |
| POST /review/{id}/reject | 21 | 32 ms | 305 ms | 360 ms | 360 ms | 205 ms | 200: 21 | 0 | 0 |
| GET /admin/users | 11 | 43 ms | 114 ms | 114 ms | 114 ms | 93 ms | 200: 11 | 0 | 0 |
| GET /activity | 10 | 181 ms | 732 ms | 732 ms | 732 ms | 721 ms | 200: 10 | 0 | 0 |
| GET /settings | 8 | 24 ms | 413 ms | 413 ms | 413 ms | 373 ms | 200: 8 | 0 | 0 |
| GET /review/{id}/reader | 3 | 11 ms | 11 ms | 11 ms | 11 ms | 8 ms | 200: 3 | 0 | 0 |
| POST /exports/create | 2 | 36 ms | 907 ms | 907 ms | 907 ms | 402 ms | 200: 2 | 0 | 0 |

#### Server processes

| process | CPU s | mean cores | peak cores | RSS MB first → last (peak) |
|---|---:|---:|---:|---|
| model | 0.9 | 0.0 | 0.01 | 30.2 → 22.2 (30.3) |
| server | 1.0 | 0.0 | 0.01 | 60.8 → 60.8 (60.8) |
| background | 204.7 | 0.31 | 1.32 | 209.9 → 459.8 (459.8) |
| server child | 93.7 | 0.14 | 0.99 | 132.2 → 328.3 (328.3) |
| server child | 80.7 | 0.12 | 0.96 | 132.4 → 328.1 (328.1) |
| resource tracker | 0.0 | 0.0 | 0.0 | 13.4 → 13.4 (13.4) |
| worker 1 | 270.5 | 0.41 | 1.41 | 126.4 → 895.7 (895.7) |
| worker 2 | 231.1 | 0.35 | 1.19 | 127.5 → 875.4 (875.4) |
| worker 3 | 216.6 | 0.33 | 1.26 | 127.8 → 875.9 (875.9) |
| worker 4 | 295.9 | 0.45 | 1.72 | 129.9 → 843.2 (851.6) |

#### Over time (a row a minute)

| from | requests/s | errors | p95 (worst 10 s) |
|---:|---:|---:|---:|
| 0 min | 33.6 | 0 | 497 ms |
| 1 min | 28.3 | 0 | 879 ms |
| 2 min | 22.2 | 0 | 582 ms |
| 3 min | 20.0 | 0 | 768 ms |
| 4 min | 20.7 | 0 | 661 ms |
| 5 min | 19.1 | 0 | 542 ms |
| 6 min | 18.1 | 0 | 802 ms |
| 7 min | 18.8 | 0 | 699 ms |
| 8 min | 18.2 | 0 | 905 ms |
| 9 min | 17.4 | 0 | 510 ms |
| 10 min | 18.6 | 0 | 712 ms |
| 11 min | 4.0 | 0 | 427 ms |

#### Correctness after the run

- Approved twice: none; finally approved twice: none; exported twice: none.
- Approved without an approval event: none.
- Summary against a recomputation (2564 invoices: every one the run touched, and a sample): the same.
- Logins paused: none.
- Events in the run: {'downloaded': 131, 'exported': 57, 'final_approved': 109, 'import_run': 98, 'login': 100, 'page_reader_tested': 1, 'parked': 282, 'processed': 98, 'rejected': 21}; invoices now: {'approved': 95800, 'failed': 298, 'parked': 430, 'rejected': 1032, 'review': 2538}; waiting for OvisOCR2 at the end: 57.

#### What people did

- approvals not open: parked instead: 272
- export batches (jde): 2
- final approvals: 120
- invoices acted on by someone else meanwhile (409): 144
- invoices left with a draft: 34
- logins: 100
- parked: 304
- rejected: 21
- search exports downloaded: 131
- second approvals not theirs to give: 8
- static from the cache: 60520
- uploads: 98
- scenario admin: 50 done, 0 stopped by an error
- scenario batch: 113 done, 0 stopped by an error
- scenario bulk: 103 done, 0 stopped by an error
- scenario export_search: 131 done, 0 stopped by an error
- scenario look: 313 done, 0 stopped by an error
- scenario queue: 574 done, 0 stopped by an error
- scenario reports: 336 done, 0 stopped by an error
- scenario search: 508 done, 0 stopped by an error
- scenario second: 236 done, 0 stopped by an error
- scenario upload: 98 done, 0 stopped by an error
- scenario work: 1279 done, 0 stopped by an error

## The report at 300,000 invoices, 100 people

### Load test: 100 people for 600 s on 4 worker(s)

2026-11-06T14:11:46; ramp 60 s, think 10.0 s (median), connections kept 5 s unused; 300000 invoices in /tmp/claude-0/-home-user/b4fef7c4-f9cb-52fc-9da2-47ad67a7ebc2/scratchpad/bench_data/S/s300k; models: ap_coder/bench/fake_model.py, 1-4 s an answer, 2 at once, at http://192.0.2.2:53833/v1; people reach the server at 192.0.2.2. 4 cores, Python 3.12.3, Linux-6.18.44-fc-v242-x86_64-with-glibc2.39.

#### Summary
- Requests: 13658 (20.69/s); 5xx 96, without an answer 9, cut off at the end 0, over 30 s 957; refused (4xx) 120.
- Pages: p50 4.17 s, p95 46.07 s, p99 66.19 s (4643); actions (check, approve, park, reject, final approval): p50 9.88 s, p95 47.42 s (1999). Timed from when each step was due (scheduled start).
- Targets: pages p50 <= 0.3 s NOT met, pages p95 <= 1.5 s NOT met, pages p99 <= 3.0 s NOT met, actions p95 <= 1.0 s NOT met.
- Behind schedule: p50 279 ms, p95 39.11 s.
- Harness CPU: 0.122 cores (its event loop 0.12): well under one core, so the figures are the server's. Server started in 3.81 s; its stop took 8.6 s (shutdown).
- Server logs: 0 'database is locked', 96 5xx of 13661 lines, 0 tracebacks.

#### Gate
- errors: 96 5xx, 9 without an answer, 0 cut off at the end, 957 over 30 s
- server logs: 0 'database is locked', 96 5xx
- GET /login/code: p95 0.124 s, baseline 0.071 s (1.7x)
- GET /search/export.xlsx: p95 52.721 s, baseline 33.741 s (1.6x)
- POST /login: p95 0.918 s, baseline 0.609 s (1.5x)
- POST /login/code: p95 0.436 s, baseline 0.251 s (1.7x)

#### Routes

| route | n | p50 | p95 | p99 | max | server p95 | statuses | refused | errors |
|---|---:|---:|---:|---:|---:|---:|---|---:|---:|
| GET /review | 2305 | 3.73 s | 41.32 s | 60.26 s | 89.80 s | 13.41 s | 200: 2305 | 0 | 227 |
| GET /process/status | 2167 | 334 ms | 5.88 s | 11.81 s | 48.21 s | 5.79 s | 200: 2167 | 0 | 2 |
| POST /review/{id}/check | 1631 | 10.09 s | 47.62 s | 64.89 s | 90.82 s | 12.16 s | 200: 1513, 409: 118 | 118 | 271 |
| GET /static/{file} | 1553 | 17 ms | 107 ms | 323 ms | 1.78 s | 4 ms | 200: 1553 | 0 | 0 |
| GET /review/{id}/page/{n}.png | 1423 | 656 ms | 10.51 s | 26.59 s | 54.47 s | 10.48 s | 200: 1423 | 0 | 13 |
| GET /review/{id} | 1067 | 4.96 s | 45.71 s | 62.55 s | 84.20 s | 17.31 s | 200: 1067 | 0 | 116 |
| GET /review/vendors.json | 681 | 783 ms | 10.68 s | 30.58 s | 54.61 s | 10.59 s | 200: 681 | 0 | 7 |
| GET /search | 462 | 5.63 s | 47.82 s | 64.83 s | 90.01 s | 12.17 s | 200: 462 | 0 | 58 |
| GET /downloads/{id} | 283 | 356 ms | 9.14 s | 40.88 s | 58.92 s | 9.12 s | 200: 283 | 0 | 5 |
| POST /review/{id}/park | 239 | 8.71 s | 46.37 s | 59.85 s | 76.19 s | 10.22 s | 200: 239 | 0 | 34 |
| GET /assets/{file} | 200 | 32 ms | 52 ms | 263 ms | 283 ms | 0 ms | 200: 200 | 0 | 0 |
| GET /fonts/{file} | 200 | 4 ms | 17 ms | 36 ms | 132 ms | 4 ms | 200: 200 | 0 | 0 |
| GET /exports | 165 | 5.35 s | 57.37 s | 74.01 s | 81.78 s | 33.82 s | 200: 127, 503: 38 | 0 | 84 |
| GET /process | 146 | 2.66 s | 29.88 s | 59.13 s | 60.30 s | 10.54 s | 200: 146 | 0 | 7 |
| POST /review/{id}/final-approve | 108 | 10.09 s | 53.08 s | 61.46 s | 69.19 s | 11.10 s | 200: 108 | 0 | 21 |
| GET /login | 100 | 9 ms | 318 ms | 2.09 s | 2.69 s | 53 ms | 200: 100 | 0 | 0 |
| GET /login/code | 100 | 51 ms | 124 ms | 308 ms | 346 ms | 35 ms | 200: 100 | 0 | 0 |
| POST /login | 100 | 98 ms | 918 ms | 1.18 s | 1.27 s | 702 ms | 200: 100 | 0 | 0 |
| POST /login/code | 100 | 41 ms | 436 ms | 898 ms | 975 ms | 261 ms | 200: 100 | 0 | 0 |
| POST /process/upload | 73 | 7.64 s | 54.90 s | 64.77 s | 64.77 s | 9.63 s | 200: 73 | 0 | 10 |
| GET /search/export.csv | 60 | 6.53 s | 47.86 s | 62.94 s | 62.94 s | 38.82 s | 200: 60 | 0 | 9 |
| GET /search/export.csv?job={id} | 58 | 3.72 s | 67.99 s | 111.41 s | 111.41 s | - | 200: 58 | 0 | 7 |
| POST /exports/create | 54 | 4.33 s | 57.15 s | 84.16 s | 84.16 s | 30.40 s | 200: 45, 400: 2, 503: 7 | 2 | 18 |
| GET /insights | 50 | 22.06 s | 60.03 s | 88.38 s | 88.38 s | 33.95 s | 200: 33, 503: 17 | 0 | 41 |
| GET /learning | 44 | 8.70 s | 94.97 s | 120.09 s | 120.09 s | 94.96 s | 200: 43 | 0 | 10 |
| GET /sales-tax | 43 | 17.23 s | 72.40 s | 85.95 s | 85.95 s | 33.39 s | 200: 34, 503: 9 | 0 | 22 |
| GET /month-end | 41 | 32.76 s | 120.08 s | 148.49 s | 148.49 s | 354.71 s | 200: 21, 503: 12 | 0 | 38 |
| GET /vendors | 34 | 15.38 s | 50.31 s | 58.64 s | 58.64 s | 21.08 s | 200: 34 | 0 | 3 |
| GET /spend | 32 | 32.36 s | 70.33 s | 93.24 s | 93.24 s | 51.08 s | 200: 22, 503: 10 | 0 | 32 |
| GET /purchase-orders | 30 | 4.18 s | 51.07 s | 87.83 s | 87.83 s | 8.28 s | 200: 30 | 0 | 4 |
| GET /search/export.xlsx | 28 | 6.66 s | 52.72 s | 54.80 s | 54.80 s | 86.14 s | 200: 28 | 0 | 4 |
| GET /search/export.xlsx?job={id} | 27 | 4.50 s | 87.53 s | 89.44 s | 89.44 s | - | 200: 27 | 0 | 7 |
| POST /review/{id}/reject | 21 | 4.87 s | 24.99 s | 33.57 s | 33.57 s | 8.52 s | 200: 21 | 0 | 1 |
| GET /activity | 10 | 10.79 s | 47.46 s | 47.46 s | 47.46 s | 36.32 s | 200: 9, 503: 1 | 0 | 4 |
| GET /exports/batches/{id}/download | 9 | 15.29 s | 118.83 s | 118.83 s | 118.83 s | 67.39 s | 200: 7, 503: 2 | 0 | 5 |
| GET /admin/users | 7 | 1.38 s | 6.64 s | 6.64 s | 6.64 s | 6.61 s | 200: 7 | 0 | 0 |
| GET /settings | 7 | 2.85 s | 38.19 s | 38.19 s | 38.19 s | 4.75 s | 200: 7 | 0 | 2 |

#### Server processes

| process | CPU s | mean cores | peak cores | RSS MB first → last (peak) |
|---|---:|---:|---:|---|
| model | 0.9 | 0.0 | 0.02 | 22.4 → 17.9 (30.8) |
| server | 0.8 | 0.0 | 0.01 | 61.4 → 53.1 (61.4) |
| server child | 194.5 | 0.29 | 0.93 | 133.3 → 178.2 (316.4) |
| background | 275.5 | 0.4 | 1.03 | 208.9 → 734.3 (2500.6) |
| server child | 188.9 | 0.28 | 1.0 | 133.3 → 174.9 (316.4) |
| resource tracker | 0.0 | 0.0 | 0.0 | 13.5 → 11.3 (13.5) |
| worker 1 | 415.0 | 0.61 | 1.6 | 125.2 → 0.0 (1767.8) |
| worker 2 | 455.9 | 0.67 | 1.72 | 125.3 → 0.0 (1682.9) |
| worker 3 | 434.9 | 0.64 | 1.27 | 128.8 → 1653.6 (1769.9) |
| worker 4 | 404.3 | 0.6 | 1.16 | 125.4 → 0.0 (1726.2) |

#### Over time (a row a minute)

| from | requests/s | errors | p95 (worst 10 s) |
|---:|---:|---:|---:|
| 0 min | 33.1 | 0 | 568 ms |
| 1 min | 24.2 | 7 | 30.11 s |
| 2 min | 8.2 | 18 | 48.10 s |
| 3 min | 14.5 | 12 | 84.08 s |
| 4 min | 15.9 | 13 | 64.77 s |
| 5 min | 25.7 | 24 | 37.27 s |
| 6 min | 26.0 | 27 | 31.23 s |
| 7 min | 20.4 | 1 | 30.81 s |
| 8 min | 17.6 | 0 | 33.41 s |
| 9 min | 15.9 | 0 | 47.08 s |
| 10 min | 24.1 | 3 | 46.37 s |
| 11 min | 5.9 | 0 | 42.57 s |

#### Correctness after the run

- Approved twice: none; finally approved twice: none; exported twice: none.
- Approved without an approval event: none.
- Summary against a recomputation (3624 invoices: every one the run touched, and a sample): the same.
- Logins paused: none.
- Events in the run: {'downloaded': 92, 'exported': 1309, 'final_approved': 98, 'import_run': 73, 'login': 100, 'page_reader_tested': 1, 'parked': 222, 'processed': 73, 'rejected': 21}; invoices now: {'approved': 293357, 'failed': 890, 'parked': 346, 'rejected': 2872, 'review': 2608}; waiting for OvisOCR2 at the end: 37.

#### What people did

- approvals not open: parked instead: 198
- export batches (csv): 41
- export batches (jde): 4
- export batches refused: 2
- final approvals: 108
- invoices acted on by someone else meanwhile (409): 118
- invoices left with a draft: 35
- logins: 100
- parked: 239
- rejected: 21
- schedule restarted (over a minute behind): 136
- search exports downloaded: 85
- search exports still being made when left: 3
- second approvals not theirs to give: 7
- static from the cache: 48641
- uploads: 73
- scenario admin: 43 done, 2 stopped by an error
- scenario batch: 55 done, 38 stopped by an error
- scenario bulk: 89 done, 0 stopped by an error
- scenario export_search: 88 done, 0 stopped by an error
- scenario look: 276 done, 0 stopped by an error
- scenario queue: 449 done, 0 stopped by an error
- scenario reports: 215 done, 65 stopped by an error
- scenario search: 373 done, 0 stopped by an error
- scenario second: 208 done, 0 stopped by an error
- scenario upload: 73 done, 0 stopped by an error
- scenario work: 1029 done, 0 stopped by an error
