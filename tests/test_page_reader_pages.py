"""Settings → Page reader and the page reader's line on the review screen, run headless (Streamlit AppTest) with a
faked page reader: confirming the model, saving how it is used, asking for a reading, a reading arriving."""

import json
import sys
import time

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

    def tested(settings, model=None, on_page=None):
        for number in (1, 2):
            if on_page:
                on_page(number, 2)
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


def _test_finished(db, seconds=30):
    from ap_coder import page_worker

    deadline = time.monotonic() + seconds
    while page_worker.test_under_way(Store(db)) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not page_worker.test_under_way(Store(db)), "the test never finished"


def test_testing_the_reader_links_it(db, ready, monkeypatch):
    at = _ok(_settings_page().run())
    assert not at.button(key="pr_test").disabled
    _ok(at.button(key="pr_test").click().run())
    _test_finished(db)  # in the background: the page needn't stay open for it
    _ok(at.run())

    saved = json.loads(Store(db).get_setting("page_reader_test"))["models"][MODEL]
    assert saved["ok"] and saved["fields_right"] == 2 and saved["model"] == MODEL
    assert "Linked: passed its test" in _html(at) and "read 2 of 2 fields right" in _html(at)
    events = [e["action"] for e in Store(db).events()]
    assert "page_reader_tested" in events

    # Another model is picked: it is not linked until it passes the test too.
    monkeypatch.setattr(page_reader, "reader_status", lambda settings, use_cache=True: _status(model="qwen3.5-9b"))
    at = _ok(_settings_page().run())
    assert "Not tested with qwen3.5-9b" in _html(at)


def test_the_model_is_saved_to_the_env_file(db, ready):
    """The page reader is always on, for every invoice: only the model is saved (how it is used is not a choice)."""
    from ap_coder.envfile import read_env

    at = _ok(_settings_page().run())
    at.selectbox(key="pr_model").set_value(MODEL)
    submit = next(b for b in at.button if b.label == "Save page reader settings" and b.proto.is_form_submitter)
    _ok(submit.click().run())

    env = read_env(db.parent / ".env")
    assert "AP_PAGE_READER" not in env and "AP_PAGE_READER_SCOPE" not in env
    assert env["AP_PAGE_READER_MODEL"] == MODEL
    assert "AP_PAGE_READER_BASE_URL" not in env  # left empty: the same server as the AI model


def _invoice(db, monkeypatch, path=None):
    """One sample invoice processed (queued for the page reader, as every invoice but a text file is)."""
    from dataclasses import replace

    from ap_coder.config import Settings
    from ap_coder.page_worker import TEST_KEY
    from ap_coder.pipeline import InvoicePipeline

    store = Store(db)
    load_sample_setup(store)
    store.set_setting(TEST_KEY, json.dumps({"model": MODEL, "ok": True, "fields_right": 9, "fields_total": 9}))
    s = Settings()
    settings = replace(s, llm=replace(s.llm, provider="off"))
    result = InvoicePipeline(settings, store.reference_data(), store=store).process(path or SAMPLES / f"{STEM}.pdf")
    assert result.ok, result.error
    return store, result.invoice_id, settings


def _review(invoice_id):
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.session_state["open_invoice"] = invoice_id
    return at


def _captions(at) -> str:
    return " ".join(c.value for c in at.caption)


def test_asking_for_a_reading_again_from_the_invoice(db, monkeypatch, ready):
    store, invoice_id, _ = _invoice(db, monkeypatch)
    store.next_page_read(invoice_id, reader="1:gone")
    store.finish_page_read(invoice_id, "failed", MODEL, error="the model server cut the reading off 3 times")
    at = _ok(_review(invoice_id).run())
    _ok(at.button(key=f"inv{invoice_id}_read_pages").click().run())

    row = store.page_read(invoice_id)
    assert row["status"] == "waiting" and row["reason"] == "asked"
    assert "Page reader: next" in _captions(at)


