"""Review queue: today's numbers, the invoices waiting for review, and the other lists (second approval, parked,
approved, failed / rejected).

Clicking an invoice opens ``/review/<id>`` (``pages/invoice.py``). Filters, search and sort are in the address
(``?show=attention&q=northwind&sort=due``), so a view can be bookmarked and Back works. Approving the clean invoices
in one go, and the small actions of the other lists, are POSTs answered with where to go next.
"""

from __future__ import annotations

import datetime as dt
import json
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import anyio
from fastapi import Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from ap_coder.coding import bulk
from ap_coder.coding.bulk import needs_a_person, queue_checks, sort_for_bulk
from ap_coder.coding.review import uses_cost_centers
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
    bulk_jobs,
    fold_text,
    load_sample_setup,
    reports,
)
from ap_coder.core.store.users import same_approver
from ap_coder.erp import recurring
from ap_coder.web import identity, jobs
from ap_coder.web.access import person_key
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
    local_url,
    login,
    money,
    one_connection,
    page_router,
    posted_form,
    reference_or_none,
    refuse,
    render,
    reviewer,
    server_mode,
)
from ap_coder.web.identity import shown_path
from ap_coder.web.invoice_view import confidence_pct, document_kind

router = page_router()  # each plain handler: one connection a request

QUEUE_PAGE = 50  # rows drawn at once; more on request
APPROVED_SHOWN = 100
SHOWS = {"all": "All", "attention": "Needs attention", "ready": "Ready"}
SORTS = {"priority": "Priority", "due": "Due date: soonest", "amount": "Amount: high to low",
         "newest": "Newest invoice date", "vendor": "Vendor A–Z"}  # fmt: skip
TABS = ("review", "second", "parked", "approved", "other")
LATE_SETTLE = 60.0  # seconds the regular invoices said late may be behind a change (D-164, D-165)


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


TO_PICK_CODES = frozenset({"GL_UNASSIGNED", "CC_UNASSIGNED"})  # a line to code: "Pick a GL account…" says it
FLAGGED_BEFORE = "Flagged when it was last checked: open it to check it again"  # (B-1522)


def _to_fix(facts: dict[str, Any], cost_centers: bool) -> tuple[str, int, int]:
    """(what the lines still need to be approved, how many errors the checks found, how many other findings bulk
    approval leaves for a person: a warning, or one that always goes to a person, ``bulk.needs_a_person``, B-1027)
    of one invoice, from what the queue reads of it (``bulk.queue_checks``: its findings by today's checks, and what
    its lines need, worked out in the invoices' summary, D-155)."""
    errors = others = 0  # (in one pass: 2,700 invoices of a dozen findings each, at each visit of the queue)
    for i in facts.get("found") or []:
        severity, code = i["severity"], i["code"]
        if severity == "error":
            errors += 1
        # the lines to code are said in "Pick a GL account…", never counted again as warnings (B-1530)
        elif needs_a_person(severity, code) and not (severity == "warning" and code in TO_PICK_CODES):
            others += 1
    return facts.get("q_pick_cc" if cost_centers else "q_pick") or "", errors, others


