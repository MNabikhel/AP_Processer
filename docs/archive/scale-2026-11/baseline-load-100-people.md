# Load test: 100 people for 600 s on 4 worker(s)

2026-11-05T03:27:35; ramp 60 s, think 10.0 s (median); 100000 invoices in (the seeded 100k folder); models: ap_coder/bench/fake_model.py, 1-4 s an answer, 2 at once, at http://192.0.2.2:52603/v1; people reach the server at 192.0.2.2. 4 cores, Python 3.12.13, Linux-6.18.44-fc-v236-x86_64-with-glibc2.39.

## Summary
- Requests: 5616 (8.51/s); 5xx 5, without an answer 345, over 30 s 356; refused (4xx) 4.
- Pages: p50 5.14 s, p95 67.26 s, p99 105.08 s (654); actions (check, approve, park, reject, final approval): p50 47.99 s, p95 115.53 s (228). Timed from when each step was due (scheduled start).
- Targets: pages p50 <= 0.3 s NOT met, pages p95 <= 1.5 s NOT met, pages p99 <= 3.0 s NOT met, actions p95 <= 1.0 s NOT met.
- Behind schedule: p50 1 ms, p95 45.89 s.
- Harness CPU: 0.103 cores (its event loop 0.054): well under one core, so the figures are the server's. Server started in 2.56 s; its stop took 29.12 s (shutdown).
- Server logs: 10 'database is locked', 74 5xx of 5369 lines, 74 tracebacks.

## Gate
- errors: 5 5xx, 345 without an answer, 356 over 30 s
- server logs: 10 'database is locked', 74 5xx
- 4 logins refused

## Routes

| route | n | p50 | p95 | p99 | max | server p95 | statuses | refused | errors |
|---|---:|---:|---:|---:|---:|---:|---|---:|---:|
| GET /static/{file} | 3673 | 330 ms | 2.30 s | 4.87 s | 120.93 s | 2.02 s | 200: 1127, 304: 2518 | 0 | 46 |
| GET /review | 447 | 6.36 s | 40.48 s | 62.99 s | 67.90 s | 419.40 s | 200: 242 | 0 | 229 |
| POST /review/{id}/check | 228 | 50.28 s | 120.15 s | 168.71 s | 169.82 s | 120.24 s | 200: 194, 409: 3, 500: 1 | 3 | 190 |
| GET /assets/{file} | 200 | 57 ms | 2.06 s | 3.34 s | 4.27 s | 2.20 s | 200: 200 | 0 | 0 |
| GET /fonts/{file} | 200 | 48 ms | 2.10 s | 3.83 s | 4.79 s | 2.21 s | 200: 200 | 0 | 0 |
| GET /review/{id}/page/{n}.png | 132 | 1.36 s | 9.63 s | 24.56 s | 62.67 s | 9.19 s | 200: 131 | 0 | 2 |
| GET /review/{id} | 110 | 32.01 s | 90.50 s | 109.86 s | 138.38 s | 102.46 s | 200: 103 | 0 | 63 |
| GET /login | 104 | 51 ms | 2.36 s | 13.14 s | 13.15 s | 807 ms | 200: 104 | 0 | 0 |
| POST /login | 104 | 940 ms | 33.26 s | 43.17 s | 60.13 s | 32.93 s | 200: 101, 500: 3 | 0 | 11 |
| GET /login/code | 101 | 116 ms | 1.30 s | 1.91 s | 2.33 s | 974 ms | 200: 101 | 0 | 0 |
| POST /login/code | 101 | 1.14 s | 45.29 s | 48.65 s | 75.83 s | 29.06 s | 200: 100, 500: 1 | 0 | 18 |
| GET /search | 78 | 6.40 s | 57.02 s | 67.22 s | 67.22 s | 211.52 s | 200: 52 | 0 | 39 |
| GET /exports | 25 | 67.27 s | 140.65 s | 140.65 s | 140.65 s | 372.51 s | 200: 12 | 0 | 25 |
| POST /review/{id}/park | 17 | 34.32 s | 89.31 s | 89.31 s | 89.31 s | 52.29 s | 200: 15 | 0 | 13 |
| GET /process | 13 | - | - | - | - | 846.69 s |  | 0 | 13 |
| POST /review/{id}/final-approve | 12 | 8.85 s | 56.60 s | 56.60 s | 56.60 s | 27.23 s | 200: 12 | 0 | 4 |
| GET /insights | 10 | 29.54 s | 116.57 s | 116.57 s | 116.57 s | 116.53 s | 200: 8 | 0 | 6 |
| GET /month-end | 9 | - | - | - | - | 360.06 s |  | 0 | 9 |
| GET /spend | 9 | 38.01 s | 86.57 s | 86.57 s | 86.57 s | 40.63 s | 200: 8 | 0 | 8 |
| GET /vendors | 9 | 52.03 s | 115.72 s | 115.72 s | 115.72 s | 68.73 s | 200: 8 | 0 | 7 |
| GET /sales-tax | 6 | 21.92 s | 76.06 s | 76.06 s | 76.06 s | 22.54 s | 200: 6 | 0 | 3 |
| GET /search/export.csv | 6 | 51.86 s | 115.99 s | 115.99 s | 115.99 s | 139.14 s | 200: 4 | 0 | 6 |
| GET /learning | 5 | 36.62 s | 56.22 s | 56.22 s | 56.22 s | 25.46 s | 200: 5 | 0 | 3 |
| POST /exports/create | 5 | 47.75 s | 85.66 s | 85.66 s | 85.66 s | 85.64 s | 200: 3, 400: 1 | 1 | 4 |
| GET /purchase-orders | 4 | 12.08 s | 37.92 s | 37.92 s | 37.92 s | 11.67 s | 200: 3 | 0 | 2 |
| GET /search/export.xlsx | 3 | - | - | - | - | 231.27 s |  | 0 | 3 |
| POST /review/{id}/reject | 3 | 45.24 s | 52.59 s | 52.59 s | 52.59 s | 2.94 s | 200: 3 | 0 | 2 |
| GET /admin/users | 1 | 20.95 s | 20.95 s | 20.95 s | 20.95 s | 3.04 s | 200: 1 | 0 | 0 |
| GET /settings | 1 | 3.21 s | 3.21 s | 3.21 s | 3.21 s | 3.20 s | 200: 1 | 0 | 0 |

