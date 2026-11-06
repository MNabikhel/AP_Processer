"""The invoice review screen, ``/review/<id>``: opened from the review queue (and from the other lists).

An invoice waiting for review opens as the workspace: the page with a box over every field read from it (click a
field to see it on the page, or teach a field by clicking its words), the checks, the invoice details, the line
items with their GL coding, the sales tax, the GL posting preview and the approve bar (Approve & teach, Park,
Reject, Delete). Any other invoice opens read-only, with what can be done in its state: the second approval or
sending it back, bringing a parked one back, reopening an approved (for correction) or a rejected one.

Every edit is checked as the reviewer types (``POST /review/<id>/check`` answers the sections again) and kept as a
draft, per person and in the store too (D-86), so moving to another invoice and back keeps it, a restart too.
What is worked out lives in ``ap_coder/web/invoice_view.py``; the page script is ``static/js/invoice.js`` with the
page viewer ``static/js/invoice_viewer.js``.

Routes (all under ``/review/<id>``): GET the screen, ``page/<n>.png`` (a page's picture), ``words`` (the words on
the pages, to teach a field), ``reader`` (the screen's poll: OvisOCR2's progress on it, who else has it open and
whether the screen is out of date, D-184), ``approved.pdf``; POST ``check``,
``approve``, ``undo-approve`` (the approval banner's Undo), ``park``, ``reject``, ``note``, ``teach``, ``split``,
``apply-rules``, ``po-coding``,
``suggestion``, ``fill-lines`` (Apply to all lines), ``vendor`` (add the printed vendor), ``read-pages``,
``review-again`` (the AI reviewer), ``start-over``, ``resume`` (a parked invoice), ``restore`` (a rejected one),
``correct`` (an approved one). ``GET /review/vendors.json``: the vendor list the screen picks from (D-160).
Delete, final approval and send back are the queue's own POSTs (``pages/review.py``).
"""

from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
import re
import secrets
import sqlite3
import threading
from copy import copy
from dataclasses import replace
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import urlencode

from fastapi import Form, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, Response
from markupsafe import Markup

from ap_coder.capture.confidence import LABELS
from ap_coder.capture.workflow import learn_from_approval, on_reopen, taught_message
from ap_coder.coding.tax import OUTSIDE_CANADA, PROVINCE_NAMES
from ap_coder.coding.vendors import MOVED_OFF_HOLD, ON_HOLD, mask_account
from ap_coder.core import audit, ui
from ap_coder.core.memory import ACCEPTED, CODED, CORRECTED, vendor_key
from ap_coder.core.reference_data import ReferenceData
from ap_coder.core.schema import InvoiceCoding
from ap_coder.core.store import APPROVED, FAILED, PARKED, PENDING, REJECTED, REVIEW, Store, coded_blanks
from ap_coder.core.store.users import same_approver
from ap_coder.erp import jde, stamp
from ap_coder.web import document, identity
from ap_coder.web import invoice_view as view
from ap_coder.web.access import is_admin, key_of, may_lift_hold, person_key
from ap_coder.web.core import (
    can,
    done,
    flash_message,
    get_settings,
    get_store,
    login,
    message,
    one_connection,
    page_router,
    posted_form,
    reading_status_or_none,
    reference_or_none,
    refuse,
    render,
    reviewer,
    server_mode,
    templates,
)
from ap_coder.web.files import download
from ap_coder.web.identity import shown_path
from ap_coder.web.pages.activity import timeline_items
from ap_coder.web.pages.review import attention, draft_reasons, filter_queue, sort_queue

MAX_VENDOR_NAME = 120  # a vendor name typed in Add new vendor (B-1531)
router = page_router()  # each plain handler: one connection a request

VIEWER_HEIGHT = 760  # px; the page scrolls inside, never taller than the window
PAGE_READER_POLL_SECONDS = 20  # while OvisOCR2 has the open invoice in its queue
NAV_PARAMS = ("show", "q", "sort", "limit")  # the queue's view (its rows: Show more), carried along so Previous / Next
# and the Queue button follow it


# --- Drafts: edits not approved yet --------------------------------------------------------------------------------
# Per invoice and per person: this computer's in ``app.state.invoice_drafts``, each colleague's on the office network
# (web/identity.py) in ``app.state.person_drafts[(<their name, case aside>, <their browser's id>)]``, so one person's
# edits never open on another's screen, even one who gives the same name on another computer, and stay theirs when
# their computer gets a new address on the office network; a new name on /who takes them along. Each is kept in the
# store too (``invoice_drafts``, D-86) and read back when AP Coder starts again: "leave and come back, they are still
# here" holds over a restart.

_drafts_lock = threading.Lock()


def _person(key: tuple[str, ...] | None) -> str:
    """The store's name for whose draft it is: "" this computer, else the colleague's key."""
    return "" if key is None else json.dumps(list(key))


def _holder(request: Request) -> Any:
    """Where the drafts are held: the app's state (one process, read from the store once), or, with several web
    workers (the company server, D-111), this request's, read from the store afresh: a draft another worker kept or
    dropped is seen at once."""
    if getattr(request.app.state, "processes", 1) > 1:
        return request.state
    return request.app.state


def _all_drafts(request: Request) -> list[dict[int, dict[str, Any]]]:
    state = _holder(request)
    with _drafts_lock:
        if not hasattr(state, "invoice_drafts"):  # AP Coder (re)started: the drafts kept in the store
            state.invoice_drafts, state.person_drafts = {}, {}
            try:
                kept = get_store(request).invoice_drafts()
            except Exception:  # noqa: BLE001 - no drafts rather than no review screen
                kept = []
            for invoice_id, person, draft in kept:
                try:
                    key = tuple(json.loads(person)) if person else None
                except ValueError:
                    continue
                target = state.invoice_drafts if key is None else state.person_drafts.setdefault(key, {})
                target[invoice_id] = draft
        if not hasattr(state, "person_drafts"):
            state.person_drafts = {}
        return [state.invoice_drafts, *state.person_drafts.values()]


def _drafts(request: Request) -> dict[int, dict[str, Any]]:
    """The edits of the person asking: this computer's, or a colleague's on the office network."""
    _all_drafts(request)
    key = person_key()  # None: this computer's own person (web/access.py)
    if key is None:
        return _holder(request).invoice_drafts
    with _drafts_lock:
        return _holder(request).person_drafts.setdefault(key, {})


def rename_drafts(request: Request, found: identity.Person, now: identity.Person) -> None:
    """A colleague gave a new name on /who: their edits go with them (else they would be no one's, and bulk approval
    would leave those invoices alone until a restart)."""
    _all_drafts(request)
    everyone = _holder(request).person_drafts
    with _drafts_lock:
        kept = everyone.pop(key_of(found), None) if key_of(found) != key_of(now) else None
        if kept:
            everyone.setdefault(key_of(now), {}).update(kept)
    if kept:
        store = get_store(request)
        store.drop_invoice_drafts(person=_person(key_of(found)))
        for invoice_id, draft in kept.items():
            store.save_invoice_draft(invoice_id, _person(key_of(now)), draft)


def unsaved_edits(request: Request) -> set[int]:
    """The invoices with edits on someone's review screen not approved yet (bulk approval would approve them without
    the edits, so it leaves them alone)."""
    return {invoice_id for drafts in _all_drafts(request) for invoice_id in drafts}


def forget_draft(request: Request, invoice_id: int) -> None:
    """Drop the edits of the person asking (Start over)."""
    get_store(request).drop_invoice_drafts(invoice_id, _person(person_key()))  # first: it may be left undone
    _drafts(request).pop(invoice_id, None)


def forget_everyones_draft(request: Request, invoice_id: int) -> None:
    """Drop everyone's edits of an invoice that was approved, rejected, deleted or reopened: they no longer apply."""
    for drafts in _all_drafts(request):
        drafts.pop(invoice_id, None)
    get_store(request).drop_invoice_drafts(invoice_id)


def forget_all_drafts(request: Request) -> None:
    """After a restore the invoices are the backup's (and their numbers are given again): no edit applies to them."""
    for drafts in _all_drafts(request):
        drafts.clear()
    get_store(request).drop_invoice_drafts()


def _reader_at(inv: dict[str, Any]) -> str:
    return str(((inv.get("meta") or {}).get("page_reader") or {}).get("at") or "")


def _start(inv: dict[str, Any]) -> dict[str, Any]:
    """What the screen starts from: a sent-back invoice reopens with its first approver's version (corrections
    kept); the AI's own output stays the reference for what was changed and what is learned."""
    return inv.get("final_output") or inv.get("ai_output") or {}


