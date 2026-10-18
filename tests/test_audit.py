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


def _logged_actions() -> set[str]:
    """Every event action the code logs, read from the source: the literal (or the constant, or the values of a
    local ``action``) given to ``_log`` / ``log_event``, and the update script's own insert."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    found: set[str] = set()
    for path in [*(root / "ap_coder").rglob("*.py"), root / "scripts" / "upgrade.py"]:
        text = path.read_text(encoding="utf-8")
        constants = dict(re.findall(r'^([A-Z_]+_ACTION) = "(\w+)"', text, re.M))
        for arg in re.findall(r"(?:\b_log\(conn,|\blog_event\()\s*([^,()]+)", text):
            found |= set(re.findall(r'"(\w+)"', arg))
            found |= {constants[n] for n in re.findall(r"\b[A-Z_]+_ACTION\b", arg) if n in constants}
            if arg.strip() == "action":
                found |= set(re.findall(r'\baction = "(\w+)"', text))
                for values in re.findall(r"\baction = \{([^}]*)\}", text):
                    found |= set(re.findall(r'"(\w+)"', values))
        found |= set(re.findall(r'\("(\w+)", UPDATE_ACTOR', text))
    return found


def test_every_logged_action_has_a_label_and_a_description():
    from ap_coder.audit import ACTIONS

    logged = _logged_actions()
    assert {"vendor_added", "lesson_retired", "lesson_restored", "autonomy_held", "autonomy_on", "email_sorted",
            "files_moved", "vendors_deleted"} <= logged  # fmt: skip
    assert sorted(logged - set(ACTIONS)) == []
    from ap_coder.web.pages.activity import GROUPS  # and every one can be found by the Activity page's filters

    assert sorted(set(ACTIONS) - {a for actions in GROUPS.values() for a in actions}) == []
    details = {
        "vendor_added": {"vendor": "Acme", "gst": "123456789RT0001"}, "verified": {"status": "agreed", "rounds": 2},
        "azure_checked": {"compared": 5, "agreed": 4, "differ": ["total"]},
        "azure_check_disagrees": {"fields": ["total", "date"]}, "lesson_explained": {"reason": "Rent is 6200"},
        "lesson_retired": {"lesson": 3}, "lesson_restored": {"lesson": 3}, "directory_preset_saved": {"name": "Q3"},
        "directory_preset_deleted": {"name": "Q3"}, "vendors_deleted": {"vendors": ["acme"]},
    }  # fmt: skip
    for action, detail in details.items():
        assert describe({"action": action, "detail": detail}), action
