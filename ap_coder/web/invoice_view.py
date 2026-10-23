"""What the invoice review screen shows, worked out from the invoice and the reviewer's edits. No web code here:
``pages/invoice.py`` has the routes, ``templates/invoice.html`` and ``_invoice_*.html`` draw it.

* ``Draft``: what is on the screen (the header fields, the line grid, the tax grid, the words taught on the page),
  from the stored coding (``Draft.stored``) or from the posted form (``Draft.from_form``).
* ``analyse``: rebuilds the coding from a draft (``review.coding_from_inputs``) and runs the checks approval runs
  (``pipeline.finalise_coding``); ``Analysis.live`` holds plain values for the templates: the summary, the checks,
  why these codes, GL suggestions, the PO match, the tax check, the posting preview and the approve bar.
* The grid actions (split a line, the coding rules, the PO's coding, a suggested GL account) return new grid rows.

The store calls and the rules for what may be approved and what is learned are the store's and the pipeline's own.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from ap_coder.ai.extraction import ExtractionResult
from ap_coder.ai.history import HISTORY
from ap_coder.ai.pipeline import finalise_coding
from ap_coder.ai.suggest import suggest_gl
from ap_coder.capture.normalize import parse_amount, parse_date
from ap_coder.capture.types import MISSING, Box, CaptureResult
from ap_coder.coding import rules, vendor_mail
from ap_coder.coding.line_tax import (
    all_labels,
    charged_types,
    exemption_to_confirm,
    fill_exempt,
    legacy_basis,
    parse_code,
    tax_code_of,
    tax_guessed,
    tax_learned,
)
from ap_coder.coding.line_tax import options as tax_code_options
from ap_coder.coding.po import match_invoice, po_label
from ap_coder.coding.review import (
    codes_to_pick,
    coding_from_inputs,
    is_blank,
    split_line,
    to_number,
    uses_cost_centers,
)
from ap_coder.coding.tax import (
    OUTSIDE_CANADA,
    PLACE_OF_SUPPLY,
    PROVINCE_NAMES,
    TAX_TYPES,
    place_of_supply,
    province_label,
    stored_supply,
)
from ap_coder.coding.taxmath import math_check  # noqa: F401 - the review screen's math, shown with the tax lines
from ap_coder.coding.terms import describe as terms_describe
from ap_coder.coding.terms import payment
from ap_coder.core.config import Settings
from ap_coder.core.memory import ACCEPTED, CODED, CORRECTED, pair_lines, vendor_key
from ap_coder.core.reference_data import UNASSIGNED, ReferenceData, short_name
from ap_coder.core.schema import FROM_VENDOR_LIST, PROVINCE_VALUES, InvoiceCoding
from ap_coder.core.store import Store, nothing_proposed, suggestion_kept
from ap_coder.core.ui import TAX_LABELS, plural
from ap_coder.ops.help import help_for
from ap_coder.web.document import clean_boxes

# --- The form ------------------------------------------------------------------------------------------------------

# Capture fields a reviewer can teach by clicking the words on the page: they are fields of the form (same names).
TEACHABLE = (
    "vendor_name", "invoice_number", "invoice_date", "due_date", "po_number", "gst_hst_registration_number",
    "qst_registration_number", "pst_registration_number", "payment_terms", "currency", "subtotal", "tax_total",
    "grand_total",
)  # fmt: skip
AMOUNT_FIELDS = ("subtotal", "tax_total", "grand_total")
# remit_bank_account: the vendor's banking details as read, shown read-only (evidence for the fraud check, D-66);
# pay_from_account: our bank account the invoice is paid from (coding/banks.py).
FORM_FIELDS = (*TEACHABLE, "supplier_province", "ship_to_province", "remit_bank_account", "original_invoice_number",
               "pay_from_account")  # fmt: skip
# Kept as read whatever is posted: the vendor's printed bank details are evidence for the fraud check, shown read-only
# and never taught on the page (not in TEACHABLE) (D-66, B-686).
READ_ONLY = frozenset({"remit_bank_account"})
FIELD_LABELS = {
    "vendor_name": "Vendor", "gst_hst_registration_number": "GST/HST #", "qst_registration_number": "QST #",
    "pst_registration_number": "PST #", "supplier_province": "Supplier province",
    "remit_bank_account": "Bank details printed on the invoice", "pay_from_account": "Pay from",
    "invoice_number": "Invoice #", "invoice_date": "Invoice date", "po_number": "PO #",
    "payment_terms": "Payment terms", "due_date": "Due date", "currency": "Currency",
    "ship_to_province": "Place of supply", "original_invoice_number": "Credits invoice #", "subtotal": "Subtotal",
    "tax_total": "Tax total", "grand_total": "Total",
}  # fmt: skip
LINE_COLUMNS = ("line_number", "description", "quantity", "unit_price", "amount", "predicted_gl_code",
                "predicted_cost_center", "taxes_applied", "tax_exempt", "tax_status",
                "reasoning_justification")  # fmt: skip
LINE_TAX_CODE = "line_tax_code"  # a line's tax code, one dropdown a row (D-64)
TAX_COLUMNS = ("tax_type", "province", "rate_pct", "taxable_amount", "tax_amount")
# The posted names of a grid row's cells (repeated once per row, in order).
LINE_INPUTS = {"line_number": "line_number", "description": "line_description", "amount": "line_amount",
               "predicted_gl_code": "line_gl", "predicted_cost_center": "line_cc", "quantity": "line_quantity",
               "unit_price": "line_unit_price", "reasoning_justification": "line_reason"}  # fmt: skip
TAX_INPUTS = {"tax_type": "tax_type", "province": "tax_province", "rate_pct": "tax_rate_pct",
              "taxable_amount": "tax_taxable", "tax_amount": "tax_amount"}  # fmt: skip
NO_PROVINCE = "—"
NEEDS_GL = "Needs an account"  # what a clerk sees instead of the stored UNASSIGNED code
NEEDS_CC = "Needs a cost center"
NOT_TOUCHLESS = "It can't be approved without a person until OvisOCR2 has read it."
EDIT_LABELS = {
    "line_coding": "GL coding", "line_coded": "lines the AI left blank coded by AP",
    "line_count": "lines added or removed", "tax_lines": "sales tax",
    "vendor_name": "vendor", "invoice_number": "invoice #", "invoice_date": "date", "po_number": "PO #",
    "payment_terms": "terms", "due_date": "due date", "currency": "currency",
    "supplier_province": "supplier province", "ship_to_province": "place of supply",
    "gst_hst_registration_number": "GST/HST #", "qst_registration_number": "QST #", "pst_registration_number": "PST #",
    "original_invoice_number": "invoice credited", "remit_bank_account": "bank account", "subtotal": "subtotal",
    "tax_total": "tax total", "grand_total": "total",
}  # fmt: skip
# Fields the reviewer can mark "Not on this invoice" (the review screen's right-click menu, D-60): optional fields
# that an invoice may simply not print. The check that would ask for one is then said once, as information, or not at
# all (None). The vendor, the invoice number, the date, the currency and the amounts are needed: never absent.
ABSENT_FIELDS = {
    "gst_hst_registration_number": {"GST_HST_NUMBER_MISSING": (
        "GST_HST_NUMBER_ABSENT", "you marked the GST/HST number as not on this invoice: no input tax credit can be "
        "claimed without it")},
    "qst_registration_number": {"QST_NUMBER_MISSING": (
        "QST_NUMBER_ABSENT", "you marked the QST number as not on this invoice: no input tax refund can be claimed "
        "without it")},
    "pst_registration_number": {},
    "po_number": {"PO_NOT_QUOTED": None},
    "payment_terms": {},
    "due_date": {},
    "original_invoice_number": {},
}  # fmt: skip
# The tax registration numbers a vendor in the vendor list fills when they are blank (D-65): {field: vendor column}.
TAX_NUMBER_FIELDS = {"gst_hst_registration_number": "expected_gst", "qst_registration_number": "qst_number",
                     "pst_registration_number": "pst_number"}  # fmt: skip
FROM_VENDOR_NOTE = "From the vendor list — not printed on the invoice"
# The checks that are about one invoice detail: the detail is highlighted in amber with a short note under it (the
# checks panel lists them too). {check: (field, note)}; a note of None is worked out (``field_flags``).
FIELD_FLAGS: dict[str, tuple[str, str | None]] = {
    "VENDOR_TAX_NUMBER_CHANGED": ("gst_hst_registration_number", None),
    "GST_NUMBER_CHECK_DIGIT": ("gst_hst_registration_number", "Fails the CRA check digit"),
    "GST_HST_NUMBER_FORMAT": ("gst_hst_registration_number", "Not in the format 123456789 RT0001"),
    "VENDOR_QST_CHANGED": ("qst_registration_number", None),
    "QST_NUMBER_FORMAT": ("qst_registration_number", "Not in the format 1234567890 TQ0001"),
    "VENDOR_PST_CHANGED": ("pst_registration_number", None),
    "VENDOR_BANK_CHANGED": ("remit_bank_account", "Not the account on this vendor's approved invoices: confirm by "
                            "phone before paying"),
    "PAY_FROM_CURRENCY": ("pay_from_account", "Not an account in the invoice's currency"),
    "PAY_FROM_MISSING": ("pay_from_account", "Pick the account that pays this invoice"),
    "PAY_FROM_UNKNOWN": ("pay_from_account", "Not one of your bank accounts in use"),
}  # fmt: skip
# How sure the capture is of a field, beside its label in the form: (tone, icon).
CAPTURE_BADGES = {"verified": ("ok", "check_circle"), "likely": ("info", "check"), "check": ("warn", "error"),
                  "failed": ("err", "cancel")}  # fmt: skip
# ... in the words of the page's colour key (static/js/invoice_viewer.js, STATUS_HELP), with what each means (B-435).
CAPTURE_WORDS = {"verified": "Verified", "likely": "Likely", "check": "Check", "failed": "Failed check"}
CAPTURE_HELP = {
    "verified": "Verified: the readers agree and the checks pass.",
    "likely": "Likely: one strong reading and the checks pass. A quick look.",
    "check": "Check: the readers disagree or are unsure. Look at it on the page.",
    "failed": "Failed check: a check on this value failed. Look at it on the page.",
}


def signed(value: Any) -> str:
    """An amount as the review screen shows it, a credit with a minus sign as its boxes show it: -2,050.00 (the other
    pages' (2,050.00) beside a box holding -2050.00 read as two different amounts)."""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"-{-amount:,.2f}" if amount < -0.004 else f"{abs(amount) if amount > -0.005 else 0.0:,.2f}"


def _text(value: Any) -> str:
    return "" if is_blank(value) else str(value)


def _amount_text(value: Any) -> str:
    try:
        return f"{float(value or 0):.2f}"
    except (TypeError, ValueError):
        return str(value)


NUMBER_CELLS = ("amount", "quantity", "unit_price", "rate_pct", "taxable_amount", "tax_amount")


def _number_cell(value: Any) -> str:
    """A number cell of the grids as posted, or "" when it is not a finite number ("1e309" reads as infinity),
    which the checks and the page could not use."""
    text = str(value or "").strip()
    try:
        number = float(text.replace(",", "").replace("$", ""))
    except ValueError:
        return ""
    return text if math.isfinite(number) else ""


def _line_number(value: Any) -> int | None:
    if is_blank(value):
        return None
    try:
        number = float(str(value).strip())
    except ValueError:
        return None
    return int(number) if math.isfinite(number) else None


@dataclass
class Draft:
    """What is on the review screen: text as typed (numbers too), the grids as lists of rows."""

    header: dict[str, str]
    lines: list[dict[str, Any]]
    taxes: list[dict[str, Any]]
    taught: dict[str, list[list[float]]] = field(default_factory=dict)  # {field: [[page, x0, y0, x1, y1], ...]}
    override: str = ""  # "approve anyway, despite the errors", ticked for these errors
    override_reason: str = ""  # why, typed with it (D-75)
    reader_at: str = ""  # when OvisOCR2's reading came in, as the screen was drawn ("" before it)
    mail_lang: str = ""
    absent: list[str] = field(default_factory=list)  # fields marked "Not on this invoice" (ABSENT_FIELDS), blank
    # The tax numbers filled from the vendor list (D-65): {"vendor": the vendor key they were filled for, "fields":
    # {field: number filled}}; {} before any fill was tried.
    from_vendor: dict[str, Any] = field(default_factory=dict)
    # The currency the reviewer picked the Pay from account for ("" while it is the default, which follows the
    # invoice's currency and vendor: D-66).
    pay_from_for: str = ""

    @classmethod
    def stored(cls, start: dict[str, Any], reader_at: str = "", approved: bool = False) -> Draft:
        """The screen as it opens: the stored coding (a sent-back invoice: its first approver's version, ``approved``:
        its Pay from account is the one approved, kept)."""
        header = {}
        for name in FORM_FIELDS:
            value = start.get(name, "CAD" if name == "currency" else "")
            header[name] = _amount_text(value) if name in AMOUNT_FIELDS else _text(value)
        lines = [{c: li.get(c) for c in LINE_COLUMNS} for li in start.get("line_items") or []]
        for li in lines:
            li["taxes_applied"] = list(li.get("taxes_applied") or [])
            li["tax_status"] = str(li.get("tax_status") or "")
        # an invoice stored before a line said which taxes it is exempt from: read as the vendor meant it (D-64)
        lines = fill_exempt(lines, start.get("tax_lines") or [])
        taxes = []
        for t in start.get("tax_lines") or []:
            try:
                rate_pct = round(float(t.get("rate") or 0) * 100, 4)
            except (TypeError, ValueError):
                rate_pct = None
            taxes.append({"tax_type": t.get("tax_type") or "", "province": t.get("province") or "",
                          "rate_pct": rate_pct, "taxable_amount": t.get("taxable_amount"),
                          "tax_amount": t.get("tax_amount")})  # fmt: skip
        filled = [n for n in start.get(FROM_VENDOR_LIST) or [] if n in TAX_NUMBER_FIELDS and header.get(n)]
        from_vendor = (
            {"vendor": vendor_key(header["vendor_name"]), "fields": {n: header[n] for n in filled}} if filled else {}
        )
        pay_from_for = (header["currency"] or "CAD") if approved and header.get("pay_from_account") else ""
        return cls(header=header, lines=lines, taxes=taxes, reader_at=reader_at, from_vendor=from_vendor,
                   pay_from_for=pay_from_for)  # fmt: skip

    @classmethod
    def from_form(cls, form: Any, base: Draft) -> Draft:
        """The screen as posted. A header field missing from the form keeps ``base``'s value; the grids are taken
        from the form when it says it has them (``grids``), so deleting the last row is an edit too."""
        header = {name: str(form.get(name)) if form.get(name) is not None and name not in READ_ONLY
                  else base.header.get(name, "") for name in FORM_FIELDS}  # fmt: skip
        lines, taxes = base.lines, base.taxes
        if form.get("grids"):
            keys = form.getlist("line_key")
            cols = {c: form.getlist(name) for c, name in LINE_INPUTS.items()}
            codes = form.getlist(LINE_TAX_CODE)
            lines = []
            for i, key in enumerate(keys):
                row: dict[str, Any] = {c: (cols[c][i] if i < len(cols[c]) else "") for c in LINE_INPUTS}
                row["line_number"] = _line_number(row["line_number"])
                row.update({c: _number_cell(row[c]) for c in NUMBER_CELLS if c in row})
                # The line's tax code (one dropdown a row, posted in order: "GST+PST", "EXEMPT", D-64); a form of
                # before has the tax boxes, named line_tax_<key> (B-620), and no status.
                code = codes[i] if i < len(codes) else None
                parsed = parse_code(code) if code is not None else None
                if parsed is not None:
                    row["taxes_applied"], row["tax_exempt"], row["tax_status"] = parsed
                else:
                    row["taxes_applied"] = [t for t in form.getlist(f"line_tax_{key}") if t in TAX_TYPES]
                    row["tax_exempt"], row["tax_status"] = None, ""  # read as the vendor meant it
                lines.append(row)
            tcols = {c: form.getlist(name) for c, name in TAX_INPUTS.items()}
            taxes = [{c: (tcols[c][i] if i < len(tcols[c]) else "") for c in TAX_INPUTS}
                     for i in range(len(tcols["tax_type"]))]  # fmt: skip
            for t in taxes:
                t.update({c: _number_cell(t[c]) for c in NUMBER_CELLS if c in t})
        absent = parse_absent(form.get("absent"), header) if form.get("absent") is not None else base.absent
        from_vendor = (
            parse_from_vendor(form.get("from_vendor")) if form.get("from_vendor") is not None else base.from_vendor
        )
        pay_from_for = (
            str(form.get("pay_from_for") or "") if form.get("pay_from_for") is not None else base.pay_from_for
        )
        return cls(header=header, lines=lines, taxes=taxes, taught=parse_taught(form.get("taught")),
                   override=str(form.get("override") or ""),
                   override_reason=" ".join(str(form.get("override_reason") or "").split())[:300],
                   reader_at=str(form.get("reader_at") or base.reader_at),
                   mail_lang=str(form.get("mail_lang") or ""), absent=absent, from_vendor=from_vendor,
                   pay_from_for=pay_from_for.strip().upper()[:3])  # fmt: skip

    def to_dict(self) -> dict[str, Any]:
        return {"header": self.header, "lines": self.lines, "taxes": self.taxes, "taught": self.taught,
                "reader_at": self.reader_at, "absent": self.absent, "from_vendor": self.from_vendor,
                "pay_from_for": self.pay_from_for}  # fmt: skip


def parse_from_vendor(raw: Any) -> dict[str, Any]:
    """The form's ``from_vendor`` JSON ({"vendor": key, "fields": {field: number}}), only the tax number fields."""
    try:
        data = json.loads(raw) if isinstance(raw, str) and raw.strip() else {}
    except ValueError:
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("vendor"), str):
        return {}
    fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
    return {"vendor": data["vendor"],
            "fields": {str(k): str(v) for k, v in fields.items() if k in TAX_NUMBER_FIELDS and v}}  # fmt: skip