def current_draft(request: Request, inv: dict[str, Any]) -> tuple[view.Draft, bool]:
    """The screen's state: the draft kept for it, else the stored coding; and whether OvisOCR2's reading came in
    after the draft was started (its edits are kept: the screen offers to start over from the reading)."""
    saved = _drafts(request).get(inv["id"])
    if saved is None:
        return view.Draft.stored(_start(inv), _reader_at(inv), approved=bool(inv.get("final_output"))), False
    draft = view.Draft(header=dict(saved["header"]), lines=list(saved["lines"]), taxes=list(saved["taxes"]),
                       taught=dict(saved.get("taught") or {}), reader_at=saved.get("reader_at", ""),
                       absent=list(saved.get("absent") or []), from_vendor=dict(saved.get("from_vendor") or {}),
                       pay_from_for=str(saved.get("pay_from_for") or ""))  # fmt: skip
    return draft, draft.reader_at != _reader_at(inv)


_NOT_THE_EDITS = ("saved_at", "check", "screen")  # what a kept draft carries besides the edits


def my_drafts(request: Request) -> dict[int, dict[str, Any]]:
    """The drafts of the person asking, by invoice: each its edits, ``saved_at`` (when last edited) and ``check``
    ({"why": what keeps it from being approved, "" when nothing does}), as the queue shows them (B-996)."""
    return _drafts(request)


def _keep_draft(request: Request, invoice_id: int, draft: view.Draft, keep: bool, why: str | None = None,
                screen: str = "") -> None:  # fmt: skip
    """Keep the edits (``keep``) or drop them. ``saved_at`` is when they were last edited: the same edits checked again
    (the invoice opened again) keep their time (B-997). ``why``: the draft's own blocker, for the queue (B-996).
    ``screen``: the review screen they come from (the form's ``screen``), kept as the one that wrote them (D-184)."""
    if keep:
        edits = draft.to_dict()
        before = _drafts(request).get(invoice_id) or {}
        same = before and {k: v for k, v in before.items() if k not in _NOT_THE_EDITS} == json.loads(json.dumps(edits))
        check = {"why": why} if why is not None else before.get("check")
        if same and before.get("saved_at") and before.get("check") == check:
            return  # nothing new to keep
        at = before["saved_at"] if same and before.get("saved_at") else dt.datetime.now().isoformat(timespec="minutes")
        kept = {**edits, "saved_at": at, **({"check": check} if check is not None else {})}
        if writer := before.get("screen") if same else screen:
            kept["screen"] = writer
        get_store(request).save_invoice_draft(invoice_id, _person(person_key()), kept)  # first: it may be left undone
        _drafts(request)[invoice_id] = kept
    elif invoice_id in _drafts(request):
        forget_draft(request, invoice_id)


def _keeps(draft: view.Draft, analysis: view.Analysis) -> bool:
    """Whether a check keeps the screen's edits as a draft: a field to fix, an edit of the coding, words taught."""
    return analysis.coding is None or analysis.changed or bool(draft.taught)


# --- Many people on one queue (D-184) -------------------------------------------------------------------------------
# Who else has an invoice open (server mode): the screen's poll renews it, written at most every OPEN_RENEW_SECONDS
# and gone OPEN_SECONDS after the last renewal; Previous / Next pass over those invoices. A screen carries two
# revisions: the invoice's (``Store.coding_revision``, the form's ``rev``) and the person's draft's (``draft_rev``, in
# the approve bar each check draws again). A screen behind either one is out of date: its poll or its next check
# says so once and it turns read-only; a check or a line action from it changes nothing, an action is "changed
# meanwhile". On a laptop (one person) nobody else is shown; the revisions hold between its tabs.

OPEN_SECONDS = 120  # someone's invoice open: forgotten this long after the screen's last renewal (a closed tab)
OPEN_RENEW_SECONDS = 45  # renewed at most this often (the screen asks every 30 to 60 s)
WATCH_SECONDS = 30  # how often the screen asks while nothing is being read
NO_DRAFT = "0"  # the draft revision of no draft kept


def _draft_rev(kept: dict[str, Any] | None) -> str:
    """A draft's revision: its edits in a few letters (``NO_DRAFT`` when none is kept). The same edits keep it."""
    if not kept:
        return NO_DRAFT
    edits = {k: v for k, v in kept.items() if k not in _NOT_THE_EDITS}
    return hashlib.blake2b(json.dumps(edits, sort_keys=True).encode(), digest_size=6).hexdigest()


def shown_name(name: str) -> str:
    """A person's name as the screens show it to the others: never an email (the part before its @)."""
    name = str(name or "").strip()
    return name.split("@")[0] if "@" in name else name


def names_text(names: list[str]) -> str:
    """ "Ann Lee", "Ann Lee and Bo Chen", "Ann Lee, Bo Chen and 2 others"."""
    if len(names) <= 2:
        return " and ".join(names)
    return f"{names[0]}, {names[1]} and {ui.plural(len(names) - 2, 'other')}"


def open_elsewhere(request: Request, store: Store, invoice_id: int | None = None) -> dict[int, list[str]]:
    """The invoices someone else has open now, with their names (every one's, or one's); none on a laptop."""
    if not server_mode(request):
        return {}
    me = _person(person_key())
    found = {}
    for open_id, people in store.open_by(invoice_id).items():
        names = list(dict.fromkeys(shown_name(name) for who, name in people.items() if who != me))
        if names:
            found[open_id] = names
    return found


def working_here(names: list[str]) -> str:
    """What the screen says of the others who have it open ("" when nobody else does)."""
    return f"{names_text(names)} {'is' if len(names) == 1 else 'are'} working on this invoice too." if names else ""


def _keep_open(request: Request, store: Store, inv: dict[str, Any]) -> None:
    """The person asking has the invoice open (server mode, in the review queue, a role that may edit it)."""
    if server_mode(request) and inv["status"] == REVIEW and can(request, f"/review/{inv['id']}/check"):
        store.keep_open(inv["id"], _person(person_key()), reviewer(), OPEN_SECONDS, OPEN_RENEW_SECONDS)


_CHANGES = {"approved": "Approved", "final_approved": "Given its second approval", "sent_back": "Sent back",
            "reopened": "Reopened", "parked": "Parked", "unparked": "Brought back from Parked", "rejected": "Rejected",
            "deleted": "Deleted", "sent_to_manager": "Sent to the manager to confirm the coding",
            "manager_confirmed": "Coding confirmed", "manager_sent_back": "Coding sent back",
            "manager_request_withdrawn": "Request to the manager withdrawn"}  # fmt: skip
STALE_SUB = "This screen is read-only now: what you typed is still here to copy."


def _stale(store: Store, invoice_id: int, query: dict[str, str], gone: bool, draft: dict[str, Any] | None = None,
           mine: bool = False) -> dict[str, str]:  # fmt: skip
    """What an out-of-date screen says, once: what was done since it was drawn, by whom and when ("Approved by Ann
    Lee today at 10:41 a.m.", ``ui.day_and_time``), or ``mine``: the person's own edits saved in another tab
    (``draft``, None: dropped there); and where to see the invoice as it is now (the queue: ``gone``, deleted)."""
    if mine:
        at = _when(str((draft or {}).get("saved_at") or ""))
        text = f"Edited in another tab or window {at}" if draft and at else "Edits undone in another tab or window"
    else:
        change = store.last_change(invoice_id) or {}
        verb = _CHANGES.get(str(change.get("action") or ""), "Changed")
        by = f" by {shown_name(change['actor'])}" if change.get("actor") else ""
        try:
            at = " " + ui.day_and_time(dt.datetime.fromisoformat(str(change.get("created_at") or "")))
        except ValueError:
            at = ""
        text = f"{verb}{by}{at}"
    text += "" if text.endswith(".") else "."  # ("10:41 a.m." ends its sentence)
    return {"text": text, "sub": STALE_SUB, "url": _with_query("/review" if gone else f"/review/{invoice_id}", query),
            "link": "Open the queue" if gone else "Open it as it is now"}  # fmt: skip


def _out_of_date(request: Request, store: Store, invoice_id: int, inv: dict[str, Any] | None,
                 form: Any) -> dict[str, str] | None:  # fmt: skip
    """``_stale`` when the screen that posted ``form`` is behind the invoice (``rev``) or the person's draft
    (``draft_rev``: one of the revisions it may be at), else None; a form with neither (not the screen's) is not
    compared."""
    rev, drawn = form.get("rev"), form.get("draft_rev")
    query = _nav_query(form)
    if rev is not None and (inv is None or inv["status"] != REVIEW or str(store.coding_revision(invoice_id)) != rev):
        return _stale(store, invoice_id, query, inv is None)
    kept = _drafts(request).get(invoice_id)
    if drawn is not None and not _at_draft(kept, str(drawn), str(form.get("screen") or "")):
        return _stale(store, invoice_id, query, False, kept, mine=True)
    return None


