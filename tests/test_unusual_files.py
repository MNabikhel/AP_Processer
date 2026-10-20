"""Unusual invoice files (sweep 4, real-world testing): a file named as another kind, a damaged or empty one, an odd
picture. Each is read, or refused in plain words, never with a traceback, and the file is never lost. Every fixture
is made here."""

from __future__ import annotations

import dataclasses as dc
import io

import pytest
from PIL import Image, ImageDraw

from ap_coder.capture import layout
from ap_coder.core.store import FAILED, Store, load_sample_setup

from .conftest import SAMPLES

SAMPLE = SAMPLES / "northwind_ON_HST_NW-2026-0912.pdf"


def _words_page(number):
    """What the OCR stub reads on a page: a small invoice."""
    words = []
    lines = ["ACME SUPPLY LTD", "INVOICE", "Invoice No: A-1001", "Date: 2026-10-01", "Subtotal: 100.00",
             "GST 5%: 5.00", "Total: 105.00"]  # fmt: skip
    for i, text in enumerate(lines):
        words += layout._split_ocr_line(text, 100, 100 + 40 * i, 100 + 14 * len(text), 128 + 40 * i, 0.99, 1000, 1300,
                                        number)  # fmt: skip
    return layout.PageLayout(number, 1000, 1300, words, layout.group_lines(words, number))


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    """A pipeline reading on this computer with the sample setup, OCR stubbed (a page of a picture reads as a few
    words), and its invoices folder."""
    from ap_coder.ai.pipeline import InvoicePipeline
    from ap_coder.core.config import Settings

    store = Store(tmp_path / "ap.db")
    load_sample_setup(store)
    monkeypatch.setattr(layout, "ocr_available", lambda: True)
    monkeypatch.setattr(layout, "ocr_image", lambda png, number, engine=None: _words_page(number))
    settings = Settings()
    settings = dc.replace(settings, llm=dc.replace(settings.llm, provider="off"))
    folder = tmp_path / "invoices"
    folder.mkdir()
    return store, InvoicePipeline(settings, store.reference_data(), store=store), folder


def _png_bytes(size=(600, 800)) -> bytes:
    img = Image.new("RGB", size, "white")
    ImageDraw.Draw(img).rectangle([50, 60, 250, 120], fill="black")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


# --- B-260: a file named as another kind is read as what it is ------------------------------------------------------


def test_a_pdf_named_as_a_photo_is_read_as_the_pdf_it_is(pipeline):
    store, pipe, folder = pipeline
    misnamed = folder / "scan from vendor.jpg"
    misnamed.write_bytes(SAMPLE.read_bytes())
    result = pipe.process(misnamed)
    assert result.ok, result.error
    assert result.output["vendor_name"] == "Northwind IT Solutions Inc."
    assert result.source == folder / "scan from vendor.pdf" and result.source.read_bytes() == SAMPLE.read_bytes()
    assert not misnamed.exists()  # renamed, not copied: the folder does not offer it again
    assert store.get_invoice(result.invoice_id)["file_name"] == "scan from vendor.pdf"


def test_a_picture_named_as_a_pdf_is_made_into_a_pdf_and_kept(pipeline):
    store, pipe, folder = pipeline
    raw = _png_bytes()
    misnamed = folder / "invoice 77.pdf"
    misnamed.write_bytes(raw)
    result = pipe.process(misnamed)
    assert result.ok, result.error
    assert result.source.read_bytes()[:5] == b"%PDF-"  # a real PDF now: stamped and exported as one
    assert result.original["file"] == "invoice 77.png"
    assert (folder / "originals" / "invoice 77.png").read_bytes() == raw  # the picture as it came
    assert store.find_by_hash(folder / "originals" / "invoice 77.png")["id"] == result.invoice_id


def test_a_web_page_saved_as_a_pdf_is_refused_in_plain_words(pipeline):
    store, pipe, folder = pipeline
    page = folder / "invoice.pdf"
    page.write_bytes(b"\n<!DOCTYPE html><html><head><title>Sign in</title></head><body>Session expired</body></html>")
    result = pipe.process(page)
    assert not result.ok
    assert "invoice.pdf is a web page saved with a .pdf name" in result.error
    assert "download the invoice again" in result.error
    assert page.exists() and store.get_invoice(result.invoice_id)["status"] == FAILED
