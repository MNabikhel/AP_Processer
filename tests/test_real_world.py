"""Messy real-world cases: misread amounts, credit notes, foreign tax, re-runs, odd vendor names, Excel files."""

import copy
import sqlite3
import threading
from pathlib import Path

import pytest

from ap_coder.csvio import parse_date, read_csv_rows
from ap_coder.memory import CORRECTED, select_examples, vendor_key
from ap_coder.pipeline import InvoicePipeline, PipelineResult, output_stems
from ap_coder.schema import InvoiceCoding
from ap_coder.store import Store
from ap_coder.tax import TaxRateTable, build_gl_distribution, check_taxes
from ap_coder.validation import validate_coding


def _codes(doc, reference):
    return {f.code for f in check_taxes(InvoiceCoding.model_validate(doc), reference.tax, None)}


def _issues(doc, reference):
    return {i.code for i in validate_coding(InvoiceCoding.model_validate(doc), reference).issues}


# --- Postings must balance ---------------------------------------------------------------------------------


def test_small_misread_that_slips_past_each_check_is_still_caught(ground_truth, reference):
    doc = copy.deepcopy(ground_truth)
    doc["line_items"][0]["amount"] += 0.03  # within the per-line rounding allowance on its own
    assert "POSTING_UNBALANCED" in _issues(doc, reference)
    assert "POSTING_UNBALANCED" not in _issues(ground_truth, reference)


# --- Tax edge cases ------------------------------------------------------------------------------------------


def test_printed_zero_tax_is_not_a_charge(ground_truth, reference):
    doc = copy.deepcopy(ground_truth)
    doc["tax_lines"] = [{"tax_type": "HST", "province": "ON", "rate": 0.0, "taxable_amount": 0.0, "tax_amount": 0.0}]
    doc["tax_total"], doc["grand_total"] = 0.0, doc["subtotal"]
    for li in doc["line_items"]:
        li["taxes_applied"] = []
    codes = _codes(doc, reference)
    assert {"TAX_NOT_ALLOCATED", "TAX_RATE_NONSTANDARD"}.isdisjoint(codes)
    assert "NO_TAX_CHARGED" in codes  # still worth confirming the supply is exempt


def test_same_tax_printed_twice_is_checked_per_tax_type(ground_truth, reference):
    doc = copy.deepcopy(ground_truth)
    goods = sum(li["amount"] for li in doc["line_items"][:-1])
    freight = doc["line_items"][-1]["amount"]
    doc["tax_lines"] = [
        {"tax_type": "HST", "province": "ON", "rate": 0.13, "taxable_amount": base, "tax_amount": round(base * 0.13, 2)}
        for base in (goods, freight)
    ]
    doc["tax_total"] = round(sum(t["tax_amount"] for t in doc["tax_lines"]), 2)
    doc["grand_total"] = round(doc["subtotal"] + doc["tax_total"], 2)
    assert "TAX_BASE_MISMATCH" not in _codes(doc, reference)


def test_us_sales_tax_is_recorded_as_other_and_expensed(ground_truth, reference):
    doc = copy.deepcopy(ground_truth)
    doc.update(supplier_province="OUTSIDE_CANADA", ship_to_province="ON", currency="USD",
               gst_hst_registration_number="")  # fmt: skip
    doc["tax_lines"] = [{"tax_type": "OTHER", "province": "", "rate": 0.0825, "taxable_amount": 15945.0,
                         "tax_amount": 1315.46}]  # fmt: skip
    doc["tax_total"], doc["grand_total"] = 1315.46, 17260.46
    for li in doc["line_items"]:
        li["taxes_applied"] = ["OTHER"]
    coding = InvoiceCoding.model_validate(doc)
    issues = {i.code: i.severity for i in validate_coding(coding, reference).issues}
    assert "error" not in issues.values(), issues
    assert "TAX_NON_CANADIAN" in issues
    dist = build_gl_distribution(coding, reference.tax)
    assert all(e["kind"] == "expense" for e in dist)  # no recoverable tax account
    assert round(sum(e["amount"] for e in dist), 2) == 17260.46


def test_credit_notes_get_the_province_checks_too(ground_truth, reference):
    doc = copy.deepcopy(ground_truth)
    for li in doc["line_items"]:
        li["amount"], li["unit_price"] = -li["amount"], -li["unit_price"]
    doc["subtotal"], doc["grand_total"] = -doc["subtotal"], -doc["grand_total"]
    doc["tax_total"] = -doc["tax_total"]
    doc["tax_lines"][0].update(taxable_amount=-15945.0, tax_amount=-2072.85, tax_type="GST", province="")
    assert "TAX_REGIME_MISMATCH" in _codes(doc, reference)  # GST charged in an HST province


def test_credit_note_is_not_a_duplicate_of_its_invoice(tmp_path, ground_truth):
    store = Store(tmp_path / "ap.db")
    store.add_invoice(tmp_path / "inv.pdf", ground_truth, {})
    name, number = ground_truth["vendor_name"], ground_truth["invoice_number"]
    assert store.find_duplicates(name, number, grand_total=-100.0) == []
    assert store.find_duplicates(name, number, grand_total=100.0) != []


