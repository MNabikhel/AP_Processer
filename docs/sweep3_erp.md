# Sweep 3: erp

#### B-150 · A statement's payment line with the invoice's number is taken for the invoice · medium
- **Where:** `ap_coder/erp/statements.py:177` (`reconcile`)
- **Symptom:** a vendor statement whose payment or credit line carries the invoice's number (a Credit column, or a Type of PMT / CHQ) shows "Not received" for it, so AP chases a bill it has; listed newest first, the payment takes the invoice's match ("Amount differs") and the invoice itself shows "Not received".
- **Cause:** only a payment word in a free-text column was checked against the sign; a Debit/Credit line or a Type code the word list did not know (PMT, CHQ) was matched like an invoice, against any document with that number whatever its sign.
- **Reproduce:** statement rows `PMT 1001 -500.00`, `INV 1001 500.00` (or `Credit 500.00` before `Debit 500.00`) against invoice 1001 for 500.00.
- **Reproduced:** yes (`amount_differs 1001 -500.0 1`, `not_received 1001 500.0 None`; with a Type column `not_received 1001 -500.0` and `not_received CHQ 88123`)
- **Fix:** a line whose sign is opposite to every AP Coder document with its number is the payment / credit applied, whatever its words; a line never takes a document of the opposite sign (a credit line still matches a credit note AP holds under that number); pmt, pymt and chq are payment words.
- **Test:** `tests/test_statements.py::test_a_payment_line_carrying_the_invoice_number_never_takes_the_invoice`
- **Lesson:** Don't match a signed line to a document of the opposite sign because the number agrees.

#### B-151 · An invoice approved without a stored distribution is missing from the CSV exports · low-medium
- **Where:** `ap_coder/erp/exports.py:104` (`line_rows`)
- **Symptom:** an approved invoice from an older version (no `gl_distribution` stored) is marked as in the batch, but the CSV, Custom CSV and the xlsx "GL lines" sheet have no row for it; the JD Edwards files of the same batch have its lines.
- **Cause:** `line_rows` read only the stored distribution; `jde._distribution` rebuilt it, but only for jde.py.
- **Reproduce:** `exports.build("csv", [{"id": 5, "final_output": <ground truth without gl_distribution>}], {}, 3, {})`.
- **Reproduced:** yes (`csv rows: 0`, `custom rows: 0`, `jde F0911Z1 rows: 5`)
- **Fix:** `exports.distribution()` returns the stored lines, else builds them again from the coding with the store's tax treatments (passed by web/pages/exports.py as `tax_treatments`, so a recoverable tax line keeps its GL account); jde.py uses the same helper.
- **Test:** `tests/test_exports_bulk.py::test_an_invoice_approved_without_a_stored_distribution_still_has_its_lines_in_the_export`
- **Lesson:** Don't give one reader of a stored record a fallback the others don't share: put it in one helper.

#### B-152 · Quick search hides another vendor's ERP bill with an exported invoice's number and amount · low-medium
- **Where:** `ap_coder/erp/search.py:113` (`find`)
- **Symptom:** an ERP-register row is missing from the quick search whenever any exported invoice, of any vendor, has the same number and amount (or the same amount with the other sign); searching the other vendor's name finds nothing.
- **Cause:** the "its own ERP entry" key was (invoice number, absolute amount): no vendor, no sign.
- **Reproduce:** export Chinook's invoice 1001 for 500.00, import an ERP register row `Other Supplies Ltd, 1001, 500.00`, search "1001" and "other supplies".
- **Reproduced:** yes (`[('AP Coder', 'Chinook Courier & Office Services Ltd.')]` and `[]`)
- **Fix:** the key keeps the amount's sign and the vendor: the same vendor key, or one the start of the other (the ERP's short name, "CHINOOK COURIER", still counts as the same vendor).
- **Test:** `tests/test_search.py::test_another_vendors_erp_bill_with_the_same_number_and_amount_is_still_found`
- **Lesson:** Don't de-duplicate two records on a key that leaves out who they belong to.

