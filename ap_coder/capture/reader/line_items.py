# ruff: noqa: E501  (label and pattern tables read best one per line)
"""Line items: the table of what was bought."""

from __future__ import annotations

import re
import statistics

from ..normalize import (
    CREDIT_TITLES,
    looks_like_money,
)
from ..types import (
    Box,
    DocLayout,
    Line,
    LineReading,
    PageLayout,
    Word,
    union_all,
)
from ._common import (
    _HEAD,
    plain,
)
from .labels import (
    _DISTRACT,
    _LABELS,
)
from .values import (
    _amounts_in,
    _span_words,
)

_TOTAL_LABELS = ("subtotal", "grand_total", "tax_total")  # the line table ends at these

_FIGURE = re.compile(r"[#(]?[\d.,:/-]*\d[\d.,:/-]*[)%]?")  # a word that is a number: "9", "(2)", "5%", "2026-01"

_NUMBER_CELL = re.compile(r"^[\s$€£¢()+\-–.,'%x×@#]*\d[\d\s$€£¢()+\-–.,'%x×@]*(?:[a-z]{1,3}\.?)?\s*$", re.I)

# A row of a long table that sums its lines: carried from page to page ("Balance brought forward / Montant reporté",
# "Übertrag"), or the page's own subtotal ("Page subtotal", "Sous-total de la page", "Total de la page").
_CARRIED = re.compile(r"^(a\s*reporter|report\b|reporte|carried\s*forward|brought\s*forward|"
                      r"balance\s*(?:brought\s*|carried\s*)?forward|montant\s*report|u(?:e)?bertrag|"
                      r"continued|suite|sub\s*-?\s*total|sous\s*-?\s*total|page\s*(?:sub\s*-?\s*)?total|"
                      r"total\s*(?:de\s*la\s*)?page)")  # fmt: skip

_CREDIT_MARK = re.compile(r"\d[.,]\d{2}cr", re.I)  # "342.18CR": a credit's mark run into its amount


def _printed_as_money(token: str) -> bool:
    """A line's amount printed as money, as OCR reads it too: "1530:00" (the point read as a colon) and "342.18CR"
    (a credit's mark run into it) are money; "5%" and "12" are not."""
    from ..normalize import _ocr_amount_text

    fixed = _ocr_amount_text(token)
    return looks_like_money(fixed) or bool(_CREDIT_MARK.search(fixed))


_CREDIT_TITLE = re.compile(r"^(?:" + "|".join(CREDIT_TITLES) + r")(?![a-z])")  # as email triage reads them

# A total's label that says the document credits: "CREDIT TOTAL:", "Total Credit / Montant crédité", "Amount credited".
_CREDIT_TOTAL = re.compile(
    r"^(?:credit\s*total|total\s*(?:du\s*)?credit(?:ed)?|amount\s*credited|montant\s*(?:total\s*)?credite|"
    r"total\s*(?:de\s*la\s*)?note\s*de\s*credit)(?![a-z])(?!\s*(?:applied|available|used|received|applique|utilise))"
)

# The page says in so many words that it is a credit: "DO NOT PAY – THIS IS A CREDIT MEMO".
# The statement fills its line ("DO NOT PAY – THIS IS A CREDIT MEMO"); a condition in a sentence ("If this is a credit
# memo, do not pay.", "Unless this is a credit memo, payment is due ...") says nothing about this document.
_CREDIT_STATED = re.compile(
    r"^\W*(?:(?:please\s+)?do\s+not\s+pay\W+)?this\s+(?:document\s+)?is\s+an?\s+credit\s*(?:note|memo(?:randum)?)"
    r"(?:\W+(?:do\s+not\s+pay|no\s+payment\s+(?:is\s+)?(?:required|due)))?\W*$"
)


def credit_titled(layout: DocLayout) -> bool:
    """The document calls itself a credit note ("CREDIT MEMO", "Note de crédit", "AVOIR" ...) at the top of its
    first page, or its total is labelled a credit ("CREDIT TOTAL", "Total Credit / Montant crédité"): a fax or a scan
    can lose the title, never both. Or it says so on a line of its own in its body (``credit_stated``)."""
    return _credit_titled(layout) or credit_stated(layout)


def credit_stated(layout: DocLayout) -> bool:
    """ "DO NOT PAY – THIS IS A CREDIT MEMO" on a line of its own on the first page, under a title that does not say it."""
    return bool(layout.pages) and any(_CREDIT_STATED.search(plain(ln.text)) for ln in layout.pages[0].lines)


def _credit_titled(layout: DocLayout) -> bool:
    if not layout.pages:
        return False
    if any(ln.box.cy < 0.35 and _CREDIT_TITLE.match(plain(ln.text).strip()) for ln in layout.pages[0].lines):
        return True
    from ..normalize import find_amounts

    for page in layout.pages:
        amounts = {id(ln): [abs(v) for v, a, b in find_amounts(ln.text) if looks_like_money(ln.text[a:b])]
                   for ln in page.lines}  # fmt: skip
        largest = max((v for vs in amounts.values() for v in vs), default=0.0)
        for line in page.lines:
            if not _CREDIT_TOTAL.match(plain(line.text).strip()):
                continue
            # The credit total is the document's largest amount (an invoice's "Credit total" line is not its total).
            row = [line] + sorted((o for o in page.lines if o is not line and abs(o.box.cy - line.box.cy) < 0.006
                                   and o.box.x0 >= line.box.x0), key=lambda o: o.box.x0)  # fmt: skip
            got = next((amounts[id(o)] for o in row if amounts[id(o)]), [])
            if got and got[-1] >= largest - 0.005:
                return True
    return False


