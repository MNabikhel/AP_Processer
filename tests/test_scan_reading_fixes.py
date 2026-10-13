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
