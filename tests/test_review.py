"""Dashboard edit -> InvoiceCoding conversion (the Streamlit grids hand back DataFrames)."""

import math

import pandas as pd

from ap_coder.reference_data import UNASSIGNED
from ap_coder.review import coding_from_inputs

HEADER_FIELDS = (
    "vendor_name", "invoice_number", "invoice_date", "po_number", "payment_terms", "due_date", "currency",
    "supplier_province",
    "ship_to_province", "gst_hst_registration_number", "qst_registration_number", "remit_bank_account",
    "subtotal", "tax_total", "grand_total",
)  # fmt: skip


def _inputs(gt):
    header = {k: gt[k] for k in HEADER_FIELDS}
    return header, pd.DataFrame(gt["line_items"]), pd.DataFrame(gt["tax_lines"])


def test_unchanged_grids_round_trip(ground_truth):
    header, lines, taxes = _inputs(ground_truth)
    coding, problems = coding_from_inputs(header, lines, taxes, ground_truth)
    assert problems == []
    assert coding.to_output() == ground_truth


def test_added_row_gets_next_number_and_defaults(ground_truth):
    header, lines, taxes = _inputs(ground_truth)
    added = {"line_number": math.nan, "description": "Eco fee", "quantity": math.nan, "unit_price": math.nan,
             "amount": "1,234.50", "predicted_gl_code": None, "predicted_cost_center": None,
             "taxes_applied": None, "reasoning_justification": None}  # fmt: skip
    blank = dict.fromkeys(added, None)
    lines = pd.concat([lines, pd.DataFrame([added, blank])], ignore_index=True)
    coding, problems = coding_from_inputs(header, lines, taxes, ground_truth)
    assert problems == []
    new = coding.line_items[-1]
    assert len(coding.line_items) == 6, "the completely blank row is ignored"
    assert (new.line_number, new.quantity, new.unit_price, new.amount) == (6, 1.0, 1234.5, 1234.5)
    assert new.predicted_gl_code == UNASSIGNED and new.taxes_applied == []


def test_invalid_edits_are_reported_not_raised(ground_truth):
    header, lines, taxes = _inputs(ground_truth)
    header["invoice_date"] = "14/09/2026"
    coding, problems = coding_from_inputs(header, lines, taxes, ground_truth)
    assert coding is None and any("invoice_date" in p for p in problems)


def test_tax_rates_edited_as_percentages(ground_truth):
    header, lines, taxes = _inputs(ground_truth)
    taxes.insert(2, "rate_pct", taxes.pop("rate") * 100)  # what the dashboard grid shows
    coding, problems = coding_from_inputs(header, lines, taxes, ground_truth)
    assert problems == []
    assert [t.rate for t in coding.tax_lines] == [t["rate"] for t in ground_truth["tax_lines"]]
