"""Purchase orders: import, matching (2- and 3-way) and the checks on invoices that quote a PO."""

import copy
import sqlite3

import pytest

from ap_coder.ai.pipeline import finalise_coding
from ap_coder.coding.po import (
    map_columns,
    match_invoice,
    po_key,
    po_label,
    po_lines_billed,
    rows_from_records,
    similarity,
    template_csv,
)
from ap_coder.core.config import Settings
from ap_coder.core.schema import InvoiceCoding
from ap_coder.core.store import Store, load_sample_purchase_orders
from ap_coder.ops.demo import load_demo, remove_demo
from tests.webclient import gt as _gt

NW = "northwind_ON_HST_NW-2026-0912"
CN = "northwind_ON_HST_CN-2026-0047"


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


def test_an_item_column_is_an_sku_never_the_po_line_number():
    """B-138: an export's "Item" (or "Item Number") column holds the part number: SKU 88341 was taken as PO line
    88341. Only a line column is the line number; without one, the lines are numbered in file order."""
    headers = ["PO Number", "Vendor", "Item", "Description", "Qty", "Unit Price", "Amount"]
    assert "line_number" not in map_columns(headers)
    assert "line_number" not in map_columns(["PO", "Item Number", "Description", "Amount"])
    records = [
        {"PO Number": "PO-1", "Vendor": "Acme", "Item": "WID-0042", "Description": "Widget", "Qty": 10,
         "Unit Price": 5, "Amount": 50},
        {"PO Number": "PO-1", "Vendor": "Acme", "Item": "88341", "Description": "Gadget", "Qty": 2,
         "Unit Price": 20, "Amount": 40},
    ]  # fmt: skip
    rows, _ = rows_from_records(records, map_columns(headers))
    assert [r["line_number"] for r in rows] == [None, None]


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


def test_lump_sum_line_exported_with_zero_quantity_and_price_is_amount_only():
    """ERP exports often write 0 (not blank) for the quantity and price of a lump-sum line."""
    records = [
        {"po": "PO-1", "desc": "Consulting", "qty": "0", "price": "0", "total": "5,000.00"},
        {"po": "PO-1", "desc": "Install", "qty": "", "price": "0", "total": "800.00"},
        {"po": "PO-1", "desc": "Freebie", "qty": "0", "price": "0", "total": "0"},
    ]  # fmt: skip
    rows, _ = rows_from_records(records, map_columns(list(records[0])))
    numbers = [(r["quantity"], r["unit_price"], r["amount"], r["amount_only"]) for r in rows]
    assert numbers == [(1.0, 5000.0, 5000.0, True), (1.0, 800.0, 800.0, True), (0.0, 0.0, 0.0, False)]


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
    assert po_lines_billed(_gt(NW)["line_items"], po["lines"]) == [1, 2, 3, 4, 5]
    credit = po_lines_billed(_gt(CN)["line_items"], po["lines"])
    assert credit == [1, 4]  # "Return - Dell Latitude..." and "Credit - implementation consulting..."
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


# --- Left to expense on each PO line (shown on the review screen, never checked) ------------------------------------

PO_TOTAL = 15945.0  # PO-88213: five lines, line 1 three laptops at 1,450.00


def _laptops(quantity, number="NW-2026-0912"):
    """A Northwind invoice on PO-88213 that bills only ``quantity`` laptops (PO line 1)."""
    doc = _gt(NW)
    doc["invoice_number"] = number
    doc["line_items"] = [{**doc["line_items"][0], "quantity": quantity, "amount": 1450.0 * quantity}]
    doc["subtotal"] = 1450.0 * quantity
    return doc


def _left(store, doc, invoice_id=None):
    return match_invoice(InvoiceCoding.model_validate(doc), store, invoice_id)


def test_left_to_expense_is_the_po_line_less_the_other_invoices_and_this_one(store, tmp_path):
    match = _left(store, _laptops(2))
    line = match.left[1]
    assert (line.amount, line.open_amount, line.others, line.this_invoice) == (4350.0, None, 0.0, 2900.0)
    assert line.left == 1450.0
    assert match.left[2].left == 8900.0  # a PO line this invoice doesn't bill: all of it left
    assert match.total_left == pytest.approx(PO_TOTAL - 2900.0)

    store.add_invoice(tmp_path / "a.pdf", _laptops(1, "NW-2026-0901"), {})  # in review: counts
    rejected = store.add_invoice(tmp_path / "b.pdf", _laptops(3, "NW-2026-0902"), {})
    store.reject_invoice(rejected, "jane")  # not live: doesn't
    this = store.add_invoice(tmp_path / "c.pdf", _laptops(2), {})
    match = _left(store, _laptops(2), this)  # this invoice once, not again as another one
    assert (match.left[1].others, match.left[1].this_invoice, match.left[1].left) == (1450.0, 2900.0, 0.0)
    assert match.total_left == pytest.approx(PO_TOTAL - 1450.0 - 2900.0)


