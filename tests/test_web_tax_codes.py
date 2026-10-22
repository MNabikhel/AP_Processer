"""The review screen's line tax codes (D-64): one dropdown a line, built for the place of supply, the Tax check under
the lines, the lines it points at drawn yellow, and the old tax boxes still read. The browser tests at the end drive
the page."""

from __future__ import annotations

import pytest

from ap_coder.core.store import REVIEW
from ap_coder.web import invoice_view

from .test_web_ctxmenu import _checked, _item, _menu, _review, _taxable, browser, client, demo_db, me, store  # noqa: F401
from .test_web_invoice import check, form_of

RED_RIVER = "Red River"


def _invoice(store, name: str) -> int:
    return next(i["id"] for i in store.list_invoices(REVIEW) if name in (i["vendor_name"] or ""))


# --- On the server ---------------------------------------------------------------------------------------------------


def test_the_dropdown_offers_the_codes_of_the_place_of_supply(client, store):
    page = client.get(f"/review/{_invoice(store, RED_RIVER)}").text
    assert page.count('name="line_tax_code"') == 4 + 1  # each line, and the template of a new one
    for label in ("GST + RST", "GST only", "RST only", "Exempt", "Zero-rated", "No tax (out of scope)",
                  "No tax charged"):  # fmt: skip
        assert f">{label}</option>" in page, label
    assert '<option value="GST" selected>GST only</option>' in page  # the design service: no RST
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
    assert _codes(page) == ["GST+PST", "GST+PST", "GST", "GST+PST"]
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
    assert options[:3] == ["GST + RST", "GST only", "RST only"] and "Exempt" in options
    assert "all adds up" in page.locator("[data-live=taxcheck]").inner_text().lower()
    line = page.locator("[data-grid=lines] > tr").nth(3)
    _menu(page, line.locator("[name=line_description]"))
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
    options = page.eval_on_selector_all("[data-grid=lines] select[data-tax-code] >> nth=0 >> option",
                                        "o => o.map(x => x.textContent)")  # fmt: skip
    assert options[0] == first, options
    assert _codes(page)[0] == "GST+PST"  # each line keeps its own code, listed or not
    assert errors == []
