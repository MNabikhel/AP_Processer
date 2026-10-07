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
    assert ops["discounts_in_time"] == (1, 318.9) and ops["discounts_missed"] == (1, 318.9)


def test_operations_on_an_empty_database(tmp_path):
    ops = operations(Store(tmp_path / "a.db"))
    assert ops["waiting"] == 0 and ops["median_days_to_approve"] is None and ops["discounts_in_time"] == (0, 0.0)
