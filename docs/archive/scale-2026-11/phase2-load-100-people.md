# Load after phase 2 part 1: 100 people on 100,000 invoices (5 Nov 2026)

The same settings and seed as the baseline ([baseline-load-100-people.md](baseline-load-100-people.md)): 100 people, 600
s after a 60 s ramp, think 10 s, 4 workers, the stand-in model 1-4 s with 2 slots. The database was seeded again by
phase 2's code with identical counts.

**In short:** twice the requests answered (12,096 against 6,250), no request without an answer (577 before), no 5xx (6),
no 'database is locked' (2), no traceback (80), and the workers' memory flat at 0.8-1.2 GB (3-7 GB before, killed for
memory). Latency is still far off the targets: pages p50 23 s, p95 84 s; actions p95 88 s. The machine is saturated (3.4
of 4 cores). With 4 threads a worker, static files and logins now wait behind slow pages: /static p95 19.6 s (3.2 s),
/fonts 56.7 s (1.1 s). Next: static files cached for good and served without a thread, page images rendered once into a
shared cache, the invoice page's 3.6 MB of lists out of its HTML.

2026-11-05T08:46:52; ramp 60 s, think 10.0 s (median); 100000 invoices in
/tmp/claude-0/-home-user/b4fef7c4-f9cb-52fc-9da2-47ad67a7ebc2/scratchpad/bench_data/S/s100k; models:
ap_coder/bench/fake_model.py, 1-4 s an answer, 2 at once, at http://192.0.2.2:58099/v1; people reach the server at
192.0.2.2. 4 cores, Python 3.12.13, Linux-6.18.44-fc-v236-x86_64-with-glibc2.39.

## Summary
- Requests: 12096 (18.33/s); 5xx 0, without an answer 0, cut off at the end 0, over 30 s 1151; refused (4xx) 40.
- Pages: p50 23.25 s, p95 84.03 s, p99 94.88 s (1401); actions (check, approve, park, reject, final approval): p50 34.21
  s, p95 88.30 s (431). Timed from when each step was due (scheduled start).
- Targets: pages p50 <= 0.3 s NOT met, pages p95 <= 1.5 s NOT met, pages p99 <= 3.0 s NOT met, actions p95 <= 1.0 s NOT
  met.
- Behind schedule: p50 7 ms, p95 53.59 s.
- Harness CPU: 0.075 cores (its event loop 0.073): well under one core, so the figures are the server's. Server started
  in 2.62 s; its stop took 0.96 s (shutdown).
- Server logs: 0 'database is locked', 0 5xx of 12099 lines, 0 tracebacks.

## Gate
- errors: 0 5xx, 0 without an answer, 0 cut off at the end, 1151 over 30 s
- GET /assets/{file}: p95 16.302 s, baseline 0.680 s (24.0x)
- GET /fonts/{file}: p95 56.736 s, baseline 1.083 s (52.4x)
- GET /login: p95 4.142 s, baseline 0.510 s (8.1x)
- GET /login/code: p95 11.688 s, baseline 0.845 s (13.8x)
- GET /review/{id}/page/{n}.png: p95 40.027 s, baseline 18.544 s (2.2x)
- GET /static/{file}: p95 19.607 s, baseline 3.216 s (6.1x)

## Routes

