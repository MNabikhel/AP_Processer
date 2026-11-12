"""Activity: the audit trail of everything that changed data, with a search and filters, the controls report for
internal audit, and a link to the duplicate payment audit (on Exports: one place for it, B-513).

Everything here only reads: the filters (``?group=Backups&person=Ann&q=northwind&view=table``), the page of events
(``&before=<an event's id>``: the older ones) and the controls report's period (``?start=…&end=…``) are in the
address, and the downloads (``/activity/events.csv``, every filtered event; ``/activity/controls-report``) are GETs.
The duplicate pairs download from Exports (``/exports?audit=1``).
"""

from __future__ import annotations

import csv
import datetime as dt
import io
from collections.abc import Iterable, Iterator
from typing import Any, BinaryIO
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse

from ap_coder.core import ui
from ap_coder.core.audit import ACTIONS, describe
from ap_coder.core.safe import csv_row
from ap_coder.core.store import Store, fold_text
from ap_coder.erp import controls, insights
from ap_coder.web.access import is_admin
from ap_coder.web.core import address_day, get_store, page_router, render, server_mode
from ap_coder.web.files import download
from ap_coder.web.pages import downloads

router = page_router()  # each plain handler: one connection a request

GROUPS = {
    "Invoices": ["processed", "failed", "approved", "final_approved", "sent_back", "reopened", "parked", "unparked",
                 "sent_to_manager", "manager_confirmed", "manager_sent_back", "manager_request_withdrawn",
                 "note", "rejected", "deleted", "joined", "separated", "exported", "proposal_updated", "verified",
                 "azure_checked", "azure_check_disagrees", "jde_api_dry_run", "jde_api_approved", "jde_voucher_posted",
                 "jde_voucher_refused", "jde_voucher_mismatch", "jde_voucher_released", "jde_file_downloaded"],
    "Setup": ["accounts_imported", "accounts_edited", "accounts_deleted", "changed_by_hand", "cost_center_manager_set",
              "tax_setup_changed", "policy_changed",
              "settings_changed", "rules_changed", "vendor_updated", "vendor_added", "vendors_deleted",
              "vendors_imported", "pos_imported", "po_status", "pos_deleted", "export_undone", "erp_register_imported",
              "files_moved", "file_found", "directory_preset_saved", "directory_preset_deleted", "jde_api_tested",
              "jde_synced"],
    "Learning": ["lessons_forgotten", "lesson_explained", "lesson_one_off", "lesson_retired", "lesson_restored",
                 "history_imported",
                 "journal_imported", "journal_forgotten",
                 "autonomy_on", "autonomy_off", "autonomy_suspended", "autonomy_held", "autonomy_allowed",
                 "layout_reset", "touchless_on", "touchless_off", "page_reader_tested", "chat_model_tested"],
    "Emails": ["email_sorted", "email_bank_change", "email_triage_done", "email_attachment_to_queue"],
    "Backups": ["backup_made", "backup_restored"],
    "Logins": ["login", "login_failed", "login_locked", "logout", "password_changed", "password_reset", "user_added",
               "role_changed", "user_cost_centers_set", "user_deactivated", "user_activated", "address_unlocked",
               "downloaded", "authenticator_enrolled", "authenticator_reset"],
}  # fmt: skip
TIMELINE_SHOWN = 200  # the events a page of the timeline shows; the older ones are on the next (B-1597)
TABLE_SHOWN = 1000
CSV_CHUNK = 1000  # the CSV's rows sent at a time

VIEWS = ("timeline", "table")
DELETIONS = ("deleted", "accounts_deleted", "lessons_forgotten")


def invoice_label(event: dict[str, Any]) -> str:
    """ "Northwind IT Solutions Inc. · NW-2026-0912" for an event about an invoice (blank otherwise)."""
    if not event.get("invoice_id"):
        return ""
    parts = [event.get("invoice_vendor") or "", event.get("invoice_number") or ""]
    return " · ".join(p for p in parts if p) or f"#{event['invoice_id']} (deleted)"


