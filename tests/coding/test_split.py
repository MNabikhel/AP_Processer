"""Splitting a line across GL accounts / cost centers."""

import json
import re

import pandas as pd
import pytest

from ap_coder.ai.pipeline import finalise_coding
from ap_coder.coding.review import coding_from_inputs, split_line
from ap_coder.core.config import Settings
from ap_coder.core.store import Store, load_sample_setup
from tests.conftest import SAMPLE_STEM, SAMPLES
from tests.webclient import client_for, draft_form, follow, post


def _gt():
    return json.loads((SAMPLES / "ground_truth" / f"{SAMPLE_STEM}.json").read_text())


def test_split_keeps_the_total_and_the_numbering(reference):
    gt = _gt()
    lines = pd.DataFrame(gt["line_items"])
    out = split_line(lines, 4, [("6110", "CC400", 60), ("6110", "CC200", 25), ("6100", "CC200", 15)])
    assert out["line_number"].tolist() == [1, 2, 3, 4, 6, 7, 5]  # the parts under the line split (B-1188)
    assert round(out["amount"].sum(), 2) == gt["subtotal"]
    parts = out[out["description"].str.startswith("Implementation consulting")]
    assert parts["amount"].tolist() == [1080.0, 450.0, 270.0] and parts["quantity"].tolist() == [7.2, 3.0, 1.8]
    assert parts["predicted_cost_center"].tolist() == ["CC400", "CC200", "CC200"]
    header = {k: gt[k] for k in gt if k not in ("line_items", "tax_lines", "confidence_score")}
    coding, problems = coding_from_inputs(header, out, pd.DataFrame(gt["tax_lines"]), gt)
    assert problems == []
    _, report = finalise_coding(coding, reference, Settings())
    codes = {i.code for i in report.issues}
    assert not codes & {"LINE_NUMBERING", "LINE_MATH", "SUBTOTAL_MISMATCH", "TAX_BASE_MISMATCH", "POSTING_UNBALANCED"}


def test_rounding_goes_to_the_last_part():
    lines = pd.DataFrame([{"line_number": 1, "description": "x", "quantity": 1, "unit_price": 100.0, "amount": 100.0,
                           "predicted_gl_code": "6000", "predicted_cost_center": "", "taxes_applied": []}])  # fmt: skip
    out = split_line(lines, 1, [("", "", 33.33), ("", "", 33.33), ("", "", 33.34)])
    assert out["amount"].tolist() == [33.33, 33.33, 33.34] and set(out["predicted_gl_code"]) == {"6000"}


@pytest.mark.parametrize("parts", [[], [("6000", "", 60), ("6000", "", 30)], [("6000", "", 100), ("6000", "", 0)]])
def test_bad_splits_are_refused(parts):
    with pytest.raises(ValueError):
        split_line(pd.DataFrame(_gt()["line_items"]), 1, parts)
    with pytest.raises(ValueError):
        split_line(pd.DataFrame(_gt()["line_items"]), 99, [("6000", "", 100)])


def test_split_from_the_review_screen(tmp_path):
    from tests.web.test_web_invoice import form_of

    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    invoice_id = store.add_invoice(SAMPLES / f"{SAMPLE_STEM}.pdf", _gt(), {"requires_review": False})
    client = client_for(store)
    line = _gt()["line_items"][1]
    data = {**form_of(store, invoice_id), "split_line": "2", "part_gl": [line["predicted_gl_code"]] * 2,
            "part_cc": [line["predicted_cost_center"]] * 2, "part_pct": ["50", "50"]}  # fmt: skip
    answer = post(client, f"/review/{invoice_id}/split", data).json()
    assert answer["ok"] and "Line 2 split in 2" in answer["toast"]["text"]
    follow(client, post(client, f"/review/{invoice_id}/approve", draft_form(client, invoice_id)))
    final = store.get_invoice(invoice_id)["final_output"]
    servers = [li for li in final["line_items"] if li["description"].startswith("Dell PowerEdge")]
    assert [li["amount"] for li in servers] == [4450.0, 4450.0] and len(final["line_items"]) == 6
    assert len(store.feedback_rows()) == 6


