"""D-267: a voucher is keyed under its invoice's company (VLCO, VNCO, the keying sheet, the PO's company when Settings
gives none), in that company's currency (a US company's USD invoice is domestic, not foreign), and a company not
checked for Canadian tax takes its tax area from its PO line or its supplier, else it is held."""

import html
import json

from ap_coder.bench import jde_group
from ap_coder.core.schema import COMPANY_TAX
from ap_coder.core.store import Store
from ap_coder.erp import jde_settings, jde_voucher, jde_zfiles
from tests.erp.test_jde import AB, AN8, USD, _files, _invoice


def _settings(**currencies):
    settings = jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_GROSS)
    settings.company_currencies = currencies
    return settings


def test_each_voucher_is_keyed_under_its_invoices_company_and_the_default_without_one():
    settings = _settings()
    files = _files([_invoice(AB, 1, company="00310"), _invoice(AB, 2, invoice_number="CCO-2")], settings)
    heads = {h["VLEDTN"].split("-")[0]: h["VLCO"] for h in files[jde_zfiles.HEADER_FILE]}
    assert heads == {"APC1": "00310", "APC2": "00001"}  # (Settings' company: the default)
    assert {row["VNCO"] for row in files[jde_zfiles.DIST_FILE] if row["VNEDTN"].startswith("APC1")} == {"00310"}


def test_a_us_companys_usd_invoice_is_domestic_and_a_canadian_companys_is_foreign():
    settings = _settings(**{"00310": "USD", "00210": "CAD"})
    us = jde_voucher._prepare(_invoice(USD, company="00310"), settings, "10023")
    ca = jde_voucher._prepare(_invoice(USD, company="00210"), settings, "10023")
    assert (us.currency, us.foreign) == ("USD", False) and (ca.currency, ca.foreign) == ("USD", True)


def test_a_us_companys_voucher_takes_its_tax_area_from_the_po_line_or_the_supplier_else_it_is_held():
    settings = _settings(**{"00310": "USD"})
    given = _invoice(USD, company="00310", **{COMPANY_TAX: {"tax_area": "WA-KING", "from": "PO 30981"}})
    v = jde_voucher._prepare(given, settings, "10023")
    assert (v.tax_area, v.tax_code, v.problems) == ("WA-KING", "E", [])
    held = jde_voucher._prepare(_invoice(USD, company="00310", **{COMPANY_TAX: {"tax_area": "", "from": ""}}),
                                settings, "10023")  # fmt: skip
    assert held.problems == ["no tax area for company 00310's voucher: neither its PO line nor the supplier's default "
                             "(JD Edwards' supplier master, Tax Rate/Area) gives one"]  # fmt: skip


# --- A US company's tax as keyed (D-312) ------------------------------------------------------------------------

WA = {COMPANY_TAX: {"tax_area": "WA-SPOKANE", "from": "the supplier's default"}}


def _us_pair():
    """The US supplier's invoice to company 00310: its sales tax charged (710.01 on 6,860.00), and one of the same
    lines charging none."""
    import dataclasses

    from tests.webclient import gt

    settings = dataclasses.replace(_settings(**{"00310": "USD", "00210": "CAD"}), amount_mode=jde_settings.AMOUNT_TAX)
    lines = [{**li, "taxes_applied": []} for li in gt(USD)["line_items"]]
    untaxed = _invoice(USD, 2, company="00310", invoice_number="INV-30982", tax_lines=[], tax_total=0.0,
                       grand_total=6860.0, line_items=lines, **WA)  # fmt: skip
    return settings, _invoice(USD, 1, company="00310", **WA), untaxed


