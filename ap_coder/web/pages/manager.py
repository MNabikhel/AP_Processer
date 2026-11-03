"""A manager confirms an invoice's cost allocation (D-138): the requests' pages.

* ``GET /manager``: the requests waiting for an answer. A Manager account (server mode) sees the ones sent to it; AP
  (a Clerk and above, or anyone on the laptop) sees them all, with what to do next.
* ``GET /manager/<id>``: one request: the invoice's pages, who it is from and for how much, the lines as AP coded them
  (the manager's own cost centers marked), AP's note. The manager it was sent to answers there: **Confirm**, or **Send
  back** with what to change (``POST /manager/<id>/answer``). AP downloads the ready-made email
  (``GET /manager/<id>/email.eml``) and records an answer given by email, the reply kept in the data folder
  (``POST /manager/<id>/record``; kept relative to the data folder, so it opens wherever the data folder is restored:
  ``GET /manager/<id>/reply``, B-1552), or withdraws the request (``POST /manager/<id>/withdraw``).

Either answer puts the invoice back in the review queue; AP approves it as before. Every handler checks the request
is the asker's own (a Manager) or that the asker is AP: a Manager never opens another manager's request or any other
invoice. The invoice is sent from the review screen (``POST /review/<id>/manager``, ``pages/invoice.py``).
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any

from fastapi import File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response

from ap_coder.coding.manager_mail import request_email
from ap_coder.core.store.backup_files import in_data_folder
from ap_coder.core.store.managers import CONFIRMED, EMAIL, SCREEN, SENT_BACK, WAITING
from ap_coder.core.store.users import MANAGER
from ap_coder.web import document, identity
from ap_coder.web.core import done, flash_message, get_store, page_router, refuse, render, reviewer, server_mode

router = page_router()  # each plain handler: one connection a request

REPLY_KINDS = (".eml", ".msg", ".pdf", ".txt", ".png", ".jpg", ".jpeg")  # what a manager's reply may be saved as
MAX_REPLY = 25 * 1024 * 1024


def _me() -> identity.Person | None:
    return identity.person()


def _is_manager() -> bool:
    me = _me()
    return bool(me and me.user_id and me.role == MANAGER)


def _request(request: Request, request_id: int) -> dict[str, Any]:
    """The request, if the asker may see it: a Manager only its own; AP (every other role, or the laptop) all."""
    req = get_store(request).manager_request(request_id)
    me = _me()
    if req is None or (_is_manager() and req.get("manager_user") != (me.user_id if me else None)):
        raise HTTPException(status_code=404, detail="There is no such request (or it was not sent to you).")
    return req


def _ap_only() -> None:
    if _is_manager():
        raise HTTPException(status_code=403, detail="Only accounts payable can do this.")


def _reply_dir(request: Request) -> Path:
    return get_store(request).path.parent / "emails" / "manager"


@router.get("/manager", response_class=HTMLResponse)
def requests_page(request: Request) -> HTMLResponse:
    store = get_store(request)
    me = _me()
    mine = _is_manager()
    waiting = store.waiting_manager_requests(me.user_id if mine and me else None)
    return render(request, "manager.html", "manager", waiting=waiting, manager_view=mine, server=server_mode(request))


@router.get("/manager/{request_id:int}", response_class=HTMLResponse)
def request_page(request: Request, request_id: int) -> HTMLResponse:
    store = get_store(request)
    req = _request(request, request_id)
    inv = store.get_invoice(req["invoice_id"]) or {}
    pages = 0
    try:
        pages = len(document.page_sizes(inv.get("source_path") or ""))
    except Exception:  # noqa: BLE001 - a file gone or damaged: the page says so instead of the pictures
        pages = 0
    me = _me()
    answerable = req["status"] == WAITING and _is_manager() and me is not None and req["manager_user"] == me.user_id
    total = sum(li["amount"] for li in req["lines"])
    mine_total = sum(li["amount"] for li in req["lines"] if li["cost_center"] in set(req["cost_centers"]))
    return render(request, "manager.html", "manager", req=req, inv=inv, pages=pages, answerable=answerable,
                  manager_view=_is_manager(), server=server_mode(request), total=total, mine_total=mine_total,
                  today=dt.date.today().isoformat())  # fmt: skip


@router.get("/manager/{request_id:int}/page/{number:int}.png")
def request_page_picture(request: Request, request_id: int, number: int) -> Response:
    req = _request(request, request_id)
    inv = get_store(request).get_invoice(req["invoice_id"]) or {}
    try:
        png = document.page_png(inv.get("source_path") or "", number, 1)
    except Exception as exc:  # noqa: BLE001 - a page gone or damaged
        raise HTTPException(status_code=404, detail="This page could not be drawn.") from exc
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=300"})


@router.post("/manager/{request_id:int}/answer")
def answer(request: Request, request_id: int, decision: str = Form(""), note: str = Form("")) -> Any:
    """The manager it was sent to answers on screen."""
    store = get_store(request)
    req = _request(request, request_id)
    me = _me()
    if not (_is_manager() and me is not None and req["manager_user"] == me.user_id):
        return refuse("Only the manager it was sent to answers here.", 403)
    if decision not in (CONFIRMED, SENT_BACK):
        return refuse("Confirm it, or send it back.")
    try:
        store.answer_manager_request(request_id, decision, by=me.name, actor=me.name, note=note, via=SCREEN)
    except ValueError as exc:
        return refuse(str(exc))
    said = "Confirmed: AP can approve it." if decision == CONFIRMED else "Sent back to AP with your note."
    return done(request, "/manager", flash_message(said, "ok", icon="how_to_reg"))


@router.post("/manager/{request_id:int}/record")
async def record(request: Request, request_id: int, decision: str = Form(""), by: str = Form(""),
                 answered_on: str = Form(""), note: str = Form(""),
                 reply: UploadFile | None = File(None)) -> Any:  # noqa: B008  # fmt: skip
    """AP records an answer the manager gave by email (or by phone), the reply email kept."""
    _ap_only()
    store = get_store(request)
    req = _request(request, request_id)
    if decision not in (CONFIRMED, SENT_BACK):
        return refuse("Say whether the manager confirmed it or sent it back.")
    when = None
    if answered_on.strip():
        try:
            day = dt.date.fromisoformat(answered_on.strip())
        except ValueError:
            return refuse("The date answered must be a date (YYYY-MM-DD).")
        if day > dt.date.today() or day.isoformat() < req["sent_at"][:10]:
            return refuse("The date answered must be between the day it was sent and today.")
        now = dt.datetime.now().strftime("%H:%M:%S")
        when = f"{day.isoformat()}T{now if day == dt.date.today() else '12:00:00'}"
    kept = ""
    if reply is not None and reply.filename:
        suffix = Path(reply.filename).suffix.lower()
        if suffix not in REPLY_KINDS:
            return refuse("Keep the reply as an email (.eml or .msg), a PDF, a picture or a text file.")
        data = await reply.read(MAX_REPLY + 1)
        if len(data) > MAX_REPLY:
            return refuse("The reply is too large to keep (25 MB at most).")
        folder = _reply_dir(request)
        folder.mkdir(parents=True, exist_ok=True)
        stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(reply.filename).stem)[:60] or "reply"
        target = folder / f"AP-{request_id}-{dt.datetime.now():%Y%m%d-%H%M%S}-{stem}{suffix}"
        target.write_bytes(data)
        kept = target.relative_to(get_store(request).path.parent).as_posix()  # (B-1552)
    try:
        store.answer_manager_request(request_id, decision, by=by or req["manager_name"], actor=reviewer(),
                                     note=note, answered_at=when, reply_file=kept, via=EMAIL)  # fmt: skip
    except ValueError as exc:
        if kept:
            target.unlink(missing_ok=True)
        return refuse(str(exc))
    said = "confirmed the coding" if decision == CONFIRMED else "sent it back"
    return done(request, f"/review/{req['invoice_id']}",
                flash_message(f"Recorded: {by or req['manager_name']} {said}. The invoice is back in the review "
                              "queue.", "ok", icon="how_to_reg"))  # fmt: skip


@router.post("/manager/{request_id:int}/withdraw")
def withdraw(request: Request, request_id: int) -> Any:
    _ap_only()
    store = get_store(request)
    req = _request(request, request_id)
    if req["status"] != WAITING:
        return refuse("This request was answered or withdrawn meanwhile.")
    try:
        store.unpark_invoice(req["invoice_id"], reviewer())  # withdraws the request too
    except ValueError:
        return refuse("The invoice is no longer waiting for the manager: reload the page.")
    return done(
        request,
        f"/review/{req['invoice_id']}",
        flash_message("Request withdrawn: the invoice is back in the review queue.", "ok", icon="undo"),
    )


@router.get("/manager/{request_id:int}/reply")
def reply_file(request: Request, request_id: int) -> FileResponse:
    """The manager's reply AP kept with the answer, found in the data folder as it is now (B-1552)."""
    _ap_only()
    req = _request(request, request_id)
    path = in_data_folder(req.get("reply_file") or "", get_store(request).path.parent)
    if path is None:
        raise HTTPException(status_code=404, detail="The reply kept with this answer is not in the data folder.")
    return FileResponse(path, filename=path.name)


@router.get("/manager/{request_id:int}/email.eml")
def email(request: Request, request_id: int) -> Response:
    """The ready-made request: Outlook (or Mail) opens it as a draft with the invoice attached."""
    _ap_only()
    store = get_store(request)
    req = _request(request, request_id)
    inv = store.get_invoice(req["invoice_id"]) or {"id": req["invoice_id"]}
    source = Path(inv.get("source_path") or "")
    body = request_email(inv, req, source if source.is_file() else None, signature=reviewer())
    name = f"Confirm cost allocation AP-{request_id}.eml"
    return Response(body, media_type="message/rfc822",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})  # fmt: skip