def attention(store: Store, reference: ReferenceData | None, pending: list[dict[str, Any]],
              threshold: float | None = None, checks: dict[int, dict[str, Any]] | None = None,
              recheck: bool | None = None) -> dict[int, str]:  # fmt: skip
    """Why each invoice of ``pending`` (the queue) needs attention, by id; one not listed is Ready: its review
    screen's Approve is open as it stands, and nothing in its checks keeps bulk approval from taking it (B-1027).
    The lines' codes are asked as the review screen asks (``codes_to_pick``), then the checks' flag (an error, or
    confidence under ``threshold``; None: the flag alone, without its words).
    The checks are today's (``bulk.queue_checks``, or ``checks`` when the caller has it; ``recheck`` as there): an
    invoice whose stored checks were made against other settings is checked again (B-815)."""
    from ap_coder.coding import banks

    if checks is None:
        checks = queue_checks(store, [i["id"] for i in pending], recheck=recheck)[0]
    cost_centers = reference is not None and uses_cost_centers(reference)
    accounts = store.bank_accounts()
    paying = any(a["active"] for a in accounts)  # the review screen's Pay from rule (B-685): its Approve, its Ready
    pay = {i["id"]: _pay(checks.get(i["id"]) or {}, i) for i in pending} if paying else {}
    last = store.last_pay_froms(sorted({p[3] for p in pay.values()})) if paying else {}  # each vendor's last one
    blocks: dict[tuple[str, bool, str, str], str] = {}  # what stops each Pay from asked, worked out once a call
    out = {}
    for inv in pending:
        to_pick, errors, warned = _to_fix(checks.get(inv["id"]) or {}, cost_centers)
        low = threshold is not None and (inv.get("adjusted_confidence") or 0.0) < threshold
        bank = ""
        if paying:
            code, approved, currency, key = pay[inv["id"]]
            asked = (code, approved, currency, last.get(key, ""))
            if asked not in blocks:
                choice = banks.opening_choice({"pay_from_account": code, "currency": currency}, approved, accounts,
                                              asked[3])  # fmt: skip
                blocks[asked] = banks.blocking(choice, accounts)
            bank = blocks[asked]
        why = [
            to_pick,
            bank,
            f"{ui.plural(errors, 'error')} to fix" if errors else "",
            # Ready is what bulk approval takes: a warning, or a finding that always goes to a person, is not (B-1027)
            f"{ui.plural(warned, 'warning')} to look at" if warned else "",
            f"confidence under {threshold:.0%}" if low and inv.get("requires_review") else "",
        ]
        why = [w for w in why if w]
        if why or inv.get("requires_review"):
            # flagged when its review screen last checked it, nothing standing today: opening it checks it again
            out[inv["id"]] = (why[0][0].upper() + "; ".join(why)[1:]) if why else FLAGGED_BEFORE
    return out


def _pay(facts: dict[str, Any], inv: dict[str, Any]) -> tuple[str, bool, str, str]:
    """(its Pay from account, approved before or not, its currency, its vendor's key) of a queue invoice, as its
    review screen opens with them (``bulk.queue_facts``): each text decoded once (``_PAYS``)."""
    text = facts.get("q_pay")
    if text:
        pay = _PAYS.get(text)
        if pay is None:
            if len(_PAYS) >= PAYS_KEPT:
                _PAYS.clear()
            code, approved, currency, key = json.loads(text)
            pay = _PAYS[text] = (code, approved, currency, key)
        return pay
    return "", False, "CAD", vendor_key(inv.get("vendor_name") or "")


# Each Pay from text of the queue's facts (``q_pay``), decoded: the queue reads 2,700 at each visit, most of them read
# at the last. Past PAYS_KEPT texts they are all let go.
_PAYS: dict[str, tuple[str, bool, str, str]] = {}
PAYS_KEPT = 16_384


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
    credit, warned, audit = ready.count("credit note"), ready.count("warning"), ready.count("audit")
    parts = [ui.plural(credit, "credit note") if credit else "",
             f"{audit} picked for the audit sample" if audit else "",
             f"{warned} with a warning" if warned else ""]  # fmt: skip
    text = " and ".join(p for p in parts if p)
    return f"{text} left for a person" if text else ""


NO_VENDOR = "No vendor name"


