# ruff: noqa: E501  (label and pattern tables read best one per line)
"""Line items: the table of what was bought."""

from __future__ import annotations

import re
import statistics
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

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
    _PRETAX_HEAD,
    _TAXED_HEAD,
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

_NAMED_ITEM = re.compile(r"\s+[a-z]{3,}")  # a word after a total's label: an item's name may start with one

_NUMBER_CELL = re.compile(r"^[\s$€£¢()+\-–.,'%x×@#]*\d[\d\s$€£¢()+\-–.,'%x×@]*(?:[a-z]{1,3}\.?)?\s*$", re.I)

# A row of a long table that sums its lines: carried from page to page ("Balance brought forward / Montant reporté",
# "Übertrag"), or the page's own subtotal ("Page subtotal", "Sous-total de la page", "Total de la page").
_CARRIED = re.compile(r"^(a\s*reporter|report\b|reporte|carried\s*forward|brought\s*forward|"
                      r"balance\s*(?:brought\s*|carried\s*)?forward|montant\s*report|u(?:e)?bertrag|"
                      r"continued|suite|sub\s*-?\s*total|sous\s*-?\s*total|page\s*(?:sub\s*-?\s*)?total|"
                      r"total\s*(?:de\s*la\s*)?page)")  # fmt: skip

# A row of the supplier's account printed in the line table, as on a statement: what was owed before this invoice
# and what was paid since. No line of the invoice (B-2270); "Late payment charge" is one.
_ACCOUNT_ROW = re.compile(
    r"^(?:(?:previous|prior|opening|last)\s*(?:account\s*|statement\s*|invoice\s*)?balance\b"
    r"|balance\s*(?:from|of)\s*(?:your\s*)?(?:previous|prior|last)\b|solde\s*(?:precedent|anterieur)"
    r"|payments?\s*(?:received|applied)\b|payments?\s*[-–—:,.]*\s*thank\s*you|thank\s*you\s*for\s*your\s*payment"
    r"|paiements?\s*recus?\b|paiements?\s*[-–—:,.]*\s*merci)"
)
# "Balance forward" above the table's first line is the account's too; lower down it carries the lines above it.
_FORWARD = re.compile(r"^(?:balance\s*(?:brought\s*|carried\s*)?forward|solde\s*reporte)")

_CREDIT_MARK = re.compile(r"\d[.,]\d{2}cr", re.I)  # "342.18CR": a credit's mark run into its amount


def _set_aside(left_out: list[LineReading] | None, row: LineReading) -> None:
    """Keep a row of the supplier's account that is no line, each once, for the finding that names them."""
    same = (plain(row.description).strip(), row.amount)
    if left_out is not None and all((plain(li.description).strip(), li.amount) != same for li in left_out):
        left_out.append(row)


def without_account_rows(items: list[LineReading], left_out: list[LineReading] | None = None) -> list[LineReading]:
    """``items`` without the rows of the supplier's account ("Previous balance", "Payment received - thank you";
    "Balance forward" before the first line): they are no lines of the invoice. ``left_out`` gathers them."""
    kept: list[LineReading] = []
    for li in items:
        words = plain(li.description).strip()
        if _ACCOUNT_ROW.match(words) or (not kept and _FORWARD.match(words)):
            _set_aside(left_out, li)
        else:
            kept.append(li)
    return kept if len(kept) < len(items) else items


def _printed_as_money(token: str) -> bool:
    """A line's amount printed as money, as OCR reads it too: "1530:00" (the point read as a colon) and "342.18CR"
    (a credit's mark run into it) are money; "5%" and "12" are not."""
    from ...core.parsing import _ocr_amount_text

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

_ROW_NUMBER_ALONE = re.compile(r"^(?:no\.?|#|pos\.?|n\s?°)$")  # the row number's heading in a segment of its own

_ROW_NUMBER = re.compile(r"^\d{1,3}[.)]?$")  # a row's number under it: "1", "12.", "3)"

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


def _tax_code_cells(segs: list[Line], amt_seg: Line | None, cols: dict[str, Box], column: Box | None) -> list[Line]:
    """The tax-code column's cells of a row ("G R"): the line's tax code, not part of its description (D-64)."""
    return [s for s in segs if s is not amt_seg and _under_tax_column(s, column)
            and not ("description" in cols and abs(s.box.x0 - cols["description"].x0) < 0.05)]  # fmt: skip


def _under_tax_column(seg: Line, column: Box | None) -> bool:
    return column is not None and abs(seg.box.cx - column.cx) < max(0.035, column.width) and tax_flag_cell(seg.text)


def row_number_column(row: list[Line], cols: dict[str, Box]) -> tuple[float, float] | None:
    """Where the line table prints its rows' numbers (x from, to), when its heading row (``heading_row``'s lines)
    names their column left of the named ones: a heading of its own ("#", "No.", "Pos."), or the word before the
    description's ("# Description"). The column ends where the next heading starts. None when the rows have no
    number column."""
    left = min(box.x0 for box in cols.values())
    for line in row:
        p = plain(line.text).strip()
        if _ROW_NUMBER_ALONE.match(p) and line.box.x1 <= left:
            head, upto = line.box, left
        elif _ROW_NUMBER_HEAD.match(p) and len(line.words) > 1 and line.box.x0 <= left:
            head, upto = line.words[0].box, line.words[1].box.x0
        else:
            continue
        return head.x0 - 0.015, min(head.x1 + 0.015, upto)
    return None


