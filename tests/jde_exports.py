"""JD Edwards' grid exports as they come, with invented values: one sheet "Sheet1", the header in row 1, nearly every
cell text, an empty text field a lone space, numbers and dates real, an empty amount an empty cell.

``supplier_master``: the supplier master's grid (10 columns). ``voucher_headers``: Supplier Ledger Inquiry's grid, one
row per pay item, a totals row last with only the amounts filled.
"""

from __future__ import annotations

import datetime as dt
import io
from typing import Any

import openpyxl

from ap_coder.web.files import read_sheet

BLANK = " "
SUPPLIER_HEAD = ["Address Number", "Alpha Name", "Individual Tax ID", "Long Address", "Industry Class", "Sch Typ",
                 "C M", "Status", "Hold Payment Code", "Hold Orders Code"]  # fmt: skip
VOUCHER_HEAD = ["Document Number", "Doc Type", "Document Type", "Doc Co", "Invoice Date", "G/L Date", "Due Date",
                "Gross Amount", "Open Amount", "Foreign Amount", "Foreign Open", "Pay Stat", "Pay Status Code",
                "Supplier Number", "Supplier Number Desc", "Invoice Number", "Co", "Batch Number", "Batch Type",
                "Batch Type Desc", "Batch Date", "Purchase Order", "PO Doc Type", "Pay Item", "Curr Code",
                "Business Unit", "G/L Offset"]  # fmt: skip
AMOUNTS = ("Gross Amount", "Open Amount", "Foreign Amount", "Foreign Open")  # an empty one is an empty cell


def book(head: list[str], rows: list[dict[str, Any]]) -> bytes:
    """The workbook of ``rows`` under ``head``: a field a row does not give is blank as the export writes it."""
    made = openpyxl.Workbook()
    sheet = made.active
    sheet.title = "Sheet1"
    sheet.append(head)
    for row in rows:
        sheet.append([row.get(h, None if h in AMOUNTS else BLANK) for h in head])
    raw = io.BytesIO()
    made.save(raw)
    return raw.getvalue()


def supplier(number: int, name: str, hold: str = "N", tax: str = BLANK, long: str = BLANK) -> dict[str, Any]:
    return {"Address Number": number, "Alpha Name": name, "Individual Tax ID": tax, "Long Address": long,
            "Sch Typ": "V", "Hold Payment Code": hold}  # fmt: skip


def supplier_master(rows: list[dict[str, Any]]) -> bytes:
    return book(SUPPLIER_HEAD, rows)


def pay_item(document: int, item: str, gross: float, supplier_number: int, name: str, invoice: str, *,
             foreign: float | None = None, currency: str = "CAD", order: str = BLANK, batch: int = 5550001,
             company: str = "08801", kind: str = "PV", date: dt.datetime | None = None) -> dict[str, Any]:  # fmt: skip
    """One pay item of a voucher (the supplier's name cut at 30 characters, as the grid shows it)."""
    day = date or dt.datetime(2026, 3, 4)
    return {"Document Number": document, "Doc Type": kind, "Document Type": "Voucher", "Doc Co": company,
            "Invoice Date": day, "G/L Date": day, "Due Date": day + dt.timedelta(days=30), "Gross Amount": gross,
            "Open Amount": gross, "Foreign Amount": foreign, "Pay Stat": "A", "Pay Status Code": "Approved for Payment",
            "Supplier Number": supplier_number, "Supplier Number Desc": name[:30], "Invoice Number": invoice,
            "Co": company, "Batch Number": batch, "Batch Type": "V", "Batch Type Desc": "Voucher Entry",
            "Batch Date": day, "Purchase Order": order, "PO Doc Type": "OP" if order != BLANK else BLANK,
            "Pay Item": item, "Curr Code": currency, "Business Unit": "  0880133142"}  # fmt: skip


def voucher_headers(items: list[dict[str, Any]], head: list[str] | None = None) -> bytes:
    """The pay items and the grid's totals row: every cell empty but the gross and open amounts added up."""
    totals = {h: None for h in VOUCHER_HEAD}
    totals["Gross Amount"] = round(sum(r["Gross Amount"] for r in items), 2)
    totals["Open Amount"] = round(sum(r["Open Amount"] or 0 for r in items), 2)
    return book(head or VOUCHER_HEAD, [*items, totals])


def records(name: str, content: bytes) -> tuple[list[str], list[dict[str, Any]]]:
    """(the columns, the rows) of an export as an import page reads it (``web.files.read_sheet``)."""
    frame, _sheets = read_sheet(name, content)
    return list(frame.columns), frame.to_dict("records")
