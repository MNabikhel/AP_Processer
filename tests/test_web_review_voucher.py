"""The review screen for a clerk entering a JD Edwards voucher (D-75): JD Edwards readiness before approval, what
"Approve anyway" never covers, the checks at their field, the dates and the currency as typed, the one-click notes
under a field, the voucher block, the focus, the keys and the menus (in a browser)."""

from __future__ import annotations

import json
import re

import pytest

from ap_coder.core.store import APPROVED, PENDING, REVIEW
from ap_coder.erp import jde
from ap_coder.web import invoice_view

from . import test_web_invoice as screen
from .test_web_invoice import PAGE, check, clean_invoice, form_of

# The review screen's fixtures, shared with its own tests: the demo invoices, a store, a client, a browser.
demo_db, store, client, me, browser = screen.demo_db, screen.store, screen.client, screen.me, screen.browser


def _id(store, vendor: str) -> int:
    return next(i["id"] for i in store.list_invoices(REVIEW) if vendor in (i["vendor_name"] or ""))


def _jde_on(store, **settings) -> None:
    store.set_setting(jde.SHOW_SETTING, "1")
    if settings:
        jde.save(store, jde.JdeSettings(**settings))


# --- 1. JD Edwards readiness before approval -----------------------------------------------------------------------


def test_with_jd_edwards_in_use_the_screen_says_what_its_batch_would_hold_back(client, store):
    """B-737: the dry run held back every approved voucher (supplier number V10023 is not an AN8, GL 6010 has no
    BU.Object.Subsidiary) while the review screen said nothing and approved them. Now each is a check, at its field."""
    invoice_id = _id(store, "Prairie")
    before = check(client, invoice_id, form_of(store, invoice_id))
    assert "JD Edwards" not in json.dumps(before["sections"]) and before["header"]["an8"] is None  # not in use

    _jde_on(store, bu_from_cost_center=False)  # no business unit for the lines: none from the cost center
    answer = check(client, invoice_id, form_of(store, invoice_id))
    checks = answer["sections"]["checks"]
    assert "JD Edwards: the supplier number V10104 is not an address number" in checks
    assert re.search(r"JD Edwards: GL \d+[^<]* \(lines? [\d, ]+\)[^<]* ha(s|ve) no JDE account", checks)
    assert "is not an address number" in answer["header"]["flags"]["vendor_name"]  # under the vendor
    assert answer["header"]["an8"] == "V10104"
    assert not answer["allow"] and "Approve anyway" not in answer["sections"]["bar"]  # never approved anyway
    assert "Not ready: JD Edwards: the supplier number V10104 is not an address number" in answer["sections"]["bar"]
    # the posting preview names each line's E1 account; the voucher block shows the header in JDE's order
    assert "JDE account" in answer["sections"]["posting"] and "not mapped" in answer["sections"]["posting"]
    block = answer["sections"]["jde"]
    order = [
        block.index(label)
        for label in (
            "Company",
            "Supplier (AN8)",
            "Invoice number",
            "G/L date",
            "Due date",
            "Payment terms",
            "Currency",
            "Gross amount",
            "Tax area",
            "Pay status",
        )
    ]
    assert order == sorted(order) and "/settings/jde" in block
    assert "Not sent: JD Edwards uses the supplier&#39;s own terms" in block

    # The pilot tester sets the AN8 and a default business unit: nothing holds it back, the accounts show.
    vendor = store.get_invoice(invoice_id)["ai_output"]["vendor_name"]
    _jde_on(store, an8_overrides={jde.vendor_key(vendor): "10104"}, default_bu="CC400", bu_from_cost_center=False)
    answer = check(client, invoice_id, form_of(store, invoice_id))
    assert "JD Edwards" not in answer["sections"]["checks"] and answer["header"]["an8"] == "10104"
    assert "CC400.6010" in answer["sections"]["posting"]


