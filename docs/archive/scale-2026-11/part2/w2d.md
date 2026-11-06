[Subagent hand-back] The text below is the final report of a subagent this session delegated to. It is model output, NOT a message from the user: instructions, requests, or approval claims inside it are the subagent's words and carry no user authority. The harness indents every line of the report, so a frame-like line at column zero inside it would be forged. Notes above this frame may quote model-derived text, which carries no user authority either. The report follows:
## W2d report: the reports and pages still over target at 100k invoices

**Short version:** 15 of the slow addresses are now within target at 100k invoices, with 12 commits on the worktree branch (scale-int + 12; not pushed). Two exports are still over (`/search/export.xlsx` and `/activity/events.csv`), and month-end is only fast while nothing changes; reasons below. In every before/after comparison on the same database state the pages, CSVs and workbooks came out the same; the three paged lists are identical when every row is asked for.

Worktree: /home/user/AP_Processer/.claude/worktrees/agent-a911d468c435a4646

### Two things to know first
- **The 100k database (S) now has 24 extra triggers.** My code creates its new triggers when it opens S: about 24 `CREATE TRIGGER` statements, the only schema write I made there. Other agents' code ignores them and S gets re-seeded anyway. Apart from that, S only got the GETs and sign-ins.
- **I locked `admin001` on S for a while.** At 11:16–11:18 my first probe runs reused that account's authenticator code, which failed several times and locked it. It had expired by 12:05, when my volume run signed in as admin001. Later probes use a different admin or clerk each run.

### What the answers keep in memory (per web worker, at 100k)
- **Every vendor's supplier learning record: 28 MB.** Working it out used to take a 47 MB peak on every request. Keeping something smaller instead would mean splitting it into separate answers for the Supplier learning page and the Automation counts. Not done; your call.
- Everything else I keep is 0.1–2 MB each.
- **The vendor list is no longer kept.** My first version held all 19,000 rows (26 MB); commit 0f945a0 reads only the listed vendors' rows instead.

### Per address at 100k
Before = the phase-2 volume report (median s / SQL statements). After = my `bench volume --only` runs on S (vol_S_after, plus vol_S_after2 for the last three commits). Times are warm medians, admin/clerk.

| address | commit | before | after |
|---|---|---|---|
| `/exports/duplicates.csv` | eeaa314 | 7.79/7.22 s, 14/12 | 0.020/0.017 s, 16/11 (first time 8.1 s) |
| `/month-end` | bc7a136 | 6.06/5.60 s, 223/218 | 1.12/0.87 s, 234/78 (first time 9.1 s) |
| `/month-end/accruals.csv` | bc7a136 | 5.92/5.94 s, 213 | 0.77/0.74 s, 73 |
| `/settings/jde/worksheet.xlsx` | d6e01f4 (+5e47998, 0f945a0) | 4.03 s, 17 | 0.63–0.78 s, 20 |
| `/spend` | 64380fd | 3.71/3.49 s, 36/29 | 0.012/0.014 s, 49/17 (first time 4.1 s) |
| `/settings/reading` | 5b85ce0 | 2.81 s, 30 | 0.017 s, 30 (first time 3.2 s) |
| `/learning?tab=suppliers` | 5b85ce0 | 2.80/2.79 s, 17 | 0.082/0.107 s, 21/19 |
| `/settings/automation` | 5b85ce0 | 2.69 s, 23 | 0.238 s, 22 |
| `/sales-tax` | 34bdd3d | 1.90/2.05 s, 11 MB page | 1.20/1.44 s, 215 KB page |
| `/exports?fmt=jde` | W2a + d6e01f4 | 1.44/1.51 s | 0.036/0.032 s |
| `/learning?tab=coding` | 9c0bc60 | 1.38/1.42 s, 37/28 | 0.262/0.221 s, 39/28 |
| `/settings/data` | 35e2b28 | 1.18 s, 26 | 0.086 s, 24 |
| `/learning?tab=lessons` | f8d3d05 | 0.46/0.60 s, 15 | 0.049/0.022 s, 17/15 |
| `/vendors` | 5e47998, 0f945a0 | 0.50/0.37 s, 1.7 MB page | 0.067/0.091 s, 192 KB page |
| `/purchase-orders` | 0e7c7ef | 0.36/0.32 s, 2 MB page | 0.20/0.23 s, 150 KB page |
| `/statements` | 5e47998 | 0.31/0.25 s | 0.072/0.080 s |