def _at_draft(kept: dict[str, Any] | None, drawn: str, screen: str) -> bool:
    """Whether a screen at revisions ``drawn`` is at the person's draft ``kept``: one of them, or the draft written
    by that screen itself (``screen``: a check of its own answered after it asked)."""
    return _draft_rev(kept) in drawn.split() or bool(kept and screen and kept.get("screen") == screen)


class _OutOfDate(Exception):
    """A line action from a screen out of date (``_out_of_date``): refused as its check is."""

    def __init__(self, stale: dict[str, str]) -> None:
        super().__init__(stale["text"])
        self.stale = stale


# --- Helpers --------------------------------------------------------------------------------------------------------


def _invoice(store: Store, invoice_id: int) -> dict[str, Any]:
    inv = store.get_invoice(invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="That invoice is not in AP Coder (it may have been deleted).")
    return inv


NOT_IN_REVIEW = "This invoice is no longer in the review queue: reload the page."


def _in_review(store: Store, invoice_id: int) -> dict[str, Any]:
    """The invoice, still in the review queue; refused (409) when it was approved, parked... meanwhile."""
    inv = _invoice(store, invoice_id)
    if inv["status"] != REVIEW:
        raise HTTPException(status_code=409, detail=NOT_IN_REVIEW)
    return inv


def _fix_first(analysis: view.Analysis) -> InvoiceCoding:
    """The coding on screen; refused while a field keeps it from being built."""
    if analysis.coding is None:
        raise HTTPException(status_code=400, detail="Fix this field first: " + "; ".join(analysis.problems))
    return analysis.coding


def _back(request: Request, invoice_id: int, text: str, tone: str = "ok", icon: str = "") -> JSONResponse:
    """The invoice's screen again, with a message."""
    return done(request, f"/review/{invoice_id}", flash_message(text, tone, icon=icon))


def _nav_query(params: Any) -> dict[str, str]:
    return {k: str(params.get(k)) for k in NAV_PARAMS if params.get(k)}


def _with_query(path: str, query: dict[str, str]) -> str:
    return path + (f"?{urlencode(query)}" if query else "")


def navigation(store: Store, invoice_id: int, query: dict[str, str], request: Request | None = None) -> dict[str, Any]:
    """The invoices Previous / Next step through: the queue as shown (filter, search, sort in ``query``), or the
    whole queue if this invoice isn't part of that view. ``request``: the person asking, whose invoices with edits
    kept the queue shows under Needs attention (B-1620). In the queue's own order (no search, sort or view of its
    own) only its ids are read (B-1833)."""
    show = query.get("show", "all")
    if show not in ("attention", "ready") and not query.get("q") and query.get("sort", "priority") == "priority":
        ids = store.listed_ids(REVIEW)
    else:
        pending = store.list_invoices(REVIEW)
        reasons = attention(store, reference_or_none(store), pending) if show in ("attention", "ready") else {}
        if show in ("attention", "ready") and request is not None:
            reasons.update(draft_reasons(request, pending))
        shown = {"attention": [i for i in pending if i["id"] in reasons],
                 "ready": [i for i in pending if i["id"] not in reasons]}.get(show, pending)  # fmt: skip
        shown = sort_queue(filter_queue(shown, query.get("q", "")), query.get("sort", "priority"))
        ids = [i["id"] for i in shown]
        if invoice_id not in ids:
            ids = [i["id"] for i in pending]
    if invoice_id not in ids:
        return {"ids": ids, "position": None, "count": len(ids), "prev": "", "next": "", "next_id": None, "busy": {},
                "skipped": ""}  # fmt: skip
    position = ids.index(invoice_id)
    url = lambda i: _with_query(f"/review/{i}", query)  # noqa: E731
    # The ones someone else has open are passed over (D-184), unless every other one is
    busy = open_elsewhere(request, store) if request is not None else {}
    if all(i in busy for i in ids if i != invoice_id):
        busy = {}
    prev, before = _step(ids, position, busy, -1)
    after, passed = _step(ids, position, busy, 1)
    later = [i for i in ids[position + 1 :] if i not in busy]
    return {
        "ids": ids,
        "position": position,
        "count": len(ids),
        "done_pct": round((position + 1) / len(ids) * 100),
        # Always enabled (wrapping around), so Alt+Left/Right never fall through to the browser's Back/Forward.
        "prev": url(prev),
        "next": url(after),
        # The one after it as the screen showed it (none for the last): where an action on it goes next, even if
        # editing moved it in the queue's order meanwhile (B-426).
        "next_id": later[0] if later else None,
        "busy": busy,
        "skipped": _skipped(busy, before, passed),
    }


def _step(ids: list[int], position: int, busy: dict[int, list[str]], by: int) -> tuple[int, list[int]]:
    """The invoice ``by`` (1 the next, -1 the previous) from ``position`` (wrapping around), past those someone else
    has open (``busy``), and the ones passed over."""
    passed = []
    for k in range(1, len(ids)):
        found = ids[(position + by * k) % len(ids)]
        if found not in busy:
            return found, passed
        passed.append(found)
    return ids[(position + by) % len(ids)], []


def _skipped(busy: dict[int, list[str]], before: list[int], after: list[int]) -> str:
    """What the screen says of the invoices Previous / Next pass over ("" when none is)."""
    passed = list(dict.fromkeys(after + before))
    if not passed:
        return ""
    names = list(dict.fromkeys(name for i in passed for name in busy[i]))
    which = "Previous and Next skip" if before and after else "Next skips" if after else "Previous skips"
    return f"{which} {len(passed)} that {names_text(names)} {'has' if len(names) == 1 else 'have'} open."


def next_url(store: Store, invoice_id: int, query: dict[str, str], seen_next: Any = None,
             request: Request | None = None) -> str:  # fmt: skip
    """After an action on this invoice: the next one in the queue as shown (or the queue when none is left).
    ``seen_next``: the invoice that followed it when its screen was drawn (the form's ``next_id``), opened when it is
    still waiting: the edits refresh the invoice's confidence, which moves it in the queue's order while it is open,
    and the one after its new place would skip those in between (B-426). ``request``: as ``navigation``'s."""
    nav = navigation(store, invoice_id, query, request)
    ids, position = nav["ids"], nav["position"]
    remaining = [i for i in ids if i != invoice_id]
    if not remaining:
        return _with_query("/review", query)
    try:
        seen = int(seen_next or 0)
    except (TypeError, ValueError):
        seen = 0
    free = [i for i in remaining if i not in nav["busy"]] or remaining  # none someone else has open (D-184)
    if seen in free:
        return _with_query(f"/review/{seen}", query)
    if position is None or position >= len(remaining):  # it was the last one: back to the first, as said (B-998)
        return _with_query(f"/review/{free[0]}", query)
    return _with_query(f"/review/{next(i for i in remaining[position:] + remaining[:position] if i in free)}", query)


_NOW = {APPROVED: "approved", PENDING: "approved, waiting for a second approval", REJECTED: "rejected",
        PARKED: "parked", REVIEW: "in the review queue", FAILED: "a failed read"}  # fmt: skip


def _acted_on_meanwhile(request: Request, store: Store, invoice_id: int, inv: dict[str, Any],
                        form: Any) -> JSONResponse | None:  # fmt: skip
    """An action of the screen (approve, send to the manager) from a screen out of date (``_out_of_date``): while
    the invoice is in the review queue, refused as its check is (409, ``stale``: the screen says so and keeps what was
    typed in its boxes, B-1936); once it left it, "changed meanwhile" (D-184). None: up to date."""
    stale = _out_of_date(request, store, invoice_id, inv, form)
    if stale is None:
        return None
    if inv["status"] == REVIEW:
        return refuse(stale["text"], 409, stale=stale)
    return changed_meanwhile(request, invoice_id)


def changed_meanwhile(request: Request, invoice_id: int) -> JSONResponse:
    """The invoice was acted on after this screen was drawn (in another tab, or by someone else): say so plainly and
    show where it stands now (the queue, when it was deleted)."""
    inv = get_store(request).get_invoice(invoice_id)
    if inv is None:
        return done(request, "/review", flash_message("Not done: this invoice was deleted meanwhile.", "warn",
                                                      icon="sync_problem"))  # fmt: skip
    now = _NOW.get(inv["status"], str(inv["status"]))
    by = f" by {inv['reviewer']}" if inv["status"] in (APPROVED, PENDING) and inv.get("reviewer") else ""
    return _back(request, invoice_id, f"Not done: this invoice changed meanwhile, in another tab or by someone else. "
                 f"It is {now}{by}.", "warn", icon="sync_problem")  # fmt: skip