# --- Split by dollar amount (D-126) --------------------------------------------------------------------------------


def _one_line(amount: float = 890.0, quantity: float = 10.0) -> pd.DataFrame:
    return pd.DataFrame([{"line_number": 1, "description": "HP 26X toner", "quantity": quantity,
                          "unit_price": amount / quantity, "amount": amount, "predicted_gl_code": "6000",
                          "predicted_cost_center": "", "taxes_applied": []}])  # fmt: skip


def test_split_by_amount_keeps_the_amounts_typed():
    """An invoice line shared out in dollars ("500.00 to office supplies, the rest to IT"): the parts are the amounts
    typed, to the cent, and the quantity follows in proportion."""
    out = split_line(_one_line(), 1, [("6000", "", 500.0), ("6010", "", 390.0)], by="amount")
    assert out["amount"].tolist() == [500.0, 390.0]
    assert out["predicted_gl_code"].tolist() == ["6000", "6010"]
    assert out["description"].tolist() == ["HP 26X toner (500.00 of 890.00)", "HP 26X toner (390.00 of 890.00)"]
    assert out["line_number"].tolist() == [1, 2] and round(out["quantity"].sum(), 6) == 10.0


def test_a_credit_note_line_is_split_by_amount_in_minus_amounts():
    out = split_line(_one_line(-100.0, 1.0), 1, [("6000", "", -60.0), ("6010", "", -40.0)], by="amount")
    assert out["amount"].tolist() == [-60.0, -40.0]


@pytest.mark.parametrize(
    ("amounts", "said"),
    [
        ([500.0, 300.0], "the amounts must add up to the line's 890.00 (they add up to 800.00)"),
        ([890.0, 0.0], "every part needs an amount above 0"),
        ([900.0, -10.0], "every part needs an amount above 0"),
        ([500.0, float("nan")], "every part needs an amount"),
    ],
)
def test_a_split_by_amount_that_does_not_add_up_is_refused(amounts, said):
    with pytest.raises(ValueError, match=re.escape(said)):
        split_line(_one_line(), 1, [("6000", "", a) for a in amounts], by="amount")


def test_a_line_of_nothing_is_split_by_percentage_only():
    with pytest.raises(ValueError, match="split by percentage"):
        split_line(_one_line(0.0, 1.0), 1, [("6000", "", 0.0), ("6010", "", 0.0)], by="amount")


def test_split_by_amount_from_the_review_screen(tmp_path):
    from tests.web.test_web_invoice import form_of

    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    invoice_id = store.add_invoice(SAMPLES / f"{SAMPLE_STEM}.pdf", _gt(), {"requires_review": False})
    client = client_for(store)
    line = _gt()["line_items"][1]
    data = {**form_of(store, invoice_id), "split_line": "2", "split_by": "amount",
            "part_gl": [line["predicted_gl_code"], "6010"], "part_cc": [line["predicted_cost_center"]] * 2,
            "part_amount": ["5000", "3900"], "part_pct": ["50", "50"]}  # fmt: skip
    answer = post(client, f"/review/{invoice_id}/split", data).json()
    assert answer["ok"] and "Line 2 split in 2" in answer["toast"]["text"]
    follow(client, post(client, f"/review/{invoice_id}/approve", draft_form(client, invoice_id)))
    final = store.get_invoice(invoice_id)["final_output"]
    servers = [li for li in final["line_items"] if li["description"].startswith("Dell PowerEdge")]
    assert [(li["amount"], li["predicted_gl_code"]) for li in servers] == [(5000.0, line["predicted_gl_code"]),
                                                                          (3900.0, "6010")]  # fmt: skip
    wrong = post(client, f"/review/{invoice_id}/split", {**data, "part_amount": ["5000", "3000"]})
    assert wrong.status_code >= 400 or not wrong.json().get("ok")
