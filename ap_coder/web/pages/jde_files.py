"""JD Edwards files (GL accounts & tax's neighbour, under Setup in Simple screens): JD Edwards' own exports loaded in
one place, in the order they depend on each other.

1. the chart of accounts and 2. the business units (``ops/chart_cmd.import_files``, the reader's own size limit:
   ``core/chart_file``), with the chart's settings a person must set (``Store.save_chart_settings``);
3. the suppliers (the vendor master's import, as on Vendors: ``coding/vendors.master_rows``,
   ``Store.import_vendor_master``);
4. last year's coding, the Transaction Journal (``ops/journal_cmd``: ``look``, then ``load``; ``forget``);
5. the vouchers already in JD Edwards (the voucher list, as on Exports: ``erp/registers``,
   ``Store.import_erp_vouchers``).

Nothing is read here: each card calls the importer the command line and the other pages call, and shows what it
says. An importer that can look before it keeps (the suppliers, the journal, the voucher list) shows its summary
first, then "Load"; the chart and the business units are replaced whole by their file, their summary shown after.
Two things the files cannot say are offered with one click each, never set alone: the meals accounts (the accounts
whose names read like meals: ``_meals``) and JD Edwards' company number (the one company of the business units:
``_company_offer``). In server mode loading is an Admin's (web/roles.py).
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

from fastapi import File, Form, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse

from ap_coder.coding.vendors import master_columns, master_rows
from ap_coder.core import chart, ui
from ap_coder.core.reference_data import reads_like_meals
from ap_coder.core.store import Store
from ap_coder.erp import jde, registers
from ap_coder.erp.journal import looks_like_journal
from ap_coder.ops import chart_cmd, journal_cmd
from ap_coder.web.core import current_store, done, get_store, one_connection, page_router, refuse, render, reviewer
from ap_coder.web.files import NO_ROWS, TOO_BIG, SheetError, drop_upload, read_capped, read_upload, receive_sheet
from ap_coder.web.pages import learning
from ap_coder.web.pages.exports import jde_vouchers
from ap_coder.web.pages.vendors import MASTER_LABELS

router = page_router()  # each plain handler: one connection a request

HERE = "/jde-files"
SAID_KIND = "jde-files-said"  # what a load said, kept for the page that follows it
SAID_TTL = 3600
ORDER = ("Load them in this order: the chart of accounts and the business units first, because the suppliers, last "
         "year's coding and the vouchers are checked against them; then the suppliers; then last year's coding; "
         "then the vouchers already in JD Edwards.")  # fmt: skip
NOT_XLSX = "Upload the export as JD Edwards saves it, an Excel workbook (.xlsx)."
TICK_ANYWAY = "Or tick “Load it anyway” under the file and load it again."
NOT_VOUCHERS = ("This file is not JD Edwards' voucher list (the supplier ledger inquiry's grid, a row for each pay "
                "item): nothing was loaded. Another list of invoices is loaded on Exports, under Invoices already in "
                "the ERP, where its columns are picked.")  # fmt: skip
NO_NAME_COLUMN = ("No column of this file is a supplier name, so it was not loaded. Pick its columns on the "
                  "Vendors page.")  # fmt: skip
NO_NUMBER_COLUMN = ("This file has no supplier number column (“Address Number” in JD Edwards' supplier master), so "
                    "it was not loaded: the number is what tells one supplier from another, two of one name too. "
                    "Add the column to the grid in JD Edwards and export again.")  # fmt: skip


def _url(card: str, **query: str) -> str:
    asked = "&".join(f"{key}={value}" for key, value in query.items() if value)
    return f"{HERE}{'?' + asked if asked else ''}#{card}"


def _said(request: Request, card: str, lines: list[str]) -> JSONResponse:
    """Go back to ``card`` showing what its load said, a line each."""
    kept = json.dumps({"card": card, "lines": lines}).encode("utf-8")
    return done(request, _url(card, said=current_store().keep_ephemeral(SAID_KIND, kept, SAID_TTL, keep=8)))


def _last(store: Store, action: str, table: str = "") -> dict[str, str] | None:
    """When ``action`` was last recorded (for ``table``, an ``accounts_imported``), and by whom."""
    for event in store.events(actions=[action], limit=50):
        if not table or event["detail"].get("table") == table:
            return {"when": str(event["created_at"]).replace("T", " ")[:16], "by": event["actor"] or ""}
    return None


def _suppliers_problem(empty: bool, columns: dict[str, str]) -> str:
    """Why an uploaded supplier list is not loaded here ("" when it can be): no row, no name, or no supplier number,
    which is the supplier's identity in JD Edwards (B-2275)."""
    if empty or "vendor_name" not in columns:
        return NO_ROWS if empty else NO_NAME_COLUMN
    return "" if "erp_id" in columns else NO_NUMBER_COLUMN


