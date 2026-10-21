"""Settings → Reading, the reading banner and OvisOCR2's line on the review screen (ap_coder/web), with a faked reading
status and page reader: nothing to choose, every reader's state shown, the self-test run again in the background,
cloud services only in a developer build, and the invoice's own state with OvisOCR2."""

import html
import json
import re
import time

import pytest

from ap_coder.ai import page_reader, page_worker, reading
from ap_coder.ai.reading import ReaderLine, ReadingStatus
from ap_coder.core.store import Store, load_sample_setup
from ap_coder.web.pages import settings_reading
from ap_coder.web.pages.settings import TAB_KEYS

from .conftest import ROOT, SAMPLES
from .test_web_invoice import form_of
from .webclient import client_for, follow, post, web_settings

STEM = "harbourview_NS_HST_HPS-2026-0347"
MODEL = "ath-maas_ovisocr2"
TAB_NAMES = ["Review", "Reading", "Automation", "Data & backups", "About"]  # JD Edwards E1 hidden (D-52)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_ENV_FILE", str(tmp_path / "private" / ".env"))
    return Store(tmp_path / "private" / "ap_coder.db")


@pytest.fixture
def client(store):
    return client_for(store)


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
        figures_agree=0.97, figures_count=120, lm_studio=True,
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
        page_reader_model="", page_reader_ready=False, self_test="no model", queue=4, lm_studio=True,
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