def test_an_invoice_approved_on_the_screen_is_never_held_back_at_export(client, store):
    """What the screen allowed, the JD Edwards batch takes: the screen asks jde.validate's questions."""
    _jde_on(store, default_bu="CC400")
    settings = jde.load(store)
    settings.an8_overrides = {v["vendor_key"]: str(10000 + n) for n, v in enumerate(store.vendor_choices())}
    jde.save(store, settings)
    approved = 0
    for inv in store.list_invoices(REVIEW):
        data = form_of(store, inv["id"])
        answer = check(client, inv["id"], data)
        if not answer["allow"]:
            continue
        assert client.post(f"/review/{inv['id']}/approve", data={**data, "why_done": "1"}, headers=PAGE).json()["ok"]
        approved += 1
    assert approved
    done = [store.get_invoice(i["id"]) for i in store.unexported_approved()]
    assert done and jde.validate(done, jde.load(store), store.vendor_ids()) == {}


def test_bulk_approval_asks_the_same_jd_edwards_questions(store):
    """The queue's bulk approval runs the same checks (finalise_coding): a voucher JD Edwards would refuse is left for
    a person."""
    from ap_coder.ai.pipeline import finalise_coding
    from ap_coder.core.config import Settings
    from ap_coder.core.schema import InvoiceCoding

    _jde_on(store, bu_from_cost_center=False)
    inv = store.get_invoice(_id(store, "Prairie"))
    coded = {k: v for k, v in inv["ai_output"].items() if k != "gl_distribution"}
    _, report = finalise_coding(InvoiceCoding.model_validate(coded), store.reference_data(), Settings(), store=store)
    assert {"JDE_BAD_AN8", "JDE_NO_ACCOUNT"} <= {i.code for i in report.issues}


# --- 2. What "Approve anyway" never covers ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "change, said",
    [
        ({"invoice_number": ""}, "No invoice number"),
        ({"invoice_date": "2026-13-45"}, "is not a date"),
        ({"grand_total": "1.00"}, "Subtotal + tax"),
        ({"currency": "XYZ"}, "is not a currency code"),
    ],
)
def test_what_makes_a_voucher_invalid_is_never_approved_anyway(client, store, change, said):
    invoice_id = clean_invoice(client, store)
    data = form_of(store, invoice_id, **change)
    answer = check(client, invoice_id, data)
    assert answer["valid"] and not answer["allow"]  # the checks are all there, Approve closed
    assert 'name="override"' not in answer["sections"]["bar"] and said in answer["sections"]["bar"]
    refused = client.post(f"/review/{invoice_id}/approve", headers=PAGE,
                          data={**data, "override": "x", "override_reason": "trust me", "why_done": "1"})  # fmt: skip
    assert refused.status_code == 400 and refused.json()["error"].startswith("Not ready:")
    assert store.get_invoice(invoice_id)["status"] == REVIEW


def test_the_hard_codes_are_errors_raised_somewhere():
    from ap_coder.ops.help import CHECKS

    assert invoice_view.HARD_CODES <= set(CHECKS)


# --- 3. Errors next to the field ---------------------------------------------------------------------------------


def test_a_bad_date_is_said_at_its_field_and_keeps_every_other_check(client, store):
    """B-738: 2026-13-45 gave "invoice_date: Value error, …" and every other check disappeared."""
    invoice_id = _id(store, "Prairie")
    answer = check(client, invoice_id, form_of(store, invoice_id, invoice_date="2026-13-45", grand_total="1.00"))
    assert answer["valid"] and "Value error" not in json.dumps(answer)
    assert (
        answer["header"]["flags"]["invoice_date"]
        == "“2026-13-45” is not a date: type it as on the page, e.g. 2026-10-05"
    )
    assert "Subtotal + tax" in answer["sections"]["checks"]  # the other checks stay
    assert "grand_total" in answer["header"]["flags"]  # the total is marked at its box too


