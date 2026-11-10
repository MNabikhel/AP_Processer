"""Review screen edits -> InvoiceCoding conversion (the grids are read as DataFrames)."""

import math

import pandas as pd

from ap_coder.coding.review import coding_from_inputs
from ap_coder.core.reference_data import UNASSIGNED

HEADER_FIELDS = (
    "vendor_name", "invoice_number", "invoice_date", "po_number", "payment_terms", "due_date", "currency",
    "supplier_province",
    "ship_to_province", "gst_hst_registration_number", "qst_registration_number", "original_invoice_number",
    "remit_bank_account",
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


def test_a_persons_pick_on_the_review_screen_is_recorded_as_theirs(ground_truth):
    """D-212: a line still coded as proposed keeps what decided it; one a person coded says so, with what was
    proposed and which of the accounts offered was picked; one a rule codes so says the rule."""
    from ap_coder.coding.rules import Rule

    proposed = {**ground_truth, "line_items": [dict(li) for li in ground_truth["line_items"]]}
    one, two, three = proposed["line_items"][:3]
    one["decided_by"] = {"source": "supplier", "lines": 23, "of": 24, "share": 0.9583}
    offered = [{"account": f"CC400.{code}", "business_unit": "CC400", "code": code, "name": "x", "reason": "y",
                "source": "words"} for code in ("6010", "6020")]  # fmt: skip
    two.update(predicted_gl_code=UNASSIGNED, candidates=offered)
    three["decided_by"] = {"source": "model", "model": "qwen"}
    header, lines, taxes = _inputs(
        {
            **proposed,
            "line_items": [
                {k: v for k, v in li.items() if k not in ("candidates", "decided_by")} for li in proposed["line_items"]
            ],
        }
    )
    lines.loc[1, ["predicted_gl_code", "predicted_cost_center"]] = ["6020", "CC400"]  # the second account offered
    lines.loc[2, "predicted_gl_code"] = "6800"  # a rule a person applied
    rule = Rule("", str(three["description"]).split()[0], "6800", "", id=4)
    coding, problems = coding_from_inputs(header, lines, taxes, proposed, rules=[rule])
    assert not problems
    kept, picked, ruled = coding.line_items[:3]
    assert kept.decided_by == one["decided_by"]
    assert picked.decided_by == {"source": "person", "candidate": 2} and len(picked.candidates) == 2
    assert ruled.decided_by == {"source": "rule", "rule_id": 4, "rule": rule.label, "was": "model"}
    lines.loc[0, "predicted_gl_code"] = "6999"  # changed by hand: a person's, said with what it replaced
    by_hand = coding_from_inputs(header, lines, taxes, proposed)[0].line_items[0]
    assert by_hand.decided_by == {"source": "person", "was": "supplier"}
    lines.loc[0, "predicted_gl_code"] = None  # no account: no decision
    assert coding_from_inputs(header, lines, taxes, proposed)[0].line_items[0].decided_by == {}
