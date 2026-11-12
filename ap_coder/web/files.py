"""Files in and out of the dashboard: downloads, and spreadsheets uploaded to be imported.

Downloads are GET routes answered with ``download(data, name, mime)``: the file with its content type and name.

An import from a spreadsheet takes two steps: the clerk uploads the file, then checks
which column holds what before anything is imported. The uploaded file waits under a one-off token
(``keep_upload``) for an hour; the page reads it again with ``read_upload`` (``?file=<token>``) to show its first rows
and the column choices (``sheet_context``), and the import POST names the token. It waits in the dashboard's
database (a restart of the server keeps it), never as a file of its own, and is deleted after the hour (D-99).
Uploads larger than ``_MAX_BYTES`` are not read (``read_capped``); every page reads a CSV with ``read_csv``.

The column choices are an import's spec (``erp/import_specs``): ``spec_sheet`` offers one for each of its columns,
each first filled from the file's headers by the spec's reader, and says the needed ones still without a column
(``unpicked``); each import refuses a file without them, naming them (``pick_refusal``).
"""

from __future__ import annotations

import io
import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import pandas as pd
from fastapi import UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from ap_coder.core.csvio import sniff_delimiter
from ap_coder.erp import import_specs
from ap_coder.erp.import_specs import ImportSpec
from ap_coder.erp.journal import looks_like_journal
from ap_coder.intake import triage
from ap_coder.web import identity
from ap_coder.web.core import current_store

SHEET_TYPES = (".csv", ".xlsx", ".xls")
NONE = ""  # the value of "(none)" in a column choice
PREVIEW_ROWS = 5
HEADER_ROWS = 10  # the rows a report's column names are looked for in, under its title rows (as ``chart_file``'s)

CANNOT_READ = (
    "This file could not be read as a spreadsheet: it may be empty, damaged or another format. In Excel, save it as "
    "CSV UTF-8 or Excel Workbook (.xlsx) and upload it again."
)
NOT_A_SHEET = "Upload a CSV or Excel file (.csv or .xlsx)."
EMPTY = "This file is empty: it has no column names and no rows. Export the list again and upload the file it saved."
NO_ROWS = "This file has no rows under its header line."
GONE = "That upload is no longer here: an upload is kept for an hour, and only your last 24. Upload the file again."
TOO_BIG_UNZIPPED = (
    "This workbook is too large to open here: it unzips to over 50 MB, or one sheet to over 20 MB (JD Edwards' "
    "voucher list, to over 250 MB). In Excel, save it as CSV UTF-8 (File, Save As) and upload that, or export fewer "
    "rows."
)
TOO_BIG = "This file is larger than 50 MB. Export fewer rows, or only the columns needed."
# JD Edwards' long reports an import page reads (B-2433): a year's voucher list as .xlsx (22,000 pay items, 56
# columns) unzips to 47 MB, over what pandas may open of any other workbook (``triage.sheet_unzips_too_big``), so it
# is read a row at a time within the chart reader's own bounds (``core/chart_file``: a sheet of 250 MB at most).
LONG_REPORTS = ("vouchers",)
# The most columns a sheet is read with (B-1499): a statement or a list has a few dozen, an ERP export a few hundred;
# pandas takes about 2 KB and 40 microseconds a column, so 50 MB of short headers would take some 10 GB.
MAX_COLUMNS = 500


def too_wide(columns: int) -> str:
    return (f"This file has {columns:,} columns; AP Coder reads at most {MAX_COLUMNS}. Save only the columns needed "
            "and upload it again.")  # fmt: skip


# --- Downloads ---------------------------------------------------------------------------------------------------

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def download(data: bytes, name: str, mime: str) -> Response:
    """``data`` as a file download called ``name`` (any characters: the plain-ASCII name is a fallback)."""
    return Response(
        content=data, media_type=mime, headers={"Content-Disposition": attachment(name), "Cache-Control": "no-store"}
    )


def attachment(name: str) -> str:
    """The Content-Disposition of a download called ``name`` (any characters: the plain-ASCII name is a fallback)."""
    plain = "".join(c if c.isascii() and c.isprintable() and c not in '"\\' else "_" for c in name) or "download"
    return f"attachment; filename=\"{plain}\"; filename*=UTF-8''{quote(name, safe='')}"


