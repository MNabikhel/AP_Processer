"""D-267: a purchase order is its number, order type and order company. The same number in two companies is two
orders whose lines never mix; an invoice quoting it bills the one of its supplier, else of its company, else none
(held for a person, never a guess)."""

import sqlite3
from contextlib import closing

import pytest

from ap_coder.coding.po import match_invoice, pick_order
from ap_coder.core.keys import order_key
from ap_coder.core.schema import InvoiceCoding
from ap_coder.core.store import Store


def _line(number, company, supplier, description, price, order_type="OP", line=1, quantity=10):
    return {"po_number": number, "vendor_name": supplier, "supplier_number": "", "order_type": order_type,
            "order_company": company, "line_number": line, "erp_line": f"{line}.000", "description": description,
            "quantity": quantity, "unit_price": price, "amount": quantity * price, "received_quantity": None,
            "gl_code": "", "cost_center": "", "status": "open", "currency": "CAD"}  # fmt: skip


def _invoice(supplier, number, price, company="", quantity=2, invoice="INV-1"):
    return {"vendor_name": supplier, "invoice_number": invoice, "invoice_date": "2026-11-02", "po_number": number,
            "currency": "CAD", "subtotal": quantity * price, "tax_total": 0, "grand_total": quantity * price,
            "confidence_score": 0.9, "company": company,
            "line_items": [{"line_number": 1, "description": "Rebar 15M", "quantity": quantity, "unit_price": price,
                            "amount": quantity * price, "predicted_gl_code": "5100"}]}  # fmt: skip


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "ap.db")
    s.import_purchase_orders([
        _line("4512", "100", "Lakeside Steel Ltd", "Rebar 15M", 12.0),
        _line("4512", "200", "Lakeside Steel Ltd", "Rebar 15M", 14.0),
        _line("4512", "200", "Lakeside Steel Ltd", "Tie wire", 3.0, line=2),
        _line("7001", "100", "Harbour Concrete Inc", "Ready mix", 150.0),
        _line("7001", "300", "Prairie Rentals Co", "Scissor lift", 900.0, quantity=1),
    ])  # fmt: skip
    return s


def test_the_same_po_number_in_two_companies_is_two_orders_whose_lines_never_mix(store):
    first, second = (
        store.purchase_order(order_key("4512", "OP", "100")),
        store.purchase_order(order_key("4512", "OP", "00200")),
    )
    assert (first["order_company"], second["order_company"]) == ("00100", "00200")
    assert [li["unit_price"] for li in first["lines"]] == [12.0]
    assert [li["description"] for li in second["lines"]] == ["Rebar 15M", "Tie wire"]
    assert [o["po_key"] for o in store.orders_numbered("4512")] == ["00100-OP-4512", "00200-OP-4512"]
    assert order_key("PO-4512") == "4512"  # a file naming no type or company: its number, as before


def test_an_invoice_bills_the_order_of_its_supplier_when_the_number_is_shared(store):
    match = match_invoice(InvoiceCoding.model_validate(_invoice("Prairie Rentals Co", "7001", 900.0, quantity=1)),
                          store)  # fmt: skip
    assert match.found and match.po["order_company"] == "00300"
    assert "PO_AMBIGUOUS" not in {f[1] for f in match.findings}


def test_one_supplier_with_the_number_in_two_companies_is_held_until_its_company_is_known(store):
    held = match_invoice(InvoiceCoding.model_validate(_invoice("Lakeside Steel Ltd", "4512", 14.0)), store)
    assert not held.found and held.po is None and len(held.orders) == 2
    assert [f[:2] for f in held.findings] == [("warning", "PO_AMBIGUOUS")]
    assert "company 00100 OP, Lakeside Steel Ltd; company 00200 OP" in held.findings[0][2]
    picked = match_invoice(InvoiceCoding.model_validate(_invoice("Lakeside Steel Ltd", "4512", 14.0, "200")), store)
    assert picked.found and picked.po["po_key"] == "00200-OP-4512"
    assert {f[1] for f in picked.findings} == {"PO_MATCHED"}


def test_what_an_invoice_bills_counts_on_its_own_companys_order_only(store, tmp_path):
    store.add_invoice(tmp_path / "a.pdf", _invoice("Lakeside Steel Ltd", "4512", 14.0, "00200"), {})
    store.add_invoice(tmp_path / "b.pdf", _invoice("Lakeside Steel Ltd", "4512", 14.0, invoice="INV-2"), {})  # held
    store.add_invoice(tmp_path / "c.pdf", _invoice("Harbour Concrete Inc", "7001", 150.0, invoice="H-1"), {})
    billed = {p["po_key"]: (p["invoices"], p["billed"]) for p in store.purchase_orders()}
    assert billed["00200-OP-4512"] == (1, 28.0)
    assert billed["00100-OP-4512"] == (0, 0.0)
    assert billed["00100-OP-7001"] == (1, 300.0) and billed["00300-OP-7001"] == (0, 0.0)
    assert [r["invoice_number"] for r in store.po_invoices("00200-OP-4512")] == ["INV-1"]
    assert store.po_invoices("00100-OP-4512") == []
    rows, _, _ = store.billing_rows(["00200-OP-4512", "00100-OP-4512"], [])
    assert sorted(r[2] or "" for r in rows) == ["", "00200-OP-4512"] and None in [r[2] for r in rows]  # held: neither


def test_pick_order_never_guesses():
    a = {"po_key": "a", "vendor_key": "x", "supplier_number": "", "order_company": "00100"}
    b = {"po_key": "b", "vendor_key": "x", "supplier_number": "", "order_company": "00200"}
    c = {"po_key": "c", "vendor_key": "", "supplier_number": "04410", "order_company": "00300"}
    assert pick_order([a], "y") is a  # one order: it, whoever's (its supplier is checked as before)
    assert pick_order([a, b], "x") is None and pick_order([a, b], "y", company="100") is None
    assert pick_order([a, b], "x", company="100") is a
    assert pick_order([a, b, c], "z", "4410") is c  # by the supplier's number


def test_a_jd_edwards_po_kept_by_its_number_is_kept_again_under_its_company_and_type(tmp_path):
    db = Store(tmp_path / "old" / "ap.db").path
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("DROP INDEX purchase_orders_number")
        conn.execute("ALTER TABLE purchase_orders DROP COLUMN number_key")
        conn.execute("INSERT INTO purchase_orders (po_key, po_number, updated_at, order_type, order_company) VALUES "
                     "('4512', 'PO-4512', '2026-11', 'OP', '00200'), ('81', '81', '2026-11', '', '')")  # fmt: skip
        conn.execute("INSERT INTO po_lines (po_key, line_number, description, quantity, unit_price, amount) VALUES "
                     "('4512', 1, 'Rebar', 2, 40, 80), ('81', 1, 'Toner', 1, 5, 5)")  # fmt: skip
        conn.execute("UPDATE settings SET value = '31' WHERE key = 'schema_version'")
    store = Store(db)
    assert [li["description"] for li in store.purchase_order("00200-OP-4512")["lines"]] == ["Rebar"]
    assert (
        store.purchase_order("4512")["po_key"] == "00200-OP-4512" and store.purchase_order("81")["number_key"] == "81"
    )
    assert [o["po_key"] for o in store.orders_numbered("4512")] == ["00200-OP-4512"]
