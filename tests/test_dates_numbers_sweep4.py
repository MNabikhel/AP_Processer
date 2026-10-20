"""Reading the invoice date and number as real invoices print them (sweep 4): titles that carry the number and the date
("Rechnung Nr. 471102 vom 05.03.2018", "Facture n° 562044 du 2 juillet 2015"), receipts, other languages' months,
a credit note's own number beside the invoice it credits, and day/month order. Every fixture is generated here."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from ap_coder.capture import analyze
from ap_coder.capture.normalize import parse_date
from ap_coder.capture.reader import read_fields
from ap_coder.capture.types import VERIFIED, Box, DocLayout, PageLayout, Word

TODAY = dt.date(2026, 10, 20)


def _pdf(tmp_path: Path, items: list[tuple], name: str = "inv.pdf") -> Path:
    """A one-page PDF: (x, y, text) or (x, y, text, font size) in points from the top left."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    for x, y, text, *size in items:
        page.insert_text((x, y), text, fontsize=size[0] if size else 10)
    path = tmp_path / name
    doc.save(path)
    return path


def _read(path: Path):
    return analyze(path, ocr=False, today=TODAY, tables=False)


def _scan(rows: list[tuple[float, float, str]]) -> DocLayout:
    """A scanned page's layout: (x, y, text) per OCR line, in fractions of the page."""
    from ap_coder.capture.layout import group_lines

    words = []
    for x, y, text in rows:
        at = x
        for w in text.split():
            width = 0.0085 * len(w)
            words.append(Word(w, Box(1, at, y, at + width, y + 0.016), 0.95, "ocr"))
            at += width + 0.006
    return DocLayout([PageLayout(1, 1700, 2200, words, group_lines(words, 1))], "ocr")


def _top(layout: DocLayout, field: str):
    found = read_fields(layout).get(field) or []
    return found[0] if found else None


_TOTALS = [(380, 600, "Nettobetrag"), (500, 600, "1.000,00"), (380, 615, "Umsatzsteuer 19%"), (500, 615, "190,00"),
           (380, 630, "Gesamtbetrag"), (500, 630, "1.190,00")]  # fmt: skip


def test_a_german_title_with_the_number_and_the_date_gives_both(tmp_path):
    # "Handelsrechnung (380) Nr. 471102 vom 05.03.2018": a German invoice's title carries its number and its date (the
    # "Rechnungsnummer" and "Rechnungsdatum" labels are not printed). Neither was read.
    path = _pdf(
        tmp_path,
        [
            (60, 50, "Muster Lieferant GmbH", 14),
            (60, 90, "Handelsrechnung (380) Nr. 471102 vom 05.03.2018", 13),
            (60, 120, "Liefer- und Leistungsdatum:"),
            (250, 120, "04.03.2018"),
            *_TOTALS,
        ],
    )
    f = _read(path).fields
    assert f["invoice_number"].value == "471102"
    assert f["invoice_date"].value == "2018-03-05"
    assert f["invoice_date"].status != VERIFIED  # read from a title, not a label: measured apart, a person sees it


def test_the_date_after_the_number_may_be_the_next_pieces_of_the_row(tmp_path):
    # "Gutschrift (389) | Nr. 471102 | vom | 05.03.2018": the row printed in four pieces.
    path = _pdf(
        tmp_path,
        [
            (60, 50, "Muster Lieferant GmbH", 14),
            (60, 90, "Gutschrift (389)", 13),
            (330, 90, "Nr. 471102", 13),
            (420, 90, "vom", 13),
            (455, 90, "05.03.2018", 13),
            *_TOTALS,
        ],
    )
    f = _read(path).fields
    assert f["invoice_number"].value == "471102"
    assert f["invoice_date"].value == "2018-03-05"


def test_a_french_invoice_number_dated_du_gives_the_invoice_date_but_not_a_period(tmp_path):
    # "Facture n°562044387 du 02 Juillet 2015": the date the invoice was issued, after its number.
    layout = _scan([(0.07, 0.10, "Fibre Services SAS"), (0.07, 0.38, "Facture n°562044387 du 02 Juillet 2015")])
    assert _top(layout, "invoice_date").value == "2015-07-02"
    assert _top(layout, "invoice_number").value == "562044387"
    # "Facture no 4471 du 01/03/2026 au 31/03/2026": the period billed, not the invoice's date.
    period = _scan([(0.07, 0.10, "Fibre Services SAS"), (0.07, 0.38, "Facture no 4471 du 01/03/2026 au 31/03/2026")])
    assert _top(period, "invoice_date") is None


