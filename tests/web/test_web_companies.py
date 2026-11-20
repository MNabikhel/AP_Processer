"""D-267 on the pages: JD Edwards' companies (report 9) loaded on card 9 of JD Edwards files and in the master
workbook, and the Company box of the review screen, which holds an invoice no rule decided until a person picks."""

from __future__ import annotations

import html
import re

import pytest

from ap_coder.bench import jde_company, jde_group
from ap_coder.bench import jde_exports as made
from ap_coder.coding import companies
from ap_coder.core.store import load_sample_setup
from ap_coder.web.invoice_form import Draft
from tests.webclient import PAGE, add_clean, follow


def _card(client, data: bytes, name: str = "companies.xlsx") -> tuple[str, str]:
    answer = client.post("/jde-files/companies/upload-table", files={"file": (name, data)}, headers=PAGE)
    token = re.search(r"companies=([^&#]+)", answer.json()["redirect"]).group(1)
    shown = client.get(f"/jde-files?companies={token}").text
    loaded = follow(client, client.post("/jde-files/companies/load-table", data={"token": token}, headers=PAGE))
    return shown.split('id="companies-summary"', 1)[1].split("</section>", 1)[0], loaded


def test_card_9_loads_report_9_as_reportsnow_titles_it_and_says_which_get_canadian_tax_checks(client, store):
    page = client.get("/jde-files").text
    assert '<section class="card stack" id="companies">' in page and "9. Companies" in page
    shown, loaded = _card(client, jde_group.companies_xlsx(titles="reportsnow"))
    assert "6 companies to load, 1 row left out." in html.unescape(shown)
    said = html.unescape(loaded)
    assert "Companies: 6 companies loaded, 1 row left out (no company or name, or JD Edwards' default company " in said
    assert "00320 Cascade Line Contractors Inc. · USD, CA: not checked for Canadian tax (currency USD)" in said
    assert "00210 Maple Ridge Holdings Ltd. · CAD, CA: checked for Canadian tax (CA and CAD)" in said
    assert "00410 Maple Ridge Caribbean Ltd. · USD, no country: not checked for Canadian tax (no country)" in said
    assert "Each invoice is now told which company it is for" in said
    assert set(companies.companies(store)) == {c[0] for c in jde_group.GROUP}


def test_the_master_workbook_loads_its_companies_sheet_first(client, store):
    truth = jde_company.build(1, "small", 2024)
    data = made.master_workbook_xlsx(truth, companies=jde_group.master_records())
    answer = client.post("/jde-files/workbook/upload", files={"file": (made.MASTER_FILE, data)}, headers=PAGE)
    token = re.search(r"workbook=([^&#]+)", answer.json()["redirect"]).group(1)
    page = client.get(f"/jde-files?workbook={token}").text
    assert "Load all: 11 sheets" in page
    loaded = html.unescape(follow(client, client.post("/jde-files/workbook/load", data={"token": token},
                                                       headers=PAGE)))  # fmt: skip
    assert "Companies (Companies): Companies: 6 companies loaded" in loaded
    assert loaded.index("Companies (Companies)") < loaded.index("Accounts (Chart of accounts)")
    assert len(companies.companies(store)) == 6


def test_the_company_box_holds_an_invoice_until_a_company_is_picked(client, store):
    load_sample_setup(store)
    invoice_id = add_clean(store)
    page = client.get(f"/review/{invoice_id}").text
    assert 'name="company"' not in page  # one company: no box, as before
    _card(client, jde_group.companies_xlsx())
    page = html.unescape(client.get(f"/review/{invoice_id}").text)
    box = page.split('name="company"', 1)[1].split("</select>", 1)[0]
    assert '<option value="" selected>Pick the company</option>' in box
    assert '<option value="00310">00310 Maple Ridge Builders Inc.</option>' in box
    inv = store.get_invoice(invoice_id)
    data = {**Draft.stored(inv["ai_output"]).header}
    answer = client.post(f"/review/{invoice_id}/check", data=data, headers=PAGE).json()
    assert "company" in answer["header"]["flags"] and "Pick the company this invoice is for" in html.unescape(
        answer["sections"]["checks"])  # fmt: skip
    picked = client.post(f"/review/{invoice_id}/check", data={**data, "company": "00310"}, headers=PAGE).json()
    assert "company" not in picked["header"]["flags"]
    assert "Company 00310 is not checked for Canadian tax (currency USD)" in html.unescape(picked["sections"]["checks"])


