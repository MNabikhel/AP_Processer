"""Purchase orders: import, matching (2- and 3-way) and the checks on invoices that quote a PO."""

import copy
import json
import sqlite3

import pytest

from ap_coder.config import Settings
from ap_coder.demo import load_demo, remove_demo
from ap_coder.pipeline import finalise_coding
from ap_coder.po import (
    map_columns,
    match_invoice,
    pair_with_po,
    po_key,
    po_label,
    rows_from_records,
    similarity,
    template_csv,
)
from ap_coder.schema import InvoiceCoding
from ap_coder.store import Store, load_sample_purchase_orders

from .conftest import SAMPLES

NW = "northwind_ON_HST_NW-2026-0912"
CN = "northwind_ON_HST_CN-2026-0047"


def _gt(stem):
    return json.loads((SAMPLES / "ground_truth" / f"{stem}.json").read_text())


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "ap.db")
    load_sample_purchase_orders(s)
    return s


def _issues(store, doc, reference, exclude=None):
    _, report = finalise_coding(InvoiceCoding.model_validate(doc), reference, Settings(), store=store,
                                exclude_invoice_id=exclude)  # fmt: skip
    return {(i.code, i.line_number) for i in report.issues}, report


def _codes(store, doc, reference, exclude=None):
    return {code for code, _ in _issues(store, doc, reference, exclude)[0]}


def test_po_numbers_compare_the_way_people_mean_them():
    assert po_key("PO-00412") == po_key("PO 412") == po_key("412") == po_key("po#412") == "412"
    assert po_key("") == ""
    assert po_label("PO-90377") == "PO-90377" and po_label("90377") == "PO 90377"
    assert po_label("P.O. 55") == "P.O. 55" and po_label("POL-1") == "PO POL-1"


def test_columns_are_recognised_from_common_erp_headers():
    headers = ["PO #", "Supplier", "Line", "Item Description", "Qty Ordered", "Unit Cost", "Extended",
               "Qty Received", "GL Account", "Dept", "Status"]  # fmt: skip
    cols = map_columns(headers)
    assert cols == {
        "po_number": "PO #", "vendor_name": "Supplier", "line_number": "Line", "description": "Item Description",
        "quantity": "Qty Ordered", "unit_price": "Unit Cost", "amount": "Extended", "received_quantity": "Qty Received",
        "gl_code": "GL Account", "cost_center": "Dept", "status": "Status",
    }  # fmt: skip


def test_rows_fill_in_missing_numbers_and_skip_incomplete_rows():
    records = [
        {"po": "PO-1", "desc": "Widgets", "qty": "4", "price": "2.50", "total": "", "received": "", "status": "Open"},
        {"po": "PO-1", "desc": "Gadget", "qty": "", "price": "", "total": "1,200.00", "received": "1",
         "status": "Closed"},
        {"po": "", "desc": "orphan", "qty": "1", "price": "1", "total": "1", "received": "", "status": ""},
    ]  # fmt: skip
    rows, skipped = rows_from_records(records, map_columns(list(records[0])))
    assert skipped == 1
    assert rows[0]["amount"] == 10.0 and rows[0]["received_quantity"] is None and rows[0]["status"] == "open"
    assert rows[1]["quantity"] == 1.0 and rows[1]["unit_price"] == 1200.0 and rows[1]["status"] == "closed"


def test_template_has_every_column():
    header = template_csv().decode("utf-8-sig").splitlines()[0].split(",")
    assert set(map_columns(header)) == set(header)


def test_reimport_replaces_the_lines_of_that_po_only(store):
    before = {p["po_number"]: p for p in store.purchase_orders()}
    assert set(before) == {"PO-88213", "PO-89904", "PO-90155", "PO-90377"}
    result = store.import_purchase_orders(
        [{"po_number": "PO 90377", "vendor_name": "Red River Office Interiors Ltd.", "line_number": 1,
          "description": "Desk", "quantity": 2, "unit_price": 100.0, "amount": 200.0, "received_quantity": 2,
          "gl_code": "1510", "cost_center": "CC700", "status": "open"}]
    )  # fmt: skip
    assert result == {"orders": 1, "added": 0, "updated": 1, "lines": 1}
    assert len(store.purchase_order(po_key("PO-90377"))["lines"]) == 1
    assert len(store.purchase_order(po_key("PO-88213"))["lines"]) == 5
    store.import_purchase_orders([], replace_all=True)
    assert not store.has_purchase_orders()


def test_lines_pair_with_the_po_line_they_bill(store):
    po = store.purchase_order(po_key("PO-88213"))
    pairs = pair_with_po(_gt(NW)["line_items"], po["lines"])
    assert pairs == {1: 1, 2: 2, 3: 3, 4: 4, 5: 5}
    credit = pair_with_po(_gt(CN)["line_items"], po["lines"])
    assert credit == {1: 1, 2: 4}  # "Return - Dell Latitude..." and "Credit - implementation consulting..."
    assert similarity("Office chair", "Dell PowerEdge server") < 0.45


def test_matching_invoice_is_reported_as_matched(store, reference):
    doc = _gt(NW)
    for li in doc["line_items"]:
        if li["line_number"] == 4:
            li["quantity"], li["amount"] = 8, 1200.0  # only the hours received
    doc["subtotal"] = sum(li["amount"] for li in doc["line_items"])
    issues, report = _issues(store, doc, reference)
    assert ("PO_MATCHED", None) in issues
    assert not [i for i in report.issues if i.code.startswith("PO_") and i.severity != "info"]


