# Load after phase 3: 100 people on 100,000 and 300,000 invoices (6 Nov 2026)

The same settings as phase 2 part 2 ([phase2b-load-100-people.md](phase2b-load-100-people.md)): 600 s after a 60 s
ramp (the knee: 300 s after 30 s), think 10 s, 4 web workers, the stand-in model 1-4 s with 2 slots, Python 3.12. Each
database was seeded again by this code (schema 27) just before its run; the machine otherwise quiet. The server now
reads imports in capture processes of its own and makes the long downloads in its background process (D-168, D-169).

**In short:** on 100,000 invoices the server answered 13,912 requests for 100 people (part 2: 9,838) with pages p50 366
ms (17.3 s), p95 4.81 s (59.4 s), p99 10.5 s (75.4 s) and actions p95 2.82 s (65.8 s): 0 errors, 0 'database is
locked', 4 requests over 30 s. The workers are no longer saturated (0.60-0.65 of a core each, against 0.85) and the
harness ran on schedule (p95 357 ms behind), so the tail is waiting, not a lack of cores: the first three minutes
(p95 of the worst 10 s 9-14 s while each worker works out its kept answers for the first time) and the long pages
that still run in a web worker (Import invoices p50 3.3 s, Spend 4.3 s, month-end 3.1 s, Vendors 2.8 s). At 25 and 50
people the p50, p95 and action targets are met; p99 is 3.3 s (target 3 s). On 300,000 invoices, 100 people still
saturate the four workers (0.74-0.78 of a core each, 2 GB each at their peak): pages p50 19.4 s, p95 69 s; 27 503s
(the long routes' lane busy). What grows with the database: below.

| invoices | people | requests | pages p50 | pages p95 | pages p99 | actions p95 | errors | workers' CPU | workers' memory after |
|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|
| 100k | 25 | 2,070 (part 2: 2,035) | 144 ms (300 ms) | 1.32 s (4.45 s) | 3.34 s (9.52 s) | 263 ms (1.37 s) | 0 | 0.11-0.25 | 610-815 MB |
| 100k | 50 | 3,996 (3,911) | 148 ms (985 ms) | 1.43 s (23.4 s) | 3.27 s (43.0 s) | 333 ms (28.4 s) | 0 (5 503s) | 0.22-0.40 | 725-940 MB |
| 100k | 100 | 13,912 (9,838) | 366 ms (17.3 s) | 4.81 s (59.4 s) | 10.5 s (75.4 s) | 2.82 s (65.8 s) | 0, 4 over 30 s (37 503s) | 0.60-0.65 | 1.02-1.13 GB (1.25-1.4) |
| 300k | 100 | 7,489 | 19.4 s | 69.5 s | 91.3 s | 71.8 s | 27 503s, 4 without an answer | 0.74-0.78 | 1.46-1.68 GB (2.05 peak) |

**Where the CPU goes** (py-spy, the two busiest web workers for two minutes from the fifth minute):

On 100,000 invoices, by page:

```
 25.6%  review_page (ap_coder/web/pages/review.py)
 14.3%  invoice_page (ap_coder/web/pages/invoice.py)
 14.3%  _check (ap_coder/web/pages/invoice.py)
 10.6%  process_page (ap_coder/web/pages/process.py)
  4.5%  vendors_page (ap_coder/web/pages/vendors.py)
  4.1%  month_end_page (ap_coder/web/pages/month_end.py)
  4.0%  spend_page (ap_coder/web/pages/spend.py)
  3.1%  <no page> EpollSelector.select (selectors.py)
  3.0%  sales_tax_page (ap_coder/web/pages/sales_tax.py)
  2.4%  search_page (ap_coder/web/pages/search.py)
```

On 300,000 invoices, by page:

```
 27.2%  _check (ap_coder/web/pages/invoice.py)
 17.3%  spend_page (ap_coder/web/pages/spend.py)
 16.9%  process_page (ap_coder/web/pages/process.py)
 13.9%  invoice_page (ap_coder/web/pages/invoice.py)
  9.7%  review_page (ap_coder/web/pages/review.py)
  3.3%  month_end_page (ap_coder/web/pages/month_end.py)
  2.5%  search_page (ap_coder/web/pages/search.py)
  2.2%  insights_page (ap_coder/web/pages/insights.py)
  1.5%  sales_tax_page (ap_coder/web/pages/sales_tax.py)
  1.4%  <no page> EpollSelector.select (selectors.py)
```

On 300,000 invoices, by function (inclusive):

```
 96.7%  one_connection.<locals>.wrapped (ap_coder/web/core.py)
 39.6%  fetch_rows (ap_coder/core/store/summary.py)
 38.8%  analyse (ap_coder/web/invoice_view.py)
 29.2%  SummaryMixin.summary_memo (ap_coder/core/store/summary.py)
 27.2%  _check (ap_coder/web/pages/invoice.py)
 22.5%  suggestions (ap_coder/web/invoice_view.py)
 17.3%  spend_page (ap_coder/web/pages/spend.py)
 16.9%  process_page (ap_coder/web/pages/process.py)
 16.4%  folder_files (ap_coder/web/pages/process.py)
 16.2%  figures (ap_coder/erp/spend.py)
 16.2%  figures.<locals>.<lambda> (ap_coder/erp/spend.py)
 16.2%  read_figures (ap_coder/erp/spend.py)
 16.0%  _figures (ap_coder/erp/spend.py)
 15.8%  LearningMixin.learning_rows (ap_coder/core/store/learning.py)
 15.7%  LearningMixin._past_memory (ap_coder/core/store/learning.py)
 15.0%  SummaryMixin.summary_fetch (ap_coder/core/store/summary.py)
 13.9%  invoice_page (ap_coder/web/pages/invoice.py)
 13.9%  finalise_coding (ap_coder/ai/pipeline.py)
 10.6%  vendor_findings (ap_coder/coding/vendors.py)
  9.8%  VendorsMixin.vendor_invoices (ap_coder/core/store/vendors.py)
  9.7%  review_page (ap_coder/web/pages/review.py)
  9.3%  StoreCore._session_memo (ap_coder/core/store/_base.py)
  9.3%  VendorsMixin.vendor_invoices.<locals>.<lambda> (ap_coder/core/store/vendors.py)
  9.3%  VendorsMixin._vendor_invoices (ap_coder/core/store/vendors.py)
  6.6%  suggest_gl (ap_coder/ai/suggest.py)
  5.6%  detected (ap_coder/erp/recurring.py)
  5.6%  detected.<locals>.<lambda> (ap_coder/erp/recurring.py)
  5.6%  FolderMixin.unseen_files (ap_coder/core/store/folder.py)
  4.6%  _memory (ap_coder/ai/suggest.py)
  4.6%  _with_past (ap_coder/ai/suggest.py)
```

**What grows with the database** (300k against 100k, the work behind the collapse): Spend's figures, kept 30 s
(`summary_memo(settle=30)`) and worked out again in each worker (17%); a vendor's older lessons read again in every
process after each approval once the learning memory is full (`learning_rows`/`_past_memory`, 16%; D-171's open
item); Import invoices' listing and the folder's files seen (`folder_files`, `unseen_files`, 17%); a big vendor's
invoices read again after a change (`vendor_invoices`, 9%); recurring invoices (6%). These are the next part's work
(SCALE.md, phase 3 part 2).

## 100 people, 100,000 invoices (the full report)

### Summary
- Requests: 13912 (21.08/s); 5xx 0, without an answer 0, cut off at the end 0, over 30 s 4; refused (4xx) 139.
- Pages: p50 366 ms, p95 4.81 s, p99 10.51 s (5634); actions (check, approve, park, reject, final approval): p50 206 ms, p95 2.82 s (2534). Timed from when each step was due (scheduled start).
- Targets: pages p50 <= 0.3 s NOT met, pages p95 <= 1.5 s NOT met, pages p99 <= 3.0 s NOT met, actions p95 <= 1.0 s NOT met.
- Behind schedule: p50 1 ms, p95 357 ms.
- Harness CPU: 0.14 cores (its event loop 0.138): well under one core, so the figures are the server's. Server started in 2.94 s; its stop took 1.15 s (shutdown).
- Server logs: 0 'database is locked', 0 5xx of 13917 lines, 0 tracebacks.

### Gate
- errors: 0 5xx, 0 without an answer, 0 cut off at the end, 4 over 30 s

### Routes

| route | n | p50 | p95 | p99 | max | server p95 | statuses | refused | errors |
|---|---:|---:|---:|---:|---:|---:|---|---:|---:|
| GET /review | 2766 | 371 ms | 3.23 s | 10.51 s | 40.90 s | 2.28 s | 200: 2766 | 0 | 2 |
| POST /review/{id}/check | 2072 | 225 ms | 2.94 s | 9.87 s | 22.53 s | 1.85 s | 200: 1934, 409: 138 | 138 | 0 |
| GET /review/{id}/page/{n}.png | 1785 | 34 ms | 811 ms | 3.00 s | 8.04 s | 757 ms | 200: 1785 | 0 | 0 |
| GET /static/{file} | 1604 | 15 ms | 82 ms | 222 ms | 729 ms | 2 ms | 200: 1604 | 0 | 0 |
| GET /review/{id} | 1320 | 434 ms | 3.26 s | 7.83 s | 21.38 s | 2.64 s | 200: 1320 | 0 | 0 |
| GET /review/vendors.json | 868 | 49 ms | 1.08 s | 2.20 s | 5.12 s | 984 ms | 200: 868 | 0 | 0 |
| GET /search | 609 | 221 ms | 2.15 s | 5.78 s | 34.47 s | 1.59 s | 200: 609 | 0 | 1 |
| GET /process/status | 586 | 55 ms | 1.04 s | 2.27 s | 5.28 s | 909 ms | 200: 586 | 0 | 0 |
| POST /review/{id}/park | 316 | 161 ms | 2.03 s | 6.30 s | 14.44 s | 1.34 s | 200: 316 | 0 | 0 |
| GET /assets/{file} | 200 | 30 ms | 52 ms | 71 ms | 98 ms | 0 ms | 200: 200 | 0 | 0 |
| GET /fonts/{file} | 200 | 3 ms | 12 ms | 56 ms | 145 ms | 1 ms | 200: 200 | 0 | 0 |
| GET /process | 199 | 3.32 s | 8.93 s | 18.52 s | 18.52 s | 8.38 s | 200: 199 | 0 | 0 |
| GET /exports | 190 | 213 ms | 1.46 s | 8.84 s | 9.04 s | 1.09 s | 200: 190 | 0 | 0 |
| POST /review/{id}/final-approve | 118 | 92 ms | 2.13 s | 5.30 s | 5.39 s | 1.44 s | 200: 118 | 0 | 0 |
| GET /login | 100 | 8 ms | 60 ms | 1.48 s | 2.09 s | 18 ms | 200: 100 | 0 | 0 |
| GET /login/code | 100 | 48 ms | 82 ms | 319 ms | 364 ms | 23 ms | 200: 100 | 0 | 0 |
| POST /login | 100 | 68 ms | 411 ms | 572 ms | 583 ms | 408 ms | 200: 100 | 0 | 0 |
| POST /login/code | 100 | 16 ms | 288 ms | 610 ms | 734 ms | 131 ms | 200: 100 | 0 | 0 |
| POST /process/upload | 99 | 278 ms | 7.90 s | 15.75 s | 15.75 s | 1.40 s | 200: 99 | 0 | 0 |
| GET /search/export.csv | 90 | 2.15 s | 9.26 s | 20.21 s | 20.21 s | 9.23 s | 200: 90 | 0 | 0 |
| GET /insights | 54 | 686 ms | 8.95 s | 13.08 s | 13.08 s | 8.28 s | 200: 54 | 0 | 0 |
| GET /month-end | 54 | 3.08 s | 21.70 s | 23.17 s | 23.17 s | 16.78 s | 200: 54 | 0 | 0 |
| GET /spend | 50 | 4.29 s | 11.24 s | 13.27 s | 13.27 s | 11.23 s | 200: 50 | 0 | 0 |
| GET /search/export.xlsx | 48 | 3.49 s | 12.81 s | 18.78 s | 18.78 s | 12.52 s | 200: 48 | 0 | 0 |
| GET /vendors | 48 | 2.80 s | 6.85 s | 11.89 s | 11.89 s | 6.61 s | 200: 48 | 0 | 0 |
| POST /exports/create | 47 | 93 ms | 3.02 s | 7.20 s | 7.20 s | 964 ms | 200: 46, 400: 1 | 1 | 0 |
| GET /learning | 44 | 682 ms | 4.43 s | 8.84 s | 8.84 s | 4.39 s | 200: 44 | 0 | 0 |
| GET /purchase-orders | 36 | 260 ms | 2.09 s | 30.31 s | 30.31 s | 1.94 s | 200: 36 | 0 | 1 |
| GET /sales-tax | 32 | 1.76 s | 6.53 s | 7.64 s | 7.64 s | 5.94 s | 200: 32 | 0 | 0 |
| POST /review/{id}/reject | 28 | 169 ms | 1.24 s | 1.41 s | 1.41 s | 1.13 s | 200: 28 | 0 | 0 |
| GET /exports/batches/{id}/download | 16 | 351 ms | 3.70 s | 3.70 s | 3.70 s | 1.77 s | 200: 16 | 0 | 0 |
| GET /admin/users | 13 | 111 ms | 5.73 s | 5.73 s | 5.73 s | 761 ms | 200: 13 | 0 | 0 |
| GET /activity | 10 | 203 ms | 489 ms | 489 ms | 489 ms | 482 ms | 200: 10 | 0 | 0 |
| GET /settings | 9 | 227 ms | 4.95 s | 4.95 s | 4.95 s | 4.93 s | 200: 9 | 0 | 0 |
| GET /review/{id}/reader | 1 | 13 ms | 13 ms | 13 ms | 13 ms | 8 ms | 200: 1 | 0 | 0 |

### Server processes

| process | CPU s | mean cores | peak cores | RSS MB first → last (peak) |
|---|---:|---:|---:|---|
| model | 0.9 | 0.0 | 0.01 | 22.0 → 22.3 (30.5) |
| server | 1.0 | 0.0 | 0.01 | 60.6 → 60.6 (60.6) |
| background | 230.9 | 0.35 | 1.33 | 203.6 → 675.6 (707.9) |
| server child | 84.6 | 0.13 | 0.97 | 132.1 → 326.8 (326.9) |
| server child | 92.7 | 0.14 | 0.99 | 131.7 → 326.6 (326.6) |
| resource tracker | 0.0 | 0.0 | 0.0 | 13.4 → 13.4 (13.4) |
| worker 1 | 426.7 | 0.65 | 1.69 | 124.3 → 1125.8 (1126.0) |
| worker 2 | 401.2 | 0.61 | 1.57 | 127.5 → 1023.2 (1023.2) |
| worker 3 | 431.9 | 0.65 | 1.48 | 127.7 → 1080.8 (1094.2) |
| worker 4 | 395.9 | 0.6 | 1.28 | 124.2 → 1036.1 (1036.2) |

### Over time (a row a minute)

| from | requests/s | errors | p95 (worst 10 s) |
|---:|---:|---:|---:|
| 0 min | 33.5 | 0 | 643 ms |
| 1 min | 27.2 | 0 | 4.68 s |
| 2 min | 23.4 | 0 | 9.28 s |
| 3 min | 21.6 | 0 | 13.93 s |
| 4 min | 19.7 | 0 | 5.23 s |
| 5 min | 18.6 | 0 | 6.10 s |
| 6 min | 17.2 | 0 | 3.72 s |
| 7 min | 18.0 | 0 | 3.66 s |
| 8 min | 17.5 | 0 | 2.90 s |
| 9 min | 17.0 | 0 | 2.33 s |
| 10 min | 18.2 | 0 | 3.32 s |
| 11 min | 5.2 | 0 | 3.24 s |

### Correctness after the run

- Approved twice: none; finally approved twice: none; exported twice: none.
- Approved without an approval event: none.
- Summary against a recomputation (2907 invoices: every one the run touched, and a sample): the same.
- Logins paused: none.
- Events in the run: {'downloaded': 154, 'exported': 500, 'final_approved': 109, 'import_run': 99, 'login': 100, 'page_reader_tested': 1, 'parked': 282, 'processed': 99, 'rejected': 27}; invoices now: {'approved': 95800, 'failed': 298, 'parked': 432, 'rejected': 1038, 'review': 2531}; waiting for OvisOCR2 at the end: 62.

### What people did

- approvals not open: parked instead: 263
- export batches (csv): 44
- export batches (jde): 2
- export batches refused: 1
- final approvals: 118
- invoices acted on by someone else meanwhile (409): 138
- invoices left with a draft: 37
- logins: 100
- parked: 316
- rejected: 28
- second approvals not theirs to give: 10
- static from the cache: 60931
- uploads: 99
- scenario admin: 57 done, 0 stopped by an error
- scenario batch: 98 done, 0 stopped by an error
- scenario bulk: 107 done, 0 stopped by an error
- scenario export_search: 138 done, 0 stopped by an error
- scenario look: 302 done, 0 stopped by an error
- scenario queue: 565 done, 0 stopped by an error
- scenario reports: 339 done, 0 stopped by an error
- scenario search: 468 done, 0 stopped by an error
- scenario second: 248 done, 0 stopped by an error
- scenario upload: 99 done, 0 stopped by an error
- scenario work: 1264 done, 0 stopped by an error

## 100 people, 300,000 invoices (the full report)

### Summary
- Requests: 7489 (11.35/s); 5xx 27, without an answer 4, cut off at the end 0, over 30 s 1687; refused (4xx) 77.
- Pages: p50 19.35 s, p95 69.46 s, p99 91.25 s (2716); actions (check, approve, park, reject, final approval): p50 33.57 s, p95 71.78 s (1062). Timed from when each step was due (scheduled start).
- Targets: pages p50 <= 0.3 s NOT met, pages p95 <= 1.5 s NOT met, pages p99 <= 3.0 s NOT met, actions p95 <= 1.0 s NOT met.
- Behind schedule: p50 10.83 s, p95 53.59 s.
- Harness CPU: 0.069 cores (its event loop 0.067): well under one core, so the figures are the server's. Server started in 2.74 s; its stop took 4.95 s (shutdown).
- Server logs: 0 'database is locked', 27 5xx of 7493 lines, 0 tracebacks.

### Gate
- errors: 27 5xx, 4 without an answer, 0 cut off at the end, 1687 over 30 s
- server logs: 0 'database is locked', 27 5xx

### Routes

| route | n | p50 | p95 | p99 | max | server p95 | statuses | refused | errors |
|---|---:|---:|---:|---:|---:|---:|---|---:|---:|
| GET /static/{file} | 1488 | 20 ms | 90 ms | 318 ms | 1.53 s | 10 ms | 200: 1488 | 0 | 0 |
| GET /review | 1288 | 17.06 s | 65.10 s | 79.31 s | 93.96 s | 29.53 s | 200: 1288 | 0 | 416 |
| GET /review/{id}/page/{n}.png | 849 | 6.78 s | 28.35 s | 35.28 s | 46.00 s | 28.35 s | 200: 849 | 0 | 36 |
| POST /review/{id}/check | 849 | 33.63 s | 73.39 s | 88.47 s | 106.58 s | 33.63 s | 200: 773, 409: 76 | 76 | 464 |
| GET /review/{id} | 632 | 23.56 s | 67.90 s | 84.58 s | 95.24 s | 35.77 s | 200: 632 | 0 | 246 |
| GET /review/vendors.json | 381 | 6.06 s | 28.53 s | 35.26 s | 45.72 s | 28.46 s | 200: 381 | 0 | 15 |
| GET /search | 274 | 22.79 s | 69.31 s | 89.01 s | 104.34 s | 30.71 s | 200: 274 | 0 | 108 |
| GET /process/status | 267 | 1.33 s | 20.24 s | 30.77 s | 32.92 s | 20.22 s | 200: 267 | 0 | 3 |
| GET /assets/{file} | 200 | 29 ms | 57 ms | 72 ms | 107 ms | 0 ms | 200: 200 | 0 | 0 |
| GET /fonts/{file} | 200 | 4 ms | 27 ms | 166 ms | 639 ms | 1 ms | 200: 200 | 0 | 0 |
| POST /review/{id}/park | 116 | 25.25 s | 70.15 s | 75.52 s | 79.99 s | 28.41 s | 200: 116 | 0 | 54 |
| GET /login | 100 | 13 ms | 517 ms | 1.28 s | 1.91 s | 180 ms | 200: 100 | 0 | 0 |
| GET /login/code | 100 | 52 ms | 211 ms | 811 ms | 1.99 s | 109 ms | 200: 100 | 0 | 0 |
| POST /login | 100 | 154 ms | 716 ms | 1.96 s | 12.68 s | 712 ms | 200: 100 | 0 | 0 |
| POST /login/code | 100 | 54 ms | 976 ms | 4.75 s | 10.81 s | 889 ms | 200: 100 | 0 | 0 |
| GET /exports | 88 | 22.91 s | 65.42 s | 90.00 s | 90.00 s | 39.82 s | 200: 74, 503: 14 | 0 | 50 |
| POST /review/{id}/final-approve | 87 | 35.06 s | 67.94 s | 74.63 s | 74.63 s | 30.14 s | 200: 87 | 0 | 51 |
| GET /process | 84 | 38.40 s | 73.13 s | 87.57 s | 87.57 s | 54.05 s | 200: 84 | 0 | 60 |
| POST /process/upload | 41 | 41.70 s | 66.20 s | 74.79 s | 74.79 s | 32.20 s | 200: 41 | 0 | 24 |
| GET /search/export.csv | 35 | 81.59 s | 340.19 s | 456.60 s | 456.60 s | 305.26 s | 200: 35 | 0 | 27 |
| GET /insights | 31 | 37.71 s | 74.48 s | 100.04 s | 100.04 s | 53.38 s | 200: 28, 503: 3 | 0 | 22 |
| POST /exports/create | 30 | 36.65 s | 72.60 s | 86.86 s | 86.86 s | 32.97 s | 200: 26, 400: 1, 503: 3 | 1 | 20 |
| GET /month-end | 23 | 91.46 s | 144.73 s | 149.22 s | 149.22 s | 160.18 s | 200: 16, 503: 3 | 0 | 26 |
| GET /sales-tax | 21 | 44.81 s | 73.89 s | 77.12 s | 77.12 s | 41.20 s | 200: 19, 503: 2 | 0 | 17 |
| GET /vendors | 20 | 35.41 s | 56.12 s | 60.12 s | 60.12 s | 41.84 s | 200: 20 | 0 | 14 |
| GET /spend | 19 | 70.02 s | 118.16 s | 118.16 s | 118.16 s | 85.08 s | 200: 17, 503: 2 | 0 | 21 |
| GET /search/export.xlsx | 17 | 88.90 s | 299.34 s | 299.34 s | 299.34 s | 298.66 s | 200: 17 | 0 | 16 |
| GET /purchase-orders | 15 | 13.74 s | 64.97 s | 64.97 s | 64.97 s | 42.74 s | 200: 15 | 0 | 7 |
| GET /learning | 10 | 55.06 s | 92.97 s | 92.97 s | 92.97 s | 86.35 s | 200: 10 | 0 | 7 |
| POST /review/{id}/reject | 10 | 48.92 s | 65.19 s | 65.19 s | 65.19 s | 32.46 s | 200: 10 | 0 | 9 |
| GET /admin/users | 4 | 628 ms | 18.90 s | 18.90 s | 18.90 s | 18.88 s | 200: 4 | 0 | 0 |
| GET /settings | 4 | 42.04 s | 49.44 s | 49.44 s | 49.44 s | 24.61 s | 200: 4 | 0 | 3 |
| GET /activity | 3 | 47.72 s | 69.82 s | 69.82 s | 69.82 s | 23.36 s | 200: 3 | 0 | 2 |
| GET /exports/batches/{id}/download | 3 | 12.10 s | 22.58 s | 22.58 s | 22.58 s | 22.57 s | 200: 3 | 0 | 0 |

### Server processes

| process | CPU s | mean cores | peak cores | RSS MB first → last (peak) |
|---|---:|---:|---:|---|
| model | 1.0 | 0.0 | 0.01 | 22.5 → 17.9 (31.0) |
| server | 0.8 | 0.0 | 0.01 | 61.4 → 53.1 (61.4) |
| background | 288.1 | 0.4 | 0.93 | 214.4 → 1655.6 (1764.7) |
| server child | 109.7 | 0.15 | 0.75 | 133.3 → 265.4 (318.1) |
| server child | 103.7 | 0.14 | 0.73 | 133.3 → 265.3 (317.5) |
| resource tracker | 0.0 | 0.0 | 0.0 | 13.5 → 11.6 (13.5) |
| worker 1 | 554.8 | 0.78 | 1.79 | 128.1 → 1684.2 (2011.8) |
| worker 2 | 532.6 | 0.74 | 1.39 | 128.8 → 1616.3 (1943.4) |
| worker 3 | 540.2 | 0.75 | 1.43 | 125.8 → 1460.8 (1903.3) |
| worker 4 | 559.0 | 0.78 | 1.8 | 127.8 → 1655.0 (2054.1) |

### Over time (a row a minute)

| from | requests/s | errors | p95 (worst 10 s) |
|---:|---:|---:|---:|
| 0 min | 32.1 | 0 | 1.89 s |
| 1 min | 14.5 | 0 | 41.77 s |
| 2 min | 8.1 | 16 | 81.58 s |
| 3 min | 7.3 | 8 | 79.31 s |
| 4 min | 6.4 | 2 | 84.42 s |
| 5 min | 6.8 | 0 | 93.59 s |
| 6 min | 8.9 | 1 | 74.90 s |
| 7 min | 8.1 | 1 | 81.33 s |
| 8 min | 10.5 | 0 | 86.04 s |
| 9 min | 9.0 | 3 | 66.33 s |
| 10 min | 7.1 | 0 | 124.32 s |
| 11 min | 6.2 | 0 | 295.50 s |

### Correctness after the run

- Approved twice: none; finally approved twice: none; exported twice: none.
- Approved without an approval event: none.
- Summary against a recomputation (2980 invoices: every one the run touched, and a sample): the same.
- Logins paused: none.
- Events in the run: {'downloaded': 55, 'exported': 795, 'final_approved': 78, 'import_run': 41, 'login': 100, 'page_reader_tested': 1, 'parked': 102, 'processed': 41, 'rejected': 10}; invoices now: {'approved': 293337, 'failed': 890, 'parked': 226, 'pending_approval': 20, 'rejected': 2861, 'review': 2707}; waiting for OvisOCR2 at the end: 24.

### What people did

- approvals not open: parked instead: 101
- export batches (csv): 22
- export batches (jde): 4
- export batches refused: 1
- final approvals: 87
- invoices acted on by someone else meanwhile (409): 76
- invoices left with a draft: 16
- logins: 100
- parked: 116
- rejected: 10
- schedule restarted (over a minute behind): 428
- static from the cache: 27700
- uploads: 41
- scenario admin: 15 done, 0 stopped by an error
- scenario batch: 28 done, 15 stopped by an error
- scenario bulk: 43 done, 0 stopped by an error
- scenario export_search: 52 done, 0 stopped by an error
- scenario look: 157 done, 0 stopped by an error
- scenario queue: 240 done, 0 stopped by an error
- scenario reports: 136 done, 16 stopped by an error
- scenario search: 222 done, 0 stopped by an error
- scenario second: 87 done, 0 stopped by an error
- scenario upload: 41 done, 0 stopped by an error
- scenario work: 517 done, 0 stopped by an error
