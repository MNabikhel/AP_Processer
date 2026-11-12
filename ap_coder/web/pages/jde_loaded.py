"""What is loaded of JD Edwards' files (D-221), opened from the JD Edwards files page's "What is loaded": the rows
themselves of the chart of accounts, the business units, the suppliers, last year's coding and the vouchers already
in JD Edwards, each a table with a search, its cheap column filters, a count and pages, all of them SQLite's
(``Store.loaded_page``: a register of 600,000 bills is never read whole), and a download of what the search and the
filters keep as CSV (``Store.loaded_batches``, sent as it is read).

Whoever opens the JD Edwards files page sees them (in server mode a Clerk and above, web/roles.py). An Admin changes
a row of the first three by hand (``Store.edit_chart_account``, ``edit_business_unit``, ``edit_supplier``): the
change is kept when a newer file is loaded, with what the file says beside it, and recorded in Activity. Last year's
coding and the vouchers are a record of what happened: looked at, and forgotten whole by the buttons there were.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
from collections.abc import Iterable, Iterator
from typing import Any
from urllib.parse import urlencode

from fastapi import HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

from ap_coder.core import ui
from ap_coder.core.chart import code_of, join_account
from ap_coder.core.safe import csv_row
from ap_coder.core.store import Store, by_hand, loaded
from ap_coder.web.core import (
    can,
    done,
    get_store,
    local_url,
    one_connection,
    page_router,
    posted_form,
    refuse,
    render,
    reviewer,
)
from ap_coder.web.files import attachment

router = page_router()  # each plain handler: one connection a request

HERE = "/jde-files/loaded"
# Each list: its name, its card on the JD Edwards files page, what a search looks in, and what it is.
LISTS = {
    loaded.CHART: ("Chart of accounts", "chart", "business unit, object, description, 0021061010.7340…",
                   "Every row of the chart as loaded: each business unit's accounts, and the business unit's own "
                   "header row (no object)."),
    loaded.UNITS: ("Business units", "units", "code, name, company…",
                   "Every business unit of the business unit master as loaded."),
    loaded.SUPPLIERS: ("Suppliers", "suppliers", "name, supplier number, tax number…",
                       "Every supplier of the supplier master as loaded."),
    loaded.JOURNAL: ("Last year's coding", "journal", "supplier, account, remark, 2025-03…",
                     "Every line of the journals loaded, a voucher's lines together, the oldest first. A record of "
                     "what was keyed: it is not changed here."),
    loaded.VOUCHERS: ("Invoices already in JD Edwards", "vouchers", "supplier, invoice number, date…",
                      "Every voucher of the voucher list loaded: what the duplicate checks look in. A record of what "
                      "is in JD Edwards: it is not changed here."),
}  # fmt: skip
EDITABLE = (loaded.CHART, loaded.UNITS, loaded.SUPPLIERS)
KEPT = ("What you change here is kept when a newer file is loaded: the row keeps your value, and the page shows what "
        "the file says beside it. Type the file's value back to follow the file again.")  # fmt: skip
KEPT_MORE = {
    loaded.CHART: " The meals tick is the GL account's, on every business unit; it stays while the account is one "
                  "lines are coded to, and comes back with it.",
    loaded.SUPPLIERS: " A supplier renamed in JD Edwards arrives as a new row: what was changed on the old name "
                      "stays with the old name. The supplier number and JD Edwards' hold payment code are JD "
                      "Edwards' own: change them there.",
}  # fmt: skip
CSV_CHUNK = 1000  # the CSV's rows sent at a time
POSTABLE = "Can be posted to"
CHANGED = "Changed by hand"


def _known(what: str) -> str:
    if what not in LISTS:
        raise HTTPException(status_code=404, detail="No such list.")
    return what


def _asked(request: Request) -> tuple[str, dict[str, str]]:
    """The search and the filters in the address (everything but the page, the row being changed and a message)."""
    q = request.query_params
    return q.get("q") or "", {key: value for key, value in q.items()
                              if key not in ("q", "page", "edit", "flash") and value}  # fmt: skip


def _whole(value: Any) -> int:
    try:
        return max(1, int(value or 1))
    except ValueError:
        return 1


def _cell(kind: str, value: Any) -> Any:
    """A value as the CSV has it: a code as its text (leading zeros and all), a flag as yes or blank."""
    if kind == loaded.FLAG:
        return "yes" if value else ""
    return "" if value is None else value


def csv_lines(what: str, columns: Iterable[loaded.Column], batches: Iterable[list[dict[str, Any]]]) -> Iterator[bytes]:
    """The rows as a CSV for Excel (UTF-8 with BOM; text a spreadsheet would run as a formula is defused), sent a
    few rows at a time as they are read. The chart says in words whether each account may be posted to; a list a
    person may change names the fields changed by hand."""
    columns = list(columns)
    out = io.StringIO()
    writer = csv.writer(out)
    out.write("﻿")
    more = ([POSTABLE] if what == loaded.CHART else []) + ([CHANGED] if what in EDITABLE else [])
    writer.writerow([column.label for column in columns] + more)
    labels = {column.key: column.label for column in columns}
    n = 0
    for batch in batches:
        for row in batch:
            cells = [_cell(column.kind, row[column.key]) for column in columns]
            if what == loaded.CHART:
                cells.append(("yes" if row["postable"] else "no") if row["object"] else "")
            if what in EDITABLE:
                cells.append(", ".join(labels.get(field, field) for field in row[loaded.BY_HAND]))
            writer.writerow(csv_row(cells))
            n += 1
            if n % CSV_CHUNK == 0:
                yield out.getvalue().encode("utf-8")
                out.seek(0)
                out.truncate()
    yield out.getvalue().encode("utf-8")


@router.get(HERE + "/{what}.csv")
def loaded_csv(request: Request, what: str) -> Response:
    """Every row the page's search and filters keep (not only the page shown), sent as it is read."""
    store = get_store(request)
    q, filters = _asked(request)
    columns = store.loaded_columns(_known(what))
    name = f"ap_coder_{LISTS[what][1]}_{dt.date.today()}.csv"
    headers = {"Content-Disposition": attachment(name), "Cache-Control": "no-store"}
    return StreamingResponse(csv_lines(what, columns, store.loaded_batches(what, q, filters)),
                             media_type="text/csv; charset=utf-8", headers=headers)  # fmt: skip


