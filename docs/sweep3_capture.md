# Sweep 3: capture

#### B-182 · A currency code in an equivalent note wins over the code on the total's label · high
- **Where:** `ap_coder/capture/normalize.py:476` (`currency_evidence`)
- **Symptom:** A US-dollar invoice printing "Total USD" (its amount in the next column) and a note "CAD equivalent: CAD 1,438.50" is read as Canadian dollars, verified.
- **Cause:** Only a code printed on an amount on the same line counted as on the amounts; the layout reads "Total USD" and "1,050.00" as two lines, so the only code "on an amount" was the equivalent note's. Several codes fell back to the first in the list of currencies (CAD), not the first printed.
- **Reproduce:** `currency_evidence("Total USD\n1,050.00\nCAD equivalent: CAD 1,438.50")`; on a page, `analyze()` with the page reader.
- **Reproduced:** yes (`{'currency': ('CAD', 'verified', 0.998)}`)
- **Fix:** A code on the total's label line ("Total USD", "Amount Due CAD", "Montant dû") counts as on the total and comes before codes on other amounts; an equivalent note is an exchange note (not on the invoice's amounts); several codes give the first printed.
- **Test:** `tests/test_sweep3_capture.py::test_the_code_on_the_total_line_is_the_currency_not_one_in_an_equivalent_note`, `tests/test_sweep3_capture.py::test_several_currency_codes_are_given_in_page_order`
- **Lesson:** Don't decide that a code is on an amount from one line of text when the layout can put the label and the amount in two columns.
