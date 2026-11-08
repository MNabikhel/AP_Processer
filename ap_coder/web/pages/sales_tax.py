"""Sales tax: the GST/HST and QST to claim back for a period, the claims to check first, and PST / QST possibly to
self-assess. ``GET /sales-tax?start=&end=&show=risky|all&rows=&self_rows=``: each list ``ROWS_SHOWN`` rows at a time
("Show more", D-162), what the page shows worked out once for every process and settling for ``SETTLE_SECONDS`` after
a change (``shown``, D-179); the claims and the self-assessment list download as CSVs, every row, never settled
(``taxreturn.to_csv`` / ``taxreturn.self_assessment_csv``)."""

from __future__ import annotations

import datetime as dt
from dataclasses import astuple
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from ap_coder.coding import taxreturn
from ap_coder.core import ui
from ap_coder.core.store import Store
from ap_coder.web.core import address_day, get_store, money, page_router, render, shown_count
from ap_coder.web.files import download

router = page_router()  # each plain handler: one connection a request

ROWS_SHOWN = 500  # the claims, and the self-assessments, listed at first and how many more each time ("Show more")
# Seconds what the page shows may be behind a change of the invoices (D-164): the quarter's claims are read whole
# (3.5 s at 300,000 invoices), and the queue's approvals change them all day. A change of what they are worked out
# with (``taxreturn.setup_key``) shows at once; the CSVs, filed from, are worked out afresh.
SETTLE_SECONDS = 30.0
SHOWN_FORMAT = "sales-tax-1"  # what is kept for every process (``shown``); another when that changes

