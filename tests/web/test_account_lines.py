"""The review screen's row for an install with a chart of accounts (D-214), on the made-up company (the
``chart_company`` fixture): one Account box a line that holds the whole account string, what the row says under it
(the account's name, its business unit, why it was coded so; or why it is left for a person, with the accounts
offered), and what the server does with what is typed. An install without a chart keeps its own row."""

import json
import re
from html import unescape as html_unescape

import pytest

from ap_coder.core import chart
from ap_coder.web import account_lines
from ap_coder.web.account_lines import LINE_ACCOUNT
from ap_coder.web.invoice_view import LINE_REMARK
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


def test_a_line_a_person_coded_no_longer_says_to_pick_the_account(chart_company, chart_company_client):
    """B-2260: a line left for a person ("…: pick the account.") kept that sentence as its reason once a person had
    picked its account, and was stored with it. It says who picked it; a line still without an account keeps why."""
    invoice, _ = chart_company.invoices["four_lines"]
    page = _page(chart_company, chart_company_client, "four_lines")
    assert all(row.reason.endswith(": pick the account.") for row in page.lines)
    for n, part in enumerate(invoice.parts, start=1):
        page.set_account(n, part.account)
    assert page.approve()["ok"]
    stored = chart_company.store.get_invoice(page.id)["final_output"]["line_items"]
    assert [li["reasoning_justification"] for li in stored] == ["Picked by a person."] * 4


def test_a_reason_a_person_picked_keeps_what_the_line_says_of_its_tax():
    from ap_coder.coding.line_tax import GUESS_NOTE
    from ap_coder.coding.review import decided_on_screen

    left = "This supplier's 3 lines last year went to 3 accounts: 33% / 33% / 33%: pick the account."
    proposed = [{"line_number": n, "description": f"item {n}", "predicted_gl_code": "UNASSIGNED",
                 "predicted_cost_center": "UNASSIGNED", "reasoning_justification": f"{left} {GUESS_NOTE}"}
                for n in (1, 2)]  # fmt: skip
    lines = [{**proposed[0], "predicted_gl_code": "7420.4213", "predicted_cost_center": "0021061010"},
             dict(proposed[1])]  # fmt: skip
    decided_on_screen(lines, proposed)
    assert lines[0]["reasoning_justification"] == f"Picked by a person. {GUESS_NOTE}"
    assert lines[1]["reasoning_justification"] == f"{left} {GUESS_NOTE}"  # no account yet: why it was left
    decided_on_screen(lines, proposed)
    assert lines[0]["reasoning_justification"] == f"Picked by a person. {GUESS_NOTE}"  # checked again: the same


def test_words_in_the_account_box_say_one_thing_and_an_empty_box_is_counted_once(chart_company, chart_company_client):
    """B-2262: "off" typed in line 1's Account box read "Line 1: cost center off is not in your cost centers", and
    the bar asked for "the business unit of the account of lines 2, 3, 4", whose boxes are empty."""
    page = _page(chart_company, chart_company_client, "four_lines")
    form = {**page.form, LINE_ACCOUNT: ["off", "", "", ""]}
    answer = chart_company_client.post(f"/review/{page.id}/check", data=form, headers=PAGE).json()
    checks, bar = (html_unescape(answer["sections"][part]) for part in ("checks", "bar"))
    assert "Line 1: “off” is not an account: pick one from the list" in checks
    assert "Lines 2, 3, 4: no account yet: pick one" in checks and "Lines 1, 2, 3, 4" not in checks
    assert "cost center off" not in checks and "CC_UNKNOWN" not in checks
    assert "To fix: an account for 4 lines</button>" in bar and "business unit" not in bar
    assert "“off” is not an account: pick one from the list" in html_unescape(answer["lines"][0]["slot"])
    # A line with its GL code and no business unit is still asked for it.
    code = chart.split_account(chart_company.invoices["four_lines"][0].parts[0].account)[1]
    form = {**page.form, LINE_ACCOUNT: [code, "", "", ""]}
    bar = chart_company_client.post(f"/review/{page.id}/check", data=form, headers=PAGE).json()["sections"]["bar"]
    assert "To fix: an account for lines 2, 3, 4 and the business unit of the account of line 1</button>" in bar


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


# --- tax on every line (W4) -----------------------------------------------------------------------------------------


