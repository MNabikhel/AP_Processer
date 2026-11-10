"""The review screen's row for an install with a chart of accounts (D-214), on the made-up company (the
``chart_company`` fixture): one Account box a line that holds the whole account string, what the row says under it
(the account's name, its business unit, why it was coded so; or why it is left for a person, with the accounts
offered), and what the server does with what is typed. An install without a chart keeps its own row."""

import re

import pytest

from ap_coder.core import chart
from ap_coder.web import account_lines
from ap_coder.web.account_lines import LINE_ACCOUNT
from tests.conftest import copy_database
from tests.review_page import ReviewPage
from tests.webclient import PAGE, client_for

ROW_NAMES = ("line_key", "line_reason", "line_number", LINE_ACCOUNT, "line_gl", "line_cc", "line_amount",
             "line_description", "line_tax_code", "line_quantity", "line_unit_price")  # fmt: skip


def _page(chart_company, client, name: str) -> ReviewPage:
    return ReviewPage(client, chart_company.invoices[name][1])


def _rows(text: str) -> list[str]:
    """Each line's row of the page, as drawn (the line template's is not one)."""
    body = text.split('<tbody data-grid="lines">', 1)[1].split("</tbody>", 1)[0]
    return re.findall(r"<tr data-row=.*?</tr>", body, flags=re.S)


def test_every_row_posts_every_name_once(chart_company, chart_company_client):
    page = _page(chart_company, chart_company_client, "fifteen_lines")
    assert {name: len(page.form[name]) for name in ROW_NAMES} == dict.fromkeys(ROW_NAMES, 15)


def test_a_coded_line_says_the_accounts_name_its_business_unit_and_why(chart_company, chart_company_client):
    invoice, _ = chart_company.invoices["one_line"]
    page = _page(chart_company, chart_company_client, "one_line")
    (account,) = [part.account for part in invoice.parts]
    unit, code = chart.split_account(account)
    pairs = chart_company.store.reference_data().pairs
    (row,) = _rows(page.text)
    assert f'name="{LINE_ACCOUNT}" value="{account}"' in row and 'class="acct-row"' in row
    caption = re.search(r'data-live-line="cap">(.*?)</td>', row, flags=re.S).group(1)
    assert pairs.description(unit, code) in caption and pairs.name_of(unit).replace("&", "&amp;") in caption
    assert "this supplier's usual account: 8 of 8 lines last year" in caption.replace("&#39;", "'")


def test_a_line_left_for_a_person_is_marked_with_why_and_the_accounts_offered(chart_company, chart_company_client):
    page = _page(chart_company, chart_company_client, "four_lines")
    proposed = chart_company.store.get_invoice(page.id)["ai_output"]["line_items"]
    rows = _rows(page.text)
    assert len(rows) == 4
    for row, line in zip(rows, proposed, strict=True):
        assert 'class="acct-row is-fix"' in row and f'name="{LINE_ACCOUNT}" value=""' in row
        assert "<b>To fix:</b>" in row and "went to 3 accounts" in row  # the line's own reason
        offered = re.findall(r'data-use-account="([^"]+)"', row)
        assert offered == [c["account"] for c in line["candidates"]][:3] and len(offered) == 3  # the first three
        assert line["candidates"][0]["name"] in row and "of this supplier" in row  # its name and why it is offered


def test_a_string_the_chart_does_not_have_is_refused_and_never_replaced_by_the_account_before(
    chart_company, chart_company_client
):
    """The Account box is what is posted: the row's hidden cells still hold the account the line had, and the line
    is checked on what was typed."""
    page = _page(chart_company, chart_company_client, "one_line")
    before = page.lines[0].account
    main = chart_company.truth.company.main_business_unit
    moved = next(a for a in chart_company.truth.accounts if a.object == "1450" and a.subsidiary)
    typed = f"{main}.{moved.object}.{moved.subsidiary}"
    unit, code = chart.split_account(before)
    form = {**page.form, LINE_ACCOUNT: [typed], "line_cc": [unit], "line_gl": [code]}
    answer = chart_company_client.post(f"/review/{page.id}/check", data=form, headers=PAGE).json()
    assert answer["ok"] and not answer["allow"]
    (row,) = answer["lines"]
    assert row["state"] == "fix" and row["text"] == typed
    assert f"Account {typed} is not in the chart of accounts" in row["slot"]  # the check's own words, on the line
    page.open()
    assert page.lines[0].account == typed and before not in page.form[LINE_ACCOUNT]
    assert "ACCOUNT_NOT_IN_CHART" in page.findings and page.not_ready
    assert page.approve()["ok"] is False


