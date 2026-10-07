"""ERP export files for approved invoices.

An export batch is a set of approved invoices handed to the ERP together. Each invoice is exported
once (the store remembers its batch), and a batch's file can be downloaded again at any time: it is
rebuilt from the approved data, so it is always identical.

Formats
-------
* ``xlsx``: a workbook with an *Invoices* sheet (one row per invoice), a *GL lines* sheet (the posting:
  one row per expense line and per recoverable-tax account) and a *By GL account* summary.
* ``csv``: one row per GL line, the shape most ERP AP-invoice imports accept.
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .memory import vendor_key
from .safe import csv_row, neutralise_sheet

FORMATS = {
    "xlsx": "Excel workbook: invoices, GL lines and a GL summary",
    "csv": "CSV: one row per GL line (for ERP import)",
}

INVOICE_COLUMNS = [
    ("batch", "Batch"), ("invoice_id", "AP Coder #"), ("vendor_id", "Vendor ID"), ("vendor_name", "Vendor"),
    ("gst_hst_registration_number", "Vendor GST/HST #"), ("qst_registration_number", "Vendor QST #"),
    ("invoice_number", "Invoice #"), ("invoice_date", "Invoice date"), ("po_number", "PO #"),
    ("payment_terms", "Terms"), ("due_date", "Due date"),
    ("currency", "Currency"), ("subtotal", "Subtotal"), ("tax_total", "Tax"), ("grand_total", "Total"),
    ("supplier_province", "Supplier province"), ("ship_to_province", "Place of supply"),
    ("reviewer", "Approved by"), ("reviewed_at", "Approved at"), ("second_reviewer", "Second approver"),
    ("file_name", "File"),
]  # fmt: skip
LINE_COLUMNS = [
    ("batch", "Batch"), ("invoice_id", "AP Coder #"), ("vendor_id", "Vendor ID"), ("vendor_name", "Vendor"),
    ("invoice_number", "Invoice #"),
    ("invoice_date", "Invoice date"), ("currency", "Currency"), ("line", "Line"), ("kind", "Type"),
    ("gl_code", "GL account"), ("gl_name", "GL name"), ("cost_center", "Cost center"),
    ("description", "Description"), ("net_amount", "Net"), ("non_recoverable_tax", "Non-recoverable tax"),
    ("amount", "Amount"),
]  # fmt: skip
MONEY_HEADERS = {"Subtotal", "Tax", "Total", "Net", "Non-recoverable tax", "Amount"}


def invoice_rows(
    invoices: list[dict[str, Any]], batch: int | str = "", vendor_ids: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    """``invoices``: store.get_invoice() dicts of approved invoices; ``vendor_ids``: ``Store.vendor_ids()``."""
    vendor_ids = vendor_ids or {}
    rows = []
    for inv in invoices:
        final = inv.get("final_output") or {}
        rows.append(
            {
                **{k: final.get(k) for k, _ in INVOICE_COLUMNS if k in final},
                "batch": batch,
                "invoice_id": inv["id"],
                "vendor_id": vendor_ids.get(vendor_key(final.get("vendor_name") or ""), ""),
                "reviewer": inv.get("reviewer"),
                "reviewed_at": (inv.get("reviewed_at") or "").replace("T", " "),
                "second_reviewer": inv.get("second_reviewer") or "",
                "file_name": inv.get("file_name"),
            }  # fmt: skip
        )
    return rows


def line_rows(
    invoices: list[dict[str, Any]], gl_names: dict[str, str] | None = None, batch: int | str = "",
    vendor_ids: dict[str, str] | None = None,
) -> list[dict[str, Any]]:  # fmt: skip
    gl_names, vendor_ids = gl_names or {}, vendor_ids or {}
    rows = []
    for inv in invoices:
        final = inv.get("final_output") or {}
        for e in final.get("gl_distribution") or []:
            rows.append(
                {
                    "batch": batch,
                    "invoice_id": inv["id"],
                    "vendor_id": vendor_ids.get(vendor_key(final.get("vendor_name") or ""), ""),
                    "vendor_name": final.get("vendor_name"),
                    "invoice_number": final.get("invoice_number"),
                    "invoice_date": final.get("invoice_date"),
                    "currency": final.get("currency"),
                    "line": e.get("line_number") or "",
                    "kind": "Expense" if e.get("kind") == "expense" else "Recoverable tax",
                    "gl_code": e.get("gl_code"),
                    "gl_name": gl_names.get(e.get("gl_code") or "", ""),
                    "cost_center": e.get("cost_center") or "",
                    "description": e.get("description") or "",
                    "net_amount": e.get("net_amount"),
                    "non_recoverable_tax": e.get("non_recoverable_tax"),
                    "amount": e.get("amount"),
                }  # fmt: skip
            )
    return rows


def gl_summary(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    totals: dict[tuple[str, str, str], float] = defaultdict(float)
    for r in lines:
        totals[(r["gl_code"] or "", r["gl_name"] or "", r["currency"] or "")] += float(r["amount"] or 0)
    return [
        {"gl_code": gl, "gl_name": name, "currency": cur, "amount": round(amount, 2)}
        for (gl, name, cur), amount in sorted(totals.items())
    ]


def build_xlsx(
    invoices: list[dict[str, Any]], gl_names: dict[str, str] | None = None, batch: int | str = "",
    vendor_ids: dict[str, str] | None = None,
) -> bytes:  # fmt: skip
    lines = line_rows(invoices, gl_names, batch, vendor_ids)
    wb = Workbook()
    _sheet(wb.active, "Invoices", INVOICE_COLUMNS, invoice_rows(invoices, batch, vendor_ids))
    _sheet(wb.create_sheet(), "GL lines", LINE_COLUMNS, lines)
    _sheet(
        wb.create_sheet(), "By GL account",
        [("gl_code", "GL account"), ("gl_name", "GL name"), ("currency", "Currency"), ("amount", "Amount")],
        gl_summary(lines),
    )  # fmt: skip
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _sheet(ws: Any, title: str, columns: list[tuple[str, str]], rows: list[dict[str, Any]]) -> None:
    ws.title = title
    ws.append([label for _, label in columns])
    for r in rows:
        ws.append([r.get(key) for key, _ in columns])
    header_fill = PatternFill("solid", fgColor="E9EEF6")
    for cell in ws[1]:
        cell.font, cell.fill, cell.alignment = Font(bold=True), header_fill, Alignment(vertical="center")
    for i, (_, label) in enumerate(columns, start=1):
        width = max([len(label)] + [len(str(r.get(columns[i - 1][0]) or "")) for r in rows[:500]])
        ws.column_dimensions[get_column_letter(i)].width = min(max(10, width + 2), 60)
        if label in MONEY_HEADERS:
            for row in ws.iter_rows(min_row=2, min_col=i, max_col=i):
                row[0].number_format = "#,##0.00"
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    neutralise_sheet(ws)  # vendor names and descriptions come from documents: never formulas


def build_csv(
    invoices: list[dict[str, Any]], gl_names: dict[str, str] | None = None, batch: int | str = "",
    vendor_ids: dict[str, str] | None = None,
) -> bytes:  # fmt: skip
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([label for _, label in LINE_COLUMNS])
    for r in line_rows(invoices, gl_names, batch, vendor_ids):
        writer.writerow(csv_row([r.get(key) if r.get(key) is not None else "" for key, _ in LINE_COLUMNS]))
    return buf.getvalue().encode("utf-8-sig")  # BOM: Excel shows accents correctly


def build(
    fmt: str, invoices: list[dict[str, Any]], gl_names: dict[str, str] | None = None, batch: int | str = "",
    vendor_ids: dict[str, str] | None = None,
) -> tuple[bytes, str, str]:  # fmt: skip
    """(file bytes, file name, mime type)."""
    name = f"ap_coder_export_{batch}" if batch != "" else "ap_coder_export"
    if fmt == "csv":
        return build_csv(invoices, gl_names, batch, vendor_ids), f"{name}.csv", "text/csv"
    mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return build_xlsx(invoices, gl_names, batch, vendor_ids), f"{name}.xlsx", mime
