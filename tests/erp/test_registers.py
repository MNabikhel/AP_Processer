"""Duplicates against the ERP's invoice register."""

import datetime as dt

from ap_coder.ai.pipeline import finalise_coding
from ap_coder.core.config import Settings
from ap_coder.core.schema import InvoiceCoding
from ap_coder.core.store import Store, load_sample_setup
from ap_coder.erp import registers
from tests.jde_exports import LONG_NAME, VOUCHER_HEAD, pay_item, records, sample_pay_items, voucher_headers
from tests.webclient import gt as _gt


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


def test_the_suppliers_invoice_number_is_chosen_over_the_erps_document_number():
    """An ERP register lists its own document number next to the supplier's invoice number: the duplicate check
    needs the supplier's."""
    for header in ("Supplier Invoice", "Vendor Invoice", "Supplier Invoice Number", "Vendor Invoice No"):
        for document in ("Document Number", "Doc No", "Document"):
            cols = registers.map_columns(["Supplier Name", document, header, "Date", "Amount"])
            assert cols["invoice_number"] == header, (document, header)
    assert registers.map_columns(["Vendor", "Document Number", "Amount"])["invoice_number"] == "Document Number"


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


# --- JD Edwards' voucher list (Supplier Ledger Inquiry's grid): one row per pay item ---------------------------------

NAMES = {"410001": "MAPLE LEAF COFFEE SERVICES INC.", "410002": "QUICKSHIP COURIER CANADA LTD",
         "410005": "HARBOUR TELECOM"}  # fmt: skip


def _voucher_list(head=None):
    return records("vouchers.xlsx", voucher_headers(sample_pay_items(), head))


def test_jd_edwards_voucher_list_is_read_as_it_comes():
    """B-2084: the grid has no vendor name column the register's guess knows (a supplier number, and its name cut at
    30 characters): 0 rows were imported. Its vouchers are read by their own columns: the supplier by number, the pay
    items of one voucher added up, the totals row left out, a USD voucher by its foreign amount."""
    columns, listed = _voucher_list()
    assert "vendor_name" not in registers.map_columns(columns)  # what gave 0 rows
    chosen = registers.voucher_columns(columns)
    assert chosen == {"document": "Document Number", "doc_type": "Doc Type", "company": "Doc Co",
                      "pay_item": "Pay Item", "an8": "Supplier Number", "supplier_name": "Supplier Number Desc",
                      "invoice_number": "Invoice Number", "invoice_date": "Invoice Date", "gross": "Gross Amount",
                      "foreign": "Foreign Amount", "currency": "Curr Code", "batch": "Batch Number",
                      "gl_date": "G/L Date"}  # fmt: skip
    rows, read = registers.vouchers_from_pay_items(listed, chosen, NAMES)
    kept = ("key", "vendor_name", "an8", "invoice_number", "invoice_date", "total")
    assert [tuple(r[field] for field in kept) for r in rows] == [
        ("08801-PV-700101", "MAPLE LEAF COFFEE SERVICES INC.", "410001", "INV-0042", "2026-03-04", 214.20),
        ("08801-PV-700102", "QUICKSHIP COURIER CANADA LTD", "410002", "7-100-20001", "2026-03-04", 700.00),
        ("08801-PV-700103", "MAPLE LEAF COFFEE SERVICES INC.", "410001", "ML-3318", "2026-03-04", 642.90),
        ("08801-PV-700104", LONG_NAME[:30], "410009", "US-5001", "2026-03-04", 1100.00),  # not in the master
        ("08802-PV-700101", "QUICKSHIP COURIER CANADA LTD", "410002", "Q-9", "2026-03-04", 61.00),
    ]  # fmt: skip
    assert [(r["document"], r["batch"], r["currency"]) for r in rows] == [
        ("700101", "5550001", "CAD"), ("700102", "5550002", "CAD"), ("700103", "5550001", "CAD"),
        ("700104", "5550001", "USD"), ("700101", "5550001", "CAD"),
    ]  # fmt: skip
    day = ["2026-03-04", "2026-03-04"]
    assert read == {"pay_items": 11, "vouchers": 5, "added_up": 2, "totals_rows": 1, "named": 4, "foreign": {"USD": 1},
                    "skipped": {"with no invoice number": [1, 1], "adding up to 0.00": [1, 2]}, "by_supplier": False,
                    "domestic": {}, "complete": True, "covers": {"08801-PV-": day, "08802-PV-": day},
                    "voided": ["08801-PV-700106"]}  # fmt: skip
    assert registers.vouchers_said(read) == (
        "JD Edwards' voucher list: 5 vouchers read from 11 pay items (2 vouchers added up from more than one pay "
        "item). 4 named from the vendor master by their supplier number, 1 by the name on the list (cut at 30 "
        "characters). 1 voucher in USD, by its foreign amount. Left out: the totals row; 1 voucher with no invoice "
        "number (1 pay item); 1 voucher adding up to 0.00 (2 pay items). It covers company 08801 PV, G/L dates "
        "2026-03-04 to 2026-03-04; company 08802 PV, G/L dates 2026-03-04 to 2026-03-04 (its own rows' dates; its "
        "totals row adds up)."
    )
    # the grid's other caption for the pay item; a file without the foreign amount and the currency
    short = [h if h != "Pay Item" else "Pay Itm" for h in VOUCHER_HEAD if h not in ("Foreign Amount", "Curr Code")]
    columns, listed = _voucher_list(short)
    rows, read = registers.vouchers_from_pay_items(listed, registers.voucher_columns(columns), {})
    assert rows[3]["total"] == 1507.00 and rows[3]["currency"] == "" and read["foreign"] == {} and read["named"] == 0
    # any other list is not one: the columns are picked as before
    assert registers.voucher_columns(["Supplier", "Vendor Invoice", "Posting Date", "Gross Amount"]) == {}
    assert registers.voucher_columns(["Document Number", "Supplier Number", "Invoice Number", "Gross Amount"]) == {}


