"""The page reader's queue: which invoices are read, and how a reading is folded into an invoice (no real model:
the page reader's answers are faked)."""

import datetime as dt
import json
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ap_coder import page_reader, page_worker
from ap_coder.config import PageReaderSettings, Settings
from ap_coder.pipeline import InvoicePipeline
from ap_coder.store import APPROVED, REVIEW, Store, load_sample_setup

from .conftest import SAMPLES

STEM = "harbourview_NS_HST_HPS-2026-0347"
MODEL = "ath-maas_ovisocr2"


def _settings(**kw):
    s = Settings()
    return replace(s, llm=replace(s.llm, provider="off"), page_reader=PageReaderSettings(**kw))


@pytest.fixture
def store(tmp_path):
    """GL accounts set up, and the page reader linked: it passed its test in Settings → Page reader."""
    store = Store(tmp_path / "ap.db")
    load_sample_setup(store)
    store.set_setting(page_worker.TEST_KEY, json.dumps({"model": MODEL, "ok": True, "fields_right": 9,
                                                        "fields_total": 9}))  # fmt: skip
    return store


@pytest.fixture
def fake_reader(monkeypatch):
    """A page reader that is downloaded and reads the sample's own text (its markdown) as the transcription."""
    calls = {"load": 0, "read": 0}
    transcript = (SAMPLES / f"{STEM}.md").read_text(encoding="utf-8")

    def status(settings, use_cache=True):
        return page_reader.ReaderStatus(reachable=True, lm_studio=True, model=MODEL, document_reader=True,
                                        state="downloaded", candidates=[MODEL], note="")  # fmt: skip

    def load(settings, model=None):
        calls["load"] += 1
        return ""

    def read(settings, path, model=None, on_page=None, should_stop=None):
        calls["read"] += 1
        return page_reader.PageReading(model=model or MODEL, pages=[calls.get("text", transcript)], seconds=[95.0],
                                       page_count=1)  # fmt: skip

    monkeypatch.setattr(page_reader, "reader_status", status)
    monkeypatch.setattr(page_reader, "load_reader", load)
    monkeypatch.setattr(page_reader, "read_document", read)
    return calls


def _process(store, settings, path=None):
    result = InvoicePipeline(settings, store.reference_data(), store=store).process(path or SAMPLES / f"{STEM}.pdf")
    assert result.ok, result.error
    return result


# --- Which invoices go to the page reader ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "source", "wanted"),
    [
        ("scan.png", "", True),  # a photo or image
        ("photo.HEIC", "", True),  # an iPhone's photo
        ("scan.pdf", "ocr", True),  # a PDF that had to be OCR'd
        ("scan.pdf", "mixed", True),
        ("digital.pdf", "text", True),  # a digital PDF too: its hidden text is checked against the page
        ("invoice.md", "", False),  # a text file has no page to look at
        ("invoice.txt", "", False),
    ],
)
def test_every_invoice_is_read_but_a_text_file(name, source, wanted):
    assert page_worker.wants_reading(_settings(), Path(name), source) is wanted


def test_the_old_settings_no_longer_turn_the_page_reader_off(monkeypatch, tmp_path):
    """AP_PAGE_READER=off / ask and AP_PAGE_READER_SCOPE=scans in an older .env are ignored: every invoice is read."""
    for key, value in (("AP_PAGE_READER", "off"), ("AP_PAGE_READER_SCOPE", "scans"), ("AP_PAGE_READER_MAX_PAGES", "3")):
        monkeypatch.setenv(key, value)
    settings = Settings.from_env(tmp_path / "missing.env")
    assert (settings.page_reader.mode, settings.page_reader.scope, settings.page_reader.max_pages) == ("auto", "all", 3)
    assert page_worker.wants_reading(settings, Path("digital.pdf"), "text")


def test_queueing_never_stops_processing():
    class Broken:
        def queue_page_read(self, invoice_id, reason, requested_by=""):
            raise RuntimeError("database is locked")

    settings = _settings()
    assert page_worker.queue_new_invoice(Broken(), settings, 7, Path("a.pdf"), "text") is False
    assert page_worker.queue_new_invoice(None, settings, 7, Path("a.pdf"), "text") is False
    assert page_worker.queue_new_invoice(Broken(), settings, None, Path("a.pdf"), "text") is False


def test_processing_queues_every_invoice_but_a_text_file(store):
    digital = _process(store, _settings())
    assert store.page_read(digital.invoice_id)["status"] == "waiting"  # a digital PDF is read too
    assert store.page_read(digital.invoice_id)["reason"] == "new invoice"
    text = _process(store, _settings(), SAMPLES / "pacific_BC_GST_PST_PO-77120.md")
    assert store.page_read(text.invoice_id) is None  # a text file has no page to look at
    assert store.page_reads_waiting() == 1


def test_agreement_follows_what_capture_decided():
    """A field's sources are the readers behind the value shown ("vlm": the page reader, as the text it read the value
    from) and the other values read ("other:<value>": who read it). Agreement is what capture's fusion decided, not
    the raw text compared again."""

    def field(value, reasons=(), **sources):
        return SimpleNamespace(value=value, sources=sources, reasons=list(reasons))

    capture = SimpleNamespace(
        fields={
            "invoice_number": field("HPS-2026-0347", rules="HPS-2026-0347", vlm="HPS-2026-0347"),
            # The page reader read the amount from "@ 13% 202.97": the same value to fusion (was "reads differently").
            "hst_amount": field(202.97, ocr2="202.97", rules="202.97", vlm="@ 13% 202.97"),
            "grand_total": field(1150.0, rules="1,150.00", **{"other:1105.0": "vlm"}),  # it read another value
            # Shown as the page reader read it, but OCR read otherwise and capture marked it Check: read differently.
            "po_number": field(
                "098835",
                ["the page reader read 098835, ocr2 read p.0"],
                rules="098835",
                vlm="098835",
                **{"other:p.0": "ocr2"},
            ),
            "payment_terms": field("Net 30", vlm="Net 30"),
            "due_date": field("2026-03-01", rules="2026-03-01"),  # not read by the page reader: left out
            "currency": field("", vlm="CAD"),  # nothing shown: nothing to compare
        }
    )
    assert page_worker.agreement(capture) == {
        "agree": ["invoice_number", "hst_amount"],
        "differ": ["grand_total", "po_number"],
        "only": ["payment_terms"],
    }
    assert page_worker.agreement(None) == {"agree": [], "differ": [], "only": []}


