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


SIBLING = jde_group.GroupSupplier(*next(r for r in jde_group.SUPPLIERS if r[0] == 4410900))  # 00220, a supplier too
KELSO = {"vendor_name": "KELSO READY MIX LTD", "erp_id": "4410101"}


def _invoice(store, sender: str, billed: str, number: str, gst: str = "", bill: str = "Bill To:",
             ship: tuple[str, str] = ("", "")) -> int:  # fmt: skip
    """A made-up invoice of ``sender`` to ``billed`` (under the label ``bill``; ``ship``: a ship-to's label and name
    beside it), read and coded by the pipeline as one dropped in: its id."""
    from ap_coder.ai.pipeline import InvoicePipeline
    from tests.pdfs import text_pdf
    from tests.webclient import web_settings

    items = [(50, 50, sender, 14), (50, 68, "1180 Marine Drive, Kamloops, BC V2C 1A4"), (400, 50, "INVOICE", 14),
             (400, 70, f"Invoice #: {number}"), (400, 84, "Invoice Date: 2026-10-14"), (400, 98, "Terms: Net 30"),
             (400, 112, f"GST/HST No. {gst}" if gst else ""), (50, 120, bill), (50, 134, billed),
             (300, 120, ship[0]), (300, 134, ship[1]),
             (50, 148, "410 Elm Street, Edmonton, AB T5J 0N3"), (50, 220, "Description"), (400, 220, "Qty"),
             (500, 220, "Amount"), (50, 236, "Project management, October"), (400, 236, "1"), (500, 236, "2,000.00"),
             (400, 300, "Subtotal"), (500, 300, "2,000.00"), (400, 314, "GST 5%"), (500, 314, "100.00"),
             (400, 328, "Total"), (500, 328, "2,100.00")]  # fmt: skip
    path = text_pdf(store.path.parent / f"{number}.pdf", [i for i in items if i[2]])
    return InvoicePipeline(web_settings(), store.reference_data(), store=store).process(path).invoice_id


def _vendor_box(client, invoice_id: int) -> str:
    page = html.unescape(client.get(f"/review/{invoice_id}").text)
    return page.split('data-field="vendor_name"', 1)[1].split("</details>", 1)[0]


def test_an_intercompany_invoice_offers_the_company_of_the_group_that_sent_it_and_its_pick_decides_the_company(
    client, store
):
    """B-2790 (D-298): with report 9 loaded, an invoice of one company of the group to another (Maple Ridge
    Constructors Ltd., 00220, a supplier too, to Maple Ridge Holdings Ltd., 00210) lost its supplier when the vendor
    master has no GST/HST number for it: its name, never read as the supplier, was said nowhere, and the invoice was
    held with no supplier and no company. The vendor box offers it with one click, never filled; picked, the company
    billed is decided. Its number in the vendor master names it as before, and an outside supplier's invoice to a
    company of the group never offers that company."""
    load_sample_setup(store)
    _card(client, jde_group.companies_xlsx())
    store.import_vendor_master([{"vendor_name": SIBLING.name, "erp_id": str(SIBLING.number)}, KELSO], by_erp_id=True)
    invoice_id = _invoice(store, "Maple Ridge Constructors Ltd.", "Maple Ridge Holdings Ltd.", "IC-2611",
                          SIBLING.tax_id)  # fmt: skip
    box = _vendor_box(client, invoice_id)
    assert 'name="vendor_name" value=""' in box  # offered, never put in
    assert ("Maple Ridge Constructors Ltd. is a company of the group and a supplier: if it billed this invoice, pick "
            "it.") in box  # fmt: skip
    assert f'data-help-use="{SIBLING.name}">Use {SIBLING.name}</button>' in box
    data = {**Draft.stored(store.get_invoice(invoice_id)["ai_output"]).header}
    assert "MISSING_VENDOR" in html.unescape(client.post(f"/review/{invoice_id}/check", data=data,
                                                         headers=PAGE).json()["sections"]["checks"])  # fmt: skip
    picked = client.post(f"/review/{invoice_id}/check", data={**data, "vendor_name": SIBLING.name},
                         headers=PAGE).json()  # fmt: skip
    assert picked["header"]["values"]["company"] == "00210" and "vendor_name" not in picked["header"]["help"]
    assert "Pick the company" not in html.unescape(picked["sections"]["checks"])
    # its GST/HST number in the vendor master: the number names it, the company billed decided, nothing to offer
    store.import_vendor_master([{"vendor_name": SIBLING.name, "erp_id": str(SIBLING.number), "gst": SIBLING.tax_id},
                                KELSO], by_erp_id=True)  # fmt: skip
    by_number = _invoice(store, "Maple Ridge Constructors Ltd.", "Maple Ridge Holdings Ltd.", "IC-2612",
                         SIBLING.tax_id)  # fmt: skip
    read = store.get_invoice(by_number)["ai_output"]
    assert (read["vendor_name"], read["company"]) == (SIBLING.name, "00210")
    assert "data-help-use" not in _vendor_box(client, by_number)
    # an outside supplier's invoice to the company of the group that is a supplier too: never offered
    outside = _invoice(store, "Kelso Ready Mix Ltd.", "Maple Ridge Constructors Ltd.", "KR-4410")
    read = store.get_invoice(outside)["ai_output"]
    assert (read["vendor_name"], read["company"]) == ("Kelso Ready Mix Ltd.", "00220")
    assert "data-help-use" not in _vendor_box(client, outside)
    cleared = {**Draft.stored(read).header, "vendor_name": ""}
    answer = client.post(f"/review/{outside}/check", data=cleared, headers=PAGE).json()
    assert answer["header"]["help"]["vendor_name"]["actions"] == [  # the page's own reading, never the company billed
        {"label": "Use Kelso Ready Mix Ltd. from the page", "value": "Kelso Ready Mix Ltd."}
    ]