def _text(page: str) -> str:
    """A page as its words read (tags dropped, entities decoded)."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))


def _self_test_button(page: str) -> str:
    return page.split('action="/settings/reading/self-test"', 1)[1].split("</button>", 1)[0]


def _test_finished(store, seconds=30):
    deadline = time.monotonic() + seconds
    while page_worker.test_under_way(store) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not page_worker.test_under_way(store), "the test never finished"


# --- Settings: the tabs, nothing to choose -------------------------------------------------------------------------


def test_settings_tabs_are_review_reading_automation_data_about(client, status):
    page = client.get("/settings").text
    tabs = re.findall(r'href="/settings/(\w+)"[^>]*>(?:<span[^>]*>[^<]*</span>)?\s*([^<]+)<', page)
    assert [key for key, _ in tabs][:5] == ["review", "reading", "automation", "data", "about"]
    assert [html.unescape(name).strip() for _, name in tabs][:5] == TAB_NAMES
    assert sorted(TAB_KEYS) == sorted([*(key for key, _ in tabs[:5]), "jde"])
    labels = " ".join(name for _, name in tabs)
    assert "AI model" not in labels and "Page reader" not in labels and "Azure" not in labels


def test_every_settings_tab_the_app_names_exists():
    """No message points AP to a Settings tab that is gone ("Settings → AI model", "Settings → Page reader")."""
    named = []
    for path in [
        *(ROOT / "ap_coder").rglob("*.py"),
        *(ROOT / "ap_coder").rglob("*.html"),
        *(ROOT / "scripts").rglob("*.py"),
    ]:
        source = re.sub(r"\"\s*\n\s*(f?)\"", "", path.read_text(encoding="utf-8"))  # strings split over lines
        named += [
            (path.name, m.group(1)) for m in re.finditer(r"Settings (?:→|->|>|&rarr;) ([A-Za-z&][\w &;]*)", source)
        ]
    assert named
    wrong = [
        (name, tab)
        for name, tab in named
        if not any(tab.startswith(t) or t.startswith(tab) for t in TAB_NAMES)
        and not tab.startswith(("JD", "Data &amp;"))
    ]  # fmt: skip  (JD Edwards E1: hidden, D-52)
    assert not wrong, wrong


def test_there_is_nothing_to_choose_about_reading(client, status):
    page = client.get("/settings/reading").text
    for gone in ("pr_mode", "pr_scope", "pr_model", "pr_base_url", "llm_provider", "llm_model", "llm_vision",
                 "llm_base_url"):  # fmt: skip
        assert f'name="{gone}"' not in page, gone
    text = _text(page)
    assert "page images" not in text and "When it reads" not in text and "Which invoices" not in text
    assert "linked" not in text.lower()  # it is a self-test now


def test_a_developer_build_shows_cloud_services_inside_reading(client, status, monkeypatch):
    monkeypatch.setenv("AP_ALLOW_INTERNET", "1")
    text = _text(client.get("/settings/reading").text)
    assert "Developer: cloud services" in text and "Document Intelligence endpoint" in text
    assert "The offline build ignores these settings" in text


# --- Settings → Reading ---------------------------------------------------------------------------------------------


def test_reading_tab_when_every_reader_works(client, status):
    page = client.get("/settings/reading").text
    text = _text(page)
    assert "How AP Coder reads every invoice" in text and "OvisOCR2 reads every page too" in text
    for name in ("PDF text layer", "OCR (two engines)", "Page reader (OvisOCR2)", "Rule reader"):
        assert name in text, name
    assert "Working" in text and "Not working" not in text
    assert "Found in LM Studio" in text and MODEL in text and "Self-test passed" in text
    assert "read 9 of 9 fields right" in text
    assert "3 invoices waiting" in text and "about 12 minutes" in text
    assert "read 97% of figures the same as the PDF's own text (120 figures)" in text
    assert " disabled" not in _self_test_button(page)
    assert "LM Studio is running" in text and "OvisOCR2 is in LM Studio" in text
    assert "Self-test passed (automatic)" in text and "Chat model for GL suggestions (optional)" in text


def test_reading_tab_when_ovisocr2_is_missing(client, status, monkeypatch):
    status.now = broken()
    monkeypatch.setattr(page_reader, "reader_status", lambda settings, use_cache=True: _reader_status("missing", ""))
    page = client.get("/settings/reading").text
    text = _text(page)
    assert "Not working" in text and "Needs a look" in text and "Not in LM Studio" in text
    assert "No self-test: OvisOCR2 isn't in LM Studio" in text
    assert " disabled" in _self_test_button(page)
    assert "ATH-MaaS_OvisOCR2-GGUF" in text and "nothing is downloaded" in text  # copied in, never downloaded
    assert "4 invoices waiting" in text


def test_reading_tab_when_lm_studio_is_not_running(client, status, monkeypatch):
    status.now = broken(page_reader_model="", readers=[], lm_studio=False)
    monkeypatch.setattr(page_reader, "reader_status", lambda settings, use_cache=True: _reader_status("down", ""))
    monkeypatch.setattr(page_reader, "lm_studio_models", lambda settings, use_cache=True: None)
    text = _text(client.get("/settings/reading").text)
    assert "IT team installs it" in text and "Start server" in text and "lmstudio.ai" not in text
    assert "start LM Studio first to check" in text
    assert "LM Studio isn't answering" in text
    assert "ATH-MaaS_OvisOCR2-GGUF" not in text  # the copy-in note waits until LM Studio can be asked


def test_reading_tab_when_the_status_cannot_be_read(client, status):
    status.now = RuntimeError("database locked")
    page = client.get("/settings/reading")
    assert page.status_code == 200 and "couldn't check the readers just now" in _text(page.text)
    for key in TAB_KEYS:  # the rest of Settings still works
        assert client.get(f"/settings/{key}").status_code == 200


def test_check_again_asks_afresh(client, status):
    client.get("/settings/reading")
    status.asked.clear()
    follow(client, post(client, "/settings/reading/check"))
    assert False in status.asked  # not from the cache


def test_the_self_test_runs_in_the_background_and_its_result_is_kept(client, store, status, tested):
    page = follow(client, post(client, "/settings/reading/self-test"))
    assert f"Self-test of {MODEL} started in the background (about 6 minutes)" in _text(page)
    _test_finished(store)  # in the background: the page needn't stay open for it
    saved = json.loads(store.get_setting("page_reader_test"))["models"][MODEL]
    assert saved["ok"] and saved["fields_right"] == 2 and saved["model"] == MODEL
    assert "page_reader_tested" in [e["action"] for e in store.events()]
    assert "What it read on the test invoice (Read 2 of 2 fields" in _text(client.get("/settings/reading").text)


def test_a_self_test_under_way_shows_its_progress_and_isnt_started_twice(client, store, status, monkeypatch):
    """The self-test runs in the background: the tab says since when and how many pages are read; the button waits."""
    import threading

    release, started = threading.Event(), threading.Event()

    def slow(settings, model=None, on_page=None):
        on_page(1, 2)
        on_page(2, 2)
        started.set()
        release.wait(30)
        return page_reader.ReaderTest(model=model, ok=True, seconds=1157.0, rows=[], fields_right=9, fields_total=9,
                                      when="2026-10-10T22:27")  # fmt: skip

    monkeypatch.setattr(page_reader, "test_reader", slow)
    follow(client, post(client, "/settings/reading/self-test"))
    try:
        assert started.wait(30)
        page = client.get("/settings/reading").text  # the page opened again (or another browser)
        text = _text(page)
        assert f"Self-test of {MODEL} … started " in text and "1 of 2 pages read" in text
        assert "close the browser" in text
        assert " disabled" in _self_test_button(page)
        answer = post(client, "/settings/reading/self-test").json()
        assert "already running" in answer["toast"]["text"]
    finally:
        release.set()
    _test_finished(store)
    assert page_worker.saved_test(store, MODEL)["ok"]


def test_the_self_test_table_shows_each_value_as_text(client, store, status):
    rows = [{"field": "invoice_number", "expected": "NW-2026-0912", "read": "NW-2026-0912", "match": True},
            {"field": "tax_total", "expected": 2072.85, "read": None, "match": False},
            {"field": "grand_total", "expected": 18017.85, "read": 18017.85, "match": True}]  # fmt: skip
    page_worker.save_test(store, {"model": MODEL, "ok": False, "fields_right": 2, "fields_total": 3, "rows": rows})
    page = client.get("/settings/reading").text
    table = page.split("On the invoice", 1)[1].split("</table>", 1)[0]
    cells = [_text(c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", table, re.S)]
    assert "2072.85" in cells and "18017.85" in cells and "None" not in cells


def test_the_wait_for_the_self_test_is_per_page_times_its_pages(monkeypatch):
    asked = []

    def estimate(settings, model=None):
        asked.append(model)
        return speeds[model]

    speeds = {MODEL: 90.0, "fast": 2.0, "new": None}
    monkeypatch.setattr(page_reader, "page_seconds_estimate", estimate)
    assert settings_reading.test_page_count() == 2  # the test invoice's two pages
    assert settings_reading.test_wait(None, MODEL) == "about 3 minutes"  # 90 s a page, two pages
    assert settings_reading.test_wait(None, "fast") == "about 10 seconds"  # never "about 0 min"
    assert settings_reading.test_wait(None, "new") == "about 10–20 minutes on a laptop without a graphics card"
    assert asked == [MODEL, "fast", "new"]  # the model being tested, not whichever read last


def test_setup_steps_follow_the_one_way_of_reading():
    setup_steps = settings_reading.setup_steps
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


BANNER = '<div class="banner warn"'


def test_the_banner_shows_on_the_process_page_from_the_cache(client, status):
    status.now = broken()
    status.asked.clear()
    page = client.get("/process").text
    assert BANNER in page and "OvisOCR2 isn't in LM Studio: invoices wait for it" in _text(page)
    assert status.asked and all(status.asked)  # from the cache: cheap


def test_the_banner_shows_on_the_review_queue_with_a_link_to_settings(client, store, status):
    from ap_coder.ops.demo import load_demo

    load_demo(store)
    status.now = broken()
    page = client.get("/review").text
    banner = page.split(BANNER, 1)[1].split("</div>", 1)[0]
    assert "OvisOCR2 isn't in LM Studio" in _text(banner) and "Open Settings → Reading" in _text(banner)
    assert 'href="/settings/reading"' in banner


def test_no_banner_when_every_reader_works_or_the_status_fails(client, status):
    assert BANNER not in client.get("/process").text
    status.now = RuntimeError("no status")
    page = client.get("/process")
    assert page.status_code == 200 and BANNER not in page.text  # never breaks the page


def test_the_banner_is_not_on_other_pages(client, status):
    status.now = broken()
    assert BANNER not in client.get("/spend").text


# --- OvisOCR2's line on the review screen ---------------------------------------------------------------------------


def _invoice(store, queued=True):
    """One sample invoice processed, in OvisOCR2's queue (``queued``) or not."""
    from ap_coder.ai.page_worker import TEST_KEY
    from ap_coder.ai.pipeline import InvoicePipeline

    load_sample_setup(store)
    store.set_setting(TEST_KEY, json.dumps({"model": MODEL, "ok": True, "fields_right": 9, "fields_total": 9}))
    settings = web_settings()
    result = InvoicePipeline(settings, store.reference_data(), store=store).process(SAMPLES / f"{STEM}.pdf")
    assert result.ok, result.error
    with store._conn() as conn:  # however the pipeline queued it, the test says
        conn.execute("DELETE FROM page_reads WHERE invoice_id = ?", (result.invoice_id,))
    if queued:
        store.queue_page_read(result.invoice_id, "new invoice")
    return result.invoice_id, settings


