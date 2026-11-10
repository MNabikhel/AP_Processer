"""The Account box's list and the review screen's keys in a real browser (D-214), on the made-up company (the
``chart_company`` fixture): the groups a box opens on, the search (digits without dots, a business unit's name with an
account chosen, 50 rows drawn at most), a string the chart does not have, and approving with Ctrl+Enter."""

import pytest

from ap_coder.core import chart
from ap_coder.core.store import APPROVED, REVIEW
from ap_coder.web.app import create_app
from tests.browser import browser_on, wait_until
from tests.webclient import web_settings

pytestmark = pytest.mark.slow

FIRST = '[data-grid="lines"] [data-row]:nth-child(1) input[data-account]'
LIST = "[data-picker-layer]:not([hidden])"


@pytest.fixture
def browser(chromium, chart_company):
    with browser_on(chromium, create_app(web_settings(), chart_company.store, start_reader=False)) as opened:
        yield opened


def _review(browser, chart_company, name: str):
    chromium, base = browser
    page = chromium.new_page(viewport={"width": 1366, "height": 768})
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    with page.expect_response(lambda r: "/review/accounts.json" in r.url):  # the list, fetched as the screen opens
        page.goto(f"{base}/review/{chart_company.invoices[name][1]}")
    page.wait_for_load_state("networkidle")  # the opening check has answered
    return page, errors


def _options(page) -> list[str]:
    return page.eval_on_selector_all(f"{LIST} .pk-option .pk-code", "els => els.map((e) => e.textContent)")


def _groups(page) -> list[str]:
    return page.eval_on_selector_all(f"{LIST} .pk-group", "els => els.map((e) => e.textContent)")


def test_an_empty_box_opens_on_the_lines_candidates_and_typing_digits_without_dots_finds_the_account(
    browser, chart_company
):
    invoice, invoice_id = chart_company.invoices["four_lines"]
    page, errors = _review(browser, chart_company, "four_lines")
    proposed = chart_company.store.get_invoice(invoice_id)["ai_output"]["line_items"][0]
    page.click(FIRST)
    page.wait_for_selector(LIST)
    assert _groups(page)[0] == "Suggested for this line"
    assert _options(page)[: len(proposed["candidates"])] == [c["account"] for c in proposed["candidates"]]
    assert "accounts are searched" in page.inner_text(f"{LIST} .pk-note")
    wanted = invoice.parts[0].account
    unit, code = chart.split_account(wanted)
    page.keyboard.type(code.replace(".", ""))  # "14509309": the object and subsidiary, no dot
    wait_until(page, lambda: bool(_options(page)) and all(o.endswith(code) for o in _options(page)[:1]))
    assert all(option.endswith(f".{code}") for option in _options(page)), _options(page)
    page.keyboard.type(" " + unit[-4:])  # and part of the business unit's number
    wait_until(page, lambda: _options(page)[:1] == [wanted])
    page.keyboard.press("Enter")  # the first match is the one Enter takes
    assert page.input_value(FIRST) == wanted and not page.query_selector(LIST)
    page.wait_for_selector('[data-grid="lines"] [data-row]:nth-child(1):not(.is-fix)')  # checked: no longer to fix
    assert "picked by a person" in page.inner_text('[data-grid="lines"] [data-row]:nth-child(1) .c-cap')
    assert not errors


def test_a_business_units_name_typed_over_an_account_offers_the_same_account_there_first(browser, chart_company):
    page, errors = _review(browser, chart_company, "one_line")
    pairs = chart_company.store.reference_data().pairs
    # an account that two business units of different names have: the line is on one, the other's name is typed
    code, unit, other = next(
        (code, units[0], u) for code in sorted(pairs.offered_codes()) for units in [pairs.bus_of(code)]
        for u in units[1:] if pairs.name_of(u) and pairs.name_of(u) != pairs.name_of(units[0]))  # fmt: skip
    before = chart.join_account(unit, code)
    page.fill(FIRST, before)
    page.keyboard.press("Tab")
    page.click(FIRST)
    page.wait_for_selector(LIST)
    assert page.inner_text(f"{LIST} .pk-option.is-active .pk-code") == before  # open on the box's own value
    page.keyboard.press("Control+a")
    page.keyboard.type(pairs.name_of(other))
    wait_until(page, lambda: _groups(page)[:1] == [f"{code} on the business unit typed"])
    first = _options(page)[0]  # the same account, on a business unit of the name typed
    assert first.endswith(f".{code}") and first != before
    assert pairs.name_of(chart.split_account(first)[0]) == pairs.name_of(other)
    page.keyboard.press("Escape")  # the box is left as typed: never back to the account before without a word
    assert page.input_value(FIRST) == pairs.name_of(other) and not page.query_selector(LIST)
    assert not errors