def credit_printed_positive(layout: DocLayout) -> bool:
    """A credit memo (titled so at the top of its first page) whose subtotal and total are printed without a sign:
    its amounts are credits however they are printed, and AP posts credits as negative amounts. The subtotal's and
    total's own signs decide: one adjustment line printed negative ("Restocking fee -50.00") is that line's sign,
    not the document's. With no subtotal or total found, any amount printed with a sign means the signs are printed.
    A zero has no sign ("Balance Due $0.00": nothing left to pay), and a line that only looks like a total
    ("Previous Balance", "Payments received") is not the credit's own: neither votes."""
    if not credit_titled(layout):
        return False
    from ..normalize import find_amounts

    def money(line: Line) -> list[float]:
        return [v for v, a, b in find_amounts(line.text) if looks_like_money(line.text[a:b])]

    totals: list[float] = []
    for page in layout.pages:
        for line in page.lines:
            p = plain(line.text).strip()
            if not any(rx.match(p) for f in ("subtotal", "grand_total") for rx, _ in _LABELS[f]):
                continue
            if any(rx.match(p) for rx in _DISTRACT["grand_total"]) and not any(
                rx.match(p) for rx, _ in _LABELS["subtotal"]
            ):
                continue  # "Previous Balance": a total's label, not this credit's total (a subtotal is one of them)
            # The amount on the label's line, or in the cells beside it on its row.
            row = [line] + sorted((o for o in page.lines if o is not line and abs(o.box.cy - line.box.cy) < 0.006
                                   and o.box.x0 >= line.box.x0), key=lambda o: o.box.x0)  # fmt: skip
            totals += [v for v in next((m for m in map(money, row) if m), [])[-1:] if v]
    if totals:
        return all(v > 0 for v in totals)
    return not any(v < 0 for line in layout.lines() for v in money(line))  # the signs are printed: read as they are


_TAX_HEAD = re.compile(r"^(?:vat|tax|gst|hst|pst|qst|tps|tvq|tvh|tva|ust|mwst|iva)\b")  # a tax column's heading word

_ROW_NUMBER_HEAD = re.compile(r"^(?:no\.?|#|pos\.?|n\s?°)\s+(?=[a-z])")  # "No. Description": the row number's heading

# The heading of a line table's tax-code column ("Tax", "Tax code", "Tx", "GST/PST", "Code taxe"): its cells say which
# taxes the line carries ("G", "GP", "G R", "E", "*"), kept as the line's ``tax_flags`` and not in its description.
_TAX_CODE_HEAD = re.compile(
    r"^(?:tax(?:es)?(?:\s*(?:code|cd|cat(?:egory)?|flag|type|ind(?:icator)?))?|tx|taxe?s?\s*code|codes?\s*(?:de\s*)?"
    r"tax(?:e|es)?|(?:gst|tps)\s*/\s*(?:pst|hst|qst|rst|tvq|tvh)|(?:gst|hst|pst|qst|rst|tps|tvq|tvh))\s*:?$"
)

# A tax-code cell: letters ("G", "GP", "G R", "G,P"), a star, or a word that says it ("Exempt", "GST/PST", "HST ON").
_TAX_FLAG_CELL = re.compile(
    r"^(?:[A-Z*]{1,3}(?:[\s,/+&]+[A-Z*]{1,3}){0,3}"
    r"|(?i:exempt(?:ed)?|exon[ée]r[ée]e?|zero[- ]?rated|non[- ]?taxable|taxable|n\s*/\s*[ta]|yes|no|oui|non)"
    r"|(?i:(?:gst|hst|pst|qst|rst|tps|tvq|tvh)(?:\s*[/+&]\s*(?:gst|hst|pst|qst|rst|tps|tvq|tvh))?)(?:\s+[A-Z]{2})?)$"
)


def tax_code_column(row: list[Line], cols: dict[str, Box]) -> Box | None:
    """The box of the line table's tax-code column heading in its heading row (``heading_row``'s lines), when it has
    one: a segment that is only that heading, or such a word in a run of headings ("Unit Price Tax Amount"). None when
    there is none, or it is one of the named columns."""
    for line in row:
        p = plain(line.text).strip()
        if _TAX_CODE_HEAD.match(p):
            box = line.box
        else:
            word = next((w for w in line.words if re.fullmatch(r"tax(?:es)?|tx|taxe?s?", plain(w.text).strip(" :"))),
                        None)  # fmt: skip
            if word is None or not _heading_cells(line, {}):
                continue
            box = word.box
        if not any(abs(box.cx - b.cx) < 0.01 and abs(box.cy - b.cy) < 0.01 for b in cols.values()):
            return box
    return None


def tax_flag_cell(text: str) -> bool:
    """Whether a cell's text reads as a line's tax code ("G", "G R", "GP", "E", "*", "Exempt", "HST ON")."""
    return bool(_TAX_FLAG_CELL.match(" ".join((text or "").split())))


def _under_tax_column(seg: Line, column: Box | None) -> bool:
    return column is not None and abs(seg.box.cx - column.cx) < max(0.035, column.width) and tax_flag_cell(seg.text)


def _heading_name(text: str) -> str | None:
    """The column a heading names, by the longest heading it starts with ("Unit Cost" is the unit price, not a
    quantity in "Unit"; "Prix total" the amount, not a price); a heading printed in two languages ("Product /
    Service / Désignation") by its parts, when they name one column; a row number's heading before it is no part of
    it ("No. Description", "# Item"). None when it names none, or two."""
    text = _ROW_NUMBER_HEAD.sub("", text)
    longest = {name: m.end() for name, rx in _HEAD.items() if (m := rx.match(text))}
    if longest:
        top = max(longest.values())
        named = [name for name, end in longest.items() if end == top]
        return named[0] if len(named) == 1 else None
    if "/" not in text or any(rx.match(text) for f in _TOTAL_LABELS for rx, _ in _LABELS[f]):
        return None  # "Balance Due / Total à payer" is the invoice's total, not a column
    parts = {_heading_name(part.strip()) for part in text.split("/") if part.strip()} - {None}
    return parts.pop() if len(parts) == 1 else None


