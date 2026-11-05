# Load test: 100 people for 600 s on 4 worker(s)

2026-11-05T04:24:04; ramp 60 s, think 10.0 s (median); 100000 invoices in (the seeded 100k folder); models: ap_coder/bench/fake_model.py, 1-4 s an answer, 2 at once, at http://192.0.2.2:35027/v1; people reach the server at 192.0.2.2. 4 cores, Python 3.12.13, Linux-6.18.44-fc-v236-x86_64-with-glibc2.39.

## Summary
- Requests: 6250 (9.47/s); 5xx 6, without an answer 577, cut off at the end 0, over 30 s 370; refused (4xx) 12.
- Pages: p50 27.26 s, p95 140.44 s, p99 166.55 s (1166); actions (check, approve, park, reject, final approval): p50 41.67 s, p95 120.00 s (294). Timed from when each step was due (scheduled start).
- Targets: pages p50 <= 0.3 s NOT met, pages p95 <= 1.5 s NOT met, pages p99 <= 3.0 s NOT met, actions p95 <= 1.0 s NOT met.
- Behind schedule: p50 1 ms, p95 46.18 s.
- Harness CPU: 0.072 cores (its event loop 0.038): well under one core, so the figures are the server's. Server started in 2.27 s; its stop took 28.3 s (shutdown).
- Server logs: 2 'database is locked', 80 5xx of 5782 lines, 80 tracebacks.

## Gate
- errors: 6 5xx, 577 without an answer, 0 cut off at the end, 370 over 30 s
- server logs: 2 'database is locked', 80 5xx
- 7 logins refused

## Routes

| route | n | p50 | p95 | p99 | max | server p95 | statuses | refused | errors |
|---|---:|---:|---:|---:|---:|---:|---|---:|---:|
| GET /static/{file} | 4020 | 226 ms | 3.22 s | 120.03 s | 141.68 s | 1.91 s | 200: 1191, 304: 2741 | 0 | 143 |
| GET /review | 615 | 120.00 s | 152.22 s | 167.10 s | 184.59 s | 189.12 s | 200: 250, 500: 5 | 0 | 378 |
| POST /review/{id}/check | 256 | 43.74 s | 120.00 s | 136.37 s | 143.32 s | 68.34 s | 200: 224, 409: 10 | 10 | 177 |
| GET /assets/{file} | 200 | 49 ms | 680 ms | 1.78 s | 2.36 s | 648 ms | 200: 200 | 0 | 0 |
| GET /fonts/{file} | 200 | 19 ms | 1.08 s | 1.75 s | 2.13 s | 1.13 s | 200: 200 | 0 | 0 |
| GET /review/{id}/page/{n}.png | 150 | 933 ms | 18.54 s | 37.80 s | 46.65 s | 18.51 s | 200: 150 | 0 | 3 |
| GET /review/{id} | 119 | 26.38 s | 120.00 s | 120.00 s | 120.00 s | 68.77 s | 200: 112 | 0 | 55 |
| GET /login | 105 | 15 ms | 510 ms | 1.14 s | 2.83 s | 402 ms | 200: 101, 303: 4 | 0 | 0 |
| GET /search | 105 | 28.13 s | 149.06 s | 163.61 s | 164.49 s | 129.65 s | 200: 71 | 0 | 52 |
| GET /login/code | 101 | 64 ms | 845 ms | 2.14 s | 2.33 s | 569 ms | 200: 101 | 0 | 0 |
| POST /login | 101 | 486 ms | 29.25 s | 36.30 s | 73.97 s | 29.18 s | 200: 101 | 0 | 4 |
| POST /login/code | 101 | 450 ms | 39.68 s | 46.15 s | 68.11 s | 28.27 s | 200: 100, 500: 1 | 0 | 13 |
| GET /exports | 36 | 93.94 s | 159.99 s | 173.56 s | 173.56 s | 134.18 s | 200: 20 | 0 | 29 |
| POST /review/{id}/park | 21 | 37.29 s | 120.00 s | 120.00 s | 120.00 s | 51.37 s | 200: 19 | 0 | 12 |
| GET /process | 17 | 120.03 s | 166.58 s | 166.58 s | 166.58 s | 195.26 s |  | 0 | 17 |
| GET /vendors | 14 | 62.40 s | 134.32 s | 134.32 s | 134.32 s | 106.29 s | 200: 11 | 0 | 11 |
| POST /review/{id}/final-approve | 14 | 3.17 s | 57.75 s | 57.75 s | 57.75 s | 36.04 s | 200: 14 | 0 | 3 |
| GET /month-end | 12 | 120.02 s | 163.53 s | 163.53 s | 163.53 s | 159.34 s |  | 0 | 12 |
| GET /sales-tax | 10 | 37.98 s | 127.06 s | 127.06 s | 127.06 s | 129.42 s | 200: 6 | 0 | 6 |
| GET /learning | 9 | 69.19 s | 120.00 s | 120.00 s | 120.00 s | 92.69 s | 200: 8 | 0 | 6 |
| GET /search/export.csv | 9 | 77.10 s | 123.67 s | 123.67 s | 123.67 s | 46.52 s | 200: 6 | 0 | 7 |
| GET /spend | 8 | 37.11 s | 113.96 s | 113.96 s | 113.96 s | 37.37 s | 200: 8 | 0 | 7 |
| GET /insights | 7 | 70.35 s | 120.05 s | 120.05 s | 120.05 s | 143.94 s | 200: 4 | 0 | 5 |
| POST /exports/create | 7 | 30.61 s | 76.74 s | 76.74 s | 76.74 s | 35.59 s | 200: 5, 400: 2 | 2 | 4 |
| GET /purchase-orders | 6 | 14.42 s | 120.22 s | 120.22 s | 120.22 s | 15.55 s | 200: 4 | 0 | 3 |
| POST /review/{id}/reject | 3 | 56.64 s | 145.23 s | 145.23 s | 145.23 s | 35.64 s | 200: 2 | 0 | 3 |
| GET /search/export.xlsx | 2 | 30.28 s | 120.03 s | 120.03 s | 120.03 s | 130.31 s | 200: 1 | 0 | 2 |
| GET /admin/users | 1 | 3.28 s | 3.28 s | 3.28 s | 3.28 s | 2.29 s | 200: 1 | 0 | 0 |
| GET /settings | 1 | 120.16 s | 120.16 s | 120.16 s | 120.16 s | - |  | 0 | 1 |