def timeline_items(events: list[dict[str, Any]], with_invoice: bool = True) -> list[dict[str, str]]:
    """Events as ``ui.timeline`` items (plain text; the timeline escapes it), newest first; ``with_invoice``: each
    title names its invoice (not on the invoice's own screen, ``invoice_approve.history``)."""
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
    """The event's words as the page shows them, accents and case aside (``fold_text``, as the invoice directory
    compares them: B-2528)."""
    detail = event["detail"] or {}
    what = ACTIONS.get(event["action"], ("", "", event["action"]))[2]  # "Approved": the word the log shows
    return fold_text(" ".join([what, describe(event), str(detail.get("vendor") or ""),
                               str(detail.get("invoice_number") or ""), str(event["invoice_id"] or ""),
                               invoice_label(event), event["actor"] or ""]))  # fmt: skip


def filter_events(store: Store, group: str, person: str, query: str, *, hidden: Iterable[str] = (),
                  before: int | None = None, limit: int | None = None) -> Iterator[dict[str, Any]]:  # fmt: skip
    """Every event (not only the newest few thousand, B-1597) of ``group`` ("All" or a key of GROUPS), by ``person``
    ("Everyone" or a name), whose text holds ``query`` (any case, accents aside), but none of the ``hidden``
    actions; newest first, older than event ``before``, ``limit`` at most. SQLite picks them by group and person; the
    words are looked for in each as the page shows it (``searchable``). A search that matches nothing is simply
    none."""
    query = fold_text((query or "").strip())
    return store.find_events(actions=GROUPS.get(group), hidden=hidden,
                             actor=None if person in ("", "Everyone") else person, before=before, limit=limit,
                             matches=(lambda e: query in searchable(e)) if query else None)  # fmt: skip


def table_row(e: dict[str, Any]) -> dict[str, str]:
    return {
        "When": str(e["created_at"] or "").replace("T", " ")[:19],
        "Who": e["actor"] or "",
        "What": ACTIONS.get(e["action"], ("", "", e["action"]))[2],
        "Invoice": invoice_label(e),
        "Details": describe(e),
    }