def queue_rows(store: Store, shown: list[dict[str, Any]], threshold: float, reasons: dict[int, str] | None = None,
               others: dict[int, list[str]] | None = None) -> list[dict[str, Any]]:  # fmt: skip
    """What each row of the queue shows (plain values; the template escapes them). ``reasons``: ``attention()``.
    ``others``: the names of those who have an invoice open (``invoice.open_elsewhere``, D-184)."""
    from ap_coder.web.pages.invoice import names_text

    reasons, others = reasons or {}, others or {}
    ids = [i["id"] for i in shown]
    outputs = {  # a sent-back invoice shows the first approver's corrections, as its review screen does
        r["id"]: r["final_output"] or r["ai_output"] or {}
        for r in store.invoice_columns(("id", "ai_output", "final_output"), ids=ids)
    }
    sources = store.layout_sources(ids)
    vendor_checks = store.vendor_name_checks(ids)
    default_days = store.default_terms_days()
    # every vendor's terms (20,000 at 100,000 invoices), read once a change of the vendor list, not on each visit
    # (B-1733); shared, read-only
    terms = store.summary_memo("vendor_terms", store.all_vendor_terms, topic="", changes="vendors")
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
            "open": f"{names_text(names)} {'has' if len(names) == 1 else 'have'} it open"
            if (names := others.get(inv["id"])) else "",
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
    to_export = store.ready_to_export_count()  # counted, not read: a year of approvals not exported yet
    # the recurring vendors, from every vendor's invoice dates (0.95 s at 100,000 invoices): only shown, so worked out
    # again at most every LATE_SETTLE seconds under a stream of uploads and rejections, once for every process, and
    # read as they are, never settled again on top (B-1732, B-1798); while another works them out with none kept yet
    # (the server's first start), the queue opens at once without the pill (D-201)
    try:
        with reports.asked_by_a_page(0.0):
            late = [r.vendor_name for r in recurring.detected(store, settle=LATE_SETTLE) if r.status == recurring.LATE]
    except reports.Working:
        late = []
    pills = [
        (f"{overdue} past due, not approved yet", "err") if overdue else None,
        (f"{due_soon} due within {DUE_SOON_DAYS} days", "warn") if due_soon else None,
        (f"{follow_ups} parked to follow up", "warn") if follow_ups else None,
        (f"{second} waiting for your second approval", "info") if second else None,
        (f"{to_export} approved, ready to export", "gray") if to_export else None,
        (f"{ui.plural(len(late), 'regular invoice')} late (Vendors page): "
         + ", ".join(late[:2]) + ("…" if len(late) > 2 else ""), "gray") if late else None,
    ]  # fmt: skip
    return [p for p in pills if p]