NOTE_ACTIONS = ("note", "parked", "sent_back")  # what the notes card lists


def history(events: list[dict[str, Any]]) -> list[dict[str, str]]:
    """The invoice's audit events as timeline items (``ui.timeline``), newest first, as Activity draws them."""
    return timeline_items(events, with_invoice=False)


def _lesson_hints(store: Store, inv: dict[str, Any]) -> list[str]:
    """What AP taught for this vendor (lessons.py), shown as "Learned" hints beside the document."""
    from ap_coder.ai import lessons

    output = inv.get("final_output") or inv.get("ai_output") or {}
    return lessons.prompt_lines(
        store,
        output.get("vendor_name") or "",
        lessons.kind_of(output),
        gst=output.get("gst_hst_registration_number"),
        limit=5,
        screen=True,
    )  # a person decides: shown from the first correction (D-72)


def viewer_data(inv: dict[str, Any], capture: Any, sizes: list[tuple[float, float]]) -> dict[str, Any]:
    """What the page viewer draws: the pages (pictures by address), the fields with their boxes, the line items."""
    v = document.version(inv["source_path"])
    pages = [{"src": f"/review/{inv['id']}/page/{n}.png?v={v}", "width": w, "height": h}
             for n, (w, h) in enumerate(sizes, start=1)]  # fmt: skip
    from ap_coder.capture.bridge import currency_read

    # A currency the page does not print (the domestic CAD assumed) is nothing to check on the page.
    shown = [f for f in capture.fields.values()
             if not (f.field == "currency" and not currency_read(f) and str(f.value or "CAD").upper() == "CAD")
             ] if capture is not None else []  # fmt: skip
    fields = document.shape_fields(shown, LABELS) if capture is not None else []
    lines = document.shape_line_items(capture.line_items) if capture is not None else []
    return {"pages": pages, "fields": fields, "line_items": lines, "height": VIEWER_HEIGHT}


# With LM Studio off, the banner over an invoice being reviewed says what the two notes under the document title said
# again (OvisOCR2 isn't running…; the AI reviewer checks it when the chat model is free…): one line, so the invoice's
# picture shows on a 1366 x 768 laptop (B-444).
LM_STUDIO_OFF = ("LM Studio isn't running: a person approves it; OvisOCR2 and the AI reviewer check it once LM Studio "
                 "runs.")  # fmt: skip


def lm_studio_off(status: Any) -> bool:
    """LM Studio itself isn't running (not: OvisOCR2 missing, testing itself or failed, which keep their notes)."""
    return (status is not None and not getattr(status, "page_reader_ready", True)
            and not getattr(status, "lm_studio", True) and bool(getattr(status, "banner", "")))  # fmt: skip


def folded_reader(state: dict[str, Any]) -> dict[str, Any]:
    """OvisOCR2's line for an invoice waiting for it, or not queued, without "OvisOCR2 isn't running" (the banner
    says it): its other lines (a try cut off last time) and its button stay."""
    if state.get("state") not in ("waiting", "none"):
        return state
    return {
        **state,
        "lines": [(i, t) for i, t in state.get("lines") or [] if i not in ("pause_circle", "visibility_off")],
    }


def folded_reviewer(state: dict[str, Any]) -> dict[str, Any]:
    """The AI reviewer's "checks this invoice when the chat model is free" line, left out (the banner says it)."""
    return {**state, "lines": []} if state.get("state") == "waiting" else state


def _document(store: Store, settings: Any, inv: dict[str, Any], status: Any, editable: bool,
              capture: Any) -> dict[str, Any]:  # fmt: skip
    """The document card: the pages (or the extracted text), what OvisOCR2 made of it, how the lines were read.
    ``capture``: the invoice's (``view.capture_of``)."""
    from ap_coder.capture.line_items import sources_line

    path = Path(inv.get("source_path") or "")
    sizes: list[tuple[float, float]] = []
    problem = ""
    if not path.exists():  # (a file an update moved was found again by get_invoice)
        problem = f"This invoice's file is no longer at {shown_path(path)}."
    elif path.suffix.lower() in document.VIEWABLE:
        try:
            sizes = document.page_sizes(path)
        except Exception:  # noqa: BLE001 - a damaged file: the extracted text still shows what was read
            problem = "This file could not be shown (it may be damaged)."
    original = (inv.get("meta") or {}).get("original") or {}
    folded = editable and lm_studio_off(status)  # the banner says LM Studio is off: the lines don't say it again
    reader, checker = view.reader_state(store, settings, inv, status), view.reviewer_state(store, inv)
    return {
        "file_name": inv.get("file_name") or path.name,
        "kind": view.document_kind(capture.layout_source) if capture is not None else None,
        "viewer": viewer_data(inv, capture, sizes) if sizes else None,
        "teachable": editable and bool(sizes) and capture is not None,
        "teach_fields": [(f, LABELS.get(f, f)) for f in view.TEACHABLE],
        "problem": problem,
        "text": Markup(ui.document_text(inv.get("extraction_md") or "")),  # noqa: S704 - document_text escapes
        "reader": folded_reader(reader) if folded else reader,
        "reviewer": folded_reviewer(checker) if folded else checker,
        "lessons": _lesson_hints(store, inv),
        "lines_note": sources_line(capture.line_items, capture.checks) if capture is not None else "",
        "original": {"file": str(original["file"]),
                     "folder": Path(str(original.get("kept") or "")).parent.name or "originals"}
        if original.get("file") else None,
    }  # fmt: skip


UNDONE = "undone right after approving"  # the reason an approval undone from its banner is reopened with


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


def _shown_live(request: Request, live: dict[str, Any], may_edit: bool = True, draft_rev: str = "") -> dict[str, Any]:
    """The sections' values with what the person's role may use (server mode, D-98): Read-only changes nothing, and
    the JD Edwards settings are an Admin's. ``draft_rev``: the draft revision the screen is at (D-184)."""
    return {**live, "read_only": not may_edit, "jde_settings": can(request, "/settings/jde", "GET"),
            "setup_contact": "an Admin" if server_mode(request) else "the pilot tester",
            "draft_rev": draft_rev}  # fmt: skip


def _render_live(live: dict[str, Any], invoice_id: int) -> dict[str, str]:
    """Every section that follows the edits, drawn again (``_invoice_live.html``)."""
    mod = templates.env.get_template("_invoice_live.html").module
    names = ("summary", "checks", "amounts", "reasons", "suggest", "po", "taxcheck", "posting", "bar", "jde")
    if not live["ok"]:  # a field to fix: the coding can't be built, so the rest keeps its last state
        names = ("checks", "amounts", "taxcheck", "posting", "bar")
    return {name: str(getattr(mod, name)(live, invoice_id)) for name in names}


def _render_lines(reference: ReferenceData, draft: view.Draft, ai: dict[str, Any] | None = None) -> str:
    mod = templates.env.get_template("_invoice_lines.html").module
    return str(mod.line_rows(view.grid(reference, draft, ai)))


def _reference(store: Store) -> ReferenceData:
    reference = reference_or_none(store)
    if reference is None:
        raise HTTPException(status_code=409, detail="Import your GL accounts first (GL accounts & tax).")
    return reference


# --- The screen -----------------------------------------------------------------------------------------------------


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
    capture = view.capture_of(store, invoice_id)  # read once: the document card and the checks use it
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
    sure = view.shown_sure(view.capture_confidence(capture), draft.header, inv.get("ai_output") or {}, capture)
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
        grid={**view.grid(reference, draft, inv.get("ai_output") or {},
                          view.vendor_accounts(store, draft.header.get("vendor_name") or "")),
              "tax_marks": analysis.live.get("tax_marks") or {}},
        labels=view.FIELD_LABELS, provinces=view.province_options(),
        due_placeholder=f"{computed.isoformat()} (from terms)" if computed else
        "None: a credit is not paid" if float(start.get("grand_total") or 0) < 0 else "YYYY-MM-DD",
        sent_back=_sent_back(events), poll_seconds=PAGE_READER_POLL_SECONDS,
        today=dt.date.today().isoformat(), taught_json=draft.taught, vendor_seed=vendor_seed(store, header),
        absent_ok=list(view.ABSENT_FIELDS), from_vendor_note=view.FROM_VENDOR_NOTE,
        tax_fields=list(view.TAX_NUMBER_FIELDS), pay_from=view.pay_from_choices(store, draft.header),
        unread_numbers=sorted(view.unread_numbers(inv.get("ai_output") or {}) or []),
        bank_shown=mask_account(bank) if (bank := draft.header.get("remit_bank_account")) else "",
        supply=view.supply_line(draft.header, draft.lines, reference.tax),
        usual_cc=view.usual_cost_center(store, reference, draft.header.get("vendor_name") or ""),
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