def test_every_line_says_which_tax_was_charged_and_how_much_each_tax_its_own_amount(
    chart_company, chart_company_client
):
    """The Quebec invoice: the GST is recovered, the QST stays in the lines. Each line shows the two amounts apart,
    the amount keyed for it under its Amount box, and under the lines one sentence a tax."""
    page = _page(chart_company, chart_company_client, "quebec")
    answer = page.check()
    first, second = answer["lines"]
    assert "GST <b>50.00</b>" in first["tax"] and "QST <b>99.75</b> · in the line" in first["tax"]
    assert "GST <b>12.00</b>" in second["tax"] and "QST <b>23.94</b> · in the line" in second["tax"]
    assert first["has_keyed"] and "keyed <b>1,099.75</b>" in first["keyed"]
    assert second["has_keyed"] and "keyed <b>263.94</b>" in second["keyed"]
    assert "≠" not in first["tax"] and ">read<" not in first["tax"]  # the usual tax, worked out from the total
    said = answer["sections"]["taxline"]
    assert "GST: lines taxed 1,240.00 × 5% = 62.00 · printed 62.00" in said and "(recovered: on the pay item)" in said
    assert "QST: lines taxed 1,240.00 × 9.975% = 123.69" in said and "(not recovered: inside the lines)" in said
    rows = _rows(page.text)  # the page as drawn says the same as the check's answer
    assert "GST <b>50.00</b>" in rows[0] and "keyed <b>263.94</b>" in rows[1] and "has-keyed" in rows[0]
    assert "Show the tax table" in page.text


def test_a_line_with_no_tax_kept_in_it_shows_no_keyed_amount(chart_company, chart_company_client):
    (row,) = _page(chart_company, chart_company_client, "one_line").check()["lines"]
    assert "GST <b>240.00</b>" in row["tax"] and not row["has_keyed"] and not row["keyed"]


def test_a_meals_line_says_so_under_its_tax():
    """B-2208: a line coded to a meals account says under its tax that half is claimable; another line doesn't."""
    from ap_coder.coding.tax import meals_note
    from ap_coder.coding.taxmath import LineTaxes

    def line(meals):
        return LineTaxes(0, 1, "G", "GST 5%", False, 100.0, (), (), 0.0, 100.0, meals, False, ())

    note = meals_note("AB")
    assert account_lines.tax_view(line(True), note)["notes"] == [note]
    assert account_lines.tax_view(line(False), note)["notes"] == []
    assert account_lines.tax_view(line(True))["notes"] == []


def test_a_line_whose_tax_is_not_the_invoices_usual_one_is_marked(chart_company, chart_company_client):
    page = _page(chart_company, chart_company_client, "four_lines")
    codes = page.form["line_tax_code"]
    form = {**page.form, "line_tax_code": [*codes[:3], "EXEMPT"]}
    answer = chart_company_client.post(f"/review/{page.id}/check", data=form, headers=PAGE).json()
    *usual, odd = [row["tax"] for row in answer["lines"]]
    assert all("≠" not in tax and "odd" not in tax for tax in usual)
    assert "≠" in odd and 'class="tx-list odd"' in odd and "no tax" in odd


def _with_a_tax_printed_on_its_line(store, invoice_id: int, read: dict[str, float]) -> None:
    proposed = store.get_invoice(invoice_id)["ai_output"]
    proposed["line_items"][0]["taxes_read"] = read
    with store._conn() as conn:
        conn.execute("UPDATE invoices SET ai_output = ? WHERE id = ?", (json.dumps(proposed), invoice_id))


def test_a_tax_amount_printed_on_a_line_is_marked_read_and_survives_the_save(chart_company, chart_company_client):
    """B-2207: the form posts no tax amount for a line, and the coding rebuilt from it dropped the one the invoice
    prints on the line."""
    store = chart_company.store
    invoice_id = chart_company.invoices["one_line"][1]
    _with_a_tax_printed_on_its_line(store, invoice_id, {"GST": 240.0})
    page = ReviewPage(chart_company_client, invoice_id)
    (row,) = page.check()["lines"]
    assert 'GST <b>240.00</b> <i class="rd"' in row["tax"] and ">read</i>" in row["tax"]
    assert page.approve()["ok"]
    assert store.get_invoice(invoice_id)["final_output"]["line_items"][0]["taxes_read"] == {"GST": 240.0}


def test_a_tax_printed_for_another_amount_is_not_the_lines(chart_company, chart_company_client):
    store = chart_company.store
    invoice_id = chart_company.invoices["one_line"][1]
    _with_a_tax_printed_on_its_line(store, invoice_id, {"GST": 240.0})
    page = ReviewPage(chart_company_client, invoice_id)
    form = {**page.form, "line_amount": ["4000.00"]}
    (row,) = chart_company_client.post(f"/review/{page.id}/check", data=form, headers=PAGE).json()["lines"]
    assert ">read</i>" not in row["tax"]  # worked out again for the amount typed


