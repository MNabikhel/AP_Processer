"""The page reader's transcription, laid out as a page the rule reader can read.

A page reader (a vision model that reads document pages: OvisOCR2, or a general model that can see) writes
each page as text: lines with blank lines between paragraphs, a label and its value on consecutive lines
("INVOICE #:" then "26/71677") or on one line ("Total before tax $3,722.18"), and tables as HTML ``<table>``
(OvisOCR2) or markdown ``| a | b |`` rows (a general model). ``layout_from_transcript`` lays that text out as
a page of words with boxes (source "vlm"), so the rule reader reads it unchanged:

* each line of text is a row of its own, one line below the last (a blank line adds a little more), so a
  label line followed by its value line reads as a label with its value below it;
* words are boxed by their characters' positions on a fixed-pitch grid;
* each table row is a row with every cell its own line at its column's position, so a grid of labels over
  values reads label-below by column, and a line-item table's heading row and rows read as on paper.

Coordinates are fractions of the page, like a real layout's. A transcription too long for one page goes on
over the next (the table's heading row repeated), so a page never holds more rows than the reader can tell
apart, and reading a long transcription takes time in proportion to its length.
"""

from __future__ import annotations

import datetime as dt
import html
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

from .reader import read_fields, read_line_items
from .types import Box, DocLayout, Line, LineReading, PageLayout, Reading, Word

LEFT, WIDTH = 0.05, 0.9  # the text area across the page
TOP, SPAN = 0.03, 0.94  # ... and down it
PITCH = 0.0125  # one line of text (about 10 pt on a letter page)
MIN_PITCH = 0.0075  # rows closer than this would merge (the reader groups a table row by centres 0.006 apart)
BLANK = 0.4  # a blank line between paragraphs adds this much of a line
HEIGHT = 0.75  # a line's box, as a share of the pitch
CHAR = 0.0085  # one character (narrower when a line or a table needs it to fit the width)
GAP = 2  # characters between two table columns
MAX_COLUMN = 40  # widest a table column is laid out, in characters (a longer description is squeezed into it)
PAGE_SIZE = (612.0, 792.0)


def layout_from_transcript(pages: list[str]) -> DocLayout:
    """The page reader's transcription (one text per page) as a layout the rule reader reads: source "vlm",
    pages numbered from 1 (a transcribed page too long for one goes on over the next)."""
    out: list[PageLayout] = []
    for text in pages:
        for rows in _fit(_page_rows("" if made_up(text or "") else text)):
            out.append(_page(len(out) + 1, rows))
    return DocLayout(out, "vlm")


# What a vision model writes for a page with nothing on it: a page number on its own ("## 1", "Page 1"), or a
# typing-practice sentence (OvisOCR2 wrote "The quick brown fox jumps over the lazy dog." for a blank page).
_PANGRAMS = ("the quick brown fox jumps over the lazy dog", "lorem ipsum dolor sit amet")
_PAGE_WORDS = {"page", "p", "pg", "of"}


def made_up(text: str) -> bool:
    """The transcription of a page holds nothing a page shows: empty, a page number alone, or a boilerplate sentence
    (the pangram) a vision model writes when it is shown a blank page. Read as a blank page."""
    words = re.findall(r"\w+", " ".join(_clean(line, cell=False) for line in _without_thinking(text).splitlines()))
    words = [word.casefold() for word in words]
    if not words:
        return True
    if len(words) <= 4 and all(word.isdigit() or word in _PAGE_WORDS for word in words):
        return True
    joined = " ".join(words)
    return any(joined.startswith(pangram) and len(words) <= 3 * len(pangram.split()) for pangram in _PANGRAMS)


def transcript_fields(pages: list[str], received: dt.date | None = None) -> dict[str, list[Reading]]:
    """Candidate readings of every header field in the transcription, best first (``reader.read_fields``)."""
    return read_fields(layout_from_transcript(pages), received=received)


