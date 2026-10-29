"""Vendors: every supplier seen on an invoice, its spend and AI accuracy, and AP's controls.

* ``GET /vendors?view=all|hold|new&q=&sort=``: the list; ``&open=<vendor key>`` opens one vendor under it: GL accounts
  used, GST/HST numbers and bank accounts its invoices carried (the history, masked), recent invoices, the terms and
  ERP ID from the vendor master, the controls (hold, expected GST/HST number, notes) and the emails asking the vendor
  for what its invoices in the queue are missing (``&lang=``).
* The ERP's vendor master: upload (``POST /vendors/master/upload``), check the columns (``?file=``), import.
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from urllib.parse import urlencode

from fastapi import File, Form, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from ap_coder.coding import vendor_mail
from ap_coder.coding.tax import OUTSIDE_CANADA, PROVINCE_NAMES
from ap_coder.coding.vendors import (
    ACTIVE,
    ON_HOLD,
    bank_branch,
    mask_account,
    master_columns,
    master_rows,
    norm_tax_number,
    same_bank_account,
    vendor_number_problems,
)
from ap_coder.core import ui
from ap_coder.core.store import PARKED, REVIEW, Store
from ap_coder.erp import recurring
from ap_coder.erp.insights import vendor_workload
from ap_coder.ops.help import help_for
from ap_coder.web.core import (
    done,
    flash_message,
    get_store,
    page_router,
    posted_form,
    reference_or_none,
    refuse,
    render,
    reviewer,
)
from ap_coder.web.files import (
    NO_ROWS,
    SheetError,
    drop_upload,
    picked_columns,
    read_upload,
    receive_sheet,
    sheet_context,
)
from ap_coder.web.invoice_view import gl_name

router = page_router()  # each plain handler: one connection a request

SORTS = {
    "spend": "Spend: high to low",
    "invoices": "Most invoices",
    "newest": "Newest first",
    "accuracy": "Lowest AI accuracy",
    "name": "Name A–Z",
}
VIEWS = {"all": "All", "hold": "On hold", "new": "New"}
MASTER_LABELS = {
    "vendor_name": "Vendor name",
    "erp_id": "Vendor ID",
    "gst": "GST/HST number",
    "terms": "Payment terms",
    "status": "Status",
    "default_gl": "Default GL account",
}
TARGET = 0.9  # the AI accuracy shown in green from here


def sort_vendors(rows: list[dict[str, Any]], order: str) -> list[dict[str, Any]]:
    if order == "invoices":
        return sorted(rows, key=lambda r: -r["invoices"])
    if order == "newest":
        return sorted(rows, key=lambda r: r["first_seen"] or "", reverse=True)
    if order == "accuracy":
        return sorted(rows, key=lambda r: (r["accuracy"] is None, r["accuracy"] or 0))
    if order == "name":
        return sorted(rows, key=lambda r: (r["vendor_name"] or "").lower())
    return sorted(rows, key=lambda r: -r["spend_in_cad"])


def spend_items(v: dict[str, Any]) -> list[tuple[str, float]]:
    """Approved spend per currency: a USD-only vendor never shows 0.00 CAD."""
    return [(c, t) for c, t in (v.get("spend") or {}).items() if t]


def history(
    invoices: list[dict[str, Any]], field: str, same: Any, show: Any, apart: Any = None
) -> list[dict[str, Any]]:
    """The values ``field`` took on a vendor's invoices, oldest first: each with the first and last invoice date it
    was seen on and how many invoices carried it (a change of bank account or GST/HST number stands out). Two
    values ``same`` tells apart but shown alike (two accounts ending in the same 4 digits) also show ``apart``'s
    text (their bank and branch numbers), or, when that is alike too, that the number is another."""
    out: list[dict[str, Any]] = []
    for inv in sorted(invoices, key=lambda i: (i.get("invoice_date") or "", i["id"])):
        value = inv.get(field) or ""
        if not value:
            continue
        entry = next((e for e in out if same(e["raw"], value)), None)
        when = inv.get("invoice_date") or (inv.get("created_at") or "")[:10]
        if entry is None:
            out.append({"raw": value, "shown": show(value), "first": when, "last": when, "invoices": 1})
        else:
            entry["last"] = when or entry["last"]
            entry["invoices"] += 1
    for e in out:
        twins = [o for o in out if o["shown"] == e["shown"]]
        if apart is not None and len(twins) > 1:
            extra = apart(e["raw"])
            if extra and sum(apart(o["raw"]) == extra for o in twins) == 1:
                e["shown_apart"] = f"{e['shown']}, bank and branch {extra}"
            elif twins[0] is not e:
                e["shown_apart"] = f"{e['shown']}, another account number"
    for e in out:
        e["shown"] = e.pop("shown_apart", e["shown"])
        del e["raw"]  # never the whole bank account on the page
    return out


def same_bank(a: Any, b: Any) -> bool:
    """One account, however written (``same_bank_account``, never only its last 4 digits); details without a number
    go together."""
    return same_bank_account(a, b) or mask_account(a) == mask_account(b) == "(none)"


def ask_vendor(store: Store, invoices: list[dict[str, Any]], language: str) -> list[dict[str, Any]]:
    """Emails asking the vendor for what its invoices waiting in the queue (or parked) are missing: only what the
    vendor can fix, never the internal checks (``vendor_mail``)."""
    out = []
    waiting = [row for row in invoices if row["status"] in (REVIEW, PARKED)]
    full = {inv["id"]: inv for inv in store.get_invoices([row["id"] for row in waiting])}  # one query, not one a row
    for row in waiting:
        inv = full.get(row["id"]) or {}
        doc = inv.get("final_output") or inv.get("ai_output") or {}
        issues = [
            i for i in ((inv.get("validation") or {}).get("issues") or []) if isinstance(i, dict) and i.get("code")
        ]
        if not vendor_mail.points(doc, issues):
            continue
        mail = vendor_mail.draft(doc, issues, language, reviewer())
        out.append(
            {
                "id": row["id"],
                "number": row["invoice_number"] or "?",
                "subject": mail.subject,
                "body": mail.body,
                "mailto": mail.mailto(),
            }
        )
    return out


def vendor_detail(store: Store, v: dict[str, Any], language: str) -> dict[str, Any]:
    key = v["vendor_key"]
    invoices = store.vendor_invoices(key)
    reference = reference_or_none(store)
    numbers = sorted({i["gst_hst_number"] for i in invoices if i["gst_hst_number"]})
    suggested = vendor_mail.ENGLISH
    for inv in invoices[:1]:
        doc = (store.get_invoice(inv["id"]) or {}).get("final_output") or {}
        suggested = vendor_mail.suggested_language(doc)
    language = language if language in vendor_mail.LANGUAGES else suggested
    return {
        "v": v,
        "has_master": store.has_vendor_master(),
        "usage": [{**u, "name": gl_name(reference, u["gl_code"])} for u in store.vendor_gl_usage(key)[:8]],
        "numbers": numbers,
        "recent": [{**i, "pill": ui.STATUS_PILLS.get(i["status"], (i["status"], "gray", ""))} for i in invoices[:8]],
        "tax_history": history(invoices, "gst_hst_number", lambda a, b: norm_tax_number(a) == norm_tax_number(b), str),
        "bank_history": history(invoices, "bank_account", same_bank, mask_account, apart=bank_branch),
        "asks": ask_vendor(store, invoices, language),
        "number_problems": vendor_number_problems(v),
        "lang": language,
    }


def recurring_rows(store: Store) -> tuple[list[Any], list[Any]]:
    found = recurring.detected(store)
    return found, [r for r in found if r.status == recurring.LATE]


def workload_rows(store: Store) -> list[dict[str, Any]]:
    rows = []
    for v in vendor_workload(store)[:10]:
        problems = ", ".join((help_for(c).title if help_for(c) else c) for c in v["top_codes"]) or "—"
        rows.append({**v, "problems": problems})
    return rows


@router.get("/vendors", response_class=HTMLResponse)
def vendors_page(
    request: Request,
    view: str = "all",
    q: str = "",
    sort: str = "spend",
    opened: str = Query("", alias="open"),
    file: str = "",
    sheet: str = "",
    lang: str = "",
    setup: str = "",
) -> HTMLResponse:
    store = get_store(request)
    view = view if view in VIEWS else "all"
    sort = sort if sort in SORTS else "spend"
    vendors = store.vendor_summaries()
    month_ago = (dt.datetime.now() - dt.timedelta(days=30)).isoformat()
    new = [v for v in vendors if (v["first_seen"] or "") >= month_ago]
    on_hold = [v for v in vendors if v["status"] == ON_HOLD or v.get("erp_hold")]  # here, or in JD Edwards
    shown = on_hold if view == "hold" else new if view == "new" else vendors
    if q.strip():
        shown = [v for v in shown if q.strip().lower() in (v["vendor_name"] or "").lower()]
    shown = sort_vendors(shown, sort)

    tiles = []
    if vendors:
        top = vendors[0]  # the most spend, in CAD at the Settings exchange rates
        top_spend = sorted(((t, c) for c, t in (top.get("spend") or {}).items() if t), reverse=True)
        others = ", ".join(c for _, c in top_spend[1:])
        tiles = [
            ("Vendors", len(vendors), f"{sum(v['invoices'] for v in vendors)} invoices"),
            ("New this month", len(new), "first invoice in 30 days"),
            ("On hold", len(on_hold), "invoices from them can't be approved without an override"),
            (
                "Top spend",
                f"{ui.money(top_spend[0][0])} {top_spend[0][1]}" if top_spend else "—",
                ((top["vendor_name"] or "") + (f" · also in {others}" if others else ""))
                if top_spend
                else "no approved spend yet",
            ),
        ]

    # The vendor master waiting for its columns
    master, master_error = None, ""
    if file:
        try:
            upload, df, sheets = read_upload(file, sheet or None)
            columns = list(df.columns)
            if df.empty:
                master_error = NO_ROWS
            chosen = picked_columns({}, MASTER_LABELS, columns, master_columns(columns))
            master = sheet_context(file, upload, df, sheets, sheet, MASTER_LABELS, chosen, ("vendor_name",))
        except SheetError as exc:
            master_error = str(exc)

    chosen_vendor = next((v for v in vendors if v["vendor_key"] == opened), None) if opened else None
    found, late = recurring_rows(store) if vendors else ([], [])
    keep = {"view": view, "q": q, "sort": sort}
    return render(
        request,
        "vendors.html",
        "vendors",
        vendors=vendors,
        tiles=tiles,
        shown=shown,
        view=view,
        views=VIEWS,
        q=q,
        sort=sort,
        sorts=SORTS,
        keep=keep,
        list_query=urlencode(keep),
        has_master=store.has_vendor_master(),
        master_count=0 if vendors else len(store.master_vendors()),  # imported, no invoice yet (B-1008)
        spend_items=spend_items,
        target=TARGET,
        master=master,
        setup=bool(setup),  # from Getting started: the vendor master's import open
        master_error=master_error,
        detail=vendor_detail(store, chosen_vendor, lang) if chosen_vendor else None,
        statuses={ACTIVE: "Active", ON_HOLD: "On hold"},
        provinces={**PROVINCE_NAMES, OUTSIDE_CANADA: "Outside Canada"},
        on_hold=ON_HOLD,
        languages=vendor_mail.LANGUAGES,
        recurring=found,
        late=late,
        late_total=sum(r.typical_total for r in late),
        recurring_late=recurring.LATE,
        recurring_soon=recurring.DUE_SOON,
        workload=workload_rows(store) if vendors else [],
        number_problems=vendor_number_problems,
    )


@router.post("/vendors/save")
def save_vendor(
    request: Request,
    key: str = Form(...),
    status: str = Form(ACTIVE),
    expected_gst: str = Form(""),
    qst_number: str = Form(""),
    pst_number: str = Form(""),
    province: str = Form(""),
    notes: str = Form(""),
    back: str = Form(""),
) -> JSONResponse:
    """The vendor's controls: on hold (every new invoice needs an override to approve), the GST/HST, QST and PST
    numbers its invoices must show, its province, notes."""
    store = get_store(request)
    vendor = next((v for v in store.vendor_summaries() if v["vendor_key"] == key), None)
    if vendor is None:
        return refuse("That vendor is not in AP Coder.", 404)
    if status not in (ACTIVE, ON_HOLD):
        return refuse("Choose Active or On hold.")
    if province and province not in PROVINCE_NAMES and province != OUTSIDE_CANADA:
        return refuse("Choose a province from the list.")
    name = vendor["vendor_name"] or ""
    store.save_vendor(key, name, status, expected_gst, notes, actor=reviewer(), qst_number=qst_number,
                      pst_number=pst_number, province=province)  # fmt: skip
    url = "/vendors?" + urlencode({"open": key}) + "#vendor"
    if back.startswith("/vendors?"):
        url = back
    return done(request, url, flash_message(f"Saved {name.rstrip('.')}.", "ok", icon="storefront"))


@router.post("/vendors/master/upload")
async def master_upload(request: Request, file: UploadFile = File(...)) -> JSONResponse:  # noqa: B008
    token, problem = await receive_sheet(file)
    if problem:
        return refuse(problem)
    return done(request, f"/vendors?file={token}#import")


@router.post("/vendors/master/import")
async def master_import(request: Request) -> JSONResponse:
    """Import the ERP's vendor list: matched by name; ERP ID, GST/HST number, terms, status and default GL."""
    form = await posted_form(request)
    store = get_store(request)
    token, sheet = str(form.get("file") or ""), str(form.get("sheet") or "")
    try:
        _upload, df, _sheets = read_upload(token, sheet or None)
    except SheetError as exc:
        return refuse(str(exc))
    if df.empty:
        return refuse(NO_ROWS)
    chosen = picked_columns(form, MASTER_LABELS, list(df.columns), {})
    if "vendor_name" not in chosen:
        return refuse("Pick the column with the vendor name.")
    result = store.import_vendor_master(master_rows(df.to_dict("records"), chosen), actor=reviewer())
    drop_upload(token)
    notes = [
        flash_message(f"Vendor master: {result['added']} added, {result['updated']} updated.", "ok", icon="storefront")
    ]
    dupes = result["duplicates"]
    if dupes:
        notes.append(
            flash_message(
                f"{ui.plural(len(dupes), 'vendor')} {'appears' if len(dupes) == 1 else 'appear'} more than once in the "
                "file under similar names (merged here; one on hold keeps the vendor on hold). Often the same supplier "
                "set up twice in the ERP:",
                "warn",
                [" / ".join(n) for n in dupes[:8]],
            )
        )
    return done(request, "/vendors", *notes)
