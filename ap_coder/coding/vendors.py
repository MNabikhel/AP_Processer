"""Vendor master and payment-fraud / duplicate signals.

Each vendor is identified by its normalised name (``memory.vendor_key``). Its history comes from the
invoices already in the database; a small ``vendors`` table holds what AP sets by hand (on hold,
expected GST/HST number, notes). ``vendor_findings`` turns that into checks on a new invoice:

* VENDOR_ON_HOLD (error): AP put this vendor on hold
* VENDOR_TAX_NUMBER_CHANGED (warning): GST/HST number differs from earlier invoices / the vendor master,
  a classic sign of a fake invoice or changed payment details
* AMOUNT_UNUSUAL (warning): far above what this vendor usually bills
* POSSIBLE_DUPLICATE_AMOUNT (warning): same vendor, same total, close date, different invoice number
* VENDOR_NEW (info): first invoice from this vendor, or none approved yet (no penalty, but never touchless)
* DUPLICATE_IN_ERP (error): the same vendor and invoice number is in the ERP's invoice register
* DUPLICATE_OTHER_VENDOR (warning): same invoice number and total under another vendor name
* VENDOR_NOT_IN_MASTER (error): a vendor master was imported from the ERP and this vendor is not in it
  (by name or GST/HST number): pick the vendor from the list, or add it as a new vendor
* VENDOR_ADDED_HERE (error): a new vendor added to the vendor list from the review screen, not yet in the ERP
* VENDOR_QST_CHANGED / VENDOR_PST_CHANGED (warning): the QST or PST number differs from the vendor's
* VENDOR_MATCHED_BY_TAX_NUMBER (info): not found by name, but the GST/HST number belongs to a master vendor
* CREDIT_NOTE_FOR (info) / CREDIT_NOTE_ORIGINAL_UNKNOWN (info) / CREDIT_EXCEEDS_INVOICE (warning): the invoice a
  credit note credits, found or not, and a credit larger than it
* VENDOR_BANK_CHANGED (warning): the bank details printed for payment differ from the ones on this vendor's approved
  invoices, the most common payment fraud (a fake "our banking details have changed")
"""

from __future__ import annotations

import datetime as dt
import re
import statistics
from typing import TYPE_CHECKING, Any

from ..core.headers import header_key, match_columns
from ..core.memory import vendor_key

if TYPE_CHECKING:
    from ..core.schema import InvoiceCoding
    from ..core.store import Store

ERROR, WARNING, INFO = "error", "warning", "info"
ON_HOLD, ACTIVE = "on_hold", "active"
# vendors.in_master: 0 a vendor AP only set controls for, 1 in the vendor master imported from the ERP, 2 added to
# the vendor list from the review screen (a new vendor: set up in the ERP before it is paid).
IN_ERP, ADDED_HERE = 1, 2
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


_QST_FILE = re.compile(r"(\d{10})(?:tq\d{0,4})?")


def same_qst_number(a: Any, b: Any) -> bool:
    """Whether two QST numbers are the same registration: like ``same_tax_number``, a vendor list keeping only the
    10 digits ("1234567890") matches the invoice's full number ("1234567890 TQ 0001"); two full ones must match."""
    a, b = norm_tax_number(a), norm_tax_number(b)
    if not a or not b:
        return False
    if a == b:
        return True
    file_a, file_b = _QST_FILE.fullmatch(a), _QST_FILE.fullmatch(b)
    if not file_a or not file_b or (len(a) == 16 and len(b) == 16):
        return False
    return file_a.group(1) == file_b.group(1)


def master_vendor_id(ids: dict[str, str], vendor_name: str | None, gst_number: Any = None) -> str:
    """The vendor master's ERP vendor ID for an invoice (``ids``: ``Store.vendor_ids()``): by its GST/HST number
    however much of it either side keeps (``same_tax_number``: the master's 9-digit Business Number for the invoice's
    full account, or the master's full account for 9 digits), else by name. The number first: two suppliers whose
    names read the same ("… Inc." and "… Limited") have their own numbers (B-930). "" when the master doesn't know it.
    The supplier key (``Store.supplier_key_for``) and the export files both use this, so they agree."""
    if gst_number:
        number = norm_tax_number(gst_number)
        bn = number[:9] if number[:9].isdigit() else ""
        found = ids.get(f"gst:{number}") or (
            bn and (ids.get(f"bn:{bn}") or (ids.get(f"bnx:{bn}") if len(number) == 9 else None))
        )
        if found:
            return found
    return ids.get(vendor_key(vendor_name or "")) or ""