def _heading_cells(line: Line, taken: dict[str, Box]) -> list[tuple[str, Box]]:
    """The column headings a line segment holds, each with its box. Headings set close together read as one
    segment ("Qty Unit Price"): when the segment is nothing but two or more different headings, each gets the
    box of its own words, so a column's values are looked for under its heading and not the segment's middle. A
    heading printed in two languages is one heading ("Price / Taux Ext. Price / Prix total": the unit price, then
    the amount); words after a heading are more of it ("Price Each"); a second heading naming a column already named
    is another column without a name ("Quantity Unit Unit Cost": the unit of measure between the quantity and the
    price). A segment with a number in it is no heading ("Unit 9" is an address; "Rate per m3" can be)."""
    p = plain(line.text).strip()
    if any(_FIGURE.fullmatch(word) for word in p.split()):
        return []
    name = _heading_name(p)
    whole = [(name, line.box)] if name and name not in taken else []
    # (word or "/", its box): "Hrs/Qty" is "hrs", "/", "qty"; "UnitéRate" (two headings a PDF's text or OCR runs
    # together) "unite", "rate", each with its share of the word's box.
    tokens: list[tuple[str, Box]] = []
    for w in line.words:
        at = 0
        for piece in re.split(r"(?<=[a-zà-ÿ])(?=[A-Z])", w.text):
            share = w.box.width / max(len(w.text), 1)  # a piece's box: its share of the word's
            box = Box(w.box.page, w.box.x0 + at * share, w.box.y0, w.box.x0 + (at + len(piece)) * share, w.box.y1)
            tokens += [(part, box) for part in re.split(r"(/)", plain(piece)) if part.strip()]
            at += len(piece)
    words = [t for t, _ in tokens]
    if not 2 <= sum(t != "/" for t in words) <= 16 or "/" in (words[0], words[-1]):
        return whole
    cells: list[tuple[str, int, int]] = []  # (heading, first token, last token + 1); "" another column's heading
    extra = 0  # words of the last heading that are no heading themselves, not counting its other language's
    taxed = False  # a tax's column set close beside a heading ("Amount VAT")
    noise = 0  # such words, and headings naming a column already named, in the whole segment
    i = 0
    while i < len(words):
        joined = words[i] == "/"  # a heading's other language follows
        i += joined
        rest = " ".join(words[i:])
        ends = [len(" ".join(words[i : j + 1])) for j in range(i, len(words))]
        best = None
        for heading, rx in _HEAD.items():
            m = rx.match(rest)
            if m and m.end() in ends and (best is None or m.end() > best[1]):
                best = (heading, m.end())  # the longest heading here: "Unit Price", not "Unit"
        if best is None and cells and not joined and _TAX_HEAD.match(words[i]):
            cells.append(("", i, i + 1))  # a tax's column ("Net worth VAT[%]"): another column, without a name
            taxed = True
            extra = 0
            i += 1
            continue
        if best is None and cells:  # more words of the heading before: "Price Each", "Qty Shipped"
            extra += not joined
            noise += not joined
            if extra > 2:
                return whole  # a sentence that starts with a heading's word, not a run of headings
            cells[-1] = (cells[-1][0], cells[-1][1], i + 1)
            i += 1
            continue
        if best is None:
            return whole  # a segment that starts with no heading
        extra = 0
        n = ends.index(best[1]) + 1
        seen = any(c[0] == best[0] for c in cells)
        if joined and (cells[-1][0] == best[0] or (cells[-1][0] == "" and seen)):  # the other language
            cells[-1] = (cells[-1][0], cells[-1][1], i + n)
        else:  # (after a "/", another column: OCR runs "Qty/Qté Price Each/Taux" together)
            cells.append(("" if seen else best[0], i, i + n))
            noise += seen
        i += n
    columns = sum(1 for c in cells if c[0])
    if (columns < 2 and not (columns and taxed)) or noise > columns:  # (more words that are no heading: a sentence)
        return whole
    out = []
    for heading, a, b in cells:
        box = union_all([box for _, box in tokens[a:b]])
        if heading and heading not in taken and box is not None:
            out.append((heading, box))
    return out


def heading_row(page: PageLayout) -> tuple[Line, list[Line], dict[str, Box]] | None:
    """The line table's column headings on the page: (the first line of their row, the row's lines, {column: heading
    box}). A row of headings names the amount and the description or the quantity; of several, the one naming the
    most columns (an address's "Unit 9" set level with "Amount Due" at the top of a bill names two). None when there
    is none."""
    named = {id(line) for line in page.lines if _heading_cells(line, {})}  # the lines that hold a heading at all
    best = None
    for line in page.lines:
        row = [other for other in page.lines if abs(other.box.cy - line.box.cy) < 0.006]
        cols: dict[str, Box] = {}
        for other in row:
            if id(other) in named:
                for name, box in _heading_cells(other, cols):
                    cols[name] = box
        if "amount" in cols and len(cols) >= 2 and ("description" in cols or "quantity" in cols):
            if best is None or len(cols) > len(best[2]):
                best = (line, row, cols)
    return best


_THREE_PLACES = re.compile(r"-?\d{1,3}([.,])\d{3}")  # "1.459": a price to three decimals, or 1,459

_SPACED_PAIR = re.compile(r"(\d{1,3})[   ](\d{3}[.,]\d{1,2})")  # "3 250,00": quantity 3 at 250,00

_SPACED_WHOLE = re.compile(r"(\d{1,3})[   ](\d{3})")  # "2 500": 2500 (French thousands), or 2 and 500


def spaced_whole(text: str) -> float | None:
    """A cell that is one whole number in one space group ("2 500", "6 000"): the number kept whole (2500).
    ``find_amounts`` reads it as two numbers (a quantity and a price); the line's amount tells which it is."""
    m = _SPACED_WHOLE.fullmatch(text.strip())
    return float(m.group(1) + m.group(2)) if m else None


