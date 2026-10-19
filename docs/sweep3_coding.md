# Sweep 3: coding (B-132 to B-139)

#### B-132 · Bulk approval takes a clean credit note · medium
- **Where:** `ap_coder/coding/bulk.py:22-24` (`_needs_a_person`, used by `clean_candidates` and `bulk_approve`)
- **Symptom:** a credit note with no warning (it credits an approved invoice) is listed as clean on the queue and approved by "Approve all clean" with nobody opening it, although Settings → Automation says a credit is always applied by a person.
- **Cause:** bulk approval only looked at the findings' severity and `ALWAYS_A_PERSON`; the credit-note gate lives in `capture/supplier.touchless_gates` (a total below zero, or titled a credit note) and bulk.py never asked it.
- **Reproduce:** approve the Northwind ON sample, then process a negated copy `CN-1` with `original_invoice_number` set (only info findings `CREDIT_NOTE_FOR`), and call `clean_candidates` / `bulk_approve`.
- **Reproduced:** yes (`assert (2 not in [2, 3])`: the credit note and a positive one titled a credit note were both listed and approved)
- **Fix:** `_credit_note()` asks `touchless_gates(grand_total=..., credit_note=<capture's credit_note>)`, the touchless rules' own notion; `clean_candidates` leaves such an invoice out and `bulk_approve` skips it with "it is a credit note: a credit is always applied by a person".
- **Test:** `tests/test_exports_bulk.py::test_bulk_approval_never_takes_a_credit_note`
- **Lesson:** Don't write a second list of what always needs a person: ask the gate the touchless rules use.

#### B-133 · A line on an unlisted home cost center lets another site decide the place of supply · medium-low
- **Where:** `ap_coder/coding/tax.py:311-314` (`supply_source`)
- **Symptom:** we are in Alberta with `CC400=BC` in Settings → Review; an invoice with one line to CC400 and one to the home cost center CC100 is checked (and self-assessed in the tax return) as a BC supply: PST expected on the whole invoice.
- **Cause:** cost centers not in `site_provinces` (and blank ones) were dropped from the set of sites, so the one listed site looked like "every line goes to BC".
- **Reproduce:** `supply_source(coding([(1000, ["GST"], "CC400"), (1000, ["GST"], "CC100")], ...), TaxSetup(our_province="AB", site_provinces={"CC400": "BC"}))`.
- **Reproduced:** yes (`('BC', 'site')`, expected `('AB', 'ours')`)
- **Fix:** an unlisted (or blank) cost center counts as our province; a site's province is used only when every line maps to it. Without `our_province` set, unlisted cost centers still say nothing (as before). taxreturn.py goes through the same `supply_source` (B-13), so the tax return follows.
- **Test:** `tests/test_tax.py::test_a_line_on_an_unlisted_site_is_delivered_to_our_province`
- **Lesson:** Don't drop the values a lookup doesn't list: an unlisted key has a default meaning too.

#### B-134 · A site typed in lower case in Settings is never matched · low
- **Where:** `ap_coder/coding/tax.py:311` (`supply_source`)
- **Symptom:** "cc400=BC" typed in Settings → Review is saved, but lines coded to CC400 are still taxed as delivered to our province.
- **Cause:** `site_provinces` keys were compared with the line's cost center exactly (case and spaces), while the JD Edwards mapping compares cost centers upper-cased.
- **Reproduce:** `supply_source(<one line on CC400>, TaxSetup(our_province="AB", site_provinces={"cc400": "BC"}))`.
- **Reproduced:** yes (`('AB', 'ours')`, expected `('BC', 'site')`)
- **Fix:** both sides are trimmed and upper-cased before the lookup.
- **Test:** `tests/test_tax.py::test_a_site_typed_in_lower_case_is_still_that_cost_center`
- **Lesson:** Don't compare codes a person types more strictly than the rest of the app compares them.