def test_a_us_companys_tax_is_held_until_settings_key_its_sales_tax_or_use_tax():
    """D-312: a US company's voucher goes with the exempt code and its tax in the expense (D-267) until Settings → JD
    Edwards E1 → US sales and use tax keys it, for every US company or one: then the sales tax charged is the pay
    item's tax with the sales tax code (S), still inside the G/L lines; an invoice charging none goes with the use tax
    code (U), its whole amount taxable and the tax left for JD Edwards to work out. A PO-matched voucher, and the tax
    coded to G/L accounts, are keyed as before."""
    import dataclasses

    held, taxed, untaxed = _us_pair()
    for inv in (taxed, untaxed):
        v = jde_voucher._prepare(inv, held, "10023")
        assert (v.tax_code, v.tax_area, v.stam, v.atxa, v.problems) == ("E", "WA-SPOKANE", 0, 0, [])
    keyed = dataclasses.replace(held, us_tax=jde_settings.US_TAX_KEYED)
    s, u = (jde_voucher._prepare(inv, keyed, "10023") for inv in (taxed, untaxed))
    assert (s.tax_code, s.stam, s.atxa, s.recoverable, s.problems) == ("S", 71001, 686000, 0, [])
    assert (u.tax_code, u.stam, u.atxa, u.problems) == ("U", 0, 686000, [])
    files = _files([taxed, untaxed], keyed)
    heads = {h["VLEXR1"]: (h["VLATXA"], h["VLATXN"], h["VLSTAM"], h["VLTXA1"]) for h in files[jde_zfiles.HEADER_FILE]}
    assert heads == {"S": ("686000", "0", "71001", "WA-SPOKANE"), "U": ("686000", "0", "0", "WA-SPOKANE")}
    lines = [int(row["VNAA"]) for row in files[jde_zfiles.DIST_FILE] if row["VNEDTN"].startswith("APC1")]
    assert sum(lines) == 757001  # (the sales tax inside the expense lines: the gross)
    own = dataclasses.replace(keyed, company_us_tax={"00310": jde_settings.US_TAX_HELD})
    assert jde_voucher._prepare(taxed, own, "10023").tax_code == "E"  # (its own: held, whatever every one's is)
    one = dataclasses.replace(held, company_us_tax={"00310": jde_settings.US_TAX_KEYED})
    assert jde_voucher._prepare(taxed, one, "10023").tax_code == "S"
    on_po = _invoice(USD, 3, company="00310", po_number="30981", **WA)
    assert jde_voucher._prepare(on_po, dataclasses.replace(keyed, po_matched=True), "10023").tax_code == "E"
    on_gl = dataclasses.replace(keyed, amount_mode=jde_settings.AMOUNT_GL)
    assert jde_voucher._prepare(taxed, on_gl, "10023").tax_code == "E"


def test_a_us_companys_tax_settings_are_saved_read_back_and_kept_per_company():
    """D-312: the US sales and use tax settings saved and read back; anything not one of their choices falls back to
    the default (held, S and U, no rates)."""
    rates = {"wa-spokane": "9.1", "MT": 0, "XX": 120, "YY": "nine"}
    settings = jde_settings.JdeSettings(us_tax="keyed", company_us_tax={"310": "held", "320": "maybe"},
                                        sales_tax_code=" s ", use_tax_code="U1", use_tax_rates=rates)  # fmt: skip
    back = jde_settings.JdeSettings.from_json(settings.to_json())
    assert (back.us_tax, back.company_us_tax, back.sales_tax_code, back.use_tax_code) == ("keyed", {"00310": "held"},
                                                                                          "S", "U1")  # fmt: skip
    assert back.use_tax_rates == {"WA-SPOKANE": 9.1, "MT": 0.0}
    odd = jde_settings.JdeSettings.from_json(json.dumps({"us_tax": "sometimes", "sales_tax_code": "SALES"}))
    assert (odd.us_tax, odd.sales_tax_code, odd.use_tax_code, odd.use_tax_rates) == ("held", "S", "U", {})
    assert [jde_settings.of_company(back, c).us_tax for c in ("00310", "00320")] == ["held", "keyed"]


