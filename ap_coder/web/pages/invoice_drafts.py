"""The review screen's edits not approved yet (drafts), who else has the invoice open, and a screen out of date.

No routes here: the screen's handlers (``pages/invoice.py`` and its ``invoice_<subject>.py`` siblings), the queue,
/who and Settings call these.

* Drafts, per invoice and per person, in the app's state and in the store (D-86): ``current_draft`` (what the screen
  starts from), ``_from_form`` (what it posted), ``_keep_draft``, ``my_drafts``, ``unsaved_edits``, and dropping
  them (``forget_draft``, ``forget_everyones_draft``, ``forget_all_drafts``); ``rename_drafts`` for a new name.
* Many people on one queue (D-184): who has an invoice open (``open_elsewhere``, ``_keep_open``), a draft's
  revision (``_draft_rev``), a screen behind the invoice or the draft (``_out_of_date``, ``_stale``,
  ``_OutOfDate``) and an action on an invoice acted on meanwhile (``changed_meanwhile``).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import threading
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from ap_coder.core import ui
from ap_coder.core.store import APPROVED, FAILED, PARKED, PENDING, REJECTED, REVIEW, Store
from ap_coder.web import identity, invoice_form
from ap_coder.web import invoice_view as view
from ap_coder.web.access import key_of, person_key
from ap_coder.web.core import can, done, flash_message, get_store, reference_or_none, refuse, reviewer, server_mode
from ap_coder.web.pages.invoice_router import _back, _nav_query, _with_query

# --- Drafts: edits not approved yet -------------------------------------------------------------------------------
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
    """Drop everyone's edits of an invoice that was approved, deleted or reopened after its approval: they no longer
    apply (a rejected one keeps them for a reopen, B-2361)."""
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


def current_draft(request: Request, inv: dict[str, Any]) -> tuple[invoice_form.Draft, bool]:
    """The screen's state: the draft kept for it, else the stored coding; and whether OvisOCR2's reading came in
    after the draft was started (its edits are kept: the screen offers to start over from the reading)."""
    saved = _drafts(request).get(inv["id"])
    if saved is None:
        return invoice_form.Draft.stored(_start(inv), _reader_at(inv), approved=bool(inv.get("final_output"))), False
    draft = invoice_form.Draft(header=dict(saved["header"]), lines=list(saved["lines"]), taxes=list(saved["taxes"]),
                       taught=dict(saved.get("taught") or {}), reader_at=saved.get("reader_at", ""),
                       absent=list(saved.get("absent") or []), from_vendor=dict(saved.get("from_vendor") or {}),
                       pay_from_for=str(saved.get("pay_from_for") or ""))  # fmt: skip
    return draft, draft.reader_at != _reader_at(inv)


def _from_form(request: Request, inv: dict[str, Any], form: Any) -> invoice_form.Draft:
    base, _ = current_draft(request, inv)
    reference = reference_or_none(get_store(request))
    return invoice_form.Draft.from_form(form, base, reference.pairs if reference is not None else None)


def _screen(form: Any) -> str:
    """The review screen a form comes from (its ``screen``, D-184)."""
    return str(form.get("screen") or "")


_NOT_THE_EDITS = ("saved_at", "check", "screen")  # what a kept draft carries besides the edits


def my_drafts(request: Request) -> dict[int, dict[str, Any]]:
    """The drafts of the person asking, by invoice: each its edits, ``saved_at`` (when last edited) and ``check``
    ({"why": what keeps it from being approved, "" when nothing does}), as the queue shows them (B-996)."""
    return _drafts(request)


def _when(iso: str) -> str:
    """When a draft was last edited, as the screens say it (``ui.day_and_time``: "today at 4:23 p.m.")."""
    try:
        at = dt.datetime.fromisoformat(iso)
    except ValueError:
        return ""
    return ui.day_and_time(at)


def _keep_draft(request: Request, invoice_id: int, draft: invoice_form.Draft, keep: bool, why: str | None = None,
                screen: str = "", form: Any = None) -> None:  # fmt: skip
    """Keep the edits (``keep``) or drop them. ``saved_at`` is when they were last edited: the same edits checked again
    (the invoice opened again) keep their time (B-997). ``why``: the draft's own blocker, for the queue (B-996).
    ``screen``: the review screen they come from (the form's ``screen``), kept as the one that wrote them (D-184).
    ``form``: the screen's form (its ``screen`` and ``draft_rev``): written only while that screen is still at the
    person's draft, read again in the write's own transaction; else refused as out of date (``_OutOfDate``: two tabs
    whose checks overlap, the second to write, B-1938)."""
    store, person = get_store(request), _person(person_key())
    drawn = None if form is None else form.get("draft_rev")
    screen = screen if form is None else _screen(form)
    expect = None if drawn is None else lambda now: _at_draft(store, invoice_id, now, str(drawn), screen)
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
        if not store.save_invoice_draft(invoice_id, person, kept, expect=expect):  # first: it may be left undone
            raise _OutOfDate(_stale(store, invoice_id, _nav_query(form), False, store.invoice_draft(invoice_id, person),
                                    mine=True))  # fmt: skip
        _drafts(request)[invoice_id] = kept
    elif invoice_id in _drafts(request):  # (the screen's own requests sent meanwhile are still at it: ``_at_draft``)
        if not store.save_invoice_draft(invoice_id, person, None, screen=screen, seconds=UNDONE_SECONDS, expect=expect):
            raise _OutOfDate(_stale(store, invoice_id, _nav_query(form), False, store.invoice_draft(invoice_id, person),
                                    mine=True))  # fmt: skip
        _drafts(request).pop(invoice_id, None)


def _keeps(draft: invoice_form.Draft, analysis: view.Analysis) -> bool:
    """Whether a check keeps the screen's edits as a draft: a field to fix, an edit of the coding, words taught."""
    return analysis.coding is None or analysis.changed or bool(draft.taught)


# --- Many people on one queue (D-184) -----------------------------------------------------------------------------
# Who else has an invoice open (server mode): the screen's poll renews it, written at most every OPEN_RENEW_SECONDS
# and gone OPEN_SECONDS after the last renewal; Previous / Next pass over those invoices. A screen carries two
# revisions: the invoice's (``Store.coding_revision``, the form's ``rev``) and the person's draft's (``draft_rev``, in
# the approve bar each check draws again). A screen behind either one is out of date: its poll or its next check
# says so once and it turns read-only; a check or a line action from it changes nothing, an action is "changed
# meanwhile". On a laptop (one person) nobody else is shown; the revisions hold between its tabs.

OPEN_SECONDS = 120  # someone's invoice open: forgotten this long after the screen's last renewal (a closed tab)
OPEN_RENEW_SECONDS = 45  # renewed at most this often (the screen asks every 30 to 60 s)
WATCH_SECONDS = 30  # how often the screen asks while nothing is being read
UNDONE_SECONDS = 120  # a draft a screen's own check dropped: the requests it sent meanwhile are its own this long
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
    if drawn is not None and not _at_draft(store, invoice_id, kept, str(drawn), _screen(form)):
        return _stale(store, invoice_id, query, False, kept, mine=True)
    return None


def _at_draft(store: Store, invoice_id: int, kept: dict[str, Any] | None, drawn: str, screen: str) -> bool:
    """Whether a screen at revisions ``drawn`` is at the person's draft ``kept``: one of them, the draft written by
    that screen itself (``screen``: a check of its own answered after it asked), or none since that screen's own check
    dropped it (its edits put back as stored, B-1937)."""
    if _draft_rev(kept) in drawn.split():
        return True
    if not screen:
        return False
    if kept:
        return kept.get("screen") == screen
    return store.draft_dropped_by(invoice_id, _person(person_key())) == screen


class _OutOfDate(Exception):
    """A line action from a screen out of date (``_out_of_date``): refused as its check is."""

    def __init__(self, stale: dict[str, str]) -> None:
        super().__init__(stale["text"])
        self.stale = stale


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
