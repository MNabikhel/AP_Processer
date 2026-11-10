"""Exports: approved invoices go to the ERP in batches; each invoice is exported once, plus the
duplicate payment audit.

* JD Edwards through the API (D-104, step 2 on): ``POST /exports/batches/<n>/jde-api/dry-run`` (a Clerk: what would
  post, checked here and by JDE's proof mode), ``.../jde-api/post`` (an Approver or Admin: the go-ahead) and
  ``POST /exports/jde-api/retry`` (the queue), each a background job; the batch's vouchers and their results are on
  the batch's panel (``jde_post.status_rows``).
* ``GET /exports``: the ready list (``?fmt=`` picks the format; JD Edwards E1 checks every invoice first), the batch
  just made (``?made=<batch>``), the custom CSV layout, the ERP's invoice register, past batches (``?batch=&bfmt=``
  picks one to download again, stamp or undo) and the duplicate audit (``?audit=1``).
* Downloads (GET; a batch's JD Edwards ZIP by POST, D-135): ``/exports/batches/<n>/download?fmt=``,
  ``/exports/batches/<n>/approved-pdfs.zip``, ``/exports/duplicates.csv``, ``/exports/jde/dry-run.txt`` (what a JD
  Edwards batch of the ready invoices would load). The files are the ones ``exports.build`` / ``jde.build_zip`` /
  ``stamp.batch_zip`` / ``dupaudit.to_csv`` / ``jde.dry_run_text`` make.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, BinaryIO

from fastapi import File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, Response

from ap_coder.core import ui
from ap_coder.core.reference_data import short_name
from ap_coder.core.store import Store
from ap_coder.erp import dupaudit, export_layout, exports, jde, jde_ais, jde_keying, jde_post, registers, stamp
from ap_coder.web.core import (
    by_currency,
    done,
    flash_message,
    get_store,
    jde_shown,
    jobs,
    message,
    one_connection,
    page_router,
    posted_form,
    reference_or_none,
    refuse,
    render,
    run_heavy,
)
from ap_coder.web.core import reviewer as current_reviewer
from ap_coder.web.files import (
    NO_ROWS,
    SheetError,
    download,
    drop_upload,
    picked_columns,
    read_upload,
    receive_sheet,
    sheet_context,
)
from ap_coder.web.pages import downloads

router = page_router()  # each plain handler: one connection a request

LAYOUT_SETTING = "export_layout"
SHORT_FORMATS = {
    "xlsx": "Excel workbook",
    "csv": "CSV, one row per GL line",
    "custom": "Custom CSV layout",
    jde.FORMAT: "JD Edwards E1 (Z-tables)",
    jde_keying.FORMAT: "JD Edwards keying sheet",
}
AGAIN_FORMATS = {"custom": "Custom CSV", jde.FORMAT: "JD Edwards E1 (ZIP)",
                 jde_keying.FORMAT: "JD Edwards keying sheet",
                 jde_keying.CSV_FORMAT: "JD Edwards keying sheet (CSV)"}  # the others read as XLSX, CSV  # fmt: skip
JDE_FORMATS = (jde.FORMAT, jde_keying.FORMAT, jde_keying.CSV_FORMAT)
# A batch made in one of these holds only invoices that passed the JD Edwards checks (the others stay in Ready to
# export, listed with why): the Z-files to load, and the sheet to type from (B-2148).
JDE_CHECKED = (jde.FORMAT, jde_keying.FORMAT)
FORMAT_SETTING = "export_format"  # the format of the last batch made: the page opens on it (Excel at first)


def shown_formats(store: Store, *keep: str) -> list[str]:
    """The export formats offered: JD Edwards E1 and its keying sheet only once its settings are shown (Settings →
    About; a laptop exports to Excel and CSV), or when ``keep`` names one (a JD Edwards batch made before, a link to
    it)."""
    shown = jde_shown(store)
    return [f for f in exports.FORMATS if f not in JDE_FORMATS or shown or f in keep]


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

    A batch made as JD Edwards, or as its keying sheet, passed the checks when it was made and keeps every invoice:
    checking it again would count its own vouchers, once in the ERP register, as duplicates of themselves (B-2149:
    a sheet's vouchers, typed and back in the voucher list, were each "already in the ERP"). A batch made in
    another format is checked now, as it never was for JD Edwards."""
    ids = store.batch_invoice_ids(batch)
    invoices = store.get_invoices(ids)
    known = store.export_batch(batch)
    if known and known["format"] in JDE_CHECKED:
        return invoices, {}
    prior = jde.prior_records(store, exclude_ids=ids, invoices=invoices)  # only what has their numbers (D-156)
    left_out = jde.validate(invoices, jde.load(store), store.vendor_ids(), prior)
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
    known = store.export_batch(batch) or {}
    created = known.get("created_at") or ""
    kept = [i for i in invoices if i["id"] not in left_out]
    return jde.build_zip(
        kept,
        settings,
        lookup,
        batch=batch,
        fx_rates=store.fx_rates(),
        on=dt.date.fromisoformat(created[:10]) if created else None,
        left_out=left_out,
        check_now=True,  # to_check: what the current settings would now refuse (B-21)
        left_invoices=[i for i in invoices if i["id"] in left_out],
        no_undo=jde_post.undo_problem(store, batch),
        site=jde.batch_site(known, invoices),  # the keys the API sends too (D-182)
    )


