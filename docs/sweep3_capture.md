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

#### B-183 · "Order ID 77812" read as an Idaho address makes a Calgary supplier's dates month first · high
- **Where:** `ap_coder/capture/reader.py:1419` (`_US_ADDRESS`, `us_address`, `read_fields`)
- **Symptom:** A Calgary supplier's invoice dated 03/04/2026 with "Order ID 77812" near the top is verified as March 4 (and its due date 03/05/2026 as March 5), not left ambiguous or settled by its other dates.
- **Cause:** The US address pattern took any two capitals and five digits ("ID 77812": Idaho), and a US address forced month-first dates before the Canadian postal code was looked at; the order then dropped the ambiguous mark. `offline_coder.provinces()` used the same pattern, so the same line above the letterhead's address made the supplier OUTSIDE_CANADA.
- **Reproduce:** the Acme invoice with "Order ID 77812", Invoice Date 03/04/2026, Due Date 03/05/2026, `analyze(..., page_text=[...])`, today 2026-04-10.
- **Reproduced:** yes (`{'invoice_date': ('2026-03-04', 'verified', 0.997), 'due_date': ('2026-03-05', 'verified', 0.991)}`)
- **Fix:** A state and ZIP code count as an address only after a city, a comma or at the start of a line, never after a label word (Order, Vendor, Store, Job, Ticket...): `us_address()`, used by the reader and `provinces()`. Month first is never forced on a supplier with a Canadian postal code or a valid GST/HST number.
- **Test:** `tests/test_sweep3_capture.py::test_an_order_id_is_not_a_us_address_and_a_calgary_suppliers_date_is_not_read_month_first`, `tests/test_sweep3_capture.py::test_a_label_with_an_id_is_not_a_us_address`, `tests/test_sweep3_capture.py::test_a_canadian_supplier_billing_in_us_dollars_is_not_read_month_first`
- **Lesson:** Don't let a two-letter code and five digits stand for an address: check the shape around it, and the evidence against it.

#### B-184 · "Due Date: Upon Receipt" takes the delivery date printed under it · medium
- **Where:** `ap_coder/capture/reader.py:635` (`_labelled`)
- **Symptom:** An invoice printing "Due Date: Upon Receipt" (or "Due Date: N/A") over "Delivery Date: Sep 20, 2026" has its due date verified as 2026-09-20; its terms are not read.
- **Cause:** When the label's own line held no date, the reader looked right of it and then below it, and took the first date it found there even when that date came after a label of its own ("Delivery Date:"). The page reader's transcription, read by the same rules, agreed. "Upon Receipt" after a due-date label was never read as the terms.
- **Reproduce:** the Acme invoice with "Invoice Date: Sep 14, 2026", "Due Date: Upon Receipt", "Delivery Date: Sep 20, 2026" on three lines, `analyze(..., page_text=[...])`.
- **Reproduced:** yes (`{'due_date': ('2026-09-20', 'verified', 0.99), 'payment_terms': (None, 'missing', 0.0)}`)
- **Fix:** A value right of or below a label is not taken when it comes after another label on its own line (`_after_another_label`: a label shape, a colon, or another field's label before it). A due-date label followed by "Upon Receipt" gives the payment terms "Due on receipt" when no terms label was read.
- **Test:** `tests/test_sweep3_capture.py::test_due_upon_receipt_does_not_take_the_delivery_date_below_it`
- **Lesson:** Don't take a value from a neighbouring line without checking it isn't already another label's value.

#### B-185 · An invoice number printed in groups of three is cut at the first space · medium
- **Where:** `ap_coder/capture/reader.py:328` (`_id_from`)
- **Symptom:** "No de facture 123 456" is read as invoice number 123, verified (the page reader's transcription, read by the same rules, agrees).
- **Cause:** An identifier ended at the first space unless a short prefix of letters came before it ("INV 4471"); digit groups set apart the French way were never joined.
- **Reproduce:** the Acme invoice with "No de facture" and "123 456" in the next column, `analyze(..., page_text=[...])`.
- **Reproduced:** yes (`{'invoice_number': ('123', 'verified', 0.998)}`)
- **Fix:** A number of one to three digits followed only by groups of three digits, each one space apart, with nothing else after it on the line, is read whole ("123 456"; compared without spaces).
- **Test:** `tests/test_sweep3_capture.py::test_an_invoice_number_in_french_digit_groups_is_read_whole`
- **Lesson:** Don't end a number at a space before checking whether the space is part of how the number is printed.

#### B-186 · Text drawn twice for bold is read twice ("Acme Acme Supply Supply") · low
- **Where:** `ap_coder/capture/layout.py:192` (`_text_layer_page`)
- **Symptom:** On a PDF whose writer makes bold by drawing the text twice a hair apart, every bold word is read twice: the supplier is "Acme Acme Supply Supply Ltd. Ltd." and a bold "Invoice Date:" label is not recognised, so the invoice date is missing.
- **Cause:** The text layer's words went into the layout as they were stored; only the text-layer check (`_printed_once` in `capture/__init__.py`) kept a word drawn over itself once, so the readers saw both copies.
- **Reproduce:** the Acme invoice with "Acme Supply Ltd." and "Invoice Date:" each inserted again 0.3 pt to the right, `analyze(..., ocr=False)`.
- **Reproduced:** yes (`{'vendor_name': ('Acme Acme Supply Supply Ltd. Ltd.', 'likely'), 'invoice_date': (None, 'missing')}`)
- **Fix:** The layout drops a text-layer word with the same text as one of the last few words that covers mostly the same spot; the text-layer check now reads the layout's lines as they are (its `_printed_once` moved into the layout).
- **Test:** `tests/test_sweep3_capture.py::test_text_drawn_twice_for_bold_is_read_once`
- **Lesson:** Don't clean the input in one consumer only: clean it where it is built, so every reader sees the same page.
