"""The invoice review screen, ``/review/<id>``: opened from the review queue (and from the other lists).

An invoice waiting for review opens as the workspace: the page with a box over every field read from it (click a
field to see it on the page, or teach a field by clicking its words), the checks, the invoice details, the line
items with their GL coding, the sales tax, the GL posting preview and the approve bar (Approve & teach, Park,
Reject, Delete). Any other invoice opens read-only, with what can be done in its state: the second approval or
sending it back, bringing a parked one back, reopening an approved (for correction) or a rejected one.

Every edit is checked as the reviewer types (``POST /review/<id>/check`` answers the sections again) and kept as a
draft for this dashboard's lifetime, so moving to another invoice and back keeps it.
What is worked out lives in ``ap_coder/web/invoice_view.py``; the page script is ``static/js/invoice.js`` with the
page viewer ``static/js/invoice_viewer.js``.

Routes (all under ``/review/<id>``): GET the screen, ``page/<n>.png`` (a page's picture), ``words`` (the words on
the pages, to teach a field), ``reader`` (OvisOCR2's progress on it), ``approved.pdf``; POST ``check``,
``approve``, ``park``, ``reject``, ``note``, ``teach``, ``split``, ``apply-rules``, ``po-coding``,
``suggestion``, ``fill-lines`` (Apply to all lines), ``vendor`` (add the printed vendor), ``read-pages``,
``review-again`` (the AI reviewer), ``start-over``, ``resume`` (a parked invoice), ``restore`` (a rejected one),
``correct`` (an approved one).
Delete, final approval and send back are the queue's own POSTs (``pages/review.py``).
"""

from __future__ import annotations

import datetime as dt
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from markupsafe import Markup

from ap_coder.capture.confidence import LABELS
from ap_coder.capture.workflow import learn_from_approval, on_reopen, taught_message
from ap_coder.coding.tax import OUTSIDE_CANADA, PROVINCE_NAMES
from ap_coder.core import ui
from ap_coder.core.memory import ACCEPTED, CODED, CORRECTED, vendor_key
from ap_coder.core.reference_data import ReferenceData
from ap_coder.core.store import APPROVED, FAILED, PARKED, PENDING, REJECTED, REVIEW, Store
from ap_coder.erp import stamp
from ap_coder.web import document, identity
from ap_coder.web import invoice_view as view
from ap_coder.web.core import (
    done,
    flash_message,
    get_settings,
    get_store,
    login,
    message,
    posted_form,
    reading_status_or_none,
    reference_or_none,
    refuse,
    render,
    reviewer,
    templates,
)
from ap_coder.web.files import download
from ap_coder.web.pages.review import attention, filter_queue, sort_queue

router = APIRouter()

VIEWER_HEIGHT = 760  # px; the page scrolls inside, never taller than the window
PAGE_READER_POLL_SECONDS = 20  # while OvisOCR2 has the open invoice in its queue
NAV_PARAMS = ("show", "q", "sort")  # the queue's view, carried along so Previous / Next follow it


# --- Drafts: edits not approved yet --------------------------------------------------------------------------------
# Kept in memory for the dashboard's lifetime, per invoice and per person: this computer's in
# ``app.state.invoice_drafts``, each colleague's on the office network (web/identity.py) in
# ``app.state.person_drafts[(<their name, case aside>, <their browser's id>)]``, so one person's edits never open on
# another's screen, even one who gives the same name on another computer, and stay theirs when their computer gets a
# new address on the office network; a new name on /who takes them along.

_drafts_lock = threading.Lock()


def _all_drafts(request: Request) -> list[dict[int, dict[str, Any]]]:
    state = request.app.state
    with _drafts_lock:
        if not hasattr(state, "invoice_drafts"):
            state.invoice_drafts = {}
        if not hasattr(state, "person_drafts"):
            state.person_drafts = {}
        return [state.invoice_drafts, *state.person_drafts.values()]


def _drafts(request: Request) -> dict[int, dict[str, Any]]:
    """The edits of the person asking: this computer's, or a colleague's on the office network."""
    _all_drafts(request)
    found = identity.person()
    if found is None:
        return request.app.state.invoice_drafts
    with _drafts_lock:
        return request.app.state.person_drafts.setdefault(_draft_key(found), {})


def _draft_key(found: identity.Person) -> tuple[str, str]:
    return found.name.casefold(), found.browser or found.address  # by address: a cookie made without a browser id


def rename_drafts(request: Request, found: identity.Person, now: identity.Person) -> None:
    """A colleague gave a new name on /who: their edits go with them (else they would be no one's, and bulk approval
    would leave those invoices alone until a restart)."""
    _all_drafts(request)
    everyone = request.app.state.person_drafts
    with _drafts_lock:
        kept = everyone.pop(_draft_key(found), None) if _draft_key(found) != _draft_key(now) else None
        if kept:
            everyone.setdefault(_draft_key(now), {}).update(kept)


