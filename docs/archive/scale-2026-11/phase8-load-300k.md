# Phase 8 measured: 100 people on 300,000 invoices, and an hour of it (7 Nov 2026)

4 cores, Python 3.12 with both OCR models, the bench's stand-in model server (1-4 s an answer, 2 at once).
`python -m ap_coder.bench load --users 100 --ramp 60 --think 10 --workers 4 --data <300k database> --sample-every 10`
(30 for the hour), `--duration 600`, then `--duration 3600` on the same database. The database made by `bench seed
--invoices 300000` at the code measured (9.7 GB), nothing else running.

## The runs

| run | requests | pages p50 / p95 / p99 | actions p95 | over 30 s | 5xx | 'database is locked' |
|---|---:|---|---:|---:|---:|---:|
| phase 3 part 3, 10 min (for comparison) | 13,658 | 4.17 s / 46.07 s / 66.19 s | 47.42 s | 957 | 96 | 0 |
| 1 (761eb35), 10 min, first start of the database | 15,591 | 185 ms / 3.14 s / 11.65 s | 1.38 s | 8 | 0 | 0 |
| 1 (761eb35), the hour after it | 71,919 | 115 ms / 1.78 s / 6.76 s | 1.10 s | 0 | 0 | 0 |
| 2 (fd60164: B-2042, D-197), 10 min, first start | 15,206 | 175 ms / 2.98 s / 16.87 s | 1.85 s | 19 | 3 (503 busy) | 0 |
| **2 (fd60164), the hour after it** | **72,477** | **119 ms / 1.49 s / 5.50 s** | **996 ms** | 2 | 0 | 0 |

Targets: pages p50 ≤ 300 ms, p95 ≤ 1.5 s, p99 ≤ 3 s; actions p95 ≤ 1 s; 0 errors. Over the hour: p50, p95 and the
actions met; p99 not (5.50 s). The first ten minutes after a database's first start are slower: every answer kept for
every process is worked out for the first time (none has a work time yet to settle by, D-197), and the queue is checked
again once against the server's settings (the seed's checks were made under others). Its three 5xx are the reports
lane's "AP Coder is busy, try again in a minute" (`/exports` once, `/sales-tax` twice: a report waited over 30 s for
its turn).

## What the hour's p99 is (run 2)

| route | n | p50 | p95 | p99 | server p95 |
|---|---:|---:|---:|---:|---:|
| GET /review | 15,170 | 104 ms | 767 ms | 3.62 s | 560 ms |
| POST /review/{id}/check | 12,895 | 83 ms | 1.07 s | 3.41 s | 812 ms |
| GET /review/{id} | 7,127 | 146 ms | 1.54 s | 4.46 s | 1.33 s |
| GET /search | 3,551 | 238 ms | 1.36 s | 3.20 s | 1.21 s |
| GET /spend | 283 | 48 ms | 11.42 s | 16.28 s | 11.36 s |
| GET /month-end | 251 | 689 ms | 8.89 s | 13.39 s | 8.67 s |
| GET /vendors | 305 | 2.00 s | 6.43 s | 7.74 s | 5.98 s |
| GET /insights | 289 | 47 ms | 6.11 s | 8.85 s | 6.00 s |
| GET /sales-tax | 252 | 57 ms | 5.80 s | 7.73 s | 5.79 s |

The analytics pages work their answers out in the worker of the person who asks first once the answer has settled
(Spend's figures about 11 s at 300,000, settled for ten times that since D-197), and while they do, that worker's other
requests wait behind its one running thread: the queue's and the invoice page's p99 of 3.4-4.5 s. Next step (not
done, SCALE.md): those answers worked out by the background process (a function for each, by name, from what was
asked), the page always taking the last one, so no person and no web worker waits on them; and worked out once at a
start.

## Memory

| process | run 1 hour: RSS first → last (peak) | second half, MB a 10 min | run 2 hour: first → last (peak) | second half |
|---|---|---:|---|---:|
| web worker 1 | 279 → 1,004 (1,129) | +29.8 | 264 → 944 (980) | +10.5 |
| web worker 2 | 428 → 937 (1,033) | −1.0 | 471 → 988 (1,025) | +16.8 |
| web worker 3 | 230 → 934 (946) | +22.8 | 567 → 939 (1,028) | +18.7 |
| web worker 4 | 422 → 1,023 (1,157) | +16.9 | 382 → 988 (1,023) | −4.1 |
| background | 116 → 155 (158) | −0.2 | 114 → 153 (157) | −0.3 |
| capture (2) | 290/134 → 319 (320) | +0.1/+0.2 | 290/134 → 321 (321) | +0.0/+0.1 |

Under the 1,500 MB at which a worker is started afresh, by about 500 MB. The workers grow by up to about 19 MB in ten
minutes in the hour's second half (an hour: about 110 MB at that rate), slower and slower: their caches still filling
toward their bounds (D-195). In run 1's first ten minutes the background process reached 4,774 MB (a session's memo
holding every vendor's invoices at each change, B-2042); in run 2's, 438 MB.

## Where the workers' time went (run 1's profile, py-spy at +240 s for 120 s)

- A web worker: `shared_answer` working answers out again 46% (Spend's figures 27%, the Vendors page 20%), then the
  queue, the invoice page and the check: D-197 settles the first by its own work.
- The background process: the queue checked again after the change of settings 57%, the search exports 37%.
