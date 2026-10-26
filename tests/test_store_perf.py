"""The database does no more work than a page needs: one-off clean-ups run once, indexes are used, the background
workers reuse one store (the SQL statements and connections counted)."""

import sqlite3
import threading

from ap_coder.core import store as store_mod
from ap_coder.core.store import Store


def _statements(monkeypatch) -> list[str]:
    """Every SQL statement run from now on (each connection opened traces into the list)."""
    seen: list[str] = []
    real = sqlite3.connect

    def tracing(*args, **kwargs):
        conn = real(*args, **kwargs)
        conn.set_trace_callback(seen.append)
        return conn

    monkeypatch.setattr(store_mod.sqlite3, "connect", tracing)
    return seen


def test_opening_the_database_again_does_not_scan_the_learning_memory(tmp_path, monkeypatch):
    """Every Store() (each round of the page reader, the AI reviewer and the Azure check) withdrew the AP Coder's own
    approvals from the lessons and recounted blanks as coding: two scans of the whole feedback table, each time."""
    path = tmp_path / "ap.db"
    Store(path)
    seen = _statements(monkeypatch)
    Store(path)
    assert not [s for s in seen if "FROM feedback" in s or "UPDATE feedback" in s]
    with sqlite3.connect(path) as conn:  # as an older version left it: the clean-ups run once more
        conn.execute("DELETE FROM settings WHERE key = 'feedback_cleanup'")
    seen.clear()
    Store(path)
    assert [s for s in seen if s.startswith("DELETE FROM feedback")]
    seen.clear()
    Store(path)
    assert not [s for s in seen if "FROM feedback" in s or "UPDATE feedback" in s]


def test_the_background_workers_build_their_store_once(tmp_path, monkeypatch):
    from ap_coder.ai import azure_check, page_worker

    store = Store(tmp_path / "ap.db")
    built: list[int] = []

    def factory():
        built.append(1)
        return store

    monkeypatch.setattr(page_worker, "IDLE_SECONDS", 0.005)
    monkeypatch.setattr(page_worker, "OFF_SECONDS", 0.005)
    monkeypatch.setattr(page_worker, "read_one", lambda *a, **k: None)
    monkeypatch.setattr(page_worker, "self_test_if_due", lambda *a, **k: None)
    monkeypatch.setattr(page_worker.BackgroundReader, "_review_one", lambda self, s, st: 0.005)
    monkeypatch.setattr(azure_check, "OFF_SECONDS", 0.005)
    rounds: list[int] = []
    for worker in (
        page_worker.BackgroundReader(lambda: rounds.append(1) or object(), factory),
        azure_check.AzureChecker(lambda: rounds.append(1) or None, factory),
    ):
        built.clear()
        rounds.clear()
        worker.start()
        try:
            threading.Event().wait(0.2)
        finally:
            worker.stop()
            worker.join(5)
        assert len(rounds) > 3 and built == [1]


