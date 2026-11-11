"""The review screen's check of the edits: ``POST /review/<id>/check`` and ``start-over``.

Every edit is checked as the reviewer types: ``check`` answers the sections that follow the edits again
(``_render_live``, ``_shown_live``; with a chart of accounts each row's live cells, ``_line_views``) and keeps the
edits as the person's draft with what keeps Approve closed, for the queue (``_keep_checked``, ``_draft_blocker``).
``start_over`` drops them. The drafts themselves are ``invoice_drafts.py``'s; what is worked out,
``web/invoice_view.py``'s (``analyse``).
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from ap_coder.core import ui
from ap_coder.core.store import Store
from ap_coder.web import account_lines, invoice_form, invoice_grid
from ap_coder.web import invoice_view as view
from ap_coder.web.core import (
    can,
    get_settings,
    get_store,
    one_connection,
    posted_form,
    refuse,
    reviewer,
    server_mode,
    templates,
)
from ap_coder.web.pages.invoice_drafts import (
    _draft_rev,
    _drafts,
    _from_form,
    _keep_draft,
    _keeps,
    _out_of_date,
    _OutOfDate,
    forget_draft,
)
from ap_coder.web.pages.invoice_router import _back, _in_review, _invoice, _reference, router


def _shown_live(request: Request, live: dict[str, Any], may_edit: bool = True, draft_rev: str = "") -> dict[str, Any]:
    """The sections' values with what the person's role may use (server mode, D-98): Read-only changes nothing, and
    the JD Edwards settings are an Admin's. ``draft_rev``: the draft revision the screen is at (D-184)."""
    first = (live.get("bar") or {}).get("first") if live.get("account") else None
    return {**live, "read_only": not may_edit, "jde_settings": can(request, "/settings/jde", "GET"),
            **({"bar_fix": first["said"]} if first and first.get("said") else {}),
            "setup_contact": "an Admin" if server_mode(request) else "the pilot tester",
            "draft_rev": draft_rev}  # fmt: skip


def _render_live(live: dict[str, Any], invoice_id: int) -> dict[str, str]:
    """Every section that follows the edits, drawn again (``_invoice_live.html``)."""
    mod = templates.env.get_template("_invoice_live.html").module
    names = ("summary", "checks", "amounts", "reasons", "suggest", "po", "taxcheck", "posting", "bar", "jde")
    if live.get("account"):  # with a chart of accounts: the line of what is keyed on the pay item (D-214)
        names += ("keyed", "taxline")
    if not live["ok"]:  # a field to fix: the coding can't be built, so the rest keeps its last state
        names = ("checks", "amounts", "taxcheck", "posting", "bar")
    return {name: str(getattr(mod, name)(live, invoice_id)) for name in names}


def _line_views(grid: dict[str, Any]) -> list[dict[str, str]] | None:
    """With a chart of accounts (D-214): each row's live cells as a check answers them, in row order (its state,
    and the cells marked ``data-live-line`` drawn again); None for the rows of an install without one."""
    if not grid.get("account"):
        return None
    mod = templates.env.get_template("_invoice_lines.html").module
    return [{"state": a["state"], "text": a["text"], "cap": str(mod.acct_cap(a)), "slot": str(mod.acct_slot(a)),
             "candidates": a["listed"],
             "tax": str(mod.acct_tax(a)), "keyed": str(mod.acct_keyed(a)),
             "remark": str(mod.acct_remark(a["remark"])),
             "remark_box": {k: a["remark"][k] for k in ("text", "typed", "posted", "why")},
             "has_keyed": bool(a["taxes"] and a["taxes"]["keyed"])}
            for a in (row["acct"] for row in grid["lines"])]  # fmt: skip