def _row_number(text: str, box: Box, column: tuple[float, float] | None) -> bool:
    return column is not None and column[0] <= box.cx <= column[1] and bool(_ROW_NUMBER.match(text.strip()))


def _without_row_number(seg: Line, column: tuple[float, float] | None) -> str:
    """A cell's text without the row's number set close before it ("1 Copy paper" read as one cell)."""
    first = seg.words[0] if len(seg.words) > 1 else None
    if first is not None and _row_number(first.text, first.box, column) and seg.text.startswith(first.text):
        return seg.text[len(first.text) :].lstrip()
    return seg.text


def _row_description(segs: list[Line], flags: list[Line], amt_seg: Line | None, cols: dict[str, Box],
                     number_column: tuple[float, float] | None) -> str:  # fmt: skip
    """A row's description: its cells but the tax code's (``flags``). A number cell (qty, price, amount, row number)
    is not part of it; a description with a number in it ("Meraki MR46", "Cat6 cables", "1 year licence") is, and
    so is a number under the description's heading. The row's number under the "#" heading never is: in a cell of
    its own beside the description's, or set close before it (B-2095)."""
    return " ".join(_without_row_number(s, number_column) for s in segs
                    if s not in flags and not _row_number(s.text, s.box, number_column)
                    and (s is not amt_seg and not _NUMBER_CELL.match(s.text)
                         or ("description" in cols and abs(s.box.x0 - cols["description"].x0) < 0.05)))  # fmt: skip


def _heading_name(text: str) -> str | None:
    """The column a heading names, by the longest heading it starts with ("Unit Cost" is the unit price, not a
    quantity in "Unit"; "Prix total" the amount, not a price); a heading printed in two languages ("Product /
    Service / Désignation") by its parts, when they name one column; a row number's heading before it is no part of
    it ("No. Description", "# Item"). None when it names none, or two."""
    text = _ROW_NUMBER_HEAD.sub("", text)
    longest = {name: m.end() for name, rx in _HEAD.items() if (m := rx.match(text))}
    if _PRETAX_HEAD.match(text) and "unit_price" not in longest:  # "Net", "Excl. tax", "Montant HT": the amount
        longest = {"amount": len(text)}
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


# The heading of a column that prints each line's tax as an amount: one tax ("GST", "PST", "TVQ"), or the tax
# whichever it is ("Tax", "Tax amt", "GST/HST", "TPS/TVQ", "VAT").
_TAX_AMOUNT_HEAD = re.compile(
    r"^(?:(?P<GST>gst|tps)|(?P<HST>hst|tvh)|(?P<PST>pst|rst|tvp)|(?P<QST>qst|tvq)|tax(?:es?)?|vat|tva|"
    r"(?:gst|tps)\s*/\s*(?:hst|tvh|pst|qst|tvq))(?:\s*(?:amt|amount|montant)\.?)?\s*:?$"
)

# The heading of a column that prints what is taken off each line ("Discount", "Disc. %", "Rabais", "Remise").
_DISCOUNT_HEAD = re.compile(r"^(?:disc(?:ount|\.)?|rabais|remise|escompte)(?:\s*(?:%|\$|amt\.?|amount))?\s*:?$")

_NO_TAX_CELL = re.compile(
    r"^(?:[-–—]+|n\s*/\s*a|nil|none|aucune?)$"
)  # a tax cell that prints "no tax" without a figure


def _heading_says(line: Line, box: Box) -> str:
    """What a heading that names the amount says of the tax: "before" ("Net", "Excl. tax", "Montant HT",
    "Subtotal"), "with" ("Total incl. tax", "Montant TTC"), or "" ("Amount", "Total")."""
    words = plain(" ".join(w.text for w in line.words if box.x0 - 0.002 <= w.box.cx <= box.x1 + 0.002)).strip()
    for said in (plain(line.text).strip(), words):  # (the segment whole: "Total incl. tax" is no "Total" and a tax)
        if _PRETAX_HEAD.match(said) or _TAXED_HEAD.match(said):
            return "before" if _PRETAX_HEAD.match(said) else "with"
    return ""


def _amount_heading(row: list[Line], named: list[tuple[Line, Box]]) -> Box:
    """Which heading names the lines' amount, of the headings of the row that could (``named``: its line, its box,
    in the row's order). The whole precedence, by the headings alone:

    1. After a discount column, only the headings right of it: the amount after the discount is the line's ("Qty |
       Price | Subtotal | Discount | Total" reads Total; "Price | Discount | Net" reads Net).
    2. Never a heading that says the tax is in it ("Total incl. tax", "Montant TTC") while another could be it.
    3. A heading that says before tax ("Net", "Excl. tax", "Before tax", "Montant HT", "Subtotal") over a plain one
       ("Amount", "Total"), but only in a table that also has a tax column (its amounts, its rate or its code) or a
       heading that says the tax is in it ("Net | Tax | Total", "Net | Tax % | Total", "Excl. tax | Incl. tax"); in
       any other table the plain one over it ("Gross | Tare | Net |
       Rate | Amount": Net is a weight; "Subtotal | Total": quantity x list price, then the line's amount).
    4. Of equals, the first in the row.

    The numbers then confirm or change the COLUMN for the whole table, never one row's figure (``_Columns``): a column
    of money that makes the amount with each row's tax, or the column that adds up to the invoice's subtotal."""
    says = [_heading_says(line, box) for line, box in named]
    heads = [plain(other.text).strip() for other in row]
    taxed = ("with" in says or any(_TAX_HEAD.match(p) for p in heads)
             or any(_TAXED_HEAD.match(p) and not p.startswith("gross") for p in heads))  # fmt: skip
    after = max((o.box.x1 for o, p in zip(row, heads, strict=True) if _DISCOUNT_HEAD.match(p)), default=None)
    pool = [k for k, (_, box) in enumerate(named) if after is None or box.x0 >= after - 0.002] or range(len(named))
    rank = {"before": 3 if taxed else 1, "": 2, "with": 0}
    return named[max(pool, key=lambda k: (rank[says[k]], -k))][1]


