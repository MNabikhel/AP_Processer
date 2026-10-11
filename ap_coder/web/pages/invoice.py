"""The invoice review screen, ``/review/<id>``: opened from the review queue.

For now a placeholder that names the invoice and links back; the full screen (the document, the coding, the
checks, approve / park / reject) is built here next, from ``ap_coder/webapp/review.py`` ``render_invoice``.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from ap_coder.web.core import get_store, render

router = APIRouter()


@router.get("/review/{invoice_id:int}", response_class=HTMLResponse)
def invoice_page(request: Request, invoice_id: int) -> HTMLResponse:
    invoice = get_store(request).get_invoice(invoice_id)
    if invoice is None:
        raise HTTPException(status_code=404, detail="That invoice is not in AP Coder (it may have been deleted).")
    return render(request, "invoice.html", "review", invoice=invoice)