def test_an_english_number_issued_at_a_date_gives_the_date():
    layout = _scan([(0.1, 0.1, "Bei Spiel GmbH"), (0.1, 0.42, "Invoice # RE-20201121/508 issued at 2020-11-21")])
    assert _top(layout, "invoice_number").value == "RE-20201121/508"
    assert _top(layout, "invoice_date").value == "2020-11-21"


def test_a_grid_of_beleg_labels_with_the_colon_beside_the_value(tmp_path):
    # "Beleg-Nr | : RK21012345", "Beleg-Datum | : 16.09.2013": the label's colon printed in the value's column was taken
    # for another label before the value, and the German "document number / date" labels were not known.
    path = _pdf(
        tmp_path,
        [
            (40, 50, "MUSTERLIEFERANT GMBH", 12),
            (370, 50, "RECHNUNGS-KORREKTUR"),
            (370, 80, "Beleg-Nr"),
            (470, 80, ": RK21012345"),
            (370, 92, "Beleg-Datum"),
            (470, 92, ": 16.09.2013"),
            (370, 104, "Bestell-Nr"),
            (470, 104, ": B123456789"),
            *_TOTALS,
        ],
    )
    f = _read(path).fields
    assert f["invoice_number"].value == "RK21012345"
    assert f["invoice_date"].value == "2013-09-16"


def test_months_of_other_languages_are_read():
    assert parse_date("24. Januar 2014") == "2014-01-24"
    assert parse_date("11. März 2014") == "2014-03-11"
    assert parse_date("29 maart 2014") == "2014-03-29"
    assert parse_date("10 de febrero de 2022") == "2022-02-10"
    assert parse_date("12 de março de 2020") == "2020-03-12"
    assert parse_date("3 dicembre 2021") == "2021-12-03"
    # Short forms that are English words are not months: "1 set 2500" is a quantity and a price.
    assert parse_date("1 set 2500") is None


def test_a_page_number_run_into_the_label_still_reads_the_date():
    # OCR ran "Page 1/2" into the next words: "Page1/2 Invoice date(Y-M-D) 2025-06-21".
    layout = _scan([(0.06, 0.07, "POSTES CANADA"), (0.61, 0.07, "Page1/2 Invoice date(Y-M-D) 2025-06-21")])
    assert _top(layout, "invoice_date").value == "2025-06-21"


def test_a_receipts_number_and_date_are_read():
    # "ReceiptNumber334567", "ReceiptDate01/18/2024" (OCR glued the words), and "Receipt # No.: 9876".
    layout = _scan(
        [(0.27, 0.19, "Contoso"), (0.27, 0.46, "ReceiptNumber334567"), (0.27, 0.49, "ReceiptDate01/18/2024")]
    )
    assert _top(layout, "invoice_number").value == "334567"
    assert _top(layout, "invoice_date").value == "2024-01-18"
    other = _scan([(0.6, 0.03, "Project Statement"), (0.77, 0.11, "Receipt # No.: 9876")])
    assert _top(other, "invoice_number").value == "9876"


def test_a_credit_notes_own_number_not_the_invoice_it_credits(tmp_path):
    # A French credit note: "Avoir AV-2017-0005" is its number, "Facture liée FA-2017-0010" the invoice it credits.
    # The credit's number was not read and the invoice's was taken for it (likely).
    path = _pdf(
        tmp_path,
        [(40, 40, "Moulin Rouge SARL", 12), (40, 90, "Avoir AV-2017-0005", 16), (40, 210, "Date de facture"),
         (190, 210, "16/11/2017"), (40, 226, "Facture liée"), (190, 226, "FA-2017-0010"), (380, 600, "Total HT"),
         (500, 600, "-100,00"), (380, 615, "TVA 20%"), (500, 615, "-20,00"), (380, 630, "Total TTC"),
         (500, 630, "-120,00")],
    )  # fmt: skip
    assert _read(path).fields["invoice_number"].value == "AV-2017-0005"
    # Its scan, OCR glued the title to the number: "AvoirAV-2017-0005".
    scan = _scan([(0.07, 0.11, "AvoirAV-2017-0005"), (0.07, 0.36, "Facture liee"), (0.27, 0.36, "FA-2017-0010")])
    assert _top(scan, "invoice_number").value == "AV-2017-0005"


