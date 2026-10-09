"""Capture edge cases found by a bug hunt: each test reproduces a wrong result the reader once gave."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from ap_coder.capture import analyze, build_layout
from ap_coder.capture.bridge import ai_values
from ap_coder.capture.supplier import AUTONOMOUS, confirmed_values, learn, should_auto_approve
from ap_coder.capture.types import VERIFIED

TODAY = dt.date(2026, 10, 9)


def _pdf(tmp_path: Path, lines: list[tuple[float, float, str]], name: str = "inv.pdf") -> Path:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    for x, y, text in lines:
        page.insert_text((x, y), text, fontsize=10)
    path = tmp_path / name
    doc.save(path)
    return path


_ACME = [
    (60, 60, "Acme Supply Ltd."), (60, 75, "12 Main St, Calgary, AB T2P 1A1"),
    (60, 90, "GST Reg. No. 123456782 RT0001"),
    (380, 120, "Invoice No:"), (460, 120, "AC-1001"), (380, 135, "Invoice Date:"), (460, 135, "2026-09-14"),
    (60, 250, "Description"), (300, 250, "Qty"), (500, 250, "Amount"),
    (60, 265, "Toner cartridges"), (300, 265, "2"), (500, 265, "1,000.00"),
    (380, 500, "Subtotal"), (500, 500, "1,000.00"), (380, 515, "GST 5%"), (500, 515, "50.00"),
    (380, 530, "Total Due CAD"), (500, 530, "1,050.00"),
]  # fmt: skip
_ACME_TRUTH = {
    "vendor_name": "Acme Supply Ltd.", "invoice_number": "AC-1001", "invoice_date": "2026-09-14", "currency": "CAD",
    "gst_hst_registration_number": "123456782RT0001", "subtotal": 1000.0, "grand_total": 1050.0,
    "tax_lines": [{"tax_type": "GST", "tax_amount": 50.0}],
}  # fmt: skip


def test_an_amount_the_coding_has_differently_is_not_verified(tmp_path):
    """The coding (what an autonomous approval posts) says subtotal 1,450.00; the page prints 1,000.00. The
    page's value adding up must not make the field "verified" (and the invoice touchless) while the posted
    value differs from it."""
    pdf = _pdf(tmp_path, _ACME)
    layout = build_layout(pdf, ocr=False)
    template = None
    for _ in range(3):
        template = learn(template, layout, confirmed_values(_ACME_TRUTH))
    agree = analyze(pdf, ocr=False, ai_values=ai_values(_ACME_TRUTH), template=template, today=TODAY)
    assert should_auto_approve(AUTONOMOUS, agree, True)[0]  # the coding matches the page: touchless
    coding = {**_ACME_TRUTH, "subtotal": 1450.0}
    capture = analyze(pdf, ocr=False, ai_values=ai_values(coding), template=template, today=TODAY)
    assert capture.fields["subtotal"].status != VERIFIED
    assert not should_auto_approve(AUTONOMOUS, capture, True)[0]
