"""Regression tests for the bugs found in the overnight review of the new features."""

import datetime as dt
import json
import math
import sqlite3

import pandas as pd
import pytest

from ap_coder import accruals, controls
from ap_coder import statements as stm
from ap_coder.cli import new_files
from ap_coder.config import Settings
from ap_coder.pipeline import finalise_coding
from ap_coder.po import billed_quantity, map_columns, po_key, rows_from_records
from ap_coder.review import split_line
from ap_coder.schema import InvoiceCoding
from ap_coder.store import Store, load_sample_purchase_orders, load_sample_setup
from ap_coder.terms import Terms, parse_terms, payment

from .conftest import SAMPLE_STEM, SAMPLES


def _gt(stem=SAMPLE_STEM, **changes):
    return {**json.loads((SAMPLES / "ground_truth" / f"{stem}.json").read_text()), **changes}


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Net 30 days upon receipt of invoice", Terms(30)),
        ("30 jours sur réception de la facture", Terms(30)),
        ("Net 45, 1% 15 days", Terms(45, 1.0, 15)),
        ("Due in 45 days, 2% discount if paid within 10 days", Terms(45, 2.0, 10)),
        ("2/10 EOM net 30", Terms(30, 2.0, 10, eom=True)),
        ("Net 30 EOM", Terms(30, eom=True)),
        ("Due 10/15 net 45", Terms(45)),
    ],
)
def test_terms_misreads(text, expected):
    assert parse_terms(text) == expected


def test_eom_terms_count_from_the_end_of_the_month():
    p = payment({"invoice_date": "2026-09-20", "payment_terms": "2/10 EOM net 30", "grand_total": 100, "subtotal": 100})
    assert p.due == dt.date(2026, 10, 30) and p.discount_by == dt.date(2026, 10, 10)


@pytest.mark.parametrize(
    "text, value",
    [("-150.00", -150.0), ("$-150", -150.0), ("150,00", 150.0), ("1 234,56", 1234.56), ("(2,316.50)", -2316.5)],
)
def test_statement_amounts(text, value):
    assert stm._number(text) == value


def test_statement_credit_matches_and_description_words_are_not_payments():
    invoices = [{"id": 1, "invoice_number": "CN-47", "grand_total": -150.0, "invoice_date": "2026-09-01",
                 "status": "approved"},
                {"id": 2, "invoice_number": "1001", "grand_total": 80.0, "invoice_date": "2026-09-02",
                 "status": "approved"}]  # fmt: skip
    rows = [{"n": "CN-47", "a": "-150.00", "t": "Credit"}, {"n": "1001", "a": "80,00", "t": "Shelving left side"}]
    rec = stm.reconcile(rows, {"number": "n", "amount": "a", "type": "t"}, invoices)
    assert [li.status for li in rec.lines] == [stm.MATCHED, stm.MATCHED]


def test_po_import_tolerates_nan_and_decimal_commas(tmp_path):
    records = [{"PO": "PO-1", "Description": "Widgets", "Qty": "NaN", "Price": "25,00", "Amount": "50,00"}]
    rows, _ = rows_from_records(records, map_columns(list(records[0])))
    assert rows[0]["quantity"] == 2.0 and rows[0]["unit_price"] == 25.0
    Store(tmp_path / "a.db").import_purchase_orders(rows)  # no NOT NULL failure
    assert po_key("PO Number 412") == "412"


def _po_store(tmp_path, lines):
    store = Store(tmp_path / "a.db")
    load_sample_setup(store)
    rows, _ = rows_from_records(lines, map_columns(list(lines[0])))
    store.import_purchase_orders(rows)
    return store


def _check(store, doc):
    _, report = finalise_coding(InvoiceCoding.model_validate(doc), store.reference_data(), Settings(), store=store)
    return {i.code for i in report.issues}


