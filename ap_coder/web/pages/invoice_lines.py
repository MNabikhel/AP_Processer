"""The review screen's actions on the lines: POST ``split``, ``apply-rules``, ``po-coding``, ``suggestion``,
``fill-lines`` (Apply to all lines), all under ``/review/<id>``.

Each answers the grid with its new rows (``_new_lines``), kept as the draft; the page draws them and checks again.
What an action does to the rows is ``web/invoice_grid.py``'s.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from ap_coder.core import ui
from ap_coder.core.reference_data import ReferenceData
from ap_coder.core.schema import InvoiceCoding
from ap_coder.core.store import Store
from ap_coder.web import account_lines, invoice_form, invoice_grid
from ap_coder.web import invoice_view as view
from ap_coder.web.core import get_settings, get_store, message, one_connection, posted_form, refuse, templates
from ap_coder.web.pages.invoice_drafts import _draft_rev, _drafts, _from_form, _keep_draft, _out_of_date, _OutOfDate
from ap_coder.web.pages.invoice_router import _fix_first, _in_review, _reference, router

# --- Changing the line grid ---------------------------------------------------------------------------------------
# Each action's form is read on the event loop (a long invoice posts thousands of fields, B-455); the rest runs on a
# threadpool thread (D-158), as the screen's Check does.


def _posted_in_review(request: Request, invoice_id: int,
                      form: Any) -> tuple[Store, dict[str, Any], ReferenceData, invoice_form.Draft]:  # fmt: skip
    """A grid action's invoice (still in the review queue), the GL accounts and the draft the form posted."""
    store = get_store(request)
    if stale := _out_of_date(request, store, invoice_id, store.get_invoice(invoice_id), form):  # first, as a check
        raise _OutOfDate(stale)  # (left the queue meanwhile: what was done, once, B-1940)
    inv = _in_review(store, invoice_id)
    reference = _reference(store)
    return store, inv, reference, _from_form(request, inv, form)


def _coded(request: Request, invoice_id: int,
           form: Any) -> tuple[Store, dict[str, Any], ReferenceData, invoice_form.Draft, InvoiceCoding]:  # fmt: skip
    """As ``_posted_in_review``, with the coding on screen (refused while a field keeps it from being built)."""
    store, inv, reference, draft = _posted_in_review(request, invoice_id, form)
    coding = _fix_first(view.analyse(store, get_settings(request), reference, inv, draft))
    return store, inv, reference, draft, coding


def _render_lines(reference: ReferenceData, draft: invoice_form.Draft, ai: dict[str, Any] | None = None) -> str:
    mod = templates.env.get_template("_invoice_lines.html").module
    return str(mod.line_rows(invoice_grid.grid(reference, draft, ai)))


def _new_lines(request: Request, invoice_id: int, reference: ReferenceData, draft: invoice_form.Draft,
               lines: list[dict[str, Any]], text: str, icon: str, form: Any) -> JSONResponse:  # fmt: skip
    """The grid with its new rows, kept as the draft; the page draws them and checks again (at the draft's revision
    now, ``draft_rev``)."""
    draft.lines = lines
    _keep_draft(request, invoice_id, draft, True, form=form)
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
    number = invoice_form._line_number(form.get("split_line"))
    if number is None:
        return refuse("Pick the line to split.")
    by = "amount" if str(form.get("split_by") or "") == "amount" else "percent"
    parts = []
    gls, ccs = form.getlist("part_gl"), form.getlist("part_cc")
    if whole := form.getlist("part_account"):  # with a chart of accounts: one Account box a part (D-214)
        gls, ccs = (list(codes) for codes in zip(*(account_lines.posted(a, reference.pairs) for a in whole),
                                                 strict=True))  # fmt: skip
    for gl, cc, share in zip(gls, ccs or [""] * len(gls),
                             form.getlist("part_amount" if by == "amount" else "part_pct"), strict=False):  # fmt: skip
        try:
            value = float(str(share).strip() or "nan")
        except ValueError:
            value = float("nan")
        parts.append((str(gl or ""), str(cc or ""), value))
    try:
        lines = invoice_grid.split(draft, number, parts, reference.cost_centers is not None, by=by)
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
    lines, n = invoice_grid.apply_rules(store, coding, draft, invoice_id, inv["ai_output"] or {})
    return _new_lines(request, invoice_id, reference, draft, lines,
                      f"Coding rules applied to {ui.plural(n, 'line')}.", "rule_settings", form)  # fmt: skip


@router.post("/review/{invoice_id:int}/po-coding")
async def use_po_coding(request: Request, invoice_id: int) -> JSONResponse:
    """Set the GL account and cost center of the lines that differ from the purchase order to the PO's."""
    return await _line_action(_use_po_coding, request, invoice_id)


@one_connection
def _use_po_coding(request: Request, invoice_id: int, form: Any) -> JSONResponse:
    store, _, reference, draft, coding = _coded(request, invoice_id, form)
    lines, n = invoice_grid.po_coding(store, coding, draft, invoice_id)
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
        lines = invoice_grid.use_suggestion(reference, coding, draft, position, gl, str(form.get("cc") or ""))
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
    if account := " ".join(str(form.get("fill_account") or "").split()):  # one Account box a line (D-214)
        gl, cc = account_lines.posted(account, reference.pairs)
        if reference.pairs is None or not reference.pairs.offered(cc, gl):
            return refuse(f"{account} is not an account of the chart that invoices are coded to: pick one from the "
                          "list.")  # fmt: skip
        lines, n = account_lines.fill(draft.lines, gl, cc)
        text = f"Account {account} set on {ui.plural(n, 'line')}. Lines that had an account kept theirs."
        return _new_lines(request, invoice_id, reference, draft, lines, text, "playlist_add_check", form)
    gl, cc = str(form.get("fill_gl") or "").strip(), str(form.get("fill_cc") or "").strip()
    if not gl and not cc:
        return refuse("Pick a GL account or a cost center to apply to the lines without one.")
    if gl and (reference.chart_of_accounts.get(gl) is None or gl in reference.tax.tax_gl_codes()):
        return refuse(f"GL {gl} is not in your GL accounts (or it is a sales-tax account).")
    if cc and (reference.cost_centers is None or reference.cost_centers.get(cc) is None):
        return refuse(f"Cost center {cc} is not in your cost center list.")
    lines, n_gl, n_cc = invoice_grid.fill_lines(draft, gl, cc)
    done_text = [f"GL {gl} set on {ui.plural(n_gl, 'line')}" if gl else "",
                 f"Cost center {cc} set on {ui.plural(n_cc, 'line')}" if cc else ""]  # fmt: skip
    text = "; ".join(t for t in done_text if t) + ". Lines already coded kept theirs."
    return _new_lines(request, invoice_id, reference, draft, lines, text, "playlist_add_check", form)
