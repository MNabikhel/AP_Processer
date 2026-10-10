"""The page reader's queue: which invoices are read, and how a reading is folded into an invoice (no real model:
the page reader's answers are faked)."""

import json
import threading
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


def _settings(mode="auto", scope="scans", **kw):
    s = Settings()
    return replace(s, llm=replace(s.llm, provider="off"), page_reader=PageReaderSettings(mode=mode, scope=scope, **kw))


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
        return page_reader.PageReading(model=model or MODEL, pages=[calls.get("text", transcript)], seconds=[95.0])

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
    ("mode", "scope", "name", "source", "wanted"),
    [
        ("auto", "scans", "scan.png", "", True),  # a photo or image: always
        ("auto", "scans", "scan.pdf", "ocr", True),  # a PDF that had to be OCR'd
        ("auto", "scans", "scan.pdf", "mixed", True),
        ("auto", "scans", "digital.pdf", "text", False),  # a digital PDF: its text layer is exact
        ("auto", "all", "digital.pdf", "text", True),  # unless every invoice is read
        ("auto", "all", "invoice.md", "", False),  # text files never
        ("ask", "scans", "scan.png", "", False),  # only when AP asks
        ("off", "all", "scan.png", "", False),
    ],
)
def test_which_invoices_are_read_in_the_background(mode, scope, name, source, wanted):
    assert page_worker.wants_reading(_settings(mode, scope), Path(name), source) is wanted


def test_queueing_never_stops_processing():
    class Broken:
        def queue_page_read(self, invoice_id, reason, requested_by=""):
            raise RuntimeError("database is locked")

    settings = _settings("auto", "all")
    assert page_worker.queue_new_invoice(Broken(), settings, 7, Path("a.pdf"), "text") is False
    assert page_worker.queue_new_invoice(None, settings, 7, Path("a.pdf"), "text") is False
    assert page_worker.queue_new_invoice(Broken(), settings, None, Path("a.pdf"), "text") is False


def test_processing_queues_what_the_settings_say(store):
    digital = _process(store, _settings("auto", "scans"))
    assert store.page_read(digital.invoice_id) is None  # a digital PDF, scans only: not queued
    every = _process(store, _settings("auto", "all"))
    assert store.page_read(every.invoice_id)["status"] == "waiting"
    assert store.page_read(every.invoice_id)["reason"] == "new invoice"
    asked = _process(store, _settings("ask", "all"))
    assert store.page_read(asked.invoice_id) is None
    assert store.page_reads_waiting() == 1


def test_agreement_compares_the_page_reader_with_the_value_shown():
    def field(value, **sources):
        return SimpleNamespace(value=value, sources=sources)

    capture = SimpleNamespace(
        fields={
            "invoice_number": field("HPS-2026-0347", rules="HPS-2026-0347", vlm="HPS-2026-0347"),
            "grand_total": field(1150.0, rules="1,150.00", vlm="1,105.00"),
            "po_number": field("PO-1", vlm="PO-1"),
            "due_date": field("2026-03-01", rules="2026-03-01"),  # not read by the page reader: left out
            "currency": field("", vlm="CAD"),  # nothing shown: nothing to compare
        }
    )
    assert page_worker.agreement(capture) == {
        "agree": ["invoice_number"],
        "differ": ["grand_total"],
        "only": ["po_number"],
    }
    assert page_worker.agreement(None) == {"agree": [], "differ": [], "only": []}


# --- Reading one invoice ---------------------------------------------------------------------------------------


def test_a_reading_is_folded_into_an_untouched_invoice(store, fake_reader):
    settings = _settings("auto", "all")
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
    settings = _settings("auto", "all")
    first = _process(store, settings)
    store.approve_invoice(first.invoice_id, dict(first.output, invoice_number="TYPED-BY-AP"), "Pat")
    store.reopen(first.invoice_id, "Pat", "wrong cost center")
    assert store.get_invoice(first.invoice_id)["status"] == REVIEW

    outcome = page_worker.read_one(settings, store)

    assert outcome.status == "done" and not outcome.updated
    assert "already edited or decided" in outcome.message
    inv = store.get_invoice(first.invoice_id)
    assert inv["final_output"]["invoice_number"] == "TYPED-BY-AP" and "page_reader" not in (inv["meta"] or {})
    assert store.page_read(first.invoice_id)["status"] == "done"


