# AP Coder at scale: targets, measurements and progress

The goal is in [SCALE_GOAL.md](SCALE_GOAL.md):
- 300–400 people at once and about 25,000 invoices a month (1,250 a day, 300,000 a year);
- one company server (a Windows VM, 32 vCPU, 64 GB), with the AI models on a separate machine over the network;
- the pilot itself on one Mac mini.

What the code was like when the goal started is in [archive/scale-2026-11/DIGEST.md](archive/scale-2026-11/DIGEST.md),
with seven notes beside it.

Every number here was measured, with the machine, the command and the date. Estimates are marked *estimate*.

## The machine the numbers come from
- **Where they come from:** a 4-core Linux container (shared, so on the slow side), Python 3.11, no graphics card.
- **The real-server figures** (32 vCPU) are a projection from the per-core numbers until they are measured on the
  real server.
- **The models:** played by the bench's stand-in model server, `python -m ap_coder.bench fake-model`, with set
  delays, slots and failures.

## Why 100,000 invoices and not 300,000 on this machine
- **The size:** a seeded database (`python -m ap_coder.bench seed`) takes 22–32 KB an invoice:
  - the invoice row itself about 12 KB (the coding as read 3.6, as approved 3.5, the checks 2.0);
  - its capture 2.1;
  - events 1.75;
  - feedback 1.4;
  - the summary and search 2.6;
  - the JD Edwards register 1.2;
  - the full learning history about 9 KB more for each approved invoice.
- **The two seeds:** 100,000 invoices (600,000 register rows) came to 2.2 GB with learning rows for 5% of approvals, and 3.25 GB in the current seed. A year, 300,000, would be about 7–9 GB. This machine's whole disk allowance leaves about 3 GB for data.
- **So the numbers here are at 100,000 invoices,** about four months at 25,000 a month, with a scaling check against 12,000:
  - each page's time at 12k and at 100k;
  - no full scan of a big table on a hot path, guarded by tests.
  - A page that only seeks indexes adds about one B-tree level from 100k to 300k. A page that scans grows threefold, and the scaling check is what catches it.
- **Stage 2's real server measures a year's database itself** (docs/SERVER.md).

## Targets (the goal's "Done means")

