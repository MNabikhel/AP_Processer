"""The invoices' summary (D-110): the lists, the search and the reports read it, and give what they gave when they
read every invoice (``tests/summary_reference.py``: the code as it was), on realistic data: the ten sample invoices
over a year, approved (some corrected, some with findings left open, some by an older version without their posting
lines), exported, waiting for a second approval, parked, rejected, failed, in review, a duplicate, a credit note, a
foreign currency, meals and a PST to self-assess."""

import datetime as dt
import json
import re
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
from ap_coder.core.store._base import SCHEMA_VERSION
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
            elif k >= 2:  # coded as AP recoded it in the second round: AP's latest decision (D-118)
                doc["line_items"] = [{**doc["line_items"][0], "predicted_gl_code": meals}, *doc["line_items"][1:]]
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


def test_the_spend_page_shows_what_it_showed(year, tmp_path):
    """D-156: the Spend page's figures summed by SQLite, not every cost line of the period made a Python object: the
    page as it was (``tests/reports_reference.py``), word for word, in each currency and all in CAD, over each period,
    with a rate that makes conversions land on half a cent, and with dates it can't read or backwards."""
    from tests import reports_reference as old
    from tests.webclient import client_for

    def shown(address: str) -> str:  # each chart's id numbered as the first of the page
        page = client.get(address).text.replace("/old-spend", "/spend")
        return re.sub(r"(apc-chart-[a-z0-9-]+?)-\d+-", r"\1-0-", page)

    client = client_for(year)
    client.app.add_api_route("/old-spend", old.spend_page)
    year.set_setting("fx_rates", "USD=1.25")  # a line of 0.75 USD is 0.9375 CAD
    usd = next(d for d in _docs() if d["currency"] == "USD")  # one more in USD, approved: the year's may wait
    invoice_id, output = _add(year, tmp_path, "usd", {**usd, "invoice_number": "USD-9", "invoice_date": "2026-02-10"})
    year.approve_invoice(invoice_id, output, "Jordan Lee")
    periods = [*PERIODS, (dt.date(2026, 9, 1), dt.date(2026, 1, 1)), (dt.date(2030, 1, 1), dt.date(2030, 2, 1))]
    for start, end in periods:
        for currency in ("", "All, in CAD", "CAD", "USD", "EUR"):
            query = f"?start={start}&end={end}&currency={currency}"
            assert shown(f"/spend{query}") == shown(f"/old-spend{query}"), query
    assert shown("/spend?start=nope") == shown("/old-spend?start=nope")
    assert "All currencies in CAD at the rates" in shown(f"/spend?start={YEAR[0]}&end={YEAR[1]}")


def test_activity_counts_the_approvals_as_insights_does(year):
    """D-156: Activity's tile counts the latest approvals in SQLite (``approval_counts``): the same as reading each one
    (``approved_as_coded``, ``filled_in_count``), an approval the summary could not read read again as before."""
    counts = (*insights.approved_as_coded(year), insights.filled_in_count(year))
    assert insights.approval_counts(year) == counts and counts[1]
    invoice_id = next(r["id"] for r in ref.old_list_invoices(year, APPROVED))
    with sqlite3.connect(year.path) as conn:  # an approval the summary could not read: its facts read again
        conn.execute("UPDATE invoice_summary SET broken = broken | ? WHERE id = ?",
                     (summary_module.BIT["approval"], invoice_id))  # fmt: skip
    assert insights.approval_counts(year) == (*insights.approved_as_coded(year), insights.filled_in_count(year))


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


MORE_QUERIES = [*QUERIES, "2026", "1", "0", "#0912", "n°0912", "inv 0912", "18017,85", "-18017.85", "(18017.85)",
                "northwind 18017.85", "solutions it", "pdf", "12.5", "x-1", "erp", "vendor 3", "straße",
                '"north"']  # fmt: skip


