"""A CSV layout of your own, for an ERP whose import expects specific columns.

One row per GL posting line (expense lines and recoverable-tax lines), with the columns, their order and
headers chosen on the Exports page. A column takes a field (invoice or line), or fixed text such as a
company code or a journal name. Dates, decimal mark and delimiter follow the ERP's expectations.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
from dataclasses import asdict, dataclass, field
from typing import Any

from .safe import csv_cell

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
        return layout


def default_columns() -> list[Column]:
    return [
        Column("VendorID", "vendor_id"), Column("InvoiceNo", "invoice_number"), Column("InvoiceDate", "invoice_date"),
        Column("DueDate", "due_date"), Column("Currency", "currency"), Column("GLAccount", "gl_code"),
        Column("CostCenter", "cost_center"), Column("Description", "description"), Column("Amount", "amount"),
        Column("InvoiceTotal", "grand_total"),
    ]  # fmt: skip


def _value(row: dict[str, Any], col: Column, layout: Layout) -> Any:
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
        text = f"{float(value):.2f}"
        return text.replace(".", ",") if layout.decimal_comma else text
    return value


def build(rows: list[dict[str, Any]], layout: Layout) -> bytes:
    """``rows``: ``exports.custom_rows`` output (posting lines with their invoice's fields)."""
    out = io.StringIO()
    writer = csv.writer(out, delimiter=layout.delimiter, lineterminator="\r\n")
    if layout.header_row:
        writer.writerow([c.header for c in layout.columns])
    for row in rows:
        writer.writerow([csv_cell(_value(row, c, layout)) for c in layout.columns])
    return out.getvalue().encode("utf-8-sig")
