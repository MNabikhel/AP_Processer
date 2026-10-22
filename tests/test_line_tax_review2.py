"""The CPA's second review of the line tax codes (D-64): B-714 to B-718."""

from __future__ import annotations

import random

from ap_coder.coding.line_tax import (
    GUESS_NOTE,
    code_label,
    confirm_line,
    exemption_to_confirm,
    options,
    prefill,
    tax_code_of,
    tax_guessed,
)
from ap_coder.coding.review import codes_to_pick
from ap_coder.coding.tax import check_taxes
from ap_coder.coding.taxmath import math_check
from ap_coder.core.schema import InvoiceCoding, LineItem, TaxLine

from .test_line_tax_review import _invoice


def _lines(pairs):
    return [LineItem(line_number=i + 1, description=d, amount=a, quantity=1, unit_price=a, predicted_gl_code="6000")
            for i, (d, a) in enumerate(pairs)]  # fmt: skip


def _bc22(taxable_printed: str = "partial"):
    """22 lines in BC: PST rightly charged on the 15 goods, not on the 7 installation lines."""
    rng = random.Random(5)
    amounts = [round(rng.uniform(10, 500), 2) for _ in range(22)]
    descriptions = [f"Hardware item {i}" for i in range(15)] + [f"Installation labour {i}" for i in range(7)]
    pst_base, subtotal = round(sum(amounts[:15]), 2), round(sum(amounts), 2)
    taxable = subtotal if taxable_printed == "subtotal" else pst_base
    return _invoice("BC", list(zip(descriptions, amounts, strict=True)),
                    [("GST", "", 0.05, subtotal, round(subtotal * 0.05, 2)),
                     ("PST", "BC", 0.07, taxable, round(pst_base * 0.07, 2))])  # fmt: skip


# --- B-714: a total right for some of the lines is no vendor error ----------------------------------------------------


def test_a_correct_vendor_is_not_blamed_when_the_lines_can_make_its_tax_b714(reference):
    for printed in ("partial", "subtotal"):
        coding = _bc22(printed)
        assert all(tax_guessed(li) for li in coding.line_items)  # several ways: every line to check
        row = next(r for r in math_check(coding, reference.tax)["rows"] if r.get("tax_type") == "PST")
        assert not row["ok"] and "pick which lines carry PST" in row["why"]
        assert "vendor" not in row["why"] and "claimed" not in row["why"]
        found = [f for f in check_taxes(coding, reference.tax) if f.code in ("TAX_CALC_MISMATCH", "TAX_BASE_MISMATCH")]
        assert found and all(f.severity == "warning" for f in found)
        assert all("pick which lines carry PST" in f.message and "vendor" not in f.message for f in found)


def test_a_tax_no_set_of_lines_makes_is_the_vendors_and_pst_is_never_said_claimable_b714(reference):
    taxes = [("GST", "", 0.05, 1000.0, 50.0), ("PST", "BC", 0.07, 1000.0, 90.0)]
    pst = _invoice("BC", [("Desk", 1000.0, "GST+PST")], taxes, prefilled=False)
    row = next(r for r in math_check(pst, reference.tax)["rows"] if r.get("tax_type") == "PST")
    assert "the vendor's tax looks wrong" in row["why"] and "claimed" not in row["why"]
    hst = _invoice("ON", [("Desk", 1000.0)], [("HST", "ON", 0.13, 1000.0, 140.0)])
    row = next(r for r in math_check(hst, reference.tax)["rows"] if r.get("tax_type") == "HST")
    assert "the vendor's tax looks wrong" in row["why"] and "can't be claimed" in row["why"]


def test_the_posting_preview_says_pst_is_spread_over_lines_still_to_check_b714(reference):
    from ap_coder.web.invoice_view import posting_note

    assert "still to check" in posting_note(_bc22(), reference)
    taxes = [("GST", "", 0.05, 1000.0, 50.0), ("PST", "BC", 0.07, 1000.0, 70.0)]
    clear = _invoice("BC", [("Desk", 1000.0, "GST+PST")], taxes, prefilled=False)
    assert posting_note(clear, reference) == ""


# --- B-715: an inferred PST exemption is confirmed by a person -------------------------------------------------------


