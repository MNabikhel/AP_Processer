"""Canadian sales tax: rates, calculation checks, regime checks and GL distribution."""

import copy
import datetime as dt
import json

import pytest

from ap_coder.schema import InvoiceCoding
from ap_coder.tax import (
    EXPENSE_SEPARATE,
    TaxRateTable,
    TaxSetup,
    TaxTreatment,
    build_gl_distribution,
    check_taxes,
    valid_gst_number,
    valid_qst_number,
)
from ap_coder.validation import validate_coding

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
