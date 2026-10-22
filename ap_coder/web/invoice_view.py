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
from ap_coder.ai.pipeline import finalise_coding
from ap_coder.ai.suggest import suggest_gl
from ap_coder.capture.normalize import parse_amount, parse_date
from ap_coder.capture.types import MISSING, Box, CaptureResult
from ap_coder.coding import rules, vendor_mail
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
from ap_coder.core.memory import pair_lines, vendor_key
from ap_coder.core.reference_data import UNASSIGNED, ReferenceData, short_name
from ap_coder.core.schema import PROVINCE_VALUES, InvoiceCoding
from ap_coder.core.store import Store, nothing_proposed, suggestion_kept
from ap_coder.core.ui import TAX_LABELS, money, plural
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
FORM_FIELDS = (*TEACHABLE, "supplier_province", "ship_to_province", "remit_bank_account", "original_invoice_number")
FIELD_LABELS = {
    "vendor_name": "Vendor", "gst_hst_registration_number": "GST/HST #", "qst_registration_number": "QST #",
    "pst_registration_number": "PST #", "supplier_province": "Supplier province",
    "remit_bank_account": "Pay into (bank account)",
    "invoice_number": "Invoice #", "invoice_date": "Invoice date", "po_number": "PO #",
    "payment_terms": "Payment terms", "due_date": "Due date", "currency": "Currency",
    "ship_to_province": "Place of supply", "original_invoice_number": "Credits invoice #", "subtotal": "Subtotal",
    "tax_total": "Tax total", "grand_total": "Total",
}  # fmt: skip
LINE_COLUMNS = ("line_number", "description", "quantity", "unit_price", "amount", "predicted_gl_code",
                "predicted_cost_center", "taxes_applied", "reasoning_justification")  # fmt: skip
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
    "line_coding": "GL coding", "line_count": "lines added or removed", "tax_lines": "sales tax",
    "vendor_name": "vendor", "invoice_number": "invoice #", "invoice_date": "date", "po_number": "PO #",
    "payment_terms": "terms", "due_date": "due date", "currency": "currency",
    "supplier_province": "supplier province", "ship_to_province": "place of supply",
    "gst_hst_registration_number": "GST/HST #", "qst_registration_number": "QST #", "pst_registration_number": "PST #",
    "original_invoice_number": "invoice credited", "remit_bank_account": "bank account", "subtotal": "subtotal",
    "tax_total": "tax total", "grand_total": "total",
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
    reader_at: str = ""  # when OvisOCR2's reading came in, as the screen was drawn ("" before it)
    mail_lang: str = ""

    @classmethod
    def stored(cls, start: dict[str, Any], reader_at: str = "") -> Draft:
        """The screen as it opens: the stored coding (a sent-back invoice: its first approver's version)."""
        header = {}
        for name in FORM_FIELDS:
            value = start.get(name, "CAD" if name == "currency" else "")
            header[name] = _amount_text(value) if name in AMOUNT_FIELDS else _text(value)
        lines = [{c: li.get(c) for c in LINE_COLUMNS} for li in start.get("line_items") or []]
        for li in lines:
            li["taxes_applied"] = list(li.get("taxes_applied") or [])
        taxes = []
        for t in start.get("tax_lines") or []:
            try:
                rate_pct = round(float(t.get("rate") or 0) * 100, 4)
            except (TypeError, ValueError):
                rate_pct = None
            taxes.append({"tax_type": t.get("tax_type") or "", "province": t.get("province") or "",
                          "rate_pct": rate_pct, "taxable_amount": t.get("taxable_amount"),
                          "tax_amount": t.get("tax_amount")})  # fmt: skip
        return cls(header=header, lines=lines, taxes=taxes, reader_at=reader_at)

    @classmethod
    def from_form(cls, form: Any, base: Draft) -> Draft:
        """The screen as posted. A header field missing from the form keeps ``base``'s value; the grids are taken
        from the form when it says it has them (``grids``), so deleting the last row is an edit too."""
        header = {name: str(form.get(name)) if form.get(name) is not None else base.header.get(name, "")
                  for name in FORM_FIELDS}  # fmt: skip
        lines, taxes = base.lines, base.taxes
        if form.get("grids"):
            keys = form.getlist("line_key")
            cols = {c: form.getlist(name) for c, name in LINE_INPUTS.items()}
            lines = []
            for i, key in enumerate(keys):
                row: dict[str, Any] = {c: (cols[c][i] if i < len(cols[c]) else "") for c in LINE_INPUTS}
                row["line_number"] = _line_number(row["line_number"])
                row.update({c: _number_cell(row[c]) for c in NUMBER_CELLS if c in row})
                row["taxes_applied"] = [t for t in form.getlist(f"line_tax_{key}") if t in TAX_TYPES]
                lines.append(row)
            tcols = {c: form.getlist(name) for c, name in TAX_INPUTS.items()}
            taxes = [{c: (tcols[c][i] if i < len(tcols[c]) else "") for c in TAX_INPUTS}
                     for i in range(len(tcols["tax_type"]))]  # fmt: skip
            for t in taxes:
                t.update({c: _number_cell(t[c]) for c in NUMBER_CELLS if c in t})
        return cls(header=header, lines=lines, taxes=taxes, taught=parse_taught(form.get("taught")),
                   override=str(form.get("override") or ""), reader_at=str(form.get("reader_at") or base.reader_at),
                   mail_lang=str(form.get("mail_lang") or ""))  # fmt: skip

    def to_dict(self) -> dict[str, Any]:
        return {"header": self.header, "lines": self.lines, "taxes": self.taxes, "taught": self.taught,
                "reader_at": self.reader_at}  # fmt: skip


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


