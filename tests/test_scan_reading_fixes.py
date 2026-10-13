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