@pytest.mark.parametrize("typed", ["audit fees", "7420", "0021061010"])
def test_words_or_part_of_a_number_stay_in_the_box_and_keep_approve_closed(chart_company, chart_company_client, typed):
    page = _page(chart_company, chart_company_client, "one_line")
    page.set_account(1, typed)
    form = {**page.form, LINE_ACCOUNT: [typed]}
    answer = chart_company_client.post(f"/review/{page.id}/check", data=form, headers=PAGE).json()
    assert not answer["allow"] and answer["lines"][0]["state"] == "fix"
    page.open()
    assert page.form[LINE_ACCOUNT] == [typed] and page.approve()["ok"] is False


def test_an_account_a_person_picks_says_so_and_approves(chart_company, chart_company_client):
    invoice, _ = chart_company.invoices["four_lines"]
    page = _page(chart_company, chart_company_client, "four_lines")
    for n, part in enumerate(invoice.parts, start=1):
        page.set_account(n, part.account)
    answer = page.check()
    assert answer["allow"] and [row["state"] for row in answer["lines"]] == [""] * 4
    assert all("picked by a person" in row["cap"] and not row["slot"] for row in answer["lines"])
    assert page.approve()["ok"]
    stored = chart_company.store.get_invoice(page.id)["final_output"]["line_items"]
    assert [chart.join_account(li["predicted_cost_center"], li["predicted_gl_code"]) for li in stored] == [
        part.account for part in invoice.parts]  # fmt: skip
    assert {li["decided_by"]["source"] for li in stored} == {"person"}


def test_the_same_account_for_all_fills_the_lines_without_one(chart_company, chart_company_client):
    invoice, _ = chart_company.invoices["four_lines"]
    page = _page(chart_company, chart_company_client, "four_lines")
    account = invoice.parts[0].account
    page.set_account(2, invoice.parts[1].account)
    answer = chart_company_client.post(f"/review/{page.id}/fill-lines", data={**page.form, "fill_account": account},
                                       headers=PAGE).json()  # fmt: skip
    assert answer["ok"] and "set on 3 lines" in answer["toast"]["text"]
    page.open()
    assert [row.account for row in page.lines] == [account, invoice.parts[1].account, account, account]
    refused = chart_company_client.post(f"/review/{page.id}/fill-lines", headers=PAGE,
                                        data={**page.form, "fill_account": "0021061010.0000.0000"}).json()  # fmt: skip
    assert refused["ok"] is False and "is not an account of the chart" in refused["error"]


def test_the_box_as_posted():
    assert account_lines.posted("") == ("", "")
    assert account_lines.posted("  0021061010.7420.4213 ") == ("7420.4213", "0021061010")
    assert account_lines.posted("0021061010\t7420\t4213") == ("7420.4213", "0021061010")  # three cells pasted
    assert account_lines.posted("0021061010 7420 4213") == ("7420.4213", "0021061010")
    assert account_lines.posted("audit fees") == ("", "audit fees")  # words: kept as typed, no account
    assert account_lines.account_text("", "audit fees") == "audit fees"
    assert account_lines.account_text("7420.4213", "0021061010") == "0021061010.7420.4213"
    assert account_lines.account_text("UNASSIGNED", "UNASSIGNED") == ""


def test_why_a_line_was_coded_in_plain_words():
    why = account_lines.why_words
    assert why({"source": "supplier", "lines": 8, "of": 8}) == "this supplier's usual account: 8 of 8 lines last year"
    assert why({"source": "vendor", "lines": 3}) == "approved this way 3 times"
    assert why({"source": "rule", "rule": "Courier → 7420"}) == "the rule “Courier → 7420”"
    assert why({"source": "person"}) == "picked by a person"
    assert why({"source": "model"}) == "AP Coder's guess: check it"
    assert why({}) == "" and why(None) == ""