def _reader_line(client, invoice_id) -> str:
    return _text(client.get(f"/review/{invoice_id}/reader").json()["html"])


def test_waiting_for_ovisocr2_says_how_many_are_ahead_and_how_long(client, store, status):
    invoice_id, _ = _invoice(store)
    for other in (901, 902):  # queued after it
        store.queue_page_read(other, "new invoice")
    with store._conn() as conn:
        conn.execute("UPDATE page_reads SET created_at = '2999-01-01T00:00:00' WHERE invoice_id IN (901, 902)")
    page = client.get(f"/review/{invoice_id}").text
    shown = _text(page)
    assert "Waiting for OvisOCR2: it's next, about 4 minutes." in shown  # 12 minutes for 3: a third of it
    assert "can't be approved without a person until OvisOCR2 has read it" in shown
    assert f"/review/{invoice_id}/read-pages" not in page  # nothing to ask for

    store.queue_page_read(900, "new invoice")
    with store._conn() as conn:
        conn.execute("UPDATE page_reads SET created_at = '2000-01-01T00:00:00' WHERE invoice_id = 900")
    assert "Waiting for OvisOCR2: 1 invoice ahead, about 8 minutes." in _reader_line(client, invoice_id)


def test_when_ovisocr2_isnt_running_the_invoice_says_so(client, store, status):
    status.now = broken()
    invoice_id, _ = _invoice(store)
    shown = _text(client.get(f"/review/{invoice_id}").text)
    assert "OvisOCR2 isn't running: see Settings → Reading." in shown
    assert "can't be approved without a person until OvisOCR2 has read it" in shown


