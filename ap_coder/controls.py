"""Controls report for internal audit: the exceptions in a period, from the audit trail.

* approvals that overrode an error
* approvals made while a fraud or duplicate signal was showing
* second approvals and invoices sent back; invoices over the limit approved by one person only
* changes to setup (accounts, tax, policy, settings, vendors, purchase orders), lessons forgotten,
  exports undone and backups restored
* approvals per person, and how many were bulk approvals
"""

from __future__ import annotations

import datetime as dt
import html
from collections import Counter
from typing import Any

from .audit import ACTIONS, describe
from .store import APPROVED, PENDING, Store

SIGNALS = {
    "DUPLICATE_INVOICE", "POSSIBLE_DUPLICATE_AMOUNT", "VENDOR_ON_HOLD", "VENDOR_TAX_NUMBER_CHANGED",
    "VENDOR_BANK_CHANGED", "AMOUNT_UNUSUAL", "PO_UNKNOWN", "PO_VENDOR_MISMATCH", "PO_PRICE_OVER", "PO_QTY_OVER",
    "PO_NOT_RECEIVED", "PO_OVER_BILLED", "PO_CLOSED",
}  # fmt: skip
SETUP_ACTIONS = (
    "accounts_imported", "accounts_edited", "accounts_deleted", "tax_setup_changed", "policy_changed",
    "settings_changed", "rules_changed", "vendor_updated", "vendors_imported", "pos_imported", "po_status",
    "pos_deleted", "lessons_forgotten", "export_undone", "backup_restored",
)  # fmt: skip


def _codes(event: dict[str, Any], severity: str | None = None) -> list[str]:
    issues = (event["detail"] or {}).get("open_issues") or []
    return [i["code"] for i in issues if severity is None or i.get("severity") == severity]


def build(store: Store, start: dt.date, end: dt.date) -> dict[str, Any]:
    """Sections of the report for events from ``start`` to ``end`` (inclusive)."""
    first, last = start.isoformat(), (end + dt.timedelta(days=1)).isoformat()
    events = [e for e in store.events(limit=1_000_000) if first <= e["created_at"] < last]
    approvals = [e for e in events if e["action"] == "approved"]
    overrides = [e for e in approvals if _codes(e, "error")]
    signals = [e for e in approvals if set(_codes(e)) & SIGNALS]
    finals = [e for e in events if e["action"] == "final_approved"]
    sent_back = [e for e in events if e["action"] == "sent_back"]
    waiting = store.list_invoices(PENDING)
    limit = store.approval_limit()
    one_person = (
        [
            i
            for i in store.invoice_columns(("id", "reviewed_at", "second_reviewer", "final_output"), APPROVED)
            if not i["second_reviewer"]
            and abs(float((i["final_output"] or {}).get("grand_total") or 0)) > limit
            and first <= (i["reviewed_at"] or "") < last
        ]
        if limit
        else []
    )
    setup = [e for e in events if e["action"] in SETUP_ACTIONS]
    by_person = Counter(e["actor"] or "?" for e in approvals)
    bulk = Counter(e["actor"] or "?" for e in approvals if (e["detail"] or {}).get("bulk"))
    return {
        "start": start, "end": end, "events": len(events), "approvals": len(approvals),
        "overrides": overrides, "signals": signals, "finals": finals, "sent_back": sent_back,
        "waiting": waiting, "setup": setup, "one_person": one_person,
        "by_person": [(p, n, bulk.get(p, 0)) for p, n in by_person.most_common()],
        "limit": store.approval_limit(),
    }  # fmt: skip


def exceptions(report: dict[str, Any]) -> int:
    return len(report["overrides"]) + len(report["signals"])


REPORT_CSS = """
body {font-family: Segoe UI, Inter, Arial, sans-serif; color: #142033; max-width: 980px; margin: 2rem auto;
      padding: 0 1rem;}
h1 {font-size: 1.5rem; margin: .2rem 0;} h2 {font-size: 1.05rem; margin-top: 1.6rem;} .muted {color: #5b6474;}
table {border-collapse: collapse; width: 100%; margin: .4rem 0 1rem; font-size: .9rem;}
td, th {border-bottom: 1px solid #e4e8ef; padding: .35rem .3rem; text-align: left; vertical-align: top;}
.n {text-align: right;} .none {color: #1a7f4b;}
"""


