# ruff: noqa: E501  (label and pattern tables read best one per line)
"""Invoice capture: layout, readers, confidence, supplier learning (see docs/CAPTURE_DESIGN.md).

``analyze`` reads one invoice with every reader available and returns, per header field, the value,
a calibrated confidence, a status (verified / likely / check / missing), the boxes on the page and
the reasons, plus the cross-field checks.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
import re
from pathlib import Path
from typing import Any

from .confidence import LABELS, TAX_FIELDS, fuse
from .instructions import addressed_to_software
from .layout import IMAGE_EXTENSIONS, CannotRead, build_layout, ocr_available, read_pages
from .locate import locate
from .normalize import find_amounts
from .not_invoice import not_an_invoice
from .reader import (
    STRAIGHTEN_FROM,
    _on_tilted_page,
    _table_header_line,
    credit_titled,
    listed_items,
    read_fields,
    read_line_items,
    without_account_rows,
)
from .types import (
    AMOUNT_FIELDS,
    CHECK,
    EXTRA_FIELDS,
    FIELDS,
    MISSING,
    Box,
    CaptureResult,
    DocLayout,
    FieldResult,
    LineReading,
    Reading,
    union_all,
)

log = logging.getLogger(__name__)

# One word in capitals ("POSTES" for "POSTES CANADA"): often part of a name, or a heading read as one.
_ONE_CAPS_WORD = re.compile(r"[^\W\d_]{2,}")
ONE_WORD_REASON = "the vendor reads as one word in capitals: check it is the whole name"


def doubtful_vendor(value: Any, customer_names: set[str]) -> str:
    """Why the vendor name read is doubtful, "" when it is not: only a legal form ("Inc.", "Limitée"), one word in
    capitals ("POSTES"), or the name printed in the bill-to block (ours, as the customer). ``customer_names``: the
    bill-to lines, folded (``reader.plain``). Such a name is shown "check", never verified (B-669)."""
    from ..core.memory import _LEGAL_SUFFIXES
    from .reader import plain

    text = " ".join(str(value or "").split())
    if not text:
        return ""
    words = re.findall(r"[a-z0-9]+", plain(text))
    if not words or all(w in _LEGAL_SUFFIXES for w in words):
        return "the vendor reads as a legal form only: check the name on the page"
    if _ONE_CAPS_WORD.fullmatch(text) and text.isupper():
        return ONE_WORD_REASON
    if plain(text) in customer_names:
        return "the vendor reads as the name in the bill-to (yours, as the customer): check who the vendor is"
    return ""


def _approved_before(template: Any, value: Any) -> bool:
    """Whether AP approved this very vendor name (its ``vendor_key``) on a page of this supplier's template."""
    from ..core.memory import vendor_key

    key = vendor_key(str(value or ""))
    documents = (
        template.get("documents") if isinstance(template, dict) else getattr(template, "documents", None)
    ) or {}
    return bool(key) and any(key in (seen or {}).get("vendor_name", []) for seen in documents.values())


def _mark_doubtful_vendor(layout: DocLayout, fields: dict[str, FieldResult], checks: list[dict[str, Any]],
                          template: Any = None) -> None:  # fmt: skip
    """A doubtful vendor name (``doubtful_vendor``) is "check", unless the vendor master confirms it by its name; a
    one-word name (TELUS, IKEA) also not when AP approved that very name for this supplier before (``template``: the
    supplier's, B-675), so such a vendor can still go through on its own without a vendor master."""
    from .reader import _in_bill_to, plain

    fr = fields.get("vendor_name")
    if fr is None or fr.status in (CHECK, MISSING) or not fr.value:
        return
    if any(c.get("code") == "VENDOR_NAME_MATCH" and c.get("ok") for c in checks):
        return
    customers = {plain(ln.text.strip()) for ln in layout.lines() if _in_bill_to(layout, ln)}
    why = doubtful_vendor(fr.value, customers)
    if why == ONE_WORD_REASON and _approved_before(template, fr.value):
        return
    if why:
        fr.status, fr.confidence = CHECK, min(fr.confidence, 0.6)
        fr.reasons = [*fr.reasons, why]


# The readings' confidence was measured on scans of 150 dpi and up. On a coarser page (a fax at 100 dpi: two thirds of
# its values right in the October 2026 stress test, some wrong ones shown as verified) a person checks every value.
COARSE_DPI = 140

__all__ = ["LABELS", "analyze", "build_layout", "read_fields", "locate", "CaptureResult", "CannotRead"]