def test_a_warning_new_since_the_first_approval_is_shown_to_the_second_approver_and_approved_only_anyway(
    chart_company, chart_company_client, monkeypatch
):
    """B-2695: a warning the first approver never saw (here a bill of the same number loaded since under another
    company, DUPLICATE_OTHER_COMPANY) passed the second approval with nothing said: only errors kept it. It is shown on
    the invoice waiting for its second approval, and the second approval takes "Approve anyway" with a reason, kept
    with it; without them it is refused, the warning said. The approved company stays the one approved (D-286)."""
    from ap_coder.core import audit
    from ap_coder.core.store import APPROVED, PENDING
    from tests.web.test_chart_eligible import _first_approval, _one_line, _two_companies
    from tests.webclient import post

    store, client = chart_company.store, chart_company_client
    _two_companies(store)
    page, _courier = _one_line(chart_company, client)
    _first_approval(store, monkeypatch, page)
    final = store.get_invoice(page.id)["final_output"]
    assert final["company"] == "00210"
    store.import_erp_vouchers([{"key": "00310-PV-700901-001", "vendor_name": final["vendor_name"],
                                "invoice_number": final["invoice_number"], "total": final["grand_total"],
                                "invoice_date": final["invoice_date"], "an8": "", "document": "700901"}])  # fmt: skip
    monkeypatch.setenv("AP_REVIEWER", "Kim")
    for name in ("LOGNAME", "USER", "LNAME", "USERNAME"):
        monkeypatch.setenv(name, "kim")
    approval = store.latest_approval_id(page.id)
    shown = html.unescape(follow(client, post(client, f"/review/{page.id}/final-approve", {"approval": approval})))
    assert store.get_invoice(page.id)["status"] == PENDING
    assert "Not done: since its first approval, checked again, it says what its first approver did not see: " \
           "Already entered under another company" in shown  # fmt: skip
    screen = html.unescape(client.get(f"/review/{page.id}").text)
    card = screen.split('class="second-new', 1)[1].split("</div>", 1)[0]
    assert "is already in the ERP under company 00310" in card and 'name="override_reason"' in card
    key = re.search(r'name="override" value="([0-9a-f]+)"', card).group(1)
    data = {"approval": approval, "override": key, "override_reason": ""}
    assert "say why" in html.unescape(follow(client, post(client, f"/review/{page.id}/final-approve", data)))
    assert store.get_invoice(page.id)["status"] == PENDING
    data["override_reason"] = "Keyed to 00310 by mistake, reversed there"
    follow(client, post(client, f"/review/{page.id}/final-approve", data))
    assert store.get_invoice(page.id)["status"] == APPROVED
    event = next(iter(store.events(page.id, ["final_approved"], limit=1)))
    assert event["detail"]["open_issues"] == [{"code": "DUPLICATE_OTHER_COMPANY", "severity": "warning", "line": None,
                                               "override_reason": data["override_reason"]}]  # fmt: skip
    assert "approved anyway over a warning new since the first approval (Already entered under another company): " \
           "“Keyed to 00310 by mistake, reversed there”" in audit.describe(event)  # fmt: skip


def _box(client, invoice_id: int) -> str:
    page = html.unescape(client.get(f"/review/{invoice_id}").text)
    return page.split('data-field="company"', 1)[1].split("</label>", 1)[0]