def _doc(number, lines, po="PO-500"):
    items = [{"line_number": i + 1, "description": d, "quantity": q, "unit_price": p, "amount": round(q * p, 2),
              "predicted_gl_code": "6000", "predicted_cost_center": "CC100", "taxes_applied": []}
             for i, (d, q, p) in enumerate(lines)]  # fmt: skip
    subtotal = round(sum(li["amount"] for li in items), 2)
    return {
        "vendor_name": "Widget Co",
        "invoice_number": number,
        "invoice_date": "2026-09-10",
        "po_number": po,
        "currency": "CAD",
        "supplier_province": "AB",
        "ship_to_province": "AB",
        "subtotal": subtotal,
        "tax_lines": [],
        "tax_total": 0.0,
        "grand_total": subtotal,
        "confidence_score": 0.9,
        "line_items": items,
    }


def test_credit_with_positive_quantity_and_negative_price_reduces_billing(tmp_path):
    store = _po_store(tmp_path, [{"PO": "PO-500", "Vendor": "Widget Co", "Description": "Blue widgets", "Qty": "10",
                                  "Price": "50"}])  # fmt: skip
    first = _doc("A", [("Blue widgets", 10, 50.0)])
    store.approve_invoice(store.add_invoice(tmp_path / "a.pdf", first, {}), first, "Jane")
    credit = _doc("CN", [("Blue widgets returned", 2, -50.0)])
    assert billed_quantity(credit["line_items"][0]) == -2
    assert "PO_QTY_OVER" not in _check(store, credit)
    store.approve_invoice(store.add_invoice(tmp_path / "c.pdf", credit, {}), credit, "Jane")
    assert "PO_QTY_OVER" not in _check(store, _doc("B", [("Blue widgets", 2, 50.0)]))


def test_amount_only_po_lines_compare_amounts(tmp_path):
    store = _po_store(tmp_path, [{"PO": "PO-500", "Vendor": "Widget Co", "Description": "Consulting services",
                                  "Amount": "5000"}])  # fmt: skip
    assert store.purchase_order("500")["lines"][0]["amount_only"] == 1
    assert "PO_QTY_OVER" not in _check(store, _doc("A", [("Consulting services (hours)", 20, 250.0)]))
    assert "PO_QTY_OVER" in _check(store, _doc("B", [("Consulting services (hours)", 24, 250.0)]))


def test_received_goods_invoiced_after_the_period_end_are_accrued(tmp_path):
    store = Store(tmp_path / "a.db")
    load_sample_setup(store)
    load_sample_purchase_orders(store)
    end = dt.date(2026, 9, 30)
    before = accruals.summary(accruals.build(store, end))[accruals.RECEIVED][1]
    later = _gt(invoice_date="2026-10-02")
    output, _ = finalise_coding(InvoiceCoding.model_validate(later), store.reference_data(), Settings(), store=store)
    store.add_invoice(tmp_path / "a.pdf", output, {})
    assert accruals.summary(accruals.build(store, end))[accruals.RECEIVED][1] == before


def test_final_approval_rechecks_the_first_approver(tmp_path):
    store = Store(tmp_path / "a.db")
    store.set_setting("approval_limit", "1000")
    invoice_id = store.add_invoice(tmp_path / "a.pdf", _gt(), {})
    store.approve_invoice(invoice_id, _gt(), "Alice")
    stale = store.get_invoice(invoice_id)
    store.send_back(invoice_id, "Carol")
    store.approve_invoice(invoice_id, _gt(), "Bob")
    real_get = store.get_invoice
    store.get_invoice = lambda i: stale  # Bob's page still shows Alice's first approval
    with pytest.raises(ValueError):
        store.final_approve(invoice_id, "Bob")
    store.get_invoice = real_get
    assert store.get_invoice(invoice_id)["status"] == "pending_approval"


def test_restoring_an_old_backup_creates_the_new_tables(tmp_path):
    old = tmp_path / "old.db"
    Store(old)
    with sqlite3.connect(old) as conn:
        conn.executescript("DROP TABLE po_lines; DROP TABLE purchase_orders;")
        conn.execute("UPDATE settings SET value = '3' WHERE key = 'schema_version'")
    current = Store(tmp_path / "cur.db")
    current.restore_from(old)
    assert current.has_purchase_orders() is False


