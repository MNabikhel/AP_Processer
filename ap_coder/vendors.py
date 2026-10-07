"""Vendor master and payment-fraud / duplicate signals.

Each vendor is identified by its normalised name (``memory.vendor_key``). Its history comes from the
invoices already in the database; a small ``vendors`` table holds what AP sets by hand (on hold,
expected GST/HST number, notes). ``vendor_findings`` turns that into checks on a new invoice:

* VENDOR_ON_HOLD (error): AP put this vendor on hold
* VENDOR_TAX_NUMBER_CHANGED (warning): GST/HST number differs from earlier invoices / the vendor master,
  a classic sign of a fake invoice or changed payment details
* AMOUNT_UNUSUAL (warning): far above what this vendor usually bills
* POSSIBLE_DUPLICATE_AMOUNT (warning): same vendor, same total, close date, different invoice number
* VENDOR_NEW (info): first invoice from this vendor (no penalty; just worth knowing)
"""

from __future__ import annotations

import datetime as dt
import re
import statistics
from typing import TYPE_CHECKING, Any

from .memory import vendor_key

if TYPE_CHECKING:
    from .schema import InvoiceCoding
    from .store import Store

ERROR, WARNING, INFO = "error", "warning", "info"
ON_HOLD, ACTIVE = "on_hold", "active"
UNUSUAL_FACTOR = 3.0  # total above 3x this vendor's median...
UNUSUAL_MIN_GAP = 1000.0  # ...and at least this much above it
UNUSUAL_MIN_HISTORY = 3
DUPLICATE_WINDOW_DAYS = 45

_NUMBER_PREFIXES = re.compile(r"^(invoice|inv|facture|fact|bill|no|num|nr|n°|#)+")


def norm_invoice_number(value: Any) -> str:
    """Compare invoice numbers the way people mean them: "INV-00123", "#123" and "123" are the same."""
    text = re.sub(r"[^0-9a-z°#]", "", str(value or "").lower())
    text = _NUMBER_PREFIXES.sub("", text)
    text = re.sub(r"[^0-9a-z]", "", text)
    return text.lstrip("0") or text


def norm_tax_number(value: Any) -> str:
    return re.sub(r"[^0-9a-z]", "", str(value or "").lower())


def _date(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def vendor_findings(
    coding: InvoiceCoding, store: Store, exclude_invoice_id: int | None = None
) -> list[tuple[str, str, str]]:
    """(severity, code, message) for the vendor-level checks on one invoice."""
    key = vendor_key(coding.vendor_name)
    if not key:
        return []
    findings: list[tuple[str, str, str]] = []
    master = store.get_vendor(key)
    if master and master.get("status") == ON_HOLD:
        note = f": {master['notes']}" if master.get("notes") else ""
        findings.append((ERROR, "VENDOR_ON_HOLD", f"this vendor is on hold in the vendor list{note}"))

    history = [h for h in store.vendor_invoices(key) if h["id"] != exclude_invoice_id]
    if not history:
        if store.has_other_vendors(key):
            findings.append((INFO, "VENDOR_NEW", "first invoice from this vendor: confirm it is a known supplier"))
        return findings

    # GST/HST number compared with what this vendor used before (approved invoices) or what AP recorded.
    number = norm_tax_number(coding.gst_hst_registration_number)
    known = {norm_tax_number(h["gst_hst_number"]) for h in history if h["status"] == "approved"} - {""}
    if master and master.get("expected_gst"):
        known = {norm_tax_number(master["expected_gst"])}
    if number and known and number not in known:
        findings.append(
            (
                WARNING,
                "VENDOR_TAX_NUMBER_CHANGED",
                f"GST/HST number {coding.gst_hst_registration_number} differs from this vendor's usual "
                f"({', '.join(sorted(known))}); confirm with the vendor before paying",
            )
        )

    total, currency = coding.grand_total, coding.currency
    same_currency = [h for h in history if (h["currency"] or "") == currency and h["grand_total"] is not None]
    approved_totals = [h["grand_total"] for h in same_currency if h["status"] == "approved" and h["grand_total"] > 0]
    if total > 0 and len(approved_totals) >= UNUSUAL_MIN_HISTORY:
        median = statistics.median(approved_totals)
        if total > UNUSUAL_FACTOR * median and total - median >= UNUSUAL_MIN_GAP:
            findings.append(
                (
                    WARNING,
                    "AMOUNT_UNUSUAL",
                    f"total {total:,.2f} is {total / median:.1f}x this vendor's usual {median:,.2f} "
                    f"(median of {len(approved_totals)} approved invoices)",
                )
            )

    this_date, this_number = _date(coding.invoice_date), norm_invoice_number(coding.invoice_number)
    for h in same_currency:
        if h["status"] in ("rejected", "failed") or abs(total) < 0.01 or abs(h["grand_total"] - total) > 0.005:
            continue
        if norm_invoice_number(h["invoice_number"]) == this_number:
            continue  # same number: the duplicate-invoice check already reports it
        other = _date(h["invoice_date"])
        if this_date and other and abs((this_date - other).days) <= DUPLICATE_WINDOW_DAYS:
            findings.append(
                (
                    WARNING,
                    "POSSIBLE_DUPLICATE_AMOUNT",
                    f"same total as invoice #{h['id']} ({h['invoice_number']}, {h['invoice_date']}) from this "
                    "vendor: check it is not billed twice under a new number",
                )
            )
            break
    return findings
