# Phase 8 part 8 measured: one person on 300,000 invoices, every page under 300 ms (9 Nov 2026)

`python -m ap_coder.bench volume --data <300k database>` on a database made by `bench seed --invoices 300000` (300,084
invoices, 1,259,965 events, 1,290,708 lines of feedback, 600,000 JD Edwards register rows), asked as an Admin and a
Clerk, each address the median of 3 after a warm request; 4 cores, Python 3.12, nothing else running. Targets: a page
300 ms; a report, an export or a search 3 s, or made as a background job (the goal's "or a job").

| | code | addresses | over target | pages over 300 ms | 0 errors |
|---|---|---:|---:|---:|---|
| before D-206 | 64046fc (main after phase 8 part 7) | 171 | 12 | 9 (4 pages, as each role) | yes |
| **after D-206** | **72c33f5** | **171** | **3** | **0** | **yes** |

**Every page answers under 300 ms for one person at 300,000 invoices** (94 pages; the slowest the purchase orders,
0.27-0.29 s). The 3 over 3 s are downloads the company server makes as background jobs, the page saying how far the
file got (D-169): the Activity CSV (15.8 s as a Clerk, 25.3 s as an Admin: every event) and the duplicate audit (19.6 s,
then kept: 0.17 s for the next person).

| page | before (Admin / Clerk) | after (Admin / Clerk) | what was slow |
|---|---|---|---|
| Exports (`/exports`) | 0.88 / 0.89 s | 0.05 / 0.05 s | the JD Edwards check of the 1,122 ready invoices at every visit, each read and decoded twice; now a kept answer for every process (D-206) |
| Exports, JD Edwards (`?fmt=jde`) | 0.79 / 0.82 s | 0.05 / 0.05 s | the same |
| Activity (`/activity`, `?q=`) | 0.51-0.53 / 0.71-0.73 s | 0.02-0.06 / 0.06-0.08 s | counts over 1.26 million events and the list of people at every visit; now kept by the events' stamp, and the people found by an index skip |
| Settings → Automation | 0.35 s | 0.07 s | 3.4 million supplier outcomes grouped; now a small index of the audits |

Each page shows exactly what it showed before (compared byte for byte with the old code at the start and after 13
changes by another Store or plain SQL, D-206's tests). The first look after a change that matters still does the work
(Exports 0.43-0.50 s after an invoice or vendor change, Activity 0.27-0.60 s), as every kept answer does.

**A year's controls report** (an auditor's usual request, not among the bench's addresses until then): 317 s in the
request at 300,000 invoices, because a period's events were selected again for each batch of 2,000 (B-2076); now read
once, 16 s, and on the server a background download like the Activity CSV. The Activity page's tiles count a month at
most on the server (0.02-0.07 s for a year's address, 0.4-0.5 s for a month's first look after a change).
