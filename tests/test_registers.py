"""Duplicates against the ERP's invoice register."""

import json

from ap_coder import registers
from ap_coder.config import Settings
from ap_coder.pipeline import finalise_coding
from ap_coder.schema import InvoiceCoding
from ap_coder.store import Store, load_sample_setup

from .conftest import SAMPLE_STEM, SAMPLES


def _gt(**changes):
    return {**json.loads((SAMPLES / "ground_truth" / f"{SAMPLE_STEM}.json").read_text()), **changes}


def _codes(store, doc):
    _, report = finalise_coding(InvoiceCoding.model_validate(doc), store.reference_data(), Settings(), store=store)
    return {i.code: i.severity for i in report.issues}


def test_register_columns_and_rows():
    records = [
        {"Supplier": "Northwind IT Solutions", "Vendor Invoice": "NW 2026 0912", "Posting Date": "2026-09-16",
         "Gross Amount": "18,017.85"},
        {"Supplier": "Northwind IT Solutions", "Vendor Invoice": "", "Posting Date": "", "Gross Amount": "1"},
    ]  # fmt: skip
    cols = registers.map_columns(list(records[0]))
    assert cols == {"vendor_name": "Supplier", "invoice_number": "Vendor Invoice", "invoice_date": "Posting Date",
                    "total": "Gross Amount"}  # fmt: skip
    rows, skipped = registers.rows_from_records(records, cols)
    assert skipped == 1 and rows[0]["total"] == 18017.85


def test_invoice_already_in_the_erp_is_an_error(tmp_path):
    store = Store(tmp_path / "a.db")
    load_sample_setup(store)
    assert "DUPLICATE_IN_ERP" not in _codes(store, _gt())
    rows = [{"vendor_name": "Northwind IT Solutions", "invoice_number": "NW 2026 0912", "invoice_date": "2026-09-16",
             "total": 18017.85}]  # fmt: skip
    assert store.import_erp_register(rows) == 1
    assert store.import_erp_register(rows) == 1  # importing again adds nothing
    assert _codes(store, _gt())["DUPLICATE_IN_ERP"] == "error"
    assert "DUPLICATE_IN_ERP" not in _codes(store, _gt(invoice_number="NW-2026-0999"))
    credit = _gt(grand_total=-18017.85, subtotal=-15945.0, tax_total=-2072.85)  # reverses it: not a duplicate
    credit["line_items"] = [{**li, "amount": -li["amount"], "quantity": -li["quantity"]} for li in credit["line_items"]]
    credit["tax_lines"] = [{**t, "taxable_amount": -t["taxable_amount"], "tax_amount": -t["tax_amount"]}
                           for t in credit["tax_lines"]]  # fmt: skip
    assert "DUPLICATE_IN_ERP" not in _codes(store, credit)
    store.clear_erp_register()
    assert store.erp_register_count() == 0