def test_the_keying_sheet_says_how_a_us_companys_tax_is_keyed():
    """D-312: the keying sheet's voucher of a US company's tax keyed says its code, the tax amount and taxable amount
    of a sales tax charged, and for use tax the taxable amount alone, the tax left blank for JD Edwards to work out
    (and what it is at the rate Settings give its tax area)."""
    import dataclasses
    import io

    import openpyxl

    from ap_coder.erp import jde_keying
    from tests.erp.test_jde_keying import blocks, field, said

    held, taxed, untaxed = _us_pair()
    keyed = dataclasses.replace(held, us_tax=jde_settings.US_TAX_KEYED, use_tax_rates={"WA-SPOKANE": 9.1})
    data, _name, _mime = jde_keying.build([taxed, untaxed], keyed, AN8, batch=7)
    sales, use = blocks(openpyxl.load_workbook(io.BytesIO(data)).active)[:2]
    labels = ("Tax explanation code", "Tax rate/area", "Tax amount", "Taxable amount")
    assert [field(sales, label).value for label in labels] == ["S", "WA-SPOKANE", 710.01, 6860]
    assert [field(use, label).value for label in labels] == ["U", "WA-SPOKANE", None, 6860]
    assert ("Code S: the sales tax charged (710.01) is the tax amount, and it stays inside the G/L lines, as JD "
            "Edwards distributes a sales tax (not recovered).") in said(sales, "Note")  # fmt: skip
    assert ("Code U: no sales tax charged, so use tax may be due. JD Edwards works it out on the taxable amount at tax "
            "area WA-SPOKANE's rate and accrues it, not in the gross: leave the tax amount blank (at 9.1%, Settings → "
            "JD Edwards E1, it is 624.26).") in said(use, "Note")  # fmt: skip


def test_the_settings_read_the_companies_currencies_and_never_save_them(tmp_path):
    store = Store(tmp_path / "ap.db")
    store.set_setting("jde_companies", json.dumps({c[0]: {"name": c[1], "currency": c[2], "country": c[3]}
                                                   for c in jde_group.GROUP}))  # fmt: skip
    settings = jde_settings.load(store)
    assert settings.company_currencies["00310"] == "USD"
    assert jde_settings.domestic_of({"company": "310"}, settings) == "USD"
    assert jde_settings.domestic_of({}, settings) == "CAD"
    assert "company_currencies" not in json.loads(settings.to_json())
    assert not jde_settings.set_up(settings)  # (report 9 alone is no JD Edwards setup)
    assert AN8({}) == "10023"


# --- A batch's duplicates, per company (D-287) ----------------------------------------------------------------------


def _ready(tmp_path, template):
    """The demo's approved invoices on a copy of ``template`` (Northwind's ready for JD Edwards), with JD Edwards' two
    made-up companies loaded (report 9): the store, the dashboard and Northwind's coding."""
    from tests.erp.test_jde_keying import demo
    from tests.web.test_chart_eligible import _two_companies

    store, client, northwind, _chinook = demo(tmp_path, template)
    _two_companies(store)
    return store, client, store.get_invoice(northwind["id"])["final_output"]


def _approved(store, tmp_path, doc, number, company, over=()):
    """A copy of ``doc`` numbered ``number`` for ``company``, approved with the warnings ``over`` (their codes)
    showing: approved over them."""
    from ap_coder.core.store import APPROVED

    copy = {**doc, "invoice_number": number, "company": company}
    inv_id = store.add_invoice(tmp_path / f"{number}-{company}-{len(store.unexported_approved())}.pdf", copy, {})
    shown = [{"code": code, "severity": "warning", "line": None} for code in over]
    store.approve_invoice(inv_id, copy, "Jordan Lee", open_issues=shown)
    assert store.get_invoice(inv_id)["status"] == APPROVED
    return inv_id


