"""Activity: the audit trail of everything that changed data, with a search and filters, the controls report for
internal audit, and a link to the duplicate payment audit (on Exports: one place for it, B-513).

Everything here only reads: the filters (``?group=Backups&person=Ann&q=northwind&view=table``), the controls
report's period (``?start=…&end=…``) and the duplicate audit (``?audit=1``) are in the address, and the downloads
(CSV of the filtered events, the controls report, the duplicate pairs) are GETs.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from ap_coder.core import ui
from ap_coder.core.audit import ACTIONS, describe
from ap_coder.core.safe import csv_row
from ap_coder.erp import controls, dupaudit, insights
from ap_coder.web.access import is_admin
from ap_coder.web.core import clamp_day, get_store, render, server_mode

router = APIRouter()

GROUPS = {
    "Invoices": ["processed", "failed", "approved", "final_approved", "sent_back", "reopened", "parked", "unparked",
                 "note", "rejected", "deleted", "exported", "proposal_updated", "verified", "azure_checked",
                 "azure_check_disagrees", "jde_api_dry_run", "jde_api_approved", "jde_voucher_posted",
                 "jde_voucher_refused", "jde_voucher_mismatch", "jde_voucher_released"],
    "Setup": ["accounts_imported", "accounts_edited", "accounts_deleted", "tax_setup_changed", "policy_changed",
              "settings_changed", "rules_changed", "vendor_updated", "vendor_added", "vendors_deleted",
              "vendors_imported", "pos_imported", "po_status", "pos_deleted", "export_undone", "erp_register_imported",
              "files_moved", "file_found", "directory_preset_saved", "directory_preset_deleted", "jde_api_tested",
              "jde_synced"],
    "Learning": ["lessons_forgotten", "lesson_explained", "lesson_retired", "lesson_restored", "history_imported",
                 "autonomy_on", "autonomy_off", "autonomy_suspended", "autonomy_held", "autonomy_allowed",
                 "layout_reset", "touchless_on", "touchless_off", "page_reader_tested"],
    "Emails": ["email_sorted", "email_bank_change", "email_triage_done", "email_attachment_to_queue"],
    "Backups": ["backup_made", "backup_restored"],
    "Logins": ["login", "login_failed", "login_locked", "logout", "password_changed", "password_reset", "user_added",
               "role_changed", "user_deactivated", "user_activated", "address_unlocked", "downloaded"],
}  # fmt: skip
EVENTS_READ = 5000  # the newest events the page works on
TIMELINE_SHOWN = 200
TABLE_SHOWN = 1000

VIEWS = ("timeline", "table")
DELETIONS = ("deleted", "accounts_deleted", "lessons_forgotten")


def invoice_label(event: dict[str, Any]) -> str:
    """ "Northwind IT Solutions Inc. · NW-2026-0912" for an event about an invoice (blank otherwise)."""
    if not event.get("invoice_id"):
        return ""
    parts = [event.get("invoice_vendor") or "", event.get("invoice_number") or ""]
    return " · ".join(p for p in parts if p) or f"#{event['invoice_id']} (deleted)"


def timeline_items(events: list[dict[str, Any]], with_invoice: bool = True) -> list[dict[str, str]]:
    """Events as ``ui.timeline`` items (plain text; the timeline escapes it), newest first."""
    items = []
    for e in events:
        icon_name, tone, label = ACTIONS.get(e["action"], ("circle", "gray", e["action"]))
        about = invoice_label(e) if with_invoice else ""
        when = str(e["created_at"] or "")
        items.append({
            "icon": icon_name, "tone": tone, "title": f"{label} · {about}" if about else label, "text": describe(e),
            "who": e.get("actor") or "", "when": f"{ui.time_ago(when)} · {when.replace('T', ' ')[:16]}",
        })  # fmt: skip
    return items


def searchable(event: dict[str, Any]) -> str:
    detail = event["detail"] or {}
    what = ACTIONS.get(event["action"], ("", "", event["action"]))[2]  # "Approved": the word the log shows
    return " ".join([what, describe(event), str(detail.get("vendor") or ""), str(detail.get("invoice_number") or ""),
                     str(event["invoice_id"] or ""), invoice_label(event), event["actor"] or ""]).lower()  # fmt: skip


def filter_events(events: list[dict[str, Any]], group: str, person: str, query: str) -> list[dict[str, Any]]:
    """The events of ``group`` ("All" or a key of GROUPS), by ``person`` ("Everyone" or a name), whose text holds
    ``query`` (any case). A search that matches nothing is simply an empty list."""
    query = (query or "").strip().lower()
    actions = GROUPS.get(group)
    return [
        e
        for e in events
        if (actions is None or e["action"] in actions)
        and (person in ("", "Everyone") or e["actor"] == person)
        and (not query or query in searchable(e))
    ]


def table_rows(events: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "When": str(e["created_at"] or "").replace("T", " ")[:19],
            "Who": e["actor"] or "",
            "What": ACTIONS.get(e["action"], ("", "", e["action"]))[2],
            "Invoice": invoice_label(e),
            "Details": describe(e),
        }
        for e in events
    ]


def events_csv(events: list[dict[str, Any]]) -> bytes:
    """The events as a CSV for Excel (UTF-8 with BOM; text a spreadsheet would run as a formula is defused)."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["When", "Who", "What", "Invoice", "Details"])
    for row in table_rows(events):
        writer.writerow(csv_row(list(row.values())))
    return ("﻿" + out.getvalue()).encode("utf-8")


