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
- **The vendor an invoice is filed under, asked one way everywhere (after Monday: a bug fix).** Two suppliers whose
  names read the same are two vendors, the second kept as "<name> #<supplier number>". Processing looks the payment
  terms up under the vendor the invoice is filed under (`Store.vendor_key_for`, `ai/pipeline.py`); the review screen
  (`web/invoice_view.py`, `web/pages/invoice.py`) and the queue's due note (`web/pages/review.py`) under the name's
  key (`memory.vendor_key`). Measured with the made-up company's SILVERTIP SOFTWARE INC. (supplier 35436, Net 30)
  and a twin SILVERTIP SOFTWARE LTD. (35437, Net 60): for an invoice from the Ltd., `vendor_key` gives "silvertip
  software" and Net 30, `vendor_key_for` with its GST/HST number "silvertip software #35437" and Net 60. Seven
  per-vendor lookups in `web/` and `coding/` use the name's key (terms, open POs, `get_vendor`). Start with a failing
  test that the screen, the queue, processing and bulk approval agree on terms, due date, open POs and Pay from for
  two vendors of one name and for a name AP corrected (D-119); then send those lookups through `vendor_key_for`
  (not every `vendor_key` call: matching names rightly uses a name's key).
- **What the weekend's checks found in the app, to fix after Monday's test (each a bug fix: a failing test first).**
  - The command line on older Pythons: `backup` and `journal` refuse an option put between their two names, such as
    `backup restore --yes NAME`, `backup restore --from FOLDER NAME` or `journal import --dry-run FILE`. This happens
    on Python 3.11, 3.12.0 to 3.12.6 (Ubuntu 24.04's 3.12.3, the server's) and 3.13.0. The forms the docs give put
    the options last and work everywhere. Fix it with `parse_intermixed_args`, as `scripts/moved_same.py` did (B-3025).
  - The page reader's second reading (`ai/page_worker.py` `chat_again`) replaces a proposal without
    `unless_in_use`, and logs `proposal_updated`, which is not a revision, so a review screen open on it is not told
    (B-2805's kind; `NOT_REVISIONS` in `tests/meta/test_lockstep.py` names it).
  - JD Edwards' AIS keep-alive: `erp/jde_ais.py` `_closed_meanwhile` uses `select.select`, which fails once the
    socket's file number is 1,024 or more, so on a busy Linux server every call opens a new connection. Use
    `selectors` (keeping `select` on Windows), with a test that holds 1,100 files open.
  - Windows' "database is locked" when several processes open a Store at once (the four-reader test in
    `tests/core/test_imports.py`, Python 3.12): a writer held the lock for 30 s. The children now print SQLite's
    name for the error, so read it from the next failure first; every call opens and closes its own connection
    (`core/store/_base.py`), so the WAL files come and go between processes.
  - `ap_coder/bench/lines.py` writes `lines.json` with no encoding (ASCII only, a developer's benchmark).
- **Keep the test map and the test times fresh (after Monday).** CI keeps no per-test times: add `--junitxml` and an
  upload to each tests part, then a nightly job that refreshes `tests/test_times.json` and records
  `tests/test_map.json.gz` (`python -m tests.test_map record`, D-333) and opens a pull request with both.
- **Monday's build.** The weekend's work (PRs 159 to 162: the check, the logs, CI, the probes, the map) changed no
  app behaviour: only docstrings in `ap_coder/`. The build to test is the newest `v0.3.0-build.<sha>` release.
