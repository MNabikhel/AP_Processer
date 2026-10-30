"""Month-end accruals: received not invoiced, invoices not in the ERP, expected recurring invoices."""

import datetime as dt
import json

import pytest

from ap_coder.ai.pipeline import finalise_coding
from ap_coder.core.config import Settings
from ap_coder.core.schema import InvoiceCoding
from ap_coder.core.store import Store, load_sample_purchase_orders, load_sample_setup
from ap_coder.erp import accruals
from tests.conftest import SAMPLES, copy_database

END = dt.date(2026, 9, 30)


def _coded(store, stem, **changes):
    doc = {**json.loads((SAMPLES / "ground_truth" / f"{stem}.json").read_text()), **changes}
    output, _ = finalise_coding(InvoiceCoding.model_validate(doc), store.reference_data(), Settings(), store=store)
    return output


def test_demo_accruals(tmp_path, demo_template):
    store = copy_database(demo_template, tmp_path / "a.db")
    items = accruals.build(store, END)
    received = [a for a in items if a.source == accruals.RECEIVED]
    # Only the mount kits: the returned laptop and the freight billed without the PO number are not accrued.
    assert [(a.reference, a.amount, a.gl_code) for a in received] == [("PO-89904 line 5", 210.0, "6010")]
    not_in_erp = {a.reference.split(" (")[0] for a in items if a.source == accruals.NOT_IN_ERP}
    assert "Invoice NW-2026-0912" in not_in_erp and "Invoice ACMR-2026-1187" in not_in_erp
    assert "Invoice INV-30981" not in not_in_erp  # Cascade is dated October 1, after the period end
    totals = accruals.summary(items)
    assert totals[accruals.RECEIVED] == (1, {"CAD": 210.0})
    by_gl = [t for _, totals in accruals.by_gl_columns(items)[1] for t in totals.values()]
    assert sum(by_gl) == pytest.approx(round(sum(a.amount for a in items), 2))
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
    store.create_export_batch([a], "csv")  # today: in the ERP at today's period end (D-133)
    assert not any("NW-2026-0912" in x.reference for x in accruals.build(store, dt.date.today()))
    assert any(x.reference == "PO-89904 line 1" for x in accruals.build(store, END))  # nothing billed yet
    store.set_po_status("89904", "closed")
    assert not any(x.reference.startswith("PO-89904") for x in accruals.build(store, END))


def test_an_invoice_exported_after_the_period_end_is_accrued_for_that_period(tmp_path):
    # Dated 14 September, exported on 2 October: JD Edwards has it in October, so at 30 September it is accrued, and
    # says why (D-133). Exported by 30 September, it was in the ERP then.
    from tests.webclient import client_for

    store = Store(tmp_path / "a.db")
    load_sample_setup(store)
    nw = _coded(store, "northwind_ON_HST_NW-2026-0912")
    a = store.add_invoice(tmp_path / "a.pdf", nw, {})
    store.approve_invoice(a, nw, "Jane")
    batch = store.create_export_batch([a], "csv")

    def made_on(when):
        with store._conn() as conn:
            conn.execute("UPDATE export_batches SET created_at = ? WHERE id = ?", (when, batch))
        return [x for x in accruals.build(store, END) if f"(#{a})" in x.reference]

    late = made_on("2026-10-02T09:15:00")
    assert late and {x.note for x in late} == {"exported after the month end"}
    assert round(sum(x.amount for x in late), 2) == nw["subtotal"]  # its cost; the recoverable HST is not
    assert "exported after the month end" in accruals.to_csv(late, END).decode("utf-8-sig")
    assert "Exported after the month end" in client_for(store).get(f"/month-end?end={END}").text
    assert made_on("2026-09-30T16:40:00") == []
    assert made_on("2026-10-02T09:15:00") and not [x for x in accruals.build(store, dt.date(2026, 10, 31))
                                                   if f"(#{a})" in x.reference]  # fmt: skip


def test_a_resent_copy_of_a_bill_already_counted_is_not_accrued_again(tmp_path):
    store = Store(tmp_path / "a.db")
    load_sample_setup(store)
    nw = _coded(store, "northwind_ON_HST_NW-2026-0912")
    a = store.add_invoice(tmp_path / "a.pdf", nw, {})
    store.approve_invoice(a, nw, "Jane")
    store.create_export_batch([a], "csv")
    flagged = {"issues": [{"severity": "error", "code": "DUPLICATE_INVOICE", "message": f"same as invoice #{a}"}]}
    copy = store.add_invoice(tmp_path / "b.pdf", nw, flagged)  # the vendor sent it again: in review
    other = store.add_invoice(tmp_path / "c.pdf", {**nw, "invoice_number": "NW-2026-0999"}, flagged)
    accrued = {x.reference for x in accruals.build(store, END) if x.source == accruals.NOT_IN_ERP}
    assert not any(f"(#{copy})" in r for r in accrued)  # the bill is in the ERP already
    assert any(f"(#{other})" in r for r in accrued)  # flagged, but no bill with its number is counted
    in_erp = {"issues": [{"severity": "error", "code": "DUPLICATE_IN_ERP", "message": "in the ERP"}]}
    keyed = store.add_invoice(tmp_path / "d.pdf", {**nw, "invoice_number": "NW-2026-1000"}, in_erp)
    assert not any(f"(#{keyed})" in x.reference for x in accruals.build(store, END))


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


def test_an_expected_recurring_invoice_is_not_accrued_beside_its_vendors_received_po_lines(tmp_path):
    # Chinook bills monthly, and its freight on PO-90155 was received, not invoiced, by 30 September: its next invoice
    # bills that freight. Accrued once, on the PO line; the page says why the expected invoice is left out.
    from tests.webclient import client_for

    store = Store(tmp_path / "a.db")
    load_sample_setup(store)
    load_sample_purchase_orders(store)
    stem = "chinook_AB_GST_CCO-26-10418"
    lines = json.loads((SAMPLES / "ground_truth" / f"{stem}.json").read_text())["line_items"]
    doc = _coded(store, stem, line_items=[li for li in lines if "PO-90155" not in li["description"]])
    for i, day in enumerate(("2026-05-31", "2026-06-30", "2026-07-31")):
        inv = {**doc, "invoice_number": f"C-{i}", "invoice_date": day}
        store.approve_invoice(store.add_invoice(tmp_path / f"{i}.pdf", inv, {}), inv, "Jane")
    items = accruals.build(store, END)
    chinook = [(a.source, a.reference, a.amount) for a in items
               if a.source != accruals.NOT_IN_ERP and a.vendor == doc["vendor_name"]]  # fmt: skip
    assert chinook == [(accruals.RECEIVED, "PO-90155 line 1", 385.0)]
    left_out: list[accruals.Accrual] = []
    assert accruals.build(store, END, left_out=left_out) == items
    why = (doc["vendor_name"], doc["grand_total"], accruals.ON_PO)
    assert [(a.vendor, a.amount, a.note) for a in left_out] == [why]
    text = client_for(store).get(f"/month-end?end={END}").text
    assert "Not accrued as expected recurring invoices" in text and "Chinook Courier" in text.split("Not accrued as")[1]


def test_default_period_end():
    assert accruals.default_period_end(dt.date(2026, 10, 3)) == dt.date(2026, 9, 30)
    assert accruals.default_period_end(dt.date(2026, 10, 20)) == dt.date(2026, 10, 31)
    assert accruals.default_period_end(dt.date(2026, 12, 15)) == dt.date(2026, 12, 31)
