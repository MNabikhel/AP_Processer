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


def test_credit_marks_and_separator_dashes_around_amounts(tmp_path):
    """A "Cr" after an amount is a credit however it is cased; a dash between the amount and more words on
    the line ("$1,050.00 - Payable on receipt") is a separator, not a trailing minus."""
    from ap_coder.capture.normalize import find_amounts, parse_amount

    assert parse_amount("1,050.00 Cr") == -1050.0
    assert parse_amount("1,050.00 CR") == -1050.0
    assert parse_amount("1,050.00-") == -1050.0
    assert parse_amount("$1,050.00 - Payable on receipt") == 1050.0
    assert [v for v, _, _ in find_amounts("Period 2026-10 total 45.00")][-1] == 45.0
    assert -2026.0 not in [v for v, _, _ in find_amounts("Period 2026-10 total 45.00")]
    pdf = _pdf(tmp_path, [(60, 60, "Acme Supply Ltd."), (380, 500, "Subtotal"), (500, 500, "1,000.00"),
                          (380, 515, "GST 5%"), (500, 515, "50.00"),
                          (380, 530, "Total Due: $1,050.00 - Payable on receipt")])  # fmt: skip
    capture = analyze(pdf, ocr=False, today=TODAY)
    assert capture.fields["grand_total"].value == 1050.0
    assert all(c["ok"] for c in capture.checks if c["code"] == "TOTALS_ADD_UP")


def _fake_ocr(monkeypatch) -> list:
    """OCR that records the page images it is given (and reads nothing): no model needed."""
    from ap_coder.capture import layout as layout_mod

    seen: list = []
    monkeypatch.delenv("AP_OCR_CACHE", raising=False)
    monkeypatch.setattr(layout_mod, "ocr_available", lambda: True)
    monkeypatch.setattr(layout_mod, "_engine", lambda name=None: lambda img: seen.append(img) or [])
    return seen


def test_a_multi_page_tiff_scan_is_read_page_by_page_at_its_own_resolution(tmp_path, monkeypatch):
    """A faxed or scanned invoice saved as one TIFF with a page per frame, at 200 dpi: every page is read
    (the totals are often on the last), and at the resolution it was scanned at, not shrunk to 72 dpi."""
    from PIL import Image

    seen = _fake_ocr(monkeypatch)
    pages = [Image.new("RGB", (1700, 2200), "white") for _ in range(2)]
    path = tmp_path / "scan.tif"
    pages[0].save(path, save_all=True, append_images=pages[1:], dpi=(200, 200))
    layout = build_layout(path)
    assert [p.number for p in layout.pages] == [1, 2]
    assert [img.shape[:2] for img in seen] == [(2200, 1700), (2200, 1700)]


