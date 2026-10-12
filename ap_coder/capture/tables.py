"""The line table of a digital PDF read from its text layer: columns from the lines drawn down the table and the
gaps between its columns, then rows from where each word sits.

The rule reader (``reader.read_line_items``) takes each row's cells by how close they sit to the column headings.
This reader, ported in essence from CloseDesk's (``pdf_layout.py``), finds the columns themselves first, across all
the rows of the table, the way a person sees them:

* a line drawn down the table (a cell border) is a column edge;
* without drawn borders, a strip of the table that no row writes across is a column edge (a sheet saved as PDF has
  the same columns on every row, even where two cells sit closer than two words of a sentence);
* each column is named by the heading printed over it (description, quantity, unit price, amount); a column with
  no heading holds codes (left out) or more of the description;
* a row with no amount just under a line item is the rest of its description, wrapped onto a second line.

It reads the table the table finder found on the page when there is one (its box bounds the columns), else the
rows under the column headings across the page. It is a second reading of the same text, organised differently:
the line items it reads are compared with OvisOCR2's reading of the page image (``line_items.py``).

Camelot (CloseDesk's second table reader) is not used: it needs ``opencv-python-headless``, which can't be
installed beside the ``opencv-python`` the OCR engines need (both are the same ``cv2`` module), and it adds about
45 MB a platform to the offline bundle.
"""

from __future__ import annotations

import logging
import re
import statistics
from pathlib import Path
from typing import Any

from .types import Box, DocLayout, LineReading, PageLayout, Word, union_all

log = logging.getLogger(__name__)

MIN_GAP = 0.006  # a strip at least this wide (fraction of the page width) that no row writes across: a column edge
RULE_MIN_HEIGHT = 4.0  # points: a line drawn down the page at least this long is a cell border
RULE_MAX_WIDTH = 2.0  # points: ... and at most this wide
RULE_SAME = 0.004  # two borders closer than this are one
WRAP_ROWS = 2.5  # a line without an amount at most this many text heights under a line item continues it
TABLE_MARGIN = 0.03  # around the table the table finder found (fraction of the page)


def text_table_items(path: str | Path, layout: DocLayout,
                     tables: dict[int, list[Box]] | None = None) -> list[LineReading]:  # fmt: skip
    """The line items of a digital PDF read from its text layer's columns (empty for a scan, or a PDF whose line
    table this reader can't find)."""
    if layout.source != "text":
        return []
    import pymupdf

    from .reader import credit_printed_positive

    items: list[LineReading] = []
    try:
        with pymupdf.open(path) as doc:
            for page_layout in layout.pages:
                if page_layout.number > doc.page_count or not page_layout.words:
                    continue
                rules = vertical_rules(doc[page_layout.number - 1])
                items += _page_items(page_layout, rules, (tables or {}).get(page_layout.number) or [])
    except Exception as exc:  # noqa: BLE001 - an extra reader: never fatal
        log.warning("%s: the PDF's table not read (%s)", Path(path).name, exc)
        return []
    numbered = [re.match(rf"^{i}\s+(?=\S)", li.description) for i, li in enumerate(items, 1)]
    if len(items) >= 2 and all(numbered):  # a "#" column read into the description
        for li, m in zip(items, numbered, strict=True):
            li.description = li.description[m.end() :]
    if items and credit_printed_positive(layout):
        for li in items:
            li.amount = -li.amount if li.amount else li.amount
            li.unit_price = -li.unit_price if li.unit_price else li.unit_price
    return items


def vertical_rules(page: Any) -> list[tuple[float, float, float]]:
    """The lines drawn down a PDF page (cell borders), as (x, y0, y1) fractions of the page as displayed: thin lines
    and rectangles running down it, and the left and right sides of drawn cells."""
    import pymupdf

    rect = page.rect
    width, height = rect.width or 1.0, rect.height or 1.0
    found: list[tuple[float, float, float]] = []

    def add(x0: float, y0: float, x1: float, y1: float) -> None:
        r = pymupdf.Rect(x0, y0, x1, y1)
        if page.rotation:
            r = r * page.rotation_matrix
            r.normalize()
        if r.width <= RULE_MAX_WIDTH and r.height >= RULE_MIN_HEIGHT:
            found.append(((r.x0 + r.x1) / 2 / width, r.y0 / height, r.y1 / height))

    try:
        drawings = page.get_drawings()
    except Exception:  # noqa: BLE001 - a page whose drawings can't be read: no borders
        return []
    for path in drawings:
        for item in path.get("items") or []:
            kind = item[0]
            if kind == "l":
                p, q = item[1], item[2]
                add(min(p.x, q.x), min(p.y, q.y), max(p.x, q.x), max(p.y, q.y))
            elif kind == "re":
                r = item[1]
                if r.width <= RULE_MAX_WIDTH:
                    add(r.x0, r.y0, r.x1, r.y1)
                elif r.height >= RULE_MIN_HEIGHT:  # a drawn cell or band: its two sides
                    add(r.x0, r.y0, r.x0, r.y1)
                    add(r.x1, r.y0, r.x1, r.y1)
    return found


