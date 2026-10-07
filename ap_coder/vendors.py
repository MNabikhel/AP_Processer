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
* DUPLICATE_IN_ERP (error): the same vendor and invoice number is in the ERP's invoice register
* DUPLICATE_OTHER_VENDOR (warning): same invoice number and total under another vendor name
* VENDOR_NOT_IN_MASTER (warning): a vendor master was imported from the ERP and this vendor is not in it
  (by name or GST/HST number)
* VENDOR_MATCHED_BY_TAX_NUMBER (info): not found by name, but the GST/HST number belongs to a master vendor
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
    if store.has_vendor_master() and not (master and master.get("in_master")):
        number = norm_tax_number(coding.gst_hst_registration_number)
        real = sum(c.isdigit() for c in number) >= 9  # a Business Number, not "N/A" or "pending"
        by_number = [v for v in store.master_vendors() if real and norm_tax_number(v["expected_gst"]) == number]
        if by_number:
            master = by_number[0]
            findings.append(
                (INFO, "VENDOR_MATCHED_BY_TAX_NUMBER",
                 f"not found by name in the vendor master, but GST/HST {coding.gst_hst_registration_number} is "
                 f"{master['display_name']} ({master['erp_id'] or 'no ID'})")
            )  # fmt: skip
        else:
            findings.append(
                (WARNING, "VENDOR_NOT_IN_MASTER",
                 "this vendor is not in the vendor master imported from the ERP: set it up (and verify it) first")
            )  # fmt: skip
    in_master = bool(master and master.get("in_master"))
    for posted in store.in_erp(coding.vendor_name, coding.invoice_number, coding.grand_total)[:2]:
        findings.append(
            (ERROR, "DUPLICATE_IN_ERP",
             f"invoice {posted['invoice_number']} from this vendor is already in the ERP"
             f"{' (dated ' + posted['invoice_date'] + ')' if posted['invoice_date'] else ''}, total "
             f"{posted['total']:,.2f}: possible duplicate payment")
        )  # fmt: skip
    for other in store.duplicates_elsewhere(
        coding.vendor_name, coding.invoice_number, coding.grand_total, exclude_invoice_id
    )[:3]:
        findings.append(
            (WARNING, "DUPLICATE_OTHER_VENDOR",
             f"invoice #{other['id']} from {other['vendor_name']} has the same number and total: the same bill "
             "under two vendor names?")
        )  # fmt: skip
    if master and master.get("status") == ON_HOLD:
        note = f": {master['notes']}" if master.get("notes") else ""
        findings.append((ERROR, "VENDOR_ON_HOLD", f"this vendor is on hold in the vendor list{note}"))

    history = [h for h in store.vendor_invoices(key) if h["id"] != exclude_invoice_id]
    if not history:
        if store.has_other_vendors(key) and not in_master:  # the ERP's vendor master already vouches for it
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


# --- Vendor master import ------------------------------------------------------------------------------

MASTER_ALIASES: dict[str, tuple[str, ...]] = {
    "vendor_name": ("vendorname", "vendor", "name", "supplier", "suppliername", "fournisseur", "legalname"),
    "erp_id": ("vendorid", "vendornumber", "vendorno", "vendorcode", "supplierid", "suppliernumber", "id",
               "number", "code", "accountnumber"),
    "gst": ("gsthstnumber", "gstnumber", "hstnumber", "gsthst", "gst", "businessnumber", "bn", "taxnumber",
            "tps", "numerotps"),
    "terms": ("paymentterms", "terms", "termes", "conditions"),
    "status": ("status", "active", "blocked", "hold", "statut"),
    "default_gl": ("defaultgl", "glaccount", "gl", "glcode", "expenseaccount", "account", "defaultaccount"),
}  # fmt: skip
_HOLD_WORDS = {"hold", "on hold", "on_hold", "blocked", "inactive", "suspended", "closed", "disabled", "i", "h", "b",
               "bloqué", "bloque", "inactif", "suspendu"}  # fmt: skip


def _norm_header(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def master_columns(headers: list[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    normalised = {_norm_header(h): h for h in headers}
    for target, aliases in MASTER_ALIASES.items():
        for alias in (_norm_header(target), *aliases):
            if alias in normalised and normalised[alias] not in found.values():
                found[target] = normalised[alias]
                break
    return found


def master_rows(records: list[dict[str, Any]], columns: dict[str, str]) -> list[dict[str, Any]]:
    """Vendor master rows from a spreadsheet, using ``master_columns`` output (a vendor name is required)."""
    rows = []
    for rec in records:

        def get(target: str, rec: dict[str, Any] = rec) -> str:
            value = rec.get(columns[target]) if target in columns else None
            text = "" if value is None else str(value).strip()
            return "" if text.lower() in ("nan", "none") else text

        name = get("vendor_name")
        if not name:
            continue
        status = get("status").lower()
        header = _norm_header(columns.get("status"))
        if header in ("active", "enabled"):  # an "Active" yes/no column: "No" means on hold
            on_hold = status in ("no", "n", "false", "0", "inactive", "disabled")
        elif header in ("blocked", "hold", "onhold", "inactive", "suspended"):  # "Blocked: Yes / All / Payment"
            on_hold = status in ("yes", "y", "true", "1", "x", "all", "payment", "payments", "blocked", "oui")
        else:  # a status column with words or codes
            on_hold = status in _HOLD_WORDS
        rows.append({
            "vendor_name": name, "erp_id": get("erp_id"), "gst": get("gst"), "terms": get("terms"),
            "default_gl": get("default_gl"), "status": ON_HOLD if on_hold else ACTIVE, "status_given": bool(status),
        })  # fmt: skip
    return rows
