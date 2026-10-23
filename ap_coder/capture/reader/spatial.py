# ruff: noqa: E501  (label and pattern tables read best one per line)
"""Spatial search: the value nearest a label on the page."""

from __future__ import annotations

import re

from ..normalize import (
    find_gst_numbers,
    find_pst_numbers,
    find_qst_numbers,
    looks_like_money,
)
from ..types import (
    AMOUNT_FIELDS,
    Box,
    Line,
    Reading,
    Word,
    union_all,
)
from ._common import (
    _LABEL_SHAPE,
    plain,
)
from .labels import (
    _DISTRACT,
    _REG_WORDS,
    _LabelHit,
)
from .values import (
    _amounts_in,
    _boxes,
    _label_hits,
    _min_conf,
    _span_words,
    _value_reading,
)

MAX_RIGHT = 0.5  # fraction of page width

BELOW_LINES = 3.2


def _right_of(lines: list[Line], line: Line) -> list[Line]:
    out = []
    for other in lines:
        if other is line or other.box.page != line.box.page or other.box.x0 < line.box.x1 - 0.002:
            continue
        overlap = min(line.box.y1, other.box.y1) - max(line.box.y0, other.box.y0)
        if overlap > 0.5 * min(line.box.height, other.box.height) and other.box.x0 - line.box.x1 < MAX_RIGHT:
            out.append(other)
    return sorted(out, key=lambda ln: ln.box.x0)


def _below(lines: list[Line], line: Line, label_box: Box) -> list[Line]:
    out = []
    h = max(line.box.height, 0.008)
    for other in lines:
        if other is line or other.box.page != line.box.page or other.box.y0 < line.box.y1 - 0.2 * h:
            continue
        if other.box.y0 - line.box.y1 > BELOW_LINES * h:
            continue
        overlap = min(label_box.x1, other.box.x1) - max(label_box.x0, other.box.x0)
        aligned = abs(other.box.x0 - label_box.x0) < 0.03 or abs(other.box.cx - label_box.cx) < 0.04
        if overlap > 0 or aligned:
            out.append(other)
    return sorted(out, key=lambda ln: ln.box.y0)


def _aligned(a: Box, b: Box) -> bool:
    """``b`` is in ``a``'s column: they overlap across, or their left edges or centres line up."""
    overlap = min(a.x1, b.x1) - max(a.x0, b.x0)
    return overlap > 0 or abs(b.x0 - a.x0) < 0.03 or abs(b.cx - a.cx) < 0.04


LABEL_UNDER_GAP = 0.6  # a value printed over its label sits this close to it, in label heights


def _over(lines: list[Line], line: Line, label_box: Box) -> Line | None:
    """The line printed just over ``line`` in the label's column (within ``LABEL_UNDER_GAP`` of its height), if any."""
    h = max(line.box.height, 0.008)
    near = [
        other
        for other in lines
        if other is not line
        and other.box.page == line.box.page
        and other.box.cy < line.box.cy
        and -0.3 * h <= line.box.y0 - other.box.y1 <= LABEL_UNDER_GAP * h
        and _aligned(label_box, other.box)
    ]
    return min(near, key=lambda o: abs(o.box.x0 - label_box.x0), default=None)


def _a_label(line: Line) -> bool:
    return bool(_label_hits(line) or _LABEL_SHAPE.search(plain(line.text).strip()))


def _bare_hits(line: Line) -> list[_LabelHit]:
    """The labels that are all of their line ("Invoice Number", "PO #:"): a grid's label, its value elsewhere."""
    return [h for h in _label_hits(line) if h.start == 0 and not plain(line.text[h.end :]).strip(" :#.")]


def labels_under_values(lines: list[Line]) -> bool:
    """The page prints its header grid's labels UNDER their values ("5874088" over "Invoice Number", the next row's
    "5874082" over "PO #" below it): most bare labels have a value of theirs just over them, closer than any value
    under them. Then a label's value is the one over it, and the one under it is the next label's."""
    under = over = 0
    for line in lines:
        for hit in _bare_hits(line)[:1]:
            if hit.field in AMOUNT_FIELDS:
                continue
            lb = _label_box(hit)
            up = _over(lines, line, lb)
            up_gap = line.box.y0 - up.box.y1 if up and not _a_label(up) and _value_reading(
                hit.field, up, 0, 1.0, "x") else None  # fmt: skip
            down = next((o for o in _below(lines, line, lb)[:1] if _value_reading(hit.field, o, 0, 1.0, "x")), None)
            down_gap = down.box.y0 - line.box.y1 if down else None
            if up_gap is not None and (down_gap is None or up_gap < 0.6 * down_gap):
                under += 1
            elif down_gap is not None and (up_gap is None or down_gap < 0.6 * up_gap):
                over += 1
    return under >= 2 and under > over


