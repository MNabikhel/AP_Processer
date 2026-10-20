"""Reading bugs found by testing on a hard corpus of Canadian invoices (sweep 4): page layouts and printed formats the
rule reader read wrong or not at all. Each fixture is a small generated PDF; the corpus itself is not in the repo."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from ap_coder.capture import analyze
from ap_coder.capture.types import VERIFIED

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


def _read(path: Path, today: dt.date = TODAY):
    return analyze(path, ocr=False, today=today)


_VENDOR = [
    (60, 60, "Distributions Gagnon Ltd.", 16), (60, 80, "9235 Main St, Halifax, NS B3H 1A1"),
    (60, 95, "GST/HST No. 123456782 RT0001"),
]  # fmt: skip
_TABLE = [
    (60, 330, "Description"), (400, 330, "Qty"), (500, 330, "Amount"),
    (60, 345, "Forklift rental (weekly)"), (400, 345, "2"), (500, 345, "1,000.00"),
    (380, 500, "Subtotal"), (500, 500, "1,000.00"), (380, 515, "HST 15%"), (500, 515, "150.00"),
    (380, 530, "Invoice Total"), (500, 530, "1,150.00"),
]  # fmt: skip


def test_a_grid_that_prints_each_label_under_its_value(tmp_path):
    """A header grid of values, each with its small label printed UNDER it, two rows deep: the value under "Invoice
    Number" is the next row's PO number (one digit off), and the invoice date, the due date and the PO were missed."""
    grid = [
        (60, 200, "5874088"), (160, 200, "25/10/2025"), (260, 200, "8574088"), (360, 200, "09/12/2025"),
        (60, 209, "Invoice Number", 7), (160, 209, "Date", 7), (260, 209, "Sales Order", 7),
        (360, 209, "Payment Due", 7),
        (60, 232, "5874082"), (160, 232, "5847088"), (260, 232, "146890"),
        (60, 241, "PO #", 7), (160, 241, "Reference", 7), (260, 241, "Client ID", 7),
    ]  # fmt: skip
    got = _read(_pdf(tmp_path, _VENDOR + grid + _TABLE)).fields
    assert got["invoice_number"].value == "5874088"
    assert got["invoice_date"].value == "2025-10-25"
    assert got["due_date"].value == "2025-12-09"
    assert got["po_number"].value == "5874082"
    assert got["invoice_number"].status != VERIFIED  # a layout the benchmark has not measured


def test_a_grid_of_labels_over_values_is_still_read_below(tmp_path):
    """The usual grid (labels over values, and an address line just over the labels) is read as before."""
    grid = [
        (60, 189, "Bill To: Kestrel Foods, 12 Bay St"),
        (60, 200, "Invoice Number", 7), (160, 200, "Invoice Date", 7), (260, 200, "PO #", 7),
        (60, 213, "INV-7781"), (160, 213, "2026-09-14"), (260, 213, "PO-5521"),
    ]  # fmt: skip
    got = _read(_pdf(tmp_path, _VENDOR + grid + _TABLE)).fields
    assert (got["invoice_number"].value, got["invoice_date"].value, got["po_number"].value) == (
        "INV-7781",
        "2026-09-14",
        "PO-5521",
    )
