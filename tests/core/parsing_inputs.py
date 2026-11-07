"""Inputs for the parsers' agreement test (tests/core/test_parsing.py): amounts and dates as invoices print them, as
spreadsheets hold them, and the near misses around them (ids, two numbers, OCR's slips, words), made from a seed so
the same seed gives the same inputs."""

from __future__ import annotations

import random
from typing import Any

_SIGNS_BEFORE = ["", "", "", "-", "−", "–", "+", "(", "-$", "$-", "- ", "CR ", "cr", "--", "$(", "( "]
_CURRENCY_BEFORE = ["", "", "", "$", "$ ", "CAD ", "CA$", "C$", "US$ ", "USD", "€", "£", "CDN$", "GBP "]
_CURRENCY_AFTER = ["", "", "", " $", "$", " CAD", " USD", " €", "€", " EUR", " cad", " GBP"]
_SIGNS_AFTER = ["", "", "", "", "-", ")", " CR", "CR", ".CR", " DR", "dr", " cr.", ") CR", " -", "%", ".%"]
_WORDS = ["", "", "", "", "", "Total ", "Total due ", "Sous-total: ", "TPS ", "GST 5% ", "Amount ", "Qty 3 ",
          "INV-", "No.", "#", "approx. ", "Balance due – ", "Total ------ "]  # fmt: skip
_AFTER_WORDS = ["", "", "", "", "", " each", " total", " 12", " /2026", " net 30", " x 2", " TVQ"]
_GROUPS = [",", ",", ".", " ", " ", " ", " ", "'", ""]
_JUNK = "0123456789.,-−–+ $()CRDrcdaeEnIl;:_'/%€ "
_OCR = {",": ";", ".": ":", "1": "I"}


def _number(rng: random.Random) -> str:
    whole = rng.choice([0, 0, 1, 7, 12, 99, 100, 250, 999, 1000, 1050, 1234, 12345, 99999, 123456, 1234567, 10**7 + 3])
    if rng.random() < 0.3:
        whole = rng.randrange(0, 10 ** rng.randint(1, 8))
    group = rng.choice(_GROUPS) if whole >= 1000 and rng.random() < 0.7 else ""
    digits = str(whole)
    if group:
        parts = []
        while len(digits) > 3:
            parts.insert(0, digits[-3:])
            digits = digits[:-3]
        digits = group.join([digits, *parts])
    mark = rng.choice([".", ".", ",", ",", ""]) if group not in (".", ",") else ("," if group == "." else ".")
    decimals = rng.choice([0, 1, 2, 2, 2, 2, 3, 4, 5])
    if mark and decimals:
        digits += mark + "".join(rng.choice("0123456789") for _ in range(decimals))
    elif mark and rng.random() < 0.1:
        digits += mark  # "5." / "5,"
    if rng.random() < 0.05:
        digits = digits.lstrip("0") or "0"
        digits = "." + digits[-2:] if rng.random() < 0.5 else digits  # ".65"
    if rng.random() < 0.05:
        digits = "0" + digits  # a leading zero ("0012,345")
    if rng.random() < 0.04:
        digits = "".join(_OCR.get(c, c) if rng.random() < 0.5 else c for c in digits)  # OCR's slips
    return digits


def amount_texts(seed: int, n: int) -> list[str]:
    """``n`` texts of amounts, near amounts and junk."""
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        roll = rng.random()
        if roll < 0.08:
            out.append("".join(rng.choice(_JUNK) for _ in range(rng.randint(0, 12))))
            continue
        text = _number(rng)
        if roll < 0.75:
            text = rng.choice(_SIGNS_BEFORE) + rng.choice(_CURRENCY_BEFORE) + text
            text += rng.choice(_CURRENCY_AFTER) + rng.choice(_SIGNS_AFTER)
        if rng.random() < 0.25:
            text = rng.choice(_WORDS) + text + rng.choice(_AFTER_WORDS)
        if rng.random() < 0.06:
            text = f"{text} {_number(rng)}"  # two numbers
        if rng.random() < 0.1:
            text = rng.choice(["", " ", "  ", "\t"]) + text + rng.choice(["", " ", "\n"])
        out.append(text)
    return out


