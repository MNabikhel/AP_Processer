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


def test_a_seed_made_in_this_process_leaves_its_logging_as_it_was(tmp_path):
    """B-1759: a seed made in the caller's own process (``--workers 1``: the tests, the load bench's small folder)
    turned its INFO logging off for good (the clones' quiet, meant for their worker processes): every later test of
    the run that reads a log (the access log, the background process's, the launcher window's) read nothing."""
    import logging

    before = logging.root.manager.disable
    with isolated(tmp_path):
        assert main(["seed", "--out", str(tmp_path / "data"), "--invoices", "200", "--vendors", "40", "--master",
                     "300", "--gl", "200", "--cost-centers", "30", "--pos", "20", "--users", "8", "--queue", "60",
                     "--register", "500", "--workers", "1"]) == 0  # fmt: skip
    assert logging.root.manager.disable == before
    said: list[str] = []
    log = logging.getLogger("ap_coder.test.seed_logging")
    handler = logging.Handler()
    handler.emit = lambda record: said.append(record.getMessage())  # type: ignore[method-assign]
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    try:
        log.info("an INFO line still reaches its log")
    finally:
        log.removeHandler(handler)
    assert said == ["an INFO line still reaches its log"]


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


def test_the_big_reports_give_what_they_gave_on_the_seed(seeded, tmp_path, monkeypatch):
    """D-156: the reports made light for a year of invoices (summed and picked by SQLite, read a batch at a time) give
    what they gave reading every invoice, PO or event (``tests/reports_reference.py``), on the seed's realistic data:
    the Spend page and its workbook, month-end's received-not-invoiced lines, the controls report's one-person check
    and the JD Edwards duplicate checks of the ready invoices (with copies of exported bills and of the register's)."""
    import re

    from ap_coder.erp import accruals, controls, jde, jde_settings, spend
    from ap_coder.web.pages import exports as exports_page
    from tests import reports_reference as old
    from tests.conftest import copy_database
    from tests.webclient import client_for

    with isolated(tmp_path):
        store = copy_database(seeded / "ap_coder.db", tmp_path / "private" / "ap_coder.db")
        ready = store.unexported_approved()
        exported = [r for r in store.search_rows(exported=True)][:3]
        register = store.erp_register()[:3]
        for n, source in enumerate(exported + register):  # copies, approved: duplicates of a bill already out
            doc = store.get_invoice(ready[n]["id"])["final_output"]
            copy = {**doc, "vendor_name": source["vendor_name"], "invoice_number": source["invoice_number"]}
            store.approve_invoice(store.add_invoice(tmp_path / f"copy-{n}.pdf", copy, {}), copy, "Jordan Lee")
        invoices = store.get_invoices([r["id"] for r in store.unexported_approved()])
        settings, lookup = jde_settings.load(store), store.vendor_ids()
        found = jde.validate(invoices, settings, lookup, jde.prior_records(store, invoices=invoices))
        assert found == jde.validate(invoices, settings, lookup, jde.prior_records(store))
        assert sum("already exported" in m or "already in the ERP" in m for ms in found.values() for m in ms) >= 6
        assert exports_page.jde_check(store, [i["id"] for i in invoices])[1] == found
        batch = next(b["id"] for b in store.export_batches() if not b["undone_at"])
        ids = store.batch_invoice_ids(batch)
        batch_invoices = store.get_invoices(ids)
        assert jde.validate(batch_invoices, settings, lookup, jde.prior_records(store, ids, batch_invoices)) == \
            jde.validate(batch_invoices, settings, lookup, jde.prior_records(store, ids))  # fmt: skip

        client = client_for(store)
        client.app.add_api_route("/old-spend", old.spend_page)
        today = dt.date.today()
        for query in ("", "?currency=CAD", "?currency=USD", f"?start={today - dt.timedelta(days=60)}&end={today}"):
            new, was = (re.sub(r"(apc-chart-[a-z0-9-]+?)-\d+-", r"\1-0-", client.get(f"{path}{query}").text)
                        for path in ("/spend", "/old-spend"))  # fmt: skip
            assert new == was.replace("/old-spend", "/spend"), query
        monkeypatch.setattr(spend, "WORKBOOK_BATCH", 37)
        assert old.workbook_contents(spend.workbook(store, store.reference_data())) == old.workbook_contents(
            old.spend_workbook(store, store.reference_data()))  # fmt: skip
        for end in (today, today - dt.timedelta(days=90)):
            assert accruals._received_not_invoiced(store, end) == old.received_not_invoiced(store, end)
        rates, limit = store.fx_rates(), store.approval_limit()
        for first, last in ((YEAR[0].isoformat(), YEAR[1].isoformat()), (today.replace(day=1).isoformat(), "9999")):
            assert controls.one_person(store, first, last, limit, rates) == old.one_person(store, first, last, limit,
                                                                                            rates)  # fmt: skip


