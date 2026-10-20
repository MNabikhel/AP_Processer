"""A CSV layout of your own, for an ERP whose import expects specific columns.

One row per GL posting line (expense lines and recoverable-tax lines), with the columns, their order and
headers chosen on the Exports page. A column takes a field (invoice or line), or fixed text such as a
company code or a journal name. Dates, decimal mark and delimiter follow the ERP's expectations, and so does the
UTF-8 byte-order mark at the start (on unless the layout leaves it out: Excel needs it to show accents, a loader
that reads plain UTF-8 takes it as part of the first header).

The file is for the ERP's loader, so text is written exactly as it is: no spreadsheet apostrophe in front of
``+A-100`` or ``- Labour``, which would no longer match what the ERP holds.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
from dataclasses import asdict, dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from ..coding.tax import currency_places

FIXED = "(fixed text)"
FIELDS: dict[str, str] = {
    "vendor_id": "Vendor ID (vendor master)", "vendor_name": "Vendor name", "invoice_number": "Invoice number",
    "invoice_date": "Invoice date", "due_date": "Due date", "po_number": "PO number",
    "payment_terms": "Payment terms", "currency": "Currency", "subtotal": "Invoice subtotal",
    "tax_total": "Invoice tax", "grand_total": "Invoice total", "gst_hst_registration_number": "Vendor GST/HST #",
    "qst_registration_number": "Vendor QST #", "line": "Line number", "kind": "Line type (Expense / Tax)",
    "gl_code": "GL account", "gl_name": "GL name", "cost_center": "Cost center", "description": "Line description",
    "net_amount": "Line net amount", "non_recoverable_tax": "Line non-recoverable tax",
    "amount": "Line amount (posting)", "batch": "Export batch", "invoice_id": "AP Coder #",
    "reviewer": "Approved by", "reviewed_at": "Approved at",
}  # fmt: skip
DATE_FIELDS = {"invoice_date", "due_date", "reviewed_at"}
MONEY_FIELDS = {"subtotal", "tax_total", "grand_total", "net_amount", "non_recoverable_tax", "amount"}
DATE_FORMATS = {"YYYY-MM-DD": "%Y-%m-%d", "MM/DD/YYYY": "%m/%d/%Y", "DD/MM/YYYY": "%d/%m/%Y", "YYYYMMDD": "%Y%m%d"}
DELIMITERS = {",": "Comma", ";": "Semicolon", "\t": "Tab", "|": "Pipe"}


@dataclass
class Column:
    header: str
    field: str  # a key of FIELDS, or FIXED
    text: str = ""  # the value when field is FIXED


@dataclass
class Layout:
    columns: list[Column] = field(default_factory=list)
    date_format: str = "YYYY-MM-DD"
    delimiter: str = ","
    decimal_comma: bool = False
    header_row: bool = True
    byte_order_mark: bool = True

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str) -> Layout:
        try:
            data = json.loads(text or "{}")
        except ValueError:
            data = {}
        columns = [
            Column(**c) for c in data.get("columns") or [] if c.get("field") in FIELDS or c.get("field") == FIXED
        ]
        layout = cls(columns=columns or default_columns())
        if data.get("date_format") in DATE_FORMATS:
            layout.date_format = data["date_format"]
        if data.get("delimiter") in DELIMITERS:
            layout.delimiter = data["delimiter"]
        layout.decimal_comma = bool(data.get("decimal_comma", False))
        layout.header_row = bool(data.get("header_row", True))
        layout.byte_order_mark = bool(data.get("byte_order_mark", True))
        return layout


def default_columns() -> list[Column]:
    return [
        Column("VendorID", "vendor_id"), Column("InvoiceNo", "invoice_number"), Column("InvoiceDate", "invoice_date"),
        Column("DueDate", "due_date"), Column("Currency", "currency"), Column("GLAccount", "gl_code"),
        Column("CostCenter", "cost_center"), Column("Description", "description"), Column("Amount", "amount"),
        Column("InvoiceTotal", "grand_total"),
    ]  # fmt: skip


def _money(value: Any, places: int) -> Decimal:
    """Rounded half away from zero, on the decimal value (2.5 yen -> 3), as the JD Edwards files round."""
    return Decimal(str(float(value))).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def _line_amounts(rows: list[dict[str, Any]]) -> dict[int, Decimal]:
    """Each row's posting amount (by position), rounded to what its currency holds. In a currency without cents (the
    yen), lines spread in cents (10.66 + 10.67 + 10.67) each rounded to the yen can be a yen off the invoice's total:
    when the lines balance in cents, the largest takes the difference (like jde.py's vouchers)."""
    out: dict[int, Decimal] = {}
    whole: dict[Any, list[int]] = {}
    for i, row in enumerate(rows):
        if row.get("amount") is None or row.get("amount") == "":
            continue
        places = currency_places(row.get("currency"))
        out[i] = _money(row["amount"], places)
        if places == 0:
            whole.setdefault(row.get("invoice_id"), []).append(i)
    for lines in whole.values():
        total = rows[lines[0]].get("grand_total")
        if total is None or total == "" or sum(_money(rows[i]["amount"], 2) for i in lines) != _money(total, 2):
            continue
        biggest = max(lines, key=lambda i: abs(out[i]))
        out[biggest] += _money(total, 0) - sum(out[i] for i in lines)
    return out


def _value(row: dict[str, Any], col: Column, layout: Layout, amount: Decimal | None = None) -> Any:
    if col.field == FIXED:
        return col.text
    value = row.get(col.field)
    if value is None or value == "":
        return ""
    if col.field in DATE_FIELDS:
        try:
            return dt.date.fromisoformat(str(value)[:10]).strftime(DATE_FORMATS[layout.date_format])
        except ValueError:
            return value
    if col.field in MONEY_FIELDS:
        places = currency_places(row.get("currency"))  # the yen has no cents
        money = amount if col.field == "amount" and amount is not None else _money(value, places)
        text = f"{money:.{places}f}"
        return text.replace(".", ",") if layout.decimal_comma else text
    return value


def build(rows: list[dict[str, Any]], layout: Layout) -> bytes:
    """``rows``: ``exports.custom_rows`` output (posting lines with their invoice's fields)."""
    out = io.StringIO()
    writer = csv.writer(out, delimiter=layout.delimiter, lineterminator="\r\n")
    if layout.header_row:
        writer.writerow([c.header for c in layout.columns])
    amounts = _line_amounts(rows)
    for i, row in enumerate(rows):
        writer.writerow([_value(row, c, layout, amounts.get(i)) for c in layout.columns])
    return out.getvalue().encode("utf-8-sig" if layout.byte_order_mark else "utf-8")