# Azure Document Intelligence's ``prebuilt-invoice`` fields (API 2024-11-30) -> capture's (D-245). VendorTaxId goes
# where its pattern says (``_di_tax_id``); CustomerTaxId, the customer's, never. The currency is the totals' code.
# Every part of an answer is checked for its shape: one of another shape is left out, never a crash.
DI_FIELDS = {
    "VendorName": "vendor_name",
    "InvoiceId": "invoice_number",
    "InvoiceDate": "invoice_date",
    "DueDate": "due_date",
    "PurchaseOrder": "po_number",
    "VendorTaxId": "gst_hst_registration_number",
    "SubTotal": "subtotal",
    "TotalTax": "tax_total",
    "InvoiceTotal": "grand_total",
    "PaymentTerm": "payment_terms",
}
# TaxDetails' rate -> the tax it is: only a rate that names one tax (13 % is HST, 9.975 % QST); any other is left to
# TotalTax. Read only off an invoice in Canadian dollars from a Canadian supplier: a US 7 % sales tax is no PST.
DI_TAX_RATES = {0.05: "gst_amount", 0.13: "hst_amount", 0.14: "hst_amount", 0.15: "hst_amount",
                0.09975: "qst_amount", 0.06: "pst_amount", 0.07: "pst_amount"}  # fmt: skip
_RATE = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_DI_LARGEST = 1e12  # an amount past this is no invoice's
_DI_MOST_LINES = 2000


def _di_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _di_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _di_number(value: Any) -> float | None:
    """A finite number of a sane size, else None (text, NaN, 1e300, a boolean)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and abs(number) < _DI_LARGEST else None


def _di_value(fld: Any) -> Any:
    """A field's value: an amount (a sane number), a date or a text; None for anything else."""
    fld = _di_dict(fld)
    if "valueCurrency" in fld:
        return _di_number(_di_dict(fld["valueCurrency"]).get("amount"))
    for key in ("valueString", "valueDate", "valueNumber", "content"):
        if key in fld:
            value = fld[key]
            if key == "valueNumber":
                return _di_number(value)
            return " ".join(value.split()) if isinstance(value, str) else None
    return None


def _di_score(fld: dict[str, Any]) -> float:
    score = _di_number(fld.get("confidence"))
    return 0.5 if score is None else min(1.0, max(0.0, score))


def _di_pages(raw: dict[str, Any]) -> dict[int, dict[str, Any]]:
    pages = {}
    for i, page in enumerate(_di_list(raw.get("pages"))):
        number = _di_number(_di_dict(page).get("pageNumber"))
        pages[int(number) if number is not None else i + 1] = _di_dict(page)
    return pages


def _di_boxes(fld: dict[str, Any], pages: dict[int, dict[str, Any]]) -> list[Box]:
    """Where Azure read it: each region's polygon (inches on a PDF, pixels on a picture) as a page-relative box; one
    of another shape, or off its page, is left out."""
    boxes = []
    for region in _di_list(fld.get("boundingRegions")):
        region = _di_dict(region)
        number = _di_number(region.get("pageNumber"))
        page = pages.get(int(number)) if number is not None else None
        poly = [_di_number(v) for v in _di_list(region.get("polygon"))]
        w, h = (_di_number(page.get("width")), _di_number(page.get("height"))) if page else (None, None)
        if not page or len(poly) < 8 or None in poly or not w or not h or w <= 0 or h <= 0:
            continue
        xs, ys = poly[0::2], poly[1::2]
        x0, y0, x1, y1 = min(xs) / w, min(ys) / h, max(xs) / w, max(ys) / h
        if 0.0 <= x0 <= x1 <= 1.0 and 0.0 <= y0 <= y1 <= 1.0:
            boxes.append(Box(int(number), x0, y0, x1, y1))
    return boxes


def _di_reading(field: str, fld: dict[str, Any], pages: dict[int, dict[str, Any]], value: Any = None) -> Reading:
    value = _di_value(fld) if value is None else value
    content = fld.get("content")
    raw = content if isinstance(content, str) and content.strip() else str(value)
    return Reading(field, value, raw, _di_boxes(fld, pages), _di_score(fld), "di")


def _di_tax_id(value: Any) -> str:
    """Where Azure's VendorTaxId goes: the GST/HST number or the QST number, by its pattern; "" for neither."""
    from .normalize import gst_valid, qst_valid

    if gst_valid(str(value)):
        return "gst_hst_registration_number"
    return "qst_registration_number" if qst_valid(str(value)) else ""


def _di_rate(fld: dict[str, Any]) -> float | None:
    """A TaxDetails rate as a fraction: Azure's number (13, or 0.13), else the number before a "%" in its text
    ("GST 2026: 5 %", "9,975 %"), else its only number; None when there is none."""
    number = _di_number(fld.get("valueNumber"))
    if number is None:
        text = fld.get("valueString") if isinstance(fld.get("valueString"), str) else fld.get("content")
        text = text if isinstance(text, str) else ""
        found = _RATE.search(text) or (_NUMBER.fullmatch(text.strip()) if text.strip() else None)
        if found is None:
            return None
        number = float((found.group(1) if found.groups() else found.group()).replace(",", "."))
    return round(number if number < 1 else number / 100, 5)


