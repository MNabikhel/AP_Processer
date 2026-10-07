"""Insights for the business case: volume, straight-through rate, time saved and Azure cost.

Everything is computed from data AP Coder already keeps (invoices, their processing metadata with page
counts and token usage, and the audit trail of approvals). Time and price figures are *assumptions* the
AP team can edit on the Insights page; they are stored in the database (setting ``insights_assumptions``).
"""

from __future__ import annotations

import datetime as dt
import html
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, fields
from typing import Any

from .memory import vendor_key
from .store import APPROVED, FAILED, PARKED, PENDING, REVIEW, Store
from .terms import payment


@dataclass
class Assumptions:
    manual_minutes: float = 6.0  # keying and coding one invoice by hand today
    clean_minutes: float = 1.0  # reviewing an invoice the AI got right
    changed_minutes: float = 3.0  # reviewing and correcting one it got partly wrong
    hourly_cost: float = 45.0  # loaded cost of an AP clerk hour (CAD)
    di_usd_per_1000_pages: float = 10.0  # Document Intelligence prebuilt-layout / -invoice
    aoai_usd_per_m_input: float = 2.50  # gpt-4o input tokens
    aoai_usd_per_m_cached: float = 1.25  # cached input tokens
    aoai_usd_per_m_output: float = 10.0  # output tokens
    usd_to_cad: float = 1.37
    monthly_volume: int = 500  # invoices per month, for the projection

    @classmethod
    def load(cls, store: Store) -> Assumptions:
        try:
            saved = json.loads(store.get_setting("insights_assumptions") or "{}")
        except ValueError:
            saved = {}
        known = {f.name: f.type for f in fields(cls)}
        values = {}
        for key, value in saved.items():
            if key in known:
                try:
                    values[key] = int(value) if key == "monthly_volume" else float(value)
                except (TypeError, ValueError):
                    continue
        return cls(**values)

    def save(self, store: Store, actor: str | None = None) -> None:
        store.set_setting("insights_assumptions", json.dumps(asdict(self)), actor=actor)


def invoice_cost_usd(meta: dict[str, Any], a: Assumptions) -> tuple[float, bool]:
    """(Azure cost in USD, whether usage was recorded) for one processed invoice."""
    pages = ((meta.get("extraction") or {}).get("page_count")) or 1
    usage = (meta.get("inference") or {}).get("usage") or {}
    prompt = usage.get("prompt_tokens", 0)
    cached = min(usage.get("cached_prompt_tokens", 0), prompt)
    completion = usage.get("completion_tokens", 0)
    cost = pages * a.di_usd_per_1000_pages / 1000
    cost += (prompt - cached) * a.aoai_usd_per_m_input / 1e6 + cached * a.aoai_usd_per_m_cached / 1e6
    cost += completion * a.aoai_usd_per_m_output / 1e6
    return cost, bool(usage)