def heading_row(page: PageLayout) -> tuple[Line, list[Line], dict[str, Box]] | None:
    """The line table's column headings on the page: (the first line of their row, the row's lines, {column: heading
    box}). A row of headings names the amount and the description or the quantity; of several, the one naming the
    most columns (an address's "Unit 9" set level with "Amount Due" at the top of a bill names two). Of several
    headings that could name the amount, the one ``_amount_heading`` ranks first. None when there is none."""
    named = {id(line) for line in page.lines if _heading_cells(line, {})}  # the lines that hold a heading at all
    best = None
    for line in page.lines:
        row = [other for other in page.lines if abs(other.box.cy - line.box.cy) < 0.006]
        cols: dict[str, Box] = {}
        amounts: list[tuple[Line, Box]] = []  # every heading of the row that could name the amount
        for other in row:
            if id(other) in named:
                for name, box in _heading_cells(other, {k: v for k, v in cols.items() if k != "amount"}):
                    cols[name] = box
                    amounts += [(other, box)] if name == "amount" else []
        if "amount" in cols and len(cols) >= 2 and ("description" in cols or "quantity" in cols):
            if best is None or len(cols) > len(best[2]):
                best = (line, row, {**cols, "amount": _amount_heading(row, amounts)})
    return best


COLUMN_ALIGNED = 0.012  # cells of one column: their left edges, middles or right edges this close (of the page width)

HEADING_REACH = 0.1  # a heading stands over a column of numbers at most this far from it, side to side


@dataclass
class _Cell:
    """A number printed in a row of the line table: its row, the segment it is in, its value, where it is printed,
    and whether it is printed as money ("1,250.00", not "12" or "5%")."""

    row: int
    seg: Line
    value: float
    box: Box
    money: bool


def _row_cells(row: int, segs: list[Line], cols: dict[str, Box]) -> list[_Cell]:
    """The numbers of one row, each a cell where the number itself is printed; not those among words ("Storage unit
    95", "case of 10"), nor in the description's own cell (an item's code). Amounts printed as money that were read
    as one segment are each a cell ("9,98 114,98": the tax set close to the total)."""
    cells = []
    for seg in segs:
        found = _amounts_in(seg.text)
        if (not found or not _NUMBER_CELL.match(seg.text)
                or ("description" in cols and abs(seg.box.x0 - cols["description"].x0) < 0.05)):  # fmt: skip
            continue
        money = [_printed_as_money(seg.text[a:b]) for _v, a, b in found]
        if len(found) >= 2 and all(money):
            for value, a, b in found:
                cells.append(
                    _Cell(row, seg, value, union_all([w.box for w in _span_words(seg, a, b)]) or seg.box, True)
                )
        else:  # (where the number itself is printed: "387.24 H" is an amount with the tax code set close after it)
            value, a, b = found[-1]
            cells.append(
                _Cell(row, seg, value, union_all([w.box for w in _span_words(seg, a, b)]) or seg.box, money[-1])
            )
    return cells


def _span(cells: list[_Cell]) -> Box:
    first = cells[0].box
    return Box(first.page, statistics.median(c.box.x0 for c in cells), min(c.box.y0 for c in cells),
               statistics.median(c.box.x1 for c in cells), max(c.box.y1 for c in cells))  # fmt: skip


def _number_columns(cells: list[_Cell]) -> list[list[_Cell]]:
    """The table's columns of numbers, left to right: the cells of different rows printed in line, whichever way
    the column is aligned (left edges, middles or right edges within ``COLUMN_ALIGNED``)."""
    columns: list[list[_Cell]] = []
    for cell in sorted(cells, key=lambda c: (c.box.x1, c.row)):
        best = None
        for column in columns:
            if any(c.row == cell.row for c in column):
                continue  # (two numbers of one row are two columns)
            span = _span(column)
            off = min(abs(span.x0 - cell.box.x0), abs(span.cx - cell.box.cx), abs(span.x1 - cell.box.x1))
            if off <= COLUMN_ALIGNED and (best is None or off < best[0]):
                best = (off, column)
        if best is None:
            columns.append([cell])
        else:
            best[1].append(cell)
    return sorted(columns, key=lambda column: _span(column).cx)


