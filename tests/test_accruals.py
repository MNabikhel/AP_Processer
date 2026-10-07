"""Month-end accruals: received not invoiced, invoices not in the ERP, expected recurring invoices."""

import datetime as dt
import json

from ap_coder import accruals
from ap_coder.config import Settings
from ap_coder.demo import load_demo
from ap_coder.pipeline import finalise_coding
from ap_coder.schema import InvoiceCoding
from ap_coder.store import Store, load_sample_purchase_orders, load_sample_setup

from .conftest import SAMPLES

END = dt.date(2026, 9, 30)


def _coded(store, stem, **changes):
    doc = {**json.loads((SAMPLES / "ground_truth" / f"{stem}.json").read_text()), **changes}
    output, _ = finalise_coding(InvoiceCoding.model_validate(doc), store.reference_data(), Settings(), store=store)
    return output


def test_demo_accruals(tmp_path):
    store = Store(tmp_path / "a.db")
    load_demo(store)
    items = accruals.build(store, END)
    received = [a for a in items if a.source == accruals.RECEIVED]
    # Only the mount kits: the returned laptop and the freight billed without the PO number are not accrued.
    assert [(a.reference, a.amount, a.gl_code) for a in received] == [("PO-89904 line 5", 210.0, "6010")]
    not_in_erp = {a.reference.split(" (")[0] for a in items if a.source == accruals.NOT_IN_ERP}
    assert "Invoice NW-2026-0912" in not_in_erp and "Invoice ACMR-2026-1187" in not_in_erp
    assert "Invoice INV-30981" not in not_in_erp  # Cascade is dated October 1, after the period end
    totals = accruals.summary(items)
    assert totals[accruals.RECEIVED] == (1, 210.0)
    assert sum(t for _, _, t in accruals.by_gl(items)) == round(sum(a.amount for a in items), 2)
    assert "PO-89904 line 5" in accruals.to_csv(items, END).decode("utf-8-sig")


def test_exported_rejected_and_closed_are_left_out(tmp_path):
    store = Store(tmp_path / "a.db")
    load_sample_setup(store)
    load_sample_purchase_orders(store)
    nw = _coded(store, "northwind_ON_HST_NW-2026-0912")
    a = store.add_invoice(tmp_path / "a.pdf", nw, {})
    store.approve_invoice(a, nw, "Jane")
    b = store.add_invoice(tmp_path / "b.pdf", {**nw, "invoice_number": "X-1"}, {})
    store.reject_invoice(b, "Jane", "duplicate")
    refs = {x.reference for x in accruals.build(store, END)}
    assert any("NW-2026-0912" in r for r in refs) and not any("X-1" in r for r in refs)
    store.create_export_batch([a], "csv")
    assert not any("NW-2026-0912" in x.reference for x in accruals.build(store, END))
    assert any(x.reference == "PO-89904 line 1" for x in accruals.build(store, END))  # nothing billed yet
    store.set_po_status("89904", "closed")
    assert not any(x.reference.startswith("PO-89904") for x in accruals.build(store, END))


def test_expected_recurring_invoice(tmp_path):
    store = Store(tmp_path / "a.db")
    load_sample_setup(store)
    doc = _coded(store, "chinook_AB_GST_CCO-26-10418")
    for i, day in enumerate(("2026-05-31", "2026-06-30", "2026-07-31")):
        inv = {**doc, "invoice_number": f"C-{i}", "invoice_date": day}
        invoice_id = store.add_invoice(tmp_path / f"{i}.pdf", inv, {})
        store.approve_invoice(invoice_id, inv, "Jane")
    expected = [x for x in accruals.build(store, END) if x.source == accruals.RECURRING]
    assert len(expected) == 1 and expected[0].amount == doc["grand_total"] and expected[0].gl_code
    assert not [x for x in accruals.build(store, dt.date(2026, 8, 15)) if x.source == accruals.RECURRING]


def test_default_period_end():
    assert accruals.default_period_end(dt.date(2026, 10, 3)) == dt.date(2026, 9, 30)
    assert accruals.default_period_end(dt.date(2026, 10, 20)) == dt.date(2026, 10, 31)
    assert accruals.default_period_end(dt.date(2026, 12, 15)) == dt.date(2026, 12, 31)