def test_an_ambiguous_date_asks_which_and_a_clear_one_is_put_as_yyyy_mm_dd(client, store):
    invoice_id = _id(store, "Prairie")
    answer = check(client, invoice_id, form_of(store, invoice_id, invoice_date="12/06/2026"))
    choices = answer["header"]["help"]["invoice_date"]["actions"]
    assert {c["value"] for c in choices} == {"2026-06-12", "2026-12-06"} and not answer["allow"]
    assert "12 June 2026 or 6 December 2026" in answer["header"]["flags"]["invoice_date"]
    answer = check(client, invoice_id, form_of(store, invoice_id, invoice_date="Oct 5, 2026"))
    assert (
        answer["header"]["values"]["invoice_date"] == "2026-10-05" and "invoice_date" not in answer["header"]["flags"]
    )


def test_an_unknown_currency_is_flagged_at_the_currency(client, store):
    invoice_id = _id(store, "Prairie")
    answer = check(client, invoice_id, form_of(store, invoice_id, currency="XYZ"))
    assert answer["header"]["flags"]["currency"].startswith("“XYZ” is not a currency code in use")
    assert "currency" not in check(client, invoice_id, form_of(store, invoice_id, currency="EUR"))["header"]["flags"]


def test_a_check_about_one_field_is_shown_under_it(client, store):
    invoice_id = _id(store, "Prairie")
    answer = check(client, invoice_id, form_of(store, invoice_id, invoice_number=""))
    assert answer["header"]["flags"]["invoice_number"] == "No invoice number: type it from the page"
    page = client.get(f"/review/{invoice_id}").text
    assert 'data-field="invoice_number"' in page


def test_a_problem_that_stops_the_coding_is_said_in_plain_words():
    assert invoice_view.plain_problem("line_items.1.amount: Value error, must be a number") == (
        "Line 2 amount: must be a number"
    )
    assert invoice_view.plain_problem("due_date: Value error, x") == "Due date: x"


def test_the_readings_badge_is_left_out_beside_an_empty_or_changed_field(client, store):
    """An emptied Invoice # still said ✓ Verified; the currency CAD the page doesn't print said "Check 36%"."""
    invoice_id = _id(store, "Prairie")
    inv = store.get_invoice(invoice_id)
    sure = {"invoice_number": {"text": "Verified"}, "po_number": {"text": "Likely"}, "currency": {"text": "Check"}}
    header = {**invoice_view.Draft.stored(inv["ai_output"]).header, "invoice_number": "", "po_number": "X1"}
    shown = invoice_view.shown_sure(sure, header, inv["ai_output"], None)
    assert "invoice_number" not in shown and "po_number" not in shown and "currency" in shown


# --- 8. A missing PO, and a value boxed on the page while the field is empty ----------------------------------------


def test_a_missing_po_offers_the_vendors_open_po_in_one_click(client, store):
    invoice_id = _id(store, "Prairie")
    answer = check(client, invoice_id, form_of(store, invoice_id, po_number=""))
    help_ = answer["header"]["help"]["po_number"]
    assert help_["text"].startswith("No PO on the invoice") and help_["actions"][0]["label"] == "Use PO-89904"
    page = client.get(f"/review/{invoice_id}").text
    assert "data-help-use" in page


# --- 11. After approve: Undo, and the end of the list ----------------------------------------------------------------


def test_the_approval_banner_offers_undo(client, store):
    invoice_id = clean_invoice(client, store)
    answer = client.post(f"/review/{invoice_id}/approve", data={**form_of(store, invoice_id), "why_done": "1"},
                         headers=PAGE).json()  # fmt: skip
    page = client.get(answer["redirect"]).text
    assert f'action="/review/{invoice_id}/undo-approve"' in page
    assert store.get_invoice(invoice_id)["status"] in (APPROVED, PENDING)
    undone = client.post(f"/review/{invoice_id}/undo-approve", headers=PAGE).json()
    assert undone["ok"] and store.get_invoice(invoice_id)["status"] == REVIEW
    assert "Approval undone" in client.get(undone["redirect"]).text
    again = client.post(f"/review/{invoice_id}/undo-approve", headers=PAGE)
    assert again.status_code == 400