def test_the_dashboard_hands_its_own_store_to_the_background_workers(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from ap_coder.ai import azure_check, page_worker
    from ap_coder.web.app import create_app

    from .webclient import web_settings

    factories = []
    monkeypatch.setattr(page_worker, "start_background", lambda s, store_factory, **k: factories.append(store_factory))
    monkeypatch.setattr(azure_check, "start_background", lambda s, store_factory: factories.append(store_factory))
    store = Store(tmp_path / "private" / "ap_coder.db")
    with TestClient(create_app(web_settings(), store, start_reader=True), base_url="http://localhost"):
        pass
    assert len(factories) == 2 and all(f() is store for f in factories)


def _plan(store: Store, monkeypatch, call, table: str) -> str:
    """The query plan of the SELECT on ``table`` that ``call`` runs (the statement traced, then explained)."""
    seen = _statements(monkeypatch)
    call()
    monkeypatch.undo()
    asked = [s for s in seen if s.lstrip().upper().startswith("SELECT") and f"FROM {table}" in s]
    assert asked, seen
    with sqlite3.connect(store.path) as conn:
        return " | ".join(str(r[-1]) for r in conn.execute("EXPLAIN QUERY PLAN " + asked[-1]))


def test_the_lookups_by_file_total_and_event_use_an_index(tmp_path, monkeypatch):
    """find_by_hash, duplicates_elsewhere and latest_events read the whole invoices or events table."""
    store = Store(tmp_path / "ap.db")
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4 a")
    ids = [store.add_invoice(pdf, {"vendor_name": "Acme", "invoice_number": f"A-{i}", "grand_total": 10.0 + i}, {})
           for i in range(3)]  # fmt: skip
    assert "invoices_sha" in _plan(store, monkeypatch, lambda: store.find_by_hash(pdf), "invoices")
    found = _plan(store, monkeypatch, lambda: store.duplicates_elsewhere("Other", "A-1", 11.0), "invoices")
    assert "invoices_total" in found
    assert store.duplicates_elsewhere("Other", "A-1", 11.005) and not store.duplicates_elsewhere("Other", "A-1", 11.02)
    assert "events_action" in _plan(store, monkeypatch, lambda: store.latest_events("processed", ids), "events")


def test_one_vendors_history_is_read_once_per_page(tmp_path, monkeypatch):
    """vendor_findings and expected_tax_numbers each read every invoice of the vendor, with its whole AI output and
    final coding parsed for five fields: now read once while nothing changes (inside a session), by json_extract."""
    from ap_coder.coding.vendors import expected_tax_numbers, vendor_findings
    from ap_coder.core.schema import InvoiceCoding

    from .webclient import add_clean, gt

    store = Store(tmp_path / "ap.db")
    ids = [add_clean(store, invoice_number=f"N-{i}") for i in range(3)]
    store.approve_invoice(ids[0], {**gt(), "invoice_number": "N-0", "gst_hst_registration_number": "1"}, "Ann")
    coding = InvoiceCoding.model_validate({**gt(), "invoice_number": "N-9"})
    key = store.get_invoice(ids[0])["vendor_key"]
    expected = store.vendor_invoices(key)
    assert (
        expected[-1]["gst_hst_number"] == "1" and expected[0]["gst_hst_number"] == gt()["gst_hst_registration_number"]
    )
    seen = _statements(monkeypatch)

    def reads() -> int:
        return len([s for s in seen if "FROM invoices WHERE vendor_key" in s and "ORDER BY id DESC" in s])

    with store.session():
        vendor_findings(coding, store)
        expected_tax_numbers(coding, store)
        assert reads() == 1
        assert not [s for s in seen if "ORDER BY id DESC" in s and "ai_output, final_output" in s]
        store.reject_invoice(ids[1], "Ann", "duplicate")  # a change: read again
        history = store.vendor_invoices(key)
        assert reads() == 2 and [h["status"] for h in history] == ["review", "rejected", "approved"]
        with sqlite3.connect(store.path) as other:  # another connection's change: read again too
            other.execute("UPDATE invoices SET status = 'parked' WHERE id = ?", (ids[2],))
        assert store.vendor_invoices(key)[0]["status"] == "parked"
        assert reads() == 3


def test_the_reference_data_is_built_again_only_when_it_changes(tmp_path, monkeypatch):
    """Every page and every invoice read parsed the tax-rate file and built the accounts again."""
    import os
    import shutil

    from ap_coder.coding import tax
    from ap_coder.core.store import load_sample_setup

    rates = tmp_path / "rates.csv"
    shutil.copy(tax.DEFAULT_RATES_PATH, rates)
    monkeypatch.setattr(tax, "rates_path", lambda: rates)
    loads: list[object] = []
    real = tax.TaxRateTable.load.__func__
    monkeypatch.setattr(tax.TaxRateTable, "load", classmethod(lambda cls, path=None: loads.append(path) or real(
        cls, path)))  # fmt: skip
    store = Store(tmp_path / "ap.db")
    load_sample_setup(store)
    first = store.reference_data()
    assert store.reference_data() is first and Store(store.path).reference_data() is first and len(loads) == 1
    gl = store.list_accounts("gl_accounts")
    store.save_accounts("gl_accounts", [{**gl[0], "description": "Renamed"}, *gl[1:]])
    renamed = store.reference_data()
    assert renamed is not first and renamed.chart_of_accounts.rows[0]["description"] == "Renamed"
    store.set_setting("our_province", "QC")
    assert store.reference_data().tax.our_province == "QC"
    store.set_tax_treatment("GST", tax.EXPENSE_TO_LINE, "")
    assert store.reference_data().tax.treatments["GST"].treatment == tax.EXPENSE_TO_LINE
    before = len(loads)
    assert store.reference_data() is store.reference_data() and len(loads) == before
    stamp = rates.stat().st_mtime_ns + 5_000_000_000
    os.utime(rates, ns=(stamp, stamp))  # the rate table edited: read again
    store.reference_data()
    assert len(loads) == before + 1
    given = tax.TaxRateTable.load()
    assert store.reference_data(given).tax.rates is given  # rates given: always those


def test_the_metrics_read_the_learning_memory_again_only_when_it_changes(tmp_path, monkeypatch):
    from .webclient import add_clean, gt

    store = Store(tmp_path / "ap.db")
    ids = [add_clean(store, invoice_number=f"N-{i}") for i in range(3)]
    store.approve_invoice(ids[0], {**gt(), "invoice_number": "N-0"}, "Ann")
    seen = _statements(monkeypatch)

    def scans() -> int:
        return len([s for s in seen if "FROM feedback WHERE outcome" in s])

    first = store.metrics()  # three scans: the lines by outcome are counted as lessons come and go (D-110)
    assert scans() == 3 and first["lines_reviewed"] > 0
    assert store.metrics() == first and scans() == 3
    store.reject_invoice(ids[1], "Ann", "duplicate")  # no lesson: the memory is kept, the invoice counts are not
    assert store.metrics()["invoices_by_status"] == {"approved": 1, "rejected": 1, "review": 1} and scans() == 3
    store.approve_invoice(ids[2], {**gt(), "invoice_number": "N-2"}, "Ann")
    assert store.metrics()["lines_reviewed"] == 2 * first["lines_reviewed"] and scans() == 6


def test_bulk_sorting_reuses_the_queue_the_page_read(tmp_path, monkeypatch):
    from ap_coder.coding.bulk import sort_for_bulk
    from ap_coder.core.store import REVIEW

    from .webclient import add_clean

    store = Store(tmp_path / "ap.db")
    for i in range(3):
        add_clean(store, invoice_number=f"N-{i}")
    queue = [i for i in store.list_invoices() if i["status"] == REVIEW]
    expected = sort_for_bulk(store)
    seen = _statements(monkeypatch)
    assert sort_for_bulk(store, queue) == expected
    assert not [s for s in seen if s.startswith("SELECT id, file_name, status")]


def test_month_end_asks_only_whether_there_is_an_invoice(tmp_path, monkeypatch):
    """Month-end read the id of every invoice to tell whether there was any, and every PO with its lines."""
    from ap_coder.core.store import load_sample_setup

    from .webclient import add_clean, client_for

    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    add_clean(store)
    client = client_for(store)
    seen = _statements(monkeypatch)
    page = client.get("/month-end")
    assert page.status_code == 200 and "Nothing to accrue yet" not in page.text
    assert not [s for s in seen if s.startswith("SELECT id FROM invoices ORDER BY id")]
    assert store.has_invoices() and not Store(tmp_path / "empty.db").has_invoices()


def test_the_settings_are_read_again_only_when_the_env_file_changes(tmp_path, monkeypatch):
    """Every request read and parsed the .env again."""
    import os

    from ap_coder.core import envfile
    from ap_coder.web.app import create_app

    env = tmp_path / ".env"
    env.write_text("AP_REVIEW_THRESHOLD=0.8\n", encoding="utf-8")
    monkeypatch.setenv("AP_ENV_FILE", str(env))
    reads: list[object] = []
    real = envfile.read_text
    monkeypatch.setattr(envfile, "read_text", lambda path, *a, **k: reads.append(path) or real(path, *a, **k))
    app = create_app(None, Store(tmp_path / "ap.db"), start_reader=False)
    first = app.state.settings_factory()
    assert app.state.settings_factory() is first and len(reads) == 1
    stamp = env.stat().st_mtime_ns + 5_000_000_000
    os.utime(env, ns=(stamp, stamp))  # saved again (the Settings page)
    app.state.settings_factory()
    assert len(reads) == 2
    monkeypatch.setenv("AP_REVIEWER", "Someone else")  # the environment changed: read again too
    app.state.settings_factory()
    assert len(reads) == 3


def test_a_list_of_invoices_does_not_look_for_each_file_every_time(tmp_path, monkeypatch):
    from pathlib import Path

    from .webclient import add_clean

    store = Store(tmp_path / "ap.db")
    ids = [add_clean(store, invoice_number=f"N-{i}") for i in range(4)]
    store.get_invoices(ids)
    files = {str(Path(i["source_path"])) for i in store.get_invoices(ids)}
    looked: list[str] = []
    real = Path.exists
    monkeypatch.setattr(Path, "exists", lambda self, *a, **k: looked.append(str(self)) or real(self, *a, **k))
    assert len(store.get_invoices(ids)) == 4
    assert not [p for p in looked if p in files]


def test_the_queue_checks_an_invoice_again_once_per_change_of_settings(tmp_path, monkeypatch):
    """B-815: the queue's re-check against today's settings is done once per change, and kept: loading the queue
    again checks nothing again."""
    from ap_coder.coding import bulk
    from ap_coder.core.store import REVIEW, load_sample_setup

    from .webclient import add_clean

    store = Store(tmp_path / "ap.db")
    load_sample_setup(store)
    ids = [add_clean(store, invoice_number=f"N-{i}") for i in range(3)]
    calls = []
    real = bulk.finalise_coding
    monkeypatch.setattr(bulk, "finalise_coding", lambda *a, **k: calls.append(1) or real(*a, **k))
    assert bulk.issues_today(store, ids) == {} and calls == []  # checked against the settings as they are
    store.set_setting("default_terms_days", "45")
    assert set(bulk.issues_today(store, ids)) == set(ids) and len(calls) == 3
    assert set(bulk.issues_today(store, ids)) == set(ids) and len(calls) == 3  # kept: not checked again
    queue = [i for i in store.list_invoices() if i["status"] == REVIEW]
    bulk.sort_for_bulk(store, queue)
    assert len(calls) == 3