_ACCOUNT_LABEL = re.compile(r"(?i)\b(?:account|acct|acc|a/c|compte|cpte)\b\D{0,12}?(\d[\d\s-]*\d|\d)")


def bank_digits(value: Any) -> tuple[str, tuple[str, ...]]:
    """(every digit in order, the sorted numbers without leading zeros): the same account written
    "004-12345-1234567", "Transit 12345, Institution 004, Account 1234567" or "inst 4 transit 12345 ..."
    compares equal on one of them."""
    runs = re.findall(r"\d+", str(value or ""))
    return "".join(runs), tuple(sorted(r.lstrip("0") or "0" for r in runs))


_MONEY = re.compile(r"\d[\d,. ]*[.,]\d{2}(?!\d)")
# Where the banking details end: a number after its own label (an IBAN or SWIFT code after theirs), and the words that
# start something else on the same line ("..., quoting invoice NW-2026-0912", "ref. 88231", "Call 416-555-1212").
_BANK_NUMBER = re.compile(
    r"(?i)\biban\b\W{0,3}[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?\b"
    r"|\b(?:swift|bic)\b\W{0,3}[A-Z]{6}[A-Z0-9]{2}(?:[A-Z0-9]{3})?\b"
    r"|\b(?:transit|institution|inst|branch|account|acct|a/c|compte|routing|aba)\b\D{0,15}?\d(?:[\d -]*\d)?"
)
_BANK_END = re.compile(
    r"(?i)\b(?:quot(?:e|ing)|invoices?|inv|facture|ref|reference|r[ée]f[ée]rence|date|order|po|questions?|call|"
    r"phone|tel|t[ée]l[ée]phone|fax|e-?mail|courriel)\b"
)


def bank_digit_count(text: str) -> int:
    """How many digits bank details hold, amounts of money not counted."""
    return sum(c.isdigit() for c in _MONEY.sub(" ", text))


def cut_bank_details(details: str) -> str:
    """Bank details (from their first banking word) up to the end of the account: up to the first word of something
    else once the account's digits are in (the invoice number every invoice quotes would make each one a changed
    account), then up to the last labelled number."""
    end = next((m.start() for m in _BANK_END.finditer(details) if bank_digit_count(details[: m.start()]) >= 5), None)
    details = details[:end]
    numbers = list(_BANK_NUMBER.finditer(details))
    return (details[: numbers[-1].end()] if numbers else details).rstrip(" ,;:.(-–")


def _same_digits(a: Any, b: Any) -> bool:
    (digits_a, runs_a), (digits_b, runs_b) = bank_digits(a), bank_digits(b)
    return bool(digits_a) and (digits_a == digits_b or runs_a == runs_b)


def same_bank_account(a: Any, b: Any) -> bool:
    """One account however written. Invoices approved before B-176 stored the whole banking line ("... Account
    1234567. Questions? Call 416-555-1212"): both sides are also compared cut at the account, as they are read now."""
    if _same_digits(a, b):
        return True
    cut_a, cut_b = ("; ".join(cut_bank_details(part) for part in str(v or "").split("; ")) for v in (a, b))
    return _same_digits(cut_a, cut_b)


def mask_account(value: Any) -> str:
    """ "…4567": the last 4 digits of the account number (the number after "Account", else the end of the
    details), never the whole account."""
    text = str(value or "")
    labelled = _ACCOUNT_LABEL.search(text)
    digits = re.sub(r"\D", "", labelled.group(1)) if labelled else "".join(re.findall(r"\d+", text))
    return f"…{digits[-4:]}" if digits else "(none)"


def bank_branch(value: Any) -> str:
    """The bank details' other numbers, as printed ("00012 003": transit, institution or the like), never the
    account's digits: the numbers outside the one after "Account", else every number but the last. "" when none."""
    text = str(value or "")
    labelled = _ACCOUNT_LABEL.search(text)
    if labelled:
        return " ".join(re.findall(r"\d+", f"{text[: labelled.start(1)]} {text[labelled.end(1) :]}"))
    return " ".join(re.findall(r"\d+", text)[:-1])


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


def _by(vendor: dict[str, Any]) -> str:
    """ " by Jane on 2026-10-16" from the note a vendor added on the review screen keeps."""
    match = re.match(r"Added by (.+?) on (\d{4}-\d{2}-\d{2})", str(vendor.get("notes") or ""))
    return f" by {match.group(1)} on {match.group(2)}" if match else ""


def norm_pst_number(value: Any) -> str:
    """A PST/RST number by its digits: "PST-1234-5678" and "1234 5678" are the same."""
    return re.sub(r"\D", "", str(value or ""))