def transcript_line_items(pages: list[str]) -> list[LineReading]:
    """The line items of the transcription's line-item table (``reader.read_line_items``)."""
    return read_line_items(layout_from_transcript(pages))


# ---------------------------------------------------------------- rows


@dataclass
class _Row:
    cells: list[tuple[float, float, str]]  # (x0, x1, text): where each piece of text sits across the page
    space: float = 0.0  # extra space above the row, in lines (a blank line before it)
    head: _Row | None = field(default=None, repr=False)  # the table's heading row, repeated on a new page


def _fit(rows: list[_Row]) -> Iterator[list[_Row]]:
    """The rows of one transcribed page, cut into pages that each hold them at least ``MIN_PITCH`` apart. A table
    cut in two starts again on the next page with its heading row."""
    if _units(rows) * MIN_PITCH <= SPAN:
        yield rows
        return
    page: list[_Row] = []
    used = 0.0
    for row in rows:
        if page and (used + 1 + row.space) * MIN_PITCH > SPAN:
            yield page
            page = [row.head] if row.head is not None and row.head is not row else []
            used = float(len(page))
        used += 1 + (row.space if page else 0.0)
        page.append(row)
    yield page


def _units(rows: list[_Row]) -> float:
    """The height of these rows, in lines."""
    return sum(1 + (row.space if i else 0.0) for i, row in enumerate(rows))


def _page(number: int, rows: list[_Row]) -> PageLayout:
    pitch = min(PITCH, SPAN / max(_units(rows), 1.0))
    words: list[Word] = []
    lines: list[Line] = []
    y = TOP
    for i, row in enumerate(rows):
        if i:
            y += pitch * (1 + row.space)
        for x0, x1, text in row.cells:
            ws = _words(text, x0, x1, y, y + HEIGHT * pitch, number)
            if ws:
                box = Box(number, ws[0].box.x0, ws[0].box.y0, ws[-1].box.x1, ws[0].box.y1)
                lines.append(Line(ws, box, " ".join(w.text for w in ws)))
                words += ws
    lines.sort(key=lambda ln: (round(ln.box.cy, 3), ln.box.x0))
    return PageLayout(number, PAGE_SIZE[0], PAGE_SIZE[1], words, lines)


def _words(text: str, x0: float, x1: float, y0: float, y1: float, page: int) -> list[Word]:
    """The words of ``text`` set between x0 and x1, each boxed by its characters' positions."""
    parts = text.split()
    length = len(" ".join(parts))
    if not length:
        return []
    step = (x1 - x0) / length
    out, pos = [], 0
    for part in parts:
        out.append(Word(part, Box(page, x0 + pos * step, y0, x0 + (pos + len(part)) * step, y1), 1.0, "vlm"))
        pos += len(part) + 1
    return out


def _page_rows(text: str) -> list[_Row]:
    """One transcribed page as rows, top to bottom."""
    rows: list[_Row] = []
    space = 0.0
    for kind, block in _blocks(_without_thinking(text)):
        if kind == "table":
            table = _table_rows(block)
            if table:
                table[0].space = BLANK if rows else 0.0
                rows += table
                space = BLANK
            continue
        row = _text_row(_clean(block, cell=False))
        if row is None:
            space = BLANK if rows else 0.0
            continue
        row.space = space
        rows.append(row)
        space = 0.0
    return rows


def _text_row(line: str) -> _Row | None:
    """A line of text as a row; runs of text two or more spaces apart are set apart (columns side by side)."""
    line = line.expandtabs(4).strip()
    if not line:
        return None
    step = min(CHAR, WIDTH / len(line))
    cells = [(LEFT + m.start() * step, LEFT + m.end() * step, m.group(0)) for m in re.finditer(r"\S+(?: \S+)*", line)]
    return _Row(cells)


# ---------------------------------------------------------------- tables