def _table_headings(row: list[Line], cols: dict[str, Box]) -> list[tuple[str, Box]]:
    """Every heading of the heading row but the description's, left to right, with what it heads: a named column
    ("quantity", "unit_price", "amount": the one the lines' amount is read under), "amount?" (another heading that
    could name the amount), "tax:GST" (a tax printed line by line; "tax:" when it names no tax, or two), "discount",
    "#" (the rows' numbers) or "" (anything else: "Days", "Code", "Base")."""
    out: list[tuple[str, Box]] = []
    for line in row:
        cells = _heading_cells(line, {})
        p = plain(line.text).strip()
        tax = _TAX_AMOUNT_HEAD.match(p)
        if cells:
            for name, box in cells:
                mine = name in cols and abs(box.cx - cols[name].cx) < 0.002
                out.append((name if mine else "amount?" if name == "amount" else "", box))
        elif tax:
            out.append(("tax:" + next((name for name, said in tax.groupdict().items() if said), ""), line.box))
        else:
            kind = "discount" if _DISCOUNT_HEAD.match(p) else "#" if _ROW_NUMBER_ALONE.match(p) else ""
            out.append((kind, line.box))
    return sorted(((kind, box) for kind, box in out if kind != "description"), key=lambda h: h[1].cx)


def _heads_words(heads: list[tuple[str, Box]], rows: list[list[Line]]) -> set[int]:
    """The headings (their place in ``heads``) that stand over a column of words, not of numbers: a tax code's ("G",
    "GP", "H"), a unit's ("ea"). Of the headings that name no column of numbers for sure ("Tax", and those the reader
    does not know), the ones with a word of letters printed under them on more than half of the rows that hold a
    number. Printed from its column's left edge, such a heading sits nearer the amounts before it than the amount's
    own heading does, and would be taken for theirs."""
    data = [segs for segs in rows if any(_amounts_in(s.text) for s in segs)]
    out = set()
    for k, (kind, box) in enumerate(heads):
        if kind and not kind.startswith("tax:"):
            continue
        worded = sum(
            any(min(w.box.x1, box.x1) - max(w.box.x0, box.x0) > 0.002 and not any(ch.isdigit() for ch in w.text)
                and any(ch.isalpha() or ch == "*" for ch in w.text) for s in segs for w in s.words)
            for segs in data
        )  # fmt: skip
        if worded > len(data) / 2:
            out.add(k)
    return out


def _paired(heads: int, columns: int, worth: Callable[[int, int], float | None]) -> dict[int, int]:
    """{heading: column} of the pairing, both taken in order and each used once at most, that makes the most pairs
    and, of those, is worth the most (``worth``: a pair's worth; None for two that are no pair)."""
    best = [[(0, 0.0)] * (columns + 1) for _ in range(heads + 1)]  # (pairs, their worth) of the first i and j
    for i in range(1, heads + 1):
        for j in range(1, columns + 1):
            w = worth(i - 1, j - 1)
            paired = (best[i - 1][j - 1][0] + 1, best[i - 1][j - 1][1] + w) if w is not None else (-1, 0.0)
            best[i][j] = max(best[i - 1][j], best[i][j - 1], paired)
    pairs: dict[int, int] = {}
    i, j = heads, columns
    while i and j:
        if best[i][j] == best[i - 1][j]:
            i -= 1
        elif best[i][j] == best[i][j - 1]:
            j -= 1
        else:
            pairs[i - 1] = j - 1
            i, j = i - 1, j - 1
    return pairs


def _columns_under(heads: list[Box], spans: list[Box]) -> dict[int, int]:
    """{heading: the column of numbers it stands over}, both left to right. A heading printed over its numbers
    (their spans across the page overlap) has that column. The others are paired with the columns left between
    those, in the order both come across the page and within ``HEADING_REACH``: as many pairs as can be made, the
    closest overall. Headings printed from their columns' left edges over numbers that end at their right edges
    ("Tax" and "Total" as a spreadsheet prints them) so keep their own columns, though the numbers of the column
    before end nearer to each: a heading never has the numbers printed wholly to its left, which are the column
    before's (a heading sits over its numbers, or starts before them). A heading with no column in reach has none (a
    column of words, or of blanks)."""

    def over(i: int, j: int) -> float | None:
        width = min(heads[i].x1, spans[j].x1) - max(heads[i].x0, spans[j].x0)
        return width if width > 0.002 else None

    def off(h: Box, c: Box) -> float:
        gap = max(0.0, c.x0 - h.x1, h.x0 - c.x1)
        return gap + 0.1 * min(abs(h.x0 - c.x0), abs(h.cx - c.cx), abs(h.x1 - c.x1))

    pairs = _paired(len(heads), len(spans), over)
    fixed = [(-1, -1), *sorted(pairs.items()), (len(heads), len(spans))]
    for (h0, c0), (h1, c1) in zip(fixed, fixed[1:], strict=False):
        hs, cs = range(h0 + 1, h1), range(c0 + 1, c1)

        def near(i: int, j: int, hs: range = hs, cs: range = cs) -> float | None:
            away = off(heads[hs[i]], spans[cs[j]])
            behind = spans[cs[j]].x1 < heads[hs[i]].x0 - 0.002  # the numbers end before the heading starts
            return -away if away <= HEADING_REACH and not behind else None

        pairs.update({hs[i]: cs[j] for i, j in _paired(len(hs), len(cs), near).items()})
    return pairs


