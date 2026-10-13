"""A page scanned sideways or upside down: OCR reads each line's words right (it turns a line to read it), but the
lines lie where the turned page has them, so labels and amounts no longer line up (stress test of October 2026: 22%
of the fields right, totals shown as verified that were not). The lines are turned upright before they are read, and
the PDF is then shown upright, the scan kept as it came."""

from __future__ import annotations

import io

import pymupdf
import pytest
from PIL import Image

from ap_coder import intake
from ap_coder.capture import layout

W, H = 1000, 1300
UPRIGHT = [  # (text, x0, y0, x1, y1) as printed on the upright page
    ("Lakeshore Plumbing Ltd.", 80, 60, 420, 100), ("INVOICE", 700, 60, 900, 110),
    ("Invoice No: A-1001", 600, 150, 900, 180), ("Date: 2026-10-01", 600, 190, 880, 220),
    ("Description", 80, 400, 260, 430), ("Amount", 800, 400, 920, 430),
    ("Technician on site", 80, 450, 380, 480), ("1,000.00", 800, 450, 920, 480),
    ("Subtotal", 600, 900, 720, 930), ("1,000.00", 800, 900, 920, 930),
    ("HST 13%", 600, 950, 710, 980), ("130.00", 830, 950, 920, 980),
    ("Total", 600, 1000, 680, 1030), ("1,130.00", 800, 1000, 920, 1030),
]  # fmt: skip


def _ocr_result() -> list:
    return [[[[x0, y0], [x1, y0], [x1, y1], [x0, y1]], text, 0.98] for text, x0, y0, x1, y1 in UPRIGHT]


def _read_as_scanned(monkeypatch, turn: int) -> layout.PageLayout:
    """The page scanned so that ``turn`` (clockwise) sets it upright, as OCR reports its lines."""
    scanned = layout._turned(_ocr_result(), (360 - turn) % 360, W, H) if turn else _ocr_result()
    size = (W, H) if turn in (0, 180) else (H, W)
    monkeypatch.setattr(layout, "_ocr_cached", lambda *a, **k: scanned)
    png = io.BytesIO()
    Image.new("RGB", size, "white").save(png, format="PNG")
    return layout.ocr_image(png.getvalue(), 1)


@pytest.mark.parametrize("turn", [0, 90, 180, 270])
def test_a_turned_page_is_read_upright(monkeypatch, turn):
    page = _read_as_scanned(monkeypatch, turn)
    assert page.turn == turn
    total = next(w for w in page.words if w.text == "Total")
    amount = next(w for w in page.words if w.text == "1,130.00")
    assert total.box.x1 < amount.box.x0 and abs(total.box.cy - amount.box.cy) < 0.01  # the amount right of its label
    assert total.box.x0 == pytest.approx(600 / W, abs=0.002) and total.box.y0 == pytest.approx(1000 / H, abs=0.002)


def test_an_upright_page_with_few_amounts_is_never_turned(monkeypatch):
    lines = [[[[80, 60 + 40 * i], [600, 60 + 40 * i], [600, 90 + 40 * i], [80, 90 + 40 * i]], f"Line of text {i}", 0.9]
             for i in range(12)]  # fmt: skip
    monkeypatch.setattr(layout, "_ocr_cached", lambda *a, **k: lines)
    png = io.BytesIO()
    Image.new("RGB", (W, H), "white").save(png, format="PNG")
    assert layout.ocr_image(png.getvalue(), 1).turn == 0


def _scanned_pdf(path):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    png = io.BytesIO()
    Image.new("RGB", (850, 1100), "white").save(png, format="PNG")
    page.insert_image(page.rect, stream=png.getvalue())
    doc.save(path)
    return path


def test_a_scanned_pdf_read_turned_is_shown_upright_and_kept_as_it_came(tmp_path):
    pdf = _scanned_pdf(tmp_path / "scan.pdf")
    raw = pdf.read_bytes()
    turned = intake.turn_upright(pdf, {1: 180})
    assert turned is not None and turned.pdf == pdf
    assert turned.original == tmp_path / "originals" / "scan.pdf" and turned.original.read_bytes() == raw
    with pymupdf.open(pdf) as doc:
        assert doc[0].rotation == 180  # only how the page is shown; what it prints is untouched
        # Not a PDF made from a picture: OCR reads the page as shown, never its picture as stored (a scan inset in a
        # margin, or a page with more than its picture on it, would be read out of place).
        assert not intake.made_from_picture(doc)
    assert intake.original_of(pdf) == {"original": "scan.pdf", "sha256": turned.sha256, "kept": "originals/scan.pdf"}
    assert intake.turn_upright(pdf, {1: 180}) is None  # turned again where it is: its original is kept already
    with pymupdf.open(pdf) as doc:
        assert doc[0].rotation == 0
    assert [p.name for p in (tmp_path / "originals").iterdir()] == ["scan.pdf"]