def _table_rows(grid: list[list[tuple[int, int, str]]]) -> list[_Row]:
    """A table (each row's cells as (first column, columns spanned, text)) as rows, each cell at its column's
    position. A cell merged across columns and followed by more cells is set at the right of its columns, as
    a "Subtotal" merged across the line table sits beside its amount."""
    grid = [[(col, span, " ".join(_clean(text, cell=True).split())) for col, span, text in row] for row in grid]
    columns = max((col + span for row in grid for col, span, _ in row), default=0)
    if not columns:
        return []
    widths = [1] * columns
    for row in grid:
        for col, span, text in row:
            if span == 1:
                widths[col] = max(widths[col], min(len(text), MAX_COLUMN))
    starts, x = [], 0
    for width in widths:
        starts.append(x)
        x += width + GAP
    step = min(CHAR, WIDTH / max(x - GAP, 1))
    out: list[_Row] = []
    head: _Row | None = None
    for row in grid:
        filled = [(col, span, text) for col, span, text in row if text]
        cells = []
        for i, (col, span, text) in enumerate(filled):
            room = sum(widths[col : col + span]) + GAP * (span - 1)
            length = min(len(text), room)
            at = starts[col] + (room - length if span > 1 and i < len(filled) - 1 else 0)
            cells.append((LEFT + at * step, LEFT + (at + length) * step, text))
        if not cells:
            continue
        out.append(_Row(cells, head=head))
        if head is None and len(cells) >= 2:
            head = out[-1]
            for earlier in out:
                earlier.head = head
    return out