def _qty_times_price(segs: list[Line], nums: list[tuple[Line, list[tuple[float, int, int]]]], amt_seg: Line | None,
                     amount: float) -> tuple[float, float] | None:  # fmt: skip
    """The quantity and unit price of a row whose columns did not give two that make its amount: two numbers
    left of the amount, in order, whose product is the amount. A number printed "1.459" may be a price to three
    decimals as well as 1459, and "3 250,00" a quantity and a price set close together; among readings that fit,
    those that read each separator as the amount's own decimal mark (or not) the way the amount does win."""
    amt_text = (amt_seg.text if amt_seg else "").strip()
    decimal = "," if re.search(r",\d{2}(?!\d)", amt_text) and not re.search(r"\.\d{2}(?!\d)", amt_text) else "."
    # (position, value, how unnatural the reading is)
    options: list[tuple[float, float, int]] = []
    k = 0
    for s in sorted(segs, key=lambda seg: seg.box.x0):
        found = next((f for seg, f in nums if seg is s), [])
        if s is amt_seg:
            found = found[:-1]  # the amount itself
        whole = spaced_whole(s.text) if s is not amt_seg and len(found) == 2 else None
        if whole is not None:
            options.append((k + 1, whole, 0))  # "2 500" kept whole, in the place of its first part
        for value, a, b in found:
            token = s.text[a:b].strip()
            k += 1
            options.append((k, value, 0))
            three = _THREE_PLACES.fullmatch(token)
            if three:
                as_decimal = round(value / 1000, 4)
                natural = three.group(1) == decimal
                options[-1] = (k, value, 1 if natural else 0)
                options.append((k, as_decimal, 0 if natural else 1))
            pair = _SPACED_PAIR.fullmatch(token)
            if pair:
                second = re.sub(r"[.,]", ".", pair.group(2))
                options.append((k, float(pair.group(1)), 1))
                options.append((k + 0.5, float(second), 1))
    # A credit's line (-200.00, "200.00 CR") prints its price with or without the sign: the price is a credit too,
    # unless the quantity carries the sign (-4 returned at 45.13).
    credit = amount < 0
    best = None
    for k1, q, cost1 in options:
        for k2, p, cost2 in options:
            signed = credit and q < 0 < p  # (a "-2" in "6205-2RS" is no quantity: a reading without one first)
            price = p if signed else -abs(p) if credit else p
            if k2 <= k1 or (q <= 0 and not signed) or abs(q * price - amount) > 0.011:
                continue
            if best is None or cost1 + cost2 + signed < best[0]:
                best = (cost1 + cost2 + signed, q, price)
    return (best[1], best[2]) if best else None


# How far around a table the table finder found its words are still taken: its box can sit a hair inside the first and
# last column, or just under the column headings (fractions of the page).
TABLE_MARGIN_X = 0.03

TABLE_MARGIN_TOP = 0.02

TABLE_MARGIN_BOTTOM = 0.01


def inside_tables(layout: DocLayout, tables: dict[int, list[Box]]) -> DocLayout:
    """``layout`` with, on each page where the table finder found tables, only the words inside them (their middle
    within ``TABLE_MARGIN_*`` of a table), put back into lines: text printed beside or between the rows is left out.
    A page where it found none keeps all its words."""
    from ..layout import group_lines
    from ..types import PageLayout

    pages = []
    for page in layout.pages:
        boxes = tables.get(page.number) or []
        if not boxes:
            pages.append(page)
            continue

        words = [w for w in page.words if _in_table(w, boxes)]
        pages.append(PageLayout(page.number, page.width, page.height, words, group_lines(words, page.number),
                                page.rotation, page.skew))  # fmt: skip
    return DocLayout(pages, layout.source)


def _in_table(w: Word, boxes: list[Box]) -> bool:
    return any(b.x0 - TABLE_MARGIN_X <= w.box.cx <= b.x1 + TABLE_MARGIN_X
               and b.y0 - TABLE_MARGIN_TOP <= w.box.cy <= b.y1 + TABLE_MARGIN_BOTTOM for b in boxes)  # fmt: skip


STRAIGHTEN_FROM = 0.002  # a scan tilted more than this (down per across: about a tenth of a degree) is straightened


def straightened(layout: DocLayout) -> DocLayout:
    """``layout`` with each tilted scan's words moved up or down to where they would be on a straight page
    (``PageLayout.skew``), then put back into lines: on a page scanned at 1.4 degrees, a row's description sits a
    whole row higher or lower than its amount at the other end of the table. Pages that aren't tilted are kept."""
    from ..layout import group_lines
    from ..types import PageLayout

    pages = []
    for page in layout.pages:
        if abs(page.skew) < STRAIGHTEN_FROM:
            pages.append(page)
            continue
        words = []
        for w in page.words:
            dy = -page.skew * (w.box.cx - 0.5)
            words.append(Word(w.text, Box(w.box.page, w.box.x0, w.box.y0 + dy, w.box.x1, w.box.y1 + dy), w.conf,
                              w.source))  # fmt: skip
        pages.append(PageLayout(page.number, page.width, page.height, words, group_lines(words, page.number),
                                page.rotation, page.skew))  # fmt: skip
    return DocLayout(pages, layout.source)


def _on_tilted_page(box: Box, skew: float) -> Box:
    """A box on the straightened page, back on the tilted one: the box around where it is printed."""
    a, b = skew * (box.x0 - 0.5), skew * (box.x1 - 0.5)
    return Box(box.page, box.x0, box.y0 + min(a, b), box.x1, box.y1 + max(a, b))


AMOUNT_COLUMN = 0.04  # a row's amount ends this close to the amount column's right edge (fraction of the page)

# A tax's or a total's row in a table read without its headings, in any wording ("Umsatzsteuer 19 %", "Steuerbasisbetrag
# USt. 7%", "GST 5%", "Positionssumme", "Rechnungssumme netto", "Total TTC"): no line.
_TAX_ROW = re.compile(r"\b(?:tax(?:es)?|gst|hst|pst|qst|tps|tvq|tvh|rst|vat|tva|ust|mwst|iva|btw|ppn)\b|"
                      r"steuer|summe|gesamt|\btotal|totaal|\bsomme\b|\bnetto\b|\bbrutto\b|betrag\b|\bh\.?t\.?$|\bttc\b|"
                      r"amount\s*due|balance\s*due")  # fmt: skip

SUM_ROWS_ABOVE = 2  # a row is the sum of the rows above it when they are at least this many lines with an amount


