"""The vendor master imported from the ERP: vendor IDs in exports, unknown vendors, terms, default GL."""

import sqlite3

from ap_coder.ai.pipeline import finalise_coding
from ap_coder.ai.suggest import suggest_gl
from ap_coder.coding.vendors import ON_HOLD, master_columns, master_rows, vendor_findings
from ap_coder.core.config import Settings
from ap_coder.core.keys import master_vendor_id
from ap_coder.core.memory import vendor_key
from ap_coder.core.schema import InvoiceCoding
from ap_coder.core.store import Store, load_sample_setup, load_sample_vendor_master
from ap_coder.core.store.vendors import HOLD_CODES_SETTING, SKIP_WORDS_SETTING, name_marker, person_name
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


def test_a_name_marked_as_retired_is_told_from_the_name_itself():
    """B-2083: the words of the list (in any case, however spaced), or a star before or after the name."""
    words = ["DO NOT USE", "DNU"]
    marked = {
        "NORTHERN PAPER CO  DO NOT USE": ("northern paper", "DO NOT USE"),
        "Northern Paper Co - do  not   use": ("northern paper", "DO NOT USE"),
        "*** DO-NOT-USE *** Northern Paper Co": ("northern paper", "DO NOT USE"),
        "DNU - OLD PRINT SHOP": ("old print shop", "DNU"),
        "Old Print Shop (dnu)": ("old print shop", "DNU"),
        "*LAKESIDE CATERING": ("lakeside catering", "*"),
        "Lakeside Catering *": ("lakeside catering", "*"),
    }
    for name, (key, marker) in marked.items():
        plain, found = name_marker(name, words)
        assert (vendor_key(plain), found) == (key, marker), name
    for name in ("Northern Paper Co", "Dnuba Imports", "SIDNU Tools", "Star*Light Signs", "Do Not Disturb Signs"):
        assert name_marker(name, words) == (name, ""), name
    assert name_marker("ACME FASTENERS INACTIVE", ["INACTIVE"])[1] == "INACTIVE"


def test_a_supplier_marked_do_not_use_is_found_by_its_real_name_and_stopped(tmp_path):
    """B-2083: "NORTHERN PAPER CO  DO NOT USE" was kept under a key with the marker in it: an invoice printed
    "Northern Paper Co" found no vendor (a new one, not in the master) and nothing stopped it being paid to a retired
    supplier number. The marker is not part of the key, and the supplier is on hold with a note that says why."""
    store = Store(tmp_path / "a.db")
    _import_export(store)
    for printed, number, note in (("Northern Paper Co", "410003", "marked DO NOT USE in the supplier master"),
                                  ("Old Print Shop", "410004", "marked DNU in the supplier master"),
                                  ("Lakeside Catering", "410007", "marked * in the supplier master")):  # fmt: skip
        found = store.find_vendor(printed, None)
        assert found and (found["erp_id"], found["status"], found["notes"]) == (number, ON_HOLD, note), printed
        assert found["vendor_key"] == vendor_key(printed) == store.vendor_key_for(printed)
        said = _said(store, "VENDOR_ON_HOLD", vendor_name=printed)
        assert said == [f"this vendor is on hold in the vendor list: {note}"]
        assert _said(store, "VENDOR_NOT_IN_MASTER", vendor_name=printed) == []
    # picked from the vendor list by the name the master shows: the same vendor, and stopped as well
    assert store.vendor_key_for("NORTHERN PAPER CO  DO NOT USE") == "northern paper"
    assert _said(store, "VENDOR_ON_HOLD", vendor_name="NORTHERN PAPER CO  DO NOT USE")
    assert _by_id(store)["410003"]["display_name"] == "NORTHERN PAPER CO  DO NOT USE"  # shown as the master has it
    assert _by_id(store)["410001"]["status"] == "active" and _by_id(store)["410001"]["notes"] == ""
    # the words are a setting (``skip_words``); a supplier no longer marked in the master is taken off the hold
    store.set_setting(SKIP_WORDS_SETTING, "DO NOT USE, DNU, INACTIVE")
    store.import_vendor_master([{"vendor_name": "Cedar Ridge Towing INACTIVE", "erp_id": "410010"},
                                {"vendor_name": "OLD PRINT SHOP", "erp_id": "410004"}])  # fmt: skip
    assert store.find_vendor("Cedar Ridge Towing")["status"] == ON_HOLD
    again = store.find_vendor("Old Print Shop")
    assert (again["status"], again["notes"], again["erp_id"]) == ("active", "", "410004")


