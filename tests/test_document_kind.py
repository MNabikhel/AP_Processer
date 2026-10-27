"""A vendor's own PDF (its text inside) and a scan or photo (read with OCR) are told apart: a tag on the review screen
and the queue, and the Learning page's Readers tab scores the two apart."""

import shutil
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from ap_coder.capture.types import CaptureResult
from ap_coder.core.config import Settings
from ap_coder.core.store import load_sample_setup
from ap_coder.web.app import create_app
from ap_coder.web.invoice_view import document_kind

from .conftest import SAMPLE_STEM, SAMPLES


@pytest.fixture
def client(store, monkeypatch):
    monkeypatch.setenv("AP_DEVELOPER_TOOLS", "1")  # the Readers tab is a developer tool (B-511)
    s = Settings()
    return TestClient(create_app(replace(s, llm=replace(s.llm, provider="off")), store), base_url="http://localhost")


def _invoice(store, tmp_path, ground_truth, layout_source, name):
    pdf = tmp_path / "in" / f"{name}.pdf"
    pdf.parent.mkdir(exist_ok=True)
    shutil.copy(SAMPLES / f"{SAMPLE_STEM}.pdf", pdf)
    invoice_id = store.add_invoice(pdf, {**ground_truth, "vendor_name": name}, {"requires_review": True})
    store.save_capture(invoice_id, CaptureResult({}, layout_source=layout_source, page_count=1).to_dict())
    return invoice_id


def test_document_kind_by_how_the_pages_were_read():
    assert document_kind("text")["label"] == "Vendor PDF"
    assert document_kind("ocr")["label"] == document_kind("di")["label"] == "Scan or photo"
    assert document_kind("mixed")["label"] == "Partly scanned"
    assert document_kind("ocr")["tone"] == "warn" and document_kind("text")["tone"] == "ok"
    assert document_kind("") is None and document_kind("none") is None


def test_layout_sources_in_one_query(store, tmp_path, ground_truth):
    a = _invoice(store, tmp_path, ground_truth, "text", "Acme Paper")
    b = _invoice(store, tmp_path, ground_truth, "ocr", "Brick Supply")
    assert store.layout_sources([a, b, 999]) == {a: "text", b: "ocr"}
    assert store.layout_sources([]) == {}


def test_review_screen_and_queue_tag_the_kind(client, store, tmp_path, ground_truth):
    load_sample_setup(store)  # past the getting-started screen, to the queue
    pdf = _invoice(store, tmp_path, ground_truth, "text", "Acme Paper")
    scan = _invoice(store, tmp_path, ground_truth, "ocr", "Brick Supply")
    assert "Vendor PDF" in client.get(f"/review/{pdf}").text
    page = client.get(f"/review/{scan}").text
    assert "Scan or photo" in page and "read with OCR" in page
    queue = client.get("/review").text
    # Only the scan is tagged on the queue: vendor PDFs are the usual case.
    assert queue.count("Scan or photo") == 1 and "Vendor PDF" not in queue


def test_readers_tab_scores_vendor_pdfs_and_scans_apart(client, store, tmp_path, ground_truth):
    a = _invoice(store, tmp_path, ground_truth, "text", "Acme Paper")
    b = _invoice(store, tmp_path, ground_truth, "ocr", "Brick Supply")
    row = {"reader": "fused", "field": "grand_total", "read_value": 1.0, "final_value": 1.0, "status": "verified"}
    store.record_reader_outcomes(
        a,
        [
            {**row, "correct": True, "layout_source": "text"},
            {**row, "field": "invoice_number", "correct": True, "layout_source": "text"},
        ],
    )
    store.record_reader_outcomes(
        b,
        [
            {**row, "correct": True, "layout_source": "ocr"},
            {**row, "field": "invoice_number", "correct": False, "layout_source": "ocr"},
        ],
    )
    kinds = store.fused_kind_scorecard()
    assert kinds["text"] == {"fields": 2, "agreed": 2, "rate": 1.0, "invoices": 1}
    assert kinds["scan"] == {"fields": 2, "agreed": 1, "rate": 0.5, "invoices": 1}
    page = client.get("/learning?tab=readers").text
    assert "Vendor PDFs read right" in page and "100.0%" in page
    assert "Scans and photos read right" in page and "50.0%" in page