def _when(iso: str) -> str:
    """When a draft was last edited, as the screens say it (``ui.day_and_time``: "today at 4:23 p.m.")."""
    try:
        at = dt.datetime.fromisoformat(iso)
    except ValueError:
        return ""
    return ui.day_and_time(at)


def vendor_options(store: Store) -> dict[str, Any]:
    """The vendor list the review screen picks from: each vendor's name, its key (memory.vendor_key, which the name
    typed is compared with), ERP ID and tax numbers (the screen says whether the name typed is in it, and which
    vendor a printed GST/HST number belongs to)."""
    choices = store.vendor_choices()
    return {
        "enforced": store.has_vendor_master(),  # with a vendor master, an invoice's vendor must be in the list
        "list": [{"name": v["display_name"], "key": vendor_key(v["display_name"]), "id": v.get("erp_id") or "",
                  "gst": v.get("expected_gst") or "", "qst": v.get("qst_number") or "",
                  "pst": v.get("pst_number") or "", "added": v.get("in_master") == 2}
                 for v in choices if v.get("display_name")],
    }  # fmt: skip


class VendorList(NamedTuple):
    """The vendor list as the review screen fetches it (``GET /review/vendors.json``), kept by ``vendor_list``."""

    body: bytes  # the JSON of ``vendor_options``
    gzipped: bytes
    version: str  # the JSON's hash: the address's ?v=
    count: int
    enforced: bool
    by_key: dict[str, dict[str, Any]]  # the first vendor of each vendor key, and of each GST/HST business number,
    by_number: dict[str, dict[str, Any]]  # as invoice.js finds them in the list


def vendor_list(store: Store) -> VendorList:
    """The vendor list (``vendor_options``) as JSON, gzipped, with its version, its count and an index of it: worked
    out once per change of it in each process (``summary_memo``: ``changes/vendors``; without a vendor master the
    vendors approved before are in it too, the ``approved_vendors`` topic). 20,000 vendors: 3 MB of JSON, 0.4 MB
    gzipped, about 0.8 s to build and 21 MB kept. Shared: read it, don't change it."""
    master = store.has_vendor_master()
    return store.summary_memo("vendor_list", lambda: _vendor_list(store), "" if master else "approved_vendors",
                              changes="vendors", asked=master)  # fmt: skip


def _vendor_list(store: Store) -> VendorList:
    options = vendor_options(store)
    body = json.dumps(options, ensure_ascii=False, separators=(",", ":")).encode()
    by_key: dict[str, dict[str, Any]] = {}
    by_number: dict[str, dict[str, Any]] = {}
    for v in options["list"]:
        by_key.setdefault(v["key"], v)
        if number := business_number(v["gst"]):
            by_number.setdefault(number, v)
    return VendorList(body, gzip.compress(body, mtime=0), hashlib.sha256(body).hexdigest()[:16], len(options["list"]),
                      options["enforced"], by_key, by_number)  # fmt: skip


def business_number(value: Any) -> str:
    """A GST/HST number's business number as invoice.js ``businessNumber`` compares them: its first 9 digits, "" with
    fewer."""
    digits = re.sub(r"[^0-9]", "", str(value or ""))
    return digits[:9] if len(digits) >= 9 else ""


def vendor_seed(store: Store, header: dict[str, Any]) -> dict[str, Any]:
    """What the page holds of the vendor list (``#vendor-seed``; invoice.js fetches the rest): its address, whether a
    vendor master enforces it, its count, and the vendors the screen names as it opens (the invoice's vendor, the one
    whose GST/HST number it shows) and what it was asked about (so that none named is known too: "Not in the vendor
    list" is right at once, and a read-only screen fetches nothing)."""
    kept = vendor_list(store)
    key = vendor_key(header.get("vendor_name") or "")
    number = business_number(header.get("gst_hst_registration_number"))
    named = [kept.by_key.get(key) if key else None, kept.by_number.get(number) if number else None]
    return {"url": f"/review/vendors.json?v={kept.version}", "enforced": kept.enforced, "count": kept.count,
            "list": [v for i, v in enumerate(named) if v and v not in named[:i]],
            "asked": {"vendor": key, "number": number}}  # fmt: skip


@router.get("/review/vendors.json")
def vendors_json(request: Request, v: str = "") -> Response:
    """The vendor list the review screen picks from (``vendor_list``), fetched as an editable screen opens. On a laptop
    the browser keeps it for good while ``v`` is its version (the page says the version it was drawn with), and asks
    again otherwise (its ETag: 304 while it did not change). On the company server every answer behind the login is
    ``no-store`` (B-1070, ``identity._no_store``): nothing of the company's stays in a shared computer's browser."""
    kept = vendor_list(get_store(request))
    tag = f'"{kept.version}"'
    headers = {
        "ETag": f"W/{tag}",
        "Vary": "Accept-Encoding",
        "Cache-Control": "private, max-age=31536000, immutable" if v == kept.version else "private, no-cache",
    }
    asked = request.headers.get("if-none-match", "")
    if any(t.strip().removeprefix("W/") in (tag, "*") for t in asked.split(",")):
        return Response(status_code=304, headers=headers)
    if "gzip" in request.headers.get("accept-encoding", "").lower():
        return Response(kept.gzipped, media_type="application/json", headers={**headers, "Content-Encoding": "gzip"})
    return Response(kept.body, media_type="application/json", headers=headers)


@router.post("/review/{invoice_id:int}/vendor")
def add_vendor(
    request: Request, invoice_id: int, name: str = Form(""), gst: str = Form(""), qst: str = Form(""),
    pst: str = Form(""), province: str = Form(""), an8: str = Form(""),
) -> JSONResponse:  # fmt: skip
    """Add new vendor: a vendor not in the vendor list, added from the review screen. Its invoices need a person's
    "Approve anyway" (VENDOR_ADDED_HERE) until it is set up in JD Edwards and the vendor list is imported again. With
    JD Edwards on, its address number (AN8) can be given here (an Admin's, as in Settings → JD Edwards E1, D-112)."""
    store = get_store(request)
    _in_review(store, invoice_id)
    name = identity.clean_name(name, MAX_VENDOR_NAME)  # no control characters, a vendor name's length (B-1531)
    if province and province not in PROVINCE_NAMES and province != OUTSIDE_CANADA:
        return refuse("Choose a province from the list.")
    an8 = an8.strip()
    if an8 and not is_admin(request):
        return refuse("Only an Admin sets a vendor's JD Edwards address number (Settings → JD Edwards E1).", 403)
    if an8 and (not an8.isdigit() or len(an8) > 8):
        return refuse(f"“{an8}” is not a JD Edwards address number (AN8): digits only, up to 8.")
    before = store.get_vendor(vendor_key(name)) or {}
    if (before.get("status") == ON_HOLD or before.get("erp_hold")) and not may_lift_hold(request):
        held = before.get("display_name") or name  # its numbers replaced, the hold would not catch them (B-1554)
        return refuse(f"{held} is on hold: only an Approver can change its tax numbers. Pick it from the list.", 403)
    try:
        store.add_vendor_here(name, gst=gst, qst=qst, pst=pst, province=province, actor=reviewer(),
                              invoice_id=invoice_id)  # fmt: skip
    except ValueError as exc:
        return refuse(str(exc))
    said = f"Added {name} to the vendor list"
    if an8:
        settings = copy(jde.load(store))
        settings.an8_overrides = {**settings.an8_overrides, vendor_key(name): an8}
        jde.save(store, settings, actor=reviewer())
        said += f", with JD Edwards address number {an8}"
    return message(f"{said}. Set it up in JD Edwards before it is paid: until then a person approves each of its "
                   "invoices with “Approve anyway”.", "ok", icon="person_add", vendor=name,
                   key=vendor_key(name))  # fmt: skip


def _mode(inv: dict[str, Any]) -> str:
    if inv["status"] == REVIEW:
        return "nogl"  # waiting for review, but there are no GL accounts to code it with yet
    return {PENDING: "pending", PARKED: "parked", APPROVED: "approved", REJECTED: "rejected",
            FAILED: "failed"}.get(inv["status"], "other")  # fmt: skip


