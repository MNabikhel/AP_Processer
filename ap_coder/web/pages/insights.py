"""Insights: the business case. Volume, the straight-through rate (approved exactly as coded), the time it saves,
the cloud cost, AP operations (queue age, days to approve, discounts), what the checks catch, approvals by week and
the projection at your monthly volume, from ``ap_coder.insights`` (the same figures as the one-page report).

The assumptions behind the figures are a POST (saved with who changed them); the one-page business case is a GET
download.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import fields
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from ap_coder import ui
from ap_coder.insights import Assumptions, compute, operations, report_html
from ap_coder.web.charts import OK_GREEN, SERIES, WARN_AMBER, Series, bar_chart
from ap_coder.web.core import done, get_store, refuse, render, reviewer

router = APIRouter()

LABELS = {
    "manual_minutes": ("Manual processing today (min / invoice)", 0.5),
    "clean_minutes": ("Review when the AI is right (min)", 0.5),
    "changed_minutes": ("Review when it needs corrections (min)", 0.5),
    "hourly_cost": ("Loaded AP hourly cost (CAD)", 1.0),
    "monthly_volume": ("Invoices per month", 50),
    "di_usd_per_1000_pages": ("Document Intelligence, USD per 1,000 pages", 0.5),
    "aoai_usd_per_m_input": ("Azure OpenAI input, USD per million tokens", 0.05),
    "aoai_usd_per_m_cached": ("Azure OpenAI cached input, USD per million", 0.05),
    "aoai_usd_per_m_output": ("Azure OpenAI output, USD per million tokens", 0.1),
    "usd_to_cad": ("USD → CAD", 0.01),
}


def pct(v: float | None) -> str:
    return "—" if v is None else f"{v:.0%}"


def check_title(code: str) -> str:
    """A check's plain-English name ("No cost center"), as the Help page lists it; the code as a fallback."""
    from ap_coder.help import CHECKS

    found = CHECKS.get(code)
    return found.title if found else code.replace("_", " ").capitalize()


def per_currency(totals: dict[str, float]) -> str:
    """Amounts kept apart by currency ("12 CAD + 3,000 JPY"): adding yen to dollars means nothing."""
    return " + ".join(f"{t:,.0f} {c}" for c, t in totals.items()) or "0"


def kpis(s: dict[str, Any]) -> list[tuple[str, str, str, str]]:
    """The four headline figures: (label, value, tone, note)."""
    cost = s["cost_per_invoice_cad"]
    return [
        ("Invoices processed", f"{s['processed']:,}", "blue", f"{s['approved']} approved · {s['failed']} unreadable"),
        ("Approved as coded", pct(s["straight_through"]), "green",
         f"{s['approved_clean']} of {s['approved']} needed no change"),
        ("Hours saved", f"{s['hours_saved']:,.1f}", "violet", f"≈ ${s['value_saved']:,.0f} of AP time"),
        ("Cloud cost / invoice", "$0" if cost is None else f"${cost:,.3f}", "amber",
         f"Azure, measured on {ui.plural(s['cost_measured_invoices'], 'invoice')}" if cost is not None
         else "read and coded on this computer"),
    ]  # fmt: skip


def operation_kpis(ops: dict[str, Any]) -> list[tuple[str, str, str, str]]:
    median = ops["median_days_to_approve"]
    taken, taken_amount = ops["discounts_in_time"]
    missed, missed_amount = ops["discounts_missed"]
    return [
        ("Waiting", f"{ops['waiting']:,}", "blue",
         f"oldest {ui.plural(ops['oldest_days'], 'day')}" if ops["waiting"] else "queue is clear"),
        ("Days to approve", "—" if median is None else f"{median:g}", "violet", "median, from received to approved"),
        ("Approved after due", f"{ops['approved_after_due']:,}", "amber" if ops["approved_after_due"] else "green",
         f"of {ops['approved']} approved"),
        ("Discounts in time", f"{taken} / {taken + missed}", "green" if not missed else "amber",
         f"{per_currency(taken_amount)} taken · {per_currency(missed_amount)} missed" if taken + missed
         else "no discount terms yet"),
    ]  # fmt: skip


