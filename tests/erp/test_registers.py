"""Duplicates against the ERP's invoice register."""

from ap_coder.ai.pipeline import finalise_coding
from ap_coder.core.config import Settings
from ap_coder.core.schema import InvoiceCoding
from ap_coder.core.store import Store, load_sample_setup
from ap_coder.erp import registers
from tests.jde_exports import LONG_NAME, VOUCHER_HEAD, records, sample_pay_items, voucher_headers
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
                      "foreign": "Foreign Amount", "currency": "Curr Code", "batch": "Batch Number"}  # fmt: skip
    rows, read = registers.vouchers_from_pay_items(listed, chosen, NAMES)
    kept = ("key", "vendor_name", "an8", "invoice_number", "invoice_date", "total")
    assert [tuple(r[field] for field in kept) for r in rows] == [
        ("08801-PV-700101", "MAPLE LEAF COFFEE SERVICES INC.", "410001", "INV-0042", "2026-03-04", 214.20),
        ("08801-PV-700102", "QUICKSHIP COURIER CANADA LTD", "410002", "7-100-20001", "2026-03-04", 700.00),
        ("08801-PV-700103", "HARBOUR TELECOM", "410005", "00812", "2026-03-04", 438.90),
        ("08801-PV-700104", LONG_NAME[:30], "410009", "US-5001", "2026-03-04", 1100.00),  # not in the master
        ("08802-PV-700101", "QUICKSHIP COURIER CANADA LTD", "410002", "Q-9", "2026-03-04", 61.00),
    ]  # fmt: skip
    assert [(r["document"], r["batch"], r["currency"]) for r in rows] == [
        ("700101", "5550001", "CAD"), ("700102", "5550002", "CAD"), ("700103", "5550001", "CAD"),
        ("700104", "5550001", "USD"), ("700101", "5550001", "CAD"),
    ]  # fmt: skip
    assert read == {"pay_items": 11, "vouchers": 5, "added_up": 2, "totals_rows": 1, "named": 4, "foreign": {"USD": 1},
                    "skipped": {"with no invoice number": [1, 1], "adding up to 0.00": [1, 2]}}  # fmt: skip
    assert registers.vouchers_said(read) == (
        "JD Edwards' voucher list: 5 vouchers read from 11 pay items (2 vouchers added up from more than one pay "
        "item). 4 named from the vendor master by their supplier number, 1 by the name on the list (cut at 30 "
        "characters). 1 voucher in USD, by its foreign amount. Left out: the totals row; 1 voucher with no invoice "
        "number (1 pay item); 1 voucher adding up to 0.00 (2 pay items)."
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
