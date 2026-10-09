"""Duplicate payment audit across approved invoices and the ERP register."""

import json

import pytest

from ap_coder import dupaudit
from ap_coder.store import Store

from .conftest import SAMPLES


@pytest.mark.parametrize(
    "a, b, typo",
    [
        ("10482", "10428", True),  # swapped neighbours
        ("10482", "10483", True),  # one changed
        ("inv5531", "inv5531a", True),  # one added
        ("10482", "10482", False),  # the same
        ("10482", "20483", False),  # two changes
        ("12", "13", False),  # too short to tell
        ("10482", "1048234", False),
    ],
)
def test_one_keystroke(a, b, typo):
    assert dupaudit.one_keystroke(a, b) is typo


def _approve(store, tmp_path, **changes):
    doc = {**json.loads((SAMPLES / "ground_truth" / "chinook_AB_GST_CCO-26-10418.json").read_text()), **changes}
    invoice_id = store.add_invoice(tmp_path / f"{len(store.list_invoices())}.pdf", doc, {})
    store.approve_invoice(invoice_id, doc, "Jane")
    return invoice_id, doc


def test_audit_finds_the_likely_duplicates(tmp_path):
    store = Store(tmp_path / "d.db")
    a, doc = _approve(store, tmp_path)  # CCO-26-10418
    b, _ = _approve(store, tmp_path, invoice_number="CCO-26-10481")  # a typo of the same bill
    c, _ = _approve(store, tmp_path, vendor_name="Chinook Courier Services", invoice_number="CCO-26-10418")
    d, _ = _approve(store, tmp_path, invoice_number="CCO-26-99999", invoice_date="2026-09-25")  # same amount, close
    _approve(store, tmp_path, invoice_number="CCO-26-55555", invoice_date="2026-06-01")  # same amount, months away
    _approve(store, tmp_path, invoice_number="CR-1", grand_total=-doc["grand_total"])  # a credit note: left out
    store.import_erp_register([
        {"vendor_name": doc["vendor_name"], "invoice_number": doc["invoice_number"], "invoice_date": "2026-09-30",
         "total": doc["grand_total"]},  # the same bill as #a, exported: not a duplicate
    ])  # fmt: skip
    found = {(p.reason, p.first.invoice_id, p.second.invoice_id) for p in dupaudit.find(store)}
    assert (dupaudit.NUMBER_TYPO, a, b) in found or (dupaudit.NUMBER_TYPO, b, a) in found
    assert any(r == dupaudit.OTHER_VENDOR and {x, y} == {a, c} for r, x, y in found)
    assert any(r == dupaudit.SAME_AMOUNT and d in (x, y) for r, x, y in found)
    assert not any(None in (x, y) and a in (x, y) and r != dupaudit.OTHER_VENDOR for r, x, y in found)
    assert not any("55555" in p.first.number + p.second.number for p in dupaudit.find(store))
    text = dupaudit.to_csv(dupaudit.find(store)).decode("utf-8-sig")
    assert "one keystroke" in text and "CCO-26-10481" in text


def test_twice_in_the_erp(tmp_path):
    store = Store(tmp_path / "d.db")
    rows = [
        {"vendor_name": "Acme Ltd", "invoice_number": "A-1001", "invoice_date": "2026-01-05", "total": 500.0},
        {"vendor_name": "ACME Limited", "invoice_number": "A-1010", "invoice_date": "2026-01-09", "total": 500.0},
    ]
    store.import_erp_register(rows)
    (pair,) = dupaudit.find(store)
    assert pair.first.source == pair.second.source == "ERP" and pair.amount == 500.0
    assert pair.reason == dupaudit.NUMBER_TYPO


def test_monthly_bills_are_not_typos(tmp_path):
    store = Store(tmp_path / "d.db")
    for month in range(1, 13):
        _approve(store, tmp_path, vendor_name="Maple Leasing", invoice_number=f"{1000 + month}",
                 invoice_date=f"2026-{month:02d}-01")  # fmt: skip
    assert dupaudit.find(store) == []


def test_an_exported_invoice_is_not_its_own_duplicate_in_the_erp(tmp_path):
    store = Store(tmp_path / "d.db")
    a, doc = _approve(store, tmp_path, vendor_name="Bell Mobility Inc.", invoice_number="INV-5531")
    row = {"vendor_name": "BELL CANADA", "invoice_number": "INV-5531", "invoice_date": "09/30/2026",
           "total": doc["grand_total"]}  # fmt: skip
    store.import_erp_register([row])
    assert [p.reason for p in dupaudit.find(store)] == [dupaudit.OTHER_VENDOR]  # not exported: it could be twice
    store.create_export_batch([a], "csv")
    assert dupaudit.find(store) == []  # exported: that ERP row is this invoice


def test_erp_dates_in_other_formats(tmp_path):
    from ap_coder import registers

    rows, _ = registers.rows_from_records(
        [{"Vendor": "Acme", "Invoice": "A-1", "Date": "25/09/2026", "Amount": "500.00"},
         {"Vendor": "Acme", "Invoice": "B-77", "Date": "2026-09-27 00:00:00", "Amount": "500.00"}],
        {"vendor_name": "Vendor", "invoice_number": "Invoice", "invoice_date": "Date", "total": "Amount"},
    )  # fmt: skip
    assert [r["invoice_date"] for r in rows] == ["2026-09-25", "2026-09-27"]
    store = Store(tmp_path / "d.db")
    store.import_erp_register(rows)
    (pair,) = dupaudit.find(store)
    assert pair.reason == dupaudit.SAME_AMOUNT


def test_erp_dates_with_a_time_and_single_digit_day_or_month(tmp_path):
    # Excel / JDE exports write "1/5/2026 0:00": the date must not be cut in the middle of the time.
    from ap_coder import registers

    rows, _ = registers.rows_from_records(
        [{"Vendor": "Acme", "Invoice": "A-1", "Date": "2026/1/5 0:00", "Amount": "500.00"},
         {"Vendor": "Acme", "Invoice": "B-77", "Date": "1/8/2026 0:00", "Amount": "500.00"},
         {"Vendor": "Acme", "Invoice": "C-9", "Date": "1/20/2026 0:00", "Amount": "75.00"}],  # month first
        {"vendor_name": "Vendor", "invoice_number": "Invoice", "invoice_date": "Date", "total": "Amount"},
    )  # fmt: skip
    assert [r["invoice_date"] for r in rows] == ["2026-01-05", "2026-01-08", "2026-01-20"]
    store = Store(tmp_path / "d.db")
    store.import_erp_register(rows)
    (pair,) = dupaudit.find(store)
    assert pair.reason == dupaudit.SAME_AMOUNT


def test_dates_already_stored_with_a_time_are_still_compared():
    assert dupaudit._days("2026/1/5 0:00", "2026-01-08") == 3
