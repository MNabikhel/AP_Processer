"""The CPA's final check of the line tax codes (D-64): B-719 to B-723."""

from __future__ import annotations

import datetime as dt

from ap_coder.ai.offline_coder import tax_codes
from ap_coder.coding.line_tax import confirm_line, exemption_to_confirm, inferred_note, tax_code_of
from ap_coder.coding.tax import check_taxes
from ap_coder.coding.taxmath import math_check
from ap_coder.core.schema import LineItem, TaxLine

from .test_line_tax_review import _invoice


def _bc():
    lines = [LineItem(line_number=i + 1, description=d, amount=a, quantity=1, unit_price=a, predicted_gl_code="6000")
             for i, (d, a) in enumerate([("Laptop", 1000.0), ("Bookkeeping setup", 300.0)])]  # fmt: skip
    taxes = [TaxLine(tax_type="GST", rate=0.05, taxable_amount=1300.0, tax_amount=65.0),
             TaxLine(tax_type="PST", province="BC", rate=0.07, taxable_amount=1000.0, tax_amount=70.0)]  # fmt: skip
    return lines, taxes


# --- B-719: no PST number printed, but PST charged: the vendor collects it ------------------------------------------


def test_a_missing_pst_number_does_not_make_a_line_due_when_pst_is_charged_b719():
    lines, taxes = _bc()
    got = tax_codes(lines, taxes, [], "", "BC", "2026-06-01", supplier_province="BC", pst_number="")
    assert [tax_code_of(li) for li in got] == ["GST+PST", "GST~PST"]
    assert exemption_to_confirm(got[1]) == "PST"  # asked, as B-715 asks it
