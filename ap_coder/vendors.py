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
* CREDIT_NOTE_FOR (info) / CREDIT_NOTE_ORIGINAL_UNKNOWN (info) / CREDIT_EXCEEDS_INVOICE (warning): the invoice a
  credit note credits, found or not, and a credit larger than it
* VENDOR_BANK_CHANGED (warning): the bank account to pay into differs from the one on this vendor's approved
  invoices, the most common payment fraud (a fake "our banking details have changed")
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


_BUSINESS_NUMBER = re.compile(r"(\d{9})(?:rt\d{0,4})?")


def same_tax_number(a: Any, b: Any) -> bool:
    """Whether two GST/HST numbers are the same registration. A vendor master often keeps only the 9-digit Business
    Number ("123456789") while the invoice shows the full account ("123456789 RT0001"): when either side lacks the
    RT program suffix, the 9 digits decide. Two full accounts (RT0001 and RT0002) must match exactly."""
    a, b = norm_tax_number(a), norm_tax_number(b)
    if not a or not b:
        return False
    if a == b:
        return True
    bn_a, bn_b = _BUSINESS_NUMBER.fullmatch(a), _BUSINESS_NUMBER.fullmatch(b)
    if not bn_a or not bn_b or (len(a) == 15 and len(b) == 15):
        return False
    return bn_a.group(1) == bn_b.group(1)


_ACCOUNT_LABEL = re.compile(r"(?i)\b(?:account|acct|acc|a/c|compte|cpte)\b\D{0,12}?(\d[\d\s-]*\d|\d)")


def bank_digits(value: Any) -> tuple[str, tuple[str, ...]]:
    """(every digit in order, the sorted numbers without leading zeros): the same account written
    "004-12345-1234567", "Transit 12345, Institution 004, Account 1234567" or "inst 4 transit 12345 ..."
    compares equal on one of them."""
    runs = re.findall(r"\d+", str(value or ""))
    return "".join(runs), tuple(sorted(r.lstrip("0") or "0" for r in runs))


def same_bank_account(a: Any, b: Any) -> bool:
    (digits_a, runs_a), (digits_b, runs_b) = bank_digits(a), bank_digits(b)
    return bool(digits_a) and (digits_a == digits_b or runs_a == runs_b)


def mask_account(value: Any) -> str:
    """ "…4567": the last 4 digits of the account number (the number after "Account", else the end of the
    details), never the whole account."""
    text = str(value or "")
    labelled = _ACCOUNT_LABEL.search(text)
    digits = re.sub(r"\D", "", labelled.group(1)) if labelled else "".join(re.findall(r"\d+", text))
    return f"…{digits[-4:]}" if digits else "(none)"


