# Code review of the core engine

A skeptical review of the engine behind the dashboard: reading, coding, checks, touchless processing, the store,
the exports and the background workers. Reviewed at `main` f56a800. Each bug fixed here has a test that failed
first, in the same commit. The full suite, `ruff check .` and `ruff format --check .` pass after every commit.

What was looked for: logic errors, money and tax math, holes in the invoice status machine, touchless approvals that
should not happen, ways to lose data, races between the web threads and the background workers (page reader, Azure
check, jobs), crashes on odd files (empty, damaged, password-protected, blank, rotated, non-UTF-8, odd dates), dead
code, misleading names and duplicated logic that has drifted apart.

**Overall verdict:** the engine is sound and works as it is meant to. The status machine is enforced in SQL (each
transition is a guarded `UPDATE ... WHERE status = ?`, or a `BEGIN IMMEDIATE` around a read and a write). Money is
rounded the way an ERP expects. Every background worker re-checks the invoice before it changes it. The bugs found
were all at edges: a stale button, a check made at one moment and acted on later, or two copies of the same logic
that had drifted apart. Nine are fixed (listed below). What is left is listed per module, with the reason it was
left.

## Bugs fixed

| # | Module | Bug | Fix | Test |
|---|---|---|---|---|
| 1 | `bulk.py` | Bulk approval re-ran `finalise_coding`, which does not raise the warnings only the stored validation holds (`AZURE_DI_DISAGREES`, `LINE_ITEMS_AGREE`, `TEXT_LAYER_MATCHES_PAGE`). An invoice flagged between the queue page loading and the click was approved anyway. | Skip an invoice whose stored validation has an error or a warning | `test_exports_bulk.py::test_bulk_approve_skips_an_invoice_flagged_after_the_page_listed_it` |
| 2 | `store.py`, `web/pages/review.py`, `invoice.py` | A stale **Delete** button (on the review screen, or on the Failed / rejected list) deleted the invoice whatever it had become since, including an invoice that had been approved and exported. The rejected list's **Reopen** / **Restore** reopened an invoice approved since, with no reason given: what it taught was withdrawn and the touchless suspension (`on_reopen`) was skipped. | `Store.delete_invoice(keep_approved=True)` (check and delete in one transaction), `Store.reopen(only_rejected=True)` | `test_web_review.py::test_a_stale_page_never_deletes_or_reopens_an_approved_invoice` |
| 3 | `exports.py` / `store.py` | Two lookups of the vendor master's ID had drifted apart. `Store.supplier_key_for` matched a master that keeps only the 9-digit Business Number against the invoice's full RT account; `exports._vendor_id` did not. The CSV, XLSX and JDE files left the Vendor ID / AN8 blank for a vendor whose touchless record was found. | One helper, `vendors.master_vendor_id`, used by both | `test_vendor_master.py::test_exports_find_the_vendor_id_as_supplier_learning_does` |
| 4 | `azure_check.py` | An invoice held for a spot-check was approved by AP Coder as soon as Azure agreed, however long after: even if the switch had been turned off meanwhile, the vendor kept supervised or suspended, or the touchless limit lowered below its total. The existing test approved an 18,017.85 invoice over the 5,000.00 limit. | `workflow.still_touchless` checks again, as things stand now | `test_azure_check.py::test_a_held_invoice_is_not_approved_once_touchless_no_longer_applies` |
| 5 | `pipeline.py` | A password-protected PDF showed "ValueError: document closed or encrypted". | Said plainly in `plain_error` | `test_pipeline.py::test_a_password_protected_pdf_is_said_plainly` |
| 6 | `terms.py` | An invoice date at the very end of the calendar (a placeholder 9999-12-31 typed on the review screen) made the due-date sum overflow. The review screen's check, the approval and the store's `_due` raised `OverflowError`. | No due date is worked out from such a date | `test_terms.py::test_a_date_at_the_end_of_the_calendar_has_no_due_date_instead_of_crashing` |
| 7 | `capture/supplier.py` | Touchless processing approved an invoice with a line posted to `UNASSIGNED`. `GL_UNASSIGNED` and `CC_UNASSIGNED` are only warnings, and `should_auto_approve` blocks only errors. The review screen's approve bar never lets a person do this. | `should_auto_approve` refuses them | `test_touchless.py::test_a_line_without_an_account_is_never_approved_without_a_person` |
| 8 | `capture/normalize.py` | With thousands separators and a decimal point both printed, a decimal point followed by three digits was read as a thousands separator: "1,234.567" (a unit price) became 1,234,567. | When the earlier separators are all the other kind, the last one is the decimal point | `test_capture.py::test_parse_amount` (new cases) |
| 9 | `capture/workflow.py`, `inference.py` | A line that neither AP's approvals nor a coding rule could code gets the local chat model's account. The pipeline's own note says that is right on 11 of 41 sample lines. A valid account raises no finding, and the touchless bar measures header fields only, so the guess was posted without a person. | `invoice_gates` counts lines marked `inference.MODEL_SUGGESTED` | `test_touchless.py::test_a_line_only_the_local_model_coded_is_never_approved_without_a_person` |

