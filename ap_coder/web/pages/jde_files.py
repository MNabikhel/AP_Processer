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
import secrets
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
from ap_coder.core.store.vendor_master import JDE_FILES, name_marker
from ap_coder.erp import import_specs, jde_settings, journal, registers
from ap_coder.erp.import_specs import SPECS, ImportSpec
from ap_coder.erp.journal import looks_like_journal
from ap_coder.ops import chart_cmd, journal_cmd
from ap_coder.web import files
from ap_coder.web.core import current_store, done, get_store, one_connection, page_router, refuse, render, reviewer
from ap_coder.web.files import (
    EMPTY,
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
from ap_coder.web.pages.exports import jde_vouchers, load_vouchers, named_vouchers, vouchers_left_said

router = page_router()  # each plain handler: one connection a request

HERE = "/jde-files"
SAID_KIND = "jde-files-said"  # what a load said, kept for the page that follows it
SAID_TTL = 3600
INFO_KIND = "jde-vendor-info"  # the vendor information list as read at its upload, kept for its summary and its load
LOOK_KIND = "jde-files-look"  # a chart or business unit file's first rows and count as read at its upload
VOUCHERS_KIND = "jde-files-vouchers"  # the voucher list as read at its upload, for its summary and its load
ORDER = ("Load them in this order: the chart of accounts and the business units first, because the suppliers, last "
         "year's coding and the vouchers are checked against them; then the suppliers; then last year's coding; "
         "then the invoices already in JD Edwards, its voucher list.")  # fmt: skip
# Each card: its title, and the reports loaded on it (``import_specs.SPECS``), in the order the page names them.
CARDS = {
    "chart": ("Chart of accounts", ("chart",)),
    "units": ("Business units", ("units",)),
    "suppliers": ("Suppliers", ("supplier_master", "vendor_info")),
    "journal": ("Last year's coding", ("journal",)),
    "vouchers": ("Invoices already in JD Edwards", ("vouchers",)),
}
NOT_XLSX = "Upload the export as JD Edwards saves it, an Excel workbook (.xlsx)."
GONE = files.GONE  # (one message for an upload no longer kept, on every page)
LOADED_KIND = "jde-files-loaded"  # an upload's token once its file is loaded: a second Load says so (B-2416)
LOADED = "That file is loaded already: the card says what it holds. To load another, upload it."
# A chart or business unit file being loaded (its lease, ``Store.take_lease``): the card says so, a second Load waits
# for none (B-2437). A load takes 10 to 20 s; a lease of a load that stopped half way ends after LOADING_MOST.
LOADING, LOADING_MOST = "jde-files-loading:", 600
BEING_LOADED = ("That file is being loaded now (a chart takes 10 to 20 seconds): nothing more to do. Open the page "
                "again in a moment to see what was loaded.")  # fmt: skip
JOURNAL_XLSX = ("This is the Transaction Journal saved as an Excel workbook: it is read as CSV. Open it in Excel, "
                "save it as CSV UTF-8 (File, Save As) and load that file.")  # fmt: skip


def _url(card: str, **query: str) -> str:
    asked = "&".join(f"{key}={value}" for key, value in query.items() if value)
    return f"{HERE}{'?' + asked if asked else ''}#{card}"


def _said(request: Request, card: str, lines: list[str]) -> JSONResponse:
    """Go back to ``card`` showing what its load said, a line each."""
    kept = json.dumps({"card": card, "lines": lines}).encode("utf-8")
    return done(request, _url(card, said=current_store().keep_ephemeral(SAID_KIND, kept, SAID_TTL, keep=8)))


def _loaded(token: str) -> None:
    """Remember that the upload ``token`` was loaded, for an hour: a second Load of it says so (``_gone``)."""
    current_store().keep_ephemeral(LOADED_KIND, b"1", SAID_TTL, token=token)


def _gone(token: str) -> str:
    """Why an upload is not here to load: it was loaded already, or it is no longer kept."""
    return LOADED if token and current_store().ephemeral(LOADED_KIND, token) is not None else GONE


def _last(store: Store, action: str, table: str = "") -> dict[str, str] | None:
    """When ``action`` was last recorded (for ``table``, an ``accounts_imported``), and by whom."""
    for event in store.events(actions=[action], limit=50):
        if not table or event["detail"].get("table") == table:
            return {"when": str(event["created_at"]).replace("T", " ")[:16], "by": event["actor"] or ""}
    return None


# --- What an uploaded file is, shown before it is loaded ------------------------------------------------------------


def _where(spec: ImportSpec | None) -> str:
    """The card a report is loaded on, as the page numbers it ("1. Chart of accounts": ``ImportSpec.card``)."""
    return spec.card if spec is not None else ""


def _cells(row: Any) -> list[str]:
    return ["" if cell is None else str(cell).strip() for cell in row]


def _judge(card: str, headers: list[str]) -> dict[str, Any]:
    """A file's column names against the card's reports: which one it is (``import_specs.identify``, else by
    JD Edwards' own headers whichever columns it lacks: ``recognise``), each of its columns found as which header or
    missing, and why it is not loaded ("" when it can be): a column it needs is missing, or it is another report of
    the page, named with the card it goes on, or a voucher's own grid. A file that is none of them is judged as the
    card's report with the most of its needed columns there."""
    own = [SPECS[key] for key in CARDS[card][1]]
    seen = import_specs.recognise(headers)
    known = import_specs.identify(headers) or SPECS.get(seen or "")
    checks = {spec.key: import_specs.check(spec, headers) for spec in own}
    most = max(own, key=lambda spec: len(spec.required) - len(checks[spec.key].missing))
    spec = known if known in own else most
    belongs = known if known is not None and known not in own else None
    result = checks[spec.key]
    problem = result.problem(belongs, _where(belongs))
    if seen in import_specs.VOUCHER_GRIDS:
        problem = import_specs.elsewhere(seen, card)
    return {"spec": spec, "check": result, "belongs": belongs, "where": _where(belongs), "problem": problem}


def _look(card: str, token: str, name: str, headers: list[str], sample: dict[str, Any] | None = None,
          count: int | None = None) -> dict[str, Any]:  # fmt: skip
    """What the card shows of an uploaded file before anything is kept: its name and rows (``count``), the header
    check (``_judge``) and its first rows under the columns the report uses (``sample``: ``import_specs.preview``)."""
    judged = _judge(card, headers)
    return {"card": card, "token": token, "name": name, "count": count, "sample": sample, **judged}


def _sample(df: Any, used: Any) -> dict[str, Any]:
    columns = [str(c) for c in df.columns]
    return import_specs.preview([columns, *df.head(import_specs.PREVIEW_ROWS).values.tolist()], 0, used)


def _read_head(content: bytes, name: str, store: Store | None = None) -> dict[str, Any]:
    """A chart or business unit export's first rows (its title rows, its column names and the rows under them) and
    how many rows it has with a cell filled, and in each column (``filled``: how many accounts a chart holds, its
    rows with an object account), read a row at a time by the chart's reader (its own bounds: ``core/chart_file``).
    With ``store``, a chart's GL accounts too, as its load would list them (``gl``: {code: description}, worked out
    in the same read: B-2438). ``ChartError`` in plain words for a file that is no workbook it can read."""
    rows: list[list[str]] = []
    count = 0
    filled: list[int] = []
    found: dict[str, str] | None = None
    at, picked = 0, []
    for number, row in enumerate(chart_file.read_rows(content, name), 1):
        if store is not None and found is None and number <= chart_file.HEADER_ROWS:
            try:  # (the chart reader's own way to its column names: ``chart_file._columns``)
                found = chart.chart_columns(["" if h is None else str(h) for h in row[: chart_file.COLUMNS_MOST]])
                headers = ["" if h is None else str(h) for h in row]
                at, kept = number, [(headers.index(column), column) for column in found.values()]
            except chart.ChartError:
                pass
        elif found is not None:
            picked.append({column: row[j] if j < len(row) else None for j, column in kept})
        cells = _cells(row)[: chart_file.COLUMNS_MOST]
        if not any(cells):
            continue
        count += 1
        filled += [0] * (len(cells) - len(filled))
        for j, cell in enumerate(cells):
            filled[j] += bool(cell)
        if len(rows) < chart_file.HEADER_ROWS + import_specs.PREVIEW_ROWS:
            rows.append(cells)
    head = {"name": name, "rows": rows, "count": count, "filled": filled}
    if found is not None:
        try:
            read = chart.chart_rows(picked, found, at)
        except chart.ChartError:  # (its load says why)
            return head
        accounts = chart.account_lists(read.rows, store.business_units(), store.chart_settings())[0]
        head["gl"] = {code: description for code, (description, _category) in accounts.items()}
    return head


def _gl_replaced(store: Store, look: dict[str, Any] | None) -> dict[str, int] | None:
    """What loading the chart previewed does to the GL accounts listed now (B-2438): how many leave the list
    (``removed``: a code only a list imported on GL accounts & tax had) and how many get the chart's description
    (``changed``); None with no chart to load."""
    gl = (look or {}).get("gl")
    if gl is None or look.get("problem"):
        return None
    listed = {row["code"]: row["description"] for row in store.list_accounts("gl_accounts")}
    changed = sum(1 for code, description in listed.items() if code in gl and gl[code] != description)
    return {"removed": sum(1 for code in listed if code not in gl), "changed": changed}


def _export_look(card: str, token: str) -> dict[str, Any] | None:
    """The uploaded chart or business unit file as its card shows it (None when the upload has expired); ``loading``:
    a Load of it is under way."""
    kept = current_store().ephemeral(LOOK_KIND, token)
    if kept is None:
        return None
    head = json.loads(kept)
    rows = head["rows"]
    at = import_specs.header_row(rows[: chart_file.HEADER_ROWS], [SPECS[key] for key in import_specs.JDE_REPORTS])
    look = _look(card, token, head["name"], rows[at] if rows else [], count=max(head["count"] - at - 1, 0))
    look["loading"] = _being_loaded(token)  # (a Load of it under way: the card says so, B-2437)
    look["gl"] = head.get("gl")  # (the GL accounts its load lists: ``_gl_replaced``)
    look["sample"] = import_specs.preview(rows, at, look["check"].found.values()) if rows else None
    if not rows:
        look["problem"] = EMPTY
    elif not look["problem"] and not look["count"]:
        look["problem"] = NO_ROWS
    elif look["problem"] and head.get("on_sheet"):
        look["problem"] += " " + _on_sheet(head["first_sheet"], head["on_sheet"], look["spec"])
    if card == "chart" and rows and (named := look["check"].found.get("object")) in rows[at]:
        column = rows[at].index(named)  # the accounts: the rows under the column names with an object account
        filled = head.get("filled") or []
        if column < len(filled):
            look["accounts"] = filled[column] - sum(1 for row in rows[: at + 1] if column < len(row) and row[column])
    return look


def _on_sheet(first: str, other: str, spec: ImportSpec) -> str:
    return (f"Only the workbook's first sheet (“{first}”) is read, and {spec.title.lower()} is on its sheet “{other}”: "
            "move that sheet to the front in Excel (or delete the others), save, and load the file again.")  # fmt: skip


def _report_sheet(content: bytes, name: str, card: str) -> tuple[str, str]:
    """(the first sheet's name, the name of a later sheet that is the card's report whole) of a workbook whose
    first sheet is not that report; ("", "") when no later sheet is (or the workbook has one sheet)."""
    own = [SPECS[key] for key in CARDS[card][1]]
    sheets = chart_file.sheet_heads(content, name)
    for title, rows in sheets[1:]:
        rows = [_cells(row) for row in rows]
        at = import_specs.header_row(rows, own) if rows else 0
        if rows and any(import_specs.check(spec, rows[at]).ok for spec in own):
            return sheets[0][0], title
    return "", ""


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
    for at, row in enumerate(chart_file.read_rows(content, name)):
        if at >= read["header_row"]:  # (under a grid's title rows)
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
    new = sum(1 for row in read["rows"] if row["erp_id"].lstrip("0") not in known)  # (as vendor_info.said counts)
    return {**look, "info": True, "kept": len(read["rows"]), "new": new, "said": vendor_info.said(read, known, marked),
            "problem": look["problem"] or ("" if read["rows"] else NO_ROWS)}  # fmt: skip


def _suppliers_look(store: Store, token: str) -> dict[str, Any]:
    """The uploaded supplier list as it would be loaded, nothing written: its rows, the columns found by their
    names and the ones that were not; the vendor information list, what it holds (``_vendor_info_look``)."""
    kept = current_store().ephemeral(INFO_KIND, token)
    if kept is not None:
        return _vendor_info_look(store, token, json.loads(kept))
    try:
        upload, df, sheets = _card_sheet("suppliers", token)
    except SheetError as exc:
        return {"problem": str(exc)}
    look = _look("suppliers", token, upload.name, [str(c) for c in df.columns], count=len(df))
    look["sample"] = _sample(df, look["check"].found.values())
    problem = NO_ROWS if df.empty and not look["problem"] else _master_problem(look, df, sheets)
    rows = [] if problem else master_rows(df.to_dict("records"), master_columns(list(df.columns)))
    # (the suppliers it would add: those of no supplier loaded now, by the import's own rules, nothing written)
    there = {key for key, _number in store.master_vendor_ids()}
    new = sum(1 for key in store._master_rows_merged(rows, True)[0] if key not in there) if rows else 0
    return {**look, "kept": len(rows), "left_out": len(df) - len(rows), "problem": problem, "new": new}


def _card_sheet(card: str, token: str) -> tuple[Any, Any, list[str]]:
    """The upload ``token`` read as the card's reports: the sheet that is one of them, its column names under its
    title rows (``files.read_upload``)."""
    return read_upload(token, specs=[SPECS[key] for key in CARDS[card][1]])


def _master_problem(judged: dict[str, Any], df: Any, sheets: list[str]) -> str:
    """Why a supplier file read as a sheet is not loaded as the supplier master ("" when it is): the header check's
    words, or the vendor information list saved otherwise than JD Edwards saves it (it is read from its workbook's
    first sheet)."""
    if judged["problem"] or judged["spec"].key == "supplier_master":
        return judged["problem"]
    if sheets and df.attrs.get("sheet", sheets[0]) != sheets[0]:
        return "Not loaded: " + _on_sheet(sheets[0], df.attrs["sheet"], judged["spec"])
    return "Load the vendor information list as JD Edwards saves it, an Excel workbook (.xlsx): nothing was loaded."


def _keep_vouchers(name: str, content: bytes) -> tuple[str, str]:
    """(token, "") of an uploaded voucher list, read once (a year's list as .xlsx: 8 s) and kept as read for its
    summary and its load (B-2441): its column names, rows and first rows, the pay items in the columns the voucher
    reader uses, and what it reads of them; ("", why) when the file cannot be read as a sheet."""
    try:
        df, _sheets = files.read_sheet(name, content, specs=[SPECS[key] for key in CARDS["vouchers"][1]])
    except SheetError as exc:
        return "", str(exc)
    headers = [str(c) for c in df.columns]
    found = None if df.empty or _judge("vouchers", headers)["problem"] else jde_vouchers(current_store(), df)
    columns = registers.voucher_columns(headers) if found else {}
    used = list(dict.fromkeys(columns.values()))
    head = df.head(import_specs.PREVIEW_ROWS).values.tolist()
    read = {"name": name, "headers": headers, "count": len(df), "columns": columns, "head": head,
            "items": df[used].values.tolist() if used else [],
            "said": registers.vouchers_said(found[1]) if found else "",
            "kept": len(found[0]) if found else 0}  # fmt: skip
    kept = json.dumps(read, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return current_store().keep_ephemeral(VOUCHERS_KIND, kept, SAID_TTL, keep=4), ""


def _kept_vouchers(token: str) -> dict[str, Any] | None:
    kept = current_store().ephemeral(VOUCHERS_KIND, token)
    return None if kept is None else json.loads(kept)


def _vouchers_look(store: Store, token: str) -> dict[str, Any]:
    """The uploaded voucher list as it would be loaded, nothing written, as read at its upload (``_keep_vouchers``:
    ``registers.vouchers_said``)."""
    read = _kept_vouchers(token)
    if read is None:
        return {"problem": GONE}
    look = _look("vouchers", token, read["name"], read["headers"], count=read["count"])
    look["sample"] = import_specs.preview([read["headers"], *read["head"]], 0, look["check"].found.values())
    if look["problem"] or not read["count"] or not read["columns"]:
        return {**look, "problem": look["problem"] or NO_ROWS}
    return {**look, "said": read["said"], "kept": read["kept"],
            "problem": "" if read["kept"] else "No voucher of this list can be kept."}  # fmt: skip


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
    """Which report of the page a file that is not the journal saved as CSV is, by its first rows (None when none):
    the journal itself when it is a workbook."""
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
    if name.lower().endswith(".xlsx") and rows and (_cells(rows[0])[:1] == [journal.REPORT] or any(
            import_specs.check(import_specs.JOURNAL, row).found for row in rows)):  # fmt: skip
        return import_specs.JOURNAL  # (the journal saved as a workbook)
    for row in rows:
        if found := import_specs.identify(row) or SPECS.get(import_specs.recognise(row) or ""):
            return found
    return None


def template_links(card: str) -> list[dict[str, str]]:
    return [{"key": key, "title": SPECS[key].title, "url": f"{HERE}/template/{key}"} for key in CARDS[card][1]
            if not SPECS[key].no_template]  # fmt: skip


def _meals(accounts: list[dict[str, Any]]) -> dict[str, Any]:
    """The meals accounts of the GL accounts (``accounts``): how many are ticked and, when none is, the accounts
    whose names read like meals (``reference_data.reads_like_meals``), offered for the tick: a chart of accounts does
    not say which accounts are meals, and half the GST/HST of a line coded to one that is not ticked is claimed in
    full (B-2277)."""
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
    (accounts, chart_units), units, pairs = store.chart_counts(), store.business_units(), store.account_pairs()
    settings = store.chart_settings()
    journals = learning._journal_context(store, q.get("journal"), q)
    said = current_store().ephemeral(SAID_KIND, q.get("said"))
    loaded = {
        "chart": accounts,
        "units": len(units),
        "suppliers": store.master_vendor_count(),
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
    gl = store.list_accounts("gl_accounts")  # (read once: B-2436)
    meals = _meals(gl) if chart_units else None
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
                "attention": _attention(card, looks[card], loaded[card], store, tax_ids)
                or ("No meals account ticked yet" if card == "chart" and meals and not meals["ticked"] else ""),
            }
            for card, (title, _keys) in CARDS.items()
        ],  # fmt: skip
        "loaded": loaded,
        "last": last,
        "specs": {card: [SPECS[key] for key in keys] for card, (_title, keys) in CARDS.items()},
        "templates": {card: template_links(card) for card in CARDS},
        "chart_units": chart_units,
        "meals": meals,
        "lists": {"gl": len(gl), "cc": len(store.list_accounts("cost_centers"))},
        "load": {card: _load_words(card, looks[card], loaded[card], by_hand.get(card, 0)) for card in CARDS},
        "company": _company_offer(store, units),
        "gl_replaced": _gl_replaced(store, looks["chart"]),
        "tally": [line.strip() for line in chart_cmd.tally_lines(store)] if chart_units else [],
        "chart_settings": settings.saved(),
        "home_name": pairs.name_of(settings.home_business_unit) if pairs and settings.home_business_unit else "",
        "said": _said_view(json.loads(said), loaded) if said else None,
        "looks": looks,
        "upload_gone": journals.pop("upload_gone", False)
        or any(q.get(card) and looks[card] is None for card in ("chart", "units")),
        "tax_ids": tax_ids,
        **journals,
    }


