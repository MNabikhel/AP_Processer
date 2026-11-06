"""The chat model on an invoice's arrival (D-168): on the company server it waits at most ARRIVAL_SECONDS, a chat
slot included; a hung or slow model holds up no import (a breaker stops asking after two timeouts in a row), and the
page reader's pass asks again. A model that answers in time codes as before."""

from __future__ import annotations

import json
import time
from dataclasses import replace

import pytest

from ap_coder.ai import local_llm, pipeline
from ap_coder.ai.inference import MODEL_SUGGESTED
from ap_coder.ai.pipeline import ArrivalBreaker, InvoicePipeline
from ap_coder.bench.fake_model import FakeModel
from ap_coder.core.config import Settings
from ap_coder.core.store import Store, load_sample_setup
from ap_coder.core.store.folder import read_file
from ap_coder.intake.imports import Reader
from tests.conftest import SAMPLES

REAL_FETCH = local_llm._fetch_json  # conftest replaces it for every test: these ask a fake model server


@pytest.fixture(autouse=True)
def fresh_breaker(monkeypatch):
    monkeypatch.setattr(pipeline, "ARRIVAL_BREAKER", ArrivalBreaker())


@pytest.fixture
def real_fetch(monkeypatch):
    monkeypatch.setattr(local_llm, "_fetch_json", REAL_FETCH)
    local_llm.forget_status()
    yield
    local_llm.forget_status()


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_PRIVATE_DIR", str(tmp_path / "private"))  # (the chat model's answers kept there)
    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    return store


def _settings(base_url: str) -> Settings:
    s = Settings()
    return replace(s, llm=replace(s.llm, provider="local", base_url=base_url, timeout_seconds=600))


def _text_invoices(store: Store, n: int) -> list[tuple]:
    """``n`` invoices as text (the samples' text copies, each made distinct): read in a moment, each with lines
    nothing has taught yet, so each asks the chat model."""
    folder = store.path.parent / "invoices"
    folder.mkdir(exist_ok=True)
    texts = sorted(p for p in SAMPLES.glob("*.md") if p.name != "README.md")
    read = []
    for k in range(n):
        source = texts[k % len(texts)]
        path = folder / f"{k:02d}-{source.name}"
        path.write_text(source.read_text(encoding="utf-8") + f"\n\nCopy {k}\n", encoding="utf-8")
        read.append(read_file(path))
    return read


def test_a_model_that_never_answers_holds_up_no_import(store, monkeypatch, real_fetch):
    """20 invoices in under 60 s against a model server that lists its models but never answers a question (600 s
    of AP_LLM_TIMEOUT_SECONDS each before): two arrivals wait their 20 s, then the breaker asks no more. Each
    invoice is in the review queue, its lines left for AP, and none is left out of the page reader's pass."""
    monkeypatch.setenv("AP_SERVER_MODE", "1")  # the company server's default: 20 s
    monkeypatch.delenv(pipeline.ARRIVAL_ENV, raising=False)
    with FakeModel(hang_rate=1.0) as fake:
        store.queue_imports(_text_invoices(store, 20), source="upload", person=True)
        started = time.monotonic()
        read = Reader(store, _settings(fake.base_url), "r").read_all()
        took = time.monotonic() - started
        hung = fake.stats()["hung"]
    assert read == 20 and took < 60, took
    assert len(store.list_invoices()) == 20 and hung == pipeline.BREAKER_FAILURES
    assert pipeline.ARRIVAL_BREAKER.opened is not None


def test_a_model_that_answers_in_time_codes_as_before(store, monkeypatch, real_fetch):
    """The same invoices coded with the arrival's limit (the server) and without it (a laptop, as before), against a
    model that answers in a moment: the same coding."""
    texts = _text_invoices(store, 3)
    with FakeModel(latency="0-0") as fake:
        codings = {}
        for limit in ("20", "0"):
            monkeypatch.setenv(pipeline.ARRIVAL_ENV, limit)
            run = InvoicePipeline(_settings(fake.base_url), store.reference_data(), store=store)
            assert run.arrival_seconds == float(limit)
            results = [run.process(read[0], save=False) for read in texts]
            assert all(r.ok and not r.chat_deferred for r in results)
            codings[limit] = [r.output for r in results]
        asked = fake.stats()["by_kind"].get("suggest", 0)
    assert codings["20"] == codings["0"] and asked
    assert MODEL_SUGGESTED in json.dumps(codings["20"])  # the model's accounts are in, both times


def test_the_breaker_opens_after_two_timeouts_then_lets_one_ask(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(pipeline.time, "monotonic", lambda: clock[0])
    breaker = ArrivalBreaker()
    assert breaker.asks()
    breaker.answered(False)
    assert breaker.asks()  # one timeout: still asked
    breaker.answered(False)
    assert not breaker.asks()  # two in a row: open
    clock[0] += pipeline.BREAKER_SECONDS
    assert breaker.asks() and not breaker.asks()  # half open: one asks, the others don't meanwhile
    breaker.answered(False)
    assert not breaker.asks()  # out of time again: open again
    clock[0] += pipeline.BREAKER_SECONDS
    assert breaker.asks()
    breaker.answered(True)
    assert breaker.asks() and breaker.failures == 0  # in time: closed
    breaker.answered(None)  # not running: said at once, no wait to count
    assert breaker.asks() and breaker.failures == 0


def test_the_arrival_waits_on_the_company_server_only(monkeypatch):
    monkeypatch.delenv(pipeline.ARRIVAL_ENV, raising=False)
    monkeypatch.delenv("AP_SERVER_MODE", raising=False)
    assert pipeline.arrival_seconds() == 0.0  # a laptop: as before
    monkeypatch.setenv("AP_SERVER_MODE", "1")
    assert pipeline.arrival_seconds() == pipeline.ARRIVAL_SECONDS == 20.0
    monkeypatch.setenv(pipeline.ARRIVAL_ENV, "30")
    assert pipeline.arrival_seconds() == 30.0


def test_a_chat_slot_not_free_in_time_is_a_timeout(monkeypatch):
    """The arrival's wait for a chat slot counts too: the slots held by the AI reviewer's long questions don't hold
    an import up."""
    import threading

    from ap_coder.ai import local_llm

    slots = local_llm.Slots("test")
    held, release = threading.Event(), threading.Event()

    def hold():
        with slots.held(1):
            held.set()
            release.wait(10)

    threading.Thread(target=hold, daemon=True).start()
    assert held.wait(5)
    started = time.monotonic()
    with pytest.raises(TimeoutError), slots.held(1, deadline=time.monotonic() + 0.3):
        pass
    assert time.monotonic() - started < 2
    release.set()