class _Columns:
    """The line table's columns of numbers on one page, each under its heading: where every row's amount and tax
    are read. The amount is ONE column for the whole table: the one under the heading ``_amount_heading`` chose,
    unless the numbers say another column is the amount before tax (``before_tax``), or the amount that adds up to
    the invoice's subtotal (``adding_up``). No row's own arithmetic picks a figure for that row alone: a row number,
    an item code, a count of days or a number in the description is never an amount (B-2197)."""

    def __init__(self, head_row: list[Line], cols: dict[str, Box], rows: list[list[Line]]) -> None:
        heads = _table_headings(head_row, cols)
        words = _heads_words(heads, rows)
        heads = [head for k, head in enumerate(heads) if k not in words]
        self.columns = _number_columns([c for k, segs in enumerate(rows) for c in _row_cells(k, segs, cols)])
        under = _columns_under([box for _, box in heads], [_span(column) for column in self.columns])
        self.kinds = {j: heads[i][0] for i, j in under.items()}  # {column: what its heading heads}
        self.amount = next((j for j, kind in self.kinds.items() if kind == "amount"), None)
        # A tax heading's column counts when a row prints an amount other than zero in it (not codes, not blanks,
        # not rates: "13%").
        self.taxes = [(kind[4:] or "TAX", j, heads[i][1]) for i, j in sorted(under.items(), key=lambda ij: ij[1])
                      for kind in [heads[i][0]]
                      if kind.startswith("tax:") and any(abs(c.value) >= 0.005 for c in self.columns[j])
                      and all(c.money or not c.value for c in self.columns[j])]  # fmt: skip

    def cell(self, column: int | None, row: int) -> _Cell | None:
        return next((c for c in self.columns[column] if c.row == row), None) if column is not None else None

    def elsewhere(self, seg: Line) -> bool:
        """The segment's numbers all stand under another heading than one that could name the amount: no amount,
        however near the amount's column they are printed."""
        mine = [j for j, column in enumerate(self.columns) if any(c.seg is seg for c in column)]
        return bool(mine) and all(self.kinds.get(j, "amount") not in ("amount", "amount?") for j in mine)

    def row_taxes(self, row: int, segs: list[Line]) -> dict[str, float | None] | None:
        """``LineReading.taxes_read`` of a row: the amount in each tax column, 0.0 where its cell prints that there
        is none ("-", "n/a"), None where no cell was read."""
        if not self.taxes:
            return None
        out: dict[str, float | None] = {}
        for name, j, head in self.taxes:
            cell = self.cell(j, row)
            span = _span(self.columns[j])
            said = any(_NO_TAX_CELL.match(plain(s.text).strip())
                       and min(span.x0, head.x0) - 0.01 <= s.box.cx <= max(span.x1, head.x1) + 0.01 for s in segs)  # fmt: skip
            value = cell.value if cell is not None else 0.0 if said else None
            out[name] = value if name not in out else None if None in (value, out[name]) else out[name] + value
        return out

    def _money_columns(self) -> list[int]:
        """The columns that could hold the lines' amounts beside the one read: every number in them printed as
        money, under a heading that could name the amount or one the reader does not know ("Base", "Each")."""
        return [j for j, column in enumerate(self.columns) if j != self.amount
                and self.kinds.get(j, "") in ("amount", "amount?", "") and all(c.money for c in column)]  # fmt: skip

    def before_tax(self, amounts: dict[int, float], taxes: dict[int, float]) -> int | None:
        """The column of the amounts before tax, when the amounts read (``amounts``, by row) are the totals with the
        tax in them: on every row that prints a tax (``taxes``) and a number in that column, the number and the tax
        make the amount read, to the cent, the tax the smaller of the two (a discount and the amount after it make
        the amount before it just so); at least one row does. One column or none: two that fit prove nothing."""
        fits = []
        for j in self._money_columns():
            taxed = [(c.value, taxes[c.row], amounts[c.row]) for c in self.columns[j]
                     if c.row in amounts and abs(taxes.get(c.row, 0.0)) >= 0.005]  # fmt: skip
            if taxed and all(
                abs(value + tax - amount) <= 0.011 and abs(tax) < abs(value) for value, tax, amount in taxed
            ):
                fits.append(j)
        return fits[0] if len(fits) == 1 else None

    def adding_up(self, amounts: dict[int, float], targets: list[float] | None) -> int | None:
        """Another column headed as an amount whose numbers, one on every row read, add up to what the lines should
        (``targets``), when the amounts read do not."""
        if not targets or any(abs(round(sum(amounts.values()), 2) - t) <= 0.02 for t in targets):
            return None
        for j, column in enumerate(self.columns):
            if j != self.amount and self.kinds.get(j) == "amount?" and {c.row for c in column} >= amounts.keys():
                total = round(sum(c.value for c in column if c.row in amounts), 2)
                if all(c.money for c in column) and any(abs(total - t) <= 0.02 for t in targets):
                    return j
        return None

    def totals_with_tax(self, amounts: dict[int, float], taxes: dict[int, float]) -> int | None:
        """The column of each row's total with its tax: the amount and the tax make its number on every row that
        prints a tax. A row whose amount alone makes it has no tax in a cell the reader missed."""
        for j in self._money_columns():
            taxed = [(c.value, taxes[c.row], amounts[c.row]) for c in self.columns[j]
                     if c.row in amounts and abs(taxes.get(c.row, 0.0)) >= 0.005]  # fmt: skip
            if taxed and all(abs(amount + tax - value) <= 0.011 for value, tax, amount in taxed):
                return j
        return None


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


