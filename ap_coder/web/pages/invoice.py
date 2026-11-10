"""The invoice review screen, ``/review/<id>``: opened from the review queue (and from the other lists).

An invoice waiting for review opens as the workspace: the page with a box over every field read from it (click a
field to see it on the page, or teach a field by clicking its words), the checks, the invoice details, the line
items with their GL coding, the sales tax, the GL posting preview and the approve bar (Approve & teach, Park,
Reject, Delete). Any other invoice opens read-only, with what can be done in its state: the second approval or
sending it back, bringing a parked one back, reopening an approved (for correction) or a rejected one.

Every edit is checked as the reviewer types (``POST /review/<id>/check`` answers the sections again) and kept as a
draft, per person and in the store too (D-86), so moving to another invoice and back keeps it, a restart too.
What is worked out lives in ``ap_coder/web/invoice_view.py`` and its siblings (``invoice_<subject>.py``); the page
script is ``static/js/invoice.js`` with the page viewer ``static/js/invoice_viewer.js``.

This module opens and draws the screen (``GET /review/<id>``: ``invoice_page``; what an invoice that is not being
reviewed shows: ``_closed``), and is the one the app registers: its ``router`` is the screen's one router, on which
the other routes are declared by one module a subject, all imported here (D-223: a new route goes in the module of
its subject; a new subject, in a module of its own, imported here):

* ``invoice_router.py``: the ``router``, and what every handler starts with (the invoice asked for, the queue's
  view carried along).
* ``invoice_drafts.py``: each person's edits not approved yet, who else has the invoice open, a screen out of date
  (D-86, D-184). ``invoice_navigation.py``: Previous / Next, where to go after an action. Neither has routes.
* ``invoice_check.py``: POST ``check`` (the sections again, the edits kept as a draft), ``start-over``.
* ``invoice_lines.py``: POST ``split``, ``apply-rules``, ``po-coding``, ``suggestion``, ``fill-lines`` (Apply to all
  lines).
* ``invoice_approve.py``: POST ``approve``, ``why``, ``undo-approve`` (the approval banner's Undo), ``park``,
  ``manager``, ``reject``, ``note``, ``resume`` (a parked invoice), ``restore`` (a rejected one), ``correct`` (an
  approved one).
* ``invoice_lookups.py``: ``GET /review/vendors.json`` and ``accounts.json``, the lists the screen picks from
  (D-160); POST ``vendor`` (add the printed vendor).
* ``invoice_document.py``: GET ``page/<n>.png`` (a page's picture), ``words`` (the words on the pages, to teach a
  field), ``reader`` (the screen's poll: OvisOCR2's progress on it, who else has it open and whether the screen is
  out of date, D-184), ``approved.pdf``; POST ``teach``, ``read-pages``, ``review-again`` (the AI reviewer).

Delete, final approval and send back are the queue's own POSTs (``pages/review.py``).
"""

from __future__ import annotations

import datetime as dt
import secrets
import sqlite3
from dataclasses import replace
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse

from ap_coder.coding.vendors import mask_account
from ap_coder.core import audit, ui
from ap_coder.core.memory import vendor_key
from ap_coder.core.reference_data import ReferenceData
from ap_coder.core.store import APPROVED, FAILED, PARKED, PENDING, REJECTED, REVIEW, Store, coded_blanks
from ap_coder.core.store.users import same_approver
from ap_coder.erp import stamp
from ap_coder.erp.jde_keying import keying_batches
from ap_coder.web import invoice_coding, invoice_fields, invoice_form, invoice_grid, invoice_reading, invoice_totals
from ap_coder.web import invoice_view as view
from ap_coder.web.core import (
    can,
    get_settings,
    get_store,
    login,
    one_connection,
    reading_status_or_none,
    reference_or_none,
    render,
    reviewer,
    server_mode,
)
from ap_coder.web.pages import invoice_lines  # noqa: F401 (the actions on the lines: its routes)
from ap_coder.web.pages.invoice_approve import NOTE_ACTIONS, UNDONE, history
from ap_coder.web.pages.invoice_check import _keep_checked, _shown_live
from ap_coder.web.pages.invoice_document import (
    LM_STUDIO_OFF,
    PAGE_READER_POLL_SECONDS,
    _document,
    lm_studio_off,
    reader_tag,
)
from ap_coder.web.pages.invoice_drafts import (
    NO_DRAFT,
    WATCH_SECONDS,
    _draft_rev,
    _drafts,
    _keep_open,
    _keeps,
    _start,
    _when,
    current_draft,
    open_elsewhere,
    working_here,
)
from ap_coder.web.pages.invoice_lookups import account_seed, vendor_seed
from ap_coder.web.pages.invoice_navigation import navigation
from ap_coder.web.pages.invoice_router import _invoice, _nav_query, _with_query, router