#### B-135 · "End of month" in the interest clause makes Net 30 count from month end · medium-low
- **Where:** `ap_coder/coding/terms.py:141` (`parse_terms`)
- **Symptom:** "Net 30, interest of 2% per month charged at end of month on overdue accounts" on an invoice of 2026-09-10 is due 2026-10-30 instead of 2026-10-10: the due-date sort and the overdue check are 20 days late.
- **Cause:** EOM was any "end of month" anywhere in the terms, including the late-payment clause.
- **Reproduce:** `payment({"invoice_date": "2026-09-10", "payment_terms": "Net 30, interest of 2% per month charged at end of month on overdue accounts", "grand_total": 1130}).due`.
- **Reproduced:** yes (`2026-10-30`, eom=True)
- **Fix:** `_eom()` leaves out an EOM whose clause has interest words before it ("interest ... at end of month", "Overdue accounts are charged ... at the end of the month"); "Net 30 EOM, interest 2% per month" keeps its EOM.
- **Test:** `tests/test_terms.py::test_end_of_month_in_the_interest_clause_does_not_make_the_terms_eom`
- **Lesson:** Don't read the words of the late-payment clause as the payment terms (B-83 again, for EOM).

#### B-136 · "Net 2 weeks", "Net 3 semaines" and "Net 10 prox" are read as days · low
- **Where:** `ap_coder/coding/terms.py:76-79` (`_NET`)
- **Symptom:** "Net 2 weeks" is due 2 days after the invoice, "Net 3 semaines" 3 days, "Net 10 prox" 10 days (instead of the 10th of the next month); "2% 2 weeks net 30" offers a 2-day discount.
- **Cause:** the "net N" pattern only refused months after the number; weeks and prox were never parsed.
- **Reproduce:** `payment({"invoice_date": "2026-09-10", "payment_terms": "Net 2 weeks", "grand_total": 1}).due`.
- **Reproduced:** yes (`2026-09-12` net=2; "Net 3 semaines" `2026-09-13`; "Net 10 prox" `2026-09-20`)
- **Fix:** weeks / semaines count 7 days each (also "Payable within 2 weeks"); "Net N prox" (also "10th prox", "proximo") is due on day N of the month after the invoice's (last day of a shorter month), `Terms.prox_day`; a discount's number before weeks is not its days. A slash discount's "2/10th prox" is not read as prox.
- **Test:** `tests/test_terms.py::test_weeks_and_prox_terms_are_never_read_as_days`
- **Lesson:** Don't take the number after "net" as days without looking at the unit that follows it.

#### B-137 · "Net EOM" with no days is due 30 days after the invoice · low
- **Where:** `ap_coder/coding/terms.py:172` (`parse_terms`, `payment`)
- **Symptom:** "Net EOM" / "Due EOM" on an invoice of 2026-09-10 is due 2026-10-10 (the default days, source "default") instead of 2026-09-30.
- **Cause:** EOM terms with no day count gave no net days, so `payment()` fell back to the default days from the invoice date.
- **Reproduce:** `payment({"invoice_date": "2026-09-10", "payment_terms": "Net EOM", "grand_total": 1})`.
- **Reproduced:** yes (`due=2026-10-10 src=default`)
- **Fix:** EOM terms with no days are net 0 from the end of the invoice's month.
- **Test:** `tests/test_terms.py::test_eom_terms_with_no_days_are_due_at_the_end_of_the_month`
- **Lesson:** Don't fall back to the default when the terms printed say when it is due, only without a number.

#### B-138 · A PO export's "Item" column (an SKU) is taken as the PO line number · low
- **Where:** `ap_coder/coding/po.py:52` (`COLUMN_ALIASES`, `map_columns`)
- **Symptom:** importing a PO file with columns "PO Number, Vendor, Item, Description, ..." guesses "Item" as the line number: SKU 88341 becomes PO line 88341 (and "WID-0042" no number), shown on the PO and its matches.
- **Cause:** "item" and "itemnumber" were aliases of the line number, though in most exports they hold the part number.
- **Reproduce:** `map_columns(["PO Number","Vendor","Item","Description","Qty","Unit Price","Amount"])` then `rows_from_records` with Item "88341".
- **Reproduced:** yes (`'line_number': 'Item'`, row `line_number: 88341`)
- **Fix:** "item" / "itemnumber" are no longer guessed as the line number; lines without a line column are numbered in file order on import, and a person can still pick the column by hand.
- **Test:** `tests/test_po.py::test_an_item_column_is_an_sku_never_the_po_line_number`
- **Lesson:** Don't guess a column from a header that usually means something else.

### Not fixed
- **N-XX** · coding · Discount terms written with prox or weeks ("2/10th prox net 30th", "2% 10th prox", "2% 2 weeks net 30") still give no discount (the due date is the default or the prox day): the discount date model has only days from the invoice or month end; rare, left for a design of prox discounts.
