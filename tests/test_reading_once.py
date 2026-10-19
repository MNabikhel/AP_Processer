"""A scanned page is OCR'd once by each engine, however often its invoice is read (on arrival, again with its
supplier's template, again once the page reader has read it), and a file's tables are looked for once."""

import io
from dataclasses import replace

import pymupdf
import pytest
from PIL import Image, ImageDraw

from ap_coder.ai.pipeline import InvoicePipeline
from ap_coder.capture import layout, table_finder
from ap_coder.capture.types import Box
from ap_coder.core.config import Settings
from ap_coder.core.store import Store

LINES = [
    ([[20, 20], [300, 20], [300, 40], [20, 40]], "Northwind Supplies Ltd.", 0.98),
    ([[20, 60], [300, 60], [300, 80], [20, 80]], "Invoice No: NW-2026-0912", 0.97),
    ([[20, 100], [300, 100], [300, 120], [20, 120]], "Total due: $1,130.00", 0.96),
]


def _png(mark: int = 0) -> bytes:
    img = Image.new("RGB", (400, 300), "white")
    ImageDraw.Draw(img).text((20, 20 + mark), f"Invoice {mark}", fill="black")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def engine(monkeypatch):
    """A fake OCR engine: every run is recorded with the engine's name."""
    runs: list[str] = []

    def make(name=None):
        def run(img):
            runs.append(name or layout._engine_name())
            return [(list(map(list, quad)), text, score) for quad, text, score in LINES]

        return run

    monkeypatch.setattr(layout, "_engine", make)
    monkeypatch.delenv("AP_OCR_CACHE", raising=False)
    return runs


def test_outside_the_cache_every_read_runs_the_engine(engine):
    png = _png()
    layout.ocr_image(png, 1, "rapidocr")
    layout.ocr_image(png, 1, "rapidocr")
    assert engine == ["rapidocr", "rapidocr"]


def test_inside_the_cache_a_page_is_ocrd_once_by_each_engine(engine, tmp_path):
    png = _png()
    with layout.ocr_cache(tmp_path / "ocr"):
        first = layout.ocr_image(png, 1, "rapidocr")
        again = layout.ocr_image(png, 1, "rapidocr")
        layout.ocr_image(png, 1, "ppocrv5")  # another engine: its own reading
        layout.ocr_image(_png(1), 1, "rapidocr")  # another page
    assert engine == ["rapidocr", "ppocrv5", "rapidocr"]
    assert [w.text for w in again.words] == [w.text for w in first.words] and first.words
    assert [line.text for line in again.lines] == [line.text for line in first.lines]
    assert len(list((tmp_path / "ocr").glob("*.json"))) == 3
    assert not list((tmp_path / "ocr").glob("*.part"))  # each reading written whole, then put in place


def test_a_damaged_reading_is_read_again(engine, tmp_path):
    png = _png()
    with layout.ocr_cache(tmp_path):
        layout.ocr_image(png, 1, "rapidocr")
        (cached,) = tmp_path.glob("*.json")
        cached.write_text("{not json", encoding="utf-8")
        page = layout.ocr_image(png, 1, "rapidocr")
    assert engine == ["rapidocr", "rapidocr"] and page.words


def test_the_cache_keeps_the_newest_readings(engine, tmp_path, monkeypatch):
    import os
    import time

    monkeypatch.setattr(layout, "OCR_CACHE_MAX_FILES", 2)
    with layout.ocr_cache(tmp_path):
        for mark in range(4):
            layout.ocr_image(_png(mark), 1, "rapidocr")
    files = sorted(tmp_path.glob("*.json"))
    for age, path in enumerate(files):
        os.utime(path, (time.time() - 1000 * (age + 1),) * 2)  # files[0] is the newest
    layout._pruned.clear()
    layout._prune(tmp_path)
    assert sorted(tmp_path.glob("*.json")) == files[:2]


def _scan(path):
    """A scanned invoice: one page that is only a picture."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_image(page.rect, stream=_png())
    doc.save(path)
    return path


def test_the_pipeline_ocrs_a_scan_once_when_it_reads_it_again(engine, tmp_path, monkeypatch, reference):
    monkeypatch.setattr(layout, "ocr_available", lambda: True)
    s = Settings()
    store = Store(tmp_path / "ap.db")
    pipeline = InvoicePipeline(replace(s, llm=replace(s.llm, provider="off")), reference, store=store)
    scan = _scan(tmp_path / "scan.pdf")
    first = pipeline.process(scan)
    on_arrival = len(engine)
    assert first.error is None and on_arrival >= 1
    again = pipeline.process(first.source, page_text=["Northwind Supplies Ltd."], invoice_id=first.invoice_id)
    assert again.error is None
    assert len(engine) == on_arrival  # read again (the page reader's pass): no page OCR'd a second time
    folder = layout._ocr_cache_folder.get()
    assert folder is None  # the cache is on only while the pipeline reads
    assert list((tmp_path / "private" / ".cache" / "ocr").glob("*.json"))


def test_a_files_tables_are_looked_for_once(monkeypatch, tmp_path):
    seen: list[int] = []
    monkeypatch.setattr(table_finder, "available", lambda: True)
    monkeypatch.setattr(table_finder, "page_pictures", lambda path, max_pages: [b"page 1", b"page 2"])
    monkeypatch.setattr(
        table_finder, "find", lambda png, number: seen.append(number) or [Box(number, 0.1, 0.4, 0.9, 0.7)]
    )
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF one invoice")
    first = table_finder.find_tables(pdf)
    first[1].append(Box(1, 0, 0, 1, 1))  # what a caller does with its answer doesn't change the next one
    again = table_finder.find_tables(pdf)
    assert seen == [1, 2] and again == {1: [Box(1, 0.1, 0.4, 0.9, 0.7)], 2: [Box(2, 0.1, 0.4, 0.9, 0.7)]}
    other = tmp_path / "b.pdf"
    other.write_bytes(b"%PDF another invoice")
    table_finder.find_tables(other)
    assert seen == [1, 2, 1, 2]


def test_a_pages_image_is_made_once_for_both_engines(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(layout, "ocr_available", lambda: True)
    made = []
    real = layout._make_scan_png
    monkeypatch.setattr(layout, "_make_scan_png", lambda doc, page: made.append(page.number) or real(doc, page))
    scan = _scan(tmp_path / "scan.pdf")
    layout.build_layout(scan)
    layout.second_read_layout(scan)
    assert made == [0]  # about a second a page, saved for the second engine
    scan.write_bytes(scan.read_bytes() + b"\n")  # the file changed: its page is made again
    layout.build_layout(scan)
    assert made == [0, 0]