def _di_canadian(fields: dict[str, Any]) -> bool:
    """The invoice is in Canadian dollars from a supplier in Canada, as far as Azure's answer says."""
    total = _di_dict(fields.get("InvoiceTotal") or fields.get("SubTotal"))
    code = _di_dict(total.get("valueCurrency")).get("currencyCode")
    if isinstance(code, str) and code.strip() and code.strip().upper() != "CAD":
        return False
    country = _di_dict(_di_dict(fields.get("VendorAddress")).get("valueAddress")).get("countryRegion")
    return not (isinstance(country, str) and country.strip().upper() not in ("CA", "CAN", "CANADA"))


def _di_taxes(fields: dict[str, Any], pages: dict[int, dict[str, Any]]) -> dict[str, list[Reading]]:
    """Each tax of TaxDetails by its rate (``DI_TAX_RATES``); two amounts at one rate say neither."""
    if not _di_canadian(fields):
        return {}
    found: dict[str, list[Reading]] = {}
    for item in _di_list(_di_dict(fields.get("TaxDetails")).get("valueArray")):
        detail = _di_dict(_di_dict(item).get("valueObject"))
        amount, rate = _di_dict(detail.get("Amount")), _di_dict(detail.get("Rate"))
        name = DI_TAX_RATES.get(_di_rate(rate) or -1.0)
        if name and _di_value(amount) is not None:
            found.setdefault(name, []).append(_di_reading(name, amount, pages))
    return {name: readings for name, readings in found.items() if len(readings) == 1}


def _di_document(raw: Any) -> dict[str, Any]:
    """The answer's first document's fields ({} for an answer of another shape)."""
    docs = _di_list(_di_dict(raw).get("documents"))
    return _di_dict(_di_dict(docs[0]).get("fields")) if docs else {}


def _di_readings(raw: dict[str, Any]) -> dict[str, list[Reading]]:
    """Azure's reading of the header, one reading a field (``DI_FIELDS``, the taxes by rate, the currency)."""
    fields = _di_document(raw)
    if not fields:
        return {}
    pages = _di_pages(raw)
    out: dict[str, list[Reading]] = {}
    for di_name, field in DI_FIELDS.items():
        fld = _di_dict(fields.get(di_name))
        value = _di_value(fld)
        if value in (None, ""):
            continue
        if di_name == "VendorTaxId":
            field = _di_tax_id(value)
            if not field:
                continue
        out[field] = [_di_reading(field, fld, pages, value)]
    out.update(_di_taxes(fields, pages))
    total = _di_dict(fields.get("InvoiceTotal") or fields.get("SubTotal"))
    code = _di_dict(total.get("valueCurrency")).get("currencyCode")
    code = code.strip().upper() if isinstance(code, str) else ""
    if re.fullmatch(r"[A-Z]{3}", code):
        out["currency"] = [Reading("currency", code, code, [], _di_score(total), "di")]
    return out


def _di_lines(raw: dict[str, Any]) -> list[LineReading]:
    """Azure's line items (Items), each boxed where Azure read its row; a row without an amount is left out."""
    fields = _di_document(raw)
    if not fields:
        return []
    pages = _di_pages(raw)
    lines = []
    for item in _di_list(_di_dict(fields.get("Items")).get("valueArray"))[:_DI_MOST_LINES]:
        item = _di_dict(item)
        row = _di_dict(item.get("valueObject"))

        def number(name: str, row: dict[str, Any] = row) -> float | None:
            value = _di_value(row.get(name))
            return float(value) if isinstance(value, (int, float)) else None

        amount, tax = number("Amount"), number("Tax")
        if amount is None:
            continue
        description = _di_value(row.get("Description"))
        lines.append(LineReading(description if isinstance(description, str) else "", number("Quantity"),
                                 number("UnitPrice"), amount, _di_boxes(item, pages), _di_score(item), "di",
                                 taxes_read=None if tax is None else {"TAX": tax}))  # fmt: skip
    return lines


def _di_sources(raw: dict[str, Any] | None) -> tuple[dict[str, list[Reading]], list[LineReading]]:
    """Azure's readings and lines, or none of either when its answer can't be read: the invoice is read without it."""
    if not raw:
        return {}, []
    try:
        return _di_readings(raw), _di_lines(raw)
    except Exception as exc:  # noqa: BLE001 - an extra reader: never stops the invoice
        log.warning("Azure Document Intelligence's answer not read (%s)", type(exc).__name__)
        return {}, []


def _with_di_lines(items: list[LineReading], di_items: list[LineReading]) -> list[LineReading]:
    """The page's line items, each marked read the same by Azure when Azure's rows agree with them; Azure's rows only
    where the page gave none (D-245: they never replace the page's own)."""
    from .line_items import agree, mark_agreed

    if not di_items:
        return items
    if not items:
        return di_items
    if agree(items, di_items):
        mark_agreed(items, "di")
    return items


