"""Review queue: today's numbers, the invoices waiting for review, and the other lists (second approval, parked,
approved, failed / rejected).

Clicking an invoice opens ``/review/<id>`` (``pages/invoice.py``). Filters, search and sort are in the address
(``?show=attention&q=northwind&sort=due``), so a view can be bookmarked and Back works. Approving the clean invoices
in one go, and the small actions of the other lists, are POSTs answered with where to go next.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from ap_coder.coding.bulk import bulk_approve, issues_today, sort_for_bulk
from ap_coder.coding.review import codes_to_pick, uses_cost_centers
from ap_coder.coding.tax import OUTSIDE_CANADA, PROVINCE_NAMES, province_label, stored_supply
from ap_coder.coding.terms import DUE_SOON_DAYS, LONG_OVERDUE_DAYS, overdue_since, payment
from ap_coder.core import audit, ui
from ap_coder.core.memory import vendor_key
from ap_coder.core.reference_data import ReferenceData
from ap_coder.core.store import (
    APPROVED,
    FAILED,
    PARKED,
    PENDING,
    REJECTED,
    REVIEW,
    Store,
    fold_text,
    load_sample_setup,
)
from ap_coder.erp import recurring
from ap_coder.web.core import (
    TARGET_ACCURACY,
    accuracy_note,
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
    one_connection,
    posted_form,
    reference_or_none,
    refuse,
    render,
    reviewer,
)
from ap_coder.web.invoice_view import document_kind

router = APIRouter()

QUEUE_PAGE = 50  # rows drawn at once; more on request
APPROVED_SHOWN = 100
SHOWS = {"all": "All", "attention": "Needs attention", "ready": "Ready"}
SORTS = {"priority": "Priority", "due": "Due date: soonest", "amount": "Amount: high to low",
         "newest": "Newest invoice date", "vendor": "Vendor A–Z"}  # fmt: skip
TABS = ("review", "second", "parked", "approved", "other")


# --- The queue's rows -----------------------------------------------------------------------------------------------


def filter_queue(rows: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """The rows whose vendor, invoice number or file name contains ``query`` (any case, accents or not: "quebec"
    finds "Hydro-Québec", B-456)."""
    query = fold_text((query or "").strip())
    if not query:
        return rows
    fields = ("vendor_name", "invoice_number", "file_name")
    return [r for r in rows if any(query in fold_text(str(r.get(f) or "")) for f in fields)]


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
        return sorted(rows, key=lambda r: fold_text(r.get("vendor_name") or r.get("file_name") or ""))
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
    if left < -LONG_OVERDUE_DAYS:  # "Overdue since Dec 2012", not "Overdue 5042 days" (B-469)
        return f"Overdue since {overdue_since(p.due)}", "err"
    if left < 0:
        return f"Overdue {-left} day{'s' if left != -1 else ''}", "err"
    if left <= DUE_SOON_DAYS:
        return ("Due today" if left == 0 else f"Due in {left} day{'s' if left != 1 else ''}"), "warn"
    return f"Due {p.due:%b} {p.due.day}", ""


_TO_FIX: dict[int, tuple[int, tuple[str, int]]] = {}  # ``_to_fix`` by invoice id: (what it was asked, the answer)
_PAY_FROM: dict[int, tuple[int, str]] = {}  # the Pay from part of ``attention`` by invoice id: (asked, why)


def _to_fix(invoice_id: int, final: str | None, ai: str | None, validation: str | None,
            cost_centers: bool) -> tuple[str, int]:  # fmt: skip
    """(what the lines still need to be approved, how many errors the checks found) of one invoice, from its stored
    coding and checks (their JSON text). Remembered with the hash of that text: the queue asks it of every waiting
    invoice on each load (also to count them on the other tabs), and decoding them all again took most of the
    page's time (0.34 s at 5,000 waiting); a changed invoice is asked again."""
    asked = hash((cost_centers, final, ai, validation))
    known = _TO_FIX.get(invoice_id)
    if known and known[0] == asked:
        return known[1]
    coded = (json.loads(final) if final else None) or (json.loads(ai) if ai else None) or {}
    issues = ((json.loads(validation) if validation else None) or {}).get("issues") or []
    answer = (
        codes_to_pick(coded.get("line_items"), cost_centers, hint=False).removesuffix(" to approve"),
        sum(1 for i in issues if i.get("severity") == "error"),
    )
    _TO_FIX[invoice_id] = (asked, answer)
    return answer