def test_a_reading_that_arrives_while_the_invoice_is_open_is_shown(db, monkeypatch, ready):
    from ap_coder import page_worker

    store, invoice_id, settings = _invoice(db, monkeypatch)
    transcript = (SAMPLES / f"{STEM}.md").read_text(encoding="utf-8")

    def read(settings, path, model=None, on_page=None, should_stop=None):
        return page_reader.PageReading(model=MODEL, pages=[transcript], seconds=[120.0], page_count=1)

    monkeypatch.setattr(page_reader, "read_document", read)
    at = _ok(_review(invoice_id).run())
    assert "Page reader:" in _captions(at)  # queued: waiting for its turn

    outcome = page_worker.read_one(settings, store)  # the background thread's work
    assert outcome.updated, outcome
    _ok(at.run())
    assert any("page reader has read this invoice" in t.value for t in at.toast)
    assert "ovisocr2: agrees on" in _captions(at) and "figures on the page read the same" in _captions(at)
    assert at.session_state[f"inv{invoice_id}_page_reader_seen"]


def test_the_wait_for_the_test_is_per_page_times_its_pages(db, monkeypatch):
    from ap_coder.webapp.page_reader_settings import _test_page_count, test_wait

    asked = []

    def estimate(settings, model=None):
        asked.append(model)
        return speeds[model]

    speeds = {MODEL: 90.0, "fast": 2.0, "new": None}
    monkeypatch.setattr(page_reader, "page_seconds_estimate", estimate)
    assert _test_page_count() == 2  # the test invoice's two pages
    assert test_wait(None, MODEL) == "about 3 minutes"  # 90 s a page, two pages (was "about 2 min")
    assert test_wait(None, "fast") == "about 10 seconds"  # never "about 0 min"
    assert test_wait(None, "new") == "about 10–20 minutes on a laptop without a graphics card"  # not measured yet
    assert asked == [MODEL, "fast", "new"]  # the model being tested, not whichever read last


def test_the_queue_position_counts_only_who_is_ahead(db, monkeypatch, ready):
    store, invoice_id, _ = _invoice(db, monkeypatch)
    for other in (901, 902):  # queued after it
        store.queue_page_read(other, "new invoice")
    with store._conn() as conn:
        conn.execute("UPDATE page_reads SET created_at = '2999-01-01T00:00:00' WHERE invoice_id IN (901, 902)")
    at = _ok(_review(invoice_id).run())
    assert "Page reader: next" in _captions(at)  # first in line: nothing ahead of it (was "2 ahead")

    store.queue_page_read(900, "new invoice")
    with store._conn() as conn:
        conn.execute("UPDATE page_reads SET created_at = '2000-01-01T00:00:00' WHERE invoice_id = 900")
    _ok(at.run())
    assert "waiting to read it (1 ahead)" in _captions(at)


def test_an_edit_made_as_the_reading_arrives_is_kept(db, monkeypatch, ready):
    """The reading lands in the store, then the reviewer's first edit comes in before the screen looked again: the
    edit is on that same run, not counted yet, and must not be wiped by the fields being drawn from the reading."""
    store, invoice_id, _ = _invoice(db, monkeypatch)
    inv = store.get_invoice(invoice_id)
    at = _ok(_review(invoice_id).run())
    key = f"inv{invoice_id}"
    assert at.text_input(key=f"{key}_invoice_number").value == inv["ai_output"]["invoice_number"]

    read = dict(inv["ai_output"], po_number="PO-FROM-THE-READER")
    assert store.replace_proposal(invoice_id, read, inv["validation"], None,
                                  meta={"page_reader": {"model": MODEL, "at": "2026-10-10T12:00:00"}})  # fmt: skip
    _ok(at.text_input(key=f"{key}_invoice_number").input("CORRECTED-123").run())

    assert at.text_input(key=f"{key}_invoice_number").value == "CORRECTED-123"
    assert not any("fields show its reading now" in t.value for t in at.toast)
    assert "your edits are kept" in _captions(at)
    assert [b for b in at.button if b.key == f"{key}_page_reader_reset"]  # it can still start over from the reading


def test_a_text_invoice_has_no_page_reader_line(db, monkeypatch, ready):
    store, invoice_id, _ = _invoice(db, monkeypatch, SAMPLES / "pacific_BC_GST_PST_PO-77120.md")
    assert store.page_read(invoice_id) is None  # no page to look at
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


