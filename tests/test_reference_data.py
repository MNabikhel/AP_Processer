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


def test_coding_policy_saved_with_a_byte_order_mark_keeps_its_comment_line(tmp_path):
    """Notepad saves coding_policy.md with a byte-order mark: its "#" comment line stays a comment, both when the
    reference files are loaded and when the sample setup is put in the database."""
    import shutil

    from ap_coder.store import Store, load_sample_setup

    from .conftest import DATA

    data = tmp_path / "data"
    shutil.copytree(DATA, data)
    (data / "coding_policy.md").write_text("# Coding policy notes\n- Rule one\n", encoding="utf-8-sig")
    ref = load_reference_data(data / "chart_of_accounts.csv", policy_notes=data / "coding_policy.md")
    assert ref.notes == ["Rule one"]
    store = Store(tmp_path / "a.db")
    load_sample_setup(store, data)
    assert store.get_setting("policy_notes") == "# Coding policy notes\n- Rule one\n"


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


def test_header_that_spells_the_standard_name_is_renamed(tmp_path):
    # "GL Code" and "Active" normalise exactly to the standard names, so they must be used as those columns.
    p = tmp_path / "coa.csv"
    p.write_text("GL Code,Description,Active\n6000,Office,yes\n6100,Retired,no\n")
    table = load_table(p, "GL account", "gl_code")
    assert table.codes == ["6000"]
    assert "Active" not in table.rows[0]
