"""Make untrusted text safe for the places it ends up: spreadsheets (and amounts read from them).

Vendor names, descriptions and invoice numbers come from documents and uploaded files. In a CSV or
Excel file, a cell starting with ``=``, ``+``, ``-``, ``@`` (or a tab / carriage return) can run as a
formula when opened in Excel. (On the dashboard, every text is HTML-escaped by its templates.)
"""

from __future__ import annotations

import math
import re
from typing import Any

FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
_NUMBER = re.compile(r"^[+-]?(\d[\d,]*)?(\.\d+)?$")


def csv_cell(value: Any) -> Any:
    """``value`` unchanged, unless it is text that a spreadsheet would read as a formula: then it is
    prefixed with an apostrophe. Numbers (also as text, like "-12.50") are left alone."""
    if isinstance(value, str) and value.startswith(FORMULA_START) and not _NUMBER.match(value.strip()):
        return "'" + value
    return value


def csv_row(values: list[Any]) -> list[Any]:
    return [csv_cell(v) for v in values]


_XLSX_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")  # control characters Excel files cannot hold


def xlsx_row(values: list[Any]) -> list[Any]:
    """A row for an openpyxl sheet: control characters (from a damaged PDF's text) are dropped instead of
    making the whole workbook fail."""
    return [_XLSX_ILLEGAL.sub("", v) if isinstance(v, str) else v for v in values]


def neutralise_sheet(ws: Any) -> None:
    """Store every formula-looking text cell of an openpyxl worksheet as plain text."""
    for row in ws.iter_rows():
        for cell in row:
            if isinstance(cell.value, str) and cell.value.startswith(FORMULA_START):
                cell.data_type = "s"
                cell.quotePrefix = True


_CURRENCY = re.compile(r"(?i)(cad|usd|eur|\$|€|£)")


def _thousands(text: str, sep: str) -> bool:
    """Whether ``text`` is digits in real thousands groups: 1-3 digits (not 0) first, then threes ("1,234,567";
    not "0,125", "1,2345" or "12.50.00")."""
    return re.fullmatch(rf"[1-9]\d{{0,2}}(?:{re.escape(sep)}\d{{3}})+", text) is not None


def parse_amount(value: Any) -> float | None:
    """A number from a spreadsheet cell as people write it: "1,234.50", "1 234,56 $", "(150.00)",
    "150.00-", "99.00 CR", "-$150", "150,00". None when it is not a number (or NaN / infinite)."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    text = _CURRENCY.sub("", str(value)).replace(" ", "").replace(" ", "").replace(" ", "").strip()
    if not text:
        return None
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative, text = True, text[1:-1]
    if text.upper().endswith("CR"):
        negative, text = True, text[:-2]
    if text.endswith("-"):
        negative, text = True, text[:-1]
    if text.startswith("-"):
        negative, text = True, text[1:]
    elif text.startswith("+"):
        text = text[1:]
    if "," in text and "." in text:  # the last separator is the decimal mark, the other the thousands one
        mark = "," if text.rfind(",") > text.rfind(".") else "."
        whole, _, decimals = text.rpartition(mark)
        group = "." if mark == "," else ","
        if group in whole and not _thousands(whole, group):
            return None  # "12,34.50": not real thousands
        text = f"{whole.replace(group, '')}.{decimals}"
    elif "," in text or "." in text:
        sep = "," if "," in text else "."
        if _thousands(text, sep) and (sep == "," or text.count(".") > 1):  # "1,234", "1,234,567", "1.234.567"
            text = text.replace(sep, "")
        elif text.count(sep) == 1:  # "150,00", "0,125", "1,2345": a decimal comma ("1.5" as it is)
            text = text.replace(",", ".")
        else:  # "12.50.00", "01.02.2026": not a number
            return None
    try:
        number = float(text)
    except ValueError:
        return None
    if not math.isfinite(number):
        return None
    return -number if negative else number
