"""Parking invoices that wait for information, and notes for the team."""

import datetime as dt
import json
import sys

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from ap_coder import accruals
from ap_coder.audit import describe
from ap_coder.store import PARKED, REVIEW, Store, load_sample_setup

from .conftest import ROOT, SAMPLE_STEM, SAMPLES


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
    assert [a.note for a in items if a.source == accruals.NOT_IN_ERP] == ["parked"]


def test_notes(tmp_path):
    store = Store(tmp_path / "a.db")
    invoice_id = store.add_invoice(tmp_path / "a.pdf", _gt(), {})
    store.add_note(invoice_id, "Jane", "  asked Sam about the PO  ")
    store.add_note(invoice_id, "Jane", "   ")  # blank: ignored
    notes = [e for e in store.events(invoice_id) if e["action"] == "note"]
    assert len(notes) == 1 and describe(notes[0]) == "asked Sam about the PO"


def test_park_from_the_review_screen_and_bring_back(tmp_path, monkeypatch):
    path = tmp_path / "private" / "ap_coder.db"
    monkeypatch.setenv("AP_DB_PATH", str(path))
    for name in [m for m in sys.modules if m.startswith(("ap_coder.webapp", "ap_coder.dashboard"))]:
        monkeypatch.delitem(sys.modules, name)
    st.cache_resource.clear()
    st.cache_data.clear()
    store = Store(path)
    load_sample_setup(store)
    invoice_id = store.add_invoice(SAMPLES / f"{SAMPLE_STEM}.pdf", _gt(), {"requires_review": True})
    key = f"inv{invoice_id}"
    at = AppTest.from_file(str(ROOT / "ap_coder" / "dashboard.py"), default_timeout=90)
    at.session_state["open_invoice"] = invoice_id
    at.run()
    at.text_input(key=f"{key}_note").input("checking with Sam")
    next(b for b in at.button if b.label == "Add note").click().run()
    assert any(e["action"] == "note" for e in store.events(invoice_id))
    at.text_input(key=f"{key}_park_reason").input("buyer to confirm").run()
    at.button(key=f"{key}_park").click().run()
    assert not at.exception and store.get_invoice(invoice_id)["status"] == PARKED
    at = AppTest.from_file(str(ROOT / "ap_coder" / "dashboard.py"), default_timeout=90).run()
    assert not at.exception
    at.button(key=f"unpark_{invoice_id}").click().run()
    assert store.get_invoice(invoice_id)["status"] == REVIEW