def test_the_company_box_says_how_its_company_was_decided_and_only_a_persons_pick_is_kept(client, store):
    """B-2692 on the review screen: the Company box says how its company was decided (AP Coder's rule, or a person's
    pick). AP Coder's company, the box left as it was drawn, is decided again as the facts change; one a person
    picks (the box's hidden field set as it is changed, or a form that posts another company) is kept and approved
    as picked."""
    load_sample_setup(store)
    invoice_id = add_clean(store)
    _card(client, jde_group.companies_xlsx())
    inv = store.get_invoice(invoice_id)
    voucher = {"key": "00230-PV-700101-001", "vendor_name": inv["ai_output"]["vendor_name"], "invoice_number": "N-1",
               "total": 10.0, "invoice_date": "2026-01-02", "an8": "", "document": "700101"}  # fmt: skip
    store.import_erp_vouchers([voucher])
    box = _box(client, invoice_id)
    assert '<option value="00230" selected>' in box and 'name="company_how" form="inv" value=""' in box
    assert ("Decided by AP Coder from the supplier's history: the only company this supplier has billed. Decided "
            "again as the invoice changes; pick one to keep it.") in re.sub(r"<[^>]+>", "", box)  # fmt: skip
    # the screen as drawn, then the supplier bills another company too: decided again, none now
    data = {**Draft.stored(inv["ai_output"]).header, "company": "00230", "company_how": ""}
    store.import_erp_vouchers([{**voucher, "key": "00210-PV-700102-001", "invoice_number": "N-2"}])
    answer = client.post(f"/review/{invoice_id}/check", data=data, headers=PAGE).json()
    assert answer["header"]["values"]["company"] == ""
    assert "Pick the company this invoice is for" in html.unescape(answer["sections"]["checks"])
    # picked by a person: kept, and said so
    picked = {**data, "company": "00230", "company_how": "picked"}
    answer = client.post(f"/review/{invoice_id}/check", data=picked, headers=PAGE).json()
    assert "company" not in answer["header"]["values"] and "company" not in answer["header"]["flags"]
    box = _box(client, invoice_id)
    assert '<option value="00230" selected>' in box and 'value="picked" data-company-how' in box
    assert "Picked by a person: kept whatever else the invoice says." in re.sub(r"<[^>]+>", "", box)
    assert follow(client, client.post(f"/review/{invoice_id}/approve", data=picked, headers=PAGE))
    final = store.get_invoice(invoice_id)["final_output"]
    assert (final["company"], final["company_how"]) == ("00230", "picked")
    # a form without the hidden field (an older screen): another company than the screen had is a pick
    assert Draft.from_form({"company": "00310"}, Draft.stored(inv["ai_output"])).company_how == "picked"
    assert Draft.from_form({"company": "00230"}, Draft.stored(final)).company_how == "picked"
    assert Draft.from_form({"company": "00230", "company_how": ""}, Draft.stored(final)).company_how == ""


def test_a_name_written_for_a_company_the_companies_report_does_not_list_is_not_saved(client, store):
    """B-2694: a variation of Settings → Review's "Your company's names" written "name = company" with the company
    mistyped ("Cascade Line = 330", meant 00320) was saved and decided invoices for a company JD Edwards may not have.
    With report 9 loaded, the names are checked on save, and each line naming a company it does not list is said (one
    the pilot names may stay, D-272)."""
    import dataclasses

    _card(client, jde_group.companies_xlsx())
    typed = "Maple Ridge Holdings Ltd.\nCascade Line = 330\nFraser Bend = 230\nYard 9 ="
    refused = client.post("/settings/review", data={"our_names": typed}, headers=PAGE)
    assert refused.status_code == 400 and store.our_names() == []
    assert html.unescape(refused.json()["error"]) == (
        "Not saved: “Cascade Line = 330”, “Yard 9 =” name no company of the companies report (JD Edwards files, card "
        "9). Write each name, = and the number of one of its companies, e.g. Maple Ridge Holdings Ltd. = 00210."
    )
    assert follow(client, client.post("/settings/review", data={"our_names": typed.replace("330", "320")
                                                                .replace("\nYard 9 =", "")}, headers=PAGE))  # fmt: skip
    assert companies.name_variations(store)["cascade line"] == "00320"
    store.save_chart_settings(dataclasses.replace(store.chart_settings(), pilot=("00210", "00330")))
    assert follow(client, client.post("/settings/review", data={"our_names": "Cascade Line = 330"}, headers=PAGE))
    assert store.our_names() == ["Cascade Line = 330"]


def test_settings_turn_a_companys_canadian_tax_checks_on_or_off_and_say_how_they_were_worked_out(client, store):
    from ap_coder.erp import jde_settings
    from ap_coder.web.core import SHOW_JDE

    store.set_setting(SHOW_JDE, "1")
    page = client.get("/settings/jde").text
    assert 'id="company-tax"' not in page  # one company: no such table
    _card(client, jde_group.companies_xlsx())
    page = html.unescape(client.get("/settings/jde").text)
    table = page.split('id="company-tax"', 1)[1].split("</table>", 1)[0]
    assert "<td>USD, CA</td><td>not checked (currency USD)</td>" in table
    assert "<td>CAD, CA</td><td>checked (CA and CAD)</td>" in table and "not checked (no country)" in table
    tax_map = jde_settings.load(store).tax_map
    form = {"province": list(tax_map), "tax_area": [a.tax_area for a in tax_map.values()],
            "code": [a.code for a in tax_map.values()], "company": [c[0] for c in jde_group.GROUP],
            "company_tax": ["auto", "off", "auto", "auto", "on", "auto"]}  # fmt: skip
    assert client.post("/settings/jde/tax-map", data=form, headers=PAGE).json()["ok"]
    assert jde_settings.load(store).company_tax == {"00220": "off", "00320": "on"}
    assert companies.canadian(store, "00320") == (True, "turned on in Settings")