def _ai_readings(layout: DocLayout, ai_values: dict[str, Any]) -> dict[str, list[Reading]]:
    out: dict[str, list[Reading]] = {}
    for field, value in ai_values.items():
        if field not in FIELDS or value in (None, "", 0) and field not in ("subtotal", "grand_total"):
            continue
        found = locate(layout, field, value)
        if found:
            best = found[0]
            out[field] = [Reading(field, value, best.raw, best.boxes, min(1.0, 0.75 + 0.25 * best.score), "ai-located")]
        else:
            # Not found on the page: weak evidence (it may be derived, or invented).
            out[field] = [Reading(field, value, str(value), [], 0.45, "ai-not-on-page")]
    return out


def _page_reader(layout: DocLayout, page_text: list[str], today: dt.date | None,
                 left_out: list[LineReading] | None = None,
                 ) -> tuple[dict[str, list[Reading]], list[LineReading]]:  # fmt: skip
    """The page reader's transcription read by the rule reader: its readings, each with the boxes where its
    value is printed on the page (none when the page's text does not show it: it cannot be verified alone),
    and its line items. Its first choice only, but for amounts (the runners-up there let the totals that add
    up be found): a transcription has no columns, so a label's "value below" is often just the next line."""
    from .transcript import layout_from_transcript

    vlm = layout_from_transcript(page_text)
    out: dict[str, list[Reading]] = {}
    for field, readings in read_fields(vlm, received=today).items():
        out[field] = []
        for r in readings[: 3 if field in AMOUNT_FIELDS else 1]:  # at most what fusion weighs
            found = locate(layout, field, r.value)
            out[field].append(Reading(field, r.value, r.raw, list(found[0].boxes) if found else [], r.score, r.method))
    return out, read_line_items(vlm, left_out=left_out)


def _line_targets(fields: dict[str, FieldResult]) -> list[float]:
    """What the line items should add up to: the subtotal, or the total less the taxes and charges."""

    def amount(f: str) -> float | None:
        fr = fields.get(f)
        ok = fr is not None and fr.status != MISSING and isinstance(fr.value, (int, float))
        return float(fr.value) if ok else None

    targets = [] if amount("subtotal") is None else [amount("subtotal")]
    total = amount("grand_total")
    if total is not None:
        taxes = [t for t in (amount(f) for f in TAX_FIELDS) if t is not None]
        tax = sum(taxes) if taxes else (amount("tax_total") or 0.0)
        targets.append(round(total - tax - (amount("other_charges") or 0.0), 2))
    return targets


def _adds_up(items: list[LineReading], targets: list[float]) -> bool:
    amounts = [li.amount for li in items if li.amount is not None]
    return bool(amounts) and any(abs(round(sum(amounts), 2) - t) <= 0.02 for t in targets)


def _choose_lines(layout: DocLayout, fields: dict[str, FieldResult], own: list[LineReading],
                  vlm: list[LineReading]) -> list[LineReading]:  # fmt: skip
    """The page reader's line items when they add up to the invoice's subtotal and the page's own don't (each
    row boxed where its amount is printed on the page). On a scan, when both add up to the same amounts row by
    row, the page reader's descriptions, quantities and prices are taken on the page's own row boxes (OCR breaks
    words up and misplaces a tilted row's cells; the page reader reads a table cell by cell)."""
    targets = _line_targets(fields)
    if not vlm or not targets or not _adds_up(vlm, targets):
        return own
    if not _adds_up(own, targets):
        return _boxed_rows(layout, vlm)
    same = len(own) == len(vlm) and all(
        a.amount is not None and b.amount is not None and abs(a.amount - b.amount) <= 0.005
        for a, b in zip(own, vlm, strict=True)
    )
    if same and layout.source in ("ocr", "mixed"):
        return [LineReading(b.description, b.quantity, b.unit_price, b.amount, list(a.boxes), max(a.score, b.score),
                            "vlm", sorted({a.source, *a.agreed} - {"", "vlm"}), b.tax_flags or a.tax_flags,
                            taxes_read=a.taxes_read)
                for a, b in zip(own, vlm, strict=True)]  # fmt: skip
    return own


def _to_subtotal(items: list[LineReading], targets: list[float]) -> list[LineReading]:
    """``items`` without the rows read past the end of the table: when the lines don't add up to the subtotal, but the
    rows above the first row that prints their sum do (that row is the subtotal, its label misread, as OCR's
    "Totalbefore.tax"), it and the rows after it (the taxes, the total) are no lines. A row with a quantity is a line."""
    if not targets or _adds_up(items, targets):
        return items
    above = 0.0
    for k, li in enumerate(items):
        if li.amount is None:
            continue
        if k and li.quantity is None and abs(round(above, 2) - li.amount) <= 0.011 and _adds_up(items[:k], targets):
            return items[:k]
        above += li.amount
    return items


_DISCOUNT_LINE = re.compile(r"\b(?:discount|escompte|rabais|remise|rebate|coupon)", re.I)