def unsaved_edits(request: Request) -> set[int]:
    """The invoices with edits on someone's review screen not approved yet (bulk approval would approve them without
    the edits, so it leaves them alone)."""
    return {invoice_id for drafts in _all_drafts(request) for invoice_id in drafts}


def forget_draft(request: Request, invoice_id: int) -> None:
    """Drop the edits of the person asking (Start over)."""
    _drafts(request).pop(invoice_id, None)


def forget_everyones_draft(request: Request, invoice_id: int) -> None:
    """Drop everyone's edits of an invoice that was approved, rejected, deleted or reopened: they no longer apply."""
    for drafts in _all_drafts(request):
        drafts.pop(invoice_id, None)


def forget_all_drafts(request: Request) -> None:
    """After a restore the invoices are the backup's (and their numbers are given again): no edit applies to them."""
    for drafts in _all_drafts(request):
        drafts.clear()


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
        return view.Draft.stored(_start(inv), _reader_at(inv)), False
    draft = view.Draft(header=dict(saved["header"]), lines=list(saved["lines"]), taxes=list(saved["taxes"]),
                       taught=dict(saved.get("taught") or {}), reader_at=saved.get("reader_at", ""))  # fmt: skip
    return draft, draft.reader_at != _reader_at(inv)


def _keep_draft(request: Request, invoice_id: int, draft: view.Draft, keep: bool) -> None:
    if keep:
        _drafts(request)[invoice_id] = draft.to_dict()
    else:
        forget_draft(request, invoice_id)


# --- Helpers --------------------------------------------------------------------------------------------------------


def _invoice(store: Store, invoice_id: int) -> dict[str, Any]:
    inv = store.get_invoice(invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="That invoice is not in AP Coder (it may have been deleted).")
    return inv


def _nav_query(params: Any) -> dict[str, str]:
    return {k: str(params.get(k)) for k in NAV_PARAMS if params.get(k)}


def _with_query(path: str, query: dict[str, str]) -> str:
    return path + (f"?{urlencode(query)}" if query else "")


def navigation(store: Store, invoice_id: int, query: dict[str, str]) -> dict[str, Any]:
    """The invoices Previous / Next step through: the queue as shown (filter, search, sort in ``query``), or the
    whole queue if this invoice isn't part of that view."""
    pending = store.list_invoices(REVIEW)
    show = query.get("show", "all")
    reasons = attention(store, reference_or_none(store), pending) if show in ("attention", "ready") else {}
    shown = {"attention": [i for i in pending if i["id"] in reasons],
             "ready": [i for i in pending if i["id"] not in reasons]}.get(show, pending)  # fmt: skip
    shown = sort_queue(filter_queue(shown, query.get("q", "")), query.get("sort", "priority"))
    ids = [i["id"] for i in shown]
    if invoice_id not in ids:
        ids = [i["id"] for i in pending]
    if invoice_id not in ids:
        return {"ids": ids, "position": None, "count": len(ids), "prev": "", "next": "", "next_id": None}
    position = ids.index(invoice_id)
    url = lambda i: _with_query(f"/review/{i}", query)  # noqa: E731
    return {
        "ids": ids,
        "position": position,
        "count": len(ids),
        "done_pct": round((position + 1) / len(ids) * 100),
        # Always enabled (wrapping around), so Alt+Left/Right never fall through to the browser's Back/Forward.
        "prev": url(ids[(position - 1) % len(ids)]),
        "next": url(ids[(position + 1) % len(ids)]),
        # The one after it as the screen showed it (none for the last): where an action on it goes next, even if
        # editing moved it in the queue's order meanwhile (B-426).
        "next_id": ids[position + 1] if position + 1 < len(ids) else None,
    }


def next_url(store: Store, invoice_id: int, query: dict[str, str], seen_next: Any = None) -> str:
    """After an action on this invoice: the next one in the queue as shown (or the queue when none is left).
    ``seen_next``: the invoice that followed it when its screen was drawn (the form's ``next_id``), opened when it is
    still waiting: the edits refresh the invoice's confidence, which moves it in the queue's order while it is open,
    and the one after its new place would skip those in between (B-426)."""
    nav = navigation(store, invoice_id, query)
    ids, position = nav["ids"], nav["position"]
    remaining = [i for i in ids if i != invoice_id]
    if not remaining:
        return _with_query("/review", query)
    try:
        seen = int(seen_next or 0)
    except (TypeError, ValueError):
        seen = 0
    if seen in remaining:
        return _with_query(f"/review/{seen}", query)
    at = min(position if position is not None else 0, len(remaining) - 1)
    return _with_query(f"/review/{remaining[at]}", query)