def kpis(store: Store, pending: list[dict[str, Any]], flagged: list[dict[str, Any]],
         awaiting: list[dict[str, Any]] = (), settings: Any = None) -> list[dict[str, Any]]:  # fmt: skip
    """The four figures at the top: waiting, approved today, coding accuracy, the coding memory (B-512). Approved today
    counts this reviewer's approvals, a first approval waiting for its second (``awaiting``) included, and says so in
    plain words (B-517)."""
    metrics = store.line_metrics()  # the lessons' counts: the whole metrics() is four scans of them (D-110)
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
    # The queue's invoices only (in review, parked, waiting for a second approval), and the others counted: a year of
    # approved invoices is not read on every visit (D-110). The tabs only show their counts: a few seconds behind
    # (B-1738); the first start's page offers the demo by them: exact.
    if reference is None or not store.has_invoices():
        return _getting_started(request, store, store.status_counts())
    counts = store.status_counts(settled=True)
    invoices = store.list_invoices(statuses=(REVIEW, PARKED, PENDING))
    tab = tab if tab in TABS else "review"
    show = show if show in SHOWS else "all"
    sort = sort if sort in SORTS else "priority"
    limit = max(QUEUE_PAGE, min(limit, 5000))

    threshold = settings.engine.review_threshold
    pending = [i for i in invoices if i["status"] == REVIEW]
    # The checks as they are now: a flagged invoice is never Ready nor offered in bulk, but its reasons are today's
    # too, never only "flagged" (B-1522). On the company server, the ones still checked again after a change of
    # settings show their earlier checks meanwhile, and the page says so (its background process does it, D-155).
    checks, updating = queue_checks(store, [i["id"] for i in pending], settings, recheck=not server_mode(request))
    reasons = attention(store, reference, pending, threshold, checks)  # Ready means its Approve is open
    reasons.update(draft_reasons(request, pending))  # an invoice with edits kept: what its screen says (B-996)
    flagged = [i for i in pending if i["id"] in reasons]
    approved = counts.get(APPROVED, 0)
    awaiting = [i for i in invoices if i["status"] == PENDING]
    parked = [i for i in invoices if i["status"] == PARKED]
    others = counts.get(FAILED, 0) + counts.get(REJECTED, 0)
    minutes = len(flagged) * 2 + (len(pending) - len(flagged))
    hello = f"{ui.greeting()}, {first_name()}"
    waiting = f"{ui.plural(len(pending), 'invoice')} waiting, about {ui.plural(minutes, 'minute')} of review"
    lead = f"{hello} · {waiting}" if pending else f"{hello} · your queue is clear."

    tabs = [("review", f"To review · {len(pending)}")]
    if awaiting or store.approval_limit():
        tabs.append(("second", f"Second approval · {len(awaiting)}"))
    if parked:
        tabs.append(("parked", f"Parked · {len(parked)}"))
    tabs += [("approved", f"Approved · {approved}"), ("other", f"Failed / rejected · {others}")]
    if tab not in dict(tabs):
        tab = "review"

    from ap_coder.web.pages.process import name_step

    unnamed = reviewer() if name_step()[0] != "ok" else ""  # approvals carry the computer login until a name is given
    context: dict[str, Any] = {"tab": tab, "tabs": tabs, "lead": lead, "show": show, "q": q, "sort": sort,
                               "unnamed": unnamed,
                               "kpis": kpis(store, pending, flagged, awaiting, settings),
                               "today": today_pills(store, invoices),
                               "shows": SHOWS, "sorts": SORTS, "view_url": view_url}  # fmt: skip
    context["bulk_job"] = bulk_notice(store)  # the person's bulk approval under way, or ended since (D-185)
    if tab == "review":
        from ap_coder.web.pages.invoice import open_elsewhere

        shown = {"attention": flagged, "ready": [i for i in pending if i["id"] not in reasons]}.get(show, pending)
        shown = sort_queue(filter_queue(shown, q), sort)
        candidates, left = sort_for_bulk(store, pending, checks) if show != "attention" else ([], {})
        context.update(
            pending=pending,
            updating=updating,
            counts={"all": len(pending), "attention": len(flagged), "ready": len(pending) - len(flagged)},
            rows=queue_rows(store, shown[:limit], threshold, reasons, open_elsewhere(request, store)),
            # the view, as this address has it, carried by each row's link: the invoice's count, Previous / Next and
            # Queue button follow it (B-1592), its rows too (Show more: the queue opens once coming back, B-1621)
            row_query="?" + view if (view := urlencode({
                k: request.query_params[k] for k in ("show", "q", "sort", "limit") if request.query_params.get(k)
            })) else "",
            more=max(0, len(shown) - limit),
            next_limit=limit + QUEUE_PAGE,
            threshold=threshold,
            candidates=candidates if len(candidates) >= 2 else [],
            # each one's stamp as the page lists it: one changed before the job comes to it is left (D-185)
            bulk_revs=store.bulk_stamps([c["id"] for c in candidates]) if len(candidates) >= 2 else {},
            candidates_total=by_currency(candidates),
            candidates_left=left_out(pending, reasons, left),
        )  # fmt: skip
    elif tab == "approved":
        recent = store.recently_approved(APPROVED_SHOWN)
        context.update(approved=recent, approved_total=approved, to_export=store.ready_to_export_count(),
                       awaiting_count=len(awaiting))  # fmt: skip
    elif tab == "other":
        context.update(others=[_other(i) for i in store.list_invoices(statuses=(FAILED, REJECTED))])
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
    same = same_approver(inv["reviewer"], first_login, me, my_login)
    detail = (first or {}).get("detail") or {}
    why = audit.second_reason(detail) if detail.get("needs_second") else ""
    return {**inv, "same": same, "approval": (first or {}).get("id") or "", "second_why": why,
            "approved_over": audit.approved_over(detail)}  # fmt: skip


