# Phase 8 part 2 measured: the morning's drop of 1,250 invoices and the model machine's drills, at the final code (7 Nov 2026)

4 cores, Python 3.12 with both OCR models, the bench's stand-in model server (8-20 s a page, 8-20 s a chat answer, 2
slots, time × 0.05), the AI reviewer on for every invoice. Measured at 963a7cc (D-198 and D-199 in), nothing else
running.

## The day: 1,250 invoices, half scans, dropped at once

`python -m ap_coder.bench day --n 1250 --scanned 0.5` through `serve --server --workers 4`.

| | phase 3 (9b86872) | phase 8 part 2 (963a7cc) |
|---|---:|---:|
| all 1,250 in the review queue after | 65.2 min | 64.0 min |
| arrival, invoices an hour (measured / at full latency) | 1,150 / 354 | 1,172 / 354 |
| page reader drained, at full latency | 8.2 h | 8.2 h |
| AI reviewer: done, failed | 1,250, 0 | 1,250, 0 |
| an invoice's wait for the reviewer once read, median / max | | 1 s / 5 s |
| `GET /review` during the drop, p50 / p95 / max | p95 144 ms | 32 ms / 104 ms / 217 ms (1,263 probes) |
| web workers' CPU, cores each | 0.03 | 0.03 |
| failures (imports, reads, reviews, probes, locked, tracebacks) | 0 | 0 |

Read the same day: at full latency the invoices arrive in 3.5 h and the page reader finishes in 8.2 h, set by the
model machine (57.6 s of model time an invoice on 2 slots: 1,249 in a 10-hour day). The web stays as fast as idle while
the drop is read (its idle p95, 475 ms, is the first page views of a database never served). The capture processes
did the reading (1.72 cores), the background process the queues (0.42).

## The model machine killed and hung (the backup taking over)

`python -m ap_coder.bench day --n 40 --scanned 0.5 --workers 2 --backup --reviewer all --drill <kill|hang>
--drill-for 60`: two stand-in model servers, the primary killed or hung for 60 s while the 40 invoices are read and
reviewed.

| drill | reads lost / done twice / stored twice | the backup's first answer | back on the primary after the drill | 40 in the queue |
|---|---|---|---|---|
| kill (phase 4) | 0 / 0 / 0 | 1 s after the drill began | 15 s | |
| **kill** | **0 / 0 / 0** | **1 s** after the drill began (1 s after the first unanswered request) | 13 s | 2.0 min |
| hang (phase 4) | 0 / 0 / 0 | 31 s | | |
| **hang** | **0 / 0 / 0** | **31 s** after the drill began (10 s after the first unanswered request) | 37 s | 2.4 min |

Failover in under a minute both ways, nothing lost or done twice: target 3's second half holds at the final code.