def _headerless_rows(page: PageLayout, whole: PageLayout | None = None) -> tuple[list[LineReading], float | None]:
    """The rows of one table the table finder found whose column headings can't be read (OCR runs bilingual headings
    together, or they are in a language the reader doesn't know): each row whose last cell is an amount, in the
    column where most rows end, down to the subtotal. The numbers before it are the quantity and unit price when they
    make the amount; the words are the description. With the amount on the totals' row the rows end at (None: none).
    Rows read this way are a guess, kept only when they add up (``_proven_rows``). None when the table found stops
    short of the rows' last amount on the page (``whole``: the whole page): its last column is not theirs (a tax's
    column, the total's beside it)."""
    rows: dict[float, list[Line]] = {}
    for line in page.lines:
        key = next((k for k in rows if abs(k - line.box.cy) < 0.006), line.box.cy)
        rows.setdefault(key, []).append(line)
    ordered = [sorted(segs, key=lambda s: s.box.x0) for _, segs in sorted(rows.items())]

    def money(seg: Line) -> list[tuple[float, int, int]]:  # amounts printed as money: "1,250.00", "$12"
        return [(v, a, b) for v, a, b in _amounts_in(seg.text) if _printed_as_money(seg.text[a:b])]

    ends = [segs[-1].box.x1 for segs in ordered if len(segs) >= 2 and money(segs[-1])]
    if len(ends) < 2:
        return [], None
    right = statistics.median(ends)
    items: list[LineReading] = []
    printed_sum = None  # the amount on the totals' row the rows end at
    for segs in ordered:
        p = plain(" ".join(s.text for s in segs))
        if any(rx.match(plain(s.text)) for s in segs for f in ("subtotal", "grand_total", "tax_total")
               for rx, _ in _LABELS[f]):  # fmt: skip
            if not items:
                continue  # a total's row before the rows: no line either
            found = money(segs[-1])
            printed_sum = found[-1][0] if found else None
            break  # the table's totals: its rows end above
        last = segs[-1]
        found = money(last)
        if not found or abs(last.box.x1 - right) > AMOUNT_COLUMN or len(segs) < 2 or _CARRIED.match(p.strip()):
            continue
        if whole is not None and any(abs(o.box.cy - last.box.cy) < 0.006 and o.box.x0 > last.box.x1 + 0.005 and money(o)
                                     for o in whole.lines):  # fmt: skip
            return [], None
        amount = found[-1][0]
        desc = " ".join(s.text for s in segs if s is not last and not _NUMBER_CELL.match(s.text)).strip()
        if not desc or _TAX_ROW.search(plain(desc)):
            continue
        fit = _qty_times_price(segs, [(s, _amounts_in(s.text)) for s in segs], last, amount)
        qty, price = fit if fit else (None, None)
        if not amount and qty is None:
            continue  # "Erhaltene Anzahlungen 0,00": nothing to code
        row_box = union_all([s.box for s in segs])
        items.append(LineReading(desc, qty, price, amount, [row_box] if row_box else [], 0.6))
    return items, printed_sum


def _proven_rows(items: list[LineReading], others: list[LineReading], printed_sum: float | None,
                 targets: list[float] | None, printed: set[float] | None = None) -> list[LineReading]:  # fmt: skip
    """The rows read by their amounts (``items``, of every page in order) that add up, else none. They end at the
    first one that prints the sum of the rows above it (at least ``SUM_ROWS_ABOVE`` lines: a subtotal in a wording the
    reader doesn't know, "Positionssumme", "Summe netto"), the rows between them (allowances, taxes) and after it left
    out; else they are all kept when they add up, alone or with the lines read under column headings on other pages
    (``others``), to the totals' row they end at (``printed_sum``), to one of ``targets`` (the invoice's subtotal, or
    its total less the taxes) or, two lines or more, to an amount printed elsewhere on the invoice (``printed``: its
    subtotal under a label the reader doesn't know)."""
    for k, li in enumerate(items):
        above = 0.0
        lines = 0
        best = None
        for j, prev in enumerate(items[:k], 1):
            above += prev.amount or 0.0
            lines += bool(prev.amount)
            if (
                lines >= SUM_ROWS_ABOVE
                and li.amount
                and li.quantity is None
                and abs(round(above, 2) - li.amount) <= 0.011
            ):  # (a row with a quantity is a line)
                best = j
        if best is not None:
            return items[:best]
    sums = [t for t in (printed_sum, *(targets or [])) if t is not None]
    own = {round(li.amount, 2) for li in [*others, *items] if li.amount is not None}
    for rows in (items, [*others, *items]) if others else (items,):
        total = round(sum(li.amount or 0.0 for li in rows), 2)
        more = [t for t in printed or () if t and t not in own]  # (not one of the rows' own amounts)
        if any(abs(total - t) <= 0.02 for t in sums) or (
            sum(1 for li in rows if li.amount) >= SUM_ROWS_ABOVE and any(abs(total - t) <= 0.02 for t in more)
        ):
            return items
    return []


def read_line_items(layout: DocLayout, tables: dict[int, list[Box]] | None = None,
                    targets: list[float] | None = None) -> list[LineReading]:  # fmt: skip
    """The line items under the line table's column headings, read on the page straightened (``straightened``).
    ``tables``: the tables the table finder found on each page (``table_finder.find_tables``); the rows are then read
    from the words inside them only (``inside_tables``). ``targets``: what the lines should add up to (the subtotal,
    the total less the taxes), for a table whose headings can't be read (``_headerless_rows``)."""
    skews = {page.number: page.skew for page in layout.pages if abs(page.skew) >= STRAIGHTEN_FROM}
    one_table = {n: [straightened(inside_tables(layout, {n: [box]})) for box in boxes]
                 for n, boxes in (tables or {}).items() if boxes}  # fmt: skip
    printed = {round(v, 2) for line in layout.lines() for v, a, b in _amounts_in(line.text)
               if _printed_as_money(line.text[a:b])} if tables else set()  # fmt: skip
    whole = straightened(layout) if tables else None
    items = _read_line_items(straightened(inside_tables(layout, tables) if tables else layout), one_table, targets,
                             printed, whole)  # fmt: skip
    for li in items if skews else []:
        li.boxes = [_on_tilted_page(b, skews[b.page]) if b.page in skews else b for b in li.boxes]
    if items and credit_printed_positive(layout):
        for li in items:
            li.amount = -li.amount if li.amount else li.amount
            li.unit_price = -li.unit_price if li.unit_price else li.unit_price
    return items


