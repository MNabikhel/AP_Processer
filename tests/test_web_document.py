"""The invoice's pages on the review screen (ap_coder/web/document.py): their printed size, the PNG of each page, the
words to teach a field from, and the field list and boxes the page viewer is given."""

from __future__ import annotations

import datetime as dt
import io
import os
import re
import shutil
from pathlib import Path

import pytest
from PIL import Image

from ap_coder.capture.confidence import LABELS
from ap_coder.capture.types import FIELDS, Box, FieldResult, LineReading
from ap_coder.web import document
from ap_coder.web.core import STATIC_DIR
from ap_coder.web.document import FIELD_ORDER, page_png, page_sizes, page_words, shape_fields

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "samples" / "northwind_ON_HST_NW-2026-0912.pdf"


def png_size(data: bytes) -> tuple[int, int]:
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(io.BytesIO(data)) as img:
        return img.size


@pytest.fixture(autouse=True)
def fresh_cache():
    for cached in (document._png_cached, document._sizes_cached):
        cached.cache_clear()
    yield
    for cached in (document._png_cached, document._sizes_cached):
        cached.cache_clear()


# --- Pages: printed size and picture ---------------------------------------------------------------------------------


def test_pdf_pages_sizes_in_points_and_pixels():
    assert page_sizes(SAMPLE) == [(612, 792), (612, 792)]  # US letter in points, whatever the resolution
    for number in (1, 2):
        assert png_size(page_png(SAMPLE, number)) == (1020, 1320)  # 8.5 x 11 in at 120 dpi
    with pytest.raises(IndexError):
        page_png(SAMPLE, 3)
    with pytest.raises(IndexError):
        page_png(SAMPLE, 0)


def test_a_page_is_never_drawn_bigger_than_the_cap(monkeypatch):
    monkeypatch.setattr(document, "DPI", 1200)
    assert max(png_size(page_png(SAMPLE, 1))) <= document.MAX_SIDE
    assert page_sizes(SAMPLE)[0] == (612, 792)  # the printed size does not change with the cap


def test_pictures_are_cached_until_the_file_changes(tmp_path):
    pdf = tmp_path / "inv.pdf"
    shutil.copy(SAMPLE, pdf)
    first = page_png(pdf, 1)
    assert page_png(pdf, 1) == first
    info = document._png_cached.cache_info()
    assert (info.hits, info.misses) == (1, 1)
    version = document.version(pdf)
    stat = pdf.stat()
    os.utime(pdf, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))  # changed on disk: drawn again
    page_png(pdf, 1)
    assert document._png_cached.cache_info().misses == 2
    assert document.version(pdf) != version  # and the picture's address changes, so no browser shows the old one
    assert document.version(tmp_path / "gone.pdf") == "0"


def test_an_image_uses_its_dpi_and_paper_white(tmp_path):
    path = tmp_path / "scan.png"
    Image.new("RGBA", (1700, 2200), (0, 0, 0, 0)).save(path, dpi=(200, 200))
    assert page_sizes(path) == [(612, 792)]
    png = page_png(path, 1)
    assert png_size(png) == (1020, 1320)  # brought down to 120 dpi
    with Image.open(io.BytesIO(png)) as img:
        assert img.mode == "RGB" and img.getpixel((5, 5)) == (255, 255, 255)  # transparent becomes white paper


def test_an_image_without_dpi_and_a_multi_frame_tiff(tmp_path):
    path = tmp_path / "scan.tif"
    frames = [Image.new("L", (850, 1100), 255) for _ in range(3)]
    frames[0].save(path, save_all=True, append_images=frames[1:])
    # No dpi recorded: taken as a letter-width page (850 px over 8.5 in is 100 dpi)...
    assert page_sizes(path) == [(612, 792)] * 3
    assert png_size(page_png(path, 3)) == (850, 1100)
    # ... but never below screen resolution, so a small picture is not blown up to a page.
    small = tmp_path / "small.png"
    Image.new("RGB", (480, 300), "white").save(small)
    assert page_sizes(small) == [(360, 225)]


def test_unknown_and_missing_files(tmp_path):
    other = tmp_path / "notes.txt"
    other.write_text("not an invoice")
    assert page_sizes(other) == []
    with pytest.raises(IndexError):
        page_png(other, 1)
    with pytest.raises(FileNotFoundError):
        page_sizes(tmp_path / "gone.pdf")


# --- Words ------------------------------------------------------------------------------------------------------------


def test_page_words_are_fractions_of_the_page():
    words = page_words(SAMPLE)
    assert len(words) == 2
    found = [w for w in words[0] if w["t"] == "NW-2026-0912"]
    assert len(found) == 1
    x0, y0, x1, y1 = found[0]["b"]
    assert 0 < x0 < x1 < 1 and 0 < y0 < y1 < 1
    assert x0 == pytest.approx(98 / 612, abs=0.01) and y0 == pytest.approx(122 / 792, abs=0.01)


