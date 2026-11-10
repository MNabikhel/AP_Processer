"""The chart of accounts and the business unit master read from JD Edwards' exports as they come (D-209): an .xlsx
with its column names in row 1 (or under a grid's title rows, within the first ``HEADER_ROWS``), nearly every cell
stored as text, a business unit padded with spaces and an empty field a lone space.

The account master is wide and long (110 columns, about 14,700 rows): its one sheet unzips to about 104 MB, far over
what any other spreadsheet AP Coder opens may (``intake/triage.sheet_unzips_too_big``: 20 MB a sheet). So this
reader, and only it, has a higher limit of its own (``SHEET_MOST``), and never holds the sheet: it is read a row at a
time (``read_export`` gives the rows one by one) and only the columns the import needs are kept (six of the chart's).

What bounds a file before memory is spent (B-2123): the sheet's size unzipped (``SHEET_MOST``), every other part's
(``PART_MOST``), how many parts (``PARTS_MOST``), the longest cell (``CELL_MOST``, looked for in the sheet's XML
before it is parsed), the columns (``COLUMNS_MOST``) and the rows (``chart.ROWS_MOST``). Measured on files made up
at each bound (November 2026, a shared machine): a sheet of 249 MB as wide as the export is read in 39 s holding 7 MB
more; 200,000 rows in 18 s holding 40 MB, their import 2 s under the write lock; a file of 500,000 rows is refused
after 15 s with nothing held. A file that is no such workbook is refused in plain words that say what to do, never
with a traceback (B-2124).

Entry points: ``read_chart``, ``read_business_units`` (a path, or an upload's bytes), giving ``chart.Rows``;
``read_rows``, the rows as they are, for another of JD Edwards' long exports (the vendor information list, D-219).
"""

from __future__ import annotations

import io
import itertools
import zipfile
import zlib
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import chart
from .chart import ChartError

SHEET_MOST = 250_000_000  # a sheet of one of these exports, unzipped (the chart's: 104 MB): read a row at a time
PART_MOST = 20_000_000  # any other file inside the workbook, unzipped (the workbook's texts are read whole)
PARTS_MOST = 1000  # the files inside the workbook (an export has nine)
CELL_MOST = 32_768  # a cell's text (Excel's own limit): a longer run of the sheet's XML with no tag is no cell of ours
COLUMNS_MOST = 1000  # the columns of the first row (the chart's export has 110)
HEADER_ROWS = 10  # the rows the column names are looked for in: a grid's title rows stand above them (B-2273)
MB = 1_000_000
OLD_EXCEL = bytes.fromhex("D0CF11E0A1B11AE1")  # how an .xls, and an .xlsx protected with a password, begins
# What openpyxl and the zip and XML readers under it raise for a file that is no readable workbook.
UNREADABLE = (OSError, SyntaxError, IndexError, AttributeError, EOFError, KeyError, TypeError, ValueError,
              zlib.error, zipfile.BadZipFile)  # fmt: skip


def _start(source: Any) -> bytes:
    if isinstance(source, Path):
        with source.open("rb") as file:
            return file.read(len(OLD_EXCEL))
    return source.getvalue()[: len(OLD_EXCEL)]


