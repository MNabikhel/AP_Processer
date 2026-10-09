# ruff: noqa: E501  (label and pattern tables read best one per line)
"""Invoice capture: layout, readers, confidence, supplier learning (see docs/CAPTURE_DESIGN.md).

``analyze`` reads one invoice with every reader available and returns, per header field, the value,
a calibrated confidence, a status (verified / likely / check / missing), the boxes on the page and
the reasons, plus the cross-field checks.
"""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path
from typing import Any

from .confidence import LABELS, fuse
from .layout import build_layout, ocr_available
from .locate import locate
from .reader import read_fields, read_line_items
from .types import EXTRA_FIELDS, FIELDS, Box, CaptureResult, DocLayout, Reading

log = logging.getLogger(__name__)

__all__ = ["LABELS", "analyze", "build_layout", "read_fields", "locate", "CaptureResult"]

# Document Intelligence prebuilt-invoice field -> capture field
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


def _di_readings(raw: dict[str, Any]) -> dict[str, list[Reading]]:
    docs = raw.get("documents") or []
    if not docs:
        return {}
    pages = {int(p.get("pageNumber") or i + 1): p for i, p in enumerate(raw.get("pages") or [])}
    out: dict[str, list[Reading]] = {}
    for di_name, field in DI_FIELDS.items():
        fld = (docs[0].get("fields") or {}).get(di_name)
        if not fld:
            continue
        if "valueCurrency" in fld:
            value: Any = (fld["valueCurrency"] or {}).get("amount")
        else:
            value = next((fld[k] for k in ("valueString", "valueDate", "valueNumber") if k in fld), fld.get("content"))
        if value in (None, ""):
            continue
        boxes = []
        for region in fld.get("boundingRegions") or []:
            page = pages.get(int(region.get("pageNumber") or 1))
            poly = region.get("polygon") or []
            if page and len(poly) >= 8 and page.get("width") and page.get("height"):
                xs, ys = poly[0::2], poly[1::2]
                w, h = float(page["width"]), float(page["height"])
                boxes.append(
                    Box(int(region.get("pageNumber") or 1), min(xs) / w, min(ys) / h, max(xs) / w, max(ys) / h)
                )
        out[field] = [Reading(field, value, str(fld.get("content") or value), boxes,
                              float(fld.get("confidence") or 0.5), "di")]  # fmt: skip
    return out


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
            cross_read: bool = False, layout: DocLayout | None = None,
            today: dt.date | None = None) -> CaptureResult:  # fmt: skip
    path = Path(path)
    layout = layout or build_layout(path, di_raw=di_raw, ocr=ocr)
    sources: dict[str, dict[str, list[Reading]]] = {"rules": read_fields(layout)}
    if template is not None:
        try:
            from .supplier import apply_template

            sources["template"] = apply_template(template, layout)
        except Exception as exc:  # a bad template must not stop the capture
            log.warning("%s: template not applied (%s)", path.name, exc)
    if di_raw:
        sources["di"] = _di_readings(di_raw)
    if ai_values:
        sources["ai"] = _ai_readings(layout, ai_values)
    if cross_read:
        second = _cross_read(path, layout)
        if second:
            sources["ocr"] = second
    items = read_line_items(layout)
    fields, checks = fuse(sources, items, vendor=vendor, today=today, layout_source=layout.source,
                          fields=FIELDS + EXTRA_FIELDS)  # fmt: skip
    return CaptureResult(fields=fields, line_items=items, checks=checks, layout_source=layout.source,
                         page_count=len(layout.pages))  # fmt: skip