def weekly_chart(weekly: list[dict[str, Any]]) -> Any:
    weeks = [w["week"] for w in weekly]
    clean = [w["clean"] for w in weekly]
    changed = [w["changed"] for w in weekly]
    total = sum(clean) + sum(changed)
    return bar_chart(
        weeks,
        [Series("Approved as coded", clean, OK_GREEN), Series("Corrected", changed, WARN_AMBER)],
        title="Approvals by week",
        desc=f"Invoices approved each week, {sum(clean)} of {total} approved exactly as coded and {sum(changed)} "
        f"corrected, over {ui.plural(len(weeks), 'week')} from {weeks[0]} to {weeks[-1]}.",
        mode="stacked",
        y_format=lambda v: f"{v:,.0f}",
        width=1040,
        height=280,
    )


def assumption_fields(a: Assumptions) -> list[dict[str, Any]]:
    out = []
    for f in fields(Assumptions):
        label, step = LABELS[f.name]
        whole = f.name == "monthly_volume"
        value = getattr(a, f.name)
        out.append({"name": f.name, "label": label, "step": step, "value": int(value) if whole else f"{value:.2f}"})
    return out


@router.get("/insights", response_class=HTMLResponse)
def insights_page(request: Request) -> HTMLResponse:
    store = get_store(request)
    s = compute(store)
    a: Assumptions = s["assumptions"]
    if not s["processed"]:
        return render(request, "insights.html", "insights", s=s, fields=assumption_fields(a))
    ops = operations(store)
    biggest_age = max(ops["ageing"].values()) or 1
    ageing = [
        {"label": label, "n": n, "share": n / biggest_age, "late": label.startswith(("8", "15"))}
        for label, n in ops["ageing"].items()
    ]
    p = s["projection"]
    issues = [{"code": code, "title": check_title(code), "n": n} for code, n in s["top_issues"]]
    biggest_issue = issues[0]["n"] if issues else 1
    stopped = ""
    if s["duplicates_stopped"]:
        totals = " · ".join(f"{t:,.2f} {c}" for c, t in s["duplicates_stopped_total"].items())
        stopped = f"{ui.plural(s['duplicates_stopped'], 'duplicate invoice')} stopped · {totals}"
    return render(
        request,
        "insights.html",
        "insights",
        s=s,
        a=a,
        kpis=kpis(s),
        ops=ops,
        ops_kpis=operation_kpis(ops),
        ageing=ageing,
        projection=p,
        net=(p["value_saved"] - p["azure_cost"]) if p else None,
        issues=issues,
        biggest_issue=biggest_issue,
        issue_color=SERIES[0],
        attention=pct(s["needs_attention_rate"]),
        stopped=stopped,
        chart=weekly_chart(s["weekly"]) if s["weekly"] and s["approved"] else None,
        fields=assumption_fields(a),
    )


@router.get("/insights/business-case")
def insights_business_case(request: Request) -> Response:
    """The one-page business case: totals only (no vendor names, invoice amounts or file names)."""
    html = report_html(compute(get_store(request)))
    return Response(
        html.encode("utf-8"),
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="ap_coder_business_case_{dt.date.today()}.html"'},
    )


@router.post("/insights/assumptions")
async def insights_assumptions(request: Request) -> JSONResponse:
    form = await request.form()
    values: dict[str, Any] = {}
    for f in fields(Assumptions):
        label = LABELS[f.name][0]
        text = str(form.get(f.name) or "").strip().replace(",", "")
        try:
            number = int(float(text)) if f.name == "monthly_volume" else float(text)
        except (ValueError, OverflowError):  # int() of an endless number ("1e309", "inf") overflows
            return refuse(f"{label}: enter a number.")
        if number < 0 or number != number or number in (float("inf"), float("-inf")):
            return refuse(f"{label}: enter a number of 0 or more.")
        values[f.name] = number
    Assumptions(**values).save(get_store(request), actor=reviewer())
    return done(request, "/insights#assumptions", "Assumptions saved.")