class _TableCells(HTMLParser):
    """An HTML table's rows of cells [colspan, rowspan, text parts], read as a browser reads them: a cell or a
    row ends where the next one starts even without its closing tag, a cell after a row's end starts a row,
    and other tags are spaces. Text outside any cell (a caption) is kept apart."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[list[Any]]] = []
        self.outside: list[str] = []
        self.cell: list[Any] | None = None
        self.row_open = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.cell = None
            self.rows.append([])
            self.row_open = True
        elif tag in ("td", "th"):
            if not self.row_open:
                self.rows.append([])
                self.row_open = True
            spans = dict(attrs)
            self.cell = [_span(spans.get("colspan"), 50), _span(spans.get("rowspan"), 200), []]
            self.rows[-1].append(self.cell)
        else:
            self.handle_data(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th", "tr"):
            self.cell = None
            self.row_open = self.row_open and tag != "tr"
        else:
            self.handle_data(" ")

    def handle_data(self, data: str) -> None:
        (self.cell[2] if self.cell is not None else self.outside).append(data)


def _span(value: str | None, most: int) -> int:
    number = re.match(r"\s*(\d+)", value or "")
    return max(1, min(int(number.group(1)), most)) if number else 1


def _html_grid(table: str) -> tuple[str, list[list[tuple[int, int, str]]]]:
    """(text outside the cells, each row's cells as (first column, columns spanned, text)). A cell merged down
    over rows keeps its columns in the rows below it (its text is in its first row only)."""
    parser = _TableCells()
    try:
        parser.feed(table)
        parser.close()
    except Exception:  # a broken table reads as far as it goes
        pass
    taken: dict[int, set[int]] = {}
    grid = []
    for r, cells in enumerate(parser.rows):
        busy = taken.pop(r, set())
        row, col = [], 0
        for colspan, rowspan, parts in cells:
            while col in busy:
                col += 1
            row.append((col, colspan, " ".join("".join(parts).split())))
            for down in range(1, rowspan):
                taken.setdefault(r + down, set()).update(range(col, col + colspan))
            col += colspan
        grid.append(row)
    return " ".join("".join(parser.outside).split()), grid


_TABLE_TAG = re.compile(r"<(/?)table\b[^>]*>", re.I)


def _html_table_spans(text: str) -> list[tuple[int, int]]:
    """Where each table is: from its <table> to its </table>, or to the next <table> or the end of the text when
    the reading never closed it."""
    spans, start = [], None
    for match in _TABLE_TAG.finditer(text):
        if not match.group(1):
            if start is not None:
                spans.append((start, match.start()))
            start = match.start()
        elif start is not None:
            spans.append((start, match.end()))
            start = None
    if start is not None:
        spans.append((start, len(text)))
    return spans


def _md_cells(line: str) -> list[str]:
    inner = line.strip()
    inner = inner[1:] if inner.startswith("|") else inner
    inner = inner[:-1] if inner.endswith("|") and not inner.endswith("\\|") else inner
    return [cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", inner)]


def _rule_row(line: str) -> bool:
    """A markdown table's rule line: "|---|:--:|"."""
    cells = _md_cells(line)
    return "|" in line and any(cells) and all(re.fullmatch(r":?-{2,}:?|", cell) for cell in cells)


def _table_start(lines: list[str], i: int) -> bool:
    line = lines[i].strip()
    if "|" not in line:
        return False
    if line.startswith("|") and line.endswith("|") and len(line) > 1:
        return True
    return i + 1 < len(lines) and _rule_row(lines[i + 1])  # a table written without its outer pipes


# ---------------------------------------------------------------- the transcription's text


_THINK = re.compile(r"<think>.*?(?:</think>|\Z)", re.S)
_COMMENT = re.compile(r"<!--.*?(?:-->|\Z)", re.S)
_IMG = re.compile(r"<img\b[^>]*>", re.I)
_BREAK = re.compile(r"<br\s*/?>|</(?:p|div|h[1-6]|li|ul|ol|blockquote|pre)\s*>", re.I)
_TAG = re.compile(r"</?[A-Za-z][A-Za-z0-9]*(?:\s[^<>]*)?/?>")


def _without_thinking(text: str) -> str:
    """A reasoning model's scratch work (<think>…</think>, or everything before a lone </think>) left out."""
    if "</think>" in text and "<think>" not in text.split("</think>", 1)[0]:
        text = text.split("</think>", 1)[1]
    return _THINK.sub("", text)


def _blocks(text: str) -> Iterator[tuple[str, Any]]:
    """("line", text) for each line ("" for a blank line) and ("table", grid) for each table, in order."""
    text = _IMG.sub(" ", _COMMENT.sub(" ", text.replace("\r\n", "\n").replace("\r", "\n")))
    last = 0
    for start, end in _html_table_spans(text):
        yield from _text_blocks(text[last:start])
        caption, grid = _html_grid(text[start:end])
        if caption:
            yield "line", caption
        yield "table", grid
        last = end
    yield from _text_blocks(text[last:])


def _text_blocks(text: str) -> Iterator[tuple[str, Any]]:
    lines = _TAG.sub(" ", _BREAK.sub("\n", text)).split("\n")
    i = 0
    while i < len(lines):
        if _table_start(lines, i):
            grid = []
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                if not _rule_row(lines[i]):
                    grid.append([(col, 1, cell) for col, cell in enumerate(_md_cells(lines[i]))])
                i += 1
            yield "table", grid
            continue
        yield "line", lines[i]
        i += 1


_RULE_LINE = re.compile(r"^\s*(?:[-*_=]\s*){3,}$")  # a horizontal rule
_HEADING = re.compile(r"^\s{0,3}#{1,6}(?:\s+|$)")
_BULLET = re.compile(r"^\s*(?:[-*+•▪]|>+)\s+")
_PICTURE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_STRONG = re.compile(r"(?<!\*)\*{2,3}(?!\*)|(?<!_)_{2,3}(?!_)|`+")  # "****9012" (a masked number) is kept
_EMPHASIS = re.compile(
    r"(?<![\w*])\*(?=[^\s*])([^*\n]*?[^\s*])\*(?![\w*])"  # *italic*
    r"|(?<![\w_])_(?=[^\s_])([^_\n]*?[^\s_])_(?![\w_])"  # _italic_ (not inside a word: INV_2026_01)
)
_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|$%&<>~])")