def test_a_decided_invoice_is_not_changed(store, fake_reader):
    settings = _settings("auto", "all")
    first = _process(store, settings)
    store.approve_invoice(first.invoice_id, first.output, "Pat")

    outcome = page_worker.read_one(settings, store)

    assert outcome.status == "done" and not outcome.updated
    assert store.get_invoice(first.invoice_id)["status"] == APPROVED


def test_not_enough_memory_leaves_it_waiting(store, fake_reader, monkeypatch):
    settings = _settings("auto", "all")
    first = _process(store, settings)
    monkeypatch.setattr(page_reader, "load_reader", lambda settings, model=None: "not enough memory to load it")

    outcome = page_worker.read_one(settings, store)

    assert outcome.status == "postponed" and "memory" in outcome.message
    assert store.page_read(first.invoice_id)["status"] == "waiting"
    assert fake_reader["read"] == 0


def test_a_failed_read_is_recorded_and_the_invoice_is_unchanged(store, fake_reader, monkeypatch):
    settings = _settings("auto", "all")
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
    settings = _settings("auto", "all")
    first = _process(store, settings)
    fake_reader["text"] = "   \n"

    outcome = page_worker.read_one(settings, store)

    assert outcome.status == "failed" and "nothing" in outcome.message
    assert store.get_invoice(first.invoice_id)["status"] == REVIEW


def test_a_missing_file_is_skipped(store, fake_reader, tmp_path):
    settings = _settings("auto", "all")
    copy = tmp_path / f"{STEM}.pdf"
    copy.write_bytes((SAMPLES / f"{STEM}.pdf").read_bytes())
    first = _process(store, settings, copy)
    copy.unlink()

    outcome = page_worker.read_one(settings, store)

    assert outcome.status == "skipped" and store.page_read(first.invoice_id)["status"] == "skipped"
    assert fake_reader["read"] == 0


def test_nothing_is_read_while_the_reader_is_off_or_missing(store, fake_reader, monkeypatch):
    first = _process(store, _settings("auto", "all"))
    assert page_worker.read_one(_settings("off", "all"), store) is None
    monkeypatch.setattr(page_reader, "reader_status", lambda settings, use_cache=True: page_reader.ReaderStatus(
        reachable=True, lm_studio=True, model="", document_reader=False, state="missing", candidates=[],
        note="OvisOCR2 isn't downloaded"))  # fmt: skip
    assert page_worker.read_one(_settings("auto", "all"), store) is None
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
def test_only_a_linked_model_reads(store, fake_reader, test):
    """The page reader is linked by passing its test in Settings: a model that hasn't (or another one than the one
    that passed) reads nothing, and the invoices wait for it."""
    settings = _settings("auto", "all")
    first = _process(store, settings)
    store.set_setting(page_worker.TEST_KEY, json.dumps(test) if test else "")

    assert page_worker.read_one(settings, store) is None
    model, why = page_worker.ready(settings, store)
    assert model == "" and "Test the page reader" in why
    assert store.page_read(first.invoice_id)["status"] == "waiting" and fake_reader["read"] == 0


def test_an_invoice_asked_for_is_read_first(store, fake_reader):
    settings = _settings("ask", "all")
    a = _process(store, settings)
    b = _process(store, settings)
    store.queue_page_read(a.invoice_id, "asked", requested_by="Pat")
    store.queue_page_read(b.invoice_id, "asked", requested_by="Pat")

    outcome = page_worker.read_one(settings, store, invoice_id=b.invoice_id)

    assert outcome.invoice_id == b.invoice_id
    assert store.page_read(a.invoice_id)["status"] == "waiting"


def test_run_queue_reads_until_the_queue_is_empty(store, fake_reader):
    settings = _settings("auto", "all")
    ids = [_process(store, settings).invoice_id for _ in range(2)]
    seen = []

    done = page_worker.run_queue(settings, store, minutes=5, on_result=seen.append)

    assert sorted(o.invoice_id for o in done) == sorted(ids) and seen == done
    assert all(o.status == "done" for o in done)
    assert store.page_reads_waiting() == 0