def _discounts_taken_off(items: list[LineReading], targets: list[float]) -> list[LineReading]:
    """``items`` with a discount's line printed without its sign ("Discount 5%  2,689.02", OCR's lost "-" or
    brackets) taken off, when that, and not adding it, makes the lines add up to the subtotal."""
    if not targets or _adds_up(items, targets):
        return items
    off = [LineReading(li.description, li.quantity, li.unit_price, -li.amount, list(li.boxes), li.score, li.source,
                       list(li.agreed), li.tax_flags)
           if li.amount and li.amount > 0 and li.quantity is None and _DISCOUNT_LINE.search(li.description) else li
           for li in items]  # fmt: skip
    return off if off != items and _adds_up(off, targets) else items


ACCOUNT_ROWS_CHECK = "ACCOUNT_ROWS"


def account_rows_check(left_out: list[LineReading]) -> dict[str, Any]:
    """The check that names the rows of the line table left out as the supplier's account, not lines of the invoice
    (``reader.without_account_rows``): passed, said as an info finding (``bridge.review_issues``)."""
    named = ", ".join(f"“{li.description[:60]}” {li.amount:,.2f}" for li in left_out[:6] if li.amount is not None)
    more = f" and {len(left_out) - 6} more" if len(left_out) > 6 else ""
    one = len(left_out) == 1
    return {"code": ACCOUNT_ROWS_CHECK, "ok": True, "fields": [], "severity": "info",
            "detail": f"{'a row' if one else 'rows'} of the line table left out: {named}{more}. "
                      f"{'It is' if one else 'They are'} the supplier's account (an earlier balance, a payment), as "
                      "on a statement: no line of this invoice, so not coded"}  # fmt: skip


def _labelled(items: list[LineReading], source: str) -> list[LineReading]:
    for li in items:
        li.source = li.source or source
    return items


def _page_lines(path: Path, layout: DocLayout, fields: dict[str, FieldResult], words: list[LineReading],
                tables: Any, left_out: list[LineReading] | None = None,
                ) -> tuple[list[LineReading], list[list[LineReading]]]:  # fmt: skip
    """The line items read from the page itself, and every reading of them, best first: the words inside the tables
    the table finder found (``tables``: True finds them now, a {page: boxes} dict found before, None/False: not
    looked for), the words under the column headings across the page (``words``, the reading as before) and the
    PDF's table from its text layer. The first reading that adds up to the subtotal is kept, else the amounts
    listed above the subtotal when they add up to it (a receipt, ``reader.listed_items``), else the first one
    found inside a table, else the words across the page. The PDF's table is last: it is a second reading of the
    same text, there to be compared with OvisOCR2's when the others read the lines differently from it. A reading
    that ran on into the totals under the table is cut at its subtotal row first (``_to_subtotal``). No reading
    keeps a row of the supplier's account (``left_out`` gathers them)."""
    from .tables import text_table_items

    if tables is True:
        from .table_finder import find_tables

        tables = find_tables(path, max_pages=min(len(layout.pages), read_pages()) or 1)
    found = tables if isinstance(tables, dict) else None
    targets = _line_targets(fields)
    in_tables = _labelled(read_line_items(layout, found, targets, left_out), "table") if found else []
    text_table: list[LineReading] = []
    if layout.source == "text":
        text_table = _labelled(without_account_rows(text_table_items(path, layout, found), left_out), "text table")
    in_tables, words, text_table = (_discounts_taken_off(_to_subtotal(r, targets), targets)
                                    for r in (in_tables, words, text_table))  # fmt: skip
    readings = [r for r in (in_tables, words, text_table) if r]
    adds = [r for r in readings if targets and _adds_up(r, targets)]
    if adds:
        return adds[0], [adds[0], *[r for r in readings if r is not adds[0]]]
    # No reading adds up: a receipt's list of amounts.
    listed = _labelled(without_account_rows(listed_items(layout, targets), left_out), "listed")
    if listed:
        return listed, [listed, *readings]
    return (in_tables or words), readings


def _boxed_rows(layout: DocLayout, items: list[LineReading]) -> list[LineReading]:
    """``items`` with the box of the page row that prints each one's amount: in reading order, below the line
    table's heading, the rightmost copy on the first row below the last one found."""
    spots: dict[float, list[Box]] = {}
    for line in layout.lines():
        for value, a, b in find_amounts(line.text):
            box = _span_box(line, a, b)
            if box is not None:
                spots.setdefault(round(abs(value), 2), []).append(box)
    heading = next((ln.box for page in layout.pages for ln in page.lines if _table_header_line(ln, page.lines)), None)
    after = (heading.page, heading.cy + 0.006) if heading else (0, -1.0)  # rows are further apart than this
    out = []
    for li in items:
        boxes: list[Box] = []
        amount = round(abs(li.amount), 2) if li.amount is not None else None
        below = sorted((b for b in spots.get(amount, []) if (b.page, b.cy) > after), key=lambda b: (b.page, b.cy))
        if below:
            first = below[0]
            spot = max((b for b in below if b.page == first.page and abs(b.cy - first.cy) < 0.006), key=lambda b: b.x1)
            page = next((p for p in layout.pages if p.number == spot.page), None)
            boxes = [union_all([ln.box for ln in page.lines if _same_row(ln.box, spot)] if page else []) or spot]
            after = (spot.page, max(first.cy, spot.cy) + 0.006)
        out.append(LineReading(li.description, li.quantity, li.unit_price, li.amount, boxes, li.score, "vlm",
                               list(li.agreed), li.tax_flags))  # fmt: skip
    return out


