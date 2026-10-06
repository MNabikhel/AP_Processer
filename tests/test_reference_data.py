import json

import pytest

from ap_coder.reference_data import load_reference_data, load_table


def test_loads_sample_reference_data(reference):
    assert "6030" in reference.chart_of_accounts.codes
    assert "CC410" in reference.cost_centers.codes
    assert 0.2 in reference.tax_rate_values()
    assert any("Capitalisation threshold" in n for n in reference.notes)


def test_inactive_accounts_are_excluded(reference):
    assert "6999" not in reference.chart_of_accounts.codes
    assert "active" not in reference.chart_of_accounts.rows[0]


def test_prompt_context_contains_tables(reference):
    ctx = reference.to_prompt_context()
    assert "### GL Chart of Accounts" in ctx
    assert "| gl_code | account_name |" in ctx
    assert "CC410" in ctx
    assert "### Coding Policy Notes" in ctx


def test_json_reference_files(tmp_path):
    coa = tmp_path / "coa.json"
    coa.write_text(json.dumps({"accounts": [{"gl_code": 6000, "account_name": "Office"}]}))
    cc = tmp_path / "cc.json"
    cc.write_text(json.dumps([{"cost_center": "CC1", "name": "Ops"}]))
    ref = load_reference_data(coa, cc)
    assert ref.chart_of_accounts.codes == ["6000"]
    assert ref.tax_codes is None


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
