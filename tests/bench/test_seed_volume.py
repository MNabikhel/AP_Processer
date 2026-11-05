"""``python -m ap_coder.bench seed`` (a big server-mode data folder made fast) and ``volume`` (its pages timed, their
full scans found), on a small folder: what the seed writes is what the app would have (its summary as the reports
read every invoice, its queue's checks current, its people able to log in)."""

import datetime as dt
import json
import sqlite3

import pytest

from ap_coder.bench import volume
from ap_coder.bench.__main__ import main
from ap_coder.coding import taxreturn
from ap_coder.core.paths import file_sha256
from ap_coder.core.store import APPROVED, PARKED, PENDING, REVIEW, SCHEMA_VERSION, Store, database_schema
from ap_coder.erp import insights, spend
from tests import summary_reference as ref
from tests.conftest import isolated

YEAR = (dt.date.today() - dt.timedelta(days=400), dt.date.today())


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    folder = tmp_path_factory.mktemp("seed")
    data = folder / "data"
    with isolated(folder):
        assert main(["seed", "--out", str(data), "--invoices", "200", "--vendors", "40", "--master", "300", "--gl",
                     "200", "--cost-centers", "30", "--pos", "20", "--users", "8", "--queue", "60", "--register",
                     "500", "--workers", "1"]) == 0  # fmt: skip
    return data


def test_the_seed_makes_the_folder_it_says(seeded):
    report = json.loads((seeded / "seed.json").read_text(encoding="utf-8"))
    counts = report["counts"]
    assert counts["rows/invoices"] == counts["rows/invoice_summary"] == 200 and counts["rows/summary_dirty"] == 0
    assert counts["rows/gl_accounts"] == 200 and counts["rows/cost_centers"] == 30 and counts["rows/users"] == 8
    assert counts["rows/erp_invoices"] == 500 and counts["rows/feedback"] > 200 and counts["rows/events"] > 400
    assert report["sizes"]["per_1000_invoices_mb"] > 0 and report["seconds"]["total"] > 0
    people = json.loads((seeded / "users.json").read_text(encoding="utf-8"))
    assert {p["role"] for p in people} == {"admin", "approver", "clerk", "readonly"}
    assert all(len(p["password"]) >= 12 and len(p["totp_secret"]) == 32 for p in people)
    # A database as this version makes it: nothing to migrate, in WAL mode, every invoice's file where it says, each
    # with a SHA-256 of its own (a clone's file is its template's, ids 1-10: hashing it finds the template).
    assert database_schema(seeded / "ap_coder.db") == SCHEMA_VERSION
    conn = sqlite3.connect(seeded / "ap_coder.db")
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        files = conn.execute("SELECT id, source_path, file_sha256 FROM invoices").fetchall()
    finally:
        conn.close()
    assert len({sha for _, _, sha in files}) == len(files) == 200
    templates = {sha: i for i, _, sha in files if i <= 10}
    assert all(file_sha256(path) == sha for i, path, sha in files if i <= 10)
    assert all(file_sha256(path) in templates for _, path, _ in files)
    with pytest.raises(FileExistsError):  # never over a folder made already
        from ap_coder.bench.seed import build

        build(seeded, invoices=20, vendors=10, users=4)