def _erp_register(store: Store) -> None:
    """An ERP register with more hits than the directory shows at first, three exported invoices' own rows (not shown
    again) and another vendor's bill with one of their numbers and amounts (shown)."""
    exported = [r for r in store.search_rows(exported=True)][:3]
    rows = [{"vendor_name": r["vendor_name"], "invoice_number": r["invoice_number"], "total": r["grand_total"],
             "invoice_date": r["invoice_date"]} for r in exported]  # fmt: skip
    rows.append({"vendor_name": "Other Vendor 3", "invoice_number": exported[0]["invoice_number"],
                 "total": exported[0]["grand_total"]})  # fmt: skip
    rows += [{"vendor_name": "Northwind IT Solutions", "invoice_number": f"NW-ERP-{n:04d}", "total": 10.5 * n,
              "invoice_date": f"2026-0{1 + n % 9}-1{n % 10}"} for n in range(1, 131)]  # fmt: skip
    rows += [{"vendor_name": "Old Vendor", "invoice_number": "X-1", "total": 12.5},
             {"vendor_name": "Straße Erp Supplies", "invoice_number": "N°0912", "total": -18017.85}]  # fmt: skip
    store.import_erp_register(rows)


def _old_run(store: Store, query, page_size: int, everything: bool = False):
    """``directory.run`` as it was: every quick-search hit found first (``ref.search_find``: what ``search.find``
    gave, ``test_the_quick_search_finds_what_it_found``), their ids handed to SQLite as one JSON list."""
    hits = ref.search_find(store, query.q, directory.EXPORT_LIMIT)
    ids = [h.row["id"] for h in hits if h.source != "ERP"]
    sql_where, params, problems = directory.where(directory.Query(**{**query.__dict__, "q": ""}))
    sql_where = f"({sql_where}) AND " + ("id IN (SELECT value FROM json_each(?))" if ids else "0")
    params += [json.dumps(ids)] if ids else []
    total = store.directory_count(directory.ROWS_SQL, sql_where, params)
    if everything:
        limit, offset, page, pages = directory.EXPORT_LIMIT, 0, 1, 1
    else:
        pages = max(1, -(-total // page_size))
        page = min(query.page, pages)
        limit, offset = page_size, (page - 1) * page_size
    rows = store.directory_rows(directory.ROWS_SQL, directory.ROW_FIELDS, sql_where, params,
                                directory.order_by(query), limit, offset)  # fmt: skip
    return rows, total, page, pages, problems, [h for h in hits if h.source == "ERP"]


@pytest.mark.parametrize("fts", [True, False])
def test_the_directory_pages_the_quick_search_as_it_did(year, monkeypatch, fts):
    """The quick search as one more condition of the directory's SQL (D-157): the invoices it finds are those
    ``search._score`` finds, every one and no other, and the directory's count, pages, sort and export are what they
    were when every hit was found first in Python. Only the ERP register's hits are shown ERP_SHOWN at a time ("Show
    more"), best first as before."""
    _erp_register(year)
    if not fts:
        monkeypatch.setitem(summary_module._FTS, "ok", False)
    monkeypatch.setattr(directory, "EXPORT_CHUNK", 7)  # an export read a few rows at a time
    every = year.search_rows()
    for q in MORE_QUERIES:
        words, numbers = search.parts(q)
        sql, params = search.condition(words, numbers, fts)
        assert [r["id"] for r in year.search_rows(sql, params)] == [
            r["id"] for r in every if search._score(r, words, numbers)], q  # fmt: skip
        for n, extra in enumerate(({"sort": "vendor", "f_total": ">100"}, {"sort": "total", "dir": "asc", "page": "2"},
                                   {"f_status": "approved", "page": "3", "erp": "100"})):  # fmt: skip
            query = directory.Query.from_params({**extra, "q": q})
            new = directory.run(year, query, page_size=4)
            *old, old_erp = _old_run(year, query, 4)
            assert [new.rows, new.total, new.page, new.pages, new.problems] == old, (q, extra)
            assert [(h.score, h.row) for h in new.erp] == [(h.score, h.row) for h in old_erp][: query.erp_shown]
            assert new.erp_total == len(old_erp), (q, extra)
            if n == 0:
                everything = _old_run(year, query, 4, everything=True)[0]
                assert list(directory.export_rows(year, query)) == everything
                assert directory.run(year, query, everything=True).rows == everything
    assert directory.run(year, directory.Query(q="northwind")).erp_total > directory.ERP_SHOWN  # (shown in parts)


def test_the_erp_registers_search_keys_are_made_for_an_older_database(year):
    """A register imported before schema 22 gets its vendor names and numbers as the quick search compares them."""
    _erp_register(year)
    before = search.find(year, "northwind", 500)
    with sqlite3.connect(year.path) as conn:
        conn.execute("ALTER TABLE erp_invoices DROP COLUMN plain_name")
        conn.execute("ALTER TABLE erp_invoices DROP COLUMN raw_number")
        conn.execute("UPDATE settings SET value = '20' WHERE key = 'schema_version'")
    store = Store(year.path)
    assert [(h.score, h.row) for h in search.find(store, "northwind", 500)] == [(h.score, h.row) for h in before]
    assert any(h.source == "ERP" for h in before)


_REGISTER_NAMES = ["Granite Logistics", "Granite Supply Co", "Café Livraison Ltée", "3M Canada", "Straße Erp Supplies",
                   "O'Brien & Sons", "Aurora Electric 2", "Northwind IT Solutions", "LP Fuels"]  # fmt: skip


def _register_in_step(store: Store) -> bool:
    with sqlite3.connect(store.path) as conn:
        found = conn.execute(
            "SELECT (SELECT value FROM settings WHERE key = 'register_search_step') = COALESCE("
            "(SELECT value FROM settings WHERE key = 'changes/register'), '0')"
        ).fetchone()
    return bool(found[0])


def _register_change(rng, store: Store, monkeypatch, seen, kind: str = "") -> bool | None:
    """One random write to ``store``'s ERP register; whether it keeps the search's index as it was (in step when it
    was), False: it leaves it behind, None: it makes it again (a start)."""
    from ap_coder.core.memory import vendor_key

    def bill() -> dict:
        number = rng.choice(["H00{:05d}", "INV-{:04d}", "{:07d}", "NW-ERP-{:04d}", "N°{:04d}", "CN-2026-{:04d}"])
        return {"vendor_name": rng.choice(_REGISTER_NAMES), "invoice_number": number.format(rng.randrange(1, 3000)),
                "total": rng.choice([1, -1]) * round(rng.uniform(1, 20000), 2),
                "invoice_date": "2026-10-01"}  # fmt: skip

    kind = kind or rng.choice(["file", "file", "vouchers", "vouchers", "gone", "clear", "sql", "huge", "older",
                               "no fts", "start"])  # fmt: skip
    if kind == "file":
        store.import_erp_register([bill() for _ in range(rng.randrange(1, 40))], replace_all=rng.random() < 0.2)
    elif kind == "vouchers":  # new, seen again, or moved to another bill (its supplier renamed, its gross corrected)
        store.import_jde_vouchers([{**bill(), "key": f"1-PV-{rng.randrange(60)}", "an8": "4001"}
                                   for _ in range(rng.randrange(1, 30))], seen=next(seen))  # fmt: skip
    elif kind == "gone":  # vouchers JD Edwards no longer lists, and their bills with their last
        with sqlite3.connect(store.path) as conn:
            keys = [r[0] for r in conn.execute("SELECT erp_key FROM erp_vouchers")]
        store.remove_jde_vouchers(rng.sample(keys, min(len(keys), rng.randrange(1, 10))), next(seen))
    elif kind == "clear":
        store.clear_erp_register()
    elif kind in ("sql", "huge", "older"):  # an older AP Coder's write, or plain SQL: the index left as it was
        with sqlite3.connect(store.path) as conn:
            row = bill()
            plain, raw = search.register_keys(row["vendor_name"], row["invoice_number"])
            insert = ("INSERT OR IGNORE INTO erp_invoices (rowid, vendor_key, number_key, total, vendor_name, "
                      "invoice_number, imported_at, plain_name, raw_number) VALUES (?, ?, ?, ?, ?, ?, '2026', ?, "
                      "?)")  # fmt: skip
            values = (vendor_key(row["vendor_name"]), search.norm_invoice_number(row["invoice_number"]), row["total"],
                      row["vendor_name"], row["invoice_number"], plain, raw)  # fmt: skip
            if kind == "huge":  # (SQLite's largest rowid taken: a row added later gets one at random)
                conn.execute(insert, (2**63 - 1, *values))
            elif kind == "older":  # (a database, or a backup restored, of an AP Coder before the index)
                conn.execute("DROP TABLE IF EXISTS register_search")
                conn.execute("DELETE FROM settings WHERE key = 'register_search_step'")
            else:
                conn.execute(insert, (None, *values))
                conn.execute("DELETE FROM erp_invoices WHERE rowid IN (SELECT rowid FROM erp_invoices ORDER BY "
                             "random() LIMIT 1)")  # fmt: skip
                conn.execute("UPDATE erp_invoices SET plain_name = 'granite ' || plain_name WHERE rowid IN (SELECT "
                             "rowid FROM erp_invoices ORDER BY random() LIMIT 1)")  # fmt: skip
            wrote = kind == "older" or conn.total_changes > 0  # (none: a row there already, a register empty)
        return not wrote
    elif kind == "no fts":  # a computer whose SQLite has no FTS5 writes the register (and reads it itself)
        with monkeypatch.context() as patched:
            patched.setitem(summary_module._FTS, "ok", False)
            store.import_erp_register([bill() for _ in range(3)])
            assert [h.row for h in search.find(store, "granite", 500)] == [
                h.row for h in ref.search_find(store, "granite", 500)]  # fmt: skip
        return False
    else:
        Store(store.path)
        return None
    return True


@pytest.mark.parametrize("seed", [1, 2, 3, 4])
def test_the_quick_searchs_register_hits_are_scoring_every_row_whatever_writes_the_register(year, monkeypatch, seed):
    """D-194: the quick search reads the ERP register's rows holding a word through the register's FTS5 index
    (``register_search``), kept by the register's own writes, not each row of the register (600,000 at 300,000
    invoices). Random writes (a file imported, added to the register or in its place; JD Edwards' vouchers new, seen
    again, moved to another bill, gone with their bill; the register cleared; an older AP Coder's rows or plain SQL,
    a computer whose SQLite has no FTS5, SQLite's largest rowid taken, a database of an AP Coder before the index:
    each leaves it behind; a start, which makes it again), and after each the hits of many searches are those scoring
    every row of the register and the summary gives (``ref.search_find``), in the same order, and the directory's ERP
    hits too; the index is in step whenever the writes since a start kept it."""
    import itertools
    import random

    rng, seen = random.Random(seed), itertools.count(1)
    _erp_register(year)
    queries = [*MORE_QUERIES, "granite", "gran", "granite supply", "supply co", "livraison", "cafe", "o'brien",
               "brien sons", "aurora 2", "electric", "lp", "fuels", "h0001", "h00012", "inv-0042", "0042", "cn-2026",
               "2026", "-1", "nw-erp-0012", "erp 0012", "3m canada", "zzz"]  # fmt: skip
    in_step, used = _register_in_step(year), 0
    assert in_step
    for step in range(25):
        with sqlite3.connect(year.path) as conn:
            top = conn.execute("SELECT MAX(rowid) FROM erp_invoices").fetchone()[0]
        kept = _register_change(rng, year, monkeypatch, seen, "file" if step == 0 else "start" if not in_step and
                                rng.random() < 0.5 else "")  # fmt: skip
        now = _register_in_step(year)
        if kept is None:  # (a start: made again)
            assert now
        elif kept is False:
            assert not now
        elif top != 2**63 - 1:  # (else a row it added may be below the others: left behind, if it added one)
            assert now == in_step
        in_step = now
        used += in_step
        for q in [*queries, *rng.sample([r["invoice_number"] for r in year.erp_register()] or ["x"], 1)]:
            new = [(h.score, h.source, h.row.get("id"), h.row) for h in search.find(year, q, 500)]
            old = [(h.score, h.source, h.row.get("id"), h.row) for h in ref.search_find(year, q, 500)]
            assert new == old, q
        query = directory.Query.from_params({"q": "granite", "erp": "100"})
        old_erp = [(h.score, h.row) for h in ref.search_find(year, "granite", 500) if h.source == "ERP"]
        assert [(h.score, h.row) for h in directory.run(year, query).erp] == old_erp[: query.erp_shown]
    assert used  # (the index read, not only the register)


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
    """``summary_memo``: an approval changes no batch's totals; an export does. (The vendors' invoice dates are read
    for the recurring vendors' one answer and not kept, B-1803: its own test is in tests/core/test_kept_answers.py.)"""
    totals = year.batch_totals()
    # One under the approval limit (15,000), so it is approved and can be exported (the queue's first one is not
    # always: its order depends on the confidences).
    invoice = min(
        (i for i in year.list_invoices(REVIEW) if float(i["grand_total"] or 0) < 15000), key=lambda i: i["id"]
    )
    year.approve_invoice(invoice["id"], year.get_invoice(invoice["id"])["ai_output"], "Jordan Lee")
    assert year.get_invoice(invoice["id"])["status"] == "approved"
    assert year.batch_totals() is totals
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
    # brought up to this version's schema, whatever its number
    assert store.summary_query("SELECT value FROM settings WHERE key = 'schema_version'")[0][0] == str(SCHEMA_VERSION)


def test_a_backup_with_the_summary_restores_and_one_with_another_trigger_is_refused(year, tmp_path):
    copy = year.backup_to(tmp_path / "copy.db")
    Store.check_backup(copy)  # the summary's own triggers
    with sqlite3.connect(copy) as conn:
        conn.execute("CREATE TRIGGER planted AFTER INSERT ON invoices BEGIN DELETE FROM feedback; END")
    with pytest.raises(ValueError, match="trigger"):
        Store.check_backup(copy)


def test_a_summary_trigger_another_version_made_is_made_again_at_the_start_as_this_one_makes_it(year):
    """A trigger was made only when its name was missing: one another version made with other SQL (a column it
    did not count) stayed so, its change uncounted, and every backup of the database refused (D-159). At the start
    it is dropped and made again as this version makes it."""
    older = ("CREATE TRIGGER changes_vendor_invoice_changed AFTER UPDATE OF status, vendor_key ON invoices BEGIN "
             "SELECT 1; END")  # fmt: skip
    with sqlite3.connect(year.path) as conn:
        conn.execute("DROP TRIGGER changes_vendor_invoice_changed")
        conn.execute(older)
    store = Store(year.path)
    triggers = "SELECT name, sql FROM sqlite_master WHERE type = 'trigger'"
    assert dict(store.summary_query(triggers)) == summary_module.SUMMARY_TRIGGERS
    invoice = store.get_invoice(store.list_invoices(APPROVED)[0]["id"])
    count = f"SELECT value FROM settings WHERE key = 'changes/vendor/{invoice['vendor_key']}'"
    before = int(store.summary_query(count)[0][0])
    with sqlite3.connect(store.path) as conn:  # a coding change: a column the older trigger did not count
        conn.execute("UPDATE invoices SET final_output = ai_output WHERE id = ?", (invoice["id"],))
    assert int(store.summary_query(count)[0][0]) == before + 1
    Store.check_backup(store.backup_now("manual"))


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