def _load_words(card: str, look: dict[str, Any] | None, loaded: int, by_hand: int) -> dict[str, str]:
    """What a card's Load button loads, and the line above it saying what that replaces or adds to ({} with nothing
    to load): the chart and the business units are replaced whole, the suppliers updated by their number, a journal
    added to those loaded, the voucher list added to or replacing the one loaded."""
    if not look or look.get("problem") or not look.get("name"):
        return {}
    kept = f" The {ui.plural(by_hand, 'row')} changed by hand {'is' if by_hand == 1 else 'are'} kept." * bool(by_hand)
    if card == "chart":
        accounts = look.get("accounts")
        button = f"Load {ui.plural(accounts or look['count'], 'account' if accounts else 'row')}"
        then = f"Replaces the {ui.plural(loaded, 'account')} loaded now: the chart is taken whole from this file.{kept}"
    elif card == "units":
        button = f"Load {ui.plural(look['count'], 'business unit')}"
        then = (f"Replaces the {ui.plural(loaded, 'business unit')} loaded now: the list is taken whole from this "
                f"file.{kept}")  # fmt: skip
    elif card == "suppliers":
        button = f"Load {ui.plural(look['kept'], 'supplier')}"
        new = look.get("new", 0)
        then = (f"Updates the {ui.plural(look['kept'] - new, 'supplier')} loaded already and adds {new:,}: no "
                f"supplier is removed.{kept}")  # fmt: skip
        if look.get("info"):  # (each one's hold stays; its tax ID and the rest are this list's, B-2459)
            then += " Each keeps its payment hold; its tax ID, currency, terms and address are taken from this list."
    elif card == "journal":
        button = "Load this journal" + (f": {look['size']}" if look.get("size") else "")
        then = f"Added to the {ui.plural(loaded, 'journal')} loaded now: a voucher in both is counted once."
    else:
        button, then = f"Load {ui.plural(look.get('kept', 0), 'voucher')}", ""
    return {"button": button, "then": then if loaded else ""}