def _over_its_own_label(lines: list[Line], value: Line) -> bool:
    """On a page that prints labels under their values: ``value`` has a label of its own just under it."""
    h = max(value.box.height, 0.008)
    return any(
        other is not value
        and other.box.page == value.box.page
        and other.box.cy > value.box.cy
        and -0.3 * h <= other.box.y0 - value.box.y1 <= LABEL_UNDER_GAP * h
        and _aligned(value.box, other.box)
        and (_bare_hits(other) or _labelish(other))
        for other in lines
    )


def _labelish(line: Line) -> bool:
    """A line that reads as some field's label, a wanted one or not ("Quote # / Soumission", "Account #"): no figures,
    and a label's words or marks."""
    p = plain(line.text).strip()
    if not p or any(c.isdigit() for c in p):
        return False
    return _a_label(line) or "#" in p or p.endswith(":") or any(rx.match(p) for rxs in _DISTRACT.values() for rx in rxs)


def _in_a_sentence(up: Line, got: Reading) -> bool:
    """A date read over its label from a line that starts with words ("Mailed 11/14/22 for Account #" over "DUE
    DATE"): another date's sentence, not the label's value."""
    text = up.text.strip()
    return got.field in ("invoice_date", "due_date") and text[:1].isalpha() and not text.startswith(str(got.raw))


def _value_over_label(lines: list[Line], line: Line, hit: _LabelHit) -> Reading | None:
    """One label printed under its value on a page not found to print them all so (a scan's tilt runs a row of
    labels into one line): the line just over the label reads as its value, and the one under it is another label's,
    printed right over that label and farther from this one; or nothing under the label reads as its value, and the
    line over it is no other label's value (no label of its own just over it)."""
    lb = _label_box(hit)
    up = _over(lines, line, lb)
    if up is None or _a_label(up):
        return None
    above = _value_reading(hit.field, up, 0, 0.85 * hit.strength, "label-above")
    if above is None or _in_a_sentence(up, above):
        return None
    down = next(iter(_below(lines, line, lb)[:1]), None)
    if down is None or not _value_reading(hit.field, down, 0, 1.0, "x"):
        hu = max(up.box.height, 0.008)
        labelled = any(
            other is not up
            and other is not line
            and other.box.page == up.box.page
            and other.box.cy < up.box.cy
            and -0.3 * hu <= up.box.y0 - other.box.y1 <= LABEL_UNDER_GAP * hu
            and _aligned(up.box, other.box)
            and (_labelish(other) or bool(_label_hits(other)))
            for other in lines
        )
        return None if labelled else above
    h = max(down.box.height, 0.008)
    over_its_label = any(
        other is not down
        and other.box.page == down.box.page
        and other.box.cy > down.box.cy
        and -0.3 * h <= other.box.y0 - down.box.y1 <= LABEL_UNDER_GAP * h
        and _aligned(down.box, other.box)
        and _labelish(other)
        for other in lines
    )
    if over_its_label and line.box.y0 - up.box.y1 < 0.5 * (down.box.y0 - line.box.y1):
        return above
    return None


def _under_another_label(lines: list[Line], value: Line, label: Line) -> bool:
    """``value`` sits right under a label of its own (a header grid: a row of labels over a row of values), not
    ``label``: on a scan, where the rows are a little off, it can look level with the label on its left."""
    h = max(value.box.height, 0.008)
    for other in lines:
        if other is value or other is label or other.box.page != value.box.page:
            continue
        gap = value.box.y0 - other.box.y1
        if not -0.3 * h <= gap <= 1.2 * h or other.box.cy >= value.box.cy:
            continue
        # In the same row of labels as ``label``, printed as small (a title or a heading higher up is not a grid's
        # label)...
        if abs(other.box.cy - label.box.cy) > 0.8 * label.box.height or other.box.height > 1.5 * label.box.height:
            continue
        # ...and over the value, as a grid prints them: left edges or centres lined up.
        if abs(other.box.x0 - value.box.x0) >= 0.03 and abs(other.box.cx - value.box.cx) >= 0.04:
            continue
        if _label_hits(other) or _LABEL_SHAPE.search(plain(other.text).strip()):
            return True
    return False