def _manager_context(request: Request, store: Store, inv: dict[str, Any], lines: list[Any] | None = None) -> dict:
    """What the review screen says about a manager confirming the coding (D-138): the request waiting (a parked
    invoice), the last answer and whether the lines on screen still are the ones confirmed, who can be asked."""
    from ap_coder.core.store.managers import CONFIRMED, same_coding
    from ap_coder.core.store.users import MANAGER

    answer = store.last_manager_answer(inv["id"])
    changed = bool(answer and answer["status"] == CONFIRMED and lines and not same_coding(answer["lines"], lines))
    users = [u for u in store.list_users() if u["role"] == MANAGER and u["active"]] if server_mode(request) else []
    known = store.cost_center_managers()
    centers = [str(li.get("predicted_cost_center") or "") for li in lines or []]
    suggested = next((known[c] for c in centers if c in known), {})
    return {"waiting": store.waiting_manager_request(inv["id"]) if inv["status"] == PARKED else None,
            "answer": answer, "changed": changed, "users": users, "suggested": suggested}  # fmt: skip


@router.get("/review/{invoice_id:int}", response_class=HTMLResponse)
@one_connection
def invoice_page(request: Request, invoice_id: int) -> Any:
    store, settings = get_store(request), get_settings(request)
    inv = _invoice(store, invoice_id)
    reference = reference_or_none(store)
    query = _nav_query(request.query_params)
    status = reading_status_or_none(settings, store)
    events = store.events(invoice_id)
    context: dict[str, Any] = {
        "inv": inv,
        "query": query,
        "queue_url": _with_query("/review", query),
        "nav": navigation(store, invoice_id, query, request),
        "history": history(events),
        "notes": history([e for e in events if e["action"] in NOTE_ACTIONS]),
        "title": inv.get("vendor_name") or inv.get("file_name") or "Invoice",
    }
    editable = inv["status"] == REVIEW and reference is not None
    may_edit = can(request, f"/review/{invoice_id}/check")  # server mode: Read-only looks and changes nothing (D-98)
    capture = invoice_reading.capture_of(store, invoice_id)  # read once: the document card and the checks use it
    context["doc"] = _document(store, settings, inv, status, editable and may_edit, capture)
    may_read = can(request, f"/review/{invoice_id}/read-pages")
    context["doc"]["reader_tag"] = reader_tag(context["doc"]["reader"], may_read)  # what its poll sends back (D-160)
    context["doc"]["watch"] = editable  # its poll: who else has it open, and whether it is out of date (D-184)
    if not editable:
        mode = _mode(inv)
        if mode in ("approved", "pending", "rejected", "parked"):  # nothing left to read: no reading tools' warning
            context["banner"] = ""
        return render(request, "invoice.html", "review", mode=mode, reading_status=status, **context,
                      manager=_manager_context(request, store, inv), **_closed(store, reference, inv))  # fmt: skip
    saved_at = str((_drafts(request).get(invoice_id) or {}).get("saved_at") or "")  # edits kept from before
    drawn = _draft_rev(_drafts(request).get(invoice_id))
    draft, kept = current_draft(request, inv)
    analysis = view.analyse(store, settings, reference, inv, draft, reviewer(), capture=capture)
    # The draft's revision once the page has kept it, or the one before when that is left to the next check (D-184)
    will = _draft_rev(draft.to_dict()) if _keeps(draft, analysis) else NO_DRAFT
    draft_rev = will if will == drawn else f"{will} {drawn}"
    screen = secrets.token_hex(4)  # this screen's own, its drafts say they are from it
    context["manager"] = _manager_context(request, store, inv, list(analysis.output.get("line_items") or []))
    sure = invoice_reading.shown_sure(invoice_reading.capture_confidence(capture), draft.header,
                                      inv.get("ai_output") or {}, capture)  # fmt: skip
    start = _start(inv)
    computed = None
    try:
        if float(start.get("grand_total") or 0) > 0:  # a credit note has no due date
            from ap_coder.coding.terms import payment

            computed = payment({**start, **draft.header}, store.default_terms_days(),
                               store.vendor_terms(vendor_key(draft.header.get("vendor_name") or ""))).due  # fmt: skip
    except (TypeError, ValueError):
        computed = None
    if lm_studio_off(status):  # one short note in the top row: the queue's banner says the rest (B-467)
        context["lm_off"], context["banner"] = LM_STUDIO_OFF, ""
    header = {**draft.header, **analysis.filled}  # as its check puts it: a date as YYYY-MM-DD
    page = render(
        request, "invoice.html", "review", mode="review", reading_status=status, **context,
        draft=replace(draft, header=header),
        kept=kept, live=_shown_live(request, analysis.live, may_edit, draft_rev), sure=sure,
        rev=store.coding_revision(invoice_id), screen=screen, watch_seconds=WATCH_SECONDS,
        others=working_here(open_elsewhere(request, store, invoice_id).get(invoice_id, [])),
        grid={**invoice_grid.grid(reference, draft, inv.get("ai_output") or {},
                          invoice_grid.vendor_accounts(store, draft.header.get("vendor_name") or ""), analysis),
              "tax_marks": analysis.live.get("tax_marks") or {}},
        labels=invoice_form.FIELD_LABELS, provinces=invoice_grid.province_options(),
        due_placeholder=f"{computed.isoformat()} (from terms)" if computed else
        "None: a credit is not paid" if float(start.get("grand_total") or 0) < 0 else "YYYY-MM-DD",
        sent_back=_sent_back(events), poll_seconds=PAGE_READER_POLL_SECONDS,
        today=dt.date.today().isoformat(), taught_json=draft.taught, vendor_seed=vendor_seed(store, header),
        absent_ok=list(invoice_form.ABSENT_FIELDS), from_vendor_note=invoice_fields.FROM_VENDOR_NOTE,
        tax_fields=list(invoice_form.TAX_NUMBER_FIELDS), pay_from=invoice_fields.pay_from_choices(store, draft.header),
        unread_numbers=sorted(invoice_form.unread_numbers(inv.get("ai_output") or {}) or []),
        bank_shown=mask_account(bank) if (bank := draft.header.get("remit_bank_account")) else "",
        supply=invoice_grid.supply_line(draft.header, draft.lines, reference.tax),
        usual_cc=invoice_grid.usual_cost_center(store, reference, draft.header.get("vendor_name") or ""),
        account_seed=account_seed(store, reference, draft.header.get("vendor_name") or ""),
        draft_from=_when(saved_at),
    )  # fmt: skip
    # What the screen's check did once it opened, a second request (the screen's own checks again, whose answer the
    # page drew already): written once the page is drawn, so nothing it reads comes after. Behind another writer it
    # is left to the next open or check: the page never waits for it.
    if may_edit:
        try:
            with store.briefly():
                _keep_checked(request, store, invoice_id, draft, analysis, screen)
                _keep_open(request, store, inv)  # the others see who has it open (D-184)
        except sqlite3.OperationalError:
            pass
    return page