def attention(store: Store, reference: ReferenceData | None, pending: list[dict[str, Any]],
              threshold: float | None = None,
              today: dict[int, list[dict[str, Any]]] | None = None) -> dict[int, str]:  # fmt: skip
    """Why each invoice of ``pending`` (the queue) needs attention, by id; one not listed is Ready: its review
    screen's Approve is open as it stands. The lines' codes are asked as the review screen asks (``codes_to_pick``),
    then the checks' flag (an error, or confidence under ``threshold``; None: the flag alone, without its words).
    The checks are today's (``bulk.issues_today``, or ``today`` when the caller has it): an invoice whose stored
    checks were made against other settings is checked again (B-815)."""
    from ap_coder.coding import banks
    from ap_coder.coding.bulk import issues_today

    if today is None:
        today = issues_today(store, [i["id"] for i in pending if not i.get("requires_review")], reference)

    cost_centers = reference is not None and uses_cost_centers(reference)
    accounts = store.bank_accounts()
    last: dict[str, str] = {}  # this vendor's last Pay from account, asked once per vendor
    # what the Pay from answer depends on besides the invoice: the bank accounts, and the approvals (a vendor's last
    # Pay from account)
    bank_stamp = (json.dumps(accounts, sort_keys=True, default=str), store.approvals_stamp()) if accounts else None
    stored = {  # one query, not one per invoice; the text is decoded only for an invoice not asked before
        r["id"]: r
        for r in store.invoice_columns(
            ("id", "validation", "ai_output", "final_output"), ids=[i["id"] for i in pending], decode=False
        )
    }
    out = {}
    for inv in pending:
        r = stored.get(inv["id"]) or {}
        checked = json.dumps({"issues": today[inv["id"]]}) if inv["id"] in today else r.get("validation")
        to_pick, errors = _to_fix(inv["id"], r.get("final_output"), r.get("ai_output"), checked, cost_centers)
        low = threshold is not None and (inv.get("adjusted_confidence") or 0.0) < threshold
        bank = ""
        if any(a["active"] for a in accounts):  # the review screen's Pay from rule (B-685): its Approve, its Ready
            asked = hash((bank_stamp, r.get("final_output"), r.get("ai_output")))
            known = _PAY_FROM.get(inv["id"])
            if known and known[0] == asked:  # remembered while nothing it depends on changed (B-690)
                bank = known[1]
            else:
                final = json.loads(r["final_output"]) if r.get("final_output") else None
                doc = final or (json.loads(r["ai_output"]) if r.get("ai_output") else {}) or {}
                key = vendor_key(doc.get("vendor_name") or inv.get("vendor_name") or "")
                if key not in last:
                    last[key] = store.last_pay_from(key)
                bank = banks.blocking(banks.opening_choice(doc, bool(final), accounts, last[key]), accounts)
                _PAY_FROM[inv["id"]] = (asked, bank)
        why = [
            to_pick,
            bank,
            f"{ui.plural(errors, 'error')} to fix" if errors else "",
            f"confidence under {threshold:.0%}" if low and inv.get("requires_review") else "",
        ]
        why = [w for w in why if w]
        if why or inv.get("requires_review"):
            out[inv["id"]] = (why[0][0].upper() + "; ".join(why)[1:]) if why else "Flagged by the checks"
    return out


def draft_reasons(request: Request, pending: list[dict[str, Any]]) -> dict[int, str]:
    """The queue's reason for each invoice of ``pending`` with edits kept on this person's review screen: the draft's
    own check, not the stored one ("Pick a GL account for line 4" after line 4 was coded in the draft, B-996). Never
    Ready: bulk approval leaves an invoice with a draft alone, and the edits are approved on its screen."""
    from ap_coder.web.pages.invoice import my_drafts

    ids = {i["id"] for i in pending}
    out = {}
    for invoice_id, kept in my_drafts(request).items():
        if invoice_id not in ids or not isinstance(kept.get("check"), dict):
            continue
        why = str(kept["check"].get("why") or "")
        if why[1:2].islower():  # "Pick a GL account…" → "pick a GL account…"; "JD Edwards…" stays
            why = why[:1].lower() + why[1:]
        out[invoice_id] = "Edits not approved yet: " + (why or "ready to approve")
    return out