# --- Reading one invoice ---------------------------------------------------------------------------------------


def test_a_reading_is_folded_into_an_untouched_invoice(store, fake_reader):
    settings = _settings()
    first = _process(store, settings)
    before = store.get_invoice(first.invoice_id)

    outcome = page_worker.read_one(settings, store)

    assert outcome is not None and outcome.status == "done" and outcome.updated, outcome
    assert outcome.invoice_id == first.invoice_id and outcome.model == MODEL and outcome.pages == 1
    assert fake_reader == {"load": 1, "read": 1}
    after = store.get_invoice(first.invoice_id)
    assert after["status"] == REVIEW and after["id"] == before["id"]
    meta = after["meta"]
    reading = meta["page_reader"]
    assert reading["model"] == MODEL and reading["pages"] == 1 and reading["seconds"] == 95.0
    assert "invoice_number" in reading["agree"]  # the page reader read what OCR read
    assert meta.get("timings_seconds")  # the rest of the invoice's record is kept
    # Figure by figure against the PDF's own text (exact): the page reader's accuracy, counted for Settings.
    figs = reading["figures"]
    assert figs["confirmed"] >= 0.9 * max(figs["figures"], figs["first_figures"]) > 0
    same, seen = page_worker.figure_totals(store)["digital"]
    assert seen == max(figs["figures"], figs["first_figures"]) and same == figs["confirmed"]
    capture = store.get_capture(first.invoice_id)
    sources = {s for f in capture["fields"].values() for s in (f.get("sources") or {})}
    assert "vlm" in sources
    done = store.page_read(first.invoice_id)
    assert done["status"] == "done" and done["model"] == MODEL and done["pages"] == 1
    assert page_worker.read_one(settings, store) is None  # nothing left


def test_an_edited_invoice_keeps_what_ap_typed(store, fake_reader):
    """Approved with a correction, then reopened: back in review, with AP's coding as its starting point."""
    settings = _settings()
    first = _process(store, settings)
    store.approve_invoice(first.invoice_id, dict(first.output, invoice_number="TYPED-BY-AP"), "Pat")
    store.reopen(first.invoice_id, "Pat", "wrong cost center")
    assert store.get_invoice(first.invoice_id)["status"] == REVIEW

    outcome = page_worker.read_one(settings, store)

    assert outcome.status == "skipped" and not outcome.updated
    assert "already edited or decided" in outcome.message
    inv = store.get_invoice(first.invoice_id)
    assert inv["final_output"]["invoice_number"] == "TYPED-BY-AP" and "page_reader" not in (inv["meta"] or {})
    row = store.page_read(first.invoice_id)
    assert row["status"] == "skipped" and "already edited or decided" in row["error"]
    assert fake_reader == {"load": 0, "read": 0}  # its pages weren't read: no minutes spent for nothing


@pytest.mark.parametrize("decide", ["approve", "reject", "park"])
def test_a_decided_invoice_is_not_read(store, fake_reader, decide):
    settings = _settings()
    first = _process(store, settings)
    if decide == "approve":
        store.approve_invoice(first.invoice_id, first.output, "Pat")
    elif decide == "reject":
        store.reject_invoice(first.invoice_id, "Pat", "not ours")
    else:
        store.park_invoice(first.invoice_id, "Pat", "waiting for the buyer")
    before = store.get_invoice(first.invoice_id)

    outcome = page_worker.read_one(settings, store)

    assert outcome.status == "skipped" and not outcome.updated and "already" in outcome.message
    assert fake_reader == {"load": 0, "read": 0}
    assert store.page_read(first.invoice_id)["status"] == "skipped"
    after = store.get_invoice(first.invoice_id)
    assert after == before and (decide != "approve" or after["status"] == APPROVED)
    assert page_worker.read_one(settings, store) is None  # out of the queue


def test_a_reading_is_not_a_duplicate_of_the_invoice_itself(store, fake_reader):
    """The invoice is read again with the page reader's text while it is in the store: its own row is no duplicate
    of it."""
    settings = _settings()
    first = _process(store, settings)
    assert "DUPLICATE_INVOICE" not in {i["code"] for i in store.get_invoice(first.invoice_id)["validation"]["issues"]}

    outcome = page_worker.read_one(settings, store)

    assert outcome.status == "done" and outcome.updated, outcome
    issues = store.get_invoice(first.invoice_id)["validation"]["issues"]
    assert "DUPLICATE_INVOICE" not in {i["code"] for i in issues}, issues
    # A real duplicate (the same invoice processed twice) is still found when the second one is read.
    second = _process(store, settings)
    assert page_worker.read_one(settings, store).invoice_id == second.invoice_id
    issues = store.get_invoice(second.invoice_id)["validation"]["issues"]
    assert any(i["code"] == "DUPLICATE_INVOICE" and f"#{first.invoice_id}" in i["message"] for i in issues), issues


@pytest.mark.parametrize("pages_read", [0, 1])
def test_a_stopped_reading_keeps_its_place_in_line(store, fake_reader, monkeypatch, pages_read):
    """Stopped part way (time is up, the app is closing): nothing is folded in, and it waits to be read again."""
    settings = _settings()
    first = _process(store, settings)
    before = store.get_invoice(first.invoice_id)

    def stopped(settings, path, model=None, on_page=None, should_stop=None):
        return page_reader.PageReading(model=MODEL, pages=["Page one\nWidget 1,234.00"][:pages_read],
                                       seconds=[60.0][:pages_read], stopped=True, page_count=2)  # fmt: skip

    monkeypatch.setattr(page_reader, "read_document", stopped)
    outcome = page_worker.read_one(settings, store)

    assert outcome.status == "postponed" and not outcome.updated and outcome.pages == pages_read
    assert f"{pages_read} of 2" in outcome.message
    assert store.page_read(first.invoice_id)["status"] == "waiting" and store.page_reads_waiting() == 1
    after = store.get_invoice(first.invoice_id)
    assert after["ai_output"] == before["ai_output"] and "page_reader" not in after["meta"]