def _bc_partial(**prefill_args):
    lines = _lines([("Laptop", 1000.0), ("Bookkeeping setup", 300.0)])
    taxes = [TaxLine(tax_type="GST", rate=0.05, taxable_amount=1300.0, tax_amount=65.0),
             TaxLine(tax_type="PST", province="BC", rate=0.07, taxable_amount=1000.0, tax_amount=70.0)]  # fmt: skip
    return prefill(lines, taxes, **prefill_args)


def test_an_inferred_pst_exemption_must_be_confirmed_before_approval_b715():
    got = _bc_partial()
    line = got.lines[1]
    assert tax_code_of(line) == "GST~PST" and exemption_to_confirm(line) == "PST"
    pick = codes_to_pick(got.lines, cost_centers=False)
    assert pick == "Confirm the PST on line 2: exempt, or not charged (self-assess)?"
    # approving does not confirm it (B-713 confirms only the codes worked out to check)
    from ap_coder.coding.line_tax import confirm_codes

    output = {"line_items": [li.model_dump() for li in got.lines]}
    confirm_codes(output)
    assert exemption_to_confirm(output["line_items"][1]) == "PST"
    # confirmed by the reviewer: recorded in the line's reasoning, and the approval may go on
    confirmed = line.model_copy(
        update={"reasoning_justification": confirm_line(line.reasoning_justification, "GST~PST")}
    )
    assert "PST exemption confirmed on review" in confirmed.reasoning_justification
    assert exemption_to_confirm(confirmed) == "" and codes_to_pick([got.lines[0], confirmed], False) == ""
    # picking "not charged (self-assess)" resolves it too
    reason = confirm_line(line.reasoning_justification, "GST")
    due = line.model_copy(update={"taxes_applied": ["GST"], "tax_exempt": [], "reasoning_justification": reason})
    assert exemption_to_confirm(due) == "" and "confirmed" not in due.reasoning_justification


def test_printed_codes_need_no_confirmation_b715():
    legend_text = {"G": ("GST",), "P": ("PST",)}
    got = prefill(_lines([("Laptop", 1000.0), ("Bookkeeping setup", 300.0)]),
                  [TaxLine(tax_type="GST", rate=0.05, taxable_amount=1300.0, tax_amount=65.0),
                   TaxLine(tax_type="PST", province="BC", rate=0.07, taxable_amount=1000.0, tax_amount=70.0)],
                  ["GP", "G"], legend_text)  # fmt: skip
    assert tax_code_of(got.lines[1]) == "GST~PST" and exemption_to_confirm(got.lines[1]) == ""


def test_an_out_of_province_vendor_or_one_without_a_pst_number_is_due_not_exempt_b715():
    for args in ({"vendor_collects": False},):
        got = _bc_partial(**args)
        assert tax_code_of(got.lines[1]) == "GST" and exemption_to_confirm(got.lines[1]) == ""
    from ap_coder.ai.offline_coder import tax_codes

    lines = _lines([("Laptop", 1000.0), ("Bookkeeping setup", 300.0)])
    taxes = [TaxLine(tax_type="GST", rate=0.05, taxable_amount=1300.0, tax_amount=65.0),
             TaxLine(tax_type="PST", province="BC", rate=0.07, taxable_amount=1000.0, tax_amount=70.0)]  # fmt: skip
    away = tax_codes(lines, taxes, [], "", "BC", "2026-06-01", supplier_province="AB", pst_number="PST-1234-5678")
    nonum = tax_codes(lines, taxes, [], "", "BC", "2026-06-01", supplier_province="BC", pst_number="")
    home = tax_codes(lines, taxes, [], "", "BC", "2026-06-01", supplier_province="BC", pst_number="PST-1234-5678")
    assert [tax_code_of(li) for li in away] == [tax_code_of(li) for li in nonum] == ["GST+PST", "GST"]
    assert [tax_code_of(li) for li in home] == ["GST+PST", "GST~PST"]


