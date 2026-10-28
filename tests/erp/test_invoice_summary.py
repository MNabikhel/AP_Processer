"""The invoices' summary (D-110): the lists, the search and the reports read it, and give what they gave when they
read every invoice (``tests/summary_reference.py``: the code as it was), on realistic data: the ten sample invoices
over a year, approved (some corrected, some with findings left open, some by an older version without their posting
lines), exported, waiting for a second approval, parked, rejected, failed, in review, a duplicate, a credit note, a
foreign currency, meals and a PST to self-assess."""

import datetime as dt
import json
import sqlite3
from dataclasses import asdict

import pytest

from ap_coder.ai.pipeline import finalise_coding
from ap_coder.coding import taxreturn
from ap_coder.coding.tax import EXPENSE_SEPARATE, EXPENSE_TO_LINE
from ap_coder.core.config import Settings
from ap_coder.core.schema import InvoiceCoding
from ap_coder.core.store import APPROVED, FAILED, PARKED, PENDING, REJECTED, REVIEW, Store, load_sample_setup
from ap_coder.core.store import summary as summary_module
from ap_coder.core.store.samples import load_sample_purchase_orders
from ap_coder.erp import accruals, directory, insights, search, spend
from tests import summary_reference as ref
from tests.conftest import SAMPLES, copy_database, isolated

YEAR = (dt.date(2025, 10, 1), dt.date(2026, 12, 31))
PERIODS = [YEAR, (dt.date(2026, 1, 1), dt.date(2026, 3, 31)), (dt.date(2026, 7, 1), dt.date(2026, 9, 30))]


def _docs() -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted((SAMPLES / "ground_truth").glob("*.json"))]


def _add(store: Store, tmp_path, name: str, doc: dict, meta: dict | None = None) -> tuple[int, dict]:
    output, report = finalise_coding(InvoiceCoding.model_validate(doc), store.reference_data(), Settings(), store=store)
    meta = meta or {"timings_seconds": {"read": 1.5, "code": 0.25}, "extraction": {"page_count": 2},
                    "inference": {"usage": {"prompt_tokens": 900, "cached_prompt_tokens": 100,
                                            "completion_tokens": 50}}}  # fmt: skip
    return store.add_invoice(tmp_path / f"{name}.pdf", output, report.to_dict(), meta=meta), output


@pytest.fixture(scope="module")
def year_template(tmp_path_factory):
    """A year of the sample invoices, in every state an invoice can be in: 40 invoices coded, approved, exported...
    made once for the module (5 s), copied for each test (``year``)."""
    folder = tmp_path_factory.mktemp("year")
    with isolated(folder):
        store = Store(folder / "ap.db")
        _fill_year(store, folder)
    return store.path


@pytest.fixture
def year(tmp_path, year_template):
    """A copy of the year for this test (it may change it)."""
    return copy_database(year_template, tmp_path / "ap.db")


