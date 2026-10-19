# ap_coder/erp: ERP files and reports (exports, JD Edwards, registers, accruals, statements, directory, insights)

What leaves AP Coder for the ERP and what AP reads back: export batches (xlsx, CSV, a CSV layout of AP's own), JD
Edwards E1 Z-files and the JDE settings worksheet, the ERP's invoice register, approval-stamped PDFs; and the reports
over the invoices: month-end accruals, statement reconciliation, duplicate payments, audit exceptions, spend, insights,
recurring vendors, the invoice directory and its quick search.
Grouped here from the flat `ap_coder/` (docs/decisions.md D-33): `ap_coder.exports` and the other old paths are aliases for one release.

**Depends on:** `ap_coder/ai`, `ap_coder/capture`, `ap_coder/coding`, `ap_coder/core`, `ap_coder/intake`, `ap_coder/ops`
**Used by:** `ap_coder/ai`, `ap_coder/coding`, `ap_coder/web/pages`

## Interface
- `build()` (exports.py): format, approved invoices, GL names, batch, vendor ids, layout -> (bytes, name, mime);
  `fmt="jde"` raises ValueError: JDE files come from `build_zip()` (jde.py; columns in docs/JDE_E1.md). `FORMATS` (exports.py):
  xlsx, csv, custom, jde, with their help; a new one is a key there, a branch in `build()` (else it is built as xlsx) and a tab in web/pages/exports.py's `SHORT_FORMATS` (`AGAIN_FORMATS` labels past batches).
- `stamped_pdf()` / `batch_zip()` (stamp.py): the approved PDFs; `rows_from_records()` (registers.py): an ERP register's rows (the register itself is in the store).
- Reports: `build()` (accruals.py), `reconcile()` (statements.py), `find()` (dupaudit.py), `exceptions()` (controls.py), `workbook()` (spend.py),
  `compute()` (insights.py), `detect()` (recurring.py).
- `run()` (directory.py): a `Query` -> `Result` (the column filters, as JD Edwards' "query by example"); `find()` (search.py): the quick search.

## Gotchas and decisions
- A batch re-downloads the same invoices with today's settings and master data.
- Loader files (JDE Z-files, CSV, Custom CSV) hold text as it is (B-26, B-92); CSV/xlsx files for people go through `core/safe.py`.
- directory.py builds query text only; the store runs it (id lists are one JSON parameter, B-11).
- The JDE worksheet is read with openpyxl after `triage.sheet_unzips_too_big()` (an emailed workbook is untrusted).

### Bug lessons
From docs/BUGS.md (one line each; the entry has the repro and the fix).
- B-22: Don't treat "same vendor and number" as "same bill" unless we actually sent it. `tests/test_dupaudit.py::test_an_unexported_invoice_already_in_the_erp_is_a_duplicate`
- B-23: Don't write document text to a workbook without neutralising it. `tests/test_jde_readiness.py::test_the_worksheet_holds_no_formula_from_a_document`
- B-24: Don't compare two records with lookups of different strength. `tests/test_jde.py::test_an_earlier_export_known_only_by_its_gst_number_is_a_duplicate`
- B-25: Don't read meaning into free-text columns. `tests/test_statements.py::test_a_payment_word_in_a_description_does_not_turn_an_invoice_into_a_payment`
- B-26: Don't apply Excel quoting to files meant for a database. `tests/test_jde.py::test_z_files_hold_the_text_as_it_is_without_spreadsheet_quoting`
- B-27: Don't accrue a bill you have already flagged as a duplicate. `tests/test_accruals.py::test_a_resent_copy_of_a_bill_already_counted_is_not_accrued_again`
- B-29: Don't bind an unbounded id list as separate SQL variables. `tests/test_directory.py::test_quick_search_with_more_hits_than_sqlite_takes_variables`
- B-85: Don't show a column under a label it doesn't mean. `tests/test_jde_readiness.py::test_the_dry_run_gross_is_the_whole_invoice_in_every_amount_mode`
- B-86: Don't fix one misreading of free text by trusting another field without the sign. `tests/test_statements.py::test_a_payment_line_with_the_invoices_number_is_a_payment`
- B-87: Don't let a file that skipped re-checking hit code that assumes the checks ran. `tests/test_jde.py::test_a_due_date_jde_cannot_hold_leaves_vlddj_blank_in_a_batch_downloaded_again`
- B-89: Don't compare non-ASCII text with SQLite `lower()` (core's store lesson, again). `tests/test_directory.py::test_text_filters_ignore_accents_and_case_beyond_ascii`
- B-90: Don't collect codes in cells that Excel will turn into numbers. `tests/test_jde_readiness.py::test_the_worksheet_answer_and_code_cells_are_text`
- B-92: Don't apply Excel quoting to files meant for a database (B-26 again). `tests/test_exports_bulk.py::test_erp_csv_keeps_text_exactly_as_typed`
- B-126: Don't fix a repeated mistake one place at a time: write a check that finds every place, then fix what it finds. `tests/test_directory.py::test_a_status_filter_with_more_statuses_than_sqlite_takes_variables`

## Files
- `__init__.py`: the package docstring only.
- ERP: `exports.py` (xlsx/csv batches), `export_layout.py` (custom CSV); `jde.py` (JDE Z-files), `jde_worksheet.py` (JDE questions workbook); `registers.py` (reads an ERP register's columns), `stamp.py` (approved PDFs).
- Reports: `accruals.py` (month-end), `statements.py` (statement reconciliation); `dupaudit.py` (duplicate payments), `controls.py` (audit exceptions); `spend.py` (spend, data download), `insights.py` (business case, operations, vendor workload); `recurring.py` (late recurring vendors).
- Directory: `directory.py` (invoice table), `search.py` (quick search).
