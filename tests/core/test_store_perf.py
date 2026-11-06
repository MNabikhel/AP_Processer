"""The database does no more work than a page needs: one-off clean-ups run once, indexes are used, the background
workers reuse one store (the SQL statements and connections counted)."""

import re
import sqlite3
import threading
import time

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
        try:  # until several rounds ran (B-1492: Windows' timer ticks of about 15 ms made 0.2 s only 2 rounds)
            deadline = time.monotonic() + 10
            while len(rounds) <= 3 and time.monotonic() < deadline:
                threading.Event().wait(0.01)
        finally:
            worker.stop()
            worker.join(5)
        assert len(rounds) > 3 and built == [1]


def test_the_dashboard_hands_its_own_store_to_the_background_workers(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from ap_coder.ai import azure_check, page_worker
    from ap_coder.web.app import create_app
    from tests.webclient import web_settings

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


def _plans(store: Store, monkeypatch, call, table: str, where: str) -> list[str]:
    """The query plans of the SELECTs, UPDATEs and DELETEs on ``table`` that ``call`` runs whose condition has
    ``where`` (each statement traced, then explained)."""
    seen = _statements(monkeypatch)
    call()
    monkeypatch.undo()
    asked = sorted({s for s in seen if s.split(" ", 1)[0] in ("SELECT", "UPDATE", "DELETE")
                    and re.search(rf"\b(FROM|UPDATE|JOIN) {table}\b", s) and where in s})  # fmt: skip
    assert asked, seen
    with sqlite3.connect(store.path) as conn:
        return [" | ".join(str(r[-1]) for r in conn.execute("EXPLAIN QUERY PLAN " + s)) for s in asked]


def test_the_lookups_by_batch_invoice_event_kind_account_expiry_and_erp_number_use_an_index(tmp_path, monkeypatch):
    """B-1581: an export undone, an empty batch, who may post a batch, a reopen or delete (the coding memory
    withdrawn), the events of a kind since a day, an account's sessions, the expired one-off values and the supplier
    sync's vendors by JD Edwards number each read their whole table (a year's invoices, events and coding memory)."""
    from ap_coder.core.store.users import POSTER_NOT_APPROVER
    from ap_coder.erp import jde_sync
    from tests.webclient import add_clean, gt

    store = Store(tmp_path / "ap.db")
    ids = [add_clean(store, invoice_number=f"N-{i}") for i in range(3)]
    for i in ids:
        store.approve_invoice(i, {**gt(), "invoice_number": f"N-{i}"}, "Ann")
    batch = store.create_export_batch(ids[:2], "csv", actor="Ann")
    store.set_setting(POSTER_NOT_APPROVER, "1")
    ann, boss = store.add_user("ann", "correct horse battery"), store.add_user("boss", "correct horse battery")
    token = store.start_session(ann)
    jde = type("Ais", (), {"rows": lambda self, part: [{"name": "Acme Ltd", "an8": "1001", "bank_last4": "1234"}]})
    lookups = [
        (lambda: store.posting_refusal(batch, ann), "events", "export_batch =", "invoices_export_batch"),
        (lambda: store.undo_export_batch(batch, actor="Ann"), "invoices", "export_batch =", "invoices_export_batch"),
        (lambda: store.delete_empty_batches({batch}), "invoices", "export_batch =", "invoices_export_batch"),
        (lambda: store.reopen(ids[2], "Bob", "wrong supplier"), "feedback", "invoice_id =", "feedback_invoice"),
        (lambda: store.delete_invoice(ids[1], True, "Ann"), "feedback", "invoice_id =", "feedback_invoice"),
        (lambda: store.touchless_summary(), "events", "created_at >=", "events_action_at"),
        (lambda: store.approval_controls("USD", "2026-01-01T00:00:00"), "events", "created_at >=", "events_action_at"),
        (lambda: store.vouched_for(boss, ann), "events", "created_at >=", "events_action_at"),
        (lambda: store.other_sessions(ann, token), "sessions", "user_id =", "sessions_user"),
        (lambda: store.end_sessions(ann, keep=token), "sessions", "user_id =", "sessions_user"),
        (lambda: store.deactivate_user(ann, actor="boss"), "sessions", "user_id =", "sessions_user"),
        (lambda: store.keep_ephemeral("upload", b"x", 60), "ephemeral", "expires_at <", "ephemeral_expires"),
        (lambda: jde_sync.sync_suppliers(store, jde(), "Ann"), "vendors", "erp_id =", "vendors_erp_id"),
    ]  # fmt: skip
    for call, table, where, index in lookups:
        for plan in _plans(store, monkeypatch, call, table, where):
            assert index in plan and "SCAN" not in plan, (table, where, plan)


def test_finding_an_invoices_vendor_seeks_its_name_in_the_vendor_list_and_picks_the_same_one(tmp_path, monkeypatch):
    """B-1582: find_vendor read the whole vendor list (a 20,000-vendor master) at every look, 3 or 4 an invoice: its
    test of a second vendor by the name ("<name> #<ERP ID>", B-930) defeated the vendor key's index. The same vendor
    is picked as before: by the GST/HST number, else the ERP ID, else the name's own, the first kept of several."""
    store = Store(tmp_path / "ap.db")
    gst = "123456789RT0001"
    with store._conn() as conn:
        for key, number, erp in (("acme #2002", gst, "2002"), ("acme", "", "1000"), ("acme #1001", gst, "1001"),
                                 ("acme #1002", "", "1002"), ("acme $", "", "9"), ("acme 1", "", "8"),
                                 ("acmee #1003", "", "7"), ("acm #1", "", "6"), ("acme #", "", "5")):  # fmt: skip
            conn.execute("INSERT INTO vendors (vendor_key, display_name, expected_gst, erp_id, updated_at) "
                         "VALUES (?, ?, ?, ?, '')", (key, key.title(), number, erp))  # fmt: skip
    picked = {
        (number, erp): store.find_vendor("Acme Ltd", number, erp)["vendor_key"]
        for number, erp in (("", ""), (gst, ""), ("", "1002"), ("", "5"), ("", "9"), ("", "7"), ("", "6"))
    }
    assert picked == {("", ""): "acme", (gst, ""): "acme #2002", ("", "1002"): "acme #1002", ("", "5"): "acme #",
                      ("", "9"): "acme", ("", "7"): "acme", ("", "6"): "acme"}  # fmt: skip
    plans = _plans(store, monkeypatch, lambda: store.find_vendor("Acme Ltd", gst), "vendors", "vendor_key")
    assert plans and all("SCAN" not in plan and "USING INDEX" in plan for plan in plans), plans


def test_a_vendors_invoices_without_a_po_are_read_from_the_vendors_invoices_not_from_every_one_without_a_po(
    tmp_path, monkeypatch
):
    """B-1583: the month end's accruals read, for each vendor with an open PO received, its invoices that quote no PO
    through the PO's index, where "no PO" is nearly every invoice (89%): the whole table, vendor after vendor."""
    from tests.webclient import add_clean

    store = Store(tmp_path / "ap.db")
    kept = [add_clean(store, invoice_number="N-1", po_number=""), add_clean(store, invoice_number="N-2", po_number="")]
    add_clean(store, invoice_number="N-3")  # quotes a PO (the sample's)
    store.reject_invoice(add_clean(store, invoice_number="N-4", po_number=""), "Ann", "not ours")  # no longer active
    add_clean(store, invoice_number="N-5", po_number="", vendor_name="Another Vendor Inc")
    key = store.get_invoice(kept[0])["vendor_key"]
    assert [i["id"] for i in store.vendor_invoices_without_po(key)] == kept
    (plan,) = _plans(store, monkeypatch, lambda: store.vendor_invoices_without_po(key), "invoices", "po_key")
    assert "invoices_vendor" in plan and "SCAN" not in plan and "invoices_po" not in plan, plan


def test_one_vendors_history_is_read_once_per_page(tmp_path, monkeypatch):
    """vendor_findings and expected_tax_numbers each read every invoice of the vendor, with its whole AI output and
    final coding parsed for five fields: now read once, by json_extract, and kept while nothing of the vendor's
    changes (from one page to the next: D-160)."""
    from ap_coder.coding.vendors import expected_tax_numbers, vendor_findings
    from ap_coder.core.schema import InvoiceCoding
    from tests.webclient import add_clean, gt

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
        assert reads() == 0  # kept since it was read above
        assert not [s for s in seen if "ORDER BY id DESC" in s and "ai_output, final_output" in s]
        store.reject_invoice(ids[1], "Ann", "duplicate")  # a change: read again
        history = store.vendor_invoices(key)
        assert reads() == 1 and [h["status"] for h in history] == ["review", "rejected", "approved"]
        with sqlite3.connect(store.path) as other:  # another connection's change: read again too
            other.execute("UPDATE invoices SET status = 'parked' WHERE id = ?", (ids[2],))
        assert store.vendor_invoices(key)[0]["status"] == "parked"
        assert reads() == 2


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


def test_the_metrics_read_the_learning_memory_again_only_when_it_changes(tmp_path, monkeypatch):
    from tests.webclient import add_clean, gt

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
    from tests.webclient import add_clean

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
    from tests.webclient import add_clean, client_for

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
    once = len(reads)  # (the .env's values are read, then its settings: B-1404)
    assert app.state.settings_factory() is first and len(reads) == once
    stamp = env.stat().st_mtime_ns + 5_000_000_000
    os.utime(env, ns=(stamp, stamp))  # saved again (the Settings page)
    app.state.settings_factory()
    assert len(reads) == 2 * once
    monkeypatch.setenv("AP_REVIEWER", "Someone else")  # the environment changed: read again too
    app.state.settings_factory()
    assert len(reads) > 2 * once


def test_a_list_of_invoices_does_not_look_for_each_file_every_time(tmp_path, monkeypatch):
    from pathlib import Path

    from tests.webclient import add_clean

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
    from tests.webclient import add_clean

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


def test_a_vendors_lessons_count_their_corrections_off_an_index_and_an_older_database_gets_it(tmp_path, monkeypatch):
    """B-1714: the review screen's "Learned" hints (``lessons_for``) counted each lesson's corrections, and the ones
    agreeing with it, by reading every approval that saw its situation: 1.4 s for the biggest vendor at 100,000
    invoices (220,000 approvals of its situations, 2.5% of them corrections). Both counts are read off one index now,
    and an older database's index by situation alone is replaced by it."""
    store = Store(tmp_path / "ap.db")
    row = {"key": "acme|invoice|invoice_number|", "vendor": "Acme", "kind": "invoice", "field": "invoice_number",
           "value": "A-1", "proposed": "1", "corrected": True}  # fmt: skip
    for n, value in ((1, "A-0"), (2, "A-1"), (3, "A-1")):
        store.record_lesson({**row, "value": value}, invoice_id=n)
    store.record_lesson({**row, "corrected": False}, invoice_id=4)  # confirmed
    plan = _plan(store, monkeypatch, lambda: store.lessons_for("acme"), "lessons")
    assert plan.count("COVERING INDEX lesson_approvals_counted") == 2, plan
    assert [(r["corrected"], r["agreeing"]) for r in store.lessons_for("acme")] == [(3, 2)]

    def indexes() -> set[str]:
        with sqlite3.connect(store.path) as conn:
            return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index' AND "
                                               "tbl_name = 'lesson_approvals' AND sql IS NOT NULL")}  # fmt: skip

    assert indexes() == {"lesson_approvals_counted"}
    with sqlite3.connect(store.path) as conn:  # as schema 24 left it
        conn.execute("DROP INDEX lesson_approvals_counted")
        conn.execute("CREATE INDEX lesson_approvals_situation ON lesson_approvals (situation)")
        conn.execute("UPDATE settings SET value = '24' WHERE key = 'schema_version'")
    assert indexes() == {"lesson_approvals_situation"}
    store = Store(store.path)
    assert indexes() == {"lesson_approvals_counted"}
    assert [(r["corrected"], r["agreeing"]) for r in store.lessons_for("acme")] == [(3, 2)]