def test_a_transparent_png_is_read_on_white_paper(tmp_path, monkeypatch):
    """A PNG with a transparent background (an export, a screenshot): its transparent pixels are paper, not
    black, or the black text disappears into a black page."""
    from PIL import Image, ImageDraw

    seen = _fake_ocr(monkeypatch)
    img = Image.new("RGBA", (400, 300), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((20, 20), "Total 1,050.00", fill=(0, 0, 0, 255))
    path = tmp_path / "invoice.png"
    img.save(path)
    build_layout(path)
    assert tuple(seen[0][150, 200]) == (255, 255, 255)  # the background
    assert seen[0].min() < 50  # the text is still dark


def _unicode_pdf(tmp_path: Path, lines: list[tuple[float, float, str]], name: str) -> Path:
    """A PDF whose text layer holds exactly these characters (a Unicode font, so accents and leaders survive)."""
    import pymupdf

    font = pymupdf.Font("cjk")
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    for x, y, text in lines:
        writer = pymupdf.TextWriter(page.rect)
        writer.append((x, y), text, font=font, fontsize=10)
        writer.write_text(page)
    path = tmp_path / name
    doc.save(path)
    return path


def test_accents_stored_decomposed_in_the_text_layer(tmp_path):
    """Some PDF producers store "é" as "e" + a combining accent: the labels must still point at their values."""
    import unicodedata

    lines = [(60, 60, "Acme Supply Ltd."), (300, 120, "Numéro de facture: 12345"),
             (300, 135, "Date de facturation: 2026-09-14")]  # fmt: skip
    pdf = _unicode_pdf(tmp_path, [(x, y, unicodedata.normalize("NFD", t)) for x, y, t in lines], "nfd.pdf")
    import pymupdf

    with pymupdf.open(pdf) as doc:
        assert "é" in doc[0].get_text()  # the text layer really is decomposed
    capture = analyze(pdf, ocr=False, today=TODAY)
    assert capture.fields["invoice_number"].value == "12345"


def test_an_amount_after_ellipsis_leaders_keeps_its_place(tmp_path):
    """Word turns "..." into "…": "Total………… 1,050.00". The amount's span must stay on the printed text
    (compatibility forms such as "…" -> "..." change the length), or the total is lost."""
    from ap_coder.capture.normalize import find_amounts

    text = "Total………………… 1,050.00"
    assert [(v, text[a:b].strip()) for v, a, b in find_amounts(text)] == [(1050.0, "1,050.00")]
    pdf = _unicode_pdf(tmp_path, [(60, 60, "Acme Supply Ltd."), (300, 530, text)], "leaders.pdf")
    assert analyze(pdf, ocr=False, today=TODAY).fields["grand_total"].value == 1050.0


def _credit_memo(title: str, total_label: str, amounts: tuple[str, str, str, str]) -> list[tuple[float, float, str]]:
    line, sub, gst, total = amounts
    return [
        (60, 60, "Acme Supply Ltd."), (60, 75, "12 Main St, Calgary, AB T2P 1A1"), (400, 60, title),
        (380, 100, "Credit Memo No: CM-2045"), (380, 115, "Date: 2026-09-20"), (380, 130, "Original Invoice: AC-0998"),
        (60, 200, "Description"), (300, 200, "Qty"), (500, 200, "Amount"),
        (60, 215, "Returned toner cartridges"), (300, 215, "2"), (500, 215, line),
        (380, 500, "Subtotal"), (500, 500, sub), (380, 515, "GST 5%"), (500, 515, gst),
        (380, 530, total_label), (500, 530, total),
    ]  # fmt: skip


_POSITIVE = ("1,000.00", "1,000.00", "50.00", "1,050.00")


def test_a_credit_memo_printed_with_positive_amounts_is_a_credit(tmp_path):
    """Many suppliers print a credit memo's amounts without a sign, under a "CREDIT MEMO" title. AP Coder
    posts credits as negative amounts: read as printed, the credit would be posted as an invoice to pay."""
    for i, (title, label) in enumerate([("CREDIT MEMO", "Total Credit"), ("NOTE DE CRÉDIT", "Montant crédité")]):
        pdf = _unicode_pdf(tmp_path, _credit_memo(title, label, _POSITIVE), f"cm{i}.pdf")
        capture = analyze(pdf, ocr=False, today=TODAY)
        got = {f: capture.fields[f].value for f in ("subtotal", "gst_amount", "grand_total")}
        assert got == {"subtotal": -1000.0, "gst_amount": -50.0, "grand_total": -1050.0}, title
        assert [li.amount for li in capture.line_items] == [-1000.0]
        assert all(c["ok"] for c in capture.checks if c["code"] in ("TOTALS_ADD_UP", "LINES_ADD_UP"))
    # Printed with its signs, a credit memo is read as printed; an invoice stays positive.
    signed = ("-1,000.00", "-1,000.00", "-50.00", "-1,050.00")
    capture = analyze(_pdf(tmp_path, _credit_memo("CREDIT MEMO", "Total Credit", signed), "signed.pdf"), ocr=False,
                      today=TODAY)  # fmt: skip
    assert capture.fields["grand_total"].value == -1050.0 and capture.line_items[0].amount == -1000.0
    invoice = [(x, y, "INVOICE" if t == "CREDIT MEMO" else t) for x, y, t in _credit_memo("CREDIT MEMO", "Total Due",
               _POSITIVE) if not t.startswith(("Credit Memo No", "Original"))]  # fmt: skip
    capture = analyze(_pdf(tmp_path, invoice, "invoice.pdf"), ocr=False, today=TODAY)
    assert capture.fields["grand_total"].value == 1050.0


def test_a_learned_template_reads_a_positive_credit_memo_as_a_credit(tmp_path):
    from ap_coder.capture.supplier import apply_template

    truth = {"vendor_name": "Acme Supply Ltd.", "subtotal": -1000.0, "grand_total": -1050.0,
             "tax_lines": [{"tax_type": "GST", "tax_amount": -50.0}]}  # fmt: skip
    layout = build_layout(_pdf(tmp_path, _credit_memo("CREDIT MEMO", "Total Credit", _POSITIVE)), ocr=False)
    read = apply_template(learn(None, layout, confirmed_values(truth)), layout)
    assert read["grand_total"][0].value == -1050.0 and read["subtotal"][0].value == -1000.0


def test_a_canadian_supplier_billing_in_usd_keeps_03_04_ambiguous(tmp_path):
    """A Toronto supplier billing in US dollars may well print 03/04/2026 day first: the currency alone does
    not make it March 4 (that is for a supplier with a US address), so it is settled like any other 03/04."""
    lines = [(60, 60, "Maple Tech Ltd."), (60, 75, "12 Main St, Toronto, ON M5V 1A1"),
             (380, 100, "Invoice No: MT-1001"), (380, 115, "Invoice Date: 03/04/2026"), (380, 500, "Subtotal"),
             (500, 500, "1,000.00"), (380, 530, "Total USD"), (500, 530, "1,000.00")]  # fmt: skip
    capture = analyze(_pdf(tmp_path, lines), ocr=False, today=dt.date(2026, 4, 9))
    fr = capture.fields["invoice_date"]
    assert fr.value == "2026-04-03" and fr.status != VERIFIED  # received on April 9: April 3, not March 4
    us = [(x, y, t.replace("12 Main St, Toronto, ON M5V 1A1", "500 Pine St, Seattle, WA 98101")) for x, y, t in lines]
    assert analyze(_pdf(tmp_path, us, "us.pdf"), ocr=False, today=dt.date(2026, 4, 9)).fields["invoice_date"].value \
        == "2026-03-04"  # fmt: skip
