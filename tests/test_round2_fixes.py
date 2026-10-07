"""Fixes from the second browser bug hunt."""

import json

from ap_coder.store import Store

from .conftest import SAMPLES


def _doc(stem):
    return json.loads((SAMPLES / "ground_truth" / f"{stem}.json").read_text())


def test_batch_totals_keep_currencies_apart(tmp_path):
    store = Store(tmp_path / "e.db")
    ids = []
    for stem in ("northwind_ON_HST_NW-2026-0912", "cascade_US_SalesTax_INV-30981", "chinook_AB_GST_CCO-26-10418"):
        doc = _doc(stem)
        invoice_id = store.add_invoice(tmp_path / f"{stem}.pdf", doc, {})
        store.approve_invoice(invoice_id, doc, "Jane")
        ids.append(invoice_id)
    batch = store.create_export_batch(ids, "csv")
    totals = store.batch_totals()[batch]
    assert set(totals) == {"CAD", "USD"}
    assert totals["USD"] == _doc("cascade_US_SalesTax_INV-30981")["grand_total"]
    store.undo_export_batch(batch)
    assert batch not in store.batch_totals()


def test_by_currency_text():
    from ap_coder.webapp.common import by_currency

    text = by_currency([{"currency": "USD", "grand_total": 10}, {"currency": "CAD", "grand_total": 5},
                        {"currency": "", "grand_total": 1}])  # fmt: skip
    assert text == "6.00 CAD · 10.00 USD"


def test_every_recorded_action_has_an_activity_filter():
    from ap_coder.audit import ACTIONS
    from ap_coder.webapp.activity import GROUPS

    assert set(ACTIONS) <= {a for actions in GROUPS.values() for a in actions}


def test_the_reviewers_currency_is_saved(tmp_path):
    import sqlite3

    store = Store(tmp_path / "c.db")
    doc = _doc("northwind_ON_HST_NW-2026-0912")
    invoice_id = store.add_invoice(tmp_path / "a.pdf", doc, {})
    store.approve_invoice(invoice_id, {**doc, "currency": "usd"}, "Jane")
    assert store.list_invoices()[0]["currency"] == "USD"
    store.create_export_batch([invoice_id], "csv")
    assert store.batch_totals()[1] == {"USD": doc["grand_total"]}
    # A database approved by an older version is put right on upgrade.
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE invoices SET currency = 'CAD'")
        conn.execute("UPDATE settings SET value = '11' WHERE key = 'schema_version'")
    assert Store(store.path).list_invoices()[0]["currency"] == "USD"
