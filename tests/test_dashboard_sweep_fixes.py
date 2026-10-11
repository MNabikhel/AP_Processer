"""Bugs found driving the dashboard in a browser as a clerk and a manager would (third sweep), each reproduced here
through the page or the helper it came from, so they are not made again."""

from types import SimpleNamespace

from streamlit.testing.v1 import AppTest

from ap_coder.store import Store, load_sample_setup

from .test_dashboard_pages import APP, TIMEOUT, _ok
from .test_dashboard_pages import db as db  # the fixture (a fresh database per test)


def _html(at):
    return " ".join(str(h.proto.body) for h in at.get("html"))


def test_the_queued_note_reads_as_a_sentence(db, monkeypatch):
    """The toast after processing said "2 invoices queued for OvisOCR2, which isn't running: them wait for it"."""
    from ap_coder.webapp import process

    monkeypatch.setattr(process, "reading_status_or_none", lambda: SimpleNamespace(page_reader_ready=False))
    assert process.queued_note(2) == (
        "2 invoices queued for OvisOCR2, which isn't running: they wait for it (Settings → Reading)."
    )
    assert process.queued_note(1) == (
        "1 invoice queued for OvisOCR2, which isn't running: it waits for it (Settings → Reading)."
    )
    assert process.queued_note(0) == ""


def test_getting_started_counts_only_the_steps_that_are_needed(db):
    """The welcome screen's ring counted the optional chat model, so it read 1/6 where the Process page said 1 of 4
    (plus processing the first invoices), and could never be complete without that optional model."""
    at = _ok(AppTest.from_file(APP, default_timeout=TIMEOUT).run())
    html = _html(at)
    assert "Chat model for GL suggestions" in html and "optional" in html
    assert "aria-label='1/5'" in html, html  # reading invoices done; GL, tax mapping, OvisOCR2, first invoices to do
    store = Store(db)
    load_sample_setup(store)
    html = _html(_ok(AppTest.from_file(APP, default_timeout=TIMEOUT).run()))
    assert "aria-label='3/5'" in html
