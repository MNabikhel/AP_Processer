"""The big reports as they were before they were made light for a year of invoices (D-156): every invoice, PO or
event they needed read and decoded on each call. The tests check the reports' results against these on the same
data; the bodies are the old code as it was (only renamed)."""

from __future__ import annotations

import datetime as dt
import io
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse

from ap_coder.coding.po import CLOSED, billed_by_line, po_label
from ap_coder.coding.tax import PLACE_OF_SUPPLY
from ap_coder.core import ui
from ap_coder.core.reference_data import ReferenceData
from ap_coder.core.safe import neutralise_sheet, xlsx_row
from ap_coder.core.store import APPROVED, FAILED, Store
from ap_coder.erp import spend
from ap_coder.erp.accruals import RECEIVED, Accrual
from ap_coder.erp.exports import distribution
from ap_coder.erp.spend import INVOICE_COLUMNS, accounts_by_code
from ap_coder.web.core import get_store, money, reference_or_none, render
from ap_coder.web.pages.spend import ALL_IN_CAD, _date, month_chart, ranked


def spend_page(request: Request) -> HTMLResponse:
    """``GET /spend``: every cost line of the period a Python object (``spend.parts``), totalled in Python."""
    store = get_store(request)
    reference = reference_or_none(store)
    q = request.query_params
    default_start, default_end = spend.default_period()
    start, start_ok = _date(q.get("start"), default_start)
    end, end_ok = _date(q.get("end"), default_end)
    rows = spend.parts(store, start, end) if start <= end else []
    currencies = spend.currencies(rows) or ["CAD"]
    rates = store.fx_rates()
    if len(currencies) > 1 and all(c in rates for c in currencies):
        currencies = [ALL_IN_CAD, *currencies]
    currency = q.get("currency") or currencies[0]
    if currency not in currencies:
        currency = currencies[0]
    context: dict[str, Any] = {
        "start": start,
        "end": end,
        "bad_date": not (start_ok and end_ok),
        "currencies": currencies,
        "currency": currency,
        "backwards": start > end,
        "rates_note": "",
    }
    shown_currency = currency
    if currency == ALL_IN_CAD:
        rows = spend.in_cad(rows, rates)
        shown_currency = "CAD"
        context["rates_note"] = (
            "All currencies in CAD at the rates in Settings → Review ("
            + ", ".join(f"{c} {r:g}" for c, r in sorted(rates.items()) if c != "CAD")
            + "): an estimate."
        )
    else:
        rows = [r for r in rows if r.currency == currency]
    context["shown_currency"] = shown_currency
    if not rows:
        return render(request, "spend.html", "spend", rows=False, **context)

    total = round(sum(r.amount for r in rows), 2)
    invoices = {r.invoice_id for r in rows}
    vendors = spend.total_by(rows, "vendor", shown_currency)
    months = sorted({r.month for r in rows})
    tiles = [
        ("Spend", f"{total:,.0f} {shown_currency}", "blue", "net of recoverable tax"),
        ("Invoices", f"{len(invoices):,}", "violet", f"average {money(total / len(invoices))}"),
        ("Vendors", f"{len(vendors):,}", "green", f"top one {vendors[0][1] / total:.0%} of spend" if total else ""),
        ("Per month", money(total / len(months)), "amber", f"over {ui.plural(len(months), 'month')} with invoices"),
    ]
    accounts = spend.accounts_by_code(reference)
    names = {code: row.get("description", "") for code, row in accounts.items()}
    category = {code: row.get("category", "") for code, row in accounts.items()}
    order = list(dict.fromkeys(c for c in category.values() if c))
    data = spend.by_month_and_category(rows, shown_currency, category)
    chart, legend = month_chart(data, months, order, shown_currency)
    centers = spend.total_by(rows, "cost_center", shown_currency)
    cc_names = {code: row.get("description", "") for code, row in spend.accounts_by_code(reference, True).items()}
    return render(
        request,
        "spend.html",
        "spend",
        rows=True,
        tiles=tiles,
        chart=chart,
        legend=legend,
        month_rows=[{"month": d["month"], "category": d["category"], "amount": money(d["amount"])} for d in data],
        gl_rows=ranked(spend.total_by(rows, "gl_code", shown_currency), names, total),
        vendor_rows=ranked(vendors, {}, total),
        cc_rows=ranked(centers, cc_names, total) if any(key for key, _, _ in centers) else [],
        **context,
    )


