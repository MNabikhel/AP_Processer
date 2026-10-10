"""AP operations KPIs: queue ageing, days to approve, late approvals, discounts in time."""

import datetime as dt
import json
import sqlite3

from ap_coder.insights import operations
from ap_coder.store import Store

from .conftest import SAMPLE_STEM, SAMPLES


def _gt(**changes):
    return {**json.loads((SAMPLES / "ground_truth" / f"{SAMPLE_STEM}.json").read_text()), **changes}


def test_operations(tmp_path):
    path = tmp_path / "a.db"
    store = Store(path)
    a = store.add_invoice(tmp_path / "a.pdf", _gt(invoice_number="A"), {})
    b = store.add_invoice(tmp_path / "b.pdf", _gt(invoice_number="B"), {})
    c = store.add_invoice(tmp_path / "c.pdf", _gt(invoice_number="C", payment_terms="2/10 Net 30", due_date=""), {})
    d = store.add_invoice(tmp_path / "d.pdf", _gt(invoice_number="D", payment_terms="2/10 Net 30", due_date=""), {})
    store.approve_invoice(c, _gt(invoice_number="C", payment_terms="2/10 Net 30", due_date=""), "Jane")
    store.approve_invoice(d, _gt(invoice_number="D", payment_terms="2/10 Net 30", due_date=""), "Jane")
    store.park_invoice(b, "Jane", "waiting")
    with sqlite3.connect(path) as conn:  # received on different days, approved on known days
        conn.execute("UPDATE invoices SET created_at = '2026-09-20T09:00:00' WHERE id = ?", (a,))
        conn.execute("UPDATE invoices SET created_at = '2026-10-05T09:00:00' WHERE id = ?", (b,))
        conn.execute("UPDATE invoices SET created_at = '2026-09-15T09:00:00', reviewed_at = '2026-09-18T10:00:00' "
                     "WHERE id = ?", (c,))  # fmt: skip
        conn.execute("UPDATE invoices SET created_at = '2026-09-15T09:00:00', reviewed_at = '2026-10-20T10:00:00', "
                     "due_date = '2026-10-14' WHERE id = ?", (d,))  # fmt: skip
    ops = operations(store, today=dt.date(2026, 10, 7))
    assert ops["waiting"] == 2 and ops["oldest_days"] == 17
    assert ops["ageing"] == {"0–2 days": 1, "3–7 days": 0, "8–14 days": 0, "15+ days": 1}
    assert ops["median_days_to_approve"] == 19  # 3 and 35 days
    assert ops["approved_after_due"] == 1
    assert ops["discounts_in_time"] == (1, {"CAD": 318.9}) and ops["discounts_missed"] == (1, {"CAD": 318.9})


def test_discounts_missed_are_kept_apart_by_currency(tmp_path):
    """A yen discount is not dollars: the amounts stay per currency, like the duplicates stopped."""
    from ap_coder.webapp.insights import _per_currency

    store = Store(tmp_path / "a.db")
    for number, currency in (("A", "CAD"), ("B", "JPY"), ("C", "JPY")):
        doc = _gt(invoice_number=number, currency=currency, payment_terms="2/10 Net 30", due_date="")
        store.approve_invoice(store.add_invoice(tmp_path / f"{number}.pdf", doc, {}), doc, "Jane")
    ops = operations(store, today=dt.date(2026, 12, 1))  # approved today, well after the 10 days
    assert ops["discounts_missed"] == (3, {"CAD": 318.9, "JPY": 637.8})
    assert _per_currency(ops["discounts_missed"][1]) == "319 CAD + 638 JPY"


def test_operations_on_an_empty_database(tmp_path):
    ops = operations(Store(tmp_path / "a.db"))
    assert ops["waiting"] == 0 and ops["median_days_to_approve"] is None and ops["discounts_in_time"] == (0, {})


def test_vendors_that_make_work(tmp_path):
    import json

    from ap_coder.insights import vendor_workload
    from ap_coder.store import Store

    from .conftest import SAMPLE_STEM, SAMPLES

    store = Store(tmp_path / "w.db")
    gt = json.loads((SAMPLES / "ground_truth" / f"{SAMPLE_STEM}.json").read_text())
    bad = {"issues": [{"severity": "error", "code": "GST_HST_NUMBER_MISSING", "message": "x"},
                      {"severity": "warning", "code": "VENDOR_BANK_CHANGED", "message": "internal"}]}  # fmt: skip
    store.add_invoice(tmp_path / "a.pdf", gt, bad)
    b = store.add_invoice(tmp_path / "b.pdf", {**gt, "invoice_number": "X-2"}, {"issues": []})
    changed = {**gt, "invoice_number": "X-2", "line_items": [{**gt["line_items"][0], "predicted_gl_code": "6900"},
                                                              *gt["line_items"][1:]]}  # fmt: skip
    store.approve_invoice(b, changed, "Jane")
    c = store.add_invoice(tmp_path / "d.pdf", {**gt, "invoice_number": "X-3"}, {"issues": []})
    store.approve_invoice(c, {**gt, "invoice_number": "X-3", "invoice_date": "2026-09-30"}, "Jane")  # a date only
    store.add_invoice(tmp_path / "c.pdf", {**gt, "vendor_name": "Solo Vendor"}, bad)  # one invoice: too few
    (row,) = vendor_workload(store)
    assert (row["invoices"], row["with_problems"], row["corrected"], row["approved"]) == (3, 1, 1, 2)
    assert row["top_codes"] == ["GST_HST_NUMBER_MISSING"]  # internal checks never count as the vendor's


def test_duplicates_stopped_in_the_business_case(tmp_path):
    import json

    from ap_coder.insights import compute, report_html
    from ap_coder.store import Store

    from .conftest import SAMPLE_STEM, SAMPLES

    store = Store(tmp_path / "d.db")
    gt = json.loads((SAMPLES / "ground_truth" / f"{SAMPLE_STEM}.json").read_text())
    flagged = {"issues": [{"severity": "error", "code": "DUPLICATE_INVOICE", "message": "x"}]}
    a = store.add_invoice(tmp_path / "a.pdf", gt, flagged)
    store.reject_invoice(a, "Jane", "duplicate")
    b = store.add_invoice(tmp_path / "b.pdf", {**gt, "invoice_number": "Z"}, {"issues": []})
    store.reject_invoice(b, "Jane", "not ours")  # rejected, but not as a duplicate
    s = compute(store)
    assert s["duplicates_stopped"] == 1 and s["duplicates_stopped_total"] == {"CAD": gt["grand_total"]}
    assert "Duplicate invoices stopped" in report_html(s)