def _suppliers_look(token: str) -> dict[str, Any]:
    """The uploaded supplier list as it would be loaded, nothing written: its rows, the columns found by their
    names and the ones that were not."""
    try:
        upload, df, _sheets = read_upload(token)
    except SheetError as exc:
        return {"problem": str(exc)}
    columns = master_columns(list(df.columns))
    rows = master_rows(df.to_dict("records"), columns) if "vendor_name" in columns else []
    return {
        "token": token, "name": upload.name, "rows": len(df), "kept": len(rows), "left_out": len(df) - len(rows),
        "found": [(label, columns[field]) for field, label in MASTER_LABELS.items() if field in columns],
        "missing": [label for field, label in MASTER_LABELS.items() if field not in columns],
        "problem": _suppliers_problem(df.empty, columns),
    }  # fmt: skip


def _vouchers_look(store: Store, token: str) -> dict[str, Any]:
    """The uploaded voucher list as it would be loaded, nothing written (``registers.vouchers_said``)."""
    try:
        upload, df, _sheets = read_upload(token)
    except SheetError as exc:
        return {"problem": str(exc)}
    found = None if df.empty else jde_vouchers(store, df)
    if found is None:
        return {"token": token, "name": upload.name, "problem": NO_ROWS if df.empty else NOT_VOUCHERS, "other": True}
    return {"token": token, "name": upload.name, "said": registers.vouchers_said(found[1]), "kept": len(found[0]),
            "problem": "" if found[0] else "No voucher of this list can be kept."}  # fmt: skip


def _meals(store: Store) -> dict[str, Any]:
    """The meals accounts of the GL accounts: how many are ticked and, when none is, the accounts whose names read
    like meals (``reference_data.reads_like_meals``), offered for the tick: a chart of accounts does not say which
    accounts are meals, and half the GST/HST of a line coded to one that is not ticked is claimed in full
    (B-2277)."""
    accounts = store.list_accounts("gl_accounts")
    ticked = sum(1 for row in accounts if row["meals"])
    return {"ticked": ticked,
            "like": [] if ticked else [row for row in accounts if reads_like_meals(row["description"])]}  # fmt: skip


def _company_offer(store: Store, units: list[chart.BuRow]) -> str:
    """The one company every business unit loaded belongs to, when JD Edwards' company number is still the one a
    new installation starts with and is another ("" otherwise: several companies, none, or a number set already)."""
    companies = {unit.company for unit in units if unit.company}
    settings = jde.load(store)
    if len(companies) != 1 or settings.company != jde.JdeSettings().company:
        return ""
    (company,) = companies
    return "" if company in (settings.company, settings.company_code) else company


def page_context(store: Store, q: Any) -> dict[str, Any]:
    rows, units, pairs = store.chart_accounts(), store.business_units(), store.account_pairs()
    settings = store.chart_settings()
    journals = learning._journal_context(store, q.get("journal"), q)
    said = current_store().ephemeral(SAID_KIND, q.get("said"))
    loaded = {
        "chart": len([r for r in rows if not r.is_header]),
        "units": len(units),
        "suppliers": len(store.master_vendors()),
        "journal": len(journals["journals"]),
        "vouchers": store.erp_register_count(),
    }
    names = {"chart": "Chart of accounts", "units": "Business units", "suppliers": "Suppliers",
             "journal": "Last year's coding", "vouchers": "Vouchers already in JD Edwards"}  # fmt: skip
    return {
        "order": ORDER,
        "checklist": [{"card": card, "name": name, "loaded": loaded[card]} for card, name in names.items()],
        "loaded": loaded,
        "last": {
            "chart": _last(store, "accounts_imported", "chart_accounts"),
            "units": _last(store, "accounts_imported", "business_units"),
            "suppliers": _last(store, "vendors_imported"),
            "vouchers": _last(store, "erp_register_imported"),
        },
        "chart_units": len({r.bu for r in rows}),
        "meals": _meals(store) if rows else None,
        "company": _company_offer(store, units),
        "tally": [line.strip() for line in chart_cmd.tally_lines(store)] if rows else [],
        "chart_settings": settings.saved(),
        "home_name": pairs.name_of(settings.home_business_unit) if pairs and settings.home_business_unit else "",
        "said": json.loads(said) if said else None,
        "suppliers": _suppliers_look(q["suppliers"]) if q.get("suppliers") else None,
        "vouchers": _vouchers_look(store, q["vouchers"]) if q.get("vouchers") else None,
        **journals,
    }


@router.get(HERE, response_class=HTMLResponse)
def jde_files_page(request: Request) -> HTMLResponse:
    return render(request, "jde_files.html", "jde_files", **page_context(get_store(request), request.query_params))