def test_a_chart_too_big_to_send_is_searched_as_an_account_box_is_typed_in(client, store, monkeypatch):
    from ap_coder.web.pages import invoice_lookups
    from tests import chart_files as cf
    from tests.core.test_chart import _chart, _units

    load_sample_setup(store)
    store.import_business_units(_units().rows)
    store.import_chart(_chart().rows)
    reference = store.reference_data()
    seed = invoice_lookups.account_seed(store, reference, "")
    assert seed["url"] and "search" not in seed  # a chart of a few accounts: sent whole, as before
    monkeypatch.setattr(invoice_lookups, "LIST_MOST", 5)
    seed = invoice_lookups.account_seed(store, reference, "")
    assert seed["url"] == "" and seed["search"] == "/review/accounts.json?company=&q="
    found = client.get("/review/accounts.json?q=coffee").json()["list"]
    assert found == [[f"{cf.HOME}.5410.2210", "Coffee and Refreshments", "Head Office General"]]


def test_a_chart_searched_as_typed_lists_a_business_units_accounts_with_nothing_typed(client, store, monkeypatch):
    """With nothing typed, the Account box of a chart searched as typed lists the accounts of the line's business unit
    ("On ..."): it was empty, the box holding no list to take them from. One company's of several: its units alone."""
    from ap_coder.web.pages import invoice_lookups
    from tests import chart_files as cf
    from tests.core.test_chart import _chart, _units

    load_sample_setup(store)
    store.import_business_units(_units().rows)
    store.import_chart(_chart().rows)
    monkeypatch.setattr(invoice_lookups, "LIST_MOST", 5)
    seed = invoice_lookups.account_seed(store, store.reference_data(), "")
    assert seed["unit"] == "/review/accounts.json?company=&unit="
    found = client.get(f"/review/accounts.json?unit={cf.HOME}").json()["list"]
    assert [row[0] for row in found] == [f"{cf.HOME}.{code}" for code in ("5410", "5410.2210", "5410.7701", "5710")]
    assert found[1] == [f"{cf.HOME}.5410.2210", "Coffee and Refreshments", "Head Office General"]
    assert client.get(f"/review/accounts.json?unit={cf.CLOSED}").json()["list"] == []  # (closed: nothing offered)
    assert client.get("/review/accounts.json?unit=").json()["list"] != []  # (no unit: the whole list, as before)


def test_an_invoice_approved_before_report_9_is_sent_back_at_its_second_approval_to_be_given_its_company(
    chart_company, chart_company_client, monkeypatch
):
    """B-2685: approved first while AP Coder knew one company (no company on its coding: its voucher is keyed under
    Settings → JD Edwards E1's, here 00001), then JD Edwards' companies loaded: its second approval checked it again as
    the company its page names (00210) and approved the coding without one, to be keyed under 00001. It is refused,
    said so; sent back, approved again for its company, it passes."""
    import dataclasses

    from ap_coder.core.store import APPROVED, PENDING
    from ap_coder.erp import jde_settings
    from tests.web.test_chart_eligible import _first_approval, _one_line, _second_approval, _two_companies

    store, client = chart_company.store, chart_company_client
    jde_settings.save(store, dataclasses.replace(jde_settings.load(store), company="00001"))
    page, _courier = _one_line(chart_company, client)
    _first_approval(store, monkeypatch, page)
    _two_companies(store)
    shown = html.unescape(_second_approval(store, client, monkeypatch, page))
    assert store.get_invoice(page.id)["status"] == PENDING
    assert "its company was not decided when it was approved" in shown
    assert follow(client, client.post(f"/review/{page.id}/send-back", data={"reason": "its company"}, headers=PAGE))
    for name in ("LOGNAME", "USER", "LNAME", "USERNAME"):  # (Sam on his own computer login again)
        monkeypatch.setenv(name, "sam")
    _first_approval(store, monkeypatch, page.open())
    assert store.get_invoice(page.id)["final_output"]["company"] == "00210"
    shown = html.unescape(_second_approval(store, client, monkeypatch, page))
    assert store.get_invoice(page.id)["status"] == APPROVED, shown[shown.find("Not done") :][:300]


