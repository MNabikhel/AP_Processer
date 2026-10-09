"""The invoice page viewer's Python side: page pictures, words, and the arguments sent to the component."""

from __future__ import annotations

import base64
import datetime as dt
import io
import os
import re
import shutil
from pathlib import Path

import pytest
from PIL import Image

from ap_coder.capture.types import FIELDS, Box, FieldResult, LineReading, Word
from ap_coder.webapp import viewer

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10: the same parser as tomli, or pip's own copy of it
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        from pip._vendor import tomli as tomllib
from ap_coder.webapp.viewer import (
    COMPONENT_DIR,
    FIELD_LABELS,
    FIELD_ORDER,
    page_words,
    render_pages,
    shape_fields,
    viewer_args,
)

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "samples" / "northwind_ON_HST_NW-2026-0912.pdf"


def png_size(src: str) -> tuple[int, int]:
    assert src.startswith("data:image/png;base64,")
    with Image.open(io.BytesIO(base64.b64decode(src.split(",", 1)[1]))) as img:
        assert img.format == "PNG"
        return img.size


@pytest.fixture(autouse=True)
def fresh_cache():
    viewer._render_cached.cache_clear()
    yield
    viewer._render_cached.cache_clear()


# --- render_pages ---------------------------------------------------------------------------------------------


def test_render_pdf_pages_sizes_in_points_and_pixels():
    pages = render_pages(SAMPLE)
    assert len(pages) == 2
    for page in pages:
        assert set(page) == {"src", "width", "height"}
        assert (page["width"], page["height"]) == (612, 792)  # US letter in points, whatever the dpi
        assert png_size(page["src"]) == (935, 1210)  # 8.5 x 11 in at the default 110 dpi


def test_render_pdf_honours_dpi_max_pages_and_the_pixel_cap():
    assert png_size(render_pages(SAMPLE, dpi=72)[0]["src"]) == (612, 792)
    assert len(render_pages(SAMPLE, max_pages=1)) == 1
    big = render_pages(SAMPLE, dpi=1200, max_pages=1)[0]
    assert max(png_size(big["src"])) <= viewer.MAX_SIDE
    assert (big["width"], big["height"]) == (612, 792)  # the printed size does not change with the cap