def _row_title(what: str, row: dict[str, Any]) -> str:
    if what == loaded.CHART:
        return f"{join_account(row['bu'], code_of(row['object'], row['subsidiary']))} {row['description']}".strip()
    if what == loaded.UNITS:
        return f"business unit {row['code']} {row['name']}".strip()
    return f"supplier {row.get('erp_id') or ''} {row['display_name']}".strip()


def _field(row: dict[str, Any], name: str, label: str, kind: str = "text", **more: Any) -> dict[str, Any]:
    """One field of the form that changes a row: its value now and, when it was changed by hand, what the file
    says and who changed it."""
    edit = row[loaded.BY_HAND].get(name)
    return {"name": name, "label": label, "kind": kind, "value": row.get(name), "file": edit["file"] if edit else None,
            "by": (edit or {}).get("by", ""), **more}  # fmt: skip


def edit_form(store: Store, what: str, row: dict[str, Any]) -> dict[str, Any]:
    """The form that changes ``row`` by hand: its fields, each a text box, a tick, or shown only (``fixed``)."""
    if what == loaded.CHART:
        fields = [_field(row, "description", "Description")]
        if row["object"]:  # (a business unit's own header row has no account to post to)
            said = row[loaded.BY_HAND].get("posting_edit")
            fields.append({"name": "postable", "label": "Can be posted to", "kind": "tick", "value": row["postable"],
                           "file": said["file"] or "(blank)" if said else None, "by": (said or {}).get("by", ""),
                           "note": f"its posting edit code is {row['posting_edit'] or 'blank'}"})  # fmt: skip
            note = "" if row["listed"] else "not a GL account lines are coded to: no tick to keep"
            fields.append({"name": "meals", "label": "Meals account: half its GST/HST is claimable", "kind": "tick",
                           "value": row["meals"], "file": None, "disabled": not row["listed"],
                           "note": note})  # fmt: skip
    elif what == loaded.UNITS:
        note = "ACT, or blank, takes postings; any other status does not"
        fields = [_field(row, "name", "Name"), _field(row, "status", "Status", note=note)]
    else:
        fields = []
        for column in store.supplier_master_fields():
            if not column.editable:
                fields.append(_field(row, column.name, column.label, "fixed", note="from JD Edwards"))
            elif column.name == "status":
                held = row["status"] == by_hand.ON_HOLD
                fields.append({**_field(row, "status", "On hold: AP's own hold, which stops the supplier's invoices",
                                        "tick"), "value": held})  # fmt: skip
            else:
                fields.append(_field(row, column.name, column.label))
    return {"key": row["row_key"], "title": _row_title(what, row), "fields": fields}


