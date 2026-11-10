"""The review screen's money: the invoice at a glance, the Tax check and the GL posting preview.

* ``summary`` (supplier, number, dates, due, confidence, amount) and ``stored_summary`` (an invoice not in review).
* The Tax check: each tax line against the official rate (``tax_check``), the lines it points at (``tax_marks``).
* The posting: the journal lines approval sends (``distribution``), where the money goes (``spend_split``) and a
  word over the preview while tax codes are to check (``posting_note``).

The tax rules are ``coding/tax.py``'s and ``coding/taxmath.py``'s; the journal lines, the store's.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from ap_coder.coding.line_tax import charged_types, exemption_to_confirm, tax_guessed
from ap_coder.coding.money import tax_right
from ap_coder.coding.tax import (
    EXPENSE_TO_LINE,
    OUTSIDE_CANADA,
    PROVINCE_NAMES,
    currency_places,
    place_of_supply,
    province_label,
    stored_supply,
    tax_line_ways,
)
from ap_coder.coding.terms import describe as terms_describe
from ap_coder.coding.terms import payment
from ap_coder.coding.validation import off_text
from ap_coder.core.reference_data import UNASSIGNED, ReferenceData
from ap_coder.core.schema import InvoiceCoding
from ap_coder.core.ui import TAX_LABELS
from ap_coder.web.invoice_findings import checks_summary, confidence_pct, confidences, low_confidence, readable
from ap_coder.web.invoice_form import signed
from ap_coder.web.invoice_grid import cc_display, gl_display, gl_name

# --- The invoice at a glance --------------------------------------------------------------------------------------

METER_ABOUT = {
    "Reading": (
        "How sure AP Coder is of what it read: the supplier, number, dates, amounts and tax numbers. It rises as AP "
        "approves this supplier's invoices."
    ),
    "Coding": (
        "How sure AP Coder is of the lines' GL accounts, cost centers and tax codes. A line still to code counts as "
        "nothing."
    ),
}


def due_text(coding: dict[str, Any], default_days: int, vendor_terms: str = "") -> str:
    p = payment(coding, default_days, vendor_terms)
    text = terms_describe(p) + (" (vendor master terms)" if p.source == "vendor" else "")
    if p.discount_open():
        text += f" · {p.terms.discount_pct:g}% off until {p.discount_by:%b} {p.discount_by.day}"
    return text


def summary(coding: InvoiceCoding, report: Any, errors: list[Any], warnings: list[Any], default_days: int,
            vendor_terms: str, setup: Any = None) -> dict[str, Any]:  # fmt: skip
    """The invoice at a glance: supplier, number, dates, status, confidence and the amount."""
    supply = place_of_supply(coding, setup)
    if not supply and coding.ship_to_province == OUTSIDE_CANADA:  # as Place of supply says it (supply_line, B-441)
        supply = OUTSIDE_CANADA
    credit = coding.grand_total < 0
    meta = [
        (f"{'Credit note' if credit else 'Invoice'} {coding.invoice_number}", "Invoice number"),
        (f"Issued {coding.invoice_date}" if coding.invoice_date else "", "Invoice date"),
        (due_text(coding.to_output(), default_days, vendor_terms) if coding.grand_total > 0 else
         "Nothing to pay: a credit" if credit else "", "Payment due"),
        (province_label(supply), "Place of supply"),
        (f"GST/HST {coding.gst_hst_registration_number}" if coding.gst_hst_registration_number else "",
         "Supplier's GST/HST number"),
    ]  # fmt: skip
    look = len(warnings) + (len(low_confidence(report)) if not errors else 0)
    # as many as the checks list before approving and to fix (B-1189)
    blocking = sum(1 for i in checks_summary(report, errors, warnings)["items"] if i["bad"])
    status = ((f"{blocking} to fix", "err") if blocking else ("Needs attention", "warn") if look
              else ("Ready to approve", "ok"))  # fmt: skip
    value = max(0.0, min(1.0, report.adjusted_confidence or 0.0))
    return {
        "vendor": coding.vendor_name or "Unknown vendor",
        "number": coding.invoice_number,
        "status": status,
        "confidence": value,
        "confidence_text": confidence_pct(value, report.review_threshold),
        "confidence_ok": value >= report.review_threshold,
        # Reading and Coding (D-89): each with its share, as shown, and whether it is up to the threshold.
        "meters": [
            {
                "name": name,
                "value": max(0.0, min(1.0, v or 0.0)),
                "text": confidence_pct(max(0.0, min(1.0, v or 0.0)), report.review_threshold),
                "ok": (v or 0.0) >= report.review_threshold,
                "about": METER_ABOUT.get(name, ""),
            }
            for name, v in confidences(report)
        ],
        "threshold": report.review_threshold,
        "meta": [(t, about) for t, about in meta if t],
        "taxes": [TAX_LABELS.get(t, t) for t in dict.fromkeys(t.tax_type for t in coding.tax_lines)],
        "learned": any(h["status"] == "match" for h in report.checks.get("history") or []),
        "total_label": "Credit" if coding.grand_total < 0 else "Total due",
        "total": signed(coding.grand_total),
        "currency": coding.currency,
    }


def stored_summary(inv: dict[str, Any], doc: dict[str, Any]) -> dict[str, Any]:
    """The header of an invoice that is not being reviewed (approved, parked, rejected...): as stored."""
    supply = stored_supply(doc)
    meta = [
        (
            f"{'Credit note' if float(doc.get('grand_total', inv.get('grand_total')) or 0) < 0 else 'Invoice'} "
            f"{doc.get('invoice_number') or inv.get('invoice_number') or '—'}",
            "Invoice number",
        ),
        (f"Issued {doc['invoice_date']}" if doc.get("invoice_date") else "", "Invoice date"),
        (f"Due {inv['due_date']}" if inv.get("due_date") else "", "Payment due"),
        (province_label(supply) if supply else "", "Place of supply"),
    ]
    total = doc.get("grand_total", inv.get("grand_total"))
    return {
        "vendor": doc.get("vendor_name") or inv.get("vendor_name") or inv.get("file_name") or "Unknown vendor",
        "meta": [(t, about) for t, about in meta if t],
        "taxes": [
            TAX_LABELS.get(t, t) for t in dict.fromkeys(t.get("tax_type", "") for t in doc.get("tax_lines") or []) if t
        ],  # fmt: skip
        "total_label": "Credit" if (total or 0) < 0 else "Total",
        "total": signed(total),
        "currency": doc.get("currency") or inv.get("currency") or "",
    }


# --- The Tax check ------------------------------------------------------------------------------------------------


def tax_check(coding: InvoiceCoding, reference: ReferenceData) -> list[dict[str, Any]]:
    """Each tax line's rate against the official rate, its arithmetic, and the account it posts to."""
    try:
        on = dt.date.fromisoformat(coding.invoice_date)
    except ValueError:
        on = dt.date.today()
    rows = []
    places = currency_places(coding.currency)
    for t in coding.tax_lines:
        prov = t.province or place_of_supply(coding, reference.tax)
        official = reference.tax.rates.rate_for(t.tax_type, prov if prov in PROVINCE_NAMES else "", on)
        treat = reference.tax.treatment(t.tax_type)
        gl = treat.gl_code if treat.needs_gl else "expense lines"
        rows.append({
            "tax": TAX_LABELS.get(t.tax_type, t.tax_type), "province": t.province,
            "rate_ok": official is not None and abs(official - t.rate) < 1e-6, "rate": f"{t.rate * 100:.3f}%",
            "official": "not levied" if official is None else f"{official * 100:.3f}%",
            # As TAX_CALC_MISMATCH and the Tax check above it: a unit either way, in the currency's units (B-1908)
            "math_ok": tax_right(t.tax_amount, tax_line_ways(coding, t, places), places),
            "amount": signed(t.tax_amount), "posts_to": gl or "⚠ not mapped",
        })  # fmt: skip
    return rows


