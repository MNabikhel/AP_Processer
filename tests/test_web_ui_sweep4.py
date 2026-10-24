"""The dashboard's fourth UI sweep (B-990 on): the second approver sees why it is needed and what was approved over,
the screen's shortcuts work from the box the page focused, drafts in the queue, plain words for codes and settings,
and the small things."""

from __future__ import annotations

import re

from ap_coder.core.store import PENDING, REVIEW

from .test_web_invoice import clean_invoice
from .test_web_review_signoff import _id, _review, browser, client, demo_db, me, store

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


# --- 2. The screen's shortcuts from the box the page focused ---------------------------------------------------------


def _autofocused(page) -> str:
    page.wait_for_function("['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement?.tagName)")
    return page.evaluate("document.activeElement.name || document.activeElement.className")


def test_in_a_browser_ctrl_z_undoes_the_approval_from_the_box_the_next_invoice_focused(browser, store):
    """B-992: after an approval the next invoice focused its Vendor box, and from there Ctrl+Z did nothing though the
    banner said "Undo Ctrl+Z"; the box sat half under the approve bar at 1366."""
    invoice_id = _id(store, "Pacific")
    page = _review(browser, store, "Pacific")
    page.keyboard.press("Control+Enter")
    page.wait_for_selector(".flash form[action$='/undo-approve']")
    page.wait_for_load_state("networkidle")
    _autofocused(page)
    page.wait_for_timeout(300)
    at = page.evaluate("""() => [document.activeElement.getBoundingClientRect().top,
      document.activeElement.getBoundingClientRect().bottom,
      document.querySelector('.inv-actionbar').getBoundingClientRect().top]""")
    assert at[1] <= at[2] + 1 or at[0] >= at[2], at  # in view above the bar, or wholly below it (B-792)
    page.keyboard.press("Control+z")
    page.wait_for_url(re.compile(rf"/review/{invoice_id}\b"), timeout=10000)
    page.wait_for_selector(".flash")
    assert store.get_invoice(invoice_id)["status"] == REVIEW
    assert page.errors == []


def test_in_a_browser_ctrl_z_in_a_box_typed_in_stays_the_boxs_own(browser, store):
    """B-992: once something is typed in the box, Ctrl+Z is the box's own undo, never the approval's."""
    page = _review(browser, store, "Pacific")
    page.keyboard.press("Control+Enter")
    page.wait_for_selector(".flash form[action$='/undo-approve']")
    page.wait_for_load_state("networkidle")
    _autofocused(page)
    url = page.url
    page.keyboard.type("x")
    page.keyboard.press("Control+z")
    page.wait_for_timeout(800)
    assert page.url == url and page.locator(".flash form[action$='/undo-approve']").count()
    assert page.errors == []


def test_in_a_browser_alt_arrows_work_from_the_box_the_page_focused(browser, store):
    """B-993: Alt+← / Alt+→ / Alt+↑ did nothing from a text box, where the page puts the focus on opening."""
    page = _review(browser, store, "Pacific")
    _autofocused(page)
    page.keyboard.press("Alt+ArrowUp")
    page.wait_for_url(re.compile(r"/review(\?.*)?$"), timeout=10000)
    page.close()
    page = _review(browser, store, "Pacific")
    _autofocused(page)
    before = page.url
    page.keyboard.press("Alt+ArrowRight")
    page.wait_for_function(f"location.href !== {before!r}", timeout=10000)
    assert re.search(r"/review/\d+", page.url)
    assert page.errors == []