def _label_box(hit: _LabelHit) -> Box:
    words = _span_words(hit.line, hit.start, hit.end)
    return union_all([w.box for w in words]) or hit.line.box


# "TPS (824373609RT0001)  $3.82": a receipt prints the supplier's number in brackets after the tax's label, its amount
# further right.
_BRACKETED_REG = re.compile(r"\s*[(（\[]\s*\d{9,10}\s*(?:RT|TQ)\s*\d{4}\s*[)）\]]\s*$", re.I)


def _registration_line(line: Line, hit: _LabelHit) -> bool:
    after = line.text[hit.end :]
    return bool(
        _REG_WORDS.search(plain(after[:12]))
        or find_gst_numbers(after)
        or find_qst_numbers(after)
        or find_pst_numbers(f"PST {after[:40]}")
    )


# "zzgl. Versandkosten 5,80 7", "abzgl. Sondernachlass 10% von 147,30 -14,73": "plus" and "less" open the lines of a
# totals block (the charge, its base, its VAT rate), never a product's row.
_OWN_VALUE = re.compile(r"[\s:#.]*[A-Za-z]{0,3}[-.]?\d")  # a label's value printed after it on its line

_TOTALS_BLOCK_WORD = re.compile(r"(?:zzgl|abzgl|zuzuglich|abzuglich)(?![a-z])")


def _table_row(lines: list[Line], line: Line) -> bool:
    """A line items row: two or more numbers to its right (quantity, price, amount), where a charge in the totals
    prints one."""
    numbers = 0
    for other in lines:
        if other is line or other.box.page != line.box.page or other.box.x0 < line.box.x1 - 0.002:
            continue
        overlap = min(line.box.y1, other.box.y1) - max(line.box.y0, other.box.y0)
        if overlap > 0.5 * min(line.box.height, other.box.height) and _value_reading("subtotal", other, 0, 1.0, "x"):
            numbers += 1
    return numbers >= 2


def _own_label(line: Line, field: str) -> bool:
    return any(h.field == field for h in _label_hits(line))


def _after_another_label(line: Line, got: Reading) -> bool:
    """``got``, read from ``line`` for a label on another line, comes after a label of its own on ``line``
    ("Delivery Date: Sep 20, 2026"): it is that label's value."""
    at = line.text.find(str(got.raw))
    before = plain(line.text[: max(at, 0)]).strip()
    if at <= 0 or not re.search(r"[a-z]", before):
        return False  # nothing before it, or only the colon of the label on its left (": RK21012345")
    return bool(_LABEL_SHAPE.search(before) or before.endswith(":") or any(
        h.field != got.field and h.end <= at for h in _label_hits(line)))  # fmt: skip


_TOTALS_ROW_FIELDS = (*AMOUNT_FIELDS,)

COLUMN_GAP = 0.15  # a column of amounts ends at a gap taller than this (fraction of the page)

_AMOUNT_PART = re.compile(r"^[$€£]?-?[\d.,]+\$?$|^[$€£]$")


def _totals_heading_row(lines: list[Line], line: Line) -> bool:
    """``line`` is one of three or more total labels printed side by side with nothing after them ("Subtotal | GST |
    HST | Total"): the headings of a summary table, one row per kind of charge under them."""
    if line.box.height <= 0:
        return False
    beside = 0
    for other in lines:
        if other is line or other.box.page != line.box.page or abs(other.box.cy - line.box.cy) > 0.5 * line.box.height:
            continue
        hits = [h for h in _label_hits(other) if h.field in _TOTALS_ROW_FIELDS]
        if hits and not any(_value_reading(h.field, other, h.end, 1.0, "x") for h in hits):
            beside += 1
    return beside >= 2