def _getting_started(request: Request, store: Store, counts: dict[str, int]) -> HTMLResponse:
    from ap_coder.web.pages.process import demo_offered, first_steps, next_step, shown_setup, step_state

    steps, done_steps, needed = first_steps(store, get_settings(request), bool(counts))  # as Import invoices counts
    named = step_state(steps, "Your name") == "ok"  # (before it, first_name() is the login: "Good morning, root")
    steps, coming, links = shown_setup(request, steps, next_step(steps))
    return render(
        request, "review_welcome.html", "review",
        greeting=f"{ui.greeting()}, {first_name()}" if named else ui.greeting(),
        next=coming,
        step_links=links,
        steps=steps,
        ring_value=done_steps / needed,
        ring_label=f"{done_steps}/{needed}",
        waiting=counts.get(REVIEW, 0) if reference_or_none(store) is None else 0,
        demo_available=demo_offered(store.demo_count(), sum(counts.values())),
        demo_count=store.demo_count(),
        where="/review",
    )  # fmt: skip


# --- Actions -------------------------------------------------------------------------------------------------------


def exported_reason(store: Store, inv: dict[str, Any] | None) -> str:
    """Why an exported invoice can't be reopened or deleted ("" when it isn't exported): posted to JD Edwards, or
    in its export batch (B-1201)."""
    if not inv or not inv.get("export_batch"):
        return ""
    from ap_coder.erp import jde_post

    batch = inv["export_batch"]
    if any(p["invoice_id"] == inv["id"] for p in jde_post.postings(store, batch, (jde_post.POSTED,))):
        return f"it was posted to JD Edwards (batch {batch}): it stays approved in AP Coder"
    from ap_coder.erp import jde_keying

    if batch in jde_keying.keying_batches(store):  # to type by hand: not in JD Edwards until someone has
        return f"it is on keying sheet {batch}: undo the batch on the Exports page first"
    return f"it was exported in batch {batch}: undo the batch on the Exports page first"


def changed_meanwhile(request: Request, exc: Exception, back: str = "/review",
                      invoice_id: int | None = None) -> JSONResponse:  # fmt: skip
    """Someone else acted on the invoice after the page was drawn: say so, and show where it stands now. An invoice
    exported, or posted to JD Edwards, says that instead (B-1201)."""
    store = get_store(request)
    why = exported_reason(store, store.get_invoice(invoice_id) if invoice_id is not None else None)
    if why:
        return done(request, back, f"Not done: {why}.", tone="warn")
    return done(request, back, f"Not done: someone else changed this invoice meanwhile ({exc}).", tone="warn")


# --- Bulk approval: a job done in the background, as the person who asked (D-185) ----------------------------------

BULK_PAGE = "/review/bulk"
BULK_WAIT = 3.0  # seconds a bulk approval is waited for, holding no thread, before its page says how far it got
BULK_LOOK = 0.25  # seconds at most between two looks at it meanwhile
STILL_UNDER_WAY = ("Your bulk approval asked before is still under way: this page says how far it got. Approve the "
                   "others once it is done.")  # fmt: skip


def _bulk_owner() -> str:
    """Whose bulk approvals: the person asking, as their review screens' edits are theirs ("" this computer's)."""
    key = person_key()
    return "" if key is None else json.dumps(list(key))


