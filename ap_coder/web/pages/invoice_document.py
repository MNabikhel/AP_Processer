"""The document side of the review screen: the invoice's pages, what read them, and teaching a field on the page.

* The document card (``_document``): the pages for the viewer (``viewer_data``), OvisOCR2's and the AI reviewer's
  lines (folded while LM Studio is off: ``lm_studio_off``), the files joined into the voucher, what was learned.
* GET ``page/<n>.png`` (a page's picture), ``words`` (the words on the pages), ``reader`` (the screen's poll:
  OvisOCR2's progress on it, who else has it open and whether the screen is out of date, D-184), ``approved.pdf``
  (the stamped copy: ``approved_pdf_download``).
* POST ``teach`` (the words clicked on the page, as a field's value), ``read-pages`` (OvisOCR2's queue),
  ``review-again`` (the AI reviewer).

The pictures and the words are ``web/document.py``'s; what the screen says of the reading,
``web/invoice_reading.py``'s.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import Form, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from markupsafe import Markup

from ap_coder.capture.confidence import LABELS
from ap_coder.core import ui
from ap_coder.core.store import FAILED, REVIEW, Store
from ap_coder.erp import stamp
from ap_coder.web import document, identity, invoice_form, invoice_reading
from ap_coder.web.access import person_key
from ap_coder.web.core import (
    can,
    get_settings,
    get_store,
    message,
    reading_status_or_none,
    reference_or_none,
    refuse,
    reviewer,
    server_mode,
    templates,
)
from ap_coder.web.files import download
from ap_coder.web.identity import shown_path
from ap_coder.web.pages.invoice_drafts import _at_draft, _keep_open, _person, _stale, open_elsewhere, working_here
from ap_coder.web.pages.invoice_router import _back, _in_review, _invoice, _nav_query, router

# --- The document card --------------------------------------------------------------------------------------------

VIEWER_HEIGHT = 760  # px; the page scrolls inside, never taller than the window


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
    ``capture``: the invoice's (``invoice_reading.capture_of``)."""
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
    reader, checker = (invoice_reading.reader_state(store, settings, inv, status),
                       invoice_reading.reviewer_state(store, inv))  # fmt: skip
    return {
        "file_name": inv.get("file_name") or path.name,
        "kind": invoice_reading.document_kind(capture.layout_source) if capture is not None else None,
        "viewer": viewer_data(inv, capture, sizes) if sizes else None,
        "teachable": editable and bool(sizes) and capture is not None,
        "teach_fields": [(f, LABELS.get(f, f)) for f in invoice_form.TEACHABLE],
        "problem": problem,
        "text": Markup(ui.document_text(inv.get("extraction_md") or "")),  # noqa: S704 - document_text escapes
        "reader": folded_reader(reader) if folded else reader,
        "reviewer": folded_reviewer(checker) if folded else checker,
        "lessons": _lesson_hints(store, inv),
        "lines_note": sources_line(capture.line_items, capture.checks) if capture is not None else "",
        "original": {"file": str(original["file"]),
                     "folder": Path(str(original.get("kept") or "")).parent.name or "originals"}
        if original.get("file") else None,
        "joined": joined_files(store, inv),
    }  # fmt: skip


def joined_files(store: Store, inv: dict[str, Any]) -> dict[str, Any] | None:
    """The files a person joined into this voucher (D-216), as the document card lists them: what they are ("Pages of
    one invoice"), each file with its pages ("page 3"), and whether they can still be made separate invoices again;
    None for an invoice made of one file."""
    from ap_coder.intake.joined import KINDS, pages_said

    files = store.joined_files(invoice_id=inv["id"])
    if not files:
        return None
    return {"kind": KINDS.get(files[0]["kind"], ""), "separable": inv["status"] in (REVIEW, FAILED),
            "files": [{"name": f["name"], "pages": pages_said(f)} for f in files]}  # fmt: skip


# --- The pages, the poll and the approved copy --------------------------------------------------------------------


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


PAGE_READER_POLL_SECONDS = 20  # while OvisOCR2 has the open invoice in its queue


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
    state = invoice_reading.reader_state(store, settings, inv, status)
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
        if not _at_draft(store, invoice_id, kept, draft, screen):
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


# --- Teaching a field on the page ---------------------------------------------------------------------------------


@router.post("/review/{invoice_id:int}/teach")
def teach(request: Request, invoice_id: int, field: str = Form(""), text: str = Form("")) -> JSONResponse:
    """The words the reviewer clicked on the page, as the value of a field of the form. On approval, the supplier's
    template learns where that field is printed (the boxes are sent with the approval)."""
    _invoice(get_store(request), invoice_id)
    if field in invoice_form.LINE_TEACHABLE or field == invoice_form.NEW_LINE:
        found = invoice_form.line_value(field, text)
        if found in (None, ""):
            what = ("a line (it needs an amount)" if field == invoice_form.NEW_LINE
                    else f"a line's {invoice_form.LINE_TEACHABLE[field]}")  # fmt: skip
            return refuse(f"Those words don't read as {what}: try again.")
        if field == invoice_form.NEW_LINE:
            return message(f"New line: {found['description'] or '(no description)'}, {found['amount']:.2f}. Code it "
                           "to a GL account.", "ok", field=field, value=found)  # fmt: skip
        shown = f"{found:.2f}" if field in ("line_amount", "line_unit_price") else str(found)
        return message(f"Line {invoice_form.LINE_TEACHABLE[field]} set to {shown}.", "ok", field=field, value=shown)
    if field not in invoice_form.TEACHABLE:
        return refuse("That field can't be taught.")
    value = invoice_form.form_value(field, text)
    if value in (None, ""):
        return refuse("Those words don't read as a value for that field: try again.")
    shown = f"{value:.2f}" if isinstance(value, float) else str(value)
    return message(f"{LABELS.get(field, field)} set to {shown}. Approving teaches it for this supplier.", "ok",
                   field=field, value=shown)  # fmt: skip


# --- OvisOCR2 and the AI reviewer ---------------------------------------------------------------------------------


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