def test_the_page_readers_latest_reads_are_read_off_an_index_and_an_older_database_gets_it(tmp_path, monkeypatch):
    """B-1734: the reading status on every page (how long the page reader's queue still takes) sorted every finished
    read to average the pages of the latest 50 (99,692 at 100,000 invoices: 44 ms a page). They are read off an index
    by status and time (0.02 ms), which a database made before gets at its next start."""
    store = Store(tmp_path / "ap.db")
    for invoice_id, pages in ((1, 1), (2, 3), (3, 2)):
        store.queue_page_read(invoice_id, "new")
        store.finish_page_read(invoice_id, "done", "ovis", pages=pages)
    plan = _plan(store, monkeypatch, store.page_read_pages_average, "page_reads")
    assert "INDEX page_reads_done" in plan and "TEMP B-TREE" not in plan, plan
    assert store.page_read_pages_average() == 2.0
    with sqlite3.connect(store.path) as conn:  # as a database made before it
        conn.execute("DROP INDEX page_reads_done")
    store = Store(store.path)
    assert "INDEX page_reads_done" in _plan(store, monkeypatch, store.page_read_pages_average, "page_reads")
    assert store.page_read_pages_average() == 2.0


def test_the_reading_status_counts_the_templates_and_the_open_queue_without_reading_them(tmp_path, monkeypatch):
    """B-1736: the reading status on every page counted the suppliers' learned templates by reading each one (3,008
    templates, 14.7 MB at 100,000 invoices: 6 to 13 ms a page), and the page reader's queue still open by listing
    every invoice in review first. The templates are counted off a partial index (a database made before gets it at
    its next start), the queue by looking up its own invoices."""
    from tests.webclient import add_clean

    store = Store(tmp_path / "ap.db")
    with sqlite3.connect(store.path) as conn:
        conn.executemany(
            "INSERT INTO supplier_profiles (key, template_json, updated_at) VALUES (?, ?, '2026-01-05')",
            [(f"s{n}", t) for n, t in enumerate(('{"fields": {}}', None, "", "null", "{}", "[1]"))],
        )
    plan = _plan(store, monkeypatch, store.supplier_templates_count, "supplier_profiles")
    assert "INDEX supplier_profiles_template" in plan, plan
    assert store.supplier_templates_count() == 2
    with sqlite3.connect(store.path) as conn:  # as a database made before it
        conn.execute("DROP INDEX supplier_profiles_template")
    store = Store(store.path)
    assert "INDEX supplier_profiles_template" in _plan(store, monkeypatch, store.supplier_templates_count,
                                                       "supplier_profiles")  # fmt: skip
    ids = [add_clean(store, invoice_number=f"P-{n}") for n in range(3)]
    for invoice_id in ids:
        store.queue_page_read(invoice_id, "new")
    store.reject_invoice(ids[0], "Ann", "a copy")
    plan = _plan(store, monkeypatch, lambda: store.page_reads_waiting(still_open=True), "page_reads")
    assert "LIST SUBQUERY" not in plan and "USING INTEGER PRIMARY KEY" in plan, plan
    assert store.page_reads_waiting(still_open=True) == 2 and store.page_reads_waiting() == 3


