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
import json
from collections import defaultdict
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from ..core.safe import csv_row
from ..core.schema import FROM_VENDOR_LIST
from ..core.store import APPROVED, PARKED, PENDING, REVIEW, Store
from ..core.store.summary import broken, computed
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
# Tax charged in error (B-925): only the tax properly payable is claimable (ETA s.169); what was charged too much is
# recovered from the vendor (s.232: a credit note), not claimed. From the findings still open when it was approved.
WRONG_RATE = (
    "Tax charged at the wrong rate: recover the excess from the vendor (credit note, ETA s.232), claim only the right "
    "rate"
)
NOT_PAYABLE = (
    "Tax charged that isn't payable here (HST where GST applies): claim the GST part only, or get a corrected invoice "
    "(ETA s.169)"
)
TAX_WRONG = "Tax doesn't match the rate on the lines: claim only the tax properly charged, or get a corrected invoice"
CHARGED_IN_ERROR = {"TAX_RATE_NONSTANDARD": WRONG_RATE, "ITC_AT_RISK": NOT_PAYABLE, "TAX_CALC_MISMATCH": TAX_WRONG}
RISKS = {NO_GST_NUMBER, NO_QST_NUMBER, GST_CHECK_DIGIT, GST_NOT_PRINTED, QST_NOT_PRINTED, FOREIGN,
         *CHARGED_IN_ERROR.values()}  # fmt: skip


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
    """The claimable tax on approved invoices dated from ``start`` to ``end`` (inclusive), from the invoices' summary
    (``tax_facts``, D-110)."""
    treatments = treatments if treatments is not None else store.tax_treatments()

    def recoverable(tax_type: str) -> bool:
        t = treatments.get(tax_type)
        return (t.treatment if t else DEFAULT_TREATMENTS[tax_type]) == RECOVERABLE

    setup = supply_setup(store)
    supply = _supply_memo(setup)
    first, last = start.isoformat(), end.isoformat()
    claims, not_approved = [], 0
    # (an invoice the summary could not read is read again: failing as it did when every invoice was read)
    rows = store.summary_rows(f"tax_date BETWEEN ? AND ? AND +status IN (?, ?, ?, ?) OR {broken('tax')}",
                              (first, last, APPROVED, REVIEW, PARKED, PENDING), "id, status, tax_date, export_batch, "
                              "broken, t_facts")  # fmt: skip
    for r in rows:
        found = computed(store, r, "tax")
        date = found["tax_date"]
        if not (first <= date <= last):
            continue
        if r["status"] in (REVIEW, PARKED, PENDING):
            not_approved += 1
            continue
        f = _json(found["t_facts"]) if r["status"] == APPROVED else None
        if f is None:  # approved without the reviewer's coding: nothing to claim
            continue
        for tax_type, amount, not_claimed, rate, province_text, number, issue in f["lines"]:
            if not recoverable(tax_type):
                continue
            issues = [issue] if issue and abs(f["total"]) >= document_threshold(date) else []
            issues += f["err"]
            if not_claimed:
                issues.append(MEALS)
            if f["cur"] != "CAD":
                issues.append(FOREIGN)
            if r["export_batch"] is None:
                issues.append(NOT_EXPORTED)
            place = "" if tax_type == "GST" else str(province_text or supply(f["supply"]))
            claims.append(Claim(r["id"], f["vendor"], f["number"], date, f["cur"], f["total"], tax_type, place,
                                float(rate), amount, number, issues, not_claimed))  # fmt: skip
    claims.sort(key=lambda c: (c.invoice_date, c.invoice_id))
    not_recoverable = [t for t in GROUPS if not recoverable(t)]
    return Report(start, end, claims, not_approved, not_recoverable)


