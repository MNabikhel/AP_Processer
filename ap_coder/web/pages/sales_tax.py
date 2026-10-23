"""Sales tax: the GST/HST and QST to claim back for a period, the claims to check first, and PST / QST possibly to
self-assess. ``GET /sales-tax?start=&end=&show=risky|all``; the claims and the self-assessment list download as CSVs
(``taxreturn.to_csv`` / ``taxreturn.self_assessment_csv``)."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from ap_coder.coding import taxreturn
from ap_coder.core import ui
from ap_coder.web.core import get_store, money, render
from ap_coder.web.files import download
from ap_coder.web.pages.month_end import day

router = APIRouter()

ISSUE_TONES = {
    taxreturn.NO_GST_NUMBER: "err",
    taxreturn.NO_QST_NUMBER: "err",
    taxreturn.GST_CHECK_DIGIT: "err",
    taxreturn.GST_NOT_PRINTED: "warn",
    taxreturn.QST_NOT_PRINTED: "warn",
    taxreturn.FOREIGN: "warn",
    taxreturn.NOT_EXPORTED: "info",
    taxreturn.MEALS: "info",
}


def amounts(by_currency: dict[str, float], rates: dict[str, float]) -> tuple[str, str]:
    """(the CAD total, the other currencies as a hint, with a CAD estimate when Settings has the rate)."""
    others = {c: t for c, t in sorted(by_currency.items()) if c != "CAD"}
    if not others:
        return f"{money(by_currency.get('CAD', 0))} CAD", ""
    text = " · ".join(f"{money(t)} {c}" for c, t in others.items())
    if all(c in rates for c in others):
        estimate = sum(t * rates[c] for c, t in others.items())
        return f"{money(by_currency.get('CAD', 0))} CAD", f"plus {text} (≈ {money(estimate)} CAD)"
    return f"{money(by_currency.get('CAD', 0))} CAD", f"plus {text} to convert"


def period(start: str, end: str) -> tuple[dt.date, dt.date]:
    default_start, default_end = taxreturn.default_period()
    return day(start, default_start), day(end, default_end)


@router.get("/sales-tax", response_class=HTMLResponse)
def sales_tax_page(request: Request, start: str = "", end: str = "", show: str = "") -> HTMLResponse:
    store = get_store(request)
    first, last = period(start, end)
    context = {"start": first.isoformat(), "end": last.isoformat(), "backwards": first > last}
    if first > last:
        return render(request, "sales_tax.html", "sales_tax", **context)
    report = taxreturn.build(store, first, last)
    totals, at_risk, rates = report.totals(), report.at_risk(), store.fx_rates()
    itc, itc_hint = amounts(totals[taxreturn.ITC], rates)
    itr, itr_hint = amounts(totals[taxreturn.ITR], rates)
    invoices = len({c.invoice_id for c in report.claims})
    risky = len({c.invoice_id for c in at_risk})
    tiles = [
        ("GST/HST to claim", itc, "ITCs · " + (itc_hint or ui.plural(invoices, "invoice"))),
        ("QST to claim", itr, "ITRs" + (f" · {itr_hint}" if itr_hint else "")),
        (
            "Claims to check",
            risky,
            (("invoice" if risky == 1 else "invoices") + " with something to check") if risky else "nothing to check",
        ),
        ("Not approved yet", report.not_approved, "dated in the period, not claimed"),
    ]
    only_risky = (show or ("risky" if at_risk else "all")) == "risky"
    items = taxreturn.self_assessment(store, first, last)
    sa_totals: dict[tuple[str, str, str], list[float]] = {}
    for s in items:
        entry = sa_totals.setdefault((s.province, s.tax_type, s.currency), [0.0, 0])
        entry[0] += s.estimate
        entry[1] += 1
    return render(
        request,
        "sales_tax.html",
        "sales_tax",
        **context,
        report=report,
        tiles=tiles,
        by_type=report.by_type(),
        shown=at_risk if only_risky else report.claims,
        only_risky=only_risky,
        risky=risky,
        tones=ISSUE_TONES,
        self_items=items,
        self_tiles=[
            (f"{tax} {prov}", f"{money(total)} {cur}", ui.plural(int(n), "invoice") + ", estimate")
            for (prov, tax, cur), (total, n) in sorted(sa_totals.items())
        ],
    )


@router.get("/sales-tax/claims.csv")
def claims_csv(request: Request, start: str = "", end: str = "") -> Response:
    first, last = period(start, end)
    if first > last:
        raise HTTPException(status_code=400, detail="The start date is after the end date.")
    report = taxreturn.build(get_store(request), first, last)
    return download(taxreturn.to_csv(report), f"sales_tax_{first}_{last}.csv", "text/csv")


@router.get("/sales-tax/self-assessment.csv")
def self_assessment_csv(request: Request, start: str = "", end: str = "") -> Response:
    first, last = period(start, end)
    if first > last:
        raise HTTPException(status_code=400, detail="The start date is after the end date.")
    items = taxreturn.self_assessment(get_store(request), first, last)
    return download(
        taxreturn.self_assessment_csv(items, first, last), f"self_assessment_{first}_{last}.csv", "text/csv"
    )
