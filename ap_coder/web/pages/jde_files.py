"""JD Edwards files (GL accounts & tax's neighbour, under Setup in Simple screens): JD Edwards' own exports loaded in
one place, in the order they depend on each other.

1. the chart of accounts and 2. the business units (``ops/chart_cmd.import_files``, the reader's own size limit:
   ``core/chart_file``), with the chart's settings a person must set (``Store.save_chart_settings``);
3. the suppliers, either of two exports told apart by their column names: the supplier master's grid (the vendor
   master's import, as on Vendors: ``coding/vendors.master_rows``, ``Store.import_vendor_master``), which gives the
   payment holds, and the vendor information list (``coding/vendor_info``, ``Store.import_vendor_info``, D-219),
   which gives the tax IDs, currencies, terms and addresses. Each goes to the supplier of its number, and neither
   removes what only the other gives;
4. last year's coding, the Transaction Journal (``ops/journal_cmd``: ``look``, then ``load``; ``forget``);
5. the vouchers already in JD Edwards (the voucher list, as on Exports: ``erp/registers``,
   ``Store.import_erp_vouchers``).

Nothing is read here: each card calls the importer the command line and the other pages call, and shows what it
says. Every card looks before it keeps (D-234): the file is uploaded and shown first (its name and rows, each column
of the report found as which header or missing, its other columns and first rows: ``erp/import_specs``), and "Load"
is offered only when it is that report with every column it needs (there is no "load it anyway"); a file that is
another report this page loads says which card it goes on. Each card says which JD Edwards report to export and its
columns, and gives a blank template of them. The chart and the business units are replaced whole by their file.
Two things the files cannot say are offered with one click each, never set alone: the meals accounts (the accounts
whose names read like meals: ``_meals``) and JD Edwards' company number (the one company of the business units:
``_company_offer``). In server mode loading is an Admin's (web/roles.py).
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

from fastapi import File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, Response

from ap_coder.coding import vendor_info
from ap_coder.coding.vendors import master_columns, master_rows
from ap_coder.core import chart, chart_file, ui
from ap_coder.core.reference_data import reads_like_meals
from ap_coder.core.store import Store
from ap_coder.core.store.vendor_master import name_marker
from ap_coder.erp import import_specs, jde_settings, registers
from ap_coder.erp.import_specs import SPECS, ImportSpec
from ap_coder.erp.journal import looks_like_journal
from ap_coder.ops import chart_cmd, journal_cmd
from ap_coder.web.core import current_store, done, get_store, one_connection, page_router, refuse, render, reviewer
from ap_coder.web.files import (
    NO_ROWS,
    TOO_BIG,
    SheetError,
    download,
    drop_upload,
    get_upload,
    keep_upload,
    read_capped,
    read_upload,
    receive_sheet,
)
from ap_coder.web.pages import learning
from ap_coder.web.pages.exports import jde_vouchers

router = page_router()  # each plain handler: one connection a request

HERE = "/jde-files"
SAID_KIND = "jde-files-said"  # what a load said, kept for the page that follows it
SAID_TTL = 3600
INFO_KIND = "jde-vendor-info"  # the vendor information list as read at its upload, kept for its summary and its load
LOOK_KIND = "jde-files-look"  # a chart or business unit file's first rows and count as read at its upload
ORDER = ("Load them in this order: the chart of accounts and the business units first, because the suppliers, last "
         "year's coding and the vouchers are checked against them; then the suppliers; then last year's coding; "
         "then the vouchers already in JD Edwards.")  # fmt: skip
# Each card: its title, and the reports loaded on it (``import_specs.SPECS``), in the order the page names them.
CARDS = {
    "chart": ("Chart of accounts", ("chart",)),
    "units": ("Business units", ("units",)),
    "suppliers": ("Suppliers", ("supplier_master", "vendor_info")),
    "journal": ("Last year's coding", ("journal",)),
    "vouchers": ("Vouchers already in JD Edwards", ("vouchers",)),
}
NOT_XLSX = "Upload the export as JD Edwards saves it, an Excel workbook (.xlsx)."
OTHER_INVOICES = ("Another list of invoices is loaded on Exports, under Invoices already in the ERP, where its "
                  "columns are picked.")  # fmt: skip
OTHER_SUPPLIERS = "Another ERP's supplier list is loaded on Vendors, where its columns are picked."
GONE = "That upload has expired. Upload the file again."


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


# --- What an uploaded file is, shown before it is loaded ------------------------------------------------------------


def _where(spec: ImportSpec | None) -> str:
    """The card a report is loaded on, as the page numbers it ("1. Chart of accounts")."""
    for n, (title, keys) in enumerate(CARDS.values(), start=1):
        if spec is not None and spec.key in keys:
            return f"{n}. {title}"
    return ""


def _cells(row: Any) -> list[str]:
    return ["" if cell is None else str(cell).strip() for cell in row]


def _judge(card: str, headers: list[str]) -> dict[str, Any]:
    """A file's column names against the card's reports: which one it is (``import_specs.identify``), each of its
    columns found as which header or missing, and why it is not loaded ("" when it can be): a column it needs is
    missing, or it is another report of the page, named with the card it goes on."""
    own = [SPECS[key] for key in CARDS[card][1]]
    known = import_specs.identify(headers)
    spec = known if known in own else own[0]
    belongs = known if known is not None and known not in own else None
    result = import_specs.check(spec, headers)
    return {"spec": spec, "check": result, "belongs": belongs, "where": _where(belongs),
            "problem": result.problem(belongs, _where(belongs))}  # fmt: skip


def _look(card: str, token: str, name: str, headers: list[str], sample: dict[str, Any] | None = None,
          count: int | None = None) -> dict[str, Any]:  # fmt: skip
    """What the card shows of an uploaded file before anything is kept: its name and rows (``count``), the header
    check (``_judge``) and its first rows under the columns the report uses (``sample``: ``import_specs.preview``)."""
    judged = _judge(card, headers)
    return {"card": card, "token": token, "name": name, "count": count, "sample": sample, **judged}


def _sample(df: Any, used: Any) -> dict[str, Any]:
    columns = [str(c) for c in df.columns]
    return import_specs.preview([columns, *df.head(import_specs.PREVIEW_ROWS).values.tolist()], 0, used)


def _read_head(content: bytes, name: str) -> dict[str, Any]:
    """A chart or business unit export's first rows (its title rows, its column names and the rows under them) and
    how many rows it has with a cell filled, read a row at a time by the chart's reader (its own bounds:
    ``core/chart_file``). ``ChartError`` in plain words for a file that is no workbook it can read."""
    rows: list[list[str]] = []
    count = 0
    for row in chart_file.read_rows(content, name):
        cells = _cells(row)
        if not any(cells):
            continue
        count += 1
        if len(rows) < chart_file.HEADER_ROWS + import_specs.PREVIEW_ROWS:
            rows.append(cells[: chart_file.COLUMNS_MOST])
    return {"name": name, "rows": rows, "count": count}


def _export_look(card: str, token: str) -> dict[str, Any] | None:
    """The uploaded chart or business unit file as its card shows it (None when the upload has expired)."""
    kept = current_store().ephemeral(LOOK_KIND, token)
    if kept is None:
        return None
    head = json.loads(kept)
    rows = head["rows"]
    at = import_specs.header_row(rows[: chart_file.HEADER_ROWS], [SPECS[key] for key in import_specs.JDE_REPORTS])
    look = _look(card, token, head["name"], rows[at] if rows else [], count=max(head["count"] - at - 1, 0))
    look["sample"] = import_specs.preview(rows, at, look["check"].found.values()) if rows else None
    if not look["problem"] and not look["count"]:
        look["problem"] = NO_ROWS
    return look


def _keep_vendor_info(name: str, content: bytes) -> tuple[str, str]:
    """(token, "") of an uploaded workbook that is the vendor information list, read once (20,000 suppliers: about
    5 s) and kept as read for its summary and its load, with its column names and first rows; ("", why) when its
    rows cannot be read; ("", "") for any other file."""
    try:
        read = vendor_info.read(content, name)
    except chart.ChartError as exc:
        return "", str(exc)
    if read is None:
        return "", ""
    head = []
    for row in chart_file.read_rows(content, name):
        head.append(_cells(row))
        if len(head) > import_specs.PREVIEW_ROWS:
            break
    read["head"] = head
    kept = json.dumps(read, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return current_store().keep_ephemeral(INFO_KIND, kept, SAID_TTL, keep=2), ""


def _vendor_info_look(store: Store, token: str, read: dict[str, Any]) -> dict[str, Any]:
    """The vendor information list as it would be loaded, nothing written (``vendor_info.said``)."""
    words = store.supplier_markers()
    known = {str(number).strip().lstrip("0") for _key, number in store.master_vendor_ids() if number}
    marked = sum(1 for row in read["rows"] if name_marker(row["vendor_name"], words)[1])
    look = _look(
        "suppliers", token, read["name"], read["head"][0], count=len(read["rows"]) + sum(read["left"].values())
    )
    look["sample"] = import_specs.preview(read["head"], 0, look["check"].found.values())
    return {**look, "info": True, "kept": len(read["rows"]), "said": vendor_info.said(read, known, marked),
            "problem": look["problem"] or ("" if read["rows"] else NO_ROWS)}  # fmt: skip


def _suppliers_look(store: Store, token: str) -> dict[str, Any]:
    """The uploaded supplier list as it would be loaded, nothing written: its rows, the columns found by their
    names and the ones that were not; the vendor information list, what it holds (``_vendor_info_look``)."""
    kept = current_store().ephemeral(INFO_KIND, token)
    if kept is not None:
        return _vendor_info_look(store, token, json.loads(kept))
    try:
        upload, df, _sheets = read_upload(token)
    except SheetError as exc:
        return {"problem": str(exc)}
    look = _look("suppliers", token, upload.name, [str(c) for c in df.columns], count=len(df))
    look["sample"] = _sample(df, look["check"].found.values())
    problem = NO_ROWS if df.empty else _master_problem(look)
    rows = [] if problem else master_rows(df.to_dict("records"), master_columns(list(df.columns)))
    return {**look, "kept": len(rows), "left_out": len(df) - len(rows), "other": not look["belongs"],
            "problem": problem + (" " + OTHER_SUPPLIERS if problem and not look["belongs"] else "")}  # fmt: skip


def _master_problem(judged: dict[str, Any]) -> str:
    """Why a supplier file read as a sheet is not loaded as the supplier master ("" when it is): the header check's
    words, or the vendor information list saved otherwise than JD Edwards saves it (it is read from its workbook)."""
    if judged["problem"] or judged["spec"].key == "supplier_master":
        return judged["problem"]
    return "Load the vendor information list as JD Edwards saves it, an Excel workbook (.xlsx): nothing was loaded."


def _vouchers_look(store: Store, token: str) -> dict[str, Any]:
    """The uploaded voucher list as it would be loaded, nothing written (``registers.vouchers_said``)."""
    try:
        upload, df, _sheets = read_upload(token)
    except SheetError as exc:
        return {"problem": str(exc)}
    look = _look("vouchers", token, upload.name, [str(c) for c in df.columns], count=len(df))
    look["sample"] = _sample(df, look["check"].found.values())
    found = None if df.empty or look["problem"] else jde_vouchers(store, df)
    if found is None:
        problem = NO_ROWS if df.empty else look["problem"] + ("" if look["belongs"] else " " + OTHER_INVOICES)
        return {**look, "problem": problem, "other": not look["belongs"]}
    return {**look, "said": registers.vouchers_said(found[1]), "kept": len(found[0]),
            "problem": "" if found[0] else "No voucher of this list can be kept."}  # fmt: skip


def _journal_look(look: dict[str, Any] | None) -> dict[str, Any] | None:
    """The uploaded journal's summary (``learning._journal_context``) with its header check and first lines."""
    kept = learning.get_journal(look["token"]) if look else None
    if look is None or kept is None:
        return look
    try:
        rows, at = import_specs.journal_head(kept[1])
    except ValueError:
        return look
    if at is None:  # (no header row: the report's first cell is its number, read by its standard positions)
        return {**look, "positions": True}
    judged = _judge("journal", [str(cell) for cell in rows[at]])
    return {**look, **judged, "problem": look["problem"] or judged["problem"],
            "sample": import_specs.journal_lines(rows, at)}  # fmt: skip