def _read_line_items(layout: DocLayout, one_table: dict[int, list[DocLayout]] | None = None,
                     targets: list[float] | None = None, printed: set[float] | None = None,
                     whole: DocLayout | None = None) -> list[LineReading]:  # fmt: skip
    """``one_table``: for each page where the table finder found tables, the words of each table on its own; on a
    page whose column headings can't be read, the table whose rows read best by their amounts is read
    (``_headerless_rows``)."""
    items: list[LineReading] = []
    money: list[bool] = []  # each line's amount is printed as money ("1,450.00", "$12"), not "5%" or "12"
    guessed: list[LineReading] = []  # rows read by their amounts, on pages whose headings can't be read
    printed_sum = None
    for page in layout.pages:
        found = heading_row(page)
        header = (found[0].box.y1, found[2]) if found else None
        summary = _charges_summary(page) if header is None else []
        if summary:
            items += summary
            money += [True] * len(summary)
            continue
        if header is None:
            tables = [t.pages[page.number - 1] for t in (one_table or {}).get(page.number, [])]
            page_whole = whole.pages[page.number - 1] if whole is not None else None
            rows_read, at_sum = max((_headerless_rows(t, page_whole) for t in tables), key=lambda r: len(r[0]),
                                    default=([], None))  # fmt: skip
            items += rows_read
            money += [True] * len(rows_read)
            guessed += rows_read
            printed_sum = at_sum if rows_read else printed_sum
            continue
        y_top, cols = header
        tax_column = tax_code_column(found[1], cols) if found else None
        rows: dict[float, list[Line]] = {}
        stop_y = stop_cy = 1.0
        for line in page.lines:
            if line.box.y0 <= y_top:
                continue
            p = plain(line.text)
            if any(rx.match(p) for f in _TOTAL_LABELS for rx, _ in _LABELS[f]):
                stop_y, stop_cy = min(stop_y, line.box.y0), min(stop_cy, line.box.cy)
        for line in page.lines:
            # The table ends at its subtotal or total: its amount, printed a hair higher than its label, too.
            if not (y_top < line.box.y0 < stop_y) or line.box.cy > stop_cy - 0.006:
                continue
            key = next((k for k in rows if abs(k - line.box.cy) < 0.006), line.box.cy)
            rows.setdefault(key, []).append(line)
        amount_x = cols["amount"].cx
        last: tuple[LineReading, float] | None = None  # the line item last read on this page, and its bottom
        for _, segs in sorted(rows.items()):
            if any(_CARRIED.match(plain(s.text).strip()) for s in segs):
                last = None
                continue
            nums = [(s, _amounts_in(s.text)) for s in segs]
            amount = None
            amt_seg = None
            amt_token = ""
            near = 1.0  # of the cells under the amount's heading, the nearest to it ("10%" in a VAT column beside)
            for s, found in nums:
                off = min(abs(s.box.x1 - cols["amount"].x1), abs(s.box.cx - amount_x))
                if found and off < 0.08 and off <= near:
                    pick = _under(s, found, cols["amount"])
                    amount, amt_seg, near = pick[0], s, off
                    amt_token = s.text[pick[1] : pick[2]]
            if amount is None:
                if last is not None and _wrapped(segs, cols, last[1]):  # the rest of the description above
                    li = last[0]
                    li.description = " ".join([li.description, *(s.text for s in segs)]).strip()
                    li.boxes = [union_all([*li.boxes, *(s.box for s in segs)]) or li.boxes[0]] if li.boxes else []
                    last = (li, max(s.box.y1 for s in segs))
                else:
                    last = None
                continue
            # The tax-code column's cell ("G R") is the line's tax code, not part of its description (D-64).
            flags = [s for s in segs if s is not amt_seg and _under_tax_column(s, tax_column)
                     and not ("description" in cols and abs(s.box.x0 - cols["description"].x0) < 0.05)]  # fmt: skip
            # A number cell (qty, price, amount, row number) is not part of the description; a description
            # with a number in it ("Meraki MR46", "Cat6 cables", "1 year licence") is.
            desc = " ".join(s.text for s in segs if s not in flags and (s is not amt_seg and not _NUMBER_CELL.match(s.text)
                            or ("description" in cols and abs(s.box.x0 - cols["description"].x0) < 0.05)))  # fmt: skip
            qty = price = None
            if "quantity" in cols:
                q = next((f for s, f in nums if f and abs(s.box.cx - cols["quantity"].cx) < 0.05), None)
                qty = q[0][0] if q else None
            if "unit_price" in cols:
                u = next(
                    (f for s, f in nums if f and s is not amt_seg and abs(s.box.cx - cols["unit_price"].cx) < 0.07),
                    None,
                )
                price = u[-1][0] if u else None
            # The quantity or the price not read under its heading (OCR lost a heading, or a digit): the two numbers
            # left of the amount whose product it is, when the table has a quantity or a price column.
            if {"quantity", "unit_price"} & cols.keys() and not (
                qty is not None and price is not None and abs(qty * price - amount) <= 0.011
            ):
                fit = _qty_times_price(segs, nums, amt_seg, amount)
                if fit:
                    qty, price = fit
            row_box = union_all([s.box for s in segs])
            score = 0.8 if (qty is None or price is None or abs(qty * price - amount) <= 0.011) else 0.5
            items.append(LineReading(desc.strip(), qty, price, amount, [row_box] if row_box else [], score,
                                     tax_flags=" ".join(" ".join(s.text.split()) for s in flags)))  # fmt: skip
            money.append(_printed_as_money(amt_token))
            last = (items[-1], max(seg.box.y1 for seg in segs))
    if guessed:  # kept only when they add up
        ids = {id(li) for li in guessed}
        kept = {id(li) for li in _proven_rows(guessed, [li for li in items if id(li) not in ids], printed_sum,
                                               targets, printed)}  # fmt: skip
        keep = [id(li) in kept or id(li) not in ids for li in items]
        items = [li for li, k in zip(items, keep, strict=True) if k]
        money = [m for m, k in zip(money, keep, strict=True) if k]
    # Where the lines' amounts are printed as money, a "line" whose amount isn't is a sentence under the table ("All
    # services subject to GST 5%", "4 of 12 hours cancelled") that reaches the amount column.
    if sum(money) >= 2 and not all(money):
        items = [li for li, m in zip(items, money, strict=True) if m]
    # Not one amount printed as money ("774320", "-9692": a customer number, a phone number read from a page upside
    # down): no line table, unless the rows add up to what the lines should.
    if len(items) >= 2 and not any(money):
        total = round(sum(li.amount or 0.0 for li in items), 2)
        if not any(abs(total - t) <= 0.02 for t in targets or []):
            return []
    # A last row with no words of its own whose amount is the sum of the rows above: the table's total row, its label
    # read as no label (OCR runs "Total before tax" together).
    if len(items) >= 3 and not items[-1].description.strip() and items[-1].amount is not None:
        above = [li.amount for li in items[:-1] if li.amount is not None]
        if abs(round(sum(above), 2) - items[-1].amount) <= 0.011:
            items.pop()
    # A row with no words that prints the sum of the lines above it among its figures is no line: the table's summary
    # ("10% 44 364,64 4 436,46 48 801,10": the rate, the net sum, the tax, the gross), or a balance brought forward
    # whose label OCR lost.
    # The tax's and total's rows after such a sum ("GST (5%)") are no lines either.
    kept: list[LineReading] = []
    summed = False
    for li in items:
        above = round(sum(k.amount or 0.0 for k in kept), 2)
        # (under one line, its own amount printed again is not enough: a figure beside it must print the sum)
        figures = (li.quantity, li.unit_price, *([li.amount] if len(kept) >= 2 else []))
        if kept and not li.description.strip() and any(v is not None and abs(v - above) <= 0.011 for v in figures):
            summed = True
        elif not (summed and li.quantity is None and _TAX_ROW.search(plain(li.description))):
            kept.append(li)
    items = kept
    # A row naming a total in a wording the totals' labels don't know ("Montant HT", "Summe netto") that prints the sum
    # of the lines above it is the subtotal: the lines end above it. A row with no words and nothing to pay is no line.
    for k, li in enumerate(items):
        lines = [x for x in items[:k] if x.amount]
        if (len(lines) >= 2 and li.quantity is None and li.amount is not None and _TAX_ROW.search(plain(li.description))
                and abs(round(sum(x.amount for x in lines), 2) - li.amount) <= 0.011):  # fmt: skip
            items = items[:k]
            break
    items = [li for li in items if li.amount or li.description.strip() or li.quantity is not None]
    # A "#" column read into the description: "1 Observability…", "2 Premium…". Only when every row starts
    # with its own number, so "6 Dell monitors" on its own is left alone.
    numbered = [re.match(rf"^{i}\s+(?=\S)", li.description) for i, li in enumerate(items, 1)]
    if len(items) >= 2 and all(numbered):
        for li, m in zip(items, numbered, strict=True):
            li.description = li.description[m.end() :]
    return items