def _closed(store: Store, reference: ReferenceData | None, inv: dict[str, Any]) -> dict[str, Any]:
    """What the read-only screen shows for an invoice that is not being reviewed."""
    doc = inv.get("final_output") or inv.get("ai_output") or {}
    out: dict[str, Any] = {"stored": view.stored_summary(inv, doc), "posting": None, "edits": "", "same": False}
    if doc.get("gl_distribution"):
        out["posting"] = view.distribution(doc, reference)
    if inv["status"] in (APPROVED, PENDING):
        edits = inv.get("edits") or []
        changed = [e for e in edits if e != "line_coded"]
        out["edits"] = ("no changes to the suggested coding" if not changed
                        else "changed: " + ", ".join(view.EDIT_LABELS.get(e, e) for e in changed))  # fmt: skip
        if "line_coded" in edits:  # the reviewer's own coding where the AI proposed none (B-670)
            n = coded_blanks(inv.get("ai_output"), inv.get("final_output"))
            out["edits"] += (
                f"; AP coded {ui.plural(n, 'line')} the AI left blank" if n else "; AP coded lines the AI left blank"
            )
        out["can_stamp"] = stamp.can_stamp(inv)
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


@router.get("/review/{invoice_id:int}/page/{number:int}.png")
def page_picture(request: Request, invoice_id: int, number: int, scale: str = "1") -> Response:
    """The page as a picture; ``?scale=2`` or ``3`` draws it at 2 or 3 times the dpi for a page zoomed in (anything else
    is 1). The scale is in the address, so the browser keeps each one apart. On the company server the pictures are
    drawn once for all its web workers, in the data folder's ``.cache/pages`` (D-160)."""
    store = get_store(request)
    inv = _invoice(store, invoice_id)
    if not Path(inv.get("source_path") or "").is_file():
        gone = shown_path(inv.get("source_path"))  # its name alone to a colleague on another computer (B-979)
        raise HTTPException(status_code=404, detail=f"This invoice's file is no longer at {gone}.")
    kept_in = store.path.parent / ".cache" / "pages" if server_mode(request) else None
    try:
        png = document.page_png(inv["source_path"], number, document.clean_scale(scale), kept_in)
    except (IndexError, OSError) as exc:
        raise HTTPException(status_code=404, detail="That page is not in the invoice's file.") from exc
    except Exception as exc:  # noqa: BLE001 - a damaged file: said plainly
        raise HTTPException(status_code=404, detail="This page could not be drawn (the file may be damaged).") from exc
    # The address carries the file's version (?v=), so the picture can be kept by the browser.
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})


@router.get("/review/{invoice_id:int}/words")
def page_words(request: Request, invoice_id: int) -> JSONResponse:
    """The words on the invoice's pages, to teach a field by clicking them (``document.page_words``)."""
    inv = _invoice(get_store(request), invoice_id)
    if not Path(inv.get("source_path") or "").is_file():  # its name alone to a colleague (B-979, B-1403)
        gone = f"This invoice's file is no longer at {shown_path(inv.get('source_path'))}."
        return JSONResponse({"ok": False, "error": gone, "words": []})
    try:
        words = document.page_words(inv["source_path"])
    except Exception as exc:  # noqa: BLE001 - no OCR on this computer, a damaged file...
        said = identity.without_folders(str(exc))
        return JSONResponse({"ok": False, "error": f"The words on the page can't be read here ({said}).",
                             "words": []})  # fmt: skip
    return JSONResponse({"ok": True, "words": words})


@router.get("/review/{invoice_id:int}/reader")
def reader_progress(request: Request, invoice_id: int, seen: str = "", rev: str | None = None,
                    draft: str | None = None, screen: str = "") -> JSONResponse:  # fmt: skip
    """The review screen's poll: OvisOCR2's progress on this invoice, for the page to follow while it waits or reads
    (``reading``); on a screen of an invoice in the review queue, who else has it open (renewing the person's own,
    D-184) and, given the screen's revisions (``rev``, ``draft``), whether it is out of date (``stale``). ``seen``:
    the ``tag`` of what the page's panel shows (``reader_tag``): while it still is, the answer says ``same``, without
    the panel. ``poll``: whether to ask again."""
    store, settings = get_store(request), get_settings(request)
    inv = store.get_invoice(invoice_id)
    if inv is None and rev is not None:  # deleted meanwhile: the screen says so
        stale = _stale(store, invoice_id, _nav_query(request.query_params), True)
        return JSONResponse({"poll": False, "stale": stale}, headers={"Cache-Control": "no-store"})
    inv = inv if inv is not None else _invoice(store, invoice_id)
    status = reading_status_or_none(settings, store)
    state = view.reader_state(store, settings, inv, status)
    if inv["status"] == REVIEW and lm_studio_off(status):
        state = folded_reader(state)  # as the screen drew it (B-444)
    may = can(request, f"/review/{invoice_id}/read-pages")
    tag = reader_tag(state, may)
    watch = inv["status"] == REVIEW and reference_or_none(store) is not None  # as the screen's doc.watch
    answer = {"state": state["state"], "poll": state["poll"] or watch, "tag": tag, "same": bool(seen) and seen == tag}
    if answer["poll"] and not state["poll"]:
        answer["reading"] = False  # (else it is the poll's: OvisOCR2 has it, or nothing more to ask)
    if not answer["same"]:
        answer["html"] = str(templates.env.get_template("_invoice_live.html").module.reader(state, invoice_id, may))
    if watch and server_mode(request):
        answer["others"] = working_here(open_elsewhere(request, store, invoice_id).get(invoice_id, []))
        _renew_open(request, store, inv)
    query = _nav_query(request.query_params)
    if rev is not None and (inv["status"] != REVIEW or str(store.coding_revision(invoice_id)) != rev):
        answer["stale"] = _stale(store, invoice_id, query, False)
    elif draft is not None and inv["status"] == REVIEW:
        kept = store.invoice_draft(invoice_id, _person(person_key()))
        if not _at_draft(kept, draft, screen):
            answer["stale"] = _stale(store, invoice_id, query, False, kept, mine=True)
    return JSONResponse(answer, headers={"Cache-Control": "no-store"})


def _renew_open(request: Request, store: Store, inv: dict[str, Any]) -> None:
    """The screen's poll renews who has it open; behind another writer it waits for the next one (a poll never holds
    the page up)."""
    try:
        with store.briefly():
            _keep_open(request, store, inv)
    except sqlite3.OperationalError:
        pass