@dataclass
class _RowAmounts:
    """What the columns of one page's table give its rows, by row: ``picked`` (its amount, the segment it is in,
    printed as money), ``taxes`` (its ``taxes_read``), ``discounts`` (the number in its discount column) and
    ``aside`` (as ``picked``, of the rows that are no lines: a sum carried over, the supplier's account)."""

    picked: dict[int, tuple[float, Line, bool]]
    taxes: dict[int, Any]
    discounts: dict[int, float]
    aside: dict[int, tuple[float, Line, bool]]


def _no_line(segs: list[Line]) -> bool:
    return any(_CARRIED.match(p) or _ACCOUNT_ROW.match(p) for p in (plain(s.text).strip() for s in segs))


def _row_amounts(head_row: list[Line], cols: dict[str, Box], ordered: list[list[Line]],
                 targets: list[float] | None) -> _RowAmounts:  # fmt: skip
    """The amounts of the rows under the headings of one page (``ordered``, top to bottom): the amount's column for
    the whole table (``_Columns``)."""
    table = _Columns(head_row, cols, ordered)
    # Each row's amount: its number in the amount's column, else (a row printed out of line, a heading with no
    # column found under it) the number nearest the column that stands under no other heading.
    under = _span(table.columns[table.amount]) if table.amount is not None else cols["amount"]
    picked: dict[int, tuple[float, Line, bool]] = {}  # {row: (amount, its segment, printed as money)}
    for k, segs in enumerate(ordered):
        cell = table.cell(table.amount, k)
        if cell is not None:
            picked[k] = (cell.value, cell.seg, cell.money)
            continue
        near = 1.0  # of the cells under the amount's heading, the nearest to it ("10%" in a VAT column beside)
        for s in segs:
            read = _amounts_in(s.text)
            off = min(abs(s.box.x1 - under.x1), abs(s.box.cx - under.cx))
            if read and off < 0.08 and off <= near and not table.elsewhere(s):
                pick = _under(s, read, under)
                picked[k] = (pick[0], s, _printed_as_money(s.text[pick[1] : pick[2]]))
                near = off
    aside = {k: picked.pop(k) for k in list(picked) if _no_line(ordered[k])}
    # Each line's tax printed as an amount (B-2106), and what the numbers say of the amount's column: for the
    # whole table, never for one row.
    taxes = {k: table.row_taxes(k, ordered[k]) for k in picked}
    tax = {k: sum(v for v in (taxes[k] or {}).values() if v) for k in picked}
    amounts = {k: v[0] for k, v in picked.items()}
    column = table.before_tax(amounts, tax)
    column = table.adding_up(amounts, targets) if column is None else column
    if column is not None:
        table.amount = column
        for k in picked:
            cell = table.cell(column, k)
            picked[k] = (cell.value, cell.seg, cell.money) if cell is not None else picked[k]
        amounts = {k: v[0] for k, v in picked.items()}
    with_tax = table.totals_with_tax(amounts, tax)
    for k in picked if with_tax is not None else ():
        cell = table.cell(with_tax, k)
        if cell is not None and abs(amounts[k] + tax[k] - cell.value) <= 0.011:  # no tax in a cell not read
            taxes[k] = {name: value or 0.0 for name, value in (taxes[k] or {}).items()}
    discount = next((j for j, kind in table.kinds.items() if kind == "discount"), None)
    discounts = {c.row: c.value for c in table.columns[discount]} if discount is not None else {}
    return _RowAmounts(picked, taxes, discounts, aside)


def _after_discount(qty: float | None, price: float | None, discount: float | None, amount: float) -> float | None:
    """The unit price after the row's discount, when the discount (an amount, or a percentage) explains why the
    quantity and the unit price do not make the amount (B-2269); None when it does not."""
    if not qty or price is None or not discount or abs(qty * price - amount) <= 0.011:
        return None
    gross = qty * price
    explained = abs(gross - discount - amount) <= 0.011 or abs(gross * (1 - discount / 100) - amount) <= 0.011
    return round(amount / qty, 4) if explained else None