def _known_report(name: str, content: bytes) -> ImportSpec | None:
    """Which report of the page a file that is not the journal is, by its first rows ("" when none)."""
    try:
        if name.lower().endswith(".xlsx"):
            rows = []
            for row in chart_file.read_rows(content, name):
                rows.append(_cells(row))
                if len(rows) >= chart_file.HEADER_ROWS:
                    break
        else:
            rows = import_specs.journal_head(content)[0][: chart_file.HEADER_ROWS]
    except ValueError:  # (a ChartError too)
        return None
    for row in rows:
        if found := import_specs.identify(row):
            return found
    return None


def template_links(card: str) -> list[dict[str, str]]:
    return [{"key": key, "title": SPECS[key].title, "url": f"{HERE}/template/{key}"} for key in CARDS[card][1]
            if not SPECS[key].no_template]  # fmt: skip


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
    settings = jde_settings.load(store)
    if len(companies) != 1 or settings.company != jde_settings.JdeSettings().company:
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
    # Each opens its rows (pages/jde_loaded.py, D-221); the three a person may change say how many rows were.
    rows_of = {"chart": "accounts", "units": "business units", "suppliers": "suppliers", "journal": "lines",
               "vouchers": "vouchers"}  # fmt: skip
    edits = store.hand_edits()
    by_hand = {"chart": len(edits["chart_accounts"]), "units": len(edits["business_units"]),
               "suppliers": len(edits["vendors"])}  # fmt: skip
    looks = {
        "chart": _export_look("chart", q["chart"]) if q.get("chart") else None,
        "units": _export_look("units", q["units"]) if q.get("units") else None,
        "suppliers": _suppliers_look(store, q["suppliers"]) if q.get("suppliers") else None,
        "journal": _journal_look(journals.pop("journal")),
        "vouchers": _vouchers_look(store, q["vouchers"]) if q.get("vouchers") else None,
    }
    last = {
        "chart": _last(store, "accounts_imported", "chart_accounts"),
        "units": _last(store, "accounts_imported", "business_units"),
        "suppliers": _last(store, "vendors_imported"),
        "journal": max(
            ({"when": f["when"], "by": f.get("loaded_by") or ""} for f in journals["journals"]),
            key=lambda f: f["when"],
            default=None,
        ),
        "vouchers": _last(store, "erp_register_imported"),
    }
    tax_ids = store.vendor_info_count()
    return {
        "order": ORDER,
        "checklist": [
            {
                "card": card,
                "name": title,
                "loaded": loaded[card],
                "rows": rows_of[card],
                "by_hand": by_hand.get(card, 0),
                "last": last[card],
                "attention": _attention(card, looks[card], loaded[card], store, tax_ids),
            }
            for card, (title, _keys) in CARDS.items()
        ],  # fmt: skip
        "loaded": loaded,
        "last": last,
        "specs": {card: [SPECS[key] for key in keys] for card, (_title, keys) in CARDS.items()},
        "templates": {card: template_links(card) for card in CARDS},
        "chart_units": len({r.bu for r in rows}),
        "meals": _meals(store) if rows else None,
        "company": _company_offer(store, units),
        "tally": [line.strip() for line in chart_cmd.tally_lines(store)] if rows else [],
        "chart_settings": settings.saved(),
        "home_name": pairs.name_of(settings.home_business_unit) if pairs and settings.home_business_unit else "",
        "said": json.loads(said) if said else None,
        "looks": looks,
        "upload_gone": journals.pop("upload_gone", False)
        or any(q.get(card) and looks[card] is None for card in ("chart", "units")),
        "tax_ids": tax_ids,
        **journals,
    }