def test_a_reading_of_some_pages_only_is_not_folded_in(store, fake_reader, monkeypatch):
    settings = _settings()
    first = _process(store, settings)
    before = store.get_invoice(first.invoice_id)

    def partial(settings, path, model=None, on_page=None, should_stop=None):
        return page_reader.PageReading(model=MODEL, pages=["Page one\nWidget 1,234.00"], seconds=[60.0],
                                       page_count=2)  # fmt: skip

    monkeypatch.setattr(page_reader, "read_document", partial)
    outcome = page_worker.read_one(settings, store)

    assert outcome.status == "failed" and "1 of 2" in outcome.message
    assert store.get_invoice(first.invoice_id)["ai_output"] == before["ai_output"]


def _two_page_reader(monkeypatch, tmp_path, transcribe):
    """The real ``read_document`` on a two-page sample, with ``transcribe`` in place of the model."""
    monkeypatch.setattr(page_reader, "reader_status", lambda settings, use_cache=True: page_reader.ReaderStatus(
        reachable=True, lm_studio=True, model=MODEL, document_reader=True, state="downloaded", candidates=[MODEL],
        note=""))  # fmt: skip
    monkeypatch.setattr(page_reader, "load_reader", lambda settings, model=None: "")
    monkeypatch.setattr(page_reader, "cache_dir", lambda: tmp_path / "page-reader")
    monkeypatch.setattr(page_reader, "transcribe", transcribe)


TWO_PAGES = SAMPLES / "northwind_ON_HST_NW-2026-0912.pdf"


def test_run_queue_finishes_the_page_under_way(store, monkeypatch, tmp_path):
    """Time runs out while page 1 is read: page 1 is finished (and kept), page 2 isn't started, and the invoice keeps
    its place in line, so the next run reads page 2 only."""
    settings = _settings()
    first = _process(store, settings, TWO_PAGES)
    pages = []

    def slow(settings, png, model=None, should_stop=None):
        for _ in range(15):  # 1.5 s, past the deadline below, asking to stop all the while
            if should_stop and should_stop():
                raise page_reader.Stopped("stopped before the page was read")
            time.sleep(0.1)
        pages.append(png)
        return f"Page {len(pages)}\nWidget 1,234.00"

    _two_page_reader(monkeypatch, tmp_path, slow)
    seen = []
    done = page_worker.run_queue(settings, store, minutes=0.6 / 60, on_result=seen.append)

    assert done == [] and len(seen) == 1 and seen[0].status == "postponed", seen
    assert len(pages) == 1 and seen[0].pages == 1  # page 1 finished, page 2 not started
    row = store.page_read(first.invoice_id)
    assert row["status"] == "waiting" and row["pages"] == 1

    pages.clear()
    _two_page_reader(monkeypatch, tmp_path, lambda settings, png, model=None, should_stop=None: (
        pages.append(png) or "Page 2\nTotal 2,457.83"))  # fmt: skip
    outcome = page_worker.read_one(settings, store)
    assert outcome.status == "done" and outcome.pages == 2 and len(pages) == 1  # page 1 came from the disk


def test_the_background_reader_stops_mid_page(store, monkeypatch, tmp_path):
    """The dashboard closing (``should_stop`` without ``finish_page``) stops at once; the invoice keeps its place."""
    settings = _settings()
    first = _process(store, settings, TWO_PAGES)
    halt = threading.Event()

    def stopped(settings, png, model=None, should_stop=None):
        halt.set()
        if should_stop and should_stop():
            raise page_reader.Stopped("stopped before the page was read")
        return "never"

    _two_page_reader(monkeypatch, tmp_path, stopped)
    outcome = page_worker.read_one(settings, store, should_stop=halt.is_set)

    assert outcome.status == "postponed" and outcome.pages == 0
    assert store.page_read(first.invoice_id)["status"] == "waiting"


def test_queue_position_counts_who_is_ahead(store):
    """Where an invoice is in the page reader's line: those waiting before it (oldest first, as ``next_page_read``
    takes them) and the one being read now."""
    for invoice_id in (11, 12, 13):
        store.queue_page_read(invoice_id, "new invoice")
    with store._conn() as conn:  # queued a second apart, 13 first
        for invoice_id, at in ((13, "2026-10-10T09:00:00"), (11, "2026-10-10T09:00:01"), (12, "2026-10-10T09:00:02")):
            conn.execute("UPDATE page_reads SET created_at = ? WHERE invoice_id = ?", (at, invoice_id))

    assert [store.page_reads_ahead(i) for i in (13, 11, 12)] == [0, 1, 2]
    assert store.page_reads_ahead(99) == 0  # not in line
    assert store.next_page_read()["invoice_id"] == 13
    assert [store.page_reads_ahead(i) for i in (11, 12)] == [1, 2]  # 13 is being read: still ahead of both
    store.finish_page_read(13, "done")
    assert [store.page_reads_ahead(i) for i in (11, 12)] == [0, 1]


def test_figures_are_compared_on_the_pages_the_page_reader_read(store, fake_reader, monkeypatch):
    """At most AP_PAGE_READER_MAX_PAGES pages are read: OCR's later pages aren't counted as figures it missed."""
    from ap_coder import figures
    from ap_coder.figures import PAGE_BREAK, compare_figures, first_pages

    pages = [f"Page {p}\nLine A 1,{p}00.00\nLine B {p}50.75\nSubtotal 9,{p}12.40" for p in range(1, 8)]
    full = f"\n\n{PAGE_BREAK}\n\n".join(pages)
    read = "\n\n".join(pages[:5])
    assert compare_figures(full, read).share < 0.75  # against the whole text, 6 figures look missed
    assert compare_figures(first_pages(full, 5), read).share == 1.0
    assert first_pages(full, 9) == full and first_pages("no breaks 1,234.00", 1) == "no breaks 1,234.00"

    settings = _settings()
    _process(store, settings)
    seen = []
    monkeypatch.setattr(figures, "first_pages", lambda text, count: seen.append(count) or first_pages(text, count))
    assert page_worker.read_one(settings, store).status == "done"
    assert seen == [1]  # the one page it read


