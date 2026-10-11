"""Settings → Reading, the reading banner and OvisOCR2's line on the review screen, run headless (Streamlit AppTest)
with a faked reading status and page reader: nothing to choose, every reader's state shown, the self-test run again,
cloud services only in a developer build, and the invoice's own state with OvisOCR2."""

import html
import json
import sys
import time

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from ap_coder import page_reader, reading
from ap_coder.reading import ReaderLine, ReadingStatus
from ap_coder.store import Store, load_sample_setup

from .conftest import ROOT, SAMPLES

APP = str(ROOT / "ap_coder" / "dashboard.py")
TIMEOUT = 90
STEM = "harbourview_NS_HST_HPS-2026-0347"
MODEL = "ath-maas_ovisocr2"
TABS = ["Reading", "Automation", "Review", "JD Edwards E1", "Data & backups", "About"]


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "private" / "ap_coder.db"
    monkeypatch.setenv("AP_DB_PATH", str(path))
    monkeypatch.setenv("AP_ENV_FILE", str(tmp_path / "private" / ".env"))
    monkeypatch.setenv("AP_LLM_PROVIDER", "off")
    monkeypatch.delenv("AP_ALLOW_INTERNET", raising=False)
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


def _reader_status(state="downloaded", model=MODEL):
    return page_reader.ReaderStatus(reachable=state != "down", lm_studio=state != "down", model=model,
                                    document_reader=bool(model), state=state, candidates=[model] if model else [],
                                    note="" if model else page_reader.NOT_DOWNLOADED)  # fmt: skip


def working(**changes) -> ReadingStatus:
    """Every reader working, OvisOCR2 found and trusted."""
    status = ReadingStatus(
        readers=[
            ReaderLine("PDF text layer", "ok", "reads a digital PDF's own text"),
            ReaderLine("OCR (two engines)", "ok", "reads scans and photos"),
            ReaderLine("Page reader (OvisOCR2)", "ok", "reads every page as a second reader"),
            ReaderLine("Rule reader", "ok", "finds each field by its label"),
        ],
        page_reader_model=MODEL, page_reader_ready=True, self_test="passed",
        self_test_detail="read 9 of 9 fields right on 2026-10-10", queue=3, eta_minutes=12.0,
        figures_agree=0.97, figures_count=120,
    )  # fmt: skip
    for name, value in changes.items():
        setattr(status, name, value)
    return status


def broken(**changes) -> ReadingStatus:
    """LM Studio answers but has no OvisOCR2: the page reader can't read, the banner says so."""
    status = ReadingStatus(
        readers=[
            ReaderLine("PDF text layer", "ok", "reads a digital PDF's own text"),
            ReaderLine("OCR (two engines)", "warn", "one engine only: run the launcher again"),
            ReaderLine("Page reader (OvisOCR2)", "off", "OvisOCR2 isn't in LM Studio: copy its two files from IT"),
        ],
        page_reader_model="", page_reader_ready=False, self_test="no model", queue=4,
        banner="OvisOCR2 isn't in LM Studio: invoices wait for it and need a person to approve them.",
    )  # fmt: skip
    for name, value in changes.items():
        setattr(status, name, value)
    return status


@pytest.fixture
def status(monkeypatch):
    """The reading status the pages see: set ``status.now`` (a ReadingStatus, or an exception to raise)."""

    class Holder:
        now: object = working()
        asked: list = []

    def fake(settings, store, *, use_cache=True):
        Holder.asked.append(use_cache)
        if isinstance(Holder.now, Exception):
            raise Holder.now
        return Holder.now

    monkeypatch.setattr(reading, "reading_status", fake)
    monkeypatch.setattr(page_reader, "reader_status", lambda settings, use_cache=True: _reader_status())
    monkeypatch.setattr(page_reader, "lm_studio_models", lambda settings, use_cache=True: [])
    monkeypatch.setattr(page_reader, "page_seconds_estimate", lambda settings, model=None: 180.0)
    return Holder