def _date(text: str | None, default: dt.date) -> dt.date:
    try:
        return clamp_day(dt.date.fromisoformat(str(text or "")[:10]))  # not past the calendar's ends (B-659)
    except ValueError:
        return default


def controls_period(start: str | None, end: str | None, today: dt.date | None = None) -> tuple[dt.date, dt.date]:
    """The controls report's period: from the address, else this month so far (never after today)."""
    today = today or dt.date.today()
    first, last = _date(start, today.replace(day=1)), _date(end, today)
    return first, min(last, today)


def _filters(request: Request) -> tuple[str, str, str, str]:
    q = request.query_params
    group = q.get("group") or "All"
    group = group if group in GROUPS else "All"
    view = q.get("view") or "timeline"
    return group, q.get("person") or "Everyone", q.get("q") or "", view if view in VIEWS else "timeline"


ACCOUNT_GROUP = "Logins"  # logins, refusals, lockouts and changes to accounts: an Admin's to see (D-101)


def _events(request: Request) -> list[dict[str, Any]]:
    """The newest events, as the person asking may see them: on the server, the login and account events only to an
    Admin (a refusal names the account tried, and the addresses people log in from are theirs)."""
    events = get_store(request).events(limit=EVENTS_READ)
    if is_admin(request):
        return events
    hidden = set(GROUPS[ACCOUNT_GROUP])
    return [e for e in events if e["action"] not in hidden]


def _groups(request: Request) -> list[str]:
    return ["All", *(g for g in GROUPS if g != ACCOUNT_GROUP or is_admin(request))]


def _attachment(data: bytes, filename: str, media_type: str) -> Response:
    return Response(data, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/activity", response_class=HTMLResponse)
def activity_page(request: Request) -> HTMLResponse:
    store = get_store(request)
    events = _events(request)
    group, person, query, view = _filters(request)
    params = {"group": group, "person": person, "q": query, "view": view}
    start, end = controls_period(request.query_params.get("start"), request.query_params.get("end"))
    params.update(start=start.isoformat(), end=end.isoformat())

    def url(**changes: Any) -> str:
        merged = {**params, **changes}
        keep = {k: v for k, v in merged.items() if v not in ("", None, "All", "Everyone", "timeline")}
        return "/activity" + (f"?{urlencode(keep)}" if keep else "")

    if not events:
        return render(request, "activity.html", "activity", events=[], url=url)

    today = dt.date.today()
    week = sum(1 for e in events if str(e["created_at"])[:10] >= (today - dt.timedelta(days=7)).isoformat())
    # Each invoice's standing approval once, as on Insights and Learning (D-39): one taken back and given again is
    # one, one taken back and not given again is none.
    clean, approved = insights.approved_as_coded(store)
    people = sorted({e["actor"] for e in events if e["actor"]})
    tiles = [
        ("Events", len(events), "blue", f"{week} in the last 7 days"),
        ("Invoices approved", approved, "green", insights.approvals_note(clean, insights.filled_in_count(store),
                                                                         approved)),  # (D-112)
        ("People", len(people), "violet", (", ".join(people[:3]) + (f" and {len(people) - 3} more" if len(people) > 3
                                                                   else "")) or "none recorded yet"),  # (B-1009)
        ("Deletions", sum(1 for e in events if e["action"] in DELETIONS), "amber", "invoices, accounts, lessons"),
    ]  # fmt: skip

    shown = filter_events(events, group, person, query)
    report = controls.build(store, start, end, duties=server_mode(request)) if start <= end else None
    return render(
        request,
        "activity.html",
        "activity",
        events=events,
        tiles=tiles,
        groups=_groups(request),
        people=people,
        group=group,
        person=person,
        query=query,
        view=view,
        shown=shown,
        timeline=timeline_items(shown[:TIMELINE_SHOWN]),
        timeline_more=max(0, len(shown) - TIMELINE_SHOWN),
        rows=table_rows(shown[:TABLE_SHOWN]),
        table_more=max(0, len(shown) - TABLE_SHOWN),
        url=url,
        csv_url=url().replace("/activity", "/activity/events.csv", 1),
        start=start,
        end=end,
        today=today,
        report=report,
    )


@router.get("/activity/events.csv")
def activity_csv(request: Request) -> Response:
    """The events the page shows with the same filters, all of them (not only the newest 200)."""
    group, person, query, _ = _filters(request)
    shown = filter_events(_events(request), group, person, query)
    return _attachment(events_csv(shown), f"ap_coder_activity_{dt.date.today()}.csv", "text/csv; charset=utf-8")


@router.get("/activity/controls-report")
def activity_controls_report(request: Request, start: str = "", end: str = "") -> Response:
    first, last = controls_period(start, end)
    if first > last:
        first, last = last, first
    html = controls.report_html(controls.build(get_store(request), first, last, duties=server_mode(request)))
    return _attachment(html.encode("utf-8"), f"ap_coder_controls_{first}_{last}.html", "text/html; charset=utf-8")


@router.get("/activity/duplicates.csv")
def activity_duplicates_csv(request: Request) -> Response:
    pairs = dupaudit.find(get_store(request))
    return _attachment(dupaudit.to_csv(pairs), f"duplicate_audit_{dt.date.today()}.csv", "text/csv; charset=utf-8")