def compute(store: Store, a: Assumptions | None = None) -> dict[str, Any]:
    a = a or Assumptions.load(store)
    rows = {r["id"]: r for r in store.list_invoices()}
    light = store.invoice_columns(("id", "meta", "validation"))
    metas = {r["id"]: r["meta"] or {} for r in light}
    processed = [r for r in rows.values() if r["status"] != FAILED]
    failed = [r for r in rows.values() if r["status"] == FAILED]
    approvals = store.events(actions=["approved"], limit=100_000)
    clean = [e for e in approvals if not (e["detail"] or {}).get("changes")]
    changed = [e for e in approvals if (e["detail"] or {}).get("changes")]

    costs = [invoice_cost_usd(metas.get(r["id"]) or {}, a) for r in processed]
    measured = [c for c, recorded in costs if recorded]
    cost_per_invoice_cad = (sum(measured) / len(measured) * a.usd_to_cad) if measured else None
    seconds = [
        sum(((metas.get(r["id"]) or {}).get("timings_seconds") or {}).values())
        for r in processed
        if (metas.get(r["id"]) or {}).get("timings_seconds")
    ]

    minutes_saved = len(clean) * (a.manual_minutes - a.clean_minutes) + len(changed) * (
        a.manual_minutes - a.changed_minutes
    )
    stp = len(clean) / len(approvals) if approvals else None
    per_invoice_minutes = (
        (stp * (a.manual_minutes - a.clean_minutes) + (1 - stp) * (a.manual_minutes - a.changed_minutes))
        if stp is not None
        else None
    )

    issue_counts: Counter[str] = Counter()
    for r in light:
        for issue in (r["validation"] or {}).get("issues") or []:
            if issue.get("severity") in ("error", "warning"):
                issue_counts[issue["code"]] += 1

    weekly: dict[str, dict[str, int]] = defaultdict(lambda: {"processed": 0, "clean": 0, "changed": 0})
    for r in processed:
        weekly[_week(r["created_at"])]["processed"] += 1
    for e in approvals:
        weekly[_week(e["created_at"])]["clean" if not (e["detail"] or {}).get("changes") else "changed"] += 1

    projection = None
    if per_invoice_minutes is not None:
        hours = a.monthly_volume * per_invoice_minutes / 60
        projection = {
            "hours_saved": hours,
            "value_saved": hours * a.hourly_cost,
            "azure_cost": (cost_per_invoice_cad or 0) * a.monthly_volume,
        }
    return {
        "assumptions": a,
        "processed": len(processed),
        "failed": len(failed),
        "approved": len(approvals),
        "approved_clean": len(clean),
        "approved_changed": len(changed),
        "bulk_approved": sum(1 for e in approvals if (e["detail"] or {}).get("bulk")),
        "needs_attention_rate": (sum(1 for r in processed if r["requires_review"]) / len(processed))
        if processed
        else None,
        "straight_through": stp,
        "hours_saved": minutes_saved / 60,
        "value_saved": minutes_saved / 60 * a.hourly_cost,
        "cost_per_invoice_cad": cost_per_invoice_cad,
        "cost_measured_invoices": len(measured),
        "avg_seconds": (sum(seconds) / len(seconds)) if seconds else None,
        "top_issues": issue_counts.most_common(8),
        "weekly": [{"week": w, **v} for w, v in sorted(weekly.items())],
        "projection": projection,
        "approved_status": APPROVED,
    }


AGE_BUCKETS = (("0–2 days", 0, 2), ("3–7 days", 3, 7), ("8–14 days", 8, 14), ("15+ days", 15, 10**6))


def _day(iso: str | None) -> dt.date | None:
    try:
        return dt.date.fromisoformat((iso or "")[:10])
    except ValueError:
        return None


def operations(store: Store, today: dt.date | None = None) -> dict[str, Any]:
    """AP operations: how old the queue is, how long approval takes, approvals after the due date, and
    early-payment discounts approved in time to take them (or not)."""
    today = today or dt.date.today()
    rows = store.list_invoices()
    waiting = [r for r in rows if r["status"] in (REVIEW, PARKED)]
    ageing = {label: 0 for label, _, _ in AGE_BUCKETS}
    for r in waiting:
        received = _day(r["created_at"])
        age = (today - received).days if received else 0
        for label, low, high in AGE_BUCKETS:
            if low <= age <= high:
                ageing[label] += 1
    approved = [r for r in rows if r["status"] in (APPROVED, PENDING) and r["reviewed_at"]]
    cycle = [
        (_day(r["reviewed_at"]) - _day(r["created_at"])).days
        for r in approved
        if _day(r["reviewed_at"]) and _day(r["created_at"])
    ]
    late = sum(1 for r in approved if r["due_date"] and (r["reviewed_at"] or "")[:10] > r["due_date"])
    finals = {r["id"]: r["final_output"] or {} for r in store.invoice_columns(("id", "final_output"), APPROVED)}
    taken = missed = 0
    taken_amount = missed_amount = 0.0
    default_days, vendor_terms = store.default_terms_days(), store.all_vendor_terms()
    for r in approved:
        final = finals.get(r["id"]) or {}
        p = payment(final, default_days, vendor_terms.get(vendor_key(final.get("vendor_name") or ""), ""))
        if p.discount_by is None:
            continue
        if (r["reviewed_at"] or "")[:10] <= p.discount_by.isoformat():
            taken, taken_amount = taken + 1, taken_amount + p.discount_amount
        else:
            missed, missed_amount = missed + 1, missed_amount + p.discount_amount
    return {
        "waiting": len(waiting),
        "ageing": ageing,
        "oldest_days": max(((today - _day(r["created_at"])).days for r in waiting if _day(r["created_at"])), default=0),
        "median_days_to_approve": statistics.median(cycle) if cycle else None,
        "approved": len(approved),
        "approved_after_due": late,
        "discounts_in_time": (taken, round(taken_amount, 2)),
        "discounts_missed": (missed, round(missed_amount, 2)),
    }


def _week(iso: str | None) -> str:
    try:
        d = dt.date.fromisoformat((iso or "")[:10])
    except ValueError:
        return "?"
    year, week, _ = d.isocalendar()
    return f"{year}-W{week:02d}"


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v:.0%}"