def _lines_under(head_row: list[Line], cols: dict[str, Box], ordered: list[list[Line]],
                 targets: list[float] | None, first: bool = True,
                 left_out: list[LineReading] | None = None) -> tuple[list[LineReading], list[bool]]:  # fmt: skip
    """(the line items of the rows under the headings of one page, whether each one's amount is printed as money):
    a row with an amount is a line; one without, right under a line, the rest of its description. A row of the
    supplier's account is no line (``left_out`` gathers them); ``first``: no line was read on a page before."""
    read = _row_amounts(head_row, cols, ordered, targets)
    picked, taxes = read.picked, read.taxes
    tax_column = tax_code_column(head_row, cols)
    number_column = row_number_column(head_row, cols)
    items: list[LineReading] = []
    money: list[bool] = []
    last: tuple[LineReading, float] | None = None  # the line item last read on this page, and its bottom
    above = _wrapped_above(ordered, picked, cols)
    hanging: list[Line] = []  # the lines of the description of the next row, printed above its amount
    for k, segs in enumerate(ordered):
        if k in above:
            hanging += segs
            last = None
            continue
        if _no_line(segs):
            words = [plain(s.text).strip() for s in segs]
            carried = any(_CARRIED.match(p) for p in words)
            if k in read.aside and (not carried or (first and not items and any(_FORWARD.match(p) for p in words))):
                amount, amt_seg, _ = read.aside[k]
                box = union_all([s.box for s in segs])
                _set_aside(left_out, LineReading(_row_description(segs, [], amt_seg, cols, number_column).strip(),
                                                 None, None, amount, [box] if box else []))  # fmt: skip
            last = None
            continue
        if k not in picked:
            if last is not None and _wrapped(segs, cols, last[1]):  # the rest of the description above
                li = last[0]
                li.description = " ".join([li.description, *(s.text for s in segs)]).strip()
                li.boxes = [union_all([*li.boxes, *(s.box for s in segs)]) or li.boxes[0]] if li.boxes else []
                last = (li, max(s.box.y1 for s in segs))
            else:
                last = None
            continue
        amount, amt_seg, as_money = picked[k]
        nums = [(s, _amounts_in(s.text)) for s in segs]
        flags = _tax_code_cells(segs, amt_seg, cols, tax_column)
        desc = _row_description(segs, flags, amt_seg, cols, number_column)
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
        net = _after_discount(qty, price, read.discounts.get(k), amount)
        price = price if net is None else net
        # The quantity or the price not read under its heading (OCR lost a heading, or a digit): the two numbers
        # left of the amount whose product it is, when the table has a quantity or a price column.
        if {"quantity", "unit_price"} & cols.keys() and not (
            qty is not None and price is not None and abs(qty * price - amount) <= 0.011
        ):
            fit = _qty_times_price(segs, nums, amt_seg, amount)
            if fit:
                qty, price = fit
        if hanging:
            desc = " ".join([*(s.text for s in hanging), desc.strip()])
        row_box = union_all([s.box for s in [*hanging, *segs]])
        hanging = []
        score = 0.8 if (qty is None or price is None or abs(qty * price - amount) <= 0.011) else 0.5
        items.append(LineReading(desc.strip(), qty, price, amount, [row_box] if row_box else [], score,
                                 tax_flags=" ".join(" ".join(s.text.split()) for s in flags),
                                 taxes_read=taxes[k]))  # fmt: skip
        money.append(as_money)
        last = (items[-1], max(seg.box.y1 for seg in segs))
    return items, money


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
                    targets: list[float] | None = None,
                    left_out: list[LineReading] | None = None) -> list[LineReading]:  # fmt: skip
    """The line items under the line table's column headings, read on the page straightened (``straightened``).
    ``tables``: the tables the table finder found on each page (``table_finder.find_tables``); the rows are then read
    from the words inside them only (``inside_tables``). ``targets``: what the lines should add up to (the subtotal,
    the total less the taxes), for a table whose headings can't be read (``_headerless_rows``). ``left_out``: a list
    that gathers the rows of the supplier's account, which are no lines (``without_account_rows``)."""
    skews = {page.number: page.skew for page in layout.pages if abs(page.skew) >= STRAIGHTEN_FROM}
    one_table = {n: [straightened(inside_tables(layout, {n: [box]})) for box in boxes]
                 for n, boxes in (tables or {}).items() if boxes}  # fmt: skip
    printed = {round(v, 2) for line in layout.lines() for v, a, b in _amounts_in(line.text)
               if _printed_as_money(line.text[a:b])} if tables else set()  # fmt: skip
    whole = straightened(layout) if tables else None
    items = _read_line_items(straightened(inside_tables(layout, tables) if tables else layout), one_table, targets,
                             printed, whole, left_out)  # fmt: skip
    for li in items if skews else []:
        li.boxes = [_on_tilted_page(b, skews[b.page]) if b.page in skews else b for b in li.boxes]
    if items and credit_printed_positive(layout):
        for li in items:
            li.amount = -li.amount if li.amount else li.amount
            li.unit_price = -li.unit_price if li.unit_price else li.unit_price
            li.taxes_read = li.taxes_read and {name: -v if v else v for name, v in li.taxes_read.items()}
    return items


def _totals_row(line: Line, lines: list[Line]) -> bool:
    """``line`` starts with a subtotal's, a total's or the tax's label: the line table ends at it. Not an item named
    with one's word, priced as the lines are ("Total Station survey rental | 2 | 250.00 | 500.00", B-2603): words
    that are no label after it, and beside it a quantity times a price that make an amount ("Total Units | 4 |
    200.00" and "Total Current Charges | 200.00 | 10.00 | 210.00" are totals)."""
    p = plain(line.text)
    ends = [(m.end(), f) for f in _TOTAL_LABELS for rx, _ in _LABELS[f] if (m := rx.match(p))]
    if not ends:
        return False
    end, field = max(ends)
    rest = p[end:].lstrip()
    if not _NAMED_ITEM.match(p[end:]) or any(rx.match(p) for rx in _DISTRACT[field]):
        return True  # "Total", "Total: 700.00", "Total hours" (no grand total, still under the lines)
    if any(rx.match(rest) for pats in _LABELS.values() for rx, _ in pats):
        return True  # "Total GST 5%"
    beside = sorted((o for o in lines if o is not line and abs(o.box.cy - line.box.cy) < 0.006
                     and o.box.x0 > line.box.x1 and _NUMBER_CELL.match(o.text)), key=lambda o: o.box.x0)  # fmt: skip
    figures = [abs(v) for o in beside for v, _a, _b in _amounts_in(o.text)]
    return not any(abs(q * price - amount) <= 0.011 and amount
                   for i, q in enumerate(figures) for j, price in enumerate(figures[i + 1 :], i + 1)
                   for amount in figures[j + 1 :])  # fmt: skip