@pytest.fixture
def tested(monkeypatch):
    """OvisOCR2's self-test reads every field right."""

    def run(settings, model=None, on_page=None):
        for number in (1, 2):
            if on_page:
                on_page(number, 2)
        rows = [
            {"field": "invoice_number", "expected": "NW-2026-0912", "read": "NW-2026-0912", "match": True},
            {"field": "grand_total", "expected": "2,457.83", "read": "2,457.83", "match": True},
        ]
        return page_reader.ReaderTest(model=model or MODEL, ok=True, seconds=171.0, rows=rows, fields_right=2,
                                      fields_total=2, when="2026-10-10T09:00")  # fmt: skip

    monkeypatch.setattr(page_reader, "test_reader", run)


def _settings_page():
    return AppTest.from_string("from ap_coder.webapp.settings import page_settings\npage_settings()",
                               default_timeout=TIMEOUT)  # fmt: skip


def _ok(at):
    assert not at.exception, [e.value for e in at.exception]
    return at


def _html(at) -> str:
    return html.unescape(" ".join(str(getattr(h, "proto", h)) for h in at.get("html")).replace("\\'", "'"))


def _captions(at) -> str:
    return " ".join(c.value for c in at.caption)


def _markdown(at) -> str:
    return " ".join(m.value for m in at.markdown)


def _test_finished(db, seconds=30):
    from ap_coder import page_worker

    deadline = time.monotonic() + seconds
    while page_worker.test_under_way(Store(db)) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not page_worker.test_under_way(Store(db)), "the test never finished"


# --- Settings: the tabs, nothing to choose -------------------------------------------------------------------------


def test_settings_tabs_are_reading_automation_review_erp_data_about(db, status):
    at = _ok(_settings_page().run())
    assert [t.label for t in at.tabs] == TABS
    labels = " ".join(t.label for t in at.tabs)
    assert "AI model" not in labels and "Page reader" not in labels and "Azure" not in labels
    assert "touchless processing" in (_markdown(at) + _captions(at)).lower()  # the Automation tab is drawn


def test_every_settings_tab_the_app_names_exists():
    """No message points AP to a Settings tab that is gone ("Settings → AI model", "Settings → Page reader")."""
    import re

    named = []
    for path in [*(ROOT / "ap_coder").rglob("*.py"), *(ROOT / "scripts").rglob("*.py")]:
        source = re.sub(r"\"\s*\n\s*(f?)\"", "", path.read_text(encoding="utf-8"))  # strings split over lines
        named += [(path.name, m.group(1)) for m in re.finditer(r"Settings (?:→|->|>) ([A-Za-z&][\w &]*)", source)]
    assert named
    wrong = [(name, tab) for name, tab in named if not any(tab.startswith(t) or t.startswith(tab) for t in TABS)
             and not tab.startswith("JD Edwards")]  # fmt: skip
    assert not wrong, wrong


def test_there_is_nothing_to_choose_about_reading(db, status):
    at = _ok(_settings_page().run())
    keys = {w.key for w in [*at.radio, *at.selectbox, *at.toggle, *at.text_input] if w.key}
    for gone in ("pr_mode", "pr_scope", "pr_model", "pr_base_url", "llm_provider", "llm_model", "llm_vision",
                 "llm_base_url"):  # fmt: skip
        assert gone not in keys, gone
    assert not [t for t in at.toggle if "page images" in t.label]
    assert not [r for r in at.radio if r.label in ("When it reads", "Which invoices")]
    page = _markdown(at) + _captions(at) + _html(at)
    assert "linked" not in page.lower()  # it is a self-test now


def test_azure_is_hidden_unless_the_internet_is_allowed(db, status):
    at = _ok(_settings_page().run())
    assert not [e for e in at.expander if "cloud services" in e.label]
    assert not [t for t in at.text_input if "endpoint" in t.label.lower()]
    assert "Azure" not in _markdown(at) + _captions(at) + _html(at) + " ".join(t.label for t in at.tabs)


def test_a_developer_build_shows_cloud_services_inside_reading(db, status, monkeypatch):
    monkeypatch.setenv("AP_ALLOW_INTERNET", "1")
    at = _ok(_settings_page().run())
    assert [e for e in at.expander if e.label == "Developer: cloud services"]
    assert [t for t in at.text_input if t.label == "Document Intelligence endpoint"]
    assert "The offline build ignores these settings" in _captions(at)


# --- Settings → Reading ---------------------------------------------------------------------------------------------