def test_a_us_companys_invoice_shows_no_canadian_place_of_supply(client, store):
    """B-2871: the review screen's header said "Place of supply: Alberta" (Settings → Review's province) on a US
    company's invoice shipped to Spokane, WA, or with no ship-to printed, though no Canadian tax is checked for it. A
    company not checked for Canadian tax shows the ship-to printed (outside Canada), never a province worked out."""
    load_sample_setup(store)
    store.set_setting("our_province", "AB")
    _card(client, jde_group.companies_xlsx())
    us = {"company": "00310", "company_how": "picked", "currency": "USD"}
    shown = {}
    for ship_to in ("OUTSIDE_CANADA", ""):
        invoice_id = add_clean(store, ship_to_province=ship_to, **us)
        data = Draft.stored(store.get_invoice(invoice_id)["ai_output"]).header
        answer = client.post(f"/review/{invoice_id}/check", data=data, headers=PAGE).json()
        head = html.unescape(answer["sections"]["summary"]).split('class="rvw-head-meta"', 1)[1].split("</div>")[0]
        shown[ship_to] = re.findall(r'title="Place of supply">([^<]*)<', head)
    assert shown == {"OUTSIDE_CANADA": ["Outside Canada"], "": []}
    canadian = add_clean(store, ship_to_province="", company="00210", company_how="picked")
    data = Draft.stored(store.get_invoice(canadian)["ai_output"]).header
    head = html.unescape(client.post(f"/review/{canadian}/check", data=data, headers=PAGE).json()["sections"][
        "summary"])  # fmt: skip
    assert 'title="Place of supply">Alberta<' in head  # (a Canadian company's: Settings' province, as before)


def test_the_us_sales_and_use_tax_card_keeps_how_each_us_companys_tax_is_keyed(client, store):
    """D-312: Settings → JD Edwards E1's US sales and use tax card, with several companies: each company not checked
    for Canadian tax, its tax held for a person until the card keys it (for every one, or a company its own way); the
    sales and use tax codes and the use tax rates, a tax area a line, kept and shown again; a code of the Canadian
    taxes, the same code twice or a line that is no rate refused, said why."""
    from ap_coder.erp import jde_settings
    from ap_coder.web.core import SHOW_JDE

    store.set_setting(SHOW_JDE, "1")
    assert 'id="us-tax"' not in client.get("/settings/jde").text  # one company: no such card
    _card(client, jde_group.companies_xlsx())
    card = html.unescape(client.get("/settings/jde").text).split('id="us-tax"', 1)[1].split("</section>", 1)[0]
    assert re.findall(r'name="us_company" value="(\d+)"', card) == ["00310", "00320", "00410"]  # (not the Canadian)
    assert '<option value="held" selected>Held for a person to confirm it (as before)</option>' in card
    form = {"us_tax": "keyed", "us_company": ["00310", "00320", "00410"], "us_company_tax": ["held", "", ""],
            "sales_tax_code": "s", "use_tax_code": "U", "use_tax_rates": "WA-SPOKANE = 9.1\n\nmt-blgs 0%"}  # fmt: skip
    assert "US sales and use tax saved." in follow(client, client.post("/settings/jde/us-tax", data=form,
                                                                         headers=PAGE))  # fmt: skip
    js = jde_settings.load(store)
    assert (js.us_tax, js.company_us_tax, js.sales_tax_code, js.use_tax_code) == ("keyed", {"00310": "held"}, "S", "U")
    assert js.use_tax_rates == {"WA-SPOKANE": 9.1, "MT-BLGS": 0.0}
    card = html.unescape(client.get("/settings/jde").text).split('id="us-tax"', 1)[1].split("</section>", 1)[0]
    assert "MT-BLGS = 0\nWA-SPOKANE = 9.1</textarea>" in card and '<option value="keyed" selected>' in card
    for change, said in (
        ({"sales_tax_code": "V"}, "not V, C, B or E (the Canadian taxes' codes)"),
        ({"use_tax_code": "S"}, "The sales tax and the use tax codes must differ."),
        ({"use_tax_rates": "WA-SPOKANE nine"}, "“WA-SPOKANE nine” is no tax area and rate"),
        ({"use_tax_rates": "MT-BILLINGS 0"}, "Tax area MT-BILLINGS is longer than JD Edwards' 10"),
    ):
        answer = client.post("/settings/jde/us-tax", data={**form, **change}, headers=PAGE)
        assert answer.status_code == 400 and said in answer.json()["error"], answer.text
    assert jde_settings.load(store).use_tax_rates == {"WA-SPOKANE": 9.1, "MT-BLGS": 0.0}  # (nothing refused kept)