| route | n | p50 | p95 | p99 | max | server p95 | statuses | refused | errors |
|---|---:|---:|---:|---:|---:|---:|---|---:|---:|
| GET /static/{file} | 9178 | 36 ms | 19.61 s | 35.69 s | 82.09 s | 19.78 s | 200: 1381, 304: 7797 | 0 | 136 |
| GET /review | 652 | 26.63 s | 83.73 s | 92.79 s | 102.57 s | 42.64 s | 200: 652 | 0 | 296 |
| GET /review/{id}/page/{n}.png | 385 | 15.88 s | 40.03 s | 51.05 s | 51.86 s | 40.00 s | 200: 385 | 0 | 81 |
| POST /review/{id}/check | 350 | 36.60 s | 88.37 s | 97.55 s | 118.24 s | 52.87 s | 200: 313, 409: 37 | 37 | 207 |
| GET /review/{id} | 279 | 38.43 s | 93.88 s | 103.19 s | 105.05 s | 50.72 s | 200: 279 | 0 | 165 |
| GET /assets/{file} | 200 | 63 ms | 16.30 s | 24.43 s | 28.56 s | 26.51 s | 200: 200 | 0 | 0 |
| GET /fonts/{file} | 200 | 113 ms | 56.74 s | 92.17 s | 94.27 s | 70.19 s | 200: 200 | 0 | 18 |
| GET /search | 122 | 30.84 s | 80.60 s | 93.46 s | 101.87 s | 39.99 s | 200: 122 | 0 | 64 |
| GET /login | 100 | 91 ms | 4.14 s | 9.81 s | 11.31 s | 4.13 s | 200: 100 | 0 | 0 |
| GET /login/code | 100 | 586 ms | 11.69 s | 15.73 s | 21.84 s | 11.58 s | 200: 100 | 0 | 0 |
| POST /login | 100 | 5.80 s | 31.38 s | 62.02 s | 64.55 s | 11.92 s | 200: 100 | 0 | 6 |
| POST /login/code | 100 | 8.56 s | 53.04 s | 62.54 s | 76.58 s | 20.13 s | 200: 100 | 0 | 23 |
| GET /exports | 57 | 23.16 s | 76.07 s | 80.46 s | 80.46 s | 36.93 s | 200: 57 | 0 | 21 |
| POST /review/{id}/park | 47 | 26.31 s | 89.68 s | 95.65 s | 95.65 s | 40.60 s | 200: 47 | 0 | 21 |
| GET /process/status | 35 | 65 ms | 39.09 s | 44.10 s | 44.10 s | 39.05 s | 200: 35 | 0 | 6 |
| GET /process | 32 | 34.83 s | 71.77 s | 74.16 s | 74.16 s | 70.38 s | 200: 32 | 0 | 22 |
| POST /review/{id}/final-approve | 30 | 24.36 s | 68.04 s | 71.06 s | 71.06 s | 35.49 s | 200: 30 | 0 | 7 |
| POST /exports/create | 23 | 35.25 s | 73.85 s | 80.19 s | 80.19 s | 25.27 s | 200: 20, 400: 3 | 3 | 13 |
| POST /process/upload | 16 | 41.19 s | 80.27 s | 80.27 s | 80.27 s | 41.16 s | 200: 16 | 0 | 11 |
| GET /month-end | 14 | 46.67 s | 69.41 s | 69.41 s | 69.41 s | 55.31 s | 200: 14 | 0 | 10 |
| GET /search/export.csv | 14 | 42.86 s | 109.69 s | 109.69 s | 109.69 s | 60.12 s | 200: 14 | 0 | 11 |
| GET /vendors | 10 | 40.23 s | 79.16 s | 79.16 s | 79.16 s | 42.75 s | 200: 10 | 0 | 6 |
| GET /exports/batches/{id}/download | 7 | 17.02 s | 56.42 s | 56.42 s | 56.42 s | 17.51 s | 200: 7 | 0 | 2 |
| GET /learning | 7 | 51.18 s | 86.44 s | 86.44 s | 86.44 s | 44.35 s | 200: 7 | 0 | 7 |
| GET /sales-tax | 7 | 14.30 s | 27.36 s | 27.36 s | 27.36 s | 18.08 s | 200: 7 | 0 | 0 |
| GET /insights | 6 | 5.46 s | 32.95 s | 32.95 s | 32.95 s | 23.91 s | 200: 6 | 0 | 1 |
| GET /search/export.xlsx | 6 | 69.49 s | 124.46 s | 124.46 s | 124.46 s | 104.70 s | 200: 6 | 0 | 5 |
| GET /spend | 6 | 28.88 s | 74.62 s | 74.62 s | 74.62 s | 25.90 s | 200: 6 | 0 | 3 |
| GET /admin/users | 4 | 44.79 s | 80.23 s | 80.23 s | 80.23 s | 51.77 s | 200: 4 | 0 | 3 |
| POST /review/{id}/reject | 4 | 38.85 s | 40.51 s | 40.51 s | 40.51 s | 40.51 s | 200: 4 | 0 | 3 |
| GET /purchase-orders | 3 | 82.92 s | 88.70 s | 88.70 s | 88.70 s | 41.66 s | 200: 3 | 0 | 2 |
| GET /settings | 2 | 23.25 s | 85.98 s | 85.98 s | 85.98 s | 28.66 s | 200: 2 | 0 | 1 |

