# Load after phase 2 part 2: 100 people on 100,000 invoices (5 Nov 2026)

The same settings as the baseline and part 1 ([phase2-load-100-people.md](phase2-load-100-people.md)): 100 people, 600
s after a 60 s ramp, think 10 s, 4 workers, the stand-in model 1-4 s with 2 slots. The database was seeded again by
this code (schema 25, with a CAD and a USD bank account in use, B-1739). Python 3.12, the machine otherwise quiet.

**In short:** the server answered 9,838 requests (part 1: 12,096, of which 9,178 static files that browsers now keep
for good, D-161; 37,732 static files came from the browsers' cache here). The real work answered about tripled against part 1: the queue
1,764 against 652, the Check 1,092 against 350, an invoice 810 against 279, search 365 against 122. Static files, fonts
and logins no longer wait behind slow pages (static p95 19.6 s → 0.27 s, fonts 56.7 s → 0.09 s, logins 31-53 s →
3-6 s). 0 'database is locked', 0 tracebacks; 37 503s (the long routes' lane saying it is busy, by design). Latency is
still far off the targets: pages p50 17 s, p95 59 s; actions p95 66 s. The 4 cores are saturated (each worker about
0.85 of a core: Python runs one thread of a process at a time).

**The knee** (the same seed and settings, 300 s after a 30 s ramp):

| people | requests | pages p50 | pages p95 | pages p99 | actions p95 | errors | workers' CPU |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 25 | 2,035 | 300 ms | 4.45 s | 9.52 s | 1.37 s | 0 | 0.40-0.49 each |
| 50 | 3,911 | 985 ms | 23.4 s | 43.0 s | 28.4 s | 0 at the client, 5 503s logged | 0.66-0.74 each |
| 100 | 9,838 | 17.3 s | 59.4 s | 75.4 s | 65.8 s | 37 503s, 5 without an answer | 0.84-0.86 each |

At 25 people the workers are not saturated, yet a page's p95 is 4.45 s: a long request (month-end's first visit in a
worker, about 8 s; search to Excel, 12 s; an import read in the web worker) holds the process's one running thread
and slows the light requests beside it. Phase 3 takes that work out of the web workers.

**Where the CPU goes now** (py-spy, the four workers for two minutes under 100 people; part 2's first profile in
brackets): the queue 18% (27%), the Check 12% (5%), an invoice 12% (9%), month-end 10% (10%), Import invoices 10% (14%)
and an import read in the web worker 5%, search to Excel 7.5% (7.7%) and to CSV 3.6%, Spend 3.4%. `summary_memo`
working answers out again: 24% (32%).

## Summary
- Requests: 9838 (14.91/s); 5xx 37, without an answer 5, cut off at the end 0, over 30 s 1967; refused (4xx) 96.
- Pages: p50 17.29 s, p95 59.37 s, p99 75.39 s (3616); actions (check, approve, park, reject, final approval): p50 32.16 s, p95 65.84 s (1383). Timed from when each step was due (scheduled start).
- Targets: pages p50 <= 0.3 s NOT met, pages p95 <= 1.5 s NOT met, pages p99 <= 3.0 s NOT met, actions p95 <= 1.0 s NOT met.
- Behind schedule: p50 13.35 s, p95 52.56 s.
- Harness CPU: 0.127 cores (its event loop 0.124): well under one core, so the figures are the server's. Server started in 3.4 s; its stop took 1.35 s (shutdown).
- Server logs: 0 'database is locked', 37 5xx of 9843 lines, 0 tracebacks.

## Gate
- errors: 37 5xx, 5 without an answer, 0 cut off at the end, 1967 over 30 s
- server logs: 0 'database is locked', 37 5xx

## Routes

| route | n | p50 | p95 | p99 | max | server p95 | statuses | refused | errors |
|---|---:|---:|---:|---:|---:|---:|---|---:|---:|
| GET /review | 1764 | 16.72 s | 59.14 s | 72.35 s | 94.36 s | 25.08 s | 200: 1764 | 0 | 543 |
| GET /static/{file} | 1531 | 43 ms | 273 ms | 696 ms | 1.29 s | 27 ms | 200: 1531 | 0 | 0 |
| GET /review/{id}/page/{n}.png | 1118 | 3.84 s | 22.87 s | 26.38 s | 37.08 s | 22.80 s | 200: 1118 | 0 | 6 |
| POST /review/{id}/check | 1092 | 32.24 s | 66.55 s | 79.29 s | 95.41 s | 25.66 s | 200: 997, 409: 95 | 95 | 580 |
| GET /process/status | 916 | 3.38 s | 21.11 s | 25.83 s | 37.15 s | 21.05 s | 200: 916 | 0 | 4 |
| GET /review/{id} | 810 | 21.59 s | 58.18 s | 73.39 s | 104.38 s | 25.33 s | 200: 810 | 0 | 283 |
| GET /review/vendors.json | 483 | 4.17 s | 23.77 s | 26.93 s | 37.11 s | 23.63 s | 200: 483 | 0 | 4 |
| GET /search | 365 | 19.75 s | 56.09 s | 64.75 s | 85.55 s | 24.14 s | 200: 365 | 0 | 116 |
| GET /assets/{file} | 200 | 45 ms | 129 ms | 275 ms | 904 ms | 4 ms | 200: 200 | 0 | 0 |
| GET /fonts/{file} | 200 | 9 ms | 86 ms | 270 ms | 516 ms | 10 ms | 200: 200 | 0 | 0 |
| POST /review/{id}/park | 155 | 32.02 s | 60.43 s | 75.53 s | 84.64 s | 22.99 s | 200: 155 | 0 | 81 |
| GET /exports | 141 | 16.26 s | 57.40 s | 85.44 s | 94.58 s | 36.19 s | 200: 129, 503: 12 | 0 | 59 |
| POST /review/{id}/final-approve | 124 | 29.37 s | 68.37 s | 72.41 s | 76.36 s | 22.69 s | 200: 124 | 0 | 61 |
| GET /login | 100 | 25 ms | 1.77 s | 4.09 s | 4.09 s | 801 ms | 200: 100 | 0 | 0 |
| GET /login/code | 100 | 60 ms | 1.78 s | 5.25 s | 5.42 s | 1.58 s | 200: 100 | 0 | 0 |
| POST /login | 100 | 206 ms | 3.15 s | 7.63 s | 9.17 s | 3.11 s | 200: 100 | 0 | 0 |
| POST /login/code | 100 | 88 ms | 5.65 s | 8.70 s | 13.25 s | 3.18 s | 200: 100 | 0 | 0 |
| GET /process | 92 | 22.97 s | 70.99 s | 81.50 s | 81.50 s | 36.99 s | 200: 92 | 0 | 25 |
| GET /search/export.csv | 54 | 30.39 s | 72.06 s | 83.44 s | 83.44 s | 40.75 s | 200: 49, 503: 5 | 0 | 34 |
| POST /exports/create | 54 | 24.64 s | 71.11 s | 85.50 s | 85.50 s | 35.72 s | 200: 47, 400: 1, 503: 6 | 1 | 29 |
| POST /process/upload | 46 | 21.18 s | 55.62 s | 83.67 s | 83.67 s | 21.94 s | 200: 46 | 0 | 20 |
| GET /learning | 44 | 19.60 s | 61.71 s | 65.27 s | 65.27 s | 25.65 s | 200: 44 | 0 | 12 |
| GET /month-end | 34 | 45.29 s | 87.92 s | 88.51 s | 88.51 s | 64.55 s | 200: 30, 503: 4 | 0 | 35 |
| GET /insights | 30 | 22.37 s | 66.02 s | 85.68 s | 85.68 s | 43.73 s | 200: 27, 503: 3 | 0 | 16 |
| GET /sales-tax | 28 | 24.08 s | 75.71 s | 93.44 s | 93.44 s | 36.75 s | 200: 27, 503: 1 | 0 | 14 |
| GET /purchase-orders | 27 | 23.23 s | 55.77 s | 57.44 s | 57.44 s | 24.08 s | 200: 27 | 0 | 9 |
| GET /spend | 26 | 48.37 s | 83.93 s | 112.66 s | 112.66 s | 47.97 s | 200: 22, 503: 4 | 0 | 22 |
| GET /vendors | 25 | 25.58 s | 83.80 s | 88.38 s | 88.38 s | 32.08 s | 200: 25 | 0 | 11 |
| GET /search/export.xlsx | 21 | 109.86 s | 133.40 s | 165.53 s | 165.53 s | 130.65 s | 200: 15, 503: 1 | 0 | 22 |
| GET /exports/batches/{id}/download | 16 | 19.35 s | 61.42 s | 61.42 s | 61.42 s | 30.75 s | 200: 15, 503: 1 | 0 | 7 |
| GET /admin/users | 12 | 8.83 s | 71.49 s | 71.49 s | 71.49 s | 22.75 s | 200: 12 | 0 | 3 |
| POST /review/{id}/reject | 12 | 35.01 s | 56.69 s | 56.69 s | 56.69 s | 20.50 s | 200: 12 | 0 | 8 |
| GET /settings | 11 | 17.61 s | 57.62 s | 57.62 s | 57.62 s | 18.52 s | 200: 11 | 0 | 2 |
| GET /activity | 7 | 17.18 s | 55.09 s | 55.09 s | 55.09 s | 32.31 s | 200: 7 | 0 | 3 |

## Server processes

| process | CPU s | mean cores | peak cores | RSS MB first → last (peak) |
|---|---:|---:|---:|---|
| model | 1.4 | 0.0 | 0.03 | 22.4 → 22.6 (30.8) |
| server | 1.3 | 0.0 | 0.01 | 61.3 → 61.3 (61.3) |
| background | 137.6 | 0.19 | 0.74 | 174.0 → 608.3 (641.6) |
| resource tracker | 0.0 | 0.0 | 0.0 | 13.4 → 13.4 (13.4) |
| worker 1 | 627.8 | 0.86 | 1.72 | 128.1 → 1376.0 (1400.8) |
| worker 2 | 620.0 | 0.85 | 1.51 | 125.1 → 1265.3 (1265.5) |
| worker 3 | 626.5 | 0.86 | 1.73 | 128.4 → 1282.1 (1292.4) |
| worker 4 | 609.9 | 0.84 | 1.56 | 124.9 → 1252.9 (1253.3) |

## Over time (a row a minute)

| from | requests/s | errors | p95 (worst 10 s) |
|---:|---:|---:|---:|
| 0 min | 30.6 | 0 | 3.75 s |
| 1 min | 18.9 | 0 | 37.65 s |
| 2 min | 11.0 | 3 | 69.21 s |
| 3 min | 11.7 | 3 | 68.64 s |
| 4 min | 12.1 | 0 | 63.85 s |
| 5 min | 13.9 | 1 | 67.39 s |
| 6 min | 13.6 | 4 | 67.45 s |
| 7 min | 11.2 | 10 | 68.20 s |
| 8 min | 13.9 | 7 | 58.67 s |
| 9 min | 11.0 | 9 | 67.26 s |
| 10 min | 9.2 | 5 | 68.46 s |
| 11 min | 6.6 | 0 | 72.83 s |
| 12 min | 1.2 | 0 | 96.81 s |

## Correctness after the run

- Approved twice: none; finally approved twice: none; exported twice: none.
- Approved without an approval event: none.
- Summary against a recomputation (2696 invoices: every one the run touched, and a sample): the same.
- Logins paused: none.
- Events in the run: {'downloaded': 84, 'exported': 498, 'final_approved': 109, 'import_run': 46, 'login': 100, 'page_reader_tested': 1, 'parked': 142, 'processed': 46, 'rejected': 11}; invoices now: {'approved': 95800, 'failed': 298, 'parked': 292, 'rejected': 1022, 'review': 2634}; waiting for OvisOCR2 at the end: 31.

## What people did

- approvals not open: parked instead: 138
- export batches (csv): 45
- export batches (jde): 2
- export batches refused: 1
- final approvals: 124
- invoices acted on by someone else meanwhile (409): 95
- invoices left with a draft: 18
- logins: 100
- parked: 155
- rejected: 12
- schedule restarted (over a minute behind): 297
- second approvals not theirs to give: 10
- static from the cache: 37732
- uploads: 46
- scenario admin: 53 done, 0 stopped by an error
- scenario batch: 47 done, 16 stopped by an error
- scenario bulk: 51 done, 0 stopped by an error
- scenario export_search: 64 done, 11 stopped by an error
- scenario look: 211 done, 0 stopped by an error
- scenario queue: 343 done, 0 stopped by an error
- scenario reports: 207 done, 15 stopped by an error
- scenario search: 288 done, 0 stopped by an error
- scenario second: 155 done, 0 stopped by an error
- scenario upload: 46 done, 0 stopped by an error
- scenario work: 699 done, 0 stopped by an error