def test_not_enough_memory_leaves_it_waiting(store, fake_reader, monkeypatch):
    settings = _settings()
    first = _process(store, settings)
    monkeypatch.setattr(page_reader, "load_reader", lambda settings, model=None: "not enough memory to load it")

    outcome = page_worker.read_one(settings, store)

    assert outcome.status == "postponed" and "memory" in outcome.message
    assert store.page_read(first.invoice_id)["status"] == "waiting"
    assert fake_reader["read"] == 0


def test_a_failed_read_is_recorded_and_the_invoice_is_unchanged(store, fake_reader, monkeypatch):
    settings = _settings()
    first = _process(store, settings)
    before = store.get_invoice(first.invoice_id)

    def fails(settings, path, model=None, on_page=None, should_stop=None):
        raise page_reader.CutOff("the model kept repeating itself")

    monkeypatch.setattr(page_reader, "read_document", fails)
    outcome = page_worker.read_one(settings, store)

    assert outcome.status == "failed" and "repeating" in outcome.message
    row = store.page_read(first.invoice_id)
    assert row["status"] == "failed" and "repeating" in row["error"]
    assert store.get_invoice(first.invoice_id)["ai_output"] == before["ai_output"]


def test_a_blank_reading_is_a_failure_not_an_empty_invoice(store, fake_reader):
    settings = _settings()
    first = _process(store, settings)
    fake_reader["text"] = "   \n"

    outcome = page_worker.read_one(settings, store)

    assert outcome.status == "failed" and "nothing" in outcome.message
    assert store.get_invoice(first.invoice_id)["status"] == REVIEW


def test_a_missing_file_is_skipped(store, fake_reader, tmp_path):
    settings = _settings()
    copy = tmp_path / f"{STEM}.pdf"
    copy.write_bytes((SAMPLES / f"{STEM}.pdf").read_bytes())
    first = _process(store, settings, copy)
    copy.unlink()

    outcome = page_worker.read_one(settings, store)

    assert outcome.status == "skipped" and store.page_read(first.invoice_id)["status"] == "skipped"
    assert fake_reader["read"] == 0


def test_nothing_is_read_while_ovisocr2_is_missing(store, fake_reader, monkeypatch):
    first = _process(store, _settings())
    monkeypatch.setattr(page_reader, "reader_status", lambda settings, use_cache=True: page_reader.ReaderStatus(
        reachable=True, lm_studio=True, model="", document_reader=False, state="missing", candidates=[],
        note="OvisOCR2 isn't downloaded"))  # fmt: skip
    assert page_worker.read_one(_settings(), store) is None
    assert store.page_read(first.invoice_id)["status"] == "waiting"  # left in the queue
    assert fake_reader["read"] == 0


@pytest.mark.parametrize(
    "test",
    [
        None,  # never tested
        {"model": MODEL, "ok": False, "fields_right": 4, "fields_total": 9},  # failed its test
        {"model": "qwen3.5-9b", "ok": True, "fields_right": 9, "fields_total": 9},  # another model passed
    ],
)
def test_only_a_model_that_passed_its_self_test_reads(store, fake_reader, test):
    """The page reader is trusted once it passed its self-test: a model that hasn't (or another one than the one
    that passed) reads nothing, and the invoices wait for it."""
    settings = _settings()
    first = _process(store, settings)
    store.set_setting(page_worker.TEST_KEY, json.dumps(test) if test else "")

    assert page_worker.read_one(settings, store) is None
    model, why = page_worker.ready(settings, store)
    assert model == "" and ("self-test" in why or "test invoice" in why)
    assert store.page_read(first.invoice_id)["status"] == "waiting" and fake_reader["read"] == 0


def test_an_invoice_asked_for_is_read_first(store, fake_reader):
    settings = _settings()
    a = _process(store, settings)
    b = _process(store, settings)
    store.queue_page_read(a.invoice_id, "asked", requested_by="Pat")
    store.queue_page_read(b.invoice_id, "asked", requested_by="Pat")

    outcome = page_worker.read_one(settings, store, invoice_id=b.invoice_id)

    assert outcome.invoice_id == b.invoice_id
    assert store.page_read(a.invoice_id)["status"] == "waiting"


def test_run_queue_reads_until_the_queue_is_empty(store, fake_reader):
    settings = _settings()
    ids = [_process(store, settings).invoice_id for _ in range(2)]
    seen = []

    done = page_worker.run_queue(settings, store, minutes=5, on_result=seen.append)

    assert sorted(o.invoice_id for o in done) == sorted(ids) and seen == done
    assert all(o.status == "done" for o in done)
    assert store.page_reads_waiting() == 0


def test_the_background_thread_reads_and_stops(store, fake_reader, monkeypatch):
    settings = _settings()
    first = _process(store, settings)
    read = threading.Event()
    real = page_worker.read_one

    def read_and_tell(*args, **kwargs):
        outcome = real(*args, **kwargs)
        if outcome is not None:
            read.set()
        return outcome

    monkeypatch.setattr(page_worker, "read_one", read_and_tell)
    worker = page_worker.BackgroundReader(lambda: settings, lambda: store)
    worker.start()
    try:
        assert read.wait(60), "the background reader never read the waiting invoice"
    finally:
        worker.stop()
        worker.join(10)
    assert not worker.is_alive()
    assert worker.last is not None and worker.last.invoice_id == first.invoice_id
    assert store.page_read(first.invoice_id)["status"] == "done"