def _fill_year(store: Store, tmp_path) -> None:
    load_sample_setup(store)
    load_sample_purchase_orders(store)
    store.set_setting("fx_rates", "USD=1.37")
    store.set_setting("approval_limit", "15000")
    meals = "6320"  # the sample's meals and entertainment: half the tax claimable (D-90)
    exported = []
    for k in range(4):
        for n, doc in enumerate(_docs()):
            day = dt.date(2025, 10, 5) + dt.timedelta(days=29 * (3 * k + n % 3) + n)
            doc = {**doc, "invoice_date": day.isoformat(), "invoice_number": f"{doc['invoice_number']}-{k}",
                   "due_date": "", "payment_terms": "2% 10, net 30" if n % 2 else doc.get("payment_terms")}  # fmt: skip
            if k == 3 and n == 1:  # a GL account picked by the reviewer that is the meals one
                doc["line_items"] = [{**li, "predicted_gl_code": meals} for li in doc["line_items"]]
            invoice_id, output = _add(store, tmp_path, f"{k}-{n}", doc)
            fate = (k, n % 4)
            if k == 0 or fate == (3, 1):
                store.approve_invoice(invoice_id, output, "Jordan Lee")
                exported.append(invoice_id)
            elif k == 1:
                edited = {**output, "line_items": [{**output["line_items"][0], "predicted_gl_code": meals},
                                                   *output["line_items"][1:]]}  # fmt: skip
                store.approve_invoice(invoice_id, edited, "Sam Roy", open_issues=[
                    {"code": "TAX_RATE_NONSTANDARD", "severity": "warning"}, {"code": "VENDOR_GST_MISSING"}],
                    login="sroy")  # fmt: skip
                if (store.get_invoice(invoice_id) or {})["status"] == PENDING and n % 2:
                    store.final_approve(
                        invoice_id, "Alex Kim", login="akim", shown=store.latest_approval_id(invoice_id)
                    )
            elif fate == (2, 0):
                store.park_invoice(invoice_id, "Jordan Lee", "waiting for the buyer", "2026-11-02")
            elif fate == (2, 1):
                store.reject_invoice(invoice_id, "Jordan Lee", "not ours")
            elif fate == (3, 2):
                store.approve_invoice(invoice_id, output, "Jordan Lee", bulk=True)
    store.create_export_batch(exported[:12], "csv", actor="Jordan Lee")
    # An older version's approval: no posting lines stored, built again with the tax setup.
    old = {**_docs()[3], "invoice_number": "OLD-1", "invoice_date": "2026-05-04"}
    store.approve_invoice(store.add_invoice(tmp_path / "old.pdf", old, {}), old, "Jordan Lee")
    # A copy of an exported bill, in review: flagged as a duplicate.
    first = store.get_invoice(exported[0])["final_output"]
    _add(store, tmp_path, "copy", {k: v for k, v in first.items() if k != "gl_distribution"})
    stopped, _ = _add(store, tmp_path, "copy2", {k: v for k, v in first.items() if k != "gl_distribution"})
    store.reject_invoice(stopped, "Jordan Lee", "a duplicate")  # a duplicate stopped (Insights)
    # A BC supply whose vendor charged no PST: to self-assess.
    pacific = next(d for d in _docs() if d["vendor_name"].startswith("Pacific"))
    no_pst = {**pacific, "invoice_number": "SA-1", "invoice_date": "2026-08-14",
              "tax_lines": [t for t in pacific["tax_lines"] if t["tax_type"] != "PST"]}  # fmt: skip
    no_pst["grand_total"] = round(no_pst["subtotal"] + sum(t["tax_amount"] for t in no_pst["tax_lines"]), 2)
    sa_id, sa_output = _add(store, tmp_path, "sa", no_pst)
    store.approve_invoice(sa_id, sa_output, "Jordan Lee")
    store.add_invoice(tmp_path / "bad.pdf", None, None, error="could not be read")
    store.add_invoice(tmp_path / "demo.pdf", first, {}, meta={"demo": True})


def _pairs(rows):
    return [asdict(r) for r in rows]


def test_the_year_has_every_state(year):
    counts = year.status_counts()
    assert {APPROVED, PENDING, PARKED, REJECTED, REVIEW, FAILED} <= set(counts)
    assert year.summary_query("SELECT COUNT(*) FROM invoice_summary WHERE legacy = 1")[0][0] == 1
    assert year.summary_query("SELECT COUNT(*) FROM summary_dirty")[0][0] == 0  # in step after each change
    assert year.summary_query("SELECT COUNT(*) FROM invoice_summary")[0][0] == len(ref.old_list_invoices(year))


def test_the_lists_are_the_invoices_own(year):
    for status in (None, REVIEW, APPROVED, PENDING, PARKED, REJECTED, FAILED):
        assert year.list_invoices(status) == ref.old_list_invoices(year, status)
    assert year.search_rows() == ref.old_search_rows(year)
    exported = [r for r in year.search_rows() if r["export_batch"]]
    assert year.search_rows(exported=True) == exported and exported  # (the JD Edwards duplicate check's)
    assert year.unexported_approved() == ref._old_unexported(year)
    assert year.demo_count() == 1
    assert year.approved_since("2000-01-01", "Jordan Lee") > 0
    with year._conn() as conn:
        old = conn.execute("SELECT status, COUNT(*) FROM invoices GROUP BY status").fetchall()
        batch = conn.execute("SELECT export_batch, SUM(grand_total) FROM invoices WHERE export_batch IS NOT NULL "
                             "GROUP BY export_batch").fetchall()  # fmt: skip
    assert year.metrics()["invoices_by_status"] == {r[0]: r[1] for r in old}
    assert {b: sum(t.values()) for b, t in year.batch_totals().items()} == {r[0]: round(r[1], 2) for r in batch}