def jde_check(store: Store, invoice_ids: list[int],
              files: bool = True) -> tuple[list[int], dict[int, list[str]], list[str]]:  # fmt: skip
    """(invoices that can go out, {invoice: why not}, settings problems that stop everything). ``files``: for the
    Z-files; not so for the keying sheet, which a setting only the files need (the user ID, the batch prefix) does
    not stop."""
    settings = jde.load(store)
    blocking = jde.settings_problems(settings) if files else []
    invoices = store.get_invoices(invoice_ids)
    problems = jde.validate(invoices, settings, store.vendor_ids(), jde.prior_records(store, invoices=invoices))
    ok = [] if blocking else [i["id"] for i in invoices if i["id"] not in problems]
    return ok, problems, blocking


def keying_file(store: Store, batch: int, fmt: str) -> tuple[bytes, str, str]:
    """The batch's JD Edwards keying sheet (the workbook or its CSV), from the approved data and the current JD
    Edwards settings: every invoice of the batch is on it, one the checks hold back (a batch made in another
    format, or settings changed since), or that JD Edwards has (or may have) through the API, on its "Not ready"
    sheet with why."""
    invoices, left_out = jde_left_out(store, batch)
    left_out = {**left_out, **jde_post.left_out_of_files(store, batch)}
    created = (store.export_batch(batch) or {}).get("created_at") or ""
    return jde_keying.build(fmt, invoices, jde.load(store), store.vendor_ids(), batch=batch, gl_names=gl_names(store),
                            on=dt.date.fromisoformat(created[:10]) if created else None, left_out=left_out)  # fmt: skip


def batch_file(store: Store, batch: int, fmt: str) -> tuple[bytes, str, str]:
    """(bytes, file name, content type) of export batch ``batch`` in format ``fmt``."""
    if fmt == jde.FORMAT:
        return jde_file(store, batch)
    if fmt in (jde_keying.FORMAT, jde_keying.CSV_FORMAT):
        return keying_file(store, batch, fmt)
    invoices = store.get_invoices(store.batch_invoice_ids(batch))
    return exports.build(fmt, invoices, gl_names(store), batch, store.vendor_ids(), layout=layout(store),
                         tax_treatments=store.tax_treatments(), bank_accounts=bank_accounts(store))  # fmt: skip


