"""D-267: a voucher is keyed under its invoice's company (VLCO, VNCO, the keying sheet, the PO's company when Settings
gives none), in that company's currency (a US company's USD invoice is domestic, not foreign), and a company not
checked for Canadian tax takes its tax area from its PO line or its supplier, else it is held."""

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
