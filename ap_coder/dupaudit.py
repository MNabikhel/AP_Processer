"""Duplicate payment audit: bills that may have been approved (or posted in the ERP) twice.

Looks across the invoices approved in AP Coder (including those waiting for a second approval) and the
ERP's invoice register (Exports page), for pairs that are probably the same bill:

* EXACT: same vendor, same invoice number, same amount, approved twice (or in the ERP twice)
* NUMBER_TYPO: same vendor and amount, invoice numbers one keystroke apart ("10482" / "10428",
  "INV-5531" / "INV-5531A")
* OTHER_VENDOR: same invoice number and amount under two vendor names (a duplicate vendor record)
* SAME_AMOUNT: same vendor and amount, different number, dated within a week of each other

An AP Coder invoice and its own entry in the ERP register (same vendor, number and amount) are the same
bill, not a duplicate. Credit notes are left out.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations

from .csvio import parse_date
from .memory import vendor_key
from .safe import csv_row
from .statements import date_part
from .store import APPROVED, PENDING, Store
from .vendors import norm_invoice_number

EXACT, NUMBER_TYPO, OTHER_VENDOR, SAME_AMOUNT = "EXACT", "NUMBER_TYPO", "OTHER_VENDOR", "SAME_AMOUNT"
REASONS = {
    EXACT: "Same vendor, number and amount",
    NUMBER_TYPO: "Invoice numbers one keystroke apart",
    OTHER_VENDOR: "Same number and amount, another vendor name",
    SAME_AMOUNT: "Same amount within a week",
}
SAME_AMOUNT_DAYS = 7
TYPO_DAYS = 14  # a bill entered twice with a typo carries (about) the same date
MIN_TYPO_LENGTH = 4  # "12" and "13" are not a typo of each other


@dataclass(frozen=True)
class Bill:
    source: str  # "AP Coder" or "ERP"
    invoice_id: int | None
    vendor_key: str
    vendor_name: str
    number: str
    number_key: str
    date: str
    total: float
    currency: str = ""
    exported: bool = False  # an AP Coder invoice already sent to the ERP

    @property
    def label(self) -> str:
        where = f"#{self.invoice_id}" if self.invoice_id else "ERP"
        return f"{self.number} ({where}{', ' + self.date if self.date else ''})"


@dataclass(frozen=True)
class Pair:
    reason: str
    first: Bill
    second: Bill

    @property
    def amount(self) -> float:
        return self.first.total


def one_keystroke(a: str, b: str) -> bool:
    """One character added, removed, changed, or two neighbours swapped."""
    if a == b or min(len(a), len(b)) < MIN_TYPO_LENGTH or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        if len(diff) == 1:
            return True
        return len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    i = 0
    while i < len(short) and short[i] == long_[i]:
        i += 1
    return short[i:] == long_[i + 1 :]


def _date(text: str) -> dt.date | None:
    try:
        return parse_date(date_part(text))
    except ValueError:  # empty, or day and month cannot be told apart
        return None


def _days(a: str, b: str) -> int | None:
    first, second = _date(a), _date(b)
    return abs((first - second).days) if first and second else None


def bills(store: Store) -> list[Bill]:
    out = []
    for r in store.invoice_columns(("id", "status", "export_batch", "final_output")):
        if r["status"] not in (APPROVED, PENDING) or not r["final_output"]:
            continue
        doc = r["final_output"]
        total = float(doc.get("grand_total") or 0)
        if total <= 0:
            continue
        name = str(doc.get("vendor_name") or "")
        number = str(doc.get("invoice_number") or "")
        date, currency = str(doc.get("invoice_date") or ""), str(doc.get("currency") or "")
        out.append(Bill("AP Coder", r["id"], vendor_key(name), name, number, norm_invoice_number(number), date,
                        round(total, 2), currency, bool(r["export_batch"])))  # fmt: skip
    for r in store.erp_register():
        if (r["total"] or 0) <= 0:
            continue
        out.append(Bill("ERP", None, r["vendor_key"], r["vendor_name"], r["invoice_number"], r["number_key"],
                        r["invoice_date"] or "", round(r["total"], 2)))  # fmt: skip
    return out


def _same_bill(a: Bill, b: Bill) -> bool:
    """An AP Coder invoice and its own ERP entry: the same vendor and number, or an exported invoice whose number
    and amount are in the ERP under the ERP's own name for the vendor."""
    if {a.source, b.source} != {"AP Coder", "ERP"} or a.number_key != b.number_key:
        return False
    ours = a if a.source == "AP Coder" else b
    return a.vendor_key == b.vendor_key or (ours.exported and a.total == b.total)


def find(store: Store) -> list[Pair]:
    all_bills = bills(store)
    pairs: dict[tuple, Pair] = {}

    def add(reason: str, a: Bill, b: Bill) -> None:
        if _same_bill(a, b):
            return
        first, second = sorted((a, b), key=lambda x: (x.date, x.source, x.invoice_id or 0, x.number))
        ident = (first, second)
        if ident not in pairs:  # the first (strongest) reason found is kept
            pairs[ident] = Pair(reason, first, second)

    by_vendor_amount: dict[tuple[str, float], list[Bill]] = defaultdict(list)
    by_number_amount: dict[tuple[str, float], list[Bill]] = defaultdict(list)
    for bill in all_bills:
        if bill.vendor_key:
            by_vendor_amount[(bill.vendor_key, bill.total)].append(bill)
        if bill.number_key:
            by_number_amount[(bill.number_key, bill.total)].append(bill)

    for group in by_vendor_amount.values():
        for a, b in combinations(group, 2):
            if a.number_key and a.number_key == b.number_key:
                add(EXACT, a, b)
            elif one_keystroke(a.number_key, b.number_key):
                days = _days(a.date, b.date)
                if days is None or days <= TYPO_DAYS:  # monthly bills numbered 1001, 1002... are not typos
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


def to_csv(pairs: list[Pair]) -> bytes:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Why", "Amount", "Currency", "Vendor", "Invoice", "Date", "Where", "Other vendor", "Other invoice",
                "Other date", "Other where"])  # fmt: skip
    for p in pairs:
        a, b = p.first, p.second
        w.writerow(csv_row([
            REASONS[p.reason], f"{p.amount:.2f}", a.currency or b.currency, a.vendor_name, a.number, a.date,
            f"AP Coder #{a.invoice_id}" if a.invoice_id else "ERP", b.vendor_name, b.number, b.date,
            f"AP Coder #{b.invoice_id}" if b.invoice_id else "ERP",
        ]))  # fmt: skip
    return ("﻿" + out.getvalue()).encode("utf-8")