## Server processes

| process | CPU s | mean cores | peak cores | RSS MB first → last (peak) |
|---|---:|---:|---:|---|
| model | 1.1 | 0.0 | 0.03 | 21.8 → 22.1 (30.3) |
| server | 1.2 | 0.0 | 0.01 | 62.0 → 62.0 (62.0) |
| background | 462.0 | 0.63 | 0.99 | 240.4 → 422.7 (426.9) |
| resource tracker | 0.0 | 0.0 | 0.0 | 15.3 → 15.3 (15.3) |
| worker 1 | 248.4 | 0.34 | 1.36 | 128.4 → 823.1 (860.7) |
| worker 2 | 246.2 | 0.33 | 1.26 | 127.5 → 866.6 (927.0) |
| worker 3 | 719.6 | 0.97 | 1.86 | 124.7 → 1169.3 (1220.3) |
| worker 4 | 769.2 | 1.04 | 2.05 | 126.0 → 1076.0 (1150.0) |

## Over time (a row a minute)

| from | requests/s | errors | p95 (worst 10 s) |
|---:|---:|---:|---:|
| 0 min | 33.8 | 0 | 13.62 s |
| 1 min | 21.6 | 0 | 57.23 s |
| 2 min | 17.8 | 0 | 73.09 s |
| 3 min | 18.1 | 0 | 50.47 s |
| 4 min | 17.6 | 0 | 53.04 s |
| 5 min | 10.7 | 0 | 76.55 s |
| 6 min | 10.8 | 0 | 71.77 s |
| 7 min | 15.3 | 0 | 71.14 s |
| 8 min | 14.8 | 0 | 65.46 s |
| 9 min | 13.1 | 0 | 83.09 s |
| 10 min | 14.9 | 0 | 76.96 s |
| 11 min | 12.3 | 0 | 73.68 s |
| 12 min | 2.6 | 0 | 109.69 s |

## Correctness after the run

- Approved twice: none; finally approved twice: none; exported twice: none.
- Approved without an approval event: none.
- Summary against a recomputation (2485 invoices: every one the run touched, and a sample): the same.
- Logins paused: none.
- Events in the run: {'downloaded': 27, 'exported': 420, 'final_approved': 30, 'import_run': 16, 'login': 100,
  'page_reader_tested': 1, 'parked': 44, 'processed': 16, 'rejected': 4}; invoices now: {'approved': 95721, 'failed':
  298, 'parked': 195, 'pending_approval': 79, 'rejected': 1015, 'review': 2708}; waiting for OvisOCR2 at the end: 19.

## What people did

- approvals not open: parked instead: 45
- export batches (csv): 18
- export batches (jde): 2
- export batches refused: 3
- final approvals: 30
- invoices acted on by someone else meanwhile (409): 37
- invoices left with a draft: 3
- logins: 100
- parked: 47
- rejected: 4
- schedule restarted (over a minute behind): 541
- second approvals not theirs to give: 2
- static from the cache: 5204
- uploads: 16
- scenario admin: 12 done, 0 stopped by an error
- scenario batch: 23 done, 0 stopped by an error
- scenario bulk: 18 done, 0 stopped by an error
- scenario export_search: 20 done, 0 stopped by an error
- scenario look: 74 done, 0 stopped by an error
- scenario queue: 106 done, 0 stopped by an error
- scenario reports: 58 done, 0 stopped by an error
- scenario search: 100 done, 0 stopped by an error
- scenario second: 32 done, 0 stopped by an error
- scenario upload: 16 done, 0 stopped by an error
- scenario work: 240 done, 0 stopped by an error