#### B-153 · The ERP's document number is guessed as the supplier's invoice number · low-medium
- **Where:** `ap_coder/erp/registers.py:18` (`COLUMN_ALIASES`)
- **Symptom:** an ERP register with both "Document Number" (or "Doc No") and "Supplier Invoice" / "Vendor Invoice" columns is imported with the ERP's own document number as the invoice number, so the duplicate check against the ERP never matches.
- **Cause:** the aliases were tried in order and "documentnumber" / "docno" came before "vendorinvoice" / "supplierinvoice".
- **Reproduce:** `registers.map_columns(["Supplier Name", "Document Number", "Supplier Invoice", "Date", "Amount"])`.
- **Reproduced:** yes (`invoice_number: 'Document Number'`)
- **Fix:** the supplier / vendor invoice aliases (and their "number" / "no" forms) come first; the document number only when there is nothing better.
- **Test:** `tests/test_registers.py::test_the_suppliers_invoice_number_is_chosen_over_the_erps_document_number`
- **Lesson:** Don't order column guesses so that a generic name wins over the specific one.

#### B-154 · A bill entered twice in the ERP collapses into one register row · low-medium
- **Where:** `ap_coder/core/store.py:956` (`import_erp_register`), `ap_coder/erp/dupaudit.py:116` (`bills`)
- **Symptom:** the same bill keyed twice in the ERP (same vendor, number and amount) is one row in AP Coder's ERP register, so the duplicate payment audit can never flag it.
- **Cause:** `erp_invoices` has the primary key (vendor_key, number_key, total) and the import upserts on it: the second entry only overwrote the first one's date.
- **Reproduce:** import `[Acme Ltd A-77 812.50 2026-01-05, Acme Ltd A-77 812.50 2026-02-05]`, run `dupaudit.find`.
- **Reproduced:** yes (`stored 1`, `pairs []`)
- **Fix:** `erp_invoices.copies` (a column added whatever the schema version) counts how many times the file lists the bill, kept at the highest count when an overlapping register is imported again (never added up); the audit pairs such a row with itself as EXACT ("entered 2 times"), and the register import's message names how many bills the ERP holds more than once (`Store.erp_register_repeats()`).
- **Test:** `tests/test_dupaudit.py::test_the_same_bill_entered_twice_in_the_erp_is_a_duplicate`
- **Lesson:** Don't make the primary key of imported records the very thing a duplicate check needs to see twice.

#### B-155 · VLCRR is 1 for a CAD invoice when JD Edwards keeps its books in another currency · low-medium
- **Where:** `ap_coder/erp/jde.py:577` (`voucher_rows`)
- **Symptom:** with the JDE domestic currency set to USD, a CAD invoice's F0411Z1 row has VLCRR `1` (and a EUR invoice its CAD rate), so E1 converts at a wrong rate instead of its own table.
- **Cause:** VLCRR was the invoice currency's rate from Settings → Review, which are CAD per unit, whatever the domestic currency.
- **Reproduce:** `jde.export_batch([<CAD invoice>], JdeSettings(domestic_currency="USD", currencies=["CAD","USD"]), AN8, fx_rates={"CAD": 1.0, "USD": 1.37})`.
- **Reproduced:** yes (`CRRM F CRCD CAD CRR 1`)
- **Fix:** `jde._rate()`: domestic units per unit of the invoice's currency, the rate across CAD (1 / 1.37 = 0.729927; EUR 1.50 / 1.37), and blank (E1's exchange-rate table) without a rate for the domestic currency; docs/JDE_E1.md says so.
- **Test:** `tests/test_jde.py::test_the_exchange_rate_is_in_the_domestic_currency_when_it_is_not_cad`
- **Lesson:** Don't send a rate without saying, and converting to, the currency it is quoted in.

