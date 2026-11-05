"""Controls report for internal audit: the exceptions in a period, from the audit trail.

* approvals that overrode an error
* approvals made while a fraud or duplicate signal was showing
* second approvals and invoices sent back; invoices over the limit approved by one person only
* changes to setup (accounts, tax, policy, settings, vendors, purchase orders), lessons forgotten,
  exports undone and backups restored
* approvals per person, and how many were bulk approvals
* invoices where one person did several of the steps: first approval, second approval, export, posting to JD Edwards

Entry points: ``build`` (the sections of a period) and ``report_html``.
"""

from __future__ import annotations

import datetime as dt
import html
import json
from collections import Counter
from typing import Any

from ..core.audit import ACTIONS, describe
from ..core.store import APPROVED, PENDING, Store

SIGNALS = {
    "DUPLICATE_INVOICE", "POSSIBLE_DUPLICATE_AMOUNT", "VENDOR_ON_HOLD", "VENDOR_CHANGED_FROM_HELD",
    "VENDOR_TAX_NUMBER_CHANGED", "VENDOR_BANK_CHANGED", "VENDOR_BANK_NOT_JDE", "AMOUNT_UNUSUAL", "PO_UNKNOWN",
    "PO_VENDOR_MISMATCH", "PO_PRICE_OVER", "PO_QTY_OVER", "PO_NOT_RECEIVED", "PO_OVER_BILLED", "PO_CLOSED",
}  # fmt: skip
SETUP_ACTIONS = (
    "accounts_imported", "accounts_edited", "accounts_deleted", "tax_setup_changed", "policy_changed",
    "settings_changed", "rules_changed", "vendor_updated", "vendor_added", "vendors_deleted", "vendors_imported",
    "erp_register_imported", "pos_imported", "po_status", "pos_deleted", "deleted", "history_imported",
    "lessons_forgotten", "lesson_retired", "lesson_restored", "export_undone", "backup_restored", "autonomy_on",
    "autonomy_off", "autonomy_suspended", "autonomy_held", "autonomy_allowed", "layout_reset", "touchless_on",
    "touchless_off",
)  # fmt: skip


# The steps of an invoice that separation of duties keeps apart (D-109), by their audit event.
DUTY_STEPS = {"approved": "approved", "final_approved": "gave the second approval", "exported": "exported",
              "jde_voucher_posted": "posted to JD Edwards"}  # fmt: skip
# The events the report lists or counts (``build``), the only ones of its period it reads.
REPORTED = ("sent_back", "reopened", *SETUP_ACTIONS, *DUTY_STEPS)