def test_reading_tab_when_every_reader_works(db, status):
    at = _ok(_settings_page().run())
    page = _markdown(at)
    assert "How AP Coder reads every invoice" in page and "OvisOCR2 reads every page too" in page
    html = _html(at)
    for name in ("PDF text layer", "OCR (two engines)", "Page reader (OvisOCR2)", "Rule reader"):
        assert name in html, name
    assert "Working" in html and "Not working" not in html
    assert "Found in LM Studio" in html and MODEL in html and "Self-test passed" in html
    assert "read 9 of 9 fields right" in html
    assert "3 invoices waiting" in page and "about 12 minutes" in page
    assert "read **97%** of figures the same as the PDF's own text (120 figures)" in page
    assert not at.button(key="rd_selftest").disabled
    steps = html
    assert "LM Studio is running" in steps and "OvisOCR2 is in LM Studio" in steps
    assert "Self-test passed (automatic)" in steps and "Chat model for GL suggestions (optional)" in steps


def test_reading_tab_when_ovisocr2_is_missing(db, status, monkeypatch):
    status.now = broken()
    monkeypatch.setattr(page_reader, "reader_status", lambda settings, use_cache=True: _reader_status("missing", ""))
    at = _ok(_settings_page().run())
    html = _html(at)
    assert "Not working" in html and "Needs a look" in html and "Not in LM Studio" in html
    assert "No self-test: OvisOCR2 isn't in LM Studio" in html
    assert at.button(key="rd_selftest").disabled
    shown = " ".join(i.value for i in at.info)
    assert "ATH-MaaS_OvisOCR2-GGUF" in shown and "nothing is downloaded" in shown  # copied in, never downloaded
    assert "4 invoices waiting" in _markdown(at)


def test_reading_tab_when_lm_studio_is_not_running(db, status, monkeypatch):
    status.now = broken(page_reader_model="", readers=[])
    monkeypatch.setattr(page_reader, "reader_status", lambda settings, use_cache=True: _reader_status("down", ""))
    monkeypatch.setattr(page_reader, "lm_studio_models", lambda settings, use_cache=True: None)
    at = _ok(_settings_page().run())
    page = _markdown(at)
    assert "IT team installs it" in page and "Start server" in page and "lmstudio.ai" not in page
    assert "start LM Studio first to check" in _html(at)
    assert "LM Studio isn't answering" in _captions(at)
    assert not at.info  # the copy-in note waits until LM Studio can be asked


def test_reading_tab_when_the_status_cannot_be_read(db, status):
    status.now = RuntimeError("database locked")
    at = _ok(_settings_page().run())
    assert "couldn't check the readers just now" in " ".join(w.value for w in at.warning)
    assert [t.label for t in at.tabs] == TABS  # the rest of Settings still works


def test_check_again_asks_afresh(db, status):
    at = _ok(_settings_page().run())
    status.asked.clear()
    _ok(at.button(key="rd_check").click().run())
    assert False in status.asked  # not from the cache


def test_run_the_self_test_again_starts_it(db, status, monkeypatch):
    from ap_coder import page_worker

    started = []
    monkeypatch.setattr(page_worker, "start_test", lambda settings, store, model: started.append(model) or True)
    at = _ok(_settings_page().run())
    _ok(at.button(key="rd_selftest").click().run())
    assert started == [MODEL]
    assert any("Self-test of" in t.value and "started in the background" in t.value for t in at.toast)


def test_the_self_test_runs_in_the_background_and_its_result_is_kept(db, status, tested):
    at = _ok(_settings_page().run())
    _ok(at.button(key="rd_selftest").click().run())
    _test_finished(db)  # in the background: the page needn't stay open for it
    saved = json.loads(Store(db).get_setting("page_reader_test"))["models"][MODEL]
    assert saved["ok"] and saved["fields_right"] == 2 and saved["model"] == MODEL
    assert "page_reader_tested" in [e["action"] for e in Store(db).events()]
    _ok(at.run())
    assert [e for e in at.expander if e.label.startswith("What it read on the test invoice (Read 2 of 2 fields")]