def _span_box(line: Any, a: int, b: int) -> Box | None:
    from .reader import _span_words

    return union_all([w.box for w in _span_words(line, a, b)])


def _same_row(box: Box, spot: Box) -> bool:
    overlap = min(box.y1, spot.y1) - max(box.y0, spot.y0)
    return box.page == spot.page and overlap > 0.5 * min(box.height, spot.height)


# The text a PDF carries against the page as printed (the page reader reads the page image). A PDF whose hidden text
# says something else than its page shows is a known way to slip a different amount, bank account or supplier past a
# reader of the text alone. The page reader can misread a digit, so the rule is generous: one total read otherwise is
# its field's own disagreement (marked Check by fusion); the check fails when most figures differ, or when the totals
# the text gives are not on the page while the page reader read the rest of it the same.
TEXT_LAYER_CHECK = "TEXT_LAYER_MATCHES_PAGE"
TEXT_LAYER_MIN_FIGURES = 8  # figures compared before the share read the same is judged
TEXT_LAYER_MIN_SHARE = 0.6  # fewer of the text layer's figures confirmed than this: the text is not the page
TEXT_LAYER_REST_SHARE = 0.9  # ... the other figures it read the same, for missing totals to count
TOTAL_FIELDS = ("subtotal", *TAX_FIELDS, "tax_total", "grand_total")


def text_layer_check(layout: DocLayout, page_text: list[str], fields: dict[str, FieldResult],
                     text_readings: dict[str, list[Reading]] | None = None) -> dict[str, Any]:  # fmt: skip
    """``TEXT_LAYER_MATCHES_PAGE`` for a digital PDF the page reader read: its figures (``figures.compare_figures``)
    against the PDF's text layer on the same pages. Fails (a warning, never fatal) when at least
    ``TEXT_LAYER_MIN_FIGURES`` figures were compared and fewer than 60% were read the same, or when two or more of the
    totals the text layer gives (subtotal, taxes, total: the rule reader's reading of the text layer,
    ``text_readings``, else the value fusion chose) are nowhere in the page reader's reading while it read at least
    90% of the other figures the same: the PDF's hidden text is not what its page shows. The fields of the totals it
    didn't see are marked Check."""
    from decimal import Decimal

    from ..ai.figures import compare_figures, figures
    from .transcript import read_in_part

    # Page by page, the pages the page reader read only (one it found blank, or didn't reach, is left out of both).
    pairs = [(page, text) for page, text in zip(layout.pages, page_text, strict=False) if (text or "").strip()]
    partial = [page.number for page, text in pairs if read_in_part(text)]
    if partial:  # what it never read is not the PDF showing other figures than its page (B-1518)
        pages = ", ".join(str(n) for n in partial)
        return {"code": TEXT_LAYER_CHECK, "ok": True, "fields": [], "severity": "warning", "figures": 0,
                "confirmed": 0, "detail": f"the page reader read only part of page {pages}: the PDF's text was not "
                "compared with the page"}  # fmt: skip
    hidden = "\n".join(line.text for page, _text in pairs for line in page.lines)  # bold drawn twice: read once
    shown = "\n\n".join(text for _page, text in pairs)
    comparison = compare_figures(hidden, shown)
    compared = max(comparison.figures, comparison.first_figures)
    in_text, on_page = figures(hidden), figures(shown)
    unseen: list[str] = []
    totals: set[Decimal] = set()
    for name in TOTAL_FIELDS:
        read = (text_readings or {}).get(name) or []
        result = fields.get(name)
        value = read[0].value if read else (result.value if result is not None and result.status != MISSING else None)
        try:
            amount = Decimal(f"{float(value):.2f}")
        except (TypeError, ValueError):
            continue
        if not amount or amount in totals or (amount not in in_text and -amount not in in_text):
            continue  # none, the same amount as another total, or not printed (worked out from the others)
        totals.add(amount)
        if amount not in on_page and -amount not in on_page:
            unseen.append(name)
    # The text layer's other figures: how many of them the page reader saw (it read the rest of the page well).
    rest_compared = sum(len(v) for k, v in in_text.items() if abs(k) not in totals)
    rest_same = sum(min(len(v), len(on_page.get(k, []))) for k, v in in_text.items() if abs(k) not in totals)
    rest_share = rest_same / rest_compared if rest_compared else 0.0
    share = comparison.share
    check: dict[str, Any] = {"code": TEXT_LAYER_CHECK, "ok": True, "fields": [], "severity": "warning",
                             "figures": compared, "confirmed": comparison.confirmed}  # fmt: skip
    if compared >= TEXT_LAYER_MIN_FIGURES and share is not None and share < TEXT_LAYER_MIN_SHARE:
        check.update(ok=False, detail=(
            f"the page as printed shows only {comparison.confirmed} of the {compared} figures in the PDF's hidden "
            "text: compare every amount with the page, and ask the supplier for a fresh copy"))  # fmt: skip
        return check
    if len(unseen) >= 2 and rest_compared >= TEXT_LAYER_MIN_FIGURES and rest_share >= TEXT_LAYER_REST_SHARE:
        labels = ", ".join(LABELS.get(name, name) for name in unseen)
        check.update(ok=False, fields=unseen, detail=(
            f"the page as printed doesn't show the {labels} the PDF's hidden text gives, while the rest of the page "
            "reads the same: compare them with the page, and ask the supplier for a fresh copy"))  # fmt: skip
        for name in unseen:
            result = fields.get(name)
            if result is not None and result.status != MISSING:
                result.status = CHECK
                result.reasons = [*result.reasons, "the page as printed doesn't show the amount in the PDF's text"]
        return check
    check["detail"] = f"the page as printed shows {comparison.confirmed} of the {compared} figures in the PDF's text"
    return check


