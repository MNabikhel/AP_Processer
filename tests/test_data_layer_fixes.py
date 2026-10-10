"""Regression tests for defects found in the store, matching and reconciliation modules."""

import json

import pytest

from ap_coder.config import Settings
from ap_coder.pipeline import finalise_coding
from ap_coder.schema import InvoiceCoding
from ap_coder.store import APPROVED, REJECTED, Store

from .conftest import SAMPLE_STEM, SAMPLES


def _gt():
    return json.loads((SAMPLES / "ground_truth" / f"{SAMPLE_STEM}.json").read_text())


def test_an_approved_and_exported_invoice_cannot_be_rejected_from_a_stale_screen(tmp_path):
    # Two people have the same invoice open: one approves and exports it, the other then clicks Reject.
    store = Store(tmp_path / "a.db")
    invoice_id = store.add_invoice(tmp_path / "a.pdf", _gt(), {})
    store.approve_invoice(invoice_id, _gt(), "Jane")
    store.create_export_batch([invoice_id], "jde", "Jane")
    with pytest.raises(ValueError):
        store.reject_invoice(invoice_id, "Sam", "not ours")
    inv = store.get_invoice(invoice_id)
    assert inv["status"] == APPROVED and inv["reviewer"] == "Jane"
    assert store.feedback_rows()  # what the approval taught is kept with it
    # An exported invoice is reopened only by undoing its batch first, never through a rejection.
    with pytest.raises(ValueError):
        store.reopen(invoice_id, "Sam")


def test_review_and_parked_invoices_can_still_be_rejected(tmp_path):
    store = Store(tmp_path / "a.db")
    a = store.add_invoice(tmp_path / "a.pdf", _gt(), {})
    store.reject_invoice(a, "Jane", "duplicate")
    b = store.add_invoice(tmp_path / "b.pdf", {**_gt(), "invoice_number": "B"}, {})
    store.park_invoice(b, "Jane", "waiting for the buyer")
    store.reject_invoice(b, "Jane", "not ours")
    assert [store.get_invoice(i)["status"] for i in (a, b)] == [REJECTED, REJECTED]


def test_rejecting_an_approved_invoice_withdraws_what_it_taught(tmp_path):
    store = Store(tmp_path / "a.db")
    invoice_id = store.add_invoice(tmp_path / "a.pdf", _gt(), {})
    store.approve_invoice(invoice_id, _gt(), "Jane")
    assert store.feedback_rows()
    with pytest.raises(ValueError):  # a Reject from a screen opened before the approval keeps the approval
        store.reject_invoice(invoice_id, "Sam", "not ours after all")
    assert store.get_invoice(invoice_id)["status"] == APPROVED and store.feedback_rows()
    store.reopen(invoice_id, "Sam", "not ours after all")  # the way: reopen, then reject
    store.reject_invoice(invoice_id, "Sam", "not ours after all")
    assert store.get_invoice(invoice_id)["status"] == REJECTED
    assert store.feedback_rows() == []  # a rejected bill must not keep teaching the AI its coding


def test_a_rejected_invoice_cannot_be_approved_from_a_stale_screen(tmp_path):
    # One person rejects the bill ("not ours"); another, with the invoice still open, clicks Approve.
    store = Store(tmp_path / "a.db")
    invoice_id = store.add_invoice(tmp_path / "a.pdf", _gt(), {})
    store.reject_invoice(invoice_id, "Jane", "not ours")
    with pytest.raises(ValueError):
        store.approve_invoice(invoice_id, _gt(), "Sam")
    assert store.get_invoice(invoice_id)["status"] == REJECTED
    assert store.unexported_approved() == [] and store.feedback_rows() == []
    store.reopen(invoice_id, "Sam", "it is ours after all")  # the way back: reopen, then approve
    store.approve_invoice(invoice_id, _gt(), "Sam")
    assert store.get_invoice(invoice_id)["status"] == APPROVED


def test_the_approval_limit_applies_in_cad_to_a_foreign_currency_invoice(tmp_path):
    from ap_coder import controls
    from ap_coder.store import PENDING

    store = Store(tmp_path / "a.db")
    store.set_setting("approval_limit", "10000")
    store.set_setting("fx_rates", "USD=1.37")
    usd = {**_gt(), "currency": "USD", "grand_total": 9000.0}  # about 12,330 CAD: over the limit
    invoice_id = store.add_invoice(tmp_path / "a.pdf", usd, {})
    store.approve_invoice(invoice_id, usd, "Jane")
    assert store.get_invoice(invoice_id)["status"] == PENDING
    # Without a rate for the currency, the amount is compared as it is.
    eur = {**_gt(), "invoice_number": "E-1", "currency": "EUR", "grand_total": 9000.0}
    other = store.add_invoice(tmp_path / "b.pdf", eur, {})
    store.approve_invoice(other, eur, "Jane")
    assert store.get_invoice(other)["status"] == APPROVED
    # The controls report sees the USD invoice over the limit too (had it been approved by one person).
    store.final_approve(invoice_id, "Sam")
    with store._conn() as conn:
        conn.execute("UPDATE invoices SET second_reviewer = NULL WHERE id = ?", (invoice_id,))
    import datetime as dt

    today = dt.date.today()
    report = controls.build(store, today - dt.timedelta(days=1), today + dt.timedelta(days=1))
    assert [i["id"] for i in report["one_person"]] == [invoice_id]


def test_vendor_spend_counts_invoices_that_print_no_currency_as_cad(tmp_path):
    store = Store(tmp_path / "a.db")
    doc = {**_gt(), "currency": ""}  # most Canadian invoices print no currency code
    invoice_id = store.add_invoice(tmp_path / "a.pdf", doc, {})
    store.approve_invoice(invoice_id, doc, "Jane")
    (row,) = store.vendor_summaries()
    assert row["spend_cad"] == pytest.approx(doc["grand_total"])


def test_same_amount_check_treats_an_invoice_without_currency_as_cad(tmp_path, reference):
    store = Store(tmp_path / "a.db")
    first = {**_gt(), "invoice_number": "A-1", "currency": ""}  # the first invoice printed no currency
    invoice_id = store.add_invoice(tmp_path / "a.pdf", first, {})
    store.approve_invoice(invoice_id, first, "Jane")
    again = {**_gt(), "invoice_number": "A-2", "currency": "CAD"}  # same bill, new number, CAD printed this time
    _, report = finalise_coding(InvoiceCoding.model_validate(again), reference, Settings(), store=store)
    assert "POSSIBLE_DUPLICATE_AMOUNT" in {i.code for i in report.issues}