def _column_below(lines: list[Line], line: Line, label_box: Box) -> list[tuple[float, list[Word]]] | None:
    """The amounts printed under a summary table's heading, top to bottom: in each line below, the words under the
    heading (their boxes overlap its width) when they print one amount. ``None`` when text that is no amount is
    printed under it first (another table, a note: no column of figures). A line far taller than the heading (a
    watermark, a logo) is passed over; the column ends at a gap taller than ``COLUMN_GAP``."""
    out: list[tuple[float, list[Word]]] = []
    last = line.box.y1
    for other in sorted(lines, key=lambda ln: ln.box.y0):
        if other is line or other.box.page != line.box.page or other.box.y0 < line.box.y1 - 0.2 * line.box.height:
            continue
        if other.box.height > 3 * max(line.box.height, 0.008):
            continue
        idx = [i for i, w in enumerate(other.words) if min(w.box.x1, label_box.x1) - max(w.box.x0, label_box.x0) > 0]
        if not idx:
            continue
        a, b = idx[0], idx[-1]
        # The rest of an amount that sticks out of the heading's width: "1 936,72 $" under "Total".
        while (
            a > 0
            and _AMOUNT_PART.match(other.words[a - 1].text)
            and other.words[a].box.x0 - other.words[a - 1].box.x1 < 0.012
        ):
            a -= 1
        while (b + 1 < len(other.words) and _AMOUNT_PART.match(other.words[b + 1].text)
               and other.words[b + 1].box.x0 - other.words[b].box.x1 < 0.012):  # fmt: skip
            b += 1
        under = other.words[a : b + 1]
        if other.box.y0 - last > COLUMN_GAP:
            break
        text = " ".join(w.text for w in under)
        amounts = _amounts_in(text)
        if len(amounts) != 1 or not looks_like_money(text) or re.sub(r"[\d\s$€£.,()\-–+]", "", text):
            return out or None
        out.append((round(amounts[0][0], 2), under))
        last = other.box.y1
    return out


def _column_foot(field: str, hit: _LabelHit, lines: list[Line]) -> list[Reading]:
    """A summary table's column: its foot when it is the sum of the rows above it (one row: the row, printed again
    as the foot, or alone). A column of rows with no foot that sums them has no invoice total in it."""
    label_box = _label_box(hit)
    column = _column_below(lines, hit.line, label_box)
    if not column:
        return []
    *rows, (foot, words) = column
    if rows and abs(round(sum(v for v, _ in rows), 2) - foot) > 0.011:
        return []
    raw = " ".join(w.text for w in words)
    return [Reading(field, foot, raw, _boxes(words), 0.95 * hit.strength * _min_conf(words), "label-column-foot")]


_TAX_OR_TOTAL = ("gst_amount", "hst_amount", "pst_amount", "qst_amount", "tax_total")

_RIGHTMOST = (
    *_TAX_OR_TOTAL,
    "subtotal",  # "SUBTOTAL: | 23.00 | 23,115.00": the hours' column, then the amount's
    "grand_total",
    "other_charges",
)  # a row of figures after the label: the last one is the field's


def _rightmost_tax(field: str, hit: _LabelHit, lines: list[Line], first: Line) -> Reading | None:
    """A tax's row printed in cells, its base first and the tax last ("Umsatzsteuer (S) | 275,00 | 7 | 19,25"): the
    rightmost amount printed as money, further right than ``first`` with no label between them."""
    best = None
    row = [
        o
        for o in lines
        if o.box.page == first.box.page
        and o.box.x0 > first.box.x0
        and min(first.box.y1, o.box.y1) - max(first.box.y0, o.box.y0) > 0.5 * min(first.box.height, o.box.height)
    ]
    label = hit.line.box
    for other in sorted(row, key=lambda o: o.box.x0):
        if _label_hits(other):
            break
        far = other.box.x0 - label.x1
        if far > FAR_RIGHT or (
            far > MAX_RIGHT and abs(other.box.cy - label.cy) >= 0.25 * min(label.height, other.box.height)
        ):
            break  # so far across, only a figure level with the label is its (a tilted scan pairs it with the next row)
        got = _value_reading(field, other, 0, 0.9 * hit.strength, "label-right")
        if got and looks_like_money(str(got.raw)):
            got.score *= 1.0 - min(other.box.x0 - hit.line.box.x1, 0.4) * 0.5
            best = got
    return best