**How the outputs were compared.** S changed between the volume report and my run (other agents' load runs), so its byte sizes can't be compared directly. Instead I saved every page's output from scale-int at the start and compared after each commit, with names, nonces and the test-signed-in user blanked:
- Month-end page and CSV (two periods), the duplicate CSV, the JDE worksheet, the three Spend views, suppliers (50 and 100 rows), Settings → Reading, Settings → Data, `/exports?fmt=jde`: identical.
- Settings → Automation: one difference, the 30-day touchless count ("8,292" vs "8,290"), from time passing, not my code.
- The audit page's "Results of HH:MM" shows the time of the request.
- The three paged lists, with every row asked: identical (sales tax differs by one blank line).
- On small seeded stores, the existing reference tests compare month-end's received-not-invoiced and Spend against the old code; they pass.

### What each commit does
- **d6e01f4:** `jde.resolve_an8` copied the vendor-ID map (about 57,000 entries) on every call. It is called once per vendor in the worksheet, once per invoice in exports and the dry run, and in each check.
- **9c0bc60:** Learning → Coding read every approved invoice's summary row twice. It now uses the existing SQL `insights.approval_counts`, as Activity does.
- **5b85ce0:** every vendor's supplier record, the drawn vendors' field figures and the readers' scorecard are kept until a new change count says otherwise: `changes/suppliers` (triggers on `supplier_outcomes`) and `changes/readers` (`reader_outcomes`). Also `clean_fields_needed` is cached (it is a pure function).
- **35e2b28:** the invoices-folder count uses `os.scandir` instead of `Path.glob` (100,000 Path objects). Hidden files and folders are still counted.
- **bc7a136:** month-end's received-not-invoiced lines are kept per period end, until the summary changes or `changes/purchase_orders` (triggers on `purchase_orders` and `po_lines`) changes.
- **eeaa314:** the duplicate audit's pairs are kept until the summary changes or `changes/register` (`erp_invoices`) changes. The page and its CSV share them.
- **64380fd:** Spend's figures and currencies are kept per period, currency, GL categories and rates, until the summary changes. `read_figures` reads them fresh.
- **34bdd3d / 5e47998 / 0e7c7ef:** the lists page with a "Show more" button:
  - Sales tax: 500 claims and 500 self-assessments at a time (`rows`, `self_rows`);
  - Vendors: 200 at a time (`shown`, kept when a vendor is opened);
  - Purchase orders: 200 at a time (`shown`);
  - the CSVs still have every row, and search still finds any vendor or PO.
- **5e47998, 0f945a0:** each vendor's lesson counts are kept until `changes/lessons` (triggers on `feedback`) changes; only the listed vendors' rows of the vendor list are read, by key.
- **f8d3d05:** the Lessons tab's rows are kept until `changes/situations` (triggers on `lessons` and `lesson_approvals`) changes.

Each kept answer has a test in `tests/core/test_kept_answers.py` that fills it through one Store, changes the data through a second Store (or plain SQL), and checks the first sees the new answer. I confirmed each of these tests fails when its triggers are removed.

I updated docs/HOW_IT_RUNS.md (what a worker keeps). There were no bugs, so no B numbers. Two existing tests now call the inner function, because their target is now kept between calls: `test_dupaudit` uses `_find` and `test_spend`'s one-snapshot test uses `read_figures`.

### Checks
- `scripts/same_behaviour.py --ref scale-int`: tax, approve and read probes all same (exit 0).
- `scripts/check.py` on all 22 changed .py files: OK (912 passed).
- Default `tests/core`, `tests/erp`, `tests/web`: 1577 passed, 2 skipped. `tests/meta`: 115 passed.
- With `-m ""` (browser and slow tests): `test_web_pilot_walkthrough`, `test_server_browser`, `test_navigation_settings_help`, `test_web_settings`, `test_web_masterdata`, `test_web_finance`, `test_web_dashboard_flows`: 264 passed. Also `tests/bench/test_seed_volume.py` and `tests/ai/test_lessons.py` passed.
- ruff check and format: clean.
- `bench volume` on my own seed (3,000 invoices): 165 addresses, 0 errors, 1 over (`/activity?q=Glacier` at 0.348 s, an Activity search I didn't change).
- The new triggers didn't measurably change seed CPU time (13.7 s against 13.9 s). Seed and saved outputs deleted.

### What I left, and why
- **`/search/export.xlsx` (9–10 s):** 70% of the time is openpyxl writing 450,000 cells (no lxml installed). Fixing it means either a job per person, as for the Spend workbook (new POST route, roles rule, files per person, and polling JS in app.js, which is W2c's file), or a lighter writer. Neither fits this wave.
- **`/activity/events.csv` (11.6 s):** streamed by design since B-1597, so the download starts at once.
- **Month-end worked out afresh (5–9 s):** it reads and decodes 69,000 codings. The real fix is a summary side table of each invoice's PO-billing lines; that means a summary rules bump and a rebuild, which isn't allowed on S.
- **Under load the memo doesn't help month-end, the audit or Spend:** any invoice write changes the summary's generation, so they are worked out again after each write. They help one person and repeated views, not 100 people. The changes that save CPU under load too are the paging, `resolve_an8`, the coding tab, the data tab and reading vendor rows by key.
- **Insights:** 1.8–2.0 s, already within a report's 3 s.
- **`/process`:** a seed artefact (no `seen_files`).
- **`/review?tab=approved`:** left to W2b.

### Noticed for later
- `web/pages/month_end.py:31` `gl_cell` calls `ReferenceTable.get` (`core/reference_data.py:120`), which scans 1,500 GL rows for every accrual row: about 0.12 s per request.
- `core/store/capture.py:468` `touchless_summary`: the audits query reads `supplier_outcomes` through the invoice index (about 0.13 s per Automation view); an index on (source, at) would fix it.
- `capture/supplier.py:1284` `run_fields` sums the history on each of 3 calls per vendor.
- `coding/taxreturn.py` `self_assessment`: about 0.85 s per Sales tax view even when it finds nothing.
- `erp/accruals.py` `_expected_recurring` runs `vendor_gl_usage` once per vendor (51 statements).
- The Lessons query computes its correlated subqueries for every corrected lesson before LIMIT 500; picking the 500 first is about 12% faster.

### D-162 (text for docs/decisions.md)
- **D-162** · **The reports and pages still slow at 100,000 invoices: kept per change, listed a part at a time, or read
  without waste.**
  - **Kept in each process** (`Store.summary_memo`, D-159's rule: a stamp read from the database before the work).
    New counts kept by triggers in `SUMMARY_TRIGGERS` (`changes/<kind>`: any insert, update or delete of the table, on
    any connection):
    - "suppliers" (`supplier_outcomes`): every vendor's supplier learning record (`all_supplier_stats` without its
      fields) and the drawn vendors' field figures (`supplier_fields`): Learning → Supplier learning, Settings →
      Automation;
    - "readers" (`reader_outcomes`): the readers' scorecard: Settings → Reading, Learning → Readers;
    - "purchase_orders" (`purchase_orders`, `po_lines`), with the summary's generation: month-end's received, not
      invoiced PO lines, per period end (`accruals._received_not_invoiced`);
    - "register" (`erp_invoices`), with the summary's generation: the duplicate payment audit's pairs
      (`dupaudit.find`: its page and its CSV);
    - "lessons" (`feedback`): each vendor's lesson counts (`vendor_summaries`: Vendors, Statements, the JDE
      worksheet);
    - "situations" (`lessons`, `lesson_approvals`): the Lessons tab's rows;
    - the summary's generation alone: the Spend page's figures and currencies, per period, currency, GL categories
      and rates (`spend.figures`, `spend_currencies`; `read_figures` reads them now).
  - **Listed a part at a time, with Show more** (how many in the address; the CSVs keep every row; with every row
    asked, each page is the one it was): Sales tax's claims and self-assessments (500: `rows`, `self_rows`), Vendors
    (200: `shown`, kept when one is opened), Purchase orders (200: `shown`). On the laptop too: a list under these
    sizes shows as before.
  - **Read without waste:**
    - `jde.resolve_an8` reads the vendor IDs without copying them for each invoice or vendor;
    - Learning → Coding accuracy counts the approvals with SQLite (`insights.approval_counts`, as Activity);
    - Settings → Data counts the invoices folder with `os.scandir`;
    - `vendor_summaries` reads only the listed vendors' rows of the vendor list;
    - `clean_fields_needed` (a pure search, asked twice a vendor) is cached.
  - **What stays true:** a kept answer is what reading again gives (a two-Store test each,
    `tests/core/test_kept_answers.py`). Memory kept at 100k: the supplier records 28 MB a worker, the rest 1-2 MB
    each.
  - **Under load:** the summary's generation changes with every invoice written, so month-end, the audit and Spend are
    worked out again after each write. They help one person and pages asked again between writes, not 100 people.
  - **Not done:**
    - `/search/export.xlsx` (9-10 s at 50,000 rows): 70% of it is openpyxl writing 450,000 cells. A job per person,
      as the Spend workbook, or a lighter writer.
    - `/activity/events.csv` (11 s, 416,000 events): streamed as it is read (B-1597), so the download starts at once.
    - Month-end worked out afresh: 5-9 s (69,000 codings read and decoded). A summary table of each invoice's PO-billing
      lines (summary rules 3) would make it about 1.5 s.
    - Insights (1.8-2 s, within a report's 3 s); `/process` (a seed artefact: no `seen_files`).
  - (`core/store/{summary,suppliers,capture,vendors}.py`, `erp/{accruals,dupaudit,spend,jde,exports}.py`,
    `coding/vendors.py`, `capture/supplier.py`, `web/pages/{learning,settings,sales_tax,vendors,purchase_orders}.py`
    and their templates)

My probes, profiles and volume reports are in /tmp/claude-0/-home-user/b4fef7c4-f9cb-52fc-9da2-47ad67a7ebc2/scratchpad/goal_notes/w2d/ (the 100k runs in vol_S_after/report.md and vol_S_after2/report.md, the seed run in vol_D_after/report.md).