_SUMMARY_SUBTOTAL = re.compile(r"^(?:sub\s*-?\s*total|sous\s*-?\s*total)$")

_SUMMARY_TAX = re.compile(r"^(?:gst|hst|pst|qst|tps|tvh|tvp|tvq|rst)\b")

_SUMMARY_TOTAL = re.compile(r"^total$")


def _charges_summary(page: PageLayout) -> list[LineReading]:
    """The lines of a summary of charges (Canada Post: "Parcels | Subtotal | GST | HST | PST/QST | Total", one row a
    service): the columns are headed by the subtotal, the taxes and the total, the description column has no heading.
    Each row's amount is the one under "Subtotal" (before tax). Kept only when they add up to the amount printed
    under "Subtotal" on the row with no words that ends them (the invoice's subtotal)."""
    for line in page.lines:
        if not _SUMMARY_SUBTOTAL.match(plain(line.text).strip()):
            continue
        row = [o for o in page.lines if abs(o.box.cy - line.box.cy) < 0.006 and o.box.x0 > line.box.x1]
        if not any(_SUMMARY_TAX.match(plain(o.text).strip()) for o in row) or not any(
            _SUMMARY_TOTAL.match(plain(o.text).strip()) for o in row
        ):
            continue
        col = line.box
        rows: dict[float, list[Line]] = {}
        for other in page.lines:
            if other.box.y0 > line.box.y1:
                key = next((k for k in rows if abs(k - other.box.cy) < 0.006), other.box.cy)
                rows.setdefault(key, []).append(other)
        items: list[LineReading] = []
        for _, segs in sorted(rows.items()):
            segs.sort(key=lambda o: o.box.x0)
            under = [(o, f) for o in segs if (f := _amounts_in(o.text)) and abs(o.box.x1 - col.x1) < 0.03]
            if not under:
                break
            amount = under[0][1][-1][0]
            words = " ".join(o.text for o in segs if o.box.x1 < col.x0 and not _NUMBER_CELL.match(o.text)).strip()
            if not words or any(rx.match(plain(words)) for f in _TOTAL_LABELS for rx, _ in _LABELS[f]):
                # the total row (with no words, or "Total"): the lines end, proven when they add up to it
                total = round(sum(li.amount or 0.0 for li in items), 2)
                return items if items and abs(total - amount) <= 0.011 else []
            box = union_all([o.box for o in segs])
            items.append(LineReading(words, None, None, amount, [box] if box else [], 0.8))
        return []
    return []


def _under(seg: Line, found: list[tuple[float, int, int]], head: Box) -> tuple[float, int, int]:
    """Of the amounts in one cell, the one under the heading: the last, unless OCR ran two columns' amounts together
    ("100.00 20.00": the amount and the VAT rate beside it) and another sits nearer the heading's middle."""
    if (len(found) < 2 or seg.box.x1 <= head.x1 + 0.01
            or not all(_printed_as_money(seg.text[a:b]) for _v, a, b in found)):  # fmt: skip
        return found[-1]  # ("2 500.00" is one amount, not 2 and 500.00)

    def middle(f: tuple[float, int, int]) -> float:
        box = union_all([w.box for w in _span_words(seg, f[1], f[2])])
        return box.cx if box is not None else seg.box.cx

    return min(found, key=lambda f: abs(middle(f) - head.cx))