def test_missing_ovisocr2_says_where_to_copy_it_never_downloads(db, monkeypatch):
    monkeypatch.setattr(page_reader, "reader_status", lambda settings, use_cache=True: page_reader.ReaderStatus(
        reachable=True, lm_studio=True, model="", document_reader=False, state="missing", candidates=[],
        note=page_reader.NOT_DOWNLOADED))  # fmt: skip
    at = _ok(_settings_page().run())
    shown = " ".join(i.value for i in at.info)
    assert "ATH-MaaS_OvisOCR2-GGUF" in shown and "nothing is downloaded" in shown
    assert not [b for b in at.button if (b.key or "") == "pr_download"]


# --- The second review's findings ----------------------------------------------------------------------------------


def test_a_test_under_way_shows_its_progress_and_isnt_started_twice(db, ready, monkeypatch):
    """The test runs in the background: the tab says since when and how many pages are read, and the button waits."""
    import threading

    from ap_coder import page_worker

    release, started = threading.Event(), threading.Event()

    def slow(settings, model=None, on_page=None):
        on_page(1, 2)
        on_page(2, 2)
        started.set()
        release.wait(30)
        return page_reader.ReaderTest(model=model, ok=True, seconds=1157.0, rows=[], fields_right=9, fields_total=9,
                                      when="2026-10-10T22:27")  # fmt: skip

    monkeypatch.setattr(page_reader, "test_reader", slow)
    at = _ok(_settings_page().run())
    _ok(at.button(key="pr_test").click().run())
    try:
        assert started.wait(30)
        _ok(at.run())  # the page opened again (or another browser): the test is still under way
        shown = _captions(at).replace("\\", "")  # model names are escaped for markdown
        assert f"Testing {MODEL}… started " in shown and "1 of 2 pages read" in shown and "close the browser" in shown
        assert at.button(key="pr_test").disabled
    finally:
        release.set()
    _test_finished(db)
    assert page_worker.saved_test(Store(db), MODEL)["ok"]


def test_other_models_keep_their_tests(db, ready, monkeypatch):
    from ap_coder import page_worker

    store = Store(db)
    page_worker.save_test(store, {"model": MODEL, "ok": True, "fields_right": 9, "fields_total": 9,
                                  "when": "2026-10-10T22:27"})  # fmt: skip
    monkeypatch.setattr(page_reader, "reader_status", lambda settings, use_cache=True: _status(model="qwen3.5-9b"))
    at = _ok(_settings_page().run())
    assert "Not tested with qwen3.5-9b" in _html(at)
    assert f"Also tested here: {MODEL} passed, linked (9 of 9 fields right)" in _captions(at).replace("\\", "")
    assert page_worker.confirmed(store, MODEL)  # still linked: switching back needs no new test


def test_the_test_table_shows_text_only(db, ready):
    """Amounts and text in one column made Streamlit log an ArrowTypeError on every look at the tab."""
    from ap_coder import page_worker

    rows = [{"field": "invoice_number", "expected": "NW-2026-0912", "read": "NW-2026-0912", "match": True},
            {"field": "tax_total", "expected": 2072.85, "read": None, "match": False},
            {"field": "grand_total", "expected": 18017.85, "read": 18017.85, "match": True}]  # fmt: skip
    page_worker.save_test(Store(db), {"model": MODEL, "ok": False, "fields_right": 2, "fields_total": 3, "rows": rows})
    at = _ok(_settings_page().run())
    table = next(d.value for d in at.dataframe if "On the invoice" in d.value.columns)
    for column in ("On the invoice", "Page reader read"):
        assert all(isinstance(v, str) for v in table[column]), table[column].tolist()
    assert table["On the invoice"].tolist() == ["NW-2026-0912", "2072.85", "18017.85"]
    assert table["Page reader read"].tolist()[1] == ""


def test_a_model_that_cannot_see_cannot_be_tested_or_loaded(db, monkeypatch):
    blind = _status(model="qwen2.5-7b-instruct")
    blind.state, blind.document_reader = "blind", False
    blind.note = "qwen2.5-7b-instruct can't look at pictures: pick a model that can, e.g. OvisOCR2 (or Automatic)."
    monkeypatch.setattr(page_reader, "reader_status", lambda settings, use_cache=True: blind)
    at = _ok(_settings_page().run())
    assert at.button(key="pr_test").disabled and at.button(key="pr_load").disabled
    assert "look at pictures: can" in _html(at) and "Ready" not in _html(at)
    assert "pick a model that can, e.g. OvisOCR2" in _captions(at)
