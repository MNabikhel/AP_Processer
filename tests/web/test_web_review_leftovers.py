"""The review screen's leftovers after its sign-off (B-840 on): the Tax check inside its card at 1366 when a sum is off,
the approval's Undo one Shift+Tab from where the next invoice opens (and said to a screen reader), and the queue's
checks seeing a vendor or PO change at once."""

from __future__ import annotations

from ap_coder.coding.bulk import clean_candidates, issues_today
from ap_coder.core.memory import vendor_key
from ap_coder.core.store import APPROVED, PENDING
from tests.web.test_web_review_signoff import _id, _review, browser, demo_db, me, store

__all__ = ["browser", "demo_db", "me", "store"]  # the fixtures, shared


# --- 1. The Tax check at 1366 (B-840) -------------------------------------------------------------------------------


def test_in_a_browser_the_tax_check_stays_in_its_card_at_1366_when_the_total_is_off(browser, store):
    """B-840: at 1366, with the total 10.00 off ("Subtotal + tax = total" to check), the Tax check ran 12 px past its
    card: the header "Lines × rate = expected" and the sum's label never wrapped."""
    for vendor in ("Red River", "Agence"):
        page = _review(browser, store, vendor, 1366, 768)
        total = page.locator("[form=inv][name=grand_total]")
        total.fill(str(round(float(total.input_value()) + 10, 2)))
        total.press("Tab")
        page.wait_for_selector("[data-tax-check] tr[data-tax-row=total].is-check")
        room = page.evaluate("""() => { const t = document.querySelector('[data-tax-check]');
            const wrap = t.closest('.table-wrap');
            return {scroll: wrap.scrollWidth - wrap.clientWidth,
                    out: t.getBoundingClientRect().right - t.closest('.card').getBoundingClientRect().right}; }""")
        assert room["scroll"] <= 0 and room["out"] <= 1, (vendor, room)
        assert page.errors == []
        page.close()


# --- 2. The approval's Undo from the next invoice (B-841) -----------------------------------------------------------


def test_in_a_browser_the_approvals_undo_is_one_shift_tab_away_and_said(browser, store):
    """B-841: after approving, the banner's Undo was 59 Shift+Tabs back from the field the next invoice opened on, and
    a screen reader was not told it was there (a status shown with the page is not read out)."""
    invoice_id = _id(store, "Pacific")
    page = _review(browser, store, "Pacific")
    page.keyboard.press("Control+Enter")
    page.wait_for_selector(".flash form[action$='/undo-approve']")
    page.wait_for_load_state("networkidle")
    page.wait_for_function("document.activeElement && document.activeElement.dataset.autofocused === '1'")
    assert store.get_invoice(invoice_id)["status"] in (APPROVED, PENDING)
    page.evaluate("window.openedOn = document.activeElement")
    page.keyboard.press("Shift+Tab")
    assert page.evaluate("!!document.activeElement.closest('form.flash-undo')")
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement === window.openedOn")  # and back to where the work is
    said = page.evaluate("""() => [...document.querySelectorAll('[aria-live]')].map((e) => e.textContent).join(' ')""")
    assert "Undo" in said and "Ctrl+Z" in said, said
    assert page.errors == []


# --- 3. The queue's checks follow the vendor master and the POs (B-842) ---------------------------------------------


def test_a_vendor_put_on_hold_is_seen_by_the_queues_checks_at_once(store):
    """B-842: the queue re-checks an invoice when what its checks read changed (D-88), but the vendor master and the
    POs were not in that fingerprint: a vendor put on hold still looked clean in the queue."""
    candidate = clean_candidates(store)[0]
    assert issues_today(store, [candidate["id"]]) == {}  # nothing changed: the stored checks stand
    name = candidate["vendor_name"]
    store.save_vendor(vendor_key(name), name, "on_hold")
    today = issues_today(store, [candidate["id"]])
    assert "VENDOR_ON_HOLD" in {i["code"] for i in today.get(candidate["id"], [])}


def test_our_bank_accounts_are_in_the_checks_stamp(store):
    """The checks read our bank accounts (the account an invoice is paid from, D-66), but a change to them did not
    change the checks stamp: the queue kept the findings made against the old accounts (B-958)."""
    first = store.checks_stamp()
    row = {"code": "OPS-CAD", "name": "Operating", "currency": "CAD", "institution": "", "transit": "",
           "account_number": "", "gl_account": "", "is_default": 1, "active": 1}  # fmt: skip
    store.save_bank_accounts([row])
    second = store.checks_stamp()
    store.save_bank_accounts([{**row, "active": 0}])
    assert len({first, second, store.checks_stamp()}) == 3


def test_each_po_change_changes_the_checks_stamp_even_in_the_same_second(store):
    """B-842: a PO imported, then its received quantity changed a moment later: each is a change the checks read."""
    row = {"po_number": "PO-77001", "vendor_name": "Pacific Office Supply Ltd.", "line_number": 1,
           "description": "Toner", "quantity": 4, "unit_price": 50.0, "amount": 200.0, "received_quantity": 0,
           "gl_code": "", "cost_center": "", "status": "open"}  # fmt: skip
    first = store.checks_stamp()
    store.import_purchase_orders([row])
    second = store.checks_stamp()
    store.import_purchase_orders([{**row, "received_quantity": 4}])
    third = store.checks_stamp()
    store.save_vendor(vendor_key("Pacific Office Supply Ltd."), "Pacific Office Supply Ltd.",
                      expected_gst="123456782 RT0001")  # fmt: skip
    assert len({first, second, third, store.checks_stamp()}) == 4
