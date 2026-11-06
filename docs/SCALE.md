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
| 1 | 100 simulated people on 4 cores, 300k invoices (here 100k): pages p50 ≤ 300 ms, p95 ≤ 1.5 s, p99 ≤ 3 s; actions p95 ≤ 1 s; 0 errors, 0 "database is locked"; memory flat for an hour (stretch: 200 people) | **Not met.** Pages p50 27 s, p95 140 s, p99 167 s; actions p95 120 s (the timeout); 577 requests with no answer, 6 5xx at the client and 80 in the server's log, 2 "database is locked", 7 logins refused; web workers grew to 3.4–6.9 GB each and were killed for memory (12 started to keep 4) | **Met at 100k invoices; not at 300k.** After phase 3 part 3, on 100k: **100 people** (600 s) 14,108 requests, pages p50 136 ms, p95 673 ms, p99 1.84 s; actions p95 348 ms; 0 errors, 0 'database is locked' (part 2: 175 ms, 1.15 s, 2.73 s, 554 ms; phase 3: 366 ms, 4.81 s, 10.5 s, 2.82 s). **300k, 100 people: not met.** Pages p50 4.17 s (part 2: 13.8 s, phase 3: 19.4 s), p95 46 s, p99 66 s; actions p95 47 s; 13,658 requests (9,400; 7,489); 96 503s from the heavy lane, 0 locked. The workers did 0.60-0.67 of a core each and reached 1.65-1.77 GB. What is left grows with the database still: the queue's lists and checks worked out again after each change (25% of the workers' CPU), month-end's vendors' rows read and written whole (15%), the quick search scoring every register row (5%), Vendors' workload (4%). Moved to phase 8's final proof (the reason and the items in phase 3 part 3's section below). Memory over an hour: not yet run. [Report](archive/scale-2026-11/phase3c-load-100-people.md) |
| 2 | One person, 300k invoices (here 100k): every page ≤ 300 ms; reports and exports ≤ 3 s or a job; no full scans on hot paths | **Not met.** 165 addresses as Admin and Clerk: 43 over target, 45 full scans of big tables, 0 errors. Pages: median 60 ms, but 24 of 108 over 300 ms (Activity 12–20 s, Learning → Suppliers 8 s, Import invoices 6–8 s, Settings → Automation 4.7 s, an invoice 0.7 s with 689 statements). Reports and exports: Spend workbook 251 s, Month-end 202 s and its accruals CSV 195 s, Report a problem 22 s, the controls report 17 s, the duplicates CSV 14 s, search to Excel 8.6 s | **Not met: the pages and reports are, the full scans are not yet guarded by tests.** After phase 3 part 3: 3 of 167 addresses over target, 0 errors: the Activity CSV twice (6.5-7.5 s, a background job on the server, D-169, so within 'or a job') and Activity searched for a word as a Clerk, 0.316 s for 0.300 (part 2: 0.280 s; this machine's noise). The first request in a process: month-end, Spend and Insights worked out once for every process on the server (D-169, D-175, D-179). 51 full scans of big tables, the first look of the answers kept per change; no test guards them yet. [Report](archive/scale-2026-11/phase3c-volume-100k.md) |
| 3 | 1,250 invoices (half scans) dropped at once: read the same day, web p95 unchanged; the model machine killed, hung or swapped: nothing lost or doubled, failover in under a minute | **Borderline, and no failover.** 100 invoices, half scans, through `serve --server --workers 4` with the stand-in model (8–20 s a page, 2 slots, time ×0.05): all read, 0 failures. At full latency (estimate): arrival 184 an hour, because the import runs one at a time in a web worker (1.8 cores) and asks the chat model on arrival. The reader drains 100 in 43 min. So 1,250 would take about 6.8 h to arrive and 9 h to read: inside a day only just. `GET /review` p95 89 ms during the drop. Failover to a backup model: not supported. Drills: the model killed, nothing lost (42 s recovery); hung, the import stalls 30 s (600 s in production) for each suggestion | **Met on this machine.** **Failover (phase 4, D-180):** a backup for each model server, a breaker shared by the server's processes. Drills on the stand-in, AI reviewer on: the primary killed → the backup in 1 s; hung → 31 s (21 s at the app's own limits, 46 s with reviews holding both chat slots); slow → 29-31 s; a swapped chat model trusted only once it reads the test invoice right; 0 reads lost, done twice or stored twice. [Drills](archive/scale-2026-11/phase4-failover-jde-load.md). **The day (phase 3):** 1,250 invoices (half scans) dropped at once: all in the review queue after 65.2 min, read and reviewed, 0 failures. At full latency (estimate): arrival 354 an hour (was 184), the page reader 8.2 h for the day, set by the model machine's 2 slots. The web workers did 0.03 of a core each (was 1.8 cores); `GET /review` p95 144 ms during the drop. [Report](archive/scale-2026-11/phase3-day-1250.md) |
| 4 | JD Edwards (stand-in): sync of 30k suppliers and 600k vouchers, then only the changes; 500 vouchers in one run; the duplicate check right beside a 20k-voucher supplier; fiscal periods | **Not met** (no volume harness yet: phase 4 builds it). By reading and small reproductions: every nightly read stops at 100,000 rows (200 pages × 500), so 600k vouchers fail every night. Each night re-reads 730 days. Posting stops at the first late voucher (about 40 h for 500 at worst). One 20k-voucher supplier switched the live check off for everyone (B-1601, fixed in phase 1). No fiscal periods | **Met against the AIS stand-in** (phase 4, D-181, D-182). The nightly sync of 30,000 suppliers and 600,000 vouchers: 51 s the first time, the longest any other write waited 0.054 s; the next night only the changes (5.0 s); stopped at page 600, the next sync resumes after it; voided vouchers leave, and only after a read proven complete. 500 vouchers in one run with one in ten answers late and one in twenty refused: 475 posted, 25 refused, none twice (38.8 s on the stand-in; with R04110ZA at about 20 s a voucher, about 2.8 h over runs: the batch-level orchestration specified for the JDE team is the real fix). The duplicate check right beside a 20,000-voucher supplier, about 0.1 s a check. Open period and cut-off: a G/L date in a closed period moved into the open one with a finding, or held; a period closed mid-batch holds the rest; holds re-read before each voucher; Z-table keys unique per installation. To confirm with the JDE team: items 15-22 of docs/JDE_ORCHESTRATIONS.md. [Report](archive/scale-2026-11/phase4-failover-jde-load.md) |
| 5 | macOS service proven in CI; readiness check; updates under 5 minutes of downtime; backups pruned, with a free-space check | — | |
| 6 | No SQL outside `core/store`; nested transactions; a PostgreSQL seam; meta tests | — | |
| 7 | Every reproduced bug fixed test-first | 0 of about 20 | **On track.** Phases 0-1: 40 fixed, each with its failing test first and its BUGS.md line: the digest's 20 (B-1578 to B-1607), the phase 1 review's 9 (B-1608, B-1609, B-1672 to B-1678), the backups' room and hours (B-1670, B-1671), the Windows background process (B-1680) and the bench (B-1681). Phase 2 part 1: 27 more, 67 in all: its own 10 (B-1610, B-1620 to B-1622, B-1630 to B-1634, B-1660) and the 17 its three reviews found (B-1682 to B-1698). Phase 2 part 2: 28 more, 95 in all (B-1712 to B-1725, B-1730 to B-1741, B-1758, B-1759; B-1726 to B-1729 unused): 9 found by its two fresh reviewers (B-1715 to B-1723), 19 by the work, the load runs and CI. Phase 3: 23 more, 118 in all (B-1760 to B-1782; B-1742 to B-1757 unused): 19 found by its three fresh reviewers (B-1764 to B-1782), 4 by the work (B-1760 to B-1763) Phase 3 part 2: 22 more, 140 in all (B-1790, B-1797 to B-1817; B-1783 to B-1789 and B-1791 to B-1796 unused): 11 found by its three fresh reviewers (B-1807 to B-1817), 11 by the work (B-1790, B-1797 to B-1806) Phase 3 part 3: 12 more, 152 in all (B-1818, B-1832 to B-1842; B-1819 to B-1831 unused): 5 found by its two fresh reviewers (B-1838 to B-1842), 7 by the work Phase 4: 64 more, 216 in all (B-1843, B-1844, B-1850 to B-1878, B-1880 to B-1891, B-1893 to B-1904, B-1906 to B-1914; B-1845 to B-1849, B-1879, B-1892, B-1905 unused): 43 found by its four fresh reviewers, 21 by the work and its drills |
| 8 | Laptop mode unchanged; accuracy benches the same or better; `same_behaviour.py` identical | — | **So far, yes.** Phase 2 part 2 against main (`scripts/same_behaviour.py --ref origin/main`, Python 3.12 with both OCR models): tax, approve, read and pipeline the same. The OCR change (D-163) reads every page byte for byte as before; the accuracy benches `run` and `lines` the same on 3.12, and on 3.13 now the same as 3.12. Phase 3 against main (the same way): tax (20,000 outputs), approve, read (60 invoices, half scans) and pipeline the same; capture's changes (D-170) and the review fixes to it (B-1778) checked byte for byte at each merge, with `bench run --quick --scanned 1` identical. The laptop walked: 49 pages and the demo's 10 invoices, 0 errors, none over 1 s. Phase 3 part 2 against main (the same way): tax, approve, read and pipeline the same; P1's GL suggestions checked against a full build after every approval (`bench coding --realistic` identical), P3's 18 pages byte for byte as before. The laptop walked again: 49 pages and the demo's 10 invoices, 0 errors. Phase 3 part 3 against main: tax, approve, read and pipeline the same; Q2's GL suggestions byte for byte on 1,797 lines and `bench coding --realistic` identical; Q3's pages byte for byte (14 Sales tax, 6 Learning, 8 queue, 12 invoice pages); the laptop walked again, 0 errors. Phase 4 against main: approve and read the same; tax and pipeline different only as D-183 intends (half up where the float rounded half even, and the Tax check's total row showing the sum TOTAL_MISMATCH checks); the accuracy benches byte for byte the same; on the reviewer's generated invoices no good invoice newly blocked (D-183's review: cash rounding and amounts over 8.4 million kept passing); the laptop walked, 0 errors. Load at 100,000 with 100 people: p50 129 ms, p95 636 ms, p99 1.89 s, actions 296 ms (a first run's stall at its second minute was B-1914, fixed) |
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
- **Phase 3, the background work** (6 Nov 2026):
  - **What changes:**
    - imports go through a queue kept in the database (it survives a restart, takes turns between people, a person
      waiting first) and are read by capture processes of their own on the company server, never a web worker; the
      arrival's chat call is held to 20 s with a breaker; each file is hashed once (D-168, schema 26);
    - the long downloads (search to Excel and CSV, the Activity CSV, the duplicates CSV, month-end's accruals) are made
      by the background process with their progress shown, and the big answers (month-end, the duplicate audit) are
      worked out once for every process; search to Excel writes its workbook directly (D-169, schema 27);
    - a scanned page is drawn and OCR'd once, whatever reads it and in whichever process, with no picture written in
      between; one lock for PyMuPDF everywhere; one HTTP client a model server; the totals solver pruned (D-170);
    - the page reader's and the AI reviewer's queues are renewed while read, taken in order (a person's request, a
      touchless supplier's invoice, the due date), the reviewer never starved; the model's kept answers pruned; the
      learning memory updated after an approval, not built again (D-171); an arrival whose chat call was put off is
      asked again (D-172).
  - **Reviewed** by three fresh reviewers (imports and queues; the downloads; capture and memory): 19 findings
    reproduced and fixed test-first (B-1764 to B-1782), among them the laptop's import stuck after a file open in
    Excel, a page read taken twice across a change of the clocks, a file being made deleted by the sweep, and small
    coarse scans no longer sent to a person (a regression of B-1761, found before it shipped).
  - **Measured:** the target 1, 2, 3 and 7 rows above, each with its report: 100 people at 100k and 300k with the
    profiles of the busiest workers, the knee at 25 and 50 people, the volume run at 100k and the day's 1,250.
  - **Not done here, and why:** target 1 at 300,000 invoices, and its p95 and p99 at 100,000. Taking the long work out
    of the web workers left them unsaturated at 100k (0.6 of a core), and 25 and 50 people now meet all but p99; but at
    300k, 100 people still collapse, and the profile names why: what every worker works out again grows with the
    database. Phase 3 part 2 goes on with it before phase 4: (a) Spend's figures, recurring invoices, the Import
    invoices listing, Insights and the vendors page worked out once for every process, or kept up to date as they
    change; (b) a vendor's older lessons carried forward, not read again, when an approval moves the learning memory;
    (c) a big vendor's invoices refreshed from what changed, not read whole; (d) the first three minutes' tail, each
    worker's first look at what it keeps; (e) the load bench's search to Excel through its job, as a browser does; (f)
    the slowest 1% at 25 and 50 people, by route. Failover to the backup model machine is phase 4's.
- **Phase 3 part 2, what grows with the database** (6 Nov 2026):
  - **What changes** (from the 300k profile, items (a) to (e) above):
    - a vendor's older lessons, and the GL suggestions' index on them, are carried forward when an approval moves the
      learning memory, not read again in every process (D-173);
    - a vendor's invoices kept in each process are brought up to date from a log of what changed, kept by triggers,
      not read whole after any change to one of them; the vendors on hold and their approved numbers kept on their own
      changes (D-174);
    - Spend's figures, the recurring vendors, Insights and the queue's late regular bills are worked out once for
      every process, as month-end and the duplicate audit were (D-175); no worker is warmed as it starts (a fresh one
      takes these answers in 1-3 ms);
    - the Import invoices page on the server reads one listing of the invoices folder kept for every process, made
      by the pick-up or by one process at a time, so a view stats no file; the load bench follows the search export's
      job as a browser does (D-176). The file-name rules were already one since D-168.
  - **Reviewed** by three fresh reviewers (the caches carried forward; the shared answers; the folder listing): 11
    findings reproduced and fixed test-first (B-1807 to B-1817), among them a worker killed while working out a shared
    answer leaving every other one waiting 30 s on its queue page for five minutes, a vendor given lessons too many at
    every approval after one raced with the memory's read, and the load bench passing a run whose exports failed.
    Also B-1803: the recurring vendors' rows no longer kept in each worker (140 MB at 300,000 invoices).
  - **Measured:** the target 1, 2, 7 and 8 rows above, with their reports: 100 people at 100,000 and 300,000 invoices
    with the profile of the busiest workers, the knee at 25 and 50 people, and the volume run at 100,000. The day's
    1,250 was not run again: nothing in part 2 changes the reading (P4's day of 100 was not slower: 5.3 → 5.1 min).
  - **Not done here, and why:** target 1 at 300,000. At 100,000 it is met; at 300,000 the profile names two things
    that still grow with the database, and phase 3 part 3 takes them before phase 4:
    - **month-end's answer** (22% of the workers' CPU): after any change it decodes again each invoice not yet in the
      ERP (`accruals._received_not_invoiced`, `billing_codings`); kept per invoice, or in a table of each invoice's
      PO billing lines kept as they change, only what changed is read;
    - **a vendor's older lessons** (13%): each worker keeps them for 32 vendors and 60,000 rows (B-1719's caps), so at
      300,000, where most vendors have thousands past the memory, nearly every check reads its vendor's whole (115 ms):
      what the suggestions and the history check need of them (each line's pattern, its counts), kept per vendor for
      every process and brought up to date as lessons are added, not the rows in each worker;
    - then, by the next profile: an invoice's analysis (suggestions 17%), Sales tax's return (6%), Learning's coding
      context (5%), the duplicate check's numbers (3%), Spend and month-end still worked out by the first asker after
      a change while the others wait (p95 4.7 and 7.5 s at 100,000).
    - Also: memory over an hour of load (the runs are 10 minutes), and tests that guard the full scans (target 2).
- **Phase 3 part 3, one bounded round more at 300,000** (6 Nov 2026):
  - **What changes:**
    - month-end's billing lines are kept for each vendor for every process and brought up to date from the vendors'
      change log, so after a change only the invoices changed are decoded again (D-177);
    - a vendor's older lessons are kept by line for every process (`past_lines`) and carried forward; the GL suggestions
      read them so, and the rows are read only by an arrival's examples and history check (D-178);
    - the duplicate check seeks the number off the summary's index, Previous and Next read the queue's ids, Sales tax
      and Learning's coding accuracy are worked out once for every process, and a settled answer's askers take the one
      before (young enough, while another really works it out) instead of waiting (D-179).
  - **Reviewed** by two fresh reviewers (the lessons by line; month-end and the pages): 5 findings reproduced and fixed
    test-first (B-1838 to B-1842), among them a settled answer shown at any age while another worked it out and the
    Learning page's corrections missing a line description corrected on its own.
  - **Measured:** the target 1, 2, 7 and 8 rows above, with their report: at 100,000 every target with room to spare;
    at 300,000 the pages' median 13.8 → 4.2 s.
  - **Not done here, and why:** target 1 at 300,000. Three rounds took it from collapse (p50 19.4 s) to 4.2 s, and
    each round's profile named the next thing that grows with the database. As planned, the goal goes on to phase 4
    rather than stall here (the plan's "don't stall" rule); target 1 at 300,000 is taken up again in phase 8's final
    proof, with these items from this round's profile:
    - **the queue's lists and checks** (25%): worked out again after each change (`summary_rows`, `list_invoices`,
      `attention`, `queue_checks`), and bigger at 300,000 (about 2,700 invoices from about 1,000 vendors in review);
    - **month-end's vendors' rows** (15%): each vendor's row of billing lines read and written whole (the biggest about
      7 MB): kept per invoice, or a table of each invoice's lines, would read and write only what changed;
    - **the quick search** (5%): it scores every ERP register row (600,000 at 300k): an index of the register's words;
    - **Vendors' workload** (4%): each worker's own, to be shared as the others are;
    - the workers' memory (1.65-1.77 GB at 300,000, against `WORKER_MB` 1,500) and an hour-long run.
- **Phase 4, the model machine and JD Edwards** (6 Nov 2026):
  - **What changes:**
    - a backup address for each model server, a breaker shared by the server's processes that moves the calls after
      two unanswered and takes the primary back once it answers well, a hang found within a minute (a call ahead in
      AP Coder's own line excuses it only while it is itself within the hang limit), and a swapped chat model trusted
      for nothing touchless until it reads the test invoice right (D-180);
    - the JD Edwards sync streams its pages with a checkpoint per part, reads only what changed each night (and the
      whole weekly), removes voided vouchers only after a read proven complete, writes in short transactions and keeps
      its connections open (D-181);
    - posting goes on past a late answer and never sends a voucher again before JD Edwards could have finished it;
      holds re-read before each voucher; an open period and cut-off; Z-table keys unique per installation; month end
      counts the vouchers not posted yet; a spec for a batch-level orchestration (D-182);
    - one Decimal half-up money arithmetic and one tolerance table for the checks, nothing worse than before on any
      good invoice (cash rounding to the nickel recognised) (D-183).
  - **Reviewed** by four fresh reviewers (failover; the sync; posting; the money): 43 findings reproduced and fixed
    test-first, among them a voucher that could be posted twice when its wait was set longer than the resend wait, a
    cleared register whose older vouchers were not read again for up to six nights, a chat breaker that never moved
    off a dead server, and good invoices over 8.4 million or rounded for cash hard-blocked by the new tolerance.
  - **Measured:** the target 3, 4, 7 and 8 rows above, with their report.
  - **Not done here, and why:** the batch-level orchestration (one R04110ZA a batch) is specified, not built: it needs
    the JDE team (items 15-22 of docs/JDE_ORCHESTRATIONS.md, among them the open period, the error for a closed one,
    and what AIS answers when it gives up). Failover was drilled on the stand-in; the real Mac Studio and Mac mini are
    measured in stage 0 and stage 2.