def test_read_pages_command(store, fake_reader, tmp_path, monkeypatch, capsys):
    from ap_coder import cli

    env = tmp_path / "pr.env"
    env.write_text("", encoding="utf-8")
    # Set here, not in the .env: what load_dotenv puts in os.environ would outlive the test.
    first = _process(store, _settings())
    db = str(store.path)

    assert cli.main(["--env-file", str(env), "--db", db, "read-pages", "--minutes", "5", "--cache-dir", ""]) == 0
    err = capsys.readouterr().err
    assert "1 invoice(s) waiting" in err and f"invoice {first.invoice_id}: done" in err and "Read 1;" in err
    assert store.page_read(first.invoice_id)["status"] == "done"

    # One invoice by hand: queued again and read at once (from the page reader's cache in real use).
    assert cli.main(["--env-file", str(env), "--db", db, "read-pages", "--invoice", str(first.invoice_id),
                     "--cache-dir", ""]) == 0  # fmt: skip
    assert fake_reader["read"] == 2

    monkeypatch.setattr(page_reader, "reader_status", lambda settings, use_cache=True: page_reader.ReaderStatus(
        reachable=True, lm_studio=True, model="", document_reader=False, state="missing", candidates=[],
        note="OvisOCR2 isn't in LM Studio"))  # fmt: skip
    assert cli.main(["--env-file", str(env), "--db", db, "read-pages", "--cache-dir", ""]) == 1
    assert "Nothing read: OvisOCR2 isn't in LM Studio" in capsys.readouterr().err


def test_doctor_reports_the_page_reader_and_never_fails_on_it(store, fake_reader, monkeypatch):
    from ap_coder import doctor
    from ap_coder.doctor import PASS, WARN

    monkeypatch.setenv("AP_DB_PATH", str(store.path))
    area, level, detail = doctor._page_reader_check(_settings())
    assert (area, level) == ("page reader", PASS)
    assert MODEL in detail and "found in LM Studio" in detail and "self-test passed" in detail
    store.set_setting(page_worker.TEST_KEY, "")
    level, detail = doctor._page_reader_check(_settings())[1:]
    assert level == WARN and "self-test pending" in detail and "starts on its own" in detail
    page_worker.save_test(store, {"model": MODEL, "ok": False, "fields_right": 4, "fields_total": 9,
                                  "when": "2026-10-10T22:27:00", "problem": "Only 4 of 9 fields right."})  # fmt: skip
    level, detail = doctor._page_reader_check(_settings())[1:]
    assert level == WARN and "self-test failed" in detail and "Only 4 of 9 fields right" in detail
    monkeypatch.setattr(page_reader, "reader_status", lambda settings, use_cache=True: page_reader.ReaderStatus(
        reachable=False, lm_studio=False, model="", document_reader=False, state="down", candidates=[],
        note="LM Studio isn't answering"))  # fmt: skip
    assert doctor._page_reader_check(_settings())[1:] == (WARN, "LM Studio isn't answering")


def test_launcher_line(store, fake_reader, monkeypatch):
    import importlib.util
    import sys

    from .conftest import ROOT

    spec = importlib.util.spec_from_file_location("launch_for_page_reader", ROOT / "scripts" / "launch.py")
    launch = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, launch)
    spec.loader.exec_module(launch)
    monkeypatch.setenv("AP_PAGE_READER", "off")  # an older .env's setting: ignored
    monkeypatch.setenv("AP_DB_PATH", str(store.path))
    assert launch.page_reader_line() == ("ok", f"Page reader: {MODEL} found, self-test passed (reads every page of "
                                         "every invoice)")  # fmt: skip
    store.set_setting(page_worker.TEST_KEY, "")
    assert launch.page_reader_line() == ("info", f"Page reader: {MODEL} found; its self-test runs on its own before "
                                         "it reads invoices")  # fmt: skip
    page_worker.save_test(store, {"model": MODEL, "ok": False, "fields_right": 4, "fields_total": 9})
    assert launch.page_reader_line()[0] == "warn" and "failed its self-test" in launch.page_reader_line()[1]
    monkeypatch.setattr(page_reader, "reader_status", lambda settings, use_cache=True: page_reader.ReaderStatus(
        reachable=True, lm_studio=True, model="", document_reader=False, state="missing", candidates=[]))  # fmt: skip
    assert launch.page_reader_line() == ("warn", "Page reader: OvisOCR2 isn't running in LM Studio: invoices are read "
                                         "by OCR only and wait for a person")  # fmt: skip


def test_the_background_thread_waits_while_ovisocr2_is_missing(store, monkeypatch):
    """No OvisOCR2 in LM Studio: no other model reads instead, nothing is tested, the invoices wait."""
    rounds = []
    missing = page_reader.ReaderStatus(reachable=True, lm_studio=True, model="", document_reader=False,
                                       state="missing", candidates=["qwen3.5-9b"],
                                       note="OvisOCR2 isn't in LM Studio")  # fmt: skip
    monkeypatch.setattr(page_reader, "reader_status", lambda settings, use_cache=True: missing)
    monkeypatch.setattr(page_reader, "test_reader", lambda *a, **k: pytest.fail("nothing to test"))
    monkeypatch.setattr(page_reader, "read_document", lambda *a, **k: pytest.fail("nothing to read with"))
    monkeypatch.setattr(page_worker, "IDLE_SECONDS", 0.01)
    worker = page_worker.BackgroundReader(lambda: rounds.append(1) or _settings(), lambda: store)
    worker.start()
    try:
        threading.Event().wait(0.2)
    finally:
        worker.stop()
        worker.join(5)
    assert len(rounds) > 1 and not worker.is_alive() and worker.last_test is None


def _passes(calls, ok=True):
    def tested(settings, model=None, on_page=None):
        calls.append(model)
        if on_page:
            on_page(1, 2)
        right = 9 if ok else 3
        problem = "" if ok else "Only 3 of 9 fields right."
        return page_reader.ReaderTest(model=model, ok=ok, seconds=600.0, rows=[], fields_right=right, fields_total=9,
                                      when=page_reader._now(), problem=problem)  # fmt: skip

    return tested


def test_the_self_test_runs_on_its_own_before_the_first_invoice_is_read(store, fake_reader, monkeypatch):
    """Nobody has to press "Test the page reader": a model with no test that passed is tested before the queue is
    read, and the queue is read once it passed."""
    settings = _settings()
    store.set_setting(page_worker.TEST_KEY, "")
    first = _process(store, settings)
    calls = []
    monkeypatch.setattr(page_reader, "test_reader", _passes(calls))
    worker = page_worker.BackgroundReader(lambda: settings, lambda: store)
    worker.start()
    try:
        deadline = time.monotonic() + 60
        while store.page_read(first.invoice_id)["status"] != "done" and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        worker.stop()
        worker.join(10)
    assert calls == [MODEL] and worker.last_test["ok"]
    assert page_worker.confirmed(store, MODEL) and store.page_read(first.invoice_id)["status"] == "done"


