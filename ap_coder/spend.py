"""Spend analysis from approved invoices, and a full data download for Excel / Power BI.

Spend is the cost to the company: each expense line's amount plus any sales tax that is not recovered
(recoverable GST/HST/QST is left out, as it is claimed back). Credit notes reduce it. Amounts stay in
each invoice's currency; currencies are never added together.
"""

from __future__ import annotations

import datetime as dt
import io
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from .reference_data import ReferenceData
from .safe import neutralise_sheet, xlsx_row
from .store import APPROVED, FAILED, Store


@dataclass
class SpendLine:
    invoice_id: int
    month: str  # YYYY-MM of the invoice date
    vendor: str
    gl_code: str
    cost_center: str
    amount: float
    currency: str


def lines(store: Store, start: dt.date, end: dt.date) -> list[SpendLine]:
    """Expense postings of approved invoices dated from ``start`` to ``end`` (inclusive)."""
    first, last = start.isoformat(), end.isoformat()
    out = []
    for r in store.invoice_columns(("id", "final_output"), APPROVED):
        doc = r["final_output"] or {}
        date = str(doc.get("invoice_date") or "")
        if not (first <= date <= last):
            continue
        for e in doc.get("gl_distribution") or []:
            # Expense lines, and non-recoverable tax posted to its own GL account (a cost too).
            if e.get("kind") != "expense" and not e.get("non_recoverable_tax"):
                continue
            out.append(
                SpendLine(
                    r["id"],
                    date[:7],
                    str(doc.get("vendor_name") or ""),
                    str(e.get("gl_code") or ""),
                    str(e.get("cost_center") or ""),
                    float(e.get("amount") or 0),
                    str(doc.get("currency") or "CAD"),
                )  # fmt: skip
            )
    return out


def currencies(rows: list[SpendLine]) -> list[str]:
    """Currencies by total spend, largest first."""
    totals: dict[str, float] = defaultdict(float)
    for r in rows:
        totals[r.currency] += abs(r.amount)
    return sorted(totals, key=lambda c: -totals[c])


def in_cad(rows: list[SpendLine], rates: dict[str, float]) -> list[SpendLine]:
    """The lines in CAD at ``rates`` (CAD per unit); lines in a currency without a rate are left out."""
    return [
        SpendLine(
            r.invoice_id, r.month, r.vendor, r.gl_code, r.cost_center, round(r.amount * rates[r.currency], 2), "CAD"
        )
        for r in rows
        if r.currency in rates
    ]


def total_by(rows: list[SpendLine], key: str, currency: str) -> list[tuple[str, float, int]]:
    """(value of ``key``, total, invoices) for one currency, largest total first."""
    totals: dict[str, float] = defaultdict(float)
    invoices: dict[str, set[int]] = defaultdict(set)
    for r in rows:
        if r.currency == currency:
            value = getattr(r, key)
            totals[value] += r.amount
            invoices[value].add(r.invoice_id)
    return sorted(((k, round(v, 2), len(invoices[k])) for k, v in totals.items()), key=lambda x: -x[1])


def by_month_and_category(rows: list[SpendLine], currency: str, category: dict[str, str]) -> list[dict[str, Any]]:
    """[{month, category, amount}] for a stacked chart."""
    totals: dict[tuple[str, str], float] = defaultdict(float)
    for r in rows:
        if r.currency == currency:
            totals[(r.month, category.get(r.gl_code) or "Uncategorised")] += r.amount
    return [{"month": m, "category": c, "amount": round(v, 2)} for (m, c), v in sorted(totals.items())]


def default_period(today: dt.date | None = None) -> tuple[dt.date, dt.date]:
    """The last 12 months, this one included."""
    today = today or dt.date.today()
    start = dt.date(today.year - 1, today.month, 1)
    start = dt.date(start.year + (start.month == 12), start.month % 12 + 1, 1)
    return start, today


def accounts_by_code(reference: ReferenceData | None, cost_centers: bool = False) -> dict[str, dict[str, str]]:
    table = (reference.cost_centers if cost_centers else reference.chart_of_accounts) if reference else None
    return {row[table.key_column]: row for row in table.rows} if table else {}


# --- Full data download -------------------------------------------------------------------------------