@router.get(HERE + "/{what}", response_class=HTMLResponse)
def loaded_list(request: Request, what: str) -> HTMLResponse:
    store = get_store(request)
    name, card, hint, about = LISTS[_known(what)]
    q, filters = _asked(request)
    found = store.loaded_page(what, q, filters, _whole(request.query_params.get("page")))
    page_no = found.pop("page")

    def url(**changes: Any) -> str:
        kept = {"q": q, **filters, "page": page_no if page_no > 1 else "", **changes}
        asked = urlencode({key: value for key, value in kept.items() if value not in ("", None)})
        return f"{HERE}/{what}" + (f"?{asked}" if asked else "")

    may_edit = what in EDITABLE and can(request, f"{HERE}/{what}/edit")
    key = request.query_params.get("edit") or ""
    row = store.loaded_row(what, key) if may_edit and key else None
    first = (page_no - 1) * loaded.PAGE
    return render(
        request, "jde_loaded.html", "jde_files",
        what=what, name=name, card=card, hint=hint, about=about, q=q, url=url, page_no=page_no,
        lists=[(listed, said[0]) for listed, said in LISTS.items()],
        csv_url=url(page="").replace(f"{HERE}/{what}", f"{HERE}/{what}.csv", 1),
        filtered=bool(q.strip() or filters),
        only_by_hand=bool(filters.get(loaded.BY_HAND)),
        shown=(first + 1, first + len(found["rows"])),
        may_edit=may_edit,
        editing=edit_form(store, what, row) if row else None,
        edit_gone=bool(may_edit and key and row is None),
        kept=KEPT + KEPT_MORE.get(what, ""),
        journals=store.journal_files() if what == loaded.JOURNAL else [],
        **found,
    )  # fmt: skip


@router.post(HERE + "/{what}/edit")
async def loaded_edit(request: Request, what: str) -> JSONResponse:
    """A row of the chart, the business units or the suppliers changed by hand (an Admin's, web/roles.py)."""
    if _known(what) not in EDITABLE:
        return refuse(f"{LISTS[what][0]} is a record of what happened: it is not changed here.")
    form = await posted_form(request)
    return await run_in_threadpool(_edit, request, what, form)


@one_connection
def _edit(request: Request, what: str, form: Any) -> JSONResponse:
    store, key = get_store(request), str(form.get("key") or "")
    try:
        if what == loaded.CHART:
            bu, obj, subsidiary = by_hand.chart_parts(key)
            changed = store.edit_chart_account(
                bu, obj, subsidiary, description=form.get("description"),
                postable=bool(form.get("postable")) if obj else None,
                meals=bool(form.get("meals")) if obj else None, actor=reviewer(),
            )  # fmt: skip
        elif what == loaded.UNITS:
            changed = store.edit_business_unit(key, name=form.get("name"), status=form.get("status"), actor=reviewer())
        else:
            names = [column.name for column in store.supplier_master_fields() if column.editable]
            typed = {name: form.get(name) for name in names if name in form and name != "status"}
            if "status" in names:  # a tick: not sent when it is not ticked
                typed["status"] = by_hand.ON_HOLD if form.get("status") else by_hand.IN_USE
            changed = store.edit_supplier(key, typed, actor=reviewer())
    except ValueError as exc:
        return refuse(str(exc))
    back = local_url(str(form.get("back") or ""), f"{HERE}/{what}")
    if not changed:
        return done(request, back, "Nothing changed.")
    return done(request, back, f"Saved: {ui.plural(len(changed), 'change')} made by hand, recorded in Activity and "
                "kept when a newer file is loaded.")  # fmt: skip
