import json

import pytest

from ap_coder.reference_data import load_reference_data, load_table


def test_loads_sample_reference_data(reference):
    assert "6030" in reference.chart_of_accounts.codes
    assert "CC410" in reference.cost_centers.codes
    assert reference.tax.treatment("GST").gl_code == "2310"
    assert reference.tax.treatment("PST").treatment == "expense_to_line"
    assert reference.tax.tax_gl_codes() == {"2310", "2320"}
    assert any("Capitalisation threshold" in n for n in reference.notes)


def test_prompt_context_contains_tables(reference):
    ctx = reference.to_prompt_context()
    assert "### GL Accounts" in ctx
    assert "| gl_code | description | category |" in ctx
    assert "| 2310 |" not in ctx, "tax accounts are not offered for expense lines"
    assert "CC410" in ctx
    assert "Canadian Sales Tax Rates" in ctx and "| HST | ON | 0.13 |" in ctx
    assert "### Coding Policy Notes" in ctx


def test_json_reference_files_and_optional_cost_centers(tmp_path):
    coa = tmp_path / "coa.json"
    coa.write_text(json.dumps({"accounts": [{"gl_code": 6000, "description": "Office"}]}))
    ref = load_reference_data(coa)
    assert ref.chart_of_accounts.codes == ["6000"]
    assert ref.cost_centers is None
    assert "Cost Centers" not in ref.to_prompt_context()


def test_duplicate_codes_rejected(tmp_path):
    p = tmp_path / "coa.csv"
    p.write_text("gl_code,account_name\n6000,A\n6000,B\n")
    with pytest.raises(ValueError, match="duplicate"):
        load_table(p, "GL account", "gl_code")


def test_missing_key_column_rejected(tmp_path):
    p = tmp_path / "coa.csv"
    p.write_text("code,account_name\n6000,A\n")
    with pytest.raises(ValueError, match="missing required column"):
        load_table(p, "GL account", "gl_code")
