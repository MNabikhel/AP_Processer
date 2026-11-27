# To do

The owner's list of what is still to do on AP Coder, newest last. Remove an item once it is done (its bug or
decision number then says what was done).

- **User-facing wording in one place (after the pilot, once the wording settles).** The words people see kept by key in
  one file, with tests that check each key is used and each page's key exists, so a wording change is one edit and
  can't silently break the first-day walk.
- **The timing-sensitive tests the sweep left.** Of the tests CI failed by their timing since 8 November and then
  passed, all are fixed (the last: B-3054 to B-3057, each waiting on a step it controls) but three, never failed here
  (the 44 files that sleep or race a thread, four rounds beside other tests and on two CPUs beside CPU hogs; the 65
  that read the clock, at five times of day): `test_failover.py`'s hang while reviews hold the chat slots moved in 6 s
  where 3 s (Windows 3.14, once), by the page reads' path ("2 calls in a row"), not by the arrivals behind the silent
  reviews, and `local_llm` runs on the real clock, so the test can only scale it; the sync at the company's size had a
  writer wait 4.3 s where 3 s (`test_jde_sync_volume.py`, Windows, once); the load bench had requests with no answer
  on Windows (`test_bench_load.py`, two runs on 12 November, never since). Still at risk on a runner stalled past
  them: a stand-in answering after a set time while the client gives up sooner (`tests/fake_ais.py` "slow",
  "slow-after"), leases and holds renewed on the real clock (`test_kept_answers.py`, `test_web_process_listing.py`),
  and wall-clock bounds on quick work (`test_caches.py`: 0.05 s).
- **Shorter docs and docstrings from now on.** Say what a thing does in a line or two; move old decision detail to
  `docs/archive/` sooner, keeping only the decision and its reason in decisions.md.
- **Split `core/store/users.py` before the next feature there.** It is at its 1,000-line limit: people, sign-in
  (lockouts, the authenticator) and sessions in parts of their own, before anything new is added.
- **Open POs of two vendors by one name (after Monday: a bug fix).** The review screen and the queue ask the terms of
  the vendor an invoice is filed under now, as processing does (B-3035), but the purchase orders are kept under the name
  on the order (`memory.vendor_key`, `core/store/purchasing.py`), so "this vendor has open POs" (`coding/po.py`
  `po_findings`, the review screen's "Use PO" in `web/invoice_fields.py`) lists both SILVERTIP SOFTWARE INC.'s and
  LTD.'s orders for an invoice from either, and none of the corrected vendor's for a name AP corrected (D-119). Telling
  them apart needs the order's supplier number (as `match_invoice` picks a quoted PO), not `vendor_key_for`.
- **Fixed, but kept back until after the owner's test (each test-first; on the local branch `pre-test`, to merge
  after the test).** B-3036: the command line takes options between a command's names on every Python (on 3.11,
  3.12.0 to 3.12.6, the server's 3.12.3, and 3.13.0, `backup restore --yes NAME`, `backup restore --from FOLDER NAME`
  and `journal import --dry-run FILE` are refused today; the forms the docs give work everywhere). B-3040 and B-3042:
  the chat model asked again (`ai/page_worker.py` `chat_again`, server mode) leaves an invoice someone has open alone,
  tells an open screen, and codes it once it is free; B-3042 still needs its adversarial review.
- **What the pre-test rehearsal found, for after the owner's test.** JD Edwards: the Z-table batch loads charges not on
  the PO as match lines and leaves F0911Z1 empty (B-3044 holds what the keying sheet holds; the Z-tables are not the
  owner's path); the Excel/CSV export of a subcontract billing has no holdback or amount due; the Purchase orders page
  shows wrong billed and remaining figures; the Not ready reason names "Purchase orders", hidden in Simple screens
  (Setup → JD Edwards files, card 6). Tax: the review screen's Tax check still marks the GST of a billing that charges
  it on the work less the holdback in red (B-3045 fixed only the finding). Suppliers: one renamed in JD Edwards
  becomes a second supplier with the same number on a later reload (`import_vendor_master` keeps them by name); a DO
  NOT USE supplier is called "on hold". Approvals: tab counts lag a moment after Final approval or Reject; Park,
  Reject and Un-park name "Invoice #3", not the supplier and invoice number. The guide never asks the tester to set
  the tax areas. Wording: shares above line counts, "approved this way 5 times", "Only 2 of 2 lines", "ask the pilot
  tester" said to the pilot tester, "the Exports page", a sharing colleague told LM Studio "runs on the server", the
  unknown-supplier screen's mixed words, a USD voucher's "Gross", an unreadable .msg counted, the Azure card's
  "saved (enabled)" while off, and `demo` on a new folder greeting "AP Coder was updated".
- **Windows' "database is locked" when several processes open a Store at once** (the four-reader test in
  `tests/core/test_imports.py`, Python 3.12). The only failure in CI's kept logs (12 to 27 Nov, every failed or rerun
  job) is 20 Nov's, before the children printed SQLite's name for the error: three of the four readers failed after
  waiting the 30 s busy timeout (two in a claim's BEGIN IMMEDIATE, one in `Store()`'s start-up, whose every-time
  migration step `_vendor_numbers` writes, so every Store opened takes the write lock for about 1.5 ms); none since.
  The next failure now also says each reader's slowest calls on one clock (a reader holding the lock all that time,
  or the others taking turns meanwhile), whether the write lock is free once all have ended, and the database's
  files. Read those first; every call opens and closes its own connection (`core/store/_base.py`), so the WAL files
  come and go between processes.
- **Keep the test map and the test times fresh** (D-339). Done: CI's nightly measures each test file's time on
  Ubuntu and on Windows (whose parts are now split by its own, `tests/test_times_windows.json`) and records the test
  map in four parts, then puts them on the data branch `test-times`; `python -m tests.times pull` (with
  AP_ALLOW_INTERNET=1) brings them in, to commit with a pull request. Left: see that the first nights' runs push the
  branch (their test-times-branch job), and that the map's four parts end within their 45 minutes; commit a pull now and
  then (nothing does it for us, by design).