WRAP_GAP = 1.2  # a description's next line starts at most this many text heights under the line above it


def _wrapped(segs: list[Line], cols: dict[str, Box], bottom: float) -> bool:
    """A row with no amount right under a line item (``bottom``: the item's last row) is the rest of its description
    wrapped onto another line: its words all sit in the description's column (from its heading, else the page's
    left, to the next heading on the right), none is an amount printed as money or a total's label, and it starts
    within ``WRAP_GAP`` text heights."""
    if not segs:
        return False
    head = cols.get("description")
    left = head.x0 - 0.03 if head is not None else 0.0
    right = min((b.x0 for b in cols.values() if head is None or b.x0 > head.x1), default=1.0)
    height = max(statistics.median(s.box.height for s in segs), 0.004)
    return (
        min(s.box.y0 for s in segs) - bottom <= WRAP_GAP * height
        and all(s.box.x0 >= left and s.box.x1 <= right + 0.005 for s in segs)
        and not any(_printed_as_money(s.text[a:b]) for s in segs for _v, a, b in _amounts_in(s.text))
        and not any(rx.match(plain(s.text).strip()) for s in segs for f in _TOTAL_LABELS for rx, _ in _LABELS[f])
    )


_RUNNING = re.compile(
    r"brought\s*forward|carried\s*forward|balance\s*forward|report|ubertrag|page\s*(?:sub\s*-?\s*)?total"
)

LISTED_COLUMN = 0.06  # an item's amount ends this close to the subtotal's amount (fraction of the page width)

LISTED_MAX = 150  # amounts looked at above the subtotal

_LISTED_FLAG = re.compile(r"^[\s*-]*(?:[a-z]{1,2}\b)?[\s*]*$", re.I)  # a receipt's tax flag after the amount: "J", "*"


def listed_items(layout: DocLayout, targets: list[float]) -> list[LineReading]:
    """The lines of a bill that prints no column headings (a till receipt, a short bill): the amounts in the column
    right above its subtotal (or total), each with the words on its row (product codes left out), read upward from
    the subtotal until they add up to one of ``targets`` (the subtotal, the total less the taxes). None when they
    never add up exactly: amounts in a column are a guess, kept only when the bill proves them."""
    if not targets:
        return []
    for page in straightened(layout).pages:
        ends = [ln for ln in page.lines
                if any(rx.match(plain(ln.text).strip()) for f in ("subtotal", "grand_total") for rx, _ in _LABELS[f])]  # fmt: skip
        if not ends:
            continue
        end = min(ends, key=lambda ln: ln.box.y0)
        height = max(end.box.height, 0.004)
        found: list[tuple[float, float, float, Line, int]] = []  # (middle, right end, amount, line, where it starts)
        for line in page.lines:
            amounts = [(v, a, b) for v, a, b in _amounts_in(line.text) if _printed_as_money(line.text[a:b])]
            if not amounts or not _LISTED_FLAG.match(line.text[amounts[-1][2] :]):
                continue  # no amount at the line's end
            value, a, b = amounts[-1]
            box = union_all([w.box for w in _span_words(line, a, b)]) or line.box
            found.append((box.cy, box.x1, value, line, a))
        # The subtotal's own amount: on the label's row, right of it.
        mine = [f for f in found if abs(f[0] - end.box.cy) < 0.7 * height and f[1] > end.box.x0]
        right = min(mine, key=lambda f: f[1] - end.box.x1)[1] if mine else None
        above = sorted((f for f in found if f[0] < end.box.cy - 0.7 * height), key=lambda f: -f[0])[:LISTED_MAX]
        if right is None:
            if not above:
                continue
            right = statistics.median(f[1] for f in above)
        column = [f for f in above if abs(f[1] - right) <= LISTED_COLUMN]
        total = 0.0
        for k, (_cy, _x1, value, _line, _a) in enumerate(column, 1):
            total += value
            if any(abs(round(total, 2) - t) <= 0.011 for t in targets):
                lines = _listed_lines(page, list(reversed(column[:k])), {id(f[3]) for f in found}, height)
                # Amounts that name a tax, a total or a balance carried from another page ("Montant HT", "TVA",
                # "Remise totale", "Balance brought forward") are sums, not items; one amount with no words is the
                # subtotal printed twice.
                if any(
                    _TAX_ROW.search(plain(li.description)) or _RUNNING.search(plain(li.description)) for li in lines
                ) or (len(lines) == 1 and not lines[0].description):
                    break
                return lines
    return []


def _listed_lines(page: PageLayout, rows: list[tuple[float, float, float, Line, int]], priced: set[int],
                  height: float) -> list[LineReading]:  # fmt: skip
    """The listed items: each amount, and the words left of it on its row that aren't a number (a product code); a
    line of words beside two amounts' rows goes with the nearer one."""
    words: dict[int, list[Line]] = {}
    for ln in page.lines:
        if id(ln) in priced:
            continue
        near = [(abs(ln.box.cy - cy), k) for k, (cy, _x1, _v, line, _a) in enumerate(rows)
                if abs(ln.box.cy - cy) < 0.7 * height and ln.box.x1 <= line.box.x0 + 0.01]  # fmt: skip
        if near:
            words.setdefault(min(near)[1], []).append(ln)
    out = []
    for k, (_cy, _x1, amount, line, at) in enumerate(rows):
        row = sorted(words.get(k, []), key=lambda ln: ln.box.x0)
        parts = [ln.text for ln in row] + [line.text[:at]]
        text = " ".join(w for part in parts for w in part.split() if not _FIGURE.fullmatch(w))
        box = union_all([ln.box for ln in [*row, line]])
        out.append(LineReading(text.strip(), None, None, amount, [box] if box else [], 0.6))
    return out
