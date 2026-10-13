"""Wrong values a scan benchmark showed as right (seed 9102), each fixed and kept fixed: a partnership's name taken for
the customer's, "ltée" read right made "Itée", and a header grid's PO taken from the label beside it."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pymupdf

from ap_coder.capture import analyze
from ap_coder.capture.reader import _ocr_name_fix

TODAY = dt.date(2026, 10, 13)


def _pdf(tmp_path: Path, lines: list[tuple[float, float, str]], name: str = "inv.pdf", size: float = 10) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    for x, y, text in lines:
        page.insert_text((x, y), text, fontsize=size)
    path = tmp_path / name
    doc.save(path)
    return path


_TOTALS = [
    (60, 300, "Description"), (480, 300, "Amount"), (60, 316, "Technician on site"), (480, 316, "1,000.00"),
    (380, 500, "Subtotal"), (480, 500, "1,000.00"), (380, 515, "HST 13%"), (480, 515, "130.00"),
    (380, 530, "Total"), (480, 530, "1,130.00"),
]  # fmt: skip


def test_a_partnership_at_the_top_is_the_supplier_not_the_customers_corp_below(tmp_path):
    lines = [
        (60, 55, "True North Office Products LP"), (60, 70, "6287 Barrington St"), (60, 84, "Hamilton, ON L0C 4V8"),
        (60, 98, "HST No.: 950589770RT0003"), (380, 55, "TAX INVOICE"), (380, 75, "Bill No."), (470, 75, "26/2688"),
        (380, 90, "Date"), (470, 90, "2026-06-10"),
        (60, 150, "Northline Energy Corp."), (60, 164, "7124 Victoria Ave"), (60, 178, "Barrie, ON L7L 6V2"),
        *_TOTALS,
    ]  # fmt: skip
    result = analyze(_pdf(tmp_path, lines), today=TODAY)
    assert result.fields["vendor_name"].value == "True North Office Products LP"


def test_ltee_read_right_by_ocr_stays_ltee():
    assert _ocr_name_fix("Plomberie Gagnon ltée") == "Plomberie Gagnon ltée"
    assert _ocr_name_fix("Plomberie Gagnon Itée") == "Plomberie Gagnon ltée"  # the slip is still undone
    assert _ocr_name_fix("Plomberie Gagnon ltee") == "Plomberie Gagnon ltée"
    assert _ocr_name_fix("Acme Supply ltd") == "Acme Supply ltd"  # never "Itd"
    assert _ocr_name_fix("lronwood Supply") == "Ironwood Supply"  # an English word does not start "lr"


def test_a_po_in_a_header_grid_is_read_under_its_label_not_beside_it(tmp_path):
    # As a scan prints a grid of boxed fields, the rows a little off: "Purchase Order No." sits lower than the other
    # labels of its row, level with the values printed under "Order No." and "Ship Date".
    lines = [
        (60, 55, "Lakeshore Plumbing Ltd."), (60, 70, "12 Main St, Toronto, ON M5V 1A1"), (380, 55, "INVOICE"),
        (380, 75, "Invoice No."), (470, 75, "FA97024"), (380, 90, "Date"), (470, 90, "2026-04-10"),
        (230, 141, "Ship Date"), (400, 141, "Order No."), (60, 149, "Purchase Order No."),
        (230, 154, "2026-04-13"), (400, 154, "SO-28797"), (60, 164, "P70-7926"),
        *_TOTALS,
    ]  # fmt: skip
    result = analyze(_pdf(tmp_path, lines), today=TODAY)
    assert result.fields["po_number"].value == "P70-7926"


def test_a_speck_read_as_a_period_before_cr_or_a_percent_sign_or_in_a_gst_number():
    from ap_coder.capture.normalize import find_amounts, find_gst_numbers, parse_amount

    assert parse_amount("$442.91.CR") == -442.91  # still a credit
    assert (-442.91, 13, 24) in find_amounts("GST/HST(14%): $442.91.CR")
    assert [g[0] for g in find_gst_numbers("Business Number 57165.3898 RT 0001")] == ["571653898RT0001"]


def test_a_qst_rate_with_a_speck_before_its_percent_sign_is_not_the_qst(tmp_path):
    lines = [
        (60, 55, "Plomberie Gagnon ltée"), (60, 70, "1200 rue Principale, Laval (QC) H7N 1A1"),
        (60, 85, "TPS/TVH: 226689644RT0001"), (60, 100, "TVQ: 1627777827TQ0001"),
        (380, 55, "FACTURE"), (380, 75, "Facture n°"), (470, 75, "F-20417"), (380, 90, "Date"), (470, 90, "2026-05-02"),
        (60, 300, "Description"), (480, 300, "Montant"), (60, 316, "Calibration of pressure gauges"),
        (480, 316, "4 055,93 $"),
        (380, 500, "Sous-total:"), (480, 500, "4 055,93 $"), (380, 515, "T.P.S. (5 %):"), (480, 515, "202,80 $"),
        (380, 530, "T.V.Q. (9,975.%):"), (480, 530, "404,58 $"), (380, 545, "TOTAL:"), (480, 545, "4 663,31 $"),
    ]  # fmt: skip
    result = analyze(_pdf(tmp_path, lines), today=TODAY)
    assert result.fields["qst_amount"].value == 404.58
    assert result.fields["grand_total"].value == 4663.31


def test_terms_with_a_speck_between_the_words_are_still_read():
    from ap_coder.capture.reader import _terms

    assert _terms("Payable on.receipt") == "Due on receipt"  # fresh scan seed 9301: was missing
    assert _terms("Due upon. receipt") == "Due on receipt"
    assert _terms("Payable à.réception") == "Due on receipt"


def _x_of(prefix: str, x: float = 60, size: float = 10) -> float:
    """Where a word starts in a line printed at ``x``: after ``prefix``."""
    return x + pymupdf.get_text_length(prefix, fontsize=size)


def test_bilingual_labels_set_one_space_apart_each_find_their_value(tmp_path):
    # A header grid whose labels the PDF's own text joins into one line (fresh seed 9301): the second label was lost.
    head = "Invoice No. / N° de facture "
    row = head + "PO / Votre commande "
    lines = [
        (60, 55, "Lakeshore Plumbing Ltd."), (60, 70, "12 Main St, Toronto, ON M5V 1A1"), (380, 55, "INVOICE"),
        (60, 130, row + "Terms of Payment / Conditions"),
        (60, 145, "INV-97361"), (_x_of(head), 145, "PO-47389"), (_x_of(row), 145, "NET 15"),
        (380, 90, "Date"), (470, 90, "2026-04-10"),
        *_TOTALS,
    ]  # fmt: skip
    fields = analyze(_pdf(tmp_path, lines), today=TODAY).fields
    assert fields["invoice_number"].value == "INV-97361"
    assert fields["po_number"].value == "PO-47389"
    assert fields["payment_terms"].value == "Net 15"


def test_delivery_date_stays_one_label_not_a_delivery_and_a_date(tmp_path):
    lines = [
        (60, 55, "Lakeshore Plumbing Ltd."), (60, 70, "12 Main St, Toronto, ON M5V 1A1"), (380, 55, "TAX INVOICE"),
        (380, 75, "Invoice #"), (470, 75, "006534"), (380, 90, "Date"), (470, 90, "12-Apr-2026"),
        (380, 105, "Delivery Date:"), (470, 105, "07-Apr-2026"),
        *_TOTALS,
    ]  # fmt: skip
    assert analyze(_pdf(tmp_path, lines), today=TODAY).fields["invoice_date"].value == "2026-04-12"


def test_inv_date_is_not_an_invoice_number_label_and_a_title_reads_no_value_under_another_label(tmp_path):
    # Fresh seed 9301: "Inv. Date" was taken for the invoice number's label, and the big "INVOICE" title took the
    # account number printed under "Account #" below it.
    lines = [
        (60, 55, "Lakeshore Plumbing Ltd."), (60, 70, "12 Main St, Toronto, ON M5V 1A1"),
        (60, 279, "Invoice"), (190, 279, "Inv. Date"), (257, 279, "Account #"), (390, 279, "PO Date"),
        (60, 291, "63-18513"), (185, 291, "06/20/2025"), (257, 291, "C-97134"), (385, 291, "06/02/2025"),
        *_TOTALS,
    ]  # fmt: skip
    path = _pdf(tmp_path, lines)
    with pymupdf.open(path) as doc:
        doc[0].insert_text((257, 190), "INVOICE", fontsize=26)
        doc.saveIncr()
    assert analyze(path, today=TODAY).fields["invoice_number"].value == "63-18513"


def test_due_on_receipt_is_the_terms_not_a_due_date_label(tmp_path):
    lines = [
        (60, 55, "Lakeshore Plumbing Ltd."), (60, 70, "12 Main St, Toronto, ON M5V 1A1"), (380, 55, "SALES INVOICE"),
        (60, 219, "Inv #"), (160, 219, "Date of issue"), (257, 219, "Terms"), (459, 219, "Ship Date"),
        (60, 233, "8306705"), (160, 233, "2025-10-07"), (257, 233, "Due on receipt"), (459, 233, "2025-10-06"),
        *_TOTALS,
    ]  # fmt: skip
    fields = analyze(_pdf(tmp_path, lines), today=TODAY).fields
    assert fields["due_date"].value is None  # was the ship date, right of "Due on receipt"
    assert fields["payment_terms"].value == "Due on receipt"


def _titled(tmp_path, lines, title: str, at: tuple[float, float]):
    path = _pdf(tmp_path, lines)
    with pymupdf.open(path) as doc:
        doc[0].insert_text(at, title, fontsize=22)
        doc.saveIncr()
    return path


def test_a_title_never_reads_a_line_with_its_own_label_as_the_number(tmp_path):
    # Fresh seed 9701: the "INVOICE" title read "Inv 7" under it as the invoice number, shown as likely.
    lines = [(60, 55, "Courier Express Ltd."), (60, 70, "9 Dock Rd, Halifax, NS B3H 1A1"), (526, 79, "Inv 7"),
             (380, 110, "Date"), (470, 110, "2026-04-10"), *_TOTALS]  # fmt: skip
    number = analyze(_titled(tmp_path, lines, "INVOICE", (490, 55)), today=TODAY).fields["invoice_number"]
    assert number.value != "Inv 7"


def test_the_day_of_a_date_is_not_an_invoice_number(tmp_path):
    # Fresh seed 9701: "30" of "30 Jun 2025" under a bilingual title was taken for the invoice number.
    lines = [
        (60, 55, "Northwind Fournitures inc."), (60, 70, "1200 rue Principale, Laval (QC) H7N 1A1"),
        (520, 74, "30 Jun 2025"), (337, 89, "Invoice / Numéro de facture:"), (532, 89, "34-90735"),
        *_TOTALS,
    ]  # fmt: skip
    number = analyze(_titled(tmp_path, lines, "INVOICE / FACTURE", (373, 45)), today=TODAY).fields["invoice_number"]
    assert number.value == "34-90735"


def test_french_first_of_the_month_as_ocr_reads_it():
    from ap_coder.capture.normalize import parse_dates

    # Fresh scan seed 9501: OCR read "1er octobre 2026" as "ler octobre 2026" (and "Ier" happens too).
    assert [d.isoformat() for d, *_ in parse_dates("ler octobre 2026")] == ["2026-10-01"]
    assert [d.isoformat() for d, *_ in parse_dates("Ier mai 2026")] == ["2026-05-01"]
    assert parse_dates("Atelier 3") == []
    # Seed 9102: a comma and a speck between the day and the year.
    assert [d.isoformat() for d, *_ in parse_dates("October 23，.2026")] == ["2026-10-23"]
    assert [d.isoformat() for d, *_ in parse_dates("October 23, . 2026")] == ["2026-10-23"]
    assert parse_dates("Total Oct 23 1,234.56") == []


def test_a_due_date_label_with_a_speck_is_still_not_the_invoice_date():
    from ap_coder.capture.reader import _distracted

    assert _distracted("invoice_date", "date. limite de paiement")  # OCR's "Date. limite de paiement"
    assert not _distracted("invoice_date", "date")


def test_one_pound_sign_among_dollar_amounts_is_not_the_currency():
    from ap_coder.capture.normalize import currency_evidence

    assert currency_evidence("Subtotal $100.00\nTax $5.00\nTotal $105.00\nFreight £66") is None  # OCR's "$66"
    assert currency_evidence("Subtotal £1,000.00\nTotal £1,200.00") == ("GBP", 0.8)
    assert currency_evidence("Montant 1.000,00 €") == ("EUR", 0.8)


def test_a_prefixed_number_with_ocrs_space_before_the_hyphen_is_one_number():
    from ap_coder.capture.reader import _distracted, _id_from

    # Fresh scan seed 9601: "PO -20203" under "Your PO No." was read as a "PO" label and the value "20203".
    assert _id_from("PO -20203", 0)[0] == "PO-20203"
    assert _id_from("INV- 4471", 0)[0] == "INV-4471"
    assert _distracted("po_number", "po -20203") and not _distracted("po_number", "po # 20203")
    assert _distracted("invoice_number", "inv -4471")
    assert _id_from("Net -30", 0) is None  # not a capitalised prefix: left alone


def test_a_gst_hst_rate_whose_percent_sign_ocr_read_as_an_8():
    from ap_coder.capture.reader import _combined_tax_fields

    # Fresh scan seed 9601: "GST/HST @58" (5%) was taken for HST, the other engine's "@5%" for GST: the tax doubled.
    assert _combined_tax_fields("gst/hst@58") == ("gst_amount",)
    assert _combined_tax_fields("gst/hst (138)") == ("hst_amount",)
    assert _combined_tax_fields("gst/hst@5%") == ("gst_amount",)


def test_terms_with_ocrs_full_width_comma_keep_their_discount():
    from ap_coder.capture.reader import _terms

    # Fresh scan seed 9702: "2%10，Net30" lost its early-payment discount ("Net 30", shown as likely).
    assert _terms("2%10，Net30") == "2% 10 Net 30"


def test_a_registration_number_line_never_labels_a_tax_amount(tmp_path):
    # Fresh scan seed 9702: "GST/HST Reg. No.: ... RT0003" took a stray "0" further down the page as the HST (0.00).
    lines = [
        (60, 55, "Lakeshore Plumbing Ltd."), (60, 70, "12 Main St, Toronto, ON M5V 1A1"), (380, 55, "INVOICE"),
        (380, 75, "Invoice No."), (470, 75, "FA97024"), (380, 90, "Date"), (470, 90, "2026-04-10"),
        (60, 300, "Description"), (480, 300, "Amount"), (60, 316, "Technician on site"), (480, 316, "1,000.00"),
        (380, 500, "Subtotal"), (480, 500, "1,000.00"), (380, 515, "GST 5%"), (480, 515, "50.00"),
        (380, 530, "Total"), (480, 530, "1,050.00"),
        (60, 600, "GST/HST Reg. No.: 988130969 RT0003"), (75, 616, "0"),
    ]  # fmt: skip
    fields = analyze(_pdf(tmp_path, lines), today=TODAY).fields
    assert fields["hst_amount"].value is None and fields["gst_amount"].value == 50.0


def test_month_names_and_years_as_ocr_misreads_them():
    from ap_coder.capture.normalize import parse_dates

    # Fresh scan seed 9702: each left the date unread.
    for text, iso in (("28Apr 2.025", "2025-04-28"), ("15Eeb.2025", "2025-02-15"), ("Apri1 15,2026", "2026-04-15")):
        assert [d.isoformat() for d, *_ in parse_dates(text)] == [iso], text
    assert parse_dates("Total 2.025") == []  # a number, not a year


def test_a_total_label_with_ocrs_1_for_its_l_is_still_the_total(tmp_path):
    lines = [
        (60, 55, "Lakeshore Plumbing Ltd."), (60, 70, "12 Main St, Toronto, ON M5V 1A1"), (380, 55, "INVOICE"),
        (380, 75, "Invoice No."), (470, 75, "FA97024"), (380, 90, "Date"), (470, 90, "2026-04-10"),
        (60, 300, "Description"), (480, 300, "Amount"), (60, 316, "Technician on site"), (480, 316, "1,000.00"),
        (380, 500, "Total before tax"), (480, 500, "1,000.00"), (380, 515, "HST 13%"), (480, 515, "130.00"),
        (420, 530, "Tota1CAD1,130.00"),
    ]  # fmt: skip
    assert analyze(_pdf(tmp_path, lines), today=TODAY).fields["grand_total"].value == 1130.0


def test_a_street_number_under_the_invoice_title_is_not_the_invoice_number(tmp_path):
    # Fresh digital seed 9801: the "Invoice" title read "2565 av. du Parc" below it as the invoice number.
    lines = [
        (60, 120, "Bill To"), (60, 160, "2565 av. du Parc"), (60, 175, "Montreal (QC) H2V 4H6"),
        (180, 200, "Invoice"), (180, 213, "IN-2026-39513"), (380, 200, "Date"), (380, 213, "2026-04-10"),
        *_TOTALS,
    ]  # fmt: skip
    path = _pdf(tmp_path, lines)
    with pymupdf.open(path) as doc:
        doc[0].insert_text((50, 60), "Invoice", fontsize=40)
        doc.saveIncr()
    assert analyze(path, today=TODAY).fields["invoice_number"].value == "IN-2026-39513"


def test_a_bilingual_label_after_another_labels_colon_finds_its_value(tmp_path):
    # Digital seed 9101: "... livraison: Payment Terms / Conditions de paiement:" left the terms for a person.
    lines = [
        (60, 55, "Lakeshore Plumbing Ltd."), (60, 70, "12 Main St, Toronto, ON M5V 1A1"), (380, 55, "INVOICE"),
        (380, 75, "Invoice No."), (470, 75, "FA97024"), (380, 90, "Date"), (470, 90, "2026-04-10"),
        (60, 122, "Service Address / Adresse de livraison: Payment Terms / Conditions de paiement:"),
        (500, 122, "30 days"),
        *_TOTALS,
    ]  # fmt: skip
    assert analyze(_pdf(tmp_path, lines, size=8), today=TODAY).fields["payment_terms"].value == "Net 30"