def test_a_us_customers_invoice_shows_an_unsettled_date_month_first_for_a_person_to_check(tmp_path):
    # "Date: 9/10/2020" on a statement to a customer in Walnut, WY, with no Canadian address or GST number and no other
    # date to settle it: it was shown day first (October 9). US invoices print the month first; shown so, still checked.
    path = _pdf(
        tmp_path,
        [(40, 40, "Contoso Training", 14), (450, 70, "Date: 9/10/2020"), (450, 82, "Statement # 9876"),
         (100, 110, "Sold To"), (150, 110, "Wingtip Toys"), (150, 122, "554 Magnolia Way"),
         (150, 134, "Walnut, WY, 98432"), (380, 600, "Subtotal"), (500, 600, "$1,000.00"), (380, 615, "Sales Tax"),
         (500, 615, "$30.00"), (380, 630, "Total"), (500, 630, "$1,030.00")],
    )  # fmt: skip
    f = _read(path).fields
    assert f["invoice_date"].value == "2020-09-10"
    assert f["invoice_date"].status == "check"


def test_a_date_printed_with_points_is_not_turned_month_first_by_a_german_postcode(tmp_path):
    # "DE 80333 München" reads as a Delaware ZIP code; a date printed 05.03.2018 is a European one: day first.
    path = _pdf(
        tmp_path,
        [(60, 50, "Lieferant GmbH", 14), (60, 70, "DE 80333 München"), (60, 90, "Rechnungsdatum: 05.03.2018"),
         *_TOTALS],
    )  # fmt: skip
    assert _read(path).fields["invoice_date"].value == "2018-03-05"


def test_a_year_run_into_the_time_is_not_read_as_the_year():
    # A receipt's "02/15/10 11:10AM" as OCR read it: "02/15/1011:10AM". It was read as 1011-02-15 (check).
    assert parse_date("02/15/1011:10AM") == "2010-02-15"
    assert parse_date("Date 15/02/1011") is None  # no invoice is dated 1011


def test_a_date_run_into_its_time_is_read():
    # OCR drops the space between a receipt's date and time.
    assert parse_date("27Mar201818:46") == "2018-03-27"
    assert parse_date("2018-02-1514:33:37") == "2018-02-15"
    assert parse_date("12/29/20149:54:44PM") == "2014-12-29"
    assert parse_date("2/25/125:21PM0204") == "2012-02-25"
    assert parse_date("Jun14/201718:0:41#1056651") == "2017-06-14"
    assert parse_date("Ju1/15/2017_12:25") == "2017-07-15"
    assert parse_date("1/2/2026 10:30", prefer_day_first=True) == "2026-02-01"  # a date and time apart: as before


def test_a_receipts_unlabelled_date_and_time_is_its_date_but_a_labelled_date_comes_first():
    # "6/10/2019 13:59" under the store's address, no "Date" printed: when the receipt was issued.
    receipt = _scan([(0.37, 0.09, "Contoso"), (0.18, 0.26, "Redmond, WA 98052"), (0.18, 0.40, "6/10/2019 13:59"),
                     (0.18, 0.44, "Sales Associate: Paul"), (0.27, 0.74, "Sub-Total"),
                     (0.55, 0.74, "$1098.99")])  # fmt: skip
    got = _top(receipt, "invoice_date")
    assert got.value == "2019-06-10" and got.method.startswith("date-time")
    # An invoice with its date labelled: the stamp of a printout or a call log does not outrank it.
    invoice = _scan([(0.1, 0.05, "Printed 2026-10-20 14:33"), (0.1, 0.10, "Invoice Date: 2026-10-01"),
                     (0.1, 0.5, "2026-09-12 10:32 Call to 555-0101")])  # fmt: skip
    assert _top(invoice, "invoice_date").value == "2026-10-01"
    # A stamp after another date's label is that date's.
    due = _scan([(0.1, 0.10, "Due Date 2026-11-01 17:00")])
    assert _top(due, "invoice_date") is None


