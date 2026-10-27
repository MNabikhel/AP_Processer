"""A page scanned coarser than the scans its readings' confidence was measured on (150 dpi and up), e.g. a fax at
100 dpi: a person checks every value (stress test of October 2026: two thirds of a 100 dpi fax's values right, and
some wrong ones shown as verified). And a label OCR misread is never taken for a value."""

from __future__ import annotations

import io

import pymupdf
import pytest
from PIL import Image

from ap_coder.capture import analyze, layout
from ap_coder.capture.types import CHECK, DocLayout, PageLayout

from .pdfs import text_pdf as _pdf

LINES = ["ACME SUPPLY LTD", "INVOICE", "Invoice No: A-1001", "Date: 2026-10-01", "Subtotal: 100.00", "GST 5%: 5.00",
         "Total: 105.00"]  # fmt: skip


def _ocr_page(dpi: float) -> PageLayout:
    words = []
    for i, text in enumerate(LINES):
        words += layout._split_ocr_line(
            text, 100, 100 + 40 * i, 100 + 14 * len(text), 128 + 40 * i, 0.99, 1000, 1300, 1
        )
    page = PageLayout(1, 1000, 1300, words, layout.group_lines(words, 1))
    page.dpi = dpi
    return page


def test_a_page_scanned_at_100_dpi_sends_every_value_to_a_person(tmp_path):
    path = tmp_path / "fax.png"
    Image.new("RGB", (850, 1100), "white").save(path)
    fine = analyze(path, layout=DocLayout([_ocr_page(200)], "ocr"), second_read=False, tables=False).fields
    assert fine["grand_total"].value == 105.0 and fine["grand_total"].status != CHECK
    coarse = analyze(path, layout=DocLayout([_ocr_page(100)], "ocr"), second_read=False, tables=False).fields
    assert coarse["grand_total"].value == 105.0  # still read, for the person to confirm
    assert all(fr.status in (CHECK, "missing") for fr in coarse.values())
    assert any("100 dpi" in r for r in coarse["grand_total"].reasons)


def test_a_scans_resolution_is_its_pictures_own(tmp_path):
    pdf = tmp_path / "scan.pdf"
    with pymupdf.open() as doc:
        for size in ((850, 1100), (1700, 2200)):  # a letter page at 100 dpi, then at 200 dpi
            page = doc.new_page(width=612, height=792)
            png = io.BytesIO()
            Image.new("L", size, 255).save(png, format="PNG")
            page.insert_image(page.rect, stream=png.getvalue())
        doc.save(pdf)
    with pymupdf.open(pdf) as doc:
        assert [layout._native_dpi(page) for page in doc] == [100.0, 200.0]
    stated = Image.new("L", (1275, 1650))
    stated.info["dpi"] = (150, 150)
    assert layout._stated_dpi(stated) == 150.0
    phone = Image.new("L", (3024, 4032))
    phone.info["dpi"] = (72, 72)  # a phone's "72 dpi": a page 56 inches long, not believed
    assert layout._stated_dpi(phone) == 0.0


def test_a_label_ocr_misread_is_not_the_invoice_number(tmp_path):
    from ap_coder.capture.layout import build_layout
    from ap_coder.capture.reader import read_fields

    # Fax stress test: "Invoice #" over "INVO1CE" (the title, OCR's 1 for its I) made INVO1CE the invoice number,
    # shown as verified.
    lines = [(60, 55, "Riverside Hydraulics Co."), (380, 75, "Invoice #"), (380, 90, "INVO1CE"),
             (470, 75, "Date"), (470, 90, "2026-04-10"), (380, 500, "Total"), (480, 500, "1,130.00")]  # fmt: skip
    got = read_fields(build_layout(_pdf(tmp_path, lines), ocr=False))["invoice_number"]
    assert "INVO1CE" not in [r.value for r in got]


def test_a_standard_fax_is_a_full_page_measured_by_its_coarser_rows(tmp_path):
    from ap_coder.intake import pictures as intake

    # A fax-to-email TIFF in standard mode: 204 dpi across, 98 down. Sweep: it was letterboxed into its page, measured
    # at 204 dpi (so the coarse-scan guard missed it), and OCR read it squashed to half its height.
    fax = tmp_path / "fax.tif"
    Image.new("L", (1728, 1078), 255).save(fax, dpi=(204, 98))
    with pymupdf.open(intake.to_pdf(fax).pdf) as doc:
        page = doc[0]
        assert page.rect.width == pytest.approx(1728 / 204 * 72, rel=0.01)
        assert page.rect.height == pytest.approx(1078 / 98 * 72, rel=0.01)
        assert layout._native_dpi(page) == pytest.approx(98, abs=1)
        assert intake.page_picture(doc, page).size == (1728, 2244)  # rows stretched 204/98, as a fax viewer shows them


def test_a_small_stretched_background_is_not_the_scan(tmp_path):
    pdf = tmp_path / "outlined.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page(width=612, height=792)
        png = io.BytesIO()
        Image.new("L", (2, 256), 230).save(png, format="PNG")
        page.insert_image(page.rect, stream=png.getvalue(), keep_proportion=False)
        doc.save(pdf)
    with pymupdf.open(pdf) as doc:
        assert layout._native_dpi(doc[0]) == 0.0  # unknown, not "scanned at 0 dpi"
