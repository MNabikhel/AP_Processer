"""The invoice details of the review screen (its header fields): the note under each, and the dates and currency typed.

* The note under a detail a check is about (``field_flags``, ``code_flags``, which are errors: ``flag_errors``;
  ``merge_flags`` says each once), a tax number filled from the vendor list that the invoice does not print
  (``not_printed``), and a field the coding can't be built from, in plain words (``plain_problem``).
* The dates and the currency as typed, settled before the coding is built (``settle_dates``, ``DateCheck``,
  ``currency_issues``).
* A one-click fix under an empty or unclear field (``field_help``) and the Pay from list (``pay_from_choices``).

The fields' names and what is posted are ``invoice_form.py``'s; the findings' words, ``invoice_findings.py``'s.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any

from ap_coder.capture.normalize import parse_dates
from ap_coder.capture.types import DATE_FIELDS, MISSING, CaptureResult
from ap_coder.coding import banks
from ap_coder.coding.po import po_label
from ap_coder.coding.validation import ERROR, INFO, Issue
from ap_coder.coding.vendors import expected_tax_numbers
from ap_coder.core.memory import vendor_key
from ap_coder.core.schema import InvoiceCoding
from ap_coder.core.store import Store
from ap_coder.web.invoice_findings import CODE_FIELDS, FIELD_FLAGS, readable
from ap_coder.web.invoice_form import FIELD_LABELS, TEACHABLE, Draft, supplier_by_number

# --- The note under a detail --------------------------------------------------------------------------------------

FROM_VENDOR_NOTE = "From the vendor list — not printed on the invoice"


def field_flags(
    issues: list[Any], store: Store, coding: InvoiceCoding, invoice_id: int | None = None
) -> dict[str, str]:
    """{invoice detail: the note under it} for the checks about one detail (``FIELD_FLAGS``): the screen highlights the
    detail in amber, with the note, as well as listing the check. The tax numbers' note says what the vendor list (or
    this vendor's approved invoices) has."""
    flags: dict[str, list[str]] = {}
    expected: dict[str, str] | None = None
    for issue in issues:
        if issue.code not in FIELD_FLAGS or issue.severity == "info":
            continue
        name, note = FIELD_FLAGS[issue.code]
        if note is None:
            if expected is None:
                expected = expected_tax_numbers(coding, store, invoice_id)
            note = expected.get(name) or "Differs from this vendor's"
        if note not in flags.setdefault(name, []):
            flags[name].append(note)
    return {name: " · ".join(notes) for name, notes in flags.items()}


# What the note under a field says for a check whose own message names the field already.
_FIELD_PREFIX = re.compile(r"^(?:JD Edwards: )?(?:Invoice date|Due date|Currency): ")


def code_flags(issues: list[Any]) -> dict[str, str]:
    """{field: note}: the checks about one invoice detail (``CODE_FIELDS``), in their own words, for under the field."""
    flags: dict[str, list[str]] = {}
    for issue in issues:
        name = CODE_FIELDS.get(issue.code)
        if not name or issue.severity == "info" or issue.line_number:
            continue
        note = _FIELD_PREFIX.sub("", readable(issue.message))
        note = note[:1].upper() + note[1:]
        if note not in flags.setdefault(name, []):
            flags[name].append(note)
    return {name: " · ".join(notes) for name, notes in flags.items()}


def flag_errors(errors: list[Any], date_notes: dict[str, str]) -> list[str]:
    """The invoice details whose note under them is an error: red there as in "Must be fixed", not amber (B-796)."""
    out = set(date_notes)
    for issue in errors:
        if issue.line_number:
            continue
        name = CODE_FIELDS.get(issue.code) or FIELD_FLAGS.get(issue.code, (None, None))[0]
        if name:
            out.add(name)
    return sorted(out)


def merge_flags(*sets: dict[str, str]) -> dict[str, str]:
    """The notes under each field, from several sources, each said once."""
    out: dict[str, list[str]] = {}
    for flags in sets:
        for name, note in flags.items():
            for part in note.split(" · "):
                if part and part not in out.setdefault(name, []):
                    out[name].append(part)
    return {name: " · ".join(notes) for name, notes in out.items() if notes}