def test_the_review_screen_blocks_approval_until_the_pst_is_confirmed_b715(reference):
    coding = InvoiceCoding.model_validate({
        "vendor_name": "V", "invoice_number": "1", "invoice_date": "2026-06-01", "currency": "CAD",
        "confidence_score": 0.9, "subtotal": 1300.0, "tax_total": 135.0, "grand_total": 1435.0,
        "ship_to_province": "BC", "supplier_province": "BC",
        "line_items": [li.model_dump() for li in _bc_partial().lines],
        "tax_lines": [{"tax_type": "GST", "rate": 0.05, "taxable_amount": 1300.0, "tax_amount": 65.0},
                      {"tax_type": "PST", "province": "BC", "rate": 0.07, "taxable_amount": 1000.0,
                       "tax_amount": 70.0}],
    })  # fmt: skip
    found = [f for f in check_taxes(coding, reference.tax) if f.code == "PST_EXEMPTION_TO_CONFIRM"]
    assert [f.line_number for f in found] == [2]
    from ap_coder.capture.supplier import ALWAYS_A_PERSON

    assert "PST_EXEMPTION_TO_CONFIRM" in ALWAYS_A_PERSON  # never approved without a person


def test_the_audit_trail_records_the_confirmed_exemption_b715():
    from ap_coder.core.audit import diff_coding

    line = _bc_partial().lines[1].model_dump()
    final = {**line, "reasoning_justification": confirm_line(line["reasoning_justification"], "GST~PST")}
    change = next(c for c in diff_coding({"line_items": [line]}, {"line_items": [final]})
                  if c["what"] == "line 2 PST exemption")  # fmt: skip
    assert (change["before"], change["after"]) == ("inferred", "confirmed on review")


# --- B-716: the self-assessment and JD Edwards' use-tax code -------------------------------------------------------


def test_the_dry_run_warns_that_a_use_tax_code_and_the_self_assessment_could_accrue_twice_b716():
    from ap_coder.erp import jde

    from .test_jde import AN8, SK
    from .test_jde import _invoice as jde_invoice

    doc_inv = jde_invoice(SK)
    final = doc_inv["final_output"]
    final["tax_lines"] = [t for t in final["tax_lines"] if t["tax_type"] != "PST"]
    final["tax_total"] = sum(t["tax_amount"] for t in final["tax_lines"])
    final["grand_total"] = round(final["subtotal"] + final["tax_total"], 2)
    for li in final["line_items"]:
        li["taxes_applied"], li["tax_exempt"] = ["GST"], []
    from ap_coder.coding.tax import TaxRateTable, TaxSetup, build_gl_distribution, load_tax_mapping
    from ap_coder.core.schema import InvoiceCoding as IC

    from .conftest import DATA

    setup = TaxSetup(TaxRateTable.load(DATA / "canada_tax_rates.csv"), load_tax_mapping(DATA / "tax_gl_mapping.csv"))
    final["gl_distribution"] = build_gl_distribution(
        IC.model_validate({k: v for k, v in final.items() if k != "gl_distribution"}), setup
    )
    report = jde.dry_run([doc_inv], jde.JdeSettings(), AN8)
    voucher = report["vouchers"][0]
    assert voucher["tax_code"] == "B"
    assert any("accrued twice" in w for w in voucher["warnings"])
    assert "accrued twice" in jde.dry_run_text(report)


# --- B-717: GST charged where HST applies is no ITC risk ------------------------------------------------------------


def test_gst_charged_where_hst_applies_is_hst_not_charged_not_an_itc_risk_b717(reference):
    on = _invoice("ON", [("Software", 1000.0)], [("GST", "", 0.05, 1000.0, 50.0)], supplier="AB")
    codes = {f.code: f for f in check_taxes(on, reference.tax)}
    assert "ITC_AT_RISK" not in codes and "HST_NOT_CHARGED" in codes
    message = codes["HST_NOT_CHARGED"].message
    assert "the GST is claimable" in message and "ETA s.218.1" in message and "13%" in message
    from ap_coder.ops.help import CHECKS

    assert "HST_NOT_CHARGED" in CHECKS


# --- B-718: QST not charged is not "self-assess" -----------------------------------------------------------------


def test_qst_not_charged_is_said_without_self_assess_b718(reference):
    assert code_label("GST", "QC") == "GST only · QST not charged"
    assert "GST only · QST not charged" in [label for _, label in options("QC", ["GST", "QST"])]
    assert code_label("GST", "BC") == "GST only · PST not charged (self-assess)"
    coding = _invoice("QC", [("Software", 1000.0, "GST")], [("GST", "", 0.05, 1000.0, 50.0)], prefilled=False)
    found = [f for f in check_taxes(coding, reference.tax) if f.code == "PROVINCIAL_TAX_NOT_CHARGED"]
    assert found and "generally owes nothing net" in found[0].message and "it is due" not in found[0].message
    assert GUESS_NOTE not in found[0].message
