"""Review queue: today's numbers, the invoices waiting for review, and the other lists (second approval, parked,
approved, failed / rejected).

Clicking an invoice opens ``/review/<id>`` (``pages/invoice.py``). Filters, search and sort are in the address
(``?show=attention&q=northwind&sort=due``), so a view can be bookmarked and Back works. Approving the clean invoices
in one go, and the small actions of the other lists, are POSTs answered with where to go next.
"""

from __future__ import annotations

import datetime as dt
import math
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ap_coder import recurring, ui
from ap_coder.bulk import bulk_approve, clean_candidates
from ap_coder.memory import vendor_key
from ap_coder.store import APPROVED, FAILED, PARKED, PENDING, REJECTED, REVIEW, Store, load_sample_setup
from ap_coder.tax import OUTSIDE_CANADA, PROVINCE_NAMES, province_label
from ap_coder.terms import DUE_SOON_DAYS, payment
from ap_coder.web.core import (
    TARGET_ACCURACY,
    approved_today,
    by_currency,
    done,
    first_name,
    flash_message,
    get_settings,
    get_store,
    invoice_dir,
    jobs,
    local_url,
    login,
    money,
    reference_or_none,
    refuse,
    render,
    reviewer,
)

router = APIRouter()

QUEUE_PAGE = 50  # rows drawn at once; more on request
APPROVED_SHOWN = 100
SHOWS = {"all": "All", "attention": "Needs attention", "ready": "Ready"}
SORTS = {"priority": "Priority", "due": "Due date: soonest", "amount": "Amount: high to low",
         "newest": "Newest invoice date", "vendor": "Vendor A–Z"}  # fmt: skip
TABS = ("review", "second", "parked", "approved", "other")


# --- The queue's rows -----------------------------------------------------------------------------------------------