@router.post("/review/bulk-approve")
async def review_bulk_approve(request: Request) -> JSONResponse:
    """Approve the clean invoices the page listed, exactly as coded: a job done in the background as the person asking
    (D-185), the web worker holding no thread for it. Each is re-checked first; any that is no longer clean is left
    for a person, with the reason. One done within ``BULK_WAIT`` is answered as before, with what was done; a longer
    one goes to its page, which says how far it got (it goes on when the page is left)."""
    try:  # one field per invoice the page lists, however many (B-461), and its stamp as the page listed it
        form = await posted_form(request)
        ids, revs = [int(str(i)) for i in form.getlist("ids")], [int(str(r)) for r in form.getlist("revs")]
    except ValueError:
        return refuse("Nothing to approve.")
    started = await run_in_threadpool(_start_bulk, request, ids, revs)
    if not isinstance(started, tuple):
        return started
    job, here = started  # (here: set once it ends in this process)
    elsewhere = request.app.state.processes > 1  # (approved by the background process: looked at in the database)
    try:
        ends, wait = time.monotonic() + BULK_WAIT, 0.05
        while time.monotonic() < ends:
            if here.is_set() or (elsewhere and await run_in_threadpool(_bulk_ended, request, job)):
                return await run_in_threadpool(_bulk_done, request, job)
            await anyio.sleep(wait)
            wait = min(BULK_LOOK, wait * 1.5)
    finally:
        jobs.forget_ended(here)
    return done(request, BULK_PAGE)


def _start_bulk(request: Request, ids: list[int], revs: list[int]) -> tuple[int, threading.Event] | JSONResponse:
    """The bulk approval of ``ids`` asked for (``revs``: their stamps as the page listed them, ``Store.bulk_stamps``;
    else as they are now): the job started (its id, and what is set once it ends in this process), or the answer when
    none is."""
    store = get_store(request)
    with store.session():
        if reference_or_none(store) is None or not ids:
            return refuse("Nothing to approve.")
        person = {"name": reviewer(), "login": login(), "server": server_mode(request), "as": identity.kept_person()}
        listed = dict(zip(ids, revs, strict=True)) if len(revs) == len(ids) else store.bulk_stamps(ids)
        asked = [[i, list(store.row_identity(i) or []), listed[i]] for i in dict.fromkeys(ids)]  # each as listed
        job, new = store.start_bulk_job(_bulk_owner(), person, asked, bulk.JOB_FORMAT)
    if not new:
        return done(request, BULK_PAGE, flash_message(STILL_UNDER_WAY, "info", icon="hourglass_top"))
    here = jobs.ended(store, job)  # (before it can end)
    _approve_bulk(request)
    return job, here


def _approve_bulk(request: Request) -> None:
    """The bulk approvals waiting looked at now: by this process's runner, or by a thread of its own when it is the
    only process and has none (a laptop; a first test of the server); else the server's background process does."""
    runner = request.app.state.bulk_runner
    if runner is not None:
        runner.wake()
    elif request.app.state.processes == 1:
        jobs.approve_here(get_store(request), request.app.state.settings_factory)


def _bulk_left(request: Request, job: dict[str, Any]) -> None:
    """``job`` taken up again when it was left waiting, or by a process that stopped while approving it (a restart):
    only a new one asked started a thread (as B-1777's downloads)."""
    if job["state"] == bulk_jobs.WAITING or (
            job["state"] == bulk_jobs.RUNNING and job["renewed_at"] < time.time() - jobs.BULK_STALE):  # fmt: skip
        _approve_bulk(request)


def _bulk_ended(request: Request, job_id: int) -> bool:
    job = get_store(request).bulk_job(job_id)
    return job is None or job["state"] in (bulk_jobs.DONE, bulk_jobs.STOPPED)


def _bulk_done(request: Request, job_id: int) -> JSONResponse:
    """The answer to a bulk approval done while it was waited for: back to the queue, saying what was done."""
    store = get_store(request)
    with store.session():
        job = store.bulk_job(job_id)
        if job is None:  # (a restore meanwhile)
            return done(request, "/review")
        store.saw_bulk_job(job_id)
        return done(request, "/review", *_bulk_messages(store, job))


