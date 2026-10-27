"""Parking invoices that wait for information, and notes for the team."""

import datetime as dt
import json

import pytest

from ap_coder.core.audit import describe
from ap_coder.core.store import PARKED, REVIEW, Store, load_sample_setup
from ap_coder.erp import accruals
from tests.conftest import SAMPLE_STEM, SAMPLES
from tests.webclient import client_for, follow, post


def _gt(**changes):
    return {**json.loads((SAMPLES / "ground_truth" / f"{SAMPLE_STEM}.json").read_text()), **changes}


def test_park_and_unpark(tmp_path):
    store = Store(tmp_path / "a.db")
    invoice_id = store.add_invoice(tmp_path / "a.pdf", _gt(), {})
    store.park_invoice(invoice_id, "Jane", "buyer to confirm the price", "2026-10-20")
    assert store.get_invoice(invoice_id)["status"] == PARKED and store.list_invoices(REVIEW) == []
    assert store.parked()[0]["parked_reason"] == "buyer to confirm the price"
    assert store.find_duplicates(_gt()["vendor_name"], _gt()["invoice_number"]) == [invoice_id]  # still counts
    parked = next(e for e in store.events(invoice_id) if e["action"] == "parked")
    assert describe(parked) == "waiting for: buyer to confirm the price · follow up 2026-10-20"
    with pytest.raises(ValueError):
        store.park_invoice(invoice_id, "Jane", "again")
    store.unpark_invoice(invoice_id, "Sam")
    inv = store.get_invoice(invoice_id)
    assert inv["status"] == REVIEW and inv["parked_reason"] is None and inv["follow_up"] is None
    with pytest.raises(ValueError):
        store.unpark_invoice(invoice_id, "Sam")


def test_parked_invoices_are_still_accrued(tmp_path):
    store = Store(tmp_path / "a.db")
    load_sample_setup(store)
    invoice_id = store.add_invoice(tmp_path / "a.pdf", {**_gt(), "gl_distribution": [
        {"kind": "expense", "gl_code": "6010", "cost_center": "", "amount": 100.0}]}, {})  # fmt: skip
    store.park_invoice(invoice_id, "Jane", "credit note promised")
    items = accruals.build(store, dt.date(2026, 9, 30))
    assert [a.note for a in items if a.source == accruals.NOT_IN_ERP] == [f"parked, {accruals.PROPOSED}"]


def test_notes(tmp_path):
    store = Store(tmp_path / "a.db")
    invoice_id = store.add_invoice(tmp_path / "a.pdf", _gt(), {})
    store.add_note(invoice_id, "Jane", "  asked Sam about the PO  ")
    store.add_note(invoice_id, "Jane", "   ")  # blank: ignored
    notes = [e for e in store.events(invoice_id) if e["action"] == "note"]
    assert len(notes) == 1 and describe(notes[0]) == "asked Sam about the PO"


def test_park_from_the_review_screen_and_bring_back(tmp_path):
    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    invoice_id = store.add_invoice(SAMPLES / f"{SAMPLE_STEM}.pdf", _gt(), {"requires_review": True})
    client = client_for(store)
    follow(client, post(client, f"/review/{invoice_id}/note", {"text": "checking with Sam"}))
    assert any(e["action"] == "note" for e in store.events(invoice_id))
    follow(client, post(client, f"/review/{invoice_id}/park", {"reason": "buyer to confirm"}))
    assert store.get_invoice(invoice_id)["status"] == PARKED
    assert "buyer to confirm" in client.get("/review?tab=parked").text
    follow(client, post(client, f"/review/{invoice_id}/unpark"))
    assert store.get_invoice(invoice_id)["status"] == REVIEW