def _attention(card: str, look: dict[str, Any] | None, loaded: int, store: Store, tax_ids: int) -> str:
    """What the checklist says a card needs ("" nothing): a file chosen that cannot be loaded, a chart loaded
    without a column it needs (from the command line's --anyway), the vendor information list still to load."""
    if look and look.get("problem"):
        return "The file chosen cannot be loaded"
    if card == "chart" and loaded and store.chart_without():
        return "Loaded without a column it needs"
    if card == "suppliers" and loaded and not tax_ids:
        return "The vendor information list is still to load"
    return ""


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


@router.get(HERE + "/template/{key}")
def template_download(key: str) -> Response:
    """A blank template of one of the page's reports: its columns' names, as JD Edwards writes them."""
    spec = SPECS.get(key)
    if spec is None or not spec.jde:
        raise HTTPException(status_code=404, detail="No such template.")
    return any_template(key)


@router.get("/imports/template/{key}")
def any_template(key: str) -> Response:
    """A blank template of any import of the app (``import_specs.SPECS``) that has one: its columns' names, as the
    page that imports it reads them first."""
    spec = SPECS.get(key)
    if spec is None or spec.no_template:
        raise HTTPException(status_code=404, detail="No such template.")
    return download(*import_specs.template(spec))


@router.post(HERE + "/chart/upload")
async def chart_upload(request: Request, file: UploadFile = File(...)) -> JSONResponse:  # noqa: B008
    return await _export_upload(request, "chart", file)


