# Phase 8 part 2 measured: 100 people on 300,000 invoices for an hour, with the background refresher (7 Nov 2026)

4 cores, Python 3.12 with both OCR models, the bench's stand-in model server (1-4 s an answer, 2 at once).
`python -m ap_coder.bench load --users 100 --ramp 60 --think 10 --workers 4 --data <300k database> --sample-every 10`
(30 for the hour), `--duration 600`, then `--duration 3600` on the same database. The database made afresh by `bench
seed --invoices 300000` at the code measured, nothing else running. Measured twice: at 8e3915a (D-198 and its review
fixes) and at the final code, 963a7cc (D-199 and B-2049 too).

## At the final code (963a7cc)

| run | requests | pages p50 / p95 / p99 | actions p95 | over 30 s | 5xx | 'database is locked' |
|---|---:|---|---:|---:|---:|---:|
| 100k, 10 min (the s100k database, served before) | 14,300 | 70 ms / 276 ms / 484 ms | 159 ms | 0 | 0 | 0 |
| 300k, 10 min, first start of the database | 15,413 | 129 ms / 1.17 s / 5.78 s | 660 ms | 7 | 0 | 0 |
| **300k, the hour after it** | **74,167** | **102 ms / 653 ms / 2.02 s** | **389 ms** | **0** | **0** | **0** |
| 300k, 10 min, 200 people (the stretch), after the hour | 39,611 | 360 ms / 5.30 s / 30.08 s | 9.82 s | 147 | 0 | 0 |

**Every target is met over the hour at 300,000 invoices and in the 100,000 run.** The server's logs: 0 'database is
locked', 0 5xx, 0 tracebacks; approved, finally approved or exported twice: none; the summary equal to a
recomputation for every invoice the runs touched.

- **The hour:** no minute's worst 10 s over 2.18 s at p95 (minute 2). The server took 5 s or more on 16 of 74,174
  requests, 13 of them opens and checks of the biggest vendor's invoices on its standing PO (3,414 invoices): under
  load the PO match shares the worker with the other requests (`po_invoices` 5.1% of the busiest worker's CPU, was
  10.9% before D-199). The stack dumps of every web worker every 2 s from +90 to +600 s show each one's event loop
  waiting in `select` and the requests in its threads: no stall, the requests sharing the one running thread.
- **Memory over the hour:** web workers 712-735 MB at the end (737 at most), growing −4.3 to +9.7 MB a 10 minutes in
  the second half; the background process 300 → 525 MB (+1.4 in the second half); the capture processes 321 MB, flat.
- **The first 10 minutes of a database never served:** p95 1.17 s, but p99 5.78 s and 7 over 30 s: every kept
  answer is worked out a first time. Month-end's lines by invoice are built once (D-194; four asks at 17:18:24, the
  first worked out and the others waited for it: 12-50 s), Spend 15.8 s, Sales tax 9-11 s, Learning 6-12 s, each
  once. A database served before starts with its answers; a database's first start on this version pays it once.
- **The stretch, 200 people** (`--users 200`, the rest as above): not met here. 200 people on 4 cores is 50 a core,
  four times what 400 people put on each of the next stage's 32 vCPUs (100 here is twice). The cores were full: the
  web workers 0.51-0.53 cores each, the background process 0.70, the capture processes 0.44 each (3.6 of 4), and the
  wait grew from the seventh minute (the worst 10 s at p95: 11-31 s). 0 5xx, 0 'database is locked', nothing approved,
  finally approved or exported twice, the summary equal to a recomputation.
- **Where the CPU went at 30 minutes:** the busiest web worker: the check 22.5%, the queue 20%, the invoice page
  16%, search 15%; the background process: the kept answers worked out again 51%, the search downloads 32%.

## The first measurement (8e3915a)

| run | requests | pages p50 / p95 / p99 | actions p95 | over 30 s | 5xx | 'database is locked' |
|---|---:|---|---:|---:|---:|---:|
| phase 8 run 2 (fd60164), the hour (for comparison) | 72,477 | 119 ms / 1.49 s / 5.50 s | 996 ms | 2 | 0 | 0 |
| part 2 (8e3915a), 10 min, first start of the database | 15,363 | 146 ms / 1.12 s / 5.09 s | 663 ms | 6 | 0 | 0 |
| **part 2 (8e3915a), the hour after it** | **73,564** | **114 ms / 793 ms / 2.52 s** | **572 ms** | 4 | 0 | 0 |

Targets: pages p50 ≤ 300 ms, p95 ≤ 1.5 s, p99 ≤ 3 s; actions p95 ≤ 1 s; 0 errors. **Over the hour every one is met.**
The server's logs: 0 'database is locked', 0 5xx, 0 tracebacks. Approved twice, finally approved twice, exported
twice: none.