def _header(page: PageLayout) -> tuple[float, dict[str, Box]] | None:
    """The line table's column headings on the page: (their bottom, {column: heading box})."""
    from .reader import _heading_cells

    for line in page.lines:
        cols: dict[str, Box] = {}
        row = [other for other in page.lines if abs(other.box.cy - line.box.cy) < 0.006]
        for other in row:
            for name, box in _heading_cells(other, cols):
                cols[name] = box
        if "amount" in cols and len(cols) >= 2 and ("description" in cols or "quantity" in cols):
            return max(other.box.y1 for other in row), cols
    return None


def _rows(words: list[Word]) -> list[list[Word]]:
    """Words into rows (the same text band), top to bottom, each left to right."""
    rows: list[list[Word]] = []
    for w in sorted(words, key=lambda w: (w.box.cy, w.box.x0)):
        for row in reversed(rows[-4:]):
            ref = row[0].box
            if min(ref.y1, w.box.y1) - max(ref.y0, w.box.y0) > 0.5 * min(ref.height, w.box.height):
                row.append(w)
                break
        else:
            rows.append([w])
    for row in rows:
        row.sort(key=lambda w: w.box.x0)
    return rows


def _gap_cuts(rows: list[list[Word]], lo: float, hi: float) -> list[float]:
    """Column edges where no row writes across: the middle of each strip at least ``MIN_GAP`` wide that every row
    leaves empty, between the table's left and right ends."""
    spans: list[tuple[float, float]] = []
    for row in rows:
        for w in row:
            spans.append((w.box.x0, w.box.x1))
    spans.sort()
    cuts = []
    reach = lo
    for x0, x1 in spans:
        if x0 - reach >= MIN_GAP and reach > lo:
            cuts.append((reach + x0) / 2)
        reach = max(reach, x1)
    return [c for c in cuts if lo < c < hi]


def _rule_cuts(rules: list[tuple[float, float, float]], lo: float, hi: float, top: float, bottom: float) -> list[float]:
    """Cell borders drawn down the table's body (at least half of it, or two rows' worth), between its ends."""
    body = max(bottom - top, 1e-6)
    down = min(0.5 * body, 0.03)
    inside = [(x, y0, y1) for x, y0, y1 in rules if lo + RULE_SAME < x < hi - RULE_SAME]
    xs = sorted(x for x, y0, y1 in inside if min(y1, bottom) - max(y0, top) >= down)
    cuts: list[float] = []
    for x in xs:
        if not cuts or x - cuts[-1] > RULE_SAME:
            cuts.append(x)
    return cuts


