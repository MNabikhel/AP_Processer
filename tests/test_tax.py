"""Canadian sales tax: rates, calculation checks, regime checks and GL distribution."""

import copy
import datetime as dt
import json

import pytest

from ap_coder.coding.tax import (
    EXPENSE_SEPARATE,
    TaxRateTable,
    TaxSetup,
    TaxTreatment,
    build_gl_distribution,
    check_taxes,
    valid_gst_number,
    valid_qst_number,
)
from ap_coder.coding.validation import validate_coding
from ap_coder.core.schema import InvoiceCoding

from .conftest import SAMPLES

QC = "laurentides_QC_TPS_TVQ_SIL-4471"
BC = "pacific_BC_GST_PST_PO-77120"
ON = "northwind_ON_HST_NW-2026-0912"


def _gt(name):
    return json.loads((SAMPLES / "ground_truth" / f"{name}.json").read_text())


def _codes(coding_dict, reference):
    return {f.code for f in check_taxes(InvoiceCoding.model_validate(coding_dict), reference.tax, None)}


# --- Rates ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tax_type", "province", "date", "rate"),
    [
        ("GST", "AB", "2026-01-01", 0.05),
        ("HST", "ON", "2026-01-01", 0.13),
        ("HST", "NS", "2025-03-31", 0.15),
        ("HST", "NS", "2025-04-01", 0.14),
        ("PST", "BC", "2026-01-01", 0.07),
        ("PST", "MB", "2019-06-30", 0.08),
        ("PST", "MB", "2019-07-01", 0.07),
        ("QST", "QC", "2026-01-01", 0.09975),
        ("PST", "ON", "2026-01-01", None),
        ("HST", "AB", "2026-01-01", None),
    ],
)
def test_effective_dated_rates(tax_type, province, date, rate):
    assert TaxRateTable.load().rate_for(tax_type, province, dt.date.fromisoformat(date)) == rate


@pytest.mark.parametrize(
    ("number", "gst_ok", "qst_ok"),
    [
        ("123456789 RT0001", True, False),
        ("123456789-RT-0001", True, False),
        ("12345678 RT0001", False, False),
        ("1234567890 TQ0001", False, True),
        ("", False, False),
    ],
)
def test_registration_number_formats(number, gst_ok, qst_ok):
    assert valid_gst_number(number) is gst_ok
    assert valid_qst_number(number) is qst_ok


# --- Checks -------------------------------------------------------------------------------


@pytest.mark.parametrize("name", [ON, QC, BC])
def test_sample_invoices_pass_all_tax_checks(reference, name):
    assert _codes(_gt(name), reference) == set()


def test_tax_amount_must_equal_base_times_rate(reference):
    gt = _gt(ON)
    gt["tax_lines"][0]["tax_amount"] = 2000.00
    gt["tax_total"], gt["grand_total"] = 2000.00, 17945.00
    assert "TAX_CALC_MISMATCH" in _codes(gt, reference)


def test_wrong_rate_for_province(reference):
    gt = _gt(ON)
    gt["tax_lines"][0].update(rate=0.15, tax_amount=2391.75)
    gt["tax_total"], gt["grand_total"] = 2391.75, 18336.75
    assert "TAX_RATE_NONSTANDARD" in _codes(gt, reference)


def test_gst_only_charged_in_hst_province(reference):
    gt = _gt(ON)
    gt["tax_lines"] = [
        {"tax_type": "GST", "province": "", "rate": 0.05, "taxable_amount": 15945.0, "tax_amount": 797.25}
    ]
    gt["tax_total"], gt["grand_total"] = 797.25, 16742.25
    for li in gt["line_items"]:
        li["taxes_applied"] = ["GST"]
    assert "TAX_REGIME_MISMATCH" in _codes(gt, reference)


def test_tax_not_levied_in_province(reference):
    gt = _gt(ON)
    gt["tax_lines"].append({"tax_type": "PST", "province": "ON", "rate": 0.08, "taxable_amount": 100, "tax_amount": 8})
    assert "TAX_TYPE_NOT_LEVIED" in _codes(gt, reference)


