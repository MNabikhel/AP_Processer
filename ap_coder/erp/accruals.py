"""Month-end accruals: costs incurred by the period end that are not yet in the ERP.

Three sources, none counted twice:

* Received, not invoiced: purchase order lines received (quantity received known) beyond what has been
  invoiced so far, at the PO price, on the PO line's GL account
* Invoices not yet in the ERP: invoices dated on or before the period end that are still in review,
  waiting for a second approval, or approved but not exported, by GL account (net of recoverable tax); a copy
  flagged as a duplicate of a bill already exported or counted, or as already in the ERP, is left out. One no
  person has approved yet is "proposed", on the accounts it was given (a guessed account counts as none, B-466);
  one dated more than a year before the period end is accrued and flagged to check (a date read wrong, or a bill
  long overdue: AP decides), one with no date is not accrued but listed (``undated``)
* Expected recurring invoices: regular vendors whose next invoice was due by the period end and has not
  arrived, at their usual amount, on the GL account most used for them (an estimate)
"""

from __future__ import annotations

import csv
import datetime as dt
import io
from collections import defaultdict
from dataclasses import dataclass

from ..coding.po import CLOSED, billed_by_line, po_label
from ..coding.vendors import norm_invoice_number
from ..core.memory import vendor_key
from ..core.safe import csv_row
from ..core.store import APPROVED, PARKED, PENDING, REVIEW, Store
from . import recurring
from .exports import distribution

RECEIVED, NOT_IN_ERP, RECURRING = "Received, not invoiced", "Invoice not in the ERP yet", "Expected recurring invoice"
SOURCES = (RECEIVED, NOT_IN_ERP, RECURRING)
OLD_DAYS = 365  # an invoice dated more than this before the period end is flagged: a misread date, or long overdue
OLD_NOTE = "dated over a year ago: check"
# Not approved by anyone yet: its coding is what AP Coder proposed, not what a person confirmed.
PROPOSED = "proposed: not approved yet"


@dataclass
class Accrual:
    source: str
    vendor: str
    reference: str
    description: str
    gl_code: str
    cost_center: str
    amount: float
    currency: str = "CAD"
    note: str = ""
    old: bool = False  # dated more than OLD_DAYS before the period end: accrued, flagged for AP to check


def _received_not_invoiced(store: Store, period_end: dt.date) -> list[Accrual]:
    out = []
    for po in store.purchase_orders():
        if po["status"] == CLOSED or not po["received_lines"]:
            continue
        full = store.purchase_order(po["po_key"])
        if full is None:
            continue
        # Returns are assumed to be reflected in the ERP's received quantity, so credits do not count here;
        # the vendor's invoices that quote no PO may still bill these lines ("PO-90155" in a description).
        # Invoices dated after the period end do not count: the goods were received, not yet invoiced, by then.
        end = period_end.isoformat()
        invoices = [
            inv
            for inv in store.po_invoices(po["po_key"]) + store.vendor_invoices_without_po(full["vendor_key"])
            if str(inv.get("invoice_date") or inv["coding"].get("invoice_date") or "") <= end
        ]
        billed = billed_by_line(full, invoices, positive_only=True)
        for li in full["lines"]:
            if li["received_quantity"] is None or li.get("amount_only"):
                continue
            qty = li["received_quantity"] - billed.get(li["line_number"], 0.0)
            if qty <= 1e-6:
                continue
            out.append(
                Accrual(
                    RECEIVED,
                    full["vendor_name"],
                    f"{po_label(full['po_number'])} line {li['line_number']}",
                    li["description"],
                    li["gl_code"],
                    li["cost_center"],
                    round(qty * li["unit_price"], 2),
                    note=f"{qty:g} received and not invoiced at {li['unit_price']:,.2f}",
                )  # fmt: skip
            )
    return out


def _bill(doc: dict) -> tuple[str, str, bool]:
    """(vendor, invoice number, is a credit): what makes two invoices the same bill."""
    return (vendor_key(doc.get("vendor_name") or ""), norm_invoice_number(doc.get("invoice_number")),
            float(doc.get("grand_total") or 0) < 0)  # fmt: skip


