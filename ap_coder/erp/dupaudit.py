"""Duplicate payment audit: bills that may have been approved (or posted in the ERP) twice.

Looks across the invoices approved in AP Coder (including those waiting for a second approval) and the
ERP's invoice register (Exports page), for pairs that are probably the same bill:

* EXACT: same vendor, same invoice number, same amount, approved twice (or in the ERP twice: also one register row
  the ERP lists more than once, its ``copies``)
* NUMBER_TYPO: same vendor and amount, invoice numbers one keystroke apart ("10482" / "10428",
  "INV-5531" / "INV-5531A")
* OTHER_VENDOR: same invoice number and amount under two vendor names (a duplicate vendor record)
* SAME_AMOUNT: same vendor and amount, different number, dated within a week of each other

An exported AP Coder invoice and its own entry in the ERP register (same vendor, number and amount) are the same
bill, not a duplicate. Credit notes are left out.

Entry points: ``find`` (the pairs, strongest reason first) and ``to_csv``.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import sqlite3
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import combinations
from typing import Any

from ..coding.vendors import norm_invoice_number
from ..core.csvio import parse_date
from ..core.memory import vendor_key
from ..core.safe import csv_row
from ..core.store import APPROVED, PENDING, Store
from .statements import date_part

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
    copies: int = 1  # an ERP bill the register lists more than once (entered twice in the ERP)

    @property
    def label(self) -> str:
        where = f"#{self.invoice_id}" if self.invoice_id else "ERP"
        again = f", entered {self.copies} times" if self.copies > 1 else ""
        return f"{self.number} ({where}{', ' + self.date if self.date else ''}{again})"


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


_FIELDS = ("vendor_name", "invoice_number", "invoice_date", "currency", "grand_total")


def _approved(store: Store) -> Iterator[tuple[int, Any, dict[str, Any]]]:
    """(id, export batch, the coding's fields the audit reads) of each invoice approved or waiting for a second
    approval, oldest first. With SQLite 3.38 or later, SQLite reads out those fields (their JSON as stored, so an
    amount reads back exactly as from the whole coding) instead of every coding being read and decoded (D-157)."""
    if sqlite3.sqlite_version_info < (3, 38):  # no ``->``
        for r in store.invoice_columns(("id", "status", "export_batch", "final_output")):
            if r["status"] in (APPROVED, PENDING) and r["final_output"]:
                yield r["id"], r["export_batch"], r["final_output"]
        return
    fields = ", ".join(f"final_output -> '$.{name}'" for name in _FIELDS)
    for r in store.summary_fetch(  # (any query, its rows read in one go)
        f"SELECT id, export_batch, json_array({fields}) AS doc FROM invoices WHERE status IN (?, ?) "
        "AND COALESCE(final_output, '') != '' ORDER BY id", (APPROVED, PENDING)
    ):  # fmt: skip
        yield r["id"], r["export_batch"], dict(zip(_FIELDS, json.loads(r["doc"]), strict=True))


def bills(store: Store) -> list[Bill]:
    out: list[Bill] = []
    keys: dict[str, str] = {}  # each vendor name's key worked out once (a vendor has many invoices)
    for invoice_id, batch, doc in _approved(store):
        total = float(doc.get("grand_total") or 0)
        if total <= 0:
            continue
        name = str(doc.get("vendor_name") or "")
        number = str(doc.get("invoice_number") or "")
        date, currency = str(doc.get("invoice_date") or ""), str(doc.get("currency") or "")
        key = keys[name] if name in keys else keys.setdefault(name, vendor_key(name))
        out.append(Bill("AP Coder", invoice_id, key, name, number, norm_invoice_number(number), date, round(total, 2),
                        currency, bool(batch)))  # fmt: skip
    for r in store.erp_register_rows("total > 0"):
        out.append(Bill("ERP", None, r["vendor_key"], r["vendor_name"], r["invoice_number"], r["number_key"],
                        r["invoice_date"] or "", round(r["total"], 2), copies=int(r.get("copies") or 1)))  # fmt: skip
    return out


def _same_bill(a: Bill, b: Bill) -> bool:
    """An exported AP Coder invoice and its own ERP entry: the same number, and the same vendor or the same amount
    (the ERP's own name for the vendor). An invoice not exported yet cannot be in the ERP already: an entry there
    was keyed in some other way, and paying both would pay the bill twice."""
    if {a.source, b.source} != {"AP Coder", "ERP"} or a.number_key != b.number_key:
        return False
    ours = a if a.source == "AP Coder" else b
    return ours.exported and (a.vendor_key == b.vendor_key or a.total == b.total)


def _number_pairs(group: list[Bill]) -> list[tuple[int, int]]:
    """The pairs (i, j), i < j, in order, of ``group``'s bills with the same invoice number or with numbers one
    keystroke apart. Two numbers one keystroke apart share one of the numbers left once a character is taken out
    (a character changed: the same one out of both; swapped: one of the two; added: the longer one less it), so
    only the numbers that share one are compared."""
    at: dict[str, list[int]] = defaultdict(list)
    for i, bill in enumerate(group):
        if bill.number_key:
            at[bill.number_key].append(i)
    found = {pair for places in at.values() for pair in combinations(places, 2)}
    sharing: dict[str, list[str]] = defaultdict(list)
    for key in at:
        if len(key) >= MIN_TYPO_LENGTH:
            for less in {key, *(key[:n] + key[n + 1 :] for n in range(len(key)))}:
                sharing[less].append(key)
    near = {tuple(sorted(keys)) for same in sharing.values() for keys in combinations(same, 2)}
    for a, b in near:
        if one_keystroke(a, b):
            found |= {(min(i, j), max(i, j)) for i in at[a] for j in at[b]}
    return sorted(found)


def _other_vendor_pairs(group: list[Bill]) -> list[tuple[int, int]]:
    """The pairs (i, j), i < j, in order, of ``group``'s bills under two vendors."""
    by_vendor: dict[str, list[int]] = defaultdict(list)
    for i, bill in enumerate(group):
        by_vendor[bill.vendor_key].append(i)
    return sorted((min(i, j), max(i, j)) for one, other in combinations(by_vendor.values(), 2) for i in one
                  for j in other)  # fmt: skip


def _close_pairs(group: list[Bill], dated: dict[str, dt.date | None], most: int) -> list[tuple[int, int]]:
    """The pairs (i, j), i < j, in order, of ``group``'s dated bills at most ``most`` days apart (by date, each
    with the ones just before it)."""
    known = sorted((day, i) for i, bill in enumerate(group) if (day := dated[bill.date]))
    found, start = [], 0
    for n, (day, i) in enumerate(known):
        while (day - known[start][0]).days > most:
            start += 1
        found += [(min(i, j), max(i, j)) for _, j in known[start:n]]
    return sorted(found)


def find(store: Store) -> list[Pair]:
    """Every pair the audit finds, strongest reason first: kept until an invoice or the ERP register changes (D-162),
    its page and its CSV sharing them. Read it, don't change it."""
    return store.summary_memo("duplicate_pairs", lambda: _find(store), changes="register")


def _find(store: Store) -> list[Pair]:
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
    dated = {text: _date(text) for text in {bill.date for bill in all_bills}}  # each date read once

    def days(a: Bill, b: Bill) -> int | None:
        first, second = dated[a.date], dated[b.date]
        return abs((first - second).days) if first and second else None

    # Each group's pairs that can be one, in the order ``combinations`` gives every pair (a pair keeps the first
    # reason found, and the list keeps that order among equals): a vendor's many bills of one amount are not
    # compared each with each (D-157).
    for bill in all_bills:
        if bill.copies > 1:  # the same bill entered more than once in the ERP: one register row, paired with itself
            add(EXACT, bill, bill)
    vendor_groups = [group for group in by_vendor_amount.values() if len(group) > 1]  # (one bill: no pair)
    for group in vendor_groups:
        for i, j in _number_pairs(group):
            a, b = group[i], group[j]
            if a.number_key == b.number_key:
                add(EXACT, a, b)
            elif (apart := days(a, b)) is None or apart <= TYPO_DAYS:  # monthly bills 1001, 1002... are not typos
                add(NUMBER_TYPO, a, b)
    for group in by_number_amount.values():
        for i, j in _other_vendor_pairs(group) if len(group) > 1 else ():
            add(OTHER_VENDOR, group[i], group[j])
    for group in vendor_groups:
        for i, j in _close_pairs(group, dated, SAME_AMOUNT_DAYS):
            if group[i].number_key != group[j].number_key:
                add(SAME_AMOUNT, group[i], group[j])
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
    return ("\ufeff" + out.getvalue()).encode("utf-8")
