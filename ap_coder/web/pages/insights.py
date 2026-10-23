"""Insights: the business case. Volume, the straight-through rate (approved exactly as coded), the time it saves
(the cloud cost only in a build that may use the internet), AP operations (queue age, days to approve,
discounts), what the checks catch, approvals by week and the projection at your monthly volume, from
``ap_coder.erp.insights`` (the same figures as the one-page report).

The assumptions behind the figures are a POST (saved with who changed them); the one-page business case is a GET
download. Before ``MIN_APPROVALS`` approvals the page says when its figures come (B-515): a share of two approvals is
noise, not a business case.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import fields
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from ap_coder.core import ui
from ap_coder.core.store import APPROVED
from ap_coder.erp.insights import AS_CODED, Assumptions, compute, operations, report_html
from ap_coder.ops.offline import internet_allowed
from ap_coder.web.charts import OK_GREEN, SERIES, WARN_AMBER, Series, bar_chart
from ap_coder.web.core import done, get_store, posted_form, refuse, render, reviewer

router = APIRouter()

MIN_APPROVALS = 20  # Insights' figures show once this many invoices are approved
CLOUD_FIELDS = {"di_usd_per_1000_pages", "aoai_usd_per_m_input", "aoai_usd_per_m_cached", "aoai_usd_per_m_output",
                "usd_to_cad"}  # fmt: skip  # the cloud's prices: shown only where the internet is allowed

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
    from ap_coder.ops.help import CHECKS

    found = CHECKS.get(code)
    return found.title if found else code.replace("_", " ").capitalize()


def per_currency(totals: dict[str, float]) -> str:
    """Amounts kept apart by currency ("12 CAD + 3,000 JPY"): adding yen to dollars means nothing."""
    return " + ".join(f"{t:,.0f} {c}" for c, t in totals.items()) or "0"


def kpis(s: dict[str, Any], cloud: bool = False) -> list[tuple[str, str, str, str]]:
    """The four headline figures: (label, value, tone, note). The cloud's cost only where the internet is allowed
    (``cloud``): the offline build has none to show (B-515)."""
    cost = s["cost_per_invoice_cad"]
    last = (
        ("Cloud cost / invoice", "$0" if cost is None else f"${cost:,.3f}", "amber",
         f"Azure, measured on {ui.plural(s['cost_measured_invoices'], 'invoice')}" if cost is not None
         else "read and coded on this computer")
        if cloud
        else ("Needs attention", pct(s["needs_attention_rate"]), "amber", "of invoices, flagged by the checks")
    )  # fmt: skip
    return [
        ("Invoices processed", f"{s['processed']:,}", "blue", f"{s['approved']} approved · {s['failed']} unreadable"),
        (AS_CODED, pct(s["straight_through"]), "green",
         f"{s['approved_clean']} of {s['approved']} needed no change"),
        ("Hours saved", f"{s['hours_saved']:,.1f}", "violet", f"≈ ${s['value_saved']:,.0f} of AP time"),
        last,
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
    """The assumptions' fields; the cloud's prices are posted as they are (hidden) where the internet isn't allowed."""
    out = []
    cloud = internet_allowed()
    for f in fields(Assumptions):
        label, step = LABELS[f.name]
        whole = f.name == "monthly_volume"
        value = getattr(a, f.name)
        out.append({"name": f.name, "label": label, "step": step, "value": int(value) if whole else f"{value:.2f}",
                    "hidden": f.name in CLOUD_FIELDS and not cloud})  # fmt: skip
    return out


@router.get("/insights", response_class=HTMLResponse)
def insights_page(request: Request) -> HTMLResponse:
    store = get_store(request)
    s = compute(store)
    a: Assumptions = s["assumptions"]
    fully = len(store.list_invoices(APPROVED))  # fully approved (a second approval waiting doesn't count yet)
    if fully < MIN_APPROVALS:
        return render(request, "insights.html", "insights", s=s, fields=assumption_fields(a), early=True,
                      min_approvals=MIN_APPROVALS, fully=fully, pilot=pilot_card(store))  # fmt: skip
    cloud = internet_allowed()
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
        kpis=kpis(s, cloud),
        cloud=cloud,
        ops=ops,
        ops_kpis=operation_kpis(ops),
        ageing=ageing,
        projection=p,
        net=(p["value_saved"] - (p["azure_cost"] if cloud else 0)) if p else None,
        issues=issues,
        biggest_issue=biggest_issue,
        issue_color=SERIES[0],
        attention=pct(s["needs_attention_rate"]),
        stopped=stopped,
        chart=weekly_chart(s["weekly"]) if s["weekly"] and s["approved"] else None,
        fields=assumption_fields(a),
        pilot=pilot_card(store),
    )


def pilot_card(store: Any) -> dict[str, Any] | None:
    """The last shadow-pilot score (``python -m ap_coder pilot-score``, D-92) as shares, or None before the first."""
    from ap_coder.ops.pilot_score import last_figures

    f = last_figures(store)
    if not f:
        return None

    def share(a: Any, b: Any) -> str:
        return f"{a / b:.0%}" if b else "—"

    return {**f, "rows": [
        ("Invoices matched in JD Edwards", f"{f.get('matched', 0)} of {f.get('ap_coder', 0)}"),
        ("Header fields as posted", share(f.get("fields_right", 0), f.get("fields", 0))),
        ("Lines coded as posted (approved)", share(f.get("lines_right", 0), f.get("lines", 0))),
        ("Lines coded as posted (as AP Coder proposed)", share(f.get("proposed_right", 0), f.get("lines", 0))),
        ("Would-be touchless invoices, wrong", f"{f.get('touchless_wrong', 0)} of {f.get('touchless', 0)}"),
    ]}  # fmt: skip


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
    form = await posted_form(request)
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