def table_rows(events: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    return [table_row(e) for e in events]


def events_csv(events: Iterable[dict[str, Any]]) -> Iterator[bytes]:
    """The events as a CSV for Excel (UTF-8 with BOM; text a spreadsheet would run as a formula is defused), sent a
    few rows at a time as they are read: a year of events is never held at once."""
    out = io.StringIO()
    writer = csv.writer(out)
    out.write("﻿")
    writer.writerow(["When", "Who", "What", "Invoice", "Details"])
    for n, e in enumerate(events, 1):
        writer.writerow(csv_row(list(table_row(e).values())))
        if n % CSV_CHUNK == 0:
            yield out.getvalue().encode("utf-8")
            out.seek(0)
            out.truncate()
    yield out.getvalue().encode("utf-8")


def controls_period(start: str | None, end: str | None, today: dt.date | None = None) -> tuple[dt.date, dt.date]:
    """The controls report's period: from the address, else this month so far (never after today)."""
    today = today or dt.date.today()
    first, last = address_day(start, today.replace(day=1)), address_day(end, today)  # (B-659)
    return first, min(last, today)


def _filters(request: Request) -> tuple[str, str, str, str]:
    q = request.query_params
    group = q.get("group") or "All"
    group = group if group in GROUPS else "All"
    view = q.get("view") or "timeline"
    return group, q.get("person") or "Everyone", q.get("q") or "", view if view in VIEWS else "timeline"


def _before(request: Request) -> int | None:
    """The page of events asked for: those older than this event (None: the newest)."""
    try:
        before = int(request.query_params.get("before") or 0)
    except ValueError:
        return None
    return before if before > 0 else None


ACCOUNT_GROUP = "Logins"  # logins, refusals, lockouts and changes to accounts: an Admin's to see (D-101)


def _hidden(request: Request) -> tuple[str, ...]:
    """The events the person asking may not see: on the server, the login and account events are an Admin's only (a
    refusal names the account tried, and the addresses people log in from are theirs)."""
    return () if is_admin(request) else tuple(GROUPS[ACCOUNT_GROUP])


def _groups(request: Request) -> list[str]:
    return ["All", *(g for g in GROUPS if g != ACCOUNT_GROUP or is_admin(request))]


def distinct_people(actors: Iterable[str], events: Iterable[dict[str, Any]]) -> list[str]:
    """The people the events are by (``actors``), a renamed one once under the name it has now: saving your name in
    Settings is recorded under the old one (the computer login, before a name was saved), ``{"reviewer": {"from",
    "to"}}`` in a ``settings_changed`` event of ``events`` (B-1305)."""
    renamed: dict[str, str] = {}
    for e in sorted(events, key=lambda e: e["id"]):
        rename = (e.get("detail") or {}).get("reviewer") if e["action"] == "settings_changed" else None
        if isinstance(rename, dict) and rename.get("from") and rename.get("to") and rename["from"] != rename["to"]:
            renamed[str(rename["from"])] = str(rename["to"])

    def now(name: str) -> str:
        seen = {name}
        while name in renamed and renamed[name] not in seen:  # a name taken back later stops the chain
            name = renamed[name]
            seen.add(name)
        return name

    return sorted({now(actor) for actor in actors if actor})


TILE_COUNTS = ("overrides", "signals", "finals", "sent_back", "waiting", "setup")  # the report's lists the tiles count
# On the company server the tiles count a period this long at most (days, the first and the last included): a
# year's took 6.5 s at 300,000 invoices, and there the events recorded all day keep them for moments only; a
# longer period's report is a download its background process makes (D-206). A laptop counts any period.
TILES_MOST_DAYS = 31


def controls_report(store: Store, start: dt.date, end: dt.date, duties: bool) -> dict[str, int | None]:
    """How many of each list of the controls report (``controls.build``) the page's tiles show (``same_person``: None
    without the duties), kept until an event is recorded or an invoice changes (0.2 s at 300,000 invoices, at every
    visit before: D-206). Only the counts, none of them of the approvals over the limit: the whole report of a year was
    153 MB at 15,000 invoices (B-2073); the download builds it whole. Shared: read it, don't change it."""

    def counts() -> dict[str, int | None]:
        report = controls.build(store, start, end, duties=duties, over=False)
        same = report["same_person"]
        return {**{name: len(report[name]) for name in TILE_COUNTS}, "same_person": None if same is None else len(same)}

    return store.summary_memo("activity_controls", counts, asked=(start, end, duties, store.events_stamp()))


@router.get("/activity", response_class=HTMLResponse)
def activity_page(request: Request) -> HTMLResponse:
    store = get_store(request)
    hidden = _hidden(request)
    group, person, query, view = _filters(request)
    before = _before(request)
    params = {"group": group, "person": person, "q": query, "view": view}
    start, end = controls_period(request.query_params.get("start"), request.query_params.get("end"))
    params.update(start=start.isoformat(), end=end.isoformat())

    def url(**changes: Any) -> str:
        merged = {**params, **changes}
        keep = {k: v for k, v in merged.items() if v not in ("", None, "All", "Everyone", "timeline")}
        return "/activity" + (f"?{urlencode(keep)}" if keep else "")

    total = store.count_events(hidden=hidden)  # every event, counted by SQLite (B-1597)
    if not total:
        return render(request, "activity.html", "activity", total=0, url=url)

    today = dt.date.today()
    week = store.count_events(hidden=hidden, since=(today - dt.timedelta(days=7)).isoformat())
    # Each invoice's standing approval once, as on Insights and Learning (D-39): one taken back and given again is
    # one, one taken back and not given again is none. Counted by SQLite (D-156), kept until an invoice changes (D-206).
    clean, approved, filled = store.summary_memo("approval_counts", lambda: insights.approval_counts(store))
    people = store.event_actors(hidden)  # the Person filter: every name events carry
    persons = distinct_people(people, store.find_events(actions=["settings_changed"], hidden=hidden))
    tiles = [
        ("Events", total, "blue", f"{week} in the last 7 days"),
        ("Invoices approved", approved, "green", insights.approvals_note(clean, filled, approved)),  # (D-112)
        ("People", len(persons), "violet", (", ".join(persons[:3]) + (
            f" and {len(persons) - 3} more" if len(persons) > 3 else "")) or "none recorded yet"),  # (B-1009, B-1305)
        ("Deletions", store.count_events(actions=DELETIONS, hidden=hidden), "amber", "invoices, accounts, lessons"),
    ]  # fmt: skip

    # A page of the events that match, and whether older ones follow: their page starts after its last (B-1597).
    size = TABLE_SHOWN if view == "table" else TIMELINE_SHOWN
    shown = list(filter_events(store, group, person, query, hidden=hidden, before=before, limit=size + 1))
    older = url(before=shown[size - 1]["id"]) if len(shown) > size else ""
    shown = shown[:size]
    # How many match: counted by SQLite; for a search (each event's words looked for), only when this page has them all.
    if not query.strip():
        matching = store.count_events(actions=GROUPS.get(group), hidden=hidden,
                                      actor=None if person in ("", "Everyone") else person)  # fmt: skip
    else:
        matching = len(shown) if before is None and not older else None
    duties = server_mode(request)
    counted = start <= end and not (duties and (end - start).days >= TILES_MOST_DAYS)
    report = controls_report(store, start, end, duties) if counted else None
    return render(
        request,
        "activity.html",
        "activity",
        total=total,
        tiles=tiles,
        groups=_groups(request),
        people=people,
        group=group,
        person=person,
        query=query,
        view=view,
        shown=shown,
        matching=matching,
        timeline=timeline_items(shown) if view == "timeline" else [],
        rows=table_rows(shown) if view == "table" else [],
        older=older,
        newest=url() if before else "",
        url=url,
        csv_url=url().replace("/activity", "/activity/events.csv", 1),
        start=start,
        end=end,
        today=today,
        report=report,
        long_period=start <= end and not counted,
    )


@router.get("/activity/events.csv")
async def activity_csv(request: Request) -> Response:
    """The events the page shows with the same filters, all of them (not only a page): on a laptop sent as they are
    read (B-1597), on the company server made by its background process (``downloads``, D-169)."""
    return await downloads.answer(request, "/activity/events.csv", _csv_now)


def _csv_now(request: Request) -> Response:
    params, name = csv_params(request)
    shown = filter_events(get_store(request), params["group"], params["person"], params["q"], hidden=params["hidden"])
    return StreamingResponse(events_csv(shown), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{name}"',
                                      "Cache-Control": "no-store"})  # fmt: skip


