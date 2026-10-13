"""Vendor statements: reconcile a vendor's statement of account with the invoices AP Coder holds.

The statement is uploaded (``POST /statements/upload``, kept in memory for an hour, never stored), then
``GET /statements?vendor=&file=&col_<field>=`` shows its columns and the reconciliation, the email asking the vendor
for what is missing (``&lang=``) and ``GET /statements/reconciliation.csv`` (same address) downloads
``statements.to_csv``.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response

from ap_coder import inbox, vendor_mail
from ap_coder import statements as stm
from ap_coder.store import Store
from ap_coder.web.core import done, get_store, invoice_dir, money, refuse, render, reviewer
from ap_coder.web.files import (
    SheetError,
    download,
    keep_upload,
    picked_columns,
    read_sheet,
    read_upload,
    receive_sheet,
    sheet_context,
)

router = APIRouter()

TONES = {
    stm.MATCHED: ("ok", "check"),
    stm.DIFFERS: ("err", "error"),
    stm.NOT_RECEIVED: ("warn", "mail"),
    stm.NOT_ON_STATEMENT: ("info", "help"),
    stm.PAYMENT: ("gray", "payments"),
}
FIELD_LABELS = {
    "number": "Invoice number",
    "date": "Date",
    "amount": "Amount (or Debit)",
    "credit": "Credit (if separate)",
    "balance": "Open balance per document",
    "type": "Type",
}


def ap_status(line: stm.Line, invoices: dict[int, dict[str, Any]]) -> str:
    if not line.invoice_id:
        return ""
    inv = invoices.get(line.invoice_id) or {}
    if inv.get("export_batch"):
        return f"Exported (batch {inv['export_batch']})"
    return {"approved": "Approved", "review": "In review", "pending_approval": "Second approval"}.get(
        line.ap_status, line.ap_status
    )


def mapping_ready(chosen: dict[str, str]) -> bool:
    """The invoice number column and an amount or balance column are needed."""
    return "number" in chosen and bool({"amount", "credit", "balance"} & set(chosen))


def reconciliation(store: Store, vendor: str, records: list[dict[str, Any]], chosen: dict[str, str]) -> Any:
    return stm.reconcile(records, chosen, store.vendor_invoices(vendor))


@router.get("/statements", response_class=HTMLResponse)
def statements_page(
    request: Request, vendor: str = "", file: str = "", sheet: str = "", lang: str = vendor_mail.ENGLISH
) -> HTMLResponse:
    store = get_store(request)
    vendors = sorted(store.vendor_summaries(), key=lambda v: v["vendor_name"] or "")
    names = {v["vendor_key"]: v["vendor_name"] for v in vendors}
    vendor = vendor if vendor in names else (next(iter(names)) if names else "")
    context: dict[str, Any] = {"names": names, "vendor": vendor, "s": None, "error": "", "rec": None,
                               "emailed": inbox.statements(store, invoice_dir(store))}  # fmt: skip
    if file and names:
        try:
            upload, df, sheets = read_upload(file, sheet or None)
        except SheetError as exc:
            context["error"] = str(exc)
        else:
            columns = list(df.columns)
            chosen = picked_columns(request.query_params, FIELD_LABELS, columns, stm.map_columns(columns))
            context["s"] = sheet_context(file, upload, df, sheets, sheet, FIELD_LABELS, chosen, ("number",))
            if df.empty:
                context["error"] = "This file has no rows under its header line."
            elif not mapping_ready(chosen):
                context["error"] = "Pick the invoice number column and an amount or balance column."
            else:
                rec = reconciliation(store, vendor, df.to_dict("records"), chosen)
                by_id = {i["id"]: i for i in store.vendor_invoices(vendor)}
                lang = lang if lang in vendor_mail.LANGUAGES else vendor_mail.ENGLISH
                mail = vendor_mail.statement_request(rec.lines, lang, reviewer())
                params = [(k, v) for k, v in request.query_params.multi_items() if k != "lang"]
                context.update(
                    rec=rec,
                    tiles=[
                        ("Matched", rec.count(stm.MATCHED), money(rec.total(stm.MATCHED))),
                        ("Amount differs", rec.count(stm.DIFFERS), "same number, different amount"),
                        (
                            "Not received",
                            rec.count(stm.NOT_RECEIVED),
                            f"{money(rec.total(stm.NOT_RECEIVED))} to request",
                        ),
                        ("Not on statement", rec.count(stm.NOT_ON_STATEMENT), "in AP Coder for the period"),
                    ],
                    lines=[
                        {
                            "line": li,
                            "tone": TONES[li.status],
                            "label": stm.LABELS[li.status],
                            "where": ap_status(li, by_id) or li.note,
                        }
                        for li in rec.lines
                    ],
                    mail=mail,
                    mailto=mail.mailto() if mail else "",
                    lang=lang,
                    languages=vendor_mail.LANGUAGES,
                    query=urlencode(params),
                )
    return render(request, "statements.html", "statements", **context)


@router.post("/statements/upload")
async def statements_upload(request: Request, vendor: str = Form(""), file: UploadFile = File(...)) -> JSONResponse:  # noqa: B008
    """Keep the statement for the page (in memory for an hour; nothing is stored) and show its reconciliation."""
    token, problem = await receive_sheet(file)
    if problem:
        return refuse(problem)
    return done(request, "/statements?" + urlencode({"vendor": vendor, "file": token}))


@router.post("/statements/emailed/{entry_id}/{n}")
def statements_emailed(request: Request, entry_id: str, n: int, vendor: str = Form("")) -> JSONResponse:
    """Reconcile a statement spreadsheet that arrived by email (triage put it in ``statements/``)."""
    path = inbox.file_of(invoice_dir(get_store(request)), entry_id, n)
    if path is None:
        return refuse("That statement is no longer in the invoices folder.")
    content = path.read_bytes()
    try:
        read_sheet(path.name, content)
    except SheetError as exc:
        return refuse(str(exc))
    return done(request, "/statements?" + urlencode({"vendor": vendor, "file": keep_upload(path.name, content)}))


@router.get("/statements/reconciliation.csv")
def reconciliation_csv(request: Request, vendor: str = "", file: str = "", sheet: str = "") -> Response:
    store = get_store(request)
    try:
        _upload, df, _sheets = read_upload(file, sheet or None)
    except SheetError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    columns = list(df.columns)
    chosen = picked_columns(request.query_params, FIELD_LABELS, columns, stm.map_columns(columns))
    if not mapping_ready(chosen):
        raise HTTPException(status_code=400, detail="Pick the invoice number column and an amount or balance column.")
    rec = reconciliation(store, vendor, df.to_dict("records"), chosen)
    return download(stm.to_csv(rec), "statement_reconciliation.csv", "text/csv")