def test_jd_edwards_vouchers_are_duplicates_by_supplier_number_and_keep_their_document_and_batch(tmp_path):
    """B-2084: the vouchers read are the register's bills (the duplicate check finds one by its supplier's number
    whatever name the list cut), each with JD Edwards' document number and batch, looked up by the supplier, the
    invoice number and the gross."""
    store = Store(tmp_path / "a.db")
    load_sample_setup(store)
    store.import_vendor_master([{"vendor_name": LONG_NAME + " AND SUPPLY", "erp_id": "410009"},
                                {"vendor_name": "Quickship Courier Canada Ltd.", "erp_id": "410002"}])  # fmt: skip
    columns, listed = _voucher_list()
    names = {v["erp_id"]: v["display_name"] for v in store.master_vendors()}
    rows, read = registers.vouchers_from_pay_items(listed, registers.voucher_columns(columns), names)
    assert read["named"] == 3 and rows[3]["vendor_name"] == LONG_NAME + " AND SUPPLY"
    assert store.import_erp_vouchers(rows, actor="jane") == 5
    assert store.import_erp_vouchers(rows) == 5  # the same list again adds nothing
    assert {r["copies"] for r in store.erp_register()} == {1}
    quickship = _gt(vendor_name="Quickship Courier Canada Ltd.", invoice_number="7-100-20001", grand_total=700.00,
                    gst_hst_registration_number="")  # fmt: skip
    assert _codes(store, quickship)["DUPLICATE_IN_ERP"] == "error"
    assert "DUPLICATE_IN_ERP" not in _codes(store, {**quickship, "invoice_number": "7-100-20002"})
    # the supplier as the invoice prints it, not as the list names it: found by its number
    plains = {**quickship, "vendor_name": "Great Plains Industrial Equipment Rental and Supply",
              "invoice_number": "US-5001", "grand_total": 1100.00, "currency": "USD"}  # fmt: skip
    assert _codes(store, plains)["DUPLICATE_IN_ERP"] == "error"
    assert store.erp_vouchers_of("Quickship Courier Canada Ltd.", "7-100-20001", 700.00) == [
        {"erp_key": "08801-PV-700102", "document": "700102", "batch": "5550002", "currency": "CAD",
         "invoice_date": "2026-03-04", "total": 700.00}
    ]  # fmt: skip
    found = store.erp_vouchers_of("GREAT PLAINS INDUSTRIAL", "US 5001", 1100.0, an8="410009")  # by supplier number
    assert [(v["document"], v["batch"], v["currency"]) for v in found] == [("700104", "5550001", "USD")]
    assert store.erp_vouchers_of("Quickship Courier Canada Ltd.", "7-100-20001", 300.00) == []  # a pay item's gross
    assert store.erp_vouchers_of("Quickship Courier Canada Ltd.", "Q-9", 61.0)[0]["erp_key"] == "08802-PV-700101"
    assert "jane" in {e["actor"] for e in store.events(actions=["erp_register_imported"])}
    # Replace: the list as it now is, and nothing else
    assert store.import_erp_vouchers(rows[:1], replace_all=True) == 1 and store.erp_register_count() == 1
    assert store.erp_vouchers_of("Quickship Courier Canada Ltd.", "7-100-20001", 700.00) == []
    # the vouchers replaced are gone themselves, not only their bills: the same bill listed again by a plain
    # register (no document number) has no voucher of the old list under it
    store.import_erp_register([{"vendor_name": "Quickship Courier Canada Ltd.", "invoice_number": "7-100-20001",
                                "total": 700.00}])  # fmt: skip
    assert store.in_erp("Quickship Courier Canada Ltd.", "7-100-20001", 700.00)
    assert store.erp_vouchers_of("Quickship Courier Canada Ltd.", "7-100-20001", 700.00) == []