def _mode(inv: dict[str, Any]) -> str:
    if inv["status"] == REVIEW:
        return "nogl"  # waiting for review, but there are no GL accounts to code it with yet
    return {PENDING: "pending", PARKED: "parked", APPROVED: "approved", REJECTED: "rejected",
            FAILED: "failed"}.get(inv["status"], "other")  # fmt: skip


def _closed(store: Store, reference: ReferenceData | None, inv: dict[str, Any]) -> dict[str, Any]:
    """What the read-only screen shows for an invoice that is not being reviewed."""
    doc = inv.get("final_output") or inv.get("ai_output") or {}
    out: dict[str, Any] = {"stored": invoice_totals.stored_summary(inv, doc), "posting": None, "edits": "",
                           "same": False}  # fmt: skip
    if doc.get("gl_distribution"):
        out["posting"] = invoice_totals.distribution(doc, reference)
    if inv["status"] in (APPROVED, PENDING):
        edits = inv.get("edits") or []
        changed = [e for e in edits if e != "line_coded"]
        labels = {**invoice_coding.EDIT_LABELS,
                  "line_coding": invoice_coding.coding_changed(inv.get("ai_output") or {}, doc)}  # fmt: skip
        said = [("changed: " + ", ".join(labels.get(e, e) for e in changed)) if changed else "",
                invoice_coding.remarks_changed(inv.get("ai_output") or {}, doc)]  # fmt: skip
        out["edits"] = "; ".join(filter(None, said)) or "no changes to the suggested coding"
        if "line_coded" in edits:  # the reviewer's own coding where the AI proposed none (B-670)
            n = coded_blanks(inv.get("ai_output"), inv.get("final_output"))
            out["edits"] += (
                f"; AP coded {ui.plural(n, 'line')} the AI left blank" if n else "; AP coded lines the AI left blank"
            )
        out["can_stamp"] = stamp.can_stamp(inv)
        out["on_keying_sheet"] = bool(inv.get("export_batch")) and inv["export_batch"] in keying_batches(store)
    if inv["status"] == PENDING:
        me, my_login = reviewer(), login()
        first = next(iter(store.events(inv["id"], ["approved"], limit=1)), None)
        first_login = ((first or {}).get("detail") or {}).get("login") or ""
        out["same"] = same_approver(inv["reviewer"], first_login, me, my_login)
        out["limit"] = store.approval_limit()
        detail = (first or {}).get("detail") or {}
        out["second_why"] = audit.second_reason(detail) if detail.get("needs_second") else ""
        out["approved_over"] = audit.approved_over(detail)  # what "Approve anyway" went over, and why (D-75)
        out["approval"] = (first or {}).get("id") or ""  # posted back with the final approval (B-654)
    if inv["status"] == FAILED:
        error = (inv.get("error") or "No reason recorded.").strip()
        first, _, details = error.partition("\n")
        out["error_first"] = first[:240] + ("…" if len(first) > 240 else "")
        out["error_details"] = error if details.strip() or len(first) > 240 else ""
    return out


def _sent_back(events: list[dict[str, Any]]) -> dict[str, str] | None:
    """Who sent the invoice back or reopened it, and why, from its events (``Store.events``, newest first)."""
    events = [e for e in events if e["action"] in ("sent_back", "reopened", "approved", "rejected")]
    back = events[0] if events else None
    if not back or back["action"] not in ("sent_back", "reopened"):
        return None
    if (back["detail"] or {}).get("reason") == UNDONE:  # the banner's Undo says it already (B-828)
        return None
    if len(events) > 1 and events[1]["action"] == "rejected":  # a rejection undone: never approved (B-1181)
        return {"verb": "Rejected", "actor": events[1]["actor"] or "?", "reopened_by": back["actor"] or "?",
                "reason": (events[1]["detail"] or {}).get("reason") or "no reason given"}  # fmt: skip
    return {"verb": "Sent back" if back["action"] == "sent_back" else "Reopened", "actor": back["actor"] or "?",
            "reason": (back["detail"] or {}).get("reason") or "no reason given"}  # fmt: skip
