"""Vendor statement reconciliation.

A vendor's statement of account (CSV or Excel: one row per invoice, credit or payment) is compared with
the invoices AP Coder holds for that vendor, by invoice number (compared the way people mean them:
"INV-00123" = "123"):

* matched: same number, same amount
* amount differs: same number, different amount
* not received: on the statement but not in AP Coder (ask the vendor for a copy)
* not on statement: in AP Coder for the statement period but not on the statement (on a statement of open items,
  the invoices exported are counted instead: they are paid or on their way, D-242)
* payment / credit: without a number, or with an invoice's number and the opposite sign: shown for information

Entry points: ``map_columns``, ``reconcile`` (a ``Reconciliation``) and ``to_csv``.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re
from dataclasses import dataclass, field
from typing import Any

from ..coding.vendors import norm_invoice_number
from ..core.csvio import parse_date
from ..core.headers import header_key, match_columns
from ..core.safe import csv_row, parse_amount

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
_PAYMENT_WORDS = re.compile(
    r"\b(payments?|paiements?|receipts?|cheques?|checks?|chqs?|pmts?|pymts?|eft|wire|virement|remittance)\b"
)

COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "number": ("invoicenumber", "invoice", "invoiceno", "invno", "number", "document", "documentnumber", "docno",
               "reference", "ref", "facture", "numero"),
    "date": ("date", "invoicedate", "documentdate", "docdate", "datefacture"),
    "amount": ("amount", "total", "invoiceamount", "originalamount", "debit", "montant", "invoicetotal"),
    "credit": ("credit", "credits", "creditamount", "credit_amount", "crédit", "credit$"),
    "balance": ("balance", "openbalance", "balancedue", "openamount", "amountdue", "outstanding", "due", "solde"),
    "type": ("type", "doctype", "documenttype", "transactiontype", "description"),
}  # fmt: skip
TYPE_HEADERS = {"type", "doctype", "documenttype", "transactiontype"}  # a column that says what each line is


def _norm(value: Any) -> str:
    return header_key(value)  # accents folded first: "Reçu", "Numéro" (B-657)


def map_columns(headers: list[str]) -> dict[str, str]:
    return match_columns(headers, COLUMN_ALIASES)


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


PERIOD_UNKNOWN = (
    "The statement's dates read either way (day or month first), so its period is unknown: the invoices "
    "it leaves out may not be listed as Not on statement."
)
DATES_UNREAD = (
    "The statement's dates could not be read (or no date column is picked), so its period is unknown: the "
    "invoices it leaves out are not listed as Not on statement."
)


@dataclass
class Reconciliation:
    lines: list[Line] = field(default_factory=list)
    # The statement's dates read either way (day or month first) and nothing told which: its period is unknown, so
    # the invoices it leaves out may not all be listed "Not on statement".
    period_unknown: bool = False
    # None of the statement's dates read as a date (15-Sep-2026, 15.09.2026, no date column): its period is unknown
    # too, so no invoice it leaves out is listed "Not on statement".
    dates_unread: bool = False
    # An open-items statement (``reconcile``'s ``open_items``): the invoices exported it leaves out, counted.
    exported_left_out: int = 0

    @property
    def exported_note(self) -> str:
        """``exported_left_out`` in a sentence, as the page and the CSV say it; "" when none."""
        n = self.exported_left_out
        if not n:
            return ""
        invoices, are = ("invoice", "is") if n == 1 else ("invoices", "are")
        return f"{n:,} {invoices} already exported {are} not expected on an open-items statement."

    def count(self, status: str) -> int:
        return sum(1 for li in self.lines if li.status == status)

    def total(self, status: str) -> float:
        return round(sum((li.statement_amount or li.ap_amount or 0) for li in self.lines if li.status == status), 2)


def reconcile(
    records: list[dict[str, Any]], columns: dict[str, str], invoices: list[dict[str, Any]], open_items: bool = False
) -> Reconciliation:
    """``records``: statement rows; ``columns``: ``map_columns`` output (number and amount or balance needed);
    ``invoices``: ``Store.vendor_invoices`` rows (id, invoice_number, invoice_date, grand_total, status,
    export_batch); ``open_items``: the statement lists only unpaid invoices, so the ones exported (an export batch:
    sent to the ERP or on a keying sheet) it leaves out are counted in ``exported_left_out``, not listed (D-242)."""

    def get(rec: dict[str, Any], key: str) -> Any:
        return rec.get(columns[key]) if key in columns else None

    by_number: dict[str, list[dict[str, Any]]] = {}
    for inv in sorted(invoices, key=_first_to_match):
        if inv.get("status") in ("failed", "rejected"):
            continue
        by_number.setdefault(norm_invoice_number(inv.get("invoice_number")), []).append(inv)
    result = Reconciliation()
    seen: set[int] = set()
    dates = []
    day_first = day_first_order([str(get(rec, "date") or "") for rec in records])
    period = _period([read_date(get(rec, "date"), day_first) for rec in records])
    # A description is free text ("14/2 NMD90 wire", "Brass check valves"): a payment word in it makes a line a
    # payment only when no invoice has its number, or the amount's sign says so (below). A Type column is believed.
    free_text = "type" in columns and _norm(columns["type"]) not in TYPE_HEADERS
    for rec in records:
        raw_number = str(get(rec, "number") or "").strip()
        if raw_number.lower() in ("nan", "none"):
            raw_number = ""
        amount = parse_amount(get(rec, "amount"))
        if "credit" in columns:  # separate Debit / Credit columns: the amount is debit minus credit
            debit, credit = amount, parse_amount(get(rec, "credit"))
            amount = None if debit is None and credit is None else (debit or 0.0) - abs(credit or 0.0)
        elif amount is None:  # only when there is no amount: an open-balance column, per document
            amount = parse_amount(get(rec, "balance"))
        kind_text = str(get(rec, "type") or "").strip()
        kind = kind_text.lower()
        date = read_date(get(rec, "date"), day_first)
        if not raw_number and amount is None:
            continue  # blank or subtotal row
        if date:
            dates.append(date)
        # A date not read is shown as written ("1 sept. 2026"), not cut at its first word (B-2640)
        shown = date if _ISO.fullmatch(date) or _EITHER_WAY.fullmatch(date) else str(get(rec, "date") or "").strip()
        key = norm_invoice_number(raw_number)
        same_number = by_number.get(key, [])

        def opposite(inv: dict[str, Any], amount: float | None = amount) -> bool:
            return amount is not None and amount * (inv["grand_total"] or 0) < 0

        # Statements often print the invoice's number on its payment or credit line: a sign opposite to every
        # document AP Coder holds with that number makes it the payment / credit applied, whatever its words
        # (a Credit column, "PMT", "CHQ"), before or after the invoice.
        reversed_ = bool(same_number) and all(opposite(i) for i in same_number)
        paid = reversed_ or bool(_PAYMENT_WORDS.search(kind))
        if paid and not reversed_ and free_text and same_number:
            # A payment word in a description, with the invoice's sign: an invoice ("Brass check valves" billed
            # again). With no amount, a payment only when every invoice with that number is matched already.
            paid = amount is None and all(i["id"] in seen for i in same_number)
        if not key or paid:
            result.lines.append(Line(PAYMENT, raw_number, shown, amount, note=kind_text or "no invoice number"))
            continue
        # Never a document of the opposite sign: a credit line does not take the invoice, nor an invoice the credit.
        candidates = [i for i in same_number if i["id"] not in seen and not opposite(i)]
        candidates.sort(key=lambda i, date=date: _of_the_line(i, date, period))  # (then ``_first_to_match``)
        if not candidates:
            result.lines.append(Line(NOT_RECEIVED, raw_number, shown, amount))
            continue
        # (a float a hair over the cent is still the cent: 100.01 - 100.00 is 0.010000000000005116, B-1371)
        same = [i for i in candidates
                if amount is not None and abs((i["grand_total"] or 0) - amount) <= TOLERANCE + 1e-9]  # fmt: skip
        inv = (same or candidates)[0]
        seen.add(inv["id"])
        status = MATCHED if same else DIFFERS
        result.lines.append(Line(status, raw_number, shown, amount, inv["grand_total"], inv["id"], inv["status"]))
    if day_first is None:  # every date read both ways: the matched invoices' own dates may tell the order
        day_first = _order_of_matches(result.lines, invoices)
        for li in result.lines:
            if _EITHER_WAY.fullmatch(li.date):
                li.date = read_date(li.date, day_first)
        dates = [read_date(d, day_first) for d in dates]
    result.period_unknown = any(_EITHER_WAY.fullmatch(d) for d in dates)
    result.dates_unread = bool(result.lines) and _period(dates) is None and not result.period_unknown
    left_out = _not_on_statement(_period(dates), invoices, seen)
    if open_items:
        exported = {inv["id"] for inv in invoices if inv.get("export_batch")}
        result.exported_left_out = sum(1 for li in left_out if li.invoice_id in exported)
        left_out = [li for li in left_out if li.invoice_id not in exported]
    result.lines += left_out
    order = {DIFFERS: 0, NOT_RECEIVED: 1, NOT_ON_STATEMENT: 2, MATCHED: 3, PAYMENT: 4}
    result.lines.sort(key=lambda li: (order[li.status], li.date, li.number))
    return result


def _first_to_match(inv: dict[str, Any]) -> tuple[bool, bool, int]:
    """Of the invoices with one number (a bill sent again), the one a statement line takes first: approved (or at its
    second approval) and exported, then the oldest (``Store.vendor_invoices`` is newest first)."""
    return inv.get("status") not in ("approved", "pending_approval"), not inv.get("export_batch"), inv["id"]


def _period(dates: list[str]) -> tuple[str, str] | None:
    """(the first, the last) of the statement's dates read (YYYY-MM-DD); None when none was."""
    read = [d for d in dates if _ISO.fullmatch(d)]
    return (min(read), max(read)) if read else None


