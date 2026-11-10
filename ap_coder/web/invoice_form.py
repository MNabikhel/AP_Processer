"""The review screen's form: its fields, what is on the screen (``Draft``) and what the server fills in. No web code.

* The names: the invoice details (``FORM_FIELDS``, ``FIELD_LABELS``), the posted cells of a line and of a tax line
  (``LINE_INPUTS``, ``TAX_INPUTS``, ``LINE_REMARK``, ``LINE_TAX_CODE``), the details that may be marked "Not on this
  invoice" (``ABSENT_FIELDS``).
* ``Draft``: what is on the screen (the invoice details, the line grid, the tax grid, the words taught on the page),
  from the stored coding (``Draft.stored``) or from the posted form (``Draft.from_form``).
* What the server fills in before the details are checked (``settle_header``: the supplier a GST/HST number belongs
  to, the vendor list's tax numbers).
* What words picked on the page mean for a field or a line's cell (``form_value``, ``line_value``), and the words
  taught (``parse_taught``, ``taught_boxes``).
* The grids as the coding takes them (``lines_frame``, ``lines_records``, ``tax_frame``, ``header_values``), and how
  an amount is written (``signed``).

The other parts of the screen are its siblings (``invoice_view.py`` lists them).
"""

from __future__ import annotations

import datetime as dt
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from ap_coder.capture.normalize import find_amounts, parse_amount, parse_date
from ap_coder.capture.types import Box
from ap_coder.coding import banks
from ap_coder.coding.line_tax import fill_exempt, parse_code
from ap_coder.coding.money import LARGEST
from ap_coder.coding.remark import typed as typed_remark
from ap_coder.coding.review import is_blank, to_number
from ap_coder.coding.tax import TAX_TYPES, follow_supply
from ap_coder.core.memory import pair_lines, vendor_key
from ap_coder.core.schema import FROM_VENDOR_LIST
from ap_coder.core.store import Store
from ap_coder.core.store.vendor_master import with_number
from ap_coder.web import account_lines
from ap_coder.web.document import clean_boxes

# --- The names of the form ----------------------------------------------------------------------------------------

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
                "reasoning_justification", "remark", "remark_from")  # fmt: skip
LINE_REMARK = "line_remark"  # a line's remark for JD Edwards, one box a row, posted in order (D-213)
LINE_TAX_CODE = "line_tax_code"  # a line's tax code, one dropdown a row (D-64)
TAX_COLUMNS = ("tax_type", "province", "rate_pct", "taxable_amount", "tax_amount")
# The posted names of a grid row's cells (repeated once per row, in order).
LINE_INPUTS = {"line_number": "line_number", "description": "line_description", "amount": "line_amount",
               "predicted_gl_code": "line_gl", "predicted_cost_center": "line_cc", "quantity": "line_quantity",
               "unit_price": "line_unit_price", "reasoning_justification": "line_reason"}  # fmt: skip
TAX_INPUTS = {"tax_type": "tax_type", "province": "tax_province", "rate_pct": "tax_rate_pct",
              "taxable_amount": "tax_taxable", "tax_amount": "tax_amount"}  # fmt: skip
NO_PROVINCE = "—"


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


# --- Amounts, numbers and dates as typed --------------------------------------------------------------------------


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
    """A number cell of the grids as posted, or "" when it is not a finite number ("1e309" reads as infinity) or
    is longer than any amount (27 digits, B-2096), which the checks and the page could not use."""
    text = str(value or "").strip()
    return text if _finite(text) is not None else ""


def _finite(value: Any) -> float | None:
    """A number as typed or read ("1,234.50", "$12"), None when it is no finite number, or one beyond the largest
    amount (``money.LARGEST``)."""
    try:
        number = float(str(value or 0).replace(",", "").replace("$", ""))
    except ValueError:
        return None
    return number if abs(number) < LARGEST else None  # (a NaN is under nothing)


def _number(value: Any) -> float:
    number = _finite(value)
    return 0.0 if number is None else number


def _line_number(value: Any) -> int | None:
    if is_blank(value):
        return None
    try:
        number = float(str(value).strip())
    except ValueError:
        return None
    return int(number) if math.isfinite(number) else None


def _date_or_none(text: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(text or "").strip()[:10])
    except ValueError:
        return None