def test_a_supplier_number_written_with_leading_zeros_is_named_from_the_master():
    """The voucher list writes "0410002" where the master has 410002 (or the reverse): the same supplier."""
    items = [pay_item(700201, "001", 10.0, 410002, "QUICKSHIP COURIER CANADA LTD", "Z-1")]
    items[0]["Supplier Number"] = "0410002"
    columns, listed = records("vouchers.xlsx", voucher_headers(items))
    rows, read = registers.vouchers_from_pay_items(listed, registers.voucher_columns(columns), NAMES)
    assert (rows[0]["vendor_name"], read["named"]) == ("QUICKSHIP COURIER CANADA LTD", 1)
    rows, read = registers.vouchers_from_pay_items(listed, registers.voucher_columns(columns),
                                                   {"00410002": "Quickship Courier"})  # fmt: skip
    assert (rows[0]["vendor_name"], read["named"]) == ("Quickship Courier", 1)


# --- What the voucher list cannot tell, and the vouchers voided since (B-2164, B-2165) -----------------------------


def _read(items, head=None, names=None):
    columns, listed = records("vouchers.xlsx", voucher_headers(items, head))
    return registers.vouchers_from_pay_items(listed, registers.voucher_columns(columns), names or {})


def test_a_voucher_list_without_its_company_or_foreign_amount_says_what_was_assumed():
    """B-2165: with no "Doc Co" / "Doc Type" column, the pay items of two suppliers under one document number were
    added up as one voucher; a USD voucher with no foreign amount column was kept at its Canadian gross, labelled USD,
    without a word; and a USD pay item whose foreign amount was blank fell out of its voucher's sum."""
    courier, coffee = (410002, "QUICKSHIP COURIER CANADA LTD"), (410001, "MAPLE LEAF COFFEE SERVICES INC.")
    items = [pay_item(800401, "001", 10.00, *courier, "A-1"), pay_item(800401, "002", 20.00, *courier, "A-1"),
             pay_item(800401, "001", 5.00, *coffee, "C-9")]  # fmt: skip
    bare = [h for h in VOUCHER_HEAD if h not in ("Doc Co", "Doc Type")]
    rows, read = _read(items, bare)
    assert sorted((r["key"], r["an8"], r["invoice_number"], r["total"]) for r in rows) == [
        ("--800401-410001", "410001", "C-9", 5.00), ("--800401-410002", "410002", "A-1", 30.00)
    ]  # fmt: skip
    assert read["by_supplier"] and read["covers"] == {}  # (no company: no period it can be said to cover)
    assert ("The list has no document company or document type column: a voucher is told by its document number and "
            "its supplier.") in registers.vouchers_said(read)  # fmt: skip
    usd = [pay_item(800500, "001", 1370.00, *courier, "US-1", currency="USD"),
           pay_item(800501, "001", 1370.00, *courier, "US-2", foreign=1000.00, currency="USD"),
           pay_item(800501, "002", 137.00, *courier, "US-2", currency="USD")]  # fmt: skip
    rows, read = _read(usd)
    assert [(r["key"], r["total"], r["currency"]) for r in rows] == [("08801-PV-800500", 1370.00, "USD")]
    assert read["domestic"] == {"USD": 1} and read["skipped"] == {"with a pay item that has no foreign amount": [1, 2]}
    said = registers.vouchers_said(read)
    assert ("1 voucher in USD kept at its gross amount in Canadian dollars (the list gives no foreign amount): it will "
            "not match an invoice's USD total.") in said  # fmt: skip
    assert "1 voucher with a pay item that has no foreign amount (2 pay items)" in said