INVOICE_COLUMNS = [
    ("id", "AP Coder #"), ("status", "Status"), ("vendor_name", "Vendor"), ("invoice_number", "Invoice #"),
    ("invoice_date", "Invoice date"), ("due_date", "Due date"), ("po_number", "PO #"),
    ("payment_terms", "Terms"), ("currency", "Currency"), ("subtotal", "Subtotal"), ("tax_total", "Tax"),
    ("grand_total", "Total"), ("supplier_province", "Supplier province"), ("ship_to_province", "Place of supply"),
    ("gst_hst_registration_number", "GST/HST #"), ("qst_registration_number", "QST #"),
    ("reviewer", "Approved by"), ("reviewed_at", "Approved at"), ("second_reviewer", "Second approval by"),
    ("second_reviewed_at", "Second approval at"), ("export_batch", "Export batch"), ("created_at", "Processed at"),
]  # fmt: skip


def workbook(store: Store, reference: ReferenceData | None) -> bytes:
    """Every invoice (not the unreadable ones), its lines and its posting, as an Excel workbook with three
    tables that join on "AP Coder #"."""
    from openpyxl import Workbook
    from openpyxl.worksheet.table import Table, TableStyleInfo

    accounts = accounts_by_code(reference)

    def gl_name(code: str) -> str:
        return accounts.get(code, {}).get("description", "")

    def category(code: str) -> str:
        return accounts.get(code, {}).get("category", "")

    columns = ("id", "status", "reviewer", "reviewed_at", "second_reviewer", "second_reviewed_at", "export_batch",
               "created_at", "due_date", "ai_output", "final_output")  # fmt: skip
    rows = [r for r in store.invoice_columns(columns) if r["status"] != FAILED]
    wb = Workbook()
    sheets = {
        "Invoices": [label for _, label in INVOICE_COLUMNS],
        "Lines": ["AP Coder #", "Vendor", "Invoice #", "Invoice date", "Line", "Description", "Quantity",
                  "Unit price", "Amount", "GL account", "GL name", "GL category", "Cost center", "Currency"],
        "Posting": ["AP Coder #", "Vendor", "Invoice #", "Invoice date", "Kind", "GL account", "GL name",
                    "Cost center", "Description", "Net", "Non-recoverable tax", "Amount", "Currency"],
    }  # fmt: skip
    ws_by_name = {}
    for n, (name, header) in enumerate(sheets.items()):
        ws = wb.active if n == 0 else wb.create_sheet()
        ws.title = name
        ws.append(header)
        ws_by_name[name] = ws
    for r in rows:
        doc = r["final_output"] or r["ai_output"] or {}
        merged = {**doc, **{k: r[k] for k in r if k not in ("ai_output", "final_output")}}
        ws_by_name["Invoices"].append(xlsx_row([merged.get(k) for k, _ in INVOICE_COLUMNS]))
        head = [r["id"], doc.get("vendor_name"), doc.get("invoice_number"), doc.get("invoice_date")]
        for li in doc.get("line_items") or []:
            gl = li.get("predicted_gl_code") or ""
            ws_by_name["Lines"].append(
                xlsx_row(
                    [
                        *head,
                        li.get("line_number"),
                        li.get("description"),
                        li.get("quantity"),
                        li.get("unit_price"),
                        li.get("amount"),
                        gl,
                        gl_name(gl),
                        category(gl),
                        li.get("predicted_cost_center"),
                        doc.get("currency"),
                    ]
                )
            )
        for e in doc.get("gl_distribution") or []:
            gl = e.get("gl_code") or ""
            ws_by_name["Posting"].append(
                xlsx_row(
                    [
                        *head,
                        e.get("kind"),
                        gl,
                        gl_name(gl),
                        e.get("cost_center"),
                        e.get("description"),
                        e.get("net_amount"),
                        e.get("non_recoverable_tax"),
                        e.get("amount"),
                        doc.get("currency"),
                    ]
                )
            )
    for name, ws in ws_by_name.items():
        neutralise_sheet(ws)
        last = ws.cell(row=1, column=ws.max_column).column_letter
        if ws.max_row > 1:  # an Excel table (filters, and a named source for Power BI)
            table = Table(displayName=f"{name}Table", ref=f"A1:{last}{ws.max_row}")
            table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
            ws.add_table(table)
        for col in ws.columns:
            width = max(len(str(c.value or "")) for c in list(col)[:200])
            ws.column_dimensions[col[0].column_letter].width = min(max(width + 2, 9), 48)
        ws.freeze_panes = "A2"
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