def tax_facts(row: dict[str, Any], approval: dict[str, Any] | None) -> dict[str, Any]:
    """What ``build`` needs of an approved invoice (``row``: its columns, the JSON ones decoded; ``approval``: its
    latest ``approved`` event), whatever the tax setup: kept in the invoices' summary (D-110). Each tax line that may
    be claimed: (tax type, the amount claimed, the meals' half not claimed, rate, province printed, registration
    number, its issue)."""
    doc = row["final_output"]
    meals = meals_by_tax_line(doc)
    lines = []
    for position, tl in enumerate(doc.get("tax_lines") or []):
        tax_type = str(tl.get("tax_type") or "").upper()
        amount = round(float(tl.get("tax_amount") or 0), 2)
        if tax_type not in GROUPS or abs(amount) < 0.005:
            continue
        not_claimed = meals.get(position, 0.0)
        number, issue = _registration_issue(tax_type, doc)
        rate = tl.get("rate") or 0
        lines.append([tax_type, round(amount - not_claimed, 2), not_claimed, _float_or(rate), tl.get("province"),
                      number, issue])  # fmt: skip
    return {"cur": str(doc.get("currency") or "CAD").upper(), "total": float(doc.get("grand_total") or 0),
            "vendor": str(doc.get("vendor_name") or ""), "number": str(doc.get("invoice_number") or ""),
            "err": charged_in_error(row, approval), "lines": lines, "supply": supply_inputs(doc)}  # fmt: skip


def _json(text: str | None) -> Any:
    return json.loads(text) if text is not None else None


