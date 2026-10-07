"""Second approval above the approval limit (separation of duties)."""

import json
import sys

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from ap_coder.store import APPROVED, PENDING, REVIEW, Store, load_sample_setup

from .conftest import ROOT, SAMPLE_STEM, SAMPLES


def _gt():
    return json.loads((SAMPLES / "ground_truth" / f"{SAMPLE_STEM}.json").read_text())  # total 18,017.85


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "a.db")
    s.set_setting("approval_limit", "10000")
    return s


def test_over_the_limit_waits_for_a_second_approver(store, tmp_path):
    invoice_id = store.add_invoice(tmp_path / "a.pdf", _gt(), {})
    store.approve_invoice(invoice_id, _gt(), "Jane")
    assert store.get_invoice(invoice_id)["status"] == PENDING
    assert store.unexported_approved() == []  # not exportable yet
    assert len(store.feedback_rows()) == 5  # the coding decision is learned on the first approval
    with pytest.raises(PermissionError):
        store.final_approve(invoice_id, " jane ")
    store.final_approve(invoice_id, "Sam")
    inv = store.get_invoice(invoice_id)
    assert inv["status"] == APPROVED and inv["second_reviewer"] == "Sam" and inv["reviewer"] == "Jane"
    assert [i["id"] for i in store.unexported_approved()] == [invoice_id]
    assert [e["action"] for e in store.events(invoice_id)][:2] == ["final_approved", "approved"]
    with pytest.raises(ValueError):
        store.final_approve(invoice_id, "Sam")
    with pytest.raises(ValueError):
        store.approve_invoice(invoice_id, _gt(), "Sam")


def test_under_the_limit_or_no_limit_is_approved_at_once(store, tmp_path):
    small = {**_gt(), "grand_total": 9000.0}
    a = store.add_invoice(tmp_path / "a.pdf", small, {})
    store.approve_invoice(a, small, "Jane")
    assert store.get_invoice(a)["status"] == APPROVED
    store.set_setting("approval_limit", "0")
    b = store.add_invoice(tmp_path / "b.pdf", {**_gt(), "invoice_number": "B"}, {})
    store.approve_invoice(b, {**_gt(), "invoice_number": "B"}, "Jane")
    assert store.get_invoice(b)["status"] == APPROVED
    store.set_setting("approval_limit", "lots")
    assert store.approval_limit() == 0


def test_send_back_returns_it_to_the_queue_and_withdraws_the_lessons(store, tmp_path):
    invoice_id = store.add_invoice(tmp_path / "a.pdf", _gt(), {})
    store.approve_invoice(invoice_id, _gt(), "Jane")
    store.send_back(invoice_id, "Sam", "wrong cost center")
    inv = store.get_invoice(invoice_id)
    assert inv["status"] == REVIEW and inv["reviewer"] is None and store.feedback_rows() == []
    store.approve_invoice(invoice_id, _gt(), "Jane")  # approved again: learned again, once
    assert len(store.feedback_rows()) == 5 and store.get_invoice(invoice_id)["status"] == PENDING
    with pytest.raises(ValueError):
        store.send_back(store.add_invoice(tmp_path / "b.pdf", {**_gt(), "invoice_number": "B"}, {}), "Sam")


def test_waiting_invoices_still_count_for_duplicates_and_pos(store, tmp_path):
    from ap_coder.store import load_sample_purchase_orders

    load_sample_purchase_orders(store)
    invoice_id = store.add_invoice(tmp_path / "a.pdf", _gt(), {})
    store.approve_invoice(invoice_id, _gt(), "Jane")
    assert store.find_duplicates(_gt()["vendor_name"], _gt()["invoice_number"]) == [invoice_id]
    assert [i["id"] for i in store.po_invoices("88213")] == [invoice_id]


def test_second_approval_tab(tmp_path, monkeypatch):
    path = tmp_path / "private" / "ap_coder.db"
    monkeypatch.setenv("AP_DB_PATH", str(path))
    monkeypatch.setenv("AP_REVIEWER", "Sam")
    for name in [m for m in sys.modules if m.startswith(("ap_coder.webapp", "ap_coder.dashboard"))]:
        monkeypatch.delitem(sys.modules, name)
    st.cache_resource.clear()
    st.cache_data.clear()
    store = Store(path)
    load_sample_setup(store)
    store.set_setting("approval_limit", "10000")
    invoice_id = store.add_invoice(tmp_path / "a.pdf", _gt(), {})
    store.approve_invoice(invoice_id, _gt(), "Jane")
    at = AppTest.from_file(str(ROOT / "ap_coder" / "dashboard.py"), default_timeout=90).run()
    assert not at.exception
    assert not at.button(key=f"second_ok_{invoice_id}").disabled  # Sam did not approve it first
    at.button(key=f"second_ok_{invoice_id}").click().run()
    assert not at.exception
    assert store.get_invoice(invoice_id)["status"] == APPROVED
