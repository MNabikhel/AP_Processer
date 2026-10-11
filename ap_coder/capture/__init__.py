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

from .confidence import LABELS, TAX_FIELDS, fuse
from .layout import CannotRead, build_layout, ocr_available
from .locate import locate
from .normalize import find_amounts
from .reader import _table_header_line, read_fields, read_line_items
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

__all__ = ["LABELS", "analyze", "build_layout", "read_fields", "locate", "CaptureResult", "CannotRead"]

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


def _page_reader(layout: DocLayout, page_text: list[str],
                 today: dt.date | None) -> tuple[dict[str, list[Reading]], list[LineReading]]:  # fmt: skip
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
    return out, read_line_items(vlm)


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
        return [LineReading(b.description, b.quantity, b.unit_price, b.amount, list(a.boxes), max(a.score, b.score))
                for a, b in zip(own, vlm, strict=True)]  # fmt: skip
    return own


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
        out.append(LineReading(li.description, li.quantity, li.unit_price, li.amount, boxes, li.score))
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

    from ..figures import compare_figures, figures

    # Page by page, the pages the page reader read only (one it found blank, or didn't reach, is left out of both).
    pairs = [(page, text) for page, text in zip(layout.pages, page_text, strict=False) if (text or "").strip()]
    hidden = "\n".join(line.text for page, _text in pairs for line in page.lines)
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
            local_evidence: dict[str, tuple[int, int]] | None = None) -> CaptureResult:  # fmt: skip
    """``page_text``: the page reader's transcription of each page (a vision model's markdown), read as one more
    independent reader ("vlm"). ``local_evidence``: {evidence: (cases, right)} from what AP approved on this
    computer, for the calibration of confidence."""
    path = Path(path)
    layout = layout or build_layout(path, di_raw=di_raw, ocr=ocr)
    sources: dict[str, dict[str, list[Reading]]] = {"rules": read_fields(layout, received=today)}
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
    if second_read and layout.source == "ocr":
        try:
            from .layout import _engine_name, second_engine_name, second_read_layout

            second_layout = second_read_layout(path)
            if second_layout is not None:
                # Another OCR model makes its own mistakes: an independent reader ("ocr2"). The same
                # model on a straightened page shares the first read's blind spots ("ocr").
                name = "ocr2" if second_engine_name() != _engine_name() else "ocr"
                sources[name] = read_fields(second_layout, received=today)
        except Exception as exc:  # an extra reader: never fatal
            log.warning("%s: second read failed (%s)", path.name, exc)
    if cross_read:
        second = _cross_read(path, layout)
        if second:
            sources["ocr"] = second
    vlm_items: list[LineReading] = []
    if page_text and any((page or "").strip() for page in page_text):
        try:
            sources["vlm"], vlm_items = _page_reader(layout, page_text, today)
        except Exception as exc:  # an extra reader: never fatal
            log.warning("%s: page reader's transcription not read (%s)", path.name, exc)
    items = read_line_items(layout)

    def fused(lines: list[LineReading]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        return fuse(sources, lines, vendor=vendor, today=today, layout_source=layout.source,
                    fields=FIELDS + EXTRA_FIELDS, local_evidence=local_evidence)  # fmt: skip

    fields, checks = fused(items)
    if vlm_items:
        chosen = _choose_lines(layout, fields, items, vlm_items)
        if chosen is not items:
            items = chosen
            fields, checks = fused(items)  # the lines-add-up check on the lines kept
    if "vlm" in sources and layout.source == "text":
        checks.append(text_layer_check(layout, page_text or [], fields, sources["rules"]))
    return CaptureResult(fields=fields, line_items=items, checks=checks, layout_source=layout.source,
                         page_count=len(layout.pages))  # fmt: skip