FAR_RIGHT = 0.85  # a totals label at the left margin, its amount at the right one (fraction of the page width)


def _far_right(field: str, hit: _LabelHit, lines: list[Line]) -> Reading | None:
    """A totals row printed across the page ("Subtotal ........ $3,050" with the label at the left margin and the
    amount at the right one, further than ``MAX_RIGHT``): its amount printed as money, when the row holds only figures
    (a tax's row may print its base and rate before it: the tax is the last one)."""
    line = hit.line
    # Level with the label (a quarter of a line): so far across, a scan's tilt puts the next row's amount beside it.
    row = [
        o
        for o in lines
        if o is not line
        and o.box.page == line.box.page
        and o.box.x0 >= line.box.x1 - 0.002
        and abs(o.box.cy - line.box.cy) < 0.25 * min(line.box.height, o.box.height)
    ]
    if not row or any(re.sub(r"[\d\s$€£.,()%\-–+]", "", o.text) or o.box.x0 - line.box.x1 > FAR_RIGHT for o in row):
        return None  # something else than figures on the row, or too far
    money = [
        o for o in sorted(row, key=lambda o: o.box.x0) if len(_amounts_in(o.text)) == 1 and looks_like_money(o.text)
    ]
    if not money or (len(money) > 1 and field not in _RIGHTMOST):
        return None
    other = money[-1]  # a tax's row: its base, its rate, the tax last
    got = _value_reading(field, other, 0, 0.9 * hit.strength, "label-right")
    if got:
        got.score *= 0.8
    return got


def _labelled(field_hits: list[_LabelHit], lines: list[Line], under: bool = False) -> list[Reading]:
    """For each label: the value after it on the same line, else just right of it, else just below it
    (header grids print a row of labels over a row of values). ``under``: the page prints its labels under their
    values (``labels_under_values``): the value just over the label, before any below it."""
    out: list[Reading] = []
    for hit in field_hits:
        field, line = hit.field, hit.line
        rest = line.text[hit.end :]
        # (A label's translation after a slash is no note: "Total HST/Montant TVH Total".)
        note = re.sub(r"^\s*/[^\d]*$", "", plain(rest))
        if field in _TAX_OR_TOTAL and len(re.findall(r"[a-z]{3,}", note)) >= 3 and not looks_like_money(rest):
            continue  # "VAT 0% - export of goods outside the EU": a note about the tax, no amount of it
        inline = _value_reading(field, line, hit.end, 0.95 * hit.strength, "label-inline")
        if inline:
            out.append(inline)
            continue
        if field in ("invoice_number", "po_number") and _OWN_VALUE.match(rest):
            continue  # "NO.4 &6,": the label's value is printed after it and is no number; the next line's is not its
        if field in _TAX_OR_TOTAL and re.fullmatch(r"\s*[(\[]?\s*%\s*[)\]]?\s*", line.text[hit.end :]):
            continue  # "USt. %", "Tax [%]": the heading of a column of rates, not of the tax
        if field in AMOUNT_FIELDS and _totals_heading_row(lines, line):
            out += _column_foot(field, hit, lines)
            continue
        if field in AMOUNT_FIELDS and _registration_line(line, hit) and not _BRACKETED_REG.match(line.text[hit.end :]):
            continue  # "GST/HST Reg. No.: 988130969 RT0003": the number's label, not the tax amount's
        if field == "other_charges" and not _TOTALS_BLOCK_WORD.match(hit.label_text) and _table_row(lines, line):
            continue  # "Shipping cartons 18x12x12 | 100 | 47.04 | 4,704.00": a product, not the freight
        got = None
        for other in _right_of(lines, line)[:2]:
            if _own_label(other, field):
                break  # "Inv 7" beside or under a title: that line's own label reads its value
            if (_label_hits(other) or _LABEL_SHAPE.search(plain(other.text).strip())) and not _value_reading(
                field, other, 0, 1.0, "x"
            ):
                break  # the next thing to the right is another label ("Ship Date"): look below instead
            if _under_another_label(lines, other, line):
                break  # a header grid's value, under its own label ("Order No." over "SO-28797"): look below
            if under and _over_its_own_label(lines, other):
                break  # labels under values: the value over the next label ("A-26-54597" over "Applies to Invoice")
            got = _value_reading(field, other, 0, 0.9 * hit.strength, "label-right")
            if got and _after_another_label(other, got):
                got = None
                break  # "Delivery Date: Sep 20, 2026": that label's value, not this one's
            if got:
                got.score *= 1.0 - min(other.box.x0 - line.box.x1, 0.4) * 0.5
                rightmost = _rightmost_tax(field, hit, lines, other) if field in _RIGHTMOST else None
                if rightmost is not None:
                    got.score *= 0.85
                    out.append(got)
                    got = rightmost
                break
        if got is None and under and field not in AMOUNT_FIELDS:
            # A grid that prints each label under its value: the value is the line just over the label.
            up = _over(lines, line, _label_box(hit))
            if up is not None and not _a_label(up):
                got = _value_reading(field, up, 0, 0.85 * hit.strength, "label-above")
                if got and _in_a_sentence(up, got):
                    got = None
        elif got is None and field not in AMOUNT_FIELDS:
            got = _value_over_label(lines, line, hit)
        if got is None and field in AMOUNT_FIELDS:
            got = _far_right(field, hit, lines)
        if got is None and field in _RIGHTMOST:
            # "abzgl. Sondernachlass | 10% | von | 147,30 | -14,73": words and a base before the amount.
            got = _rightmost_tax(field, hit, lines, line)
        if got is None:
            lb = _label_box(hit)
            for other in _below(lines, line, lb)[:3]:
                if _own_label(other, field):
                    break
                if under and _over_its_own_label(lines, other):
                    break  # the value of the label printed under it, not this one's
                got = _value_reading(field, other, 0, 0.85 * hit.strength, "label-below")
                if got and _after_another_label(other, got):
                    got = None
                    break  # "Due Date: Upon Receipt" over "Delivery Date: Sep 20, 2026": not the due date
                if got:
                    break
                if _label_hits(other) or _LABEL_SHAPE.search(plain(other.text).strip()):
                    break  # the next row down is another label ("Account #"): this label has no value under it
        if got is None and field in _HEADER_IDS_AND_DATES:
            got = _far_right_header(field, hit, lines)
        if got is not None:
            out.append(got)
    return out