ISSUE_TONES = {
    taxreturn.NO_GST_NUMBER: "err",
    taxreturn.NO_QST_NUMBER: "err",
    taxreturn.GST_CHECK_DIGIT: "err",
    taxreturn.GST_NOT_PRINTED: "warn",
    taxreturn.QST_NOT_PRINTED: "warn",
    taxreturn.FOREIGN: "warn",
    taxreturn.NOT_EXPORTED: "info",
    taxreturn.MEALS: "info",
    **dict.fromkeys(taxreturn.CHARGED_IN_ERROR.values(), "err"),  # tax charged in error (B-925)
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
    return address_day(start, default_start), address_day(end, default_end)


def asked(start: str = "", end: str = "", show: str = "", rows: str = "",
          self_rows: str = "") -> tuple[dt.date, dt.date, str, int, int]:  # fmt: skip
    """What the page asks ``shown`` for from its address: the period, the list shown, how many claims and
    self-assessments. Its default view (nothing in the address), worked out at the company server's first start, is
    asked with this too (``web/refresh.py`` ``DEFAULTS``, D-200)."""
    first, last = period(start, end)
    return first, last, show, shown_count(rows, ROWS_SHOWN), shown_count(self_rows, ROWS_SHOWN)


@router.get("/sales-tax", response_class=HTMLResponse)
def sales_tax_page(request: Request, start: str = "", end: str = "", show: str = "", rows: str = "",
                   self_rows: str = "") -> HTMLResponse:  # fmt: skip
    store = get_store(request)
    first, last, show, count, self_count = asked(start, end, show, rows, self_rows)
    context = {"start": first.isoformat(), "end": last.isoformat(), "backwards": first > last}
    if first > last:
        return render(request, "sales_tax.html", "sales_tax", **context)
    view, rates = shown(store, first, last, show, count, self_count), store.fx_rates()
    itc, itc_hint = amounts(view["totals"][taxreturn.ITC], rates)
    itr, itr_hint = amounts(view["totals"][taxreturn.ITR], rates)
    risky = view["risky"]
    tiles = [
        ("GST/HST to claim", itc, "ITCs · " + (itc_hint or ui.plural(view["invoices"], "invoice"))),
        ("QST to claim", itr, "ITRs" + (f" · {itr_hint}" if itr_hint else "")),
        (
            "Claims to check",
            risky,
            (("invoice" if risky == 1 else "invoices") + " with something to check") if risky else "nothing to check",
        ),
        ("Not approved yet", view["not_approved"], "dated in the period, not claimed"),
    ]
    return render(request, "sales_tax.html", "sales_tax", **context, **view, tiles=tiles, rows=count,
                  tones=ISSUE_TONES, self_rows=self_count, step=ROWS_SHOWN)  # fmt: skip


def shown(store: Store, first: dt.date, last: dt.date, show: str, count: int, self_count: int) -> dict[str, Any]:
    """What the page shows of the period's claims (``taxreturn.build``) and self-assessments (``self_assessment``):
    their totals and counts, the first ``count`` claims of the list ``show`` picks, the first ``self_count``
    self-assessments. Worked out once for every process (D-169) and under a stream of approvals at most every
    ``SETTLE_SECONDS`` (D-164), for these dates and rows and what they are worked out with (``taxreturn.setup_key``:
    a change of the tax setup shows at once); on the company server by its background process (D-198). Shared: read
    it, don't change it."""

    def work() -> dict[str, Any]:
        report = taxreturn.build(store, first, last)
        items = taxreturn.self_assessment(store, first, last)
        at_risk = report.at_risk()
        only_risky = (show or ("risky" if at_risk else "all")) == "risky"
        listed = at_risk if only_risky else report.claims
        return {"totals": report.totals(), "invoices": len({c.invoice_id for c in report.claims}),
                "risky": len({c.invoice_id for c in at_risk}), "not_approved": report.not_approved,
                "not_recoverable": report.not_recoverable, "meals": report.meals_totals(),
                "claimed": bool(report.claims), "by_type": report.by_type(), "only_risky": only_risky,
                "shown": listed[:count], "shown_total": len(listed), "self_shown": items[:self_count],
                "self_total": len(items), "self_accrued": sum(1 for s in items if s.note),
                "self_tiles": self_tiles(items)}  # fmt: skip

    asked = (first, last, show, count, self_count, taxreturn.setup_key(store))
    return store.shared_answer("sales_tax", work, version=SHOWN_FORMAT, encode=_data, decode=_of, asked=asked,
                               settle=SETTLE_SECONDS, again=[first.isoformat(), last.isoformat(), show, count,
                                                             self_count])  # fmt: skip


_ROWS = {"shown": taxreturn.Claim, "self_shown": taxreturn.SelfAssessment}  # kept as lists of their fields
_TUPLES = ("by_type", "self_tiles")


def _data(view: dict[str, Any]) -> dict[str, Any]:
    return {**view, **{k: [list(astuple(row)) for row in view[k]] for k in _ROWS}}


def _of(data: dict[str, Any]) -> dict[str, Any]:
    """``shown`` as kept for every process (JSON) made what it was: its claims and self-assessments, and tuples."""
    return {**data, **{k: [kind(*row) for row in data[k]] for k, kind in _ROWS.items()},
            **{k: [tuple(row) for row in data[k]] for k in _TUPLES}}  # fmt: skip


def self_tiles(items: list[taxreturn.SelfAssessment]) -> list[tuple[str, str, str]]:
    """The Self-assessment tiles: the estimate to book per province, tax and currency. An item with a note (JD Edwards
    accrues it, or will once exported) is not to book from the report: left out, or it would be booked twice (B-928)."""
    totals: dict[tuple[str, str, str], list[float]] = {}
    for s in items:
        if s.note:
            continue
        entry = totals.setdefault((s.province, s.tax_type, s.currency), [0.0, 0])
        entry[0] += s.estimate
        entry[1] += 1
    return [
        (f"{tax} {prov}", f"{money(total)} {cur}", ui.plural(int(n), "invoice") + ", estimate")
        for (prov, tax, cur), (total, n) in sorted(totals.items())
    ]


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