def test_exports_activity_and_automation_show_on_the_seed_what_they_showed_before_after_each_change(
    seeded, tmp_path, monkeypatch
):
    """D-206: Exports (its JD Edwards check of the ready invoices, worked out once for every process), Activity (the
    events counted and their people found off their indexes, its approvals and controls report kept) and Settings →
    Automation (the audits off their own index) give an Admin and a Clerk the pages they gave before, byte for byte
    (``tests/reports_reference.py``, nothing kept), on the seed and after each change that matters to them, made by
    another Store or plain SQL; asked again, the same."""
    import dataclasses
    import shutil

    from fastapi.testclient import TestClient

    from ap_coder.core.memory import vendor_key
    from ap_coder.core.store import summary
    from ap_coder.erp import jde_settings
    from ap_coder.web.app import create_app
    from ap_coder.web.pages import exports as exports_page
    from tests import reports_reference as old
    from tests.conftest import copy_database
    from tests.webclient import sign_in, web_settings

    people = {p["role"]: p for p in json.loads((seeded / "users.json").read_text(encoding="utf-8"))}
    from ap_coder.core import ui
    from ap_coder.web import core as web_core

    at, ago = dt.datetime.now(), ui.time_ago  # one clock for every render: Activity's "97 days ago" turns "98 days ago"

    def time_ago(iso: str | None, now: dt.datetime | None = None) -> str:  # when a day's end passes between two renders
        return ago(iso, now or at)  # a moment apart (the seed dates its events back from the moment it seeds)

    monkeypatch.setattr(ui, "time_ago", time_ago)
    monkeypatch.setitem(web_core.templates.env.filters, "time_ago", time_ago)
    monkeypatch.setattr(summary, "COUNTS_SETTLE", 0.0)  # (the rail's counts exact: their settle, D-164, not compared)
    with isolated(tmp_path):
        store = copy_database(seeded / "ap_coder.db", tmp_path / "private" / "ap_coder.db")
        shutil.copy(seeded / "authenticator.key", store.path.parent / "authenticator.key")
        other = Store(store.path)  # another web worker, or the background process
        app = create_app(web_settings(), store, start_reader=False, server=True)
        clients = {}
        for role in ("admin", "clerk"):
            clients[role] = TestClient(app, base_url="https://localhost", client=("10.0.0.5", 50000),
                                       follow_redirects=False)  # fmt: skip
            assert sign_in(clients[role], people[role]["username"], people[role]["password"]).status_code == 200
        ready = store.unexported_approved()
        word = ready[0]["vendor_name"].split()[0]
        pages = {"admin": ["/exports", "/exports?fmt=jde", "/activity", f"/activity?q={word}", "/settings/automation"],
                 "clerk": ["/exports", "/exports?fmt=jde", "/activity", f"/activity?q={word}"]}  # fmt: skip

        def shown() -> dict[str, str]:
            got = {}
            for role, addresses in pages.items():
                for address in addresses:
                    answer = clients[role].get(address)
                    assert answer.status_code == 200, (role, address)
                    got[f"{role} {address}"] = answer.text
            return got

        mine = {"approval_counts", "activity_controls", "jde_ready"}  # (D-206's: worked out afresh before)
        memo, shared = Store.summary_memo, Store.shared_answer

        def before() -> dict[str, str]:
            with monkeypatch.context() as m:
                m.setattr(Store, "count_events", old.count_events)
                m.setattr(Store, "event_actors", old.event_actors)
                m.setattr(Store, "touchless_summary", old.touchless_summary)
                m.setattr(Store, "summary_memo", lambda self, name, compute, *a, **k: compute() if name in mine
                          else memo(self, name, compute, *a, **k))  # fmt: skip
                m.setattr(Store, "shared_answer", lambda self, name, compute, *a, **k: compute() if name in mine
                          else shared(self, name, compute, *a, **k))  # fmt: skip
                m.setattr(exports_page, "ready_problems",
                          lambda s, ids: exports_page.problem_rows(s, exports_page.jde_check(s, ids)[1]))  # fmt: skip
                return shown()  # (the controls tiles as drawn before: tests/web/test_web_analytics.py, B-2073)

        last: list[dict[str, str]] = []

        def same(why: str, changed: bool = True) -> None:
            now, was = shown(), before()
            for page, text in now.items():
                assert text == was[page], (why, page)
            assert shown() == now, why  # (kept)
            assert not changed or now != last[-1], why  # (a change the pages show)
            last.append(now)

        last.append(shown())
        same("the seed", changed=False)
        assert "Why not" in last[-1]["admin /exports?fmt=jde"]
        *_, waiting, review = (r["id"] for r in store.list_invoices(REVIEW))
        doc = store.get_invoice(review)["ai_output"]
        other.approve_invoice(review, doc, "Jordan Lee")
        same("an approval: one more ready, an event")
        first = store.get_invoice(ready[0]["id"])["final_output"]
        other.import_erp_register([{"vendor_name": first["vendor_name"], "invoice_number": first["invoice_number"],
                                    "invoice_date": first["invoice_date"], "total": first["grand_total"]}])  # fmt: skip
        same("a ready invoice's bill in the ERP register")
        with sqlite3.connect(store.path) as conn:
            conn.execute("UPDATE vendors SET erp_id = 'X' || erp_id WHERE erp_id != ''")
        same("the vendor master's ERP IDs, in plain SQL")
        with sqlite3.connect(store.path) as conn:
            conn.execute("UPDATE invoices SET final_output = json_set(final_output, '$.invoice_number', ?, "
                         "'$.vendor_name', ?) WHERE id = ?",
                         (first["invoice_number"], first["vendor_name"], ready[1]["id"]))  # fmt: skip
        same("a ready invoice the same bill as another, in plain SQL")
        assert "already in the ERP" in last[-1]["clerk /exports?fmt=jde"]
        settings = jde_settings.load(other)
        jde_settings.save(other, dataclasses.replace(settings, company="ABC"), actor="Jordan Lee")
        same("JD Edwards' settings: a company that stops every invoice")
        overrides = {**settings.an8_overrides, vendor_key(ready[6]["vendor_name"]): "ABC"}
        jde_settings.save(other, dataclasses.replace(settings, an8_overrides=overrides), actor="Jordan Lee")
        same("JD Edwards' settings: a vendor's supplier number")
        assert "supplier number &#39;ABC&#39;" in last[-1]["admin /exports?fmt=jde"]
        jde_settings.save(other, settings, actor="Jordan Lee")
        same("and back")
        with sqlite3.connect(store.path) as conn:
            conn.execute("UPDATE invoices SET status = 'pending_approval' WHERE id = ?", (waiting,))
        same("an invoice waiting for its second approval, in plain SQL")
        other.log_event("note", actor="Someone New")
        same("an event by someone new")
        other.log_event("login", actor="Only Logs In")
        same("a login by someone new: the Admin's people only")
        assert "Only Logs In" in last[-1]["admin /activity"] and "Only Logs In" not in last[-1]["clerk /activity"]
        other.set_setting("approval_limit", "50", actor="Jordan Lee")
        same("the approval limit")
        other.create_export_batch([r["id"] for r in ready[2:5]], "csv", actor="Jordan Lee")
        same("an export: three fewer ready")
        other.record_outcomes("name:acme", ready[5]["id"], [{"field": "invoice_number", "ai_value": "1",
                                                             "final_value": "2", "correct": False}],
                              source="audit")  # fmt: skip
        same("an audit of a touchless invoice, corrected")