def bank_accounts(store: Store) -> dict[str, dict[str, Any]]:
    """Our bank accounts by code: the Pay from columns of the exports (D-66)."""
    return {a["code"]: a for a in store.bank_accounts()}


READY_FORMAT = "ready-1"  # ``ready_problems``' rows as kept for every process; another when what is kept changes


def ready_problems(store: Store, invoice_ids: list[int]) -> list[dict[str, Any]]:
    """The invoices ready to export (``invoice_ids``) that cannot go to JD Edwards, as the page lists them
    (``problem_rows``): ``jde_check``'s, worked out once for every process (D-169) and kept until an invoice, the
    vendor master, the ERP register, JD Edwards' settings, the tax-rate file (the amount sent gross reads it, B-2074)
    or the day change (0.6 s at 300,000 invoices with 1,100 ready, at every visit before: D-206). Making a batch
    checks its invoices again (``_create_batch``). Shared: read it, don't change it."""
    text = store.get_setting(jde.SETTING_KEY)  # (checked by the very settings it is kept for, not read again: B-2075)

    def work() -> list[dict[str, Any]]:
        invoices = store.get_invoices(invoice_ids)
        problems = jde.validate(invoices, jde.JdeSettings.from_json(text), store.vendor_ids(),
                                jde.prior_records(store, invoices=invoices))  # fmt: skip
        return problem_rows(store, problems, invoices)

    asked = (invoice_ids, text, dt.date.today().isoformat(), store.rates_stamp())
    return store.shared_answer("jde_ready", work, version=READY_FORMAT, changes=("register", "vendors"), asked=asked)


def problem_rows(store: Store, problems: dict[int, list[str]],
                 invoices: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:  # fmt: skip
    """The page's rows of ``problems`` ({invoice: why it cannot go}), with each invoice's vendor and number (of
    ``invoices``, when they were read already)."""
    found = {i["id"]: i for i in (store.get_invoices(list(problems)) if invoices is None else invoices)}
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


def layout_preview(store: Store, new: export_layout.Layout, ready: list[dict[str, Any]] | None = None) -> str:
    """The first lines of a custom CSV in layout ``new``, from invoices ready to export (``ready``, when the page has
    read them already), else batch 1's."""
    ready = store.unexported_approved() if ready is None else ready
    sample = ready[:3] or [{"id": i} for i in store.batch_invoice_ids(1)[:3]]
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

    # Ready to export: with JD Edwards (its files or its keying sheet), the invoices that cannot go are shown, not
    # ticked.
    problems: list[dict[str, Any]] = []
    blocking: list[str] = []
    if fmt in JDE_CHECKED and ready:
        problems = ready_problems(store, [r["id"] for r in ready])
        blocking = jde.settings_problems(jde.load(store)) if fmt == jde.FORMAT else []
    held = {p["id"] for p in problems}
    rows = [{**r, "blocked": r["id"] in held or bool(blocking)} for r in ready]

    # The batch just made, ready to download
    just = next((b for b in live if b["id"] == made), None)

    # Past batches: the one picked to download again, stamp or undo
    chosen = next((b for b in live if b["id"] == batch), live[0] if live else None)
    again_fmt = bfmt if bfmt in exports.FORMATS else (chosen["format"] if chosen else "xlsx")
    if again_fmt not in exports.FORMATS:
        again_fmt = "xlsx"
    left_out = (0, 0)
    if chosen and again_fmt in JDE_CHECKED:
        invoices, missing = jde_left_out(store, chosen["id"])
        left_out = (len(missing), len(invoices))
    totals = store.batch_totals()
    # What an undone batch held, from the audit trail (0.5 s at 100,000 invoices): read only when one is shown (D-166).
    exported = store.exported_totals() if any(not totals.get(b["id"]) for b in batches) else {}

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
            found = jde_vouchers(store, df)  # JD Edwards' voucher list: read as it comes, no column to pick
            reg["vouchers"] = registers.vouchers_said(found[1]) if found else ""
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
                "keying": b["format"] == jde_keying.FORMAT,  # typed by hand: on a sheet, not yet in the ERP
                "when": (b["created_at"] or "").replace("T", " ")[:16],
            }
            for b in batches
        ],
        exported_count=sum(b["invoices"] for b in live),
        fmt=fmt,
        formats={f: SHORT_FORMATS[f] for f in shown_formats(store, fmt) if f in SHORT_FORMATS},
        format_help=exports.FORMATS,
        jde_format=jde.FORMAT,
        keying_format=jde_keying.FORMAT,
        problems=problems,
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
        csv_preview=layout_preview(store, current, ready),
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
    posted = sum(r["status"] == jde_post.POSTED for r in rows)
    # Its ZIP downloaded (D-134): posting it too asks for a person's word that the file was not loaded.
    downloads = [{**d, "at": d["at"].replace("T", " ")[:16]} for d in jde_post.downloads(store, chosen["id"])] if (
        shown) else []  # fmt: skip
    return {"shown": shown, "on": cfg.step2, "rows": rows,
            "checked": sum(r["status"] == jde_post.CHECKED for r in rows),
            # their G/L date moved into the open period by the dry run: the go-ahead confirms it (D-182)
            "moved": sum(r["status"] == jde_post.CHECKED and r["error"].startswith(jde.MOVED) for r in rows),
            "waiting": len(jde_post.waiting(store)), "done": all(r["status"] == jde_post.POSTED for r in rows) and
            bool(rows), "downloads": downloads, "posted": posted,
            "maybe": sum(r["locked"] for r in rows) - posted}  # fmt: skip


