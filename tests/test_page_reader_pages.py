"""Settings → Page reader and the page reader's line on the review screen, run headless (Streamlit AppTest) with a
faked page reader: confirming the model, saving how it is used, asking for a reading, a reading arriving."""

import json
import sys

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from ap_coder import page_reader
from ap_coder.store import Store, load_sample_setup

from .conftest import ROOT, SAMPLES

APP = str(ROOT / "ap_coder" / "dashboard.py")
TIMEOUT = 90
STEM = "harbourview_NS_HST_HPS-2026-0347"
MODEL = "ath-maas_ovisocr2"


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "private" / "ap_coder.db"
    monkeypatch.setenv("AP_DB_PATH", str(path))
    monkeypatch.setenv("AP_ENV_FILE", str(tmp_path / "private" / ".env"))
    monkeypatch.setenv("AP_LLM_PROVIDER", "off")
    for key in ("AP_PAGE_READER", "AP_PAGE_READER_SCOPE", "AP_PAGE_READER_MODEL", "AP_PAGE_READER_BASE_URL"):
        monkeypatch.setenv(key, "")  # restored after the test (the page writes os.environ)
    for name in [m for m in sys.modules if m.startswith(("ap_coder.webapp", "ap_coder.dashboard"))]:
        monkeypatch.delitem(sys.modules, name)
    st.cache_resource.clear()
    st.cache_data.clear()
    # The dashboard starts the page reader's thread: not in these tests (the queue is read by hand below).
    from ap_coder import page_worker

    monkeypatch.setattr(page_worker, "start_background", lambda *a, **k: None)
    return path


def _status(state="downloaded", model=MODEL):
    return page_reader.ReaderStatus(reachable=state != "down", lm_studio=state != "down", model=model,
                                    document_reader=bool(model), state=state, candidates=[model] if model else [],
                                    note="" if model else "OvisOCR2 isn't downloaded in LM Studio")  # fmt: skip


@pytest.fixture
def ready(monkeypatch):
    """A page reader that is downloaded; its test reads every field right."""
    monkeypatch.setattr(page_reader, "reader_status", lambda settings, use_cache=True: _status())
    monkeypatch.setattr(page_reader, "load_reader", lambda settings, model=None: "")
    monkeypatch.setattr(page_reader, "page_seconds_estimate", lambda settings, model=None: 180.0)

    def tested(settings, model=None):
        rows = [
            {"field": "invoice_number", "expected": "NW-2026-0912", "read": "NW-2026-0912", "match": True},
            {"field": "grand_total", "expected": "2,457.83", "read": "2,457.83", "match": True},
        ]
        return page_reader.ReaderTest(model=model or MODEL, ok=True, seconds=171.0, rows=rows, fields_right=2,
                                      fields_total=2, when="2026-10-10T09:00")  # fmt: skip

    monkeypatch.setattr(page_reader, "test_reader", tested)


def _settings_page():
    return AppTest.from_string("from ap_coder.webapp.settings import page_settings\npage_settings()",
                               default_timeout=TIMEOUT)  # fmt: skip


def _ok(at):
    assert not at.exception, [e.value for e in at.exception]
    return at


def _html(at) -> str:
    return " ".join(str(getattr(h, "proto", h)) for h in at.get("html"))


def test_the_tab_explains_what_to_download_when_there_is_no_reader(db):
    at = _ok(_settings_page().run())
    assert "OvisOCR2" in " ".join(m.value for m in at.markdown)  # the setup steps
    assert at.button(key="pr_test").disabled and at.button(key="pr_load").disabled
    assert "Not tested yet" in _html(at)


def test_testing_the_reader_links_it(db, ready, monkeypatch):
    at = _ok(_settings_page().run())
    assert not at.button(key="pr_test").disabled
    _ok(at.button(key="pr_test").click().run())

    saved = json.loads(Store(db).get_setting("page_reader_test"))
    assert saved["ok"] and saved["fields_right"] == 2 and saved["model"] == MODEL
    assert "Linked: passed its test" in _html(at) and "read 2 of 2 fields right" in _html(at)
    events = [e["action"] for e in Store(db).events()]
    assert "page_reader_tested" in events

    # Another model is picked: it is not linked until it passes the test too.
    monkeypatch.setattr(page_reader, "reader_status", lambda settings, use_cache=True: _status(model="qwen3.5-9b"))
    at = _ok(_settings_page().run())
    assert "Not tested with qwen3.5-9b" in _html(at)


def test_how_it_is_used_is_saved_to_the_env_file(db, ready):
    from ap_coder.envfile import read_env

    at = _ok(_settings_page().run())
    at.radio(key="pr_mode").set_value("ask")
    at.radio(key="pr_scope").set_value("all")
    at.selectbox(key="pr_model").set_value(MODEL)
    submit = next(b for b in at.button if b.label == "Save page reader settings" and b.proto.is_form_submitter)
    _ok(submit.click().run())

    env = read_env(db.parent / ".env")
    assert env["AP_PAGE_READER"] == "ask" and env["AP_PAGE_READER_SCOPE"] == "all"
    assert env["AP_PAGE_READER_MODEL"] == MODEL
    assert "AP_PAGE_READER_BASE_URL" not in env  # left empty: the same server as the AI model