def grid(reference: ReferenceData, draft: Draft, ai: dict[str, Any] | None = None) -> dict[str, Any]:
    """What the line and tax grids need: the rows, with a stable key each, and the choices of every cell. ``ai``: the
    invoice as read: a row whose account is still the one the AI only guessed for its line is marked (B-463), not one
    whose account the reviewer, a coding rule or the PO coding put there (B-475)."""
    from ap_coder.core.store import line_guessed

    guesses = {
        id(row)
        for before, row in pair_lines((ai or {}).get("line_items") or [], draft.lines)
        if before and row and line_guessed(before) and row.get("predicted_gl_code") == before.get("predicted_gl_code")
    }
    return {  # "guessed": an account no approval backs, marked for the reviewer to check (B-463)
        "lines": [
            {
                **row,
                "key": f"r{i}",
                "guessed": id(row) in guesses and row.get("predicted_gl_code") not in (None, "", UNASSIGNED),
            }
            for i, row in enumerate(draft.lines)
        ],
        "taxes": draft.taxes,
        "gl_options": gl_options(reference, draft.lines),
        "cc_options": cc_options(reference, draft.lines),
        "tax_types": list(TAX_TYPES),
        "tax_labels": {t: TAX_LABELS.get(t, t) for t in TAX_TYPES},
        "tax_provinces": tax_province_options(),
        "has_cc": reference.cost_centers is not None,
    }


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
GOTO_BOX = {"GL_UNASSIGNED": "line_gl", "CC_UNASSIGNED": "line_cc"}


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
        items.append({"bad": kind != LOOK, "kind": kind, "text": text, "goto": goto})
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


def differs_from_stored(coding: InvoiceCoding, stored: dict[str, Any]) -> bool:
    """Whether the coding on screen is not the one stored for the invoice (the reviewer changed something)."""
    try:
        before = InvoiceCoding.model_validate({k: v for k, v in stored.items() if k != "gl_distribution"})
    except ValueError:
        return True
    ignore = {"confidence_score"}
    return {k: v for k, v in coding.to_output().items() if k not in ignore} != {
        k: v for k, v in before.to_output().items() if k not in ignore
    }


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
    meta = [
        (f"Invoice {coding.invoice_number}", "Invoice number"),
        (f"Issued {coding.invoice_date}" if coding.invoice_date else "", "Invoice date"),
        (due_text(coding.to_output(), default_days, vendor_terms) if coding.grand_total > 0 else "", "Payment due"),
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
        "status": status,
        "confidence": value,
        "confidence_text": confidence_pct(value, report.review_threshold),
        "confidence_ok": value >= report.review_threshold,
        "threshold": report.review_threshold,
        "meta": [(t, about) for t, about in meta if t],
        "taxes": [TAX_LABELS.get(t, t) for t in dict.fromkeys(t.tax_type for t in coding.tax_lines)],
        "learned": any(h["status"] == "match" for h in report.checks.get("history") or []),
        "total_label": "Credit" if coding.grand_total < 0 else "Total due",
        "total": money(coding.grand_total),
        "currency": coding.currency,
    }


def stored_summary(inv: dict[str, Any], doc: dict[str, Any]) -> dict[str, Any]:
    """The header of an invoice that is not being reviewed (approved, parked, rejected...): as stored."""
    supply = stored_supply(doc)
    meta = [
        (f"Invoice {doc.get('invoice_number') or inv.get('invoice_number') or '—'}", "Invoice number"),
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
        "total": money(total),
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
            qty = f"{money(m.billed_before_amount + m.amount)} / {money(m.po_amount)}"
        else:
            qty = f"{_fmt_qty(m.billed_before + m.quantity)} / {_fmt_qty(m.po_quantity)}"
            if received:
                qty += f" / {_fmt_qty(m.received)}"
        pills = [(flags[p], "info" if p == "coding" else "warn") for p in m.problems if p in flags] or [("OK", "ok")]
        rows.append({"line": m.invoice_line, "description": m.description,
                     "po_line": f"{m.po_line} · {m.po_description}", "qty": qty, "price": money(m.unit_price),
                     "price_po": money(m.po_unit_price) if "price" in m.problems else "", "pills": pills})  # fmt: skip
    notes = [f"PO total {money(po.get('total'))}"]
    if match.other_invoices:
        notes.append(f"{money(match.billed_before)} already invoiced on "
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
            "amount": money(t.tax_amount), "posts_to": gl or "⚠ not mapped",
        })  # fmt: skip
    return rows


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
    return [{"label": label, "amount": money(amount), "share": amount / total,
             "pct": f"{amount / total:.0%}"} for label, amount in segments]  # fmt: skip