def test_a_voucher_voided_since_it_was_imported_leaves_the_register(tmp_path):
    """B-2164: a voucher voided in JD Edwards after its list was imported stayed in the register: re-keyed, the bill
    counted two copies, and an invoice whose only voucher was voided stayed DUPLICATE_IN_ERP. A later list that shows
    it at 0.00 takes it off; one that no longer shows it takes it off only when it is whole and said not filtered."""
    store = Store(tmp_path / "a.db")
    coffee = (410001, "MAPLE LEAF COFFEE SERVICES INC.")
    march = [pay_item(810001, "001", 214.20, *coffee, "INV-0042"), pay_item(810002, "001", 99.00, *coffee, "INV-0043"),
             pay_item(810003, "001", 12.00, *coffee, "INV-0044", date=dt.datetime(2026, 3, 20))]  # fmt: skip
    rows, read = _read(march)
    assert store.import_erp_vouchers(rows) == 3 and read["voided"] == [] and read["complete"]
    assert read["covers"] == {"08801-PV-": ["2026-03-04", "2026-03-20"]}
    # the next list: 810001 voided and keyed again as 810050; 810002 no longer shown (deleted); 810003 as it was
    april = [pay_item(810001, "001", 0.00, *coffee, "INV-0042"), pay_item(810050, "001", 214.20, *coffee, "INV-0042"),
             march[2]]  # fmt: skip
    rows, read = _read(april)
    assert read["voided"] == ["08801-PV-810001"] and store.import_erp_vouchers(rows) == 3
    assert store.erp_vouchers_left(read["voided"], {}, [r["key"] for r in rows]) == 1
    assert [v["document"] for v in store.erp_vouchers_of(coffee[1], "INV-0042", 214.20)] == ["810050"]
    assert store.erp_register_repeats() == 0 and store.in_erp(coffee[1], "INV-0043", 99.00)  # not shown: still there
    # said whole and not filtered: what it no longer shows for its period goes too; a second time nothing more does
    assert store.erp_vouchers_left(read["voided"], read["covers"], [r["key"] for r in rows]) == 1
    assert store.in_erp(coffee[1], "INV-0043", 99.00) == [] and store.erp_register_count() == 2
    assert store.erp_vouchers_left(read["voided"], read["covers"], [r["key"] for r in rows]) == 0
    # a voucher dated outside the list's own dates, or another company's, is not the list's to take off
    store.import_erp_vouchers(_read([pay_item(810099, "001", 7.00, *coffee, "LATE", date=dt.datetime(2026, 4, 2)),
                                     pay_item(810001, "001", 8.00, *coffee, "OTHER", company="08802")])[0])  # fmt: skip
    assert store.erp_vouchers_left([], read["covers"], [r["key"] for r in rows]) == 0
    # a file cut short (no totals row, or one that is not the sum of its rows) is not whole
    columns, listed = records("vouchers.xlsx", voucher_headers(april))
    cut = registers.vouchers_from_pay_items(listed[:-1], registers.voucher_columns(columns), {})[1]
    short = registers.vouchers_from_pay_items(listed[:1] + listed[2:], registers.voucher_columns(columns), {})[1]
    assert not cut["complete"] and not short["complete"]
    assert "may be cut short: a voucher it does not show is never taken off the list" in registers.vouchers_said(cut)
    # only the void in the list: the invoice is no duplicate any more
    store.import_erp_vouchers(_read([pay_item(810200, "001", 55.00, *coffee, "INV-0050")])[0])
    rows, read = _read([pay_item(810200, "001", 0.00, *coffee, "INV-0050")])
    assert rows == [] and store.erp_vouchers_left(read["voided"], {}, []) == 1
    assert store.in_erp(coffee[1], "INV-0050", 55.00) == []