def test_a_self_test_under_way_shows_its_progress_and_isnt_started_twice(db, status, monkeypatch):
    """The self-test runs in the background: the tab says since when and how many pages are read; the button waits."""
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
    _ok(at.button(key="rd_selftest").click().run())
    try:
        assert started.wait(30)
        _ok(at.run())  # the page opened again (or another browser): the test is still under way
        shown = _captions(at).replace("\\", "")  # model names are escaped for markdown
        assert f"Self-test of {MODEL}… started " in shown and "1 of 2 pages read" in shown
        assert "close the browser" in shown
        assert at.button(key="rd_selftest").disabled
    finally:
        release.set()
    _test_finished(db)
    assert page_worker.saved_test(Store(db), MODEL)["ok"]


def test_the_self_test_table_shows_text_only(db, status):
    """Amounts and text in one column made Streamlit log an ArrowTypeError on every look at the tab."""
    from ap_coder import page_worker

    rows = [{"field": "invoice_number", "expected": "NW-2026-0912", "read": "NW-2026-0912", "match": True},
            {"field": "tax_total", "expected": 2072.85, "read": None, "match": False},
            {"field": "grand_total", "expected": 18017.85, "read": 18017.85, "match": True}]  # fmt: skip
    page_worker.save_test(Store(db), {"model": MODEL, "ok": False, "fields_right": 2, "fields_total": 3, "rows": rows})
    at = _ok(_settings_page().run())
    table = next(d.value for d in at.dataframe if "On the invoice" in d.value.columns)
    for column in ("On the invoice", "OvisOCR2 read"):
        assert all(isinstance(v, str) for v in table[column]), table[column].tolist()
    assert table["On the invoice"].tolist() == ["NW-2026-0912", "2072.85", "18017.85"]
    assert table["OvisOCR2 read"].tolist()[1] == ""


def test_the_wait_for_the_self_test_is_per_page_times_its_pages(db, monkeypatch):
    from ap_coder.webapp.reading_settings import _test_page_count, test_wait

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


def test_the_models_lm_studio_has_and_loading_one(db, status, monkeypatch):
    rows = [{"model": MODEL, "loaded": True, "context": 20480, "vision": True, "document_reader": True,
             "used_for": "reads pages"},
            {"model": "qwen3.5-9b", "loaded": False, "context": 0, "vision": False, "document_reader": False,
             "used_for": "suggests GL accounts"}]  # fmt: skip
    loaded = []
    monkeypatch.setattr(page_reader, "lm_studio_models", lambda settings, use_cache=True: rows)
    monkeypatch.setattr(page_reader, "load_model", lambda settings, model: loaded.append(model) or "")
    at = _ok(_settings_page().run())
    tables = [d.value for d in at.dataframe if "Model" in d.value.columns and "Loaded" in d.value.columns]
    assert len(tables) == 1  # one table, on the Reading tab
    assert MODEL in tables[0]["Model"].tolist() and "yes, 20,480 tokens" in tables[0]["Loaded"].tolist()
    assert at.selectbox(key="lm_models_pick_reading").options == ["qwen3.5-9b"]
    _ok(at.button(key="lm_models_load_reading").click().run())
    assert loaded == ["qwen3.5-9b"]


def test_setup_steps_follow_the_one_way_of_reading(tmp_path):
    from ap_coder.webapp.reading_settings import setup_steps

    steps = {label: (state, detail) for state, label, detail in setup_steps(working(), True, (True, "LM Studio · q"))}
    assert [s for s, _ in steps.values()] == ["ok", "ok", "ok", "ok"]
    assert steps["OvisOCR2 is in LM Studio"][1] == f"found: {MODEL}"
    steps = {label: (state, detail) for state, label, detail in setup_steps(broken(), False, (False, ""))}
    assert steps["LM Studio is running"][0] == "todo"
    assert "start LM Studio first" in steps["OvisOCR2 is in LM Studio"][1]
    assert steps["Self-test passed (automatic)"] == ("todo", "runs by itself once OvisOCR2 is found")
    assert steps["Chat model for GL suggestions (optional)"][0] == "opt"  # optional, never a to-do
    failed = setup_steps(working(self_test="failed"), True, (False, ""))
    assert failed[2][0] == "bad" and "Run the self-test again" in failed[2][2]


