"""The review screen's line tax codes (D-64): one dropdown a line, built for the place of supply, the Tax check under
the lines, the lines it points at drawn yellow, and the old tax boxes still read. The browser tests at the end drive
the page."""

from __future__ import annotations

import pytest

from ap_coder.core.store import REVIEW
from ap_coder.web import invoice_view
from tests.browser import wait_until
from tests.web import test_web_ctxmenu as ctxmenu
from tests.web.test_web_ctxmenu import _item, _menu, _review, _taxable
from tests.web.test_web_invoice import check, form_of

# The review screen of the demo invoices, in a browser too: the fixtures of the right-click menus' tests.
demo_db, store, client, browser, me = ctxmenu.demo_db, ctxmenu.store, ctxmenu.client, ctxmenu.browser, ctxmenu.me

RED_RIVER = "Red River"


def _invoice(store, name: str) -> int:
    return next(i["id"] for i in store.list_invoices(REVIEW) if name in (i["vendor_name"] or ""))


# --- On the server ---------------------------------------------------------------------------------------------------


def test_the_dropdown_offers_the_codes_of_the_place_of_supply(client, store):
    page = client.get(f"/review/{_invoice(store, RED_RIVER)}").text
    assert page.count('name="line_tax_code"') == 4 + 1  # each line, and the template of a new one
    for label in ("GST + RST", "GST only · RST exempt", "GST only · RST not charged (self-assess)",
                  "RST only · GST exempt", "Exempt from both", "Zero-rated", "No tax (out of scope)"):  # fmt: skip
        assert f">{label}</option>" in page, label
    # the design service: RST charged on the other lines, not on it: exempt from RST
    assert '<option value="GST~PST" selected>GST only · RST exempt</option>' in page
    assert "Tax check" in page and "Lines × rate = expected" in page


def test_a_code_posted_is_read_as_taxes_and_a_status(client, store):
    invoice_id = _invoice(store, RED_RIVER)
    data = form_of(store, invoice_id)
    data[invoice_view.LINE_TAX_CODE] = ["GST+PST", "GST+PST", "EXEMPT", "GST+PST"]
    answer = check(client, invoice_id, data)
    lines = client.app.state.invoice_drafts[invoice_id]["lines"]
    assert [(r["taxes_applied"], r["tax_status"]) for r in lines] == [
        (["GST", "PST"], ""), (["GST", "PST"], ""), ([], "exempt"), (["GST", "PST"], "")
    ]  # fmt: skip
    # The GST charged is on every line: the exempt design service is the line that makes it add up.
    assert "EXEMPT_LINE_TAXED" in answer["sections"]["checks"]
    assert "exempt" in answer["tax_marks"]["3"]


def test_the_old_tax_boxes_are_still_read():
    """A page of before (or a helper) posts each line's ticked taxes as line_tax_<key> and no tax code."""
    from starlette.datastructures import FormData

    base = invoice_view.Draft(header={}, lines=[], taxes=[])
    form = FormData([("grids", "1"), ("line_key", "r0"), ("line_key", "n1"), ("line_description", "Chairs"),
                     ("line_description", "Delivery"), ("line_amount", "100"), ("line_amount", "20"),
                     ("line_tax_r0", "GST"), ("line_tax_r0", "PST"), ("line_tax_n1", "GST")])  # fmt: skip
    draft = invoice_view.Draft.from_form(form, base)
    assert [(r["taxes_applied"], r["tax_status"]) for r in draft.lines] == [(["GST", "PST"], ""), (["GST"], "")]
    # The new page: one code a row, in order; an unknown code falls back to the boxes (none here: no tax).
    form = FormData([("grids", "1"), ("line_key", "r0"), ("line_key", "r1"), ("line_amount", "100"),
                     ("line_amount", "20"), ("line_tax_code", "ZERO"), ("line_tax_code", "FOO")])  # fmt: skip
    draft = invoice_view.Draft.from_form(form, base)
    assert [(r["taxes_applied"], r["tax_status"]) for r in draft.lines] == [([], "zero_rated"), ([], "")]


def test_a_split_and_a_line_from_the_draft_keep_their_tax_code(client, store):
    invoice_id = _invoice(store, RED_RIVER)
    data = form_of(store, invoice_id)
    data[invoice_view.LINE_TAX_CODE][2] = "EXEMPT"
    check(client, invoice_id, data)
    draft = invoice_view.Draft(header={}, lines=client.app.state.invoice_drafts[invoice_id]["lines"], taxes=[])
    rows = invoice_view.split(draft, 3, [("6110", "", 50.0), ("6120", "", 50.0)], has_cc=False)
    parts = [r for r in rows if str(r["description"]).startswith("Space planning")]
    assert len(parts) == 2 and all(r["tax_status"] == "exempt" and r["taxes_applied"] == [] for r in parts)


