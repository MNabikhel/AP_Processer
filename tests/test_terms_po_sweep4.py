"""Reading terms, PO numbers and due dates on real invoices (sweep 4). What 135 public sample invoices taught about how
suppliers print their payment terms, the customer's order number and the due date. Every fixture is generated here:
small PDFs of a few lines, or a terms text as a label's value."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from ap_coder.capture import analyze
from ap_coder.capture.reader import _terms
from ap_coder.coding.terms import Terms, parse_terms

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
    return analyze(path, ocr=False, today=TODAY).fields


_HEAD = [
    (60, 60, "Northshore Industrial Supply Ltd.", 16), (60, 80, "45 Harbour Rd, Halifax, NS B3H 1A1"),
    (60, 95, "GST/HST No. 123456782 RT0001"), (380, 60, "INVOICE", 14),
    (380, 85, "Invoice No."), (470, 85, "NS-20418"), (380, 100, "Invoice Date"), (470, 100, "2026-09-15"),
]  # fmt: skip
_TABLE = [
    (60, 330, "Description"), (500, 330, "Amount"), (60, 345, "Hydraulic fittings"), (500, 345, "1,000.00"),
    (380, 500, "Subtotal"), (500, 500, "1,000.00"), (380, 515, "HST 15%"), (500, 515, "150.00"),
    (380, 530, "Invoice Total"), (500, 530, "1,150.00"),
]  # fmt: skip


# ---------------------------------------------------------------- terms as the app reads them


@pytest.mark.parametrize(
    "printed, read",
    [
        # The app counts these from the end of the month, a day of the next month, calendar months or weeks: the
        # reader's short form kept only the number ("Net 30", "Net 10", "Net 1", "Net 2"), so the due date was wrong.
        ("Net 30 EOM", "Net 30 EOM"),
        ("Net 30 days end of month", "Net 30 EOM"),
        ("30 jours fin de mois", "Net 30 EOM"),
        ("Net 10th prox", "Net 10 prox"),
        ("Net 1 month", "Net 1 month"),
        ("Net 2 weeks", "Net 14"),
        # A discount's days are not the net days.
        ("2% 10 days", "2% 10 days"),
        ("2% discount if paid within 10 days", "2% 10 days"),
        ("Net 45, 1% 15 days", "1% 15 Net 45"),
        ("2/10 EOM", "2% 10 Net 30 EOM"),
        # As before.
        ("Net 30", "Net 30"),
        ("NET 30", "Net 30"),
        ("Net 30 days", "Net 30"),
        ("30 days", "Net 30"),
        ("2% 10, Net 30", "2% 10 Net 30"),
        ("Net 60 jours", "Net 60"),
        ("30 jours net", "Net 30"),
        ("solde à 30 j", "Net 30"),
        ("Due on receipt", "Due on receipt"),
        ("Upon receipt", "Due on receipt"),
        ("Payable sur réception", "Due on receipt"),
        ("Net 30 - 1.5% per month on overdue accounts", "Net 30"),
        ("Visa", None),
    ],
)
def test_terms_are_read_in_the_form_the_app_counts_the_due_date_from(printed, read):
    assert _terms(printed) == read
    if read and parse_terms(printed) != Terms():  # the app reads the short form as it reads the printed terms
        assert parse_terms(read) == parse_terms(printed)


def test_eom_terms_on_an_invoice_are_kept_and_checked_against_the_due_date(tmp_path):
    # "Net 30 EOM" on an invoice of Sep 15 is due Oct 30 (30 days after Sep 30): read as "Net 30", the due date
    # printed failed the reader's own check (Sep 15 + 30 days is Oct 15), and the app counted the terms from Sep 15.
    lines = [(380, 115, "Terms"), (470, 115, "Net 30 EOM"), (380, 130, "Due Date"), (470, 130, "2026-10-30")]
    got = _read(_pdf(tmp_path, _HEAD + lines + _TABLE))
    assert got["payment_terms"].value == "Net 30 EOM"
    assert got["due_date"].value == "2026-10-30"
    assert not any("≠ due date" in r for r in got["due_date"].reasons)