def _of_the_line(inv: dict[str, Any], date: str, period: tuple[str, str] | None) -> tuple[bool, bool]:
    """How an invoice with a statement line's number fits the line: dated as the line is, then in the statement's
    period (#1001 of this year before #1001 of last year, exported or not)."""
    on = str(inv.get("invoice_date") or "")
    if _EITHER_WAY.fullmatch(date):  # not read yet ("05/10/2026"): either reading fits
        readings = {read_date(date, True), read_date(date, False)}
        return on not in readings, not (period and period[0] <= on <= period[1])
    return on != date, not (period and period[0] <= on <= period[1])


_EITHER_WAY = re.compile(r"\d{1,2}[/-]\d{1,2}[/-]\d{4}")  # a date not read: day or month first
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")  # a date read


def _order_of_matches(lines: list[Line], invoices: list[dict[str, Any]]) -> bool | None:
    """True (day first) or False (month first) when the statement dates of the matched lines read as their invoices'
    own dates one way only; None when they don't tell."""
    dates = {inv["id"]: str(inv.get("invoice_date") or "") for inv in invoices}
    orders = set()
    for li in lines:
        if li.invoice_id is None or not _EITHER_WAY.fullmatch(li.date):
            continue
        day, month = read_date(li.date, True), read_date(li.date, False)
        if day != month and dates.get(li.invoice_id) in (day, month):
            orders.add(dates[li.invoice_id] == day)
    return orders.pop() if len(orders) == 1 else None