# --- Exporting ---------------------------------------------------------------------------------------------------


@router.post("/exports/create")
async def create_batch(request: Request) -> JSONResponse:
    """A new batch of the ticked invoices, in the chosen format (JD Edwards: only those that pass its checks)."""
    form = await posted_form(request)  # one field per invoice ticked
    return await run_heavy(request, _create_batch, request, form)  # on a thread: never the worker's loop (D-153)


@one_connection
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
    ticked = len(chosen)
    if fmt in JDE_CHECKED:
        chosen, _problems, blocking = jde_check(store, chosen, files=fmt == jde.FORMAT)
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
    said = f"Batch {batch} created with {ui.plural(count, 'invoice')}. Download it below."
    if fmt in JDE_CHECKED and ticked > len(chosen):  # ticked on a page drawn before one stopped being ready
        left = ticked - len(chosen)
        said += (f" {ui.plural(left, 'ticked invoice')} {'is' if left == 1 else 'are'} not ready for JD Edwards and "
                 f"{'stays' if left == 1 else 'stay'} in Ready to export: the list says why.")  # fmt: skip
    return done(request, f"/exports?made={batch}&fmt={fmt}&batch={batch}", flash_message(said, "ok", icon="ios_share"))


@router.get("/exports/jde/dry-run.txt")
@one_connection
def jde_dry_run(request: Request) -> Response:
    """What a JD Edwards batch of the invoices ready to export would load, row by row, and why any invoice is held
    back. Nothing is exported and no batch is made."""
    store = get_store(request)
    invoices = store.get_invoices([r["id"] for r in store.unexported_approved()])
    prior = jde.prior_records(store, invoices=invoices)  # only what has their numbers (D-156)
    report = jde.dry_run(invoices, jde.load(store), store.vendor_ids(), prior=prior,
                         batch="(next)", fx_rates=store.fx_rates())  # fmt: skip
    return download(jde.dry_run_text(report).encode("utf-8"), "ap_coder_jde_dry_run.txt", "text/plain; charset=utf-8")


def live_batch(store: Store, batch: int) -> dict[str, Any]:
    """Export batch ``batch``, to download. An undone batch holds no invoice any more (they are back in Ready to
    export, or in a later batch): its file would be empty, so it is refused."""
    known = store.export_batch(batch)
    if known is None:
        raise HTTPException(status_code=404, detail="There is no such export batch.")
    if known["undone_at"]:
        raise HTTPException(status_code=410, detail=f"Batch {batch} was undone: its invoices can be exported again.")
    return known