## Server processes

| process | CPU s | mean cores | peak cores | RSS MB first → last (peak) |
|---|---:|---:|---:|---|
| model | 16.8 | 0.02 | 4.18 | 21.7 → 18.0 (30.2) |
| server | 23.7 | 0.03 | 6.4 | 61.7 → 50.2 (61.7) |
| background | 26.3 | 0.03 | 9.23 | 246.2 → 181.4 (260.6) |
| resource tracker | 0.0 | 0.0 | 0.0 | 15.3 → 10.2 (15.3) |
| worker 1 | 245.7 | 0.82 | 1.34 | 127.8 → 0.0 (3587.8) |
| worker 2 | 235.5 | 0.79 | 1.75 | 125.4 → 0.0 (3388.5) |
| worker 3 | 342.8 | 0.85 | 2.77 | 125.3 → 0.0 (5574.9) |
| worker 4 | 457.9 | 0.91 | 15.47 | 126.9 → 0.0 (6893.1) |
| worker 5 | 204.5 | 0.88 | 16.99 | 49.1 → 0.0 (5537.6) |
| worker 6 | 305.0 | 0.89 | 40.0 | 15.5 → 0.0 (5643.0) |
| worker 7 | 181.3 | 0.67 | 18.57 | 45.3 → 0.0 (5415.9) |
| worker 8 | 119.2 | 0.69 | 21.82 | 10.7 → 0.0 (4464.8) |
| worker 9 | 270.3 | 1.0 | 9.51 | 45.4 → 5747.3 (5749.2) |
| worker 10 | 273.5 | 1.7 | 3.64 | 38.1 → 6052.2 (6052.2) |
| worker 11 | 0.3 | 0.0 | 0.03 | 119.6 → 0.0 (123.5) |
| worker 12 | 0.4 | 0.0 | 0.06 | 120.6 → 0.0 (123.2) |

## Over time (a row a minute)

| from | requests/s | errors | p95 (worst 10 s) |
|---:|---:|---:|---:|
| 0 min | 44.0 | 0 | 4.15 s |
| 1 min | 24.2 | 1 | 48.39 s |
| 2 min | 12.0 | 5 | 120.01 s |
| 3 min | 5.7 | 3 | 89.81 s |
| 4 min | 3.2 | 77 | 173.56 s |
| 5 min | 1.9 | 15 | 152.02 s |
| 6 min | 2.2 | 89 | 179.43 s |
| 7 min | 0.8 | 36 | 165.87 s |
| 8 min | 3.0 | 108 | 158.36 s |
| 9 min | 0.1 | 0 | 8.50 s |
| 10 min | 3.1 | 146 | 165.88 s |
| 11 min | 0.7 | 5 | 86.73 s |
| 12 min | 3.0 | 95 | 142.75 s |
| 13 min | 1.4 | 3 | 143.39 s |

## Correctness after the run

- Approved twice: none; finally approved twice: none; exported twice: none.
- Approved without an approval event: none.
- Summary against a recomputation (2189 invoices: every one the run touched, and a sample): the same.
- Logins paused: none.
- Events in the run: {'downloaded': 8, 'exported': 157, 'final_approved': 14, 'login': 100, 'page_reader_tested': 1, 'parked': 17, 'rejected': 2}; invoices now: {'approved': 95705, 'failed': 298, 'parked': 167, 'pending_approval': 95, 'rejected': 1013, 'review': 2722}; waiting for OvisOCR2 at the end: 10.

## What people did

- approvals not open: parked instead: 18
- export batches (csv): 2
- export batches (jde): 3
- export batches refused: 2
- final approvals: 14
- invoices acted on by someone else meanwhile (409): 10
- logins: 100
- logins refused: 7
- parked: 19
- rejected: 2
- schedule restarted (over a minute behind): 546
- static from the cache: 2344
- scenario admin: 3 done, 1 stopped by an error
- scenario batch: 3 done, 15 stopped by an error
- scenario bulk: 3 done, 16 stopped by an error
- scenario export_search: 7 done, 13 stopped by an error
- scenario look: 23 done, 43 stopped by an error
- scenario queue: 31 done, 88 stopped by an error
- scenario reports: 43 done, 32 stopped by an error
- scenario search: 42 done, 42 stopped by an error
- scenario second: 9 done, 43 stopped by an error
- scenario upload: 0 done, 17 stopped by an error
- scenario work: 35 done, 200 stopped by an error

## Problems

- clerk052 could not log in: 500
- clerk059 could not log in: timeout
- clerk069 could not log in: timeout
- clerk059 could not log in: RemoteProtocolError
- clerk069 could not log in: timeout
- clerk059 could not log in: timeout
- clerk069 could not log in: timeout