@pytest.mark.parametrize("width", [1366, 390])
def test_in_a_browser_the_us_sales_and_use_tax_card_is_filled_in_and_saved(chromium, store, width):
    """D-312 in a browser, on a laptop and a phone: the US sales and use tax card is on Settings → JD Edwards E1 with
    every box on screen (nothing past the page's right edge), its choices made and rates typed as a person does, and
    saved with its own button, the page saying so."""
    import json

    from ap_coder.erp import jde_settings
    from ap_coder.web.app import create_app
    from ap_coder.web.core import SHOW_JDE
    from tests.browser import browser_on, wait_until
    from tests.webclient import web_settings

    store.set_setting(SHOW_JDE, "1")
    store.set_setting(companies.COMPANIES_SETTING, json.dumps({c[0]: {"name": c[1], "currency": c[2], "country": c[3]}
                                                               for c in jde_group.GROUP}))  # fmt: skip
    with browser_on(chromium, create_app(web_settings(), store, start_reader=False)) as (browser, base):
        page = browser.new_page(viewport={"width": width, "height": 800})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{base}/settings/jde")
        card = page.locator("#us-tax")
        card.scroll_into_view_if_needed()
        assert card.is_visible() and "Held for a person to confirm it (as before)" in card.inner_text()
        card.locator('select[name="us_tax"]').select_option("keyed")
        card.locator('select[aria-label="How the tax of Maple Ridge Caribbean Ltd. is keyed"]').select_option("held")
        card.locator('textarea[name="use_tax_rates"]').fill("WA-SPOKANE = 9.1")
        for box in card.locator("select, input:not([type=hidden]), textarea, button").all():
            box.scroll_into_view_if_needed()
            right = page.evaluate("b => b.getBoundingClientRect().right", box.element_handle())
            assert right <= width + 1, box.evaluate("b => b.outerHTML")[:120]
        card.locator("button[type=submit]").click()
        wait_until(page, lambda: jde_settings.load(store).us_tax == "keyed")
        page.wait_for_selector("text=US sales and use tax saved.")
        js = jde_settings.load(store)
        assert (js.company_us_tax, js.use_tax_rates) == ({"00410": "held"}, {"WA-SPOKANE": 9.1})
        assert page.evaluate("document.documentElement.scrollWidth") <= width + 1 and errors == []


def test_a_us_companys_sales_tax_rate_is_never_marked_wrong_by_canadas_rates(client, store):
    """B-2873: the review screen's Rates and GL accounts marked a US company's sales tax rate wrong (✕), its official
    rate "not levied": Canada's rate table, though the company is not checked for Canadian tax. Its rate is said not
    checked, unmarked; a Canadian company's foreign tax is still marked as before."""
    load_sample_setup(store)
    _card(client, jde_group.companies_xlsx())
    shown = {}
    for company in ("00310", "00210"):
        invoice_id = add_clean(store, "cascade_US_SalesTax_INV-30981", company=company, company_how="picked")
        data = Draft.stored(store.get_invoice(invoice_id)["ai_output"]).header
        answer = client.post(f"/review/{invoice_id}/check", data=data, headers=PAGE).json()
        rates = html.unescape(answer["sections"]["taxcheck"]).split("Rates and GL accounts", 1)[1]
        official = re.findall(r'<td class="r">(not [a-z]+)</td>', rates)
        shown[company] = (re.findall(r'aria-label="(Right|Wrong)"', rates), official)
    assert shown == {"00310": ([], ["not checked"]), "00210": (["Wrong"], ["not levied"])}