def _waiting_second(store: Store, approved: list[int]) -> int:
    """How many of the invoices ``approved`` wait for their second approval."""
    if not approved:
        return 0
    return sum(1 for r in store.invoice_columns(("id", "status"), ids=approved) if r["status"] == PENDING)


def _left_for_a_person(store: Store, job: dict[str, Any]) -> list[dict[str, Any]]:
    """The invoices of ``job`` not approved: each its id, name (vendor · number), why, and whether it is still in the
    queue (its link opens it)."""
    rows = []
    for i, why in job["skipped"]:
        inv = store.get_invoice(i) or {}
        name = " · ".join(x for x in (inv.get("vendor_name"), inv.get("invoice_number")) if x) or f"#{i}"
        rows.append({"id": i, "name": name, "why": why, "open": inv.get("status") == REVIEW})
    return rows


def _bulk_messages(store: Store, job: dict[str, Any]) -> list[dict[str, Any]]:
    """What a bulk approval did, as the queue says it: how many approved (and wait for a second approval), why it
    stopped, and the invoices that need a look, each with why."""
    approved, waiting = job["approved"], _waiting_second(store, job["approved"])
    messages = [flash_message(
        f"Approved {ui.plural(len(approved), 'invoice')}"
        + (f"; {waiting} wait{'s' if waiting == 1 else ''} for a second approval." if waiting else "."),
        "ok", icon="done_all")]  # fmt: skip
    if job["error"]:
        messages.append(flash_message(job["error"], "warn"))
    if job["skipped"]:
        details = [f"{row['name']}: {row['why']}" for row in _left_for_a_person(store, job)]
        n = len(job["skipped"])
        messages.append(flash_message(f"{ui.plural(n, 'invoice')} {'was' if n == 1 else 'were'} not approved: "
                                      f"{'it needs' if n == 1 else 'they need'} a look.", "warn", details))  # fmt: skip
    return messages


def bulk_note(job: dict[str, Any]) -> str:
    """How far a bulk approval got: "37 of 200 approved, 2 need a look"."""
    look = len(job["skipped"])
    return f"{len(job['approved']):,} of {len(job['ids']):,} approved, {look:,} need{'s' if look == 1 else ''} a look"


def bulk_notice(store: Store) -> dict[str, str] | None:
    """What the queue says of the person's bulk approval: under way, how far it got; or ended since they last looked.
    None when there is nothing to say."""
    job = store.latest_bulk_job(_bulk_owner())
    if job is None or (job["state"] not in (bulk_jobs.WAITING, bulk_jobs.RUNNING) and job["seen"]):
        return None
    if job["state"] in (bulk_jobs.WAITING, bulk_jobs.RUNNING):
        return {"text": f"Your bulk approval is under way: {bulk_note(job)}.", "link": "See how far it got",
                "tone": "info", "icon": "hourglass_top"}  # fmt: skip
    ended = "stopped" if job["error"] else "done"
    return {"text": f"Your bulk approval is {ended}: {bulk_note(job)}.", "link": "See what was done",
            "tone": "warn" if job["error"] or job["skipped"] else "ok", "icon": "done_all"}  # fmt: skip


@router.get(BULK_PAGE, response_class=HTMLResponse)
def review_bulk_page(request: Request) -> Response:
    """The person's bulk approval: how far it got while it goes on (safe to leave and come back), then what was done,
    and the invoices that need a look."""
    store = get_store(request)
    job = store.latest_bulk_job(_bulk_owner())
    if job is None:
        return RedirectResponse("/review", status_code=303)
    _bulk_left(request, job)
    running = job["state"] in (bulk_jobs.WAITING, bulk_jobs.RUNNING)
    if not running:
        store.saw_bulk_job(job["id"])
    return render(request, "review_bulk.html", "review", reading_status=None, title="Bulk approval", job=job,
                  running=running, note=bulk_note(job), status_url=f"{BULK_PAGE}/status?job={job['id']}",
                  approved=len(job["approved"]), waiting=_waiting_second(store, job["approved"]),
                  look=_left_for_a_person(store, job))  # fmt: skip