def test_watch_skips_files_it_cannot_read(tmp_path, monkeypatch):
    store = Store(tmp_path / "a.db")
    inbox = tmp_path / "in"
    inbox.mkdir()
    (inbox / "good.pdf").write_bytes(b"%PDF-1.4 good")
    (inbox / "locked.pdf").write_bytes(b"%PDF-1.4 locked")
    real = store.find_by_hash

    def find(path, include_failed=False):
        if path.name == "locked.pdf":
            raise PermissionError("in use")
        return real(path, include_failed)

    monkeypatch.setattr(store, "find_by_hash", find)
    assert [p.name for p in new_files(inbox, store, now=9e12)] == ["good.pdf"]


def test_split_refuses_blank_percentages():
    lines = pd.DataFrame(_gt()["line_items"])
    with pytest.raises(ValueError):
        split_line(lines, 1, [("6000", "", 50), ("6100", "", math.nan)])


def test_controls_list_big_invoices_approved_by_one_person(tmp_path):
    store = Store(tmp_path / "a.db")
    invoice_id = store.add_invoice(tmp_path / "a.pdf", _gt(), {})
    store.approve_invoice(invoice_id, _gt(), "Jane")  # before any limit
    store.set_setting("approval_limit", "1000")
    today = dt.date.today()
    r = controls.build(store, today, today)
    assert [i["id"] for i in r["one_person"]] == [invoice_id]
    assert "approved by one person (1)" in controls.report_html(r)


# --- From the browser review ------------------------------------------------------------------------------


def test_second_approval_needs_another_computer_login(tmp_path):
    store = Store(tmp_path / "a.db")
    store.set_setting("approval_limit", "1000")
    invoice_id = store.add_invoice(tmp_path / "a.pdf", _gt(), {})
    store.approve_invoice(invoice_id, _gt(), "Alex", login="amartin")
    with pytest.raises(PermissionError):
        store.final_approve(invoice_id, "Jordan Lee", login="AMartin")  # renamed, same Windows account
    store.final_approve(invoice_id, "Jordan Lee", login="jlee")
    assert store.get_invoice(invoice_id)["status"] == "approved"


def test_statement_with_debit_and_credit_columns():
    invoices = [{"id": 1, "invoice_number": "CN-1", "grand_total": -2316.5, "invoice_date": "2026-09-28",
                 "status": "review"},
                {"id": 2, "invoice_number": "NW-1", "grand_total": 18017.85, "invoice_date": "2026-09-14",
                 "status": "approved"}]  # fmt: skip
    rows = [
        {"Document": "NW-1", "Debit": "18,017.85", "Credit": "", "Balance": "18,017.85"},
        {"Document": "CN-1", "Debit": "", "Credit": "2,316.50", "Balance": "15,701.35"},
    ]
    cols = stm.map_columns(list(rows[0]))
    assert cols["amount"] == "Debit" and cols["credit"] == "Credit"
    assert [li.status for li in stm.reconcile(rows, cols, invoices).lines] == [stm.MATCHED, stm.MATCHED]


def test_month_end_totals_keep_currencies_apart(tmp_path):
    store = Store(tmp_path / "a.db")
    from ap_coder.demo import load_demo

    load_demo(store)
    by_currency = accruals.summary(accruals.build(store, dt.date(2026, 10, 31)))[accruals.NOT_IN_ERP][1]
    assert set(by_currency) == {"CAD", "USD"} and by_currency["USD"] == 7570.01


def test_export_due_date_is_worked_out_when_not_printed(tmp_path):
    from ap_coder import exports

    store = Store(tmp_path / "a.db")
    doc = _gt(payment_terms="Net 30", due_date="")
    invoice_id = store.add_invoice(tmp_path / "a.pdf", doc, {})
    store.approve_invoice(invoice_id, doc, "Jane")
    rows = exports.invoice_rows([store.get_invoice(invoice_id)])
    assert rows[0]["due_date"] == "2026-10-14"


def test_due_date_message_names_the_due_date():
    with pytest.raises(ValueError, match="due_date must be YYYY-MM-DD"):
        InvoiceCoding.model_validate(_gt(due_date="31/12/2026"))