def _flags(validation: object) -> set[str]:
    issues = validation.get("issues") if isinstance(validation, dict) else None
    return {str(i.get("code")) for i in issues or [] if isinstance(i, dict)}


def _unguessed(doc: dict, ai: dict) -> dict:
    """``doc`` with every guessed account taken out: an account the AI only guessed (``offline_coder.guessed``, the
    line as read in ``ai``) and still the one in ``doc``. An invoice no person has approved accrues only on accounts
    it was really given, never on a guess (B-466); an account the reviewer picked over a guess is theirs."""
    from ..ai.offline_coder import guessed
    from ..core.memory import pair_lines
    from ..core.reference_data import UNASSIGNED

    lines = doc.get("line_items") or []
    guesses = {id(after) for before, after in pair_lines((ai or {}).get("line_items") or [], lines)
               if before and after and guessed(before.get("reasoning_justification"))
               and after.get("predicted_gl_code") == before.get("predicted_gl_code")}  # fmt: skip
    if not guesses:
        return doc
    lines = [{**li, "predicted_gl_code": UNASSIGNED} if id(li) in guesses else li for li in lines]
    return {k: v for k, v in doc.items() if k != "gl_distribution"} | {"line_items": lines}


def undated(store: Store, period_end: dt.date) -> list[Accrual]:
    """Invoices not in the ERP yet with no invoice date: not accrued until they have one, listed (B-474)."""
    out: list[Accrual] = []
    _not_in_erp(store, period_end, out)
    return out


def _not_in_erp(store: Store, period_end: dt.date, no_date: list[Accrual] | None = None) -> list[Accrual]:
    end = period_end.isoformat()
    oldest = (period_end - dt.timedelta(days=OLD_DAYS)).isoformat()
    rows = store.invoice_columns(("id", "status", "ai_output", "final_output", "validation"))
    exported = {r["id"] for r in store.list_invoices(APPROVED)} - {r["id"] for r in store.unexported_approved()}
    # A copy flagged as a duplicate of a bill already in the ERP (exported) or already accrued is not accrued
    # again; one flagged as already in the ERP's invoice register is in the ERP.
    counted = {_bill(r["final_output"] or {}) for r in rows if r["id"] in exported}
    treatments = store.tax_treatments()
    out = []
    for r in rows:
        if r["status"] not in (REVIEW, PARKED, PENDING, APPROVED) or r["id"] in exported:
            continue
        doc = r["final_output"] or r["ai_output"] or {}
        date = str(doc.get("invoice_date") or "").strip() if doc else ""
        if doc and not date:  # no date: when it was incurred is not known (B-474)
            if no_date is not None:
                no_date.append(Accrual(NOT_IN_ERP, doc.get("vendor_name") or "",
                                       f"Invoice {doc.get('invoice_number') or ''} (#{r['id']})", "no invoice date",
                                       "", "", round(float(doc.get("grand_total") or 0), 2), doc.get("currency") or "",
                                       "no invoice date: can't be accrued until it has one"))  # fmt: skip
            continue
        if not doc or date > end:
            continue
        flags, bill = _flags(r["validation"]), _bill(doc)
        if "DUPLICATE_IN_ERP" in flags or ("DUPLICATE_INVOICE" in flags and bill in counted):
            continue
        counted.add(bill)
        proposed = r["status"] in (REVIEW, PARKED)  # no person has approved it: its coding is a proposal
        if proposed:
            doc = _unguessed(doc, r["ai_output"] or {})
        old = date < oldest  # accrued, flagged: AP decides (B-474)
        by_gl: dict[tuple[str, str], float] = defaultdict(float)
        for e in distribution(doc, treatments):  # an invoice coded by an older version: built again
            if e.get("kind") == "expense" or e.get("non_recoverable_tax"):  # a cost; recoverable tax is not
                by_gl[(e.get("gl_code") or "", e.get("cost_center") or "")] += float(e.get("amount") or 0)
        state = {REVIEW: f"in review, {PROPOSED}", PARKED: f"parked, {PROPOSED}",
                 PENDING: "waiting for a second approval", APPROVED: "approved, not exported"}  # fmt: skip
        for (gl, cc), amount in by_gl.items():
            out.append(
                Accrual(
                    NOT_IN_ERP,
                    doc.get("vendor_name") or "",
                    f"Invoice {doc.get('invoice_number') or ''} (#{r['id']})",
                    f"invoice dated {doc.get('invoice_date')}",
                    gl,
                    cc,
                    round(amount, 2),
                    doc.get("currency") or "",
                    state[r["status"]] + (f"; {OLD_NOTE}" if old else ""),
                    old,
                )  # fmt: skip
            )
    return out


