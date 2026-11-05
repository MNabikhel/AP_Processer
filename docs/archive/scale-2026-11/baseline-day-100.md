# Server day: a morning's drop through the company server

100 invoices (50% scans, seed 1) delivered by the invoices folder and the Process page to `serve --server --workers 4`; the fake model: 8-20 s a page, 8-20 s a chat answer, 2 slot(s), AP_LLM_PARALLEL 2, time x0.05; AI reviewer: all. 4 cores, Python 3.12.13, Linux. Wall time 10.0 min.

| | measured | at full latency (estimate) |
|---|---:|---:|
| invoices in the review queue | 100 of 100 | |
| all of them in after | 9.6 min | 32.5 min |
| arrival, invoices an hour | 626 | 184 |
| page reader: queued / skipped (clean digital) / read / failed / left | 100 / 0 / 100 / 0 / 0 | |
| page reader drained after (from the drop) | 9.9 min | 43.4 min |
| page reader, invoices an hour | 632 | |
| AI reviewer: queued / done / failed / left | 100 / 100 / 0 / 0 | |
| AI reviewer, reviews an hour (first one after) | 639 (34 s) | |
| model time an invoice | | 56.8 s |
| invoices the model machine serves in a 10-hour day | | 1,267 |
| GET /review p50 / p95, idle | 10 ms / 367 ms | |
| GET /review p50 / p95 / max, during the drop | 34 ms / 89 ms / 168 ms (198 probes, 0 errors) | |
| failures (imports, reads, reviews, probes, locked, tracebacks) | 0 | |

CPU (seconds over the run; cores = seconds / wall time):

| process | n | CPU s | busiest | cores |
|---|---:|---:|---:|---:|
| server | 1 | 0.9 | 0.9 | 0.0 |
| background | 1 | 313.8 | 313.8 | 0.52 |
| other | 1 | 0.0 | 0.0 | 0.0 |
| web worker | 4 | 1068.9 | 1061.4 | 1.78 |
| the bench and its fake model | 1 | 8.3 | | |

Over time (seconds from the drop):

| s | in the queue | page reads waiting / reading / done | reviews waiting / done | model busy / waiting |
|---:|---:|---:|---:|---:|
| 2 | 0 | 0 / 0 / 0 | 0 / 0 | 0 / 0 |
| 51 | 8 | 1 / 0 / 7 | 1 / 7 | 0 / 0 |
| 99 | 17 | 4 / 2 / 11 | 10 / 7 | 2 / 0 |
| 147 | 26 | 1 / 1 / 24 | 4 / 22 | 0 / 0 |
| 195 | 37 | 9 / 1 / 27 | 10 / 27 | 0 / 0 |
| 244 | 47 | 0 / 0 / 47 | 10 / 36 | 0 / 0 |
| 292 | 56 | 5 / 2 / 49 | 9 / 47 | 0 / 0 |
| 340 | 65 | 0 / 1 / 64 | 1 / 64 | 1 / 0 |
| 389 | 74 | 1 / 1 / 72 | 3 / 71 | 1 / 0 |
| 437 | 78 | 1 / 1 / 76 | 2 / 76 | 0 / 0 |
| 485 | 88 | 1 / 0 / 87 | 1 / 87 | 1 / 0 |
| 534 | 92 | 0 / 0 / 92 | 0 / 92 | 0 / 0 |
| 582 | 100 | 2 / 2 / 96 | 8 / 92 | 1 / 0 |
| 602 | 100 | 0 / 0 / 100 | 0 / 100 | 0 / 0 |

Page reader self-test: ath-maas_ovisocr2 passed (9 of 9 fields).

Notes: at full latency is an estimate. Arrival adds back the chat model's answers the import waited for (GL suggestions), the page reader its pages, header and review answers divided by AP_LLM_PARALLEL; the work on this computer (OCR, the pipeline) is as measured. The app's own timers are real time: a minute's pause after a failed read, 30 s when nothing waits, the model status kept 10-30 s. Failover: not supported: AP Coder has one address for each model server (AP_LLM_BASE_URL, AP_PAGE_READER_BASE_URL) and nothing switches to a backup on its own; a person changes the address.
