"""Month-end accruals: costs incurred by the period end that are not yet in the ERP.

Three sources, none counted twice:

* Received, not invoiced: purchase order lines received (quantity received known) beyond what has been
  invoiced so far, at the PO price, on the PO line's GL account
* Invoices not yet in the ERP: invoices dated on or before the period end that are still in review,
  waiting for a second approval, or approved but not exported, by GL account (net of recoverable tax)
* Expected recurring invoices: regular vendors whose next invoice was due by the period end and has not
  arrived, at their usual amount, on the GL account most used for them (an estimate)
"""

from __future__ import annotations

import csv
import datetime as dt
import io
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from . import recurring
from .po import CLOSED, billed_by_line, po_label
from .safe import csv_row
from .store import APPROVED, PENDING, REVIEW, Store

RECEIVED, NOT_IN_ERP, RECURRING = "Received, not invoiced", "Invoice not in the ERP yet", "Expected recurring invoice"
SOURCES = (RECEIVED, NOT_IN_ERP, RECURRING)


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


def _not_in_erp(store: Store, period_end: dt.date) -> list[Accrual]:
    end = period_end.isoformat()
    rows = store.invoice_columns(("id", "status", "ai_output", "final_output"))
    exported = {r["id"] for r in store.list_invoices(APPROVED)} - {r["id"] for r in store.unexported_approved()}
    out = []
    for r in rows:
        if r["status"] not in (REVIEW, PENDING, APPROVED) or r["id"] in exported:
            continue
        doc = r["final_output"] or r["ai_output"] or {}
        if not doc or str(doc.get("invoice_date") or "") > end:
            continue
        by_gl: dict[tuple[str, str], float] = defaultdict(float)
        for e in doc.get("gl_distribution") or []:
            if e.get("kind") == "expense":
                by_gl[(e.get("gl_code") or "", e.get("cost_center") or "")] += float(e.get("amount") or 0)
        state = {REVIEW: "in review", PENDING: "waiting for a second approval", APPROVED: "approved, not exported"}
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
                    state[r["status"]],
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


def summary(accruals: list[Accrual]) -> dict[str, Any]:
    return {s: (sum(1 for a in accruals if a.source == s), round(sum(a.amount for a in accruals if a.source == s), 2))
            for s in SOURCES}  # fmt: skip
