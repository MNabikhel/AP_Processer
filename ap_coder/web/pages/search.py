"""Find an invoice: where an invoice stands, for a vendor on the phone.

The search is a GET (``/search?q=...``): words match the vendor, numbers the invoice number, PO or amount. Each hit
says where the invoice is; an invoice in the queue opens, a parked one can be brought back, and an approved one can be
downloaded with its APPROVED stamp (``GET /search/<id>/approved.pdf``).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from ap_coder import search, stamp
from ap_coder.store import APPROVED, FAILED, PARKED, PENDING, REJECTED, REVIEW
from ap_coder.web.core import done, get_store, reference_or_none, refuse, render, reviewer
from ap_coder.web.files import download

router = APIRouter()

STATUS = {
    REVIEW: ("To review", "info", "inbox"),
    PARKED: ("Parked", "warn", "pause_circle"),
    PENDING: ("Second approval", "violet", "how_to_reg"),
    APPROVED: ("Approved", "ok", "task_alt"),
    REJECTED: ("Rejected", "err", "block"),
    FAILED: ("Unreadable", "err", "error"),
}


def hit_view(hit: search.Hit) -> dict[str, Any]:
    """One search result as the template draws it (plain values: the template escapes them)."""
    r = hit.row
    erp = hit.source == "ERP"
    if erp:
        pill = ("In the ERP register", "gray", "database")
        where = "Found in the ERP's invoice register (imported on the Exports page), not in AP Coder."
        status = ""
    else:
        status = r["status"]
        pill = STATUS.get(status, (status, "gray", "help"))
        where = search.where(r)
    show_due = r.get("due_date") and status not in (REJECTED, FAILED) and (r.get("grand_total") or 0) > 0
    return {
        "id": r.get("id"),
        "erp": erp,
        "status": status,
        "vendor": r.get("vendor_name") or "",
        "number": r.get("invoice_number") or "—",
        "date": r.get("invoice_date") or "no date",
        "due": r.get("due_date") if show_due else "",
        "total": r.get("grand_total") or 0,
        "currency": r.get("currency") or "",
        "pill": pill,
        "where": where,
    }


@router.get("/search", response_class=HTMLResponse)
def search_page(request: Request, q: str = "") -> HTMLResponse:
    store = get_store(request)
    hits = search.find(store, q) if q.strip() else []
    return render(
        request,
        "search.html",
        "search",
        q=q,
        searched=bool(q.strip()),
        hits=[hit_view(h) for h in hits],
        limited=len(hits) == search.LIMIT,
        limit=search.LIMIT,
    )


@router.get("/search/{invoice_id:int}/approved.pdf")
def approved_pdf(request: Request, invoice_id: int) -> Response:
    """The approved invoice with its APPROVED stamp and coding page (never before the second approval)."""
    store = get_store(request)
    inv = store.get_invoice(invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="That invoice is not in AP Coder.")
    if not stamp.can_stamp(inv):
        raise HTTPException(status_code=409, detail="No longer fully approved: no approved PDF.")
    reference = reference_or_none(store)
    names = (
        {row["gl_code"]: row.get("description", "") for row in reference.chart_of_accounts.rows} if reference else {}
    )
    return download(stamp.stamped_pdf(inv, names), stamp.file_name(inv), "application/pdf")


@router.post("/search/{invoice_id:int}/unpark")
def bring_back(request: Request, invoice_id: int) -> JSONResponse:
    """A parked invoice back to the review queue, and open it."""
    store = get_store(request)
    inv = store.get_invoice(invoice_id)
    if inv is None:
        return refuse("That invoice is not in AP Coder.", 404)
    try:
        store.unpark_invoice(invoice_id, reviewer())
    except ValueError:
        pass  # someone else brought it back meanwhile: it is opened all the same
    return done(request, f"/review/{invoice_id}", "Back in the review queue.")