| # | Target | Baseline | Now |
|---|---|---|---|
| 1 | 100 simulated people on 4 cores, 300k invoices (here 100k): pages p50 ≤ 300 ms, p95 ≤ 1.5 s, p99 ≤ 3 s; actions p95 ≤ 1 s; 0 errors, 0 "database is locked"; memory flat for an hour (stretch: 200 people) | **Not met.** Pages p50 27 s, p95 140 s, p99 167 s; actions p95 120 s (the timeout); 577 requests with no answer, 6 5xx at the client and 80 in the server's log, 2 "database is locked", 7 logins refused; web workers grew to 3.4–6.9 GB each and were killed for memory (12 started to keep 4) | **Not met; the real work answered tripled, and 25 people come close.** After phase 2 part 2, 100 people: 9,838 requests, the queue, the Check and an invoice answered 2.5-3 times as often as after part 1 (1,764, 1,092 and 810 against 652, 350 and 279); static files, fonts and logins no longer wait (p95 0.27 s, 0.09 s, 3-6 s); 0 'database is locked', 0 tracebacks, 37 503s (the long lane busy). Still pages p50 17 s, p95 59 s; actions p95 66 s: the 4 cores saturated. **25 people:** pages p50 300 ms, p95 4.45 s, p99 9.5 s; actions p95 1.37 s; 0 errors, the workers at half a core each. The tail is long requests (month-end's first visit in a process 8 s, search to Excel 12 s, an import read in the web worker) sharing a process's one running Python thread with the light ones: phase 3 takes that work out of the web workers. Workers 1.25-1.4 GB each after 10 minutes. [Report](archive/scale-2026-11/phase2b-load-100-people.md) |
| 2 | One person, 300k invoices (here 100k): every page ≤ 300 ms; reports and exports ≤ 3 s or a job; no full scans on hot paths | **Not met.** 165 addresses as Admin and Clerk: 43 over target, 45 full scans of big tables, 0 errors. Pages: median 60 ms, but 24 of 108 over 300 ms (Activity 12–20 s, Learning → Suppliers 8 s, Import invoices 6–8 s, Settings → Automation 4.7 s, an invoice 0.7 s with 689 statements). Reports and exports: Spend workbook 251 s, Month-end 202 s and its accruals CSV 195 s, Report a problem 22 s, the controls report 17 s, the duplicates CSV 14 s, search to Excel 8.6 s | **Not met; 11 over** (part 1: 38). After phase 2 part 2: 11 of 167 over target, 0 errors. Month-end 6 s → 0.4 s (8.6 s the first time in a process), Import invoices 1.5 s → 0.05 s, Spend 3.5 s → 0.02 s, Supplier learning 2.8 s → 0.12 s, Settings → Reading 2.8 s → 0.02 s, the duplicates CSV → 0.03 s (11 s the first time), an invoice → 0.1 s (its page 3.6 MB → 0.3 MB). Still over: the Activity CSV 12-16 s (streamed), search to Excel 12 s, Activity and its search 0.2-0.6 s, the JD Edwards exports page 0.5 s, Learning → Coding accuracy 0.34-0.43 s. Against main like for like: nothing slower but search to CSV, 1.5 s → 1.8 s (B-1735's trade). [Report](archive/scale-2026-11/phase2b-volume-100k.md) |
| 3 | 1,250 invoices (half scans) dropped at once: read the same day, web p95 unchanged; the model machine killed, hung or swapped: nothing lost or doubled, failover in under a minute | **Borderline, and no failover.** 100 invoices, half scans, through `serve --server --workers 4` with the stand-in model (8–20 s a page, 2 slots, time ×0.05): all read, 0 failures. At full latency (estimate): arrival 184 an hour, because the import runs one at a time in a web worker (1.8 cores) and asks the chat model on arrival. The reader drains 100 in 43 min. So 1,250 would take about 6.8 h to arrive and 9 h to read: inside a day only just. `GET /review` p95 89 ms during the drop. Failover to a backup model: not supported. Drills: the model killed, nothing lost (42 s recovery); hung, the import stalls 30 s (600 s in production) for each suggestion | |
| 4 | JD Edwards (stand-in): sync of 30k suppliers and 600k vouchers, then only the changes; 500 vouchers in one run; the duplicate check right beside a 20k-voucher supplier; fiscal periods | **Not met** (no volume harness yet: phase 4 builds it). By reading and small reproductions: every nightly read stops at 100,000 rows (200 pages × 500), so 600k vouchers fail every night. Each night re-reads 730 days. Posting stops at the first late voucher (about 40 h for 500 at worst). One 20k-voucher supplier switched the live check off for everyone (B-1601, fixed in phase 1). No fiscal periods | |
| 5 | macOS service proven in CI; readiness check; updates under 5 minutes of downtime; backups pruned, with a free-space check | — | |
| 6 | No SQL outside `core/store`; nested transactions; a PostgreSQL seam; meta tests | — | |
| 7 | Every reproduced bug fixed test-first | 0 of about 20 | **On track.** Phases 0-1: 40 fixed, each with its failing test first and its BUGS.md line: the digest's 20 (B-1578 to B-1607), the phase 1 review's 9 (B-1608, B-1609, B-1672 to B-1678), the backups' room and hours (B-1670, B-1671), the Windows background process (B-1680) and the bench (B-1681). Phase 2 part 1: 27 more, 67 in all: its own 10 (B-1610, B-1620 to B-1622, B-1630 to B-1634, B-1660) and the 17 its three reviews found (B-1682 to B-1698). Phase 2 part 2: 26 more, 93 in all (B-1712 to B-1725, B-1730 to B-1741; B-1726 to B-1729 unused): 9 found by its two fresh reviewers (B-1715 to B-1723), 17 by the work and the load runs |
| 8 | Laptop mode unchanged; accuracy benches the same or better; `same_behaviour.py` identical | — | **So far, yes.** Phase 2 part 2 against main (`scripts/same_behaviour.py --ref origin/main`, Python 3.12 with both OCR models): tax, approve, read and pipeline the same. The OCR change (D-163) reads every page byte for byte as before; the accuracy benches `run` and `lines` the same on 3.12, and on 3.13 now the same as 3.12 |
| 9 | Docs current | — | |

## Progress by phase
- **Phase 0, measure first** (5 Nov 2026; PR #92):
  - **The tools:** `bench seed`, `volume`, `load`, `fake-model` and `day`, plus the research (D-148).
  - **The load baseline on 100k invoices,** 100 people, 4 web workers, 10 minutes after a 1-minute ramp, measured with
    the corrected harness (timeouts and cut-off requests counted at their elapsed time):
    - **It collapses:** requests a second fall from 44 in the first minute (the logins) to 12 in the third and 1–3
      from the fifth, as workers grow past 3 GB and are killed.
    - **The slowest, at the server (p95):** `GET /process` (it reads every invoice file), `GET /review` (it re-checks
      the queue), `GET /exports`, `GET /month-end`, `GET /search` and `POST /review/{id}/check`. The figures are in the
      report.
    - **The full report:** [archive/scale-2026-11/baseline-load-100-people.md](archive/scale-2026-11/baseline-load-100-people.md).
  - **The volume baseline at 100k, one person:**
    - [archive/scale-2026-11/baseline-volume-100k.md](archive/scale-2026-11/baseline-volume-100k.md), in the target 2 row
      above;
    - measured with phase 1's agents on the same 4 cores, so it's on the slow side (the reports take minutes, so noise
      doesn't change what is over target).
  - **The throughput baseline:** [archive/scale-2026-11/baseline-day-100.md](archive/scale-2026-11/baseline-day-100.md), in
    the target 3 row above.
  - **Merged** 5 Nov 2026 (a565e9c).
  - **The first target for phase 2:** a web worker's memory on a big database. What makes a worker grow to 3-7 GB:
    [archive/scale-2026-11/memory.md](archive/scale-2026-11/memory.md). No leak: about 110 MB for each request in
    flight (anyio's 40 threads a worker, no limit), kept afterwards by the allocator. Capping the pool at 4 threads
    took 16 people at once from 1.9-2.15 GB to 1.33 GB with the same work done.
- **Phase 1, the verified bugs** (5 Nov 2026):
  - **What it fixes:** the digest's 20 bugs, test-first, then the 9 a fresh review found in that work. Among them:
    - a manager's double answer;
    - a page waiting 30 s behind a writer to note that someone is still there;
    - polls that kept idle sessions alive;
    - one supplier with many vouchers turning the JD Edwards check off for everyone;
    - two pick-ups storing a file twice;
    - work typed after a session ended, now kept;
    - a model server named in the company's DNS, now connected to at the address it was checked at.
  - **Schema 20** adds six indexes.
  - **The decisions:** D-149 (amended) to D-152.
  - **Also:** backups refused when they would fill the disk, and kept to set hours (B-1670, B-1671).

  - **Merged** 5 Nov 2026 (0c15aa2; PR #93).
- **Phase 2 part 1, the request path** (5 Nov 2026):
  - **What changes:**
    - a web worker on the company server runs 4 requests at once, the long routes 2 more in their own lane, and is
      started afresh past its memory or its requests (D-153);
    - the learning memory is read once and refreshed, a vendor's lessons worked out once, and an invoice opens in one
      request (D-154);
    - the review queue is drawn from the invoices' summary and checked again in the background; Import invoices reads
      no file (D-155);
    - the big reports are summed by SQLite, and the Spend workbook is made in the background (D-156);
    - search, the duplicate audit, Report a problem, Supplier learning and Automation read in SQL what they show
      (D-157);
    - no async handler does database or file work on the event loop (D-158).
  - **Schema 21 to 24:** the register's search keys and the indexes of events and the register.
  - **Reviewed** by three fresh reviewers (the workers, the pages, the data): 17 findings reproduced and fixed
    test-first (B-1682 to B-1698), among them a worker restart that could cut a job short, an invoice that
    waited behind another writer to open, and a re-check lease left behind by a stopped process.
  - **Measured:** the target 1 and 2 rows above. Stable under 100 people now (no errors, memory flat), but the 4 cores
    are saturated: part 2 takes the work out of each request (static files cached for good, page images rendered once,
    the invoice page's lists out of its HTML, the slow reports as jobs).
  - **Where the CPU goes under load** ([archive/scale-2026-11/cpu.md](archive/scale-2026-11/cpu.md)): 100 people
    ask for about 18 cores' worth of work, 4.5 times what the 4 cores give. The Check and the invoice page are 82% of
    it, and most of their work gives the same answer every time (the held vendors' approved numbers, the vendor IDs,
    the suggestions' index kept for only 2 vendors, the 3.3 MB vendor list). Part 2 starts there.
- **Phase 2 part 2, the request path** (5 Nov 2026):
  - **What changes:**
    - a web worker keeps what it worked out while the database says nothing it depends on changed: stamps read before
      the work, counts kept by triggers for each kind of change, a lock so one page works an answer out while the
      others wait (D-159); a page that only shows an answer may take one a few seconds old (D-164);
    - the review screen is light: the vendor list fetched once and kept by the browser, page pictures drawn once for
      all the workers, the reader's poll a short answer, a vendor's invoices kept (D-160);
    - static files from memory at addresses kept for good, polls that wait and stop, a busy worker hands its
      connections back (D-161);
    - the slow reports and pages kept per change, listed a part at a time, or read without waste (D-162);
    - both OCR models on every Python, PP-OCRv4 through RapidOCR 3 reading exactly as before (D-163);
    - under load: the queue, the invoice page and the reading status (D-165), Import invoices, month-end and the
      reports (D-166), the queue's last store costs (D-167).
  - **Schema 25:** the lessons' approvals counted off an index (B-1714). Six more indexes made at start.
  - **Reviewed** by two fresh reviewers (what the workers keep; the serving and the browser): 9 findings reproduced and
    fixed test-first (B-1715 to B-1723), among them suggestion memories one per lessons generation (681 MB a worker),
    kept answers wrong while a restore ran, and a page picture cut short by a power loss served for good.
  - **Measured:** the target 1, 2, 7 and 8 rows above. With the profiles (py-spy) of the workers under 100 people in
    the load report.
  - **Not done here, and why:** target 1. Two profiles and three rounds of work cut a request's CPU under load from
    about 0.5 s to 0.3 s and tripled the real work answered, but 100 people ask for about three to four times what 4
    workers give, and at 25 people a long request still slows the light ones in its process. Phase 3 goes on with it:
    the long work out of the web workers (imports and capture in their own processes, the long reports as jobs), and
    the workers' memory sized (1.3 GB each after 10 minutes). Also left: month-end's first visit (a summary table of
    each invoice's PO-billing lines), a lighter writer for search to Excel, the Import invoices listing shared by the
    workers, the suggestions' memory of a vendor's older lessons, Sales tax (1.25 s a visit, `coding/taxreturn.py`), and
    the file-name rules `process.folder_names` repeats from `ai/pipeline` (a test pins them alike).
