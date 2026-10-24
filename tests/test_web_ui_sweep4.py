"""The dashboard's fourth UI sweep (B-990 on): the second approver sees why it is needed and what was approved over,
the screen's shortcuts work from the box the page focused, drafts in the queue, plain words for codes and settings,
and the small things."""

from __future__ import annotations

import re

from ap_coder.core.store import PENDING

from .test_web_invoice import clean_invoice
from .test_web_review_signoff import browser, client, demo_db, me, store

__all__ = ["browser", "client", "demo_db", "me", "store"]  # the fixtures, shared


def _waiting_for_second(store, client, currency: str = "USD") -> int:
    """An invoice approved over two errors (each with its reason), waiting for a second approval because there is no
    CAD rate for its currency."""
    store.set_setting("approval_limit", "15000")
    invoice_id = clean_invoice(client, store)
    inv = store.get_invoice(invoice_id)
    output = dict(inv["final_output"] or inv["ai_output"], currency=currency)
    issues = [
        {"code": "VENDOR_NOT_IN_MASTER", "severity": "error", "line": None, "override_reason": "US vendor, set up next week"},  # noqa: E501
        {"code": "TAX_CALC_MISMATCH", "severity": "error", "line": None, "override_reason": "Credit promised by email"},
        {"code": "TAX_NON_CANADIAN", "severity": "warning", "line": None},
    ]  # fmt: skip
    store.approve_invoice(invoice_id, output, "Dana Clerk", open_issues=issues, login="dclerk")
    assert store.get_invoice(invoice_id)["status"] == PENDING
    return invoice_id


# --- 1. The second approver sees why, and what was approved over --------------------------------------------------


def test_the_second_approval_says_the_stored_reason_and_each_error_approved_over(client, store):
    """B-990: a USD invoice with no CAD rate said "Invoices over 15,000.00 CAD need a second approver", and the
    reasons typed for "Approve anyway" were shown nowhere."""
    invoice_id = _waiting_for_second(store, client)
    for path in (f"/review/{invoice_id}", "/review?tab=second"):
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", client.get(path).text))
        assert "No CAD exchange rate for USD" in text, path
        assert "Invoices over 15,000.00 CAD need" not in text, path
        assert "Not in the vendor master" in text and "US vendor, set up next week" in text, path
        assert "Tax miscalculated" in text and "Credit promised by email" in text, path
        assert "VENDOR_NOT_IN_MASTER" not in text and "TAX_CALC_MISMATCH" not in text, path


def test_activity_says_the_second_approval_reason_and_the_errors_approved_over_in_plain_words(client, store):
    """B-991: Activity said "approved despite TAX_CALC_MISMATCH" and "over the approval limit" for a missing rate."""
    _waiting_for_second(store, client)
    text = client.get("/activity").text
    assert "approved despite TAX" not in text and "TAX_CALC_MISMATCH" not in text and "VENDOR_NOT_IN_MASTER" not in text
    assert "Approved over: Not in the vendor master (US vendor, set up next week)" in text
    assert "no CAD exchange rate for USD" in text and "over the approval limit" not in text