@router.post("/review/{invoice_id:int}/check")
async def check(request: Request, invoice_id: int) -> JSONResponse:
    """The screen's sections again for what is on it now; the edits are kept as a draft, and the queue shows the
    confidence this screen shows (edits and today's checks included). The form is read here; the checking runs on a
    threadpool thread, so a check never holds up the dashboard's other requests."""
    form = await posted_form(request)  # a long invoice posts thousands of fields (B-455)
    try:
        return await run_in_threadpool(_check, request, invoice_id, form)
    except _OutOfDate as stale:  # another tab's check wrote the draft meanwhile (B-1938)
        return refuse(stale.stale["text"], 409, stale=stale.stale)


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
    listed = {code for code, _ in invoice_grid.gl_options(reference, draft.lines)}
    used = [c for c in invoice_grid.vendor_accounts(store, draft.header.get("vendor_name") or "") if c in listed]
    kept = _keep_checked(request, store, invoice_id, draft, analysis, form=form)  # written last: read nothing after
    # the lines' tax codes where the invoice is supplied now, and the lines the Tax check points at (D-64)
    codes = invoice_grid.tax_choices(draft, reference.tax)
    # What the server filled in the invoice details (the vendor list's tax numbers, the default Pay from account) and
    # the details to highlight: the screen shows them (invoice.js applyHeader).
    header = {"values": analysis.filled, "from_vendor": draft.from_vendor, "pay_from_for": draft.pay_from_for}
    if analysis.coding is not None:
        header["flags"] = analysis.live.get("flags") or {}
        header["flag_errors"] = analysis.live.get("flag_errors") or []  # red, as an error (B-796)
        header["help"] = analysis.live.get("help") or {}  # a note with a one-click fix under a field (D-75)
        header["an8"] = analysis.live.get("an8")  # the supplier's JD Edwards number, when it is the export in use
    # With a chart of accounts: what each line's row says under its boxes, for the account now in it (D-214)
    rows = (_line_views(invoice_grid.grid(reference, draft, inv.get("ai_output") or {}, checked=analysis))
            if account_lines.in_use(reference) else None)  # fmt: skip
    return JSONResponse({"ok": True, "valid": analysis.coding is not None, "allow": analysis.allow, "draft": kept,
                         **({"lines": rows, "supplier_accounts": account_lines.supplier_accounts(
                             store, reference, draft.header.get("vendor_name") or "")} if rows is not None else {}),
                         "problems": analysis.problems, "vendor_accounts": used, "header": header,
                         "tax_codes": codes["options"], "tax_code_labels": codes["labels"],
                         "tax_marks": {str(k): v for k, v in (analysis.live.get("tax_marks") or {}).items()},
                         # the tax rows as checked: a row moved to a new place of supply moves on screen (B-1020)
                         "taxes": [{"tax_type": t.get("tax_type") or "", "province": t.get("province") or ""}
                                   for t in draft.taxes],
                         "sections": _render_live(_shown_live(request, analysis.live, draft_rev=_draft_rev(
                             _drafts(request).get(invoice_id))), invoice_id)})  # fmt: skip


def _keep_checked(request: Request, store: Store, invoice_id: int, draft: invoice_form.Draft, analysis: view.Analysis,
                  screen: str = "", form: Any = None) -> bool:  # fmt: skip
    """What a check of the screen keeps: the edits as a draft, or none when there are none, and the confidence it
    worked out (the queue shows the one the screen shows, edits and today's checks included). Whether they are kept.
    ``screen``, ``form``: the screen's own (``_keep_draft``; refused: nothing is kept)."""
    kept = _keeps(draft, analysis)
    _keep_draft(request, invoice_id, draft, kept, _draft_blocker(analysis) if kept else None, screen, form)
    if analysis.report is not None:
        store.refresh_confidence(invoice_id, analysis.report.adjusted_confidence, analysis.report.requires_review)
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


@router.post("/review/{invoice_id:int}/start-over")
def start_over(request: Request, invoice_id: int) -> JSONResponse:
    """Drop the edits not approved yet: the fields show the invoice as read (OvisOCR2's reading, when it is in)."""
    _invoice(get_store(request), invoice_id)
    forget_draft(request, invoice_id)
    return _back(request, invoice_id, "The fields show the reading again.", icon="restart_alt")
