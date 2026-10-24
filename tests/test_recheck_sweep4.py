"""The fourth sweep's re-check (B-1020 on): the tax rows following the place of supply on screen, the vendor found by
its GST number, the GL posting preview and pilot-score in the G/L tax mode, the stored second-approval reason, one tax
row for one printed tax, the tip after the tax, Ready as bulk approval means it, rates in percent, JDE's Tax ID
column, and the setup pages kept to this computer."""

from __future__ import annotations

from .test_web_invoice import check, form_of
from .test_web_review_signoff import _id, _review, browser, client, demo_db, me, store

__all__ = ["browser", "client", "demo_db", "me", "store"]  # the fixtures, shared


# --- 1. The tax rows follow the place of supply on screen ----------------------------------------------------------


def test_the_check_sends_back_the_tax_rows_moved_to_the_new_place_of_supply(client, store):
    invoice_id = _id(store, "Pacific")
    answer = check(client, invoice_id, form_of(store, invoice_id, ship_to_province="MB"))
    assert [t["province"] for t in answer["taxes"]] == ["", "MB"]  # PST BC 7% is Manitoba's RST 7% now


def test_in_a_browser_the_tax_row_follows_the_place_of_supply_there_and_back(browser, store):
    """B-1020: the tax row kept saying PST BC once the place of supply was MB (PST MB after a reload), and back to BC
    the page posted PST MB again, so the invoice was saved with PST MB on a BC supply."""
    page = _review(browser, store, "Pacific")
    province = page.locator('[data-grid="taxes"] tr').nth(1).locator("[name=tax_province]")
    assert province.input_value() == "BC"
    details = page.locator(".inv-supply-pick")
    if not details.evaluate("d => d.open"):
        details.locator("summary").click()
    with page.expect_response(lambda r: r.url.endswith("/check")):
        page.select_option("[name=ship_to_province]", "MB")
    page.wait_for_function("""() => document.querySelectorAll('[data-grid="taxes"] [name=tax_province]')[1].value
        === 'MB'""")
    with page.expect_response(lambda r: r.url.endswith("/check")):
        page.select_option("[name=ship_to_province]", "BC")
    page.wait_for_function("""() => document.querySelectorAll('[data-grid="taxes"] [name=tax_province]')[1].value
        === 'BC'""")
    assert not page.errors
