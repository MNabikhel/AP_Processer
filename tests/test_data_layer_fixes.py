"""Regression tests for defects found in the store, matching and reconciliation modules."""

import json

import pytest

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
    store.reject_invoice(invoice_id, "Sam", "not ours after all")
    assert store.get_invoice(invoice_id)["status"] == REJECTED
    assert store.feedback_rows() == []  # a rejected bill must not keep teaching the AI its coding