def _read_straight(layout: DocLayout, received: dt.date | None) -> dict[str, list[Reading]]:
    """``read_fields`` of a scan as if it lay straight: on each tilted page every line (words on one baseline, set close
    together) is moved up or down, as one piece, to where it would be on the straightened page, so a label and its
    amount at the other end of the row stay on one row (on a page scanned at 4 degrees, "GST" sat level with the
    subtotal's amount); each reading's boxes are then put back where the words are printed on the page as scanned.
    A line is moved whole, not word by word: a title's words would come apart."""
    from .layout import TIGHT_FROM, group_lines
    from .types import PageLayout, Word

    skews = {page.number: page.skew for page in layout.pages if abs(page.skew) >= STRAIGHTEN_FROM}
    if not skews:
        return read_fields(layout, received=received)
    pages = []
    for page in layout.pages:
        if page.number not in skews:
            pages.append(page)
            continue
        # Steeply tilted, OCR's words follow the tilt (``layout.TIGHT_FROM``): each moved on its own. Else a word's
        # box is its whole OCR line's: the line is moved as one piece.
        each = abs(page.skew * page.height / max(page.width, 1.0)) >= TIGHT_FROM
        pieces = [[w] for w in page.words] if each else [line.words for line in page.lines]
        words = []
        for piece in pieces:
            dy = -page.skew * (union_all([w.box for w in piece]).cx - 0.5)
            words += [Word(w.text, Box(w.box.page, w.box.x0, w.box.y0 + dy, w.box.x1, w.box.y1 + dy), w.conf, w.source)
                      for w in piece]  # fmt: skip
        pages.append(PageLayout(page.number, page.width, page.height, words, group_lines(words, page.number),
                                page.rotation, 0.0, page.turn, page.dpi))  # fmt: skip
    straight = DocLayout(pages, layout.source, layout.pages_left_out, layout.pages_unread_locally)
    readings = read_fields(straight, received=received)
    for found in readings.values():
        for reading in found:
            reading.boxes = [_on_tilted_page(b, skews[b.page]) if b.page in skews else b for b in reading.boxes]
    return readings


def _cross_read(path: Path, layout: DocLayout) -> dict[str, list[Reading]]:
    """A second, independent reading of a digital PDF through OCR of the rendered page."""
    if layout.source != "text" or not ocr_available():
        return {}
    try:
        ocr_layout = build_layout(path, ocr=True)
    except Exception as exc:  # the cross-read is an extra; never fatal
        log.warning("%s: OCR cross-read failed (%s)", path.name, exc)
        return {}
    return read_fields(ocr_layout)