# --- 1 and 2: the chart of accounts and the business units ----------------------------------------------------------


async def _workbook(file: UploadFile) -> tuple[bytes | None, str]:
    """An uploaded export's bytes, or (None, why not). The 50 MB of any upload on disk; how far it may unzip is
    the reader's to say (a chart's sheet unzips to 104 MB: ``core/chart_file``'s own limit, D-209)."""
    if not (file.filename or "").lower().endswith(".xlsx"):
        return None, NOT_XLSX
    content = await read_capped(file)
    return (None, TOO_BIG) if content is None else (content, "")


@router.post(HERE + "/chart")
async def chart_load(request: Request, file: UploadFile = File(...), anyway: str = Form("")) -> JSONResponse:  # noqa: B008
    """``anyway``: load a chart exported without its posting edit or subsidiary column (refused otherwise)."""
    content, problem = await _workbook(file)
    if content is None:
        return refuse(problem)
    # (a chart of 15,000 rows: 10 to 20 s)
    return await run_in_threadpool(_chart_import, request, "chart", content, bool(anyway))


@router.post(HERE + "/units")
async def units_load(request: Request, file: UploadFile = File(...)) -> JSONResponse:  # noqa: B008
    content, problem = await _workbook(file)
    if content is None:
        return refuse(problem)
    return await run_in_threadpool(_chart_import, request, "units", content)


@one_connection
def _chart_import(request: Request, card: str, content: bytes, anyway: bool = False) -> JSONResponse:
    given = {"chart_source": content} if card == "chart" else {"units_source": content}
    try:
        said = chart_cmd.import_files(get_store(request), **given, actor=reviewer(), anyway=anyway)
    except chart.ColumnsMissing as exc:
        return refuse(f"Not loaded: {exc} {TICK_ANYWAY}")
    except ValueError as exc:  # (a ChartError too: the file cannot be read as what it should be)
        return refuse(f"Not loaded: {exc}")
    return _said(request, card, said)


@router.post(HERE + "/chart/settings")
def chart_settings_save(request: Request, never_offered: str = Form(""), skip_words: str = Form(""),
                        home_business_unit: str = Form("")) -> JSONResponse:  # fmt: skip
    """The chart's settings a person sets: the accounts never offered, the words that retire a business unit or a
    supplier, the home business unit (``chart settings`` on the command line)."""
    store = get_store(request)
    typed = {"never_offered": never_offered, "skip_words": skip_words, "home_business_unit": home_business_unit}
    wanted = chart.ChartSettings.read({**store.chart_settings().saved(), **typed})
    pairs, home = store.account_pairs(), wanted.home_business_unit
    if home and pairs is not None and not pairs.has_business_unit(home):
        return refuse(f"There is no business unit {home} in the chart of accounts or the business unit master. "
                      "Nothing was changed.")  # fmt: skip
    try:
        changed = store.save_chart_settings(wanted, actor=reviewer())
    except ValueError as exc:
        return refuse(f"Not saved: {exc}")
    if not changed:
        return done(request, _url("chart"), "Nothing changed.")
    return _said(request, "chart", ["The chart's settings were saved.", *chart_cmd.settings_lines(store)[1:4]])


@router.post(HERE + "/meals")
def meals_mark(request: Request, code: list[str] = Form([])) -> JSONResponse:  # noqa: B008
    """Tick the accounts the page named as meals accounts (each can be unticked on GL accounts & tax)."""
    try:
        ticked = get_store(request).mark_meals_accounts(code, actor=reviewer())
    except ValueError as exc:
        return refuse(str(exc))
    return done(request, _url("checklist"), f"{ui.plural(len(ticked), 'account')} ticked as meals accounts: half "
                "the GST/HST of a line coded to one is claimable.")  # fmt: skip


@router.post(HERE + "/company")
def company_use(request: Request) -> JSONResponse:
    """Make the one company of the business units JD Edwards' company number (Settings → JD Edwards E1)."""
    store = get_store(request)
    company = _company_offer(store, store.business_units())
    wanted = dataclasses.replace(jde.load(store), company=company)
    if not company or jde.settings_problems(wanted):
        return refuse("The business units loaded do not give one company number to use. Set it in Settings, under "
                      "JD Edwards E1.")  # fmt: skip
    jde.save(store, wanted, actor=reviewer())
    return _said(request, "units", [f"JD Edwards' company number is now {company}, the company of the business "
                                    "units loaded: vouchers and the keying sheet carry it. It is changed in "
                                    "Settings, under JD Edwards E1."])  # fmt: skip


# --- 3: the suppliers -----------------------------------------------------------------------------------------------