def filter_queue(rows: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """The rows whose vendor, invoice number or file name contains ``query`` (any case)."""
    query = (query or "").strip().lower()
    if not query:
        return rows
    fields = ("vendor_name", "invoice_number", "file_name")
    return [r for r in rows if any(query in str(r.get(f) or "").lower() for f in fields)]


def sort_queue(rows: list[dict[str, Any]], order: str) -> list[dict[str, Any]]:
    """``rows`` in the order chosen (a key of ``SORTS``); "priority" keeps the store's: flagged first, then the
    lowest confidence."""
    if order == "due":
        return sorted(rows, key=lambda r: (not r.get("due_date"), r.get("due_date") or ""))
    if order == "amount":
        return sorted(rows, key=lambda r: -(r.get("grand_total") or 0))
    if order == "newest":
        return sorted(rows, key=lambda r: r.get("invoice_date") or "", reverse=True)
    if order == "vendor":
        return sorted(rows, key=lambda r: (r.get("vendor_name") or r.get("file_name") or "").lower())
    return rows


def confidence_pct(value: float, threshold: float) -> str:
    """The invoice's confidence as a percentage, rounded the usual way, so the queue and the messages agree. Where
    that rounding would mislead (84.9% shown as the 85% threshold it is below, or 99.6% as certainty) it keeps one
    decimal, rounded down."""
    value = max(0.0, min(1.0, value or 0.0))
    whole = int(f"{value * 100:.0f}")
    if (value < threshold and whole >= int(f"{threshold * 100:.0f}")) or (value < 1 and whole >= 100):
        return f"{math.floor(value * 1000 + 1e-9) / 10:.1f}%"
    return f"{whole}%"


def due_note(ai: dict[str, Any], default_days: int, vendor_terms: str = "") -> tuple[str, str]:
    """(text, tone) of when the invoice is due: tone err (overdue), warn (soon), ok (an early-payment discount still
    open) or "" (plain)."""
    if (ai.get("grand_total") or 0) < 0:  # a credit note is not paid: no due date, no discount
        return "Credit note", ""
    if (ai.get("grand_total") or 0) == 0:
        return "", ""
    p = payment(ai, default_days, vendor_terms)
    if p.discount_open():
        return f"{p.terms.discount_pct:g}% off until {p.discount_by:%b} {p.discount_by.day}", "ok"
    left = p.days_left()
    if left is None:
        return "", ""
    if left < 0:
        return f"Overdue {-left} day{'s' if left != -1 else ''}", "err"
    if left <= DUE_SOON_DAYS:
        return ("Due today" if left == 0 else f"Due in {left} day{'s' if left != 1 else ''}"), "warn"
    return f"Due {p.due:%b} {p.due.day}", ""


def queue_rows(store: Store, shown: list[dict[str, Any]], threshold: float) -> list[dict[str, Any]]:
    """What each row of the queue shows (plain values; the template escapes them)."""
    ids = [i["id"] for i in shown]
    outputs = {  # a sent-back invoice shows the first approver's corrections, as its review screen does
        r["id"]: r["final_output"] or r["ai_output"] or {}
        for r in store.invoice_columns(("id", "ai_output", "final_output"), ids=ids)
    }
    default_days = store.default_terms_days()
    rows = []
    for inv in shown:
        ai = outputs.get(inv["id"], {})
        due, tone = due_note(ai, default_days, store.vendor_terms(vendor_key(ai.get("vendor_name") or "")))
        prov = ai.get("ship_to_province") or ai.get("supplier_province") or ""
        confidence = max(0.0, min(1.0, inv.get("adjusted_confidence") or 0.0))
        rows.append({
            "id": inv["id"],
            "name": inv.get("vendor_name") or inv.get("file_name") or "",
            "number": inv.get("invoice_number") or "—",
            "date": inv.get("invoice_date") or "—",
            "province": prov if prov in PROVINCE_NAMES else ("Int’l" if prov == OUTSIDE_CANADA else "—"),
            "province_title": province_label(prov, "Province unknown"),
            "taxes": [t for t in dict.fromkeys(t.get("tax_type", "") for t in ai.get("tax_lines", [])) if t],
            "flagged": bool(inv.get("requires_review")),
            "confidence": confidence,
            "confidence_text": confidence_pct(confidence, threshold),
            "confidence_ok": confidence >= threshold,
            "amount": money(inv.get("grand_total")),
            "currency": inv.get("currency") or "",
            "due": due,
            "due_tone": tone,
        })  # fmt: skip
    return rows


def today_pills(store: Store, invoices: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """What needs doing today beyond the queue itself, as (text, tone) chips (none when all is calm)."""
    today = dt.date.today().isoformat()
    soon = (dt.date.today() + dt.timedelta(days=DUE_SOON_DAYS)).isoformat()
    me = reviewer().strip().lower()
    # Credit notes are not paid, so they are never past due (as on the queue rows).
    active = [i for i in invoices if i["status"] in (REVIEW, PARKED, PENDING) and (i["grand_total"] or 0) > 0]
    overdue = sum(1 for i in active if i["due_date"] and i["due_date"] < today)
    due_soon = sum(1 for i in active if i["due_date"] and today <= i["due_date"] <= soon)
    follow_ups = sum(1 for i in store.parked() if i.get("follow_up") and i["follow_up"] <= today)
    second = sum(1 for i in invoices if i["status"] == PENDING and (i["reviewer"] or "").strip().lower() != me)
    to_export = len(store.unexported_approved())
    late = [r for r in recurring.detect(store.vendor_invoice_dates()) if r.status == recurring.LATE]
    pills = [
        (f"{overdue} past due, not approved yet", "err") if overdue else None,
        (f"{due_soon} due within {DUE_SOON_DAYS} days", "warn") if due_soon else None,
        (f"{follow_ups} parked to follow up", "warn") if follow_ups else None,
        (f"{second} waiting for your second approval", "info") if second else None,
        (f"{to_export} approved, ready to export", "gray") if to_export else None,
        (f"{ui.plural(len(late), 'regular invoice')} late (Vendors page): "
         + ", ".join(r.vendor_name for r in late[:2]) + ("…" if len(late) > 2 else ""), "gray") if late else None,
    ]  # fmt: skip
    return [p for p in pills if p]


def kpis(store: Store, pending: list[dict[str, Any]], flagged: list[dict[str, Any]],
         approved: list[dict[str, Any]]) -> list[dict[str, Any]]:  # fmt: skip
    """The four figures at the top: waiting, approved today, coding accuracy, lessons learned."""
    metrics = store.metrics()
    done_today = approved_today(store)
    total_today = done_today + len(pending)
    accuracy = metrics["line_accuracy"]
    corrections = metrics["lines_corrected"]
    return [
        {"label": "Waiting", "value": f"{len(pending):,}", "sub": f"{len(flagged)} flagged by checks"},
        {"label": "Approved today", "value": f"{done_today:,}",
         "sub": f"of {total_today:,} today · {len(approved):,} approved in all",
         "bar": done_today / total_today if total_today else 0.0},
        {"label": "Coding accuracy", "value": "—" if accuracy is None else f"{accuracy:.0%}",
         "sub": "after the first approvals" if accuracy is None else "vs 90% target",
         "delta": "" if accuracy is None else f"{(accuracy - TARGET_ACCURACY) * 100:+.1f} pts".replace("-", "−"),
         "good": accuracy is not None and accuracy >= TARGET_ACCURACY},
        {"label": "Lessons learned", "value": f"{metrics['lines_reviewed']:,}",
         "sub": f"{corrections} correction{'s' if corrections != 1 else ''} taught"},
    ]  # fmt: skip


def view_url(**params: Any) -> str:
    """/review with the given filters (empty ones left out)."""
    query = urlencode({k: v for k, v in params.items() if v not in (None, "", "all", "priority", "review")})
    return "/review" + (f"?{query}" if query else "")


# --- The page ------------------------------------------------------------------------------------------------------


@router.get("/review", response_class=HTMLResponse)
def review_page(request: Request, tab: str = "review", show: str = "all", q: str = "", sort: str = "priority",
                limit: int = QUEUE_PAGE) -> HTMLResponse:  # fmt: skip
    store, settings = get_store(request), get_settings(request)
    reference = reference_or_none(store)
    invoices = store.list_invoices()
    if reference is None or not invoices:
        return _getting_started(request, store, invoices)
    tab = tab if tab in TABS else "review"
    show = show if show in SHOWS else "all"
    sort = sort if sort in SORTS else "priority"
    limit = max(QUEUE_PAGE, min(limit, 5000))

    pending = [i for i in invoices if i["status"] == REVIEW]
    flagged = [i for i in pending if i["requires_review"]]
    approved = [i for i in invoices if i["status"] == APPROVED]
    awaiting = [i for i in invoices if i["status"] == PENDING]
    parked = [i for i in invoices if i["status"] == PARKED]
    others = [i for i in invoices if i["status"] in (FAILED, REJECTED)]
    minutes = len(flagged) * 2 + (len(pending) - len(flagged))
    hello = f"{ui.greeting()}, {first_name()}"
    waiting = f"{ui.plural(len(pending), 'invoice')} waiting, about {ui.plural(minutes, 'minute')} of review"
    lead = f"{hello} · {waiting}" if pending else f"{hello} · your queue is clear."

    tabs = [("review", f"To review · {len(pending)}")]
    if awaiting or store.approval_limit():
        tabs.append(("second", f"Second approval · {len(awaiting)}"))
    if parked:
        tabs.append(("parked", f"Parked · {len(parked)}"))
    tabs += [("approved", f"Approved · {len(approved)}"), ("other", f"Failed / rejected · {len(others)}")]
    if tab not in dict(tabs):
        tab = "review"

    context: dict[str, Any] = {"tab": tab, "tabs": tabs, "lead": lead, "show": show, "q": q, "sort": sort,
                               "kpis": kpis(store, pending, flagged, approved), "today": today_pills(store, invoices),
                               "shows": SHOWS, "sorts": SORTS, "view_url": view_url}  # fmt: skip
    if tab == "review":
        threshold = settings.engine.review_threshold
        shown = {"attention": flagged, "ready": [i for i in pending if not i["requires_review"]]}.get(show, pending)
        shown = sort_queue(filter_queue(shown, q), sort)
        candidates = clean_candidates(store) if show != "attention" else []
        context.update(
            pending=pending,
            counts={"all": len(pending), "attention": len(flagged), "ready": len(pending) - len(flagged)},
            rows=queue_rows(store, shown[:limit], threshold),
            more=max(0, len(shown) - limit),
            next_limit=limit + QUEUE_PAGE,
            threshold=threshold,
            candidates=candidates if len(candidates) >= 2 else [],
            candidates_total=by_currency(candidates),
        )  # fmt: skip
    elif tab == "approved":
        recent = sorted(approved, key=lambda i: i["reviewed_at"] or "", reverse=True)[:APPROVED_SHOWN]
        context.update(approved=recent, approved_total=len(approved), to_export=len(store.unexported_approved()))
    elif tab == "other":
        context.update(others=[_other(i) for i in others])
    elif tab == "parked":
        today = dt.date.today().isoformat()
        context.update(parked=[{**i, "late": bool(i["follow_up"] and i["follow_up"] <= today)} for i in store.parked()])
    elif tab == "second":
        me, my_login = reviewer(), login()
        awaiting = [_second(store, i, me, my_login) for i in awaiting]
        context.update(limit_amount=store.approval_limit(), awaiting=awaiting)
    return render(request, "review.html", "review", **context)


def _other(inv: dict[str, Any]) -> dict[str, Any]:
    error = (inv.get("error") or "No reason recorded.").strip()
    first, _, details = error.partition("\n")
    long = len(first) > 240
    return {**inv, "first": first[:240] + ("…" if long else ""), "details": error if details.strip() or long else ""}


def _second(store: Store, inv: dict[str, Any], me: str, my_login: str) -> dict[str, Any]:
    """An invoice waiting for its second approval, and whether this reviewer may give it (not the first approver)."""
    first = next((e for e in store.events(inv["id"]) if e["action"] == "approved"), None)
    first_login = ((first or {}).get("detail") or {}).get("login") or ""
    same = (me or "").strip().casefold() == (inv["reviewer"] or "").strip().casefold() or (
        bool(my_login) and my_login.casefold() == first_login.casefold()
    )
    return {**inv, "same": same}


def _getting_started(request: Request, store: Store, invoices: list[dict[str, Any]]) -> HTMLResponse:
    from ap_coder.demo import demo_available
    from ap_coder.web.pages.process import setup_steps

    steps = setup_steps(store, get_settings(request))
    has_invoices = bool(invoices)
    steps.append(("ok" if has_invoices else "todo", "Process your first invoices", "done" if has_invoices else "to do"))
    needed = [s for s, _, _ in steps if s != "opt"]  # as the Process page counts: the chat model is optional
    done_steps = sum(1 for s in needed if s == "ok")
    return render(
        request, "review_welcome.html", "review",
        greeting=f"{ui.greeting()}, {first_name()}",
        steps=steps,
        ring_value=done_steps / len(needed),
        ring_label=f"{done_steps}/{len(needed)}",
        chat_ready=steps[1][0] == "ok",
        waiting=sum(1 for i in invoices if i["status"] == REVIEW) if reference_or_none(store) is None else 0,
        demo_available=demo_available(),
        demo_count=store.demo_count(),
        where="/review",
    )  # fmt: skip


# --- Actions -------------------------------------------------------------------------------------------------------


def changed_meanwhile(request: Request, exc: Exception, back: str = "/review") -> JSONResponse:
    """Someone else acted on the invoice after the page was drawn: say so, and show where it stands now."""
    return done(request, back, f"Not done: someone else changed this invoice meanwhile ({exc}).", tone="warn")


@router.post("/review/bulk-approve")
def review_bulk_approve(request: Request, ids: list[int] = Form(default=[])) -> JSONResponse:  # noqa: B008
    """Approve the clean invoices the page listed, exactly as coded. Each is re-checked first; any that is no
    longer clean is left for a person, with the reason."""
    store, settings = get_store(request), get_settings(request)
    reference = reference_or_none(store)
    if reference is None or not ids:
        return refuse("Nothing to approve.")
    # an invoice with edits open on its review screen would be approved without them: leave it for that screen
    from ap_coder.web.pages.invoice import unsaved_edits

    editing = unsaved_edits(request)
    result = bulk_approve(store, reference, settings, [i for i in ids if i not in editing], reviewer(), login=login())
    result["skipped"] = [(i, "it has edits open on its review screen") for i in ids if i in editing] + result["skipped"]
    waiting = sum(1 for i in result["approved"] if (store.get_invoice(i) or {}).get("status") == PENDING)
    messages = [flash_message(
        f"Approved {ui.plural(len(result['approved']), 'invoice')}"
        + (f"; {waiting} wait{'s' if waiting == 1 else ''} for a second approval." if waiting else "."),
        "ok", icon="done_all")]  # fmt: skip
    if result["skipped"]:
        details = []
        for i, why in result["skipped"]:
            inv = store.get_invoice(i) or {}
            name = " · ".join(x for x in (inv.get("vendor_name"), inv.get("invoice_number")) if x) or f"#{i}"
            details.append(f"{name}: {why}")
        n = len(result["skipped"])
        messages.append(flash_message(f"{ui.plural(n, 'invoice')} {'was' if n == 1 else 'were'} not approved: "
                                      f"{'it needs' if n == 1 else 'they need'} a look.", "warn", details))  # fmt: skip
    return done(request, "/review", *messages)


@router.post("/review/sample-gl")
def review_sample_gl(request: Request, back: str = Form("/review")) -> JSONResponse:
    store = get_store(request)
    if store.list_accounts("gl_accounts"):  # a page left open while the clerk imported their own chart
        return refuse("You already have GL accounts: the sample setup is only for an empty list.")
    load_sample_setup(store)
    return done(
        request, local_url(back, "/review"), "Sample GL accounts loaded. Replace them with yours in GL accounts & tax."
    )


def delete_invoice(store: Store, invoice_id: int) -> None:
    """Delete an invoice; if its file is in the invoices folder, move it to ``invoices/deleted`` so the folder
    pick-up doesn't offer it again."""
    inv = store.get_invoice(invoice_id) or {}
    store.delete_invoice(invoice_id, actor=reviewer(), keep_approved=True)
    folder = invoice_dir(store)
    path = Path(inv.get("source_path") or "")
    try:
        if path.is_file() and path.resolve().parent == folder.resolve():
            target = folder / "deleted" / path.name
            target.parent.mkdir(exist_ok=True)
            n = 1
            while target.exists():
                target = target.with_name(f"{path.stem}_{n}{path.suffix}")
                n += 1
            path.rename(target)
    except OSError:
        pass  # the invoice is gone from the queue either way; the file just stays where it is


@router.post("/review/{invoice_id}/delete")
def review_delete(request: Request, invoice_id: int, back: str = Form("/review?tab=other")) -> JSONResponse:
    store = get_store(request)
    if store.get_invoice(invoice_id) is None:
        return refuse("That invoice is no longer in AP Coder.", 404)
    from ap_coder.web.pages.invoice import forget_draft

    try:
        delete_invoice(store, invoice_id)
    except ValueError as exc:  # approved by someone else since this Delete button was shown
        return changed_meanwhile(request, exc, back)
    forget_draft(request, invoice_id)
    return done(request, back, flash_message("Deleted. The file moved to invoices/deleted.", "ok", icon="delete"))


@router.post("/review/{invoice_id}/reopen")
def review_reopen(request: Request, invoice_id: int) -> JSONResponse:
    try:
        get_store(request).reopen(invoice_id, reviewer(), only_rejected=True)
    except ValueError as exc:
        return changed_meanwhile(request, exc, "/review?tab=other")
    return done(request, "/review?tab=other", flash_message("Back in the review queue.", "ok", icon="undo"))


@router.post("/review/{invoice_id}/retry")
def review_retry(request: Request, invoice_id: int) -> JSONResponse:
    """Read a failed invoice again (the new attempt replaces the failed one), as a Import invoices job."""
    from ap_coder.web.pages.process import JOB, process_files

    store = get_store(request)
    inv = store.get_invoice(invoice_id)
    if inv is None:
        return refuse("That invoice is no longer in AP Coder.", 404)
    path = Path(inv["source_path"])
    if not path.exists():
        return refuse(f"The original file is no longer at {path}.")
    if not jobs(request).get(JOB).start(process_files(store, get_settings(request), [path]), total=1):
        return refuse("AP Coder is already reading invoices. Wait for it to finish, then try again.", 409)
    return done(request, "/process", flash_message(f"Reading {path.name} again.", "info", icon="refresh"))


@router.post("/review/{invoice_id}/unpark")
def review_unpark(request: Request, invoice_id: int) -> JSONResponse:
    try:
        get_store(request).unpark_invoice(invoice_id, reviewer())
    except ValueError as exc:
        return changed_meanwhile(request, exc, "/review?tab=parked")
    back = flash_message(f"Invoice #{invoice_id} is back in the review queue.", "ok", icon="play_circle")
    return done(request, "/review?tab=parked", back)


@router.post("/review/{invoice_id}/final-approve")
def review_final_approve(request: Request, invoice_id: int) -> JSONResponse:
    store = get_store(request)
    inv = store.get_invoice(invoice_id) or {}
    try:
        store.final_approve(invoice_id, reviewer(), login=login())
    except PermissionError as exc:  # the first approver (or their login): nobody changed anything meanwhile
        return done(request, "/review?tab=second", f"Not done: {exc}.", tone="warn")
    except ValueError as exc:
        return changed_meanwhile(request, exc, "/review?tab=second")
    return done(request, "/review?tab=second",
                flash_message(f"{inv.get('vendor_name') or 'The invoice'} approved. It is ready to export.", "ok",
                              icon="how_to_reg"))  # fmt: skip


@router.post("/review/{invoice_id}/send-back")
def review_send_back(request: Request, invoice_id: int, reason: str = Form("")) -> JSONResponse:
    try:
        get_store(request).send_back(invoice_id, reviewer(), reason.strip())
    except ValueError as exc:
        return changed_meanwhile(request, exc, "/review?tab=second")
    return done(request, "/review?tab=second", flash_message("Sent back to the review queue.", "ok", icon="undo"))