def analyze(path: str | Path, *, di_raw: dict[str, Any] | None = None, ai_values: dict[str, Any] | None = None,
            template: Any = None, vendor: dict[str, Any] | None = None, ocr: str | bool = "auto",
            cross_read: bool = False, layout: DocLayout | None = None, second_read: bool = True,
            today: dt.date | None = None, page_text: list[str] | None = None,
            local_evidence: dict[str, tuple[int, int]] | None = None, tables: Any = True) -> CaptureResult:  # fmt: skip
    """``page_text``: the page reader's transcription of each page (a vision model's markdown), read as one more
    independent reader ("vlm"). ``local_evidence``: {evidence: (cases, right)} from what AP approved on this
    computer, for the calibration of confidence. ``tables``: look for the tables on each page with the table finder
    (True, when its model is on this computer), the tables found already ({page: boxes}), or not (False)."""
    path = Path(path)
    layout = layout or build_layout(path, di_raw=di_raw, ocr=ocr)
    sources: dict[str, dict[str, list[Reading]]] = {"rules": _read_straight(layout, today)}
    if template is not None:
        try:
            from .supplier import apply_template

            sources["template"] = apply_template(template, layout)
        except Exception as exc:  # a bad template must not stop the capture
            log.warning("%s: template not applied (%s)", path.name, exc)
    di_fields, di_items = _di_sources(di_raw)  # Azure's answer, when it reads (D-245)
    if di_fields:
        sources["di"] = di_fields
    if ai_values:
        sources["ai"] = _ai_readings(layout, ai_values)
    if second_read and layout.source == "ocr":
        try:
            from .layout import _engine_name, second_engine_name, second_read_layout

            second_layout = second_read_layout(path)
            if second_layout is not None:
                # Another OCR model makes its own mistakes: an independent reader ("ocr2"). The same
                # model on a straightened page shares the first read's blind spots ("ocr").
                name = "ocr2" if second_engine_name() != _engine_name() else "ocr"
                sources[name] = _read_straight(second_layout, today)
        except Exception as exc:  # an extra reader: never fatal
            log.warning("%s: second read failed (%s)", path.name, exc)
    if cross_read:
        second = _cross_read(path, layout)
        if second:
            sources["ocr"] = second
    vlm_items: list[LineReading] = []
    account: list[LineReading] = []  # the rows of the supplier's account no reading keeps as lines
    if page_text and any((page or "").strip() for page in page_text):
        try:
            sources["vlm"], vlm_items = _page_reader(layout, page_text, today, account)
        except Exception as exc:  # an extra reader: never fatal
            log.warning("%s: page reader's transcription not read (%s)", path.name, exc)
    words = _labelled(read_line_items(layout, left_out=account), "words")

    page_words = layout.text()  # a tip printed after the tax (B-1026)

    def fused(lines: list[LineReading]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        return fuse(sources, lines, vendor=vendor, today=today, layout_source=layout.source,
                    fields=FIELDS + EXTRA_FIELDS, local_evidence=local_evidence, text=page_words)  # fmt: skip

    fields, checks = fused(words)
    items, readings = words, [words] if words else []
    # A page read upright from a picture shown turned: the table finder would look at the page as shown.
    pictured = path.suffix.lower() == ".pdf" or path.suffix.lower() in IMAGE_EXTENSIONS
    if tables is not False and pictured and not any(page.turn for page in layout.pages):
        try:  # a page to look at (a text file has none)
            items, readings = _page_lines(path, layout, fields, words, tables, account)
        except Exception as exc:  # the table readers are extras: the words under the headings stay
            log.warning("%s: tables not read (%s)", path.name, exc)
    agreement = None
    if vlm_items:
        from .line_items import agree, agreement_check, mark_agreed

        _labelled(vlm_items, "vlm")
        agreeing = next((r for r in readings if agree(r, vlm_items)), None)
        if agreeing is not None:
            items = mark_agreed(agreeing, "vlm")
            mark_agreed(vlm_items, agreeing[0].source)
        items = _choose_lines(layout, fields, items, vlm_items)
        agreement = agreement_check(readings, vlm_items, agreeing[0].source if agreeing else None)
    if di_items:
        items = _with_di_lines(items, di_items)
    if items is not words:
        fields, checks = fused(items)  # the lines-add-up check on the lines kept
    if agreement is not None:
        checks.append(agreement)
    if account:
        checks.append(account_rows_check(account))
    if "vlm" in sources and layout.source == "text":
        checks.append(text_layer_check(layout, page_text or [], fields, sources["rules"]))
    _mark_doubtful_vendor(layout, fields, checks, template)
    coarse = min((page.dpi for page in layout.pages if page.dpi), default=0.0)
    if layout.source != "text" and 0 < coarse < COARSE_DPI:
        for fr in fields.values():
            if fr.status not in (CHECK, MISSING):
                fr.status, fr.confidence = CHECK, min(fr.confidence, 0.6)
                fr.reasons.append(f"scanned at about {coarse:.0f} dpi, coarser than the scans its reading was measured "
                                  "on (150 dpi and up): a person checks it")  # fmt: skip
    credit = credit_titled(layout)
    if credit:
        # A credit's amounts are negative: one read positive agrees with the other readings in size, not in sign (a
        # "CR" lost on a scan, a reader that missed the title). A person checks it (B-2627).
        for name in TOTAL_FIELDS:
            fr = fields.get(name)
            positive = fr is not None and isinstance(fr.value, (int, float)) and fr.value > 0
            if positive and fr.status not in (CHECK, MISSING):
                fr.status, fr.confidence = CHECK, min(fr.confidence, 0.6)
                fr.reasons.append("read as a positive amount on a credit note: check its sign on the page")
    return CaptureResult(fields=fields, line_items=items, checks=checks, layout_source=layout.source,
                         page_count=len(layout.pages) + layout.pages_left_out,
                         pages_left_out=layout.pages_left_out + layout.pages_unread_locally,
                         credit_note=credit, pages_not_read=list(layout.not_read),
                         not_invoice="" if credit else not_an_invoice(layout, fields, items),
                         instructions=addressed_to_software(layout, page_text, di_raw, path))  # fmt: skip