def test_of_a_marked_and_an_unmarked_supplier_of_one_name_the_unmarked_is_the_match(tmp_path):
    """B-2083: a supplier retired and set up again under a new number. Whatever their order in the file, the one in
    use is the vendor of the name; the retired one is kept under its number, on hold, and a voucher sent under that
    number is stopped."""
    from ap_coder.coding.vendors import voucher_hold

    rows = [{"vendor_name": "NORTHERN PAPER CO  DO NOT USE", "erp_id": "410003"},
            {"vendor_name": "Northern Paper Co.", "erp_id": "410020"}]  # fmt: skip
    for n, listed in enumerate((rows, rows[::-1])):
        store = Store(tmp_path / f"{n}.db")
        result = store.import_vendor_master([dict(r) for r in listed])
        assert result["added"] == 2 and result["duplicates"] == []
        found = store.find_vendor("Northern Paper Co")
        assert (found["erp_id"], found["status"], found["vendor_key"]) == ("410020", "active", "northern paper")
        assert _said(store, "VENDOR_ON_HOLD", vendor_name="Northern Paper Co") == []
        retired = store.get_vendor("northern paper #410003")
        assert (retired["status"], retired["notes"]) == (ON_HOLD, "marked DO NOT USE in the supplier master")
        assert store.find_vendor("NORTHERN PAPER CO  DO NOT USE")["erp_id"] == "410003"  # picked by its master name
        assert _said(store, "VENDOR_ON_HOLD", vendor_name="NORTHERN PAPER CO  DO NOT USE")
        assert "on hold" in voucher_hold(store, "", "410003") and voucher_hold(store, "northern paper", "410020") == ""
        assert store.vendor_ids()["northern paper"] == "410020"
    # The JD Edwards sync, a page at a time: the retired one came first, the one in use a night later.
    store = Store(tmp_path / "sync.db")
    store.import_vendor_master([dict(rows[0])], by_erp_id=True)
    assert store.find_vendor("Northern Paper Co")["status"] == ON_HOLD
    store.import_vendor_master([dict(rows[1])], by_erp_id=True)
    found = store.find_vendor("Northern Paper Co")
    assert (found["erp_id"], found["status"], found["notes"]) == ("410020", "active", "")
    assert store.get_vendor("northern paper #410003")["status"] == ON_HOLD
    store.import_vendor_master([dict(r) for r in rows], by_erp_id=True)  # the same again: nothing moves
    assert store.find_vendor("Northern Paper Co")["erp_id"] == "410020" and len(store.master_vendors()) == 2


def test_a_supplier_on_payment_hold_does_not_hold_the_one_in_use_of_the_same_name(tmp_path):
    """B-2083: the retired one of two suppliers by one name is as often held by its hold payment code as marked in
    its name. Merged, the one in use was on hold for it and the held one's number was lost: they are two vendors."""
    from ap_coder.coding.vendors import voucher_hold

    rows = [{"vendor_name": "Summit Pest Control Inc.", "erp_id": "410011", "hold": "Y"},
            {"vendor_name": "SUMMIT PEST CONTROL", "erp_id": "410031", "hold": "N"}]  # fmt: skip
    for n, listed in enumerate((rows, rows[::-1])):
        store = Store(tmp_path / f"{n}.db")
        assert store.import_vendor_master([dict(r) for r in listed])["added"] == 2
        found = store.find_vendor("Summit Pest Control")
        assert (found["erp_id"], found["erp_hold"], found["status"]) == ("410031", "", "active")
        assert _said(store, "VENDOR_ON_HOLD", vendor_name="Summit Pest Control") == []
        assert store.get_vendor("summit pest control #410011")["erp_hold"] == "Y"
        assert "hold code Y" in voucher_hold(store, "", "410011") and voucher_hold(store, "", "410031") == ""
    # the same supplier number listed twice, or no number to tell them by: one vendor, on hold if either row is
    store = Store(tmp_path / "same.db")
    store.import_vendor_master([{**rows[1], "erp_id": "410011"}, dict(rows[0])])
    assert store.find_vendor("Summit Pest Control")["erp_hold"] == "Y" and len(store.master_vendors()) == 1


# --- People as suppliers: "LAST, FIRST M." ------------------------------------------------------------------------


def _people(tmp_path, *names):
    store = Store(tmp_path / f"people{len(names)}.db")
    store.import_vendor_master([{"vendor_name": n, "erp_id": str(520001 + i)} for i, n in enumerate(names)])
    return store


def _number(store, printed):
    found = store.find_vendor(printed, None)
    return found["erp_id"] if found else None


def test_which_names_read_as_a_person():
    """Only a name with exactly one comma, no digit and no company word is a person's "LAST, FIRST M."."""
    assert person_name("DOE, JANE Q.") == (("doe",), "jane", ("q",))
    assert person_name("Van Der Berg, Pieter J") == (("van", "der", "berg"), "pieter", ("j",))
    assert person_name("O'Brien-Côté, Marie-Ève") == (("o", "brien", "cote"), "marie", ("eve",))
    for company in ("Smith, Brown & Co", "Smith, Brown and Partners", "Granite Ridge, Town of", "Hart, Lowe Inc.",
                    "Acme Fasteners Ltd", "Doe, Jane, Q", "Unit 4, 12 Main Street", "Jane Q. Doe", ""):  # fmt: skip
        assert person_name(company) is None, company


