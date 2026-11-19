"""JD Edwards files: all of JD Edwards' reports in one workbook, a sheet each (D-266), as the pilot tester queries them
in ReportsNow by the build sheet: one upload at the top of the page instead of one a card.

Each sheet is told by its column names (``import_specs.workbook_report``: its header row may be under title rows), and
read a row at a time within the workbook reader's bounds (``core/chart_file``). The page shows, before anything is
kept, each sheet with the report it was recognised as, its rows (and how many of each company), the columns found
and the ones missing (``look``); a sheet that is none of the reports is listed and never guessed at, and two sheets of
a report that is replaced whole (the chart, the business units, the payment terms, the tax areas) are both left out,
said. Several sheets of a report that adds up (the supplier ledger, the G/L lines, the PO lines, split by company or
year) are loaded one after the other, an overlap counted once.

"Load all" loads every sheet that can be, in the order the cards depend on each other (``ORDER``), through each card's
own loader (``jde_files``, ``ops/chart_cmd``, ``ops/journal_cmd``); a sheet that cannot be loaded is said, and the
others are loaded all the same. Each card's own upload stays.
"""

from __future__ import annotations

import itertools
import json
import secrets
from pathlib import Path
from typing import Any

from fastapi import File, Form, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from ap_coder.coding import vendor_info
from ap_coder.core import chart, chart_file, ui
from ap_coder.core.store import Store
from ap_coder.erp import import_specs, registers
from ap_coder.erp.import_specs import SPECS
from ap_coder.ops import chart_cmd, journal_cmd
from ap_coder.web import files
from ap_coder.web.core import current_store, done, get_store, one_connection, page_router, refuse, reviewer
from ap_coder.web.files import EMPTY, NO_ROWS, TOO_BIG, drop_upload, get_upload, keep_upload, read_capped
from ap_coder.web.pages import jde_files
from ap_coder.web.pages.exports import jde_vouchers, load_vouchers

router = page_router()

KIND = "jde-workbook"  # a workbook as surveyed at its upload: its sheets, their reports and rows
CARD = "workbook"
# The order the reports are loaded in: the chart and the business units first (the rest is checked against them),
# the payment terms and tax areas (the suppliers' terms read with them), the suppliers, then the rest.
ORDER = ("chart", "units", "payment_terms", "tax_areas", "supplier_master", "vendor_info", "vouchers", "gl_lines",
         "purchase_orders")  # fmt: skip
WHOLE = ("chart", "units", "payment_terms", "tax_areas")  # a newer file replaces it whole: one sheet only
# The column of each report a row's company is in.
COMPANY = {"units": "company", "vouchers": "company", "gl_lines": "company", "purchase_orders": "order_company"}
NOT_XLSX = "Upload the workbook as Excel saves it (.xlsx), a sheet for each report."
NOT_ONE = "None of the reports: not loaded."


def _cells(row: Any) -> list[str]:
    return ["" if cell is None else str(cell).strip() for cell in row]


def survey(content: bytes, name: str) -> dict[str, Any]:
    """Each sheet of the workbook: its title, the report it is (``import_specs.workbook_report``; None: none), the
    row its column names are on (``at``), its column names, how many rows it has under them with a cell filled
    (``rows``) and of each company (``companies``). ``ChartError`` for a workbook that cannot be read."""
    sheets = []
    for title, head in chart_file.sheet_heads(content, name):
        cells = [_cells(row) for row in head]
        key, at = next(((import_specs.workbook_report(row), n) for n, row in enumerate(cells)
                        if import_specs.workbook_report(row)), (None, 0))  # fmt: skip
        sheet: dict[str, Any] = {"title": title, "key": key, "at": at, "headers": cells[at] if key else [],
                                 "rows": 0, "companies": {}}  # fmt: skip
        if key:
            spec = SPECS.get(key)
            found = import_specs.check(spec, cells[at]).found if spec else {}
            company = cells[at].index(found[COMPANY[key]]) if COMPANY.get(key) in found else None
            for row in itertools.islice(chart_file.read_rows(content, name, title), at + 1, None):
                filled = _cells(row)
                if not any(filled):
                    continue
                sheet["rows"] += 1
                if company is not None and company < len(filled) and filled[company]:
                    sheet["companies"][filled[company]] = sheet["companies"].get(filled[company], 0) + 1
        sheets.append(sheet)
    return {"name": name, "sheets": sheets}


