"""Sales-tax return support: the GST/HST and QST paid to vendors that can be claimed back for a period.

* Input tax credits (ITCs): GST and HST on approved invoices dated in the period, where the tax setup
  treats the tax as recoverable
* Input tax refunds (ITRs): the same for Quebec's QST
* Claims at risk: tax claimed on an invoice of $100 or more ($30 before April 21, 2021) that shows no valid GST/HST
  (or QST) registration number (a documentary requirement for the claim; a GST/HST number failing the CRA's check
  digit is not a valid one), tax in a foreign currency (the return
  is in Canadian dollars), and approved invoices not exported to the ERP yet (the ERP's receivable will
  not match)

Credit notes reduce the claim. Invoices still in review or waiting for a second approval are counted
separately, not claimed. On meals and entertainment only half the tax is claimed (ITA s.67.1, ETA s.236 and Quebec's
QST Act alike, D-90): the other half was expensed with the line when the invoice was approved, and the report says so.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
from collections import defaultdict
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from ..core.safe import csv_row
from ..core.schema import FROM_VENDOR_LIST
from ..core.store import APPROVED, PARKED, PENDING, REVIEW, Store
from .tax import (
    DEFAULT_TREATMENTS,
    RECOVERABLE,
    TaxRateTable,
    TaxSetup,
    TaxTreatment,
    gst_check_digit_ok,
    place_of_supply,
    regime_on,
    valid_gst_number,
    valid_qst_number,
)

ITC, ITR = "GST/HST input tax credits (ITCs)", "QST input tax refunds (ITRs)"
GROUPS = {"GST": ITC, "HST": ITC, "QST": ITR}
# Below this total, no registration number is required on the invoice: $100 for invoices dated from 2021-04-21, $30
# before (Input Tax Credit Information (GST/HST) Regulations, amended by Budget 2021 with effect from April 21, 2021;
# Quebec's QST rules follow the same amounts). From $500 ($150 before) more is required (the recipient's name, the terms
# of payment, a description of each supply), which this report does not check. Source: CRA, "Input tax credits –
# Documents required" (GST/HST Memorandum 8.4, "Documentary Requirements for Claiming Input Tax Credits"), checked
# 2026-10.
DOCUMENT_THRESHOLD = 100.0
DOCUMENT_THRESHOLD_BEFORE_2021 = 30.0
DOCUMENT_THRESHOLD_FROM = "2021-04-21"


def document_threshold(invoice_date: str) -> float:
    """The invoice total from which the supplier's registration number is needed to claim the tax."""
    return DOCUMENT_THRESHOLD if str(invoice_date or "") >= DOCUMENT_THRESHOLD_FROM else DOCUMENT_THRESHOLD_BEFORE_2021


NO_GST_NUMBER = "No valid GST/HST number"
GST_NOT_PRINTED = "GST/HST number not printed on the invoice (from the vendor list)"
QST_NOT_PRINTED = "QST number not printed on the invoice (from the vendor list)"
NO_QST_NUMBER = "No valid QST number"
GST_CHECK_DIGIT = "GST/HST number fails the CRA check digit"
FOREIGN = "Foreign currency: convert to CAD"
NOT_EXPORTED = "Not exported yet"
# Half the tax on meals and entertainment is not claimable (D-90): a reason the claim is less than the tax, not a risk.
MEALS = "Meals & entertainment: 50% of the tax claimable"
MEALS_WHY = (
    "Only 50% of the GST/HST and QST paid on meals and entertainment is claimable (Income Tax Act s.67.1, Excise Tax "
    "Act s.236, and Quebec's QST Act alike): the other 50% was expensed with the meal when the invoice was approved. "
    "The ITC total above is already net of it: report it as is on line 106 of the GST/HST return, with no add-back "
    "for these meals on line 104 at year end."
)
RISKS = {NO_GST_NUMBER, NO_QST_NUMBER, GST_CHECK_DIGIT, GST_NOT_PRINTED, QST_NOT_PRINTED, FOREIGN}


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
    meals: float = 0.0  # the tax on meals not claimed (D-90): ``amount`` is what is claimed

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

    def meals_totals(self) -> dict[str, dict[str, float]]:
        """{"GST/HST" / "QST": {currency: the tax on meals and entertainment not claimed}}, only the taxes that have
        some, each currency apart (B-870), as ``totals``."""
        out: dict[str, dict[str, float]] = {}
        for c in self.claims:
            if c.meals:
                by = out.setdefault("QST" if c.tax_type == "QST" else "GST/HST", {})
                by[c.currency] = round(by.get(c.currency, 0.0) + c.meals, 2)
        return out

    def at_risk(self) -> list[Claim]:
        """Claims to check before filing (not exporting to the ERP yet is only a reconciliation note)."""
        return [c for c in self.claims if set(c.issues) & RISKS]