def reader_tag(state: dict[str, Any], may: bool) -> str:
    """What the reader panel shows (``_invoice_live.html`` ``reader`` of this state, its button if the role ``may``) in
    a few letters: the page's ``data-tag``, which its poll sends back (``?seen=``) to hear only that nothing changed."""
    shown = json.dumps([state, may], sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.blake2b(shown.encode(), digest_size=6).hexdigest()


@router.get("/review/{invoice_id:int}/approved.pdf")
def approved_pdf(request: Request, invoice_id: int) -> Response:
    store = get_store(request)
    return approved_pdf_download(store, _invoice(store, invoice_id))


def approved_pdf_download(store: Store, inv: dict[str, Any]) -> Response:
    """The approved invoice with its APPROVED stamp and coding page, as a download (here and from the invoice
    directory); 409 for one not fully approved (never before the second approval)."""
    if not stamp.can_stamp(inv):
        raise HTTPException(status_code=409, detail="No longer fully approved: no approved PDF.")
    reference = reference_or_none(store)
    names = {row[reference.chart_of_accounts.key_column]: row.get("description", "")
             for row in reference.chart_of_accounts.rows} if reference is not None else {}  # fmt: skip
    # (files.download: a vendor named "Bœuf" is not Latin-1, the only characters a plain filename= header takes)
    return download(stamp.stamped_pdf(inv, names, store.tax_treatments()), stamp.file_name(inv), "application/pdf")


# --- Checking the edits ---------------------------------------------------------------------------------------------


def _from_form(request: Request, inv: dict[str, Any], form: Any) -> view.Draft:
    base, _ = current_draft(request, inv)
    return view.Draft.from_form(form, base)


@router.post("/review/{invoice_id:int}/check")
async def check(request: Request, invoice_id: int) -> JSONResponse:
    """The screen's sections again for what is on it now; the edits are kept as a draft, and the queue shows the
    confidence this screen shows (edits and today's checks included). The form is read here; the checking runs on a
    threadpool thread, so a check never holds up the dashboard's other requests."""
    form = await posted_form(request)  # a long invoice posts thousands of fields (B-455)
    return await run_in_threadpool(_check, request, invoice_id, form)


@one_connection
def _check(request: Request, invoice_id: int, form: Any) -> JSONResponse:
    store, settings = get_store(request), get_settings(request)
    stale = _out_of_date(request, store, invoice_id, store.get_invoice(invoice_id), form)
    if stale:  # the screen says so once and turns read-only (D-184)
        return refuse(stale["text"], 409, stale=stale)
    inv = _in_review(store, invoice_id)
    reference = _reference(store)
    draft = _from_form(request, inv, form)
    analysis = view.analyse(store, settings, reference, inv, draft, reviewer())
    # the account lists' "Used for this vendor", for the vendor now in the vendor field (B-596)
    listed = {code for code, _ in view.gl_options(reference, draft.lines)}
    used = [c for c in view.vendor_accounts(store, draft.header.get("vendor_name") or "") if c in listed]
    kept = _keep_checked(request, store, invoice_id, draft, analysis, _screen(form))  # written last: read nothing after
    # the lines' tax codes where the invoice is supplied now, and the lines the Tax check points at (D-64)
    codes = view.tax_choices(draft, reference.tax)
    # What the server filled in the invoice details (the vendor list's tax numbers, the default Pay from account) and
    # the details to highlight: the screen shows them (invoice.js applyHeader).
    header = {"values": analysis.filled, "from_vendor": draft.from_vendor, "pay_from_for": draft.pay_from_for}
    if analysis.coding is not None:
        header["flags"] = analysis.live.get("flags") or {}
        header["flag_errors"] = analysis.live.get("flag_errors") or []  # red, as an error (B-796)
        header["help"] = analysis.live.get("help") or {}  # a note with a one-click fix under a field (D-75)
        header["an8"] = analysis.live.get("an8")  # the supplier's JD Edwards number, when it is the export in use
    return JSONResponse({"ok": True, "valid": analysis.coding is not None, "allow": analysis.allow, "draft": kept,
                         "problems": analysis.problems, "vendor_accounts": used, "header": header,
                         "tax_codes": codes["options"], "tax_code_labels": codes["labels"],
                         "tax_marks": {str(k): v for k, v in (analysis.live.get("tax_marks") or {}).items()},
                         # the tax rows as checked: a row moved to a new place of supply moves on screen (B-1020)
                         "taxes": [{"tax_type": t.get("tax_type") or "", "province": t.get("province") or ""}
                                   for t in draft.taxes],
                         "sections": _render_live(_shown_live(request, analysis.live, draft_rev=_draft_rev(
                             _drafts(request).get(invoice_id))), invoice_id)})  # fmt: skip


def _screen(form: Any) -> str:
    """The review screen a form comes from (its ``screen``, D-184)."""
    return str(form.get("screen") or "")


def _keep_checked(request: Request, store: Store, invoice_id: int, draft: view.Draft, analysis: view.Analysis,
                  screen: str = "") -> bool:  # fmt: skip
    """What a check of the screen keeps: the confidence it worked out (the queue shows the one the screen shows, edits
    and today's checks included), and the edits as a draft, or none when there are none. Whether they are kept.
    ``screen``: the screen's own (``_keep_draft``)."""
    if analysis.report is not None:
        store.refresh_confidence(invoice_id, analysis.report.adjusted_confidence, analysis.report.requires_review)
    kept = _keeps(draft, analysis)
    _keep_draft(request, invoice_id, draft, kept, _draft_blocker(analysis) if kept else None, screen)
    return kept


def _draft_blocker(analysis: view.Analysis) -> str:
    """What keeps the screen's Approve closed, as the queue says it ("" when nothing does): the approve bar's first
    blocker, else the errors to approve anyway (B-996)."""
    if analysis.coding is None:
        return "Fix " + (analysis.problems[0] if analysis.problems else "a field")
    if analysis.allow:
        return ""
    bar = analysis.live.get("bar") or {}
    first = (bar.get("first") or {}).get("text") or ""
    errors = int(bar.get("errors") or 0)
    return first or (f"{ui.plural(errors, 'error')} to fix" if errors else "Not ready to approve")


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
    learned = learn_from_approval(store, invoice_id, analysis.output, actor=me, taught=view.taught_boxes(draft))
    explained = 0
    for item in asked:  # the reasons AP gave on the "why" screen (none when it was skipped), after this correction
        answer = str(form.get(f"why::{item['key']}") or "").strip()  # was counted: the count starts again from 0
        if answer and store.explain_lesson(item["key"], answer, actor=me, invoice_id=invoice_id):
            explained += 1
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


@router.post("/review/{invoice_id:int}/undo-approve")
def undo_approve(request: Request, invoice_id: int) -> JSONResponse:
    """The approval banner's Undo: an invoice just approved (not exported) back in the review queue, its coding kept
    and what its approval taught withdrawn, as Reopen for correction does."""
    store = get_store(request)
    inv = _invoice(store, invoice_id)
    if inv.get("export_batch"):
        return refuse(f"It can't be undone now: it was exported in batch {inv['export_batch']}. To correct it, undo "
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


# --- Park, reject, notes --------------------------------------------------------------------------------------------


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
    return await run_in_threadpool(_send_to_manager, request, invoice_id, form)


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
    _keep_draft(request, invoice_id, draft, True, screen=_screen(form))  # the edits on screen stay for its return
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
        after = next_url(store, invoice_id, _nav_query(form), form.get("next_id"), request)
        said = f"Sent to {name}: it waits in their Manager confirmations and comes back to the queue when they answer."
        return done(request, after, flash_message(said, "ok", icon="forward_to_inbox"))
    return done(request, f"/manager/{rid}", flash_message(
        f"Parked, waiting for {name}. Download the request email below and send it; record the answer here when it "
        "comes.", "ok", icon="forward_to_inbox"))  # fmt: skip


@router.post("/review/{invoice_id:int}/reject")
def reject(request: Request, invoice_id: int, reason: str = Form(""), show: str = Form(""), q: str = Form(""),
           sort: str = Form(""), limit: str = Form(""), next_id: str = Form("")) -> JSONResponse:  # fmt: skip
    """Not ours, or not to be paid. It can be reopened later."""
    store = get_store(request)
    _invoice(store, invoice_id)
    after = next_url(store, invoice_id, _nav_query({"show": show, "q": q, "sort": sort, "limit": limit}), next_id,
                     request)  # fmt: skip
    try:
        store.reject_invoice(invoice_id, reviewer(), reason.strip())
    except ValueError:
        return changed_meanwhile(request, invoice_id)
    forget_everyones_draft(request, invoice_id)
    return done(request, after, flash_message(f"Invoice #{invoice_id} rejected.", "ok", icon="block"))


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


# --- Teaching a field on the page -------------------------------------------------------------------------------------


@router.post("/review/{invoice_id:int}/teach")
def teach(request: Request, invoice_id: int, field: str = Form(""), text: str = Form("")) -> JSONResponse:
    """The words the reviewer clicked on the page, as the value of a field of the form. On approval, the supplier's
    template learns where that field is printed (the boxes are sent with the approval)."""
    _invoice(get_store(request), invoice_id)
    if field in view.LINE_TEACHABLE or field == view.NEW_LINE:
        found = view.line_value(field, text)
        if found in (None, ""):
            what = "a line (it needs an amount)" if field == view.NEW_LINE else f"a line's {view.LINE_TEACHABLE[field]}"
            return refuse(f"Those words don't read as {what}: try again.")
        if field == view.NEW_LINE:
            return message(f"New line: {found['description'] or '(no description)'}, {found['amount']:.2f}. Code it "
                           "to a GL account.", "ok", field=field, value=found)  # fmt: skip
        shown = f"{found:.2f}" if field in ("line_amount", "line_unit_price") else str(found)
        return message(f"Line {view.LINE_TEACHABLE[field]} set to {shown}.", "ok", field=field, value=shown)
    if field not in view.TEACHABLE:
        return refuse("That field can't be taught.")
    value = view.form_value(field, text)
    if value in (None, ""):
        return refuse("Those words don't read as a value for that field: try again.")
    shown = f"{value:.2f}" if isinstance(value, float) else str(value)
    return message(f"{LABELS.get(field, field)} set to {shown}. Approving teaches it for this supplier.", "ok",
                   field=field, value=shown)  # fmt: skip


# --- Changing the line grid -----------------------------------------------------------------------------------------
# Each action's form is read on the event loop (a long invoice posts thousands of fields, B-455); the rest runs on a
# threadpool thread (D-158), as the screen's Check does.


def _posted_in_review(request: Request, invoice_id: int,
                      form: Any) -> tuple[Store, dict[str, Any], ReferenceData, view.Draft]:  # fmt: skip
    """A grid action's invoice (still in the review queue), the GL accounts and the draft the form posted."""
    store = get_store(request)
    inv = _in_review(store, invoice_id)
    if stale := _out_of_date(request, store, invoice_id, inv, form):
        raise _OutOfDate(stale)
    reference = _reference(store)
    return store, inv, reference, _from_form(request, inv, form)


def _coded(request: Request, invoice_id: int,
           form: Any) -> tuple[Store, dict[str, Any], ReferenceData, view.Draft, InvoiceCoding]:  # fmt: skip
    """As ``_posted_in_review``, with the coding on screen (refused while a field keeps it from being built)."""
    store, inv, reference, draft = _posted_in_review(request, invoice_id, form)
    coding = _fix_first(view.analyse(store, get_settings(request), reference, inv, draft))
    return store, inv, reference, draft, coding


def _new_lines(request: Request, invoice_id: int, reference: ReferenceData, draft: view.Draft,
               lines: list[dict[str, Any]], text: str, icon: str, form: Any) -> JSONResponse:  # fmt: skip
    """The grid with its new rows, kept as the draft; the page draws them and checks again (at the draft's revision
    now, ``draft_rev``)."""
    draft.lines = lines
    _keep_draft(request, invoice_id, draft, True, screen=_screen(form))
    ai = (get_store(request).get_invoice(invoice_id) or {}).get("ai_output") or {}
    return message(text, "ok", lines=_render_lines(reference, draft, ai), icon=icon,
                   draft_rev=_draft_rev(_drafts(request).get(invoice_id)))  # fmt: skip


async def _line_action(action: Any, request: Request, invoice_id: int) -> JSONResponse:
    """A line action's form read here, the action on a threadpool thread; refused as a check is when its screen is out
    of date (D-184)."""
    form = await posted_form(request)
    try:
        return await run_in_threadpool(action, request, invoice_id, form)
    except _OutOfDate as stale:
        return refuse(stale.stale["text"], 409, stale=stale.stale)


@router.post("/review/{invoice_id:int}/split")
async def split(request: Request, invoice_id: int) -> JSONResponse:
    """Split one line across several GL accounts / cost centers (shared costs), by percentage or, with
    ``split_by=amount``, by dollar amount (D-126)."""
    return await _line_action(_split, request, invoice_id)


@one_connection
def _split(request: Request, invoice_id: int, form: Any) -> JSONResponse:
    _, _, reference, draft = _posted_in_review(request, invoice_id, form)  # (approved meanwhile: B-1402)
    number = view._line_number(form.get("split_line"))
    if number is None:
        return refuse("Pick the line to split.")
    by = "amount" if str(form.get("split_by") or "") == "amount" else "percent"
    parts = []
    for gl, cc, share in zip(form.getlist("part_gl"), form.getlist("part_cc") or [""] * len(form.getlist("part_gl")),
                             form.getlist("part_amount" if by == "amount" else "part_pct"), strict=False):  # fmt: skip
        try:
            value = float(str(share).strip() or "nan")
        except ValueError:
            value = float("nan")
        parts.append((str(gl or ""), str(cc or ""), value))
    try:
        lines = view.split(draft, number, parts, reference.cost_centers is not None, by=by)
    except ValueError as exc:
        return refuse(f"Not split: {exc}.")
    shown = sum(1 for p in parts if (p[2] != 0 if by == "amount" else p[2] > 0))
    return _new_lines(
        request, invoice_id, reference, draft, lines, f"Line {number} split in {shown}.", "call_split", form
    )


@router.post("/review/{invoice_id:int}/apply-rules")
async def apply_rules(request: Request, invoice_id: int) -> JSONResponse:
    """Code the lines as the fixed coding rules say (e.g. a rule added after the invoice was processed)."""
    return await _line_action(_apply_rules, request, invoice_id)


@one_connection
def _apply_rules(request: Request, invoice_id: int, form: Any) -> JSONResponse:
    store, inv, reference, draft, coding = _coded(request, invoice_id, form)
    lines, n = view.apply_rules(store, coding, draft, invoice_id, inv["ai_output"] or {})
    return _new_lines(request, invoice_id, reference, draft, lines,
                      f"Coding rules applied to {ui.plural(n, 'line')}.", "rule_settings", form)  # fmt: skip


@router.post("/review/{invoice_id:int}/po-coding")
async def use_po_coding(request: Request, invoice_id: int) -> JSONResponse:
    """Set the GL account and cost center of the lines that differ from the purchase order to the PO's."""
    return await _line_action(_use_po_coding, request, invoice_id)


@one_connection
def _use_po_coding(request: Request, invoice_id: int, form: Any) -> JSONResponse:
    store, _, reference, draft, coding = _coded(request, invoice_id, form)
    lines, n = view.po_coding(store, coding, draft, invoice_id)
    return _new_lines(request, invoice_id, reference, draft, lines,
                      f"Applied the PO's coding to {ui.plural(n, 'line')}.", "auto_fix_high", form)  # fmt: skip


@router.post("/review/{invoice_id:int}/suggestion")
async def use_suggestion(request: Request, invoice_id: int) -> JSONResponse:
    """Code a line with a suggested GL account (one click, from past decisions and the account list)."""
    return await _line_action(_use_suggestion, request, invoice_id)


@one_connection
def _use_suggestion(request: Request, invoice_id: int, form: Any) -> JSONResponse:
    _, _, reference, draft, coding = _coded(request, invoice_id, form)
    gl = str(form.get("gl") or "")
    if reference.chart_of_accounts.get(gl) is None:
        return refuse("That GL account is not in your chart of accounts.")
    try:
        position = int(str(form.get("position") or ""))
        lines = view.use_suggestion(reference, coding, draft, position, gl, str(form.get("cc") or ""))
    except ValueError as exc:
        return refuse(f"Not changed: {exc}.")
    number = coding.line_items[position].line_number
    return _new_lines(
        request, invoice_id, reference, draft, lines, f"Line {number} coded to GL {gl}.", "add_task", form
    )


@router.post("/review/{invoice_id:int}/fill-lines")
async def fill_lines(request: Request, invoice_id: int) -> JSONResponse:
    """Apply to all lines: one GL account and / or cost center for every line that has none yet (lines already
    coded keep theirs)."""
    return await _line_action(_fill_lines, request, invoice_id)


@one_connection
def _fill_lines(request: Request, invoice_id: int, form: Any) -> JSONResponse:
    _, _, reference, draft = _posted_in_review(request, invoice_id, form)
    gl, cc = str(form.get("fill_gl") or "").strip(), str(form.get("fill_cc") or "").strip()
    if not gl and not cc:
        return refuse("Pick a GL account or a cost center to apply to the lines without one.")
    if gl and (reference.chart_of_accounts.get(gl) is None or gl in reference.tax.tax_gl_codes()):
        return refuse(f"GL {gl} is not in your GL accounts (or it is a sales-tax account).")
    if cc and (reference.cost_centers is None or reference.cost_centers.get(cc) is None):
        return refuse(f"Cost center {cc} is not in your cost center list.")
    lines, n_gl, n_cc = view.fill_lines(draft, gl, cc)
    done_text = [f"GL {gl} set on {ui.plural(n_gl, 'line')}" if gl else "",
                 f"Cost center {cc} set on {ui.plural(n_cc, 'line')}" if cc else ""]  # fmt: skip
    text = "; ".join(t for t in done_text if t) + ". Lines already coded kept theirs."
    return _new_lines(request, invoice_id, reference, draft, lines, text, "playlist_add_check", form)


# --- OvisOCR2 ---------------------------------------------------------------------------------------------------------


@router.post("/review/{invoice_id:int}/read-pages")
def read_pages(request: Request, invoice_id: int) -> JSONResponse:
    """Put the invoice in OvisOCR2's queue (it reads it in the background)."""
    store = get_store(request)
    _invoice(store, invoice_id)
    store.queue_page_read(invoice_id, "not read yet", requested_by=reviewer())
    return _back(request, invoice_id, "Added to OvisOCR2's queue: it reads it in the background.", icon="visibility")


@router.post("/review/{invoice_id:int}/review-again")
def review_again(request: Request, invoice_id: int) -> JSONResponse:
    """Put the invoice in line for the AI reviewer again (the chat model was changed, or it wasn't answering)."""
    from ap_coder.ai import verifier

    store = get_store(request)
    _in_review(store, invoice_id)
    if not verifier.load_settings(store).on:
        return refuse(f"The {verifier.NAME} is off: turn it on in Settings → Reading.")
    store.queue_verification(invoice_id, f"asked by {reviewer()}")
    return _back(request, invoice_id, f"In line for the {verifier.NAME}: it checks the invoice when the chat model "
                 "is free.", icon="playlist_add")  # fmt: skip


@router.post("/review/{invoice_id:int}/start-over")
def start_over(request: Request, invoice_id: int) -> JSONResponse:
    """Drop the edits not approved yet: the fields show the invoice as read (OvisOCR2's reading, when it is in)."""
    _invoice(get_store(request), invoice_id)
    forget_draft(request, invoice_id)
    return _back(request, invoice_id, "The fields show the reading again.", icon="restart_alt")


# --- The other states -------------------------------------------------------------------------------------------------


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