def _in_jde(store, doc, number, company, document):
    """A voucher of ``doc``'s supplier numbered ``number`` that JD Edwards' voucher list has under ``company``."""
    store.import_erp_vouchers([{"key": f"{company}-PV-{document}-001", "vendor_name": doc["vendor_name"],
                                "invoice_number": number, "total": doc["grand_total"],
                                "invoice_date": doc["invoice_date"], "an8": "", "document": document}])  # fmt: skip


def _page_holds(client, ids):
    """{invoice: whether the Exports page (the keying sheet's format) shows it greyed out, not to tick}, and the page's
    words."""
    from ap_coder.erp import jde_keying

    page = html.unescape(client.get(f"/exports?fmt={jde_keying.FORMAT}").text)
    return {n: "disabled" in page.split(f'name="ids" value="{n}"', 1)[1].split(">", 1)[0] for n in ids}, page


def _keyed(store, client, ids):
    """A keying batch of ``ids`` made on the Exports page: (the invoices on it, those of its keying sheet's vouchers,
    those of its Not ready sheet)."""
    from ap_coder.erp import jde_keying
    from tests.webclient import follow, post

    follow(client, post(client, "/exports/create", {"fmt": jde_keying.FORMAT, "ids": ids}))
    batch = store.export_batches()[0]["id"]
    sheet, held = _sheet(client, batch)
    return store.batch_invoice_ids(batch), sheet, [n for n, _why in held]


def _sheet(client, batch):
    """Batch ``batch``'s keying sheet as the Exports page downloads it: (the invoices of its vouchers, [(invoice, why)]
    of its Not ready sheet)."""
    import io

    import openpyxl

    from ap_coder.erp import jde_keying
    from tests.erp.test_jde_keying import blocks

    wb = openpyxl.load_workbook(io.BytesIO(client.get(f"/exports/batches/{batch}/download").content))
    sheet = [int(b[0][1].value.rsplit(" ", 1)[1]) for b in blocks(wb[jde_keying.SHEET])
             if str(b[0][0].value).startswith("Voucher ")]  # fmt: skip
    return sheet, [(row[0], row[-1]) for row in wb[jde_keying.NOT_READY].iter_rows(min_row=2, values_only=True)]


def test_ap_coders_own_invoice_of_another_company_approved_over_the_warning_goes_on_the_batch(
    tmp_path, cost_centers_demo_template
):
    """B-2696: the batch's duplicate check matched by supplier and invoice number only: an invoice approved over
    DUPLICATE_INVOICE_OTHER_COMPANY (the same number of the same supplier, for the other company) stayed greyed out
    on the Exports page, never on a batch nor its keying sheet. Per company now: one of another company, approved over
    the warning, goes; one of the same company is held as before, said on the page."""
    from ap_coder.erp import jde_settings

    store, client, doc = _ready(tmp_path, cost_centers_demo_template)
    other = "DUPLICATE_INVOICE_OTHER_COMPANY"
    first = _approved(store, tmp_path, doc, "NW-5101", "00210")
    second = _approved(store, tmp_path, doc, "NW-5101", "00310", over=[other])  # in the same batch
    out = _approved(store, tmp_path, doc, "NW-5102", "00310")
    store.create_export_batch([out], jde_settings.FORMAT)  # exported before, for the other company
    after = _approved(store, tmp_path, doc, "NW-5102", "00210", over=[other])
    same = _approved(store, tmp_path, doc, "NW-5101", "00210", over=[other])  # the first's company: a duplicate
    held, page = _page_holds(client, [first, second, after, same])
    assert held == {first: False, second: False, after: False, same: True}
    assert f"same supplier and invoice number as AP Coder #{first} in this batch" in page
    on_batch, sheet, not_ready = _keyed(store, client, [first, second, after, same])
    assert on_batch == sheet == [first, second, after] and not_ready == []