def _registration_issue(tax_type: str, doc: dict[str, Any]) -> tuple[str, str]:
    """(the number on the invoice, the issue when it is missing or invalid). A number the review screen filled from
    the vendor list is not on the invoice (D-65): the claim needs it printed there."""
    filled = set(doc.get(FROM_VENDOR_LIST) or [])
    if tax_type == "QST":
        number = str(doc.get("qst_registration_number") or "")
        if valid_qst_number(number) and "qst_registration_number" in filled:
            return number, QST_NOT_PRINTED
        return number, "" if valid_qst_number(number) else NO_QST_NUMBER
    number = str(doc.get("gst_hst_registration_number") or "")
    if not valid_gst_number(number):
        return number, NO_GST_NUMBER
    if not gst_check_digit_ok(number):
        return number, GST_CHECK_DIGIT
    return number, GST_NOT_PRINTED if "gst_hst_registration_number" in filled else ""


def supply_setup(store: Store, rates: TaxRateTable | None = None) -> TaxSetup:
    """What the place of supply needs from Settings → Review: our province and the provinces of our sites."""
    return TaxSetup(
        rates or TaxRateTable.load(), our_province=store.our_province(), site_provinces=store.site_provinces()
    )


def supply_province(doc: dict[str, Any], setup: TaxSetup | None = None) -> str:
    """The place of supply of a stored invoice, by the same rule as the tax checks (``tax.supply_source``): the
    ship-to, else the province of the site the lines are coded to, else ours, else the vendor's."""
    lines = [li for li in doc.get("line_items") or [] if isinstance(li, dict)]
    coding = SimpleNamespace(
        ship_to_province=str(doc.get("ship_to_province") or ""),
        supplier_province=str(doc.get("supplier_province") or ""),
        line_items=[SimpleNamespace(predicted_cost_center=li.get("predicted_cost_center") or "") for li in lines],
    )
    return place_of_supply(coding, setup)


def province(tax_type: str, tax_line: dict[str, Any], doc: dict[str, Any], setup: TaxSetup | None = None) -> str:
    """The province a tax line belongs to: GST is federal (the same wherever the supply is), HST and QST
    from the line or else the place of supply."""
    if tax_type == "GST":
        return ""
    return str(tax_line.get("province") or supply_province(doc, setup))


def build(store: Store, start: dt.date, end: dt.date, treatments: dict[str, TaxTreatment] | None = None) -> Report:
    """The claimable tax on approved invoices dated from ``start`` to ``end`` (inclusive)."""
    treatments = treatments if treatments is not None else store.tax_treatments()

    def recoverable(tax_type: str) -> bool:
        t = treatments.get(tax_type)
        return (t.treatment if t else DEFAULT_TREATMENTS[tax_type]) == RECOVERABLE

    setup = supply_setup(store)
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
        meals = meals_by_tax_line(doc)
        for position, tl in enumerate(doc.get("tax_lines") or []):
            tax_type = str(tl.get("tax_type") or "").upper()
            amount = round(float(tl.get("tax_amount") or 0), 2)
            if tax_type not in GROUPS or abs(amount) < 0.005 or not recoverable(tax_type):
                continue
            not_claimed = meals.get(position, 0.0)
            amount = round(amount - not_claimed, 2)
            number, issue = _registration_issue(tax_type, doc)
            issues = [issue] if issue and abs(total) >= document_threshold(date) else []
            if not_claimed:
                issues.append(MEALS)
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
                    province(tax_type, tl, doc, setup),
                    float(tl.get("rate") or 0),
                    amount,
                    number,
                    issues,
                    not_claimed,
                )  # fmt: skip
            )
    claims.sort(key=lambda c: (c.invoice_date, c.invoice_id))
    not_recoverable = [t for t in GROUPS if not recoverable(t)]
    return Report(start, end, claims, not_approved, not_recoverable)


def meals_by_tax_line(doc: dict[str, Any]) -> dict[int, float]:
    """{tax line position: the tax on meals not claimed} as posted when the invoice was approved (its stored GL
    distribution, D-90): an invoice approved before, or with no meals line, has none."""
    out: dict[int, float] = {}
    for e in doc.get("gl_distribution") or []:
        if isinstance(e, dict) and e.get("kind") == "tax" and e.get("meals_tax") and e.get("tax_line") is not None:
            try:
                out[int(e["tax_line"])] = round(out.get(int(e["tax_line"]), 0.0) + float(e["meals_tax"]), 2)
            except (TypeError, ValueError):
                continue
    return out


def _charged(tax_line: dict[str, Any]) -> bool:
    try:
        return abs(float(tax_line.get("tax_amount") or 0)) > 0.004
    except (TypeError, ValueError):
        return False


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
    note: str = ""  # e.g. JD Edwards accrues it itself (code B): not to book again (B-722)


JDE_ACCRUES = "JD Edwards accrues this one (code B): don't book it again from this report"


def _jde_accruing(store: Store) -> Any:
    """A test of a coded invoice: exported to JD Edwards (in a batch not undone) with the self-assessed code (B)."""
    from ..erp import jde

    batches = {b["id"] for b in store.export_batches() if b.get("format") == jde.FORMAT and not b.get("undone_at")}
    if not batches:
        return lambda batch, doc: False
    settings = jde.load(store)
    return lambda batch, doc: batch in batches and jde.accrues_use_tax(doc, settings)


