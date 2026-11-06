# Phase 3: a day's 1,250 invoices through the company server (6 Nov 2026)

The goal's target 3 at the end of phase 3 (on 9b86872, with the review fixes): 1,250 invoices, half of them scans,
dropped at once in the invoices folder of `serve --server --workers 4` on this 4-core machine, with the bench's stand-in
model (`python -m ap_coder.bench day --n 1250 --scanned 0.5 --workers 4`, Python 3.12, both OCR models).

- **Before (phase 0's baseline, 100 invoices):** the import ran one file at a time in a web worker (1.8 cores of
  CPU in the web workers) and waited for the chat model on arrival: 184 invoices an hour at full latency, so 1,250
  would have taken about 6.8 h to arrive and 9 h to be read.
- **After:** imports are read by two capture processes of their own (D-168), so the web workers did 136 s of CPU in
  65 minutes (0.03 of a core each) and `GET /review` answered in p50 53 ms, p95 144 ms, max 270 ms during the drop
  (1,282 probes, 0 errors). Every invoice was read by the page reader and reviewed by the AI reviewer, with 0
  failures, 0 "database is locked" and no traceback. At full latency (an estimate) arrival is 354 invoices an hour and
  the page reader takes 8.2 h for the day: the model machine's 2 slots (57.6 s of model time an invoice) set the pace,
  not AP Coder, and it serves about 1,250 in a 10-hour day.
- **The AI reviewer keeps up:** 1,205 of the 1,250 reviews were done while the page reader still had invoices in line
  (phase 3's interleaving, D-171); an invoice waited a median of 1 s for its review once read.
- **Not here:** failover to a backup model machine (phase 4).

The bench's report:

## Server day: a morning's drop through the company server

1250 invoices (50% scans, seed 1) delivered by the invoices folder and the Process page to `serve --server --workers 4`; the fake model: 8-20 s a page, 8-20 s a chat answer, 2 slot(s), AP_LLM_PARALLEL 2, time x0.05; AI reviewer: all. 4 cores, Python 3.12.3, Linux. Wall time 65.6 min.

| | measured | at full latency (estimate) |
|---|---:|---:|
| invoices in the review queue | 1250 of 1250 | |
| all of them in after | 65.2 min | 3.5 h |
| arrival, invoices an hour | 1,150 | 354 |
| page reader: queued / skipped (clean digital) / read / failed / left | 1250 / 0 / 1250 / 0 / 0 | |
| page reader drained after (from the drop) | 65.5 min | 8.2 h |
| page reader, invoices an hour | 1,151 | |
| AI reviewer: queued / done / failed / left | 1250 / 1250 / 0 / 0 | |
| AI reviewer, reviews an hour (first one after) | 1,152 (20 s) | |
| AI reviewer: reviews done while the page reader had invoices in line | 1205 | |
| AI reviewer: an invoice's wait for it once read, median / max | 1 s / 5 s | |
| model time an invoice | | 57.6 s |
| invoices the model machine serves in a 10-hour day | | 1,250 |
| GET /review p50 / p95, idle | 12 ms / 405 ms | |
| GET /review p50 / p95 / max, during the drop | 53 ms / 144 ms / 270 ms (1282 probes, 0 errors) | |
| failures (imports, reads, reviews, probes, locked, tracebacks) | 0 | |

CPU (seconds over the run; cores = seconds / wall time):

| process | n | CPU s | busiest | cores |
|---|---:|---:|---:|---:|
| server | 1 | 6.7 | 6.7 | 0.0 |
| background | 2 | 1871.1 | 1173.9 | 0.48 |
| capture | 2 | 6727.8 | 3391.8 | 1.71 |
| other | 1 | 0.0 | 0.0 | 0.0 |
| web worker | 4 | 136.0 | 61.0 | 0.03 |
| the bench and its fake model | 1 | 56.7 | | |

Over time (seconds from the drop):

| s | in the queue | page reads waiting / reading / done | reviews waiting / done | model busy / waiting |
|---:|---:|---:|---:|---:|
| 3 | 1 | 1 / 0 / 0 | 1 / 0 | 2 / 0 |
| 330 | 106 | 5 / 2 / 99 | 7 / 99 | 2 / 0 |
| 656 | 203 | 0 / 0 / 203 | 0 / 203 | 0 / 0 |
| 983 | 314 | 4 / 1 / 309 | 5 / 308 | 2 / 0 |
| 1310 | 434 | 18 / 1 / 415 | 19 / 414 | 2 / 0 |
| 1637 | 538 | 4 / 2 / 532 | 6 / 532 | 2 / 0 |
| 1964 | 645 | 6 / 0 / 639 | 6 / 637 | 2 / 0 |
| 2291 | 746 | 5 / 1 / 740 | 6 / 740 | 1 / 0 |
| 2619 | 841 | 7 / 0 / 834 | 7 / 833 | 2 / 0 |
| 2946 | 934 | 1 / 0 / 933 | 1 / 933 | 0 / 0 |
| 3274 | 1037 | 10 / 0 / 1027 | 10 / 1027 | 0 / 0 |
| 3601 | 1146 | 3 / 0 / 1143 | 3 / 1143 | 1 / 0 |
| 3929 | 1250 | 0 / 1 / 1249 | 1 / 1249 | 1 / 0 |
| 3935 | 1250 | 0 / 0 / 1250 | 0 / 1250 | 0 / 0 |

Page reader self-test: ath-maas_ovisocr2 passed (9 of 9 fields).

Notes: at full latency is an estimate. Arrival adds back the chat model's answers the import waited for (GL suggestions), each at most the arrival's limit and the capture processes waiting side by side; the page reader its pages, header and review answers divided by AP_LLM_PARALLEL; the work on this computer (OCR, the pipeline) is as measured. The app's own timers are real time: a minute's pause after a failed read, 30 s when nothing waits, the model status kept 10-30 s. Failover: not supported: AP Coder has one address for each model server (AP_LLM_BASE_URL, AP_PAGE_READER_BASE_URL) and nothing switches to a backup on its own; a person changes the address.
