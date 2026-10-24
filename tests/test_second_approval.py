"""Second approval above the approval limit (separation of duties)."""

import json
import re
import time

import pytest

from ap_coder.core.store import APPROVED, PENDING, REVIEW, Store, load_sample_setup

from .conftest import SAMPLE_STEM, SAMPLES
from .webclient import client_for, follow, post


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


@pytest.mark.parametrize("total", [float("nan"), float("inf"), float("-inf"), "nan"])
def test_a_total_that_is_not_a_number_needs_the_second_approver(store, total):
    # NaN compares False with everything, so "over the limit" was never true and no second approver was asked.
    assert store.second_approval_reason({"grand_total": total, "currency": "CAD"})


def test_under_the_limit_or_no_limit_is_approved_at_once(store, tmp_path):
    small = {**_gt(), "grand_total": 9000.0}
    a = store.add_invoice(tmp_path / "a.pdf", small, {})
    store.approve_invoice(a, small, "Jane")
    assert store.get_invoice(a)["status"] == APPROVED
    store.set_setting("approval_limit", "0")
    time.sleep(1.1)  # a change in the invoice's own second counts as in force when it came in (B-970)
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
    from ap_coder.core.store import load_sample_purchase_orders

    load_sample_purchase_orders(store)
    invoice_id = store.add_invoice(tmp_path / "a.pdf", _gt(), {})
    store.approve_invoice(invoice_id, _gt(), "Jane")
    assert store.find_duplicates(_gt()["vendor_name"], _gt()["invoice_number"]) == [invoice_id]
    assert [i["id"] for i in store.po_invoices("88213")] == [invoice_id]


def test_second_approval_tab(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_REVIEWER", "Sam")
    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    store.set_setting("approval_limit", "10000")
    invoice_id = store.add_invoice(tmp_path / "a.pdf", _gt(), {})
    store.approve_invoice(invoice_id, _gt(), "Jane")
    client = client_for(store)
    page = client.get("/review?tab=second").text
    button = re.search(rf'action="/review/{invoice_id}/final-approve">\s*<button[^>]*>', page).group(0)
    assert "disabled" not in button  # Sam did not approve it first
    follow(client, post(client, f"/review/{invoice_id}/final-approve"))
    assert store.get_invoice(invoice_id)["status"] == APPROVED


def test_insights_count_a_sent_back_invoice_once(store, tmp_path):
    from ap_coder.erp.insights import compute

    invoice_id = store.add_invoice(tmp_path / "a.pdf", _gt(), {})
    store.approve_invoice(invoice_id, _gt(), "Jane")
    store.send_back(invoice_id, "Sam", "check the price")
    assert compute(store)["approved"] == 0  # back in review: not approved any more
    store.approve_invoice(invoice_id, _gt(), "Jane")
    assert compute(store)["approved"] == 1