def test_a_failed_self_test_is_not_run_again_in_a_loop(store, fake_reader, monkeypatch):
    settings = _settings()
    store.set_setting(page_worker.TEST_KEY, "")
    first = _process(store, settings)
    calls = []
    monkeypatch.setattr(page_reader, "test_reader", _passes(calls, ok=False))

    tested = page_worker.self_test_if_due(settings, store)
    assert tested is not None and not tested["ok"] and calls == [MODEL]
    assert page_worker.self_test_if_due(settings, store) is None and calls == [MODEL]  # not again at once
    assert page_worker.read_one(settings, store) is None  # nothing read until it passes
    assert store.page_read(first.invoice_id)["status"] == "waiting" and fake_reader["read"] == 0
    state, detail = page_worker.self_test(store, MODEL)
    assert state == "failed" and "Only 3 of 9 fields right" in detail and "in about 24 hour(s)" in detail

    later = dt.datetime.now() + dt.timedelta(hours=25)
    assert page_worker.self_test_due(store, MODEL, now=later)  # a day later it is tried again
    assert page_worker.self_test_due(store, "another-ovisocr2-build")  # another model: its own test
    # A test that read nothing (LM Studio stopped) is tried again an hour later.
    two_hours_ago = (dt.datetime.now() - dt.timedelta(hours=2)).isoformat(timespec="seconds")
    page_worker.save_test(store, {"model": MODEL, "ok": False, "fields_right": 0, "fields_total": 0,
                                  "when": two_hours_ago, "problem": "Page 1: the server isn't answering."})  # fmt: skip
    assert page_worker.self_test_due(store, MODEL)
    # Run again by a person: start_test runs it whatever the last result.
    assert page_worker.run_test(settings, store, MODEL)["ok"] is False and calls == [MODEL, MODEL]


def test_self_test_states(store):
    assert page_worker.self_test(store, "")[0] == "no model"
    assert page_worker.self_test(store, MODEL)[0] == "passed"
    assert page_worker.self_test(store, "other-ovisocr")[0] == "pending"
    store.set_setting(page_worker.TESTING_KEY, json.dumps({"model": MODEL, "reader": page_worker.READER_ID,
                                                           "done": 1, "pages": 2}))  # fmt: skip
    page_worker._testing_here.set()
    try:
        state, detail = page_worker.self_test(store, MODEL)
        assert state == "running" and "page 2 of 2" in detail
    finally:
        page_worker._testing_here.clear()


def test_the_wait_is_estimated_from_measured_page_times(store, monkeypatch):
    settings = _settings()
    monkeypatch.setattr(page_reader, "page_seconds_estimate", lambda settings, model=None: None)
    assert page_worker.wait_seconds(settings, store, MODEL) is None  # nothing measured yet
    monkeypatch.setattr(page_reader, "page_seconds_estimate", lambda settings, model=None: 120.0)
    ids = [_process(store, settings).invoice_id for _ in range(3)]
    assert page_worker.wait_seconds(settings, store, MODEL) == 3 * 120.0  # 3 invoices of (so far) 1 page
    store.next_page_read(reader="1:x")  # the first is being read
    store.finish_page_read(ids[0], "done", MODEL, pages=2, seconds=240.0)
    assert store.page_read_pages_average() == 2.0
    assert page_worker.wait_seconds(settings, store, MODEL, invoice_id=ids[2]) == 2 * 2 * 120.0
    assert page_worker.wait_seconds(settings, store, MODEL, invoice_id=ids[0]) is None  # read already
    store.set_setting(page_worker.TEST_KEY, "")  # not tested yet: its two test pages come first
    assert page_worker.wait_seconds(settings, store, MODEL) == (2 * 2 + 2) * 120.0


# --- The second review's findings (the page reader run for real on a laptop) ---------------------------------------


def test_each_model_keeps_its_own_test(store):
    """Testing a second model used to replace the one test kept: the model that passed was unlinked and the queue
    stopped."""
    assert page_worker.confirmed(store, MODEL)  # an older AP Coder's single test is read as that model's
    page_worker.save_test(store, {"model": "qwen3.5-9b", "ok": False, "fields_right": 3, "fields_total": 9})

    assert page_worker.confirmed(store, MODEL) and not page_worker.confirmed(store, "qwen3.5-9b")
    assert set(page_worker.saved_tests(store)) == {MODEL, "qwen3.5-9b"}
    assert page_worker.saved_test(store, "qwen3.5-9b")["fields_right"] == 3
    assert page_worker.saved_test(store, "gemma-3-4b") is None and not page_worker.confirmed(store, "gemma-3-4b")
    page_worker.save_test(store, {"model": MODEL, "ok": False, "fields_right": 1, "fields_total": 9})
    assert not page_worker.confirmed(store, MODEL)  # its own new test decides for it


def _down(settings, path, model=None, on_page=None, should_stop=None, **kwargs):
    error = "page 1: the model server isn't answering at http://127.0.0.1:1234/v1"
    return page_reader.PageReading(model=MODEL, pages=[], seconds=[], page_count=1, temporary=True, error=error)


def test_a_read_cut_off_by_the_server_waits_and_gives_up_after_a_few_tries(store, fake_reader, monkeypatch):
    """LM Studio stopped part way: the invoice used to be marked failed and left the queue for good."""
    settings = _settings()
    first = _process(store, settings)
    before = store.get_invoice(first.invoice_id)
    reads = page_reader.read_document
    monkeypatch.setattr(page_reader, "read_document", _down)
    forgot = []
    monkeypatch.setattr(page_reader, "forget_status", lambda: forgot.append(1))

    for attempt in range(1, page_worker.MAX_TRIES):
        outcome = page_worker.read_one(settings, store)
        assert outcome.status == "postponed" and f"try {attempt} of {page_worker.MAX_TRIES}" in outcome.message
        row = store.page_read(first.invoice_id)
        assert (row["status"], row["tries"]) == ("waiting", attempt) and "isn't answering" in row["error"]
    assert forgot  # the server is asked again before the next round: a stopped LM Studio pauses the queue
    outcome = page_worker.read_one(settings, store)
    assert outcome.status == "failed" and f"{page_worker.MAX_TRIES} times" in outcome.message
    assert "once LM Studio is running" in outcome.message
    assert store.page_read(first.invoice_id)["status"] == "failed"
    assert store.get_invoice(first.invoice_id)["ai_output"] == before["ai_output"]

    store.queue_page_read(first.invoice_id, "asked", requested_by="Pat")  # asked again: counted afresh
    assert store.page_read(first.invoice_id)["tries"] == 0
    monkeypatch.setattr(page_reader, "read_document", reads)
    assert page_worker.read_one(settings, store).status == "done"