@router.get("/exports/batches/{batch:int}/download")
@router.post("/exports/batches/{batch:int}/download")
@one_connection
def download_batch(request: Request, batch: int, fmt: str = "") -> Response:
    """Batch ``batch`` as a file again: in ``fmt``, else in the format it went out in. The JD Edwards ZIP only by a
    POST from the page: its download is recorded and puts back the vouchers waiting in the queue (D-134), which a GET
    must not do, as another page of the site can make the browser send one (D-135)."""
    store = get_store(request)
    known = live_batch(store, batch)
    fmt = fmt or known["format"]
    if fmt not in exports.FORMATS:
        raise HTTPException(status_code=400, detail="Unknown export format.")
    if fmt == jde.FORMAT and request.method != "POST":
        raise HTTPException(status_code=405, detail="Download the JD Edwards file with its button on the Exports page.")
    if fmt == jde.FORMAT and (blocking := jde.settings_problems(jde.load(store))):
        # Z-files made with these settings cannot load (their own dry run says "NOTHING CAN LOAD"): say what to fix.
        detail = " ".join(f"{p} Set it in Settings → JD Edwards E1, then download the batch again." for p in blocking)
        raise HTTPException(status_code=409, detail=detail)
    if fmt == jde.FORMAT:  # who and when; posting the batch through the API too then asks if it was loaded (D-134)
        jde_post.file_downloaded(store, batch, current_reviewer())
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
    known = store.export_batch(batch)
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
    form = await posted_form(request)
    return await run_in_threadpool(_save_layout, request, form)


@one_connection
def _save_layout(request: Request, form: Any) -> JSONResponse:
    store = get_store(request)
    new, _incomplete, warnings = layout_from_form(form)
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
    form = await posted_form(request)
    return await run_in_threadpool(_preview_layout, request, form)


@one_connection
def _preview_layout(request: Request, form: Any) -> JSONResponse:
    store = get_store(request)
    new, _incomplete, warnings = layout_from_form(form)
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
    return await run_in_threadpool(_register_import, request, form)  # a large company's list: 600,000 rows


def jde_vouchers(store: Store, df: Any) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
    """JD Edwards' voucher list (Supplier Ledger Inquiry's grid, one row per pay item) read as it comes: (the
    register's vouchers, what was read), its suppliers named from the vendor master by their number; None when the
    file is any other list, whose columns are picked (B-2084)."""
    columns = registers.voucher_columns(list(df.columns))
    if not columns:
        return None
    names = {v["erp_id"]: v["display_name"] for v in store.master_vendors() if v.get("erp_id")}
    return registers.vouchers_from_pay_items(df.to_dict("records"), columns, names)


def _vouchers_import(request: Request, form: Any, token: str, rows: list[dict[str, Any]],
                     read: dict[str, Any]) -> JSONResponse:  # fmt: skip
    """JD Edwards' voucher list into the register, saying what was read."""
    store, said = get_store(request), registers.vouchers_said(read)
    if not rows:  # "Replace" would empty the list the duplicate checks look in (B-1405)
        return refuse(f"No voucher of this list can be kept: nothing was imported, the list is as it was. {said}")
    total = store.import_erp_vouchers(rows, replace_all=bool(form.get("replace")), actor=current_reviewer())
    drop_upload(token)
    text = f"{total:,} ERP invoices known. {said}"
    if repeats := store.erp_register_repeats():
        text += f" {ui.plural(repeats, 'bill')} entered more than once in the ERP: see the duplicate payment audit."
    return done(request, "/exports#register", flash_message(text, "ok", icon="receipt_long"))