# --- Vendor names --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Hydro-Québec", "Hydro Quebec"),
        ("Smith & Sons", "Smith and Sons Ltd."),
        ("Société ABC S.E.N.C.", "Societe ABC"),
        ("Mueller GmbH & Co. KG", "Mueller"),
        ("Œuvre Inc.", "Oeuvre"),
        ("The Home Depot", "Home Depot Inc."),
    ],
)
def test_vendor_names_match_despite_accents_and_legal_forms(a, b):
    assert vendor_key(a) == vendor_key(b) != ""


def test_legal_words_inside_a_name_are_kept():
    assert vendor_key("Co-op Atlantic Ltd") == "co op atlantic"


def test_old_databases_get_new_vendor_keys(tmp_path, ground_truth):
    db = tmp_path / "ap.db"
    store = Store(db)
    doc = {**ground_truth, "vendor_name": "Hydro-Québec"}
    store.add_invoice(tmp_path / "x.pdf", doc, {})
    with sqlite3.connect(db) as conn:  # as written by the first version
        conn.execute("UPDATE invoices SET vendor_key = 'hydro québec'")
        conn.execute("DELETE FROM settings WHERE key = 'schema_version'")
    Store(db)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT vendor_key FROM invoices").fetchone()[0] == "hydro quebec"


# --- Learning with a big vendor --------------------------------------------------------------------------------


def test_relevant_correction_reaches_the_model_for_a_big_vendor():
    def row(description, gl):
        return {"vendor_key": "staples", "vendor_name": "Staples", "description": description, "final_gl": gl,
                "final_cc": "", "outcome": CORRECTED, "created_at": "2026-01-01"}  # fmt: skip

    rows = [row(f"Binder clips size {i}", "6000") for i in range(30) for _ in range(2)]  # many older decisions
    rows.append(row("Toner cartridge HP 26A", "6010"))
    examples = select_examples(rows, "Staples invoice: Toner cartridge HP 26A x 4", vendor_hint="Staples")
    assert "Toner cartridge HP 26A" in [e.description for e in examples]


# --- Approvals ------------------------------------------------------------------------------------------------


def test_two_simultaneous_approvals_record_the_lesson_once(tmp_path, ground_truth):
    store = Store(tmp_path / "ap.db")
    inv_id = store.add_invoice(tmp_path / "x.pdf", ground_truth, {})
    errors = []

    def approve():
        try:
            Store(tmp_path / "ap.db").approve_invoice(inv_id, copy.deepcopy(ground_truth), "clerk")
        except ValueError as exc:
            errors.append(exc)

    threads = [threading.Thread(target=approve) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(store.feedback_rows()) == len(ground_truth["line_items"])
    assert len(errors) == 3


def test_source_paths_are_stored_absolute(tmp_path, ground_truth, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("inv.pdf").write_bytes(b"%PDF")
    store = Store(tmp_path / "ap.db")
    inv = store.get_invoice(store.add_invoice(Path("inv.pdf"), ground_truth, {}))
    assert Path(inv["source_path"]).is_absolute()


# --- Batch runs -------------------------------------------------------------------------------------------------


def test_same_named_files_from_different_folders_get_their_own_outputs():
    paths = [Path("a/Invoice.pdf"), Path("b/Invoice.pdf"), Path("c/invoice.md"), Path("d/Other.pdf")]
    assert output_stems(paths) == ["Invoice", "Invoice_2", "invoice_3", "Other"]


@pytest.mark.parametrize("workers", [1, 3])
def test_results_are_saved_as_each_invoice_finishes(workers, settings, reference):
    class Pipeline(InvoicePipeline):
        def process(self, path):
            return PipelineResult(source=Path(path))

    seen = []
    paths = [Path(f"{i}.pdf") for i in range(5)]
    results = Pipeline(settings, reference, extractor=object(), coder=object()).process_many(
        paths, workers=workers, on_result=lambda i, r: seen.append((i, r.source))
    )
    assert [r.source for r in results] == paths
    assert sorted(seen) == list(enumerate(paths))


# --- Files saved by Excel ---------------------------------------------------------------------------------------


def test_semicolon_csv_from_french_excel(tmp_path):
    f = tmp_path / "gl.csv"
    f.write_bytes("Compte;Description\n0100;Encaisse – générale\n".encode("cp1252"))
    assert read_csv_rows(f) == [{"Compte": "0100", "Description": "Encaisse – générale"}]


def test_tax_rate_file_resaved_by_excel(tmp_path):
    f = tmp_path / "rates.csv"
    f.write_text(
        "tax_type,province,rate,effective_from,effective_to,notes\nGST,,5%,2008/01/01,,x\nHST,ON,13,31/12/2010,,x\n"
    )
    rates = TaxRateTable.load(f)
    assert [(r.rate, str(r.effective_from)) for r in rates.rates] == [(0.05, "2008-01-01"), (0.13, "2010-12-31")]


def test_ambiguous_dates_are_refused_with_the_row():
    with pytest.raises(ValueError, match="row 3.*ambiguous"):
        parse_date("01/02/2010", "rates.csv row 3")
