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
