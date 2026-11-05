"""Duplicate payment audit across approved invoices and the ERP register."""

import json

import pytest

from ap_coder.core.store import Store
from ap_coder.erp import dupaudit
from tests.conftest import SAMPLES


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
    store.create_export_batch([a], "csv")
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


def test_an_unexported_invoice_already_in_the_erp_is_a_duplicate(tmp_path):
    store = Store(tmp_path / "d.db")
    a, doc = _approve(store, tmp_path, invoice_number="NW-100")
    keyed_by_hand = {"vendor_name": doc["vendor_name"], "invoice_number": "NW-100", "total": doc["grand_total"]}
    store.import_erp_register([keyed_by_hand])
    (pair,) = dupaudit.find(store)
    assert pair.reason == dupaudit.EXACT and {pair.first.invoice_id, pair.second.invoice_id} == {a, None}
    store.create_export_batch([a], "csv")
    assert dupaudit.find(store) == []  # exported: that ERP row is this invoice


def test_erp_dates_in_other_formats(tmp_path):
    from ap_coder.erp import registers

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
    from ap_coder.erp import registers

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


def test_the_same_bill_entered_twice_in_the_erp_is_a_duplicate(tmp_path):
    """The ERP register keeps one row per vendor, number and amount: a bill keyed twice in the ERP must still show
    as a duplicate, not collapse into one row the audit can never pair."""
    store = Store(tmp_path / "d.db")
    row = {"vendor_name": "Acme Ltd", "invoice_number": "A-77", "invoice_date": "2026-01-05", "total": 812.5}
    once = {"vendor_name": "Acme Ltd", "invoice_number": "A-78", "invoice_date": "2026-03-05", "total": 99.0}
    store.import_erp_register([row, dict(row, vendor_name="ACME Limited", invoice_date="2026-02-05"), once])
    assert store.erp_register_repeats() == 1
    (pair,) = dupaudit.find(store)
    assert pair.reason == dupaudit.EXACT and pair.first.source == pair.second.source == "ERP"
    assert "entered 2 times" in pair.first.label and "A-77" in dupaudit.to_csv([pair]).decode("utf-8-sig")
    store.import_erp_register([row, once])  # the register imported again, overlapping: still twice, not three times
    assert store.erp_register_repeats() == 1 and "entered 2 times" in dupaudit.find(store)[0].first.label
    store.import_erp_register([row, once], replace_all=True)  # the ERP's copy was deleted there
    assert store.erp_register_repeats() == 0 and dupaudit.find(store) == []


def test_a_register_listing_a_bill_once_per_gl_line_is_not_a_bill_entered_three_times(tmp_path):
    """A register exported one row per GL line repeats the bill's vendor, number, total and date on each line (as do
    JD Edwards pay items): that is one bill, not a duplicate. Only a repeat with another date was entered again."""
    store = Store(tmp_path / "d.db")
    line = {"vendor_name": "Acme Ltd", "invoice_number": "A-77", "invoice_date": "2026-01-05", "total": 812.5}
    store.import_erp_register([line, dict(line), dict(line)])
    assert store.erp_register_repeats() == 0 and dupaudit.find(store) == []
    store.import_erp_register([line, dict(line), dict(line, invoice_date="2026-02-05")])  # keyed again a month later
    assert store.erp_register_repeats() == 1 and "entered 2 times" in dupaudit.find(store)[0].first.label


# --- The audit as it was (D-157): every pair of a group compared ------------------------------------------------------


def _old_find(all_bills):
    """``dupaudit.find`` as it was, on these bills: every pair of bills of a vendor and amount compared."""
    from collections import defaultdict
    from itertools import combinations

    from ap_coder.erp import dupaudit as d

    EXACT, NUMBER_TYPO, OTHER_VENDOR, SAME_AMOUNT = d.EXACT, d.NUMBER_TYPO, d.OTHER_VENDOR, d.SAME_AMOUNT
    SAME_AMOUNT_DAYS, TYPO_DAYS, Pair = d.SAME_AMOUNT_DAYS, d.TYPO_DAYS, d.Pair
    _days, _same_bill, one_keystroke = d._days, d._same_bill, d.one_keystroke
    pairs = {}

    def add(reason, a, b):
        if _same_bill(a, b):
            return
        first, second = sorted((a, b), key=lambda x: (x.date, x.source, x.invoice_id or 0, x.number))
        if (first, second) not in pairs:
            pairs[(first, second)] = Pair(reason, first, second)

    by_vendor_amount, by_number_amount = defaultdict(list), defaultdict(list)
    for bill in all_bills:
        if bill.vendor_key:
            by_vendor_amount[(bill.vendor_key, bill.total)].append(bill)
        if bill.number_key:
            by_number_amount[(bill.number_key, bill.total)].append(bill)
    for bill in all_bills:
        if bill.copies > 1:
            add(EXACT, bill, bill)
    for group in by_vendor_amount.values():
        for a, b in combinations(group, 2):
            if a.number_key and a.number_key == b.number_key:
                add(EXACT, a, b)
            elif one_keystroke(a.number_key, b.number_key):
                days = _days(a.date, b.date)
                if days is None or days <= TYPO_DAYS:
                    add(NUMBER_TYPO, a, b)
    for group in by_number_amount.values():
        for a, b in combinations(group, 2):
            if a.vendor_key != b.vendor_key:
                add(OTHER_VENDOR, a, b)
    for group in by_vendor_amount.values():
        for a, b in combinations(group, 2):
            days = _days(a.date, b.date)
            if days is not None and days <= SAME_AMOUNT_DAYS and a.number_key != b.number_key:
                add(SAME_AMOUNT, a, b)
    order = {EXACT: 0, OTHER_VENDOR: 1, NUMBER_TYPO: 2, SAME_AMOUNT: 3}
    return sorted(pairs.values(), key=lambda p: (order[p.reason], -p.amount))