# Findings about a line's tax code: the line is drawn yellow in the grid, with the finding as its note.
TAX_LINE_FINDINGS = ("EXEMPT_LINE_TAXED", "LINE_TAX_NOT_CHARGED", "TAX_BASE_MISMATCH", "PST_EXEMPTION_TO_CONFIRM")


def tax_marks(math: dict[str, Any], issues: list[Any]) -> dict[int, str]:
    """{line number: why}: the lines whose tax code the checks point at (a finding about the line, else the Tax check
    row that does not add up), for the grid to draw yellow."""
    marks: dict[int, str] = {}
    for issue in issues:
        if issue.code in TAX_LINE_FINDINGS and issue.line_number:
            marks.setdefault(issue.line_number, readable(issue.message))
    for number, why in (math.get("marks") or {}).items():
        marks.setdefault(number, why)
    return marks


# --- The GL posting -----------------------------------------------------------------------------------------------


def posting_note(coding: InvoiceCoding, reference: ReferenceData) -> str:
    """A word over the GL posting preview when a non-recoverable tax (PST) is spread over the lines marked with it
    while some lines' tax codes are still to check: the spread may be on the wrong lines (B-714)."""
    spread = [t for t in charged_types(coding.tax_lines) if reference.tax.treatment(t).treatment == EXPENSE_TO_LINE]
    open_lines = [li.line_number for li in coding.line_items if tax_guessed(li) or exemption_to_confirm(li)]
    if not spread or not open_lines:
        return ""
    numbers = ", ".join(str(n) for n in open_lines[:8]) + ("…" if len(open_lines) > 8 else "")
    return (
        f"The {' and '.join(spread)} is spread over the lines marked with it, and the tax codes of line"
        f"{'s' if len(open_lines) > 1 else ''} {numbers} are still to check: confirm them before approving."
    )