def _expected_recurring(store: Store, period_end: dt.date) -> list[Accrual]:
    out = []
    for r in recurring.detect(store.vendor_invoice_dates(), today=period_end):
        if r.next_date > period_end:
            continue
        usage = store.vendor_gl_usage(r.vendor_key)
        out.append(
            Accrual(
                RECURRING,
                r.vendor_name,
                f"expected {r.next_date.isoformat()}",
                f"{r.cadence.lower()} invoice",
                usage[0]["gl_code"] if usage else "",
                "",
                round(r.typical_total, 2),
                r.currency,
                "estimate: the usual amount, including tax",
            )  # fmt: skip
        )
    return out


def build(store: Store, period_end: dt.date) -> list[Accrual]:
    return (
        _received_not_invoiced(store, period_end)
        + _not_in_erp(store, period_end)
        + _expected_recurring(store, period_end)
    )


def by_gl(accruals: list[Accrual]) -> list[tuple[str, str, float]]:
    """(GL account, currency, total) largest first."""
    totals: dict[tuple[str, str], float] = defaultdict(float)
    for a in accruals:
        totals[(a.gl_code or "(no GL)", a.currency)] += a.amount
    return sorted(((gl, cur, round(t, 2)) for (gl, cur), t in totals.items()), key=lambda x: -x[2])


def by_gl_columns(accruals: list[Accrual]) -> tuple[list[str], list[tuple[str, dict[str, float]]]]:
    """(currencies, [(GL account, {currency: total})]): one row per account, a column per currency (CAD first),
    largest first by its biggest amount. Currencies are never added together."""
    totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for a in accruals:
        totals[a.gl_code or "(no GL)"][a.currency or "CAD"] += a.amount
    currencies = sorted({c for t in totals.values() for c in t}, key=lambda c: (c != "CAD", c))
    rows = [(gl, {c: round(v, 2) for c, v in t.items()}) for gl, t in totals.items()]
    return currencies, sorted(rows, key=lambda r: -max(r[1].values()))


def to_csv(accruals: list[Accrual], period_end: dt.date) -> bytes:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow([f"Accruals at {period_end.isoformat()}"])
    w.writerow(["Source", "Vendor", "Reference", "Description", "GL account", "Cost center", "Amount", "Currency",
                "Note"])  # fmt: skip
    for a in accruals:
        w.writerow(csv_row([a.source, a.vendor, a.reference, a.description, a.gl_code, a.cost_center,
                            f"{a.amount:.2f}", a.currency, a.note]))  # fmt: skip
    return ("﻿" + out.getvalue()).encode("utf-8")


def default_period_end(today: dt.date | None = None) -> dt.date:
    """The last day of the previous month in the first week of a month (closing it), else of this month."""
    today = today or dt.date.today()
    first = today.replace(day=1)
    if today.day <= 7:
        return first - dt.timedelta(days=1)
    nxt = (first + dt.timedelta(days=32)).replace(day=1)
    return nxt - dt.timedelta(days=1)


def summary(accruals: list[Accrual]) -> dict[str, tuple[int, dict[str, float]]]:
    """{source: (lines, {currency: total})}: currencies are never added together."""
    out: dict[str, tuple[int, dict[str, float]]] = {}
    for s in SOURCES:
        totals: dict[str, float] = defaultdict(float)
        for a in accruals:
            if a.source == s:
                totals[a.currency or "CAD"] += a.amount
        out[s] = (sum(1 for a in accruals if a.source == s), {c: round(t, 2) for c, t in totals.items()})
    return out