# --- Uploads waiting for their columns to be picked -------------------------------------------------------------

_TTL = 3600  # seconds an upload waits
_MAX = 24  # uploads kept at most of each person (their oldest go first: B-2432)
_MAX_BYTES = 50 * 1024 * 1024  # an uploaded file, and an emailed statement read for the Statements page
# Its kind in the database's short-lived table (``Store.keep_ephemeral``): "upload:" and whose it is (``_whose``).
UPLOAD_KIND = "upload:"


@dataclass(frozen=True)
class Upload:
    name: str
    content: bytes


def _whose() -> str:
    """Whose uploads a new one is counted with (B-2432): the account's on the company server, the browser's of a
    colleague on the office network, "" this computer's."""
    found = identity.person()
    if found is None:
        return ""
    return f"user {found.user_id}" if found.user_id else f"browser {found.browser or found.address}"


def keep_upload(name: str, content: bytes) -> str:
    """Keep an uploaded file for the next page, in the dashboard's database (a restart of the server keeps it, D-99),
    the person's last ``_MAX`` kept; returns its token (whoever opens it reads it: an Admin's link to a Clerk)."""
    value = name.encode("utf-8") + b"\0" + content
    return current_store().keep_ephemeral(UPLOAD_KIND + _whose(), value, _TTL, keep=_MAX)


def get_upload(token: str | None) -> Upload | None:
    kept = current_store().ephemeral(UPLOAD_KIND, token, any_of=True) if token else None
    if kept is None:
        return None
    name, _, content = kept.partition(b"\0")
    return Upload(name.decode("utf-8", "replace"), content)


def drop_upload(token: str | None) -> None:
    current_store().drop_ephemeral(UPLOAD_KIND, token, any_of=True)


async def read_capped(file: UploadFile) -> bytes | None:
    """An uploaded spreadsheet's bytes, or None when it is larger than ``_MAX_BYTES`` (never more than that read)."""
    content = await file.read(_MAX_BYTES + 1)
    return None if len(content) > _MAX_BYTES else content


async def receive_sheet(file: UploadFile, accept: Iterable[str] | None = (),
                        keep: Callable[[str, bytes], tuple[str, str]] | None = None) -> tuple[str, str]:  # fmt: skip
    """(token, "") for an uploaded spreadsheet that can be read, else ("", the problem in plain words). The sheet is
    read and kept on a threadpool thread: a 50 MB workbook never holds up the dashboard's other requests. JD Edwards'
    Transaction Journal is refused, naming its card; so is any other of JD Edwards' reports (``jde_refusal``), but
    those of ``accept`` with every column they need. ``accept`` None: the caller judges JD Edwards' reports itself
    (the JD Edwards files page). ``keep``: what keeps the file read, (token, "") or ("", why not), instead of the
    upload kept as it came (JD Edwards files' voucher list, kept as read: B-2441)."""
    name = file.filename or "upload.csv"
    if not name.lower().endswith((*SHEET_TYPES, ".txt")):
        return "", NOT_A_SHEET
    content = await read_capped(file)
    if content is None:
        return "", TOO_BIG
    if not name.lower().endswith((".xlsx", ".xls")) and looks_like_journal(content):
        return "", import_specs.elsewhere("journal")
    if not name.lower().endswith(SHEET_TYPES):
        return "", NOT_A_SHEET
    if keep is not None:
        return await run_in_threadpool(keep, name, content)
    return await run_in_threadpool(_keep_sheet, name, content, accept)


def _keep_sheet(name: str, content: bytes, accept: Iterable[str] | None = ()) -> tuple[str, str]:
    try:
        df, _names = read_sheet(name, content)
    except SheetError as exc:
        return "", str(exc)
    if accept is not None and (refusal := jde_refusal(first_rows(df), accept)):
        return "", refusal
    return keep_upload(name, content), ""


def first_rows(df: pd.DataFrame) -> list[list[Any]]:
    """A sheet's column names and the rows under them, as many as a report's title rows may be."""
    return [list(df.columns), *df.head(HEADER_ROWS).values.tolist()]