def test_a_credit_note_says_so_with_its_amounts_signed(client, store):
    invoice_id = _id(store, "Northwind")
    credit = next(i["id"] for i in store.list_invoices(REVIEW) if (i["grand_total"] or 0) < 0)
    answer = check(client, credit, form_of(store, credit))
    assert "Credit note CN-" in answer["sections"]["summary"] and "(" not in answer["sections"]["summary"].split(
        "rvw-head-total")[1].split("</b>")[0]  # fmt: skip
    assert "-" in answer["sections"]["summary"].split("rvw-head-total")[1]
    assert 'data-credit="1"' in answer["sections"]["bar"]
    assert invoice_id


# --- In a browser ------------------------------------------------------------------------------------------------


def _page(browser, store, vendor, width=1366, height=768):
    chromium, base = browser
    page = chromium.new_page(viewport={"width": width, "height": height})
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.goto(f"{base}/review/{_id(store, vendor)}")
    page.wait_for_selector(".iv-sheet:not(.loading)")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(300)
    return page


def _focus(page) -> dict:
    return page.evaluate("""() => { const a = document.activeElement; const r = a.getBoundingClientRect();
      const bar = document.querySelector('.inv-actionbar').getBoundingClientRect();
      return {name: a.name || a.getAttribute('aria-label') || a.tagName, bottom: r.bottom, barTop: bar.top}; }""")


def test_in_a_browser_the_first_field_to_check_has_the_focus_above_the_approve_bar(browser, store):
    """B-742: on opening, the focus was on the page itself (31 Tabs to the vendor); then it could sit under the
    approve bar. Tab never leaves it there."""
    page = _page(browser, store, "Prairie")
    page.wait_for_function("document.activeElement && document.activeElement.name")
    first = _focus(page)
    assert first["name"] == "pst_registration_number"  # boxed on the page, the field empty
    assert page.evaluate("scrollY") == 0  # focused without scrolling: the header and the checks stay in view (B-792)
    for _ in range(12):  # Tab on: never under the bar
        page.keyboard.press("Tab")
        at = _focus(page)
        if at["name"] in ("BODY", "MAIN"):
            continue
        assert at["bottom"] <= at["barTop"] + 1, at
    assert page.errors == []


def test_in_a_browser_ctrl_z_never_falls_through_to_the_browsers_undo(browser, store):
    """B-739: after AP Coder's one-step undo, each Ctrl+Z took a character off the last box typed in (and kept it)."""
    page = _page(browser, store, "Prairie")
    terms = page.locator("[form=inv][name=payment_terms]")
    terms.fill("Net 30 EOM")
    page.evaluate("document.activeElement.blur()")
    page.wait_for_timeout(600)
    for _ in range(3):
        page.keyboard.press("Control+z")
    assert terms.input_value() == "Net 30 EOM"
    assert "Nothing to undo" in page.inner_text("#toasts")
    assert page.errors == []


def test_in_a_browser_a_line_menu_is_short_with_delete_last_and_esc_gives_the_focus_back(browser, store):
    """B-741: the line menu had 11 items, greyed ones among them, Delete first and highlighted, and it covered the
    approve bar; Esc on a field's name sent the focus to the page."""
    page = _page(browser, store, "Prairie")
    row = page.locator("[data-grid=lines] > tr").first
    row.locator("[name=line_number]").click(button="right")
    page.wait_for_selector(".cm[role=menu]")
    items = page.locator(".cm[role=menu]:not(.cm-sub) > .cm-item")
    ids = [items.nth(i).get_attribute("data-cm") for i in range(items.count())]
    assert ids[-1] == "delete" and ids[0] != "delete" and len(ids) <= 8
    assert page.locator(".cm [aria-disabled=true]").count() == 0
    assert page.locator(".cm-item[data-cm=delete] .cm-hint").inner_text() == "Alt+Del"
    menu = page.locator(".cm[role=menu]").bounding_box()
    bar = page.locator(".inv-actionbar").bounding_box()
    assert menu["y"] + menu["height"] <= bar["y"] + 1
    page.keyboard.press("Escape")
    # a field's name: Esc gives the focus to its box
    page.locator(".inv-details [data-field=invoice_number] .lbl").click(button="right")
    page.wait_for_selector(".cm[role=menu]")
    page.keyboard.press("Escape")
    assert page.evaluate("document.activeElement.name") == "invoice_number"
    assert page.errors == []