def _money(v: float | None, decimals: int = 0) -> str:
    return "—" if v is None else f"${v:,.{decimals}f}"


REPORT_CSS = """
body {font-family: Segoe UI, Inter, Arial, sans-serif; color: #142033; max-width: 860px; margin: 2rem auto;
      padding: 0 1rem;}
h1 {font-size: 1.6rem; margin: .2rem 0;} h2 {font-size: 1.1rem; margin-top: 1.6rem;} .muted {color: #5b6474;}
.grid {display: grid; grid-template-columns: repeat(4, 1fr); gap: .8rem; margin: 1.2rem 0;}
.k {border: 1px solid #e4e8ef; border-radius: 12px; padding: .8rem;}
.k b {display: block; font-size: 1.5rem; margin-top: .2rem;}
table {border-collapse: collapse; width: 100%; margin: .5rem 0 1.2rem;}
td, th {border-bottom: 1px solid #e4e8ef; padding: .4rem .3rem; text-align: left;} .n {text-align: right;}
@media (max-width: 640px) {.grid {grid-template-columns: repeat(2, 1fr);}}
"""


def report_html(s: dict[str, Any], organisation: str = "") -> str:
    """A one-page business case to share with management (no invoice data: only totals)."""
    a: Assumptions = s["assumptions"]
    p = s["projection"] or {}
    e = html.escape

    def row(label: str, value: str, tag: str = "td") -> str:
        return f"<tr><{tag}>{e(label)}</{tag}><{tag} class='n'>{e(value)}</{tag}></tr>"

    def tile(label: str, value: str) -> str:
        return f"<div class='k'>{e(label)}<b>{e(value)}</b></div>"

    net = (p.get("value_saved") or 0) - (p.get("azure_cost") or 0)
    avg = "—" if s["avg_seconds"] is None else f"{s['avg_seconds']:.0f} s"
    issues = "".join(row(code, str(n)) for code, n in s["top_issues"]) or row("No issues recorded", "")
    title = "AP Coder pilot: business case" + (f" · {organisation}" if organisation else "")
    assumptions = (
        f"Manual processing {a.manual_minutes:g} min per invoice; review when the AI is right "
        f"{a.clean_minutes:g} min, when it needs corrections {a.changed_minutes:g} min; loaded hourly cost "
        f"{_money(a.hourly_cost)} CAD. Azure list prices (USD): Document Intelligence "
        f"{_money(a.di_usd_per_1000_pages, 2)} per 1,000 pages; Azure OpenAI {_money(a.aoai_usd_per_m_input, 2)} "
        f"input, {_money(a.aoai_usd_per_m_cached, 2)} cached input and {_money(a.aoai_usd_per_m_output, 2)} output "
        f"per million tokens; USD to CAD {a.usd_to_cad:g}. Costs use the token usage recorded for "
        f"{s['cost_measured_invoices']} invoice(s)."
    )
    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>{e(title)}</title><style>{REPORT_CSS}</style></head><body>",
        f"<div class='muted'>Prepared {dt.date.today().isoformat()} from AP Coder's own records · "
        "no invoice details included</div>",
        f"<h1>{e(title)}</h1><div class='grid'>",
        tile("Invoices processed", str(s["processed"])),
        tile("Approved with no changes", _pct(s["straight_through"])),
        tile("Hours saved so far", f"{s['hours_saved']:,.1f}"),
        tile("Azure cost per invoice", _money(s["cost_per_invoice_cad"], 3)),
        f"</div><h2>At {a.monthly_volume:,} invoices a month</h2><table>",
        row("AP time saved", f"{p.get('hours_saved', 0):,.0f} hours / month"),
        row(f"Value of that time (at {_money(a.hourly_cost)} / hour)", f"{_money(p.get('value_saved'))} / month"),
        row("Azure cost (Document Intelligence + Azure OpenAI)", f"{_money(p.get('azure_cost'))} / month"),
        row("Net benefit", f"{_money(net)} / month", "th"),
        "</table><h2>Pilot so far</h2><table>",
        row("Approved", f"{s['approved']} ({s['approved_clean']} as coded, {s['approved_changed']} corrected)"),
        row("Sent for a closer look by the checks", _pct(s["needs_attention_rate"])),
        row("Could not be read", str(s["failed"])),
        row("Average processing time", avg),
        "</table><h2>Most common findings</h2><table>",
        issues,
        f"</table><h2>Assumptions</h2><p class='muted'>{e(assumptions)}</p></body></html>",
    ]
    return "\n".join(parts)