def jde_refusal(rows: list[list[Any]], accept: Iterable[str] = ()) -> str:
    """Why a sheet of these first rows (``first_rows``) is not imported on a page of any ERP's lists ("" when it
    may be): it is one of JD Edwards' own reports (``import_specs.recognise``), loaded on the JD Edwards files page
    only, never through another page's column picker. One the page reads (``accept``: the supplier master on Vendors,
    the voucher list on Exports) is refused only without a column it needs, naming it."""
    for row in rows[:HEADER_ROWS]:
        if key := import_specs.recognise(row):
            if key in accept:
                return import_specs.check(import_specs.SPECS[key], row).problem()
            return import_specs.elsewhere(key)
    return ""


# --- Reading a spreadsheet ---------------------------------------------------------------------------------------


class SheetError(ValueError):
    """A spreadsheet that cannot be read; the message says what to do, in plain words."""


def read_excel(content: bytes, sheet_name: int | None = None) -> Any:
    """``pd.read_excel`` of an uploaded or emailed workbook, as every page reads one: text cells exactly as typed ("NA"
    and "NULL" stay text, W2-04), and the workbook's unzipped sizes checked before pandas opens it (W2-01, the limits
    of triage, B-63). ``sheet_name``: None for every sheet ({name: DataFrame}), 0 for the first (one DataFrame).
    Raises SheetError (TOO_BIG_UNZIPPED) for a workbook that unzips to too much."""
    if triage.sheet_unzips_too_big(content):
        raise SheetError(TOO_BIG_UNZIPPED)
    widest = _widest_sheet(content)
    if widest > MAX_COLUMNS:
        raise SheetError(too_wide(widest))
    return pd.read_excel(io.BytesIO(content), sheet_name=sheet_name, dtype=str, keep_default_na=False, na_filter=False)


def read_csv(content: bytes) -> pd.DataFrame:
    """An uploaded CSV as text cells, as every page reads one: UTF-8 (with or without its mark) or Windows-1252
    (Excel's "CSV"), its delimiter found from several lines (``sniff_delimiter``: a semicolon file whose header has a
    comma in a cell too, B-1400). Raises SheetError when it is neither encoding; pandas' own error for a file that
    is no CSV."""
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = content.decode(encoding)
        except UnicodeDecodeError:
            continue
        text = _after_title(text)
        sep = sniff_delimiter(text)
        columns = _header_cells(text, sep)
        if columns > MAX_COLUMNS:
            raise SheetError(too_wide(columns))
        return pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False, sep=sep, engine="python")
    raise SheetError("Could not read this file. In Excel, use Save As → CSV UTF-8 and upload it again.")


_SEPARATORS = (",", ";", "\t", "|")
_NUMBER_LIKE = re.compile(r"[-+(]?[\d.,\s$€£%)]+")
_TITLE_LINES = 10  # a report's title and blank lines above its column names: never more than this


def _after_title(text: str) -> str:
    """A CSV without the report title an ERP puts above its column names (B-1513): the first lines with no separator
    at all, when a line under them has one. As it is otherwise."""
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines[: _TITLE_LINES + 1]):
        if any(sep in line for sep in _SEPARATORS):
            return "".join(lines[i:]) if i else text
    return text


def _header_below_title(df: pd.DataFrame) -> pd.DataFrame:
    """The sheet with its column names where they are (B-1513): when the first row is a report's title (one cell,
    the others blank: "Unnamed: 1"...), the first row below it that names more columns, none of them a number."""
    columns = [str(c) for c in df.columns]
    blank = sum(1 for c in columns if c.startswith("Unnamed:") or not c.strip())
    if len(columns) < 2 or blank * 2 < len(columns):
        return df
    named = len(columns) - blank
    for i in range(min(_TITLE_LINES, len(df))):
        cells = ["" if pd.isna(v) else str(v).strip() for v in df.iloc[i].tolist()]
        filled = [c for c in cells if c]
        if len(filled) >= 2 and len(filled) > named and not any(_NUMBER_LIKE.fullmatch(c) for c in filled):
            names: list[str] = []
            for j, cell in enumerate(cells):
                name = cell or f"Unnamed: {j}"
                while name in names:  # two columns of one name: kept apart, as pandas does
                    name += ".1"
                names.append(name)
            out = df.iloc[i + 1 :].reset_index(drop=True)
            out.columns = names
            return out
    return df


