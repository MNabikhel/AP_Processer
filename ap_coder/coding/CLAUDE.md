# ap_coder/coding: the coding checks (sales tax, validation, vendors, POs, rules, terms, review, bulk)

The deterministic side of coding an invoice: Canadian sales tax (GST/HST/PST/QST rates, checks, the GL distribution,
the tax return), the post-coding checks, the vendor master and its fraud signals, PO matching, AP's fixed rules,
payment terms, the review screen's form back to a coding, bulk approval and the email asking a vendor for what is missing.
Grouped here from the flat `ap_coder/` (docs/decisions.md D-33): `ap_coder.tax` and the other old paths are aliases for one release.
(The folder is not called `tax`: tax.py is a module in it.)

**Depends on:** `ap_coder/ai`, `ap_coder/capture`, `ap_coder/core`, `ap_coder/erp`
**Used by:** `ap_coder/ai`, `ap_coder/bench`, `ap_coder/capture`, `ap_coder/core`, `ap_coder/erp`, `ap_coder/ops`, `ap_coder/web`, `ap_coder/web/pages`

## Interface
- `check_taxes()` (tax.py): coding, `TaxSetup` -> `TaxFinding` list; `build_gl_distribution()`: (coding, `TaxSetup`) ->
  posting lines that add up to the total (tax spread in whole units for a currency without cents, e.g. JPY); `supply_source()`: (province, where it came from), the one place-of-supply rule; `place_of_supply()` gives its province (B-13).
- GST/HST number: `valid_gst_number()`, `gst_check_digit_ok()` (tax.py; taxreturn.py uses them); capture's `normalize.luhn_ok()` repeats the check digit: change both.
  With GST/HST charged, `check_taxes()` warns `GST_HST_NUMBER_MISSING`, `GST_HST_NUMBER_FORMAT`, `GST_NUMBER_CHECK_DIGIT` (all in `vendor_mail.ASKABLE`); taxreturn.py lists the claim at risk.
- `validate_coding()` (validation.py): coding, reference and the vendor, PO, terms and duplicate findings -> a report.
- `rules.apply()`: AP's fixed rules, after the coder (they win over it); `tax_number_findings()` (vendors.py); `payment()` (terms.py).
- `math_check()` (taxmath.py): the invoice math again, shown under the tax lines; `bulk_approve()` (bulk.py); `draft()` (vendor_mail.py).

## Gotchas and decisions
- Place of supply: `place_of_supply()` (printed ship-to, else the cost center's site in `site_provinces` "CC=PROV" lines, else `our_province`,
  vendor last); taxreturn.py uses the same (B-13). Terms: printed, else the vendor master's, else `default_terms_days`.
- A check code is raised in tax.py, validation.py, vendors.py, po.py, terms.py, capture or ai (azure_check, verifier), explained in `ap_coder/ops/help.py` `CHECKS`
  (tests/test_help.py fails on a code with no help) and shown by web/invoice_view.py.
- A worked-out `place_of_supply` sits beside `ship_to_province`, never in it; the model drops it.
- A check that must always reach a person goes in `capture/supplier.ALWAYS_A_PERSON` (else bulk.py approves an info-level one unseen); one a vendor can fix in `vendor_mail.ASKABLE` (never a fraud or hold code).
- tax.py's `DEFAULT_RATES_PATH` is `data/` at the project root: `Path(__file__)` is one folder deeper since the grouping.
- `norm_tax_number()` (vendors.py) also compares the invoice number in validation.py's DI check: keep it "letters and
  digits, lower case".

### Bug lessons
From docs/BUGS.md (one line each; the entry has the repro and the fix).
- B-12: Don't let file order decide which effective-dated row wins. `tests/test_tax.py::test_a_new_rate_row_wins_over_an_old_one_left_open`
- B-13: Don't compute place of supply twice with different rules. `tests/test_taxreturn.py::test_self_assessment_uses_the_place_of_supply_of_the_tax_checks`
- B-14: Don't treat 0 differently from blank for ERP quantities and prices. `tests/test_po.py::test_lump_sum_line_exported_with_zero_quantity_and_price_is_amount_only`
- B-16: Don't compare a registration number more strictly than the vendor list stores it. `tests/test_vendors.py::test_qst_numbers_compare_by_their_ten_digits`
- B-17: Don't read a number before "month" as days. `tests/test_terms.py::test_net_months_are_calendar_months_never_days`
- B-18: Don't let a regex start matching in the middle of a number. `tests/test_terms.py::test_a_half_percent_discount_is_half_a_percent`
- B-19: Don't hard-code a regulatory threshold without the date it applies from. `tests/test_taxreturn.py::test_registration_number_needed_from_100_dollars_since_2021`
- B-20: Don't throw away the % sign before deciding the unit. `tests/test_tax.py::test_a_rate_written_with_a_percent_sign_is_always_a_percentage`
- B-80: Don't treat "no warnings" as "safe to approve unseen" when some person-only checks are info-level. `tests/test_exports_bulk.py::test_bulk_approve_never_takes_a_check_that_always_needs_a_person`
- B-81: Don't reuse one variable for two records when each carries its own controls. `tests/test_vendors.py::test_a_hold_on_the_name_survives_a_match_by_tax_number`
- B-82: Don't re-implement a check with different rounding rules from the one it mirrors. `tests/test_math_check.py::test_a_yen_invoice_is_rounded_to_whole_yen_as_the_tax_checks_do`
- B-83: Don't veto a match using words that belong to another clause. `tests/test_terms.py::test_interest_words_of_another_rate_do_not_veto_the_discount`
- B-84: Don't take `float()` succeeding as proof that the text is a number. `tests/test_tax.py::test_a_rate_that_is_not_a_finite_number_is_refused`

## Files
- `__init__.py`: the package docstring only.
- Tax: `tax.py` (rates, checks, GL distribution), `taxmath.py` (invoice math again); `taxreturn.py` (ITC/ITR claims, PST/QST self-assessment), `validation.py` (post-inference checks).
- Coding rules: `vendors.py` (vendor master, fraud signals), `po.py` (PO matching); `rules.py` (fixed rules), `terms.py` (payment terms).
- Review: `review.py` (form back to coding, line splits); `bulk.py` (bulk approval), `vendor_mail.py` (request email to a vendor).
