"""The fixes from a pilot tester's walk of laptop mode (B-1297 on): a new vendor with JD Edwards on, the learning
figures without the model, a correction after a reopen, the Welcome checklist's JD Edwards line, the JD Edwards
"why not" list, ``watch --once``, emails uploaded, the second approver on Exports, the people on Activity, and
pilot-score's plurals."""

from __future__ import annotations

import json
import re

from ap_coder.core.memory import vendor_key
from ap_coder.core.store import REVIEW
from ap_coder.erp import jde

from .test_web_invoice import PAGE, check
from .test_web_review_signoff import _jde_on, client, demo_db, me, store

__all__ = ["client", "demo_db", "me", "store"]  # the fixtures, shared


def _plain(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def _cascade(store) -> dict:
    return next(i for i in store.list_invoices(REVIEW) if "Cascade" in (i["vendor_name"] or ""))


# --- 1. A new vendor with JD Edwards on ------------------------------------------------------------------------------


def test_a_new_vendor_with_jd_edwards_on_can_be_given_its_address_number_where_it_is_added(client, store):
    """B-1297: with JD Edwards on, an invoice from a vendor added with "Add new vendor…" could not be approved: "ask the
    pilot tester to add its number", on a laptop where the reader is the pilot tester, without saying where. The
    pop-up said "Its invoices need a person", as if a person could approve them. The pop-up now takes the address
    number, and the check says where it is set."""
    _jde_on(store)
    cascade = _cascade(store)
    page = client.get(f"/review/{cascade['id']}").text
    pop = re.search(r'data-vendor-add>(.*?)</details>', page, re.S).group(1)
    assert 'data-new="an8"' in pop and "Its invoices need a person" not in pop
    assert "Supplier address numbers" in _plain(pop)

    bad = client.post(f"/review/{cascade['id']}/vendor", headers=PAGE,
                      data={"name": cascade["vendor_name"], "an8": "V10104"})  # fmt: skip
    assert bad.status_code == 400 and "digits only" in bad.json()["error"]
    assert not store.get_vendor(vendor_key(cascade["vendor_name"]))  # nothing added when refused

    added = client.post(f"/review/{cascade['id']}/vendor", headers=PAGE,
                        data={"name": cascade["vendor_name"], "an8": "40123"}).json()  # fmt: skip
    assert added["ok"] and "40123" in added["toast"]["text"]
    assert jde.load(store).an8_overrides[vendor_key(cascade["vendor_name"])] == "40123"
    sections = json.dumps(check(client, cascade["id"])["sections"])
    assert "JDE_NO_AN8" not in sections and "VENDOR_ADDED_HERE" in sections


def test_a_vendor_with_no_address_number_is_told_where_to_add_it(client, store):
    """B-1297: the JD Edwards check of a vendor with no AN8 names the Settings section that sets it."""
    _jde_on(store)
    cascade = _cascade(store)
    client.post(f"/review/{cascade['id']}/vendor", headers=PAGE, data={"name": cascade["vendor_name"]})
    raw = json.dumps(check(client, cascade["id"])["sections"], ensure_ascii=False)
    sections = _plain(raw)
    assert "JDE_NO_AN8" in raw
    assert "Settings → JD Edwards E1 → Supplier address numbers" in sections and "ask the pilot tester" not in sections


# --- 2. The learning figures without the model -----------------------------------------------------------------------


def test_without_the_model_blanks_filled_in_are_neither_approved_as_coded_nor_corrected(tmp_path, ground_truth):
    """B-1298: with no coding model (every GL account left blank), Learning said "Coding accuracy 0.0%" beside "No
    corrections yet: the AI has matched every reviewer decision", and "Approved as coded 100%" beside "0 of 2 needed
    no change, 2 only had blanks filled in by AP"; Activity said "2 approved as coded, 0 corrected". Blanks filled in
    are their own count on all three pages (D-112)."""
    import copy

    from ap_coder.core.store import Store
    from ap_coder.erp import insights

    from .webclient import client_for

    store = Store(tmp_path / "ap.db")
    for n in (1, 2):
        blank = copy.deepcopy({**ground_truth, "invoice_number": f"X-{n}"})
        for li in blank["line_items"]:
            li["predicted_gl_code"] = "UNASSIGNED"
        invoice_id = store.add_invoice(tmp_path / f"{n}.pdf", blank, {"requires_review": True})
        store.approve_invoice(invoice_id, {**ground_truth, "invoice_number": f"X-{n}"}, "jane")
    assert insights.approved_as_coded(store) == (0, 2)
    page = insights.compute(store)
    assert (page["approved_clean"], page["approved_filled"], page["approved_changed"]) == (0, 2, 0)
    assert page["straight_through"] == 0

    client = client_for(store)
    learning = _plain(client.get("/learning").text)
    assert "Coding accuracy —" in learning and "0.0%" not in learning
    assert "matched every reviewer decision" not in learning and "AP coded every line" in learning
    assert "Approved as coded 0%" in learning and "0 of 2 needed no change, 2 only had blanks filled in by AP" in learning
    activity = _plain(client.get("/activity").text)
    assert "0 approved as coded, 2 had blanks filled in by AP, 0 corrected" in activity


# --- 3. A correction after "Reopen for correction" -------------------------------------------------------------------


def test_a_line_changed_after_a_reopen_is_a_correction_of_what_was_approved_before(tmp_path, ground_truth):
    """B-1299: a line reopened and changed (6110 → 6100) where the AI had proposed nothing was recorded as AP's own
    coding again: "no changes to the suggested coding", "(the AI only guessed)", "Corrections taught" 0. Against what
    was approved before, it is a correction: taught and counted (D-112). A line approved again as it was stays AP's
    own coding."""
    import copy

    from ap_coder.core.memory import CODED, CORRECTED
    from ap_coder.core.store import Store
    from ap_coder.erp import insights

    store = Store(tmp_path / "ap.db")
    blank = copy.deepcopy(ground_truth)
    for li in blank["line_items"]:
        li["predicted_gl_code"] = "UNASSIGNED"
    invoice_id = store.add_invoice(tmp_path / "a.pdf", blank, {"requires_review": True})
    first = copy.deepcopy(ground_truth)
    first["line_items"][0]["predicted_gl_code"] = "6110"
    store.approve_invoice(invoice_id, first, "jane")
    store.reopen(invoice_id, "jane", "line 1 is the wrong account")
    again = copy.deepcopy(first)
    again["line_items"][0]["predicted_gl_code"] = "6100"
    store.approve_invoice(invoice_id, again, "jane")

    rows = {r["line_number"]: r for r in store.feedback_rows() if r["invoice_id"] == invoice_id}
    one = rows[first["line_items"][0]["line_number"]]
    assert (one["outcome"], one["suggested_gl"], one["final_gl"]) == (CORRECTED, "6110", "6100")
    assert all(r["outcome"] == CODED for n, r in rows.items() if n != one["line_number"])
    assert store.metrics()["lines_corrected"] == 1
    assert "line_coding" in store.get_invoice(invoice_id)["edits"]
    assert insights.approved_as_coded(store) == (0, 1) and insights.filled_in_count(store) == 0  # corrected