def test_over_the_po_line_is_below_zero_and_the_checks_are_as_before(store, reference, tmp_path):
    earlier = store.add_invoice(tmp_path / "a.pdf", _laptops(3, "NW-2026-0901"), {})
    store.approve_invoice(earlier, _laptops(3, "NW-2026-0901"), "jane")
    match = _left(store, _laptops(2))
    assert match.left[1].left == -2900.0
    assert match.total_left == pytest.approx(PO_TOTAL - 4350.0 - 2900.0)
    assert {c for c in _codes(store, _laptops(2), reference) if c.startswith("PO_")} == {"PO_QTY_OVER"}


def test_with_an_open_amount_only_the_invoices_not_exported_yet_count(tmp_path):
    """The ERP's open amount at import has counted the invoices exported to it: only the ones not exported yet, and
    this one, come off it. A PO line without one: its amount less every live invoice on it."""
    store = Store(tmp_path / "ap.db")
    records = [
        {"PO Number": "PO-88213", "Vendor": "Northwind IT Solutions Inc.", "Line": 1,
         "Description": "Dell Latitude 7450 laptop, 32GB RAM", "Qty": 3, "Unit Price": 1450, "Amount": 4350,
         "Open Amt": "2,900.00"},
        {"PO Number": "PO-88213", "Vendor": "Northwind IT Solutions Inc.", "Line": 2,
         "Description": "Dell PowerEdge R760 rack server", "Qty": 1, "Unit Price": 8900, "Amount": 8900,
         "Open Amt": ""},
    ]  # fmt: skip
    rows, _ = rows_from_records(records, map_columns(list(records[0])))
    store.import_purchase_orders(rows)
    exported = store.add_invoice(tmp_path / "a.pdf", _laptops(1, "NW-2026-0901"), {})
    store.approve_invoice(exported, _laptops(1, "NW-2026-0901"), "jane")
    store.create_export_batch([exported], "csv")
    store.add_invoice(tmp_path / "b.pdf", _laptops(1, "NW-2026-0902"), {})  # in review, not exported

    match = _left(store, _laptops(1))
    line = match.left[1]
    assert (line.open_amount, line.others, line.this_invoice, line.left) == (2900.0, 1450.0, 1450.0, 0.0)
    assert (match.left[2].open_amount, match.left[2].left) == (None, 8900.0)
    assert match.total_left == pytest.approx(8900.0)
    # The exported invoice looked at again: the ERP has counted it, so it doesn't come off the open amount twice.
    assert _left(store, _laptops(1, "NW-2026-0901"), exported).left[1].left == 1450.0


def test_the_po_import_reads_an_optional_open_amount_column():
    for header in ("Open amount", "Amount open", "Open Amt", "AOPN"):
        cols = map_columns(["PO", "Description", "Amount", header])
        assert cols["open_amount"] == header and cols["amount"] == "Amount"
    assert "open_amount" not in map_columns(["PO", "Description", "Amount"])
    records = [{"PO": "PO-1", "Description": "Widget", "Amount": "100", "AOPN": "60.50"},
               {"PO": "PO-1", "Description": "Gadget", "Amount": "40", "AOPN": ""}]  # fmt: skip
    rows, _ = rows_from_records(records, map_columns(list(records[0])))
    assert [r["open_amount"] for r in rows] == [60.5, None]
    assert "open_amount" in template_csv().decode("utf-8-sig").splitlines()[0].split(",")


def test_a_database_from_before_the_open_amount_gets_its_column(tmp_path):
    path = tmp_path / "old.db"
    Store(path)
    with sqlite3.connect(path) as conn:  # as an AP Coder before the open amount made it
        conn.execute("ALTER TABLE po_lines DROP COLUMN open_amount")
    store = Store(path)
    row = {"po_number": "PO-1", "vendor_name": "Acme", "line_number": 1, "description": "Widget", "quantity": 1,
           "unit_price": 100.0, "amount": 100.0, "open_amount": 60.0, "status": "open"}  # fmt: skip
    store.import_purchase_orders([row, {**row, "line_number": 2, "open_amount": None}])
    assert [li["open_amount"] for li in store.purchase_order(po_key("PO-1"))["lines"]] == [60.0, None]


