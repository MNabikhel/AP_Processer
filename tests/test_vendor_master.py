"""The vendor master imported from the ERP: vendor IDs in exports, unknown vendors, terms, default GL."""

import json
import sqlite3

from ap_coder import exports
from ap_coder.config import Settings
from ap_coder.demo import load_demo, remove_demo
from ap_coder.pipeline import finalise_coding
from ap_coder.schema import InvoiceCoding
from ap_coder.store import Store, load_sample_setup, load_sample_vendor_master
from ap_coder.suggest import suggest_gl
from ap_coder.vendors import ON_HOLD, master_columns, master_rows

from .conftest import SAMPLE_STEM, SAMPLES


def _gt(**changes):
    return {**json.loads((SAMPLES / "ground_truth" / f"{SAMPLE_STEM}.json").read_text()), **changes}


def _codes(store, doc):
    _, report = finalise_coding(InvoiceCoding.model_validate(doc), store.reference_data(), Settings(), store=store)
    return {i.code for i in report.issues}


def test_columns_and_statuses_from_an_erp_export():
    records = [
        {"Supplier ID": "S1", "Supplier Name": "Acme Ltd.", "Business Number": "123456789RT0001",
         "Payment Terms": "Net 45", "Active": "No", "GL Account": "6000.0"},
        {"Supplier ID": "S2", "Supplier Name": "Beta Inc", "Business Number": "", "Payment Terms": "",
         "Active": "Yes", "GL Account": ""},
        {"Supplier ID": "S3", "Supplier Name": "", "Business Number": "", "Payment Terms": "", "Active": "",
         "GL Account": ""},
    ]  # fmt: skip
    cols = master_columns(list(records[0]))
    assert cols == {"vendor_name": "Supplier Name", "erp_id": "Supplier ID", "gst": "Business Number",
                    "terms": "Payment Terms", "status": "Active", "default_gl": "GL Account"}  # fmt: skip
    rows = master_rows(records, cols)
    assert [r["vendor_name"] for r in rows] == ["Acme Ltd.", "Beta Inc"]
    assert rows[0]["status"] == ON_HOLD and rows[1]["status"] == "active"


def test_import_updates_and_keeps_manual_controls(tmp_path):
    store = Store(tmp_path / "a.db")
    store.save_vendor("acme", "Acme", "active", "", "verified by phone")
    store.import_vendor_master([{"vendor_name": "ACME Ltd", "erp_id": "S1", "gst": "", "terms": "Net 45",
                                 "default_gl": "6000.0", "status": "active", "status_given": False}])  # fmt: skip
    v = store.get_vendor("acme")
    assert v["erp_id"] == "S1" and v["default_gl"] == "6000" and v["notes"] == "verified by phone" and v["in_master"]
    assert store.vendor_ids() == {"acme": "S1"} and store.vendor_terms("acme") == "Net 45"


def test_unknown_vendor_is_flagged_and_gst_match_is_accepted(tmp_path):
    store = Store(tmp_path / "a.db")
    load_sample_setup(store)
    assert "VENDOR_NOT_IN_MASTER" not in _codes(store, _gt())  # no vendor master: nothing to compare with
    load_sample_vendor_master(store)
    assert not {"VENDOR_NOT_IN_MASTER", "VENDOR_NEW"} & _codes(store, _gt())
    renamed = _gt(vendor_name="Northwind Technology Group")
    assert "VENDOR_MATCHED_BY_TAX_NUMBER" in _codes(store, renamed)
    stranger = _gt(vendor_name="Totally New Supplies", gst_hst_registration_number="111111111RT0001")
    assert "VENDOR_NOT_IN_MASTER" in _codes(store, stranger)


def test_vendor_terms_set_the_due_date(tmp_path):
    store = Store(tmp_path / "a.db")
    load_sample_vendor_master(store)  # Northwind: Net 30
    row = {"vendor_name": "Northwind IT Solutions Inc.", "erp_id": "V10023", "gst": "", "terms": "Net 60",
           "default_gl": "", "status": "active", "status_given": False}  # fmt: skip
    store.import_vendor_master([row])
    invoice_id = store.add_invoice(tmp_path / "a.pdf", _gt(payment_terms="", due_date=""), {})
    assert next(i for i in store.list_invoices() if i["id"] == invoice_id)["due_date"] == "2026-11-13"
    printed = store.add_invoice(tmp_path / "b.pdf", _gt(invoice_number="X"), {})  # printed due date wins
    assert next(i for i in store.list_invoices() if i["id"] == printed)["due_date"] == "2026-10-14"


def test_exports_carry_the_vendor_id(tmp_path):
    store = Store(tmp_path / "a.db")
    load_sample_vendor_master(store)
    inv = {"id": 1, "final_output": {**_gt(), "gl_distribution": []}, "reviewer": "Jane", "reviewed_at": "",
           "file_name": "a.pdf"}  # fmt: skip
    text = exports.build("csv", [inv], {}, 1, store.vendor_ids())[0].decode("utf-8-sig")
    assert text.splitlines()[0].startswith("Batch,AP Coder #,Vendor ID,Vendor")
    rows = exports.invoice_rows([inv], 1, store.vendor_ids())
    assert rows[0]["vendor_id"] == "V10023"


def test_default_gl_is_suggested(reference):
    out = suggest_gl("Miscellaneous charge", "Acme", [], reference, default_gl="6800")
    assert out[0].gl_code == "6800" and "vendor master" in out[0].reasons[0]


def test_demo_loads_and_removes_its_vendor_master(tmp_path):
    store = Store(tmp_path / "a.db")
    load_demo(store)
    assert store.has_vendor_master()
    cascade = next(i for i in store.list_invoices() if i["vendor_name"].startswith("Cascade"))
    assert "VENDOR_NOT_IN_MASTER" in {i["code"] for i in store.get_invoice(cascade["id"])["validation"]["issues"]}
    remove_demo(store)
    assert not store.has_vendor_master()


def test_upgrade_adds_the_vendor_master_columns(tmp_path):
    path = tmp_path / "old.db"
    Store(path).save_vendor("acme", "Acme")
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE settings SET value = '7' WHERE key = 'schema_version'")
    assert Store(path).get_vendor("acme")["in_master"] == 0


def test_a_partial_file_keeps_what_it_has_no_column_for(tmp_path):
    store = Store(tmp_path / "v.db")
    full = [{"Vendor": "Pacific Office Supply Ltd.", "No.": "V10057", "Terms": "Net 30", "GL": "6000"}]
    store.import_vendor_master(master_rows(full, master_columns(list(full[0]))))
    partial = [{"Vendor": "Pacific Office Supply Ltd.", "Blocked": "No", "Terms": "Net 45"}]
    store.import_vendor_master(master_rows(partial, master_columns(list(partial[0]))))
    v = store.get_vendor("pacific office supply")
    assert (v["erp_id"], v["default_gl"], v["terms"]) == ("V10057", "6000", "Net 45")
    cleared = [{"Vendor": "Pacific Office Supply Ltd.", "No.": "", "Terms": "Net 45"}]
    store.import_vendor_master(master_rows(cleared, master_columns(list(cleared[0]))))
    assert store.get_vendor("pacific office supply")["erp_id"] == ""  # the column is there, and empty