def test_an_outside_suppliers_invoice_labelled_bill_to_with_a_hyphen_never_offers_the_companies_it_names(client, store):
    """B-2792 on the screen: an outside supplier's invoice, read with its supplier in the vendor box, billed to Maple
    Ridge Holdings Ltd. under "Bill-To:" and shipped to Maple Ridge Constructors Ltd. under "Ship-To:" (two of the
    group, no company decided, both suppliers too) offered both as its supplier under the vendor box: one click put
    the customer in place of the supplier."""
    load_sample_setup(store)
    _card(client, jde_group.companies_xlsx())
    holdings = {"vendor_name": "MAPLE RIDGE HOLDINGS LTD", "erp_id": "4410901"}
    store.import_vendor_master([{"vendor_name": SIBLING.name, "erp_id": str(SIBLING.number)}, holdings, KELSO],
                               by_erp_id=True)  # fmt: skip
    outside = _invoice(store, "Kelso Ready Mix Ltd.", "Maple Ridge Holdings Ltd.", "KR-4411", bill="Bill-To:",
                       ship=("Ship-To:", "Maple Ridge Constructors Ltd."))  # fmt: skip
    read = store.get_invoice(outside)["ai_output"]
    assert read["vendor_name"] == "Kelso Ready Mix Ltd." and not read.get("company")
    assert "data-help-use" not in _vendor_box(client, outside)
    cleared = {**Draft.stored(read).header, "vendor_name": ""}
    answer = client.post(f"/review/{outside}/check", data=cleared, headers=PAGE).json()
    assert "of the group" not in answer["header"]["help"].get("vendor_name", {}).get("text", "")


def test_a_company_of_the_group_named_with_its_supplier_number_is_the_supplier_not_the_customer(client, store):
    """B-2794 as read: a company of the group with two supplier numbers under its name is named by its GST/HST number
    with its number ("MAPLE RIDGE CONSTRUCTORS LTD #4410900", D-219). Compared with the company's own names with that
    number on it, it was taken for the customer: the invoice was held to pick its company, and with no bill-to label
    its own letterhead address was taken for the bill-to (BC, not the company billed's AB)."""
    load_sample_setup(store)
    _card(client, jde_group.companies_xlsx())
    store.import_vendor_master([{"vendor_name": SIBLING.name, "erp_id": str(SIBLING.number), "gst": SIBLING.tax_id},
                                {"vendor_name": SIBLING.name, "erp_id": "4410950"}, KELSO], by_erp_id=True)  # fmt: skip
    for number, bill in (("IC-2613", "Bill To:"), ("IC-2614", "")):
        read = store.get_invoice(_invoice(store, "Maple Ridge Constructors Ltd.", "Maple Ridge Holdings Ltd.", number,
                                          SIBLING.tax_id, bill=bill))["ai_output"]  # fmt: skip
        assert read["vendor_name"] == f"{SIBLING.name} #{SIBLING.number}", bill
        assert (read["company"], read["ship_to_province"]) == ("00210", "AB"), bill


@pytest.fixture
def browser(chromium, store):
    """The dashboard on ``store`` served on this computer and a Chromium to open it: (browser, base URL)."""
    from ap_coder.web.app import create_app
    from tests.browser import browser_on
    from tests.webclient import web_settings

    with browser_on(chromium, create_app(web_settings(), store, start_reader=False)) as opened:
        yield opened