def test_each_clone_has_amounts_of_its_own_that_add_up(seeded):
    conn = sqlite3.connect(seeded / "ap_coder.db")
    try:
        rows = conn.execute("SELECT vendor_key, grand_total, COALESCE(final_output, ai_output), validation "
                            "FROM invoices WHERE id > 10 AND status != 'failed'").fetchall()  # fmt: skip
    finally:
        conn.close()
    assert len({(vendor, total) for vendor, total, _, _ in rows}) > 0.95 * len(rows)  # same vendor and amount: rare
    for _, total, text, validation in rows:
        doc, checks = json.loads(text), json.loads(validation)["checks"]
        lines = round(sum(li["amount"] for li in doc["line_items"]), 2)
        assert all(round(li["quantity"] * li["unit_price"], 2) == li["amount"] for li in doc["line_items"])
        assert all(
            abs(t["rate"] * t["taxable_amount"] - t["tax_amount"]) <= 0.011 for t in doc["tax_lines"] if t["rate"]
        )
        assert round(doc["subtotal"] + doc["tax_total"], 2) == doc["grand_total"] == total == checks["grand_total"]
        assert lines == checks["line_sum"] and round(sum(r["amount"] for r in doc["gl_distribution"]), 2) == total


def test_the_seed_has_its_bank_accounts_in_use_as_a_real_installation_has_them(seeded, tmp_path):
    """B-1739: the seed set up no bank account, so the load runs never asked the queue's Pay from (each waiting
    vendor's last account, 197 ms a visit at 100,000 invoices). Each invoice is paid from its currency's account: as
    approved, and as the queue offers it."""
    with isolated(tmp_path):
        store = Store(seeded / "ap_coder.db")
        accounts = [(a["code"], a["currency"]) for a in store.bank_accounts(active_only=True)]
        assert accounts == [("CAD-OP", "CAD"), ("USD-OP", "USD")]
        signed = store.summary_fetch("SELECT currency, pay_from, vendor_key FROM invoice_summary WHERE status IN "
                                     "('approved', 'pending_approval')")  # fmt: skip
        assert {r["currency"] for r in signed} == {"CAD", "USD"}
        assert all(r["pay_from"] == f"{r['currency']}-OP" for r in signed)
        waiting = store.summary_fetch("SELECT DISTINCT vendor_key FROM invoice_summary WHERE status = ?", (REVIEW,))
        last = store.last_pay_froms([r["vendor_key"] for r in waiting])
        assert last and set(last.values()) <= {"CAD-OP", "USD-OP"}