def amount_values(seed: int, n: int) -> list[Any]:
    """Numbers as a spreadsheet's cells or a JSON field hold them (True and NaN too), and texts."""
    rng = random.Random(seed)
    specials: list[Any] = [None, True, False, 0, -0.0, 1, -12, 1234.5, float("nan"), float("inf"), float("-inf"),
                           1e20, 1e-7, 0.125, 12345678.9, "", " ", "nan", "inf", "-inf", "1e5", "1_000",
                           "Infinity"]  # fmt: skip
    out = list(specials)
    for _ in range(n):
        out.append(rng.choice([rng.randint(-(10**6), 10**6), round(rng.uniform(-1e6, 1e6), rng.randint(0, 4))]))
    return out + amount_texts(seed + 1, n)


_EN = ["Jan", "January", "Feb", "February", "Mar", "March", "Apr", "April", "May", "Jun", "June", "Jul", "July", "Aug",
       "August", "Sep", "Sept", "September", "Oct", "October", "Nov", "November", "Dec", "December"]  # fmt: skip
_FR = ["janv", "janvier", "févr", "février", "mars", "avr", "avril", "mai", "juin", "juil", "juillet", "août", "sept",
       "septembre", "oct", "octobre", "nov", "novembre", "déc", "décembre"]  # fmt: skip
_OTHER = ["Januar", "maart", "febrero", "marzo", "Oktober", "Dezember", "dic", "mei", "setembro", "Marco", "Mon",
          "Day", "Due", "Net"]  # fmt: skip
_DATE_WORDS = ["", "", "", "", "Invoice date: ", "Date ", "le ", "Due ", "INV-", "PO ", "Dated "]
_DATE_AFTER = ["", "", "", "", " 13:59", "13:59", "T00:00:00", " Contract ID: 12", "Contract", " 10AM", " net 30"]


def date_texts(seed: int, n: int) -> list[str]:
    """``n`` texts of dates (numeric, ISO, wordy, glued, in other languages), near dates and junk."""
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        day, month = (
            rng.choice([1, 3, 4, 9, 12, 13, 15, 24, 28, 29, 30, 31, 0, 32]),
            rng.choice([1, 2, 3, 4, 9, 12, 13, 0]),
        )
        year = rng.choice([2026, 2025, 2014, 2099, 1999, 1899, 2100, 26, 25, 99, 0, 1011])
        if rng.random() < 0.2:
            day, month = rng.randint(1, 31), rng.randint(1, 12)
        pad = rng.random() < 0.5
        d, m = (f"{day:02d}", f"{month:02d}") if pad else (str(day), str(month))
        y = f"{year:02d}" if year < 100 else str(year)
        sep = rng.choice(["/", "/", "-", "-", ".", " ", ", "])
        month_word = rng.choice(_EN + _FR + _OTHER) if rng.random() < 0.9 else rng.choice(_EN).upper()
        roll = rng.random()
        if roll < 0.3:
            text = sep.join([d, m, y])
        elif roll < 0.4:
            text = sep.join([y, m, d])
        elif roll < 0.5:
            text = rng.choice([f"{y}{m}{d}", f"{y}-{m}-{d}", f"{y}-{m}-{d}T10:00"])
        elif roll < 0.62:
            text = (
                f"{month_word} {d}, {y}" if rng.random() < 0.6 else f"{month_word}{rng.choice(['', '.', '-'])}{d}-{y}"
            )
        elif roll < 0.75:
            text = f"{d} {month_word} {y}" if rng.random() < 0.6 else f"{d}-{month_word}-{y}"
        elif roll < 0.8:
            text = f"{d}{month_word[:3].upper()}{y[-2:]}"
        elif roll < 0.85:
            text = rng.choice([f"1er {month_word} {y}", f"{d}. {month_word} {y}", f"{d} de {month_word} de {y}",
                               f"{y} {month_word} {d}", f"{d}, {y} {month_word}",
                               f"{month_word} {d}th, {y}"])  # fmt: skip
        elif roll < 0.92:
            text = "".join(rng.choice("0123456789/-. ,aeJjunOoctl") for _ in range(rng.randint(0, 12)))
        else:
            text = rng.choice(["", " ", "n/a", "TBD", "31/02/2026", "29/02/2025", "29/02/2024", "2026-02-30",
                               "0.1/08/2026", "28 Apr 2.025", "2025/12/.07", "0ct 15, 2026", "15OCT26"])  # fmt: skip
        if rng.random() < 0.2:
            text = rng.choice(_DATE_WORDS) + text + rng.choice(_DATE_AFTER)
        if rng.random() < 0.05:
            text = f"{text} {rng.choice(['and', '-', 'to'])} {d}/{m}/{y}"  # two dates
        if rng.random() < 0.08:
            text = rng.choice([" ", "  "]) + text + rng.choice([" ", ""])
        out.append(text)
    return out