def test_receipt_and_cash_invoice_labels_and_ocr_zero_in_a_label():
    layout = _scan([(0.4, 0.21, "Cash Inv No.:7803F712"), (0.29, 0.14, "INV0ICE 86907-1")])
    found = [r.value for r in read_fields(layout)["invoice_number"]]
    assert "7803F712" in found and "86907-1" in found
    assert _top(_scan([(0.66, 0.13, "FECHA:31-10-21")]), "invoice_date").value == "2021-10-31"
    assert _top(_scan([(0.25, 0.15, "Data da Emissão: 12/03/2020")]), "invoice_date").value == "2020-03-12"


def test_an_invoice_number_run_into_the_next_word_is_cut_at_the_word():
    # "INV No.:1128461Cashier:Thandar": OCR lost the space after the number; it was read "1128461Cashier".
    layout = _scan([(0.26, 0.16, "RESTORAN WAN SHENG"), (0.06, 0.39, "INV No.:1128461Cashier:Thandar")])
    assert _top(layout, "invoice_number").value == "1128461"


def test_a_receipts_item_number_is_not_its_number():
    # A receipt's first item "15.Dumplings au porc" under "#1" (the guest): read as the invoice number "15.Dumplings".
    layout = _scan([(0.27, 0.05, "Restaurant Ethan"), (0.21, 0.24, "#1"), (0.21, 0.258, "2015-08-23 20:45:57 Waiter"),
                    (0.21, 0.276, "15.Dumplings au porc et nappa"), (0.65, 0.276, "$7.95")])  # fmt: skip
    assert _top(layout, "invoice_number") is None
    # Under a label of its own as well.
    other = _scan([(0.21, 0.24, "Bill No."), (0.21, 0.258, "15.Dumplings au porc et nappa")])
    assert _top(other, "invoice_number") is None


def test_a_label_whose_value_is_printed_after_it_does_not_take_the_next_lines():
    # "GROUND FLOOR, NO.4 &6," (an address): "No." with its own value "4", no number; the postal code two lines down
    # ("47500 SUBANG JAYA") was read as the invoice number.
    layout = _scan([(0.04, 0.30, "GROUND FLOOR, NO.4 &6,"), (0.04, 0.32, "JALAN SS 15/4B,"),
                    (0.04, 0.34, "47500 SUBANG JAYA, SELANGOR")])  # fmt: skip
    assert _top(layout, "invoice_number") is None


def test_a_title_glued_to_a_number_with_slashes_beats_a_customer_reference():
    # The second OCR read "Invoice INV/2023/03/0008" as "InvoiceINV/2023/03/0008", the first as "Invoice.INV/...":
    # neither reading was strong, and "Reference: | CUSTREF123" (the customer's reference) was taken for the number.
    layout = _scan([(0.05, 0.25, "InvoiceINV/2023/03/0008"), (0.67, 0.29, "Reference:"), (0.67, 0.31, "CUSTREF123")])
    assert _top(layout, "invoice_number").value == "INV/2023/03/0008"


def test_a_date_after_a_failed_one_is_read_and_a_time_may_be_the_next_piece_of_the_row():
    # "31/31/2020 05/06/2020": the first is no date; the next word is read.
    assert parse_date("31/31/2020 05/06/2020", prefer_day_first=True) == "2020-06-05"
    # But no date is pulled out of a reference's run of numbers ("45/13/12/2026" is no 2026-12-13).
    assert parse_date("Ref 45/13/12/2026") is None
    assert parse_date("ORD#94-REG#19-21/05/2018") is None
    # A receipt's "12/22/17 | 14:40:29": the time printed apart from the date, on its row.
    layout = _scan([(0.37, 0.03, "Walmart Supercentre"), (0.30, 0.72, "12/22/17"), (0.52, 0.72, "14:40:29")])
    assert _top(layout, "invoice_date").value == "2017-12-22"


def test_a_header_row_printed_across_the_page_reads_its_value_at_the_right_margin():
    # "N°Facture ........ INV-81250" and "Date ........ 11/4/2025": the label at the left margin, the value alone at
    # the right one, further than a value is looked for beside its label.
    layout = _scan(
        [(0.04, 0.30, "N°Facture"), (0.77, 0.30, "INV-81250"), (0.04, 0.35, "Date"), (0.79, 0.35, "11/4/2025")]
    )
    number = _top(layout, "invoice_number")
    assert number.value == "INV-81250" and number.method == "label-far-right"
    # Not a date: alone across the page it may as well be the due date.
    assert _top(layout, "invoice_date") is None
    # Not when the row holds more than the value, nor a value near the label (looked at beside it already).
    busy = _scan([(0.04, 0.30, "N°Facture"), (0.55, 0.30, "Ship Via"), (0.79, 0.30, "INV-81250")])
    assert _top(busy, "invoice_number") is None


