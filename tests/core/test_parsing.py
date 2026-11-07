"""One amount parser and one date parser (``core/parsing.py``, D-191), each caller keeping its own rules: the readers
(``capture/normalize.py``), supplier templates (``capture/supplier.py``) and the imports (``core/safe.py``,
``core/csvio.py``)."""

from __future__ import annotations

import datetime as dt
import functools
import hashlib
import math
import random
from collections.abc import Callable
from typing import Any

import pytest

from ap_coder.capture import normalize, supplier
from ap_coder.core import csvio, parsing, safe
from tests.core.parsing_inputs import amount_texts, amount_values, date_texts

N = 20_000


@functools.cache
def _inputs(kind: str) -> tuple[Any, ...]:
    if kind == "texts":
        return tuple(amount_texts(1, N))
    if kind == "values":
        return tuple(amount_values(2, N // 2))
    return tuple(date_texts(3, N))


def _field_date(order: str | None) -> Callable[[Any], Any]:
    return lambda text: supplier.parse_date(text, order)


def _line_date(day_first: bool | None) -> Callable[[Any], Any]:
    return lambda text: normalize.parse_date(text, prefer_day_first=day_first)


# Each caller's parser on the generated inputs, and the digest of what the parsers before D-191 (at 537708d) read
# them as. When the one parser came in, the old ones and it were compared on these inputs one by one (260,046
# readings, on Python 3.11, 3.12 and 3.13): they agreed on every one. The digests keep that, now the old ones are gone.
READINGS: dict[str, tuple[str, Callable[[Any], Any], str]] = {
    "amount printed in a line": ("texts", normalize.parse_amount, "560db438cba798f1"),
    "amount typed in a cell": ("values", safe.parse_amount, "0e7bd51ffb23c639"),
    "amount typed in a cell (texts)": ("texts", safe.parse_amount, "36ceea7d0a412385"),
    "amount printed as a field": ("values", supplier.parse_amount, "b0aa37935e1ad39d"),
    "amount printed as a field (texts)": ("texts", supplier.parse_amount, "bfbc472c66dcd827"),
    "date printed in a line": ("dates", _line_date(None), "734ca27da971df66"),
    "date printed in a line, day first": ("dates", _line_date(True), "d3116bf5f2e8eb25"),
    "date printed in a line, month first": ("dates", _line_date(False), "bd1deb05af08fc17"),
    "date typed in a CSV": ("dates", csvio.parse_date, "eb575933bf06c8b4"),
    "date printed as a field": ("dates", _field_date(None), "49c5dbdf2b553eae"),
    "date printed as a field, day first": ("dates", _field_date("dmy"), "49c5dbdf2b553eae"),
    "date printed as a field, month first": ("dates", _field_date("mdy"), "8cc8db2089fabb01"),
    "date printed as a field, an order of no day": ("dates", _field_date("ymd"), "49c5dbdf2b553eae"),
}


def _reading(read: Callable[[Any], Any], value: Any) -> tuple[str, ...]:
    try:
        found = read(value)
    except Exception as exc:  # what the caller says of it (csvio: "'03/04/2026' is ambiguous")
        return ("raised", type(exc).__name__, str(exc))
    if isinstance(found, float):
        return ("float", "nan" if math.isnan(found) else repr(found))
    return (type(found).__name__, repr(found))


@pytest.mark.parametrize("name", list(READINGS))
def test_each_caller_reads_the_generated_inputs_as_its_parser_did_before(name):
    """A different digest: some generated input reads otherwise than the old parser read it. To find which, read the
    inputs (``tests/core/parsing_inputs.py``) with the old parser too (``git show 537708d:ap_coder/core/safe.py``...):
    a change meant is a decision (docs/decisions.md) and a new digest. The digest means something only if the inputs
    reach the rules: a share of them reads as something, the rest not (ids, two numbers, words, impossible dates)."""
    kind, read, digest = READINGS[name]
    readings = [_reading(read, value) for value in _inputs(kind)]
    assert hashlib.sha256("\n".join(map(repr, readings)).encode()).hexdigest()[:16] == digest
    read_as_something = sum(1 for found in readings if found[0] not in ("NoneType", "raised"))
    assert 0.05 * len(readings) < read_as_something < len(readings)


def test_an_amount_written_the_usual_ways_reads_back_in_every_grammar():
    rng = random.Random(7)
    for _ in range(2000):
        x = round(rng.uniform(0.01, 2_000_000), 2)
        english, size = f"{x:,.2f}", f"{x:.2f}"
        french = english.replace(",", " ").replace(".", ",")
        for read in (normalize.parse_amount, supplier.parse_amount, safe.parse_amount):
            assert read(english) == read(size) == read(f"${english}") == read(f"CA${english}") == x, english
            assert read(f"-{english}") == read(f"({english})") == read(f"{english} CR") == read(f"$-{english}") == -x
        for read in (normalize.parse_amount, supplier.parse_amount):  # printed: a French invoice's grouping too
            assert read(french) == read(f"{french} $") == x, french
        assert normalize.parse_amount(f"Total due {english} CAD") == x
        assert supplier.parse_amount(f"Total due {english}") is None and safe.parse_amount(f"Total {english}") is None


_MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
                "November", "December"]  # fmt: skip


def test_a_date_written_the_usual_ways_reads_back_in_every_grammar():
    rng = random.Random(8)
    for _ in range(2000):
        d = dt.date(2000, 1, 1) + dt.timedelta(days=rng.randrange(0, 365 * 90))
        numeric = f"{d.day:02d}/{d.month:02d}/{d.year}"
        assert csvio.parse_date(d.isoformat()) == parsing.parse_date(numeric, day_first=True) == d
        month = _MONTH_NAMES[d.month - 1]
        for printed in (d.isoformat(), f"{month} {d.day}, {d.year}", f"{d.day} {month} {d.year}"):
            assert normalize.parse_date(printed) == supplier.parse_date(printed) == d.isoformat(), printed
        assert normalize.parse_date(numeric, prefer_day_first=True) == supplier.parse_date(numeric) == d.isoformat()
        assert normalize.parse_date(f"Invoice date: {numeric} 13:59", prefer_day_first=True) == d.isoformat()
        if d.day > 12:  # one way only: no order needed
            assert parsing.parse_date(numeric) == d
            assert normalize.parse_date(numeric) == supplier.parse_date(numeric, "mdy") == d.isoformat()
        elif d.day != d.month:  # two ways: the order says which, else no date (and the import says why)
            assert normalize.parse_date(numeric) is None and parsing.parse_date(numeric) is None
            with pytest.raises(ValueError, match="ambiguous"):
                csvio.parse_date(numeric, "row 2")


def test_the_rules_are_the_callers_own():
    """The same text, read by each caller's rules: "1.234" is 1,234 printed (a French invoice's grouping) and 1.234
    typed in a cell, which takes any decimals; a line's first amount is not a field's whole value; a field's credit
    may come first; an order of dates chooses, or a day/month that reads both ways is no date."""
    assert normalize.parse_amount("1.234") == supplier.parse_amount("1.234") == 1234.0
    assert safe.parse_amount("1.234") == parsing.parse_amount("1.234") == 1.234
    assert safe.parse_amount("1.23456") == 1.23456 and supplier.parse_amount("1.23456") is None
    assert normalize.parse_amount("Qty 3 at 12.50") == 3.0 and supplier.parse_amount("Qty 3 at 12.50") is None
    assert supplier.parse_amount("CR 12.00") == -12.0 and normalize.parse_amount("CR 12.00") == 12.0
    assert normalize.parse_date("Date 03/04/2026") is None
    assert normalize.parse_date("Date 03/04/2026", prefer_day_first=False) == "2026-03-04"
    assert (
        supplier.parse_date("03/04/2026") == "2026-04-03" and supplier.parse_date("03/04/2026", "mdy") == "2026-03-04"
    )
    assert parsing.parse_date("03/04/2026") is None
    assert parsing.parse_date("03/04/2026", day_first=False) == dt.date(2026, 3, 4)
    assert normalize.parse_date("24. Januar 2014") == "2014-01-24" and supplier.parse_date("24. Januar 2014") is None
