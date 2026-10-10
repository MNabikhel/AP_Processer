"""Two readings of a page compared figure by figure, as CloseDesk compares its page reader with OCR.

Every amount on the page is counted in both readings: the figures both have are confirmed; a figure each wrote
differently (a digit or two apart, same length) is one figure read two ways; the rest only one reading has. On a
digital PDF the first reading is the PDF's own text, which is exact, so the share confirmed measures the page reader
itself, on every invoice it reads, without anyone checking.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

_DATE = re.compile(r"\b\d{1,4}[/.-]\d{1,2}[/.-]\d{1,4}\b")
_FIGURE = re.compile(r"(?<![\w.])([-−(]?)\s?[$€£]?\s?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(\)?)(%?)(?![\w])")
_TAGS = re.compile(r"<[^>]+>")
LISTED = 8  # figures listed per kind (read differently, only one reading has them)


def figures(text: str) -> dict[Decimal, list[str]]:
    """Each amount in the text (signed: "(2,750)" and "-2,750" are the same figure) and how it was written. Short
    whole numbers (a page or line number, a quantity) and years are left out, and so are dates."""
    found: dict[Decimal, list[str]] = {}
    plain = _DATE.sub(" ", _TAGS.sub(" ", text or ""))  # the page reader writes tables as HTML
    for match in _FIGURE.finditer(plain):
        sign, whole, decimals, close, percent = match.groups()
        digits = whole.replace(",", "")
        if not decimals and not percent and "," not in whole:
            if len(digits) < 3 or (len(digits) == 4 and 1900 <= int(digits) <= 2100):
                continue
        try:
            value = Decimal(digits + (decimals or ""))
        except InvalidOperation:
            continue
        shown = match.group(0).strip()
        if sign in {"-", "−"} or (sign == "(" and close == ")"):
            value = -value
        elif sign == "(":  # "(13%)": the bracket around it, not a negative figure
            shown = shown[1:].strip()
        found.setdefault(value, []).append(shown)
    return found


@dataclass
class FigureComparison:
    """How the page reader's reading compares with the first one (OCR's, or the PDF's own text)."""

    figures: int = 0  # figures in the page reader's reading
    confirmed: int = 0  # ... that the first reading has too
    first_figures: int = 0  # figures in the first reading
    differ: list[tuple[str, str]] = field(default_factory=list)  # (page reader, first reading)
    only_reader: list[str] = field(default_factory=list)
    only_first: list[str] = field(default_factory=list)

    @property
    def share(self) -> float | None:
        """Confirmed figures over the figures either reading has (None: no figures at all)."""
        total = max(self.figures, self.first_figures)
        return self.confirmed / total if total else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "figures": self.figures,
            "confirmed": self.confirmed,
            "first_figures": self.first_figures,
            "differ": [list(pair) for pair in self.differ[:LISTED]],
            "only_reader": self.only_reader[:LISTED],
            "only_first": self.only_first[:LISTED],
        }


def compare_figures(first: str, reader: str) -> FigureComparison:
    """``first``: OCR's text or the PDF's own; ``reader``: the page reader's transcription (every page)."""
    mine, theirs = figures(reader), figures(first)
    reader_count = Counter({value: len(shown) for value, shown in mine.items()})
    first_count = Counter({value: len(shown) for value, shown in theirs.items()})
    both = reader_count & first_count
    pairs, left_reader, left_first = _pairs(reader_count - first_count, first_count - reader_count)
    return FigureComparison(
        figures=sum(reader_count.values()),
        confirmed=sum(both.values()),
        first_figures=sum(first_count.values()),
        differ=[(mine[a][0], theirs[b][0]) for a, b in pairs],
        only_reader=[mine[value][0] for value in left_reader],
        only_first=[theirs[value][0] for value in left_first],
    )


def _pairs(only_reader: Counter, only_first: Counter) -> tuple[list[tuple[Decimal, Decimal]], list[Decimal],
                                                                list[Decimal]]:  # fmt: skip
    """Figures the two readings wrote differently: one from each, of the same length and a digit or two apart, are
    one figure read two ways ("1,240.00" / "1,246.00"). Fewest digits apart first, then the nearest in value."""
    reader_left = [value for value, count in only_reader.items() for _ in range(count)]
    first_left = [value for value, count in only_first.items() for _ in range(count)]
    candidates = []
    for i, value in enumerate(reader_left):
        mine = _digits(value)
        for j, other in enumerate(first_left):
            theirs = _digits(other)
            if abs(other) == abs(value):  # only the sign differs
                candidates.append((0, Decimal(0), i, j))
            elif len(theirs) == len(mine) and (apart := sum(a != b for a, b in zip(mine, theirs, strict=True))) <= 2:
                candidates.append((apart, abs(abs(value) - abs(other)), i, j))
    pairs, used_reader, used_first = [], set(), set()
    for _apart, _gap, i, j in sorted(candidates):
        if i not in used_reader and j not in used_first:
            pairs.append((reader_left[i], first_left[j]))
            used_reader.add(i)
            used_first.add(j)
    rest_reader = [value for i, value in enumerate(reader_left) if i not in used_reader]
    rest_first = [value for j, value in enumerate(first_left) if j not in used_first]
    return pairs, rest_reader, rest_first


def _digits(value: Decimal) -> str:
    return re.sub(r"\D", "", format(abs(value), "f"))
