"""The JD Edwards E1 settings that are each company's own in a group (D-301): the open G/L period, the default and
header business units, and the G/L bank account (VLGLBA) of each Pay from account. A company with none of its own,
and so one company, keeps Settings' single values, as before. Every name and number is made up."""

import datetime as dt
from types import SimpleNamespace

from ap_coder.core.store import Store
from ap_coder.erp import jde_accounts, jde_post, jde_settings, jde_voucher, jde_zfiles
from ap_coder.erp.jde_formats import julian
from ap_coder.ops import pilot_score
from tests.erp.test_jde import AB, _files, _invoice

OWN = "00210"  # a company of the group with values of its own
OTHER = "00310"  # one without


def _day(*ymd: int) -> str:
    return str(julian(dt.date(*ymd)))


def _group() -> jde_settings.JdeSettings:
    """Settings' single values (open from 1 September, no end; business unit HQ100; OPS-CAD paid from 11111), and
    company 00210's own (open in October only; business unit 21000; OPS-CAD paid from 22222)."""
    settings = jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_GROSS, bu_from_cost_center=False,
                                        default_bu="HQ100", header_bu="HQ100", bank_glba={"OPS-CAD": "11111"},
                                        open_from="2026-09-01")  # fmt: skip
    settings.company_values = {OWN: {"open_from": "2026-10-01", "open_to": "2026-10-31", "default_bu": "21000",
                                     "header_bu": "21000", "bank_glba": {"OPS-CAD": "22222"}}}  # fmt: skip
    return settings


def test_a_companys_own_values_key_its_voucher_and_another_company_takes_settings():
    """B-2833: with several companies the open period, the default and header business units and the bank account
    were one for all: a company whose period is October had its September invoice keyed in September, under another
    company's business unit and bank account."""
    settings = _group()
    own = _invoice(AB, 1, company=OWN, pay_from_account="OPS-CAD")  # (dated 30 September 2026)
    other = _invoice(AB, 2, company=OTHER, invoice_number="CCO-2", pay_from_account="OPS-CAD")
    files = _files([own, other], settings, on=dt.date(2026, 10, 5))
    heads = {h["VLCO"]: h for h in files[jde_zfiles.HEADER_FILE]}
    assert [heads[OWN][c] for c in ("VLMCU", "VLGLBA", "VLDGJ")] == ["21000", "22222", _day(2026, 10, 1)]
    assert [heads[OTHER][c] for c in ("VLMCU", "VLGLBA", "VLDGJ")] == ["HQ100", "11111", _day(2026, 9, 30)]
    units = {(row["VNCO"], row["VNANI"].split(".")[0]) for row in files[jde_zfiles.DIST_FILE]}
    assert units == {(OWN, "21000"), (OTHER, "HQ100")}
    late = jde_voucher._prepare(_invoice(AB, 3, company=OWN, invoice_date="2026-11-04"), settings, "10023")
    assert any("after the open period (it ends 2026-10-31" in p for p in late.problems)
    assert not jde_voucher._prepare(_invoice(AB, 4, company=OTHER, invoice_date="2026-11-04"), settings,
                                    "10023").problems  # fmt: skip
    # Posted through the API, a voucher's period is checked again, as its company's.
    store = SimpleNamespace(get_setting=lambda key, default=None: settings.to_json() if key == "jde_e1" else default)
    run = jde_post._Run(store, 2, None)
    november = _day(2026, 11, 4)
    assert "was closed since its dry run" in run.closed(store, {"pay_items": [{"VLCO": OWN, "VLDGJ": november}]})
    assert run.closed(store, {"pay_items": [{"VLCO": OTHER, "VLDGJ": november}]}) == ""
    # JD Edwards refusing one company's voucher as in a closed period holds that company's month, not another's.
    september = {"VLCO": OTHER, "VLDGJ": _day(2026, 9, 20)}
    run.refusal({"invoice_id": 5, "error": "Period is closed"}, {"pay_items": [{**september, "VLCO": OWN}]})
    assert "JD Edwards refused AP Coder #5's" in run.closed(store, {"pay_items": [{**september, "VLCO": OWN}]})
    assert run.closed(store, {"pay_items": [september]}) == ""


def test_one_company_and_a_company_without_its_own_keep_settings_single_values():
    """B-2833: a company with no values of its own is keyed with Settings' own object; saved and read again, the
    companies' values come back, and settings saved before them read as none."""
    settings = _group()
    assert jde_settings.of_company(settings, OTHER) is settings and jde_settings.of_company(settings, "") is settings
    assert jde_settings.of_company(jde_settings.JdeSettings(), "00001") == jde_settings.JdeSettings()
    again = jde_settings.JdeSettings.from_json(settings.to_json())
    assert again.company_values == settings.company_values
    assert jde_settings.JdeSettings.from_json('{"company": "00001"}').company_values == {}
    own = jde_settings.of_company(settings, "210")  # (the company as typed: zero-padded)
    assert (own.open_from, own.open_to, own.default_bu, own.header_bu) == ("2026-10-01", "2026-10-31", "21000", "21000")
    assert own.bank_glba == {"OPS-CAD": "22222"}
    assert jde_accounts._gl_index(own) is jde_accounts._gl_index(settings)  # (the mapping's index made once)
    only = jde_settings.JdeSettings(company_values={OWN: {"bank_glba": {"OPS-CAD": "22222"}}})
    assert "VLGLBA" in jde_zfiles.header_columns(only) and "VLGLBA" not in jde_zfiles.header_columns(
        jde_settings.JdeSettings())  # fmt: skip


def test_the_pilot_score_compares_each_invoice_on_its_companys_accounts(tmp_path):
    """B-2833: the pilot's score worked out the accounts AP Coder would send with Settings' default business unit, so
    every line of a company with its own was a difference."""
    store = Store(tmp_path / "t.db")
    for n, company in enumerate((OWN, OTHER), 1):
        final = _invoice(AB, n, company=company, invoice_number=f"CCO-{n}")["final_output"]
        store.approve_invoice(store.add_invoice(tmp_path / f"{n}.pdf", final, {}), final, "Pat Tester")
    units = {o.invoice_number: {line["account"][0] for line in o.lines if line["account"]}
             for o in pilot_score.our_invoices(store, _group())}  # fmt: skip
    assert units == {"CCO-1": {"21000"}, "CCO-2": {"HQ100"}}