def test_qst_on_gst_inclusive_amount_is_an_error(reference):
    gt = _gt(QC)
    qst = gt["tax_lines"][1]
    qst.update(taxable_amount=2520.00, tax_amount=251.37)
    gt["tax_total"], gt["grand_total"] = 371.37, 2771.37
    assert "QST_ON_GST_INCLUSIVE" in _codes(gt, reference)


def test_missing_pst_suggests_self_assessment(reference):
    gt = _gt(BC)
    gt["tax_lines"] = gt["tax_lines"][:1]
    gt["tax_total"], gt["grand_total"] = 136.30, 2862.30
    for li in gt["line_items"]:
        li["taxes_applied"] = ["GST"]
    assert "PROVINCIAL_TAX_NOT_CHARGED" in _codes(gt, reference)


def test_registration_numbers_needed_for_credits(reference):
    gt = _gt(QC)
    gt["gst_hst_registration_number"] = ""
    gt["qst_registration_number"] = "12345"
    assert {"GST_HST_NUMBER_MISSING", "QST_NUMBER_FORMAT"} <= _codes(gt, reference)


def test_tax_lines_must_add_to_tax_total(reference):
    gt = _gt(QC)
    gt["tax_total"] = 300.00
    assert "TAX_LINES_TOTAL_MISMATCH" in _codes(gt, reference)


def test_taxable_base_must_match_flagged_lines(reference):
    gt = _gt(BC)
    gt["line_items"][2]["taxes_applied"] = ["GST"]  # paper no longer marked PST
    assert "TAX_BASE_MISMATCH" in _codes(gt, reference)


def test_unmapped_tax_gl_is_an_error(reference):
    gt = _gt(QC)
    setup = TaxSetup(reference.tax.rates, {})  # nothing mapped
    codes = {f.code for f in check_taxes(InvoiceCoding.model_validate(gt), setup, None)}
    assert "TAX_GL_UNMAPPED" in codes


@pytest.mark.parametrize(("gl_code", "code"), [("", "TAX_GL_UNMAPPED"), ("9999", "TAX_GL_UNKNOWN")])
def test_non_canadian_tax_posted_to_its_own_gl_must_be_mapped(reference, gl_code, code):
    us = _gt(BC)
    us.update(supplier_province="OUTSIDE_CANADA", ship_to_province="", currency="USD", gst_hst_registration_number="")
    us["tax_lines"] = [{"tax_type": "OTHER", "province": "", "rate": 0.08, "taxable_amount": 2726.0,
                        "tax_amount": 218.08}]  # fmt: skip
    us["tax_total"], us["grand_total"] = 218.08, 2944.08
    for li in us["line_items"]:
        li["taxes_applied"] = ["OTHER"]
    treatments = {**reference.tax.treatments, "OTHER": TaxTreatment("OTHER", EXPENSE_SEPARATE, gl_code)}
    setup = TaxSetup(reference.tax.rates, treatments)
    findings = check_taxes(InvoiceCoding.model_validate(us), setup, set(reference.chart_of_accounts.codes))
    assert {f.code: f.severity for f in findings}.get(code) == "error"  # not a silent posting to a blank GL


def test_line_coded_to_tax_account_is_an_error(reference):
    gt = _gt(ON)
    gt["line_items"][0]["predicted_gl_code"] = "2310"
    report = validate_coding(InvoiceCoding.model_validate(gt), reference)
    assert "GL_IS_TAX_ACCOUNT" in {i.code for i in report.issues}


# --- GL distribution -----------------------------------------------------------------------


def test_hst_goes_to_recoverable_account(reference):
    dist = build_gl_distribution(InvoiceCoding.model_validate(_gt(ON)), reference.tax)
    tax = [e for e in dist if e["kind"] == "tax"]
    assert tax == [
        {
            "kind": "tax",
            "line_number": None,
            "gl_code": "2310",
            "cost_center": "",
            "description": "HST ON 13% (recoverable)",
            "net_amount": 0.0,
            "non_recoverable_tax": 0.0,
            "amount": 2072.85,
        }
    ]
    assert round(sum(e["amount"] for e in dist), 2) == 18017.85


def test_gst_and_qst_post_to_separate_accounts(reference):
    dist = build_gl_distribution(InvoiceCoding.model_validate(_gt(QC)), reference.tax)
    assert {(e["gl_code"], e["amount"]) for e in dist if e["kind"] == "tax"} == {("2310", 120.0), ("2320", 239.4)}