def distribution(output: dict[str, Any], reference: ReferenceData | None) -> dict[str, Any]:
    """The journal lines approval sends to the ERP, their total and whether it balances with the invoice."""
    rows = []
    for e in output.get("gl_distribution") or []:
        rows.append({
            "kind": "Tax" if e["kind"] == "tax" else f"Line {e['line_number']}",
            "gl": gl_display(e["gl_code"]) if e["gl_code"] else "⚠ not mapped",
            "gl_name": gl_name(reference, e["gl_code"]) if reference is not None else "",
            "todo": e["gl_code"] in (UNASSIGNED, ""),
            "cc": cc_display(e.get("cost_center")),
            "description": e.get("description") or "",
            "net": money(e["net_amount"]) if e["kind"] == "expense" else "",
            "nonrec": money(e["non_recoverable_tax"]) if e.get("non_recoverable_tax") else "",
            "amount": money(e["amount"]),
        })  # fmt: skip
    total = round(sum(e["amount"] for e in output.get("gl_distribution") or []), 2)
    diff = round(total - (output.get("grand_total") or 0), 2)
    return {"rows": rows, "total": money(total), "balanced": abs(diff) < 0.005, "off": money(diff),
            "currency": output.get("currency") or ""}  # fmt: skip


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


def price_follows_amount(coding: InvoiceCoding, ai: dict[str, Any]) -> None:
    """On a line of quantity 1 whose amount the reviewer changed (its unit price left as read) or that has no unit
    price, the unit price is the amount: "quantity × price = 40.00 but the amount is 45.00" would only repeat the old
    reading (B-662)."""
    for before, li in pair_lines(ai.get("line_items") or [], list(coding.line_items)):
        if li.quantity != 1 or abs(li.unit_price - li.amount) < 0.005:
            continue
        price_as_read = before is not None and abs(float(before.get("unit_price") or 0) - li.unit_price) < 0.005
        amount_changed = before is not None and abs(float(before.get("amount") or 0) - li.amount) >= 0.005
        if (price_as_read and amount_changed) or not li.unit_price:
            li.unit_price = li.amount


def analyse(store: Store, settings: Settings, reference: ReferenceData, inv: dict[str, Any], draft: Draft,
            signature: str = "") -> Analysis:  # fmt: skip
    """The screen's coding, checked as approval checks it. ``signature``: the reviewer's name, for the email to the
    vendor."""
    ai = inv["ai_output"] or {}
    start = inv.get("final_output") or ai
    has_cc = reference.cost_centers is not None
    header, problems = header_values(draft.header)
    coding = None
    if not problems:
        coding, problems = coding_from_inputs(header, lines_frame(draft.lines), tax_frame(draft.taxes), ai,
                                              UNASSIGNED if has_cc else "")  # fmt: skip
    if coding is not None:
        price_follows_amount(coding, ai)
    if coding is None:
        live = {"ok": False, "problems": problems, "amount_line": _amount_line(header),
                "fallback": stored_summary(inv, start)}  # fmt: skip
        return Analysis(coding=None, problems=problems, live=live)

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
    errors = [i for i in report.issues if i.severity == "error"]
    warnings = [i for i in report.issues if i.severity == "warning"]
    default_days = store.default_terms_days()
    terms = store.vendor_terms(vendor_key(coding.vendor_name))

    # The approve bar: never post to UNASSIGNED; where cost centers are used, a line without one isn't posted either.
    # The queue asks the same question (pages/review.py attention), so its Ready means Approve is open here.
    pick = codes_to_pick(coding.line_items, uses_cost_centers(reference))
    key = override_key(errors) if errors else ""
    allow = not pick and (not errors or draft.override == key)
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
        "math": math_check(coding),
        "split": spend_split(output, reference),
        "posting": distribution(output, reference),
        "bar": {
            "amount": money(coding.grand_total), "currency": coding.currency, "lines": lines_n, "changed": changed,
            "learn": learn_text(coding, ai, uses_cost_centers(reference), meta.get("rules_applied")),
            "pick": pick, "errors": len(errors), "override_key": key, "override": bool(key) and draft.override == key,
            "allow": allow,
        },
    }  # fmt: skip
    return Analysis(coding=coding, problems=[], output=output, report=report, errors=errors, warnings=warnings,
                    allow=allow, changed=differs_from_stored(coding, start), live=live)  # fmt: skip


def _amount_line(header: dict[str, Any]) -> dict[str, Any] | None:
    """ "1,000.00 + 130.00 tax = 1,130.00 CAD", and whether it adds up (the inputs can't show separators)."""
    try:
        sub, tax, total = (float(header.get(f) or 0) for f in AMOUNT_FIELDS)
    except (TypeError, ValueError):
        return None
    return {"subtotal": money(sub), "tax": money(tax), "total": money(total), "currency": header.get("currency") or "",
            "adds_up": abs(sub + tax - total) < 0.015}  # fmt: skip


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