def test_the_queues_pay_from_accounts_are_each_vendors_latest_read_off_an_index_and_an_older_database_gets_it(
    tmp_path, monkeypatch
):
    """B-1739: the queue's Pay from (the account each waiting vendor's last approved invoice was paid from) ranked
    every approved invoice of those vendors on each visit once a bank account is in use (197 ms at 100,000 invoices).
    Each vendor's latest is sought on a partial index, which a database made before gets at its next start: the
    latest approval by time, then by number; one waiting for a second approval counts, one without an account or not
    approved doesn't. As ranking them all gives."""
    import json

    from ap_coder.core.memory import vendor_key

    store = Store(tmp_path / "ap.db")
    rows = [("Acme", "approved", "2026-01-02", "CAD-1"), ("Acme", "approved", "2026-01-03", "CAD-2"),
            ("Acme", "pending_approval", "2026-01-03", "CAD-3"),  # as late, a later number: the latest
            ("Acme", "approved", "2026-01-04", ""), ("Acme", "approved", "2026-01-05", None),  # no account
            ("Acme", "review", "2026-01-06", "CAD-9"), ("Bolt", "approved", None, "USD-0"),
            ("Bolt", "approved", "2026-01-01", "USD-1"), ("Bolt", "rejected", "2026-01-09", "USD-9"),
            ("Core", "approved", "2026-01-01", 7)]  # fmt: skip
    for n, (vendor, status, at, code) in enumerate(rows):
        pdf = tmp_path / f"{n}.pdf"
        pdf.write_bytes(f"%PDF-1.4 {n}".encode())
        invoice_id = store.add_invoice(pdf, {"vendor_name": vendor, "invoice_number": f"P-{n}", "grand_total": n}, {})
        with store._conn() as conn:
            conn.execute(
                "UPDATE invoices SET status = ?, reviewed_at = ?, final_output = ? WHERE id = ?",
                (status, at, json.dumps({"vendor_name": vendor, "pay_from_account": code}), invoice_id),
            )
    keys = [vendor_key(v) for v in ("Acme", "Bolt", "Core", "Dune")] + ["", vendor_key("Acme")]
    ranked = {r["vendor_key"]: str(r["code"]) for r in store.summary_fetch(
        "SELECT vendor_key, code FROM (SELECT vendor_key, pay_from AS code, ROW_NUMBER() OVER (PARTITION BY "
        "vendor_key ORDER BY reviewed_at DESC, id DESC) AS n FROM invoice_summary WHERE status IN ('approved', "
        "'pending_approval') AND COALESCE(pay_from, '') != '' AND vendor_key IN (SELECT value FROM json_each(?))) "
        "WHERE n = 1", (json.dumps(keys),))}  # fmt: skip
    assert store.last_pay_froms(keys) == ranked == {vendor_key("Acme"): "CAD-3", vendor_key("Bolt"): "USD-1",
                                                    vendor_key("Core"): "7"}  # fmt: skip
    assert store.last_pay_from(vendor_key("Acme")) == "CAD-3" and store.last_pay_from("") == ""
    plan = _plan(store, monkeypatch, lambda: store.last_pay_froms(keys), "invoice_summary")
    assert "INDEX summary_pay_from" in plan and "TEMP B-TREE" not in plan and "SCAN invoice_summary" not in plan, plan
    with sqlite3.connect(store.path) as conn:  # as a database made before it
        conn.execute("DROP INDEX summary_pay_from")
    store = Store(store.path)
    assert "INDEX summary_pay_from" in _plan(store, monkeypatch, lambda: store.last_pay_froms(keys), "invoice_summary")
    assert store.last_pay_froms(keys) == ranked


