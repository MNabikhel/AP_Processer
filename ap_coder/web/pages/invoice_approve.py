"""What a person decides about the invoice on the review screen, all POSTs under ``/review/<id>``.

* ``approve`` (Approve & teach), the question after a surprising correction (``why``, D-218) and ``undo-approve``
  (the approval banner's Undo).
* ``park``, ``manager`` (send it to the cost centers' manager to confirm the coding, D-138), ``reject``, ``note``
  (and the notes and history the screen lists: ``history``).
* The other states: ``resume`` (a parked invoice), ``restore`` (a rejected one), ``correct`` (reopen an approved
  one).

Delete, final approval and send back are the queue's own POSTs (``pages/review.py``).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import Form, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from ap_coder.capture.workflow import learn_from_approval, on_reopen, taught_message
from ap_coder.coding.vendors import MOVED_OFF_HOLD
from ap_coder.core import audit, ui
from ap_coder.core.memory import ACCEPTED, CODED, CORRECTED
from ap_coder.core.store import APPROVED, PARKED, PENDING, REJECTED, REVIEW
from ap_coder.erp.jde_keying import keying_batches
from ap_coder.web import identity, invoice_form
from ap_coder.web import invoice_view as view
from ap_coder.web.access import may_lift_hold
from ap_coder.web.core import (
    done,
    flash_message,
    get_settings,
    get_store,
    login,
    one_connection,
    posted_form,
    refuse,
    reviewer,
    server_mode,
)
from ap_coder.web.pages.activity import timeline_items
from ap_coder.web.pages.invoice_drafts import (
    _acted_on_meanwhile,
    _from_form,
    _keep_draft,
    _OutOfDate,
    changed_meanwhile,
    forget_everyones_draft,
)
from ap_coder.web.pages.invoice_navigation import next_url
from ap_coder.web.pages.invoice_router import _back, _fix_first, _invoice, _nav_query, _reference, router

# --- Approve ------------------------------------------------------------------------------------------------------


@router.post("/review/{invoice_id:int}/approve")
async def approve(request: Request, invoice_id: int) -> JSONResponse:
    """Approve & teach: store the coding on screen, record what was confirmed and corrected, teach the supplier's
    template where its fields are (and the words the reviewer showed), then open the next invoice. Only the form is
    read here: the approval and its learning run on a threadpool thread, never holding up the other requests."""
    form = await posted_form(request)
    return await run_in_threadpool(_approve, request, invoice_id, form)


@one_connection
def _approve(request: Request, invoice_id: int, form: Any) -> JSONResponse:
    store, settings = get_store(request), get_settings(request)
    inv = _invoice(store, invoice_id)
    if behind := _acted_on_meanwhile(request, store, invoice_id, inv, form):
        return behind
    reference = _reference(store)
    draft = _from_form(request, inv, form)
    query = _nav_query(form)
    analysis = view.analyse(store, settings, reference, inv, draft, reviewer())
    _fix_first(analysis)
    moved = next((i for i in analysis.errors if i.code == MOVED_OFF_HOLD), None)
    if moved and not may_lift_hold(request):  # a Clerk: a held vendor's invoice coded to another vendor (D-142)
        return refuse(moved.message[:1].upper() + moved.message[1:] + ".", 403)
    if not analysis.allow:
        bar = analysis.live["bar"]
        first = bar["first"]  # the first thing that keeps it closed, in screen order (B-797)
        if first and first["kind"] != "hard":
            return refuse(first["text"] + ".")
        if first:  # never approved anyway (D-75)
            return refuse("Not ready: " + "; ".join(b["text"] for b in bar["blockers"][:3]) + ".")
        return refuse(f"Tick “Approve anyway” and say why to approve despite {ui.plural(bar['errors'], 'error')}.")
    # The same correction again and again: before saving, AP is asked once why (lessons.py). Skipping is fine: the
    # question comes back next time.
    from ap_coder.ai import lessons
    from ap_coder.core.schema import as_read

    ai = inv.get("ai_output") or {}
    asked = lessons.due_for_why(store, ai, as_read(analysis.output, ai))  # a vendor-list number: no "why" (B-681)
    if asked and not form.get("why_done"):
        return JSONResponse({"ok": False, "ask_why": {"fields": asked, "threshold": lessons.why_after(store)}})
    after = next_url(store, invoice_id, query, form.get("next_id"), request)
    why = draft.override_reason.strip()  # "Approve anyway": why, kept with each error approved over (D-75)
    open_issues = [{"code": i.code, "severity": i.severity, "line": i.line_number,
                    **({"override_reason": why} if why and i.severity == "error" else {})}
                   for i in analysis.errors + analysis.warnings]  # fmt: skip
    me = reviewer()
    from ap_coder.coding.line_tax import confirm_codes

    confirm_codes(analysis.output)  # approving confirms the tax codes worked out to check (B-713)
    try:
        counts = store.approve_invoice(invoice_id, analysis.output, me, open_issues=open_issues, login=login())
    except (KeyError, ValueError):  # KeyError: deleted meanwhile, after it was read above
        return changed_meanwhile(request, invoice_id)
    learned = learn_from_approval(store, invoice_id, analysis.output, actor=me, taught=invoice_form.taught_boxes(draft))
    explained = 0
    for item in asked:  # the reasons AP gave on the "why" screen (none when it was skipped), after this correction
        answer = str(form.get(f"why::{item['key']}") or "").strip()  # was counted: the count starts again from 0
        if answer and store.explain_lesson(item["key"], answer, actor=me, invoice_id=invoice_id):
            explained += 1
    # An account AP Coder was sure of, changed: asked why on the next page, once, the approval made (D-218). Never
    # after the question before saving: one question an invoice.
    surprise = None if asked or not lessons.asks_why(store) else lessons.surprise(
        store, ai, as_read(analysis.output, ai), reference)  # fmt: skip
    if surprise:  # kept: the answer is taken for this question only, from this person only (B-2291)
        store.ask_why(invoice_id, surprise["key"], surprise["line"], actor=me, login=login())
    forget_everyones_draft(request, invoice_id)
    vendor = analysis.coding.vendor_name.rstrip(".")
    waits = (store.get_invoice(invoice_id) or {}).get("status") == PENDING
    messages = []
    if waits:  # not approved yet: never "Approved <vendor>" for it
        # the reason the approval stored: the limit and rate in force since it came in (B-970, B-1024)
        first = next(iter(store.events(invoice_id, ["approved"], limit=1)), None)
        why = audit.second_reason((first or {}).get("detail") or {})
        messages.append(flash_message(f"First approval recorded for {vendor}: {why}; it waits for a second "
                                      "approver.", "info", icon="how_to_reg"))  # fmt: skip
    total = sum(counts.values())
    taught = taught_message(store, learned)
    # A line whose account the AI only guessed is the reviewer's coding: not "as suggested", not corrected (B-475)
    coded = counts.get(CODED, 0)
    measured = counts[ACCEPTED] + counts[CORRECTED]
    yours = f"{coded} you coded" if coded else ""
    lines = (
        (f"{counts[ACCEPTED]} of {ui.plural(measured, 'line')} coded as suggested" + (f", {yours}." if yours else "."))
        if measured
        else f"{ui.plural(coded, 'line')} you coded."
    )
    learned_from = (
        f"Learned from {ui.plural(total, 'line')}: "
        + ", ".join(
            p
            for p in (
                f"{counts[ACCEPTED]} confirmed" if measured else "",
                f"{counts[CORRECTED]} corrected" if measured else "",
                yours,
            )
            if p
        )
        + "."
    )
    # One line and Undo; what it learned folded under "What it learned" (B-804).
    approved = flash_message("Its coding is kept for the second approver." if waits else f"Approved {vendor}.",
                             "ok", icon="school")  # fmt: skip
    approved["more"] = f"{taught} {lines}" if taught else learned_from
    approved["more_label"] = "What it learned"
    approved["undo"] = f"/review/{invoice_id}/undo-approve"  # the banner's Undo: back in the queue as it was
    messages.append(approved)
    if surprise:
        messages.append(_why_message(invoice_id, surprise))
    if explained:
        messages.append(flash_message(f"Thanks: {ui.plural(explained, 'reason')} kept. The AI reads "
                                      f"{'it' if explained == 1 else 'them'} with this vendor's next invoices.", "ok",
                                      icon="psychology"))  # fmt: skip
    left = store.status_counts().get(REVIEW, 0)  # counted, not read (D-110)
    if not left:
        messages.append(flash_message("Your queue is clear: every invoice has been reviewed.", "ok", icon="task_alt"))
    elif not form.get("next_id"):  # the last one of the list as shown: the rest are ones passed over
        messages.append(flash_message(f"That was the last one in the list. {ui.plural(left, 'invoice')} still "
                                      "to review: back to the first of them.", "info", icon="replay"))  # fmt: skip
    return done(request, after, *messages)


def _why_message(invoice_id: int, surprise: dict[str, Any]) -> dict[str, Any]:
    """The question after a surprising correction (``lessons.surprise``), as a message of the next page: a short
    text box, the one-click reasons and Skip (base.html)."""
    asked = flash_message(surprise["question"], "info", icon="help")
    asked["why"] = {"url": f"/review/{invoice_id}/why", "key": surprise["key"], "reasons": surprise["reasons"]}
    return asked


@router.post("/review/{invoice_id:int}/why")
def why(request: Request, invoice_id: int, situation: str = Form(""), reason: str = Form(""),
        answer: str = Form("")) -> JSONResponse:  # fmt: skip
    """The answer to the question asked after an approval (D-218): kept as that lesson's reason (Learning shows it,
    the AI reads it), or, a one-off, that line is forgotten from the coding memory as Learning's Forget does. The
    answer belongs to the question that was asked (``Store.why_asked``, never the form): taken from the person who
    made that approval, for the line it named, once, while the setting is on, and while the approved invoice still
    asks it (worked out again). Anything else is refused and changes nothing (B-2291); an Admin forgets a line on
    Learning."""
    from ap_coder.ai import lessons
    from ap_coder.core.schema import as_read

    store = get_store(request)
    inv = _invoice(store, invoice_id)
    asked = store.why_asked(invoice_id) if inv["status"] in (APPROVED, PENDING) else None
    if asked is None:
        return refuse("No question is open about this invoice: nothing was changed.", 409)
    if asked["asked_of"] != login():
        name = asked["actor"] or "the person"
        return refuse(f"That question was asked of {name}, who approved this invoice: only {name} can answer it. "
                      "Nothing was changed.", 403)  # fmt: skip
    if not lessons.asks_why(store):
        return refuse("“Ask why after a surprising correction” is turned off (Settings → Review): nothing was "
                      "changed.", 409)  # fmt: skip
    if asked["answered_at"]:
        return refuse("That question was answered already: nothing was changed.", 409)
    ai = inv.get("ai_output") or {}
    found = lessons.surprise(store, ai, as_read(inv.get("final_output") or {}, ai), _reference(store))
    if found is None or not situation == asked["situation"] == found["key"] or found["line"] != asked["line_number"]:
        return refuse("That question is no longer open: the invoice was changed since.", 409)
    typed = " ".join(answer.split())[:300]
    kept = "" if reason == lessons.ONE_OFF else lessons.reason_kept(found, reason, typed)
    if reason != lessons.ONE_OFF and not kept:
        return refuse("Write a few words or pick a reason; Skip leaves it.")
    if not store.answer_why(invoice_id, login()):  # two answers at once: the first has it
        return refuse("That question was answered already: nothing was changed.", 409)
    if reason == lessons.ONE_OFF:
        store.forget_one_off(invoice_id, found["line"], found["key"], actor=reviewer(),
                             detail={"line": found["line"], "now": found["now"], "was": found["was"],
                                     "reason": typed})  # fmt: skip
        return JSONResponse({"ok": True, "toast": {"text": "Kept as a one-off: AP Coder does not learn from that "
                                                   "line.", "tone": "ok"}})  # fmt: skip
    if not store.explain_lesson(found["key"], kept, actor=reviewer(), invoice_id=invoice_id):
        return refuse("That lesson is no longer there: nothing was kept.", 404)
    return JSONResponse({"ok": True, "toast": {"text": "Thanks: the reason is kept with that lesson (What was "
                                               "learned, Lessons).", "tone": "ok"}})  # fmt: skip


UNDONE = "undone right after approving"  # the reason an approval undone from its banner is reopened with


@router.post("/review/{invoice_id:int}/undo-approve")
def undo_approve(request: Request, invoice_id: int) -> JSONResponse:
    """The approval banner's Undo: an invoice just approved (not exported) back in the review queue, its coding kept
    and what its approval taught withdrawn, as Reopen for correction does."""
    store = get_store(request)
    inv = _invoice(store, invoice_id)
    if inv.get("export_batch"):
        how = "is on keying sheet" if inv["export_batch"] in keying_batches(store) else "was exported in batch"
        return refuse(f"It can't be undone now: it {how} {inv['export_batch']}. To correct it, undo "
                      "the batch on the Exports page first.")  # fmt: skip
    if inv["status"] not in (APPROVED, PENDING):  # the real reason (B-803): rejected, parked, back in the queue
        now = {REVIEW: "it is back in the review queue already", REJECTED: "it was rejected since",
               PARKED: "it was parked since"}.get(inv["status"], "it is no longer approved")  # fmt: skip
        return refuse(f"There is no approval to undo: {now}. Open it to see where it stands.")
    found = identity.person()
    if found is not None and found.user_id and found.role != "admin":  # server mode: one's own approval (D-98)
        last = next((e for e in store.events(invoice_id, ["approved", "final_approved"])), None)
        if ((last or {}).get("detail") or {}).get("user_id") != found.user_id:
            return refuse("Only the person who gave this approval (or an Admin) can undo it. To correct it, use "
                          "Reopen for correction.", 403)  # fmt: skip
    me = reviewer()
    try:
        store.reopen(invoice_id, me, UNDONE)
    except ValueError:
        return changed_meanwhile(request, invoice_id)
    on_reopen(store, invoice_id, me, UNDONE, before=inv)
    return done(
        request,
        f"/review/{invoice_id}",
        flash_message("Approval undone: it is back in the review queue.", "ok", icon="undo"),
    )


# --- Park, the manager, reject, notes -----------------------------------------------------------------------------


@router.post("/review/{invoice_id:int}/park")
def park(request: Request, invoice_id: int, reason: str = Form(""), follow_up: str = Form(""),
         show: str = Form(""), q: str = Form(""), sort: str = Form(""), limit: str = Form(""),
         next_id: str = Form("")) -> JSONResponse:  # fmt: skip
    """Set it aside while waiting for information (out of the queue until brought back)."""
    store = get_store(request)
    _invoice(store, invoice_id)
    if not reason.strip():
        return refuse("Say what it is waiting for.")
    follow = None
    if follow_up.strip():
        try:
            follow = dt.date.fromisoformat(follow_up.strip())
        except ValueError:
            return refuse("The follow-up date must be a date (YYYY-MM-DD).")
        if follow < dt.date.today():
            return refuse("Pick a follow-up date from today on.")
    query = _nav_query({"show": show, "q": q, "sort": sort, "limit": limit})
    after = next_url(store, invoice_id, query, next_id, request)
    try:
        store.park_invoice(invoice_id, reviewer(), reason.strip(), follow.isoformat() if follow else None)
    except ValueError:
        return changed_meanwhile(request, invoice_id)
    return done(request, after, flash_message(f"Invoice #{invoice_id} parked. It is in the Parked tab.", "ok",
                                              icon="pause_circle"))  # fmt: skip


@router.post("/review/{invoice_id:int}/manager")
async def send_to_manager(request: Request, invoice_id: int) -> JSONResponse:
    """Ask the manager of the invoice's cost centers to confirm how it is coded (D-138): the lines on screen are kept
    with the request and the invoice waits, parked, until the manager answers (pages/manager.py)."""
    form = await posted_form(request)
    try:
        return await run_in_threadpool(_send_to_manager, request, invoice_id, form)
    except _OutOfDate as stale:  # another tab's check wrote the draft meanwhile (B-1938)
        return refuse(stale.stale["text"], 409, stale=stale.stale)


@one_connection
def _send_to_manager(request: Request, invoice_id: int, form: Any) -> JSONResponse:
    from ap_coder.core.store.users import MANAGER

    store, settings = get_store(request), get_settings(request)
    inv = _invoice(store, invoice_id)
    if inv["status"] != REVIEW:
        return changed_meanwhile(request, invoice_id)
    if behind := _acted_on_meanwhile(request, store, invoice_id, inv, form):
        return behind
    draft = _from_form(request, inv, form)
    analysis = view.analyse(store, settings, _reference(store), inv, draft, reviewer())
    lines = [dict(li) for li in (analysis.output.get("line_items") or [])]
    from ap_coder.core.reference_data import UNASSIGNED

    if not lines or any(str(li.get("predicted_gl_code") or "").strip() in ("", UNASSIGNED) for li in lines):
        return refuse("Code every line to a GL account first: the manager confirms how the invoice is coded.")
    name, email = str(form.get("manager_name") or "").strip(), str(form.get("manager_email") or "").strip()
    user_id = None
    if server_mode(request) and str(form.get("manager_user") or "").strip():
        try:
            picked = int(str(form.get("manager_user")))
        except ValueError:
            return refuse("Pick a manager from the list.")
        account = next((u for u in store.list_users() if u["id"] == picked and u["role"] == MANAGER and u["active"]),
                       None)  # fmt: skip
        if account is None:
            return refuse("That manager's account is not active any more: pick another.")
        user_id, name = picked, account["display_name"] or account["username"]
    if not name:
        return refuse("Say who the manager is.")
    if email and ("@" not in email or any(c.isspace() for c in email)):
        return refuse("The manager's email doesn't look like one (name@company.com).")
    _keep_draft(request, invoice_id, draft, True, form=form)  # the edits on screen stay for its return
    # the next one while this one is in the queue: past those someone else has open, as park and approve (B-1939)
    after = next_url(store, invoice_id, _nav_query(form), form.get("next_id"), request) if user_id is not None else ""
    try:
        note = str(form.get("manager_note") or "")
        rid = store.send_to_manager(invoice_id, reviewer(), manager_name=name, manager_email=email,
                                    manager_user=user_id, lines=lines, note=note)  # fmt: skip
    except ValueError:
        return changed_meanwhile(request, invoice_id)
    centers = store.manager_request(rid)["cost_centers"]
    if form.get("remember_manager") and centers and user_id is None:
        store.set_cost_center_manager(centers, name, email, reviewer())
    if user_id is not None:
        said = f"Sent to {name}: it waits in their Manager confirmations and comes back to the queue when they answer."
        return done(request, after, flash_message(said, "ok", icon="forward_to_inbox"))
    return done(request, f"/manager/{rid}", flash_message(
        f"Parked, waiting for {name}. Download the request email below and send it; record the answer here when it "
        "comes.", "ok", icon="forward_to_inbox"))  # fmt: skip


@router.post("/review/{invoice_id:int}/reject")
def reject(request: Request, invoice_id: int, reason: str = Form(""), show: str = Form(""), q: str = Form(""),
           sort: str = Form(""), limit: str = Form(""), next_id: str = Form("")) -> JSONResponse:  # fmt: skip
    """Not ours, or not to be paid. It can be reopened later, with the edits made before, as after Park (B-2361)."""
    store = get_store(request)
    _invoice(store, invoice_id)
    after = next_url(store, invoice_id, _nav_query({"show": show, "q": q, "sort": sort, "limit": limit}), next_id,
                     request)  # fmt: skip
    try:
        store.reject_invoice(invoice_id, reviewer(), reason.strip())
    except ValueError:
        return changed_meanwhile(request, invoice_id)
    return done(request, after, flash_message(f"Invoice #{invoice_id} rejected.", "ok", icon="block"))


NOTE_ACTIONS = ("note", "parked", "sent_back")  # what the notes card lists


def history(events: list[dict[str, Any]]) -> list[dict[str, str]]:
    """The invoice's audit events as timeline items (``ui.timeline``), newest first, as Activity draws them."""
    return timeline_items(events, with_invoice=False)