def test_spend_gives_what_it_gave(year):
    rates = year.fx_rates()
    for period in PERIODS:
        old, new = ref.spend_lines(year, *period), spend.parts(year, *period)
        assert spend.lines(year, *period) == old  # the per-line report, unchanged
        assert spend.currencies(new) == spend.currencies(old)
        assert {r.invoice_id for r in new} == {r.invoice_id for r in old}
        assert {r.month for r in new} == {r.month for r in old}
        categories = {a["code"]: a["category"] for a in year.list_accounts("gl_accounts")}
        for currency, a, b in [("CAD", old, new), ("USD", old, new),
                               ("CAD", spend.in_cad(old, rates), spend.in_cad(new, rates))]:  # fmt: skip
            for key in ("vendor", "gl_code", "cost_center"):
                assert spend.total_by(b, key, currency) == spend.total_by(a, key, currency)
            assert spend.by_month_and_category(b, currency, categories) == spend.by_month_and_category(
                a, currency, categories
            )
            assert round(sum(r.amount for r in b if r.currency == currency), 2) == round(
                sum(r.amount for r in a if r.currency == currency), 2
            )


def test_spend_follows_a_change_of_the_tax_setup(year):
    """An invoice approved by an older version has its posting lines built with the tax setup: changed, it is
    worked out again (the trigger on ``tax_treatments``)."""
    before = spend.total_by(spend.parts(year, *YEAR), "gl_code", "CAD")
    year.set_tax_treatment("QST", EXPENSE_TO_LINE, "")  # the older version's invoice is from Quebec
    year.set_tax_treatment("GST", EXPENSE_SEPARATE, "5900")
    after = spend.total_by(spend.parts(year, *YEAR), "gl_code", "CAD")
    assert after != before and after == spend.total_by(ref.spend_lines(year, *YEAR), "gl_code", "CAD")


def test_the_sales_tax_report_gives_what_it_gave(year):
    for period in PERIODS:
        old, new = ref.tax_build(year, *period), taxreturn.build(year, *period)
        assert _pairs(new.claims) == _pairs(old.claims) and new.claims
        assert (new.not_approved, new.not_recoverable) == (old.not_approved, old.not_recoverable)
        assert taxreturn.self_assessment(year, *period) == ref.tax_self_assessment(year, *period)
    assert any(c.meals for c in old.claims) or any(c.meals for c in ref.tax_build(year, *YEAR).claims)
    assert ref.tax_self_assessment(year, *YEAR)  # the BC supply without PST
    treatments = {**year.tax_treatments()}
    treatments["QST"] = type(treatments["QST"])("QST", EXPENSE_TO_LINE, "")
    assert _pairs(taxreturn.build(year, *YEAR, treatments).claims) == _pairs(
        ref.tax_build(year, *YEAR, treatments).claims
    )


def test_the_queue_offers_the_same_invoices_for_bulk_approval(year):
    from ap_coder.coding.bulk import sort_for_bulk

    assert sort_for_bulk(year) == ref.bulk_sort_for_bulk(year) and sort_for_bulk(year)[1]


def test_month_end_gives_what_it_gave(year):
    for end in (dt.date(2026, 3, 31), dt.date(2026, 9, 30), dt.date(2027, 1, 31)):
        old_undated, new_undated = [], []
        assert accruals._not_in_erp(year, end, new_undated) == ref._not_in_erp(year, end, old_undated)
        assert new_undated == old_undated
        undated: list = []
        assert accruals.build(year, end, undated) == accruals.build(year, end)
        assert undated == new_undated


def test_insights_and_vendors_give_what_they_gave(year):
    new, old = insights.compute(year), ref.insights_compute(year)
    assert new == old and new["approved"] and new["duplicates_stopped"] == 1 and new["top_issues"]
    today = dt.date(2026, 10, 20)
    assert insights.operations(year, today) == ref.insights_operations(year, today)
    assert insights.approved_as_coded(year) == ref.approved_as_coded(year)
    assert insights.filled_in_count(year) == ref.filled_in_count(year)
    for min_invoices in (1, 2):
        a, b = insights.vendor_workload(year, min_invoices), ref.insights_vendor_workload(year, min_invoices)
        # the codes of a vendor's tie were in no order before (a set's): the same codes
        assert [{**v, "top_codes": sorted(v["top_codes"])} for v in a] == [
            {**v, "top_codes": sorted(v["top_codes"])} for v in b
        ]


QUERIES = ["northwind", "nw-2026", "0912", "18017.85", "18 017,85", "pacific 77120", "po-77120", "3m", "zz", "inv",
           "laurentides sil", "ch", "-1", "Énergir", "harbourview 2026"]  # fmt: skip