_NOW = {APPROVED: "approved", PENDING: "approved, waiting for a second approval", REJECTED: "rejected",
        PARKED: "parked", REVIEW: "in the review queue", FAILED: "a failed read"}  # fmt: skip


def changed_meanwhile(request: Request, invoice_id: int, exc: Exception) -> JSONResponse:
    """The invoice was acted on after this screen was drawn (in another tab, or by someone else): say so plainly and
    show where it stands now (the queue, when it was deleted)."""
    inv = get_store(request).get_invoice(invoice_id)
    if inv is None:
        return done(request, "/review", flash_message("Not done: this invoice was deleted meanwhile.", "warn",
                                                      icon="sync_problem"))  # fmt: skip
    now = _NOW.get(inv["status"], str(inv["status"]))
    by = f" by {inv['reviewer']}" if inv["status"] in (APPROVED, PENDING) and inv.get("reviewer") else ""
    return done(request, f"/review/{invoice_id}",
                flash_message(f"Not done: this invoice changed meanwhile, in another tab or by someone else. It is "
                              f"{now}{by}.", "warn", icon="sync_problem"))  # fmt: skip


NOTE_ACTIONS = ("note", "parked", "sent_back")  # what the notes card lists


def history(events: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Audit events as timeline items (``ui.timeline``), newest first."""
    from ap_coder.core.audit import ACTIONS, describe

    items = []
    for e in events:
        icon_name, tone, label = ACTIONS.get(e["action"], ("circle", "gray", e["action"]))
        when = f"{ui.time_ago(e['created_at'])} · {e['created_at'].replace('T', ' ')[:16]}"
        items.append({"icon": icon_name, "tone": tone, "title": label, "text": describe(e),
                      "who": e.get("actor") or "", "when": when})  # fmt: skip
    return items


def _lesson_hints(store: Store, inv: dict[str, Any]) -> list[str]:
    """What AP taught for this vendor (lessons.py), shown as "Learned" hints beside the document."""
    from ap_coder.ai import lessons

    output = inv.get("final_output") or inv.get("ai_output") or {}
    return lessons.prompt_lines(store, output.get("vendor_name") or "", lessons.kind_of(output), limit=5)


def viewer_data(inv: dict[str, Any], capture: Any, sizes: list[tuple[float, float]]) -> dict[str, Any]:
    """What the page viewer draws: the pages (pictures by address), the fields with their boxes, the line items."""
    v = document.version(inv["source_path"])
    pages = [{"src": f"/review/{inv['id']}/page/{n}.png?v={v}", "width": w, "height": h}
             for n, (w, h) in enumerate(sizes, start=1)]  # fmt: skip
    fields = document.shape_fields(capture.fields.values(), LABELS) if capture is not None else []
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


def _document(store: Store, settings: Any, inv: dict[str, Any], status: Any, editable: bool) -> dict[str, Any]:
    """The document card: the pages (or the extracted text), what OvisOCR2 made of it, how the lines were read."""
    from ap_coder.capture.line_items import sources_line

    path = Path(inv.get("source_path") or "")
    capture = view.capture_of(store, inv["id"])
    sizes: list[tuple[float, float]] = []
    problem = ""
    if not path.exists():  # (a file an update moved was found again by get_invoice)
        problem = f"This invoice's file is no longer at {path}."
    elif path.suffix.lower() in document.VIEWABLE:
        try:
            sizes = document.page_sizes(path)
        except Exception:  # noqa: BLE001 - a damaged file: the extracted text still shows what was read
            problem = "This file could not be shown (it may be damaged)."
    original = (inv.get("meta") or {}).get("original") or {}
    return {
        "file_name": inv.get("file_name") or path.name,
        "kind": view.document_kind(capture.layout_source) if capture is not None else None,
        "viewer": viewer_data(inv, capture, sizes) if sizes else None,
        "teachable": editable and bool(sizes) and capture is not None,
        "teach_fields": [(f, LABELS.get(f, f)) for f in view.TEACHABLE],
        "problem": problem,
        "text": Markup(ui.document_text(inv.get("extraction_md") or "")),  # noqa: S704 - document_text escapes
        "reader": (folded_reader if editable and lm_studio_off(status) else _same)(
            view.reader_state(store, settings, inv, status)),
        "reviewer": (folded_reviewer if editable and lm_studio_off(status) else _same)(view.reviewer_state(store, inv)),
        "lessons": _lesson_hints(store, inv),
        "lines_note": sources_line(capture.line_items, capture.checks) if capture is not None else "",
        "original": {"file": str(original["file"]),
                     "folder": Path(str(original.get("kept") or "")).parent.name or "originals"}
        if original.get("file") else None,
    }  # fmt: skip


def _same(state: dict[str, Any]) -> dict[str, Any]:
    return state


def _sent_back(store: Store, invoice_id: int) -> dict[str, str] | None:
    back = next((e for e in store.events(invoice_id)
                 if e["action"] in ("sent_back", "reopened", "approved", "rejected")), None)  # fmt: skip
    if not back or back["action"] not in ("sent_back", "reopened"):
        return None
    return {"verb": "Sent back" if back["action"] == "sent_back" else "Reopened", "actor": back["actor"] or "?",
            "reason": (back["detail"] or {}).get("reason") or "no reason given"}  # fmt: skip


def _render_live(live: dict[str, Any], invoice_id: int) -> dict[str, str]:
    """Every section that follows the edits, drawn again (``_invoice_live.html``)."""
    mod = templates.env.get_template("_invoice_live.html").module
    names = ("summary", "checks", "amounts", "reasons", "suggest", "po", "taxcheck", "posting", "bar")
    if not live["ok"]:  # a field to fix: the coding can't be built, so the rest keeps its last state
        names = ("checks", "amounts", "taxcheck", "posting", "bar")
    return {name: str(getattr(mod, name)(live, invoice_id)) for name in names}


def _render_lines(reference: ReferenceData, draft: view.Draft, ai: dict[str, Any] | None = None) -> str:
    mod = templates.env.get_template("_invoice_lines.html").module
    return str(mod.line_rows(view.grid(reference, draft, ai)))


async def _posted(request: Request, inv: dict[str, Any]) -> tuple[Any, view.Draft]:
    form = await posted_form(request)  # a long invoice posts thousands of fields (B-455)
    base, _ = current_draft(request, inv)
    return form, view.Draft.from_form(form, base)


def _reference(store: Store) -> ReferenceData:
    reference = reference_or_none(store)
    if reference is None:
        raise HTTPException(status_code=409, detail="Import your GL accounts first (GL accounts & tax).")
    return reference


# --- The screen -----------------------------------------------------------------------------------------------------


@router.get("/review/{invoice_id:int}", response_class=HTMLResponse)
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
        "nav": navigation(store, invoice_id, query),
        "history": history(events),
        "notes": history([e for e in events if e["action"] in NOTE_ACTIONS]),
        "title": inv.get("vendor_name") or inv.get("file_name") or "Invoice",
    }
    editable = inv["status"] == REVIEW and reference is not None
    context["doc"] = _document(store, settings, inv, status, editable)
    if not editable:
        return render(request, "invoice.html", "review", mode=_mode(inv, reference), **context,
                      **_closed(store, reference, inv))  # fmt: skip
    draft, kept = current_draft(request, inv)
    analysis = view.analyse(store, settings, reference, inv, draft, reviewer())
    sure = view.capture_confidence(view.capture_of(store, invoice_id))
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
    return render(
        request, "invoice.html", "review", mode="review", **context,
        draft=draft, kept=kept, live=analysis.live, sure=sure,
        grid=view.grid(reference, draft, inv.get("ai_output") or {}),
        labels=view.FIELD_LABELS, provinces=view.province_options(),
        due_placeholder=f"{computed.isoformat()} (from terms)" if computed else "YYYY-MM-DD",
        sent_back=_sent_back(store, invoice_id), poll_seconds=PAGE_READER_POLL_SECONDS,
        today=dt.date.today().isoformat(), taught_json=draft.taught, vendors=vendor_options(store),
        supply=view.supply_line(draft.header, draft.lines, reference.tax),
        usual_cc=view.usual_cost_center(store, reference, draft.header.get("vendor_name") or ""),
    )  # fmt: skip


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


@router.post("/review/{invoice_id:int}/vendor")
def add_vendor(
    request: Request, invoice_id: int, name: str = Form(""), gst: str = Form(""), qst: str = Form(""),
    pst: str = Form(""), province: str = Form(""),
) -> JSONResponse:  # fmt: skip
    """Add new vendor: a vendor not in the vendor list, added from the review screen. Its invoices need a person
    (VENDOR_ADDED_HERE) until it is set up in JD Edwards and the vendor list is imported again."""
    store = get_store(request)
    inv = _invoice(store, invoice_id)
    if inv["status"] != REVIEW:
        return refuse("This invoice is no longer in the review queue: reload the page.", 409)
    name = " ".join(name.split())
    if province and province not in PROVINCE_NAMES and province != OUTSIDE_CANADA:
        return refuse("Choose a province from the list.")
    try:
        store.add_vendor_here(name, gst=gst, qst=qst, pst=pst, province=province, actor=reviewer(),
                              invoice_id=invoice_id)  # fmt: skip
    except ValueError as exc:
        return refuse(str(exc))
    return message(f"Added {name} to the vendor list. Set it up in JD Edwards before it is paid: its invoices need a "
                   "person until then.", "ok", icon="person_add", vendor=name, key=vendor_key(name))  # fmt: skip


def _mode(inv: dict[str, Any], reference: ReferenceData | None) -> str:
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
        out["edits"] = ("no changes to the suggested coding" if not edits
                        else "changed: " + ", ".join(view.EDIT_LABELS.get(e, e) for e in edits))  # fmt: skip
        out["can_stamp"] = stamp.can_stamp(inv)
    if inv["status"] == PENDING:
        me, my_login = reviewer(), login()
        first = next((e for e in store.events(inv["id"]) if e["action"] == "approved"), None)
        first_login = ((first or {}).get("detail") or {}).get("login") or ""
        out["same"] = (me or "").strip().casefold() == (inv["reviewer"] or "").strip().casefold() or (
            bool(my_login) and my_login.casefold() == first_login.casefold()
        )
        out["limit"] = store.approval_limit()
    if inv["status"] == FAILED:
        error = (inv.get("error") or "No reason recorded.").strip()
        first, _, details = error.partition("\n")
        out["error_first"] = first[:240] + ("…" if len(first) > 240 else "")
        out["error_details"] = error if details.strip() or len(first) > 240 else ""
    return out


@router.get("/review/{invoice_id:int}/page/{number:int}.png")
def page_picture(request: Request, invoice_id: int, number: int, scale: str = "1") -> Response:
    """The page as a picture; ``?scale=2`` or ``3`` draws it at 2 or 3 times the dpi for a page zoomed in (anything else
    is 1). The scale is in the address, so the browser keeps each one apart."""
    inv = _invoice(get_store(request), invoice_id)
    if not Path(inv.get("source_path") or "").is_file():
        raise HTTPException(status_code=404, detail=f"This invoice's file is no longer at {inv.get('source_path')}.")
    try:
        png = document.page_png(inv["source_path"], number, document.clean_scale(scale))
    except (IndexError, OSError) as exc:
        raise HTTPException(status_code=404, detail="That page is not in the invoice's file.") from exc
    except Exception as exc:  # noqa: BLE001 - a damaged file: said plainly
        raise HTTPException(status_code=404, detail="This page could not be drawn (the file may be damaged).") from exc
    # The address carries the file's version (?v=), so the picture can be kept by the browser.
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})


@router.get("/review/{invoice_id:int}/words")
def page_words(request: Request, invoice_id: int) -> JSONResponse:
    inv = _invoice(get_store(request), invoice_id)
    try:
        words = document.page_words(inv["source_path"])
    except Exception as exc:  # noqa: BLE001 - no OCR on this computer, a damaged file...
        return JSONResponse({"ok": False, "error": f"The words on the page can't be read here ({exc}).",
                             "words": []})  # fmt: skip
    return JSONResponse({"ok": True, "words": words})


@router.get("/review/{invoice_id:int}/reader")
def reader_progress(request: Request, invoice_id: int) -> JSONResponse:
    """OvisOCR2's progress on this invoice, for the page to follow while it waits or reads."""
    store, settings = get_store(request), get_settings(request)
    inv = _invoice(store, invoice_id)
    status = reading_status_or_none(settings, store)
    state = view.reader_state(store, settings, inv, status)
    if inv["status"] == REVIEW and lm_studio_off(status):
        state = folded_reader(state)  # as the screen drew it (B-444)
    html = templates.env.get_template("_invoice_live.html").module.reader(state, invoice_id)
    return JSONResponse({"state": state["state"], "poll": state["poll"], "html": str(html)},
                        headers={"Cache-Control": "no-store"})  # fmt: skip


@router.get("/review/{invoice_id:int}/approved.pdf")
def approved_pdf(request: Request, invoice_id: int) -> Response:
    store = get_store(request)
    inv = _invoice(store, invoice_id)
    reference = reference_or_none(store)
    names = {row[reference.chart_of_accounts.key_column]: row.get("description", "")
             for row in reference.chart_of_accounts.rows} if reference is not None else {}  # fmt: skip
    try:
        pdf = stamp.stamped_pdf(inv, names, store.tax_treatments())
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    # (files.download: a vendor named "Bœuf" is not Latin-1, the only characters a plain filename= header takes)
    return download(pdf, stamp.file_name(inv), "application/pdf")


# --- Checking the edits ---------------------------------------------------------------------------------------------


@router.post("/review/{invoice_id:int}/check")
async def check(request: Request, invoice_id: int) -> JSONResponse:
    """The screen's sections again for what is on it now; the edits are kept as a draft, and the queue shows the
    confidence this screen shows (edits and today's checks included)."""
    store, settings = get_store(request), get_settings(request)
    inv = _invoice(store, invoice_id)
    if inv["status"] != REVIEW:
        return refuse("This invoice is no longer in the review queue: reload the page.", 409)
    reference = _reference(store)
    _, draft = await _posted(request, inv)
    analysis = view.analyse(store, settings, reference, inv, draft, reviewer())
    if analysis.report is not None:
        store.refresh_confidence(invoice_id, analysis.report.adjusted_confidence, analysis.report.requires_review)
    _keep_draft(request, invoice_id, draft, analysis.coding is None or analysis.changed or bool(draft.taught))
    return JSONResponse({"ok": True, "valid": analysis.coding is not None, "allow": analysis.allow,
                         "problems": analysis.problems,
                         "sections": _render_live(analysis.live, invoice_id)})  # fmt: skip


@router.post("/review/{invoice_id:int}/approve")
async def approve(request: Request, invoice_id: int) -> JSONResponse:
    """Approve & teach: store the coding on screen, record what was confirmed and corrected, teach the supplier's
    template where its fields are (and the words the reviewer showed), then open the next invoice."""
    store, settings = get_store(request), get_settings(request)
    inv = _invoice(store, invoice_id)
    reference = _reference(store)
    form, draft = await _posted(request, inv)
    query = _nav_query(form)
    analysis = view.analyse(store, settings, reference, inv, draft, reviewer())
    if analysis.coding is None:
        return refuse("Fix this field first: " + "; ".join(analysis.problems))
    if not analysis.allow:
        bar = analysis.live["bar"]
        return refuse(bar["pick"] + "." if bar["pick"] else
                      f"Tick “Approve anyway” to approve despite {ui.plural(bar['errors'], 'error')}.")  # fmt: skip
    # The same correction again and again: before saving, AP is asked once why (lessons.py). Skipping is fine: the
    # question comes back next time.
    from ap_coder.ai import lessons

    asked = lessons.due_for_why(store, inv.get("ai_output") or {}, analysis.output)
    if asked and not form.get("why_done"):
        return JSONResponse({"ok": False, "ask_why": {"fields": asked, "threshold": lessons.why_after(store)}})
    after = next_url(store, invoice_id, query, form.get("next_id"))
    open_issues = [{"code": i.code, "severity": i.severity, "line": i.line_number}
                   for i in analysis.errors + analysis.warnings]  # fmt: skip
    me = reviewer()
    try:
        counts = store.approve_invoice(invoice_id, analysis.output, me, open_issues=open_issues, login=login())
    except (KeyError, ValueError) as exc:  # KeyError: deleted meanwhile, after it was read above
        return changed_meanwhile(request, invoice_id, exc)
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
        why = store.second_approval_reason(analysis.output) or "over the approval limit"
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
    messages.append(flash_message(
        ("" if waits else f"Approved {vendor}. ") + (f"{taught} {lines}" if taught else learned_from),
        "ok", icon="school"))  # fmt: skip
    if explained:
        messages.append(flash_message(f"Thanks: {ui.plural(explained, 'reason')} kept. The AI reads "
                                      f"{'it' if explained == 1 else 'them'} with this vendor's next invoices.", "ok",
                                      icon="psychology"))  # fmt: skip
    if not store.list_invoices(REVIEW):
        messages.append(flash_message("Your queue is clear: every invoice has been reviewed.", "ok", icon="task_alt"))
    return done(request, after, *messages)


# --- Park, reject, notes --------------------------------------------------------------------------------------------


@router.post("/review/{invoice_id:int}/park")
def park(request: Request, invoice_id: int, reason: str = Form(""), follow_up: str = Form(""),
         show: str = Form(""), q: str = Form(""), sort: str = Form(""),
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
    query = _nav_query({"show": show, "q": q, "sort": sort})
    after = next_url(store, invoice_id, query, next_id)
    try:
        store.park_invoice(invoice_id, reviewer(), reason.strip(), follow.isoformat() if follow else None)
    except ValueError as exc:
        return changed_meanwhile(request, invoice_id, exc)
    return done(request, after, flash_message(f"Invoice #{invoice_id} parked. It is in the Parked tab.", "ok",
                                              icon="pause_circle"))  # fmt: skip


@router.post("/review/{invoice_id:int}/reject")
def reject(request: Request, invoice_id: int, reason: str = Form(""), show: str = Form(""), q: str = Form(""),
           sort: str = Form(""), next_id: str = Form("")) -> JSONResponse:  # fmt: skip
    """Not ours, or not to be paid. It can be reopened later."""
    store = get_store(request)
    _invoice(store, invoice_id)
    after = next_url(store, invoice_id, _nav_query({"show": show, "q": q, "sort": sort}), next_id)
    try:
        store.reject_invoice(invoice_id, reviewer(), reason.strip())
    except ValueError as exc:
        return changed_meanwhile(request, invoice_id, exc)
    forget_everyones_draft(request, invoice_id)
    return done(request, after, flash_message(f"Invoice #{invoice_id} rejected.", "ok", icon="block"))


@router.post("/review/{invoice_id:int}/note")
def note(request: Request, invoice_id: int, text: str = Form(""), back: str = Form("")) -> JSONResponse:
    store = get_store(request)
    _invoice(store, invoice_id)
    if not text.strip():
        return refuse("Write the note first.")
    store.add_note(invoice_id, reviewer(), text)
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


async def _coded(request: Request, invoice_id: int) -> tuple[Store, dict[str, Any], ReferenceData, Any, view.Draft,
                                                            view.Analysis]:  # fmt: skip
    store = get_store(request)
    inv = _invoice(store, invoice_id)
    if inv["status"] != REVIEW:
        raise HTTPException(status_code=409, detail="This invoice is no longer in the review queue: reload the page.")
    reference = _reference(store)
    form, draft = await _posted(request, inv)
    analysis = view.analyse(store, get_settings(request), reference, inv, draft)
    return store, inv, reference, form, draft, analysis


def _new_lines(request: Request, invoice_id: int, reference: ReferenceData, draft: view.Draft,
               lines: list[dict[str, Any]], text: str, icon: str) -> JSONResponse:  # fmt: skip
    """The grid with its new rows, kept as the draft; the page draws them and checks again."""
    draft.lines = lines
    _keep_draft(request, invoice_id, draft, True)
    ai = (get_store(request).get_invoice(invoice_id) or {}).get("ai_output") or {}
    return message(text, "ok", lines=_render_lines(reference, draft, ai), icon=icon)


@router.post("/review/{invoice_id:int}/split")
async def split(request: Request, invoice_id: int) -> JSONResponse:
    """Split one line across several GL accounts / cost centers by percentage (shared costs)."""
    store = get_store(request)
    inv = _invoice(store, invoice_id)
    reference = _reference(store)
    form, draft = await _posted(request, inv)
    number = view._line_number(form.get("split_line"))
    if number is None:
        return refuse("Pick the line to split.")
    parts = []
    for gl, cc, pct in zip(form.getlist("part_gl"), form.getlist("part_cc") or [""] * len(form.getlist("part_gl")),
                           form.getlist("part_pct"), strict=False):  # fmt: skip
        try:
            value = float(str(pct).strip() or "nan")
        except ValueError:
            value = float("nan")
        parts.append((str(gl or ""), str(cc or ""), value))
    try:
        lines = view.split(draft, number, parts, reference.cost_centers is not None)
    except ValueError as exc:
        return refuse(f"Not split: {exc}.")
    shown = sum(1 for p in parts if p[2] > 0)
    return _new_lines(request, invoice_id, reference, draft, lines, f"Line {number} split in {shown}.", "call_split")


@router.post("/review/{invoice_id:int}/apply-rules")
async def apply_rules(request: Request, invoice_id: int) -> JSONResponse:
    """Code the lines as the fixed coding rules say (e.g. a rule added after the invoice was processed)."""
    store, inv, reference, _, draft, analysis = await _coded(request, invoice_id)
    if analysis.coding is None:
        return refuse("Fix this field first: " + "; ".join(analysis.problems))
    lines, n = view.apply_rules(store, analysis.coding, draft, invoice_id, inv["ai_output"] or {})
    return _new_lines(request, invoice_id, reference, draft, lines,
                      f"Coding rules applied to {ui.plural(n, 'line')}.", "rule_settings")  # fmt: skip


@router.post("/review/{invoice_id:int}/po-coding")
async def use_po_coding(request: Request, invoice_id: int) -> JSONResponse:
    """Set the GL account and cost center of the lines that differ from the purchase order to the PO's."""
    store, _, reference, _, draft, analysis = await _coded(request, invoice_id)
    if analysis.coding is None:
        return refuse("Fix this field first: " + "; ".join(analysis.problems))
    lines, n = view.po_coding(store, analysis.coding, draft, invoice_id)
    return _new_lines(request, invoice_id, reference, draft, lines,
                      f"Applied the PO's coding to {ui.plural(n, 'line')}.", "auto_fix_high")  # fmt: skip


@router.post("/review/{invoice_id:int}/suggestion")
async def use_suggestion(request: Request, invoice_id: int) -> JSONResponse:
    """Code a line with a suggested GL account (one click, from past decisions and the account list)."""
    _, _, reference, form, draft, analysis = await _coded(request, invoice_id)
    if analysis.coding is None:
        return refuse("Fix this field first: " + "; ".join(analysis.problems))
    gl = str(form.get("gl") or "")
    if reference.chart_of_accounts.get(gl) is None:
        return refuse("That GL account is not in your chart of accounts.")
    try:
        position = int(str(form.get("position") or ""))
        lines = view.use_suggestion(reference, analysis.coding, draft, position, gl, str(form.get("cc") or ""))
    except ValueError as exc:
        return refuse(f"Not changed: {exc}.")
    number = analysis.coding.line_items[position].line_number
    return _new_lines(request, invoice_id, reference, draft, lines, f"Line {number} coded to GL {gl}.", "add_task")


@router.post("/review/{invoice_id:int}/fill-lines")
async def fill_lines(request: Request, invoice_id: int) -> JSONResponse:
    """Apply to all lines: one GL account and / or cost center for every line that has none yet (lines already
    coded keep theirs)."""
    store = get_store(request)
    inv = _invoice(store, invoice_id)
    if inv["status"] != REVIEW:
        return refuse("This invoice is no longer in the review queue: reload the page.", 409)
    reference = _reference(store)
    form, draft = await _posted(request, inv)
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
    return _new_lines(request, invoice_id, reference, draft, lines, text, "playlist_add_check")


# --- OvisOCR2 ---------------------------------------------------------------------------------------------------------


@router.post("/review/{invoice_id:int}/read-pages")
def read_pages(request: Request, invoice_id: int) -> JSONResponse:
    """Put the invoice in OvisOCR2's queue (it reads it in the background)."""
    store = get_store(request)
    _invoice(store, invoice_id)
    store.queue_page_read(invoice_id, "not read yet", requested_by=reviewer())
    return done(request, f"/review/{invoice_id}", flash_message(
        "Added to OvisOCR2's queue: it reads it in the background.", "ok", icon="visibility"))  # fmt: skip


@router.post("/review/{invoice_id:int}/review-again")
def review_again(request: Request, invoice_id: int) -> JSONResponse:
    """Put the invoice in line for the AI reviewer again (the chat model was changed, or it wasn't answering)."""
    from ap_coder.ai import verifier

    store = get_store(request)
    inv = _invoice(store, invoice_id)
    if inv["status"] != REVIEW:
        return refuse("This invoice is no longer in the review queue: reload the page.", 409)
    if not verifier.load_settings(store).on:
        return refuse(f"The {verifier.NAME} is off: turn it on in Settings → Reading.")
    store.queue_verification(invoice_id, f"asked by {reviewer()}")
    return done(request, f"/review/{invoice_id}", flash_message(
        f"In line for the {verifier.NAME}: it checks the invoice when the chat model is free.", "ok",
        icon="playlist_add"))  # fmt: skip


@router.post("/review/{invoice_id:int}/start-over")
def start_over(request: Request, invoice_id: int) -> JSONResponse:
    """Drop the edits not approved yet: the fields show the invoice as read (OvisOCR2's reading, when it is in)."""
    _invoice(get_store(request), invoice_id)
    forget_draft(request, invoice_id)
    return done(request, f"/review/{invoice_id}", flash_message("The fields show the reading again.", "ok",
                                                                icon="restart_alt"))  # fmt: skip


# --- The other states -------------------------------------------------------------------------------------------------


@router.post("/review/{invoice_id:int}/resume")
def resume(request: Request, invoice_id: int) -> JSONResponse:
    """A parked invoice back in the review queue, opened to review it."""
    try:
        get_store(request).unpark_invoice(invoice_id, reviewer())
    except ValueError as exc:
        return changed_meanwhile(request, invoice_id, exc)
    return done(request, f"/review/{invoice_id}", flash_message(
        f"Invoice #{invoice_id} is back in the review queue.", "ok", icon="play_circle"))  # fmt: skip


@router.post("/review/{invoice_id:int}/restore")
def restore(request: Request, invoice_id: int) -> JSONResponse:
    """A rejected invoice back in the review queue (e.g. rejected by mistake)."""
    try:
        get_store(request).reopen(invoice_id, reviewer(), only_rejected=True)
    except ValueError as exc:
        return changed_meanwhile(request, invoice_id, exc)
    return done(request, f"/review/{invoice_id}", flash_message("Back in the review queue.", "ok", icon="undo"))


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
        store.reopen(invoice_id, me, reason.strip())
    except ValueError as exc:  # exported meanwhile, say: nothing reopened, no vendor suspended
        return changed_meanwhile(request, invoice_id, exc)
    on_reopen(store, invoice_id, me, reason.strip(), before=before)
    forget_everyones_draft(request, invoice_id)
    return done(request, f"/review/{invoice_id}", flash_message("Reopened: correct it and approve it again.", "ok",
                                                                icon="undo"))  # fmt: skip
