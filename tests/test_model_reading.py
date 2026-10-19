"""The chat model reads the header from the page images (Qwen 3.5 can see pages): one more independent reader, asked
in the page reader's pass (in the background), once per file, never shown what the other readers found."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from ap_coder.ai import local_llm
from ap_coder.ai.inference import HEADER_SYSTEM, clean_header
from ap_coder.ai.pipeline import InvoicePipeline
from ap_coder.capture.types import CHECK
from ap_coder.core.store import Store

from .conftest import SAMPLES
from .test_local_llm import REAL_FETCH, FakeLMStudio, chat_reply, local_settings

SAMPLE = SAMPLES / "montroyal_QC_TPS_TVQ_ACMR-2026-1187.pdf"
QWEN = [{"id": "qwen3.5-9b", "type": "vlm", "state": "loaded"}]
TEXT_ONLY = [{"id": "qwen2.5-7b-instruct", "type": "llm", "state": "loaded"}]


@pytest.fixture
def lm_studio(monkeypatch):
    monkeypatch.setattr(local_llm, "_fetch_json", REAL_FETCH)
    servers: list[FakeLMStudio] = []

    def start(v0, reply):
        servers.append(FakeLMStudio([m["id"] for m in v0], v0, reply))
        return servers[-1]

    yield start
    for server in servers:
        server.close()


def _sees_a_page(body) -> bool:
    content = body["messages"][-1]["content"]
    return isinstance(content, list) and any(part.get("type") == "image_url" for part in content)


def _replies(header: dict):
    def reply(body):
        if _sees_a_page(body):
            return chat_reply(json.dumps(header), model="qwen3.5-9b")
        user = body["messages"][-1]["content"]
        numbers = [int(line.split(".")[0]) for line in user.splitlines() if line[:1].isdigit()]
        return chat_reply(json.dumps({"lines": [{"line_number": n, "gl_code": "6400", "cost_center": "", "reason": "x"}
                                                for n in numbers]}))  # fmt: skip

    return reply


def _read_twice(server, reference, tmp):
    """On arrival, then the page reader's pass: (arrival, page reader's pass)."""
    pipe = InvoicePipeline(local_settings(server.base_url), reference, store=Store(Path(tmp) / "ap.db"))
    first = pipe.process(SAMPLE)
    assert first.ok, first.error
    again = pipe.process(first.source, page_text=["MONT-ROYAL"], invoice_id=first.invoice_id)
    assert again.ok, again.error
    return pipe, first, again


def _sample_header(first) -> dict:
    fields = first.capture.fields
    names = ("vendor_name", "invoice_number", "invoice_date", "grand_total", "subtotal", "currency")
    return {name: fields[name].value for name in names if fields.get(name) and fields[name].value}


def test_the_model_reads_the_header_in_the_page_readers_pass_once(lm_studio, reference):
    header: dict = {}
    server = lm_studio(QWEN, _replies(header))
    with tempfile.TemporaryDirectory() as tmp:
        pipe = InvoicePipeline(local_settings(server.base_url), reference, store=Store(Path(tmp) / "ap.db"))
        first = pipe.process(SAMPLE)
        assert first.ok, first.error
        assert not any(_sees_a_page(chat) for chat in server.chats)  # on arrival: never (uploading stays quick)
        header.update(_sample_header(first))
        again = pipe.process(first.source, page_text=["MONT-ROYAL"], invoice_id=first.invoice_id)
        assert again.ok, again.error
        asked = [chat for chat in server.chats if _sees_a_page(chat)]
        assert len(asked) == 1
        assert asked[0]["messages"][0]["content"] == HEADER_SYSTEM
        sent = json.dumps(asked[0])
        assert str(header["grand_total"]) not in sent  # never shown what the other readers found
        total = again.capture.fields["grand_total"]
        assert any("ai" in reason for reason in total.reasons), total.reasons  # it is one of the readers now
        pipe.process(first.source, page_text=["MONT-ROYAL"], invoice_id=first.invoice_id)
        assert len([chat for chat in server.chats if _sees_a_page(chat)]) == 1  # asked once per file and model


def test_a_field_the_model_reads_differently_goes_to_a_person(lm_studio, reference):
    header: dict = {}
    server = lm_studio(QWEN, _replies(header))
    with tempfile.TemporaryDirectory() as tmp:
        pipe = InvoicePipeline(local_settings(server.base_url), reference, store=Store(Path(tmp) / "ap.db"))
        first = pipe.process(SAMPLE)
        header.update(_sample_header(first))
        header["invoice_number"] = "ACMR-2026-1781"  # digits swapped
        again = pipe.process(first.source, page_text=["MONT-ROYAL"], invoice_id=first.invoice_id)
    number = again.capture.fields["invoice_number"]
    assert number.status == CHECK, (number.status, number.reasons)


def test_a_model_that_cant_see_pages_is_never_asked_to_read_them(lm_studio, reference):
    server = lm_studio(TEXT_ONLY, _replies({}))
    with tempfile.TemporaryDirectory() as tmp:
        _read_twice(server, reference, tmp)
    assert not any(_sees_a_page(chat) for chat in server.chats)


def test_the_models_answer_is_kept_to_the_header_as_capture_reads_it():
    assert clean_header({
        "vendor_name": " Montroyal Inc. ", "invoice_number": "ACMR-1187", "invoice_date": "2026-09-14",
        "due_date": "14/10/2026", "currency": "cad", "grand_total": "$21,578.51", "subtotal": 18768.0,
        "hst_amount": "(130.00)", "gst_amount": "938.40 CR", "qst_amount": None, "po_number": "", "other": "x",
        "pst_amount": True, "tax_total": "n/a",
    }) == {
        "vendor_name": "Montroyal Inc.", "invoice_number": "ACMR-1187", "invoice_date": "2026-09-14",
        "currency": "CAD", "grand_total": 21578.51, "subtotal": 18768.0, "hst_amount": -130.0, "gst_amount": -938.4,
    }  # fmt: skip


def test_amounts_the_model_writes_as_text_are_read_as_printed():
    # Sweep: "1 234,56" became 123456.00, "1.234,56" 1.23, "-12.50 CR" +12.50, and "nan" / "Infinity" were kept.
    got = clean_header({"grand_total": "1 234,56 $", "subtotal": "1.234,56", "gst_amount": "-12.50 CR",
                        "hst_amount": "nan", "qst_amount": "Infinity", "pst_amount": float("inf")})  # fmt: skip
    assert got == {"grand_total": 1234.56, "subtotal": 1234.56, "gst_amount": -12.5}