@router.post("/review/{invoice_id:int}/note")
def note(request: Request, invoice_id: int, text: str = Form(""), back: str = Form(""),
         inline: str = Form("")) -> JSONResponse:  # fmt: skip
    store = get_store(request)
    _invoice(store, invoice_id)
    if not text.strip():
        return refuse("Write the note first.")
    store.add_note(invoice_id, reviewer(), text)
    if inline:  # added from the review screen without leaving it: the notes as they are now (D-60)
        notes = history([e for e in store.events(invoice_id) if e["action"] in NOTE_ACTIONS])
        return JSONResponse({"ok": True, "html": ui.timeline(notes), "count": len(notes),
                             "toast": {"text": "Note added.", "tone": "ok", "icon": "add_comment"}})  # fmt: skip
    return done(request, back or f"/review/{invoice_id}", flash_message("Note added.", "ok", icon="add_comment"))


# --- The other states ---------------------------------------------------------------------------------------------


@router.post("/review/{invoice_id:int}/resume")
def resume(request: Request, invoice_id: int) -> JSONResponse:
    """A parked invoice back in the review queue, opened to review it."""
    try:
        get_store(request).unpark_invoice(invoice_id, reviewer())
    except ValueError:
        return changed_meanwhile(request, invoice_id)
    return _back(request, invoice_id, f"Invoice #{invoice_id} is back in the review queue.", icon="play_circle")