def _invoice(db, monkeypatch, mode="ask", scope="all"):
    """One sample invoice processed with the page reader in ``mode``."""
    from dataclasses import replace

    from ap_coder.config import PageReaderSettings, Settings
    from ap_coder.page_worker import TEST_KEY
    from ap_coder.pipeline import InvoicePipeline

    monkeypatch.setenv("AP_PAGE_READER", mode)
    monkeypatch.setenv("AP_PAGE_READER_SCOPE", scope)
    store = Store(db)
    load_sample_setup(store)
    store.set_setting(TEST_KEY, json.dumps({"model": MODEL, "ok": True, "fields_right": 9, "fields_total": 9}))
    s = Settings()
    settings = replace(s, llm=replace(s.llm, provider="off"), page_reader=PageReaderSettings(mode=mode, scope=scope))
    result = InvoicePipeline(settings, store.reference_data(), store=store).process(SAMPLES / f"{STEM}.pdf")
    assert result.ok, result.error
    return store, result.invoice_id, settings


def _review(invoice_id):
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.session_state["open_invoice"] = invoice_id
    return at


def _captions(at) -> str:
    return " ".join(c.value for c in at.caption)


def test_asking_for_a_reading_from_the_invoice(db, monkeypatch, ready):
    store, invoice_id, _ = _invoice(db, monkeypatch, mode="ask")
    at = _ok(_review(invoice_id).run())
    _ok(at.button(key=f"inv{invoice_id}_read_pages").click().run())

    row = store.page_read(invoice_id)
    assert row["status"] == "waiting" and row["reason"] == "asked"
    assert "Page reader: next" in _captions(at)


def test_a_reading_that_arrives_while_the_invoice_is_open_is_shown(db, monkeypatch, ready):
    from ap_coder import page_worker

    store, invoice_id, settings = _invoice(db, monkeypatch, mode="auto")
    transcript = (SAMPLES / f"{STEM}.md").read_text(encoding="utf-8")

    def read(settings, path, model=None, on_page=None, should_stop=None):
        return page_reader.PageReading(model=MODEL, pages=[transcript], seconds=[120.0])

    monkeypatch.setattr(page_reader, "read_document", read)
    at = _ok(_review(invoice_id).run())
    assert "Page reader:" in _captions(at)  # queued: waiting for its turn

    outcome = page_worker.read_one(settings, store)  # the background thread's work
    assert outcome.updated, outcome
    _ok(at.run())
    assert any("page reader has read this invoice" in t.value for t in at.toast)
    assert "ovisocr2: agrees on" in _captions(at) and "figures on the page read the same" in _captions(at)
    assert at.session_state[f"inv{invoice_id}_page_reader_seen"]


def test_off_hides_the_page_reader_line(db, monkeypatch, ready):
    _, invoice_id, _ = _invoice(db, monkeypatch, mode="off")
    at = _ok(_review(invoice_id).run())
    assert not [b for b in at.button if b.key == f"inv{invoice_id}_read_pages"]
    assert "Page reader" not in _captions(at)


def test_setup_checklist_and_the_models_lm_studio_has(db, ready, monkeypatch):
    rows = [{"model": MODEL, "loaded": True, "context": 20480, "vision": True, "document_reader": True,
             "used_for": "reads pages"}]  # fmt: skip
    monkeypatch.setattr(page_reader, "lm_studio_models", lambda settings, use_cache=True: rows)
    at = _ok(_settings_page().run())
    html = _html(at)
    assert "LM Studio is running" in html and "OvisOCR2 is downloaded" in html and "Tested and linked" in html
    assert "Test the page reader below" in html  # not linked yet: the next step says what to do
    tables = [d.value for d in at.dataframe]
    assert any(MODEL in t["Model"].tolist() and "yes, 20,480 tokens" in t["Loaded"].tolist() for t in tables
               if "Model" in t.columns and "Loaded" in t.columns)  # fmt: skip


def test_download_button_when_ovisocr2_is_missing(db, monkeypatch):
    monkeypatch.setattr(page_reader, "reader_status", lambda settings, use_cache=True: page_reader.ReaderStatus(
        reachable=True, lm_studio=True, model="", document_reader=False, state="missing", candidates=[],
        note=page_reader.NOT_DOWNLOADED))  # fmt: skip
    monkeypatch.setattr(page_reader, "download_reader", lambda settings: ("job_9", ""))
    monkeypatch.setattr(page_reader, "download_progress", lambda settings, job: {
        "status": "downloading", "done": 400_000_000, "total": 1_000_000_000, "seconds_left": 120})  # fmt: skip
    at = _ok(_settings_page().run())
    _ok(at.button(key="pr_download").click().run())
    assert at.session_state["pr_download_job"] == "job_9"
    assert any("Downloading OvisOCR2: 400 of 1,000 MB" in str(p.proto) for p in at.get("progress"))
