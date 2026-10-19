"""Purchase orders: import open POs from the ERP; invoices quoting a PO are matched against it.

* ``GET /purchase-orders?q=&status=open|closed|all&po=<key>``: the list, and one PO's lines with what was ordered,
  received and billed, and the invoices on it.
* Import: upload (``POST /purchase-orders/upload``), check the columns (``?file=``), import; the template
  (``GET /purchase-orders/template.csv``) and the sample POs that go with the sample invoices.
* A PO is closed / reopened or deleted (with a confirmation) by POST.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request, UploadFile
from fastapi import File as FileParam
from fastapi.responses import HTMLResponse, JSONResponse, Response

from ap_coder.coding import po as po_mod
from ap_coder.core import ui
from ap_coder.core.store import Store, load_sample_purchase_orders
from ap_coder.web.core import done, flash_message, get_store, message, refuse, render, reviewer
from ap_coder.web.files import (
    SheetError,
    download,
    drop_upload,
    picked_columns,
    read_upload,
    receive_sheet,
    sheet_context,
)

router = APIRouter()

FIELD_LABELS = {
    "po_number": "PO number",
    "vendor_name": "Vendor",
    "line_number": "Line number",
    "description": "Description",
    "quantity": "Quantity ordered",
    "unit_price": "Unit price",
    "amount": "Line amount",
    "received_quantity": "Quantity received",
    "gl_code": "GL account",
    "cost_center": "Cost center",
    "status": "Status (open / closed)",
}
STATUS_FILTERS = {"open": "Open", "closed": "Closed", "all": "All"}


def over_billed(p: dict[str, Any]) -> bool:
    return bool(p["total"]) and p["billed"] > p["total"] * (1 + po_mod.TOTAL_TOLERANCE) + 0.01


def status_pill(p: dict[str, Any]) -> tuple[str, str, str]:
    if p["status"] == po_mod.CLOSED:
        return ("Closed", "gray", "lock")
    if over_billed(p):
        return ("Over-billed", "err", "error")
    if p["total"] and p["billed"] >= p["total"] - 0.01:
        return ("Fully billed", "ok", "check")
    if p["billed"]:
        return ("Partly billed", "info", "hourglass_bottom")
    return ("Open", "violet", "shopping_cart")


def qty(value: float | None) -> str:
    return "—" if value is None else po_mod._qty(value)


def missing_columns(chosen: dict[str, str]) -> list[str]:
    missing = [FIELD_LABELS[f] for f in po_mod.REQUIRED_COLUMNS if f not in chosen]
    if "quantity" not in chosen and "unit_price" not in chosen and "amount" not in chosen:
        missing.append("a quantity, unit price or amount")
    return missing


def po_detail(store: Store, p: dict[str, Any]) -> dict[str, Any] | None:
    """One PO's lines (ordered, received, billed, flags) and the invoices quoting it."""
    full = store.purchase_order(p["po_key"])
    if full is None:
        return None
    invoices = store.po_invoices(p["po_key"])
    billed = po_mod.billed_by_line(full, invoices)
    billed_amount = po_mod.billed_by_line(full, invoices, amounts=True)
    lines = []
    for li in full["lines"]:
        coding = " · ".join(x for x in (li["gl_code"], li["cost_center"]) if x)
        if li.get("amount_only"):  # services / lump sums: amounts, not quantities
            done_amount = billed_amount.get(li["line_number"], 0.0)
            over = done_amount > li["amount"] * (1 + po_mod.TOTAL_TOLERANCE) + 0.01
            flag = (
                ("Over-billed", "err", "")
                if over
                else ("Billed", "ok", "check")
                if done_amount >= li["amount"] - 0.01
                else None
            )
            lines.append(
                {
                    "n": li["line_number"],
                    "description": li["description"],
                    "ordered": "amount",
                    "received": "—",
                    "billed": ui.money(done_amount),
                    "unit_price": "—",
                    "amount": li["amount"],
                    "coding": coding,
                    "flag": flag,
                }
            )
            continue
        done_qty = billed.get(li["line_number"], 0.0)
        over = done_qty > li["quantity"] + 1e-6
        unreceived = li["received_quantity"] is not None and done_qty > li["received_quantity"] + 1e-6
        flag = (
            ("Over-billed", "err", "")
            if over
            else ("Not received", "warn", "")
            if unreceived
            else ("Billed", "ok", "check")
            if done_qty >= li["quantity"] - 1e-6
            else None
        )
        lines.append(
            {
                "n": li["line_number"],
                "description": li["description"],
                "ordered": qty(li["quantity"]),
                "received": qty(li["received_quantity"]),
                "billed": qty(done_qty),
                "unit_price": ui.money(li["unit_price"]),
                "amount": li["amount"],
                "coding": coding,
                "flag": flag,
            }
        )
    pills = {"approved": ("Approved", "ok", "check"), "pending_approval": ("Second approval", "violet", "")}
    return {
        "po": p,
        "pill": status_pill(p),
        "lines": lines,
        "total": full["total"],
        "invoices": [
            {
                **i,
                "pill": pills.get(i["status"], ("In queue", "info", "inbox")),
                "subtotal": i["coding"].get("subtotal"),
            }
            for i in invoices
        ],
        "closed": p["status"] == po_mod.CLOSED,
        "label": po_mod.po_label(p["po_number"]),
    }


