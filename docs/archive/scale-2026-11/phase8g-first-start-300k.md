# Phase 8 part 7 measured: a database's first 10 minutes at 300,000 invoices, prepared before it serves (8 Nov 2026)

4 cores, Python 3.12 with both OCR models, the bench's stand-in model server (1-4 s an answer, 2 at once).
`python -m ap_coder.bench load --users 100 --duration 600 --ramp 60 --think 10 --workers 4 --data <300k database>
--sample-every 10`, each run on a database made afresh by `bench seed --invoices 300000` and never served before,
nothing else running. D-204: on a database that never had the reports' answers, the server works out the reports'
default views before it answers (in a process of its own, 200 s at most); at every start each web worker builds its
memories before it serves (25 s at most).

| run | code | reports before answering | each worker's memories | requests | pages p50 / p95 / p99 | actions p95 | over 30 s | 5xx, 'locked' |
|---|---|---|---|---:|---|---:|---:|---|
| before D-204 (D-202), twice | aeb40ef, 1f45173 | — | — | 14,764 and 15,276 | 116-122 ms / 898-977 ms / 2.24 and 3.18 s | 397-454 ms | 0 | 0, 0 |
| 1 (a first version) | 6696496 | Month-end's 3 of 14 in 121.3 s (bound 120 s) | 8-12 of 22 steps, cut at 15 s | 15,190 | 115 ms / 686 ms / 2.91 s | 347 ms | 0 | 0, 0 |
| **2** | 24391d1 | **14 of 14 in 92.3 s** | **17, in about 13 s** | 14,784 | **132 ms / 1.06 s / 2.49 s** | **508 ms** | **0** | **0, 0** |
| **3 (the final code)** | f6ce662 | **14 of 14 in 163.4 s** | **17, in 11.5-12.0 s** | 14,849 | **106 ms / 615 ms / 1.48 s** | **322 ms** | **0** | **0, 0** |

**Target 1 is met in a database's first 10 minutes at 300,000 invoices** (runs 2 and 3; 24391d1 and f6ce662 differ
only in what a database that had the reports does: it is served at once). The server answers once that is done: 1.5
to 3 minutes after its first start at 300,000 (the reports 92-163 s, then the workers' 12-13 s), once in a database's
life. A restart or an update finds the reports kept (any version) and is served at once, its workers 12-13 s.

- **What is left slow:** the server took more than 2.5 s on 6 requests in run 3's 10 minutes, all in its first three
  minutes: Sales tax's first asks (3.8 and 4.8 s: each a new question, the bench's JD Edwards export batches change
  its tax setup's key, D-179), the import page once (5.2 s), the Activity page once (3.0 s), the queue's first
  invoice once (2.5 s), an export page once. In run 2: 23, the first two minutes' Sales tax, import page and exports,
  and later the exports page and the biggest vendor's invoices on its standing PO (2.5-4.2 s).
- **Run 1** prepared only Month-end's three (its billing lines built from nothing on a never-served database: 121 s)
  within the first bound, the quick reports then worked out under load (Sales tax p95 3.44 s), and each worker's warm
  stopped at 15 s with four warming at once: the bounds were raised to 200 s and 25 s.
- **Before D-204** (phase 8 part 5): p99 2.24 and 3.18 s, set by the first 1-2 minutes: each worker's first opens of
  an invoice (3-6 s, its memories built in the request) and Sales tax and Learning asked before the warm-up reached
  them (3.6-4.7 s).