def _made_up_bills(seed: int) -> list:
    """Bills of a few vendors and amounts with numbers alike (in a row, one keystroke apart, the same, short, none),
    dates close together, far apart, missing or unreadable, from AP Coder (some exported) and the ERP (some twice)."""
    import random

    from ap_coder.coding.vendors import norm_invoice_number

    rng = random.Random(seed)
    numbers = [f"{10000 + n}" for n in range(40)] + ["10482", "10428", "1048", "inv5531", "inv5531a", "12", "13", ""]
    numbers += [n[:2] + n[3] + n[2] + n[4:] for n in numbers[:10]] + ["A-1001", "A-1010", "A1001", "#10003"]
    dates = [f"2026-0{m}-{d:02d}" for m in (1, 2) for d in range(1, 29, 3)]
    dates += ["", "2026/1/5 0:00", "31/31/2026", "05/06/2026", "2026-02-30"]
    out = []
    for n in range(420):
        vendor = rng.choice(["acme", "acme supplies", "maple leasing", "bell", ""])
        number = rng.choice(numbers)
        erp = rng.random() < 0.4
        out.append(dupaudit.Bill("ERP" if erp else "AP Coder", None if erp else n + 1, vendor, vendor.title(), number,
                                 norm_invoice_number(number), rng.choice(dates), rng.choice([100.0, 250.5, 99.99]),
                                 exported=not erp and rng.random() < 0.3,
                                 copies=rng.choice([1, 1, 1, 2, 3]) if erp else 1))  # fmt: skip
    return out


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_the_audit_finds_what_comparing_every_pair_found(monkeypatch, seed):
    """Only the pairs of a group that can be one are looked at (D-157): the same pairs, reasons and order."""
    made_up = _made_up_bills(seed)
    monkeypatch.setattr(dupaudit, "bills", lambda store: made_up)
    found = dupaudit._find(None)
    assert found == _old_find(made_up) and len(found) > 100
    assert {p.reason for p in found} == set(dupaudit.REASONS)


def test_the_bills_read_out_by_sqlite_are_the_codings_own(tmp_path, monkeypatch):
    """The fields SQLite reads out of each approved coding (``->``) are what reading the whole coding gave: amounts
    to the last digit, numbers written as numbers, missing or empty fields; credit notes and the queue left out."""
    import sqlite3

    store = Store(tmp_path / "d.db")
    _approve(store, tmp_path)
    _approve(store, tmp_path, invoice_number=104182, grand_total=0.1 + 0.2, currency=None)
    _approve(store, tmp_path, invoice_number="X-1", grand_total="12.50", invoice_date=None)
    _approve(store, tmp_path, invoice_number="X-2", grand_total=1e-7, vendor_name="Énergir S.E.C.")
    _approve(store, tmp_path, invoice_number="X-3", grand_total=123456789.12345679)
    _approve(store, tmp_path, invoice_number="CR-1", grand_total=-5.0)
    store.add_invoice(tmp_path / "queue.pdf", {"vendor_name": "Queue", "invoice_number": "Q", "grand_total": 9.0}, {})
    store.import_erp_register([{"vendor_name": "Acme", "invoice_number": "E-1", "total": 7.25},
                               {"vendor_name": "Acme", "invoice_number": "E-2", "total": -7.25}])  # fmt: skip
    read_out = dupaudit.bills(store)
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 37, 2))  # an older SQLite: each coding read whole
    assert dupaudit.bills(store) == read_out and len(read_out) == 6