@router.get("/purchase-orders", response_class=HTMLResponse)
def purchase_orders_page(
    request: Request, q: str = "", status: str = "open", po: str = "", file: str = "", sheet: str = ""
) -> HTMLResponse:
    store = get_store(request)
    status = status if status in STATUS_FILTERS else "open"
    pos = store.purchase_orders()
    shown = [
        p
        for p in pos
        if (status == "all" or p["status"] == status)
        and (not q or re.search(re.escape(q), f"{p['po_number']} {p['vendor_name']}", re.IGNORECASE))
    ]
    open_pos = [p for p in pos if p["status"] == po_mod.OPEN]
    over = [p for p in pos if over_billed(p)]
    tiles = [
        ("Open POs", len(open_pos), f"{len(pos)} in total"),
        ("Still to be invoiced", ui.money(sum(max(p["remaining"], 0) for p in open_pos)), "on open POs"),
        (
            "Invoiced against POs",
            ui.money(sum(p["billed"] for p in pos)),
            ui.plural(sum(p["invoices"] for p in pos), "invoice"),
        ),
        ("Over-billed", len(over), "invoices exceed the PO total" if over else "none"),
    ]

    imp, imp_error, missing = None, "", []
    if file:
        try:
            upload, df, sheets = read_upload(file, sheet or None)
            columns = list(df.columns)
            if df.empty:
                imp_error = "This file has no rows under its header line."
            chosen = picked_columns({}, FIELD_LABELS, columns, po_mod.map_columns(columns))
            missing = missing_columns(chosen)
            imp = sheet_context(file, upload, df, sheets, sheet, FIELD_LABELS, chosen, po_mod.REQUIRED_COLUMNS)
        except SheetError as exc:
            imp_error = str(exc)

    chosen_po = next((p for p in shown if p["po_key"] == po), None) or next((p for p in pos if p["po_key"] == po), None)
    chosen_po = chosen_po or (shown[0] if shown else None)
    keep = {"q": q, "status": status}
    return render(
        request,
        "purchase_orders.html",
        "purchase_orders",
        pos=pos,
        shown=[
            {
                **p,
                "pill": status_pill(p),
                "fraction": (p["billed"] / p["total"]) if p["total"] else None,
                "bar": round(100 * max(0.0, min(p["billed"] / p["total"], 1.0))) if p["total"] else 0,
            }
            for p in shown
        ],
        tiles=tiles,
        q=q,
        status=status,
        statuses=STATUS_FILTERS,
        list_query=urlencode(keep),
        in_review=any(p["in_review"] for p in shown),
        detail=po_detail(store, chosen_po) if chosen_po else None,
        imp=imp,
        imp_error=imp_error,
        missing=missing,
    )