def test_an_install_without_a_chart_keeps_its_own_row(demo_template, tmp_path):
    store = copy_database(demo_template, tmp_path / "private" / "ap_coder.db")
    invoice_id = store.list_invoices()[0]["id"]
    text = client_for(store).get(f"/review/{invoice_id}").text
    assert f'name="{LINE_ACCOUNT}"' not in text and "acct-row" not in text and "inv-lines-grid acct" not in text
    assert '<select name="line_gl" form="inv"' in text and 'data-live="suggest"' in text
    assert not account_lines.in_use(store.reference_data())


# --- the frame (W2) -------------------------------------------------------------------------------------------------


def test_the_screen_with_a_chart_puts_the_lines_under_a_compact_header(chart_company, chart_company_client):
    page = _page(chart_company, chart_company_client, "one_line")
    text = page.text
    assert "data-menu-toggle" in text and 'class="inv-split inv-acct"' in text
    order = ['data-live="checks"', 'class="card inv-details inv-head"', 'data-live="keyed"', "data-more-details",
             'id="lines"', 'data-fold="tax"', 'data-fold="posting"']  # fmt: skip
    assert [text.index(mark) for mark in order] == sorted(text.index(mark) for mark in order)
    head = text[text.index('class="card inv-details inv-head"') : text.index("data-more-details")]
    for name in ("vendor_name", "invoice_number", "invoice_date", "grand_total", "tax_total", "currency", "due_date"):
        assert f'name="{name}"' in head, name
    folded = text[text.index("data-more-details") : text.index('id="lines"')]
    for name in ("po_number", "payment_terms", "gst_hst_registration_number", "ship_to_province", "subtotal"):
        assert f'name="{name}"' in folded and f'name="{name}"' not in head, name
    assert '<details class="inv-more-details" data-more-details>' in text  # closed: nothing in it has a finding


def test_what_is_keyed_on_the_pay_item_is_one_line_from_the_voucher(chart_company, chart_company_client):
    from ap_coder.erp import jde

    page = _page(chart_company, chart_company_client, "one_line")
    store = chart_company.store
    final = store.get_invoice(page.id)["ai_output"]
    settings = jde.load(store)
    keyed = jde.voucher_view(final, settings, jde.resolve_an8(final, store.vendor_ids(), settings))["keyed"]
    assert keyed.startswith("Keyed on the pay item: ")
    line = page.check()["sections"]["keyed"]
    assert keyed.partition(": ")[2].split(" · ")[0] in line and "Keyed on the pay item" in line
    four = _page(chart_company, chart_company_client, "four_lines")
    assert "Not ready to key" in four.check()["sections"]["keyed"]  # no account yet: said, not a wrong voucher


def test_a_line_without_its_account_is_one_finding(chart_company, chart_company_client):
    page = _page(chart_company, chart_company_client, "four_lines")
    checks = page.check()["sections"]["checks"]
    assert "Lines 1, 2, 3, 4: no account yet: pick one" in checks
    assert "Before you can approve (1)" in checks and "no GL account yet" not in checks.split("Details and what")[0]
    assert 'data-code="CC_UNASSIGNED" hidden>' in checks  # the same line's second code: in the list, not shown


def test_an_install_without_a_chart_keeps_its_screen(demo_template, tmp_path):
    store = copy_database(demo_template, tmp_path / "private" / "ap_coder.db")
    client = client_for(store)
    invoice_id = store.list_invoices()[0]["id"]
    page = ReviewPage(client, invoice_id)
    assert "data-menu-toggle" not in page.text and "inv-acct" not in page.text and "data-fold" not in page.text
    assert '<section class="card inv-summary" data-live="summary">' in page.text
    assert '<section class="card inv-details">' in page.text and "data-more-details" not in page.text
    answer = page.check()
    assert "keyed" not in answer["sections"] and "lines" not in answer