def test_what_the_seed_writes_reads_as_the_app_would_have_it(seeded, tmp_path):
    with isolated(tmp_path):
        store = Store(seeded / "ap_coder.db")
        statuses = store.status_counts()
        assert statuses[REVIEW] and statuses[APPROVED] and statuses.get(PARKED, 0) + statuses.get(PENDING, 0)
        assert 10 < store.ready_to_export_count() < statuses[APPROVED]  # the newest day's approvals: not exported yet
        # The summary, the quick search's rows and the reports as they were worked out before the summary (every
        # invoice read and decoded): tests/summary_reference.py.
        for status in (None, REVIEW, APPROVED, PARKED, PENDING, "rejected", "failed"):
            assert store.list_invoices(status) == ref.old_list_invoices(store, status)
        assert store.search_rows() == ref.old_search_rows(store)
        assert insights.compute(store) == ref.insights_compute(store)
        assert spend.lines(store, *YEAR) == ref.spend_lines(store, *YEAR)
        new, old = taxreturn.build(store, *YEAR), ref.tax_build(store, *YEAR)
        assert [(c.invoice_id, c.amount) for c in new.claims] == [(c.invoice_id, c.amount) for c in old.claims]
        # ...and as the store works it out again now.
        tables = ("invoice_summary", "summary_facts", "summary_codes", "summary_spend")
        with store._conn() as conn:
            before = {t: sorted(map(tuple, conn.execute(f"SELECT * FROM {t}"))) for t in tables}
            conn.execute("INSERT OR IGNORE INTO summary_dirty (id) SELECT id FROM invoices")
        with store._conn() as conn:
            assert {t: sorted(map(tuple, conn.execute(f"SELECT * FROM {t}"))) for t in tables} == before
        # The queue's checks stamped with today's reference data: the queue doesn't check them all again.
        queue = [r["id"] for r in store.list_invoices(REVIEW) if r["id"] > 10]  # the clones (ids 1-10: templates)
        stamps = store.check_stamps(queue)
        assert queue and {stamps[i][0] for i in queue} == {store.checks_stamp()}


def test_the_volume_bench_times_each_address_and_lists_the_full_scans(seeded, tmp_path):
    out = tmp_path / "volume"
    report = volume.run(seeded, out, roles=("admin", "clerk"), repeat=1, big=100,
                        only=("/review", "/review/{invoice_id:int}"))  # fmt: skip
    asked = {(r["role"], r["address"]) for r in report["results"]}
    assert ("admin", "/review") in asked and ("clerk", "/review?tab=approved") in asked
    assert {r["route"] for r in report["results"]} == {"/review", "/review/{invoice_id:int}"}
    assert all(r["status"] == 200 and r["statements"] > 0 and r["median"] > 0 for r in report["results"])
    assert all(r["target"] == volume.TARGETS["page"] for r in report["results"])
    assert report["big_tables"]["invoices"] == 200 and "lesson_approvals" in report["big_tables"]
    for scan in report["scans"]:  # each one named with its table, its rows and the addresses that caused it
        assert scan["table"] in report["big_tables"] and scan["routes"] and scan["sql"]
    text = (out / "report.md").read_text(encoding="utf-8")
    assert "| `/review` | admin | page | 200 |" in text and "## Full scans of big tables" in text
    assert json.loads((out / "report.json").read_text(encoding="utf-8"))["summary"]["asked"] == len(asked)


def test_the_full_scans_are_read_from_the_plan_with_the_tables_aliases():
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE invoices (id INTEGER PRIMARY KEY, status TEXT, vendor_name TEXT)")
        conn.execute("CREATE INDEX invoices_status ON invoices (status)")
        big = {"invoices": 300_000}
        assert volume.scans(conn, "SELECT * FROM invoices i WHERE i.vendor_name = 'x'", big) == [
            {"table": "invoices", "rows": 300_000, "how": "table"}]  # fmt: skip
        assert volume.scans(conn, "SELECT COUNT(*) FROM invoices", big)[0]["how"].startswith("COVERING INDEX")
        assert volume.scans(conn, "SELECT id FROM invoices WHERE status = 'review'", big) == []
        assert volume.scans(conn, "SELECT * FROM invoices", {}) == []  # not a big table
        assert volume.scans(conn, "PRAGMA table_info(invoices)", big) == []
        # fetch_rows naming its columns reads no row; the first row of a table stops there
        assert volume.scans(conn, "SELECT * FROM (SELECT * FROM invoices ORDER BY id) LIMIT 0", big) == []
        assert volume.scans(conn, "SELECT 1 FROM invoices LIMIT 1", big) == []
    finally:
        conn.close()
    assert volume.shape("SELECT * FROM t WHERE a = 'it''s' AND b = 12.5") == "SELECT * FROM t WHERE a = ? AND b = ?"