def test_a_bill_in_jd_edwards_under_another_company_holds_an_invoice_only_until_approved_over_it(
    tmp_path, cost_centers_demo_template
):
    """B-2696: a bill JD Edwards has under another company (DUPLICATE_OTHER_COMPANY, a warning) held the invoice at
    the batch as "already in the ERP", approved over the warning or not. Approved over it, it goes on the batch; under
    its own company it is held as before; under another, loaded since its approval (no one approved it over that), it
    is held, said so, to be reopened."""
    store, client, doc = _ready(tmp_path, cost_centers_demo_template)
    _in_jde(store, doc, "NW-5201", "00310", "700201")
    _in_jde(store, doc, "NW-5202", "00210", "700202")
    over = _approved(store, tmp_path, doc, "NW-5201", "00210", over=["DUPLICATE_OTHER_COMPANY"])
    own = _approved(store, tmp_path, doc, "NW-5202", "00210")
    since = _approved(store, tmp_path, doc, "NW-5203", "00210")
    _in_jde(store, doc, "NW-5203", "00310", "700203")
    held, page = _page_holds(client, [over, own, since])
    assert held == {over: False, own: True, since: True}
    assert f"already in the ERP (invoice NW-5202, dated {doc['invoice_date']})" in page
    assert (f"already in the ERP under company 00310 (invoice NW-5203, dated {doc['invoice_date']}), not 00210, "
            "found since it was approved: reopen it to check its company") in page  # fmt: skip
    on_batch, sheet, not_ready = _keyed(store, client, [over, own, since])
    assert on_batch == sheet == [over] and not_ready == []


def test_the_exports_page_reads_jd_edwards_companies_as_a_batch_made_does(tmp_path, cost_centers_demo_template):
    """B-2697: the Exports page's list of what cannot go to JD Edwards read Settings → JD Edwards E1 without report 9,
    which a batch made reads: a US company's USD invoice, in its own currency, was greyed out there as "currency USD
    is not set up for JD Edwards" where only CAD is set up for the export, though it is no foreign voucher."""
    store, client, doc = _ready(tmp_path, cost_centers_demo_template)
    js = jde_settings.load(store)
    js.currencies = ["CAD"]
    jde_settings.save(store, js)
    usd = _approved(store, tmp_path, {**doc, "currency": "USD"}, "NW-5401", "00310")
    held, page = _page_holds(client, [usd])
    assert held == {usd: False}
    on_batch, sheet, not_ready = _keyed(store, client, [usd])
    assert on_batch == sheet == [usd] and not_ready == []


def test_an_invoice_approved_before_its_company_was_decided_is_held_from_jd_edwards(
    tmp_path, cost_centers_demo_template
):
    """B-2698: an invoice approved while AP Coder knew one company (no company on its coding: its voucher is keyed
    under Settings → JD Edwards E1's), then JD Edwards' companies loaded (report 9), went to JD Edwards under Settings'
    company with nothing said, whichever company it is (D-285's "left as it is"). It is held, said so, to be reopened
    for its company: greyed out on the Exports page, and on the Not ready sheet of a keying sheet made before and
    downloaded again."""
    from tests.erp.test_jde_keying import demo
    from tests.web.test_chart_eligible import _two_companies

    store, client, northwind, _chinook = demo(tmp_path, cost_centers_demo_template)
    later = _approved(store, tmp_path, store.get_invoice(northwind["id"])["final_output"], "NW-5501", "")
    assert _page_holds(client, [later])[0] == {later: False}  # one company: Settings', as before
    assert _keyed(store, client, [northwind["id"]]) == ([northwind["id"]], [northwind["id"]], [])
    _two_companies(store)
    held, page = _page_holds(client, [later])
    why = ("approved before its company was decided (JD Edwards' companies were loaded since): reopen it to pick its "
           "company")  # fmt: skip
    assert held == {later: True} and why in page
    assert _sheet(client, store.export_batches()[0]["id"]) == ([], [(northwind["id"], why)])