@pytest.mark.parametrize("fts", [True, False])
def test_the_quick_search_finds_what_it_found(year, monkeypatch, fts):
    year.import_erp_register([{"vendor_name": "Northwind IT Solutions", "invoice_number": "NW-2026-0912-0",
                               "invoice_date": "2026-01-02", "total": 18017.85},
                              {"vendor_name": "Old Vendor", "invoice_number": "X-1", "total": 12.5}])  # fmt: skip
    if not fts:
        monkeypatch.setitem(summary_module._FTS, "ok", False)
    for q in QUERIES:
        new = [(h.score, h.source, h.row.get("id"), h.row.get("invoice_number")) for h in search.find(year, q, 500)]
        old = [(h.score, h.source, h.row.get("id"), h.row.get("invoice_number")) for h in ref.search_find(year, q, 500)]
        assert new == old, q


def test_the_directory_filters_and_sorts_as_it_did(year):
    old_rows = ref.OLD_ROWS_SQL
    for params in ({}, {"f_gl": "6*"}, {"f_gl": "!6*"}, {"f_po": "*"}, {"f_tax": ">100"}, {"sort": "gl", "dir": "asc"},
                   {"sort": "subtotal"}, {"f_vendor": "north*", "sort": "po"}):  # fmt: skip
        query = directory.Query.from_params(params)
        where, values, _ = directory.where(query)
        old_where = where.replace("json_each(line_gls) AS l", "json_each(doc, '$.line_items') AS l").replace(
            "CAST(l.value AS TEXT)", "CAST(json_extract(l.value, '$.predicted_gl_code') AS TEXT)"
        )
        old_order = directory.order_by(query).replace("first_gl", ref.OLD_FIRST_GL)
        new = year.directory_rows(directory.ROWS_SQL, directory.ROW_FIELDS, where, values,
                                  directory.order_by(query), 1000, 0)  # fmt: skip
        old = year.directory_rows(old_rows, directory.ROW_FIELDS, old_where, values, old_order, 1000, 0)
        assert new == old and year.directory_count(directory.ROWS_SQL, where, values) == len(old), params


def test_a_change_made_without_the_store_is_read_in_step(year):
    """The triggers mark it; the summary is worked out again before it is read."""
    invoice_id = year.list_invoices(REVIEW)[0]["id"]
    with sqlite3.connect(year.path) as conn:
        conn.execute("UPDATE invoices SET status = ?, vendor_name = 'Changed Ltd' WHERE id = ?", (PARKED, invoice_id))
    assert next(i for i in year.list_invoices(PARKED) if i["id"] == invoice_id)["vendor_name"] == "Changed Ltd"
    with sqlite3.connect(year.path) as conn:
        conn.execute("DELETE FROM invoices WHERE id = ?", (invoice_id,))
    assert invoice_id not in {i["id"] for i in year.list_invoices()}


def test_what_several_pages_read_is_read_again_only_once_it_changed(year):
    """``summary_memo``: an approval changes no vendor's invoice dates (the queue's late regular bills keep theirs);
    a date changed, by the store or plain SQL, is read again; an export changes the batches' totals."""
    dates, totals = year.vendor_invoice_dates(), year.batch_totals()
    # One under the approval limit (15,000), so it is approved and can be exported (the queue's first one is not
    # always: its order depends on the confidences).
    invoice = min(
        (i for i in year.list_invoices(REVIEW) if float(i["grand_total"] or 0) < 15000), key=lambda i: i["id"]
    )
    year.approve_invoice(invoice["id"], year.get_invoice(invoice["id"])["ai_output"], "Jordan Lee")
    assert year.get_invoice(invoice["id"])["status"] == "approved"
    assert year.vendor_invoice_dates() is dates and year.batch_totals() is totals
    with sqlite3.connect(year.path) as conn:
        conn.execute("UPDATE invoices SET invoice_date = '2026-02-02' WHERE id = ?", (invoice["id"],))
    assert year.vendor_invoice_dates() is not dates
    assert year.vendor_invoice_dates() == year._vendor_invoice_dates()
    year.create_export_batch([invoice["id"]], "csv", actor="Jordan Lee")
    assert year.batch_totals() is not totals and year.batch_totals() == year._batch_totals()
    assert year.exported_totals() == year._exported_totals()