def test_enter_on_an_untouched_filled_box_changes_nothing_and_at_most_fifty_rows_are_drawn(browser, chart_company):
    page, errors = _review(browser, chart_company, "one_line")
    before = page.input_value(FIRST)
    page.focus(FIRST)
    page.keyboard.press("Enter")  # opens the list on the box's own value
    page.wait_for_selector(f"{LIST} .pk-option.is-active")
    page.keyboard.press("Enter")  # takes it: the same account
    assert page.input_value(FIRST) == before and not page.query_selector(LIST)
    page.keyboard.press("Control+a")
    page.keyboard.type("0")
    page.wait_for_selector(f"{LIST} .pk-option")
    assert len(_options(page)) == 50 and "the first 50 are listed" in page.inner_text(f"{LIST} .pk-note")
    assert not errors


def test_a_whole_string_the_chart_does_not_have_stays_in_the_box_and_is_refused(browser, chart_company):
    page, errors = _review(browser, chart_company, "one_line")
    typed = f"{chart_company.truth.company.main_business_unit}.9999.9999"
    page.click(FIRST)
    page.keyboard.press("Control+a")
    page.keyboard.type(typed)
    page.keyboard.press("Tab")  # nothing reached with the arrows: what was typed stays
    page.wait_for_selector('[data-grid="lines"] [data-row]:nth-child(1).is-fix')
    assert page.input_value(FIRST) == typed
    assert "is not in the chart of accounts" in page.inner_text('[data-grid="lines"] [data-row]:nth-child(1) .c-slot')
    assert page.is_disabled("[data-approve]")
    assert not errors


def test_ctrl_enter_with_a_list_open_only_closes_it_then_approves(browser, chart_company):
    page, errors = _review(browser, chart_company, "one_line")
    store, invoice_id = chart_company.store, chart_company.invoices["one_line"][1]
    page.focus(FIRST)
    page.keyboard.press("Enter")
    page.wait_for_selector(LIST)
    page.keyboard.press("Control+Enter")
    page.wait_for_timeout(400)
    assert not page.query_selector(LIST) and store.get_invoice(invoice_id)["status"] == REVIEW  # only closed
    page.keyboard.press("Control+Enter")  # the suggested remark no one looked at: said in the bar first
    page.wait_for_selector("[data-bar-check]:not([hidden])")
    assert store.get_invoice(invoice_id)["status"] == REVIEW
    page.keyboard.press("Control+Enter")  # again: approves
    wait_until(page, lambda: store.get_invoice(invoice_id)["status"] == APPROVED, 10)
    assert store.get_invoice(invoice_id)["status"] == APPROVED
    assert not errors


def _focused(page) -> str:
    """What has the focus: the Account box of line N ("account 2"), "approve", or the element's name."""
    return page.evaluate("""() => {
      const el = document.activeElement;
      if (el?.matches?.("[data-approve]")) return "approve";
      const row = el?.closest?.('[data-grid="lines"] [data-row]');
      const at = row ? [...row.parentElement.children].indexOf(row) + 1 : 0;
      if (row && el.matches("input[data-account]")) return `account ${at}`;
      return el?.name || el?.tagName || "";
    }""")


def test_the_screen_opens_on_the_first_thing_to_fix_else_on_approve_and_f8_goes_to_the_next(browser, chart_company):
    page, errors = _review(browser, chart_company, "four_lines")
    assert _focused(page) == "account 1"  # four lines to fix: the first
    page.keyboard.press("F8")
    assert _focused(page) == "account 2"
    page.keyboard.press("F8")
    page.keyboard.press("F8")
    assert _focused(page) == "account 4"
    page.keyboard.press("F8")
    assert _focused(page) == "account 1"  # round again
    page.keyboard.press("Shift+F8")
    assert _focused(page) == "account 4"
    ready, more = _review(browser, chart_company, "one_line")
    assert _focused(ready) == "approve"  # nothing to fix
    assert not errors and not more