def _date(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _credit_note_findings(
    coding: InvoiceCoding, store: Store, history: list[dict[str, Any]]
) -> list[tuple[str, str, str]]:
    """A credit note names the invoice it credits: find it (here or in the ERP register) and check the credit is
    not more than that invoice."""
    printed = coding.original_invoice_number.strip()
    if coding.grand_total >= 0 or not printed:
        return []
    wanted = norm_invoice_number(printed)
    original = next(
        (h for h in history if norm_invoice_number(h["invoice_number"]) == wanted and (h["grand_total"] or 0) > 0), None
    )
    if original is None:
        posted = [r for r in store.in_erp(coding.vendor_name, printed, None) if r["total"] > 0]
        if posted:
            original = {"invoice_number": posted[0]["invoice_number"], "grand_total": posted[0]["total"], "id": None,
                        "status": "in the ERP"}  # fmt: skip
    if original is None:
        unknown = f"credits invoice {printed}, which is not in AP Coder or the ERP register: check it was billed"
        return [(INFO, "CREDIT_NOTE_ORIGINAL_UNKNOWN", unknown)]
    where = f"#{original['id']}, {original['status']}" if original.get("id") else original["status"]
    found = f"credits invoice {original['invoice_number']} ({where}, total {original['grand_total']:,.2f})"
    out = [(INFO, "CREDIT_NOTE_FOR", found)]
    # Earlier credit notes against the same invoice count too (not rejected ones).
    earlier = sum(
        abs(h["grand_total"] or 0) for h in history
        if (h["grand_total"] or 0) < 0 and h["status"] not in ("rejected", "failed")
        and norm_invoice_number(h.get("original_invoice_number") or "") == wanted
    )  # fmt: skip
    credited = abs(coding.grand_total) + earlier
    if credited > (original["grand_total"] or 0) + 0.01:
        with_earlier = f" with earlier credit notes ({credited:,.2f} in all)" if earlier else f" ({credited:,.2f})"
        out.append((WARNING, "CREDIT_EXCEEDS_INVOICE",
                    f"the credit{with_earlier} is more than invoice {original['invoice_number']} "
                    f"({original['grand_total']:,.2f}): check the amounts with the vendor"))  # fmt: skip
    return out


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
        by_number = [v for v in store.master_vendors() if real and same_tax_number(v["expected_gst"], number)]
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
    findings += _credit_note_findings(coding, store, history)
    if not history:
        if store.has_other_vendors(key) and not in_master:  # the ERP's vendor master already vouches for it
            findings.append((INFO, "VENDOR_NEW", "first invoice from this vendor: confirm it is a known supplier"))
        return findings

    # GST/HST number compared with what this vendor used before (approved invoices) or what AP recorded.
    number = norm_tax_number(coding.gst_hst_registration_number)
    known = {norm_tax_number(h["gst_hst_number"]) for h in history if h["status"] == "approved"} - {""}
    if master and master.get("expected_gst"):
        known = {norm_tax_number(master["expected_gst"])}
    if number and known and not any(same_tax_number(number, k) for k in known):
        findings.append(
            (
                WARNING,
                "VENDOR_TAX_NUMBER_CHANGED",
                f"GST/HST number {coding.gst_hst_registration_number} differs from this vendor's usual "
                f"({', '.join(sorted(known))}); confirm with the vendor before paying",
            )
        )

    # Bank account to pay into, compared with the ones on this vendor's approved invoices.
    account = coding.remit_bank_account
    known_accounts = [
        h["bank_account"] for h in history if h["status"] == "approved" and bank_digits(h["bank_account"])[0]
    ]
    if bank_digits(account)[0] and known_accounts and not any(same_bank_account(account, k) for k in known_accounts):
        usual = sorted({mask_account(k) for k in known_accounts})
        shown = mask_account(account)
        if shown in usual:  # the same account number at another bank or branch
            shown = f"{shown}, at a different bank or branch number"
        findings.append(
            (WARNING, "VENDOR_BANK_CHANGED",
             f"the bank account to pay into ({shown}) differs from the one on this vendor's "
             f"approved invoices ({', '.join(usual)}): confirm by phone, on a number from your vendor file, not from "
             "the invoice, before paying or changing the vendor's banking")
        )  # fmt: skip

    total, currency = coding.grand_total, (coding.currency or "CAD").upper()  # no currency printed: CAD
    same_currency = [
        h for h in history if (h["currency"] or "CAD").upper() == currency and h["grand_total"] is not None
    ]
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
               "number", "no", "code", "accountnumber", "suppliercode", "numero"),  # "No." in Business Central
    "gst": ("gsthstnumber", "gstnumber", "hstnumber", "gsthst", "gst", "businessnumber", "bn", "taxnumber",
            "tps", "numerotps", "vatregistrationno", "taxregistrationno", "gstregistrationno",
            "gsthstregistrationno"),
    "terms": ("paymentterms", "terms", "termes", "conditions", "paymenttermscode", "termscode"),
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
        row = {"vendor_name": name, "gst": get("gst"), "status": ON_HOLD if on_hold else ACTIVE,
               "status_given": bool(status)}  # fmt: skip
        for field in ("erp_id", "terms", "default_gl"):  # None: not in this file, so the vendor keeps its value
            row[field] = get(field) if field in columns else None
        rows.append(row)
    return rows