def test_the_seed_stands_in_for_the_day_s_backup_so_serving_it_copies_nothing(seeded, tmp_path):
    with isolated(tmp_path):
        store = Store(seeded / "ap_coder.db")
        (marker,) = store.list_backups()
        assert store.auto_backup() is None and store.list_backups() == [marker]  # no copy of the database made
        made = marker.stat().st_mtime
        assert main(["seed", "--out", str(seeded), "--mark-backed-up"]) == 0  # made again before another run
        assert store.list_backups()[0].stat().st_mtime >= made and store.auto_backup() is None
        assert not (seeded / "backups" / "files").exists()  # no invoice file copied either


def test_the_stand_in_for_the_backup_is_refused_in_a_folder_the_seed_did_not_make(tmp_path, capsys):
    real = tmp_path / "APCoder"  # an installation's data folder: its database, but no seed.json
    real.mkdir()
    sqlite3.connect(real / "ap_coder.db").close()
    assert main(["seed", "--out", str(real), "--mark-backed-up"]) == 1  # its daily backups would stop for 20 hours
    assert not (real / "backups").exists() and "seed.json" in capsys.readouterr().err


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


def test_the_volume_bench_asks_an_address_with_a_query_of_its_own(seeded, tmp_path):
    """``--only`` an address the bench doesn't list, with a query of its own (a period's controls report, Activity of
    a period): asked as it is, as its route's (its role rule, its target)."""
    import shutil

    data = shutil.copytree(seeded, tmp_path / "data")  # (its own: the seed's logins left for the tests after it)
    week = (dt.date.today() - dt.timedelta(days=7)).isoformat()
    report = volume.run(data, tmp_path / "volume", roles=("admin",), repeat=1, big=100,
                        only=(f"/activity/controls-report?start={week}", f"/activity?start={week}"))  # fmt: skip
    assert {(r["route"], r["address"], r["kind"], r["status"]) for r in report["results"]} == {
        ("/activity/controls-report", f"/activity/controls-report?start={week}", "report", 200),
        ("/activity", f"/activity?start={week}", "page", 200)}  # fmt: skip


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