def test_with_something_to_check_the_first_ctrl_enter_says_what_and_the_second_approves(browser, chart_company):
    page, errors = _review(browser, chart_company, "one_line")
    store, invoice_id = chart_company.store, chart_company.invoices["one_line"][1]
    reserved = next(a for a in chart_company.truth.accounts if a.reserved).string  # may be posted to, never offered
    page.click(FIRST)
    page.keyboard.press("Control+a")
    page.keyboard.type(reserved)
    page.keyboard.press("Escape")
    page.wait_for_selector('[data-grid="lines"] [data-row]:nth-child(1).is-check')
    page.keyboard.press("Control+Enter")
    page.wait_for_selector("[data-bar-check]:not([hidden])")
    said = page.inner_text("[data-bar-check]")
    assert said == "To check: the account of line 1; 1 suggested remark not looked at. Ctrl+Enter again approves."
    assert store.get_invoice(invoice_id)["status"] == REVIEW and _focused(page) == "account 1"
    page.keyboard.press("Control+Enter")
    wait_until(page, lambda: store.get_invoice(invoice_id)["status"] == APPROVED, 10)
    assert store.get_invoice(invoice_id)["status"] == APPROVED
    assert not errors


REMARK = '[data-grid="lines"] [data-row]:nth-child(1) input[data-remark-box]'
POSTED = '[data-grid="lines"] [data-row]:nth-child(1) input[name="line_remark"]'
COUNT = '[data-grid="lines"] [data-row]:nth-child(1) [data-remark-count]'


def test_the_remark_box_is_marked_suggested_until_visited_counts_and_is_never_cut(browser, chart_company):
    page, errors = _review(browser, chart_company, "one_line")
    suggested = page.input_value(REMARK)
    assert suggested and "is-suggested" in page.get_attribute(REMARK, "class")
    assert page.input_value(POSTED) == ""  # a suggestion is not posted: it follows the line's account
    assert page.inner_text(COUNT) == f"{len(suggested)} / 30"
    page.keyboard.press("Control+Enter")  # a suggested remark no one looked at: said first, not approved
    page.wait_for_selector("[data-bar-check]:not([hidden])")
    assert page.inner_text("[data-bar-check]") == (
        "To check: 1 suggested remark not looked at. Ctrl+Enter again approves.")  # fmt: skip
    assert page.evaluate("document.activeElement.matches('input[data-remark-box]')")  # and it goes to it
    assert "is-suggested" not in page.get_attribute(REMARK, "class")  # visited
    page.keyboard.press("Control+a")
    page.keyboard.type("KT parcels 3-1-204")
    assert page.input_value(POSTED) == "KT parcels 3-1-204" and page.inner_text(COUNT) == "18 / 30"
    assert page.is_hidden("[data-bar-check]")  # an edit: the two steps start again
    page.keyboard.type(" for the third floor kitchen")
    assert page.inner_text(COUNT) == "46 / 30: 16 too many" and "is-over" in page.get_attribute(REMARK, "class")
    assert page.input_value(REMARK) == "KT parcels 3-1-204 for the third floor kitchen"  # whole, never cut
    page.wait_for_selector('[data-grid="lines"] [data-row]:nth-child(1) .rm-note')  # the finding's words, on the line
    assert "JD Edwards keeps 30" in page.inner_text('[data-grid="lines"] [data-row]:nth-child(1) .rm-note')
    assert page.is_disabled("[data-approve]")
    assert not errors


def test_the_suggested_remark_follows_the_account_picked(browser, chart_company):
    from ap_coder.ai.supplier_prior import prior_for
    from ap_coder.coding.remark import suggested

    store = chart_company.store
    invoice, invoice_id = chart_company.invoices["four_lines"]
    page, errors = _review(browser, chart_company, "four_lines")
    proposed = store.get_invoice(invoice_id)["ai_output"]
    line = proposed["line_items"][0]
    assert page.input_value(REMARK) == line["remark"]
    account = line["candidates"][0]["account"]
    page.click(f'[data-grid="lines"] [data-row]:nth-child(1) [data-use-account="{account}"]')
    page.wait_for_selector('[data-grid="lines"] [data-row]:nth-child(1):not(.is-fix)')
    usual = suggested(line, proposed, prior_for(store, proposed["vendor_name"], always=True), account,
                      store.reference_data())  # fmt: skip
    wait_until(page, lambda: page.input_value(REMARK) == usual.text)
    assert page.input_value(REMARK) == usual.text and page.input_value(POSTED) == ""
    assert page.input_value(FIRST) == account and not errors