def test_pst_is_added_to_expense_lines_pro_rata(reference):
    dist = build_gl_distribution(InvoiceCoding.model_validate(_gt(BC)), reference.tax)
    expense = [(e["gl_code"], e["non_recoverable_tax"], e["amount"]) for e in dist if e["kind"] == "expense"]
    assert expense == [("6000", 62.30, 952.30), ("6010", 92.12, 1408.12), ("6000", 36.40, 556.40)]
    assert round(sum(e["amount"] for e in dist), 2) == 3053.12


def test_pst_only_allocated_to_lines_it_applies_to(reference):
    gt = _gt(BC)
    gt["line_items"][2]["taxes_applied"] = ["GST"]
    dist = build_gl_distribution(InvoiceCoding.model_validate(gt), reference.tax)
    assert dist[2]["non_recoverable_tax"] == 0.0
    assert round(dist[0]["non_recoverable_tax"] + dist[1]["non_recoverable_tax"], 2) == 190.82


def test_pst_to_its_own_expense_account(reference):
    setup = TaxSetup(
        reference.tax.rates, {**reference.tax.treatments, "PST": TaxTreatment("PST", EXPENSE_SEPARATE, "6900")}
    )
    dist = build_gl_distribution(InvoiceCoding.model_validate(_gt(BC)), setup)
    pst = [e for e in dist if e["kind"] == "tax" and e["gl_code"] == "6900"]
    assert pst and pst[0]["amount"] == 190.82 and pst[0]["non_recoverable_tax"] == 190.82


def test_rounding_remainder_keeps_total_exact(reference):
    gt = copy.deepcopy(_gt(BC))
    gt["tax_lines"][1]["tax_amount"] = 190.83  # a cent of vendor rounding
    gt["tax_total"], gt["grand_total"] = 327.13, 3053.13
    dist = build_gl_distribution(InvoiceCoding.model_validate(gt), reference.tax)
    assert round(sum(e["amount"] for e in dist), 2) == 3053.13


def _on_lines(amounts, hst):
    """The Ontario sample with its lines replaced: ``amounts``, all charged HST, and ``hst`` as printed."""
    gt = _gt(ON)
    item = gt["line_items"][0]
    gt["line_items"] = [
        {**item, "line_number": n, "quantity": 1, "unit_price": a, "amount": a} for n, a in enumerate(amounts, 1)
    ]
    subtotal = round(sum(amounts), 2)
    gt["tax_lines"] = [
        {"tax_type": "HST", "province": "ON", "rate": 0.13, "taxable_amount": subtotal, "tax_amount": hst}
    ]
    gt.update(subtotal=subtotal, tax_total=hst, grand_total=round(subtotal + hst, 2))
    return gt


def _findings(coding_dict, reference):
    return {(f.code, f.severity) for f in check_taxes(InvoiceCoding.model_validate(coding_dict), reference.tax, None)}


@pytest.mark.parametrize(
    ("amounts", "hst", "severity"),
    [
        ([200.0] * 5, 130.05, "error"),  # 5 cents over on 1,000.00: no rounding explains it
        ([200.0] * 40, 1040.40, "error"),  # 40 cents over on a long invoice
        ([100.01] * 40, 520.15, "warning"),  # 0.10 off: within half a cent a line, but still shown
    ],
)
def test_tax_overcharge_is_never_passed_silently(reference, amounts, hst, severity):
    assert ("TAX_CALC_MISMATCH", severity) in _findings(_on_lines(amounts, hst), reference)


@pytest.mark.parametrize(
    ("amounts", "hst"),
    [
        ([200.0] * 5, 130.00),  # worked out once on the taxable amount
        ([200.0] * 5, 130.01),  # a cent of rounding
        ([0.35] * 10, 0.50),  # worked out line by line (0.0455 → 0.05 each), not 0.46 on the total
    ],
)
def test_correctly_rounded_tax_passes(reference, amounts, hst):
    assert _codes(_on_lines(amounts, hst), reference) == set()