@router.post(HERE + "/suppliers/upload")
async def suppliers_upload(request: Request, file: UploadFile = File(...)) -> JSONResponse:  # noqa: B008
    token, problem = await receive_sheet(file)
    if problem:
        return refuse(problem)
    return done(request, _url("suppliers", suppliers=token))


@router.post(HERE + "/suppliers/load")
async def suppliers_load(request: Request, token: str = Form("")) -> JSONResponse:
    return await run_in_threadpool(_suppliers_load, request, token)  # a large company's list: 20,000 vendors


@one_connection
def _suppliers_load(request: Request, token: str) -> JSONResponse:
    try:
        _upload, df, _sheets = read_upload(token)
    except SheetError as exc:
        return refuse(str(exc))
    columns = master_columns(list(df.columns))
    if problem := _suppliers_problem(df.empty, columns):
        return refuse(problem)
    result = get_store(request).import_vendor_master(master_rows(df.to_dict("records"), columns), actor=reviewer())
    drop_upload(token)
    said = [f"Suppliers: {result['added']:,} added, {result['updated']:,} updated."]
    if dupes := result["duplicates"]:
        said.append(f"{ui.plural(len(dupes), 'supplier')} {'appears' if len(dupes) == 1 else 'appear'} more than "
                    "once in the file under similar names (merged here; one on hold keeps the supplier on hold): "
                    + "; ".join(" / ".join(names) for names in dupes[:8]) + ".")  # fmt: skip
    return _said(request, "suppliers", said)


# --- 4: last year's coding (the Transaction Journal) ----------------------------------------------------------------


@router.post(HERE + "/journal/upload")
async def journal_upload(request: Request, file: UploadFile = File(...)) -> JSONResponse:  # noqa: B008
    name = Path(file.filename or "journal.csv").name
    content = await read_capped(file)
    if content is None:
        return refuse(TOO_BIG)
    if not looks_like_journal(content):
        return refuse(journal_cmd.NOT_A_JOURNAL)
    return done(request, _url("journal", journal=learning.keep_journal(name, content)))


@router.post(HERE + "/journal/load")
async def journal_load(request: Request, token: str = Form(""), anyway: str = Form("")) -> JSONResponse:
    return await run_in_threadpool(_journal_load, request, token, bool(anyway))


@one_connection
def _journal_load(request: Request, token: str, anyway: bool) -> JSONResponse:
    """Load the uploaded journal as its summary showed it (the payable and tax objects saved, or worked out from
    the file; Learning has the boxes to change them)."""
    kept = learning.get_journal(token)
    if kept is None:
        return refuse("That upload has expired. Upload the file again.")
    name, data = kept
    outcome, said = journal_cmd.load(get_store(request), data, name, anyway=anyway, actor=reviewer())
    if outcome in (journal_cmd.REFUSED, journal_cmd.DIFFERS):
        return refuse(said[-1])
    current_store().drop_ephemeral(learning.JOURNAL_KIND, token)
    return _said(request, "journal", [line.strip() for line in said])


@router.post(HERE + "/journal/forget")
def journal_forget(request: Request, file_id: str = Form(...)) -> JSONResponse:
    forgotten, said = journal_cmd.forget(get_store(request), file_id, actor=reviewer())
    if not forgotten:
        return refuse("That journal is not loaded any more.")
    return done(request, _url("journal"), said)


# --- 5: the vouchers already in JD Edwards --------------------------------------------------------------------------


@router.post(HERE + "/vouchers/upload")
async def vouchers_upload(request: Request, file: UploadFile = File(...)) -> JSONResponse:  # noqa: B008
    token, problem = await receive_sheet(file)
    if problem:
        return refuse(problem)
    return done(request, _url("vouchers", vouchers=token))


@router.post(HERE + "/vouchers/load")
async def vouchers_load(request: Request, token: str = Form(""), replace: str = Form("")) -> JSONResponse:
    return await run_in_threadpool(_vouchers_load, request, token, bool(replace))  # a large list: 600,000 rows


@one_connection
def _vouchers_load(request: Request, token: str, replace: bool) -> JSONResponse:
    store = get_store(request)
    try:
        _upload, df, _sheets = read_upload(token)
    except SheetError as exc:
        return refuse(str(exc))
    found = None if df.empty else jde_vouchers(store, df)
    if found is None:
        return refuse(NO_ROWS if df.empty else NOT_VOUCHERS)
    rows, read = found
    said = registers.vouchers_said(read)
    if not rows:  # "Replace" would empty the list the duplicate checks look in (B-1405)
        return refuse(f"No voucher of this list can be kept: nothing was loaded, the list is as it was. {said}")
    total = store.import_erp_vouchers(rows, replace_all=replace, actor=reviewer())
    drop_upload(token)
    return _said(request, "vouchers", [said, f"{ui.plural(total, 'voucher')} of JD Edwards known now."])
