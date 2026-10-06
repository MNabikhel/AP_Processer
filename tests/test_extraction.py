from ap_coder.config import DocumentIntelligenceSettings
from ap_coder.extraction import DocumentExtractor, result_from_raw, result_from_text

from .conftest import FakeDIClient

RAW_INVOICE = {
    "modelId": "prebuilt-invoice",
    "content": "Contoso\n<table><tr><td>Laptop</td></tr></table>\n<!-- PageBreak -->\nTotal",
    "pages": [
        {
            "pageNumber": 1,
            "words": [{"content": "Contoso", "confidence": 0.99}, {"content": "Laptop", "confidence": 0.5}],
        },
        {"pageNumber": 2, "words": [{"content": "Total", "confidence": 0.97}]},
    ],
    "tables": [{"rowCount": 1, "columnCount": 1, "cells": []}],
    "documents": [
        {
            "docType": "invoice",
            "fields": {
                "VendorName": {"type": "string", "valueString": "Contoso", "confidence": 0.95},
                "InvoiceDate": {"type": "date", "valueDate": "2026-09-14", "confidence": 0.9},
                "InvoiceTotal": {
                    "type": "currency",
                    "valueCurrency": {"amount": 28494.0, "currencyCode": "GBP"},
                    "confidence": 0.93,
                },
                "Items": {"type": "array", "valueArray": [{}, {}]},
            },
        }
    ],
}


def test_result_from_raw_summarises_quality_and_fields():
    res = result_from_raw("inv.pdf", RAW_INVOICE)
    assert res.page_count == 2
    assert res.table_count == 1
    assert res.low_confidence_word_count == 1
    assert round(res.mean_word_confidence, 3) == round((0.99 + 0.5 + 0.97) / 3, 3)
    assert res.invoice_fields["VendorName"]["value"] == "Contoso"
    assert res.invoice_fields["InvoiceTotal"]["value"] == {"amount": 28494.0, "currency": "GBP"}
    assert res.invoice_fields["ItemCount"]["value"] == 2


def test_prompt_payload_includes_content_and_hints():
    payload = result_from_raw("inv.pdf", RAW_INVOICE).to_prompt_payload()
    assert "<document>" in payload and "<table>" in payload
    assert "Pre-extracted Invoice Fields" in payload
    assert "mean_ocr_word_confidence" in payload


def test_prompt_payload_truncation():
    payload = result_from_raw("inv.pdf", RAW_INVOICE).to_prompt_payload(max_chars=10)
    assert "truncated" in payload


def test_text_input_skips_document_intelligence(sample_markdown_path):
    res = result_from_text(sample_markdown_path)
    assert res.model_id == "pre-extracted"
    assert res.page_count == 2
    assert res.table_count == 3


def test_extractor_calls_di_with_markdown_and_caches(tmp_path):
    pdf = tmp_path / "inv.pdf"
    pdf.write_bytes(b"%PDF-1.7 fake")
    client = FakeDIClient(RAW_INVOICE)
    settings = DocumentIntelligenceSettings(endpoint="https://x", model_id="prebuilt-invoice")
    extractor = DocumentExtractor(settings, client=client, cache_dir=tmp_path / "cache")

    first = extractor.extract(pdf)
    second = extractor.extract(pdf)

    assert len(client.calls) == 1, "second call should hit the cache"
    call = client.calls[0]
    assert call["model_id"] == "prebuilt-invoice"
    assert call["output_content_format"] == "markdown"
    assert call["content_type"] == "application/octet-stream"
    assert first.content == second.content