def _refuse_too_big(source: Any, name: str) -> None:
    """``ChartError`` for a file that is no .xlsx, or that unzips to more than these exports do, or holds a cell
    longer than a cell can be: the zip's directory is read, then the sheets' XML looked through a block at a time,
    nothing kept."""
    try:
        with zipfile.ZipFile(source) as z:
            parts = [(item.filename, item.file_size) for item in z.infolist()]
            if len(parts) > PARTS_MOST:
                raise ChartError(f"{name} holds {len(parts):,} files inside, far more than a workbook exported from "
                                 "JD Edwards, so it was not opened.")  # fmt: skip
            for part, size in parts:
                most = SHEET_MOST if part.startswith("xl/worksheets/") else PART_MOST
                if size > most:
                    raise ChartError(f"{name} unzips to more than a chart of accounts does ({size // MB:,} MB in one "
                                     f"part; at most {most // MB:,} MB is read), so it was not opened.")  # fmt: skip
            for part, size in parts:
                if part.startswith("xl/worksheets/") and size > CELL_MOST:
                    _refuse_long_cell(z, part, name)
    except ChartError:
        raise
    except (zipfile.BadZipFile, ValueError) as exc:
        if _start(source) == OLD_EXCEL:
            raise ChartError(f"{name} is protected with a password, or is an older Excel file (.xls), so it was not "
                             "read. Open it in Excel, remove the password and save it as .xlsx, or export it from "
                             "JD Edwards again.") from exc  # fmt: skip
        raise ChartError(f"{name} is not an Excel workbook (.xlsx), so it was not read. Use the export as JD Edwards "
                         "saves it.") from exc  # fmt: skip


def _refuse_long_cell(z: zipfile.ZipFile, part: str, name: str) -> None:
    """``ChartError`` when the sheet's XML runs ``CELL_MOST`` bytes with no tag at all: a cell longer than Excel
    itself allows, which the XML reader would hold whole (40 MB of it took 270 MB). A run that long covers one of
    the blocks of half that size, so only whether each block holds a ``<`` is looked at."""
    block = CELL_MOST // 2
    with z.open(part) as sheet:
        while chunk := sheet.read(block * 64):
            for at in range(0, len(chunk) - block + 1, block):
                if b"<" not in chunk[at : at + block]:
                    raise ChartError(f"{name} holds a cell far longer than any of the export's (over {block:,} "
                                     "characters), so it was not opened. Export it from JD Edwards again and use "
                                     "the file as it comes.")  # fmt: skip


def _columns(rows: Iterator[tuple[Any, ...]], columns_of: Callable[[list[str], str], dict[str, str]], name: str,
             sheet: str, about: dict[str, Any]) -> list[tuple[int, str]]:  # fmt: skip
    """(where, header) of the columns to keep, from the row of the export's column names: the sheet's first row, or
    one of the ``HEADER_ROWS`` first under a grid's title rows (no row to delete). ``about`` is told which row it is
    (``header_row``) and the columns found (``found``: {field: header}). ``ChartError`` naming the file when no such
    row is there: an empty sheet, far too many columns, another file."""
    first = next(rows, ())
    headers = ["" if h is None else str(h) for h in first]
    if len(headers) > COLUMNS_MOST:
        raise ChartError(f"{name} has {len(headers):,} columns, far more than the export has, so it was not read. "
                         "Export it from JD Edwards again and use the file as it comes.")  # fmt: skip
    number = 1
    try:
        found = columns_of(headers, name)
    except ChartError as missing:
        below, found = [first], None
        while found is None and number < HEADER_ROWS:
            row = next(rows, None)
            if row is None:
                break
            number += 1
            below.append(row)
            headers = ["" if h is None else str(h) for h in row[:COLUMNS_MOST]]
            try:
                found = columns_of(headers, name)
            except ChartError:
                continue
        if found is None and not any(str(cell).strip() for row in below for cell in row if cell is not None):
            raise ChartError(f"{name}: its first sheet (“{sheet}”) is empty, so it was not read. The export must be "
                             "the first sheet of the workbook, its column names in row 1.") from None  # fmt: skip
        if found is None:
            raise missing from None
    about.update(header_row=number, found=found)
    return sorted((headers.index(column), column) for column in found.values())