def test_words_are_cleaned_for_the_viewer():
    words = document.shape_words([[{"t": "Total", "b": [0.5, 0.5, 0.6, 0.52]}, {"t": " ", "b": [0, 0, 0.1, 0.1]},
                                   {"t": "x"}, {"t": "Out", "b": [-0.2, 0.1, 1.4, 0.2]}]])  # fmt: skip
    assert words == [[{"t": "Total", "b": [0.5, 0.5, 0.6, 0.52]}, {"t": "Out", "b": [0.0, 0.1, 1.0, 0.2]}]]


# --- The field list and the boxes ------------------------------------------------------------------------------------


def test_field_order_covers_every_capture_field():
    assert sorted(FIELD_ORDER) == sorted(FIELDS)
    assert set(FIELDS) <= set(LABELS)


def test_shape_fields_orders_like_an_invoice_and_fills_labels():
    total = FieldResult("grand_total", 18017.85, 0.42, "check", [Box(2, 0.8, 0.22, 0.9, 0.24)],
                        {"text": 18017.85, "ai": 18071.85}, ["check failed: total does not add up"])  # fmt: skip
    rows = shape_fields([
        total,
        {"field": "custom_ref", "value": "X1", "status": "likely", "confidence": 0.8},
        FieldResult("vendor_name", "Northwind", 0.99, "verified", [Box(1, 0.1, 0.05, 0.4, 0.07)]),
        {"field": "invoice_number", "value": "NW-1", "status": "verified", "confidence": 0.999,
         "boxes": [[1, 0.16, 0.15, 0.26, 0.17]]},
    ], LABELS)  # fmt: skip
    assert [r["field"] for r in rows] == ["vendor_name", "invoice_number", "grand_total", "custom_ref"]
    by = {r["field"]: r for r in rows}
    assert by["vendor_name"]["label"] == "Supplier" and by["vendor_name"]["display"] == "Northwind"
    assert by["invoice_number"]["label"] == LABELS["invoice_number"]
    assert by["grand_total"]["display"] == "18,017.85"
    assert by["grand_total"]["failed"] is True and by["vendor_name"]["failed"] is False
    assert by["grand_total"]["boxes"] == [[2, 0.8, 0.22, 0.9, 0.24]]
    assert by["custom_ref"]["label"] == "Custom ref"


def test_shape_fields_cleans_boxes_status_and_sources():
    rows = shape_fields([
        {"field": "po_number", "value": "PO-1", "status": "check", "confidence": 1.7,
         "boxes": [[1, 0.5, 0.2, 0.4, 0.1], [0, 0.1, 0.1, 0.2, 0.2], [1, 0.3, 0.3, 0.3, 0.4], "bad",
                   [1, -0.2, 0.1, 1.4, 0.2]],
         "sources": {"rules": "PO-1", "di": {"value": "PO-1", "conf": float("nan")}, "ai": dt.date(2026, 9, 14)}},
        {"field": "qst_registration_number", "value": None, "status": "verified", "boxes": []},
        {"field": "due_date", "value": "2026-10-14", "status": "nonsense", "boxes": [[1, 0.1, 0.1, 0.2, 0.2]]},
    ], LABELS)  # fmt: skip
    by = {r["field"]: r for r in rows}
    po = by["po_number"]
    assert po["boxes"] == [[1, 0.4, 0.1, 0.5, 0.2], [1, 0.0, 0.1, 1.0, 0.2]]  # sorted corners, clamped, bad dropped
    assert po["confidence"] == 1.0
    assert po["sources"] == {"rules": "PO-1", "di": {"value": "PO-1", "conf": None}, "ai": "2026-09-14"}
    assert by["qst_registration_number"]["status"] == "missing"  # nothing read, nothing to verify
    assert by["qst_registration_number"]["display"] == ""
    assert by["due_date"]["status"] == "missing" and by["due_date"]["boxes"] == []


def test_line_items_keep_their_row_boxes():
    items = [LineReading("Server", 1, 8900.0, 8900.0, [Box(1, 0.08, 0.33, 0.9, 0.35), Box(0, 0.8, 0.33, 0.9, 0.35)]),
             LineReading("No box", 1, 1.0, 1.0, [])]  # fmt: skip
    assert document.shape_line_items(items) == [{"label": "Server", "boxes": [[1, 0.08, 0.33, 0.9, 0.35]]}]


# --- The viewer's own files -------------------------------------------------------------------------------------------


def test_the_viewer_loads_nothing_from_the_internet():
    for path in [*STATIC_DIR.glob("*.css"), *STATIC_DIR.glob("js/*.js")]:
        text = path.read_text(encoding="utf-8")
        # The SVG namespace is a name, not a download.
        assert not re.search(r"""(src=|href=|url\(|import\s|fetch\()\s*["']?(https?:)?//""", text), path.name
    assert (STATIC_DIR / "js" / "invoice_viewer.js").is_file() and (STATIC_DIR / "invoice_viewer.css").is_file()
