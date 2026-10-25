"""The fixes from an end-to-end walk of AP Coder on a laptop (B-1180 on): the demo with JD Edwards on, a rejected
invoice reopened, a duplicate pair after a reject, the vendors that make work, the email import counts, who to ask about
JD Edwards, the CSV exports, the approval limit, the review screen, the new vendor form and the setup report."""

from __future__ import annotations

import re

import pytest

from ap_coder.core.store import REVIEW
from ap_coder.erp import jde

from .test_web_review_signoff import _jde_on, browser, client, demo_db, me, store

__all__ = ["browser", "client", "demo_db", "me", "store"]  # the fixtures, shared


def _plain(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


# --- L1. The demo with JD Edwards on -------------------------------------------------------------------------------


def test_the_demo_vendors_have_jd_edwards_address_numbers(client, store):
    """B-1180: ticking "Show JD Edwards settings" blocked every demo invoice: "supplier number V10104 is not an address
    number". The demo vendor master's numbers are JD Edwards address numbers; About says the checks come with it."""
    _jde_on(store)
    settings = jde.load(store)
    for inv in store.list_invoices(REVIEW):
        output = store.get_invoice(inv["id"])["ai_output"]
        codes = [f[1] for f in jde.voucher_findings(output, settings, jde.resolve_an8(output, store.vendor_ids(),
                                                                                       settings))]  # fmt: skip
        assert "JDE_BAD_AN8" not in codes, inv["vendor_name"]
    about = _plain(client.get("/settings/about").text)
    assert "adds the JD Edwards checks to every invoice" in about


# --- L2. A rejected invoice reopened -------------------------------------------------------------------------------


def test_a_rejected_invoice_reopened_speaks_of_the_rejection_and_its_reason(client, store):
    """B-1181: reopening a rejected invoice said "Reopened by X / no reason given. The approved coding is kept below":
    it was never approved, and the rejection's reason was not shown."""
    from ap_coder.core.audit import describe

    invoice_id = store.list_invoices(REVIEW)[0]["id"]
    store.reject_invoice(invoice_id, "Sam Reviewer", "Duplicate of NW-2026-0912")
    store.reopen(invoice_id, "Jordan Lee", only_rejected=True)
    page = _plain(client.get(f"/review/{invoice_id}").text)
    assert "Rejected by Sam Reviewer, reopened by Jordan Lee" in page
    assert "Duplicate of NW-2026-0912" in page and "no reason given" not in page
    assert "approved coding is kept" not in page
    reopened = next(e for e in store.events(invoice_id) if e["action"] == "reopened")
    assert describe(reopened) == "rejection undone, back to the review queue · rejected for: Duplicate of NW-2026-0912"


# --- L3. A duplicate pair after a reject or a delete ---------------------------------------------------------------


@pytest.mark.parametrize("how", ["reject", "delete"])
def test_rejecting_one_of_a_duplicate_pair_checks_the_other_again(store, tmp_path, how):
    """B-1182: rejecting one invoice of a duplicate pair left the other "Needs attention" (its DUPLICATE_INVOICE
    stored) and out of bulk approval until it was opened."""
    from ap_coder.coding.bulk import clean_candidates

    first = clean_candidates(store)[0]
    inv = store.get_invoice(first["id"])
    copy = store.add_invoice(tmp_path / "copy.pdf", inv["ai_output"], {**inv["validation"], "requires_review": True})
    for this, other in ((first["id"], copy), (copy, first["id"])):
        said = f"same vendor and invoice number as invoice #{other}: possible duplicate payment"
        flag = {"severity": "error", "code": "DUPLICATE_INVOICE", "line_number": None, "message": said}
        assert store.update_after_check(this, None, flag, ("DUPLICATE_INVOICE",))
    assert first["id"] not in [c["id"] for c in clean_candidates(store)]

    if how == "reject":
        store.reject_invoice(copy, "Sam Reviewer", "Sent twice")
    else:
        store.delete_invoice(copy, actor="Sam Reviewer")
    left = store.get_invoice(first["id"])
    assert "DUPLICATE_INVOICE" not in [i["code"] for i in left["validation"]["issues"]]
    assert not left["requires_review"] and first["id"] in [c["id"] for c in clean_candidates(store)]