def test_three_way_match_flags_quantities_not_received(store, reference):
    issues, _ = _issues(store, _gt(NW), reference)  # 12 consulting hours billed, 8 received
    assert ("PO_NOT_RECEIVED", 4) in issues


def test_price_above_the_po_price(store, reference):
    issues, report = _issues(store, _gt("prairie_SK_GST_PST_PNS-104882"), reference)
    assert ("PO_PRICE_OVER", 1) in issues  # 1,180 billed vs 1,150 on the PO
    message = next(i.message for i in report.issues if i.code == "PO_PRICE_OVER")
    assert "$1,150.00" in message and "+2.6%" in message


def test_quantities_add_up_across_invoices_and_credits_subtract(store, reference, tmp_path):
    first = _gt(NW)
    invoice_id = store.add_invoice(tmp_path / "a.pdf", first, {})
    store.approve_invoice(invoice_id, first, "jane")
    second = copy.deepcopy(first)
    second["invoice_number"] = "NW-2026-0999"
    second["line_items"] = [li for li in second["line_items"] if li["line_number"] == 1]
    second["subtotal"] = second["line_items"][0]["amount"]
    issues, report = _issues(store, second, reference)
    assert ("PO_QTY_OVER", 1) in issues  # 3 laptops ordered, 3 + 3 billed
    assert "PO_OVER_BILLED" in {code for code, _ in issues}
    assert "3 on earlier invoices" in next(i.message for i in report.issues if i.code == "PO_QTY_OVER")

    credit = _gt(CN)
    credit_id = store.add_invoice(tmp_path / "cn.pdf", credit, {})
    store.approve_invoice(credit_id, credit, "jane")
    second["line_items"][0].update(quantity=1, amount=1450.0)
    second["subtotal"] = 1450.0
    assert "PO_QTY_OVER" not in _codes(store, second, reference)  # 3 - 1 returned + 1 = 3


def test_lines_not_on_the_po_and_coding_hints(store, reference):
    doc = _gt("redriver_MB_GST_RST_RRO-55821")
    doc["line_items"][0]["predicted_gl_code"] = "6900"
    doc["line_items"].append({**doc["line_items"][3], "line_number": 5, "description": "Rush fee for weekend install"})
    issues, _ = _issues(store, doc, reference)
    assert ("PO_CODING_DIFFERS", 1) in issues
    assert ("PO_LINE_NOT_ON_PO", 5) in issues
    assert ("PO_NOT_RECEIVED", 2) in issues  # 8 chairs billed, 6 received


def test_unknown_closed_and_other_vendor_pos(store, reference):
    doc = _gt(NW)
    doc["po_number"] = "PO-12345"
    assert "PO_UNKNOWN" in _codes(store, doc, reference)
    doc["po_number"] = "90377"  # Red River's PO, quoted without the prefix
    assert "PO_VENDOR_MISMATCH" in _codes(store, doc, reference)
    store.set_po_status(po_key("PO-88213"), "closed")
    assert "PO_CLOSED" in _codes(store, _gt(NW), reference)


def test_missing_po_number_is_mentioned_when_the_vendor_has_open_pos(store, reference):
    doc = _gt(NW)
    doc["po_number"] = ""
    _, report = _issues(store, doc, reference)
    info = next(i for i in report.issues if i.code == "PO_NOT_QUOTED")
    assert info.severity == "info" and "PO-88213" in info.message
    assert "PO_NOT_QUOTED" not in _codes(store, _gt("pacific_BC_GST_PST_PO-77120"), reference)


def test_no_po_checks_without_purchase_orders(tmp_path, reference):
    empty = Store(tmp_path / "empty.db")
    assert not {c for c in _codes(empty, _gt(NW), reference) if c.startswith("PO_")}
    assert match_invoice(InvoiceCoding.model_validate(_gt(NW)), empty) is None


def test_purchase_order_summary_counts_invoices(store, tmp_path):
    doc = _gt(NW)
    store.add_invoice(tmp_path / "a.pdf", doc, {})
    po = next(p for p in store.purchase_orders() if p["po_number"] == "PO-88213")
    assert po["invoices"] == 1 and po["in_review"] == 1
    assert po["billed"] == pytest.approx(doc["subtotal"]) and po["remaining"] == pytest.approx(0)


def test_upgrade_backfills_the_po_of_existing_invoices(tmp_path):
    path = tmp_path / "old.db"
    store = Store(path)
    invoice_id = store.add_invoice(tmp_path / "a.pdf", _gt(NW), {})
    with sqlite3.connect(path) as conn:  # pretend it was made by version 3
        conn.execute("UPDATE invoices SET po_key = ''")
        conn.execute("UPDATE settings SET value = '3' WHERE key = 'schema_version'")
    store = Store(path)
    load_sample_purchase_orders(store)
    assert [i["id"] for i in store.po_invoices(po_key("PO-88213"))] == [invoice_id]


def test_demo_loads_and_removes_its_purchase_orders(tmp_path):
    store = Store(tmp_path / "demo.db")
    load_demo(store)
    assert len(store.purchase_orders()) == 4
    redriver = next(i for i in store.list_invoices() if i["vendor_name"].startswith("Red River"))
    codes = {i["code"] for i in store.get_invoice(redriver["id"])["validation"]["issues"]}
    assert {"PO_CODING_DIFFERS", "PO_NOT_RECEIVED"} <= codes
    remove_demo(store)
    assert not store.has_purchase_orders()


def test_demo_keeps_purchase_orders_that_were_already_there(store, tmp_path):
    load_demo(store)
    remove_demo(store)
    assert len(store.purchase_orders()) == 4  # they were imported before the demo, so they stay