def not_printed(coding: InvoiceCoding, from_list: list[str]) -> list[Any]:
    """The information that a tax number filled from the vendor list is not printed on the invoice, when the invoice
    charges that tax and is at or over the documentary threshold: from $100 (CRA, Input Tax Credit Information
    (GST/HST) Regulations; Revenu Québec the same for the QST), the supplier's registration number must be on the
    invoice to claim the input tax credit (refund). The vendor list's number is no substitute (D-65)."""
    from ap_coder.coding.taxreturn import document_threshold

    charged = {t.tax_type for t in coding.tax_lines if abs(t.tax_amount) > 0.004}
    threshold = document_threshold(coding.invoice_date)
    if abs(coding.grand_total) < threshold:
        return []
    out = []
    amount = f"${threshold:,.0f}"
    if "gst_hst_registration_number" in from_list and charged & {"GST", "HST"}:
        out.append(
            Issue(
                INFO,
                "GST_HST_NUMBER_NOT_PRINTED",
                "The GST/HST number isn't printed on this invoice: CRA "
                f"needs it on the invoice to claim the input tax credit for purchases of {amount} or more",
            )
        )
    if "qst_registration_number" in from_list and "QST" in charged:
        out.append(
            Issue(
                INFO,
                "QST_NUMBER_NOT_PRINTED",
                "The QST number isn't printed on this invoice: Revenu Québec "
                f"needs it on the invoice to claim the input tax refund for purchases of {amount} or more",
            )
        )
    return out  # fmt: skip


_LOCATIONS = {**FIELD_LABELS, "line_items": "Line", "tax_lines": "Tax line"}


def plain_problem(problem: str) -> str:
    """A field the coding can't be built from, in plain words: "Line 2 amount: …", never the model's "Value error"
    or "line_items.1.amount"."""
    where, _, what = problem.partition(": ")
    if not what:
        return problem
    what = re.sub(r"^(?:Value|Assertion) error,\s*", "", what)
    what = re.sub(r"^Input should be a valid (\w+).*", r"should be a \1", what)
    parts = where.split(".")
    if parts[0] in ("line_items", "tax_lines") and len(parts) >= 2 and parts[1].isdigit():
        rest = " ".join(p.replace("_", " ") for p in parts[2:])
        where = f"{_LOCATIONS[parts[0]]} {int(parts[1]) + 1}" + (f" {rest}" if rest else "")
    else:
        where = _LOCATIONS.get(parts[0], parts[0].replace("_", " ").capitalize())
    return f"{where}: {what}"


# --- Dates and currency as typed ----------------------------------------------------------------------------------

# The currency codes in use (ISO 4217, active): a code not in it is a typo, never a voucher (D-75).
ISO_CURRENCIES = frozenset(
    """
AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BRL BSD BTN BWP BYN BZD CAD CDF CHF CLP
CNY COP CRC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD GNF GTQ GYD HKD HNL HTG HUF IDR
ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD MAD MDL MGA MKD MMK MNT
MOP MRU MUR MVR MWK MXN MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR RON RSD RUB RWF SAR SBD
SCR SDG SEK SGD SHP SLE SOS SRD SSP STN SVC SYP SZL THB TJS TMT TND TOP TRY TTD TWD TZS UAH UGX USD UYU UZS VES VND
VUV WST XAF XCD XOF XPF YER ZAR ZMW ZWL
""".split()
)


def _iso(text: str) -> bool:
    try:
        dt.date.fromisoformat(text)
    except ValueError:
        return False
    return len(text) == 10


def _long_date(d: dt.date) -> str:
    return f"{d.day} {d:%B %Y}"


@dataclass
class DateCheck:
    """The dates typed on the screen, settled before the coding is built: the values the checks use (a date that can't
    be read is never the one approved: its finding is one "Approve anyway" never covers), the findings, a date typed
    another way put as YYYY-MM-DD (``filled``), and the two readings of an ambiguous one (``choices``)."""

    header: dict[str, Any]
    issues: list[Any] = field(default_factory=list)
    filled: dict[str, str] = field(default_factory=dict)
    choices: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)  # {field: the note under it}


def settle_dates(header: dict[str, Any], fallback: dict[str, Any]) -> DateCheck:
    """The invoice and due dates as typed: YYYY-MM-DD as is; another way of writing one date (Oct 5, 2026; 2026/10/05;
    25/10/2026) put as YYYY-MM-DD; 12/06/2026 (6 December or 12 June) asked; anything else said plainly at the field.
    Meanwhile the checks run on the date as read (``fallback``), so a typo never wipes them."""
    out = DateCheck(header=dict(header))
    for name in DATE_FIELDS:
        text = str(header.get(name) or "").strip()
        label = FIELD_LABELS[name]
        if _iso(text) or (name == "due_date" and not text):
            continue
        found = parse_dates(text) if text else []
        whole = [f for f in found if f[1] == 0 and f[2] >= len(text.rstrip(". "))]
        if whole and not whole[0][3]:
            out.filled[name] = out.header[name] = whole[0][0].isoformat()
            continue
        if whole:  # day or month first: both are dates
            a = parse_dates(text, prefer_day_first=True)[0][0]
            b = parse_dates(text, prefer_day_first=False)[0][0]
            out.choices[name] = [{"value": d.isoformat(), "label": _long_date(d)} for d in (a, b)]
            note = f"“{text}” can be {_long_date(a)} or {_long_date(b)}: which is it?"
            out.issues.append(Issue(ERROR, "DATE_AMBIGUOUS", f"{label}: {note}"))
        elif not text:
            note = "No invoice date: type it from the page, e.g. 2026‑10‑05"
            out.issues.append(Issue(ERROR, "INVOICE_DATE_INVALID", note))
        elif name == "invoice_date":
            note = f"“{text}” is not a date: type it as on the page, e.g. 2026‑10‑05"
            out.issues.append(Issue(ERROR, "INVOICE_DATE_INVALID", f"{label}: {note}"))
        else:
            note = f"“{text}” is not a date: type it as on the page (e.g. 2026‑11‑04), or leave it blank"
            out.issues.append(Issue(ERROR, "DUE_DATE_INVALID", f"{label}: {note}"))
        out.notes[name] = note
        # the checks meanwhile: the date as read (or, never read, a placeholder no approval can keep)
        was = str(fallback.get(name) or "").strip()
        out.header[name] = was if _iso(was) else (dt.date.today().isoformat() if name == "invoice_date" else "")
    return out