def test_the_invoice_directorys_first_page_in_its_default_order_is_read_off_an_index(tmp_path, monkeypatch):
    """B-1740: the Search page opens on every invoice, newest received first, and each view sorted all of them for
    its first 50 (100,000 at 100,000 invoices: 81 ms). An index in that order reads the page's ids alone, which a
    database made before gets at its next start; the rows are the ones sorting them all gives."""
    from ap_coder.erp import directory

    store = Store(tmp_path / "ap.db")
    for n, created in enumerate(("2026-01-03T09:00", "2026-01-01T09:00", "2026-01-03T09:00", "", "2026-01-02T09:00")):
        pdf = tmp_path / f"{n}.pdf"
        pdf.write_bytes(f"%PDF-1.4 {n}".encode())
        invoice_id = store.add_invoice(pdf, {"vendor_name": "Acme", "invoice_number": f"S-{n}", "grand_total": n}, {})
        with store._conn() as conn:
            conn.execute("UPDATE invoices SET created_at = ? WHERE id = ?", (created, invoice_id))
    query = directory.Query()

    def sorted_off_the_index() -> bool:
        plans = _plans(store, monkeypatch, lambda: directory.run(store, query), "rows", "ORDER BY")
        return all("INDEX summary_received" in p and "TEMP B-TREE" not in p for p in plans)

    assert sorted_off_the_index()
    assert [r["id"] for r in directory.run(store, query).rows] == [3, 1, 5, 2, 4]  # the latest first, none last
    with sqlite3.connect(store.path) as conn:  # as a database made before it
        conn.execute("DROP INDEX summary_received")
    store = Store(store.path)
    assert sorted_off_the_index() and [r["id"] for r in directory.run(store, query).rows] == [3, 1, 5, 2, 4]


