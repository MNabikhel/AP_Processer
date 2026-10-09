"""Coding an invoice with no AI model: the capture reader's header and lines, coded from memory.

When neither LM Studio nor Azure OpenAI is available, an invoice still gets a full proposal: every header
field and line as the local reader found them (capture, with its own confidence), each line's GL account
from what AP approved before for this vendor and these words (``suggest.suggest_gl``), the vendor master's
default account, and fixed coding rules (applied later by the pipeline). Nothing is guessed: a line with
no history is left UNASSIGNED and a field the reader could not find stays empty, so review asks for it.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from pathlib import Path
from typing import Any

from .capture import analyze, build_layout
from .capture.bridge import vendor_record
from .capture.types import LIKELY, VERIFIED, CaptureResult, DocLayout
from .capture.workflow import supplier_for
from .extraction import ExtractionResult
from .inference import CodingResult
from .memory import vendor_key
from .reference_data import UNASSIGNED, ReferenceData
from .schema import InvoiceCoding, LineItem, TaxLine
from .suggest import suggest_gl

log = logging.getLogger(__name__)

MODEL_NAME = "local reader (no AI model)"
NO_HISTORY = "No earlier approval to learn from: pick the account."
_TAXES = (("gst_amount", "GST"), ("hst_amount", "HST"), ("pst_amount", "PST"), ("qst_amount", "QST"))
# The HST rate names the province when only one has it (13% Ontario, 14% Nova Scotia from April 2025).
_HST_PROVINCE = {0.13: "ON", 0.14: "NS"}


def read_invoice(path: str | Path, store: Any = None,
                 layout: DocLayout | None = None) -> tuple[CaptureResult, str, dict[str, Any] | None]:  # fmt: skip
    """(capture, supplier key, supplier profile): a first read, then again with the supplier's template
    and vendor-master record once the first read says who the supplier is. ``layout``: the page, already
    read (a scan is OCR'd once)."""
    path = Path(path)
    today = dt.date.today()
    layout = layout or build_layout(path)
    capture = analyze(path, layout=layout, today=today)
    first = _values(capture)
    key, profile = supplier_for(store, first)
    template = (profile or {}).get("template") or None
    vendor = vendor_record(store, first.get("vendor_name"))
    if template or vendor:
        capture = analyze(path, layout=layout, template=template, vendor=vendor, today=today)
    return capture, key, profile


def _values(capture: CaptureResult) -> dict[str, Any]:
    return {f: r.value for f, r in capture.fields.items() if r.value not in (None, "")}


def _amount(values: dict[str, Any], field: str) -> float:
    try:
        return round(float(values.get(field) or 0.0), 2)
    except (TypeError, ValueError):
        return 0.0


def _tax_lines(values: dict[str, Any], subtotal: float) -> list[TaxLine]:
    out = []
    for field, tax_type in _TAXES:
        amount = _amount(values, field)
        if not amount:
            continue
        rate = round(amount / subtotal, 4) if subtotal else 0.0
        rate = min(max(rate, 0.0), 1.0)
        province = "QC" if tax_type == "QST" else _HST_PROVINCE.get(round(rate, 2), "") if tax_type == "HST" else ""
        out.append(TaxLine(tax_type=tax_type, province=province, rate=rate, taxable_amount=subtotal, tax_amount=amount))
    return out


def _unlisted_charge(text: str, amount: float) -> str:
    """The label printed beside an amount outside the line table ("Delivery & handling: $95.00")."""
    figure = f"{abs(amount):,.2f}"
    for line in text.splitlines():
        if figure in line:
            label = line.split(figure)[0].strip(" :$-–\t")
            if 2 < len(label) < 60 and not re.search(r"(?i)sub\s*-?total|total|tax|gst|hst|pst|qst|tps|tvq", label):
                return label
    return "Charges not in the line table"


def code_from_capture(capture: CaptureResult, reference: ReferenceData, feedback: list[dict[str, Any]],
                      store: Any = None, text: str = "") -> CodingResult:  # fmt: skip
    """``text``: the page text (labels a charge printed outside the line table)."""
    values = _values(capture)
    vendor = str(values.get("vendor_name") or "")
    subtotal = _amount(values, "subtotal")
    tax_lines = _tax_lines(values, subtotal)
    taxes = [t.tax_type for t in tax_lines]
    default_gl = ""
    if store is not None and vendor:
        try:
            default_gl = (store.get_vendor(vendor_key(vendor)) or {}).get("default_gl") or ""
        except Exception:  # an older store without the vendor table
            default_gl = ""

    readings = [li for li in capture.line_items if li.amount is not None]
    if not readings:  # no table the reader could split: one line for the whole subtotal
        readings_data = [("Invoice " + str(values.get("invoice_number") or ""), 1.0, subtotal, subtotal)]
    else:
        readings_data = [
            (li.description or f"Line {i}", li.quantity if li.quantity is not None else 1.0,
             li.unit_price if li.unit_price is not None else float(li.amount), float(li.amount))
            for i, li in enumerate(readings, 1)
        ]  # fmt: skip
    # Lines that fall short of the subtotal (plus freight read beside it): a charge printed outside the table.
    charges = capture.fields.get("other_charges")
    freight = _amount(values, "other_charges") if charges is not None and charges.status in (VERIFIED, LIKELY) else 0.0
    short = round(subtotal + freight - sum(r[3] for r in readings_data), 2)
    if readings and abs(short) >= 0.01:
        readings_data.append((_unlisted_charge(text, short), 1.0, short, short))
    lines = []
    for number, (description, quantity, unit_price, amount) in enumerate(readings_data, 1):
        best = next(iter(suggest_gl(description, vendor, feedback, reference, limit=1, default_gl=default_gl)), None)
        lines.append(
            LineItem(
                line_number=number,
                description=description.strip() or f"Line {number}",
                quantity=quantity,
                unit_price=round(unit_price, 4),
                amount=round(amount, 2),
                predicted_gl_code=best.gl_code if best else UNASSIGNED,
                predicted_cost_center=(best.cost_center if best else "") or "",
                taxes_applied=taxes,
                reasoning_justification="; ".join(best.reasons) if best else NO_HISTORY,
            )
        )

    found = [r.confidence for f, r in capture.fields.items() if r.value not in (None, "")]
    gl_known = sum(1 for li in lines if li.predicted_gl_code != UNASSIGNED) / len(lines)
    confidence = round(min(sum(found) / len(found) if found else 0.0, 1.0) * (0.5 + 0.5 * gl_known), 3)
    invoice_date = str(values.get("invoice_date") or "")
    try:
        dt.date.fromisoformat(invoice_date)
    except ValueError:
        invoice_date = dt.date.today().isoformat()  # the day it arrived; capture already flags the date as missing
    coding = InvoiceCoding(
        vendor_name=vendor,
        invoice_number=str(values.get("invoice_number") or ""),
        invoice_date=invoice_date,
        po_number=str(values.get("po_number") or ""),
        payment_terms=str(values.get("payment_terms") or ""),
        due_date=_iso_or_empty(values.get("due_date")),
        currency=str(values.get("currency") or "CAD"),
        gst_hst_registration_number=str(values.get("gst_hst_registration_number") or ""),
        qst_registration_number=str(values.get("qst_registration_number") or ""),
        subtotal=subtotal,
        tax_lines=tax_lines,
        tax_total=_amount(values, "tax_total") or round(sum(t.tax_amount for t in tax_lines), 2),
        grand_total=_amount(values, "grand_total"),
        confidence_score=confidence,
        line_items=lines,
    )
    return CodingResult(coding=coding, raw_response="", model=MODEL_NAME, attempts=0)


def _iso_or_empty(value: Any) -> str:
    try:
        return dt.date.fromisoformat(str(value)).isoformat() if value else ""
    except ValueError:
        return ""


def local_extraction(path: str | Path, layout: DocLayout | None = None) -> ExtractionResult:
    """The page text as the local reader saw it (text layer, or OCR for scans), for search and the review page."""
    path = Path(path)
    layout = layout or build_layout(path)
    words = list(layout.words())
    ocr_conf = [w.conf for w in words if w.source == "ocr"]
    content = "\n\n<!-- PageBreak -->\n\n".join("\n".join(line.text for line in page.lines) for page in layout.pages)
    return ExtractionResult(
        source=str(path),
        model_id=f"local-{layout.source}",
        content=content,
        page_count=len(layout.pages),
        mean_word_confidence=(sum(ocr_conf) / len(ocr_conf)) if ocr_conf else None,
        low_confidence_word_count=sum(1 for c in ocr_conf if c < 0.8),
    )
