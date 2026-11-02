"""A digital PDF read by the page reader too: when the two disagree on a field, the PDF's own text (exact) is the value
shown and used, still marked Check with both readings named (B-1516)."""

from __future__ import annotations

import datetime as dt

from ap_coder.capture import analyze
from ap_coder.capture.types import CHECK
from tests.pdfs import text_pdf

ROWS = ["Acme Supply Ltd.", "123 Main St, Toronto, ON M5V 2T6", "GST/HST No. 123456782 RT0001", "INVOICE",
        "Invoice No: INV-1001", "Invoice Date: 2026-10-20", "PO Number: PO-55120", "Due Date: 2026-11-19",
        "Terms: Net 30", "Bill To: Our Company Inc.", "Subtotal: 100.00", "HST 13%: 13.00", "Total: 113.00"]  # fmt: skip


def test_the_pdfs_own_text_wins_over_a_page_reader_misread(tmp_path):
    pdf = text_pdf(tmp_path, [(50, 50 + 16 * i, t) for i, t in enumerate(ROWS)])
    misread = "\n".join(ROWS).replace("2026-10-20", "2026-10-28").replace("PO-55120", "PO-55126")
    result = analyze(pdf, today=dt.date(2026, 11, 2), page_text=[misread], tables=False)
    date, po = result.fields["invoice_date"], result.fields["po_number"]
    assert (date.value, po.value) == ("2026-10-20", "PO-55120")
    assert date.status == CHECK and po.status == CHECK  # the readers disagree: a person looks
    assert date.boxes and po.boxes  # shown where the page prints it


def test_the_page_readers_value_stands_when_the_pdfs_text_has_none(tmp_path):
    rows = [r for r in ROWS if not r.startswith("PO Number")]
    pdf = text_pdf(tmp_path, [(50, 50 + 16 * i, t) for i, t in enumerate(rows)])
    result = analyze(pdf, today=dt.date(2026, 11, 2), page_text=["\n".join(ROWS)], tables=False)
    assert result.fields["po_number"].value == "PO-55120"
