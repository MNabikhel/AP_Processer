# ap_coder/capture: document capture and touchless decisions

Reads an invoice's header fields and line items from the page, with boxes, a calibrated confidence and a status (verified / likely / check / missing) per field, and decides whether an invoice may be approved without a person. One invoice: `analyze()` builds the layout (text layer, Azure DI words, or local OCR), runs every reader (rules, the supplier's template, DI, the AI's values located on the page, a second OCR, the page reader's transcript) and the line readers, then `fuse()` groups their readings, runs the cross-field checks and calibrates, giving a `CaptureResult`; `workflow.py` then applies the gates (`invoice_gates()`, `should_auto_approve()`). It does not code GL accounts (`ap_coder/offline_coder.py`, `ap_coder/inference.py`), run the page reader model (`ap_coder/page_reader.py`) or store anything (`ap_coder/store.py`). Fully offline: OCR and the table model run here; DI readings only when an Azure analysis is passed in (opt-in).

**Depends on:** `ap_coder`
**Used by:** `ap_coder`, `ap_coder/bench`, `ap_coder/web`, `ap_coder/web/pages`

## Interface
- `analyze()` (__init__.py): file path + optional di_raw, ai_values, template, vendor, page_text, local_evidence -> `CaptureResult`.
- `capture_invoice()` (workflow.py): path, AI output, store, di_raw, page_text -> (CaptureResult or None, supplier key, profile); `pipeline.py` calls it.
- `autonomy_decision()` (workflow.py): -> {"state", "auto", "audit", "reason"}; `invoice_gates()`: reasons that always need a person.
- `still_touchless()`, `still_untouched()` (workflow.py): re-checked right before a late approval (Azure check, AI reviewer).
- `learn_from_approval()` (workflow.py): AP's approved values -> lessons, reader outcome rows, supplier outcome rows (the store suspends an autonomous supplier on a correction), template `learn()`; `on_reopen()`.
- `touchless_gates()`, `should_auto_approve()`, `autonomy_status()`, `apply_template()`, `supplier_key()` (supplier.py): pure, no store.
- `build_layout()` (layout.py), `read_fields()`, `read_line_items()` (reader.py), `layout_from_transcript()` (transcript.py), `fuse()`, `calibrate()`, `evidence_key()` (confidence.py).
- Data: `CaptureResult.to_dict()` is saved per invoice (`Store.save_capture`); a supplier profile's "template" is `Template` JSON.
- `calibration.json`: {"about", "runs": [benchmark configs], "evidence": {"field|readers|method|text-or-scan|flags": [cases, right]}}.

## Gotchas and decisions
- Every reader normalizes through `normalize.py`, so two readers agree when their values are `==`. A new reader must too.
- Confidence is the Wilson lower bound of an evidence pattern once it has 40+ cases (`MIN_EVIDENCE`: benchmark plus AP's approvals here), else the readers' own score; changing `evidence_key()`, reader names or methods orphans `calibration.json`: refit with `python -m ap_coder.bench calibrate RUN_DIR...`.
- Extra readers (template, second OCR, transcript, tables) are never fatal; `capture_invoice()` returns None rather than lose an invoice.
- A new "always a person" reason goes in `touchless_gates()`: both `should_auto_approve()` and `invoice_gates()` use it, but only `invoice_gates()` passes what needs the store (limits, exchange rates, pages read, coded lines).

### Bug lessons
From docs/BUGS.md (one line each; the entry has the repro and the fix).
- B-42: Don't let the sign of one number be the only thing that recognises a credit note. `tests/test_capture_edge_cases.py::test_a_french_avoir_printed_positive_is_a_credit_and_needs_a_person`
- B-43: Don't let a code mentioned anywhere on the page outrank an explicit statement in words. `tests/test_capture_edge_cases.py::test_canadian_dollars_stated_in_words_win_over_a_code_in_a_payment_note`
- B-44: Don't treat a format check as proof of whose number it is. `tests/test_capture_edge_cases.py::test_a_registration_number_read_only_in_the_bill_to_block_is_not_verified`
- B-45: Don't split a number until you have checked the reading that keeps it whole. `tests/test_capture_edge_cases.py::test_a_french_quantity_in_one_space_group_is_read_whole`
- B-46: Don't let one adjustment line decide how the whole document's signs are read. `tests/test_capture_edge_cases.py::test_a_credit_note_printed_positive_with_one_negative_adjustment_is_still_a_credit`
- B-47: Don't apply a penalty and then overwrite it. `tests/test_capture_edge_cases.py::test_a_field_that_failed_a_check_does_not_show_its_measured_confidence`
- B-49: Don't cut a number off in the middle of its decimals. `tests/test_capture_edge_cases.py::test_a_price_to_four_decimals_with_four_digits_before_the_point_is_one_number`
- B-50: Don't drop pages past a limit without counting them. `tests/test_touchless.py::test_pages_past_the_local_readers_limit_need_a_person`

## Files
- `__init__.py`: analyze: runs the readers, picks the line items, the text-layer and low-dpi checks.
- `types.py`: Box, Word, Line, DocLayout, Reading, FieldResult, LineReading, CaptureResult, field lists, statuses.
- `layout.py`: words and lines with boxes from the PDF text layer, DI or local OCR; photos, HEIC, OCR cache.
- `normalize.py`: amounts, dates, ids, GST/QST/PST numbers (`luhn_ok()`: the GST check digit, used by the GST_NUMBER_VALID check in confidence.py), currency evidence, credit titles.
- Readers: `reader.py` (rule reader: labels, totals, line items), `locate.py` (finds AI/DI values on the page), `transcript.py` (page reader's text laid out as a page).
- Line tables: `tables.py` (text-layer columns), `table_finder.py` (local layout model finds tables), `line_items.py` (page vs page-reader lines agree).
- `confidence.py`: fusion of the readers, cross-field checks, reader reliabilities, calibration; `calibration.json`: its measured table.
- `supplier.py`: supplier key, learned templates, stats, autonomy policy and states, touchless gates.
- `workflow.py`: capture inside the pipeline: capture, gates, autonomy decision, learning on approval, reopen.
- `bridge.py`: the AI's output as a reader, vendor master record, capture results as review issues.
