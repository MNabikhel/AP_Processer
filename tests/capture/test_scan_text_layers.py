"""A scanned page that carries a text layer: a scanner's "searchable PDF" (its own OCR, drawn invisibly under the
picture) or a stamp of real text on a scan (a scanner's header, a mailroom's "received" line). The page is still read
as the scan it is (B-1511, B-1512); a digital page keeps its exact text."""

from __future__ import annotations

import pymupdf
import pytest

from ap_coder.capture import layout
from ap_coder.capture.types import PageLayout

ROWS = ["Acme Supply Ltd.", "123 Main St, Toronto, ON M5V 2T6", "GST/HST No. 123456782 RT0001", "INVOICE",
        "Invoice No: INV-1001", "Invoice Date: 2026-10-20", "PO Number: PO-55120", "Bill To: Our Company Inc.",
        "Subtotal: 100.00", "HST 13%: 13.00", "Total: 113.00"]  # fmt: skip


def _picture_of(rows: list[str]) -> bytes:
    src = pymupdf.open()
    page = src.new_page(width=612, height=792)
    for i, text in enumerate(rows):
        page.insert_text((50, 50 + 16 * i), text, fontsize=10)
    return page.get_pixmap(dpi=100).tobytes("png")


def _pdf(path, *, hidden: list[str] = (), stamp: str = "", shown: list[str] = (), picture: bool = True):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    if picture:
        page.insert_image(page.rect, stream=_picture_of(ROWS))
    for i, text in enumerate(hidden):  # a scanner's OCR layer: invisible text (render mode 3)
        page.insert_text((50, 50 + 16 * i), text, fontsize=10, render_mode=3)
    for i, text in enumerate(shown):
        page.insert_text((50, 300 + 12 * i), text, fontsize=9)
    if stamp:
        page.insert_text((20, 14), stamp, fontsize=7)
    doc.save(path)
    return path


@pytest.fixture
def ocr_calls(monkeypatch):
    """OCR stubbed: each page it reads is recorded and gives the words a real OCR would."""
    calls: list[int] = []

    def fake_ocr(png: bytes, number: int, *args, **kwargs) -> PageLayout:
        calls.append(number)
        words = [layout.Word(t, layout.Box(number, 0.1, 0.05 + 0.02 * i, 0.5, 0.06 + 0.02 * i), 0.95, "ocr")
                 for i, t in enumerate("INVOICE INV-1001 Total 113.00".split())]  # fmt: skip
        return PageLayout(number, 612, 792, words, layout.group_lines(words, number), 0)

    monkeypatch.setattr(layout, "ocr_available", lambda: True)
    monkeypatch.setattr(layout, "ocr_image", fake_ocr)
    monkeypatch.setattr(layout, "_scan_png", lambda doc, page: b"png")
    monkeypatch.setattr(layout, "_second_image", lambda png: png)
    return calls


def test_a_scanners_searchable_pdf_is_read_as_the_scan_it_is(tmp_path, ocr_calls):
    """B-1511: the scanner's own OCR slips (INV-1O01, PO-55128) were taken as the PDF's exact text, shown Likely."""
    slips = [r.replace("INV-1001", "INV-1O01").replace("PO-55120", "PO-55128") for r in ROWS]
    doc = layout.build_layout(_pdf(tmp_path / "searchable.pdf", hidden=slips))
    assert doc.source == "ocr" and ocr_calls == [1]
    assert "INV-1O01" not in " ".join(w.text for w in doc.pages[0].words)


def test_a_scan_with_a_stamp_of_real_text_is_still_read_by_ocr(tmp_path, ocr_calls):
    """B-1512: five words of real text (a scanner's header) made the page a text page: OCR never ran and every field
    came out missing, with nothing said."""
    path = _pdf(tmp_path / "stamped.pdf", stamp="Scanned by Xerox WorkCentre 7845 on 2026-10-21 10:31")
    doc = layout.build_layout(path)
    assert doc.source == "ocr" and ocr_calls == [1]
    assert layout.second_read_layout(path) is not None  # the second read of a scan runs too


def test_a_digital_invoice_on_a_full_page_background_keeps_its_exact_text(tmp_path, ocr_calls):
    shown = [f"{n} Widget model {n} supplied and installed at the site {n}.00" for n in range(1, 8)]
    doc = layout.build_layout(_pdf(tmp_path / "letterhead.pdf", shown=shown))
    assert doc.source == "text" and ocr_calls == []
    assert layout.second_read_layout(tmp_path / "letterhead.pdf") is None


def test_a_plain_digital_invoice_keeps_its_exact_text(tmp_path, ocr_calls):
    doc = layout.build_layout(_pdf(tmp_path / "digital.pdf", shown=ROWS, picture=False))
    assert doc.source == "text" and ocr_calls == []


def test_without_ocr_a_searchable_pdf_is_read_from_its_text_layer(tmp_path, monkeypatch):
    """No OCR on this computer: the scanner's text is the best there is, never "OCR isn't installed"."""
    monkeypatch.setattr(layout, "ocr_available", lambda: False)
    doc = layout.build_layout(_pdf(tmp_path / "searchable.pdf", hidden=ROWS))
    assert doc.source == "text" and "INV-1001" in " ".join(w.text for w in doc.pages[0].words)


def test_a_searchable_page_ocr_cannot_read_falls_back_to_its_text_layer(tmp_path, monkeypatch):
    monkeypatch.setattr(layout, "ocr_available", lambda: True)
    monkeypatch.setattr(layout, "_scan_png", lambda doc, page: b"png")

    def broken(*args, **kwargs):
        raise RuntimeError("the OCR engine failed")

    monkeypatch.setattr(layout, "ocr_image", broken)
    doc = layout.build_layout(_pdf(tmp_path / "searchable.pdf", hidden=ROWS))
    assert doc.source == "text" and doc.pages_unread_locally == 0