# --- The banner on the working pages --------------------------------------------------------------------------------


def _banner(at) -> str:
    return " ".join(w.value for w in at.warning).replace("\\", "")  # untrusted text is escaped for markdown


PROCESS = "from ap_coder.webapp.process import page_process\npage_process()"


def _process_page():
    _ok(AppTest.from_file(APP, default_timeout=TIMEOUT).run())  # registers the pages the process page links to
    return AppTest.from_string(PROCESS, default_timeout=TIMEOUT)


def test_the_banner_shows_on_the_process_page(db, status):
    status.now = broken()
    at = _process_page()
    status.asked.clear()
    _ok(at.run())
    assert "OvisOCR2 isn't in LM Studio: invoices wait for it" in _banner(at)
    assert status.asked and all(status.asked)  # from the cache: cheap


def test_the_banner_shows_on_the_review_queue_with_a_link_to_settings(db, status):
    from ap_coder.demo import load_demo

    load_demo(Store(db))
    status.now = broken()
    at = _ok(AppTest.from_file(APP, default_timeout=TIMEOUT).run())
    assert "OvisOCR2 isn't in LM Studio" in _banner(at)
    links = [p for p in at.get("page_link") if "Settings → Reading" in str(p.proto)]
    assert links


def test_no_banner_when_every_reader_works_or_the_status_fails(db, status):
    at = _ok(_process_page().run())
    assert "OvisOCR2" not in _banner(at)
    status.now = RuntimeError("no status")
    at = _ok(AppTest.from_string(PROCESS, default_timeout=TIMEOUT).run())  # never breaks the page
    assert "OvisOCR2" not in _banner(at)


def test_the_banner_is_not_on_other_pages(db, status):
    status.now = broken()
    at = _ok(AppTest.from_string("from ap_coder.webapp.spend import page_spend\npage_spend()",
                                 default_timeout=TIMEOUT).run())  # fmt: skip
    assert "OvisOCR2" not in _banner(at)


# --- OvisOCR2's line on the review screen ---------------------------------------------------------------------------


def _invoice(db, queued=True):
    """One sample invoice processed, in OvisOCR2's queue (``queued``) or not."""
    import os

    from ap_coder.config import Settings
    from ap_coder.page_worker import TEST_KEY
    from ap_coder.pipeline import InvoicePipeline

    store = Store(db)
    load_sample_setup(store)
    store.set_setting(TEST_KEY, json.dumps({"model": MODEL, "ok": True, "fields_right": 9, "fields_total": 9}))
    settings = Settings.from_env(os.environ["AP_ENV_FILE"])
    result = InvoicePipeline(settings, store.reference_data(), store=store).process(SAMPLES / f"{STEM}.pdf")
    assert result.ok, result.error
    with store._conn() as conn:  # however the pipeline queued it, the test says
        conn.execute("DELETE FROM page_reads WHERE invoice_id = ?", (result.invoice_id,))
    if queued:
        store.queue_page_read(result.invoice_id, "new invoice")
    return store, result.invoice_id, settings


def _review(invoice_id):
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.session_state["open_invoice"] = invoice_id
    return at


def test_waiting_for_ovisocr2_says_how_many_are_ahead_and_how_long(db, status):
    store, invoice_id, _ = _invoice(db)
    for other in (901, 902):  # queued after it
        store.queue_page_read(other, "new invoice")
    with store._conn() as conn:
        conn.execute("UPDATE page_reads SET created_at = '2999-01-01T00:00:00' WHERE invoice_id IN (901, 902)")
    at = _ok(_review(invoice_id).run())
    shown = _captions(at)
    assert "Waiting for OvisOCR2: it's next, about 4 minutes." in shown  # 12 minutes for 3: a third of it
    assert "can't be approved without a person until OvisOCR2 has read it" in shown
    assert not [b for b in at.button if b.key == f"inv{invoice_id}_read_pages"]  # nothing to ask for

    store.queue_page_read(900, "new invoice")
    with store._conn() as conn:
        conn.execute("UPDATE page_reads SET created_at = '2000-01-01T00:00:00' WHERE invoice_id = 900")
    _ok(at.run())
    assert "Waiting for OvisOCR2: 1 invoice ahead, about 8 minutes." in _captions(at)