def test_a_scan_that_cannot_be_turned_is_left_as_it_was(tmp_path, monkeypatch):
    import os

    pdf = _scanned_pdf(tmp_path / "scan.pdf")
    raw = pdf.read_bytes()

    def locked(*_a, **_k):
        raise PermissionError(32, "The process cannot access the file because it is being used by another process")

    monkeypatch.setattr(os, "replace", locked)
    with pytest.raises(PermissionError):
        intake.turn_upright(pdf, {1: 180})
    assert pdf.read_bytes() == raw and sorted(p.name for p in tmp_path.iterdir()) == ["scan.pdf"]


def test_a_pdf_made_from_a_picture_is_turned_where_it_is(tmp_path):
    picture = tmp_path / "scan.png"
    Image.new("RGB", (850, 1100), "white").save(picture)
    converted = intake.to_pdf(picture)
    assert intake.turn_upright(converted.pdf, {1: 90}) is None
    with pymupdf.open(converted.pdf) as doc:
        assert doc[0].rotation == 90 and intake.made_from_picture(doc)
    assert sorted(p.name for p in (tmp_path / "originals").iterdir()) == ["scan.png"]  # the picture, kept once


def test_the_pipeline_shows_a_page_scanned_upside_down_upright(tmp_path, monkeypatch):
    import dataclasses as dc

    from ap_coder.config import Settings
    from ap_coder.pipeline import InvoicePipeline
    from ap_coder.store import Store, load_sample_setup

    store = Store(tmp_path / "ap.db")
    load_sample_setup(store)
    scanned = layout._turned(_ocr_result(), 180, W, H)
    monkeypatch.setattr(layout, "ocr_available", lambda: True)
    monkeypatch.setattr(layout, "_ocr_cached", lambda *a, **k: scanned)
    settings = Settings()
    settings = dc.replace(settings, llm=dc.replace(settings.llm, provider="off"))
    pipeline = InvoicePipeline(settings, store.reference_data(), store=store)
    inbox = tmp_path / "invoices"
    inbox.mkdir()
    pdf = _scanned_pdf(inbox / "upside down.pdf")
    raw = pdf.read_bytes()
    result = pipeline.process(pdf)
    assert result.ok and result.source == pdf
    with pymupdf.open(pdf) as doc:
        assert doc[0].rotation == 180
    assert (inbox / "originals" / "upside down.pdf").read_bytes() == raw
    assert result.original["file"] == "upside down.pdf"
    assert store.find_by_hash(inbox / "originals" / "upside down.pdf")["id"] == result.invoice_id  # sent again


INLINE = [  # each total on one OCR line with its amount: the amounts' side says nothing
    ("Lakeshore Plumbing Ltd.", 80, 60, 420, 100), ("INVOICE", 700, 60, 900, 110),
    ("Invoice No: A-1001", 600, 150, 900, 180), ("Date: 2026-10-01", 600, 190, 880, 220),
    ("Description of the work done", 80, 400, 520, 430), ("Technician on site, 8 hours", 80, 450, 500, 480),
    ("Subtotal 1,000.00", 600, 900, 900, 930), ("HST 13% 130.00", 600, 950, 900, 980),
    ("Total 1,130.00", 600, 1000, 900, 1030),
]  # fmt: skip


@pytest.mark.parametrize("turn", [90, 270])
def test_a_sideways_page_whose_labels_share_a_line_with_their_amounts_is_turned_the_right_way(monkeypatch, turn):
    # Sweep: both quarter turns scored the same, and 90 was always taken: a page needing 270 was read upside down.
    upright = [[[[x0, y0], [x1, y0], [x1, y1], [x0, y1]], text, 0.98] for text, x0, y0, x1, y1 in INLINE]
    scanned = layout._turned(upright, 360 - turn, W, H)
    monkeypatch.setattr(layout, "_ocr_cached", lambda *a, **k: scanned)
    png = io.BytesIO()
    Image.new("RGB", (H, W), "white").save(png, format="PNG")
    page = layout.ocr_image(png.getvalue(), 1)
    assert page.turn == turn
    total = next(w for w in page.words if w.text == "Total")
    assert total.box.y0 == pytest.approx(1000 / H, abs=0.002)