@pytest.mark.parametrize(
    ("tax_type", "province", "date", "rate"),
    [
        ("PST", "SK", "2017-03-22", 0.05),
        ("PST", "SK", "2017-03-23", 0.06),
        ("HST", "BC", "2012-06-01", 0.12),
        ("PST", "BC", "2012-06-01", None),
        ("PST", "BC", "2010-06-30", 0.07),
        ("PST", "BC", "2013-04-01", 0.07),
        ("HST", "BC", "2013-04-01", None),
        ("PST", "MB", "2013-06-30", 0.07),
        ("PST", "MB", "2013-07-01", 0.08),
    ],
)
def test_historical_rates(tax_type, province, date, rate):
    assert TaxRateTable.load().rate_for(tax_type, province, dt.date.fromisoformat(date)) == rate


def test_older_invoices_check_against_the_rates_of_their_day(reference):
    sk = _gt("prairie_SK_GST_PST_PNS-104882")
    pst = next(t for t in sk["tax_lines"] if t["tax_type"] == "PST")
    old = round(pst["taxable_amount"] * 0.05, 2)
    sk["tax_total"] = round(sk["tax_total"] - pst["tax_amount"] + old, 2)
    sk["grand_total"] = round(sk["subtotal"] + sk["tax_total"], 2)
    pst.update(rate=0.05, tax_amount=old)
    sk["invoice_date"] = "2016-05-02"
    assert _codes(sk, reference) == set()
    # British Columbia charged HST (12%) from July 2010 to March 2013, instead of GST + PST.
    bc = _gt(BC)
    bc["tax_lines"] = [
        {"tax_type": "HST", "province": "BC", "rate": 0.12, "taxable_amount": 2726.0, "tax_amount": 327.12}
    ]
    for li in bc["line_items"]:
        li["taxes_applied"] = ["HST"]
    assert _codes({**bc, "invoice_date": "2012-01-16"}, reference) == set()
    assert {"TAX_TYPE_NOT_LEVIED", "TAX_REGIME_MISMATCH"} <= _codes(bc, reference)  # the same invoice in 2026


def test_qst_rate_keeps_its_three_decimals_in_the_posting(reference):
    dist = build_gl_distribution(InvoiceCoding.model_validate(_gt(QC)), reference.tax)
    assert any("QST QC 9.975%" in e["description"] for e in dist)
    assert not any("9.98%" in e["description"] for e in dist)


def test_tax_spread_over_lines_in_whole_yen(reference):
    gt = _gt(BC)
    gt["line_items"] = gt["line_items"][:3]
    for li, amount in zip(gt["line_items"], (891.0, 1317.0, 519.0), strict=True):
        li.update(quantity=1, unit_price=amount, amount=amount, taxes_applied=["GST", "PST"])
    subtotal = 2727.0
    gt["tax_lines"] = [
        {"tax_type": "GST", "province": "", "rate": 0.05, "taxable_amount": subtotal, "tax_amount": 136.0},
        {"tax_type": "PST", "province": "BC", "rate": 0.07, "taxable_amount": subtotal, "tax_amount": 191.0},
    ]
    gt.update(currency="JPY", subtotal=subtotal, tax_total=327.0, grand_total=subtotal + 327.0)
    dist = build_gl_distribution(InvoiceCoding.model_validate(gt), reference.tax)
    assert all(e["amount"] == round(e["amount"]) for e in dist)  # the PST (spread over the lines) in whole yen
    assert sum(e["non_recoverable_tax"] for e in dist if e["kind"] == "expense") == 191.0
    assert sum(e["amount"] for e in dist) == gt["grand_total"]


# --- Place of supply: where it is delivered to us, not where the vendor is ---------------------------------------


def _supply(ship_to="", supplier="", centers=()):
    from types import SimpleNamespace

    lines = [SimpleNamespace(predicted_cost_center=c) for c in centers]
    return SimpleNamespace(ship_to_province=ship_to, supplier_province=supplier, line_items=lines)


