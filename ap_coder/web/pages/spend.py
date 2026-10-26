"""Spend: where the money goes, from approved invoices (by month and GL category, top GL accounts, top vendors, cost
centers), and every invoice's data as an Excel workbook for pivot tables or Power BI.

The period and currency are in the address (``?start=2026-01-01&end=2026-06-30&currency=USD``); "All, in CAD"
converts at the rates in Settings → Review. The workbook is a GET download.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from ap_coder.core import ui
from ap_coder.erp import spend
from ap_coder.web.charts import SERIES, Series, bar_chart, compact, legend_items
from ap_coder.web.core import get_store, money, reference_or_none, render
from ap_coder.web.files import XLSX, download

router = APIRouter()

TOP = 10
ALL_IN_CAD = "All, in CAD"
UNCATEGORISED = "Uncategorised"
OTHER = "Other"


def _date(text: str | None, default: dt.date) -> tuple[dt.date, bool]:
    """(the date, whether ``text`` was a valid date or empty)."""
    if not text:
        return default, True
    try:
        return dt.date.fromisoformat(str(text)[:10]), True
    except ValueError:
        return default, False


def ranked(rows: list[tuple[str, float, int]], names: dict[str, str], total: float) -> list[dict[str, Any]]:
    """The top rows of a breakdown: key, its name, invoices, spend and share of the total."""
    return [
        {
            "key": key or "—",
            "name": names.get(key, ""),
            "invoices": n,
            "amount": money(amount),
            "share": f"{amount / total:.0%}" if total else "",
        }
        for key, amount, n in rows[:TOP]
    ]


def category_colors(categories: list[str]) -> dict[str, str]:
    """A colour per GL category in a fixed order (the order of the GL accounts list, not by spend), so a category
    keeps its colour whatever the period; past the palette the rest are folded into "Other"."""
    keep = len(SERIES) - 1
    colors = {c: SERIES[i] for i, c in enumerate(categories[:keep])}
    colors[OTHER] = SERIES[keep]
    return colors


def month_chart(data: list[dict[str, Any]], months: list[str], order: list[str], currency: str) -> tuple[Any, list]:
    """The stacked monthly chart (one segment per GL category) and its legend rows."""
    known = [c for c in order if c != UNCATEGORISED] + [UNCATEGORISED]
    colors = category_colors(known)
    totals: dict[str, dict[str, float]] = {}
    for row in data:
        name = row["category"] if row["category"] in colors else OTHER
        totals.setdefault(name, {})
        totals[name][row["month"]] = totals[name].get(row["month"], 0.0) + row["amount"]
    shown = [c for c in [*colors] if c in totals]
    series = [Series(c, [round(totals[c].get(m, 0.0), 2) or None for m in months], colors[c]) for c in shown]
    grand = sum(sum(v.values()) for v in totals.values())
    chart = bar_chart(
        months,
        series,
        title=f"Spend by month, {currency}",
        desc=f"Spend from approved invoices by month and GL category in {currency}: {grand:,.2f} over "
        f"{ui.plural(len(months), 'month')}, {months[0]} to {months[-1]}.",
        mode="stacked",
        y_format=compact,
        value_format=lambda v: f"{v:,.2f} {currency}",
        legend=False,
        width=1040,
        height=280,
    )
    return chart, legend_items(series)


@router.get("/spend", response_class=HTMLResponse)
def spend_page(request: Request) -> HTMLResponse:
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


@router.get("/spend/data.xlsx")
def spend_workbook(request: Request) -> Response:
    """Every invoice, its lines and its GL posting, as three Excel tables that join on AP Coder #."""
    store = get_store(request)
    return download(spend.workbook(store, reference_or_none(store)), f"ap_coder_data_{dt.date.today()}.xlsx", XLSX)