## Per module

### `pipeline.py`: read, code, check, save

Reads one file (text layer or OCR), codes it (from capture plus history, or with the chat model), validates it, runs
capture and the touchless decision, saves it, and puts it in line for the page reader and the Azure check. The same
function runs again when the page reader has read the pages (`save=False, invoice_id=`).

**Verdict: sound.** One bad file never stops a batch, and a failed save is reported, never raised. A picture is made
into a PDF before it is read.

- *Fixed:* #5 (password-protected PDF).
- *Left:* capture's own review findings (`review_issues`: `TEXT_LAYER_MATCHES_PAGE`, `CAPTURE_TOTALS`,
  `LINE_ITEMS_AGREE`) are added to the report after `validate_coding` has worked out `adjusted_confidence` and
  `requires_review`, so they lower neither. This is consistent with how `validate_coding` treats any warning (one
  warning alone does not flag review). Touchless processing and bulk approval both look at those findings directly,
  so nothing is approved over them. The queue may still list such an invoice under "Ready". Changing that would
  change the confidence numbers the calibration and tests rely on; that is a product decision, not a bug fix.
- *Left:* `process_many(workers>1)` shares one `InvoiceCoder` and extractor across threads. That is fine for the
  local reader. Only the developer-only Azure OpenAI client relies on the `openai` client being thread-safe, which
  it is documented to be.

### `intake.py`, `extraction.py`, `capture/layout.py`: getting the words

**Verdict: sound.** An empty file, a damaged file, a non-PDF named `.pdf`, a damaged PNG, a blank page, a
non-UTF-8 `.txt` and an empty `.txt` were each run through the whole pipeline. Each one either ends as a plain-worded
failed invoice or lands in review. None raises.

- *Left:* a digital PDF whose page *displays* sideways (an upright page given `/Rotate 90`) reads almost nothing,
  because the words are mapped to display coordinates and run vertically. Every field then comes back *missing* and
  the invoice goes to a person, which is the safe outcome. Real scans are deskewed by OCR only within ±2°.
- *Left:* `build_layout(max_pages=20)` caps the pages read, and `capture.page_count` is the number of pages *read*,
  not the file's page count. The touchless gate "pages the page reader did not read" compares the page reader's
  pages with that capped count. With the default `AP_PAGE_READER_MAX_PAGES=5` this is never reached (a 6-page
  invoice already goes to a person). Someone who raises that setting above 20 would let pages 21 and beyond go unread
  by every reader. Worth a note in the setting's help, or recording the file's own page count.

### `capture/` (readers, fusion, supplier learning, table finder)

Several independent readers of each header field are fused into one value per field, with a calibrated confidence;
supplier templates are learned from approvals.

**Verdict: sound, with a large surface.** It is calibrated on the benchmark, and its rules are conservative: one
reader alone caps a field at *likely*, and dates that read two ways stay *check*.

- *Fixed:* #8 (mixed separators).
- *Left:* `parse_amount("1.234")` is 1234.0 (thousands), as is "1,234". A Canadian fuel invoice's "1.459 /L" unit
  price is therefore 1459 and gets `LINE_MATH`. The opposite rule would break European thousands. The checks catch
  the misread (line math, the totals), so it was left as it is.
- *Left (dead code):* `supplier.pick_for_audit` is used only by the benchmark (`bench/learning.py`). The pipeline
  uses `workflow.audit_pick`, which hashes the file's content instead of the invoice id. The two do the same job by
  different keys; the benchmark should probably use `audit_pick`. Kept, because the benchmark has no files.

### `capture/workflow.py`, `capture/supplier.py`: touchless processing

One company-wide switch and one bar. A vendor's invoice is approved without a person only when every gate passes.