def _said_view(said: dict[str, Any], loaded: dict[str, int]) -> dict[str, Any]:
    """What a load said, as its card shows it: the line that says what was done first (``head``; the journal's
    "Loaded …", the voucher list's count), the rest folded under it (``more``), and the next card still to load
    (``next``: None once every file is loaded)."""
    lines, card = said["lines"], said["card"]
    at = 0
    if card == "journal":
        at = next((n for n, line in enumerate(lines) if line.startswith("Loaded ")), 0)
    elif card == "vouchers":
        at = len(lines) - 1
    cards = list(CARDS)
    after = cards[cards.index(card) + 1 :] + cards[: cards.index(card)]
    coming = next((c for c in after if not loaded[c]), None)
    shown = {"card": coming, "name": f"{cards.index(coming) + 1}. {CARDS[coming][0]}"} if coming else None
    return {**said, "head": lines[at] if lines else "", "more": lines[:at] + lines[at + 1 :], "next": shown}


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
    the reader's to say (a chart's sheet unzips to 104 MB: ``core/chart_file``'s own limit, D-209). The Transaction
    Journal dropped here is named with its card; an empty file is said to be empty."""
    if not (file.filename or "").lower().endswith(".xlsx"):
        content = await read_capped(file) if (file.filename or "").lower().endswith((".csv", ".txt")) else None
        return None, import_specs.elsewhere("journal") if content and looks_like_journal(content) else NOT_XLSX
    content = await read_capped(file)
    if content == b"":
        return None, EMPTY
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
        head = _read_head(content, name, get_store(request) if card == "chart" else None)
        head["first_sheet"], head["on_sheet"] = _report_sheet(content, name, card)
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
        return refuse(_gone(token))
    if look["problem"]:
        return refuse(look["problem"])
    owner = secrets.token_urlsafe(8)
    if not current_store().take_lease(LOADING + token, owner, LOADING_MOST):  # (a Load of it under way, B-2437)
        return refuse(BEING_LOADED)
    given = {"chart_source": kept.content} if card == "chart" else {"units_source": kept.content}
    try:
        said = chart_cmd.import_files(get_store(request), **given, actor=reviewer())
    except ValueError as exc:  # (a ChartError too: the file cannot be read as what it should be)
        return refuse(f"Not loaded: {exc}")
    finally:
        current_store().drop_lease(LOADING + token, owner)
    current_store().drop_ephemeral(LOOK_KIND, token)
    drop_upload(upload["upload"])
    _loaded(token)
    return _said(request, card, said)


def _being_loaded(token: str) -> bool:
    """Whether a Load of the chart or business unit file ``token`` is under way (``_chart_import``)."""
    return current_store().ephemeral("lease", LOADING + token) is not None


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
    return done(request, _url("chart"), f"{ui.plural(len(ticked), 'account')} ticked as meals accounts: half "
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
        if not content:
            return refuse(EMPTY)
        token, problem = await run_in_threadpool(_keep_vendor_info, name, content)
        if token or problem:
            return refuse(problem) if problem else done(request, _url("suppliers", suppliers=token))
        await file.seek(0)
    token, problem = await receive_sheet(file, accept=None)
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
    if kept is None and get_upload(token) is None:
        return refuse(_gone(token))
    if kept is not None:
        read = json.loads(kept)
        if problem := _vendor_info_look(store, token, read)["problem"]:  # (as its card judged it: B-2462)
            return refuse(problem)
        rows = read["rows"]
        result = store.import_vendor_info(vendor_info.master_rows(rows), rows, actor=reviewer())
        current_store().drop_ephemeral(INFO_KIND, token)
        said = [f"Suppliers: {result['added']:,} added, {result['updated']:,} updated, from the vendor information "
                f"list. Tax IDs kept for {sum(1 for row in rows if row['tax_id']):,} of them; the payment holds "
                "are as they were."]  # fmt: skip
    else:
        try:
            _upload, df, sheets = _card_sheet("suppliers", token)
        except SheetError as exc:
            return refuse(str(exc))
        if problem := _master_problem(_judge("suppliers", [str(c) for c in df.columns]), df, sheets) or (
                NO_ROWS if df.empty else ""):  # fmt: skip
            return refuse(problem)
        columns = master_columns(list(df.columns))
        result = store.import_vendor_master(master_rows(df.to_dict("records"), columns), actor=reviewer(),
                                            by_erp_id=True, where=JDE_FILES)  # fmt: skip
        drop_upload(token)
        said = [f"Suppliers: {result['added']:,} added, {result['updated']:,} updated."]
    _loaded(token)
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
    if not content:
        return refuse(EMPTY)
    if not looks_like_journal(content):
        known = await run_in_threadpool(_known_report, name, content)
        if known is import_specs.JOURNAL:
            return refuse(JOURNAL_XLSX)
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
        return refuse(_gone(token))
    name, data = kept
    outcome, said = journal_cmd.load(get_store(request), data, name, anyway=anyway, actor=reviewer())
    if outcome in (journal_cmd.REFUSED, journal_cmd.DIFFERS):
        return refuse(said[-1])
    current_store().drop_ephemeral(learning.JOURNAL_KIND, token)
    _loaded(token)
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
    token, problem = await receive_sheet(file, accept=None, keep=_keep_vouchers)
    if problem:
        return refuse(problem)
    return done(request, _url("vouchers", vouchers=token))


@router.post(HERE + "/vouchers/load")
async def vouchers_load(request: Request, token: str = Form(""), replace: str = Form(""),
                        whole: str = Form("")) -> JSONResponse:  # fmt: skip
    return await run_in_threadpool(_vouchers_load, request, token, bool(replace), bool(whole))  # 600,000 rows


@one_connection
def _vouchers_load(request: Request, token: str, replace: bool, whole: bool) -> JSONResponse:
    """Load the uploaded voucher list as Exports loads it (``exports.load_vouchers``): its vouchers added, or
    replacing the list (never for a list that may be cut short), the ones it shows at 0.00 taken off, and with
    ``whole`` (the grid not filtered) the ones of its period it no longer shows."""
    store = get_store(request)
    kept = _kept_vouchers(token)
    if kept is None:
        return refuse(_gone(token))
    if problem := _judge("vouchers", kept["headers"])["problem"]:
        return refuse(problem)
    if not kept["count"] or not kept["columns"]:  # (the header check found its columns: the reader finds the same)
        return refuse(NO_ROWS)
    used = list(dict.fromkeys(kept["columns"].values()))
    items = [dict(zip(used, item, strict=True)) for item in kept["items"]]
    rows, read = named_vouchers(store, items, kept["columns"])
    refusal, left = load_vouchers(store, rows, read, replace=replace, whole=whole)
    if refusal:
        return refuse(refusal)
    current_store().drop_ephemeral(VOUCHERS_KIND, token)
    _loaded(token)
    gone = vouchers_left_said(left, whole, read)
    total = store.erp_register_count()
    return _said(request, "vouchers", [registers.vouchers_said(read), *([gone] if gone else []),
                                       f"{ui.plural(total, 'voucher')} of JD Edwards known now."])  # fmt: skip