def spend_split(output: dict[str, Any], reference: ReferenceData) -> list[dict[str, Any]]:
    """Where the money goes: expense GLs (incl. non-recoverable tax) and recoverable tax accounts (a credit note:
    nothing to draw)."""
    by_gl: dict[str, float] = {}
    for e in output.get("gl_distribution") or []:
        name = gl_name(reference, e["gl_code"])
        if e["kind"] != "expense":
            label = f"{name or e['gl_code'] or 'Unmapped'} (tax)"
        elif e["gl_code"] == UNASSIGNED or not e["gl_code"]:
            label = "No GL account yet"
        else:
            label = f"{e['gl_code']} {name}".strip()
        by_gl[label] = by_gl.get(label, 0.0) + e["amount"]
    segments = sorted(((k, v) for k, v in by_gl.items() if v > 0), key=lambda kv: -kv[1])
    if not segments:
        return []
    if len(segments) > 6:
        segments = [*segments[:5], ("Other", sum(v for _, v in segments[5:]))]
    total = sum(v for _, v in segments)
    return [{"label": label, "amount": signed(amount), "share": amount / total,
             "pct": f"{amount / total:.0%}"} for label, amount in segments]  # fmt: skip


def distribution(output: dict[str, Any], reference: ReferenceData | None, js: Any = None) -> dict[str, Any]:
    """The journal lines approval sends to the ERP, their total and whether it balances with the invoice; ``js``: the
    JD Edwards settings when it is the export in use, for each line's E1 account (BU.Object.Subsidiary)."""
    from ap_coder.erp import jde_accounts

    rows = []
    for e in output.get("gl_distribution") or []:
        account = None
        if js is not None:  # a recoverable tax line is posted by E1 itself (its tax AAIs): no account of ours,
            # except with the tax coded to G/L accounts, where it goes to its tax account (D-93, B-1022)
            recoverable = e["kind"] == "tax" and abs(float(e.get("non_recoverable_tax") or 0)) < 0.005
            recoverable = recoverable and not getattr(js, "tax_on_gl", False)
            mapped = jde_accounts.account_text(e["gl_code"] or "", e.get("cost_center") or "", js)
            account = "posted by JD Edwards" if recoverable else mapped or "not mapped"
        rows.append({
            "account": account,
            "kind": "Tax" if e["kind"] == "tax" else f"Line {e['line_number']}",
            "gl": gl_display(e["gl_code"]) if e["gl_code"] else "⚠ not mapped",
            "gl_name": gl_name(reference, e["gl_code"]),
            "todo": e["gl_code"] in (UNASSIGNED, ""),
            "cc": cc_display(e.get("cost_center")),
            "description": e.get("description") or "",
            "net": signed(e["net_amount"]) if e["kind"] == "expense" else "",
            "nonrec": signed(e["non_recoverable_tax"]) if e.get("non_recoverable_tax") else "",
            "amount": signed(e["amount"]),
            "line": e.get("line_number") if e["kind"] != "tax" else "", "gl_code": e["gl_code"] or "",
        })  # fmt: skip
    total = round(sum(e["amount"] for e in output.get("gl_distribution") or []), 2)
    diff = round(total - (output.get("grand_total") or 0), 2)
    off = off_text(total, output.get("grand_total") or 0)
    return {"rows": rows, "total": signed(total), "balanced": abs(diff) < 0.005, "off": signed(diff),
            "off_text": off[:1].upper() + off[1:],
            "jde": js is not None, "currency": output.get("currency") or ""}  # fmt: skip