def fill_from_vendor(
    header: dict[str, str],
    state: dict[str, Any],
    absent: list[str],
    vendor: dict[str, Any] | None,
    unread: set[str] | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """The tax numbers a vendor of the vendor list gives a draft (D-65): (the new ``Draft.from_vendor``, {field: value}
    changed in ``header``, which is updated). When the vendor is new to the draft (picked, typed, or the screen just
    opened), each blank GST/HST, QST and PST number takes the vendor list's, and a number filled from the vendor before
    follows the new one (replaced, or cleared when it has none). A number read from the invoice or typed is never
    touched, nor a field marked not on this invoice; a filled number the reviewer changed or cleared since is theirs.
    ``vendor``: the vendor's row (``Store.get_vendor``), None when it is not in the list; ``unread``: the fields the
    reading left blank (not printed), the only ones filled (None: any blank one). A number read and cleared by the
    reviewer is the reviewer's to type: filled from the vendor list, the change would be learned as no correction
    (B-688)."""
    key = vendor_key(header.get("vendor_name") or "")
    fields = {n: v for n, v in (state.get("fields") or {}).items()
              if n in TAX_NUMBER_FIELDS and v and str(header.get(n) or "").strip() == v}  # fmt: skip
    changed: dict[str, str] = {}
    if state.get("vendor") == key and "vendor" in state:
        # A number marked as the vendor list's is this vendor's own, or it follows it (B-682: an Undo that kept the
        # vendor and put back another one's numbers must not leave them labelled as this vendor's).
        for name in list(fields):
            number = str((vendor or {}).get(TAX_NUMBER_FIELDS[name]) or "").strip()
            if number != fields[name]:
                header[name] = changed[name] = number
                if number:
                    fields[name] = number
                else:
                    fields.pop(name)
        return {"vendor": key, "fields": fields}, changed
    for name, column in TAX_NUMBER_FIELDS.items():
        number = str((vendor or {}).get(column) or "").strip()
        current = str(header.get(name) or "").strip()
        if name in fields:  # filled from the vendor before: follows the vendor now in the field
            if number != current:
                header[name] = changed[name] = number
            if number:
                fields[name] = number
            else:
                fields.pop(name)
        elif not current and number and name not in absent and (unread is None or name in unread):
            header[name] = changed[name] = fields[name] = number
    return {"vendor": key, "fields": fields}, changed


def unread_numbers(read: dict[str, Any] | None) -> set[str] | None:
    """The tax number fields the reading (``ai_output``) left blank: the ones the vendor list may fill (B-688)."""
    if read is None:
        return None
    return {n for n in TAX_NUMBER_FIELDS if not str(read.get(n) or "").strip()}


def settle_header(store: Store, draft: Draft, read: dict[str, Any] | None = None) -> dict[str, str]:
    """What the server fills in the invoice details before they are checked: the vendor list's tax numbers (D-65) and
    the default Pay from account (D-66). Returns {field: value} changed, for the screen to show."""
    from ap_coder.coding import banks

    header = draft.header
    key = vendor_key(header.get("vendor_name") or "")
    vendor = store.get_vendor(key) if key else None
    draft.from_vendor, changed = fill_from_vendor(header, draft.from_vendor, draft.absent, vendor, unread_numbers(read))
    currency = (header.get("currency") or "CAD").strip().upper() or "CAD"
    if draft.pay_from_for and draft.pay_from_for != currency:
        draft.pay_from_for = ""  # picked for another currency: the default follows the invoice's currency
    if not draft.pay_from_for or not header.get("pay_from_account"):
        draft.pay_from_for = ""
        accounts = store.bank_accounts()
        last = store.last_pay_from(key) if accounts else ""
        chosen = banks.opening_choice({"currency": currency}, False, accounts, last)
        if chosen != (header.get("pay_from_account") or ""):
            header["pay_from_account"] = changed["pay_from_account"] = chosen
    return changed


def parse_absent(raw: Any, header: dict[str, str]) -> list[str]:
    """The fields marked "Not on this invoice" (the form's ``absent`` JSON list): only the ones that may be absent
    (ABSENT_FIELDS), and only while they are blank (a value typed in since says it is on the invoice after all)."""
    try:
        data = json.loads(raw) if isinstance(raw, str) and raw.strip() else []
    except ValueError:
        return []
    if not isinstance(data, list):
        return []
    return [
        n for n in dict.fromkeys(str(x) for x in data) if n in ABSENT_FIELDS and not str(header.get(n) or "").strip()
    ]


def settle_absent(issues: list[Any], absent: list[str]) -> list[Any]:
    """The checks once the reviewer said a field is not on the invoice: what asked for it is said as information
    (what it costs), or left out (ABSENT_FIELDS). Errors are never touched."""
    from ap_coder.coding.validation import INFO, Issue

    swap: dict[str, Any] = {}
    for name in absent:
        swap.update(ABSENT_FIELDS.get(name) or {})
    out = []
    for issue in issues:
        if issue.code not in swap or issue.severity == "error":
            out.append(issue)
        elif swap[issue.code] is not None:
            code, message = swap[issue.code]
            out.append(Issue(INFO, code, message, issue.line_number))
    return out


def parse_taught(raw: Any) -> dict[str, list[list[float]]]:
    """The words the reviewer showed on the page, per field (from the form's ``taught`` JSON); bad parts dropped."""
    try:
        data = json.loads(raw) if isinstance(raw, str) and raw.strip() else {}
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for name, boxes in data.items():
        if name in TEACHABLE:
            cleaned = clean_boxes(boxes)
            if cleaned:
                out[name] = cleaned
    return out


def taught_boxes(draft: Draft) -> dict[str, list[Box]]:
    """{field: [Box]} for the supplier's template (``capture.workflow.learn_from_approval``)."""
    return {f: [Box.from_list(b) for b in boxes] for f, boxes in draft.taught.items()}


# A line item's cells a reviewer can fill from words picked on the page (drag a box around them), and "line_new":
# a new line from a picked row (its description and its amount, the last amount in it).
LINE_TEACHABLE = {"line_description": "description", "line_amount": "amount", "line_quantity": "quantity",
                  "line_unit_price": "unit price"}  # fmt: skip
NEW_LINE = "line_new"


def line_value(name: str, text: str) -> Any:
    """What words picked on the page mean for a line item's cell (None when they don't read as one); for
    ``NEW_LINE``, {"description", "amount"} (None without an amount)."""
    plain = " ".join(text.split())
    if name == "line_description":
        return plain or None
    if name in ("line_amount", "line_unit_price"):
        return parse_amount(plain)
    if name == "line_quantity":
        value = parse_amount(plain)
        return None if value is None else (int(value) if float(value).is_integer() else value)
    if name == NEW_LINE:
        from ap_coder.capture.normalize import find_amounts

        found = find_amounts(plain)
        if not found:
            return None
        amount, start, end = found[-1]
        description = " ".join(plain[:start].split()) or " ".join((plain[:start] + plain[end:]).split())
        # A row reads "description  qty  price  amount": the numbers before the amount are not the description.
        # Word by word from the end (an anchored "(\s+number)+$" pattern takes quadratic time on a long run of words).
        words = description.split(" ")
        while len(words) > 1 and re.fullmatch(r"[-$]?\d[\d,.]*", words[-1]):
            words.pop()
        description = " ".join(words)
        description = re.sub(r"^\d{1,3}[.)]?\s+(?=\D)", "", description)  # the row's number ("3 Copy paper")
        return {"description": description, "amount": round(amount, 2)}
    return None


def form_value(name: str, text: str) -> Any:
    """What the words clicked on the page mean for a field of the form (None when they don't read as one)."""
    if name in AMOUNT_FIELDS:
        return parse_amount(text)
    if name in ("invoice_date", "due_date"):
        return parse_date(text, prefer_day_first=None) or parse_date(text, prefer_day_first=True) or text.strip()
    plain = " ".join(text.split())
    if name in ("invoice_number", "po_number", "gst_hst_registration_number", "qst_registration_number",
                "pst_registration_number"):  # fmt: skip
        # The label's own marks picked with the number ("#: PO-77120", "No. 4471"): the number alone.
        plain = re.sub(r"^(?:(?:no|n°|nr|num|#)\.?\s*|[#:.\-–]\s*)+", "", plain, flags=re.I)
    return plain


def lines_frame(lines: list[dict[str, Any]]) -> pd.DataFrame:
    """The line grid as ``coding_from_inputs`` and ``split_line`` take it."""
    rows = [{c: row.get(c) for c in LINE_COLUMNS} for row in lines]
    for row in rows:
        row["line_number"] = _line_number(row["line_number"])
        row["taxes_applied"] = list(row.get("taxes_applied") or [])
        row["tax_status"] = str(row.get("tax_status") or "")
        exempt = row.get("tax_exempt")
        row["tax_exempt"] = list(exempt) if isinstance(exempt, list | tuple) else None
    frame = pd.DataFrame(rows, columns=list(LINE_COLUMNS))
    frame["line_number"] = frame["line_number"].astype(object)
    return frame


def lines_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """A line grid (after an action on it) back to rows: NaN as blank, line numbers whole."""
    out = []
    for rec in frame.to_dict("records"):
        row = {}
        for c in LINE_COLUMNS:
            value = rec.get(c)
            if c == "taxes_applied":
                row[c] = list(value) if isinstance(value, list | tuple) else []
            elif c == "tax_status":
                row[c] = "" if is_blank(value) else str(value)
            elif c == "tax_exempt":
                row[c] = list(value) if isinstance(value, list | tuple) else None
            elif c == "line_number":
                row[c] = _line_number(value)
            else:
                row[c] = None if is_blank(value) else value
        out.append(row)
    return out


def tax_frame(taxes: list[dict[str, Any]]) -> pd.DataFrame:
    rows = [{c: row.get(c) for c in TAX_COLUMNS} for row in taxes]
    for row in rows:
        if row["province"] in (None, "", NO_PROVINCE):
            row["province"] = ""
    return pd.DataFrame(rows, columns=list(TAX_COLUMNS))


def header_values(header: dict[str, str]) -> tuple[dict[str, Any], list[str]]:
    """The header fields as ``InvoiceCoding`` takes them (amounts as numbers), and what can't be read."""
    out: dict[str, Any] = {}
    problems = []
    for name in FORM_FIELDS:
        text = str(header.get(name) or "").strip()
        if name in AMOUNT_FIELDS:
            try:
                number = to_number(text)
            except ValueError:
                number = math.nan
            # "nan" and "inf" read as numbers, but no check can use them (a NaN total is never over the limit).
            if math.isfinite(number):
                out[name] = number
            else:
                problems.append(f"{FIELD_LABELS[name]}: “{text}” is not an amount")
        else:
            out[name] = text
    return out, problems


# --- Labels for codes ----------------------------------------------------------------------------------------------


def gl_display(code: str | None) -> str:
    """A GL code for display: UNASSIGNED reads as "Needs an account", a blank as an em dash."""
    return NEEDS_GL if code == UNASSIGNED else (code or "—")


def cc_display(code: str | None) -> str:
    return NEEDS_CC if code == UNASSIGNED else (code or "")


def gl_name(reference: ReferenceData, code: str) -> str:
    row = reference.chart_of_accounts.get(code)
    return short_name((row or {}).get("description", "")) if row else ""


def gl_options(reference: ReferenceData, lines: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """(code, label) of the GL accounts a line may use (not the tax accounts); an unknown code a line has is kept,
    so it shows and can be fixed."""
    labels = {UNASSIGNED: NEEDS_GL}
    for row in reference.chart_of_accounts.rows:
        labels[row["gl_code"]] = f"{row['gl_code']} · {short_name(row.get('description', ''))[:44]}"
    tax_gls = reference.tax.tax_gl_codes()
    codes = [UNASSIGNED] + [c for c in reference.chart_of_accounts.codes if c not in tax_gls]
    for li in lines:
        code = _text(li.get("predicted_gl_code"))
        if code and code not in codes:
            codes.append(code)
    return [(c, labels.get(c, f"{c} · unknown code")) for c in codes]


def cc_options(reference: ReferenceData, lines: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """(code, label) of the cost centers ([] when the organisation has none)."""
    if reference.cost_centers is None:
        return []
    out = [(UNASSIGNED, NEEDS_CC)] + [
        (row["cost_center"], f"{row['cost_center']} · {row.get('description', '')[:30]}")
        for row in reference.cost_centers.rows
    ]
    known = {c for c, _ in out}
    for li in lines:
        code = _text(li.get("predicted_cost_center"))
        if code and code not in known:
            out.append((code, code))
            known.add(code)
    return out


def province_options() -> list[tuple[str, str]]:
    return [(p, f"{p} · {PROVINCE_NAMES[p]}" if p in PROVINCE_NAMES else province_label(p, "Unknown"))
            for p in PROVINCE_VALUES]  # fmt: skip


def supply_line(header: dict[str, Any], lines: list[dict[str, Any]], setup: Any) -> dict[str, Any]:
    """The place of supply as the review screen says it: {province, label, why, open}: open when it is not known or
    is only the vendor's province (a guess), so the reviewer sees the choice."""
    from types import SimpleNamespace

    from ap_coder.coding.tax import SUPPLY_OURS, SUPPLY_PRINTED, SUPPLY_SITE, SUPPLY_VENDOR, supply_source

    coding = SimpleNamespace(
        ship_to_province=str(header.get("ship_to_province") or ""),
        supplier_province=str(header.get("supplier_province") or ""),
        line_items=[SimpleNamespace(predicted_cost_center=str(r.get("predicted_cost_center") or "")) for r in lines],
    )
    province, source = supply_source(coding, setup)
    if not province and coding.ship_to_province == OUTSIDE_CANADA:
        return {"province": OUTSIDE_CANADA, "label": "Outside Canada", "why": "delivered outside Canada: no Canadian "
                "sales tax", "open": False}  # fmt: skip
    why = {  # the ship-to field holds only what is printed or set by the reviewer (pipeline.finalise_coding)
        # A bill-to read under a customer label is ours, used when no ship-to is printed (D-61): said as either.
        SUPPLY_PRINTED: "the invoice's ship-to, else its bill-to (or as you set it)",
        SUPPLY_SITE: "the province of the cost center the lines are coded to",
        SUPPLY_OURS: "your province (Settings): no ship-to or bill-to printed",
        SUPPLY_VENDOR: "only the vendor's province: set where it is delivered to you",
    }.get(source, "unknown: choose where it is delivered to you")
    label = f"{province} · {PROVINCE_NAMES[province]}" if province in PROVINCE_NAMES else "Not known"
    return {"province": province, "label": label, "why": why, "open": source in ("", SUPPLY_VENDOR)}


def tax_province_options() -> list[tuple[str, str]]:
    return [(p, province_label(p) if p == OUTSIDE_CANADA else (p or NO_PROVINCE)) for p in PROVINCE_VALUES]


def received_on(inv: dict[str, Any]) -> dt.date | None:
    """The day the invoice came in (its ``created_at``), None when it can't be read."""
    try:
        return dt.date.fromisoformat(str(inv.get("created_at") or "")[:10])
    except ValueError:
        return None


def vendor_accounts(store: Store, vendor_name: str, limit: int = 6) -> list[str]:
    """The GL accounts this vendor's lines were approved to, most used first (the newest first among equals), at most
    ``limit``: the account picker's "Used for this vendor" (D-56). Only what a person decided (or the ERP's history),
    never what the AI proposed; lines left without an account are not counted."""
    if not (vendor_name or "").strip():
        return []
    decided = {ACCEPTED, CORRECTED, CODED, HISTORY}
    counts: dict[str, int] = {}
    for row in store.feedback_rows(vendor_name=vendor_name):  # newest first: a tie keeps the newest first
        code = _text(row.get("final_gl"))
        if row.get("outcome") in decided and code and code != UNASSIGNED:
            counts[code] = counts.get(code, 0) + 1
    return sorted(counts, key=lambda c: -counts[c])[:limit]  # sorted() is stable


def grid(reference: ReferenceData, draft: Draft, ai: dict[str, Any] | None = None,
         vendor_gls: list[str] | None = None) -> dict[str, Any]:  # fmt: skip
    """What the line and tax grids need: the rows, with a stable key each, and the choices of every cell. ``ai``: the
    invoice as read: a row whose account is still the one the AI only guessed for its line is marked (B-463), not one
    whose account the reviewer, a coding rule or the PO coding put there (B-475). ``vendor_gls``: the accounts the
    vendor's lines went to (``vendor_accounts``), offered first by the account picker; those still in the list."""
    from ap_coder.core.store import line_guessed

    paired = list(pair_lines((ai or {}).get("line_items") or [], draft.lines))
    guesses = {
        id(row)
        for before, row in paired
        if before and row and line_guessed(before) and row.get("predicted_gl_code") == before.get("predicted_gl_code")
    }
    # A tax code worked out from several possible sets of lines, still as it was worked out: to check (D-64).
    charged = _charged(draft)
    tax_guesses = {
        id(row)
        for before, row in paired
        if before and row and tax_guessed(row) and tax_code_of(row, charged) == tax_code_of(before, charged)
    }
    # A PST exemption inferred when the invoice was read: picking it again later is still confirmed on review (B-723).
    inferred = {id(row): exemption_to_confirm(before) for before, row in paired if before and row}
    codes = tax_choices(draft, reference.tax)
    return {  # "guessed": an account no approval backs, marked for the reviewer to check (B-463)
        "lines": [
            {
                **row,
                "key": f"r{i}",
                "guessed": id(row) in guesses and row.get("predicted_gl_code") not in (None, "", UNASSIGNED),
                "tax_code": tax_code_of(row, charged),
                "tax_guess": id(row) in tax_guesses,
                "tax_learned": tax_learned(row),  # taken from what AP approved on the vendor's similar lines (D-70)
                "tax_inferred": inferred.get(id(row), ""),
            }
            for i, row in enumerate(draft.lines)
        ],
        "tax_codes": codes["options"],
        # a line added on screen: the code most lines have, else the province's usual taxes
        "tax_default": max((c for c, _ in codes["options"]), key=[tax_code_of(r, charged) for r in draft.lines].count)
        if draft.lines
        else codes["options"][0][0],
        "tax_code_labels": codes["labels"],
        "tax_marks": {},  # {line number: why}: the lines the Tax check points at (``analyse``), drawn yellow
        "show_qty": shows_quantities(draft.lines),
        "taxes": draft.taxes,
        "gl_options": (gls := gl_options(reference, draft.lines)),
        "cc_options": (ccs := cc_options(reference, draft.lines)),
        "gl_labels": dict(gls),  # a line's own choice, printed in its cell (the picker has the whole list)
        "cc_labels": dict(ccs),
        "vendor_accounts": [c for c in vendor_gls or [] if c in {code for code, _ in gls}],
        "tax_types": list(TAX_TYPES),
        "tax_labels": {t: TAX_LABELS.get(t, t) for t in TAX_TYPES},
        "tax_provinces": tax_province_options(),
        "has_cc": reference.cost_centers is not None,
    }


def shows_quantities(lines: list[dict[str, Any]]) -> bool:
    """Whether the line grid shows Qty and Unit price as columns: a line with a quantity other than 1, or a unit price
    other than its amount (otherwise they say nothing the amount does not, and the toggle shows them)."""

    def num(value: Any) -> float | None:
        try:
            return float(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None

    for li in lines:
        qty, price, amount = num(li.get("quantity")), num(li.get("unit_price")), num(li.get("amount"))
        if (qty is not None and abs(qty - 1) > 1e-9) or (
            price is not None and amount is not None and abs(price - amount) > 0.005
        ):
            return True
    return False


def tax_choices(draft: Draft, setup: Any = None) -> dict[str, Any]:
    """The choices of the lines' tax-code dropdowns where the invoice on screen is supplied (D-61, D-64): {options:
    [(code, label)], labels: {code: label} for every code, province}. The lines' own codes are always among them."""
    header, lines = getattr(draft, "header", None) or {}, list(getattr(draft, "lines", None) or [])
    province = supply_line(header, lines, setup)["province"]
    charged = _charged(draft)
    current = [tax_code_of(row, charged) for row in lines]
    return {"options": tax_code_options(province, charged, current), "labels": all_labels(province),
            "province": province}  # fmt: skip


def _charged(draft: Any) -> list[str]:
    """The taxes the tax grid on screen charges and some line is marked with (``line_tax.legacy_basis``)."""
    return legacy_basis(getattr(draft, "lines", None) or [],
                        [{"tax_type": t.get("tax_type"), "tax_amount": _number(t.get("tax_amount"))}
                         for t in getattr(draft, "taxes", None) or []])  # fmt: skip


def _number(value: Any) -> float:
    try:
        number = float(str(value or 0).replace(",", "").replace("$", ""))
    except ValueError:
        return 0.0
    return number if math.isfinite(number) else 0.0


# --- Capture (what was read from the page) -----------------------------------------------------------------------

# How the invoice came in, from how its pages were read: a vendor's own PDF has its text inside and is read as printed;
# a scan or photo (or a picture saved as a PDF) is read with OCR, so its values deserve a closer look.
DOCUMENT_KINDS = {
    "text": ("Vendor PDF", "ok", "picture_as_pdf", "The PDF has its text inside: read exactly as printed."),
    "scan": ("Scan or photo", "warn", "photo_camera",
             "A picture of the invoice, read with OCR (reading the letters off the image): check its values closely."),
    "mixed": ("Partly scanned", "warn", "photo_camera",
              "Some pages have their text inside; the others are pictures, read with OCR: check their values closely."),
}  # fmt: skip


def document_kind(layout_source: str) -> dict[str, str] | None:
    """{key, label, tone, icon, title} for how the pages were read (``CaptureResult.layout_source``); None when they
    were not read (no capture, or nothing on the page)."""
    key = {"text": "text", "ocr": "scan", "di": "scan", "mixed": "mixed"}.get(layout_source or "")
    if key is None:
        return None
    label, tone, icon, title = DOCUMENT_KINDS[key]
    return {"key": key, "label": label, "tone": tone, "icon": icon, "title": title}


def capture_of(store: Store, invoice_id: int) -> CaptureResult | None:
    raw = store.get_capture(invoice_id)
    if not raw:
        return None
    try:
        return CaptureResult.from_dict(raw)
    except (KeyError, TypeError, ValueError):
        return None


def capture_confidence(capture: CaptureResult | None) -> dict[str, dict[str, str]]:
    """{field: {tone, icon, text}}: how sure the capture is of each field, beside its label in the form."""
    if capture is None:
        return {}
    out = {}
    for name, fr in capture.fields.items():
        failed = any(r.startswith("check failed") for r in fr.reasons)
        status = "failed" if failed and fr.status != MISSING else fr.status
        if status not in CAPTURE_BADGES:
            continue
        tone, icon_name = CAPTURE_BADGES[status]
        # the status in words, and how sure in figures where that tells more (never rounded up to certainty)
        sure = f"{math.floor(fr.confidence * 100 + 1e-9)}%"
        words = CAPTURE_WORDS[status] + (f" {sure}" if status in ("likely", "check") else "")
        out[name] = {"tone": tone, "icon": icon_name, "text": words, "status": status,
                     "help": f"{CAPTURE_HELP[status]} How sure: {sure}."}  # fmt: skip
    return out


def shown_sure(sure: dict[str, dict[str, str]], header: dict[str, str], read: dict[str, Any],
               capture: CaptureResult | None) -> dict[str, dict[str, str]]:  # fmt: skip
    """The reading's badges the form shows: only beside a value that is still the one read (an empty field, or one
    the reviewer changed, has none: the badge would vouch for what is not there), and none for a currency the page
    does not print (the domestic CAD assumed: "Check" on almost every invoice said nothing)."""
    from ap_coder.capture.bridge import currency_read

    def same(name: str) -> bool:
        now, was = str(header.get(name) or "").strip(), read.get(name)
        if not now:
            return False
        if name in AMOUNT_FIELDS:
            try:
                return abs(float(now) - float(was or 0)) < 0.005
            except (TypeError, ValueError):
                return False
        return now.casefold() == str(was or "").strip().casefold()

    out = {}
    for name, badge in sure.items():
        if not same(name):
            continue
        fr = capture.fields.get(name) if capture is not None else None
        if name == "currency" and fr is not None and not currency_read(fr) and header.get("currency") == "CAD":
            continue
        out[name] = badge
    return out


# --- OvisOCR2, for this invoice ------------------------------------------------------------------------------------


def waiting_line(status: Any, ahead: int) -> str:
    """ "Waiting for OvisOCR2: 2 invoices ahead, about 6 minutes" (the time when a page time has been measured)."""
    from ap_coder.ai import page_reader

    where = f"{plural(ahead, 'invoice')} ahead" if ahead else "it's next"
    eta = ""
    if status is not None and status.eta_minutes and status.queue:
        eta = ", " + page_reader.duration(status.eta_minutes * 60 * min(ahead + 1, status.queue) / status.queue)
    return f"Waiting for OvisOCR2: {where}{eta}."


def agreement_line(done: dict[str, Any]) -> str:
    """ "OvisOCR2 read this invoice: 9 of 11 fields agree" and what else it found."""
    agree, differ, only = (len(done.get(k) or []) for k in ("agree", "differ", "only"))
    line = f"OvisOCR2 read this invoice: {agree} of {agree + differ + only} fields agree"
    extra = []
    if differ:
        extra.append(f"{differ} read differently, marked Check")
    if only:
        extra.append(f"{only} found only by OvisOCR2")
    figs = done.get("figures") or {}
    total = max(figs.get("figures", 0), figs.get("first_figures", 0))
    if total:
        extra.append(f"{figs.get('confirmed', 0)} of {total} figures on the page read the same by both")
    minutes = (done.get("seconds") or 0) / 60
    return line + (f" ({'; '.join(extra)})" if extra else "") + (f" · {minutes:.1f} min" if minutes else "")


def not_running(status: Any, settings: Settings, store: Store) -> str:
    """Why OvisOCR2 isn't reading now ("" when it is): from the reading status, else from the background reader."""
    if status is not None:
        return "" if status.page_reader_ready else "OvisOCR2 isn't running: see Settings → Reading."
    from ap_coder.ai.page_worker import ready

    try:
        model, _why = ready(settings, store)
    except Exception:  # noqa: BLE001 - it can't be asked: said as not running
        model = ""
    return "" if model else "OvisOCR2 isn't running: see Settings → Reading."


def reviewer_state(store: Store, inv: dict[str, Any]) -> dict[str, Any]:
    """What the AI reviewer (verifier.py) made of this invoice, for a line under the document title: {state, lines:
    [(icon, text)], again: offer to review it again}."""
    from ap_coder.ai import verifier

    if not verifier.load_settings(store).on:
        return {"state": "off", "lines": [], "again": False}
    row = store.verification(inv["id"])
    if row is None:
        return {"state": "none", "lines": [], "again": True}
    state, result = row["status"], row.get("result") or {}
    name = verifier.NAME
    if state == "verifying":
        return {"state": state, "lines": [("hourglass_top", f"The {name} is checking this invoice against its pages "
                                           "now.")], "again": False}  # fmt: skip
    if state == "waiting":
        text = (
            f"The {name} checks this invoice against its pages when the chat model is free (after OvisOCR2). "
            "It isn't approved without a person until it agrees."
        )
        lines = [("hourglass_top", text)]
        if row.get("error"):
            lines.append(("sync_problem", f"Last time: {row['error']}"))
        return {"state": state, "lines": lines, "again": False}
    if state == "failed":
        return {"state": state, "lines": [("error", f"The {name} couldn't check it: {row.get('error')}. A person "
                                                    "decides.")], "again": True}  # fmt: skip
    if state == "skipped":
        return {"state": state, "lines": [("info", f"Not checked by the {name}: {row.get('error')}.")], "again": True}
    took = f"{plural(int(result.get('rounds') or 0), 'round')}, {float(result.get('seconds') or 0):.0f} s"
    status = result.get("status")
    if status == verifier.AGREED:
        line = ("verified", f"The {name} checked every field and line against the pages and agrees ({took}).")
    elif status == verifier.DISAGREES:
        what = ", ".join(verifier.FIELDS.get(f, f) for f in result.get("fields") or {})
        lines_ = ", ".join(f"line {n}" for n in result.get("lines") or {})
        line = ("rule", f"The {name} reads {', '.join(x for x in (what, lines_) if x)} differently ({took}): see the "
                        "checks.")  # fmt: skip
    else:
        line = ("help", f"The {name} is not sure ({result.get('why') or took}): a person checks it.")
    out = [line]
    if result.get("summary"):
        out.append(("chat", f"“{result['summary']}”"))
    return {"state": status or state, "lines": out, "again": True}


def reader_state(store: Store, settings: Settings, inv: dict[str, Any], status: Any) -> dict[str, Any]:
    """What OvisOCR2 made of this invoice, for the line under the document title: ``state`` is "read" (its reading
    is in), "waiting", "reading", "skipped", "done" (read, not applied), "failed", "none" (not in its queue) or ""
    (a text file: no page to read). ``lines``: (icon, text); ``queue``: offer to put it in OvisOCR2's queue."""
    from ap_coder.ai.extraction import TEXT_EXTENSIONS

    path = str(inv.get("source_path") or "")
    if any(path.lower().endswith(s) for s in TEXT_EXTENSIONS):
        return {"state": "", "lines": [], "queue": False, "poll": False}
    meta = inv.get("meta") or {}
    done = meta.get("page_reader")
    row = store.page_read(inv["id"]) or {}
    state = row.get("status", "")
    if done:
        lines = [("visibility", agreement_line(done))]
        figs = done.get("figures") or {}
        if figs.get("differ"):
            first = "PDF text" if (meta.get("capture") or {}).get("layout") == "text" else "OCR"
            pairs = "; ".join(f"{a} (OvisOCR2) / {b} ({first})" for a, b in figs["differ"])
            lines.append(("compare_arrows", f"Read differently: {pairs}"))
        return {"state": "read", "lines": lines, "queue": False, "poll": False, "at": done.get("at", "")}
    if state == "reading":
        return {"state": state, "queue": False, "poll": True, "lines": [(
            "hourglass_top", "OvisOCR2 is reading this invoice now. The fields update when it is done, unless you "
            f"have edited the invoice. {NOT_TOUCHLESS}")]}  # fmt: skip
    if state == "waiting":
        stopped = not_running(status, settings, store)
        if stopped:
            lines = [("pause_circle", f"{stopped} {NOT_TOUCHLESS}")]
        else:
            ahead = store.page_reads_ahead(inv["id"])  # in line before it, and the one read now
            lines = [("hourglass_top", f"{waiting_line(status, ahead)} The fields update when it is done, unless "
                                       f"you have edited the invoice. {NOT_TOUCHLESS}")]  # fmt: skip
        if row.get("tries") and row.get("error"):  # cut off by the model server last time
            lines.append(("sync_problem", f"Last time: {row['error']}"))
        return {"state": state, "lines": lines, "queue": False, "poll": True}
    reason = str(row.get("error") or "no reason given").rstrip(".")
    if state == "skipped":
        return {"state": state, "lines": [("error", f"OvisOCR2 couldn't read it: {reason}; a person decides.")],
                "queue": False, "poll": False}  # fmt: skip
    if state == "done":  # read, but its reading wasn't applied (the invoice was edited or decided meanwhile)
        return {"state": state, "lines": [("visibility", f"OvisOCR2 read this invoice: {reason}.")], "queue": False,
                "poll": False}  # fmt: skip
    if state == "failed":  # e.g. cut off by the model server MAX_TRIES times: it can be read again from here
        return {"state": state, "lines": [("error", f"OvisOCR2 couldn't read it: {reason}; a person decides.")],
                "queue": True, "poll": False}  # fmt: skip
    stopped = not_running(status, settings, store)
    text = f"OvisOCR2 hasn't read this invoice yet. {stopped} {NOT_TOUCHLESS}".replace("  ", " ")
    return {"state": "none", "lines": [("visibility_off", text)], "queue": True, "poll": False}


# --- The checks ---------------------------------------------------------------------------------------------------


_PLAIN_AMOUNT = re.compile(r"(?<![\d.,])(-?\d{4,})\.(\d{2})(?![\d%])")


# The code's field names in the checks' messages ("but grand_total = 0.00"), as the form names them (B-429).
_PLAIN_FIELDS = (("quantity x unit_price", "quantity × unit price"), ("grand_total", "the total"),
                 ("tax_total", "the tax total"), ("unit_price", "unit price"))  # fmt: skip


def readable(message: str, cap: bool = True) -> str:
    """A check message as a clerk reads it: plain words for field names, a capital first letter, amounts with
    thousands separators."""
    for code, words in _PLAIN_FIELDS:
        message = message.replace(code, words)
    message = _PLAIN_AMOUNT.sub(lambda m: f"{int(m.group(1)):,}.{m.group(2)}", message)
    return message[:1].upper() + message[1:] if cap else message


def grouped_issues(issues: list[Any]) -> list[tuple[bool, str, str, list[int]]]:
    """(is error, text, code, line numbers) per distinct message: the same finding on several lines is one item
    ("Lines 1, 3, 4: …")."""
    order: dict[tuple[str, str, str], list[int]] = {}
    for issue in issues:
        order.setdefault((issue.severity, issue.code, issue.message), []).append(issue.line_number or 0)
    out = []
    for (severity, code, message), lines in order.items():
        numbers = sorted({n for n in lines if n})
        where = (
            (f"Line {numbers[0]}: " if len(numbers) == 1 else f"Lines {', '.join(map(str, numbers))}: ")
            if numbers
            else ""
        )
        out.append((severity == "error", where + readable(message, cap=not where), code, numbers))
    return out


def grouped(issues: list[Any]) -> list[tuple[bool, str]]:
    """(is error, text) per distinct message (``grouped_issues``)."""
    return [(bad, text) for bad, text, _code, _lines in grouped_issues(issues)]


# A finding about a line's account or cost center: its item in the checks is a button to that box (B-443).
GOTO_BOX = {
    "GL_UNASSIGNED": "line_gl",
    "CC_UNASSIGNED": "line_cc",
    "PST_EXEMPTION_TO_CONFIRM": "line_tax_code",
    "TAX_EXEMPTION_LEARNED": "line_tax_code",
}


def confidence_pct(value: float, threshold: float) -> str:
    from ap_coder.web.pages.review import confidence_pct as pct

    return pct(value, threshold)


# The groups of the checks, in order: what keeps Approve closed (a GL account or cost center to pick: G goes to the
# first), the errors (to fix, or to approve anyway), and the warnings to check (B-465).
BLOCK, FIX, LOOK = "block", "err", "warn"
GROUP_TITLES = {BLOCK: "Before you can approve", FIX: "Must be fixed", LOOK: "Check before approving"}


def checks_summary(report: Any, errors: list[Any], warnings: list[Any]) -> dict[str, Any]:
    """All the checks as one status: passed, or what needs attention (one line each; details below), grouped:
    before you can approve (what keeps Approve closed), must be fixed, check before approving."""
    low = report.requires_review and not errors
    items: list[dict[str, Any]] = []
    for bad, text, code, lines in grouped_issues(errors + warnings):
        kind = BLOCK if code in GOTO_BOX else FIX if bad else LOOK
        goto = {"box": GOTO_BOX[code], "line": lines[0]} if code in GOTO_BOX and lines else None
        # A possible duplicate: a link to each invoice it may duplicate, to compare them (D-60).
        links = ([{"href": f"/review/{n}", "text": f"Open #{n}"} for n in re.findall(r"#(\d+)", text)]
                 if code == "DUPLICATE_INVOICE" else [])  # fmt: skip
        field = "" if lines else CODE_FIELDS.get(code) or FIELD_FLAGS.get(code, ("", None))[0]
        items.append({"bad": kind != LOOK, "kind": kind, "text": text, "goto": goto, "links": links, "code": code,
                      "field": field})  # fmt: skip
    if low:
        items.append({"bad": False, "kind": LOOK, "text": f"Confidence {confidence_pct(report.adjusted_confidence, report.review_threshold)} "  # noqa: E501
                      f"is below the {report.review_threshold:.0%} threshold."})  # fmt: skip
    if not items:
        return {"tone": "ok", "icon": "check_circle", "title": "All checks passed", "items": [], "groups": [],
                "sub": "Totals reconcile, taxes verified, all codes valid."}  # fmt: skip
    counts = {k: sum(1 for i in items if i["kind"] == k) for k in GROUP_TITLES}
    groups = [{"kind": k, "title": f"{GROUP_TITLES[k]} ({counts[k]})",
               "items": [i for i in items if i["kind"] == k]} for k in GROUP_TITLES if counts[k]]  # fmt: skip
    title = " · ".join(g["title"] for g in groups)
    tone, icon = ("err", "error") if counts[BLOCK] or counts[FIX] else ("warn", "visibility")
    return {"tone": tone, "icon": icon, "title": title, "items": [i for g in groups for i in g["items"]],
            "groups": groups, "sub": "Press G to go to the first one." if counts[BLOCK] else ""}  # fmt: skip


def checks_details(report: Any) -> list[dict[str, str]]:
    """Every finding, with what to do about it (the arguments of ``ui.check``)."""
    items = []
    for issue in report.issues:
        where = f"Line {issue.line_number}: " if issue.line_number else ""
        about = help_for(issue.code)
        hint = about.action if about and issue.severity != "info" else ""
        kind, title = {"error": ("error", "Must be fixed"), "info": ("info", "Good to know")}.get(
            issue.severity, ("warning", GROUP_TITLES[BLOCK] if issue.code in GOTO_BOX else GROUP_TITLES[LOOK])
        )
        items.append({"kind": kind, "title": title, "message": where + readable(issue.message, cap=not where),
                      "code": issue.code,
                      "hint": "" if kind == "info" else hint})  # fmt: skip
    errors = [i for i in report.issues if i.severity == "error"]
    if report.requires_review and not errors:
        items.append({"kind": "warning", "title": "Low confidence", "code": "", "hint": "",
                      "message": f"Confidence {confidence_pct(report.adjusted_confidence, report.review_threshold)} "
                                 f"is below the {report.review_threshold:.0%} threshold, so a person should look it "
                                 "over."})  # fmt: skip
    matches = [h for h in report.checks.get("history") or [] if h["status"] == "match"]
    if matches:
        numbers = ", ".join(str(h["line_number"]) for h in matches)
        items.append({"kind": "info", "title": "Learned", "code": "", "hint": "",
                      "message": f"Line {numbers} matches how reviewers coded this vendor before."})  # fmt: skip
    if not [i for i in report.issues if i.severity != "info"]:
        items.append(
            {
                "kind": "ok",
                "title": "Numbers check out" if report.requires_review else "All clear",
                "message": "Totals reconcile, taxes verified, all codes valid.",
                "code": "",
                "hint": "",
            }
        )
    return items


def ai_changes(coding: InvoiceCoding, ai: dict[str, Any], cost_centers: bool = True) -> int:
    """The lines whose suggested coding the reviewer changed, counted as the approval counts them: a GL account or
    cost center the AI left blank and the reviewer picked changes nothing; a line still without a GL isn't taught.
    Without a cost center list (``cost_centers`` False) the page has no cost center to keep: only the GL counts."""
    changed = 0
    for before, li in pair_lines(ai.get("line_items", []), list(coding.line_items)):
        if li.predicted_gl_code == UNASSIGNED:
            continue
        s_gl = before.get("predicted_gl_code") if before else None
        s_cc = (before.get("predicted_cost_center", "") if cost_centers else "") if before else None
        if nothing_proposed(s_gl, s_cc):
            continue  # the reviewer's coding, not a change (B-650)
        if not suggestion_kept(s_gl, s_cc, li.predicted_gl_code, li.predicted_cost_center):
            changed += 1
    return changed


def learn_text(coding: InvoiceCoding, ai: dict[str, Any], cost_centers: bool = False,
               rules_applied: list[dict[str, Any]] | None = None) -> str:  # fmt: skip
    """What approving teaches, line by line, as the approval counts it (B-473, B-475): "1 line to code" (no GL account
    yet, or no cost center where they are used: nothing taught from it), then "Teaches from 3 lines: 1 you coded (the
    AI proposed nothing, or only a guess, and you picked it), 1 you changed, 1 as suggested (an account proposed and
    kept), 1 by a coding rule, 1 guessed account to check (the chat model's guess, still on screen: B-463)".
    ``rules_applied``: the invoice's (its ``meta``), the lines a fixed coding rule coded when it was read."""
    from ap_coder.core.store import line_guessed

    by_rule = {c.get("line_number"): c for c in rules_applied or []}
    todo = coded = changed = kept = guesses = ruled = 0
    for before, li in pair_lines(ai.get("line_items", []), list(coding.line_items)):
        gl, cc = li.predicted_gl_code, li.predicted_cost_center or ""
        if gl in (UNASSIGNED, "") or (cost_centers and cc in (UNASSIGNED, "")):
            todo += 1
            continue
        rule = by_rule.get(before.get("line_number")) if before else None
        s_gl = before.get("predicted_gl_code") if before else None
        s_cc = (before.get("predicted_cost_center", "") if cost_centers else "") if before else None  # B-562
        if rule is not None and rule.get("gl_to") == gl and (rule.get("cc_to") or "") == cc:
            ruled += 1
        elif line_guessed(before, rule):
            if gl == s_gl:
                guesses += 1
            else:
                coded += 1
        elif s_gl is None or nothing_proposed(s_gl, s_cc):  # nothing proposed: theirs
            coded += 1
        elif suggestion_kept(s_gl, s_cc, gl, cc):
            kept += 1
        else:
            changed += 1
    parts = [f"{coded} you coded" if coded else "", f"{changed} you changed" if changed else "",
             f"{kept} as suggested" if kept else "", f"{ruled} by a coding rule" if ruled else "",
             f"{plural(guesses, 'guessed account')} to check" if guesses else ""]  # fmt: skip
    taught = coded + changed + kept + guesses + ruled
    said = [f"{plural(todo, 'line')} to code" if todo else "",
            f"Teaches from {plural(taught, 'line')}: {', '.join(p for p in parts if p)}" if taught else ""]  # fmt: skip
    return " · ".join(s for s in said if s)


def differs_from_stored(
    coding: InvoiceCoding,
    stored: dict[str, Any],
    *,
    cost_centers: bool = True,
    filled: frozenset[str] | set[str] = frozenset(),
) -> bool:
    """Whether the coding on screen is not the one stored for the invoice (the reviewer changed something). Not an
    edit (B-801): an account or cost center blank either way ("" / "UNASSIGNED"), a cost center where none are in use
    (the screen has no box for it), and ``filled``, the details the screen fills itself on every check (the default
    Pay from account, the vendor list's tax numbers) while the invoice stored has none."""
    try:
        before = InvoiceCoding.model_validate({k: v for k, v in stored.items() if k != "gl_distribution"})
    except ValueError:
        return True
    ignore = {"confidence_score"} | {k for k in filled if not stored.get(k)}
    shown, kept = coding.to_output(), before.to_output()
    return _comparable(shown, ignore, cost_centers) != _comparable(kept, ignore, cost_centers)


# A line's account or cost center not given, however it is written: the screen posts "UNASSIGNED" for a cost center
# stored blank, and that is no edit (B-801).
_BLANK_CODES = {None, "", UNASSIGNED}


def _comparable(output: dict[str, Any], ignore: set[str], cost_centers: bool = True) -> dict[str, Any]:
    out = {k: v for k, v in output.items() if k not in ignore}
    blank = ("predicted_gl_code", "predicted_cost_center")
    out["line_items"] = [
        {k: ("" if k in blank and (v in _BLANK_CODES or (k == blank[1] and not cost_centers)) else v)
         for k, v in li.items()}
        for li in out.get("line_items") or []
    ]  # fmt: skip
    return out


def override_key(errors: list[Any]) -> str:
    """Keyed on the errors: a new error (an edit after the tick) asks again instead of riding along."""
    seen = ",".join(sorted(f"{i.code}:{i.line_number}" for i in errors))
    return hashlib.sha1(seen.encode()).hexdigest()[:10]


def due_text(coding: dict[str, Any], default_days: int, vendor_terms: str = "") -> str:
    p = payment(coding, default_days, vendor_terms)
    text = terms_describe(p) + (" (vendor master terms)" if p.source == "vendor" else "")
    if p.discount_open():
        text += f" · {p.terms.discount_pct:g}% off until {p.discount_by:%b} {p.discount_by.day}"
    return text


# --- The sections ---------------------------------------------------------------------------------------------------


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
    look = len(warnings) + (1 if report.requires_review and not errors else 0)
    status = ((f"{len(errors)} to fix", "err") if errors else ("Needs attention", "warn") if look
              else ("Ready to approve", "ok"))  # fmt: skip
    value = max(0.0, min(1.0, report.adjusted_confidence or 0.0))
    return {
        "vendor": coding.vendor_name or "Unknown vendor",
        "number": coding.invoice_number,
        "status": status,
        "confidence": value,
        "confidence_text": confidence_pct(value, report.review_threshold),
        "confidence_ok": value >= report.review_threshold,
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


def reasons(coding: InvoiceCoding, ai: dict[str, Any], report: Any, reference: ReferenceData) -> list[dict[str, Any]]:
    """Why these codes: per line, the AI's reasoning, and whether the reviewer changed it or it matches history."""
    history = {h["line_number"]: h for h in report.checks.get("history") or []}
    rows = []
    for original, li in pair_lines(ai.get("line_items", []), list(coding.line_items)):
        before = original or {}
        badges = []
        if not before:
            badges.append(("Added by you", "info"))
        elif before.get("predicted_gl_code") != li.predicted_gl_code:
            badges.append((f"Changed from {before.get('predicted_gl_code')}", "info"))
        h = history.get(li.line_number)
        if h and h["status"] == "match":
            badges.append((f"Matches {h['decisions']} past decisions", "ok"))
        elif h:
            badges.append((f"Reviewers used {h['history_gl']} before", "warn"))
        rows.append({"number": li.line_number, "description": li.description, "gl": li.predicted_gl_code,
                     "gl_name": gl_name(reference, li.predicted_gl_code),
                     "why": before.get("reasoning_justification") or "Line added by the reviewer.",
                     "badges": badges})  # fmt: skip
    return rows


def suggestions(store: Store, reference: ReferenceData, coding: InvoiceCoding) -> list[dict[str, Any]]:
    """One-click GL accounts for lines without a usable one, from past decisions and the account list."""
    accounts = reference.chart_of_accounts
    lines = [(pos, li) for pos, li in enumerate(coding.line_items)
             if li.predicted_gl_code == UNASSIGNED or accounts.get(li.predicted_gl_code) is None]  # fmt: skip
    if not lines:
        return []
    feedback = store.learning_rows(coding.vendor_name)
    default_gl = (store.get_vendor(vendor_key(coding.vendor_name)) or {}).get("default_gl") or ""
    out = []
    for pos, li in lines:
        found = suggest_gl(li.description, coding.vendor_name, feedback, reference, default_gl=default_gl)
        if found:
            out.append({
                "position": pos, "number": li.line_number, "description": li.description,
                "options": [{"gl": s.gl_code, "label": f"{s.gl_code} · {gl_name(reference, s.gl_code) or s.gl_code}",
                             "cc": s.cost_center or "", "why": "; ".join(s.reasons).capitalize(),
                             "first": s.reasons[0] if s.reasons else ""} for s in found],
            })  # fmt: skip
    return out


def _fmt_qty(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def po_section(store: Store, coding: InvoiceCoding, invoice_id: int) -> dict[str, Any] | None:
    """How the invoice lines compare with the purchase order it quotes (2- or 3-way match); None without one."""
    if not coding.po_number.strip() or not store.has_purchase_orders():
        return None
    match = match_invoice(coding, store, invoice_id)
    if match is None:
        return None
    label = po_label(match.po_number)
    if not match.found:
        return {"found": False, "label": label}
    po = match.po or {}
    problems = sum(1 for m in match.lines if set(m.problems) - {"coding"})
    received = any(m.received is not None for m in match.lines)
    flags = {"price": "Price", "quantity": "Over ordered", "received": "Not received", "coding": "Coding"}
    rows = []
    for m in match.lines:
        if m.po_line is None:
            rows.append({"line": m.invoice_line, "description": m.description, "po_line": "", "qty": "",
                         "price": "", "price_po": "", "pills": [("Not on PO", "warn")]})  # fmt: skip
            continue
        if m.amount_only:  # a PO line with only an amount: compare amounts
            qty = f"{signed(m.billed_before_amount + m.amount)} / {signed(m.po_amount)}"
        else:
            qty = f"{_fmt_qty(m.billed_before + m.quantity)} / {_fmt_qty(m.po_quantity)}"
            if received:
                qty += f" / {_fmt_qty(m.received)}"
        pills = [(flags[p], "info" if p == "coding" else "warn") for p in m.problems if p in flags] or [("OK", "ok")]
        rows.append({"line": m.invoice_line, "description": m.description,
                     "po_line": f"{m.po_line} · {m.po_description}", "qty": qty, "price": signed(m.unit_price),
                     "price_po": signed(m.po_unit_price) if "price" in m.problems else "", "pills": pills})  # fmt: skip
    notes = [f"PO total {signed(po.get('total'))}"]
    if match.other_invoices:
        notes.append(f"{signed(match.billed_before)} already invoiced on "
                     + ", ".join(f"#{o['id']}" for o in match.other_invoices[:5]))  # fmt: skip
    notes.append(f"{'closed' if po.get('status') == 'closed' else 'open'}, for "
                 f"{(po.get('vendor_name') or '?').rstrip('.')}")  # fmt: skip
    return {
        "found": True, "label": label, "rows": rows, "received": received,
        "status": (f"{plural(problems, 'line')} to check", "warn") if problems else ("Matches the PO", "ok"),
        "notes": " · ".join(notes) + ". Billed quantities include earlier invoices on this PO.",
        "differs": len(match.coding_differs),
    }  # fmt: skip


def tax_check(coding: InvoiceCoding, reference: ReferenceData) -> list[dict[str, Any]]:
    """Each tax line's rate against the official rate, its arithmetic, and the account it posts to."""
    try:
        on = dt.date.fromisoformat(coding.invoice_date)
    except ValueError:
        on = dt.date.today()
    rows = []
    for t in coding.tax_lines:
        expected = round(t.taxable_amount * t.rate, 2)
        prov = t.province or place_of_supply(coding, reference.tax)
        official = reference.tax.rates.rate_for(t.tax_type, prov if prov in PROVINCE_NAMES else "", on)
        treat = reference.tax.treatment(t.tax_type)
        gl = treat.gl_code if treat.needs_gl else "expense lines"
        rows.append({
            "tax": TAX_LABELS.get(t.tax_type, t.tax_type), "province": t.province,
            "rate_ok": official is not None and abs(official - t.rate) < 1e-6, "rate": f"{t.rate * 100:.3f}%",
            "official": "not levied" if official is None else f"{official * 100:.3f}%",
            "math_ok": abs(expected - t.tax_amount) <= 0.01 * max(1, len(coding.line_items)) + 0.01,
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


def posting_note(coding: InvoiceCoding, reference: ReferenceData) -> str:
    """A word over the GL posting preview when a non-recoverable tax (PST) is spread over the lines marked with it
    while some lines' tax codes are still to check: the spread may be on the wrong lines (B-714)."""
    from ap_coder.coding.tax import EXPENSE_TO_LINE

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
    from ap_coder.erp import jde

    rows = []
    for e in output.get("gl_distribution") or []:
        account = None
        if js is not None:  # a recoverable tax line is posted by E1 itself (its tax AAIs): no account of ours
            recoverable = e["kind"] == "tax" and abs(float(e.get("non_recoverable_tax") or 0)) < 0.005
            mapped = jde.account_text(e["gl_code"] or "", e.get("cost_center") or "", js)
            account = "posted by JD Edwards" if recoverable else mapped or "not mapped"
        rows.append({
            "account": account,
            "kind": "Tax" if e["kind"] == "tax" else f"Line {e['line_number']}",
            "gl": gl_display(e["gl_code"]) if e["gl_code"] else "⚠ not mapped",
            "gl_name": gl_name(reference, e["gl_code"]) if reference is not None else "",
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
    from ap_coder.coding.validation import off_text

    off = off_text(total, output.get("grand_total") or 0)
    return {"rows": rows, "total": signed(total), "balanced": abs(diff) < 0.005, "off": signed(diff),
            "off_text": off[:1].upper() + off[1:],
            "jde": js is not None, "currency": output.get("currency") or ""}  # fmt: skip


# --- The analysis --------------------------------------------------------------------------------------------------


@dataclass
class Analysis:
    """The coding rebuilt from the screen, its checks, and what the templates show (``live``)."""

    coding: InvoiceCoding | None
    problems: list[str]
    output: dict[str, Any] = field(default_factory=dict)
    report: Any = None
    errors: list[Any] = field(default_factory=list)
    warnings: list[Any] = field(default_factory=list)
    allow: bool = False
    changed: bool = False  # differs from the stored coding (an edit not approved yet)
    live: dict[str, Any] = field(default_factory=dict)
    filled: dict[str, str] = field(default_factory=dict)  # details the server filled in (``settle_header``)


_TOTALS = ("subtotal", "tax_total", "grand_total")


def totals_settled(coding: InvoiceCoding, ai: dict[str, Any]) -> bool:
    """Whether the reviewer changed the amounts as read and they now add up (subtotal + tax = total, the tax lines
    = the tax): capture's "as printed, … ≠ total" no longer says anything about the invoice on screen (B-662)."""

    def cents(value: Any) -> int:
        try:
            return round(float(value or 0) * 100)
        except (TypeError, ValueError):
            return 0

    changed = any(cents(ai.get(f)) != cents(getattr(coding, f)) for f in _TOTALS)
    changed = changed or sorted(cents(t.get("tax_amount")) for t in ai.get("tax_lines") or []) != sorted(
        cents(t.tax_amount) for t in coding.tax_lines
    )
    adds_up = abs(cents(coding.subtotal) + cents(coding.tax_total) - cents(coding.grand_total)) <= 1
    taxes = abs(sum(cents(t.tax_amount) for t in coding.tax_lines) - cents(coding.tax_total)) <= 1
    return changed and adds_up and (taxes or not coding.tax_lines)


def analyse(store: Store, settings: Settings, reference: ReferenceData, inv: dict[str, Any], draft: Draft,
            signature: str = "") -> Analysis:  # fmt: skip
    """The screen's coding, checked as approval checks it. ``signature``: the reviewer's name, for the email to the
    vendor."""
    ai = inv["ai_output"] or {}
    start = inv.get("final_output") or ai
    has_cc = reference.cost_centers is not None
    filled = settle_header(store, draft, ai)  # the vendor list's tax numbers, the default Pay from account
    header, problems = header_values(draft.header)
    # A date typed another way, unclear or not a date: said at its field, the checks meanwhile on the date as read.
    dates = settle_dates(header, {**ai, **{k: v for k, v in start.items() if k in DATE_FIELDS}})
    header = dates.header
    filled.update(dates.filled)
    coding = None
    if not problems:
        coding, problems = coding_from_inputs(header, lines_frame(draft.lines), tax_frame(draft.taxes), ai,
                                              UNASSIGNED if has_cc else "")  # fmt: skip
    problems = [plain_problem(p) for p in problems]
    if coding is None:
        live = {"ok": False, "problems": problems, "amount_line": _amount_line(header),
                "fallback": stored_summary(inv, start)}  # fmt: skip
        return Analysis(coding=None, problems=problems, live=live, filled=filled)

    # What the screen fills itself on every check (not an edit, B-801): the default Pay from account (none picked for
    # this currency) and the tax numbers the vendor list filled, while they are as filled.
    auto = {"pay_from_account"} if not draft.pay_from_for else set()
    auto |= {n for n, v in (draft.from_vendor.get("fields") or {}).items() if draft.header.get(n) == v}
    meta = inv.get("meta") or {}
    extraction = ExtractionResult(
        source=inv["source_path"],
        model_id=(meta.get("extraction") or {}).get("model_id", ""),
        content="",
        mean_word_confidence=(meta.get("extraction") or {}).get("mean_word_confidence"),
        invoice_fields=(meta.get("extraction") or {}).get("invoice_fields") or {},
    )
    output, report = finalise_coding(coding, reference, settings, extraction, store=store,
                                     exclude_invoice_id=inv["id"], received=received_on(inv))  # fmt: skip
    # The AI reviewer's findings, on what is on screen now: a field it disputed that the reviewer changed is settled.
    from ap_coder.ai import verifier
    from ap_coder.coding.validation import Issue

    review = store.verification(inv["id"])
    for severity, code, message in verifier.issues((review or {}).get("result"), output):
        report.issues.append(Issue(severity, code, message))
    # What capture found about the page (pages not read, a currency or a date not printed, totals that don't add up as
    # printed...): kept with the invoice when it was read, and shown here too (B-458). A date not found is assumed
    # only while the date is still the one put in for it.
    capture = capture_of(store, inv["id"])
    if capture is not None:
        from ap_coder.capture.bridge import review_issues

        # Assumed while the date is still the one put in, unless a person approved it so (reopened, sent back) or
        # showed it on the page.
        confirmed = bool(inv.get("final_output")) or "invoice_date" in draft.taught
        assumed = coding.invoice_date if coding.invoice_date == ai.get("invoice_date") and not confirmed else None
        settled = totals_settled(coding, ai)
        for severity, code, message in review_issues(capture, coding.currency, assumed_date=assumed):
            if code == "CAPTURE_CHECK_FIELDS":  # the fields to check are highlighted in amber: no need to list them
                continue
            if code == "CAPTURE_TOTALS" and settled:  # the reviewer fixed the amounts and they add up now (B-662)
                continue
            report.issues.append(Issue(severity, code, message))
    if draft.absent:  # fields the reviewer said are not on this invoice: nothing asks for them (D-60)
        report.issues = settle_absent(report.issues, draft.absent)
    from_list = sorted((draft.from_vendor.get("fields") or {}).keys())
    report.issues += not_printed(coding, from_list)
    typed = dates.issues + currency_issues(coding.currency)  # the dates and the currency as typed (D-75)
    if typed:
        report.issues = typed + report.issues
        report.requires_review = True
    if from_list:
        output[FROM_VENDOR_LIST] = from_list  # kept with the coding: the tax return's claims at risk read it
    accounts = store.bank_accounts(active_only=True)
    if not accounts:  # nothing asks for a Pay from account, and the screen says where to add them (D-66)
        from ap_coder.coding.banks import INFO, NONE_YET

        report.issues.append(Issue(INFO, "PAY_FROM_NO_ACCOUNTS", NONE_YET))
    errors = [i for i in report.issues if i.severity == "error"]
    warnings = [i for i in report.issues if i.severity == "warning"]
    default_days = store.default_terms_days()
    terms = store.vendor_terms(vendor_key(coding.vendor_name))

    # The approve bar: never post to UNASSIGNED; where cost centers are used, a line without one isn't posted either.
    # The queue asks the same question (pages/review.py attention), so its Ready means Approve is open here.
    line_pick = pick = codes_to_pick(coding.line_items, uses_cost_centers(reference))
    from ap_coder.coding.banks import blocking

    # never approved without one of our accounts in use to pay from, whatever is ticked (D-66, B-683)
    bank = blocking(coding.pay_from_account, store.bank_accounts())
    if bank:
        pick = f"{pick}; {bank[0].lower()}{bank[1:]}" if pick else bank
    # What makes the voucher invalid is never approved anyway; the rest of the errors only with a reason (D-75).
    hard = [i for i in errors if is_hard(i)]
    soft = [i for i in errors if not is_hard(i)]
    blockers = ordered_blockers(line_pick, bank, hard)
    key = override_key(soft) if soft else ""
    reason = draft.override_reason.strip()
    allow = not pick and not hard and (not soft or (draft.override == key and bool(reason)))
    changed = ai_changes(coding, ai, uses_cost_centers(reference))
    lines_n = len(coding.line_items)
    _, rule_changes = rules.apply(coding, store.coding_rules())
    doc = coding.model_dump()
    if PLACE_OF_SUPPLY in output:  # worked out, no ship-to printed: the vendor is told where it is delivered
        doc[PLACE_OF_SUPPLY] = output[PLACE_OF_SUPPLY]
    mail = None
    if vendor_mail.points(doc, report.issues):
        chosen = draft.mail_lang if draft.mail_lang in vendor_mail.LANGUAGES else vendor_mail.suggested_language(doc)
        drafts = []
        for lang, name in vendor_mail.LANGUAGES.items():
            m = vendor_mail.draft(doc, report.issues, lang, signature)
            drafts.append({"lang": lang, "name": name, "subject": m.subject, "body": m.body, "mailto": m.mailto()})
        mail = {"chosen": chosen, "drafts": drafts}

    from ap_coder.erp import jde

    js = jde.load(store) if jde.in_use(store) else None  # JD Edwards is the export in use (D-75)
    an8 = jde.resolve_an8(output, store.vendor_ids(), js) if js is not None else ""
    live = {
        "ok": True,
        "problems": [],
        "amount_line": _amount_line(header),
        "summary": summary(coding, report, errors, warnings, default_days, terms, reference.tax),
        "checks": checks_summary(report, errors, warnings),
        "details": checks_details(report),
        "rules_applied": [rules.describe(c) for c in meta.get("rules_applied") or []],
        "rule_changes": {
            "numbers": ", ".join(str(c["line_number"]) for c in rule_changes),
            "help": "; ".join(rules.describe(c) for c in rule_changes).replace("the AI chose", "now"),
        } if rule_changes else None,
        "mail": mail,
        "reasons": reasons(coding, ai, report, reference),
        "suggestions": suggestions(store, reference, coding),
        "po": po_section(store, coding, inv["id"]),
        "tax_check": tax_check(coding, reference),
        "math": (math := math_check(coding, reference.tax)),
        "tax_marks": tax_marks(math, report.issues),
        "split": spend_split(output, reference),
        "posting": {**distribution(output, reference, js), "note": posting_note(coding, reference)},
        "flags": merge_flags(field_flags(report.issues, store, coding, inv["id"]), code_flags(report.issues),
                             dates.notes),
        "flag_errors": flag_errors(errors, dates.notes),
        "help": field_help(store, coding, draft, capture, dates, report.issues),
        "jde": jde.voucher_view(output, js, an8) if js is not None else None,
        "an8": an8 if js is not None else None,
        "from_vendor": from_list,
        "bar": {
            "amount": signed(coding.grand_total), "currency": coding.currency, "lines": lines_n, "changed": changed,
            "learn": learn_text(coding, ai, uses_cost_centers(reference), meta.get("rules_applied")),
            "pick": pick, "errors": len(soft), "override_key": key, "override": bool(key) and draft.override == key,
            "override_reason": draft.override_reason if bool(key) and draft.override == key else "",
            "hard": hard_reasons(hard), "allow": allow, "credit": coding.grand_total < 0,
            "first": blockers[0] if blockers else None, "blockers": blockers,
        },
    }  # fmt: skip
    return Analysis(coding=coding, problems=[], output=output, report=report, errors=errors, warnings=warnings,
                    allow=allow, changed=differs_from_stored(coding, start, cost_centers=has_cc, filled=auto),
                    live=live, filled=filled)  # fmt: skip


def pay_from_choices(store: Store, header: dict[str, str]) -> dict[str, Any]:
    """The Pay from list (D-66): our bank accounts in use, as "name · currency · ••••1234", the one chosen (an account
    no longer in use stays listed while it is the one chosen), and whether there are any."""
    from ap_coder.coding import banks

    chosen = header.get("pay_from_account") or ""
    accounts = [a for a in store.bank_accounts() if a["active"] or a["code"] == chosen]
    return {
        "options": [(a["code"], banks.label(a) + ("" if a["active"] else " (not in use — pick another)"), a["currency"])
                    for a in accounts],
        "value": chosen,
        "any": any(a["active"] for a in accounts),
        "unknown": bool(chosen) and all(a["code"] != chosen for a in accounts),
    }  # fmt: skip


def not_printed(coding: InvoiceCoding, from_list: list[str]) -> list[Any]:
    """The information that a tax number filled from the vendor list is not printed on the invoice, when the invoice
    charges that tax and is at or over the documentary threshold: from $100 (CRA, Input Tax Credit Information
    (GST/HST) Regulations; Revenu Québec the same for the QST), the supplier's registration number must be on the
    invoice to claim the input tax credit (refund). The vendor list's number is no substitute (D-65)."""
    from ap_coder.coding.taxreturn import document_threshold
    from ap_coder.coding.validation import INFO, Issue

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


def field_flags(
    issues: list[Any], store: Store, coding: InvoiceCoding, invoice_id: int | None = None
) -> dict[str, str]:
    """{invoice detail: the note under it} for the checks about one detail (``FIELD_FLAGS``): the screen highlights the
    detail in amber, with the note, as well as listing the check. The tax numbers' note says what the vendor list (or
    this vendor's approved invoices) has."""
    from ap_coder.coding.vendors import expected_tax_numbers

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


# --- Dates, currency and what can never be approved anyway -----------------------------------------------------------

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

# The findings that make a voucher invalid: "Approve anyway" never covers them (D-75). Every JD Edwards finding
# (JDE_..., erp/jde.voucher_findings) is one too: the batch would hold the voucher back.
HARD_CODES = frozenset({
    "MISSING_VENDOR", "MISSING_INVOICE_NUMBER", "NO_LINE_ITEMS", "SUBTOTAL_MISMATCH", "TOTAL_MISMATCH",
    "POSTING_UNBALANCED", "INVOICE_DATE_INVALID", "DUE_DATE_INVALID", "DATE_AMBIGUOUS", "CURRENCY_UNKNOWN",
})  # fmt: skip


def is_hard(issue: Any) -> bool:
    """An error "Approve anyway" never covers (``HARD_CODES``, and every JD Edwards finding)."""
    return issue.severity == "error" and (issue.code in HARD_CODES or issue.code.startswith("JDE_"))


DATE_FIELDS = ("invoice_date", "due_date")


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
    from ap_coder.capture.normalize import parse_dates
    from ap_coder.coding.validation import ERROR, Issue

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
            note = "No invoice date: type it from the page, e.g. 2026-10-05"
            out.issues.append(Issue(ERROR, "INVOICE_DATE_INVALID", note))
        elif name == "invoice_date":
            note = f"“{text}” is not a date: type it as on the page, e.g. 2026-10-05"
            out.issues.append(Issue(ERROR, "INVOICE_DATE_INVALID", f"{label}: {note}"))
        else:
            note = f"“{text}” is not a date: type it as on the page (e.g. 2026-11-04), or leave it blank"
            out.issues.append(Issue(ERROR, "DUE_DATE_INVALID", f"{label}: {note}"))
        out.notes[name] = note
        # the checks meanwhile: the date as read (or, never read, a placeholder no approval can keep)
        was = str(fallback.get(name) or "").strip()
        out.header[name] = was if _iso(was) else (dt.date.today().isoformat() if name == "invoice_date" else "")
    return out


def currency_issues(currency: str) -> list[Any]:
    """A three-letter currency that is no currency in use (XYZ): flagged, never a voucher (D-75). Not three letters is
    validation's own CURRENCY_FORMAT."""
    from ap_coder.coding.validation import ERROR, Issue

    code = (currency or "").strip().upper()
    if re.fullmatch(r"[A-Z]{3}", code) and code not in ISO_CURRENCIES:
        return [Issue(ERROR, "CURRENCY_UNKNOWN", f"“{code}” is not a currency code in use: type it as on the page, "
                      "e.g. CAD, USD or EUR")]  # fmt: skip
    return []


# The checks shown under the field they are about (their own words), besides FIELD_FLAGS' notes: {code: field}.
CODE_FIELDS = {
    "MISSING_VENDOR": "vendor_name", "VENDOR_NOT_IN_MASTER": "vendor_name", "VENDOR_ADDED_HERE": "vendor_name",
    "VENDOR_ON_HOLD": "vendor_name", "JDE_NO_AN8": "vendor_name", "JDE_BAD_AN8": "vendor_name",
    "MISSING_INVOICE_NUMBER": "invoice_number", "JDE_INVOICE_NUMBER_LONG": "invoice_number",
    "DUPLICATE_INVOICE": "invoice_number", "DUPLICATE_IN_ERP": "invoice_number",
    "INVOICE_DATE_NOT_FOUND": "invoice_date", "OLD_INVOICE_DATE": "invoice_date", "FUTURE_INVOICE_DATE": "invoice_date",
    "DUE_BEFORE_INVOICE": "due_date", "JDE_DATE_RANGE": "invoice_date",
    "CURRENCY_UNKNOWN": "currency", "CURRENCY_FORMAT": "currency", "JDE_CURRENCY": "currency",
    "PO_UNKNOWN": "po_number", "PO_CLOSED": "po_number", "PO_VENDOR_MISMATCH": "po_number", "JDE_PO_LONG": "po_number",
    "SUBTOTAL_MISMATCH": "subtotal", "TOTAL_MISMATCH": "grand_total", "TAX_LINES_TOTAL_MISMATCH": "tax_total",
    "JDE_TAX_AREA": "ship_to_province",
}  # fmt: skip
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


def _short_reason(text: str) -> str:
    # the first sentence ("e.g. CAD" is no sentence's end)
    return re.split(r"(?<=[a-z0-9)])(?<!e\.g)\. (?=[A-Z])|: (?=ask the pilot tester)", text, maxsplit=1)[0].rstrip(".")


def hard_reasons(hard: list[Any]) -> list[str]:
    """What keeps a voucher from being approved, even anyway: one short line each (D-75)."""
    return [_short_reason(text) for _bad, text in grouped(hard)]


# The review screen from top to bottom (B-797): the invoice details in the form's order, then the JD Edwards block,
# the lines, the tax lines and the posting. The approve bar says the first thing that keeps it closed, as the clerk
# meets it: a currency typed wrong before the bank account that follows it.
SCREEN_ORDER = (
    "vendor_name", "gst_hst_registration_number", "qst_registration_number", "pst_registration_number",
    "supplier_province", "remit_bank_account", "invoice_number", "invoice_date", "po_number", "payment_terms",
    "due_date", "currency", "pay_from_account", "ship_to_province", "original_invoice_number", "subtotal", "tax_total",
    "grand_total", "jde", "lines", "tax_lines", "posting",
)  # fmt: skip
_CODE_PLACES = {"NO_LINE_ITEMS": "lines", "POSTING_UNBALANCED": "posting"}


def ordered_blockers(line_pick: str, bank: str, hard: list[Any]) -> list[dict[str, str]]:
    """Everything that keeps Approve closed, in the order of the screen: {"text", "kind"} (``pick``: a line's account
    or cost center, a button to it; ``bank``: the Pay from account; ``hard``: what makes the voucher invalid)."""
    place = {name: i for i, name in enumerate(SCREEN_ORDER)}
    found: list[tuple[int, int, dict[str, str]]] = []
    for n, (_bad, text, code, lines) in enumerate(grouped_issues(hard)):
        where = "lines" if lines else CODE_FIELDS.get(code) or _CODE_PLACES.get(code) or "posting"
        found.append((place.get(where, len(place)), n, {"text": _short_reason(text), "kind": "hard"}))
    if line_pick:
        found.append((place["lines"], -1, {"text": line_pick, "kind": "pick"}))
    if bank:
        found.append((place["pay_from_account"], -1, {"text": bank, "kind": "bank"}))
    return [b for _, _, b in sorted(found, key=lambda f: (f[0], f[1]))]


def field_help(store: Store, coding: InvoiceCoding, draft: Draft, capture: CaptureResult | None, dates: DateCheck,
               issues: list[Any]) -> dict[str, dict[str, Any]]:  # fmt: skip
    """A note under an empty or unclear field with a one-click fix: which of two dates, a PO this vendor has open
    ("Use PO-89904"), a value boxed on the page while the field is empty ("Use BC-4410 from the page")."""
    from ap_coder.coding.po import po_label

    out: dict[str, dict[str, Any]] = {}
    for name, choices in dates.choices.items():
        out[name] = {"text": "Which date is it?",
                     "actions": [{"label": c["label"], "value": c["value"]} for c in choices]}  # fmt: skip
    header = draft.header
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


def _amount_line(header: dict[str, Any]) -> dict[str, Any] | None:
    """ "1,000.00 + 130.00 tax = 1,130.00 CAD", and whether it adds up (the inputs can't show separators)."""
    try:
        sub, tax, total = (float(header.get(f) or 0) for f in AMOUNT_FIELDS)
    except (TypeError, ValueError):
        return None
    return {"subtotal": signed(sub), "tax": signed(tax), "total": signed(total),
            "currency": header.get("currency") or "", "adds_up": abs(sub + tax - total) < 0.015}  # fmt: skip


# --- Grid actions ------------------------------------------------------------------------------------------------


def numbered_lines(lines: pd.DataFrame) -> pd.DataFrame:
    """A copy of the line grid with the lines just added numbered as the checks number them (after the last line,
    in order), so an action on "line 6" finds the row the reviewer added."""
    out = lines.copy()
    numbers = pd.to_numeric(out["line_number"], errors="coerce")
    next_no = int(numbers.max()) + 1 if numbers.notna().any() else 1
    for index, row in out.iterrows():
        if pd.notna(numbers[index]) or all(is_blank(row.get(k)) for k in ("description", "amount")):
            continue
        out.at[index, "line_number"] = next_no
        next_no += 1
    out["line_number"] = pd.to_numeric(out["line_number"], errors="coerce")
    return out


def grid_row(lines: pd.DataFrame, coding: InvoiceCoding, position: int) -> Any:
    """The grid rows of the ``position``-th line of the coding: the first, second… row with its number, as it comes in
    the coding (lines that share a number are told apart by their order). Every row with the number if unsure."""
    li = coding.line_items[position]
    same = lines["line_number"] == li.line_number
    nth = sum(1 for x in coding.line_items[:position] if x.line_number == li.line_number)
    rows = lines.index[same]
    return [rows[nth]] if nth < len(rows) else same


def split(draft: Draft, number: int, parts: list[tuple[str, str, float]], has_cc: bool) -> list[dict[str, Any]]:
    """Split line ``number`` across several GL accounts / cost centers by percentage (``review.split_line``).
    ValueError when the parts don't add up to 100% or there is no such line."""
    frame = lines_frame(draft.lines)
    rows = frame[pd.to_numeric(frame["line_number"], errors="coerce") == number]
    if rows.empty:
        raise ValueError(f"there is no line {number}")
    row = rows.iloc[0]
    gl, cc = row.get("predicted_gl_code") or UNASSIGNED, row.get("predicted_cost_center") or ""
    chosen = [(g or gl, (c or cc) if has_cc else "", p) for g, c, p in parts if p > 0]
    return lines_records(split_line(frame, number, chosen))


def apply_rules(store: Store, coding: InvoiceCoding, draft: Draft, invoice_id: int,
                ai: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:  # fmt: skip
    """Code the lines a fixed coding rule would code differently; records the rule's change for the AI's own lines,
    so its accuracy is measured on its own answer there. Returns the new rows and how many lines changed."""
    _, changes = rules.apply(coding, store.coding_rules())
    frame = lines_frame(draft.lines)
    numbers = pd.to_numeric(frame["line_number"], errors="coerce")
    done = []
    for c in changes:
        at = numbers == c["line_number"]
        if not at.any():  # a line just added (not numbered yet): left for the reviewer to code
            continue
        frame.loc[at, "predicted_gl_code"] = c["gl_to"]
        frame.loc[at, "predicted_cost_center"] = c["cc_to"]
        done.append(c)
    ai_lines = {li.get("line_number"): li for li in ai.get("line_items") or []}
    store.add_rules_applied(invoice_id, [
        {**c, "gl_from": ai_lines[c["line_number"]].get("predicted_gl_code", ""),
         "cc_from": ai_lines[c["line_number"]].get("predicted_cost_center", "")}
        for c in done
        if c["line_number"] in ai_lines
    ])  # fmt: skip
    return lines_records(frame), len(done)


def po_coding(store: Store, coding: InvoiceCoding, draft: Draft, invoice_id: int) -> tuple[list[dict[str, Any]], int]:
    """Set the GL account and cost center of the lines whose coding differs from the PO's to the PO's."""
    match = match_invoice(coding, store, invoice_id)
    differs = match.coding_differs if match is not None else []
    frame = numbered_lines(lines_frame(draft.lines))
    for m in differs:
        at = frame["line_number"] == m.invoice_line
        if m.po_gl:
            frame.loc[at, "predicted_gl_code"] = m.po_gl
        if m.po_cc:
            frame.loc[at, "predicted_cost_center"] = m.po_cc
    return lines_records(frame), len(differs)


def fill_lines(draft: Draft, gl: str, cc: str) -> tuple[list[dict[str, Any]], int, int]:
    """Give every line without a GL account ``gl`` and every line without a cost center ``cc`` (either may be "":
    left as it is); a line already coded keeps its code. Returns the new rows and how many lines got each."""
    frame = lines_frame(draft.lines)
    real = ~frame.apply(lambda row: all(is_blank(row.get(k)) for k in ("description", "amount")), axis=1)
    counts = []
    for column, code in (("predicted_gl_code", gl), ("predicted_cost_center", cc)):
        missing = real & frame[column].apply(lambda v: is_blank(v) or v == UNASSIGNED)
        if code:
            frame.loc[missing, column] = code
        counts.append(int(missing.sum()) if code else 0)
    return lines_records(frame), counts[0], counts[1]


def usual_cost_center(store: Store, reference: ReferenceData, vendor_name: str) -> str:
    """The one cost center every past decision on this vendor's lines used (still in the list), else "": offered
    for its lines without one. Two cost centers, or a line without one, and there is no usual one."""
    if not uses_cost_centers(reference) or not (vendor_name or "").strip():
        return ""
    used = {r.get("final_cc") or "" for r in store.feedback_rows(vendor_name=vendor_name)}
    only = next(iter(used)) if len(used) == 1 else ""
    return only if only != UNASSIGNED and reference.cost_centers.get(only) is not None else ""


def use_suggestion(reference: ReferenceData, coding: InvoiceCoding, draft: Draft, position: int, gl: str,
                   cc: str) -> list[dict[str, Any]]:  # fmt: skip
    """Code the ``position``-th line with a suggested GL account (and its cost center, when the line has none)."""
    if not 0 <= position < len(coding.line_items):
        raise ValueError("that line is no longer on the invoice")
    li = coding.line_items[position]
    frame = numbered_lines(lines_frame(draft.lines))
    at = grid_row(frame, coding, position)
    frame.loc[at, "predicted_gl_code"] = gl
    if cc and li.predicted_cost_center in ("", UNASSIGNED) and reference.cost_centers is not None:
        frame.loc[at, "predicted_cost_center"] = cc
    return lines_records(frame)