def _header_cells(text: str, sep: str) -> int:
    """How many cells a CSV's header line has, counted without splitting it: the blank lines before it skipped, as
    pandas skips them, and a line break inside a quoted cell kept in it (the line ends where its quotes pair up). A
    delimiter inside quotes counts too: a cap, not a reading."""
    start = len(text) - len(text.lstrip())
    end, quotes = start, 0
    for line_break in _LINE_BREAK.finditer(text, start):
        quotes += text.count('"', end, line_break.start())
        end = line_break.start()
        if quotes % 2 == 0:
            break
    else:
        end = len(text)
    return text.count(sep, start, end) + 1


_LINE_BREAK = re.compile(r"[\r\n]")


def _widest_sheet(content: bytes) -> int:
    """The most columns a sheet of a workbook says it has (0 when it can't be told: an old .xls, a damaged file)."""
    import openpyxl

    try:
        book = openpyxl.load_workbook(io.BytesIO(content), read_only=True)
    except Exception:  # noqa: BLE001 - pandas reads it, or says why not
        return 0
    try:
        return max((sheet.max_column or 0 for sheet in book.worksheets), default=0)
    finally:
        book.close()


def read_sheet(name: str, content: bytes, sheet: str | None = None,
               specs: Sequence[ImportSpec] = ()) -> tuple[pd.DataFrame, list[str]]:  # fmt: skip
    """(the sheet as text cells, the workbook's sheet names) of an uploaded CSV or Excel file. ``sheet``: the
    workbook sheet to read (default the first with a cell filled; with ``specs``, the first that is one of them
    whole: its name is the frame's ``attrs["sheet"]``). Semicolon CSVs and Windows-1252 CSVs (Excel's "CSV") are read
    too. ``specs``: the reports the file should be, whose column names are looked for under a report's title rows
    (``import_specs.header_row``)."""
    if not content:
        raise SheetError(EMPTY)
    names: list[str] = []
    try:
        if name.lower().endswith(".xlsx") and triage.sheet_unzips_too_big(content):
            df, names = _long_report(name, content, sheet)
        elif name.lower().endswith((".xlsx", ".xls")):
            sheets = read_excel(content)
            names = [str(n) for n in sheets]
            if not names:
                raise SheetError(CANNOT_READ)
            frames = {k: _named_row(_frame(df), specs) for k, df in sheets.items()}
            key = next((k for k in frames if str(k) == sheet), None)
            if key is None:  # (the first sheet that is the report, else the first with anything on it)
                key = next((k for k in frames if specs and _whole(frames[k], specs)), None)
                key = key if key is not None else next((k for k in frames if _filled(frames[k])), next(iter(frames)))
            df = frames[key]
            df.attrs["sheet"] = str(key)
        else:
            df = _named_row(_frame(read_csv(content)), specs)
    except SheetError:
        raise
    except Exception as exc:  # noqa: BLE001 - empty file, not really a spreadsheet, damaged workbook...
        raise SheetError(CANNOT_READ) from exc
    if not _filled(df):
        raise SheetError(EMPTY)
    return df, names


def _long_report(name: str, content: bytes, sheet: str | None) -> tuple[pd.DataFrame, list[str]]:
    """(the sheet, the workbook's sheet names) of a workbook too large for ``read_excel`` that is one of JD Edwards'
    ``LONG_REPORTS`` (``sheet``, or the first sheet that is one whole), read a row at a time by the chart's reader
    within its bounds (``chart_file``), its cells as ``read_excel`` gives them. SheetError (``TOO_BIG_UNZIPPED``)
    for any other workbook, or one over those bounds."""
    from ap_coder.core import chart_file
    from ap_coder.core.chart import ChartError

    specs = [import_specs.SPECS[key] for key in LONG_REPORTS]
    try:
        heads = chart_file.sheet_heads(content, name)
        for title, rows in heads:
            cells = [["" if cell is None else str(cell).strip() for cell in row] for row in rows]
            at = import_specs.header_row(cells, specs) if cells else 0
            if (sheet in (None, title)) and cells and _whole(pd.DataFrame(columns=cells[at]), specs):
                read = [[_cell(value) for value in row] for row in chart_file.read_rows(content, name, title)]
                break
        else:
            raise SheetError(TOO_BIG_UNZIPPED)
    except ChartError as exc:
        raise SheetError(TOO_BIG_UNZIPPED) from exc
    width = max((len(row) for row in read), default=0)
    read = [row + [""] * (width - len(row)) for row in read]
    columns: list[str] = []
    for j, cell in enumerate(read[0] if read else []):
        column = cell or f"Unnamed: {j}"
        while column in columns:  # two columns of one name: kept apart, as pandas does
            column += ".1"
        columns.append(column)
    df = _named_row(_frame(pd.DataFrame(read[1:], columns=columns, dtype=str)), specs)
    df.attrs["sheet"] = title
    return df, [title for title, _rows in heads]


