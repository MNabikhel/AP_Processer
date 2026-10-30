"""Month-end: the accruals schedule, costs incurred by the period end that are not in the ERP yet.
``GET /month-end?end=YYYY-MM-DD``; the schedule downloads as a CSV (``GET /month-end/accruals.csv?end=``,
``accruals.to_csv``)."""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse, Response

from ap_coder.core import ui
from ap_coder.core.reference_data import UNASSIGNED, ReferenceData, short_name
from ap_coder.erp import accruals
from ap_coder.web.core import address_day, get_store, money, page_router, reference_or_none, render
from ap_coder.web.files import download
from ap_coder.web.invoice_view import NEEDS_CC, NEEDS_GL

router = page_router()  # each plain handler: one connection a request

TITLES = {
    accruals.RECEIVED: "Received, not invoiced",
    accruals.NOT_IN_ERP: "Invoices not in the ERP yet",
    accruals.RECURRING: "Expected recurring invoices",
}
_ROW_ID = re.compile(r"\s*\(#\d+\)$")  # "Invoice SIL-4471 (#4)": the row id stays in the CSV, not on screen


def gl_cell(reference: ReferenceData | None, code: str) -> dict[str, Any]:
    """A GL account for display: its code (UNASSIGNED reads as "Needs an account"), its short name."""
    row = reference.chart_of_accounts.get(code) if reference and code else None
    return {
        "code": NEEDS_GL if code == UNASSIGNED else (code or "—"),
        "name": short_name((row or {}).get("description", "")),
        "todo": code == UNASSIGNED,
    }


def tile_text(n: int, by_currency: dict[str, float], rates: dict[str, float]) -> tuple[str, str]:
    """(the main figure, the hint) of one source: CAD first; other currencies said, with a CAD estimate."""
    main = "CAD" if "CAD" in by_currency or not by_currency else sorted(by_currency)[0]
    rest = {c: t for c, t in sorted(by_currency.items()) if c != main}
    others = " · ".join(f"{money(t)} {c}" for c, t in rest.items())
    if rest and main == "CAD" and all(c in rates for c in rest):
        others += f" (≈ {money(sum(t * rates[c] for c, t in rest.items()))} CAD)"
    return f"{money(by_currency.get(main, 0))} {main}", ui.plural(n, "line") + (f" · plus {others}" if others else "")


@router.get("/month-end", response_class=HTMLResponse)
def month_end_page(request: Request, end: str = "") -> HTMLResponse:
    store = get_store(request)
    period_end = address_day(end, accruals.default_period_end())
    no_date: list[accruals.Accrual] = []  # the invoices with no date: read with the accruals, not again
    left_out: list[accruals.Accrual] = []  # expected recurring invoices whose received PO lines are accrued instead
    items = accruals.build(store, period_end, no_date, left_out)
    rates = store.fx_rates()
    tiles = []
    for source, (n, by_currency) in accruals.summary(items).items():
        value, hint = tile_text(n, by_currency, rates)
        tiles.append((TITLES[source], value, hint))
    reference = reference_or_none(store) if items or no_date else None
    flagged: dict[str, float] = {}  # dated over a year before the period end: accrued, flagged (B-474)
    for a in items:
        if a.old:
            flagged[a.currency or "CAD"] = round(flagged.get(a.currency or "CAD", 0.0) + a.amount, 2)
    old_invoices = len({a.reference for a in items if a.old})
    currencies, by_account = accruals.by_gl_columns(items)
    by_gl = [{"gl": gl_cell(reference, gl), "totals": totals} for gl, totals in by_account]
    half = (len(by_gl) + 1) // 2
    groups = []
    for source in accruals.SOURCES:
        rows = [
            {
                "vendor": a.vendor,
                "reference": _ROW_ID.sub("", a.reference),
                "description": a.description,
                "gl": gl_cell(reference, a.gl_code),
                "cost_center": (NEEDS_CC if a.cost_center == UNASSIGNED else a.cost_center) if a.cost_center else "",
                "amount": a.amount,
                "currency": a.currency,
                "proposed": accruals.PROPOSED in a.note,
                "exported_late": accruals.EXPORTED_LATE in a.note,
                "old": a.old,
            }
            for a in items
            if a.source == source
        ]
        if rows:
            groups.append({"title": TITLES[source], "rows": rows})
    # Nothing to accrue: what is true, not "already in the ERP" when nothing was ever imported (B-466).
    empty = (
        "Nothing to accrue yet",
        "No invoices or purchase orders yet. Invoices you import show here until they are exported to the ERP.",
    )
    if store.has_invoices() or store.has_purchase_orders():
        empty = ("Nothing to accrue",
                 f"Every invoice dated by {period_end.isoformat()} has been exported, no goods received wait for an "
                 "invoice, and no regular bill is overdue.")  # fmt: skip
    return render(
        request,
        "month_end.html",
        "month_end",
        end=period_end.isoformat(),
        tiles=tiles,
        old_period=period_end < dt.date.today() - dt.timedelta(days=31),
        items=bool(items),
        empty=empty,
        currencies=currencies,
        no_date=[
            {
                "vendor": a.vendor,
                "reference": _ROW_ID.sub("", a.reference),
                "amount": a.amount,
                "currency": a.currency,
            }
            for a in no_date
        ],
        left_out=[
            {"vendor": a.vendor, "description": a.description, "amount": a.amount, "currency": a.currency}
            for a in left_out
        ],
        flagged=" · ".join(f"{money(t)} {c}" for c, t in sorted(flagged.items())),
        old_invoices=old_invoices,
        gl_columns=[by_gl[:half], by_gl[half:]] if len(by_gl) > 4 else [by_gl],
        groups=groups,
        inbox_art=ui.EMPTY_INBOX_SVG,
    )


@router.get("/month-end/accruals.csv")
def accruals_csv(request: Request, end: str = "") -> Response:
    period_end = address_day(end, accruals.default_period_end())
    items = accruals.build(get_store(request), period_end)
    return download(accruals.to_csv(items, period_end), f"accruals_{period_end}.csv", "text/csv")