@one_connection
def _register_import(request: Request, form: Any) -> JSONResponse:
    store = get_store(request)
    token, sheet = str(form.get("file") or ""), str(form.get("sheet") or "")
    try:
        _upload, df, _sheets = read_upload(token, sheet or None)
    except SheetError as exc:
        return refuse(str(exc))
    if df.empty:
        return refuse(NO_ROWS)
    if found := jde_vouchers(store, df):
        return _vouchers_import(request, form, token, *found)
    chosen = picked_columns(form, REGISTER_LABELS, list(df.columns), {})
    missing = [REGISTER_LABELS[f] for f in registers.REQUIRED if f not in chosen]
    if missing:
        return refuse("Pick the column for: " + ", ".join(missing))
    rows, skipped = registers.rows_from_records(df.to_dict("records"), chosen)
    if not rows:  # "Replace" would empty the list the duplicate checks look in (B-1405)
        return refuse(f"No row has a vendor, an invoice number and an amount in the columns picked ({skipped} "
                      "skipped): nothing was imported, the list is as it was. Check the columns, then import "
                      "again.")  # fmt: skip
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
async def duplicates_csv(request: Request) -> Response:
    """Every pair the duplicate payment audit finds, as a CSV: on the company server made by its background process
    (``downloads``, D-169)."""
    return await downloads.answer(request, "/exports/duplicates.csv", _duplicates_now)


def _duplicates_now(request: Request) -> Response:
    pairs = dupaudit.find(get_store(request))
    return download(dupaudit.to_csv(pairs), f"duplicate_audit_{dt.date.today()}.csv", "text/csv")


def duplicates_params(request: Request) -> tuple[dict[str, Any], str]:
    return {}, f"duplicate_audit_{dt.date.today()}.csv"


def write_duplicates(store: Store, params: dict[str, Any], out: BinaryIO, step: Any) -> None:
    step(0, 0, "Looking for bills paid twice…")
    out.write(dupaudit.to_csv(dupaudit.find(store)))


# --- JD Edwards through the API (D-104) -----------------------------------------------------------------------------

API_JOB = "jde-api"


def _api_job(request: Request, note: str, work: Any) -> JSONResponse:
    if not jobs(request).get(API_JOB).start(work, total=1, note=note):
        return refuse("JD Edwards is being sent something already: wait for it to finish.", status=409)
    return JSONResponse({"ok": True, "job": API_JOB, "status": f"/jobs/{API_JOB}"})


def _progress(job: Any) -> Any:
    """Each voucher's outcome as the job's progress (D-182): how many of the run's vouchers are done, and of them how
    many posted, are being checked (answered late), were refused and wait in the queue."""

    def step(found: dict[str, Any]) -> None:
        parts = [f"{found[k]} {label}" for k, label in (("posted", "posted"), ("unknown", "being checked"),
                 ("failed", "refused"), ("queued", "waiting"), ("checked", "held back")) if found.get(k)]  # fmt: skip
        job.step(found["done"], found["total"], f"{found['note']} {found['done']} of {found['total']}"
                 + (f": {', '.join(parts)}" if parts else ""))  # fmt: skip

    return step


