"""The review screen's math, line by line (invoice_view.math_check): the Canadian cases a pilot tester meets."""

from __future__ import annotations

import pytest

from ap_coder.core.schema import InvoiceCoding
from ap_coder.web.invoice_view import math_check


def _coding(lines, taxes, *, province="BC", currency="CAD", total=None):
    """lines: (amount, [taxes]); taxes: (type, rate, taxable, amount)."""
    subtotal = round(sum(a for a, _ in lines), 2)
    tax_total = round(sum(t[3] for t in taxes), 2)
    return InvoiceCoding.model_validate({
        "vendor_name": "Vendor", "invoice_number": "1", "invoice_date": "2026-10-05", "currency": currency,
        "supplier_province": province, "ship_to_province": province, "subtotal": subtotal,
        "tax_lines": [{"tax_type": t, "province": "" if t == "GST" else province, "rate": r, "taxable_amount": b,
                       "tax_amount": a} for t, r, b, a in taxes],
        "tax_total": tax_total, "grand_total": round(subtotal + tax_total, 2) if total is None else total,
        "confidence_score": 1.0,
        "line_items": [{"line_number": i, "description": f"Line {i}", "quantity": 1, "unit_price": a, "amount": a,
                        "predicted_gl_code": "6000", "predicted_cost_center": "", "taxes_applied": list(tx),
                        "reasoning_justification": ""} for i, (a, tx) in enumerate(lines, start=1)],
    })  # fmt: skip


def _rows(m):
    return {r["what"]: r for r in m["rows"]}


def test_bc_gst_and_pst_on_every_line_adds_up():
    m = math_check(_coding([(890, ["GST", "PST"]), (1316, ["GST", "PST"]), (520, ["GST", "PST"])],
                           [("GST", 0.05, 2726, 136.30), ("PST", 0.07, 2726, 190.82)]))  # fmt: skip
    assert m["ok"] and m["text"] == "All adds up"
    assert "every line (2,726.00) × 7% = 190.82" in _rows(m)["PST"]["detail"]


def test_pst_on_some_lines_only():
    """BC: software or a service exempt from PST on the same invoice as taxable goods: PST only on the ticked lines."""
    lines = [(1000, ["GST", "PST"]), (500, ["GST"]), (250, ["GST", "PST"])]
    m = math_check(_coding(lines, [("GST", 0.05, 1750, 87.50), ("PST", 0.07, 1250, 87.50)]))
    assert m["ok"] and "2 of 3 lines (1,250.00)" in _rows(m)["PST"]["detail"]
    # PST charged on all three: the exempt line was taxed.
    wrong = math_check(_coding(lines, [("GST", 0.05, 1750, 87.50), ("PST", 0.07, 1750, 122.50)]))
    assert not wrong["ok"] and _rows(wrong)["PST"]["diff"] == "35.00" and "PST" in wrong["text"]


def test_qst_is_on_the_price_before_gst():
    lines = [(1000, ["GST", "QST"])]
    m = math_check(_coding(lines, [("GST", 0.05, 1000, 50.00), ("QST", 0.09975, 1000, 99.75)], province="QC"))
    assert m["ok"]
    on_gst = math_check(_coding(lines, [("GST", 0.05, 1000, 50.00), ("QST", 0.09975, 1050, 104.74)], province="QC"))
    assert not _rows(on_gst)["QST"]["ok"]  # QST worked out on the GST-included price


@pytest.mark.parametrize(("province", "rate"), [("ON", 0.13), ("NS", 0.14), ("NB", 0.15), ("PE", 0.15)])
def test_hst_provinces(province, rate):
    lines = [(200, ["HST"]), (300, ["HST"])]
    m = math_check(_coding(lines, [("HST", rate, 500, round(500 * rate, 2))], province=province))
    assert m["ok"]


def test_rounding_line_by_line_is_not_a_difference():
    """The vendor rounded the tax on each line, then added: a cent or two off the tax on the total, still right."""
    lines = [(10.10, ["GST", "PST"])] * 7
    per_line_pst = round(7 * round(10.10 * 0.07, 2), 2)  # 7 x 0.71 = 4.97, against 70.70 x 7% = 4.95
    m = math_check(_coding(lines, [("GST", 0.05, 70.70, 3.54), ("PST", 0.07, 70.70, per_line_pst)]))
    assert m["ok"]


def test_a_credit_note_adds_up_in_the_negative():
    lines = [(-500, ["GST", "PST"])]
    m = math_check(_coding(lines, [("GST", 0.05, -500, -25.00), ("PST", 0.07, -500, -35.00)]))
    assert m["ok"]


def test_freight_taxed_or_not():
    """Freight is taxed when its line is ticked (GST: yes; BC PST: when the goods are), else not."""
    goods, freight = (1000, ["GST", "PST"]), (50, ["GST"])
    m = math_check(_coding([goods, freight], [("GST", 0.05, 1050, 52.50), ("PST", 0.07, 1000, 70.00)]))
    assert m["ok"]
    taxed = math_check(_coding([goods, (50, ["GST", "PST"])], [("GST", 0.05, 1050, 52.50), ("PST", 0.07, 1050, 73.50)]))
    assert taxed["ok"]


def test_a_us_vendor_without_canadian_tax_and_with_sales_tax():
    m = math_check(_coding([(6860, [])], [], province="OUTSIDE_CANADA", currency="USD"))
    assert m["ok"] and set(_rows(m)) == {"Lines = subtotal", "Subtotal + tax = total"}
    other = math_check(_coding([(1000, ["OTHER"])], [("OTHER", 0.0725, 1000, 72.50)], province="OUTSIDE_CANADA",
                               currency="USD"))  # fmt: skip
    assert other["ok"]


def test_a_tax_charged_on_no_line_and_a_total_that_does_not_add_up():
    m = math_check(_coding([(100, ["GST"])], [("GST", 0.05, 100, 5.00), ("PST", 0.07, 100, 7.00)], total=120.00))
    rows = _rows(m)
    assert not rows["PST"]["ok"] and "no line is marked PST" in rows["PST"]["detail"]
    assert not rows["Subtotal + tax = total"]["ok"] and rows["Subtotal + tax = total"]["diff"] == "8.00"
    assert m["text"].startswith("2 differences")


def test_lines_that_do_not_add_up_to_the_subtotal():
    c = _coding([(100, ["GST"]), (50, ["GST"])], [("GST", 0.05, 150, 7.50)])
    c.subtotal = 160.0
    c.grand_total = 167.5
    assert _rows(math_check(c))["Lines = subtotal"]["diff"] == "-10.00"  # less: a minus (B-805)


def test_a_yen_invoice_is_rounded_to_whole_yen_as_the_tax_checks_do():
    """S2-T4: 5% GST on 1,234 yen is 61.7, charged 62: no difference (check_taxes rounds to whole yen too)."""
    m = math_check(_coding([(1234, ["GST"])], [("GST", 0.05, 1234, 62)], province="AB", currency="JPY"))
    assert m["ok"], m["text"]
    off = math_check(_coding([(1234, ["GST"])], [("GST", 0.05, 1234, 64)], province="AB", currency="JPY"))
    assert not _rows(off)["GST"]["ok"]  # two yen off is a difference