def test_render_pages_is_cached_and_returns_copies(tmp_path):
    pdf = tmp_path / "inv.pdf"
    shutil.copy(SAMPLE, pdf)
    first = render_pages(pdf)
    first[0]["src"] = "changed by the caller"
    second = render_pages(pdf)
    info = viewer._render_cached.cache_info()
    assert (info.hits, info.misses) == (1, 1)
    assert second[0]["src"].startswith("data:image/png")
    # A file changed on disk is drawn again.
    stat = pdf.stat()
    os.utime(pdf, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    render_pages(pdf)
    assert viewer._render_cached.cache_info().misses == 2
    # So is another dpi.
    render_pages(pdf, dpi=90)
    assert viewer._render_cached.cache_info().misses == 3


def test_render_image_uses_its_dpi_and_paper_white(tmp_path):
    path = tmp_path / "scan.png"
    Image.new("RGBA", (1700, 2200), (0, 0, 0, 0)).save(path, dpi=(200, 200))
    (page,) = render_pages(path)
    assert (page["width"], page["height"]) == (612, 792)
    assert png_size(page["src"]) == (935, 1210)  # brought down to 110 dpi
    with Image.open(io.BytesIO(base64.b64decode(page["src"].split(",", 1)[1]))) as img:
        assert img.mode == "RGB" and img.getpixel((5, 5)) == (255, 255, 255)  # transparent becomes white paper


def test_render_image_without_dpi_and_multi_frame_tiff(tmp_path):
    path = tmp_path / "scan.tif"
    frames = [Image.new("L", (850, 1100), 255) for _ in range(3)]
    frames[0].save(path, save_all=True, append_images=frames[1:])
    pages = render_pages(path, max_pages=2)
    assert len(pages) == 2
    # No dpi recorded: taken as a letter-width page (850 px over 8.5 in is 100 dpi)...
    assert (pages[0]["width"], pages[0]["height"]) == (612, 792)
    assert png_size(pages[0]["src"]) == (850, 1100)
    # ... but never below screen resolution, so a small picture is not blown up to a page.
    small = tmp_path / "small.png"
    Image.new("RGB", (480, 300), "white").save(small)
    (page,) = render_pages(small)
    assert (page["width"], page["height"]) == (360, 225)


def test_render_unknown_and_missing_files(tmp_path):
    other = tmp_path / "notes.txt"
    other.write_text("not an invoice")
    assert render_pages(other) == []
    with pytest.raises(FileNotFoundError):
        render_pages(tmp_path / "gone.pdf")


# --- words ----------------------------------------------------------------------------------------------------


def test_page_words_are_fractions_of_the_page():
    words = page_words(SAMPLE)
    assert len(words) == 2
    found = [w for w in words[0] if w["t"] == "NW-2026-0912"]
    assert len(found) == 1
    x0, y0, x1, y1 = found[0]["b"]
    assert 0 < x0 < x1 < 1 and 0 < y0 < y1 < 1
    assert x0 == pytest.approx(98 / 612, abs=0.01) and y0 == pytest.approx(122 / 792, abs=0.01)
    assert page_words(ROOT / "README.md") == []


# --- arguments ------------------------------------------------------------------------------------------------


def test_field_order_covers_every_capture_field():
    assert sorted(FIELD_ORDER) == sorted(FIELDS)
    assert set(FIELD_LABELS) == set(FIELDS)


def test_shape_fields_orders_like_an_invoice_and_fills_labels():
    total = FieldResult("grand_total", 18017.85, 0.42, "check", [Box(2, 0.8, 0.22, 0.9, 0.24)],
                        {"text": 18017.85, "ai": 18071.85}, ["total does not add up"]).to_dict()  # fmt: skip
    total["failed"] = True
    rows = shape_fields([
        total,
        {"field": "custom_ref", "value": "X1", "status": "likely", "confidence": 0.8},
        FieldResult("vendor_name", "Northwind", 0.99, "verified", [Box(1, 0.1, 0.05, 0.4, 0.07)]),
        {"field": "invoice_number", "value": "NW-1", "label": "Invoice #", "display": "NW-1", "status": "verified",
         "confidence": 0.999, "boxes": [[1, 0.16, 0.15, 0.26, 0.17]]},
    ])  # fmt: skip
    assert [r["field"] for r in rows] == ["vendor_name", "invoice_number", "grand_total", "custom_ref"]
    by = {r["field"]: r for r in rows}
    assert by["vendor_name"]["label"] == "Supplier" and by["vendor_name"]["display"] == "Northwind"
    assert by["invoice_number"]["label"] == "Invoice #"
    assert by["grand_total"]["label"] == "Total"
    assert by["grand_total"]["display"] == "18,017.85"
    assert by["grand_total"]["failed"] is True and by["vendor_name"]["failed"] is False
    assert by["grand_total"]["boxes"] == [[2, 0.8, 0.22, 0.9, 0.24]]
    assert by["custom_ref"]["label"] == "Custom ref"


def test_shape_fields_cleans_boxes_status_and_sources():
    rows = shape_fields({
        "po_number": {"field": "po_number", "value": "PO-1", "status": "check", "confidence": 1.7,
                      "boxes": [[1, 0.5, 0.2, 0.4, 0.1], [0, 0.1, 0.1, 0.2, 0.2], [1, 0.3, 0.3, 0.3, 0.4], "bad",
                                [1, -0.2, 0.1, 1.4, 0.2]],
                      "sources": {"rules": "PO-1", "di": {"value": "PO-1", "conf": float("nan")},
                                  "ai": dt.date(2026, 9, 14)}},
        "qst_registration_number": {"field": "qst_registration_number", "value": None, "status": "verified",
                                    "boxes": []},
        "due_date": {"field": "due_date", "value": "2026-10-14", "status": "nonsense",
                     "boxes": [[1, 0.1, 0.1, 0.2, 0.2]]},
    })  # fmt: skip
    by = {r["field"]: r for r in rows}
    po = by["po_number"]
    assert po["boxes"] == [[1, 0.4, 0.1, 0.5, 0.2], [1, 0.0, 0.1, 1.0, 0.2]]  # sorted corners, clamped, bad dropped
    assert po["confidence"] == 1.0
    assert po["sources"] == {"rules": "PO-1", "di": {"value": "PO-1", "conf": None}, "ai": "2026-09-14"}
    assert by["qst_registration_number"]["status"] == "missing"  # nothing read, nothing to verify
    assert by["qst_registration_number"]["display"] == ""
    assert by["due_date"]["status"] == "missing" and by["due_date"]["boxes"] == []


def test_viewer_args_shapes_words_line_items_and_options():
    pages = [{"src": "data:image/png;base64,AAAA", "width": 612, "height": 792}]
    args = viewer_args(
        pages, [],
        words=[[{"t": "Total", "b": [0.5, 0.5, 0.6, 0.52]}, {"t": " ", "b": [0, 0, 0.1, 0.1]}, {"t": "x"}],
               [Word("HST", Box(2, 0.1, 0.2, 0.15, 0.22))]],
        line_items=[{"label": "Laptops", "boxes": [[1, 0.08, 0.3, 0.9, 0.32]]},
                    LineReading("Server", 1, 8900.0, 8900.0,
                                [Box(1, 0.08, 0.33, 0.9, 0.35), Box(1, 0.8, 0.33, 0.9, 0.35)]),
                    {"label": "No box", "boxes": []}],
        selected="", teach="po_number", height=700,
    )  # fmt: skip
    assert args["pages"] == [{"src": "data:image/png;base64,AAAA", "width": 612.0, "height": 792.0}]
    assert args["words"] == [[{"t": "Total", "b": [0.5, 0.5, 0.6, 0.52]}], [{"t": "HST", "b": [0.1, 0.2, 0.15, 0.22]}]]
    assert args["line_items"] == [
        {"label": "Laptops", "boxes": [[1, 0.08, 0.3, 0.9, 0.32]]},
        {"label": "Server", "boxes": [[1, 0.08, 0.33, 0.9, 0.35]]},  # the row box only
    ]
    assert args["selected"] is None and args["teach"] == "po_number" and args["height"] == 700
    assert viewer_args(pages, [])["height"] is None


def test_viewer_args_refuses_what_the_page_cannot_show():
    with pytest.raises(ValueError, match="data:image"):
        viewer_args([{"src": "https://example.com/page.png", "width": 1, "height": 1}], [])
    with pytest.raises(ValueError, match="height"):
        viewer_args([], [], height=100)


def test_invoice_viewer_marks_each_event_new_once(monkeypatch):
    import streamlit as st

    sent = {}
    event = {"type": "select", "field": "po_number", "seq": 1760000000000}

    def fake_component(**kwargs):
        sent.update(kwargs)
        return event

    monkeypatch.setattr(viewer, "_component", lambda: fake_component)
    pages = render_pages(SAMPLE, max_pages=1)
    first = viewer.invoice_viewer(pages, [], key="t1")
    again = viewer.invoice_viewer(pages, [], key="t1")
    assert first == {**event, "new": True} and again["new"] is False
    assert sent["key"] == "t1" and sent["default"] is None and sent["pages"][0]["width"] == 612
    event["seq"] += 1
    assert viewer.invoice_viewer(pages, [], key="t1")["new"] is True
    monkeypatch.setattr(viewer, "_component", lambda: lambda **kw: {"type": "something else"})
    assert viewer.invoice_viewer(pages, [], key="t1") is None
    st.session_state.clear()


# --- the component's files ------------------------------------------------------------------------------------


def test_component_files_are_self_contained():
    names = {p.name for p in COMPONENT_DIR.iterdir()}
    assert {"index.html", "viewer.js", "viewer.css", "InterVariable.woff2"} <= names
    html = (COMPONENT_DIR / "index.html").read_text()
    assert 'src="viewer.js"' in html and 'href="viewer.css"' in html
    for name in ("index.html", "viewer.js", "viewer.css"):
        text = (COMPONENT_DIR / name).read_text()
        # Nothing is fetched from the internet (the SVG namespace is a name, not a download).
        assert not re.search(r"""(src=|href=|url\(|import\s|fetch\()\s*["']?(https?:)?//""", text), name
    js = (COMPONENT_DIR / "viewer.js").read_text()
    for message in ("streamlit:render", "streamlit:componentReady", "streamlit:setFrameHeight",
                    "streamlit:setComponentValue"):  # fmt: skip
        assert message in js


def test_package_data_ships_the_component():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    patterns = config["tool"]["setuptools"]["package-data"]["ap_coder"]
    assert "webapp/components/invoice_viewer/*" in patterns
    shipped = {p.name for pattern in patterns for p in (ROOT / "ap_coder").glob(pattern)}
    assert {"index.html", "viewer.js", "viewer.css"} <= shipped
