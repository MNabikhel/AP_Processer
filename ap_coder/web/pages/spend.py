"""Spend: where the money goes, from approved invoices (by month and GL category, top GL accounts, top vendors, cost
centers), and every invoice's data as an Excel workbook for pivot tables or Power BI.

The period and currency are in the address (``?start=2026-01-01&end=2026-06-30&currency=USD``); "All, in CAD"
converts at the rates in Settings → Review. The workbook is a GET download, made as it is asked for on a laptop; on
the company server it is made in the background (``POST /spend/data``, the job ``spend-data``: a year of invoices takes
minutes) and the download is the one made last (D-156).
"""

from __future__ import annotations

import contextlib
import datetime as dt
import os
import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from ap_coder.core import ui
from ap_coder.erp import spend
from ap_coder.web import identity, roles
from ap_coder.web.charts import SERIES, Series, bar_chart, compact, legend_items
from ap_coder.web.core import get_store, jobs, message, money, page_router, reference_or_none, render, server_mode
from ap_coder.web.files import XLSX, download
from ap_coder.web.jobs import ERROR, RUNNING, Job

router = page_router()  # each plain handler: one connection a request

TOP = 10
ALL_IN_CAD = "All, in CAD"
WORKBOOK_JOB = "spend-data"  # the data workbook made in the background on the company server (D-156)
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
    currencies = (spend.spend_currencies(store, start, end) if start <= end else []) or ["CAD"]
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
        "workbook": workbook_state(request),
    }
    shown_currency = currency
    if currency == ALL_IN_CAD:
        shown_currency = "CAD"
        context["rates_note"] = (
            "All currencies in CAD at the rates in Settings → Review ("
            + ", ".join(f"{c} {r:g}" for c, r in sorted(rates.items()) if c != "CAD")
            + "): an estimate."
        )
    context["shown_currency"] = shown_currency
    accounts = spend.accounts_by_code(reference)
    category = {code: row.get("category", "") for code, row in accounts.items()}
    # Summed by SQLite, not every cost line of the period read (D-156).
    found = spend.figures(store, start, end, shown_currency, category, rates if currency == ALL_IN_CAD else None) \
        if start <= end else {"lines": False}  # fmt: skip
    if not found["lines"]:
        return render(request, "spend.html", "spend", rows=False, **context)

    total = found["total"]
    vendors = found["by"]["vendor"]
    months = found["months"]
    tiles = [
        ("Spend", f"{total:,.0f} {shown_currency}", "blue", "net of recoverable tax"),
        ("Invoices", f"{found['invoices']:,}", "violet", f"average {money(total / found['invoices'])}"),
        ("Vendors", f"{len(vendors):,}", "green", f"top one {vendors[0][1] / total:.0%} of spend" if total else ""),
        ("Per month", money(total / len(months)), "amber", f"over {ui.plural(len(months), 'month')} with invoices"),
    ]
    names = {code: row.get("description", "") for code, row in accounts.items()}
    order = list(dict.fromkeys(c for c in category.values() if c))
    data = found["by_month"]
    chart, legend = month_chart(data, months, order, shown_currency)
    centers = found["by"]["cost_center"]
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
        gl_rows=ranked(found["by"]["gl_code"], names, total),
        vendor_rows=ranked(vendors, {}, total),
        cc_rows=ranked(centers, cc_names, total) if any(key for key, _, _ in centers) else [],
        **context,
    )


def _workbooks(store: Any) -> Path:
    """Where the company server keeps the data workbooks (in the data folder, not backed up; a restore empties it)."""
    return store.path.parent / ".cache" / "downloads"


def workbook_path(store: Any) -> Path | None:
    """The data workbook the company server made last; None before the first. Each is made under a name of its own:
    on Windows a workbook being downloaded can be neither replaced nor deleted."""
    found = sorted(_workbooks(store).glob("ap_coder_data.*.xlsx"))
    return found[-1] if found else None


def workbook_state(request: Request) -> dict[str, Any] | None:
    """The data workbook on the company server: when the last one was made ("" for none yet), how far the one being
    made got ("" when none is), and why the last one could not be made ("" when it was); None on a laptop (made as it
    is asked for)."""
    if not server_mode(request):
        return None
    path = workbook_path(get_store(request))
    made = f"{dt.datetime.fromtimestamp(path.stat().st_mtime):%Y-%m-%d %H:%M}" if path is not None else ""
    snap = jobs(request).get(WORKBOOK_JOB).snapshot()
    note = (snap.get("note") or "Starting…") if snap["state"] == RUNNING else ""
    failed = (snap.get("error") or "it stopped") if snap["state"] == ERROR else ""
    me = identity.person()  # Read-only changes nothing: a Clerk or above makes it (D-98)
    may = me is None or roles.allowed(me.role, roles.RULES[("POST", "/spend/data")])
    return {"made": made, "making": note, "failed": failed, "status": f"/jobs/{WORKBOOK_JOB}", "may_make": may}


@router.get("/spend/data.xlsx")
def spend_workbook(request: Request) -> Response:
    """Every invoice, its lines and its GL posting, as three Excel tables that join on AP Coder #: on a laptop made
    now; on the company server the one made last (``make_workbook``)."""
    store = get_store(request)
    if not server_mode(request):
        return download(spend.workbook(store, reference_or_none(store)), f"ap_coder_data_{dt.date.today()}.xlsx",
                        XLSX)  # fmt: skip
    path = workbook_path(store)
    if path is None:
        raise HTTPException(status_code=404, detail="No workbook has been made yet: a Clerk makes it on Spend with "
                                                    "Make the Excel workbook.")  # fmt: skip
    made = dt.datetime.fromtimestamp(path.stat().st_mtime)
    return FileResponse(path, media_type=XLSX, filename=f"ap_coder_data_{made:%Y-%m-%d_%H%M}.xlsx",
                        headers={"Cache-Control": "no-store"})  # fmt: skip


@router.post("/spend/data")
def make_workbook(request: Request) -> JSONResponse:
    """Make the data workbook in the background (the company server: one at a time, for everyone), a batch of
    invoices at a time, written beside the last one and given a name of its own once whole; the older ones are then
    removed (one being downloaded on Windows: the next time)."""
    store = get_store(request)
    reference = reference_or_none(store)
    folder = _workbooks(store)

    def work(job: Job) -> dict[str, Any]:
        def step(done: int, total: int) -> None:
            job.step(done, total, f"{done:,} of {ui.plural(total, 'invoice')} written")

        folder.mkdir(parents=True, exist_ok=True)
        part = folder / f"ap_coder_data.{os.getpid()}.part"
        try:
            with store.session():
                spend.write_workbook(store, reference, part, step)
            made = folder / f"ap_coder_data.{time.time_ns()}.xlsx"  # (the newest sorts last)
            os.replace(part, made)
        finally:
            part.unlink(missing_ok=True)
        for older in folder.glob("ap_coder_data.*.xlsx"):
            if older != made:
                with contextlib.suppress(OSError):
                    older.unlink()
        return {"made": f"{dt.datetime.now():%Y-%m-%d %H:%M}"}

    if not jobs(request).get(WORKBOOK_JOB).start(work, note="Starting…"):
        return message("The workbook is being made already: it is ready in a few minutes.", "info")
    return JSONResponse({"ok": True, "job": WORKBOOK_JOB, "status": f"/jobs/{WORKBOOK_JOB}"})