def _verdicts(sheets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each sheet as the page shows it: its report (title and card), the columns found and missing, and why it is not
    loaded ("" when it is)."""
    times = {key: sum(1 for s in sheets if s["key"] == key) for key in {s["key"] for s in sheets}}
    out = []
    for sheet in sheets:
        key, spec = sheet["key"], SPECS.get(sheet["key"] or "")
        view = {**sheet, "report": spec.title if spec else "", "card": spec.card if spec else "", "found": [],
                "missing": [], "problem": ""}  # fmt: skip
        if key is None:
            view["problem"] = NOT_ONE
        elif spec is None:  # a voucher's own grid
            view["problem"] = import_specs.elsewhere(key)
            view["report"] = import_specs.VOUCHER_GRIDS[key]
        else:
            result = import_specs.check(spec, sheet["headers"])
            view["found"] = [c.label for c in spec.columns if c.field in result.found]
            view["missing"] = [f"{c.header} ({c.label.lower()})" for c in (*result.missing, *result.none_of)]
            view["problem"] = result.problem() or ("" if sheet["rows"] else NO_ROWS)
            if key in WHOLE and times[key] > 1 and not view["problem"]:
                view["problem"] = (f"{times[key]} sheets are {spec.report}, which is replaced whole: neither is "
                                   "loaded. Keep the one to load and upload the workbook again.")  # fmt: skip
        view["by_company"] = ", ".join(f"{co} {n:,}" for co, n in sorted(sheet["companies"].items()))
        out.append(view)
    return out


def look(store: Store, token: str) -> dict[str, Any] | None:
    """The uploaded workbook as the page shows it before anything is kept (None when it is no longer kept)."""
    kept = current_store().ephemeral(KIND, token)
    if kept is None:
        return None
    surveyed = json.loads(kept)
    sheets = _verdicts(surveyed["sheets"])
    loads = [s for s in sheets if not s["problem"]]
    return {"token": token, "name": surveyed["name"], "sheets": sheets, "loads": len(loads),
            "reports": len({s["key"] for s in loads}), "loading": _being_loaded(token)}  # fmt: skip


def _being_loaded(token: str) -> bool:
    return current_store().ephemeral("lease", jde_files.LOADING + token) is not None


@router.post(jde_files.HERE + "/workbook/upload")
async def workbook_upload(request: Request, file: UploadFile = File(...)) -> JSONResponse:  # noqa: B008
    """Keep an uploaded workbook and show what each sheet is, nothing loaded yet."""
    name = Path(file.filename or "").name
    if not name.lower().endswith(".xlsx"):
        return refuse(NOT_XLSX)
    content = await read_capped(file)
    if content is None:
        return refuse(TOO_BIG)
    if not content:
        return refuse(EMPTY)
    return await run_in_threadpool(_keep, request, name, content)


@one_connection
def _keep(request: Request, name: str, content: bytes) -> JSONResponse:
    try:
        surveyed = survey(content, name)
    except chart.ChartError as exc:
        return refuse(f"Not loaded: {exc}")
    surveyed["upload"] = keep_upload(name, content)
    kept = json.dumps(surveyed, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    token = current_store().keep_ephemeral(KIND, kept, jde_files.SAID_TTL, keep=2)
    return done(request, f"{jde_files.HERE}?{CARD}={token}#{CARD}-summary")


@router.post(jde_files.HERE + "/workbook/load")
async def workbook_load(request: Request, token: str = Form("")) -> JSONResponse:
    return await run_in_threadpool(_load_all, request, token)  # (a year of every report: a minute or two)


def _frame(content: bytes, name: str, sheet: dict[str, Any]) -> Any:
    """A sheet as text cells from its column names on, as a card reads a sheet (``files.rows_frame``)."""
    rows = chart_file.read_rows(content, name, sheet["title"])
    return files.rows_frame(
        [[files._cell(value) for value in row] for row in itertools.islice(rows, sheet["at"], None)]
    )


def _load_sheet(store: Store, key: str, content: bytes, name: str, sheet: dict[str, Any]) -> list[str]:
    """Load one sheet as its card loads its report, and what that says; ``ValueError`` (or ``ChartError``) when it
    cannot be."""
    title = sheet["title"]
    if key in ("chart", "units"):
        given = {"chart_source": content, "chart_sheet": title} if key == "chart" else {
            "units_source": content, "units_sheet": title}  # fmt: skip
        return chart_cmd.import_files(store, **given, actor=reviewer())
    if key == "vendor_info":
        read = vendor_info.read(content, name, title)
        if read is None or not read["rows"]:
            raise ValueError(NO_ROWS)
        result = store.import_vendor_info(vendor_info.master_rows(read["rows"]), read["rows"], actor=reviewer())
        return [f"Suppliers: {result['added']:,} added, {result['updated']:,} updated, from the vendor information "
                "list; the payment holds are as they were."]  # fmt: skip
    if key == "gl_lines":
        data = jde_files.gl_lines_csv(content, name, title) or b""
        outcome, said = journal_cmd.load(store, data, f"{name} ({title})", actor=reviewer())
        if outcome in (journal_cmd.REFUSED, journal_cmd.DIFFERS):
            raise ValueError(said[-1])
        return [said[-1]]
    df = _frame(content, name, sheet)
    if key == "supplier_master":
        return jde_files.load_supplier_master(store, df)
    if key in ("payment_terms", "tax_areas"):
        return jde_files.load_table(store, "terms" if key == "payment_terms" else "areas", df)
    found = jde_vouchers(store, df)  # (the supplier ledger: added to the vouchers loaded, never replacing them)
    if found is None:
        raise ValueError(NO_ROWS)
    refusal, _left = load_vouchers(store, *found, replace=False, whole=False)
    if refusal:
        raise ValueError(refusal)
    return [registers.vouchers_said(found[1])]


@one_connection
def _load_all(request: Request, token: str) -> JSONResponse:
    """Load every sheet the workbook's summary showed as loadable, in ``ORDER``: what each said, a sheet that could
    not be loaded with why. The open PO lines of several sheets are one load (an order's lines replace its own)."""
    store, seen = get_store(request), look(get_store(request), token)
    surveyed = json.loads(current_store().ephemeral(KIND, token) or b"{}")
    upload = get_upload(surveyed.get("upload"))
    if seen is None or upload is None:
        return refuse(jde_files._gone(token))
    sheets = [s for s in seen["sheets"] if not s["problem"]]
    if not sheets:
        return refuse("No sheet of this workbook can be loaded: each says why.")
    owner = secrets.token_urlsafe(8)
    if not current_store().take_lease(jde_files.LOADING + token, owner, jde_files.LOADING_MOST):
        return refuse(jde_files.BEING_LOADED)
    loaded, more = 0, []
    try:
        for key in ORDER:
            mine = [s for s in sheets if s["key"] == key]
            if key == "purchase_orders" and mine:
                frames = [_frame(upload.content, upload.name, s) for s in mine]
                mine = [{**mine[0], "title": ", ".join(s["title"] for s in mine), "frames": frames,
                         "sheets": len(mine)}]  # fmt: skip
            for sheet in mine:
                try:
                    lines = (jde_files.load_orders(store, sheet["frames"]) if key == "purchase_orders"
                             else _load_sheet(store, key, upload.content, upload.name, sheet))  # fmt: skip
                except (ValueError, files.SheetError) as exc:  # (a ChartError too)
                    more.append(f"{sheet['title']} ({sheet['report']}): not loaded. {exc}")
                    continue
                loaded += sheet.get("sheets", 1)
                more += [f"{sheet['title']} ({sheet['report']}): {lines[0]}", *lines[1:]]
    finally:
        current_store().drop_lease(jde_files.LOADING + token, owner)
    left = len(sheets) - loaded
    said = [
        f"Loaded {ui.plural(loaded, 'sheet')} of {upload.name}"
        + (f"; {ui.plural(left, 'sheet')} could not be loaded (each says why below)." if left else ".")
    ]
    current_store().drop_ephemeral(KIND, token)
    drop_upload(surveyed["upload"])
    jde_files._loaded(token)
    return jde_files._said(request, CARD, said + more)