def same_person_steps(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Invoices where one person (by name) did two or more of :data:`DUTY_STEPS`, with the steps."""
    steps: dict[tuple[int, str], dict[str, Any]] = {}
    for e in events:
        if e["action"] in DUTY_STEPS and e["invoice_id"] and (e["actor"] or "").strip():
            key = (int(e["invoice_id"]), e["actor"].strip().casefold())
            found = steps.setdefault(key, {"invoice_id": key[0], "person": e["actor"].strip(), "steps": [],
                                           "vendor": e.get("invoice_vendor") or ""})  # fmt: skip
            if DUTY_STEPS[e["action"]] not in found["steps"]:
                found["steps"].append(DUTY_STEPS[e["action"]])
    order = list(DUTY_STEPS.values())
    found = [{**f, "steps": sorted(f["steps"], key=order.index)} for f in steps.values() if len(f["steps"]) > 1]
    return sorted(found, key=lambda f: (f["invoice_id"], f["person"]))


def _codes(event: dict[str, Any], severity: str | None = None) -> list[str]:
    issues = (event["detail"] or {}).get("open_issues") or []
    return [i["code"] for i in issues if severity is None or i.get("severity") == severity]


# The approved invoices of a period with no second approver that may be over the approval limit: every one but those
# SQLite can tell are not (a currency it reads as ``second_approval_reason`` does, with a rate, and a total well
# under the limit at it); ``one_person`` checks those few as before. Not every approved invoice's coding decoded for a
# period's handful (+1.5 GB at 100,000 invoices).
_MAYBE_OVER = """
WITH coded AS (
    SELECT s.id, CASE WHEN i.final_output IS NULL OR i.final_output = '' THEN '{}' ELSE i.final_output END AS doc
    FROM invoice_summary s JOIN invoices i ON i.id = s.id
    WHERE s.status = ? AND s.reviewed_at >= ? AND s.reviewed_at < ? AND COALESCE(s.second_reviewer, '') = ''),
read AS (SELECT id, doc, CASE WHEN json_valid(doc) THEN json_type(doc) = 'object' ELSE 0 END AS ok FROM coded),
facts AS (
    SELECT id, ok, CASE WHEN ok THEN json_type(doc, '$.grand_total') END AS total_type,
           CASE WHEN ok THEN json_extract(doc, '$.grand_total') END AS total,
           CASE WHEN NOT ok THEN NULL
                WHEN json_type(doc, '$.currency') IS NULL OR json_type(doc, '$.currency') = 'null' THEN 'CAD'
                WHEN json_type(doc, '$.currency') != 'text' THEN NULL
                WHEN json_extract(doc, '$.currency') = '' THEN 'CAD'
                WHEN json_extract(doc, '$.currency') NOT GLOB '*[^A-Za-z ]*'
                    THEN upper(trim(json_extract(doc, '$.currency'))) END AS currency
    FROM read),
rated AS (SELECT *, (SELECT r.value FROM json_each(?) r WHERE r.key = currency) AS rate FROM facts)
SELECT id FROM rated
WHERE NOT ok OR rate IS NULL OR NOT (total_type IS NULL OR total_type IN ('null', 'false')
                                     OR (total_type IN ('integer', 'real', 'true') AND abs(total) * rate <= ?))
ORDER BY id
"""
# How far under the limit SQLite's reading of a total must be to be taken as under: its numbers may be a unit in the
# last place off Python's; one this close is checked as before.
_UNDER = 1 - 1e-9


def one_person(store: Store, first: str, last: str, limit: float, rates: dict[str, float]) -> list[dict[str, Any]]:
    """The invoices approved from ``first`` up to ``last`` (ISO days) with no second approver that are over
    ``limit`` at ``rates`` (``second_approval_reason``), by id: id, reviewed_at, second_reviewer, final_output."""
    maybe = [r["id"] for r in store.summary_fetch(_MAYBE_OVER, (APPROVED, first, last, json.dumps(rates),
                                                                limit * _UNDER))]  # fmt: skip
    if not maybe:
        return []
    rows = store.invoice_columns(("id", "reviewed_at", "second_reviewer", "final_output"), APPROVED, ids=maybe)
    return [i for i in rows if not i["second_reviewer"] and first <= (i["reviewed_at"] or "") < last
            and store.second_approval_reason(i["final_output"] or {}, limit, rates=rates)]  # fmt: skip


def build(store: Store, start: dt.date, end: dt.date, *, duties: bool = False) -> dict[str, Any]:
    """Sections of the report for events from ``start`` to ``end`` (inclusive). ``duties``: also the invoices where
    one person did several steps (the company server; on a laptop one person does them all)."""
    first, last = start.isoformat(), (end + dt.timedelta(days=1)).isoformat()
    # The period's events, picked by SQLite by their day, not the newest million filtered here: an older period kept
    # none (B-1598, the kind of B-1251).
    events = list(store.find_events(actions=REPORTED, since=first, until=last))
    approvals = [e for e in events if e["action"] == "approved"]
    overrides = [e for e in approvals if _codes(e, "error")]
    signals = [e for e in approvals if set(_codes(e)) & SIGNALS]
    finals = [e for e in events if e["action"] == "final_approved"]
    sent_back = [e for e in events if e["action"] in ("sent_back", "reopened")]
    waiting = store.list_invoices(PENDING)
    limit = store.approval_limit()
    rates = store.fx_rates() if limit else {}  # read once, not once an approved invoice (B-1598)
    alone = one_person(store, first, last, limit, rates) if limit else []
    setup = [e for e in events if e["action"] in SETUP_ACTIONS]
    # Invoices each person approved: approving one invoice again (after a reopen or send-back) counts once.
    by_person = Counter(person for person, _ in {(e["actor"] or "?", e["invoice_id"]) for e in approvals})
    bulk = Counter(person for person, _ in {(e["actor"] or "?", e["invoice_id"]) for e in approvals
                                              if (e["detail"] or {}).get("bulk")})  # fmt: skip
    return {
        "start": start, "end": end, "events": store.count_events(since=first, until=last), "approvals": len(approvals),
        "overrides": overrides, "signals": signals, "finals": finals, "sent_back": sent_back,
        "waiting": waiting, "setup": setup, "one_person": alone,
        "by_person": [(p, n, bulk.get(p, 0)) for p, n in by_person.most_common()],
        "limit": store.approval_limit(), "same_person": same_person_steps(events) if duties else None,
    }  # fmt: skip


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
        _section("Sent back or reopened for correction", r["sent_back"], "None."),
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
    same = r.get("same_person")
    if same:
        rows = "".join(f"<tr><td>#{s['invoice_id']}</td><td>{e(s['vendor'])}</td><td>{e(s['person'])}</td>"
                       f"<td>{e(', '.join(s['steps']))}</td></tr>" for s in same)  # fmt: skip
        parts.append(
            f"<h2>One person at several steps ({len(same)})</h2><p class='muted'>The same person approved, gave the "
            "second approval, exported or posted to JD Edwards more than one of these for an invoice.</p><table>"
            f"<tr><th>#</th><th>Vendor</th><th>Person</th><th>Steps</th></tr>{rows}</table>"
        )
    elif same is not None:
        parts.append("<h2>One person at several steps</h2><p class='none'>None: each step of each invoice was done "
                     "by a different person.</p>")  # fmt: skip
    parts += [
        _section("Changes to setup, vendors, purchase orders and learning", r["setup"], "None."),
        "<h2>Approvals by person</h2><table><tr><th>Person</th><th class='n'>Invoices approved</th>"
        f"<th class='n'>of which bulk</th></tr>{people}</table></body></html>",
    ]
    return "\n".join(parts)
