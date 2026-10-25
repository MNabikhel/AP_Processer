"""Exports: approved invoices go to the ERP in batches; each invoice is exported once, plus the
duplicate payment audit.

* JD Edwards through the API (D-104, step 2 on): ``POST /exports/batches/<n>/jde-api/dry-run`` (a Clerk: what would
  post, checked here and by JDE's proof mode), ``.../jde-api/post`` (an Approver or Admin: the go-ahead) and
  ``POST /exports/jde-api/retry`` (the queue), each a background job; the batch's vouchers and their results are on
  the batch's panel (``jde_post.status_rows``).
* ``GET /exports``: the ready list (``?fmt=`` picks the format; JD Edwards E1 checks every invoice first), the batch
  just made (``?made=<batch>``), the custom CSV layout, the ERP's invoice register, past batches (``?batch=&bfmt=``
  picks one to download again, stamp or undo) and the duplicate audit (``?audit=1``).
* Downloads (GET): ``/exports/batches/<n>/download?fmt=``, ``/exports/batches/<n>/approved-pdfs.zip``,
  ``/exports/duplicates.csv``. The files are the ones ``exports.build`` / ``jde.build_zip`` / ``stamp.batch_zip`` /
  ``dupaudit.to_csv`` make.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response

from ap_coder.core import ui
from ap_coder.core.reference_data import short_name
from ap_coder.core.store import Store
from ap_coder.erp import dupaudit, export_layout, exports, jde, jde_ais, jde_post, registers, stamp
from ap_coder.web.core import (
    by_currency,
    done,
    flash_message,
    get_store,
    jde_shown,
    jobs,
    message,
    one_connection,
    posted_form,
    reference_or_none,
    refuse,
    render,
)
from ap_coder.web.core import reviewer as current_reviewer
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

LAYOUT_SETTING = "export_layout"
SHORT_FORMATS = {
    "xlsx": "Excel workbook",
    "csv": "CSV, one row per GL line",
    "custom": "Custom CSV layout",
    jde.FORMAT: "JD Edwards E1 (Z-tables)",
}
AGAIN_FORMATS = {"custom": "Custom CSV", jde.FORMAT: "JD Edwards E1 (ZIP)"}  # the others read as XLSX, CSV
FORMAT_SETTING = "export_format"  # the format of the last batch made: the page opens on it (Excel at first)


def shown_formats(store: Store, *keep: str) -> list[str]:
    """The export formats offered: JD Edwards E1 only once its settings are shown (Settings → About; the pilot exports
    to Excel and CSV), or when ``keep`` names it (a JD Edwards batch made before, a link to it)."""
    return [f for f in exports.FORMATS if f != jde.FORMAT or jde_shown(store) or f in keep]


def last_format(store: Store) -> str:
    """The format the page opens on: the last one a batch was made in (M4), else the Excel workbook."""
    fmt = store.get_setting(FORMAT_SETTING) or "xlsx"
    return fmt if fmt in shown_formats(store) else "xlsx"


REGISTER_LABELS = {
    "vendor_name": "Vendor",
    "invoice_number": "Invoice number",
    "invoice_date": "Date",
    "total": "Total",
}
BLANK_LAYOUT_ROWS = 3  # empty rows under the layout's columns, to add more
AUDIT_SHOWN = 100


# --- What the files are made of ------------------------------------------------------------------------------------


def gl_names(store: Store) -> dict[str, str]:
    reference = reference_or_none(store)
    if reference is None:
        return {}
    return {row["gl_code"]: short_name(row.get("description") or "") for row in reference.chart_of_accounts.rows}


def layout(store: Store) -> export_layout.Layout:
    return export_layout.Layout.from_json(store.get_setting(LAYOUT_SETTING))


def jde_left_out(store: Store, batch: int) -> tuple[list[dict[str, Any]], dict[int, list[str]]]:
    """(the batch's invoices, {invoice: why it cannot go to JD Edwards}) with the current JD Edwards settings.

    A batch made as JD Edwards passed the checks when it was made and keeps every invoice: checking it again would
    count its own vouchers, once in the ERP register, as duplicates of themselves. A batch made in another format is
    checked now, as it never was for JD Edwards."""
    ids = store.batch_invoice_ids(batch)
    invoices = store.get_invoices(ids)
    known = next((b for b in store.export_batches() if b["id"] == batch), None)
    if known and known["format"] == jde.FORMAT:
        return invoices, {}
    left_out = jde.validate(invoices, jde.load(store), store.vendor_ids(), jde.prior_records(store, exclude_ids=ids))
    return invoices, left_out


def jde_file(store: Store, batch: int) -> tuple[bytes, str, str]:
    """The batch's JD Edwards ZIP, rebuilt from the approved data and the current JD Edwards settings. A JD Edwards
    batch holds the same vouchers every time (what the current settings would now refuse is listed in the README
    to check before loading); in another batch an invoice that does not pass the checks is left out."""
    invoices, left_out = jde_left_out(store, batch)
    # Vouchers JD Edwards has, or may have, through the API (D-104, D-105) are left out of the files: loaded again,
    # they would be twice. The README lists them (the unknown ones: check in JD Edwards before loading).
    left_out = {**left_out, **jde_post.left_out_of_files(store, batch)}
    settings, lookup = jde.load(store), store.vendor_ids()
    created = next((b["created_at"] for b in store.export_batches() if b["id"] == batch), "")
    kept = [i for i in invoices if i["id"] not in left_out]
    return jde.build_zip(
        kept,
        settings,
        lookup,
        batch=batch,
        fx_rates=store.fx_rates(),
        on=dt.date.fromisoformat(created[:10]) if created else None,
        left_out=left_out,
        to_check=jde.validate(kept, settings, lookup),
    )


def jde_check(store: Store, invoice_ids: list[int]) -> tuple[list[int], dict[int, list[str]], list[str]]:
    """(invoices that can go out, {invoice: why not}, settings problems that stop everything)."""
    settings = jde.load(store)
    blocking = jde.settings_problems(settings)
    invoices = store.get_invoices(invoice_ids)
    problems = jde.validate(invoices, settings, store.vendor_ids(), jde.prior_records(store))
    ok = [] if blocking else [i["id"] for i in invoices if i["id"] not in problems]
    return ok, problems, blocking


def batch_file(store: Store, batch: int, fmt: str) -> tuple[bytes, str, str]:
    """(bytes, file name, content type) of export batch ``batch`` in format ``fmt``."""
    if fmt == jde.FORMAT:
        return jde_file(store, batch)
    invoices = store.get_invoices(store.batch_invoice_ids(batch))
    return exports.build(fmt, invoices, gl_names(store), batch, store.vendor_ids(), layout=layout(store),
                         tax_treatments=store.tax_treatments(), bank_accounts=bank_accounts(store))  # fmt: skip


def bank_accounts(store: Store) -> dict[str, dict[str, Any]]:
    """Our bank accounts by code: the Pay from columns of the exports (D-66)."""
    return {a["code"]: a for a in store.bank_accounts()}


def problem_rows(store: Store, problems: dict[int, list[str]]) -> list[dict[str, Any]]:
    found = {i["id"]: i for i in store.get_invoices(list(problems))}
    rows = []
    for inv_id, reasons in problems.items():
        inv = found.get(inv_id) or {}
        rows.append(
            {
                "id": inv_id,
                "vendor": inv.get("vendor_name") or "",
                "number": inv.get("invoice_number") or "",
                "reasons": list(reasons),
                "an8": any(jde.an8_problem(r) for r in reasons),  # linked to Settings → JD Edwards E1
            }
        )
    return rows


def batch_total(b: dict[str, Any], totals: dict[int, dict[str, float]], exported: dict[int, dict[str, float]]) -> list:
    """[(amount, currency)] a batch held: undone batches from the audit trail; always per currency."""
    amounts = totals.get(b["id"]) or exported.get(b["id"]) or {}
    return sorted(amounts.items())


def layout_preview(store: Store, new: export_layout.Layout) -> str:
    """The first lines of a custom CSV in layout ``new``, from invoices ready to export (else batch 1's)."""
    sample = store.unexported_approved()[:3] or [{"id": i} for i in store.batch_invoice_ids(1)[:3]]
    invoices = store.get_invoices([i["id"] for i in sample])
    rows = exports.custom_rows(invoices, gl_names(store), "", store.vendor_ids(), store.tax_treatments(),
                               bank_accounts(store))  # fmt: skip
    if not rows or not new.columns:
        return ""
    return export_layout.build(rows[:6], new).decode("utf-8-sig")


def layout_from_form(form: Any) -> tuple[export_layout.Layout, list[str], list[str]]:
    """(the layout the form describes, incomplete rows, warnings). A row needs both a header and a value."""
    headers, fields, texts = form.getlist("header"), form.getlist("field"), form.getlist("text")
    columns, incomplete = [], []
    for n, header in enumerate(headers, 1):
        header = str(header).strip()
        field = str(fields[n - 1] if n - 1 < len(fields) else "").strip()
        text = str(texts[n - 1] if n - 1 < len(texts) else "").strip()
        if field and field not in export_layout.FIELDS and field != export_layout.FIXED:
            field = ""
        if header and field:
            columns.append(export_layout.Column(header, field, text))
        elif header or field:
            incomplete.append(f"row {n} ({header or export_layout.FIELDS.get(field, field)})")
    date_format = str(form.get("date_format") or "")
    delimiter = str(form.get("delimiter") or "")
    date_format = date_format if date_format in export_layout.DATE_FORMATS else next(iter(export_layout.DATE_FORMATS))
    delimiter = delimiter if delimiter in export_layout.DELIMITERS else ","
    comma = bool(form.get("decimal_comma"))
    new = export_layout.Layout(columns, date_format, delimiter, comma, bool(form.get("header_row")),
                               bool(form.get("byte_order_mark")))  # fmt: skip
    warnings = []
    if incomplete:
        warnings.append(f"Not in the layout until it has both a Header and a Value: {', '.join(incomplete)}.")
    if comma and delimiter == ",":
        warnings.append("With a decimal comma, use a semicolon or tab as the separator.")
    return new, incomplete, warnings


# --- The page ----------------------------------------------------------------------------------------------------


@router.get("/exports", response_class=HTMLResponse)
@one_connection
def exports_page(
    request: Request,
    fmt: str = "",
    made: int = 0,
    batch: int = 0,
    bfmt: str = "",
    audit: int = 0,
    register: str = "",
    sheet: str = "",
) -> HTMLResponse:
    store = get_store(request)
    fmt = fmt if fmt in exports.FORMATS else last_format(store)
    ready = store.unexported_approved()
    batches = store.export_batches()
    live = [b for b in batches if not b["undone_at"]]

    # Ready to export: with JD Edwards, the invoices that cannot go are shown, not ticked.
    problems: dict[int, list[str]] = {}
    blocking: list[str] = []
    if fmt == jde.FORMAT and ready:
        _, problems, blocking = jde_check(store, [r["id"] for r in ready])
    rows = [{**r, "blocked": r["id"] in problems or bool(blocking)} for r in ready]

    # The batch just made, ready to download
    just = next((b for b in live if b["id"] == made), None)

    # Past batches: the one picked to download again, stamp or undo
    chosen = next((b for b in live if b["id"] == batch), live[0] if live else None)
    again_fmt = bfmt if bfmt in exports.FORMATS else (chosen["format"] if chosen else "xlsx")
    if again_fmt not in exports.FORMATS:
        again_fmt = "xlsx"
    left_out = (0, 0)
    if chosen and again_fmt == jde.FORMAT:
        invoices, missing = jde_left_out(store, chosen["id"])
        left_out = (len(missing), len(invoices))
    totals, exported = store.batch_totals(), store.exported_totals()

    # JD Edwards through the API (D-104): the chosen batch's vouchers and the queue
    jde_api = api_context(store, chosen)

    # The custom layout
    current = layout(store)
    layout_rows = [{"header": c.header, "field": c.field, "text": c.text} for c in current.columns]
    layout_rows += [{"header": "", "field": "", "text": ""}] * BLANK_LAYOUT_ROWS

    # The ERP register: an uploaded list waiting for its columns
    reg, reg_error = None, ""
    if register:
        try:
            upload, df, sheets = read_upload(register, sheet or None)
            columns = list(df.columns)
            chosen_cols = picked_columns({}, REGISTER_LABELS, columns, registers.map_columns(columns))
            reg = sheet_context(register, upload, df, sheets, sheet, REGISTER_LABELS, chosen_cols, registers.REQUIRED)
        except SheetError as exc:
            reg_error = str(exc)

    # The duplicate payment audit, when asked for
    pairs = dupaudit.find(store) if audit else None
    return render(
        request,
        "exports.html",
        "exports",
        ready=rows,
        ready_total=by_currency(ready),
        live=live,
        batches=[
            {
                **b,
                "amounts": batch_total(b, totals, exported),
                "format_label": exports.FORMATS.get(b["format"], b["format"]).split(":")[0],
                "when": (b["created_at"] or "").replace("T", " ")[:16],
            }
            for b in batches
        ],
        exported_count=sum(b["invoices"] for b in live),
        fmt=fmt,
        formats={f: SHORT_FORMATS[f] for f in shown_formats(store, fmt) if f in SHORT_FORMATS},
        format_help=exports.FORMATS,
        jde_format=jde.FORMAT,
        problems=problem_rows(store, problems),
        blocking=blocking,
        selectable=sum(1 for r in rows if not r["blocked"]),
        just=just,
        chosen=chosen,
        again_fmt=again_fmt,
        again_formats={f: AGAIN_FORMATS.get(f, f.upper()) for f in shown_formats(store, again_fmt)},
        left_out=left_out,
        layout=current,
        layout_rows=layout_rows,
        layout_fields={**export_layout.FIELDS, export_layout.FIXED: "Fixed text…"},
        date_formats=list(export_layout.DATE_FORMATS),
        delimiters=export_layout.DELIMITERS,
        csv_preview=layout_preview(store, current),
        register_count=store.erp_register_count(),
        reg=reg,
        reg_error=reg_error,
        pairs=pairs[:AUDIT_SHOWN] if pairs else pairs,
        pair_count=len(pairs or []),
        reasons=dupaudit.REASONS,
        exact=dupaudit.EXACT,
        audit_at=dt.datetime.now().strftime("%H:%M"),
        empty_art=ui.EMPTY_INBOX_SVG if live else ui.EMPTY_TRAY_SVG,
        jde_api=jde_api,
    )


def api_context(store: Store, chosen: dict[str, Any] | None) -> dict[str, Any]:
    """The batch panel's "JD Edwards API" part: shown for a JD Edwards batch while step 2 is on, or once any of its
    vouchers went that way."""
    cfg = jde_ais.load(store)
    rows = jde_post.status_rows(store, chosen["id"]) if chosen and chosen["format"] == jde.FORMAT else []
    shown = bool(chosen and chosen["format"] == jde.FORMAT and (cfg.step2 or rows))
    return {"shown": shown, "on": cfg.step2, "rows": rows,
            "checked": sum(r["status"] == jde_post.CHECKED for r in rows),
            "waiting": len(jde_post.waiting(store)), "done": all(r["status"] == jde_post.POSTED for r in rows) and
            bool(rows)}  # fmt: skip


# --- Exporting ---------------------------------------------------------------------------------------------------


@router.post("/exports/create")
async def create_batch(request: Request) -> JSONResponse:
    """A new batch of the ticked invoices, in the chosen format (JD Edwards: only those that pass its checks)."""
    form = await posted_form(request)  # one field per invoice ticked
    with get_store(request).session():  # nothing awaited inside: no other request runs on this thread meanwhile
        return _create_batch(request, form)


def _create_batch(request: Request, form: Any) -> JSONResponse:
    store = get_store(request)
    fmt = str(form.get("fmt") or "")
    if fmt not in exports.FORMATS:
        return refuse("Choose the ERP format first.")
    try:
        chosen = [int(str(i)) for i in form.getlist("ids")]
    except ValueError:
        return refuse("Tick the invoices to export.")
    if not chosen:
        return refuse("Tick the invoices to export.")
    if fmt == jde.FORMAT:
        chosen, _problems, blocking = jde_check(store, chosen)
        if blocking:
            return refuse(" ".join(f"{p} Set it in Settings → JD Edwards E1." for p in blocking))
        if not chosen:
            return refuse("None of the ticked invoices can go to JD Edwards yet: fix them first (see the list).")
    try:
        batch = store.create_export_batch(chosen, fmt, actor=current_reviewer())
        if fmt != store.get_setting(FORMAT_SETTING):
            store.set_setting(FORMAT_SETTING, fmt)  # the page opens on it next time (M4)
    except ValueError:  # the page is drawn again: what is still ready to export, without them
        return done(
            request,
            f"/exports?fmt={fmt}",
            "These invoices were exported meanwhile (or are no longer approved). The page is refreshed.",
            tone="warn",
        )
    count = len(store.batch_invoice_ids(batch))
    return done(
        request,
        f"/exports?made={batch}&fmt={fmt}&batch={batch}",
        flash_message(
            f"Batch {batch} created with {ui.plural(count, 'invoice')}. Download it below.", "ok", icon="ios_share"
        ),
    )


@router.get("/exports/jde/dry-run.txt")
@one_connection
def jde_dry_run(request: Request) -> Response:
    """What a JD Edwards batch of the invoices ready to export would load, row by row, and why any invoice is held
    back. Nothing is exported and no batch is made."""
    store = get_store(request)
    invoices = store.get_invoices([r["id"] for r in store.unexported_approved()])
    report = jde.dry_run(invoices, jde.load(store), store.vendor_ids(), prior=jde.prior_records(store),
                         batch="(next)", fx_rates=store.fx_rates())  # fmt: skip
    return download(jde.dry_run_text(report).encode("utf-8"), "ap_coder_jde_dry_run.txt", "text/plain; charset=utf-8")


def live_batch(store: Store, batch: int) -> dict[str, Any]:
    """Export batch ``batch``, to download. An undone batch holds no invoice any more (they are back in Ready to
    export, or in a later batch): its file would be empty, so it is refused."""
    known = next((b for b in store.export_batches() if b["id"] == batch), None)
    if known is None:
        raise HTTPException(status_code=404, detail="There is no such export batch.")
    if known["undone_at"]:
        raise HTTPException(status_code=410, detail=f"Batch {batch} was undone: its invoices can be exported again.")
    return known


@router.get("/exports/batches/{batch:int}/download")
@one_connection
def download_batch(request: Request, batch: int, fmt: str = "") -> Response:
    """Batch ``batch`` as a file again: in ``fmt``, else in the format it went out in."""
    store = get_store(request)
    known = live_batch(store, batch)
    fmt = fmt or known["format"]
    if fmt not in exports.FORMATS:
        raise HTTPException(status_code=400, detail="Unknown export format.")
    if fmt == jde.FORMAT and (blocking := jde.settings_problems(jde.load(store))):
        # Z-files made with these settings cannot load (their own dry run says "NOTHING CAN LOAD"): say what to fix.
        detail = " ".join(f"{p} Set it in Settings → JD Edwards E1, then download the batch again." for p in blocking)
        raise HTTPException(status_code=409, detail=detail)
    return download(*batch_file(store, batch, fmt))


@router.get("/exports/batches/{batch:int}/approved-pdfs.zip")
@one_connection
def approved_pdfs(request: Request, batch: int) -> Response:
    """Each invoice of the batch with its APPROVED stamp and coding page, in one ZIP (to attach in the ERP)."""
    store = get_store(request)
    live_batch(store, batch)
    invoices = store.get_invoices(store.batch_invoice_ids(batch))
    data = stamp.batch_zip(invoices, gl_names(store), store.tax_treatments())
    return download(data, f"ap_coder_batch_{batch}_approved_pdfs.zip", "application/zip")


@router.post("/exports/batches/{batch:int}/undo")
@one_connection
def undo_batch(request: Request, batch: int) -> JSONResponse:
    """The ERP import failed: the batch's invoices go back to Ready to export (the batch stays, marked undone)."""
    store = get_store(request)
    known = next((b for b in store.export_batches() if b["id"] == batch), None)
    if known is None or known["undone_at"]:
        return refuse(f"Batch {batch} is not an export that can be undone.")
    try:  # refused in the same transaction when JD Edwards has (or may have) one of its vouchers (D-104, D-105)
        count = jde_post.undo_batch(store, batch, actor=current_reviewer())
    except jde_post.Locked as exc:
        return refuse(str(exc))
    except ValueError:  # undone meanwhile (another click, another tab)
        return refuse(f"Batch {batch} is not an export that can be undone.")
    return done(
        request,
        "/exports",
        flash_message(f"Batch {batch} undone: {ui.plural(count, 'invoice')} ready to export again.", "ok", icon="undo"),
    )


# --- The custom layout --------------------------------------------------------------------------------------------


@router.post("/exports/layout")
async def save_layout(request: Request) -> JSONResponse:
    store = get_store(request)
    new, _incomplete, warnings = layout_from_form(await posted_form(request))
    if not new.columns:
        return refuse("Give the layout at least one column (a header and a value).")
    store.set_setting(LAYOUT_SETTING, new.to_json(), actor=current_reviewer())
    store.log_event("settings_changed", actor=current_reviewer(), detail={"keys": ["export_layout"]})
    notes = [flash_message("Export layout saved. Choose Custom CSV when exporting.", "ok", icon="view_column")]
    notes += [flash_message(w, "warn") for w in warnings]
    return done(request, "/exports?fmt=custom#layout", *notes)


@router.post("/exports/layout/preview")
async def preview_layout(request: Request) -> JSONResponse:
    """The first lines of the custom CSV as the form stands (nothing is saved)."""
    store = get_store(request)
    new, _incomplete, warnings = layout_from_form(await posted_form(request))
    return JSONResponse({"ok": True, "preview": layout_preview(store, new), "warnings": warnings})


# --- Invoices already in the ERP -----------------------------------------------------------------------------------


@router.post("/exports/register/upload")
async def register_upload(request: Request, file: UploadFile = File(...)) -> JSONResponse:  # noqa: B008
    token, problem = await receive_sheet(file)
    if problem:
        return refuse(problem)
    return done(request, f"/exports?register={token}#register")


@router.post("/exports/register/import")
async def register_import(request: Request) -> JSONResponse:
    form = await posted_form(request)
    store = get_store(request)
    token, sheet = str(form.get("file") or ""), str(form.get("sheet") or "")
    try:
        _upload, df, _sheets = read_upload(token, sheet or None)
    except SheetError as exc:
        return refuse(str(exc))
    chosen = picked_columns(form, REGISTER_LABELS, list(df.columns), {})
    missing = [REGISTER_LABELS[f] for f in registers.REQUIRED if f not in chosen]
    if missing:
        return refuse("Pick the column for: " + ", ".join(missing))
    rows, skipped = registers.rows_from_records(df.to_dict("records"), chosen)
    total = store.import_erp_register(rows, replace_all=bool(form.get("replace")), actor=current_reviewer())
    drop_upload(token)
    text = f"{total:,} ERP invoices known" + (f"; {ui.plural(skipped, 'row')} skipped." if skipped else ".")
    if repeats := store.erp_register_repeats():
        text += f" {ui.plural(repeats, 'bill')} entered more than once in the ERP: see the duplicate payment audit."
    return done(request, "/exports#register", flash_message(text, "ok", icon="receipt_long"))


@router.post("/exports/register/clear")
def register_clear(request: Request) -> JSONResponse:
    store = get_store(request)
    if not store.erp_register_count():
        return message("The list is already empty.", "info")
    store.clear_erp_register(actor=current_reviewer())
    return done(request, "/exports#register", "The ERP invoice list was cleared.")


# --- Duplicate payment audit ------------------------------------------------------------------------------------------


@router.get("/exports/duplicates.csv")
def duplicates_csv(request: Request) -> Response:
    """Every pair the duplicate payment audit finds, as a CSV."""
    pairs = dupaudit.find(get_store(request))
    return download(dupaudit.to_csv(pairs), f"duplicate_audit_{dt.date.today()}.csv", "text/csv")


# --- JD Edwards through the API (D-104) -----------------------------------------------------------------------------

API_JOB = "jde-api"


def _api_job(request: Request, note: str, work: Any) -> JSONResponse:
    if not jobs(request).get(API_JOB).start(work, total=1, note=note):
        return refuse("JD Edwards is being sent something already: wait for it to finish.", status=409)
    return JSONResponse({"ok": True, "job": API_JOB, "status": f"/jobs/{API_JOB}"})


def _api_messages(result: dict[str, Any]) -> list[dict[str, str]]:
    parts = [f"{result[k]} {label}" for k, label in (("posted", "posted"), ("failed", "refused by JD Edwards"),
             ("queued", "waiting in the queue"), ("unknown", "sent, being checked")) if result.get(k)]  # fmt: skip
    out = [
        {
            "text": ("JD Edwards: " + ", ".join(parts) + ".") if parts else "Nothing was sent.",
            "tone": "ok" if not (result.get("failed") or result.get("queued") or result.get("unknown")) else "warn",
        }
    ]
    if result.get("error"):
        out.append({"text": result["error"], "tone": "warn"})
    out += [{"text": s, "tone": "warn"} for s in result.get("skipped") or []]
    return out


@router.post("/exports/batches/{batch:int}/jde-api/dry-run")
def jde_api_dry_run(request: Request, batch: int) -> JSONResponse:
    """What the batch's vouchers would post, checked here and in JD Edwards' proof mode; nothing is posted."""
    store = get_store(request)
    try:
        jde_post.payloads(store, batch)
    except ValueError as exc:
        return refuse(str(exc))
    actor = current_reviewer()

    def work(job: Any) -> dict[str, Any]:
        job.step(0, 1, "Checking the vouchers with JD Edwards…")
        found = jde_post.dry_run(store, batch, actor=actor)
        rows = found["rows"]
        ok = sum(r["status"] == jde_post.CHECKED for r in rows)
        out = [
            {
                "text": f"Dry run of batch {batch}: {ok} of {ui.plural(len(rows), 'voucher')} ready to post.",
                "tone": "ok" if ok == len(rows) else "warn",
            }
        ]
        if found["jde"]:
            out.append({"text": f"Checked here only, not by JD Edwards: {found['jde']}", "tone": "warn"})
        return {"messages": out}

    return _api_job(request, "Dry run through the JD Edwards API", work)


@router.post("/exports/batches/{batch:int}/jde-api/post")
def jde_api_post(request: Request, batch: int) -> JSONResponse:
    """The go-ahead (an Approver or Admin): the vouchers that passed the dry run, unchanged since, are posted."""
    store = get_store(request)
    if not jde_ais.load(store).step2:
        return refuse("Step 2 of the JD Edwards API (vouchers) is off: an Admin switches it on in Settings → JD "
                      "Edwards E1 → API connection.")  # fmt: skip
    if not any(p["status"] == jde_post.CHECKED for p in jde_post.postings(store, batch)):
        return refuse("Run the dry run first: only the vouchers that passed it are posted.")
    actor = current_reviewer()

    def work(job: Any) -> dict[str, Any]:
        job.step(0, 1, "Posting the vouchers to JD Edwards…")
        try:
            return {"messages": _api_messages(jde_post.post(store, batch, actor=actor))}
        except (ValueError, jde_ais.AisError) as exc:
            return {"messages": [{"text": str(exc), "tone": "warn"}]}

    return _api_job(request, "Posting to JD Edwards", work)


@router.post("/exports/jde-api/retry")
def jde_api_retry(request: Request) -> JSONResponse:
    """The queue now: each voucher waiting is looked up in JD Edwards by its key first, then sent if it isn't there."""
    store = get_store(request)
    if not jde_post.waiting(store):
        return message("Nothing is waiting for JD Edwards.", "info")
    actor = current_reviewer()

    def work(job: Any) -> dict[str, Any]:
        job.step(0, 1, "Sending the queue to JD Edwards…")
        return {"messages": _api_messages(jde_post.retry_queue(store, actor=actor))}

    return _api_job(request, "Sending the JD Edwards queue", work)