def currency_issues(currency: str) -> list[Any]:
    """A three-letter currency that is no currency in use (XYZ): flagged, never a voucher (D-75). Not three letters is
    validation's own CURRENCY_FORMAT."""
    code = (currency or "").strip().upper()
    if re.fullmatch(r"[A-Z]{3}", code) and code not in ISO_CURRENCIES:
        return [Issue(ERROR, "CURRENCY_UNKNOWN", f"“{code}” is not a currency code in use: type it as on the page, "
                      "e.g. CAD, USD or EUR")]  # fmt: skip
    return []


# --- Fixes and choices --------------------------------------------------------------------------------------------


def field_help(store: Store, coding: InvoiceCoding, draft: Draft, capture: CaptureResult | None, dates: DateCheck,
               issues: list[Any]) -> dict[str, dict[str, Any]]:  # fmt: skip
    """A note under an empty or unclear field with a one-click fix: which of two dates, a PO this vendor has open
    ("Use PO-89904"), a value boxed on the page while the field is empty ("Use BC-4410 from the page")."""
    out: dict[str, dict[str, Any]] = {}
    for name, choices in dates.choices.items():
        out[name] = {"text": "Which date is it?",
                     "actions": [{"label": c["label"], "value": c["value"]} for c in choices]}  # fmt: skip
    header = draft.header
    # The GST/HST number on the invoice is ONE supplier's, and another is chosen: offered with one click (D-219).
    other = supplier_by_number(store, draft)
    chosen = store.find_vendor(header.get("vendor_name"), header.get("gst_hst_registration_number")) if other else None
    if other and chosen and chosen["vendor_key"] != (store.find_vendor(other) or {}).get("vendor_key"):
        out["vendor_name"] = {
            "text": f"GST/HST number {str(header['gst_hst_registration_number']).strip()} is {other}'s in the "
            "supplier list.",
            "actions": [{"label": f"Use {other}", "value": other}],
        }
    if not str(header.get("po_number") or "").strip() and any(i.code == "PO_NOT_QUOTED" for i in issues):
        numbers = store.open_pos_for_vendor(vendor_key(coding.vendor_name))[:3]
        if numbers:
            out["po_number"] = {
                "text": "No PO on the invoice. This vendor has "
                + ("an open PO:" if len(numbers) == 1 else "open POs:"),
                "actions": [{"label": f"Use {po_label(n)}", "value": n} for n in numbers],
            }
    if capture is not None:
        for name in TEACHABLE:
            if name in out or name in draft.absent or str(header.get(name) or "").strip():
                continue
            fr = capture.fields.get(name)
            value = str(fr.value if fr is not None and fr.value is not None else "").strip()
            if not value or fr.status == MISSING or not fr.boxes:
                continue
            short = value if len(value) <= 24 else f"{value[:23]}…"
            out[name] = {"text": "Boxed on the page, but the field is empty.",
                         "actions": [{"label": f"Use {short} from the page", "value": value}]}  # fmt: skip
    return out


def pay_from_choices(store: Store, header: dict[str, str]) -> dict[str, Any]:
    """The Pay from list (D-66): our bank accounts in use, as "name · currency · ••••1234", the one chosen (an account
    no longer in use stays listed while it is the one chosen), and whether there are any."""
    chosen = header.get("pay_from_account") or ""
    accounts = [a for a in store.bank_accounts() if a["active"] or a["code"] == chosen]
    return {
        "options": [(a["code"], banks.label(a) + ("" if a["active"] else " (not in use — pick another)"), a["currency"])
                    for a in accounts],
        "value": chosen,
        "any": any(a["active"] for a in accounts),
        "unknown": bool(chosen) and all(a["code"] != chosen for a in accounts),
    }  # fmt: skip