@router.get("/purchase-orders/template.csv")
def template_csv() -> Response:
    return download(po_mod.template_csv(), "purchase_orders_template.csv", "text/csv")


@router.post("/purchase-orders/upload")
async def po_upload(request: Request, file: UploadFile = FileParam(...)) -> JSONResponse:  # noqa: B008
    token, problem = await receive_sheet(file)
    if problem:
        return refuse(problem)
    return done(request, f"/purchase-orders?file={token}#import")


@router.post("/purchase-orders/import")
async def po_import(request: Request) -> JSONResponse:
    """One row per PO line; a PO in the file replaces its lines (a fresh export also updates what was received)."""
    form = await request.form()
    store = get_store(request)
    token, sheet = str(form.get("file") or ""), str(form.get("sheet") or "")
    try:
        _upload, df, _sheets = read_upload(token, sheet or None)
    except SheetError as exc:
        return refuse(str(exc))
    if df.empty:
        return refuse("This file has no rows under its header line.")
    chosen = picked_columns(form, FIELD_LABELS, list(df.columns), {})
    missing = missing_columns(chosen)
    if missing:
        return refuse("Pick the column for: " + ", ".join(missing))
    rows, skipped = po_mod.rows_from_records(df.to_dict("records"), chosen)
    result = store.import_purchase_orders(rows, replace_all=bool(form.get("replace")), actor=reviewer())
    drop_upload(token)
    text = (
        f"Imported {ui.plural(result['orders'], 'PO')} with {ui.plural(result['lines'], 'line')}: "
        f"{result['added']} new, {result['updated']} updated"
        + (f", {ui.plural(skipped, 'row')} skipped (no PO number or description)." if skipped else ".")
    )
    return done(request, "/purchase-orders", flash_message(text, "ok", icon="shopping_cart"))


@router.post("/purchase-orders/sample")
def po_sample(request: Request) -> JSONResponse:
    """The sample POs that go with the sample invoices (only before any PO is imported)."""
    store = get_store(request)
    if store.has_purchase_orders():
        return message("You already have purchase orders: the samples are only for an empty list.", "info")
    keys = load_sample_purchase_orders(store)
    return done(
        request,
        "/purchase-orders",
        flash_message(f"Loaded {len(keys)} sample purchase orders.", "ok", icon="shopping_cart"),
    )


def _known(store: Store, key: str) -> dict[str, Any] | None:
    return next((p for p in store.purchase_orders() if p["po_key"] == key), None)


@router.post("/purchase-orders/status")
def po_toggle(request: Request, key: str = Form(...)) -> JSONResponse:
    """Close an open PO (no more invoices expected on it), or reopen a closed one."""
    store = get_store(request)
    p = _known(store, key)
    if p is None:
        return refuse("That purchase order is not in AP Coder.", 404)
    closed = p["status"] == po_mod.CLOSED
    store.set_po_status(key, po_mod.OPEN if closed else po_mod.CLOSED, actor=reviewer())
    url = "/purchase-orders?" + urlencode({"status": "all", "po": key}) + "#po"  # keep this PO in view
    return done(
        request,
        url,
        flash_message(
            f"{po_mod.po_label(p['po_number'])} {'reopened' if closed else 'closed'}.", "ok", icon="shopping_cart"
        ),
    )


@router.post("/purchase-orders/delete")
def po_delete(request: Request, key: str = Form(...)) -> JSONResponse:
    """Delete a PO from the list (invoices are not affected)."""
    store = get_store(request)
    p = _known(store, key)
    if p is None:
        return refuse("That purchase order is not in AP Coder.", 404)
    store.delete_purchase_orders([key], actor=reviewer())
    return done(
        request, "/purchase-orders", flash_message(f"{po_mod.po_label(p['po_number'])} deleted.", "ok", icon="delete")
    )
