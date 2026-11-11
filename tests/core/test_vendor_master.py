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
    supplier was on hold and every invoice got VENDOR_ON_HOLD. ``N`` and blank never hold; ``Y`` and ``1`` do; ``2``
    holds the payments only (a warning, B-2157); another code (``0``) holds once it is listed in the setting."""
    store = Store(tmp_path / "a.db")
    codes = {"N": "Maple Leaf Coffee", "": "Quickship Courier", "Y": "Northern Paper", "1": "Old Print Shop",
             "2": "Harbour Telecom", "0": "Prairie Office Furniture"}  # fmt: skip
    store.import_vendor_master([{"vendor_name": name, "erp_id": f"41000{i}"} for i, name in enumerate(codes.values())])
    by_id = {f"41000{i}": ("", code) for i, code in enumerate(codes)}
    store.set_erp_bank_and_hold(by_id)
    assert _held(store) == ["Northern Paper", "Old Print Shop"]
    assert store.get_vendor("harbour telecom")["erp_hold"] == "2"  # the code is kept: the check says which
    assert store.get_vendor("maple leaf coffee")["erp_hold"] == ""
    assert _said(store, "VENDOR_ON_HOLD", vendor_name="Maple Leaf Coffee") == []
    assert _said(store, "VENDOR_PAYMENTS_HELD", vendor_name="Maple Leaf Coffee") == []
    assert "new vouchers stopped" in _said(store, "VENDOR_ON_HOLD", vendor_name="Northern Paper")[0]
    # One setting away: the codes that hold, as the JDE team confirms them. A code is read as the list says when the
    # suppliers are next imported or synced.
    store.set_setting(HOLD_CODES_SETTING, "Y, 1, 2, 0")
    store.set_erp_bank_and_hold(by_id)
    assert _held(store) == ["Northern Paper", "Old Print Shop", "Prairie Office Furniture"]
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
    assert "DNU - OLD PRINT SHOP" in _held(store) and "HARBOUR TELECOM" not in _held(store)
    assert "hold payment code 2" in _said(store, "VENDOR_PAYMENTS_HELD", vendor_name="Harbour Telecom")[0]
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
    words = ["DO NOT USE", "DNU", "*"]
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
    # the star is a word of the list like the others (B-2160)
    assert name_marker("*LAKESIDE CATERING", ["DO NOT USE", "DNU"]) == ("*LAKESIDE CATERING", "")


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
    printed = ("Jane Q. Doe", "Jane Doe", "Pieter van der Berg")
    assert [master_vendor_id(ids, name) for name in printed] == ["520001", "520001", "520003"]
    assert master_vendor_id(ids, "Jane Q. Doe") == "520001" and master_vendor_id(ids, "Janet Doe") == ""


def test_two_people_one_name_could_be_are_never_guessed_between(tmp_path):
    store = _people(tmp_path, "DOE, JANE Q.", "DOE, JANE R.")
    assert _number(store, "Jane Doe") is None and master_vendor_id(store.vendor_ids(), "Jane Doe") == ""
    assert (_number(store, "Jane Q. Doe"), _number(store, "Jane R Doe")) == ("520001", "520002")
    # one listed without a middle initial is the one a name printed without it means
    store = _people(tmp_path, "DOE, JANE Q.", "DOE, JANE R.", "DOE, JANE")
    assert _number(store, "Jane Doe") == "520003" and master_vendor_id(store.vendor_ids(), "Jane Doe") == "520003"
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


# --- The hold payment codes, once more (B-2157 to B-2159) ----------------------------------------------------------


def test_hold_payment_code_2_is_a_warning_the_invoice_is_still_coded_and_keyed(tmp_path):
    """B-2157: JD Edwards' code 2 holds a supplier's payments and still takes its vouchers, and VENDOR_ON_HOLD, an
    error no approval covers, said so in its own words. The invoice is coded, approved and keyed with a warning; its
    voucher is not held back; ``Y`` and ``1`` stop it as before."""
    from ap_coder.coding.vendors import voucher_hold

    store = Store(tmp_path / "a.db")
    store.import_vendor_master([{"vendor_name": "Harbour Telecom", "erp_id": "410005", "hold": "2"},
                                {"vendor_name": "Cedar Ridge Towing", "erp_id": "410010", "hold": "1"}])  # fmt: skip
    doc = InvoiceCoding.model_validate(_gt(gst_hst_registration_number="", vendor_name="Harbour Telecom"))
    found = {code: (severity, text) for severity, code, text in vendor_findings(doc, store)}
    assert "VENDOR_ON_HOLD" not in found
    severity, said = found["VENDOR_PAYMENTS_HELD"]
    assert severity == "warning" and "hold payment code 2" in said and "can be entered" in said
    assert _held(store) == ["Cedar Ridge Towing"]
    assert voucher_hold(store, "harbour telecom", "410005") == "" and voucher_hold(store, "", "410010")
    assert _said(store, "VENDOR_ON_HOLD", vendor_name="Cedar Ridge Towing")
    assert _said(store, "VENDOR_PAYMENTS_HELD", vendor_name="Cedar Ridge Towing") == []


def test_a_hold_code_that_no_longer_holds_is_lifted_at_the_next_import_or_sync_and_n_at_start(tmp_path):
    """B-2158: a hold an earlier version kept for JD Edwards' ``N`` (B-2081) stayed until a read listed that very
    supplier again, and a code taken off the list kept holding the suppliers not in the next file. ``N`` goes when
    the database is next opened; a code no longer listed goes from every vendor at the next import or sync."""
    path = tmp_path / "a.db"
    store = Store(path)
    names = ("Alpha Paper Ltd", "Bravo Print Inc", "Delta Fuel")
    store.import_vendor_master([{"vendor_name": name, "erp_id": str(1001 + i)} for i, name in enumerate(names)])
    with sqlite3.connect(path) as conn:  # as the sync left it before B-2081, and a code 1 from a later one
        conn.execute("UPDATE vendors SET erp_hold = 'N' WHERE erp_id IN ('1001', '1002')")
        conn.execute("UPDATE vendors SET erp_hold = '1' WHERE erp_id = '1003'")
    store = Store(path)  # the update: the database opened by the new version
    assert _held(store) == ["Delta Fuel"]
    assert {v["erp_id"]: v["erp_hold"] for v in store.master_vendors()} == {"1001": "", "1002": "", "1003": "1"}
    # the list cut to Y: code 1 is no hold any more, for a supplier this read does not list either
    store.set_setting(HOLD_CODES_SETTING, "Y")
    store.set_erp_bank_and_hold({"1001": ("", "N")})
    assert _held(store) == []
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE vendors SET erp_hold = '1' WHERE erp_id = '1003'")
    store.import_vendor_master([{"vendor_name": "Alpha Paper Ltd", "erp_id": "1001", "hold": "N"}])
    assert store.get_vendor("delta fuel")["erp_hold"] == ""


def test_the_import_counts_the_hold_codes_it_did_not_treat_as_a_hold(tmp_path):
    """B-2159: a hold payment code the list does not name (``0``, ``3``) was dropped without a word."""
    store = Store(tmp_path / "a.db")
    rows = [{"vendor_name": f"Supplier {i}", "erp_id": str(3000 + i), "hold": code}
            for i, code in enumerate(["N", "", "Y", "0", "0", "3", "2", "n", " 0 "])]  # fmt: skip
    assert store.import_vendor_master(rows)["other_holds"] == {"0": 3, "3": 1}
    store.set_setting(HOLD_CODES_SETTING, "Y, 1, 2, 0")
    assert store.import_vendor_master(rows)["other_holds"] == {"3": 1}
    assert store.import_vendor_master([{"vendor_name": "No Column Co", "erp_id": "1"}])["other_holds"] == {}


# --- The supplier's number is the identity (B-2161 to B-2163) -------------------------------------------------------

TWINS = [{"vendor_name": "LAKESIDE CATERING INC", "erp_id": "2003"},
         {"vendor_name": "LAKESIDE CATERING LTD", "erp_id": "2004"},
         {"vendor_name": "Summit Electric (Calgary)", "erp_id": "2010"},
         {"vendor_name": "SUMMIT ELECTRIC - CALGARY", "erp_id": "2011"},
         {"vendor_name": "Maple Leaf Coffee", "erp_id": "2020"}]  # fmt: skip


def _twins(tmp_path, name="twins.db"):
    store = Store(tmp_path / name)
    return store, store.import_vendor_master([dict(r) for r in TWINS])


def test_two_suppliers_of_one_name_with_no_tax_number_keep_both_address_numbers(tmp_path):
    """B-2161: two suppliers whose names read the same (Inc / Ltd, punctuation) were one vendor under the first
    number unless each had its own GST/HST number, which JD Edwards' master does not give: the second number was kept
    nowhere and every invoice of either got the first. With supplier numbers in the list, each number is a vendor."""
    store, result = _twins(tmp_path)
    assert result["added"] == 5 and result["duplicates"] == []
    assert result["shared"] == [["2003 LAKESIDE CATERING INC", "2004 LAKESIDE CATERING LTD"],
                                ["2010 Summit Electric (Calgary)", "2011 SUMMIT ELECTRIC - CALGARY"]]  # fmt: skip
    assert sorted(v["erp_id"] for v in store.master_vendors()) == ["2003", "2004", "2010", "2011", "2020"]
    assert store.import_vendor_master([dict(r) for r in TWINS])["added"] == 0  # the same list again: nothing moves
    assert len(store.master_vendors()) == 5
    # a supplier alone under its name is found as before
    assert store.find_vendor("Maple Leaf Coffee")["erp_id"] == "2020"
    assert master_vendor_id(store.vendor_ids(), "Maple Leaf Coffee Inc.") == "2020"


def test_a_name_several_supplier_numbers_share_is_never_given_the_first(tmp_path, monkeypatch):
    """B-2161: an invoice that prints a name two supplier numbers go by gets neither in silence: no vendor, no
    supplier number for the export, and a finding that lists them (and which last year's journal used most). Picked
    on the review screen, the name carries the number, and everything follows it."""
    store, _ = _twins(tmp_path)
    ids = store.vendor_ids()
    for printed in ("Lakeside Catering", "Lakeside Catering Ltd.", "LAKESIDE CATERING INC", "Summit Electric Calgary"):
        assert store.find_vendor(printed) is None, printed
        assert master_vendor_id(ids, printed) == "", printed
    assert _said(store, "VENDOR_NOT_IN_MASTER", vendor_name="Lakeside Catering Ltd.") == [
        "2 suppliers in the vendor list read “Lakeside Catering Ltd.”: pick one from the list, each has its own "
        "supplier number: 2003 LAKESIDE CATERING INC; 2004 LAKESIDE CATERING LTD"
    ]
    used = [{"supplier_number": "2004", "vouchers": 14}, {"supplier_number": " 02003", "vouchers": 3},
            {"supplier_number": "2020", "vouchers": 40}]  # fmt: skip
    monkeypatch.setattr(type(store), "journal_suppliers", lambda self: used)
    assert _said(store, "VENDOR_NOT_IN_MASTER", vendor_name="Lakeside Catering")[0].endswith(
        ": 2003 LAKESIDE CATERING INC (3 vouchers in last year's journal); 2004 LAKESIDE CATERING LTD (14 vouchers in "
        "last year's journal, the most)"
    )
    # the review screen's list offers each with its number, and the pick is found by it however the rest is typed
    assert [v["display_name"] for v in store.vendor_choices()] == [
        "LAKESIDE CATERING INC #2003", "LAKESIDE CATERING LTD #2004", "Maple Leaf Coffee",
        "Summit Electric (Calgary) #2010", "SUMMIT ELECTRIC - CALGARY #2011",
    ]  # fmt: skip
    for picked, number in (("LAKESIDE CATERING LTD #2004", "2004"), ("Lakeside Catering #2003", "2003"),
                           ("lakeside catering inc # 02004", "2004")):  # fmt: skip
        assert store.find_vendor(picked)["erp_id"] == number, picked
        assert master_vendor_id(ids, picked) == number, picked
        assert _said(store, "VENDOR_NOT_IN_MASTER", vendor_name=picked) == []
    assert store.vendor_key_for("LAKESIDE CATERING INC #2003") == "lakeside catering"
    assert store.vendor_key_for("LAKESIDE CATERING LTD #2004") == "lakeside catering #2004"
    # a number no supplier of that name has is no pick
    assert store.find_vendor("Lakeside Catering #2020") is None
    assert master_vendor_id(ids, "Lakeside Catering #2020") == ""
    assert _said(store, "VENDOR_NOT_IN_MASTER", vendor_name="Lakeside Catering #2020")


def test_suppliers_merged_under_one_name_before_are_split_at_the_next_import_and_keep_aps_work(tmp_path):
    """B-2161, a database that imported its list before: the vendor AP worked on keeps its key, its number, its
    note, its hold and its controls; the number merged into it comes back as a vendor of its own, on hold too when
    AP's hold was on the name. A list with no supplier numbers merges names that read the same, as before (B-930)."""
    store = Store(tmp_path / "before.db")
    store.import_vendor_master([dict(TWINS[0])])  # as the import before left the two: one vendor, the first number
    store.save_vendor("lakeside catering", "LAKESIDE CATERING INC", "on_hold", "123456789RT0001", "call before paying")
    result = store.import_vendor_master([dict(r) for r in TWINS])
    assert (result["added"], result["updated"]) == (4, 1)
    first, second = store.get_vendor("lakeside catering"), store.get_vendor("lakeside catering #2004")
    assert (first["erp_id"], first["status"], first["notes"]) == ("2003", ON_HOLD, "call before paying")
    assert first["expected_gst"] == "123456789RT0001"
    assert (second["erp_id"], second["status"], second["notes"]) == ("2004", ON_HOLD, "call before paying")
    assert store.get_vendor("summit electric calgary #2011")["status"] == "active"
    assert _said(store, "VENDOR_ON_HOLD", vendor_name="LAKESIDE CATERING LTD #2004")
    plain = Store(tmp_path / "plain.db")
    result = plain.import_vendor_master([{"vendor_name": "Acme Inc"}, {"vendor_name": "ACME LTD", "status": "on_hold",
                                                                      "status_given": True}])  # fmt: skip
    assert result["duplicates"] == [["Acme Inc", "ACME LTD"]] and result["shared"] == []
    assert [(v["vendor_key"], v["status"]) for v in plain.master_vendors()] == [("acme", ON_HOLD)]
    assert plain.find_vendor("Acme Limited")["status"] == ON_HOLD


def test_one_rule_says_which_vendor_a_name_is_for_the_checks_and_for_the_supplier_number(tmp_path):
    """B-2162: the review screen (``find_vendor``) and the exports' supplier number (``vendor_ids``) followed two
    rules. "John Smith Inc." got the number of the person "SMITH, JOHN" while the check said not in the master; "Paul
    S. Martin" was found by the check and had no number on the keying sheet. One function decides both, and a company
    is never a person."""
    store = _people(tmp_path, "SMITH, JOHN", "MARTIN, PAUL", "MARTIN, PAUL R.", "DOE, JANE Q.", "SINGH, HARPREET K.",
                    "ROY, LUC  DO NOT USE", "Old Mill Supply DNU")  # fmt: skip
    ids = store.vendor_ids()
    for company in ("John Smith Inc.", "John Smith Ltd", "John Smith & Sons", "Smith John Holdings"):
        assert store.find_vendor(company) is None, company
        assert master_vendor_id(ids, company) == "", company
        assert _said(store, "VENDOR_NOT_IN_MASTER", vendor_name=company), company
    expected = {"John Smith": "520001", "Smith, John": "520001", "Paul Martin": "520002", "Paul S. Martin": "520002",
                "Paul R. Martin": "520003", "Jane Quinn Doe": "520004", "Jane Doe": "520004", "Jane R. Doe": "",
                "Harpreet Kaur Singh": "520005", "Harpreet Singh": "520005", "Luc Roy": "520006",
                "ROY, LUC  DO NOT USE": "520006", "Old Mill Supply": "520007", "Old Mill Supply DNU": "520007",
                "Janet Doe": "", "Singh Harpreet": ""}  # fmt: skip
    for printed, number in expected.items():
        assert (store.find_vendor(printed) or {}).get("erp_id", "") == number, printed
        assert master_vendor_id(ids, printed) == number, printed
    assert store.in_erp("Jane Quinn Doe", "X-1", 5.0) == []  # (asked by the same rule: no error for a person's name)


def test_two_people_a_name_fits_as_well_are_never_guessed_between(tmp_path):
    """A printed "Jane Q. Doe" with "DOE, JANE Q." and "DOE, JANE QUINN" both in the list: neither is taken."""
    store = _people(tmp_path, "DOE, JANE Q.", "DOE, JANE QUINN")
    assert store.find_vendor("Jane Q. Doe") is None and master_vendor_id(store.vendor_ids(), "Jane Q. Doe") == ""
    assert store.find_vendor("Jane Quinn Doe") is None


def test_a_marked_supplier_imported_before_the_marker_was_read_is_stopped_at_the_next_import(tmp_path):
    """B-2163: a database that imported "Old Mill DNU" before B-2083 kept it, active, under a key with the marker in
    it: the next import made a held vendor beside it, and an invoice read as "Old Mill DNU" still found the old
    active one. The old row is folded into the new one (its note kept), so the supplier is stopped."""
    path = tmp_path / "a.db"
    store = Store(path)
    store.import_vendor_master([{"vendor_name": "Old Mill Supply", "erp_id": "7"}])
    with sqlite3.connect(path) as conn:  # as the version before kept the marked name
        conn.execute("UPDATE vendors SET vendor_key = 'old mill dnu', display_name = 'Old Mill DNU'")
    store = Store(path)
    store.save_vendor("old mill dnu", "Old Mill DNU", "active", "", "pays by cheque")
    assert _said(store, "VENDOR_ON_HOLD", vendor_name="Old Mill DNU") == []
    store.import_vendor_master([{"vendor_name": "Old Mill DNU", "erp_id": "7"}])
    assert [(v["vendor_key"], v["status"], v["erp_id"]) for v in store.master_vendors()] == [("old mill", ON_HOLD, "7")]
    assert "pays by cheque" in store.get_vendor("old mill")["notes"]
    assert _said(store, "VENDOR_ON_HOLD", vendor_name="Old Mill DNU") and _said(store, "VENDOR_ON_HOLD",
                                                                              vendor_name="Old Mill")  # fmt: skip


def test_a_gst_number_two_supplier_numbers_share_does_not_override_the_name_picked(tmp_path):
    """B-2341: two supplier numbers of one business with one GST/HST number. The number was looked up before the name
    and kept the first supplier number it was found with, so the reviewer's pick "LAKESIDE CATERING LTD #2004" (or a
    remit-to name of its own) was sent under the first number. A name with its number, or one supplier's name, wins;
    a number several suppliers share says none of them."""
    store = Store(tmp_path / "a.db")
    gst = "123456789RT0001"
    store.import_vendor_master([{"vendor_name": "LAKESIDE CATERING LTD", "erp_id": "2003", "gst": gst},
                                {"vendor_name": "LAKESIDE CATERING LTD", "erp_id": "2004", "gst": gst},
                                {"vendor_name": "NORTH RIDGE SUPPLY INC", "erp_id": "3001", "gst": "987654321RT0001"},
                                {"vendor_name": "NORTH RIDGE SUPPLY - REMIT CALGARY", "erp_id": "3002",
                                 "gst": "987654321RT0001"}])  # fmt: skip
    ids = store.vendor_ids()
    for name, number, expected in (("LAKESIDE CATERING LTD #2004", gst, "2004"),
                                   ("Lakeside Catering #2003", gst, "2003"),
                                   ("LAKESIDE CATERING LTD", gst, ""),  # two numbers by the name and by the GST
                                   ("NORTH RIDGE SUPPLY - REMIT CALGARY", "987654321 RT0001", "3002"),
                                   ("NORTH RIDGE SUPPLY INC", "987654321", "3001"),
                                   ("North Ridge Supplies", "987654321RT0001", "")):  # fmt: skip
        assert master_vendor_id(ids, name, number) == expected, name
    # a number one supplier has still finds it whatever the name reads
    store.import_vendor_master([{"vendor_name": "Maple Leaf Coffee", "erp_id": "2020", "gst": "555666777RT0001"}])
    assert master_vendor_id(store.vendor_ids(), "Maple Leaf Café Roasters", "555666777 RT 0001") == "2020"