def _clean(line: str, *, cell: bool) -> str:
    """A line (or a table cell) as printed: LaTeX, markdown marks (headings, bullets, emphasis, code, links,
    escapes) and HTML entities taken out. A cell keeps a leading "#" or "-": it is a heading ("#") or a figure."""
    if _RULE_LINE.match(line):
        return ""
    line = _math(line)
    if not cell:
        line = _BULLET.sub("", _HEADING.sub("", line))
    line = _LINK.sub(r"\1", _PICTURE.sub(" ", line))
    line = _STRONG.sub("", line)
    line = _EMPHASIS.sub(lambda m: m.group(1) or m.group(2) or "", line)
    line = _ESCAPE.sub(r"\1", line)
    return html.unescape(line).replace("\xa0", " ").rstrip()


# ---------------------------------------------------------------- LaTeX

_DISPLAY_MATH = re.compile(r"\$\$(.+?)\$\$|\\\[(.+?)\\\]|\\\((.+?)\\\)")
_LATEX_LIKE = re.compile(r"\\(?:[A-Za-z]+|[%$&#_{},;:! ])|[\^_]\{|\^\\?\w")
_LATEX_WRAP = re.compile(r"\\[A-Za-z]+\s*\{([^{}]*)\}")
_LATEX_COMMAND = re.compile(r"\\([A-Za-z]+|[%$&#_{},;:! ])")
_LATEX_SYMBOLS = {
    "times": "×", "cdot": "·", "pm": "±", "div": "÷", "le": "≤", "leq": "≤", "ge": "≥", "geq": "≥", "ne": "≠",
    "neq": "≠", "approx": "≈", "circ": "°", "degree": "°", "euro": "€", "pounds": "£", "dots": "...",
    "ldots": "...", "cdots": "...", "%": "%", "$": "$", "&": "&", "#": "#", "_": "_", "{": "{", "}": "}",
    ",": " ", ";": " ", ":": " ", " ": " ", "!": "", "quad": " ", "qquad": " ",
}  # fmt: skip


def _latex_text(math: str) -> str:
    """LaTeX as plain text: "5\\%" -> "5%", "\\text{GST}" -> "GST", "\\$1,234.56" -> "$1,234.56"."""
    for _ in range(3):  # nested wrappers: \text{\textbf{x}}
        math = _LATEX_WRAP.sub(r"\1", math)
    math = re.sub(r"[\^_]\{([^{}]*)\}", r"\1", math)
    math = re.sub(r"\^\\circ|\^\{\\circ\}", "°", math)
    math = _LATEX_COMMAND.sub(lambda m: _LATEX_SYMBOLS.get(m.group(1), m.group(1)), math)
    math = re.sub(r"(?<=\w)[\^_](?=\w)", "", math).replace("~", " ")
    return re.sub(r"(?<!\\)[{}]", "", math)


def _math(line: str) -> str:
    """The line with its formulas as plain text. A pair of $ is a formula only when what it holds is LaTeX:
    "$186.11 and $260.55" stays money."""
    if "$" not in line and "\\" not in line:
        return line
    line = _DISPLAY_MATH.sub(lambda m: _latex_text(next(g for g in m.groups() if g is not None)).strip(), line)
    if "$" not in line:
        return line
    out, i = [], 0
    while i < len(line):
        if line[i] == "$" and (i == 0 or line[i - 1] != "\\"):
            end = _closing_dollar(line, i + 1)
            if end > 0 and _LATEX_LIKE.search(line[i + 1 : end]):
                out.append(_latex_text(line[i + 1 : end]).strip())
                i = end + 1
                continue
        out.append(line[i])
        i += 1
    return "".join(out)


def _closing_dollar(line: str, start: int) -> int:
    """The next $ not escaped, within a formula's reach; -1 when there is none."""
    for j in range(start, min(len(line), start + 160)):
        if line[j] == "$" and line[j - 1] != "\\":
            return j
    return -1