def csv_params(request: Request) -> tuple[dict[str, Any], str]:
    """What the events' CSV is of (the page's filters, and the events the person asking may not see) and its name."""
    group, person, query, _ = _filters(request)
    return ({"group": group, "person": person, "q": query, "hidden": list(_hidden(request))},
            f"ap_coder_activity_{dt.date.today()}.csv")  # fmt: skip


def write_csv(store: Store, params: dict[str, Any], out: BinaryIO, step: Any) -> None:
    """The events' CSV (``csv_params``) to ``out``, as the laptop sends it."""
    count = 0

    def counted() -> Iterator[dict[str, Any]]:
        nonlocal count
        for event in filter_events(store, params["group"], params["person"], params["q"], hidden=params["hidden"]):
            count += 1
            yield event

    for chunk in events_csv(counted()):
        out.write(chunk)
        step(count, 0, f"{ui.plural(count, 'event')} written")


@router.get("/activity/controls-report")
async def activity_controls_report(request: Request) -> Response:
    """The controls report of the period asked (all of it, ``controls.build``): on a laptop made as it is asked for,
    as before; on the company server made by its background process (``downloads``, D-169), as a year's took 15 s at
    300,000 invoices (D-206)."""
    return await downloads.answer(request, "/activity/controls-report", _controls_now)


def _controls_now(request: Request) -> Response:
    params, name = controls_params(request)
    return download(controls_html(get_store(request), params), name, "text/html; charset=utf-8")


def controls_params(request: Request) -> tuple[dict[str, Any], str]:
    """What the controls report is of (its period, the first day first, and whether it looks at the duties: on the
    server) and its file's name."""
    first, last = controls_period(request.query_params.get("start"), request.query_params.get("end"))
    if first > last:
        first, last = last, first
    return ({"start": first.isoformat(), "end": last.isoformat(), "duties": server_mode(request)},
            f"ap_coder_controls_{first}_{last}.html")  # fmt: skip


def write_controls(store: Store, params: dict[str, Any], out: BinaryIO, step: Any) -> None:
    """The controls report (``controls_params``) to ``out``, as the laptop sends it."""
    step(0, 0, "Reading the period's events")
    out.write(controls_html(store, params))


def controls_html(store: Store, params: dict[str, Any]) -> bytes:
    report = controls.build(store, dt.date.fromisoformat(params["start"]), dt.date.fromisoformat(params["end"]),
                            duties=params["duties"])  # fmt: skip
    return controls.report_html(report).encode("utf-8")