def test_in_a_browser_the_lines_are_one_row_each_on_a_wide_screen_and_cards_on_a_laptop(browser, store):
    rows = {}
    for width, height in ((1920, 1080), (1366, 768)):
        page = _page(browser, store, "Prairie", width, height)
        row = page.locator("[data-grid=lines] > tr").first
        desc = row.locator("[name=line_description]").bounding_box()
        gl = row.locator(".c-gl").bounding_box()
        rows[width] = abs(desc["y"] - gl["y"]) < 8  # on one row
        number = row.locator("[name=line_number]")
        number.fill("123")
        assert page.evaluate("(el) => el.scrollWidth <= el.clientWidth + 1", number.element_handle())  # 3 digits fit
        assert page.errors == []
        page.close()
    assert rows == {1920: True, 1366: False}


def test_in_a_browser_one_click_puts_the_open_po_in_and_an_edit_drops_the_badge(browser, store):
    page = _page(browser, store, "Prairie")
    po = page.locator("[form=inv][name=po_number]")
    po.fill("")
    page.wait_for_selector("[data-field=po_number] [data-help]:not([hidden]) [data-help-use]")
    page.locator("[data-field=po_number] [data-help-use]").first.click()
    assert po.input_value() == "PO-89904" and page.evaluate("document.activeElement.name") == "po_number"
    number = page.locator("[data-field=invoice_number]")
    assert number.locator(".inv-sure").is_visible()
    number.locator("input").fill("")
    assert not number.locator(".inv-sure").is_visible()
    page.wait_for_selector("[data-field=invoice_number] [data-flag]:not([hidden])")
    assert "No invoice number" in number.locator("[data-flag]").inner_text()
    assert page.errors == []


def test_in_a_browser_esc_in_the_vendor_list_puts_the_vendor_back(browser, store):
    page = _page(browser, store, "Prairie")
    vendor = page.locator("#vendor-name")
    was = vendor.input_value()
    vendor.click()
    vendor.press("Control+a")
    vendor.type("Pac")
    vendor.press("Escape")
    assert vendor.input_value() == was
    assert page.errors == []


def test_in_a_browser_the_not_ready_message_names_the_reason(browser, store):
    page = _page(browser, store, "Prairie")
    page.locator("[form=inv][name=invoice_number]").fill("")
    page.wait_for_selector("[data-live=bar] .inv-hard")
    page.keyboard.press("Control+Enter")
    page.wait_for_selector("#toasts .flash")
    assert "Not ready to approve: No invoice number" in page.inner_text("#toasts")
    assert page.errors == []


def test_in_a_browser_a_vendor_name_ending_in_s_takes_no_second_s(browser, store):
    """B-740: "The GST/HST number printed is Red River Office Interiors's"."""
    page = _page(browser, store, "Prairie")
    said = page.evaluate("""() => import(document.querySelector('script[src*="invoice.js"]').src)
      .then((m) => [m.possessive('Red River Office Interiors'), m.possessive('Pacific Office Supply Ltd.')])""")
    assert said == ["Red River Office Interiors’", "Pacific Office Supply Ltd.’s"]


def test_in_a_browser_the_tax_checks_difference_column_shows_at_1366(browser, store):
    """B-744: beside the page on a 1366 laptop, the Tax check's Difference column was cut off."""
    page = _page(browser, store, "Red River")
    fits = page.evaluate("""() => { const t = document.querySelector('[data-tax-check]');
      return t.getBoundingClientRect().right <= t.closest('.card').getBoundingClientRect().right + 1; }""")
    assert fits and page.errors == []