def _page_items(page: PageLayout, rules: list[tuple[float, float, float]], tables: list[Box]) -> list[LineReading]:
    from .reader import _CARRIED, _LABELS, _amounts_in, plain

    header = _header(page)
    if header is None:
        return []
    top, cols = header
    heads = list(cols.values())
    hx = (min(b.x0 for b in heads) + max(b.x1 for b in heads)) / 2
    table = next((t for t in tables if t.x0 - TABLE_MARGIN <= hx <= t.x1 + TABLE_MARGIN
                  and t.y0 - TABLE_MARGIN <= top <= t.y1 + TABLE_MARGIN), None)  # fmt: skip
    left, right = (table.x0 - TABLE_MARGIN, table.x1 + TABLE_MARGIN) if table else (0.0, 1.0)
    bottom = table.y1 + TABLE_MARGIN if table else 1.0
    for line in page.lines:  # the table ends at its subtotal or total
        if line.box.y0 > top and any(rx.match(plain(line.text)) for f in ("subtotal", "grand_total", "tax_total")
                                     for rx, _ in _LABELS[f]):  # fmt: skip
            bottom = min(bottom, line.box.y0)
    words = [w for w in page.words if top < w.box.cy < bottom and left <= w.box.cx <= right]
    rows = _rows(words)
    if not rows:
        return []
    lo = min(min(w.box.x0 for w in rows[0]), min(b.x0 for b in heads))
    hi = max(max(w.box.x1 for row in rows for w in row), max(b.x1 for b in heads))
    lo = min(lo, *(w.box.x0 for row in rows for w in row))
    cuts = _rule_cuts(rules, lo, hi, top, rows[-1][-1].box.y1)
    if len(cuts) < 2:
        cuts = _gap_cuts(rows, lo, hi)
    # A cut through a heading splits it: not a column edge.
    cuts = [c for c in cuts if not any(b.x0 + 0.002 < c < b.x1 - 0.002 for b in heads)]
    edges = [lo - 1e-6, *cuts, hi + 1e-6]
    columns = list(zip(edges, edges[1:], strict=False))
    names: list[str] = []
    for a, b in columns:
        named = [name for name, box in cols.items() if a <= box.cx < b]
        names.append(named[0] if len(named) == 1 else ("" if not named else _closest(named, cols, a, b)))
    if "amount" not in names:
        return []
    desc_at = names.index("description") if "description" in names else None

    def cells(row: list[Word]) -> list[str]:
        out = [""] * len(columns)
        for w in row:
            k = next((i for i, (a, b) in enumerate(columns) if a <= w.box.cx < b), len(columns) - 1)
            out[k] = f"{out[k]} {w.text}".strip()
        return out

    items: list[LineReading] = []
    last_y1 = None
    heights = [w.box.height for row in rows for w in row] or [0.01]
    text_height = statistics.median(heights)
    for row in rows:
        texts = cells(row)
        joined = " ".join(t for t in texts if t)
        if _CARRIED.match(plain(joined).strip()):
            continue
        amount_cell = texts[names.index("amount")]
        found = _amounts_in(amount_cell)
        row_box = union_all([w.box for w in row])
        y0 = min(w.box.y0 for w in row)
        if not found:
            words_text = _description(texts, names, desc_at)
            near = last_y1 is not None and y0 - last_y1 <= WRAP_ROWS * text_height
            numbers = any(_amounts_in(texts[i]) for i, n in enumerate(names) if n in ("quantity", "unit_price"))
            if items and near and words_text and not numbers:  # the rest of the description above
                prev = items[-1]
                prev.description = f"{prev.description} {words_text}".strip()
                if row_box is not None and prev.boxes:
                    prev.boxes = [prev.boxes[0].union(row_box)]
                last_y1 = max(w.box.y1 for w in row)
            continue
        amount = found[-1][0]
        qty_text = texts[names.index("quantity")] if "quantity" in names else ""
        price_text = texts[names.index("unit_price")] if "unit_price" in names else ""
        qty, price = _qty_and_price(qty_text, price_text, amount)
        ok = qty is None or price is None or abs(qty * price - amount) <= 0.011
        items.append(LineReading(_description(texts, names, desc_at), qty, price, amount,
                                 [row_box] if row_box else [], 0.8 if ok else 0.5, source="text table"))  # fmt: skip
        last_y1 = max(w.box.y1 for w in row)
    return items


def _qty_and_price(qty_text: str, price_text: str, amount: float) -> tuple[float | None, float | None]:
    """The quantity and unit price cells read as numbers: a price printed "1.459" is 1.459 (a price to three
    decimals) when that, not 1,459, makes the line's amount; first readings otherwise."""
    from .reader import _THREE_PLACES, _amounts_in

    def options(text: str, last: bool) -> list[float]:
        found = _amounts_in(text)
        if not found:
            return []
        value, a, b = found[-1] if last else found[0]
        out = [value]
        if _THREE_PLACES.fullmatch(text[a:b].strip()):
            out.append(round(value / 1000, 4))
        return out

    qs, ps = options(qty_text, False), options(price_text, True)
    for q in qs:
        for p in ps:
            if abs(q * p - amount) <= 0.011:
                return q, p
    return (qs[0] if qs else None), (ps[0] if ps else None)


def _closest(named: list[str], cols: dict[str, Box], a: float, b: float) -> str:
    """Two headings over one column (set close together): the one nearest its middle."""
    mid = (a + b) / 2
    return min(named, key=lambda n: abs(cols[n].cx - mid))


def _description(texts: list[str], names: list[str], desc_at: int | None) -> str:
    """The description cell, with the text of the columns without a heading right of it (the description spread
    over them); codes and numbers in columns without a heading are left out."""
    from .reader import _NUMBER_CELL

    if desc_at is None:
        return " ".join(t for t, n in zip(texts, names, strict=True) if n == "" and t and not _NUMBER_CELL.match(t))
    parts = [texts[desc_at]]
    for i in range(desc_at + 1, len(texts)):
        if names[i]:
            break
        if texts[i] and not _NUMBER_CELL.match(texts[i]):
            parts.append(texts[i])
    return " ".join(p for p in parts if p).strip()