# (severity, check code, invoice field, vendor list column, the history key from ``Store.vendor_invoices``, name,
# normaliser, same?) for each tax registration number an invoice prints.
_TAX_NUMBERS = (
    (WARNING, "VENDOR_TAX_NUMBER_CHANGED", "gst_hst_registration_number", "expected_gst", "gst_hst_number",
     "GST/HST number", norm_tax_number, same_tax_number),
    (WARNING, "VENDOR_QST_CHANGED", "qst_registration_number", "qst_number", "qst_number", "QST number",
     norm_tax_number, same_qst_number),
    (WARNING, "VENDOR_PST_CHANGED", "pst_registration_number", "pst_number", "pst_number", "PST number",
     norm_pst_number, lambda a, b: norm_pst_number(a) == norm_pst_number(b)),
)  # fmt: skip


def tax_number_findings(
    coding: InvoiceCoding, master: dict[str, Any] | None, history: list[dict[str, Any]]
) -> list[tuple[str, str, str]]:
    """Each tax number printed on the invoice (GST/HST, QST, PST) against the vendor's: the one in the vendor list
    when it has one, else the ones on this vendor's approved invoices. A different number is a classic sign of a
    fake invoice or a bill from another company under a known name."""
    out = []
    for severity, code, field, column, past, name, norm, same in _TAX_NUMBERS:
        printed = str(getattr(coding, field, "") or "")
        if not norm(printed):
            continue
        if master and norm(master.get(column)):
            known, where = {master[column]}, "the vendor list's"
        else:
            known = {h.get(past) or "" for h in history if h["status"] == "approved"} - {""}
            where = "this vendor's usual"
        if known and not any(same(printed, k) for k in known):
            out.append((severity, code, f"{name} {printed} differs from {where} ({', '.join(sorted(known))}); "
                        "confirm with the vendor before paying"))  # fmt: skip
    return out


def vendor_number_problems(vendor: dict[str, Any]) -> list[str]:
    """What is wrong with the tax numbers the vendor list holds for a vendor (the Vendors page warns, D-65): a GST/HST
    number not in the format 123456789 RT0001 (a 9-digit Business Number alone is fine) or failing the CRA's check
    digit, a QST number not in the format 1234567890 TQ0001. The review screen fills these numbers in."""
    from .tax import gst_check_digit_ok, normalise_registration, valid_gst_number, valid_qst_number

    out = []
    gst = str(vendor.get("expected_gst") or "").strip()
    if gst:
        if not (valid_gst_number(gst) or re.fullmatch(r"\d{9}", normalise_registration(gst))):
            out.append(f"GST/HST number {gst} is not in the format 123456789 RT0001")
        elif not gst_check_digit_ok(gst):
            out.append(f"GST/HST number {gst} fails the CRA check digit: mistyped, or not a real number")
    qst = str(vendor.get("qst_number") or "").strip()
    if qst and not valid_qst_number(qst):
        out.append(f"QST number {qst} is not in the format 1234567890 TQ0001")
    return out


def expected_tax_numbers(coding: InvoiceCoding, store: Store, exclude_invoice_id: int | None = None) -> dict[str, str]:
    """{invoice field: what it is compared with} for the tax numbers ``tax_number_findings`` checks, in a few words
    for the note under the field on the review screen (D-65): "Vendor list: 123456789 RT0001", else "Usual for this
    vendor: …" from its approved invoices."""
    key = store.vendor_key_for(coding.vendor_name, getattr(coding, "gst_hst_registration_number", None))
    if not key:
        return {}
    master = store.get_vendor(key)
    history = [h for h in store.vendor_invoices(key) if h["id"] != exclude_invoice_id]
    out = {}
    for _severity, _code, field, column, past, _name, norm, _same in _TAX_NUMBERS:
        if master and norm(master.get(column)):
            out[field] = f"Vendor list: {master[column]}"
            continue
        known = sorted({h.get(past) or "" for h in history if h["status"] == "approved"} - {""})
        if known:
            out[field] = f"Usual for this vendor: {', '.join(known)}"
    return out