# --- In a browser ----------------------------------------------------------------------------------------------------


def _codes(page):
    return page.eval_on_selector_all("[data-grid=lines] select[data-tax-code]", "s => s.map(x => x.value)")


def _amount(page, kind):
    return page.eval_on_selector_all(
        "[data-grid=taxes] > tr",
        "(rows, k) => rows.filter(r => r.querySelector('[name=tax_type]').value === k)"
        ".map(r => r.querySelector('[name=tax_amount]').value)[0]",
        kind,
    )


def test_in_a_browser_a_lines_tax_code_moves_the_taxable_never_the_tax_charged_b700(browser, store):
    """B-700: changing a line's taxes recalculated the tax line's amount too, so what the invoice charges changed with
    the clerk's marking and the check could never disagree."""
    page, errors = _review(browser, _invoice(store, RED_RIVER))
    assert _codes(page) == ["GST+PST", "GST+PST", "GST~PST", "GST+PST"]
    assert _taxable(page, "PST") == "13445.00" and _amount(page, "PST") == "941.15"
    select = page.locator("[data-grid=lines] select[data-tax-code]").nth(2)
    with page.expect_response(lambda r: r.url.endswith("/check")):
        select.select_option("GST+PST")  # the design service marked RST too: it isn't, by what is charged
    page.wait_for_load_state("networkidle")
    assert _taxable(page, "PST") == "14645.00"  # the taxable follows the lines marked (B-610)
    assert _amount(page, "PST") == "941.15"  # what the invoice charges stays
    row = page.locator("[data-tax-check] tr[data-tax-row=PST]")
    assert "is-check" in (row.get_attribute("class") or "") and "84.00" in row.inner_text()
    cell = page.locator("[data-grid=lines] .c-tax").nth(2)
    assert "is-taxcheck" in (cell.get_attribute("class") or "")  # the line to look at, yellow
    assert "RST" in (cell.get_attribute("title") or "")
    assert errors == []


def test_in_a_browser_the_tax_check_follows_the_codes_and_undo_puts_a_code_back(browser, store):
    page, errors = _review(browser, _invoice(store, RED_RIVER))
    options = page.eval_on_selector_all("[data-grid=lines] select[data-tax-code] >> nth=0 >> option",
                                        "o => o.map(x => x.textContent)")  # fmt: skip
    assert options[:3] == ["GST + RST", "GST only · RST exempt", "GST only · RST not charged (self-assess)"]
    assert "all adds up" in page.locator("[data-live=taxcheck]").inner_text().lower()
    line = page.locator("[data-grid=lines] > tr").nth(3)
    _menu(page, line.locator("[name=line_description]"))
    _item(page, "tax").click()  # the line's tax code items, in their submenu (B-741)
    _item(page, "tax-exempt").click()  # the delivery marked exempt: GST and RST no longer add up
    page.wait_for_selector("[data-tax-check] tr.is-check")
    assert _codes(page)[3] == "EXEMPT"
    text = page.locator("[data-live=taxcheck]").inner_text()
    assert "Line 4 (Delivery charge, 285.00)" in text
    assert page.locator("[data-tax-check] tr.is-check").count() >= 1
    assert page.locator("[data-grid=lines] .c-tax.is-taxcheck").count() >= 1
    page.keyboard.press("Control+z")
    page.wait_for_selector("[data-tax-check] tr.is-check", state="detached")
    assert _codes(page)[3] == "GST+PST"
    assert "all adds up" in page.locator("[data-live=taxcheck]").inner_text().lower()
    assert page.locator("[data-grid=lines] .c-tax.is-taxcheck").count() == 0
    assert errors == []


@pytest.mark.parametrize("province,first", [("ON", "HST"), ("QC", "GST + QST"), ("AB", "GST")])
def test_in_a_browser_the_dropdown_follows_the_place_of_supply(browser, store, province, first):
    page, errors = _review(browser, _invoice(store, RED_RIVER))
    details = page.locator(".inv-supply-pick")
    if not details.evaluate("d => d.open"):
        details.locator("summary").click()
    with page.expect_response(lambda r: r.url.endswith("/check")):
        page.locator('select[name="ship_to_province"]').select_option(province)
    page.wait_for_load_state("networkidle")

    def options():
        return page.eval_on_selector_all("[data-grid=lines] select[data-tax-code] >> nth=0 >> option",
                                         "o => o.map(x => x.textContent)")  # fmt: skip

    wait_until(page, lambda: options()[0] == first)  # the grid is drawn again after the check's answer
    assert options()[0] == first, options()
    assert _codes(page)[0] == "GST+PST"  # each line keeps its own code, listed or not
    assert errors == []


