"""Audit trail: every change to data is recorded with who did it, and approvals record exactly what changed."""

import copy

from ap_coder.audit import describe, diff_coding
from ap_coder.store import Store


def test_approval_records_every_reviewer_change(tmp_path, ground_truth):
    store = Store(tmp_path / "ap.db")
    invoice_id = store.add_invoice(tmp_path / "x.pdf", ground_truth, {"requires_review": True})
    final = copy.deepcopy(ground_truth)
    final["vendor_name"] = "Northwind IT Solutions Incorporated"
    final["line_items"][1]["predicted_gl_code"] = "6010"
    final["line_items"].append({**final["line_items"][0], "line_number": 6, "description": "Eco fee", "amount": 5.0})
    store.approve_invoice(invoice_id, final, "jane")
    approved = store.events(invoice_id, actions=["approved"])[0]
    assert approved["actor"] == "jane"
    whats = [c["what"] for c in approved["detail"]["changes"]]
    assert whats == ["vendor", "line 2 GL", "line 6 added"]
    assert "line 2 GL: 1500 → 6010" in describe(approved)
    assert [e["action"] for e in store.events(invoice_id)] == ["approved", "processed"]


def test_rejections_deletions_and_setup_changes_are_logged(tmp_path, ground_truth):
    store = Store(tmp_path / "ap.db")
    a = store.add_invoice(tmp_path / "a.pdf", ground_truth, {})
    b = store.add_invoice(tmp_path / "b.pdf", None, None, error="boom")
    store.reject_invoice(a, "jane", "not ours")
    store.delete_invoice(b, actor="sam")
    store.import_accounts("gl_accounts", [{"c": "6000", "d": "Office"}], "c", "d", actor="jane")
    store.save_accounts("gl_accounts", [{"code": "6000", "description": "Office supplies", "category": ""}],
                        actor="jane")  # fmt: skip
    store.delete_accounts("gl_accounts", ["6000"], actor="sam")
    store.set_tax_treatment("HST", "recoverable", "2310", actor="jane")
    store.set_tax_treatment("HST", "recoverable", "2310", actor="jane")  # no change, no event
    store.set_setting("policy_notes", "- Laptops to 6010", actor="jane")
    actions = [e["action"] for e in reversed(store.events())]
    assert actions == ["processed", "failed", "rejected", "deleted", "accounts_imported", "accounts_edited",
                       "accounts_deleted", "tax_setup_changed", "policy_changed"]  # fmt: skip
    deleted = store.events(b, actions=["deleted"])[0]
    assert deleted["actor"] == "sam" and deleted["detail"]["file"] == "b.pdf"
    assert describe(store.events(a, actions=["rejected"])[0]) == "reason: not ours"


def test_diff_spots_removed_lines_and_tax_changes(ground_truth):
    final = copy.deepcopy(ground_truth)
    del final["line_items"][4]
    final["tax_lines"][0]["tax_amount"] = 2000.0
    whats = [c["what"] for c in diff_coding(ground_truth, final)]
    assert "line 5 removed" in whats and "sales tax lines" in whats
    assert diff_coding(ground_truth, copy.deepcopy(ground_truth)) == []