**Verdict: the design holds; three of its gates had gaps (#4, #7, #9), now fixed.** With the switch off nothing is
approved without a person (checked again at every decision). The audit sample is deterministic, and a correction
suspends the vendor at once.

- *Left (design question):* apart from the always-a-person findings, warnings do not block touchless approval.
  Examples are `TAX_REGIME_MISMATCH`, `GST_HST_NUMBER_MISSING` (the input tax credit cannot be claimed),
  `TAX_RATE_NONSTANDARD`, `HISTORY_CONFLICT` (the line coding contradicts AP's history) and `LINE_MATH`. The
  documented rule is "any failed check", and several of these are checks a reviewer would want to see. Deciding
  which warnings should block needs AP's input, so it was not changed here.
- *Left:* `invoice.py`'s **Correct** endpoint calls `on_reopen` (which suspends a touchless vendor) *before*
  `Store.reopen`. If the reopen is refused (the invoice was exported meanwhile), the vendor is still suspended.
  Arguably that is right, since a person did find an error, but the activity log then shows a suspension with no
  reopen.

### `inference.py`, `local_llm.py` (coding side), `offline_coder.py`

**Verdict: sound.** `suggest_accounts` only accepts codes from the chart, and the pipeline never fails over the
model. Every address goes through `refused_address`.

- *Fixed:* #9. The marker the pipeline writes on a line coded by the model is now a named constant
  (`MODEL_SUGGESTED`), not a literal string.

### `validation.py`, `tax.py`, `terms.py`

**Verdict: correct.** Checked by hand:

- HST, GST + PST, GST + QST and GST-only regimes, with BC's HST years.
- QST on the GST-exclusive base.
- Tax computed once on the total or line by line, within a cent per line.
- Credit notes (every amount negative).
- Zero-decimal currencies.
- `_allocate` always adds up to the total.
- The posting total is checked after per-line rounding.
- EOM terms (the last day of the month), and discounts measured on the subtotal.

- *Fixed:* #6.
- *Left:* `_allocate`, when the weights sum to zero (lines that cancel out), puts all the tax on the first line and
  rounds it to 2 places even for a zero-decimal currency. This is harmless: the JDE export rebalances whole units, and
  such an invoice also fails other checks.

### `memory.py`, learning

**Verdict: sound.** Lessons are withdrawn on reject, reopen and send-back (`_forget_learning`). AP Coder's own
approvals teach nothing. A bulk approval counts towards the touchless bar only through its corrections.

### `bulk.py`

**Verdict: sound after #1.** Each invoice is re-checked and claimed atomically by `approve_invoice`.

### `store.py`: schema, migrations, status, concurrency, backups

**Verdict: sound.**

- Each call opens its own connection (WAL mode, 30 s busy timeout), so threads never share one.
- The status transitions are guarded in SQL:
  - approve: only from *review*;
  - second approval: only the same first approval;
  - reject: not after approval or export;
  - reopen: not after export;
  - export batches: under `BEGIN IMMEDIATE`, and never twice.
- Migrations are idempotent and run after a backup. A database from a newer version is refused.
- A restore first checks the file (no triggers or views; no newer schema) and makes a safety copy.

- *Fixed:* #2, #3.
- *Left (approval limit, needs a decision):* `over_approval_limit` compares a foreign-currency total *as if it were
  CAD* when no exchange rate is set. This is documented. However, 9,000 EUR (about 13,000 CAD) under a 10,000 CAD
  limit needs no second approver, which weakens a segregation-of-duties control. The touchless limit already sends a
  currency with no rate to a person; the approval limit could do the same.
- *Left:* the background workers build a new `Store` every round (every 30 s per worker). Each `Store()` runs
  `executescript(_SCHEMA)` and `_migrate` (no-ops on a current database, plus one `DELETE` on `feedback`). This is
  cheap, but it writes on every round. Reusing the dashboard's `Store` would do.
- *Left:* `restore_from` replaces the database under running background workers. A page read or Azure check that
  was under way when AP restored can write its result (`replace_proposal`, `update_after_azure_check`) into the
  restored database, onto the row with the same id. Both re-check the row first (status *review*, untouched), so the
  damage is limited to a proposal written onto an untouched invoice that may be a different one. A cleaner design
  would pause the workers during a restore (a flag they check between rounds).
- *Left (naming):* `_norm_number` is a one-line alias of `vendors.norm_invoice_number`. `undo_export_batch` on a
  batch already undone logs a second `export_undone` event for 0 invoices.

### Exports (`exports.py`, `export_layout.py`, `jde.py`)

**Verdict: correct.** Amounts go out as `Decimal`, rounded half-up. Each voucher balances to the cent (to the unit for
yen) before export, and the tax is split over the pay items exactly. Julian dates are checked to be within E1's
range. Spreadsheet cells are neutralised against formula injection.

- *Fixed:* #3 (vendor ID / AN8).

### `azure_check.py`

**Verdict: sound after #4.** Only the configured host can be contacted (the request hook covers redirects and
polling), and the key is scrubbed from error messages. The daily page cap is enforced, and the queue is claimed under
`BEGIN IMMEDIATE`.

### `page_reader.py`, `page_worker.py`: the background page reader

**Verdict: sound.**

- The queue is claimed atomically, and every claim is marked with the process that took it, so a read cut off by a
  closed app is put back in line at once.
- The model server's own failures are retried up to 3 times; a bug never leaves an invoice marked as being read.
- A stop request is honoured between pages.
- The page reader's reading is folded in only while nobody has touched the invoice, and the touchless decision is
  made fresh at that moment.

- *Left (dead parameters):* `wants_reading(settings, path, layout_source)` no longer uses `settings` or
  `layout_source`.