def test_when_ovisocr2_couldnt_read_it_a_person_decides(client, store, status):
    invoice_id, _ = _invoice(store)
    store.next_page_read(invoice_id)
    store.finish_page_read(invoice_id, "failed", MODEL, 1, 3.0, "LM Studio stopped answering.")
    assert "OvisOCR2 couldn't read it: LM Studio stopped answering; a person decides." in _text(
        client.get(f"/review/{invoice_id}").text
    )


def test_an_invoice_ovisocr2_failed_to_read_can_be_added_again(client, store, status):
    """Cut off by the model server MAX_TRIES times, the invoice leaves the queue as failed, and its message says to
    read it again from the invoice (Add it to OvisOCR2's queue): that button is there."""
    invoice_id, _ = _invoice(store)
    store.next_page_read(invoice_id)
    message = ("the model server cut the reading off 3 times (no answer); read it again from the invoice (Add it to "
               "OvisOCR2's queue) once LM Studio is running with the model loaded")  # fmt: skip
    store.finish_page_read(invoice_id, "failed", MODEL, 0, 3.0, message, tries=3)
    page = client.get(f"/review/{invoice_id}").text
    assert "OvisOCR2 couldn't read it" in _text(page) and f"/review/{invoice_id}/read-pages" in page
    post(client, f"/review/{invoice_id}/read-pages")
    assert store.page_read(invoice_id)["status"] == "waiting" and store.page_read(invoice_id)["tries"] == 0
    assert "Waiting for OvisOCR2" in _reader_line(client, invoice_id)


def test_a_reading_that_arrives_while_the_invoice_is_open_is_shown(client, store, status, monkeypatch):
    invoice_id, settings = _invoice(store)
    transcript = (SAMPLES / f"{STEM}.md").read_text(encoding="utf-8")

    def read(settings, path, model=None, on_page=None, should_stop=None):
        return page_reader.PageReading(model=MODEL, pages=[transcript], seconds=[120.0], page_count=1)

    monkeypatch.setattr(page_reader, "read_document", read)
    assert "Waiting for OvisOCR2" in _text(client.get(f"/review/{invoice_id}").text)  # queued: waiting for its turn
    assert client.get(f"/review/{invoice_id}/reader").json()["poll"]  # the page keeps looking

    outcome = page_worker.read_one(settings, store)  # the background thread's work
    assert outcome.updated, outcome
    progress = client.get(f"/review/{invoice_id}/reader").json()
    assert progress["state"] not in ("waiting", "reading") and not progress["poll"]
    shown = _text(client.get(f"/review/{invoice_id}").text)
    assert "OvisOCR2 read this invoice: " in shown and " fields agree" in shown
    assert "figures on the page read the same" in shown
    assert "can't be approved without a person" not in shown


def test_an_edit_made_as_the_reading_arrives_is_kept(client, store, status):
    """The reading lands in the store after the reviewer started editing: the edit is kept, not wiped by the fields
    being drawn from the reading, and the screen offers to start over from the reading."""
    invoice_id, _ = _invoice(store)
    inv = store.get_invoice(invoice_id)
    from .test_web_invoice import check

    check(client, invoice_id, form_of(store, invoice_id, invoice_number="CORRECTED-123"))
    read = dict(inv["ai_output"], po_number="PO-FROM-THE-READER")
    assert store.replace_proposal(invoice_id, read, inv["validation"], None,
                                  meta={"page_reader": {"model": MODEL, "at": "2026-10-10T12:00:00"}})  # fmt: skip
    page = client.get(f"/review/{invoice_id}").text
    assert 'value="CORRECTED-123"' in page
    assert "your edits are kept" in _text(page) and f"/review/{invoice_id}/start-over" in page


def test_agreement_line_counts_every_field_compared():
    from ap_coder.web.invoice_view import agreement_line

    line = agreement_line({"agree": ["a", "b", "c"], "differ": ["d"], "only": ["e"], "seconds": 90})
    assert line == ("OvisOCR2 read this invoice: 3 of 5 fields agree (1 read differently, marked Check; 1 found only "
                    "by OvisOCR2) · 1.5 min")  # fmt: skip