def test_in_a_browser_the_company_of_the_group_offered_is_put_in_with_one_click_and_the_company_billed_follows(
    browser, client, store
):
    """D-298 on the screen: the offer under the vendor box (B-2790; the box had no place for it, B-2791) puts the
    supplier in with one click, and the screen, drawn again for the company the check then decides, keeps it."""
    load_sample_setup(store)
    _card(client, jde_group.companies_xlsx())
    store.import_vendor_master([{"vendor_name": SIBLING.name, "erp_id": str(SIBLING.number)}, KELSO], by_erp_id=True)
    invoice_id = _invoice(store, "Maple Ridge Constructors Ltd.", "Maple Ridge Holdings Ltd.", "IC-2611")
    chromium, base = browser
    page = chromium.new_page(viewport={"width": 1366, "height": 768})
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base}/review/{invoice_id}")
    page.wait_for_selector(".iv-sheet:not(.loading)")
    offer = page.locator("[data-field=vendor_name] > [data-help]")
    offer.locator("[data-help-use]").wait_for()
    assert "is a company of the group and a supplier: if it billed this invoice, pick it." in offer.inner_text()
    assert page.locator("[form=inv][name=company]").input_value() == ""
    offer.locator("[data-help-use]").click()
    assert page.locator("#vendor-name").input_value() == SIBLING.name
    page.wait_for_function("document.querySelector('[form=inv][name=company]').value === '00210'")
    page.wait_for_selector(".iv-sheet:not(.loading)")
    assert page.locator("#vendor-name").input_value() == SIBLING.name and not offer.is_visible()
    assert "Maple Ridge Holdings Ltd. is named on the invoice" in page.locator("[data-company-source]").inner_text()
    assert errors == []


def test_settings_review_lists_the_companies_names_of_report_9_as_never_read_as_a_supplier(client, store):
    """D-298: report 9's names are the company's own names too, never read as the supplier, but Settings → Review's
    "Your company's names" showed only the ones typed there: a pilot tester could not see why a company of the group
    was never read as its invoices' supplier. They are listed under the box, read-only, said where they come from."""
    page = html.unescape(client.get("/settings/review").text)
    assert "From JD Edwards' companies" not in page
    _card(client, jde_group.companies_xlsx())
    page = html.unescape(client.get("/settings/review").text)
    listed = page.split("From JD Edwards' companies", 1)[1].split("</p>", 1)[0]
    assert "00210 Maple Ridge Holdings Ltd." in listed and "00410 Maple Ridge Caribbean Ltd." in listed
    assert 'name="our_names"' in page and "Maple Ridge Holdings" not in page.split('name="our_names"', 1)[1].split(
        "</textarea>", 1)[0]  # fmt: skip


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
    assert client.get("/review/accounts.json?unit=").json()["list"] == []  # (no unit: none, never the whole list)


def test_a_chart_searched_as_typed_is_never_made_or_sent_whole_for_a_question_with_nothing_asked(
    client, store, monkeypatch
):
    """B-2762: a chart too big to send (more than LIST_MOST accounts, a group's of millions: D-267) was still made
    whole, sent and kept for ``GET /review/accounts.json`` with nothing typed and no business unit, by anyone who may
    look at invoices (the Read-only role): at 3,500,000 accounts about 90 s of the server and over a gigabyte, kept
    until the chart changed. No page asks for it so; it is answered with no account, what the page asks as before."""
    from ap_coder.web.pages import invoice_lookups
    from tests import chart_files as cf
    from tests.core.test_chart import _chart, _units

    load_sample_setup(store)
    store.import_business_units(_units().rows)
    store.import_chart(_chart().rows)
    monkeypatch.setattr(invoice_lookups, "LIST_MOST", 5)
    monkeypatch.setattr(invoice_lookups, "_account_list", lambda reference: pytest.fail("the whole list was made"))
    for asked in ("", "?v=0123", "?unit=", "?q=", "?company=&q=&unit="):
        answer = client.get(f"/review/accounts.json{asked}")
        assert answer.json() == {"list": []} and answer.headers["cache-control"] == "no-store", asked
    found = client.get("/review/accounts.json?q=coffee").json()["list"]
    assert found == [[f"{cf.HOME}.5410.2210", "Coffee and Refreshments", "Head Office General"]]
    assert [row[0] for row in client.get(f"/review/accounts.json?unit={cf.HOME}").json()["list"]][:1] == [
        f"{cf.HOME}.5410"
    ]


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