def _rows(events: list[dict[str, Any]]) -> str:
    e = html.escape
    return "".join(
        f"<tr><td>{e(ev['created_at'].replace('T', ' ')[:16])}</td><td>{e(ev['actor'] or '')}</td>"
        f"<td>{e(ACTIONS.get(ev['action'], ('', '', ev['action']))[2])}</td>"
        f"<td>{'#' + str(ev['invoice_id']) if ev['invoice_id'] else ''}</td><td>{e(describe(ev))}</td></tr>"
        for ev in events
    )


def _section(title: str, events: list[dict[str, Any]], empty: str) -> str:
    if not events:
        return f"<h2>{html.escape(title)}</h2><p class='none'>{html.escape(empty)}</p>"
    return (
        f"<h2>{html.escape(title)} ({len(events)})</h2><table><tr><th>When</th><th>Who</th><th>What</th>"
        f"<th>Invoice</th><th>Details</th></tr>{_rows(events)}</table>"
    )


def report_html(r: dict[str, Any]) -> str:
    e = html.escape
    period = f"{r['start']:%Y-%m-%d} to {r['end']:%Y-%m-%d}"
    people = (
        "".join(f"<tr><td>{e(p)}</td><td class='n'>{n}</td><td class='n'>{b}</td></tr>" for p, n, b in r["by_person"])
        or "<tr><td colspan='3' class='muted'>No approvals in the period</td></tr>"
    )
    limit = f"Approval limit: {r['limit']:,.2f}." if r["limit"] else "No approval limit is set."
    waiting = "".join(
        f"<tr><td>#{i['id']}</td><td>{e(i['vendor_name'] or '')}</td><td>{e(i['invoice_number'] or '')}</td>"
        f"<td class='n'>{(i['grand_total'] or 0):,.2f}</td><td>{e(i['reviewer'] or '')}</td></tr>"
        for i in r["waiting"]
    )
    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>AP Coder controls report {period}</title><style>{REPORT_CSS}</style></head><body>",
        f"<div class='muted'>Generated {dt.datetime.now():%Y-%m-%d %H:%M} from the AP Coder audit trail</div>",
        f"<h1>Controls report · {period}</h1>",
        f"<p>{r['events']} recorded event(s), {r['approvals']} approval(s). {e(limit)}</p>",
        _section("Approved despite an error", r["overrides"], "None: no error was overridden."),
        _section("Approved while a fraud or duplicate signal was showing", r["signals"], "None."),
        _section("Second approvals", r["finals"], "None in the period."),
        _section("Sent back to the review queue", r["sent_back"], "None."),
    ]
    if r["one_person"]:
        rows = "".join(
            f"<tr><td>#{i['id']}</td><td>{e((i['final_output'] or {}).get('vendor_name') or '')}</td>"
            f"<td>{e((i['final_output'] or {}).get('invoice_number') or '')}</td>"
            f"<td class='n'>{float((i['final_output'] or {}).get('grand_total') or 0):,.2f}</td>"
            f"<td>{e((i['reviewed_at'] or '')[:16].replace('T', ' '))}</td></tr>"
            for i in r["one_person"]
        )
        parts.append(
            f"<h2>Over the approval limit, approved by one person ({len(r['one_person'])})</h2>"
            "<p class='muted'>Usually approved before the limit was set or raised.</p><table><tr><th>#</th>"
            f"<th>Vendor</th><th>Invoice</th><th class='n'>Total</th><th>Approved</th></tr>{rows}</table>"
        )
    if r["waiting"]:
        parts.append(
            f"<h2>Waiting for a second approval now ({len(r['waiting'])})</h2><table><tr><th>#</th><th>Vendor</th>"
            f"<th>Invoice</th><th class='n'>Total</th><th>First approver</th></tr>{waiting}</table>"
        )
    parts += [
        _section("Changes to setup, vendors, purchase orders and learning", r["setup"], "None."),
        "<h2>Approvals by person</h2><table><tr><th>Person</th><th class='n'>Approvals</th>"
        f"<th class='n'>of which bulk</th></tr>{people}</table></body></html>",
    ]
    return "\n".join(parts)