def test_a_pos_invoices_read_lean_give_what_their_whole_codings_gave(store, tmp_path):
    """D-199: ``po_invoices`` has SQLite read out each invoice's subtotal, date and lines (a PO billed by every
    invoice of a big vendor has thousands, whose whole codings took 1.5 s to decode at each open and check), and the
    PO's billed lines match each (description, unit price) once. What the PO's match and page read is what the whole
    codings gave: the invoices, their subtotals and dates, what each PO line was billed (quantities and amounts, with
    and without credits); a coding that isn't JSON fails as it did."""
    import json
    import random

    from ap_coder.coding import po as po_mod
    from ap_coder.core.store.purchasing import PO_CODING

    key = po_key("PO-88213")
    order = store.purchase_order(key)
    base, rnd = _gt(NW), random.Random(7)
    words = [pl["description"] for pl in order["lines"]] + ["Freight", "Fuel surcharge", "Something else entirely"]
    for n in range(80):
        doc = copy.deepcopy(base)
        doc["invoice_number"] = f"NW-{n}"
        doc["line_items"] = [{**base["line_items"][0], "description": rnd.choice(words),
                              "unit_price": rnd.choice([1.5, 2.0, 120.0, "120.00", None]),
                              "quantity": rnd.choice([1, 2, -1, 0, 3.5]), "amount": rnd.choice([10.0, -10.0, 0, 7.25])}
                             for _ in range(rnd.randint(0, 6))]  # fmt: skip
        if n % 7 == 0:
            doc.pop("subtotal", None)
        if n % 11 == 0:
            doc.pop("line_items")
        store.add_invoice(tmp_path / f"{n}.pdf", doc, {})
    with sqlite3.connect(store.path) as conn:
        ids = [r[0] for r in conn.execute("SELECT id FROM invoices WHERE po_key = ? ORDER BY id", (key,))]
        for n in ids[::3]:  # approved with a coding of their own
            doc = json.loads(conn.execute("SELECT ai_output FROM invoices WHERE id = ?", (n,)).fetchone()[0])
            doc["subtotal"], doc["line_items"] = 99.5, (doc.get("line_items") or [])[:2]
            conn.execute("UPDATE invoices SET status = 'approved', final_output = ?, export_batch = ? WHERE id = ?",
                         (json.dumps(doc), "batch-1" if n % 2 else None, n))  # fmt: skip
        conn.execute("UPDATE invoices SET final_output = '' WHERE id = ?", (ids[1],))  # (AP Coder's coding, then)
        conn.execute("UPDATE invoices SET final_output = 'null' WHERE id = ?", (ids[4],))  # (valid JSON, no object)
        conn.execute("UPDATE invoices SET status = 'rejected' WHERE id = ?", (ids[5],))
        whole = [{"id": r[0], "status": r[1], "invoice_number": r[2], "invoice_date": r[3],
                  "coding": json.loads(r[5] or r[4] or "{}"), "export_batch": r[6]}
                 for r in conn.execute("SELECT id, status, invoice_number, invoice_date, ai_output, final_output, "
                                       "export_batch FROM invoices WHERE po_key = ? AND status IN ('review', 'parked', "
                                       "'pending_approval', 'approved') ORDER BY id", (key,))]  # fmt: skip
    with pytest.raises(AttributeError):  # ("null": not an object, as before)
        po_mod.billed_by_line(order, store.po_invoices(key))
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE invoices SET final_output = '{}' WHERE id = ?", (ids[4],))
    whole = [{**w, "coding": {} if w["id"] == ids[4] else w["coding"]} for w in whole]
    lean = store.po_invoices(key)
    assert len(lean) == len(whole) > 50
    for got, was in zip(lean, whole, strict=True):
        assert got == {**was, "coding": {k: was["coding"].get(k) for k in PO_CODING}}
    assert [o["id"] for o in store.po_invoices(key, exclude_id=ids[0])] == [w["id"] for w in whole[1:]]

    def billed(invoices, positive_only=False, amounts=False):  # (as before: every line matched afresh)
        out = {}
        for inv in invoices:
            lines = inv["coding"].get("line_items") or []
            if positive_only:
                lines = [li for li in lines if po_mod.billed_quantity(li) > 0]
            for li, line in zip(lines, po_mod.po_lines_billed(lines, order["lines"]), strict=True):
                if line is not None:
                    value = float(li.get("amount") or 0) if amounts else po_mod.billed_quantity(li)
                    out[line] = out.get(line, 0.0) + value
        return out

    matched = {}
    for positive_only, amounts in ((False, False), (False, True), (True, False), (True, True)):
        assert po_mod.billed_by_line(order, lean, positive_only, amounts, matched) == billed(whole, positive_only,
                                                                                               amounts)  # fmt: skip
    assert billed(whole) and matched
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE invoices SET final_output = 'not JSON' WHERE id = ?", (ids[3],))
    with pytest.raises(json.JSONDecodeError):
        store.po_invoices(key)