# --- What is on the screen ----------------------------------------------------------------------------------------


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
    def from_form(cls, form: Any, base: Draft, pairs: Any = None) -> Draft:
        """The screen as posted. A header field missing from the form keeps ``base``'s value; the grids are taken
        from the form when it says it has them (``grids``), so deleting the last row is an edit too. A row's Account
        box (``account_lines.LINE_ACCOUNT``, with a chart of accounts: ``pairs``) is the line's two codes, whatever
        the row's hidden cells say: what was typed is kept and checked, never the account the line had."""
        header = {name: str(form.get(name)) if form.get(name) is not None and name not in READ_ONLY
                  else base.header.get(name, "") for name in FORM_FIELDS}  # fmt: skip
        lines, taxes = base.lines, base.taxes
        if form.get("grids"):
            keys = form.getlist("line_key")
            cols = {c: form.getlist(name) for c, name in LINE_INPUTS.items()}
            codes = form.getlist(LINE_TAX_CODE)
            accounts = form.getlist(account_lines.LINE_ACCOUNT)
            lines = []
            for i, key in enumerate(keys):
                row: dict[str, Any] = {c: (cols[c][i] if i < len(cols[c]) else "") for c in LINE_INPUTS}
                if i < len(accounts):
                    row["predicted_gl_code"], row["predicted_cost_center"] = account_lines.posted(accounts[i], pairs)
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
            # The remark's box (D-213): as posted, a person's once it is not what the line had (``remark.typed``);
            # a form without the box keeps each line's remark.
            remarks = form.getlist(LINE_REMARK)
            for i, (before, row) in enumerate(pair_lines(base.lines, lines)):
                row["remark"], row["remark_from"] = (
                    typed_remark(remarks[i], before) if i < len(remarks)
                    else ((before or {}).get("remark"), (before or {}).get("remark_from")))  # fmt: skip
            tcols = {c: form.getlist(name) for c, name in TAX_INPUTS.items()}
            taxes = [{c: (tcols[c][i] if i < len(tcols[c]) else "") for c in TAX_INPUTS}
                     for i in range(len(tcols["tax_type"]))]  # fmt: skip
            for t in taxes:
                t.update({c: _number_cell(t[c]) for c in NUMBER_CELLS if c in t})
        supply = header.get("ship_to_province", "").strip().upper()
        if supply != base.header.get("ship_to_province", "").strip().upper():  # tax rows put in the old one follow
            taxes = follow_supply(taxes, supply, _date_or_none(header.get("invoice_date")))
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
    """What the server fills in the invoice details before they are checked: the supplier a GST/HST number read or
    typed belongs to, when none is chosen (``supplier_by_number``, D-219), the vendor list's tax numbers (D-65) and
    the default Pay from account (D-66). Returns {field: value} changed, for the screen to show."""
    header = draft.header
    one = "" if str(header.get("vendor_name") or "").strip() else supplier_by_number(store, draft)
    if one:
        header["vendor_name"] = one
    key = vendor_key(header.get("vendor_name") or "")
    # of two vendors whose names read alike, the one with the GST/HST number on screen (B-1021)
    vendor = store.find_vendor(header.get("vendor_name"), header.get("gst_hst_registration_number")) if key else None
    key = (vendor or {}).get("vendor_key") or key
    draft.from_vendor, changed = fill_from_vendor(header, draft.from_vendor, draft.absent, vendor, unread_numbers(read))
    if one:
        changed["vendor_name"] = one
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


def supplier_by_number(store: Store, draft: Draft) -> str:
    """The name, as the vendor field takes it, of the ONE supplier in use whose GST/HST number is the one in the
    invoice's box (``Store.supplier_of_tax_number``); "" when the box is empty, holds a number filled from the
    vendor list (that vendor's own: it says nothing of who sent the invoice) or one that is no supplier's, or
    several suppliers'. A name several supplier numbers go by comes with its number (B-2161)."""
    number = str(draft.header.get("gst_hst_registration_number") or "").strip()
    if not number or "gst_hst_registration_number" in (draft.from_vendor.get("fields") or {}):
        return ""
    one = store.supplier_of_tax_number(number)
    if one is None:
        return ""
    return with_number(one) if store.vendors_sharing(one["display_name"]) else str(one["display_name"])


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


# --- Words picked on the page -------------------------------------------------------------------------------------


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


# --- The grids and the details as the coding takes them -----------------------------------------------------------


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
            # "nan" and "inf" read as numbers, but no check can use them (a NaN total is never over the limit); nor
            # 27 digits, longer than any amount (B-2096).
            if abs(number) < LARGEST:
                out[name] = number
            else:
                problems.append(f"{FIELD_LABELS[name]}: “{text}” is not an amount")
        else:
            out[name] = text
    return out, problems