def test_two_tax_areas_on_one_invoice_say_why_the_voucher_is_held():
    from types import SimpleNamespace

    from ap_coder.coding.validation import Issue

    taxes = SimpleNamespace(two_tax_areas=True, check=[], notes=())
    held = Issue("error", "JDE_TAX_AREA", "the lines are in two tax areas: key two pay items")
    assert account_lines.tax_said(taxes, [held])["hold"] == "The lines are in two tax areas: key two pay items"
    assert account_lines.tax_said(taxes, [])["hold"] == account_lines.TWO_AREAS
    assert account_lines.tax_said(SimpleNamespace(two_tax_areas=False, check=[], notes=()), [held])["hold"] == ""


# --- the account list (W5) ------------------------------------------------------------------------------------------


def _seed(text: str) -> dict:
    return json.loads(text.split('<script type="application/json" id="account-seed">', 1)[1].split("</script>", 1)[0])


def test_the_account_list_is_fetched_once_not_printed_in_the_page(chart_company, chart_company_client):
    client, store = chart_company_client, chart_company.store
    page = _page(chart_company, client, "fifteen_lines")
    seed, pairs = _seed(page.text), store.reference_data().pairs
    offered = [(unit, code) for unit in pairs.business_units() for code in pairs.accounts_of(unit)]
    assert seed["count"] == len(offered) > 100 and seed["home"] == pairs.settings.home_business_unit
    assert 'id="gl-options"' not in page.text and 'id="cc-options"' not in page.text  # no chart in the page
    answer = client.get(seed["url"])
    rows = answer.json()["list"]
    assert [row[0] for row in rows] == [chart.join_account(unit, code) for unit, code in offered]
    unit, code = offered[0]
    assert rows[0] == [chart.join_account(unit, code), pairs.description(unit, code), pairs.name_of(unit)]
    assert all(pairs.offered(*chart.split_account(row[0])) for row in rows)  # nothing reserved, closed or a title
    assert answer.headers["content-encoding"] == "gzip" and answer.headers["vary"] == "Accept-Encoding"
    assert answer.headers["cache-control"] == "private, max-age=31536000, immutable"
    tag = answer.headers["etag"]
    other = client.get("/review/accounts.json?v=0123", headers={"If-None-Match": 'W/"0123"'})
    assert other.status_code == 200 and other.headers["cache-control"] == "private, no-cache"
    unchanged = client.get("/review/accounts.json?v=0123", headers={"If-None-Match": tag})
    assert unchanged.status_code == 304 and not unchanged.content
    # The page's weight no longer follows the chart's size: of the chart it holds only the accounts offered for its
    # own lines and the supplier's of last year.
    named = [row[0] for row in rows if row[0] in page.text]
    assert 0 < len(named) <= 5 * 15 + 8 and len(named) < len(rows) / 2, (len(named), len(rows))


def test_the_page_offers_the_suppliers_accounts_and_each_lines_candidates(chart_company, chart_company_client):
    invoice, _ = chart_company.invoices["four_lines"]
    page = _page(chart_company, chart_company_client, "four_lines")
    seed = _seed(page.text)
    assert {row[0] for row in seed["supplier"]} == set(invoice.supplier.accounts)  # its three accounts last year
    assert all(lines == 1 and name and unit for _, lines, name, unit in seed["supplier"])
    proposed = chart_company.store.get_invoice(page.id)["ai_output"]["line_items"]
    listed = [json.loads(html_unescape(found)) for found in re.findall(r'data-candidates="([^"]*)"', page.text)]
    assert [[row[0] for row in line] for line in listed[:4]] == [
        [c["account"] for c in line["candidates"]] for line in proposed]  # fmt: skip
    answer = page.check()
    assert answer["supplier_accounts"] == seed["supplier"]
    assert [row["candidates"] for row in answer["lines"]] == listed[:4]


def test_an_install_without_a_chart_has_no_account_list(demo_template, tmp_path):
    store = copy_database(demo_template, tmp_path / "private" / "ap_coder.db")
    client = client_for(store)
    assert client.get("/review/accounts.json").json() == {"list": []}  # no page asks for it without a chart
    text = client.get(f"/review/{store.list_invoices()[0]['id']}").text
    assert 'id="account-seed"' not in text and 'id="gl-options"' in text


# --- approving (W6) -------------------------------------------------------------------------------------------------