@router.get(BULK_PAGE + "/status")
def review_bulk_status(request: Request, job: int = 0) -> JSONResponse:
    """How far the person's bulk approval got, as ``app.js`` follows a job: running (waiting counts), done or error."""
    found = get_store(request).bulk_job(job)
    if found is None or found["owner"] != _bulk_owner():
        raise HTTPException(status_code=404, detail="No such bulk approval.")  # (another person's is as none)
    _bulk_left(request, found)
    state = {bulk_jobs.DONE: "done", bulk_jobs.STOPPED: "error"}.get(found["state"], "running")
    return JSONResponse({"state": state, "done": len(found["approved"]) + len(found["skipped"]),
                         "total": len(found["ids"]), "note": bulk_note(found), "error": found["error"]},
                        headers={"Cache-Control": "no-store"})  # fmt: skip


@router.post("/review/sample-gl")
def review_sample_gl(request: Request, back: str = Form("/review")) -> JSONResponse:
    store = get_store(request)
    if store.list_accounts("gl_accounts"):  # a page left open while the clerk imported their own chart
        return refuse("You already have GL accounts: the sample setup is only for an empty list.")
    load_sample_setup(store, cost_centers=False, actor=reviewer())  # each line needs only a GL account (D-53)
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
        return changed_meanwhile(request, exc, back, invoice_id)
    forget_everyones_draft(request, invoice_id)
    return done(request, back, flash_message("Deleted. The file moved to invoices/deleted.", "ok", icon="delete"))


@router.post("/review/{invoice_id}/reopen")
def review_reopen(request: Request, invoice_id: int) -> JSONResponse:
    try:
        get_store(request).reopen(invoice_id, reviewer(), only_rejected=True)
    except ValueError as exc:
        return changed_meanwhile(request, exc, "/review?tab=other", invoice_id)
    return done(request, "/review?tab=other", flash_message("Back in the review queue.", "ok", icon="undo"))


@router.post("/review/{invoice_id}/retry")
def review_retry(request: Request, invoice_id: int) -> JSONResponse:
    """Read a failed invoice again (the new attempt replaces the failed one): put in the import queue (D-168)."""
    from ap_coder.core.store.folder import read_file
    from ap_coder.web.pages.process import queue_files

    store = get_store(request)
    inv = store.get_invoice(invoice_id)
    if inv is None:
        return refuse("That invoice is no longer in AP Coder.", 404)
    if inv["status"] != FAILED:  # a stale page's button: reading it again would add a second copy to the queue
        return refuse("Retry reads again only an invoice that could not be read: reload the page.", 409)
    path = Path(inv["source_path"])
    if not path.exists():
        return refuse(f"The original file is no longer at {shown_path(path)}.")
    try:
        queue_files(request, [read_file(path)], source="retry")
    except OSError:
        return refuse(f"The original file at {shown_path(path)} can't be opened now (open in another program?).")
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
    """``approval``: the first approval the page showed (B-654); a page that posts none is refused as one shown an
    older approval (D-109)."""
    from ap_coder.core.store import ApprovedAgain

    store = get_store(request)
    inv = store.get_invoice(invoice_id) or {}
    try:
        store.final_approve(invoice_id, reviewer(), login=login(), shown=approval)
    except PermissionError as exc:  # the first approver (or their login): nobody changed anything meanwhile
        if server_mode(request):  # refused as the other refusals are: not done, said, and a 409 (D-102)
            return refuse(f"Not done: {exc}.", 409)
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