def test_a_date_alone_beside_the_title_is_the_invoice_date_but_a_labelled_one_comes_first():
    layout = _scan([(0.12, 0.34, "Beitragsrechnung"), (0.71, 0.34, "24. Januar 2014")])
    got = _top(layout, "invoice_date")
    assert got.value == "2014-01-24" and got.method == "title-dated"
    labelled = _scan([(0.1, 0.1, "INVOICE"), (0.7, 0.1, "March 3, 2026"), (0.1, 0.2, "Invoice Date: 2026-03-04")])
    assert _top(labelled, "invoice_date").value == "2026-03-04"


def test_a_spanish_numero_label_reads_the_number_but_not_a_phone_number():
    assert _top(_scan([(0.66, 0.13, "NUMERO: ABC12345")]), "invoice_number").value == "ABC12345"
    assert _top(_scan([(0.1, 0.1, "Numéro : 514 555 1234")]), "invoice_number") is None
    assert _top(_scan([(0.1, 0.1, "Numéro: (514) 555-1234")]), "invoice_number") is None


def test_a_fax_or_printer_stamp_is_not_the_invoice_date():
    # A faxed invoice: "SEP-13-2025 11:26 FROM:..." along the top edge is when it was faxed; its date is labelled
    # "Date: le 9 septembre 2025" (OCR glued "le9").
    layout = _scan([(0.04, 0.0, "SEP-13-2025 11:26 FROM:LOCATION PELLETIER INC 2392789463"), (0.55, 0.27, "Date:"),
                    (0.76, 0.27, "le9 septembre .2025")])  # fmt: skip
    assert _top(layout, "invoice_date").value == "2025-09-09"
    printed = _scan([(0.02, 0.01, "2026/05/07 16:51"), (0.3, 0.3, "Northline Energy")])
    assert _top(printed, "invoice_date") is None


def test_a_date_read_without_a_label_is_checked_however_many_readers_see_it():
    from ap_coder.capture.confidence import fuse

    layout = _scan([(0.18, 0.40, "6/22/2019 13:59"), (0.27, 0.74, "Sub-Total"), (0.55, 0.74, "$1098.99")])
    by_source = {"rules": read_fields(layout), "ocr2": read_fields(layout)}
    got = fuse(by_source, [], layout_source="ocr", fields=("invoice_date",))[0]["invoice_date"]
    assert got.value == "2019-06-22" and got.status == "check"


# Ordinary Canadian invoices (an independent review of this work): each was read wrong, most of them *likely*.
_MAPLE = [(40, 40, "Maple Fasteners Ltd", 14), (40, 54, "Toronto ON M5V 2T6")]
_HST = [(380, 600, "Subtotal"), (500, 600, "$1,000.00"), (380, 615, "HST 13%"), (500, 615, "$130.00"),
        (380, 630, "Total"), (500, 630, "$1,130.00")]  # fmt: skip


def _maple(tmp_path, items):
    return _read(_pdf(tmp_path, [*_MAPLE, *items, *_HST])).fields


def test_a_receipt_date_is_not_the_invoice_date_when_the_invoice_prints_its_date(tmp_path):
    # On a Canadian invoice "Receipt Date" is the day the goods came in: it gave 2026-10-05, likely.
    f = _maple(tmp_path, [(400, 60, "Invoice # 20417"), (400, 72, "Date: 2026-10-01"),
                          (400, 84, "Receipt Date: 2026-10-05")])  # fmt: skip
    assert f["invoice_date"].value == "2026-10-01"


def test_a_receipt_number_is_not_the_invoice_number_beside_the_invoices_own(tmp_path):
    # "INVOICE / No. 20417" and lower, a payment's "Receipt No: 556677": it gave 556677, likely.
    f = _maple(tmp_path, [(400, 40, "INVOICE", 16), (400, 60, "No. 20417"), (400, 72, "Date: 2026-10-01"),
                          (40, 300, "Receipt No: 556677")])  # fmt: skip
    assert f["invoice_number"].value == "20417"


