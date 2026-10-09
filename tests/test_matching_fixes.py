"""Regression tests for defects in coding rules, PO matching, statements and labels."""

import pytest

from ap_coder import statements as stm
from ap_coder.labels import _number
from ap_coder.po import match_invoice
from ap_coder.rules import Rule
from ap_coder.schema import InvoiceCoding
from ap_coder.store import Store


@pytest.mark.parametrize(
    "contains, description, matches",
    [
        ("rent", "Current transformer, 200A", False),  # not inside another word
        ("fee", "Coffee service", False),
        ("oil", "Aluminium foil rolls", False),
        ("rent", "Office rent - October", True),
        ("monitor", "Monitors 27 in", True),  # a plural still matches
        ("freight", "Freight-in (Purolator)", True),
    ],
)
def test_rule_words_match_at_the_start_of_a_word(contains, description, matches):
    assert Rule("", contains, "6100").matches("Any vendor", description) is matches


def _line(n, description, qty, price):
    return {"line_number": n, "description": description, "quantity": qty, "unit_price": price,
            "amount": round(qty * price, 2), "predicted_gl_code": "6000", "predicted_cost_center": "",
            "taxes_applied": [], "reasoning_justification": ""}  # fmt: skip


def test_po_matching_with_repeated_line_numbers_pairs_each_line(tmp_path):
    # Multi-page invoices often restart their line numbers on page 2: two lines numbered 1.
    store = Store(tmp_path / "a.db")
    store.import_purchase_orders([
        {"po_number": "PO-7001", "vendor_name": "Acme Supply", "line_number": 1, "description": "Steel bracket",
         "quantity": 10, "unit_price": 12.0, "amount": 120.0},
        {"po_number": "PO-7001", "vendor_name": "Acme Supply", "line_number": 2, "description": "Hex bolt M8",
         "quantity": 100, "unit_price": 0.5, "amount": 50.0},
    ])  # fmt: skip
    lines = [_line(1, "Steel bracket", 10, 12.0), _line(1, "Hex bolt M8", 100, 0.5)]
    coding = InvoiceCoding.model_validate({
        "vendor_name": "Acme Supply", "invoice_number": "A-1", "invoice_date": "2026-09-01", "po_number": "PO-7001",
        "currency": "CAD", "subtotal": 170.0, "tax_total": 0.0, "grand_total": 170.0, "line_items": lines,
        "tax_lines": [], "confidence_score": 0.9,
    })  # fmt: skip
    result = match_invoice(coding, store)
    assert [m.po_line for m in result.lines] == [1, 2]
    assert {f[1] for f in result.findings} == {"PO_MATCHED"}


def test_statement_dates_written_day_first_or_month_first_still_find_missing_invoices():
    statement = [
        {"No": "101", "Date": "09/01/2026", "Amt": "100.00"},  # not ISO: as most accounting packages print it
        {"No": "103", "Date": "09/30/2026", "Amt": "75.00"},
    ]
    invoices = [
        {"id": 1, "invoice_number": "101", "grand_total": 100.0, "invoice_date": "2026-09-01", "status": "approved"},
        {"id": 2, "invoice_number": "102", "grand_total": 60.0, "invoice_date": "2026-09-15", "status": "approved"},
        {"id": 3, "invoice_number": "103", "grand_total": 75.0, "invoice_date": "2026-09-30", "status": "approved"},
    ]
    rec = stm.reconcile(statement, {"number": "No", "date": "Date", "amount": "Amt"}, invoices)
    assert {(li.status, li.number) for li in rec.lines} == {
        (stm.MATCHED, "101"), (stm.MATCHED, "103"), (stm.NOT_ON_STATEMENT, "102"),
    }  # fmt: skip
    assert {li.date for li in rec.lines} == {"2026-09-01", "2026-09-15", "2026-09-30"}


@pytest.mark.parametrize("text, value", [("(250.00)", -250.0), ("150,00", 150.0), ("1,234.50", 1234.5)])
def test_label_amounts_read_credits_and_decimal_commas(text, value):
    assert _number(text, "row 2 amount") == value


def test_label_amount_that_is_not_a_number_is_an_error():
    with pytest.raises(ValueError):
        _number("n/a", "row 2 amount")