def test_a_page_problem_still_fails_at_once(store, fake_reader, monkeypatch):
    settings = _settings()
    first = _process(store, settings)

    def cut(settings, path, model=None, on_page=None, should_stop=None, **kwargs):
        return page_reader.PageReading(model=MODEL, pages=[""], seconds=[60.0], page_count=1,
                                       error="page 1: the model got stuck repeating itself")  # fmt: skip

    monkeypatch.setattr(page_reader, "read_document", cut)
    assert page_worker.read_one(settings, store).status == "failed"
    assert store.page_read(first.invoice_id)["status"] == "failed"


def test_an_interrupted_read_goes_back_in_line(store, fake_reader, monkeypatch):
    """Ctrl+C on read-pages (or the dashboard closing) used to leave the invoice "reading" for two hours."""
    settings = _settings()
    first = _process(store, settings)

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(page_reader, "read_document", interrupted)
    with pytest.raises(KeyboardInterrupt):
        page_worker.read_one(settings, store)
    row = store.page_read(first.invoice_id)
    assert row["status"] == "waiting" and row["reader"] == "" and store.page_reads_waiting() == 1


def test_reads_of_a_process_that_is_gone_are_put_back_in_line(store):
    """The dashboard killed mid-read: on its next start (and before each round) its reads wait in line again; a read
    by another process still running (read-pages beside the dashboard) is left alone."""
    import os

    for invoice_id in (21, 22, 23, 24):
        store.queue_page_read(invoice_id, "new invoice")
    gone = 2**22 + 12345  # no such process
    assert not page_worker._pid_alive(gone) and page_worker._pid_alive(os.getppid())
    readers = {21: f"{gone}:dead", 22: f"{os.getppid()}:other", 23: page_worker.READER_ID, 24: ""}
    for invoice_id, reader in readers.items():
        assert store.next_page_read(invoice_id, reader=reader)["reader"] == reader

    assert page_worker.release_interrupted(store) == 3
    states = {i: store.page_read(i)["status"] for i in readers}
    assert states == {21: "waiting", 22: "reading", 23: "waiting", 24: "waiting"}
    assert "interrupted" in store.page_read(21)["error"]
    assert store.next_page_read()["invoice_id"] in (21, 23, 24)  # read again at once, not after two hours


def test_the_background_thread_puts_back_what_a_closed_dashboard_was_reading(store, fake_reader):
    settings = _settings()
    first = _process(store, settings)
    store.next_page_read(first.invoice_id, reader=f"{2**22 + 12345}:dead")  # being read when the app was killed
    assert store.page_reads_waiting() == 0

    worker = page_worker.BackgroundReader(lambda: settings, lambda: store)
    worker.start()
    try:
        deadline = time.monotonic() + 60
        while store.page_read(first.invoice_id)["status"] != "done" and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        worker.stop()
        worker.join(10)
    assert store.page_read(first.invoice_id)["status"] == "done"


def test_read_pages_stops_cleanly_on_ctrl_c(store, fake_reader, tmp_path, monkeypatch, capsys):
    from ap_coder import cli

    env = tmp_path / "pr.env"
    env.write_text("", encoding="utf-8")
    first = _process(store, _settings())
    monkeypatch.setattr(page_reader, "read_document", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))

    code = cli.main(["--env-file", str(env), "--db", str(store.path), "read-pages", "--cache-dir", ""])

    assert code == 130
    err = capsys.readouterr().err
    assert "Traceback" not in err and err.strip().splitlines()[-1].startswith("Stopped: the invoice being read is back")
    assert store.page_read(first.invoice_id)["status"] == "waiting"


def test_read_pages_reads_what_process_coded_without_a_dashboard(tmp_path, fake_reader, monkeypatch):
    """An invoice processed from the command line on a database never opened in the dashboard is coded with the
    sample GL accounts: read-pages used to refuse it for good ("no GL accounts imported yet")."""
    store = Store(tmp_path / "cli.db")
    assert not store.has_reference()
    settings = _settings()
    result = InvoicePipeline(settings, page_worker.reference_data(store), store=store).process(SAMPLES / f"{STEM}.pdf")
    assert result.ok and store.page_reads_waiting() == 1
    model, why = page_worker.ready(settings, store)
    assert model == "" and "the test starts on its own" in why

    page_worker.save_test(store, {"model": MODEL, "ok": True, "fields_right": 9, "fields_total": 9})
    assert page_worker.ready(settings, store) == (MODEL, "")
    outcome = page_worker.read_one(settings, store)
    assert outcome.status == "done" and outcome.updated, outcome


def test_a_model_that_cannot_see_is_not_ready(store, fake_reader, monkeypatch):
    blind = page_reader.ReaderStatus(reachable=True, lm_studio=True, model="qwen2.5-7b-instruct",
                                     document_reader=False, state="blind", candidates=[MODEL],
                                     note="qwen2.5-7b-instruct can't look at pictures: pick a model that can, e.g. "
                                     "OvisOCR2 (or Automatic).")  # fmt: skip
    monkeypatch.setattr(page_reader, "reader_status", lambda settings, use_cache=True: blind)
    page_worker.save_test(store, {"model": "qwen2.5-7b-instruct", "ok": True})
    model, why = page_worker.ready(_settings(), store)
    assert model == "" and "can't look at pictures" in why


