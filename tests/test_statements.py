"""Vendor statement reconciliation."""

import csv

from ap_coder import statements as stm
from ap_coder.demo import load_demo
from ap_coder.memory import vendor_key
from ap_coder.store import Store

from .conftest import DATA


def _inv(i, number, total, date="2026-09-10", status="approved"):
    return {"id": i, "invoice_number": number, "grand_total": total, "invoice_date": date, "status": status}


def test_columns_are_recognised():
    cols = stm.map_columns(["Doc Date", "Invoice No.", "Amount", "Open Balance", "Type"])
    assert cols == {"number": "Invoice No.", "date": "Doc Date", "amount": "Amount", "balance": "Open Balance",
                    "type": "Type"}  # fmt: skip


def test_reconcile_finds_matches_differences_and_gaps():
    statement = [
        {"No": "INV-00101", "Date": "2026-09-01", "Amt": "1,000.00"},
        {"No": "102", "Date": "2026-09-05", "Amt": "250.00"},
        {"No": "103", "Date": "2026-09-20", "Amt": "75.00"},
        {"No": "", "Date": "2026-09-25", "Amt": "(500.00)", "Kind": "Payment"},
        {"No": "", "Date": "", "Amt": ""},  # blank row
    ]
    invoices = [
        _inv(1, "101", 1000.0),
        _inv(2, "INV-102", 245.0),
        _inv(3, "104", 60.0, "2026-09-12"),
        _inv(4, "105", 80.0, "2026-12-01"),  # outside the statement period
        _inv(5, "103", 75.0, status="rejected"),  # a rejected invoice does not count
    ]
    cols = {"number": "No", "date": "Date", "amount": "Amt", "type": "Kind"}
    rec = stm.reconcile(statement, cols, invoices)
    got = {(li.status, li.number) for li in rec.lines}
    assert got == {
        (stm.MATCHED, "INV-00101"), (stm.DIFFERS, "102"), (stm.NOT_RECEIVED, "103"), (stm.PAYMENT, ""),
        (stm.NOT_ON_STATEMENT, "104"),
    }  # fmt: skip
    differs = next(li for li in rec.lines if li.status == stm.DIFFERS)
    assert differs.difference == 5.0 and differs.invoice_id == 2
    assert rec.lines[0].status == stm.DIFFERS  # problems first
    assert rec.statement_total == 825.0
    assert "Amount differs" in stm.to_csv(rec).decode("utf-8-sig")


def test_negative_amount_formats():
    assert stm._number("(1,234.50)") == -1234.5
    assert stm._number("1234.50-") == -1234.5
    assert stm._number("99.00 CR") == -99.0
    assert stm._number("$1,000") == 1000.0
    assert stm._number("n/a") is None and stm._number(float("nan")) is None


def test_the_same_invoice_is_not_matched_twice():
    statement = [{"n": "7", "a": "10"}, {"n": "7", "a": "10"}]
    rec = stm.reconcile(statement, {"number": "n", "amount": "a"}, [_inv(1, "7", 10.0)])
    assert [li.status for li in rec.lines] == [stm.NOT_RECEIVED, stm.MATCHED]


def test_sample_statement_against_the_demo(tmp_path):
    store = Store(tmp_path / "a.db")
    load_demo(store)
    with (DATA / "sample_statement_northwind.csv").open(encoding="utf-8") as fh:
        records = list(csv.DictReader(fh))
    invoices = store.vendor_invoices(vendor_key("Northwind IT Solutions Inc."))
    rec = stm.reconcile(records, stm.map_columns(list(records[0])), invoices)
    assert {li.number: li.status for li in rec.lines if li.number} == {
        "NW-2026-0912": stm.MATCHED, "CN-2026-0047": stm.MATCHED, "NW-2026-0938": stm.NOT_RECEIVED,
    }  # fmt: skip
    assert rec.count(stm.PAYMENT) == 1