def _not_on_statement(period: tuple[str, str] | None, invoices: list[dict[str, Any]], seen: set[int]) -> list[Line]:
    """The invoices AP Coder holds for the statement's ``period`` that no line took, but a copy of one a line took
    (the bill sent again: its number and its total). A credit note numbered as its invoice, or another bill under a
    number the vendor used again, is listed."""
    if period is None:
        return []
    first, last = period
    taken = {_copy_key(inv) for inv in invoices if inv["id"] in seen}
    lines = []
    for inv in invoices:
        date = str(inv.get("invoice_date") or "")
        if inv["id"] in seen or inv.get("status") in ("failed", "rejected") or not first <= date <= last:
            continue
        if _copy_key(inv) not in taken:
            lines.append(Line(NOT_ON_STATEMENT, inv.get("invoice_number") or "", date, None, inv["grand_total"],
                              inv["id"], inv["status"]))  # fmt: skip
    return lines


def _copy_key(inv: dict[str, Any]) -> tuple[str, float]:
    """What a bill sent again shares with the one first received: its number (as people mean it) and its total."""
    return norm_invoice_number(inv.get("invoice_number")), round(inv.get("grand_total") or 0, 2)


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
    if rec.exported_note:  # an open-items statement: the invoices exported are left out, and the file says so
        writer.writerow([rec.exported_note] + [""] * 8)
    if rec.period_unknown or rec.dates_unread:  # as the page warns: what is Not on statement is not all there
        writer.writerow([PERIOD_UNKNOWN if rec.period_unknown else DATES_UNREAD] + [""] * 8)
    return ("\ufeff" + out.getvalue()).encode("utf-8")