def _read_line_items(layout: DocLayout, one_table: dict[int, list[DocLayout]] | None = None,
                     targets: list[float] | None = None, printed: set[float] | None = None,
                     whole: DocLayout | None = None,
                     left_out: list[LineReading] | None = None) -> list[LineReading]:  # fmt: skip
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
            rows_read = without_account_rows(rows_read, left_out)
            items += rows_read
            money += [True] * len(rows_read)
            guessed += rows_read
            printed_sum = at_sum if rows_read else printed_sum
            continue
        y_top, cols = header
        rows: dict[float, list[Line]] = {}
        stop_y = stop_cy = 1.0
        for line in page.lines:
            if line.box.y0 <= y_top:
                continue
            if _totals_row(line, page.lines):
                stop_y, stop_cy = min(stop_y, line.box.y0), min(stop_cy, line.box.cy)
        for line in page.lines:
            # The table ends at its subtotal or total: its amount, printed a hair higher than its label, too.
            if not (y_top < line.box.y0 < stop_y) or line.box.cy > stop_cy - 0.006:
                continue
            key = next((k for k in rows if abs(k - line.box.cy) < 0.006), line.box.cy)
            rows.setdefault(key, []).append(line)
        found_items, as_money = _lines_under(found[1], cols, [segs for _, segs in sorted(rows.items())], targets,
                                             not items, left_out)  # fmt: skip
        items += found_items
        money += as_money
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
    return min(s.box.y0 for s in segs) - bottom <= WRAP_GAP * _height(segs) and _in_description(segs, cols)


def _height(segs: list[Line]) -> float:
    return max(statistics.median(s.box.height for s in segs), 0.004)


def _in_description(segs: list[Line], cols: dict[str, Box]) -> bool:
    """A row's words all sit in the description's column, and none is an amount printed as money or a total's
    label (``_wrapped``)."""
    head = cols.get("description")
    left = head.x0 - 0.03 if head is not None else 0.0
    right = min((b.x0 for b in cols.values() if head is None or b.x0 > head.x1), default=1.0)
    return (
        all(s.box.x0 >= left and s.box.x1 <= right + 0.005 for s in segs)
        and not any(_printed_as_money(s.text[a:b]) for s in segs for _v, a, b in _amounts_in(s.text))
        and not any(rx.match(plain(s.text).strip()) for s in segs for f in _TOTAL_LABELS for rx, _ in _LABELS[f])
    )


WRAP_NEARER = 0.5  # a wrapped line goes with the row under it when the gap above is this many text heights wider


def _wrapped_above(ordered: list[list[Line]], picked: Mapping[int, Any], cols: dict[str, Box]) -> set[int]:
    """The rows with no amount (``picked``: the rows with one) that begin the description of the row under them:
    its numbers are printed beside the last line of its description, or a middle one (a spreadsheet's cell aligned
    at the bottom or in the middle). Of the rows between two lines that ``_wrapped`` would join to the line above,
    those under the widest gap between them, when it is ``WRAP_NEARER`` text heights wider than the gap down to the
    next line: a row's lines are closer to each other than to the next row's. When some are, the page prints so:
    the rows above its first line (or after a row that is no line) go with the line under them too. Each within
    ``WRAP_GAP`` text heights of the next and in the description's column."""

    def top(k: int) -> float:
        return min(s.box.y0 for s in ordered[k])

    def bottom(k: int) -> float:
        return max(s.box.y1 for s in ordered[k])

    above: set[int] = set()
    leading: list[int] = []  # rows with no line above them: of a page whose rows print so, the next line's
    k = 0
    while k < len(ordered):
        start = k
        while k < len(ordered) and k not in picked and ordered[k] and not _no_line(ordered[k]):
            k += 1
        run = list(range(start, k))
        if not run or k >= len(ordered) or k not in picked:
            k += 1
            continue
        gaps = [top(r + 1) - bottom(r) for r in run]  # (from each row down to the next, the last to the line)
        if not all(g <= WRAP_GAP * _height(ordered[r]) and _in_description(ordered[r], cols)
                   for r, g in zip(run, gaps, strict=True)):  # fmt: skip
            continue
        if start == 0 or start - 1 not in picked:
            leading += run
            continue
        line_bottom = bottom(start - 1)
        for r in run:  # (each joined to the line above, as ``_lines_under`` does)
            if not _wrapped(ordered[r], cols, line_bottom):
                break
            line_bottom = bottom(r)
        else:
            gaps.insert(0, top(start) - bottom(start - 1))
            widest = max(range(len(run)), key=lambda j: (gaps[j], j))
            height = statistics.median(_height(ordered[r]) for r in run)
            if gaps[widest] >= gaps[-1] + WRAP_NEARER * height:
                above.update(run[widest:])
    return above | set(leading) if above else above


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