def test_place_of_supply_is_where_it_is_delivered_to_us():
    from ap_coder.coding.tax import TaxRateTable, TaxSetup, supply_source

    setup = TaxSetup(TaxRateTable.load(), our_province="BC", site_provinces={"CC500": "ON", "CC600": "AB"})
    # A vendor in Ontario shipping from BC to our BC site: BC (GST + PST), never Ontario's HST.
    assert supply_source(_supply(supplier="ON"), setup) == ("BC", "ours")
    assert supply_source(_supply(ship_to="AB", supplier="ON"), setup) == ("AB", "printed")  # a ship-to printed wins
    assert supply_source(_supply(supplier="ON", centers=["CC500", "CC500"]), setup) == ("ON", "site")
    assert supply_source(_supply(supplier="ON", centers=["CC500", "CC600"]), setup) == ("BC", "ours")  # two sites
    assert supply_source(_supply(supplier="ON"), TaxSetup(TaxRateTable.load())) == ("ON", "vendor")  # last resort
    assert supply_source(_supply(), TaxSetup(TaxRateTable.load())) == ("", "")


def test_a_line_on_an_unlisted_site_is_delivered_to_our_province():
    """B-133: a cost center not in the site list (or none) is one of our home sites: one line to our BC site and
    one to an Alberta site is not "every line to BC", so the place of supply stays ours, in the tax checks and in the
    tax return alike (one rule, B-13)."""
    from ap_coder.coding import taxreturn
    from ap_coder.coding.tax import TaxRateTable, TaxSetup, supply_source

    setup = TaxSetup(TaxRateTable.load(), our_province="AB", site_provinces={"CC400": "BC"})
    assert supply_source(_supply(supplier="BC", centers=["CC400", "CC100"]), setup) == ("AB", "ours")
    assert supply_source(_supply(supplier="BC", centers=["CC400", ""]), setup) == ("AB", "ours")
    assert supply_source(_supply(supplier="ON", centers=["CC400", "CC400"]), setup) == ("BC", "site")
    doc = {"supplier_province": "BC", "line_items": [{"predicted_cost_center": "CC400"}, {"predicted_cost_center": ""}]}
    assert taxreturn.supply_province(doc, setup) == "AB"
    # Without our province set, an unlisted cost center says nothing: the listed site still decides.
    no_home = TaxSetup(TaxRateTable.load(), site_provinces={"CC400": "BC"})
    assert supply_source(_supply(supplier="ON", centers=["CC400", "CC100"]), no_home) == ("BC", "site")


def test_a_site_typed_in_lower_case_is_still_that_cost_center():
    """B-134: "cc400=BC" typed in Settings → Review is cost center CC400 (cost centers compare as the JD Edwards
    mapping does, whatever their case): its lines are delivered to BC, not silently to our province."""
    from ap_coder.coding.tax import TaxRateTable, TaxSetup, supply_source

    setup = TaxSetup(TaxRateTable.load(), our_province="AB", site_provinces={"cc400 ": "BC"})
    assert supply_source(_supply(supplier="ON", centers=["CC400"]), setup) == ("BC", "site")
    assert supply_source(_supply(supplier="ON", centers=[" Cc400"]), setup) == ("BC", "site")


def test_an_invoice_without_a_ship_to_is_taxed_where_we_are(ground_truth, reference):
    from dataclasses import replace

    from ap_coder.ai.pipeline import finalise_coding
    from ap_coder.core.config import Settings

    doc = InvoiceCoding.model_validate({**ground_truth, "ship_to_province": "", "supplier_province": "ON"})
    # HST charged by an Ontario vendor on goods delivered to us in BC: the wrong tax.
    ours = replace(reference, tax=replace(reference.tax, our_province="BC"))
    output, report = finalise_coding(doc, ours, Settings())
    assert output["place_of_supply"] == "BC" and output["ship_to_province"] == ""  # worked out, never "printed"
    assert "TAX_REGIME_MISMATCH" in {i.code for i in report.issues}
    output, _ = finalise_coding(doc, reference, Settings())  # no province of ours: left as printed (none)
    assert output["ship_to_province"] == "" and "place_of_supply" not in output