def test_pages_ocr_found_blank_are_not_read(store, fake_reader, monkeypatch):
    """A page with no words isn't shown to the page reader, and what it wrote for one is never taken."""
    settings = _settings()
    first = _process(store, settings)
    seen = {}

    def read(settings, path, model=None, on_page=None, should_stop=None, blank_pages=()):
        seen["blank"] = set(blank_pages)
        text = (SAMPLES / f"{STEM}.md").read_text(encoding="utf-8")
        return page_reader.PageReading(model=MODEL, pages=["The quick brown fox jumps over the lazy dog.", text],
                                       seconds=[1.0, 95.0], page_count=2)  # fmt: skip

    monkeypatch.setattr(page_reader, "read_document", read)
    monkeypatch.setattr(page_worker, "blank_pages", lambda settings, path: ({1}, 2))
    outcome = page_worker.read_one(settings, store)
    assert outcome.status == "done" and seen["blank"] == {1}
    capture = store.get_capture(first.invoice_id)
    assert capture["fields"]["vendor_name"]["value"] != "The quick brown fox jumps over the lazy dog."

    second = _process(store, settings)
    monkeypatch.setattr(page_worker, "blank_pages", lambda settings, path: ({1, 2}, 2))
    outcome = page_worker.read_one(settings, store)
    assert outcome.invoice_id == second.invoice_id and outcome.status == "skipped" and "no words" in outcome.message


def test_blank_pages_come_from_ocr(tmp_path, monkeypatch):
    import pymupdf

    from ap_coder.capture import layout

    doc = pymupdf.open()
    doc.new_page()  # blank
    doc.new_page().insert_text((72, 72), "Invoice 1042 from Harbourview Plumbing, total 198.00")
    path = tmp_path / "blank_first.pdf"
    doc.save(path)
    monkeypatch.setattr(layout, "ocr_available", lambda: True)
    monkeypatch.setattr(layout, "ocr_image", lambda png, number, engine=None: layout.PageLayout(number, 1, 1, [], []))
    assert page_worker.blank_pages(_settings(), path) == ({1}, 2)
    monkeypatch.setattr(layout, "ocr_available", lambda: False)  # a scan can't be told from a blank page then
    assert page_worker.blank_pages(_settings(), path) == (set(), 0)


def test_the_page_reader_is_tested_in_the_background_one_test_at_a_time(store, monkeypatch):
    """The test (20 minutes on a laptop) used to run inside the page: leaving it lost the result. It runs in a thread
    of its own now, its progress noted, and its result kept as its model's whatever the browser does."""
    release = threading.Event()
    pages_started = threading.Event()

    def tested(settings, model=None, on_page=None):
        on_page(1, 2)
        on_page(2, 2)
        pages_started.set()
        assert release.wait(30)
        return page_reader.ReaderTest(model=model, ok=True, seconds=1150.0, rows=[], fields_right=9, fields_total=9,
                                      when="2026-10-10T22:27")  # fmt: skip

    monkeypatch.setattr(page_reader, "test_reader", tested)
    settings = _settings()
    assert page_worker.start_test(settings, store, "qwen3.5-9b")
    assert pages_started.wait(30)
    under_way = page_worker.test_under_way(store)
    assert under_way["model"] == "qwen3.5-9b" and (under_way["done"], under_way["pages"]) == (1, 2)
    assert under_way["started"][:10] == time.strftime("%Y-%m-%d")
    assert not page_worker.start_test(settings, store, MODEL)  # never two at once
    assert page_worker.run_test(settings, store, MODEL) is None

    release.set()
    deadline = time.monotonic() + 30
    while page_worker.test_under_way(store) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert page_worker.test_under_way(store) is None
    assert page_worker.saved_test(store, "qwen3.5-9b")["fields_right"] == 9  # kept with nobody looking
    assert page_worker.confirmed(store, MODEL)  # the other model's test is kept
    assert "page_reader_tested" in [e["action"] for e in store.events()]


def test_a_test_left_by_a_closed_dashboard_is_not_under_way(store):
    left = {"model": MODEL, "started": "2026-10-10T22:00:00", "reader": f"{2**22 + 12345}:dead", "done": 1, "pages": 2}
    store.set_setting(page_worker.TESTING_KEY, json.dumps(left))
    assert page_worker.test_under_way(store) is None
    store.set_setting(page_worker.TESTING_KEY, json.dumps({"model": MODEL, "reader": page_worker.READER_ID}))
    assert page_worker.test_under_way(store) is None  # this process isn't testing


def test_the_background_reader_waits_while_the_page_reader_is_tested(store, fake_reader, monkeypatch):
    settings = _settings()
    _process(store, settings)
    monkeypatch.setattr(page_worker, "test_under_way", lambda store: {"model": MODEL})
    monkeypatch.setattr(page_worker, "IDLE_SECONDS", 0.01)
    worker = page_worker.BackgroundReader(lambda: settings, lambda: store)
    worker.start()
    try:
        time.sleep(0.3)
    finally:
        worker.stop()
        worker.join(10)
    assert fake_reader["read"] == 0 and store.page_reads_waiting() == 1


def test_read_pages_test_option(store, tmp_path, monkeypatch, capsys):
    from ap_coder import cli

    env = tmp_path / "pr.env"
    env.write_text("", encoding="utf-8")
    store.set_setting(page_worker.TEST_KEY, "")
    status = page_reader.ReaderStatus(reachable=True, lm_studio=True, model=MODEL, document_reader=True,
                                      state="downloaded", candidates=[MODEL])  # fmt: skip
    passed = page_reader.ReaderTest(model=MODEL, ok=True, seconds=1157.0, rows=[], fields_right=9, fields_total=9,
                                    when="2026-10-10T22:27")  # fmt: skip
    monkeypatch.setattr(page_reader, "reader_status", lambda settings, use_cache=True: status)
    monkeypatch.setattr(page_reader, "page_seconds_estimate", lambda settings, model=None: None)
    monkeypatch.setattr(page_reader, "test_reader", lambda settings, model=None, on_page=None: passed)

    assert cli.main(["--env-file", str(env), "--db", str(store.path), "read-pages", "--test"]) == 0
    err = capsys.readouterr().err
    assert "about 10–20 minutes on a laptop without a graphics card" in err and "passed" in err
    assert page_worker.confirmed(store, MODEL)