def _api_messages(result: dict[str, Any]) -> list[dict[str, str]]:
    parts = [f"{result[k]} {label}" for k, label in (("posted", "posted"), ("failed", "refused by JD Edwards"),
             ("queued", "waiting in the queue"), ("unknown", "sent, being checked"),
             ("held", "held back for a new dry run")) if result.get(k)]  # fmt: skip
    out = [
        {
            "text": ("JD Edwards: " + ", ".join(parts) + ".") if parts else "Nothing was sent.",
            "tone": "ok" if not any(result.get(k) for k in ("failed", "queued", "unknown", "held")) else "warn",
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
        if found["jde"]:  # JD Edwards' proof not received: never "ready" in green (B-1209)
            return {"messages": [{"text": f"Dry run of batch {batch}: {ok} of {ui.plural(len(rows), 'voucher')} "
                                  "checked here only: JD Edwards' proof wasn't received "
                                  f"({found['jde']}). They can still be posted; JD Edwards checks each one then.",
                                  "tone": "warn"}]}  # fmt: skip
        return {"messages": [{"text": f"Dry run of batch {batch}: {ok} of {ui.plural(len(rows), 'voucher')} ready "
                              "to post.", "tone": "ok" if ok == len(rows) else "warn"}]}  # fmt: skip

    return _api_job(request, "Dry run through the JD Edwards API", work)


@router.post("/exports/batches/{batch:int}/jde-api/post")
def jde_api_post(request: Request, batch: int, not_loaded: str = Form(""), why: str = Form("")) -> JSONResponse:
    """The go-ahead (an Approver or Admin): the vouchers that passed the dry run, unchanged since, are posted. Once
    the batch's ZIP was downloaded, only with ``not_loaded`` ticked and ``why`` (what was checked) said (D-134)."""
    store = get_store(request)
    if not jde_ais.load(store).step2:
        return refuse("Step 2 of the JD Edwards API (vouchers) is off: an Admin switches it on in Settings → JD "
                      "Edwards E1 → API connection.")  # fmt: skip
    if not any(p["status"] == jde_post.CHECKED for p in jde_post.postings(store, batch)):
        return refuse("Run the dry run first: only the vouchers that passed it are posted.")
    from ap_coder.web.identity import person

    who = person()
    refused = store.posting_refusal(batch, who.user_id if who else None)  # separation of duties (D-109)
    if refused:
        return refuse(refused, 403)
    downloaded, why = jde_post.downloads(store, batch), " ".join(why.split())
    if downloaded and not not_loaded:
        return refuse(jde_post.downloaded_message(batch, downloaded))
    if downloaded and not why:
        return refuse("Say what you checked in JD Edwards: it is kept with the go-ahead in the audit trail.")
    actor = current_reviewer()

    def work(job: Any) -> dict[str, Any]:
        job.step(0, 1, "Posting the vouchers to JD Edwards…")
        try:
            return {"messages": _api_messages(jde_post.post(store, batch, actor=actor, not_loaded=why,
                                                            progress=_progress(job)))}  # fmt: skip
        except (ValueError, jde_ais.AisError) as exc:
            return {"messages": [{"text": str(exc), "tone": "warn"}]}

    return _api_job(request, "Posting to JD Edwards", work)


@router.post("/exports/batches/{batch:int}/jde-api/release")
def jde_api_release(request: Request, batch: int, invoice_id: int = Form(0), note: str = Form("")) -> JSONResponse:
    """An Admin's "I've checked in JD Edwards: release this voucher" (D-107): a voucher held here (JD Edwards answered
    EXISTS for another invoice, say) is marked not sent, with the Admin's name and note in the history."""
    try:
        jde_post.release(get_store(request), batch, invoice_id, current_reviewer(), note)
    except ValueError as exc:
        return refuse(str(exc))
    return done(request, f"/exports?batch={batch}#jde-api", flash_message(
        f"AP Coder #{invoice_id} released: it is not sent, so the batch files hold it again and the batch can be "
        "undone.", "ok", icon="lock_open"))  # fmt: skip


@router.post("/exports/jde-api/retry")
def jde_api_retry(request: Request) -> JSONResponse:
    """The queue now: each voucher waiting is looked up in JD Edwards by its key first, then sent if it isn't there."""
    store = get_store(request)
    if not jde_post.waiting(store):
        return message("Nothing is waiting for JD Edwards.", "info")
    actor = current_reviewer()

    def work(job: Any) -> dict[str, Any]:
        job.step(0, 1, "Sending the queue to JD Edwards…")
        return {"messages": _api_messages(jde_post.retry_queue(store, actor=actor, progress=_progress(job)))}

    return _api_job(request, "Sending the JD Edwards queue", work)
