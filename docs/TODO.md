# To do

The owner's list of what is still to do on AP Coder, newest last. Remove an item once it is done (its bug or
decision number then says what was done).

- **User-facing wording in one place (after the pilot, once the wording settles).** The words people see kept by key in
  one file, with tests that check each key is used and each page's key exists, so a wording change is one edit and
  can't silently break the first-day walk.
- **A sweep of the timing-sensitive tests.** Beyond B-2634 and B-2635: find the tests that wait on a sleep or race a
  thread (`time.sleep`, short deadlines, "until" right after a start) and make them wait on an event or a step the test
  controls.
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
  `tests/core/test_imports.py`, Python 3.12): a writer held the lock for 30 s. The children now print SQLite's name for
  the error, so read it from the next failure first; every call opens and closes its own connection
  (`core/store/_base.py`), so the WAL files come and go between processes.
- **Keep the test map and the test times fresh (after Monday).** Each tests part of CI now keeps its `--junitxml`
  report (the artifact `junit-<OS>-python-<version>-<whole or fast>-<n>`, 14 days). Still to do: a nightly job that
  refreshes `tests/test_times.json` from main's reports (`python -m tests.times`) and records `tests/test_map.json.gz`
  (`python -m tests.test_map record`, D-333) and opens a pull request with both; then Windows' own times for its
  parts (`tests/times.py` and `_shard` in `tests/conftest.py`).