def test_a_person_in_the_master_is_found_by_the_name_as_a_claim_prints_it(tmp_path):
    """An employee is listed "DOE, JANE Q." and a claim prints "Jane Q. Doe" or "Jane Doe": the same vendor, its
    history and its supplier number; a different person who shares the surname is not."""
    store = _people(tmp_path, "DOE, JANE Q.", "DOE, JOHN", "VAN DER BERG, PIETER J", "ROE, SAM - DO NOT USE")
    for printed in ("Jane Q. Doe", "Jane Doe", "JANE Q DOE", "jane quinn doe", "Doe, Jane", "DOE, JANE Q",
                    "Doe, Jane Quinn"):  # fmt: skip
        assert _number(store, printed) == "520001", printed
        assert store.vendor_key_for(printed) == "doe jane q"
        assert _said(store, "VENDOR_NOT_IN_MASTER", vendor_name=printed) == []
    assert _number(store, "John Doe") == "520002" and _number(store, "John A. Doe") == "520002"
    assert _number(store, "Pieter van der Berg") == "520003" and _number(store, "Pieter J. Van Der Berg") == "520003"
    # another person of the same surname, another middle initial, an initial for the first name, the surname first
    # without its comma: no vendor
    for other in ("Janet Doe", "Jane R. Doe", "J. Doe", "Jane Roe", "Jane Doe Smith", "Doe Jane", "Doe, Janet",
                  "DOE, JANE R.", "Pieter Berg", "Jane Q. Doe Consulting Inc."):  # fmt: skip
        assert _number(store, other) is None, other
        assert _said(store, "VENDOR_NOT_IN_MASTER", vendor_name=other), other
    # a person the master marks as retired is found the same way, and stopped
    assert _number(store, "Sam Roe") == "520004" and _said(store, "VENDOR_ON_HOLD", vendor_name="Sam Roe")
    # the supplier number goes with the name as printed (the exports and the JD Edwards voucher look it up so)
    ids = store.vendor_ids()
    assert (ids["jane q doe"], ids["jane doe"], ids["pieter van der berg"]) == ("520001", "520001", "520003")
    assert master_vendor_id(ids, "Jane Q. Doe") == "520001" and master_vendor_id(ids, "Janet Doe") == ""


def test_two_people_one_name_could_be_are_never_guessed_between(tmp_path):
    store = _people(tmp_path, "DOE, JANE Q.", "DOE, JANE R.")
    assert _number(store, "Jane Doe") is None and "jane doe" not in store.vendor_ids()
    assert (_number(store, "Jane Q. Doe"), _number(store, "Jane R Doe")) == ("520001", "520002")
    # one listed without a middle initial is the one a name printed without it means
    store = _people(tmp_path, "DOE, JANE Q.", "DOE, JANE R.", "DOE, JANE")
    assert _number(store, "Jane Doe") == "520003" and store.vendor_ids()["jane doe"] == "520003"
    assert _number(store, "Jane Q. Doe") == "520001"


def test_a_person_listed_in_the_usual_order_is_found_by_the_name_printed_surname_first(tmp_path):
    """The reverse: the master has "Alex P. Stone", the claim prints "STONE, ALEX P." (or with no initial)."""
    store = _people(tmp_path, "Alex P. Stone", "Mira Stone", "Stone Alex Paving Ltd.")
    assert _number(store, "STONE, ALEX P.") == "520001" and _number(store, "Stone, Alex") == "520001"
    assert _number(store, "Stone, Mira K.") == "520002"
    for other in ("STONE, ALEX R.", "Stone, Alexa", "Stone, Paul"):
        assert _number(store, other) is None, other


def test_a_company_with_a_comma_in_its_name_is_never_matched_as_a_person(tmp_path):
    """ "Smith, Brown & Co" is a company (a company word): found by its own name as before, never by "Brown Smith"."""
    store = _people(tmp_path, "Smith, Brown & Co", "Granite Ridge, Town of", "Harbour Telecom, Western Division Inc.")
    assert _number(store, "Smith Brown & Co.") == "520001" and _number(store, "Smith, Brown and Co") == "520001"
    assert _number(store, "Harbour Telecom Western Division") == "520003"
    for other in ("Brown Smith", "Brown Smith & Co", "Town of Granite Ridge", "Western Division Harbour Telecom"):
        assert _number(store, other) is None, other
    assert set(store.vendor_ids()) == {"smith brown", "granite ridge town of", "harbour telecom western division"}
