"""The vendor master imported from the ERP: vendor IDs in exports, unknown vendors, terms, default GL."""

import sqlite3

from ap_coder.ai.pipeline import finalise_coding
from ap_coder.ai.suggest import suggest_gl
from ap_coder.coding.vendors import ON_HOLD, master_columns, master_rows, vendor_findings
from ap_coder.core.config import Settings
from ap_coder.core.schema import InvoiceCoding
from ap_coder.core.store import Store, load_sample_setup, load_sample_vendor_master
from ap_coder.core.store.vendors import HOLD_CODES_SETTING
from ap_coder.erp import exports
from ap_coder.ops.demo import load_demo, remove_demo
from tests.jde_exports import records, supplier, supplier_master
from tests.webclient import gt as _gt


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


# --- JD Edwards' supplier master as it is exported ---------------------------------------------------------------


def _held(store):
    return sorted(v["display_name"] for v in store.held_vendors())


def _said(store, code, **header):
    doc = _gt(gst_hst_registration_number="", **header)
    return [text for _, found, text in vendor_findings(InvoiceCoding.model_validate(doc), store) if found == code]


def test_only_the_hold_codes_listed_put_a_supplier_on_hold_in_jd_edwards(tmp_path):
    """B-2081: any hold payment code that was not blank read as "on hold", and JD Edwards' usual value is ``N``: every
    supplier was on hold and every invoice got VENDOR_ON_HOLD. ``N`` and blank never hold; ``Y``, ``1`` and ``2`` do;
    another code (``0``) holds once it is listed in the setting."""
    store = Store(tmp_path / "a.db")
    codes = {"N": "Maple Leaf Coffee", "": "Quickship Courier", "Y": "Northern Paper", "1": "Old Print Shop",
             "2": "Harbour Telecom", "0": "Prairie Office Furniture"}  # fmt: skip
    store.import_vendor_master([{"vendor_name": name, "erp_id": f"41000{i}"} for i, name in enumerate(codes.values())])
    by_id = {f"41000{i}": ("", code) for i, code in enumerate(codes)}
    store.set_erp_bank_and_hold(by_id)
    assert _held(store) == ["Harbour Telecom", "Northern Paper", "Old Print Shop"]
    assert store.get_vendor("harbour telecom")["erp_hold"] == "2"  # the code is kept: the check says which
    assert store.get_vendor("maple leaf coffee")["erp_hold"] == ""
    assert _said(store, "VENDOR_ON_HOLD", vendor_name="Maple Leaf Coffee") == []
    said = _said(store, "VENDOR_ON_HOLD", vendor_name="Harbour Telecom")[0]
    assert "on hold in JD Edwards" in said and "hold payment code 2" in said and "new vouchers still allowed" in said
    assert "new vouchers stopped" in _said(store, "VENDOR_ON_HOLD", vendor_name="Northern Paper")[0]
    # One setting away: the codes that hold, as the JDE team confirms them. A code is read as the list says when the
    # suppliers are next imported or synced.
    store.set_setting(HOLD_CODES_SETTING, "Y, 1, 2, 0")
    store.set_erp_bank_and_hold(by_id)
    assert _held(store) == ["Harbour Telecom", "Northern Paper", "Old Print Shop", "Prairie Office Furniture"]
    assert "hold payment code 0" in _said(store, "VENDOR_ON_HOLD", vendor_name="Prairie Office Furniture")[0]
    store.set_erp_bank_and_hold({"410002": ("", "n")})  # released in JD Edwards
    assert "Northern Paper" not in _held(store)


LONG_NAME = "GREAT PLAINS INDUSTRIAL EQUIPMENT RENTAL"  # 40 characters: where JD Edwards cuts a name
SUPPLIERS = [
    supplier(410001, "MAPLE LEAF COFFEE SERVICES INC."),
    supplier(410002, "QUICKSHIP COURIER CANADA LTD", tax="VERIFIED BY PHONE 2019"),
    supplier(410003, "NORTHERN PAPER CO  DO NOT USE", "Y"),
    supplier(410004, "DNU - OLD PRINT SHOP", "1"),
    supplier(410005, "HARBOUR TELECOM", "2", tax="123456789RT0001"),
    supplier(410006, "PRAIRIE OFFICE FURNITURE", "0", tax="12-3456789"),
    supplier(410007, "*LAKESIDE CATERING", tax="N/A"),
    supplier(410008, "DOE, JANE Q."),
    supplier(410009, LONG_NAME, tax="987654321", long="AND SUPPLY"),
]


def _by_id(store):
    return {v["erp_id"]: v for v in store.master_vendors()}


def _import_export(store, **picked):
    """The supplier master's export imported as the Vendors page does: the columns as guessed, but those ``picked``."""
    columns, rows = records("suppliers.xlsx", supplier_master(SUPPLIERS))
    chosen = {**master_columns(columns), **picked}
    store.import_vendor_master(master_rows(rows, chosen))
    return chosen


