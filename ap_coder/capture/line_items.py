"""Line items read by two independent readers, compared row by row.

A line item is *agreed* when two readers that don't share their mistakes read the same row: the same amount, the same
quantity (when both read one) and much the same description. The readers of the page itself are the table finder's
reading (the words inside the table the layout model found), the PDF's table (its text layer's columns) and, when
neither found the table, the words under the column headings across the page; the other reader is OvisOCR2, which
reads the page image on its own. When OvisOCR2 and a reader of the page read the line table differently, the
invoice's lines are marked for a person: the check ``LINE_ITEMS_AGREE`` fails, and a failed check keeps an invoice
from being approved without a person (``supplier.should_auto_approve``).
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Any

from .types import LINE_SOURCES, LineReading

CHECK_CODE = "LINE_ITEMS_AGREE"
AMOUNT_SAME = 0.011  # amounts read the same: within a cent
QUANTITY_SAME = 0.001
DESCRIPTION_SAME = 0.6  # descriptions read the same: this similar (OCR misreads letters; a code may be in one only)


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def description_similarity(a: str, b: str) -> float:
    """How alike two descriptions read (0..1): letter by letter, or the shorter one's words all in the longer one (a
    product code or a unit read into one description only)."""
    pa, pb = _plain(a), _plain(b)
    if not pa or not pb:
        return 1.0 if pa == pb else 0.0
    ratio = difflib.SequenceMatcher(None, pa, pb, autojunk=False).ratio()
    wa, wb = set(pa.split()), set(pb.split())
    short, long_ = (wa, wb) if len(wa) <= len(wb) else (wb, wa)
    contained = len(short & long_) / len(short) if short else 0.0
    return max(ratio, contained if len(short) >= 2 or len(pa) == len(pb) else 0.0)


def same_row(a: LineReading, b: LineReading) -> bool:
    """Two readings of one line item agree: amount, quantity when both read one, and description."""
    if a.amount is None or b.amount is None or abs(a.amount - b.amount) > AMOUNT_SAME:
        return False
    if a.quantity is not None and b.quantity is not None and abs(a.quantity - b.quantity) > QUANTITY_SAME:
        return False
    return description_similarity(a.description, b.description) >= DESCRIPTION_SAME


def first_difference(a: list[LineReading], b: list[LineReading]) -> int | None:
    """The first line (from 0) the two readings don't read the same; None when they agree on every line (and have
    the same number of lines)."""
    for i, (x, y) in enumerate(zip(a, b, strict=False)):
        if not same_row(x, y):
            return i
    return None if len(a) == len(b) and a else min(len(a), len(b))


def agree(a: list[LineReading], b: list[LineReading]) -> bool:
    return bool(a) and first_difference(a, b) is None


def source_name(source: str) -> str:
    return LINE_SOURCES.get(source, source or "the page")


def _money(value: float | None) -> str:
    return "-" if value is None else f"{value:,.2f}"


def agreement_check(page_side: list[list[LineReading]], vlm: list[LineReading],
                    agreed_with: str | None) -> dict[str, Any] | None:  # fmt: skip
    """``LINE_ITEMS_AGREE`` once OvisOCR2 and a reader of the page have both read line items: passed when one of the
    page's readings (``page_side``, best first) agrees with OvisOCR2's row by row (``agreed_with`` names it), failed
    otherwise, with the first line they read differently. None when only one side read line items (nothing to
    compare: those lines are not marked agreed)."""
    readings = [r for r in page_side if r]
    if not vlm or not readings:
        return None
    check: dict[str, Any] = {"code": CHECK_CODE, "fields": [], "severity": "warning"}
    if agreed_with:
        check.update(ok=True, detail=f"{len(vlm)} line items read the same by {source_name(agreed_with)} and OvisOCR2")
        return check
    best = readings[0]
    at = first_difference(best, vlm)
    who = source_name(best[0].source)
    if at is not None and at < len(best) and at < len(vlm):
        a, b = best[at], vlm[at]
        detail = (f"line {at + 1} reads {_money(a.amount)} (\"{a.description[:40]}\") by {who} but {_money(b.amount)} "
                  f"(\"{b.description[:40]}\") by OvisOCR2: check every line against the page")  # fmt: skip
    else:
        detail = (f"{who} read {len(best)} line items and OvisOCR2 {len(vlm)}: check every line against the "
                  "page")  # fmt: skip
    check.update(ok=False, detail=detail)
    return check


def mark_agreed(items: list[LineReading], other: str) -> list[LineReading]:
    """``items`` with ``other`` recorded on each line as a reader that read it the same."""
    for li in items:
        if other not in li.agreed and other != li.source:
            li.agreed = [*li.agreed, other]
    return items


def sources_line(items: list[LineReading], checks: list[dict[str, Any]] | None = None) -> str:
    """For the review screen: who read the line items, and whether two readers agree ("" when there are none)."""
    if not items:
        return ""
    check = next((c for c in checks or [] if c.get("code") == CHECK_CODE), None)
    counts: dict[str, int] = {}
    for li in items:
        counts[li.source] = counts.get(li.source, 0) + 1
    if not any(counts) or set(counts) == {""}:
        return ""
    parts = [f"{n} from {source_name(s)}" for s, n in counts.items() if s]
    agreed = sum(1 for li in items if li.agreed)
    if check is not None and not check.get("ok", True):
        return f"Line items read differently by two readers ({check.get('detail', '')}). Lines: {', '.join(parts)}."
    if agreed:
        both = {source_name(s) for li in items for s in [li.source, *li.agreed] if s}
        return f"Line items: {agreed} of {len(items)} read the same by {' and '.join(sorted(both))}."
    # Read by one reader only: nothing the reviewer can act on (the queue says when OvisOCR2 has not read it), B-464.
    return ""