def test_a_customers_numero_is_not_the_invoice_number(tmp_path):
    # "FACTURE" with "Client / Numéro : C-4471": the customer's number was read as the invoice's, likely.
    path = _pdf(tmp_path, [(40, 40, "Les Entreprises Gagnon", 14), (40, 54, "Montréal QC H3B 4W8"),
                           (300, 90, "FACTURE", 14), (40, 150, "Client"), (40, 162, "Numéro : C-4471"),
                           (300, 110, "Date : 2026-10-01"), *_HST])  # fmt: skip
    f = _read(path).fields
    assert f["invoice_number"].value != "C-4471"


def test_a_weak_label_is_never_likely_on_a_real_labels_record(tmp_path):
    f = _maple(tmp_path, [(300, 90, "FACTURE", 14), (300, 110, "Numéro : AB-20417"), (300, 122, "Date : 2026-10-01")])
    assert f["invoice_number"].value == "AB-20417" and f["invoice_number"].status == "check"


def test_a_bare_title_does_not_read_a_value_across_the_page(tmp_path):
    # "INVOICE" at the left margin, a GST number or a customer number alone at the right: they were taken over
    # "No. 20417".
    for far in ("R123456789", "C-10045"):
        f = _maple(tmp_path, [(30, 90, "INVOICE", 16), (450, 90, far), (30, 112, "No. 20417"),
                              (30, 124, "Date: 2026-10-01")])  # fmt: skip
        assert f["invoice_number"].value == "20417", far


def test_an_unlabelled_date_never_outranks_a_labelled_one(tmp_path):
    # A service call's "Time in: 2026-03-02 08:15" and a "Payment received 2026-04-15 14:22" stamp came before the
    # labelled "Date: 03/04/2026"; a credit note's body "Invoice # 20417 dated 2026-08-15" before its "Date:".
    for line in ("Time in: 2026-03-02 08:15", "Payment received 2026-04-15 14:22 VISA"):
        f = _maple(tmp_path, [(400, 60, "Invoice # 20417"), (400, 72, "Date: 03/04/2026"), (40, 200, line)])
        assert f["invoice_date"].value == "2026-04-03", line
    f = _maple(tmp_path, [(300, 70, "CREDIT NOTE", 14), (300, 90, "Credit Note # CN-88"),
                          (300, 102, "Date: 2026-10-01"), (40, 200, "Invoice # 20417 dated 2026-08-15")])  # fmt: skip
    assert f["invoice_date"].value == "2026-10-01"
    # With no labelled date, a stamp of what else happened ("Completed ...") is no invoice date either.
    f = _maple(tmp_path, [(400, 60, "Invoice # 20417"), (40, 200, "Completed 2026-03-02 16:15")])
    assert f["invoice_date"].value is None


def test_a_label_starting_with_invoice_is_not_the_title(tmp_path):
    # "Invoice Due Date | Nov 1, 2026" read as a date beside the title: the invoice date.
    f = _maple(tmp_path, [(300, 90, "Invoice Due Date"), (480, 90, "Nov 1, 2026"), (300, 102, "Invoice # 20417")])
    assert f["invoice_date"].value != "2026-11-01"
    layout = _scan([(0.1, 0.1, "Invoice Period Ending"), (0.7, 0.1, "Sep 30, 2026")])
    assert _top(layout, "invoice_date") is None


def test_a_canadian_page_with_a_us_address_keeps_its_date_order(tmp_path):
    # A Canadian supplier whose address is in the footer, shipping to Buffalo, NY: "Date: 03/04/2026" was turned month
    # first. A postal code or a Canadian tax anywhere on the page is something Canadian.
    f = _read(_pdf(tmp_path, [(40, 40, "Maple Fasteners Ltd", 14), (400, 60, "Invoice # 20417"),
                              (400, 72, "Date: 03/04/2026"), (40, 120, "Ship To"), (40, 132, "Acme Corp"),
                              (40, 144, "Buffalo, NY 14201"), *_HST,
                              (40, 760, "55 King St W, Toronto ON M5V 2T6")])).fields  # fmt: skip
    assert f["invoice_date"].value == "2026-04-03"
    # "CA 90210" alone may be Canada's abbreviation: no US address.
    layout = _scan([(0.1, 0.1, "Date: 03/04/2026"), (0.1, 0.2, "Ship to: Vancouver CA 90210")])
    assert _top(layout, "invoice_date").value == "2026-04-03"


def test_a_name_like_a_month_is_not_a_month():
    assert parse_date("Marco 12, 2026") is None
    assert parse_date("12 de março de 2020") == "2020-03-12"
