"""Splitting a line across GL accounts / cost centers."""

import json
import sys

import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from ap_coder.config import Settings
from ap_coder.pipeline import finalise_coding
from ap_coder.review import coding_from_inputs, split_line
from ap_coder.store import Store, load_sample_setup

from .conftest import ROOT, SAMPLE_STEM, SAMPLES


def _gt():
    return json.loads((SAMPLES / "ground_truth" / f"{SAMPLE_STEM}.json").read_text())


def test_split_keeps_the_total_and_the_numbering(reference):
    gt = _gt()
    lines = pd.DataFrame(gt["line_items"])
    out = split_line(lines, 4, [("6110", "CC400", 60), ("6110", "CC200", 25), ("6100", "CC200", 15)])
    assert out["line_number"].tolist() == [1, 2, 3, 4, 5, 6, 7]
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


def test_split_from_the_review_screen(tmp_path, monkeypatch):
    path = tmp_path / "private" / "ap_coder.db"
    monkeypatch.setenv("AP_DB_PATH", str(path))
    for name in [m for m in sys.modules if m.startswith(("ap_coder.webapp", "ap_coder.dashboard"))]:
        monkeypatch.delitem(sys.modules, name)
    st.cache_resource.clear()
    st.cache_data.clear()
    store = Store(path)
    load_sample_setup(store)
    invoice_id = store.add_invoice(SAMPLES / f"{SAMPLE_STEM}.pdf", _gt(), {"requires_review": False})
    key = f"inv{invoice_id}"
    at = AppTest.from_file(str(ROOT / "ap_coder" / "dashboard.py"), default_timeout=90)
    at.session_state["open_invoice"] = invoice_id
    at.run()
    at.selectbox(key=f"{key}_split_line").select(2).run()
    at.button(key=f"{key}_split_go").click().run()
    at.run()
    assert not at.exception
    at.button(key=f"{key}_approve").click().run()
    final = store.get_invoice(invoice_id)["final_output"]
    servers = [li for li in final["line_items"] if li["description"].startswith("Dell PowerEdge")]
    assert [li["amount"] for li in servers] == [4450.0, 4450.0] and len(final["line_items"]) == 6
    assert len(store.feedback_rows()) == 6