def test_a_worked_out_place_of_supply_follows_the_recoded_lines(reference):
    """S2-T1: the province worked out from the cost center is not stored as the printed ship-to, so recoding the
    lines to a site in another province moves it, and the review screen says where it came from."""
    from dataclasses import replace

    from ap_coder.ai.pipeline import finalise_coding
    from ap_coder.core.config import Settings
    from ap_coder.web.invoice_view import supply_line

    ours = replace(reference, tax=replace(reference.tax, our_province="AB", site_provinces={"CC400": "BC"}))
    doc = _gt("chinook_AB_GST_CCO-26-10418")
    doc = {**doc, "ship_to_province": "", "line_items": [{**li, "predicted_cost_center": "CC400"}
                                                         for li in doc["line_items"]]}  # fmt: skip
    out1, rep1 = finalise_coding(InvoiceCoding.model_validate(doc), ours, Settings())
    assert out1["ship_to_province"] == "" and out1["place_of_supply"] == "BC"
    assert "PROVINCIAL_TAX_NOT_CHARGED" in {i.code for i in rep1.issues}
    # The reviewer recodes every line to head office (AB); the form posts back the header as stored.
    edited = {k: v for k, v in out1.items() if k != "gl_distribution"}
    edited["line_items"] = [{**li, "predicted_cost_center": "CC100"} for li in edited["line_items"]]
    out2, rep2 = finalise_coding(InvoiceCoding.model_validate(edited), ours, Settings())
    assert out2["place_of_supply"] == "AB"
    assert "PROVINCIAL_TAX_NOT_CHARGED" not in {i.code for i in rep2.issues}
    line = supply_line(edited, edited["line_items"], ours.tax)
    assert line["province"] == "AB" and line["why"] == "your province (Settings): no ship-to or bill-to printed"
    # And back to the BC site: BC PST missing is flagged again.
    back = {**edited, "line_items": [{**li, "predicted_cost_center": "CC400"} for li in edited["line_items"]]}
    out3, rep3 = finalise_coding(InvoiceCoding.model_validate(back), ours, Settings())
    assert out3["place_of_supply"] == "BC" and "PROVINCIAL_TAX_NOT_CHARGED" in {i.code for i in rep3.issues}
    assert supply_line(back, back["line_items"], ours.tax)["why"].startswith("the province of the cost center")
    # A ship-to printed (or set by the reviewer) to our own province is said to be the ship-to, not Settings.
    printed = {**back, "ship_to_province": "AB"}
    why = supply_line(printed, printed["line_items"], ours.tax)["why"]
    assert why == "the invoice's ship-to, else its bill-to (or as you set it)"


def test_a_new_rate_row_wins_over_an_old_one_left_open():
    from ap_coder.coding.tax import TaxRate

    table = TaxRateTable.load()
    # Finance appends Saskatchewan's new rate but forgets to close the old row.
    newer = TaxRateTable(table.rates + (TaxRate("PST", "SK", 0.07, dt.date(2026, 7, 1), None),))
    assert newer.rate_for("PST", "SK", dt.date(2026, 9, 1)) == 0.07
    assert newer.rate_for("PST", "SK", dt.date(2026, 6, 30)) == 0.06
    shown = [r for r in newer.to_markdown(dt.date(2026, 9, 1)).splitlines() if "| PST | SK |" in r]
    assert shown == ["| PST | SK | 0.07 |"]  # one row, the rate in force
    assert newer.overlaps() and not table.overlaps()


@pytest.mark.parametrize("rate", ["nan", "inf", "-inf", "Infinity", "NaN%"])
def test_a_rate_that_is_not_a_finite_number_is_refused(tmp_path, rate):
    """float() reads "nan" and "inf": a rate like that is refused like any other text that is not a number."""
    path = tmp_path / "rates.csv"
    path.write_text(f"tax_type,province,rate,effective_from,effective_to\nGST,,{rate},2008-01-01,\n")
    with pytest.raises(ValueError, match="is not a number"):
        TaxRateTable.load(path)


def test_a_rate_written_with_a_percent_sign_is_always_a_percentage(tmp_path):
    path = tmp_path / "rates.csv"
    path.write_text(
        "tax_type,province,rate,effective_from,effective_to\nPST,BC,0.5%,2026-01-01,\nGST,,5%,2008-01-01,\n"
    )
    table = TaxRateTable.load(path)
    assert table.rate_for("PST", "BC", dt.date(2026, 2, 1)) == pytest.approx(0.005)
    assert table.rate_for("GST", "BC", dt.date(2026, 2, 1)) == pytest.approx(0.05)