def test_a_worked_out_code_is_marked_until_its_note_goes_b713(client, store):
    """A code worked out to check is marked on the line until the clerk picks or confirms it (the page takes the note
    out of the line's reasoning, posted with it)."""
    from ap_coder.coding.line_tax import GUESS_NOTE

    invoice_id = _invoice(store, RED_RIVER)
    inv = store.get_invoice(invoice_id)
    ai = inv["ai_output"]
    ai["line_items"][2]["reasoning_justification"] = f"Design. {GUESS_NOTE}"
    reference = store.reference_data()
    draft = invoice_view.Draft.stored(ai)
    assert [r["tax_guess"] for r in invoice_view.grid(reference, draft, ai)["lines"]] == [False, False, True, False]
    draft.lines[2]["reasoning_justification"] = "Design."  # confirmed: the note is gone from what is posted
    assert not any(r["tax_guess"] for r in invoice_view.grid(reference, draft, ai)["lines"])


def test_approval_waits_until_an_inferred_pst_exemption_is_confirmed_b715(client, store):
    from ap_coder.coding.line_tax import confirm_line, inferred_note
    from tests.web.test_web_ctxmenu import _set_output

    invoice_id = _invoice(store, RED_RIVER)
    output = store.get_invoice(invoice_id)["ai_output"]
    output["line_items"][2]["reasoning_justification"] = f"Design. {inferred_note('RST')}"
    _set_output(store, invoice_id, output)
    data = form_of(store, invoice_id)
    answer = check(client, invoice_id, data)
    assert (
        not answer["allow"]
        and "Confirm the RST on line 3: exempt, or not charged (self-assess)?" in (answer["sections"]["bar"])
    )
    assert "RST exemption inferred" in answer["sections"]["checks"]
    # confirmed on the page (the line's reasoning posted without the note): approval opens, the confirmation kept
    data[invoice_view.LINE_INPUTS["reasoning_justification"]][2] = confirm_line(
        data[invoice_view.LINE_INPUTS["reasoning_justification"]][2], "GST~PST"
    )
    answer = check(client, invoice_id, data)
    assert "Confirm the RST" not in answer["sections"]["bar"]
    assert (
        "RST exemption confirmed on review"
        in client.app.state.invoice_drafts[invoice_id]["lines"][2]["reasoning_justification"]
    )


def test_in_a_browser_confirm_the_tax_code_clears_an_inferred_exemption_b715(browser, store):
    from ap_coder.coding.line_tax import inferred_note
    from tests.web.test_web_ctxmenu import _set_output

    invoice_id = _invoice(store, RED_RIVER)
    output = store.get_invoice(invoice_id)["ai_output"]
    output["line_items"][2]["reasoning_justification"] = f"Design. {inferred_note('RST')}"
    _set_output(store, invoice_id, output)
    page, errors = _review(browser, invoice_id)
    assert "Confirm the RST on line 3" in page.locator("[data-live=bar]").inner_text()
    line = page.locator("[data-grid=lines] > tr").nth(2)
    _menu(page, line.locator("[name=line_description]"))
    _item(page, "tax-confirm").click()
    page.wait_for_function("!document.querySelector('[data-live=bar]').innerText.includes('Confirm the RST')")
    reason = line.locator("[name=line_reason]").input_value()
    assert "RST exemption confirmed on review" in reason and "inferred" not in reason
    assert errors == []


def test_in_a_browser_picking_the_inferred_exemption_again_confirms_it_b723(browser, store):
    from ap_coder.coding.line_tax import inferred_note
    from tests.web.test_web_ctxmenu import _set_output

    invoice_id = _invoice(store, RED_RIVER)
    output = store.get_invoice(invoice_id)["ai_output"]
    output["line_items"][2]["reasoning_justification"] = f"Design. {inferred_note('RST')}"
    _set_output(store, invoice_id, output)
    page, errors = _review(browser, invoice_id)
    line = page.locator("[data-grid=lines] > tr").nth(2)
    assert line.get_attribute("data-tax-inferred") == "RST"  # kept from the invoice as read
    select = line.locator("select[data-tax-code]")
    select.select_option("GST")  # RST not charged: the note goes
    assert "RST exemption" not in line.locator("[name=line_reason]").input_value()
    select.select_option("GST~PST")  # and back to exempt: an explicit pick, confirmed
    reason = line.locator("[name=line_reason]").input_value()
    assert "RST exemption confirmed on review" in reason and "inferred" not in reason
    assert errors == []