## Server processes

| process | CPU s | mean cores | peak cores | RSS MB first → last (peak) |
|---|---:|---:|---:|---|
| model | 19.2 | 0.02 | 2.27 | 21.8 → 17.7 (30.4) |
| server | 31.5 | 0.04 | 3.09 | 61.6 → 49.7 (61.6) |
| background | 125.7 | 0.15 | 3.26 | 246.4 → 179.0 (260.7) |
| resource tracker | 0.0 | 0.0 | 0.0 | 15.4 → 9.9 (15.4) |
| worker 1 | 246.0 | 0.87 | 6.32 | 127.4 → 0.0 (3243.4) |
| worker 2 | 342.5 | 0.85 | 18.12 | 127.1 → 0.0 (4637.6) |
| worker 3 | 488.1 | 0.99 | 27.83 | 125.1 → 0.0 (5481.7) |
| worker 4 | 658.8 | 0.76 | 8.45 | 125.0 → 0.0 (4413.6) |
| worker 5 | 293.7 | 1.2 | 2.62 | 55.9 → 0.0 (5233.4) |
| worker 6 | 294.9 | 0.88 | 4.25 | 95.2 → 4530.1 (4535.2) |
| worker 7 | 288.5 | 0.77 | 3.47 | 49.8 → 3880.8 (3883.4) |
| worker 8 | 114.6 | 0.5 | 2.24 | 46.6 → 1534.4 (1620.2) |
| worker 9 | 0.6 | 0.02 | 0.28 | 85.2 → 0.0 (85.2) |

## Over time (a row a minute)

| from | requests/s | errors | p95 (worst 10 s) |
|---:|---:|---:|---:|
| 0 min | 37.7 | 0 | 6.88 s |
| 1 min | 26.0 | 4 | 45.31 s |
| 2 min | 10.5 | 7 | 86.57 s |
| 3 min | 4.4 | 6 | 101.15 s |
| 4 min | 2.7 | 25 | 116.32 s |
| 5 min | 2.4 | 47 | 168.71 s |
| 6 min | 2.0 | 50 | 141.76 s |
| 7 min | 3.2 | 88 | 120.93 s |
| 8 min | 1.6 | 13 | 110.50 s |
| 10 min | 5.2 | 110 | 15.29 s |
| 11 min | 1.2 | 0 | 68.19 s |

## Correctness after the run

- Approved twice: none; finally approved twice: none; exported twice: none.
- Approved without an approval event: none.
- Summary against a recomputation (2133 invoices: every one the run touched, and a sample): the same.
- Logins paused: none.
- Events in the run: {'downloaded': 7, 'exported': 107, 'final_approved': 12, 'login': 100, 'page_reader_tested': 1, 'parked': 12, 'rejected': 3}; invoices now: {'approved': 95703, 'failed': 298, 'parked': 162, 'pending_approval': 97, 'rejected': 1014, 'review': 2726}; waiting for OvisOCR2 at the end: 10.

## What people did

- approvals not open: parked instead: 15
- export batches (csv): 1
- export batches (jde): 2
- export batches refused: 1
- final approvals: 12
- invoices acted on by someone else meanwhile (409): 3
- logins: 100
- logins refused: 4
- parked: 15
- people stopped at the end, a request still under way: 97
- rejected: 3
- schedule restarted (over a minute behind): 429
- static from the cache: 2186
- scenario admin: 4 done, 1 stopped by an error
- scenario batch: 1 done, 12 stopped by an error
- scenario bulk: 3 done, 11 stopped by an error
- scenario export_search: 4 done, 10 stopped by an error
- scenario look: 19 done, 28 stopped by an error
- scenario queue: 26 done, 48 stopped by an error
- scenario reports: 37 done, 20 stopped by an error
- scenario search: 31 done, 27 stopped by an error
- scenario second: 12 done, 19 stopped by an error
- scenario upload: 0 done, 13 stopped by an error
- scenario work: 24 done, 140 stopped by an error

## Problems

- readonly002 could not log in: 500
- readonly005 could not log in: 500
- clerk019 could not log in: 500
- clerk040 could not log in: 500
