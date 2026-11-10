"""The review screen's one ``router`` (``/review/<id>``), and what every one of its handlers starts with.

The screen's routes are declared on this router by one module a subject (``pages/invoice.py`` lists them, D-223);
``pages/invoice.py`` is the one the app registers. Here too, what they all use: the invoice asked for (``_invoice``,
``_in_review``), the coding on screen once it can be built (``_fix_first``), the account lists (``_reference``), the
queue's view carried along in the address (``NAV_PARAMS``, ``_nav_query``, ``_with_query``), and the screen again
with a message (``_back``).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from ap_coder.core.reference_data import ReferenceData
from ap_coder.core.schema import InvoiceCoding
from ap_coder.core.store import REVIEW, Store
from ap_coder.web import invoice_view as view
from ap_coder.web.core import done, flash_message, page_router, reference_or_none

router = page_router()  # each plain handler: one connection a request


NAV_PARAMS = ("show", "q", "sort", "limit")  # the queue's view (its rows: Show more), carried along so Previous / Next
# and the Queue button follow it


def _nav_query(params: Any) -> dict[str, str]:
    return {k: str(params.get(k)) for k in NAV_PARAMS if params.get(k)}


def _with_query(path: str, query: dict[str, str]) -> str:
    return path + (f"?{urlencode(query)}" if query else "")


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


def _reference(store: Store) -> ReferenceData:
    reference = reference_or_none(store)
    if reference is None:
        raise HTTPException(status_code=409, detail="Import your GL accounts first (GL accounts & tax).")
    return reference


def _fix_first(analysis: view.Analysis) -> InvoiceCoding:
    """The coding on screen; refused while a field keeps it from being built."""
    if analysis.coding is None:
        raise HTTPException(status_code=400, detail="Fix this field first: " + "; ".join(analysis.problems))
    return analysis.coding


def _back(request: Request, invoice_id: int, text: str, tone: str = "ok", icon: str = "") -> JSONResponse:
    """The invoice's screen again, with a message."""
    return done(request, f"/review/{invoice_id}", flash_message(text, tone, icon=icon))
