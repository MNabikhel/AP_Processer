"""The vendor master imported from the ERP: vendor IDs in exports, unknown vendors, terms, default GL."""

import json
import sqlite3

from ap_coder.ai.pipeline import finalise_coding
from ap_coder.ai.suggest import suggest_gl
from ap_coder.coding.vendors import ON_HOLD, master_columns, master_rows
from ap_coder.core.config import Settings
from ap_coder.core.schema import InvoiceCoding
from ap_coder.core.store import Store, load_sample_setup, load_sample_vendor_master
from ap_coder.erp import exports
from ap_coder.ops.demo import load_demo, remove_demo

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
    assert rows[0]["vendor_id"] == "10023"


def test_exports_find_the_vendor_id_as_supplier_learning_does(tmp_path):
    """A master that keeps only the 9-digit Business Number, an invoice under another name printing the full
    account (or the other way round): the exports find the ERP vendor ID the same way the supplier key does
    (``Store.supplier_key_for``), not only on an exact match of the whole number."""
    store = Store(tmp_path / "a.db")
    rows = master_rows(
        [{"Supplier ID": "S1", "Supplier Name": "Acme Ltd.", "Business Number": "123456789"},
         {"Supplier ID": "S2", "Supplier Name": "Beta Inc.", "Business Number": "987654321RT0001"}],
        master_columns(["Supplier ID", "Supplier Name", "Business Number"]),
    )  # fmt: skip
    store.import_vendor_master(rows)
    ids = store.vendor_ids()
    for name, number, want in (("Acme Holdings", "123456789 RT0001", "S1"), ("Beta Group", "987654321", "S2")):
        final = {"vendor_name": name, "gst_hst_registration_number": number, "gl_distribution": []}
        assert exports.invoice_rows([{"id": 1, "final_output": final}], 1, ids)[0]["vendor_id"] == want
        assert store.supplier_key_for(name, number) == f"id:{want}"


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


# --- The vendor list on the review screen --------------------------------------------------------------------------


def test_a_vendor_added_from_the_review_screen_needs_a_person_until_it_is_in_the_erp(tmp_path):
    store = Store(tmp_path / "a.db")
    load_sample_setup(store)
    load_sample_vendor_master(store)
    stranger = _gt(vendor_name="Totally New Supplies", gst_hst_registration_number="111111111RT0001")
    _, report = finalise_coding(InvoiceCoding.model_validate(stranger), store.reference_data(), Settings(),
                                store=store)  # fmt: skip
    assert {i.code: i.severity for i in report.issues}["VENDOR_NOT_IN_MASTER"] == "error"
    key = store.add_vendor_here("Totally New Supplies", gst="111111111RT0001", pst="PST-0000-1111", province="bc",
                                actor="Jane", invoice_id=7)  # fmt: skip
    vendor = store.get_vendor(key)
    assert vendor["in_master"] == 2 and vendor["province"] == "BC" and vendor["pst_number"] == "PST-0000-1111"
    assert store.events(actions=["vendor_added"])[0]["actor"] == "Jane"
    codes = _codes(store, stranger)
    assert "VENDOR_ADDED_HERE" in codes and "VENDOR_NOT_IN_MASTER" not in codes
    _, report = finalise_coding(InvoiceCoding.model_validate(stranger), store.reference_data(), Settings(),
                                store=store)  # fmt: skip
    added = next(i for i in report.issues if i.code == "VENDOR_ADDED_HERE")
    assert added.severity == "error" and "by Jane on" in added.message
    assert not store.has_vendor_master() or key not in {v["vendor_key"] for v in store.master_vendors()}
    # Set up in JD Edwards and the vendor list imported again: a vendor like any other.
    store.import_vendor_master(
        [
            {
                "vendor_name": "Totally New Supplies",
                "erp_id": "V99999",
                "gst": "111111111RT0001",
                "terms": None,
                "default_gl": None,
                "status": "active",
                "status_given": False,
            }
        ]
    )
    assert store.get_vendor(key)["in_master"] == 1
    assert not {"VENDOR_ADDED_HERE", "VENDOR_NOT_IN_MASTER"} & _codes(store, stranger)


def test_a_vendor_already_in_the_list_is_not_added_again(tmp_path):
    import pytest

    store = Store(tmp_path / "a.db")
    load_sample_vendor_master(store)
    with pytest.raises(ValueError, match="already in the vendor list"):
        store.add_vendor_here("NORTHWIND IT SOLUTIONS INC", actor="Jane")
    with pytest.raises(ValueError):
        store.add_vendor_here("   ", actor="Jane")


def test_the_vendor_choices(tmp_path):
    store = Store(tmp_path / "a.db")
    load_sample_setup(store)
    doc = _gt()
    store.approve_invoice(store.add_invoice(tmp_path / "a.pdf", doc, {}), doc, "jane")
    store.save_vendor("on hold only", "On Hold Only Ltd", "on_hold")  # controls only: not a vendor of the list
    # No vendor master: the vendors with an approved invoice.
    assert [v["display_name"] for v in store.vendor_choices()] == [doc["vendor_name"]]
    load_sample_vendor_master(store)
    store.add_vendor_here("Zeta New Co", actor="Jane")
    names = [v["display_name"] for v in store.vendor_choices()]
    assert names == sorted(names, key=str.lower) and "Zeta New Co" in names and "On Hold Only Ltd" not in names
    assert len(names) == len(store.master_vendors()) + 1