_HEADER_IDS_AND_DATES = ("invoice_number",)  # a date or PO alone across the page may be the due date, an account


def _far_right_header(field: str, hit: _LabelHit, lines: list[Line]) -> Reading | None:
    """A header row printed across the page: "Date" at the left margin, "11/4/2025" alone at the right one (further
    than ``MAX_RIGHT``). Only for a label with nothing after it, and a row that holds the value and nothing else: its own
    method ("label-far-right"), weaker than a value beside its label."""
    line = hit.line
    if hit.strength < 0.55 or hit.weak or plain(line.text[hit.end :]).strip(" :#.-"):
        return None  # a bare title ("INVOICE", "FACTURE") is no label for a value across the page
    row = [
        o
        for o in lines
        if o is not line
        and o.box.page == line.box.page
        and o.box.x0 >= line.box.x1 - 0.002
        and abs(o.box.cy - line.box.cy) < 0.25 * min(line.box.height, o.box.height)
    ]
    if len(row) != 1 or not MAX_RIGHT < row[0].box.x0 - line.box.x1 <= FAR_RIGHT or _label_hits(row[0]):
        return None  # nearer, the value beside the label was looked at already (and may be another label's)
    got = _value_reading(field, row[0], 0, 0.75 * hit.strength, "label-far-right")
    if got is None or plain(row[0].text).strip(" :#.") != plain(str(got.raw)).strip(" :#."):
        return None  # the row holds more than the value
    if field in ("invoice_number", "po_number") and _REGISTRATION_SHAPE.fullmatch(str(got.value)):
        return None  # "R123456789", "123456789 RT0001": a GST/HST or business number, not the invoice's
    return got


_REGISTRATION_SHAPE = re.compile(r"(?i)R?\s?\d{9}(?:\s?(?:RT|RP|TQ)\s?\d{4})?|\d{10}(?:\s?TQ\s?\d{4})?")