def test_jd_edwards_supplier_master_imports_as_it_comes_with_no_column_picked(tmp_path):
    """B-2082: the export's hold is ``Hold Payment Code``, which no column guess knew: the blank ``Status`` column was
    taken, so a held supplier came in active. Read as the JDE sync reads the code (B-2081); the tax ID column, free
    text, is never guessed as the GST/HST number, and ``Long Address`` is left alone."""
    assert len(LONG_NAME) == 40
    store = Store(tmp_path / "a.db")
    chosen = _import_export(store)
    assert chosen == {"vendor_name": "Alpha Name", "erp_id": "Address Number", "status": "Status",
                      "hold": "Hold Payment Code"}  # fmt: skip
    vendors = _by_id(store)
    assert sorted(vendors) == [str(n) for n in range(410001, 410010)]  # the address number, a number in the file
    assert {i: v["erp_hold"] for i, v in vendors.items() if v["erp_hold"]} == {"410003": "Y", "410004": "1",
                                                                                "410005": "2"}  # fmt: skip
    assert {"Harbour Telecom".upper(), "DNU - OLD PRINT SHOP"} <= set(_held(store))
    assert "hold payment code 2" in _said(store, "VENDOR_ON_HOLD", vendor_name="Harbour Telecom")[0]
    assert _said(store, "VENDOR_ON_HOLD", vendor_name="Maple Leaf Coffee Services Inc.") == []
    assert vendors["410001"]["status"] == "active" and vendors["410006"]["status"] == "active"  # 0: not listed
    assert {v["expected_gst"] for v in vendors.values()} == {""}
    assert vendors["410009"]["display_name"] == LONG_NAME  # the long address is not glued on: mostly an address
    # Imported again once the JDE team says 0 holds: one setting away.
    store.set_setting(HOLD_CODES_SETTING, "Y, 1, 2, 0")
    _import_export(store)
    assert _by_id(store)["410006"]["erp_hold"] == "0"
    # A file with no hold column (the sample vendor master) leaves JD Edwards' hold as it is.
    store.import_vendor_master(master_rows([{"Vendor Name": "Harbour Telecom", "Vendor ID": "410005"}],
                                           {"vendor_name": "Vendor Name", "erp_id": "Vendor ID"}))  # fmt: skip
    assert _by_id(store)["410005"]["erp_hold"] == "2"


def test_a_tax_id_column_picked_by_hand_gives_only_canadian_gst_hst_numbers(tmp_path):
    """B-2082: JD Edwards' ``Individual Tax ID`` holds notes, "N/A" and other countries' numbers. Picked by hand as the
    GST/HST number, a note was stored as the vendor's number; only a value shaped like one (#########RT####) is."""
    store = Store(tmp_path / "a.db")
    _import_export(store, gst="Individual Tax ID")
    assert {i: v["expected_gst"] for i, v in _by_id(store).items() if v["expected_gst"]} == {
        "410005": "123456789RT0001"
    }
    # A column the guess knows as the GST/HST number keeps what it holds, as before (a master with the 9 digits only).
    known = master_columns(["Vendor", "Business Number"])
    assert master_rows([{"Vendor": "Beta Inc", "Business Number": "987654321"}], known)[0]["gst"] == "987654321"


def test_the_hold_payment_code_picked_by_hand_as_the_status_is_read_as_the_code(tmp_path):
    """B-2082: with ``Hold Payment Code`` picked as the Status column, Y, 1 and 2 were not hold words: all active."""
    store = Store(tmp_path / "a.db")
    columns, rows = records("suppliers.xlsx", supplier_master(SUPPLIERS))
    store.import_vendor_master(master_rows(rows, {"vendor_name": "Alpha Name", "erp_id": "Address Number",
                                                  "status": "Hold Payment Code"}))  # fmt: skip
    vendors = _by_id(store)
    assert [i for i, v in sorted(vendors.items()) if v["erp_hold"]] == ["410003", "410004", "410005"]
    assert vendors["410001"]["erp_hold"] == "" and vendors["410006"]["erp_hold"] == ""


def test_the_sample_vendor_master_imports_as_it_did(tmp_path):
    """The repo's sample list (``data/vendor_master.csv``): the same columns guessed, no hold of JD Edwards', every
    number kept as written."""
    import csv

    from ap_coder.core.store.samples import SAMPLE_DATA_DIR

    with (SAMPLE_DATA_DIR / "vendor_master.csv").open(encoding="utf-8-sig", newline="") as fh:
        listed = list(csv.DictReader(fh))
    chosen = master_columns(list(listed[0]))
    assert chosen == {"vendor_name": "vendor_name", "erp_id": "vendor_id", "gst": "gst_hst_number",
                      "terms": "payment_terms", "status": "status", "default_gl": "default_gl"}  # fmt: skip
    rows = master_rows(listed, chosen)
    assert all(r["hold"] is None for r in rows)
    assert [(r["vendor_name"], r["erp_id"], r["gst"], r["terms"], r["default_gl"]) for r in rows] == [
        (r["vendor_name"], r["vendor_id"], r["gst_hst_number"], r["payment_terms"], r["default_gl"]) for r in listed
    ]
    store = Store(tmp_path / "a.db")
    load_sample_vendor_master(store)
    vendors = store.master_vendors()
    assert len(vendors) == len(listed) and {v["erp_hold"] for v in vendors} == {""}
    assert {v["status"] for v in vendors} == {r["status"] for r in listed}
    assert {v["notes"] for v in vendors} == {""}