def _cell(value: Any) -> str:
    """A cell as ``read_excel`` gives it as text: a whole number without its ".0", nothing as ""."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _filled(df: pd.DataFrame) -> bool:
    return not df.empty or any(str(c).strip() and not str(c).startswith("Unnamed:") for c in df.columns)


def _frame(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [str(c) for c in df.columns]  # a header like 2024 arrives as a number
    return _header_below_title(df).fillna("")


def _named_row(df: pd.DataFrame, specs: Sequence[ImportSpec]) -> pd.DataFrame:
    """The sheet with its column names on the row of ``specs``' columns (under a report's title rows: a print date
    beside its title is a second cell, B-2273), as it is without ``specs``."""
    if not specs:
        return df
    at = import_specs.header_row(first_rows(df), specs)
    if not at:
        return df
    names: list[str] = []
    for j, cell in enumerate(str(v).strip() for v in df.iloc[at - 1].tolist()):
        name = cell or f"Unnamed: {j}"
        while name in names:  # two columns of one name: kept apart, as pandas does
            name += ".1"
        names.append(name)
    out = df.iloc[at:].reset_index(drop=True)
    out.columns = names
    return out


def _whole(df: pd.DataFrame, specs: Sequence[ImportSpec]) -> bool:
    return any(import_specs.check(spec, df.columns).ok for spec in specs)


def read_upload(token: str | None, sheet: str | None = None,
                specs: Sequence[ImportSpec] = ()) -> tuple[Upload, pd.DataFrame, list[str]]:  # fmt: skip
    """The waiting upload ``token`` as (upload, sheet, sheet names) (``read_sheet``). Raises SheetError when it is
    gone or unreadable."""
    upload = get_upload(token)
    if upload is None:
        raise SheetError(GONE)
    df, names = read_sheet(upload.name, upload.content, sheet, specs)
    return upload, df, names


def picked_columns(
    params: Mapping[str, Any], fields: Mapping[str, str], columns: list[str], guessed: Mapping[str, str]
) -> dict[str, str]:
    """{field: column} from the ``col_<field>`` choices in ``params``, or the guesses when nothing was picked yet.
    A choice that is not a column of the file is ignored."""
    if not any(key.startswith("col_") for key in params):
        return {f: c for f, c in guessed.items() if f in fields and c in columns}
    picked = {}
    for field in fields:
        value = str(params.get(f"col_{field}") or "")
        if value and value in columns:
            picked[field] = value
    return picked


_NUMBERED = re.compile(r"(.+)\.\d+")  # how a reader names the 2nd column of a name the file has twice ("Code.1")


def _as_named(column: str, present: set[str]) -> str:
    """A column's name as the file has it: "Code" for a reader's "Code.1" (or "Code.1.1") when "Code" is there too."""
    while (numbered := _NUMBERED.fullmatch(column)) and numbered.group(1) in present:
        column = numbered.group(1)
    return column


def column_names(columns: Sequence[str]) -> list[str]:
    """Each column as a choice shows it (B-2469): by the name the file gives it, and one of a name the file has more
    than once with which it is ("Code (1st)", "Code (2nd)"), as ``_import.html``'s ``header_name`` shows them."""
    present = set(columns)
    named = [_as_named(c, present) for c in columns]
    times, seen, out = Counter(named), Counter[str](), []
    for name in named:
        seen[name] += 1
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(seen[name], "th")
        out.append(f"{name} ({seen[name]}{suffix})" if times[name] > 1 else name)
    return out