def test_the_queues_facts_carry_each_findings_text_once_for_all_the_invoices_that_have_it(tmp_path, monkeypatch):
    """B-1741: after each approval or park the queue read its invoices' facts again, every invoice's findings texts
    with them (2.1 MB in 190 texts for the 2,654 invoices waiting at 100,000: 36 of the 57 ms). Each text is read once
    for the invoices that have it, in the same read as their other facts: what reading each row gives."""
    import json

    from ap_coder.coding import bulk
    from ap_coder.core.store import REVIEW

    store = Store(tmp_path / "ap.db")
    total = [{"severity": "error", "code": "TOTAL_MISMATCH", "message": "The totals differ"}]
    late = {"stamp": "s2", "issues": [{"severity": "warning", "code": "DUE_DATE_PAST", "message": "Due"}]}
    found = [{"issues": total, "checks_stamp": "s1"}, {"issues": total, "checks_stamp": "s1", "recheck": late},
             {"issues": total, "checks_stamp": "s1", "recheck": late}, {"issues": []}, None,
             {"issues": total, "checks_stamp": "s1"}]  # fmt: skip
    ids = []
    for n, validation in enumerate(found):
        pdf = tmp_path / f"{n}.pdf"
        pdf.write_bytes(f"%PDF-1.4 {n}".encode())
        ids.append(store.add_invoice(pdf, {"vendor_name": "Acme", "invoice_number": f"F-{n}", "grand_total": n},
                                     validation))  # fmt: skip
    store.park_invoice(ids[-1], "Jordan Lee", "waiting for the buyer")  # (not in review: none of the queue's)
    asked = [*reversed(ids), 999]
    each = store.summary_rows("status = ? AND id IN (SELECT value FROM json_each(?))", (REVIEW, json.dumps(asked)),
                              bulk.QUEUE_COLUMNS)  # fmt: skip
    sent = []
    real = store_mod.summary._decode_rows
    monkeypatch.setattr(store_mod.summary, "_decode_rows", lambda text: sent.append(text) or real(text))
    rows = store.queue_rows(asked)
    assert rows == each and [list(r) for r in rows] == [list(r) for r in each] and len(rows) == 5
    assert "".join(sent).count("TOTAL_MISMATCH") == 1 and "".join(sent).count("DUE_DATE_PAST") == 1
    assert bulk._queue_rows(store, asked) == {r["id"]: r for r in each}