def read_export(source: str | Path | bytes, columns_of: Callable[[list[str], str], dict[str, str]],
                about: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:  # fmt: skip
    """The first sheet's rows, one at a time, each as {header: cell}, of the columns the import needs only: those
    ``columns_of`` finds among the headers (``chart.chart_columns`` or ``chart.bu_columns``). ``source``: the file's
    path, or its bytes. ``about``: a dict told, before the first row is given, the row the column names are on
    (``header_row``) and the columns found (``found``). ``ChartError``, in plain words that name the file, when it
    is not that export or cannot be read; a file open in another program is the caller's to say
    (``PermissionError``)."""
    name = chart.FILE if isinstance(source, bytes) else Path(source).name
    with _first_sheet(source, name) as sheet:
        rows = sheet.iter_rows(values_only=True)
        kept = _columns(rows, columns_of, name, sheet.title, {} if about is None else about)
        for row in rows:
            yield {column: row[at] if at < len(row) else None for at, column in kept}


def read_rows(source: str | Path | bytes, name: str = chart.FILE) -> Iterator[tuple[Any, ...]]:
    """The first sheet's rows as they are, one at a time, for an export read by where its columns are (the vendor
    information list has two columns of one name): bounded as ``read_export`` is, and refused in the same plain
    words (``ChartError``), which call the file ``name``."""
    with _first_sheet(source, name) as sheet:
        yield from sheet.iter_rows(values_only=True)


@contextmanager
def _first_sheet(source: str | Path | bytes, name: str) -> Iterator[Any]:
    """The workbook's first sheet, opened to be read a row at a time once the file is within its bounds, and closed
    at the end. ``ChartError`` for a file that is none, or cannot be read (while it is read too)."""
    import openpyxl

    opened = io.BytesIO(source) if isinstance(source, bytes) else Path(source)
    workbook = None
    try:
        _refuse_too_big(opened, name)
        workbook = openpyxl.load_workbook(opened, read_only=True, data_only=True)
        sheet = workbook.worksheets[0]
        sheet.reset_dimensions()  # the rows as they are, whatever size the file says the sheet has
        yield sheet
    except (ChartError, PermissionError):
        raise
    except UNREADABLE as exc:
        raise ChartError(f"{name} could not be read as an Excel workbook, so nothing was read: open it in Excel and "
                         "save it again as .xlsx, or export it from JD Edwards again.") from exc  # fmt: skip
    finally:
        if workbook is not None:
            workbook.close()


def _read(source: str | Path | bytes, columns_of: Callable[[list[str], str], dict[str, str]],
          rows_of: Callable[..., chart.Rows], wants: Iterable[str] = (),
          anyway: bool = True) -> chart.Rows:  # fmt: skip
    """The export's clean rows; what the rows' own rules refuse (``chart.chart_rows``) is said with the file's
    name. An export without a column of ``wants`` is refused before a row is kept (``chart.ColumnsMissing``),
    unless ``anyway``: its rows then say which columns they came without (``Rows.without``)."""
    name = chart.FILE if isinstance(source, bytes) else Path(source).name
    about: dict[str, Any] = {}
    records = read_export(source, columns_of, about)
    try:
        first = next(records, None)  # (the column names are read with the first row)
        without = tuple(field for field in wants if field not in about["found"])
        if without and not anyway:
            raise chart.ColumnsMissing(name, without)
        rows = itertools.chain(() if first is None else (first,), records)
        read = rows_of(rows, about["found"], about["header_row"])
        read.without = without
        return read
    except ChartError as exc:
        if name == chart.FILE or name in str(exc):
            raise
        raise ChartError(f"{name}: {exc}") from exc.__cause__
    finally:
        records.close()


def read_chart(source: str | Path | bytes, anyway: bool = False) -> chart.Rows:
    """The account master's export as clean rows (``chart.chart_rows``). ``chart.ColumnsMissing`` for an export
    without its posting edit or subsidiary column, unless ``anyway`` (``Rows.without`` then names them)."""
    return _read(source, chart.chart_columns, chart.chart_rows, chart.CHART_WANTS, anyway)


def read_business_units(source: str | Path | bytes) -> chart.Rows:
    """The business unit master's export as clean rows (``chart.bu_rows``)."""
    return _read(source, chart.bu_columns, chart.bu_rows)