def self_assessment(
    store: Store, start: dt.date, end: dt.date, rates: TaxRateTable | None = None
) -> list[SelfAssessment]:
    """Approved invoices for a supply in a PST or QST province with lines the PST / QST is due on and not charged (an
    out-of-province or unregistered vendor, or a line the vendor missed): the buyer self-assesses it (BC PSTA s.49 and
    the SK, MB and QST acts alike), at the official rate on those lines (``self_assessed_base``, line by line, D-64)."""
    rates = rates or TaxRateTable.load()
    setup = supply_setup(store, rates)
    first, last = start.isoformat(), end.isoformat()
    out = []
    accrued = _jde_accruing(store)
    for r in store.invoice_columns(("id", "final_output", "export_batch"), APPROVED):
        doc = r["final_output"] or {}
        date = str(doc.get("invoice_date") or "")
        if not (first <= date <= last):
            continue
        province = supply_province(doc, setup)
        # A printed "PST 0.00" is not a charge (as in the tax checks): that PST may still be owed.
        charged = {str(tl.get("tax_type") or "").upper() for tl in doc.get("tax_lines") or [] if _charged(tl)}
        items = doc.get("line_items") or []
        try:
            on = dt.date.fromisoformat(date)
        except ValueError:
            on = None
        for tax_type in sorted(regime_on(province, on, rates) & {"PST", "QST"}):
            base = self_assessed_base(doc, items, tax_type, charged)
            if abs(base) < 0.005:
                continue
            rate = rates.rate_for(tax_type, province, on) if on else None
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
                    JDE_ACCRUES if tax_type == "PST" and accrued(r["export_batch"], doc) else "",
                )  # fmt: skip
            )
    return sorted(out, key=lambda s: (s.province, s.invoice_date, s.invoice_id))


def self_assessed_base(doc: dict[str, Any], items: list[dict[str, Any]], tax_type: str,
                       charged: Any = None) -> float:  # fmt: skip
    """What a PST or QST the vendor did not collect is owed on (D-64), line by line: the lines it is due on and not
    charged (neither charged nor exempt), and the lines marked with it on an invoice that charges none of it. A line
    exempt or zero-rated from it, or out of scope, owes nothing. An invoice without lines: its subtotal when the tax
    is not charged. ``charged``: the taxes the invoice charges (worked out from its tax lines when not given)."""
    from .line_tax import CHARGED, DUE, legacy_basis, tax_state

    if charged is None:
        charged = {str(tl.get("tax_type") or "").upper() for tl in doc.get("tax_lines") or [] if _charged(tl)}
    if not items:
        return 0.0 if tax_type in charged else round(float(doc.get("subtotal") or 0), 2)
    basis = legacy_basis(items, doc.get("tax_lines") or [])
    owed = 0.0
    for li in items:
        state = tax_state(li, tax_type, basis)
        if state == DUE or (state == CHARGED and tax_type not in charged):
            owed += float(li.get("amount") or 0)
    return round(owed, 2)


def self_assessment_csv(items: list[SelfAssessment], start: dt.date, end: dt.date) -> bytes:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow([f"PST / QST possibly to self-assess, invoices dated {start.isoformat()} to {end.isoformat()}"])
    w.writerow(["Province", "Tax", "Rate", "Subtotal", "Estimate", "Currency", "Vendor", "Invoice", "Invoice date",
                "AP Coder #", "Note"])  # fmt: skip
    for s in items:
        w.writerow(csv_row([s.province, s.tax_type, f"{s.rate * 100:g}%", f"{s.base:.2f}", f"{s.estimate:.2f}",
                            s.currency, s.vendor, s.invoice_number, s.invoice_date, s.invoice_id,
                            s.note]))  # fmt: skip
    return ("\ufeff" + out.getvalue()).encode("utf-8")


def to_csv(report: Report) -> bytes:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow([f"Sales tax to claim, invoices dated {report.start.isoformat()} to {report.end.isoformat()}"])
    w.writerow(["Claim", "Tax", "Province", "Rate", "Tax amount", "Currency", "Vendor", "Invoice", "Invoice date",
                "Invoice total", "Registration number", "AP Coder #", "To check",
                "Not claimed: meals & entertainment (50%)"])  # fmt: skip
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
                    f"{c.meals:.2f}" if c.meals else "",
                ]
            )  # fmt: skip
        )
    if any(c.meals for c in report.claims):
        w.writerow([])
        w.writerow([MEALS_WHY])
    return ("﻿" + out.getvalue()).encode("utf-8")


def default_period(today: dt.date | None = None) -> tuple[dt.date, dt.date]:
    """The previous calendar quarter (the most common filing period)."""
    today = today or dt.date.today()
    quarter_start = dt.date(today.year, 3 * ((today.month - 1) // 3) + 1, 1)
    end = quarter_start - dt.timedelta(days=1)
    return dt.date(end.year, 3 * ((end.month - 1) // 3) + 1, 1), end