def _float_or(value: Any) -> Any:
    """``float(value)``, or ``value`` itself when it is not a number: converted where it is used, which then fails
    as it did before (a tax setup that never claims that tax never reads it)."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def supply_inputs(doc: dict[str, Any]) -> list[Any]:
    """What ``supply_province`` reads of an invoice's coding: its ship-to, its vendor's province, its lines' cost
    centers."""
    lines = [li for li in doc.get("line_items") or [] if isinstance(li, dict)]
    return [str(doc.get("ship_to_province") or ""), str(doc.get("supplier_province") or ""),
            [li.get("predicted_cost_center") or "" for li in lines]]  # fmt: skip


def _supply_memo(setup: TaxSetup) -> Any:
    """``supply_province`` of ``supply_inputs``, worked out once per different inputs."""
    known: dict[str, str] = {}

    def supply(inputs: list[Any]) -> str:
        key = json.dumps(inputs)
        if key not in known:
            ship, supplier, centers = inputs
            known[key] = supply_province(
                {
                    "ship_to_province": ship,
                    "supplier_province": supplier,
                    "line_items": [{"predicted_cost_center": c} for c in centers],
                },
                setup,
            )
        return known[key]

    return supply


def charged_in_error(row: dict[str, Any], approval: dict[str, Any] | None) -> list[str]:
    """What the tax checks said of tax charged in error on an approved invoice (B-925), as claim issues: from the
    findings still open when it was approved (the approval's ``open_issues``); an approval that kept none (approved
    outside the review screen) reads the checks stored when it was read, as long as its tax lines were not changed
    since."""
    detail = (approval or {}).get("detail") or {}
    if "open_issues" in detail:
        found = detail.get("open_issues") or []
    elif approval is not None and detail.get("login") is not None:
        found = []  # approved on the review screen with nothing open
    else:
        before = (row.get("ai_output") or {}).get("tax_lines")
        same = before == (row.get("final_output") or {}).get("tax_lines")
        found = ((row.get("validation") or {}).get("issues") or []) if same else []
    codes = {str(i.get("code") or "") for i in found if isinstance(i, dict)}
    return [CHARGED_IN_ERROR[c] for c in CHARGED_IN_ERROR if c in codes]


def meals_by_tax_line(doc: dict[str, Any]) -> dict[int, float]:
    """{tax line position: the tax on meals not claimed} as posted when the invoice was approved (its stored GL
    distribution, D-90): an invoice approved before, or with no meals line, has none. A tax line wholly on meals with
    nothing left to claim (a cent) has no tax entry: its meals lines say it (``meals_tax_lines``, B-893)."""
    out: dict[int, float] = {}
    held: dict[int, float] = {}
    for e in doc.get("gl_distribution") or []:
        if not isinstance(e, dict):
            continue
        try:
            if e.get("kind") == "tax" and e.get("meals_tax") and e.get("tax_line") is not None:
                out[int(e["tax_line"])] = round(out.get(int(e["tax_line"]), 0.0) + float(e["meals_tax"]), 2)
            elif e.get("kind") == "expense" and isinstance(e.get("meals_tax_lines"), dict):
                for position, part in e["meals_tax_lines"].items():
                    held[int(position)] = round(held.get(int(position), 0.0) + float(part), 2)
        except (TypeError, ValueError):
            continue
    return {**held, **out}


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
# Approved, not exported yet, while JD Edwards is the export and its settings would accrue it (B-928).
JDE_WILL_ACCRUE = "JD Edwards will accrue this one once exported (code B): don't book it from this report"


def _jde_accruing(store: Store) -> Any:
    """The note of a coded invoice whose PST JD Edwards accrues itself with the self-assessed code (B): exported to
    JD Edwards (in a batch not undone), or not exported yet while JD Edwards is the export in use ("" otherwise)."""
    from ..erp import jde

    batches = {b["id"] for b in store.export_batches() if b.get("format") == jde.FORMAT and not b.get("undone_at")}
    pending = jde.in_use(store)
    if not batches and not pending:
        return lambda batch, doc: ""
    settings = jde.load(store)

    def note(batch: Any, doc: Any) -> str:  # ``doc``: the coding, read when it is needed
        if batch in batches:
            return JDE_ACCRUES if jde.accrues_use_tax(doc(), settings) else ""
        if batch is None and pending:
            return JDE_WILL_ACCRUE if jde.accrues_use_tax(doc(), settings) else ""
        return ""

    return note


def self_assessment(
    store: Store, start: dt.date, end: dt.date, rates: TaxRateTable | None = None
) -> list[SelfAssessment]:
    """Approved invoices for a supply in a PST or QST province with lines the PST / QST is due on and not charged (an
    out-of-province or unregistered vendor, or a line the vendor missed): the buyer self-assesses it (BC PSTA s.49 and
    the SK, MB and QST acts alike), at the official rate on those lines (``self_assessed_base``, line by line, D-64).
    From the invoices' summary (``self_assessment_facts``, D-110)."""
    rates = rates or TaxRateTable.load()
    supply = _supply_memo(supply_setup(store, rates))
    out = []
    accrued = _jde_accruing(store)
    first, last = start.isoformat(), end.isoformat()
    rows = store.summary_rows(f"f_date BETWEEN ? AND ? AND +status = ? OR status = ? AND {broken('spend', 'sa')}",
                              (first, last, APPROVED, APPROVED),
                              "id, f_date, export_batch, broken, s_facts")  # fmt: skip
    for r in rows:
        found = computed(store, r, "spend", "sa")
        f, date = _json(found["s_facts"]), found["f_date"]
        if f is None or not (first <= date <= last):
            continue
        province = supply(f["supply"])
        try:
            on = dt.date.fromisoformat(date)
        except ValueError:
            on = None
        for tax_type in sorted(regime_on(province, on, rates) & {"PST", "QST"}):
            base = f["base"][tax_type]
            if abs(base) < 0.005:
                continue
            rate = rates.rate_for(tax_type, province, on) if on else None
            if not rate:
                continue
            note = accrued(r["export_batch"], lambda r=r: _final(store, r["id"])) if tax_type == "PST" else ""
            out.append(SelfAssessment(r["id"], f["vendor"], f["number"], date, province, tax_type, round(base, 2),
                                      rate, round(base * rate, 2), f["cur"], note))  # fmt: skip
    return sorted(out, key=lambda s: (s.province, s.invoice_date, s.invoice_id))


def self_assessment_facts(doc: dict[str, Any]) -> dict[str, Any]:
    """What ``self_assessment`` needs of an approved invoice's coding, whatever the settings: kept in the invoices'
    summary (D-110)."""
    # A printed "PST 0.00" is not a charge (as in the tax checks): that PST may still be owed.
    charged = {str(tl.get("tax_type") or "").upper() for tl in doc.get("tax_lines") or [] if _charged(tl)}
    items = doc.get("line_items") or []
    return {
        "supply": supply_inputs(doc),
        "base": {t: self_assessed_base(doc, items, t, charged) for t in ("PST", "QST")},
        "vendor": str(doc.get("vendor_name") or ""),
        "number": str(doc.get("invoice_number") or ""),
        "cur": str(doc.get("currency") or "CAD").upper(),
    }


def _final(store: Store, invoice_id: int) -> dict[str, Any]:
    rows = store.invoice_columns(("id", "final_output"), ids=[invoice_id])
    return (rows[0]["final_output"] if rows else None) or {}


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