def test_the_duplicate_check_seeks_the_invoice_number_not_each_invoice_of_the_vendor(tmp_path, monkeypatch):
    """B-1832: each check (the review screen's, every invoice read) looked for the same vendor's invoice with the same
    number by reading every invoice of the vendor and comparing each number in Python (23,000 for the biggest vendor at
    300,000 invoices: 3.3% of the busy web workers' CPU). It seeks the number as it compares, off the summary's index:
    what comparing each gives, the same number written another way included, a rejected or failed one and the invoice
    itself left out, a credit note no duplicate of an invoice."""
    from tests.webclient import add_clean

    store = Store(tmp_path / "ap.db")
    first = add_clean(store, invoice_number="INV-00123")
    second = add_clean(store, invoice_number="#123")
    add_clean(store, invoice_number="124")
    store.reject_invoice(add_clean(store, invoice_number="123"), "Ann", "a copy")
    credit = add_clean(store, invoice_number="123", grand_total=-50.0)
    other = add_clean(store, invoice_number="123", vendor_name="Another Vendor Inc")
    with sqlite3.connect(store.path) as conn:  # failed later, by a plain connection: the summary follows it
        conn.execute("UPDATE invoices SET status = 'failed' WHERE id = ?", (second,))
    name = store.get_invoice(first)["vendor_name"]
    assert store.find_duplicates(name, "123") == [first, credit]
    assert store.find_duplicates(name, "inv 123", exclude_id=first, grand_total=10.0) == []
    assert store.find_duplicates(name, "123", grand_total=-1.0) == [credit]
    assert store.find_duplicates("Another Vendor Inc", "0123") == [other]
    assert store.find_duplicates(name, "") == [] and store.find_duplicates("", "123") == []
    (plan,) = _plans(store, monkeypatch, lambda: store.find_duplicates(name, "123"), "invoice_summary", "number_key")
    assert "summary_number" in plan and "SCAN" not in plan, plan
