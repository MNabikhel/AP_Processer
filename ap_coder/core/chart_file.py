"""The chart of accounts and the business unit master read from JD Edwards' exports as they come (D-209): an .xlsx
with its column names in row 1, nearly every cell stored as text, a business unit padded with spaces and an empty
field a lone space.

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

Entry points: ``read_chart``, ``read_business_units`` (a path, or an upload's bytes), giving ``chart.Rows``.
"""

from __future__ import annotations

import io
import zipfile
import zlib
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from . import chart
from .chart import ChartError

SHEET_MOST = 250_000_000  # a sheet of one of these exports, unzipped (the chart's: 104 MB): read a row at a time
PART_MOST = 20_000_000  # any other file inside the workbook, unzipped (the workbook's texts are read whole)
PARTS_MOST = 1000  # the files inside the workbook (an export has nine)
CELL_MOST = 32_768  # a cell's text (Excel's own limit): a longer run of the sheet's XML with no tag is no cell of ours
COLUMNS_MOST = 1000  # the columns of the first row (the chart's export has 110)
HEADER_ROWS = 10  # the rows looked at for column names that are not in row 1, to say where they are
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
             sheet: str) -> list[tuple[int, str]]:  # fmt: skip
    """(where, header) of the columns to keep, from the sheet's first row. ``ChartError`` naming the file when the
    first row is not the export's column names: an empty sheet, far too many columns, or the names lower down."""
    first = next(rows, ())
    headers = ["" if h is None else str(h) for h in first]
    if len(headers) > COLUMNS_MOST:
        raise ChartError(f"{name} has {len(headers):,} columns, far more than the export has, so it was not read. "
                         "Export it from JD Edwards again and use the file as it comes.")  # fmt: skip
    try:
        found = columns_of(headers, name)
    except ChartError as missing:
        below = [first]
        for number in range(2, HEADER_ROWS + 1):
            row = next(rows, None)
            if row is None:
                break
            below.append(row)
            try:
                columns_of(["" if h is None else str(h) for h in row[:COLUMNS_MOST]], name)
            except ChartError:
                continue
            raise ChartError(f"{name}: the column names are in row {number}, not in row 1, so it was not read. "
                             "Delete the rows above them and save the file, or export it from JD Edwards again."
                             ) from None  # fmt: skip
        if not any(str(cell).strip() for row in below for cell in row if cell is not None):
            raise ChartError(f"{name}: its first sheet (“{sheet}”) is empty, so it was not read. The export must be "
                             "the first sheet of the workbook, its column names in row 1.") from None  # fmt: skip
        raise missing
    return sorted((headers.index(column), column) for column in found.values())


def read_export(source: str | Path | bytes,
                columns_of: Callable[[list[str], str], dict[str, str]]) -> Iterator[dict[str, Any]]:  # fmt: skip
    """The first sheet's rows, one at a time, each as {header: cell}, of the columns the import needs only: those
    ``columns_of`` finds among the headers (``chart.chart_columns`` or ``chart.bu_columns``). ``source``: the file's
    path, or its bytes. ``ChartError``, in plain words that name the file, when it is not that export or cannot be
    read; a file open in another program is the caller's to say (``PermissionError``)."""
    import openpyxl

    name = chart.FILE if isinstance(source, bytes) else Path(source).name
    opened = io.BytesIO(source) if isinstance(source, bytes) else Path(source)
    workbook = None
    try:
        _refuse_too_big(opened, name)
        workbook = openpyxl.load_workbook(opened, read_only=True, data_only=True)
        sheet = workbook.worksheets[0]
        sheet.reset_dimensions()  # the rows as they are, whatever size the file says the sheet has
        rows = sheet.iter_rows(values_only=True)
        kept = _columns(rows, columns_of, name, sheet.title)
        for row in rows:
            yield {column: row[at] if at < len(row) else None for at, column in kept}
    except (ChartError, PermissionError):
        raise
    except UNREADABLE as exc:
        raise ChartError(f"{name} could not be read as an Excel workbook, so nothing was read: open it in Excel and "
                         "save it again as .xlsx, or export it from JD Edwards again.") from exc  # fmt: skip
    finally:
        if workbook is not None:
            workbook.close()


def _read(source: str | Path | bytes, columns_of: Callable[[list[str], str], dict[str, str]],
          rows_of: Callable[[Iterator[dict[str, Any]]], chart.Rows]) -> chart.Rows:  # fmt: skip
    """The export's clean rows; what the rows' own rules refuse (``chart.chart_rows``) is said with the file's
    name."""
    name = chart.FILE if isinstance(source, bytes) else Path(source).name
    try:
        return rows_of(read_export(source, columns_of))
    except ChartError as exc:
        if name == chart.FILE or name in str(exc):
            raise
        raise ChartError(f"{name}: {exc}") from exc.__cause__


def read_chart(source: str | Path | bytes) -> chart.Rows:
    """The account master's export as clean rows (``chart.chart_rows``)."""
    return _read(source, chart.chart_columns, chart.chart_rows)


def read_business_units(source: str | Path | bytes) -> chart.Rows:
    """The business unit master's export as clean rows (``chart.bu_rows``)."""
    return _read(source, chart.bu_columns, chart.bu_rows)
