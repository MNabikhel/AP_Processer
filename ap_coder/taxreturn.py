"""Sales-tax return support: the GST/HST and QST paid to vendors that can be claimed back for a period.

* Input tax credits (ITCs): GST and HST on approved invoices dated in the period, where the tax setup
  treats the tax as recoverable
* Input tax refunds (ITRs): the same for Quebec's QST
* Claims at risk: tax claimed on an invoice of $30 or more that shows no valid GST/HST (or QST)
  registration number (a documentary requirement for the claim), tax in a foreign currency (the return
  is in Canadian dollars), and approved invoices not exported to the ERP yet (the ERP's receivable will
  not match)

Credit notes reduce the claim. Invoices still in review or waiting for a second approval are counted
separately, not claimed.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from .safe import csv_row
from .store import APPROVED, PARKED, PENDING, REVIEW, Store
from .tax import (
    DEFAULT_TREATMENTS,
    PROVINCES,
    RECOVERABLE,
    REGIME,
    TaxRateTable,
    TaxTreatment,
    valid_gst_number,
    valid_qst_number,
)

ITC, ITR = "GST/HST input tax credits (ITCs)", "QST input tax refunds (ITRs)"
GROUPS = {"GST": ITC, "HST": ITC, "QST": ITR}
DOCUMENT_THRESHOLD = 30.0  # below this total, no registration number is required on the invoice

NO_GST_NUMBER = "No valid GST/HST number"
NO_QST_NUMBER = "No valid QST number"
FOREIGN = "Foreign currency: convert to CAD"
NOT_EXPORTED = "Not exported yet"
RISKS = {NO_GST_NUMBER, NO_QST_NUMBER, FOREIGN}


@dataclass
class Claim:
    invoice_id: int
    vendor: str
    invoice_number: str
    invoice_date: str
    currency: str
    grand_total: float
    tax_type: str
    province: str
    rate: float
    amount: float
    registration: str
    issues: list[str] = field(default_factory=list)

    @property
    def group(self) -> str:
        return GROUPS[self.tax_type]


@dataclass
class Report:
    start: dt.date
    end: dt.date
    claims: list[Claim]
    not_approved: int  # invoices dated in the period still in review, parked or waiting for a second approval
    not_recoverable: list[str]  # tax types the setup expenses instead of claiming

    def totals(self) -> dict[str, dict[str, float]]:
        """{ITC / ITR: {currency: total}}."""
        out: dict[str, dict[str, float]] = {ITC: {}, ITR: {}}
        for c in self.claims:
            out[c.group][c.currency] = round(out[c.group].get(c.currency, 0.0) + c.amount, 2)
        return out

    def by_type(self) -> list[tuple[str, str, float, str, float]]:
        """(tax type, province, rate, currency, total) for each rate claimed."""
        totals: dict[tuple[str, str, float, str], float] = defaultdict(float)
        for c in self.claims:
            totals[(c.tax_type, c.province, c.rate, c.currency)] += c.amount
        return [(t, p, r, cur, round(v, 2)) for (t, p, r, cur), v in sorted(totals.items())]

    def at_risk(self) -> list[Claim]:
        """Claims to check before filing (not exporting to the ERP yet is only a reconciliation note)."""
        return [c for c in self.claims if set(c.issues) & RISKS]


def _registration_issue(tax_type: str, doc: dict[str, Any]) -> tuple[str, str]:
    """(the number on the invoice, the issue when it is missing or invalid)."""
    if tax_type == "QST":
        number = str(doc.get("qst_registration_number") or "")
        return number, "" if valid_qst_number(number) else NO_QST_NUMBER
    number = str(doc.get("gst_hst_registration_number") or "")
    return number, "" if valid_gst_number(number) else NO_GST_NUMBER


def province(tax_type: str, tax_line: dict[str, Any], doc: dict[str, Any]) -> str:
    """The province a tax line belongs to: GST is federal (the same wherever the supply is), HST and QST
    from the line or else the place of supply."""
    if tax_type == "GST":
        return ""
    return str(tax_line.get("province") or doc.get("ship_to_province") or doc.get("supplier_province") or "")


def build(store: Store, start: dt.date, end: dt.date, treatments: dict[str, TaxTreatment] | None = None) -> Report:
    """The claimable tax on approved invoices dated from ``start`` to ``end`` (inclusive)."""
    treatments = treatments if treatments is not None else store.tax_treatments()

    def recoverable(tax_type: str) -> bool:
        t = treatments.get(tax_type)
        return (t.treatment if t else DEFAULT_TREATMENTS[tax_type]) == RECOVERABLE

    first, last = start.isoformat(), end.isoformat()
    claims, not_approved = [], 0
    for r in store.invoice_columns(("id", "status", "invoice_date", "export_batch", "ai_output", "final_output")):
        doc = r["final_output"] or r["ai_output"] or {}
        date = str(doc.get("invoice_date") or r["invoice_date"] or "")
        if not (first <= date <= last):
            continue
        if r["status"] in (REVIEW, PARKED, PENDING):
            not_approved += 1
            continue
        if r["status"] != APPROVED or not r["final_output"]:
            continue
        doc = r["final_output"]
        currency = str(doc.get("currency") or "CAD").upper()
        total = float(doc.get("grand_total") or 0)
        for tl in doc.get("tax_lines") or []:
            tax_type = str(tl.get("tax_type") or "").upper()
            amount = round(float(tl.get("tax_amount") or 0), 2)
            if tax_type not in GROUPS or abs(amount) < 0.005 or not recoverable(tax_type):
                continue
            number, issue = _registration_issue(tax_type, doc)
            issues = [issue] if issue and abs(total) >= DOCUMENT_THRESHOLD else []
            if currency != "CAD":
                issues.append(FOREIGN)
            if r["export_batch"] is None:
                issues.append(NOT_EXPORTED)
            claims.append(
                Claim(
                    r["id"],
                    str(doc.get("vendor_name") or ""),
                    str(doc.get("invoice_number") or ""),
                    date,
                    currency,
                    total,
                    tax_type,
                    province(tax_type, tl, doc),
                    float(tl.get("rate") or 0),
                    amount,
                    number,
                    issues,
                )  # fmt: skip
            )
    claims.sort(key=lambda c: (c.invoice_date, c.invoice_id))
    not_recoverable = [t for t in GROUPS if not recoverable(t)]
    return Report(start, end, claims, not_approved, not_recoverable)


@dataclass
class SelfAssessment:
    invoice_id: int
    vendor: str
    invoice_number: str
    invoice_date: str
    province: str
    tax_type: str
    base: float
    rate: float
    estimate: float
    currency: str


def self_assessment(
    store: Store, start: dt.date, end: dt.date, rates: TaxRateTable | None = None
) -> list[SelfAssessment]:
    """Approved invoices for a supply in a PST or QST province that charge no PST / QST: the buyer may have to
    self-assess it (an estimate at the official rate on the subtotal; exempt goods and services owe nothing)."""
    rates = rates or TaxRateTable.load()
    first, last = start.isoformat(), end.isoformat()
    out = []
    for r in store.invoice_columns(("id", "final_output"), APPROVED):
        doc = r["final_output"] or {}
        date = str(doc.get("invoice_date") or "")
        if not (first <= date <= last):
            continue
        province = next((p for p in (doc.get("ship_to_province"), doc.get("supplier_province")) if p in PROVINCES), "")
        charged = {str(tl.get("tax_type") or "").upper() for tl in doc.get("tax_lines") or []}
        base = float(doc.get("subtotal") or 0)
        for tax_type in sorted(REGIME.get(province, frozenset()) & {"PST", "QST"}):
            if tax_type in charged or abs(base) < 0.005:
                continue
            try:
                rate = rates.rate_for(tax_type, province, dt.date.fromisoformat(date))
            except ValueError:
                rate = None
            if not rate:
                continue
            out.append(
                SelfAssessment(
                    r["id"],
                    str(doc.get("vendor_name") or ""),
                    str(doc.get("invoice_number") or ""),
                    date,
                    province,
                    tax_type,
                    round(base, 2),
                    rate,
                    round(base * rate, 2),
                    str(doc.get("currency") or "CAD").upper(),
                )  # fmt: skip
            )
    return sorted(out, key=lambda s: (s.province, s.invoice_date, s.invoice_id))


def self_assessment_csv(items: list[SelfAssessment], start: dt.date, end: dt.date) -> bytes:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow([f"PST / QST possibly to self-assess, invoices dated {start.isoformat()} to {end.isoformat()}"])
    w.writerow(["Province", "Tax", "Rate", "Subtotal", "Estimate", "Currency", "Vendor", "Invoice", "Invoice date",
                "AP Coder #"])  # fmt: skip
    for s in items:
        w.writerow(csv_row([s.province, s.tax_type, f"{s.rate * 100:g}%", f"{s.base:.2f}", f"{s.estimate:.2f}",
                            s.currency, s.vendor, s.invoice_number, s.invoice_date, s.invoice_id]))  # fmt: skip
    return ("\ufeff" + out.getvalue()).encode("utf-8")


def to_csv(report: Report) -> bytes:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow([f"Sales tax to claim, invoices dated {report.start.isoformat()} to {report.end.isoformat()}"])
    w.writerow(["Claim", "Tax", "Province", "Rate", "Tax amount", "Currency", "Vendor", "Invoice", "Invoice date",
                "Invoice total", "Registration number", "AP Coder #", "To check"])  # fmt: skip
    for c in report.claims:
        w.writerow(
            csv_row(
                [
                    c.group,
                    c.tax_type,
                    c.province,
                    f"{c.rate * 100:g}%",
                    f"{c.amount:.2f}",
                    c.currency,
                    c.vendor,
                    c.invoice_number,
                    c.invoice_date,
                    f"{c.grand_total:.2f}",
                    c.registration,
                    c.invoice_id,
                    "; ".join(c.issues),
                ]
            )  # fmt: skip
        )
    return ("﻿" + out.getvalue()).encode("utf-8")


def default_period(today: dt.date | None = None) -> tuple[dt.date, dt.date]:
    """The previous calendar quarter (the most common filing period)."""
    today = today or dt.date.today()
    quarter_start = dt.date(today.year, 3 * ((today.month - 1) // 3) + 1, 1)
    end = quarter_start - dt.timedelta(days=1)
    return dt.date(end.year, 3 * ((end.month - 1) // 3) + 1, 1), end