def left_out(pending: list[dict[str, Any]], reasons: dict[int, str], left: dict[int, str]) -> str:
    """Why the queue's Ready invoices not offered for bulk approval are left out (``sort_for_bulk``'s ``left``):
    "1 credit note and 2 with a warning left for a person", or "" when none is."""
    ready = [left[i["id"]] for i in pending if i["id"] not in reasons and i["id"] in left]
    credit, warned = ready.count("credit note"), ready.count("warning")
    parts = [ui.plural(credit, "credit note") if credit else "", f"{warned} with a warning" if warned else ""]
    text = " and ".join(p for p in parts if p)
    return f"{text} left for a person" if text else ""


NO_VENDOR = "No vendor name"


def queue_rows(store: Store, shown: list[dict[str, Any]], threshold: float,
               reasons: dict[int, str] | None = None) -> list[dict[str, Any]]:  # fmt: skip
    """What each row of the queue shows (plain values; the template escapes them). ``reasons``: ``attention()``."""
    reasons = reasons or {}
    ids = [i["id"] for i in shown]
    outputs = {  # a sent-back invoice shows the first approver's corrections, as its review screen does
        r["id"]: r["final_output"] or r["ai_output"] or {}
        for r in store.invoice_columns(("id", "ai_output", "final_output"), ids=ids)
    }
    sources = store.layout_sources(ids)
    vendor_checks = store.vendor_name_checks(ids)
    default_days = store.default_terms_days()
    terms = store.all_vendor_terms()  # read once, not once per row
    rows = []
    for inv in shown:
        ai = outputs.get(inv["id"], {})
        due, tone = due_note(ai, default_days, terms.get(vendor_key(ai.get("vendor_name") or ""), ""))
        prov = stored_supply(ai)
        confidence = max(0.0, min(1.0, inv.get("adjusted_confidence") or 0.0))
        rows.append({
            "id": inv["id"],
            # never the file's name as the vendor; a name the readers are not sure of says so (B-471)
            "name": inv.get("vendor_name") or NO_VENDOR,
            "file": inv.get("file_name") or "",
            "name_check": not inv.get("vendor_name") or vendor_checks.get(inv["id"]) in ("check", "missing"),
            "number": inv.get("invoice_number") or "—",
            "date": inv.get("invoice_date") or "—",
            "province": prov if prov in PROVINCE_NAMES else ("Int’l" if prov == OUTSIDE_CANADA else "—"),
            "province_title": province_label(prov, "Province unknown"),
            "taxes": [t for t in dict.fromkeys(t.get("tax_type", "") for t in ai.get("tax_lines", [])) if t],
            "flagged": inv["id"] in reasons,
            "reason": reasons.get(inv["id"], ""),
            "confidence": confidence,
            "confidence_text": confidence_pct(confidence, threshold),
            "confidence_ok": confidence >= threshold,
            "amount": money(inv.get("grand_total")),
            "currency": inv.get("currency") or "",
            "due": due,
            "due_tone": tone,
            "kind": document_kind(sources.get(inv["id"], "")),
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
         approved: list[dict[str, Any]], awaiting: list[dict[str, Any]] = (),
         settings: Any = None) -> list[dict[str, Any]]:  # fmt: skip
    """The four figures at the top: waiting, approved today, coding accuracy, the coding memory (B-512). Approved today
    counts this reviewer's approvals, a first approval waiting for its second (``awaiting``) included, and says so in
    plain words (B-517)."""
    metrics = store.metrics()
    done_today = approved_today(store)
    total_today = done_today + len(pending)
    accuracy = metrics["line_accuracy"]
    corrections = metrics["lines_corrected"]
    note, compared = ("", True)
    if settings is not None and accuracy is not None:
        note, compared = accuracy_note(settings, metrics["lines_measured"])
    # Of mine today, those still waiting for their second approval: said apart, as the Approved tab lists only the
    # fully approved (B-1006).
    me, today = reviewer().strip().casefold(), dt.date.today().isoformat()
    mine_waiting = sum(
        1
        for i in awaiting
        if (i.get("reviewer") or "").strip().casefold() == me and (i.get("reviewed_at") or "") >= today
    )
    waits = f" · {mine_waiting:,} wait{'s' if mine_waiting == 1 else ''} for a second approval" if mine_waiting else ""
    return [
        {"label": "Waiting", "value": f"{len(pending):,}", "sub": f"{len(flagged)} need attention"},
        {"label": "Approved today", "value": f"{done_today:,}",
         "sub": f"{max(done_today - mine_waiting, 0):,} fully approved{waits} · {len(pending):,} still to review",
         "bar": done_today / total_today if total_today else 0.0, "wrap": True},
        {"label": "Coding accuracy", "value": "—" if accuracy is None else f"{accuracy:.1%}",  # as on Learning
         "sub": "after the first approvals" if accuracy is None
                else ("vs 90% target" + (f" · {note}" if note else "")) if compared else note,
         "delta": "" if accuracy is None or not compared else f"{(accuracy - TARGET_ACCURACY) * 100:+.1f} pts".replace(
             "-", "−"),
         "good": accuracy is not None and accuracy >= TARGET_ACCURACY, "wrap": bool(note)},
        {"label": "Coding memory", "value": f"{metrics['lines_reviewed']:,}",
         "sub": f"lines reviewed · {corrections:,} corrected"},  # (short: one line at 1366, B-532)
    ]  # fmt: skip


def view_url(**params: Any) -> str:
    """/review with the given filters (empty ones left out)."""
    query = urlencode({k: v for k, v in params.items() if v not in (None, "", "all", "priority", "review")})
    return "/review" + (f"?{query}" if query else "")


# --- The page ------------------------------------------------------------------------------------------------------


@router.get("/review", response_class=HTMLResponse)
@one_connection
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

    threshold = settings.engine.review_threshold
    pending = [i for i in invoices if i["status"] == REVIEW]
    # The checks as they are now, of the invoices not flagged (a flagged one is never Ready nor offered in bulk).
    today = issues_today(store, [i["id"] for i in pending if not i["requires_review"]], reference, settings)
    reasons = attention(store, reference, pending, threshold, today)  # Ready means its Approve is open
    reasons.update(draft_reasons(request, pending))  # an invoice with edits kept: what its screen says (B-996)
    flagged = [i for i in pending if i["id"] in reasons]
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

    from ap_coder.web.pages.process import name_step

    unnamed = reviewer() if name_step()[0] != "ok" else ""  # approvals carry the computer login until a name is given
    context: dict[str, Any] = {"tab": tab, "tabs": tabs, "lead": lead, "show": show, "q": q, "sort": sort,
                               "unnamed": unnamed,
                               "kpis": kpis(store, pending, flagged, approved, awaiting, settings),
                               "today": today_pills(store, invoices),
                               "shows": SHOWS, "sorts": SORTS, "view_url": view_url}  # fmt: skip
    if tab == "review":
        shown = {"attention": flagged, "ready": [i for i in pending if i["id"] not in reasons]}.get(show, pending)
        shown = sort_queue(filter_queue(shown, q), sort)
        candidates, left = sort_for_bulk(store, pending, today) if show != "attention" else ([], {})
        context.update(
            pending=pending,
            counts={"all": len(pending), "attention": len(flagged), "ready": len(pending) - len(flagged)},
            rows=queue_rows(store, shown[:limit], threshold, reasons),
            more=max(0, len(shown) - limit),
            next_limit=limit + QUEUE_PAGE,
            threshold=threshold,
            candidates=candidates if len(candidates) >= 2 else [],
            candidates_total=by_currency(candidates),
            candidates_left=left_out(pending, reasons, left),
        )  # fmt: skip
    elif tab == "approved":
        recent = sorted(approved, key=lambda i: i["reviewed_at"] or "", reverse=True)[:APPROVED_SHOWN]
        context.update(approved=recent, approved_total=len(approved), to_export=len(store.unexported_approved()),
                       awaiting_count=len(awaiting))  # fmt: skip
    elif tab == "other":
        context.update(others=[_other(i) for i in others])
    elif tab == "parked":
        today = dt.date.today().isoformat()
        context.update(parked=[{**i, "late": bool(i["follow_up"] and i["follow_up"] <= today)} for i in store.parked()])
    elif tab == "second":
        me, my_login = reviewer(), login()
        firsts = store.latest_events("approved", [i["id"] for i in awaiting])  # one query, not one per invoice
        awaiting = [_second(i, firsts.get(i["id"]), me, my_login) for i in awaiting]
        context.update(limit_amount=store.approval_limit(), awaiting=awaiting)
    return render(request, "review.html", "review", **context)


def _other(inv: dict[str, Any]) -> dict[str, Any]:
    error = (inv.get("error") or "No reason recorded.").strip()
    first, _, details = error.partition("\n")
    long = len(first) > 240
    return {**inv, "first": first[:240] + ("…" if long else ""), "details": error if details.strip() or long else ""}


def _second(inv: dict[str, Any], first: dict[str, Any] | None, me: str, my_login: str) -> dict[str, Any]:
    """An invoice waiting for its second approval, and whether this reviewer may give it (not the first approver,
    whose ``approved`` event is ``first``)."""
    first_login = ((first or {}).get("detail") or {}).get("login") or ""
    same = (me or "").strip().casefold() == (inv["reviewer"] or "").strip().casefold() or (
        bool(my_login) and my_login.casefold() == first_login.casefold()
    )
    detail = (first or {}).get("detail") or {}
    why = audit.second_reason(detail) if detail.get("needs_second") else ""
    return {**inv, "same": same, "approval": (first or {}).get("id") or "", "second_why": why,
            "approved_over": audit.approved_over(detail)}  # fmt: skip


def _getting_started(request: Request, store: Store, invoices: list[dict[str, Any]]) -> HTMLResponse:
    from ap_coder.ops.demo import demo_available
    from ap_coder.web.pages.process import LM_STUDIO_STEPS, STEP_LINKS, first_steps, next_step, step_state

    steps, done_steps, needed = first_steps(store, get_settings(request), bool(invoices))  # as Import invoices counts
    named = step_state(steps, "Your name") == "ok"  # (before it, first_name() is the login: "Good morning, root")
    return render(
        request, "review_welcome.html", "review",
        greeting=f"{ui.greeting()}, {first_name()}" if named else ui.greeting(),
        next=next_step(steps),
        step_links=STEP_LINKS,
        lm_studio_steps=LM_STUDIO_STEPS,
        steps=steps,
        ring_value=done_steps / needed,
        ring_label=f"{done_steps}/{needed}",
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
async def review_bulk_approve(request: Request) -> JSONResponse:
    """Approve the clean invoices the page listed, exactly as coded. Each is re-checked first; any that is no
    longer clean is left for a person, with the reason."""
    try:  # one field per invoice the page lists, however many (B-461)
        ids = [int(str(i)) for i in (await posted_form(request)).getlist("ids")]
    except ValueError:
        return refuse("Nothing to approve.")
    return await run_in_threadpool(_bulk_approve, request, ids)


def _bulk_approve(request: Request, ids: list[int]) -> JSONResponse:
    with get_store(request).session():  # one connection for the whole approval, however many invoices
        return _bulk_approve_now(request, ids)


def _bulk_approve_now(request: Request, ids: list[int]) -> JSONResponse:
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
    load_sample_setup(store, cost_centers=False)  # each line needs only a GL account (D-53)
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
    from ap_coder.web.pages.invoice import forget_everyones_draft

    try:
        delete_invoice(store, invoice_id)
    except ValueError as exc:  # approved by someone else since this Delete button was shown
        return changed_meanwhile(request, exc, back)
    forget_everyones_draft(request, invoice_id)
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
    if inv["status"] != FAILED:  # a stale page's button: reading it again would add a second copy to the queue
        return refuse("Retry reads again only an invoice that could not be read: reload the page.", 409)
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
def review_final_approve(request: Request, invoice_id: int, approval: str | None = Form(None)) -> JSONResponse:
    """``approval``: the first approval the page showed (B-654); a page that posts none is not checked."""
    from ap_coder.core.store import ApprovedAgain

    store = get_store(request)
    inv = store.get_invoice(invoice_id) or {}
    try:
        store.final_approve(invoice_id, reviewer(), login=login(), shown=approval)
    except PermissionError as exc:  # the first approver (or their login): nobody changed anything meanwhile
        return done(request, "/review?tab=second", f"Not done: {exc}.", tone="warn")
    except ApprovedAgain:
        now = store.get_invoice(invoice_id) or {}
        by = f" by {now['reviewer']}" if now.get("reviewer") else ""
        return done(request, "/review?tab=second",
                    f"Not done: this invoice was sent back and approved again{by} since you opened this page, so "
                    "what waits now is not what you looked at. Reload to see the latest version, then give the "
                    "final approval.", tone="warn")  # fmt: skip
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