@router.post(HERE + "/units/upload")
async def units_upload(request: Request, file: UploadFile = File(...)) -> JSONResponse:  # noqa: B008
    return await _export_upload(request, "units", file)


async def _export_upload(request: Request, card: str, file: UploadFile) -> JSONResponse:
    """Keep an uploaded chart or business unit file and show it on its card, nothing loaded yet."""
    content, problem = await _workbook(file)
    if content is None:
        return refuse(problem)
    return await run_in_threadpool(_keep_export, request, card, Path(file.filename or "").name, content)


@one_connection
def _keep_export(request: Request, card: str, name: str, content: bytes) -> JSONResponse:
    try:
        head = _read_head(content, name)
    except chart.ChartError as exc:
        return refuse(f"Not loaded: {exc}")
    head["upload"] = keep_upload(name, content)
    kept = json.dumps(head, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    token = current_store().keep_ephemeral(LOOK_KIND, kept, SAID_TTL, keep=4)
    return done(request, _url(card, **{card: token}))


@router.post(HERE + "/chart/load")
async def chart_load(request: Request, token: str = Form("")) -> JSONResponse:
    return await run_in_threadpool(_chart_import, request, "chart", token)  # (a chart of 15,000 rows: 10 to 20 s)


@router.post(HERE + "/units/load")
async def units_load(request: Request, token: str = Form("")) -> JSONResponse:
    return await run_in_threadpool(_chart_import, request, "units", token)


@one_connection
def _chart_import(request: Request, card: str, token: str) -> JSONResponse:
    """Load the uploaded chart or business unit file as its card showed it: only a file with every column the
    report needs (no "load it anyway" here; the command line keeps its --anyway)."""
    look, upload = _export_look(card, token), json.loads(current_store().ephemeral(LOOK_KIND, token) or b"{}")
    kept = get_upload(upload.get("upload"))
    if look is None or kept is None:
        return refuse(GONE)
    if look["problem"]:
        return refuse(look["problem"])
    given = {"chart_source": kept.content} if card == "chart" else {"units_source": kept.content}
    try:
        said = chart_cmd.import_files(get_store(request), **given, actor=reviewer())
    except ValueError as exc:  # (a ChartError too: the file cannot be read as what it should be)
        return refuse(f"Not loaded: {exc}")
    current_store().drop_ephemeral(LOOK_KIND, token)
    drop_upload(upload["upload"])
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
    wanted = dataclasses.replace(jde_settings.load(store), company=company)
    if not company or jde_settings.settings_problems(wanted):
        return refuse("The business units loaded do not give one company number to use. Set it in Settings, under "
                      "JD Edwards E1.")  # fmt: skip
    jde_settings.save(store, wanted, actor=reviewer())
    return _said(request, "units", [f"JD Edwards' company number is now {company}, the company of the business "
                                    "units loaded: vouchers and the keying sheet carry it. It is changed in "
                                    "Settings, under JD Edwards E1."])  # fmt: skip


# --- 3: the suppliers -----------------------------------------------------------------------------------------------


@router.post(HERE + "/suppliers/upload")
async def suppliers_upload(request: Request, file: UploadFile = File(...)) -> JSONResponse:  # noqa: B008
    """Either supplier export. The vendor information list (told by its column names) is read here, once, by the
    reader of JD Edwards' long exports: its sheet unzips to more than any other spreadsheet may."""
    name = Path(file.filename or "").name
    if name.lower().endswith(".xlsx"):
        content = await read_capped(file)
        if content is None:
            return refuse(TOO_BIG)
        token, problem = await run_in_threadpool(_keep_vendor_info, name, content)
        if token or problem:
            return refuse(problem) if problem else done(request, _url("suppliers", suppliers=token))
        await file.seek(0)
    token, problem = await receive_sheet(file)
    if problem:
        return refuse(problem)
    return done(request, _url("suppliers", suppliers=token))


@router.post(HERE + "/suppliers/load")
async def suppliers_load(request: Request, token: str = Form("")) -> JSONResponse:
    return await run_in_threadpool(_suppliers_load, request, token)  # a large company's list: 20,000 vendors


@one_connection
def _suppliers_load(request: Request, token: str) -> JSONResponse:
    """Load the uploaded supplier list as its summary showed it. Each row goes to the supplier of its number
    (``by_erp_id``): of two suppliers by one name, each keeps its own number, hold and tax ID whatever order a list
    is in (B-2298)."""
    store = get_store(request)
    kept = current_store().ephemeral(INFO_KIND, token)
    if kept is not None:
        rows = json.loads(kept)["rows"]
        if not rows:
            return refuse(NO_ROWS)
        result = store.import_vendor_info(vendor_info.master_rows(rows), rows, actor=reviewer())
        current_store().drop_ephemeral(INFO_KIND, token)
        said = [f"Suppliers: {result['added']:,} added, {result['updated']:,} updated, from the vendor information "
                f"list. Tax IDs kept for {sum(1 for row in rows if row['tax_id']):,} of them; the payment holds "
                "are as they were."]  # fmt: skip
    else:
        try:
            _upload, df, _sheets = read_upload(token)
        except SheetError as exc:
            return refuse(str(exc))
        if problem := NO_ROWS if df.empty else _master_problem(_judge("suppliers", [str(c) for c in df.columns])):
            return refuse(problem)
        columns = master_columns(list(df.columns))
        result = store.import_vendor_master(master_rows(df.to_dict("records"), columns), actor=reviewer(),
                                            by_erp_id=True)  # fmt: skip
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
        known = await run_in_threadpool(_known_report, name, content)
        return refuse(journal_cmd.NOT_A_JOURNAL + (f" It looks like {known.report}: load it under {_where(known)}."
                                                   if known else ""))  # fmt: skip
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
        return refuse(GONE)
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
    if df.empty:
        return refuse(NO_ROWS)
    if problem := _judge("vouchers", [str(c) for c in df.columns])["problem"]:
        return refuse(problem)
    found = jde_vouchers(store, df)
    if found is None:  # (the header check found its columns: the reader finds the same)
        return refuse(NO_ROWS)
    rows, read = found
    said = registers.vouchers_said(read)
    if not rows:  # "Replace" would empty the list the duplicate checks look in (B-1405)
        return refuse(f"No voucher of this list can be kept: nothing was loaded, the list is as it was. {said}")
    total = store.import_erp_vouchers(rows, replace_all=replace, actor=reviewer())
    drop_upload(token)
    return _said(request, "vouchers", [said, f"{ui.plural(total, 'voucher')} of JD Edwards known now."])