def one_person(store: Store, first: str, last: str, limit: float, rates: dict[str, float]) -> list[dict[str, Any]]:
    """``controls.one_person``: every approved invoice's coding decoded and checked."""
    return [i for i in store.invoice_columns(("id", "reviewed_at", "second_reviewer", "final_output"), APPROVED)
            if not i["second_reviewer"] and first <= (i["reviewed_at"] or "") < last
            and store.second_approval_reason(i["final_output"] or {}, limit, rates=rates)]  # fmt: skip


def received_not_invoiced(store: Store, period_end: dt.date) -> list[Accrual]:
    """``accruals._received_not_invoiced``: each PO and its invoices read in turn, each line of the vendor's invoices
    matched again for every PO of the vendor."""
    out = []
    end = period_end.isoformat()

    def by_end(invoices: list[dict]) -> list[dict]:
        return [i for i in invoices if str(i.get("invoice_date") or i["coding"].get("invoice_date") or "") <= end]

    without_po: dict[str, list[dict]] = {}  # by vendor: read once, not once for each of its POs
    for po in store.purchase_orders():
        if po["status"] == CLOSED or not po["received_lines"]:
            continue
        full = store.purchase_order(po["po_key"])
        if full is None:
            continue
        # Returns are assumed to be reflected in the ERP's received quantity, so credits do not count here;
        # the vendor's invoices that quote no PO may still bill these lines ("PO-90155" in a description).
        # Invoices dated after the period end do not count: the goods were received, not yet invoiced, by then.
        vendor = full["vendor_key"]
        if vendor not in without_po:
            without_po[vendor] = by_end(store.vendor_invoices_without_po(vendor))
        invoices = by_end(store.po_invoices(po["po_key"])) + without_po[vendor]
        billed = billed_by_line(full, invoices, positive_only=True)
        for li in full["lines"]:
            if li["received_quantity"] is None or li.get("amount_only"):
                continue
            qty = li["received_quantity"] - billed.get(li["line_number"], 0.0)
            if qty <= 1e-6:
                continue
            out.append(
                Accrual(
                    RECEIVED,
                    full["vendor_name"],
                    f"{po_label(full['po_number'])} line {li['line_number']}",
                    li["description"],
                    li["gl_code"],
                    li["cost_center"],
                    round(qty * li["unit_price"], 2),
                    note=f"{qty:g} received and not invoiced at {li['unit_price']:,.2f}",
                )  # fmt: skip
            )
    return out


def spend_workbook(store: Store, reference: ReferenceData | None) -> bytes:
    """``spend.workbook``: every invoice read at once, the workbook built in memory (openpyxl's normal mode)."""
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
    treatments = store.tax_treatments()
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
        # the ship-to printed, else the place of supply worked out when it was coded (pipeline)
        merged["ship_to_province"] = doc.get("ship_to_province") or doc.get(PLACE_OF_SUPPLY) or ""
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
        for e in distribution(doc, treatments):
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


def workbook_contents(data: bytes) -> list[dict[str, Any]]:
    """What an Excel workbook holds, sheet by sheet, to compare two: each cell's value, type and quote prefix, the
    columns' widths, the frozen panes and the tables."""
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data))
    return [
        {
            "title": ws.title,
            "cells": [[(c.value, c.data_type, c.quotePrefix) for c in row] for row in ws.iter_rows()],
            "widths": {k: d.width for k, d in ws.column_dimensions.items()},
            "frozen": ws.freeze_panes,
            "tables": [
                (
                    t.displayName,
                    t.ref,
                    t.tableStyleInfo.name,
                    t.tableStyleInfo.showRowStripes,
                    t.autoFilter.ref if t.autoFilter else None,
                    [(c.id, c.name) for c in t.tableColumns],
                )
                for t in ws.tables.values()
            ],
        }
        for ws in wb.worksheets
    ]