def sheet_context(
    token: str,
    upload: Upload,
    df: pd.DataFrame,
    sheets: list[str],
    sheet: str | None,
    fields: Mapping[str, str],
    chosen: Mapping[str, str],
    required: tuple[str, ...] = (),
) -> dict[str, Any]:
    """What ``_sheet.html`` draws: the file, its first rows and one column choice per field."""
    columns = list(df.columns)
    return {
        "token": token,
        "name": upload.name,
        "sheets": sheets,
        "sheet": sheet if sheet in sheets else df.attrs.get("sheet", sheets[0] if sheets else ""),
        "columns": columns,
        "options": list(zip(columns, column_names(columns), strict=True)),
        "count": len(df),
        "preview": [[str(v) for v in row] for row in df.head(PREVIEW_ROWS).itertuples(index=False)],
        "fields": [
            {"key": f, "label": label + (" *" if f in required else ""), "value": chosen.get(f, NONE)}
            for f, label in fields.items()
        ],
    }


# --- The column choices of an import's spec ---------------------------------------------------------------------


def unpicked(spec: ImportSpec, chosen: Mapping[str, str]) -> list[str]:
    """The fields ``spec`` needs that have no column in ``chosen``, by their labels: each required one, and its
    ``one_of`` fields as one ("one of Amount, Credit or Open balance") when none of them has one."""
    missing = [c.label for c in spec.required if c.field not in chosen]
    if spec.one_of and not set(spec.one_of) & set(chosen):
        labels = [spec.column(f).label for f in spec.one_of]
        missing.append("one of " + ", ".join(labels[:-1]) + " or " + labels[-1])
    return missing


def pick_refusal(spec: ImportSpec, chosen: Mapping[str, str]) -> str:
    """Why an import of ``spec`` with these columns is refused ("" when it is not): the needed fields still without a
    column, named. There is no import without them."""
    missing = unpicked(spec, chosen)
    return f"Pick the column for: {', '.join(missing)}. Nothing was loaded." if missing else ""


def spec_sheet(token: str, upload: Upload, df: pd.DataFrame, sheets: list[str], sheet: str | None, spec: ImportSpec,
               params: Mapping[str, Any] | None = None, accept: Iterable[str] = ()) -> dict[str, Any]:  # fmt: skip
    """What a page shows of an uploaded sheet to import as ``spec`` (``_sheet.html``'s ``columns``, ``_import.html``'s
    ``preview``): ``sheet_context`` with a choice for each of the spec's columns, filled from the file's headers by
    the spec's reader (``ImportSpec.find``: their order never matters) until the person picks (``params``'
    ``col_<field>``), the needed ones marked, but a needed one whose name the file has twice: the person picks which
    (``look.twice``, B-2469); ``missing``: the needed fields still without a column; ``look``: the
    file's header check (each column found as which header, or missing; its other columns) and its first rows, and
    why it is not imported when it is one of JD Edwards' reports (``jde_refusal``: a sheet chosen on the page)."""
    columns = [str(c) for c in df.columns]
    fields = {c.field: c.label for c in spec.columns}
    found = import_specs.check(spec, columns)
    twice = {c.field for c in found.twice}  # (a needed name the file has twice: the person picks which, B-2469)
    chosen = picked_columns(params or {}, fields, columns, {f: c for f, c in found.found.items() if f not in twice})
    context = sheet_context(token, upload, df, sheets, sheet, fields, chosen)
    needed = {c.field for c in spec.required}
    context["fields"] = [{**f, "label": fields[f["key"]], "need": f["key"] in needed, "one_of": f["key"] in spec.one_of}
                         for f in context["fields"]]  # fmt: skip
    rows = [columns, *df.head(import_specs.PREVIEW_ROWS).values.tolist()]
    return {**context, "spec": spec, "missing": unpicked(spec, chosen),
            "need": {c.field: c.label for c in spec.required},
            "one_of": {"fields": list(spec.one_of), "label": unpicked(spec, {})[-1]} if spec.one_of else None,
            "look": {"name": upload.name, "count": len(df), "spec": spec, "check": found, "pick": True,
                     "unfound": unpicked(spec, found.found),
                     "twice": [f"“{_as_named(found.found[c.field], set(columns))}” ({c.label})" for c in found.twice],
                     "guessed": "; ".join(f"{spec.column(f).label} as “{found.found[f]}”" for f in found.guessed),
                     "problem": jde_refusal(first_rows(df), accept) or ("" if len(df) else NO_ROWS),
                     "sample": import_specs.preview(rows, 0)}}  # fmt: skip