def test_a_seed_on_a_file_system_out_of_links_for_a_template_copies_it_and_goes_on(tmp_path, monkeypatch):
    """B-1943: the seed's invoices are hard links to a few templates' PDFs, and NTFS allows 1,023 links to one file:
    at 20,000 invoices the Windows CI job's seed failed ("An attempt was made to create more links on a file than the
    file system supports"). A template out of links is copied once and the copy takes the next links."""
    import errno
    import os

    real, made = os.link, {}

    def link(src, dst, *args, **kwargs):  # a file system allowing 3 links to a file
        if made.get(str(src), 0) >= 3:
            raise OSError(errno.EMLINK, "Too many links", str(src))
        made[str(src)] = made.get(str(src), 0) + 1
        return real(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "link", link)
    data = tmp_path / "data"
    with isolated(tmp_path):
        assert main(["seed", "--out", str(data), "--invoices", "60", "--vendors", "10", "--master", "50", "--gl",
                     "50", "--cost-centers", "10", "--pos", "5", "--users", "4", "--queue", "20", "--register",
                     "50", "--workers", "1"]) == 0  # fmt: skip
    files = [p for p in (data / "invoices").iterdir() if p.suffix == ".pdf"]
    assert len(files) >= 60 and all(p.stat().st_size > 0 for p in files)
    assert max(made.values()) <= 3


# The full scans of big tables the hot pages (the queue, an invoice, the search, Import invoices, the vendor list)
# run, each with why it is not a scan of what grows with the invoices at every request (target 2 of the scale goal:
# no full scans on hot paths, guarded by tests). A new one fails the test; one gone must leave the list too.
HOT_PAGES = ("/review", "/review/{invoice_id:int}", "/search", "/process", "/review/vendors.json")
HOT_SCANS = {
    ("feedback", "/review/{invoice_id:int}", "table"): "the learning memory's stamp and its read: kept by the process "
    "until a lesson is added, FEEDBACK_READ lessons at most (D-173, D-195)",
    ("vendors", "/review/{invoice_id:int}", "table"): "the vendor list and the master's numbers: kept by the process "
    "until a vendor changes (D-159, D-174)",
    ("vendors", "/review", "table"): "the vendors' terms: kept until a vendor changes (D-165)",
    (
        "invoice_summary",
        "/review",
        "table",
    ): "the regular vendors' dates for the queue's late pill: worked out once for "
    "every process, settled 60 s (D-175, B-1803)",
    ("vendors", "/process", "table"): "the master's vendors with an ERP id: kept until a vendor changes",
    ("vendors", "/review/vendors.json", "table"): "whether the master has a vendor at all (LIMIT 1)",
    ("gl_accounts", "/review", "index"): "the chart of accounts (a few thousand rows): the reference data, kept",
    ("gl_accounts", "/review/{invoice_id:int}", "index"): "the chart of accounts: the reference data, kept",
    ("gl_accounts", "/process", "index"): "the chart of accounts: the reference data, kept",
    ("invoice_summary", "/review/{invoice_id:int}", "index"): "the counts by status (the rail), off a covering index, "
    "settled 5 s (D-164)",
    ("invoice_summary", "/process", "index"): "the invoices received, off a covering index: kept until one comes in",
    ("invoice_summary", "/search", "index"): "the directory's rows off covering indexes, paged in SQL (D-157)",
}


def test_the_hot_pages_scan_no_big_table_but_those_listed_with_why(seeded, tmp_path):
    import shutil

    data = shutil.copytree(seeded, tmp_path / "data")  # (its own, the answers kept for every process let go: each
    with sqlite3.connect(data / "ap_coder.db") as conn:  # page's first look is the one measured, whatever ran before)
        conn.execute("DELETE FROM report_answers")
    report = volume.run(data, tmp_path / "volume", roles=("admin", "clerk"), repeat=1, big=100, only=HOT_PAGES)
    route_of = {r["address"]: r["route"] for r in report["results"]}
    found = {(s["table"], route_of.get(a, a), "table" if s["how"] == "table" else "index")
             for s in report["scans"] for a in s["routes"]}  # fmt: skip
    assert not found - set(HOT_SCANS), f"a hot page scans a big table: {sorted(found - set(HOT_SCANS))}"
    assert not set(HOT_SCANS) - found, f"no longer scanned, take it off HOT_SCANS: {sorted(set(HOT_SCANS) - found)}"
