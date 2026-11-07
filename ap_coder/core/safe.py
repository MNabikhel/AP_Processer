"""Make untrusted text safe for the places it ends up: spreadsheets (and amounts read from them).

Vendor names, descriptions and invoice numbers come from documents and uploaded files. In a CSV or
Excel file, a cell starting with ``=``, ``+``, ``-``, ``@`` (or a tab / carriage return) can run as a
formula when opened in Excel. (On the dashboard, every text is HTML-escaped by its templates.)
"""

from __future__ import annotations

import re
from typing import Any

# An amount from a spreadsheet's cell (D-191): read in core/parsing.py, its rules for typed values; imported from
# here as before.
from .parsing import parse_amount  # noqa: F401

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
