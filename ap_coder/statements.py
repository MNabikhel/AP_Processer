"""Vendor statement reconciliation.

A vendor's statement of account (CSV or Excel: one row per invoice, credit or payment) is compared with
the invoices AP Coder holds for that vendor, by invoice number (compared the way people mean them:
"INV-00123" = "123"):

* matched: same number, same amount
* amount differs: same number, different amount
* not received: on the statement but not in AP Coder (ask the vendor for a copy)
* not on statement: in AP Coder for the statement period but not on the statement
* payment / credit without a number: shown for information
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re
from dataclasses import dataclass, field
from typing import Any

from .csvio import parse_date
from .safe import csv_row, parse_amount
from .vendors import norm_invoice_number

MATCHED, DIFFERS, NOT_RECEIVED, NOT_ON_STATEMENT, PAYMENT = (
    "matched", "amount_differs", "not_received", "not_on_statement", "payment",
)  # fmt: skip
LABELS = {
    MATCHED: "Matched",
    DIFFERS: "Amount differs",
    NOT_RECEIVED: "Not received",
    NOT_ON_STATEMENT: "Not on statement",
    PAYMENT: "Payment / credit",
}
TOLERANCE = 0.01
_PAYMENT_WORDS = re.compile(r"\b(payments?|paiements?|receipts?|cheques?|checks?|eft|wire|virement|remittance)\b")

COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "number": ("invoicenumber", "invoice", "invoiceno", "invno", "number", "document", "documentnumber", "docno",
               "reference", "ref", "facture", "numero"),
    "date": ("date", "invoicedate", "documentdate", "docdate", "datefacture"),
    "amount": ("amount", "total", "invoiceamount", "originalamount", "debit", "montant", "invoicetotal"),
    "credit": ("credit", "credits", "creditamount", "credit_amount", "crédit", "credit$"),
    "balance": ("balance", "openbalance", "balancedue", "openamount", "amountdue", "outstanding", "due", "solde"),
    "type": ("type", "doctype", "documenttype", "transactiontype", "description"),
}  # fmt: skip


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def map_columns(headers: list[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    normalised = {_norm(h): h for h in headers}
    for target, aliases in COLUMN_ALIASES.items():
        for alias in (target, *aliases):
            if alias in normalised and normalised[alias] not in found.values():
                found[target] = normalised[alias]
                break
    return found


def _number(value: Any) -> float | None:
    return parse_amount(value)


def date_part(value: Any) -> str:
    """The date of a date and time as exports write it: "1/5/2026 0:00", "2026-01-05T00:00:00" -> the part
    before the time (never cut at 10 characters, which splits "1/5/2026 0:00" in the middle of the time)."""
    return re.split(r"[\sT]", str(value or "").strip(), maxsplit=1)[0]


def read_date(value: Any, day_first: bool | None = None) -> str:
    """YYYY-MM-DD when the date can be read ("2026-09-15", "15/09/2026", "09/15/2026 0:00"), else the text as
    it is. ``day_first``: how the file writes its dates (``day_first_order``), for one that could be read either
    way ("09/10/2026")."""
    text = date_part(value)
    try:
        return parse_date(text).isoformat()
    except ValueError:
        pass
    if day_first is not None:
        try:
            fmt = "%d/%m/%Y" if day_first else "%m/%d/%Y"
            return dt.datetime.strptime(text.replace("-", "/"), fmt).date().isoformat()
        except ValueError:
            pass
    return text


def day_first_order(texts: list[str]) -> bool | None:
    """True when a file writes day/month/year, False for month/day/year, None when nothing tells."""
    orders = set()
    for text in texts:
        m = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-]\d{4}", date_part(text))
        if m and int(m.group(1)) > 12 >= int(m.group(2)):
            orders.add(True)
        elif m and int(m.group(2)) > 12 >= int(m.group(1)):
            orders.add(False)
    return orders.pop() if len(orders) == 1 else None


@dataclass
class Line:
    status: str
    number: str = ""
    date: str = ""
    statement_amount: float | None = None
    ap_amount: float | None = None
    invoice_id: int | None = None
    ap_status: str = ""
    note: str = ""

    @property
    def difference(self) -> float | None:
        if self.statement_amount is None or self.ap_amount is None:
            return None
        return round(self.statement_amount - self.ap_amount, 2)


@dataclass
class Reconciliation:
    lines: list[Line] = field(default_factory=list)

    def count(self, status: str) -> int:
        return sum(1 for li in self.lines if li.status == status)

    def total(self, status: str) -> float:
        return round(sum((li.statement_amount or li.ap_amount or 0) for li in self.lines if li.status == status), 2)

    @property
    def statement_total(self) -> float:
        return round(sum(li.statement_amount or 0 for li in self.lines if li.status != NOT_ON_STATEMENT), 2)


def reconcile(records: list[dict[str, Any]], columns: dict[str, str], invoices: list[dict[str, Any]]) -> Reconciliation:
    """``records``: statement rows; ``columns``: ``map_columns`` output (number and amount or balance needed);
    ``invoices``: ``Store.vendor_invoices`` rows (id, invoice_number, invoice_date, grand_total, status)."""

    def get(rec: dict[str, Any], key: str) -> Any:
        return rec.get(columns[key]) if key in columns else None

    by_number: dict[str, list[dict[str, Any]]] = {}
    for inv in invoices:
        if inv.get("status") in ("failed", "rejected"):
            continue
        by_number.setdefault(norm_invoice_number(inv.get("invoice_number")), []).append(inv)
    result = Reconciliation()
    seen: set[int] = set()
    dates = []
    day_first = day_first_order([str(get(rec, "date") or "") for rec in records])
    for rec in records:
        raw_number = str(get(rec, "number") or "").strip()
        if raw_number.lower() in ("nan", "none"):
            raw_number = ""
        amount = _number(get(rec, "amount"))
        if "credit" in columns:  # separate Debit / Credit columns: the amount is debit minus credit
            debit, credit = amount, _number(get(rec, "credit"))
            amount = None if debit is None and credit is None else (debit or 0.0) - abs(credit or 0.0)
        elif amount is None:  # only when there is no amount: an open-balance column, per document
            amount = _number(get(rec, "balance"))
        kind_text = str(get(rec, "type") or "").strip()
        kind = kind_text.lower()
        date = read_date(get(rec, "date"), day_first)
        if not raw_number and amount is None:
            continue  # blank or subtotal row
        if date:
            dates.append(date)
        key = norm_invoice_number(raw_number)
        if not key or _PAYMENT_WORDS.search(kind):
            result.lines.append(Line(PAYMENT, raw_number, date, amount, note=kind_text or "no invoice number"))
            continue
        candidates = [i for i in by_number.get(key, []) if i["id"] not in seen]
        if not candidates:
            result.lines.append(Line(NOT_RECEIVED, raw_number, date, amount))
            continue
        same = [i for i in candidates if amount is not None and abs((i["grand_total"] or 0) - amount) <= TOLERANCE]
        inv = (same or candidates)[0]
        seen.add(inv["id"])
        status = MATCHED if same else DIFFERS
        result.lines.append(Line(status, raw_number, date, amount, inv["grand_total"], inv["id"], inv["status"]))
    period = [d for d in dates if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d)]
    if period:
        first, last = min(period), max(period)
        for inv in invoices:
            date = str(inv.get("invoice_date") or "")
            if inv["id"] in seen or inv.get("status") in ("failed", "rejected") or not first <= date <= last:
                continue
            result.lines.append(
                Line(
                    NOT_ON_STATEMENT,
                    inv.get("invoice_number") or "",
                    date,
                    None,
                    inv["grand_total"],
                    inv["id"],
                    inv["status"],
                )  # fmt: skip
            )
    order = {DIFFERS: 0, NOT_RECEIVED: 1, NOT_ON_STATEMENT: 2, MATCHED: 3, PAYMENT: 4}
    result.lines.sort(key=lambda li: (order[li.status], li.date, li.number))
    return result


def to_csv(rec: Reconciliation) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Result", "Invoice #", "Date", "Statement amount", "AP Coder amount", "Difference",
                     "AP Coder #", "AP Coder status", "Note"])  # fmt: skip
    for li in rec.lines:
        writer.writerow(csv_row([
            LABELS[li.status], li.number, li.date,
            "" if li.statement_amount is None else f"{li.statement_amount:.2f}",
            "" if li.ap_amount is None else f"{li.ap_amount:.2f}",
            "" if li.difference is None else f"{li.difference:.2f}",
            li.invoice_id or "", li.ap_status, li.note,
        ]))  # fmt: skip
    return ("﻿" + out.getvalue()).encode("utf-8")