The first ten minutes after a database's first start are still slower at p99 (5.09 s; run 2's 16.9 s): every answer
kept for every process is worked out a first time, and with nobody's answer yet to show, the first to ask works it
out (D-198: the background process works out only an answer that has been asked once). Its 6 over 30 s are the
first month-end, Exports, Vendors and Spend pages (p99 25-39 s), each once. A database served before (a restart, an
update) starts with its answers and the refresher works out again those asked in the last day.

## What changed the hour's p99 (5.50 → 2.52 s, 8e3915a)

D-198: a settled answer whose time is up (Spend, Month-end, Vendors' totals, Insights, Sales tax, Learning's coding
accuracy, the recurring vendors) is worked out again by the background process; the request takes the last one at
once. The report pages' server p95 over the hour: Spend 104 ms (run 2: 11.4 s at p95), Month-end 328 ms (8.7 s),
Vendors 400 ms (6.0 s), Insights 153 ms (6.0 s), Sales tax 145 ms (5.8 s). The queue, the invoice and the check no
longer wait behind them in a web worker: `GET /review` p99 1.76 s (3.62 s), the invoice 3.39 s (4.5 s), the check
3.11 s (3.41 s).

| route | n | p50 | p95 | p99 | max | server p95 |
|---|---:|---:|---:|---:|---:|---:|
| GET /review | 15,212 | 105 ms | 530 ms | 1.76 s | 41.58 s | 430 ms |
| POST /review/{id}/check | 12,785 | 78 ms | 615 ms | 3.11 s | 20.56 s | 522 ms |
| GET /review/{id} | 7,048 | 139 ms | 1.13 s | 3.39 s | 23.69 s | 1.01 s |
| GET /search | 3,484 | 242 ms | 1.30 s | 2.70 s | 19.74 s | 1.12 s |
| GET /vendors | 331 | 96 ms | 515 ms | 4.89 s | 30.55 s | 400 ms |
| GET /month-end | 254 | 80 ms | 430 ms | 1.60 s | 12.84 s | 328 ms |
| GET /spend | 278 | 31 ms | 162 ms | 688 ms | 5.65 s | 104 ms |
| GET /sales-tax | 283 | 39 ms | 188 ms | 3.84 s | 10.06 s | 145 ms |
| GET /learning | 297 | 102 ms | 467 ms | 7.23 s | 17.35 s | 363 ms |
| GET /purchase-orders | 209 | 414 ms | 1.22 s | 2.01 s | 6.18 s | 1.15 s |
| GET /activity | 65 | 869 ms | 1.98 s | 3.58 s | 3.58 s | 1.94 s |

## The 4 requests over 30 s, and D-199

Three queue pages (`GET /review`, up to 41.6 s) and a Vendors page (30.6 s), in the hour's first minutes. The server's
own times for them were under 3 s: they waited for a web worker. Just before, every web worker was opening and
checking invoices of the biggest vendor (23,174 invoices), whose standing PO is billed by 3,381 of them: in the
server's logs, from 14:36:56 to 14:37:23 (minute 2 to 3), 17 opens and checks of one of its invoices took 4-15 s each.
Each one matched the invoice against its PO, which read every other invoice on the PO with its whole coding (1.5 s)
and matched their lines twice (0.25 s). `po_invoices` was 10.9% of the busiest web worker's CPU at 30 minutes. D-199
(963a7cc, after these runs) reads out only the subtotal, date and lines, and matches each (description, unit price)
once: 1.75 → 0.3 s for that PO, with the same results. The wait itself (up to 40 s before a worker took the
request, longer than the work in the logs) is not explained by these logs. At the final code (above) the hour had no
request over 30 s, and the stack dumps of its first ten minutes show no stall.

## Memory over the hour (8e3915a)

| process | RSS MB first → last (peak) | MB a 10 minutes: whole run, second half |
|---|---|---|
| web worker 1 | 394 → 773 (814) | +23.2, +2.3 |
| web worker 2 | 267 → 753 (803) | +31.8, +2.3 |
| web worker 3 | 435 → 774 (776) | +22.5, +13.5 |
| web worker 4 | 235 → 741 (769) | +17.9, +8.4 |
| background | 273 → 516 (559) | +15.8, +4.5 |
| capture (2) | 290 → 319, 134 → 319 (320) | −0.2, −0.2 |

Flat once the caches fill: the web workers end at 741-774 MB (run 2: 939-988), growing 2-14 MB a 10 minutes in the
second half, slower and slower; the background process, which now works out the report answers, at 516 MB. (The
bench's own table said the workers ended at 0.0 MB with falling slopes: its last sample, taken as the run stopped,
found them ended but not yet waited for. B-2049 fixed the bench; the figures above leave that sample out.)

## Where the CPU went (py-spy at 30 minutes, 8e3915a)

- **The busiest web worker:** the queue 22%, the invoice page 22% (of which the PO match's `po_invoices` 11%:
  D-199), the check 22%, search 8.5%, the Import invoices listing 4.8%; the report pages under 1% each.
- **The background process:** kept answers worked out again 51% (the refresher: Vendors' totals, recurring, Spend),
  the search downloads 31% (CSV and Excel of up to 25,000 rows).