def test_the_background_thread_reads_and_stops(store, fake_reader, monkeypatch):
    settings = _settings("auto", "all")
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
    env.write_text("AP_PAGE_READER=auto\nAP_PAGE_READER_SCOPE=all\nAP_LLM_PROVIDER=off\n", encoding="utf-8")
    for key in ("AP_PAGE_READER", "AP_PAGE_READER_SCOPE", "AP_LLM_PROVIDER"):
        monkeypatch.delenv(key, raising=False)
    first = _process(store, _settings("auto", "all"))
    db = str(store.path)

    assert cli.main(["--env-file", str(env), "--db", db, "read-pages", "--minutes", "5", "--cache-dir", ""]) == 0
    err = capsys.readouterr().err
    assert "1 invoice(s) waiting" in err and f"invoice {first.invoice_id}: done" in err and "Read 1;" in err
    assert store.page_read(first.invoice_id)["status"] == "done"

    # One invoice by hand: queued again and read at once (from the page reader's cache in real use).
    assert cli.main(["--env-file", str(env), "--db", db, "read-pages", "--invoice", str(first.invoice_id),
                     "--cache-dir", ""]) == 0  # fmt: skip
    assert fake_reader["read"] == 2

    monkeypatch.setenv("AP_PAGE_READER", "off")
    assert cli.main(["--env-file", str(env), "--db", db, "read-pages", "--cache-dir", ""]) == 1
    assert "off" in capsys.readouterr().err


def test_doctor_reports_the_page_reader_and_never_fails_on_it(store, fake_reader, monkeypatch):
    from ap_coder import doctor
    from ap_coder.doctor import PASS, SKIP, WARN

    assert doctor._page_reader_check(_settings("off"))[1] == SKIP
    monkeypatch.setenv("AP_DB_PATH", str(store.path))
    area, level, detail = doctor._page_reader_check(_settings("auto", "scans"))
    assert (area, level) == ("page reader", PASS)
    assert MODEL in detail and "a document reader" in detail and "linked" in detail and "in the background" in detail
    store.set_setting(page_worker.TEST_KEY, "")
    level, detail = doctor._page_reader_check(_settings("auto", "scans"))[1:]
    assert level == WARN and "hasn't passed its test" in detail
    monkeypatch.setattr(page_reader, "reader_status", lambda settings, use_cache=True: page_reader.ReaderStatus(
        reachable=False, lm_studio=False, model="", document_reader=False, state="down", candidates=[],
        note="LM Studio isn't answering"))  # fmt: skip
    assert doctor._page_reader_check(_settings("auto"))[1:] == (WARN, "LM Studio isn't answering")


def test_launcher_line(store, fake_reader, monkeypatch):
    import importlib.util
    import sys

    from .conftest import ROOT

    spec = importlib.util.spec_from_file_location("launch_for_page_reader", ROOT / "scripts" / "launch.py")
    launch = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, launch)
    spec.loader.exec_module(launch)
    monkeypatch.setenv("AP_PAGE_READER", "auto")
    monkeypatch.setenv("AP_DB_PATH", str(store.path))
    assert launch.page_reader_line() == ("ok", f"Page reader: {MODEL} linked (reads scans as a second reader, in "
                                         "the background)")  # fmt: skip
    store.set_setting(page_worker.TEST_KEY, "")
    assert launch.page_reader_line() == ("info", f"Page reader: {MODEL} in LM Studio, not tested yet (Settings > Page "
                                         "reader > Test)")  # fmt: skip
    monkeypatch.setenv("AP_PAGE_READER", "off")
    assert launch.page_reader_line() == ("info", "Page reader: off (optional)")


def test_the_background_thread_waits_while_off(monkeypatch):
    rounds = []
    worker = page_worker.BackgroundReader(lambda: rounds.append(1) or _settings("off"), lambda: pytest.fail("no store"))
    monkeypatch.setattr(page_worker, "OFF_SECONDS", 0.01)
    worker.start()
    try:
        deadline = threading.Event()
        deadline.wait(0.2)
    finally:
        worker.stop()
        worker.join(5)
    assert rounds and not worker.is_alive()
