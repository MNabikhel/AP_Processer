"""D-267 on the pages: JD Edwards' companies (report 9) loaded on card 9 of JD Edwards files and in the master
workbook, and the Company box of the review screen, which holds an invoice no rule decided until a person picks."""

from __future__ import annotations

import html
import re

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