#### B-156 · Custom CSV whole-yen lines don't add up to the invoice total · low
- **Where:** `ap_coder/erp/export_layout.py:97` (`_value`)
- **Symptom:** a JPY invoice whose distribution was spread in cents (10.66 + 10.67 + 10.67 + 1000 + 52) is written 11, 11, 11, 1000, 52: 1,085 against an InvoiceTotal of 1,084; and 2.5 yen is written 2.
- **Cause:** each money cell was formatted on its own with `f"{x:.0f}"` (round half to even), with no balancing per invoice as jde.py's vouchers have.
- **Reproduce:** `exports.build("custom", [_yen_invoice(old_distribution=True)], {}, 1, {})`.
- **Reproduced:** yes (`['11', '11', '11', '1000', '52'] sum 1085 total 1084`; `f"{2.5:.0f}"` is `2`)
- **Fix:** money rounds half away from zero on the decimal value; in a currency without cents, when an invoice's lines balance in cents, the largest line takes the difference to the rounded total (11, 11, 11, 999, 52).
- **Test:** `tests/test_export_layout.py::test_whole_yen_lines_add_up_to_the_invoice_total`
- **Lesson:** Don't round posting lines one by one in a coarser unit without giving the remainder to one of them.

#### B-157 · How the line numbers VLEDLN / VNEDLN load is never asked of the JDE team · low
- **Where:** `ap_coder/erp/jde.py:106` (`UNCONFIRMED`), `ap_coder/erp/jde_worksheet.py:44` (`QUESTIONS`)
- **Symptom:** the Z-files write VLEDLN / VNEDLN as 1, 2, 3; E1 shows EDLN with display decimals, so a direct table load may read line 1 as 0.001, and nothing in the README, the worksheet or docs/JDE_E1.md asks the JDE team about it.
- **Cause:** the unconfirmed list and the open questions covered VNEDLN numbering (sequential or matching) but not how the number itself is stored.
- **Reproduce:** read the README's "To confirm with your JDE team" line and the worksheet's Amounts questions.
- **Reproduced:** yes (no mention of EDLN's decimals)
- **Fix:** an UNCONFIRMED entry (in the README), an Amounts question in the worksheet (free text), and the open-questions bullet in docs/JDE_E1.md (still 8 bullets, so test_jde_readiness keeps its count).
- **Test:** `tests/test_jde_readiness.py::test_how_the_line_numbers_load_is_asked_of_the_jde_team`
- **Lesson:** Don't leave a load assumption out of the questions list because the value looks obvious.

### Not fixed
- **N-XX** · erp · The ERP CSV export (`ap_coder/erp/exports.py` `build_csv`) and the Custom CSV (`export_layout.build`) are written with a UTF-8 byte-order mark ("Excel shows accents correctly"), while the JDE Z-files are deliberately without one (docs/JDE_E1.md). D-28 / B-92 decided only the quoting, not the BOM. A loader that reads plain UTF-8 (SQL*Loader, a bulk insert) sees the first header as "﻿Batch"; but some Windows import tools need the BOM to read UTF-8 at all. Which one the pilot's ERP wants is a choice (a setting, or a D-NN), not a bug fix: left as it is.
- **N-XX** · erp · `dupaudit._same_bill` treats an exported invoice and an ERP-register row with the same number as the same bill when the vendor OR the amount agrees; the audit only compares rows with equal amounts, so another vendor's bill with the same number and amount is never flagged OTHER_VENDOR once ours is exported. That is the documented choice (the ERP's own name for the vendor, `test_an_exported_invoice_is_not_its_own_duplicate_in_the_erp`); quick search (B-152) now asks for the same vendor or the ERP's short name. Aligning the audit is a decision about false alarms, left.
- **N-XX** · erp · Besides the exports (B-151), `spend.py`, `accruals.py` and `stamp.py` also read only a stored `gl_distribution`, so an invoice approved by an older version has no lines in the spend report, the accrual detail or the stamp's posting table. They could use `exports.distribution()`; outside this finding's scope, left.