@router.post("/review/{invoice_id:int}/restore")
def restore(request: Request, invoice_id: int) -> JSONResponse:
    """A rejected invoice back in the review queue (e.g. rejected by mistake)."""
    try:
        get_store(request).reopen(invoice_id, reviewer(), only_rejected=True)
    except ValueError:
        return changed_meanwhile(request, invoice_id)
    return _back(request, invoice_id, "Back in the review queue.", icon="undo")


@router.post("/review/{invoice_id:int}/correct")
def correct(request: Request, invoice_id: int, reason: str = Form("")) -> JSONResponse:
    """Reopen an approved invoice (not exported yet) for correction: what its approval taught is withdrawn and
    learned again at the next approval."""
    store = get_store(request)
    _invoice(store, invoice_id)  # no such invoice: not found
    if not reason.strip():
        return refuse("Say what needs correcting.")
    me = reviewer()
    before = store.get_invoice(invoice_id)  # who approved it: the reopen clears it
    try:
        store.reopen(invoice_id, me, reason.strip(), only_approved=True)  # never a rejection made since (B-1533)
    except ValueError:  # exported or rejected meanwhile, say: nothing reopened, no vendor suspended
        from ap_coder.web.pages.review import exported_reason

        why = exported_reason(store, store.get_invoice(invoice_id))
        if why:  # exported, or posted to JD Edwards: the real reason (B-1201)
            return _back(request, invoice_id, f"Not done: {why}.", "warn", icon="undo")
        return changed_meanwhile(request, invoice_id)
    on_reopen(store, invoice_id, me, reason.strip(), before=before)
    forget_everyones_draft(request, invoice_id)
    return _back(request, invoice_id, "Reopened: correct it and approve it again.", icon="undo")