def test_the_bar_says_in_one_sentence_what_approving_does_and_what_is_to_fix(chart_company, chart_company_client):
    ready = _page(chart_company, chart_company_client, "one_line").check()["sections"]["bar"]
    assert ">Ready</span>" in ready and 'data-not-ready=""' in ready
    assert "Approving posts 5,040.00 CAD on 1 line and teaches AP Coder this coding, then opens the next one" in ready
    four = _page(chart_company, chart_company_client, "four_lines")
    closed = four.check()["sections"]["bar"]
    assert "To fix: an account for 4 lines</button>" in closed and "data-goto-pick" in closed
    assert "Approving posts 1,393.61 CAD on 4 lines and teaches AP Coder this coding once that is fixed." in closed
    assert four.not_ready.startswith("Pick a GL account for 4 lines")  # what the approval's refusal says, as before
    credit = _page(chart_company, chart_company_client, "credit_note").check()["sections"]["bar"]
    assert "Approving credits -606.90 CAD on 1 line" in credit


def test_what_approval_still_needs_in_the_words_of_one_account_box():
    words = account_lines.pick_words
    assert words("Pick a GL account for 4 lines and a cost center for 4 lines to approve (or use Apply to all "
                 "lines…)") == "an account for 4 lines"  # fmt: skip
    assert words("Pick a GL account for line 2 and a cost center for lines 1, 2 to approve") == (
        "an account for line 2 and the business unit of the account of lines 1, 2")  # fmt: skip
    assert words("Pick a cost center for line 3 to approve") == "the business unit of the account of line 3"
    confirm = "Confirm the PST on line 1: exempt, or not charged (self-assess)?"
    assert words(f"Pick a GL account for line 2 to approve. {confirm}") == f"an account for line 2. {confirm}"
    assert words(confirm) == confirm


# --- the Remark box (W7) --------------------------------------------------------------------------------------------


def _remark_boxes(text: str) -> list[tuple[str, str]]:
    """Each row's Remark box as drawn: (what it shows, its classes)."""
    return [(html_unescape(value), classes) for value, classes in
            re.findall(r'data-remark-box value="([^"]*)" class="([^"]*)"', text)]  # fmt: skip


def test_every_line_has_a_remark_box_showing_the_suggestion_marked_as_suggested(chart_company, chart_company_client):
    page = _page(chart_company, chart_company_client, "four_lines")
    proposed = chart_company.store.get_invoice(page.id)["ai_output"]["line_items"]
    boxes = _remark_boxes(page.text)[:4]
    assert [shown for shown, _ in boxes] == [li["remark"] for li in proposed]
    assert all("is-suggested" in classes and "is-over" not in classes for _, classes in boxes)
    assert page.form[LINE_REMARK] == [""] * 4  # a suggestion is not posted: it follows the line's account
    rows = _rows(page.text)
    for row, li in zip(rows, proposed, strict=True):
        assert f"{len(li['remark'])} / 30</span>" in row  # the count under the box
        assert f"“{li['description']}”" in html_unescape(row)  # the line as the invoice prints it
        assert 'name="line_description"' in row  # still posted (the row's details change it)
    assert ">suggested</span>" in page.text  # the column's legend


def test_the_suggested_remark_follows_the_account_and_a_typed_one_stays(chart_company, chart_company_client):
    invoice, _ = chart_company.invoices["one_line"]
    page = _page(chart_company, chart_company_client, "one_line")
    before = _remark_boxes(page.text)[0][0]
    (row,) = page.check()["lines"]
    assert row["remark_box"] == {"text": before, "typed": False, "why": row["remark_box"]["why"]}
    assert row["remark_box"]["why"].startswith("Suggested")
    typed = "KT parcels 3-1-204"
    form = {**page.form, LINE_REMARK: [typed]}
    (row,) = chart_company_client.post(f"/review/{page.id}/check", data=form, headers=PAGE).json()["lines"]
    assert row["remark_box"] == {"text": typed, "typed": True, "why": ""}
    page.open()  # the draft kept it: drawn as a person's, posted as typed
    assert _remark_boxes(page.text)[0] == (typed, "") and page.form[LINE_REMARK] == [typed]
    assert page.approve()["ok"]
    stored = chart_company.store.get_invoice(page.id)["final_output"]["line_items"][0]
    assert (stored["remark"], stored["remark_from"]) == (typed, "person")


def test_a_remark_over_30_characters_is_never_cut_and_says_the_findings_words(chart_company, chart_company_client):
    page = _page(chart_company, chart_company_client, "one_line")
    long = "coffee supplies for the 3rd floor kitchen"
    form = {**page.form, LINE_REMARK: [long]}
    answer = chart_company_client.post(f"/review/{page.id}/check", data=form, headers=PAGE).json()
    (row,) = answer["lines"]
    assert not answer["allow"] and row["remark_box"]["text"] == long  # whole, as typed
    assert "The remark has 41 characters and JD Edwards keeps 30" in row["remark"]  # REMARK_LONG's words, on the line
    page.open()
    shown, classes = _remark_boxes(page.text)[0]
    assert shown == long and "is-over" in classes and "41 / 30: 11 too many" in page.text
