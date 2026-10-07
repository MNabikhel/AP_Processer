"""Make untrusted text safe for the places it ends up: spreadsheets and Markdown.

Vendor names, descriptions and invoice numbers come from documents and uploaded files. In a CSV or
Excel file, a cell starting with ``=``, ``+``, ``-``, ``@`` (or a tab / carriage return) can run as a
formula when opened in Excel; in Streamlit Markdown, ``[text](url)`` becomes a clickable link.
"""

from __future__ import annotations

import math
import re
from typing import Any

FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
_NUMBER = re.compile(r"^[+-]?(\d[\d,]*)?(\.\d+)?$")
_MARKDOWN = re.compile(r"([\\`*_{}\[\]()#+!|<>~:$-])")


def csv_cell(value: Any) -> Any:
    """``value`` unchanged, unless it is text that a spreadsheet would read as a formula: then it is
    prefixed with an apostrophe. Numbers (also as text, like "-12.50") are left alone."""
    if isinstance(value, str) and value.startswith(FORMULA_START) and not _NUMBER.match(value.strip()):
        return "'" + value
    return value


def csv_row(values: list[Any]) -> list[Any]:
    return [csv_cell(v) for v in values]


def neutralise_sheet(ws: Any) -> None:
    """Store every formula-looking text cell of an openpyxl worksheet as plain text."""
    for row in ws.iter_rows():
        for cell in row:
            if isinstance(cell.value, str) and cell.value.startswith(FORMULA_START):
                cell.data_type = "s"
                cell.quotePrefix = True


def md(text: Any) -> str:
    """Text shown through st.markdown / st.caption / toasts, with Markdown syntax made literal."""
    return _MARKDOWN.sub(r"\\\1", str(text if text is not None else ""))


_CURRENCY = re.compile(r"(?i)(cad|usd|eur|\$|€|£)")


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
    if "," in text and "." in text:  # the last separator is the decimal mark
        text = text.replace(".", "").replace(",", ".") if text.rfind(",") > text.rfind(".") else text.replace(",", "")
    elif "," in text:  # "150,00" is a decimal comma; "1,234" and "1,234,567" are thousands
        whole, _, decimals = text.rpartition(",")
        text = (
            f"{whole.replace(',', '')}.{decimals}"
            if text.count(",") == 1 and len(decimals) in (1, 2)
            else text.replace(",", "")
        )
    elif text.count(".") > 1:  # "1.234.567" thousands
        text = text.replace(".", "")
    try:
        number = float(text)
    except ValueError:
        return None
    if not math.isfinite(number):
        return None
    return -number if negative else number