def test_an_older_database_gets_its_summary(year):
    """Schema 18 had none: the migration builds it, and every page reads the same."""
    before = (insights.compute(year), _pairs(taxreturn.build(year, *YEAR).claims), year.search_rows())
    with sqlite3.connect(year.path) as conn:
        for name in summary_module.SUMMARY_TRIGGERS:
            conn.execute(f"DROP TRIGGER {name}")
        tables = ("invoice_summary", *summary_module.SIDE_TABLES, "summary_spend", "summary_dirty", "invoice_search")
        for table in (*tables, "feedback_counts"):
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.execute("DELETE FROM settings WHERE key = 'summary_rules'")
        conn.execute("UPDATE settings SET value = '18' WHERE key = 'schema_version'")
    store = Store(year.path)
    assert (insights.compute(store), _pairs(taxreturn.build(store, *YEAR).claims), store.search_rows()) == before
    assert store.summary_query("SELECT value FROM settings WHERE key = 'schema_version'")[0][0] == "19"


def test_a_backup_with_the_summary_restores_and_one_with_another_trigger_is_refused(year, tmp_path):
    copy = year.backup_to(tmp_path / "copy.db")
    Store.check_backup(copy)  # the summary's own triggers
    with sqlite3.connect(copy) as conn:
        conn.execute("CREATE TRIGGER planted AFTER INSERT ON invoices BEGIN DELETE FROM feedback; END")
    with pytest.raises(ValueError, match="trigger"):
        Store.check_backup(copy)


def test_a_capture_is_kept_compressed_and_read_either_way(year):
    invoice_id = year.list_invoices(REVIEW)[0]["id"]
    capture = {"fields": {"vendor_name": {"value": "X", "status": "check"}}, "credit_note": True, "text": "a" * 5000}
    year.save_capture(invoice_id, capture)
    with sqlite3.connect(year.path) as conn:
        stored = conn.execute("SELECT capture_json FROM invoice_capture WHERE invoice_id = ?", (invoice_id,)).fetchone()
    assert isinstance(stored[0], bytes) and len(stored[0]) < 500
    assert year.get_capture(invoice_id) == capture
    assert year.vendor_name_checks([invoice_id]) == {invoice_id: "check"}
    assert invoice_id in year.credit_note_ids(REVIEW)
    with sqlite3.connect(year.path) as conn:  # as an older version kept it
        conn.execute("UPDATE invoice_capture SET capture_json = ?, name_status = NULL, credit_note = NULL "
                     "WHERE invoice_id = ?", (json.dumps({**capture, "credit_note": False}), invoice_id))  # fmt: skip
    assert year.vendor_name_checks([invoice_id]) == {invoice_id: "check"}
    assert invoice_id not in year.credit_note_ids(REVIEW)


def test_a_damaged_part_leaves_the_other_reports_alone(year):
    """A tax line that can't be read (a hand-edited database): the sales-tax report fails as it did when it read every
    invoice; Insights, Vendors and Spend, which never read tax lines, still draw, and the same."""
    invoice_id = next(r["id"] for r in ref.old_list_invoices(year, APPROVED))
    with sqlite3.connect(year.path) as conn:
        conn.execute("UPDATE invoices SET final_output = json_set(final_output, '$.tax_lines', json('[\"x\"]')) "
                     "WHERE id = ?", (invoice_id,))  # fmt: skip
    assert year.summary_query("SELECT broken FROM invoice_summary WHERE id = ?", (invoice_id,))[0][0]
    with pytest.raises(AttributeError):
        taxreturn.build(year, *YEAR)
    with pytest.raises(AttributeError):
        ref.tax_build(year, *YEAR)
    assert insights.compute(year) == ref.insights_compute(year)
    assert insights.vendor_workload(year) and spend.parts(year, *YEAR)


def test_insights_counts_every_approval_past_a_hundred_thousand(tmp_path):
    """B-1251: Insights and Learning read only the newest 100,000 approval events: with more (a year at 10,000
    invoices a month), an invoice approved before them was no longer counted."""
    store = Store(tmp_path / "ap.db")
    load_sample_setup(store)
    doc = _docs()[0]
    first = store.add_invoice(tmp_path / "a.pdf", doc, {})
    store.approve_invoice(first, doc, "Jordan Lee")
    second = store.add_invoice(tmp_path / "b.pdf", {**doc, "invoice_number": "2"}, {})
    store.approve_invoice(second, {**doc, "invoice_number": "2"}, "Jordan Lee")
    with sqlite3.connect(store.path) as conn:  # the second one approved again and again since
        conn.executemany("INSERT INTO events (invoice_id, action, actor, detail, created_at) VALUES (?, ?, ?, ?, ?)",
                         [(second, "approved", "x", "{}", "2026-10-26T10:00:00")] * 100_000)  # fmt: skip
    assert insights.approved_as_coded(store)[1] == 2
