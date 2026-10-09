# ruff: noqa: E501  (label and pattern tables read best one per line)
"""Find where a value read by another reader (the AI, Document Intelligence) is printed.

The AI and Document Intelligence say *what* a field is; the page says *where*. A value that can be
found on the page is evidence that it was read, not invented, and its box is what the reviewer sees
highlighted. When a value is printed several times, the copy next to a label for that field wins.
"""

from __future__ import annotations

from .normalize import (
    find_amounts,
    find_gst_numbers,
    find_qst_numbers,
    norm_gst,
    norm_id,
    norm_name,
    norm_qst,
    parse_dates,
)
from .reader import _label_hits, _span_words, plain
from .types import AMOUNT_FIELDS, DATE_FIELDS, DocLayout, Line, Reading, union_all


def _near_label(layout: DocLayout, field: str, line: Line) -> float:
    """1.0 when the line itself carries a label for ``field``, 0.6 when a line just left or above does."""
    if any(h.field == field for h in _label_hits(line)):
        return 1.0
    for other in layout.pages[line.box.page - 1].lines if 0 < line.box.page <= len(layout.pages) else []:
        if other is line:
            continue
        same_row = (
            abs(other.box.cy - line.box.cy) < 0.6 * max(line.box.height, 0.008) and other.box.x1 <= line.box.x0 + 0.01
        )
        just_above = (
            0 < line.box.y0 - other.box.y1 < 3 * max(line.box.height, 0.008) and abs(other.box.x0 - line.box.x0) < 0.06
        )
        if (same_row or just_above) and any(h.field == field for h in _label_hits(other)):
            return 0.6
    return 0.0


def locate(layout: DocLayout, field: str, value: object) -> list[Reading]:
    """Every place ``value`` is printed, best first (a copy next to the field's label wins)."""
    if value is None or value == "":
        return []
    found: list[Reading] = []
    for line in layout.lines():
        text = line.text
        spans: list[tuple[int, int]] = []
        if field in AMOUNT_FIELDS:
            try:
                target = round(float(value), 2)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return []
            spans = [
                (a, b)
                for v, a, b in find_amounts(text)
                if abs(round(v, 2) - target) <= 0.005 or abs(abs(v) - abs(target)) <= 0.005 and target != 0
            ]
        elif field in DATE_FIELDS:
            spans = [(a, b) for d, a, b, _ in parse_dates(text) if d.isoformat() == str(value)]
            spans += [
                (a, b)
                for d, a, b, amb in parse_dates(text, prefer_day_first=True)
                if amb is False and d.isoformat() == str(value) and (a, b) not in spans
            ]
            spans += [
                (a, b)
                for d, a, b, amb in parse_dates(text, prefer_day_first=False)
                if d.isoformat() == str(value) and (a, b) not in spans
            ]
        elif field == "gst_hst_registration_number":
            target_s = norm_gst(str(value))
            spans = [(a, b) for v, a, b, _ in find_gst_numbers(text) if v == target_s or v[:9] == target_s[:9]]
        elif field == "qst_registration_number":
            target_s = norm_qst(str(value))
            spans = [(a, b) for v, a, b in find_qst_numbers(text) if v == target_s]
        elif field in ("invoice_number", "po_number"):
            spans = _id_spans(line, norm_id(str(value)))
        elif field == "vendor_name":
            target_n = norm_name(str(value))
            if target_n and (target_n == norm_name(text) or (len(target_n) > 6 and target_n in norm_name(text))):
                spans = [(0, len(text))]
        else:
            p, t = plain(text), plain(str(value))
            i = p.find(t)
            if t and i >= 0:
                spans = [(i, i + len(t))]
        for a, b in spans:
            words = _span_words(line, a, b)
            box = union_all([w.box for w in words])
            if box is None:
                continue
            near = _near_label(layout, field, line)
            conf = min((w.conf for w in words), default=1.0)
            score = (0.55 + 0.4 * near) * conf
            found.append(Reading(field, value, text[a:b], [box], score, "located"))
    found.sort(key=lambda r: -r.score)
    return found


def _id_spans(line: Line, target: str) -> list[tuple[int, int]]:
    """Character spans of runs of 1-3 words whose normalized text equals ``target``."""
    if not target:
        return []
    spans = []
    starts, pos = [], 0
    for w in line.words:
        starts.append((pos, pos + len(w.text)))
        pos += len(w.text) + 1
    for i in range(len(line.words)):
        joined = ""
        for j in range(i, min(i + 3, len(line.words))):
            joined += norm_id(line.words[j].text)
            if joined == target:
                spans.append((starts[i][0], starts[j][1]))
                break
            if not target.startswith(joined):
                # the label may be glued to the value ("InvoiceNo:NW-0912"): look for a suffix match
                if j == i and norm_id(line.words[i].text).endswith(target) and len(target) >= 4:
                    spans.append((starts[i][0], starts[i][1]))
                break
    return spans
