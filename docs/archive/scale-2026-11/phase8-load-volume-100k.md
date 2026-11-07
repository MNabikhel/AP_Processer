# Phase 8 measured: 100 people and one person on 100,000 invoices (7 Nov 2026)

At fd60164 (all of phase 8 and its reviews' fixes), 4 cores, Python 3.12 with both OCR models, nothing else running.

## 100 people, 10 minutes, on a database made four hours before and never served

`python -m ap_coder.bench load --users 100 --duration 600 --ramp 60 --think 10 --workers 4 --data <100k database>
--baseline <phase 5's run 2> --sample-every 10`, with every server process's stack dumped every 2 s for the first 70 s.

| run | requests | pages p50 / p95 / p99 | actions p95 | over 30 s | 5xx | 'database is locked' |
|---|---:|---|---:|---:|---:|---:|
| phase 5, run 2 (a database served by earlier runs) | 14,149 | 143 ms / 809 ms / 2.31 s | 452 ms | 0 | 0 | 0 |
| **phase 8 (a database never served)** | **14,271** | **90 ms / 580 ms / 2.63 s** | **342 ms** | 6 | 0 | 0 |

Every latency target met. The 6 requests over 30 s all came in the first 90 s: the first month-end page on a database
never served builds month-end's lines by invoice for every vendor (D-194: about 45 s at 100,000; the dumps show one
worker in `billing_codings` and another waiting for that shared answer), and four month-end pages finished together
at 48.6 s; Activity's and Learning's first answers took 12 and 14 s. From the third minute to the tenth the worst 10 s of
each minute had a p95 of 0.35-0.66 s. This is phase 5's first-minutes stall too: its run 1 was the one on a database never served.
In a real installation it comes once, the first visit after the update that brings D-194 (and a first start), which is
why phase 8 leaves "answers worked out at a start by the background process" as the next step (SCALE.md).

The web workers ended at 616-676 MB (peak 704), the background process at 390 MB, the capture processes at 320 MB.

The gate's slower-than-baseline routes were these first answers (`/month-end` p95 11.9 s against 4.75, `/activity`
12.1 s, a single first visit among 11, against 0.80 s) and a few small ones (`/exports` 0.84 s against 0.29,
`/login/code` 0.29 s against 0.16).

## One person, every address (`bench volume`, the database made again)

173 addresses asked as an Admin and a Clerk, the median of 3 after a warm request: **3 over target, 0 errors**:
the Activity CSV as an Admin (8.2 s) and as a Clerk (6.3 s), which the company server makes as a background job with
its progress (D-169: "or a job"), and `/exports?fmt=jde` as an Admin at 0.310 s cold (0.162 s warm, against 0.300).
49 full scans of big tables, all on reports' pages that work their answer out once per change and keep it; the hot
pages' own are listed, each with why, in `tests/bench/test_seed_volume.py` (`HOT_SCANS`), which fails on a new one.

## Nothing worse

- `scripts/same_behaviour.py --ref origin/main tax approve read pipeline migrate` (Python 3.12): tax (20,000 outputs),
  approve, read (60 invoices, half scans) and pipeline the same. `migrate` different only by phase 8's additions to
  the 22 old databases once brought up to date: the tables `billing_facts`, `billing_vendors`, `queue_changes` and
  `register_search` (with FTS5's own five), the index `summary_recent`, the column `report_answers.lasted`, and the
  settings `register_search_step` and `summary_logged`; every other table's rows and the invoices' snapshot the same.
- `bench run --quick` and `bench coding --quick` on origin/main and here: the same (reading 100% on its 24 invoices;
  coding 83.3% accuracy, 98.2% precision).
