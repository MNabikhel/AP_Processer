# Phase 5 measured: 100 people on one queue, 100,000 invoices (7 Nov 2026)

On the integration branch at e51646c (phase 5 with its review's fixes), 4 cores, Python 3.12 with both OCR models.
`python -m ap_coder.bench load --users 100 --duration 600 --ramp 60 --think 10 --workers 4 --data <100k database>`
with phase 4's rerun as the baseline (`--baseline`).

## The two 10-minute runs

| run | requests | pages p50 / p95 / p99 | actions p95 | over 30 s | 5xx | 'database is locked' |
|---|---:|---|---:|---:|---:|---:|
| 1 (a database made hours before by phase 4's code) | 14,252 | 281 ms / 35.96 s / 55.21 s | 35.71 s | 609 | 17 | 2 |
| 2 (a database made by phase 4's code, minutes old, used by three short runs) | 14,149 | **143 ms / 809 ms / 2.31 s** | **452 ms** | 0 | 0 | 0 |
| phase 4's rerun (for comparison) | 14,100 | 129 ms / 636 ms / 1.89 s | 296 ms | 0 | 0 | 0 |

Run 2 meets every target. Run 1's whole difference is its first 4 minutes (minute by minute, the worst 10 s's p95:
27 s, 54 s, 54 s, 28 s, then 0.6-1.4 s for the rest, as run 2).

## Run 1's stall

- In the access logs, the run's first `GET /review` (worker 4, 23:41:13) took 18.7 s, and every `GET /review` of the
  four workers that came after it finished in the same second as it (23:41:31): they waited on something the
  processes share (the database's write lock, or an answer kept for every process), not each on its own work.
- A sign-in's `BEGIN IMMEDIATE` waited out its 30 s (`database is locked`) a minute later: the write lock was held, or
  taken by others ahead of it, for 30 s.
- The workers did 0.4-0.5 of a core each over the run: they waited, they did not compute.
- **Not reproduced** in four more runs of 3-4 minutes with the same load: on a database made by phase 5's code; on one
  made by phase 4's code (as run 1's was); and twice from a cold file cache (`drop_caches` before the start). None
  stalled (pages p95 0.95-1.16 s, actions 0.46-0.58 s, 0 errors). Stack dumps of every process every 2 s during the
  first 70 s of the reproductions show nothing waiting.
- **The one difference left:** run 1's database was made hours before it ran, as was the one of phase 4's first run,
  which also stalled in its second minute (B-1914, fixed, was a part of that). Every run on a database made minutes
  before was clean. **Open, for phase 8:** the load run again on a database left for hours, with stack dumps every 2 s
  in its first minute.

## Phase 4's code against phase 5's, on the same database, one after the other

240 s each, the four workers recorded by py-spy for 120 s from the 75th second.

| code | requests | pages p50 / p95 / p99 | actions p50 / p95 |
|---|---:|---|---|
| phase 4 (main, 1696f20) | 7,236 | 153 ms / 838 ms / 2.41 s | 70 / 494 ms |
| phase 5 (e51646c) | 7,204 | 122 ms / 670 ms / 2.52 s | 54 / 342 ms |

The workers' time by page: the queue 27.0% and 25.8%, the invoice page 8.1% and 9.4%, a check 8.2% and 7.8%. Phase 5's
own additions each cost 1-5 ms at 100k: an invoice's revision, who has what open, the queue's bulk stamps for 2,317
invoices, the last change. Run 2's routes were slower than phase 4's rerun (the invoice page's mean at the server 146
→ 218 ms, a check 82 → 116 ms) on another database and at another time; on the same one, as here, they are not.

## Phase 5's own costs

- Presence: 1.38 writes a second at 100 people (S1's run on a 20k database: 413 in 300 s; write transactions 13.2 →
  14.2 a second); one write for each invoice opened and one a minute for each screen left open, about 7 a second at
  400 people beyond the opens.
- Bulk approval of 200 invoices: 6.2 s in the background (the request answered in 3 s), against 6.6 s inside one
  request before (`scripts/ci/perf_timing.py --invoices 600 --bulk 200`).

## Nothing worse

- `scripts/same_behaviour.py --ref origin/main approve pipeline`: both the same.
- The laptop walked: 61 pages and the demo's invoices, 0 errors, none over 1 s.
- The server's role walk-throughs in a browser: 175 passed (the clerk's and the pilot tester's walks, the role pages,
  the second approval, many people on one invoice, bulk approval, the manager's page).
- The default tier: 4,885 passed.