def test_when_ovisocr2_isnt_running_the_invoice_says_so(db, status):
    status.now = broken()
    _, invoice_id, _ = _invoice(db)
    at = _ok(_review(invoice_id).run())
    shown = _captions(at)
    assert "OvisOCR2 isn't running: see Settings → Reading." in shown
    assert "can't be approved without a person until OvisOCR2 has read it" in shown


def test_when_ovisocr2_couldnt_read_it_a_person_decides(db, status):
    store, invoice_id, _ = _invoice(db)
    store.next_page_read(invoice_id)
    store.finish_page_read(invoice_id, "failed", MODEL, 1, 3.0, "LM Studio stopped answering.")
    at = _ok(_review(invoice_id).run())
    assert "OvisOCR2 couldn't read it: LM Studio stopped answering; a person decides." in _captions(at)


def test_an_invoice_ovisocr2_failed_to_read_can_be_added_again(db, status):
    """Cut off by the model server MAX_TRIES times, the invoice leaves the queue as failed, and its message says to
    read it again from the invoice (Add it to OvisOCR2's queue): that button is there."""
    store, invoice_id, _ = _invoice(db)
    store.next_page_read(invoice_id)
    message = ("the model server cut the reading off 3 times (no answer); read it again from the invoice (Add it to "
               "OvisOCR2's queue) once LM Studio is running with the model loaded")  # fmt: skip
    store.finish_page_read(invoice_id, "failed", MODEL, 0, 3.0, message, tries=3)
    at = _ok(_review(invoice_id).run())
    assert "OvisOCR2 couldn't read it" in _captions(at)
    _ok(at.button(key=f"inv{invoice_id}_read_pages").click().run())
    assert store.page_read(invoice_id)["status"] == "waiting" and store.page_read(invoice_id)["tries"] == 0


def test_an_invoice_not_in_its_queue_can_be_added(db, status):
    store, invoice_id, _ = _invoice(db, queued=False)
    at = _ok(_review(invoice_id).run())
    assert "OvisOCR2 hasn't read this invoice yet" in _captions(at)
    _ok(at.button(key=f"inv{invoice_id}_read_pages").click().run())
    assert store.page_read(invoice_id)["status"] == "waiting"
    assert "Waiting for OvisOCR2" in _captions(at)


def test_a_reading_that_arrives_while_the_invoice_is_open_is_shown(db, status, monkeypatch):
    from ap_coder import page_worker

    store, invoice_id, settings = _invoice(db)
    transcript = (SAMPLES / f"{STEM}.md").read_text(encoding="utf-8")

    def read(settings, path, model=None, on_page=None, should_stop=None):
        return page_reader.PageReading(model=MODEL, pages=[transcript], seconds=[120.0], page_count=1)

    monkeypatch.setattr(page_reader, "read_document", read)
    at = _ok(_review(invoice_id).run())
    assert "Waiting for OvisOCR2" in _captions(at)  # queued: waiting for its turn

    outcome = page_worker.read_one(settings, store)  # the background thread's work
    assert outcome.updated, outcome
    _ok(at.run())
    assert any("OvisOCR2 has read this invoice" in t.value for t in at.toast)
    shown = _captions(at)
    assert "OvisOCR2 read this invoice: " in shown and " fields agree" in shown
    assert "figures on the page read the same" in shown
    assert "can't be approved without a person" not in shown
    assert at.session_state[f"inv{invoice_id}_page_reader_seen"]


def test_an_edit_made_as_the_reading_arrives_is_kept(db, status):
    """The reading lands in the store, then the reviewer's first edit comes in before the screen looked again: the
    edit is on that same run, not counted yet, and must not be wiped by the fields being drawn from the reading."""
    store, invoice_id, _ = _invoice(db)
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


def test_agreement_line_counts_every_field_compared():
    from ap_coder.webapp.capture_panel import agreement_line

    line = agreement_line({"agree": ["a", "b", "c"], "differ": ["d"], "only": ["e"], "seconds": 90})
    assert line == ("OvisOCR2 read this invoice: 3 of 5 fields agree (1 read differently, marked Check; 1 found only "
                    "by OvisOCR2) · 1.5 min")  # fmt: skip