def vendor_findings(
    coding: InvoiceCoding, store: Store, exclude_invoice_id: int | None = None
) -> list[tuple[str, str, str]]:
    """(severity, code, message) for the vendor-level checks on one invoice."""
    key = store.vendor_key_for(
        coding.vendor_name, getattr(coding, "gst_hst_registration_number", None)
    )  # its own of two (B-1021)
    if not key:
        return []
    findings: list[tuple[str, str, str]] = []
    # The vendor's own row (a hold AP put on the name) and the master vendor it is matched to (by name, or by its
    # GST/HST number below): each carries its own controls, so both are checked for a hold.
    own = master = store.get_vendor(key)
    if master and master.get("in_master") == ADDED_HERE:
        findings.append(
            (ERROR, "VENDOR_ADDED_HERE",
             f"a new vendor, added to the vendor list from the review screen{_by(master)}: set it up in JD Edwards "
             "(and verify it by phone) before it is paid")
        )  # fmt: skip
    elif store.has_vendor_master() and not (master and master.get("in_master")):
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
                (ERROR, "VENDOR_NOT_IN_MASTER",
                 "this vendor is not in the vendor list imported from the ERP: pick it from the list, or add it as a "
                 "new vendor")
            )  # fmt: skip
    in_master = bool(master and master.get("in_master") == IN_ERP)
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
    held = [v for v in (own, master) if v and v.get("status") == ON_HOLD]
    if held:
        notes = list(dict.fromkeys(v["notes"] for v in held if v.get("notes")))
        note = f": {'; '.join(notes)}" if notes else ""
        findings.append((ERROR, "VENDOR_ON_HOLD", f"this vendor is on hold in the vendor list{note}"))

    history = [h for h in store.vendor_invoices(key) if h["id"] != exclude_invoice_id]
    findings += _credit_note_findings(coding, store, history)
    # No invoice under this name approved yet (its first, or the others still wait): a person confirms it is a known
    # supplier, even when the GST/HST number printed is a touchless supplier's. The ERP's vendor master vouches for
    # a vendor in it.
    approved = any(h["status"] in ("approved", "pending_approval") for h in history)
    if not in_master and not approved and store.has_other_vendors(key):
        findings.append((INFO, "VENDOR_NEW", "first invoice from this vendor: confirm it is a known supplier"
                         if not history else "no invoice from this vendor approved yet: confirm it is a known "
                         "supplier"))  # fmt: skip
    findings += tax_number_findings(coding, master, history)
    if not history:
        return findings

    # The bank details printed for payment, compared with the ones on this vendor's approved invoices.
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
             f"the bank details printed for payment ({shown}) differ from the ones on this vendor's "
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
    "vendor_name": ("vendorname", "vendor", "name", "supplier", "suppliername", "fournisseur", "legalname",
                    "alphaname"),  # "Alpha Name" in JD Edwards' address book
    "erp_id": ("vendorid", "vendornumber", "vendorno", "vendorcode", "supplierid", "suppliernumber", "id",
               "number", "no", "code", "accountnumber", "suppliercode", "numero",  # "No." in Business Central
               "addressnumber", "an8", "suppliernumberan8"),  # JD Edwards' supplier number (B-929)
    "gst": ("gsthstnumber", "gstnumber", "hstnumber", "gsthst", "gst", "businessnumber", "bn", "taxnumber",
            "tps", "numerotps", "vatregistrationno", "taxregistrationno", "gstregistrationno",
            "gsthstregistrationno"),
    "qst": ("qstnumber", "qst", "tvq", "numerotvq", "qstregistrationno", "qstregistrationnumber"),
    "pst": ("pstnumber", "pst", "rstnumber", "rst", "tvp", "pstregistrationno", "pstregistrationnumber",
            "provincialtaxnumber"),
    "province": ("province", "prov", "state", "region", "provincestate"),
    "terms": ("paymentterms", "terms", "termes", "conditions", "paymenttermscode", "termscode"),
    "status": ("status", "active", "blocked", "hold", "statut"),
    "default_gl": ("defaultgl", "glaccount", "gl", "glcode", "expenseaccount", "account", "defaultaccount"),
}  # fmt: skip
_HOLD_WORDS = {"hold", "on hold", "on_hold", "blocked", "inactive", "suspended", "closed", "disabled", "i", "h", "b",
               "bloqué", "bloque", "inactif", "suspendu"}  # fmt: skip


def _province_or_blank(value: str) -> str:
    """A province code from the vendor master's column ("British Columbia", "BC", "Qc"), "" when it is not one."""
    from ..core.schema import province_code

    try:
        return province_code(value) if value else ""
    except ValueError:
        return ""


def _norm_header(value: Any) -> str:
    return header_key(value)  # accents folded first: "Reçu", "Numéro" (B-657)


def master_columns(headers: list[str]) -> dict[str, str]:
    return match_columns(headers, MASTER_ALIASES)


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
               "status_given": bool(status), "qst": get("qst"), "pst": get("pst"),
               "province": _province_or_blank(get("province"))}  # fmt: skip
        for field in ("erp_id", "terms", "default_gl"):  # None: not in this file, so the vendor keeps its value
            row[field] = get(field) if field in columns else None
        rows.append(row)
    return rows
