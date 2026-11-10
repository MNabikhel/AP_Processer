"""The chart of accounts and the business unit master read from JD Edwards' exports as they come (D-209): an .xlsx
with its column names in row 1, nearly every cell stored as text, a business unit padded with spaces and an empty
field a lone space.

The account master is wide and long (110 columns, about 14,700 rows): its one sheet unzips to about 104 MB, far over
what any other spreadsheet AP Coder opens may (``intake/triage.sheet_unzips_too_big``: 20 MB a sheet). So this
reader, and only it, has a higher limit of its own (``SHEET_MOST``), and never holds the sheet: it is read a row at a
time and only the columns the import needs are kept (six of the chart's). Entry points: ``read_chart``,
``read_business_units`` (a path, or an upload's bytes), giving ``chart.Rows``.
"""

from __future__ import annotations

import datetime as dt
import io
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import chart
from .chart import ChartError

SHEET_MOST = 400_000_000  # a sheet of one of these exports, unzipped: read a row at a time, never held whole
PART_MOST = 20_000_000  # any other file inside the workbook, unzipped (the workbook's texts are read whole)
MB = 1_000_000


def _text(value: Any) -> Any:
    """A cell as the rows' cleaning takes it: text as it is (its padding too), a whole number without ".0"."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return value


def _refuse_too_big(source: Any, name: str) -> None:
    """``ChartError`` for a file that is no .xlsx, or that unzips to more than these exports do: only the zip's
    directory is read, nothing is unzipped."""
    try:
        with zipfile.ZipFile(source) as z:
            parts = [(item.filename, item.file_size) for item in z.infolist()]
    except (zipfile.BadZipFile, ValueError) as exc:
        raise ChartError(f"{name} is not an Excel workbook (.xlsx), so it was not read. Use the export as JD Edwards "
                         "saves it.") from exc  # fmt: skip
    for part, size in parts:
        most = SHEET_MOST if part.startswith("xl/worksheets/") else PART_MOST
        if size > most:
            raise ChartError(f"{name} unzips to more than a chart of accounts does ({size // MB:,} MB in one part; "
                             f"at most {most // MB:,} MB is read), so it was not opened.")  # fmt: skip


def read_export(source: str | Path | bytes,
                columns_of: Callable[[list[str]], dict[str, str]]) -> list[dict[str, Any]]:  # fmt: skip
    """The first sheet's rows as {header: cell}, of the columns the import needs only: those ``columns_of`` finds
    among the headers (``chart.chart_columns`` or ``chart.bu_columns``; ``ChartError`` when the file is not that
    export). ``source``: the file's path, or its bytes."""
    import openpyxl

    name = "This file" if isinstance(source, bytes) else Path(source).name
    opened = io.BytesIO(source) if isinstance(source, bytes) else Path(source)
    _refuse_too_big(opened, name)
    try:
        workbook = openpyxl.load_workbook(opened, read_only=True, data_only=True)
    except (KeyError, ValueError, zipfile.BadZipFile) as exc:
        raise ChartError(f"{name} could not be opened as an Excel workbook: save it again as .xlsx.") from exc
    try:
        sheet = workbook.worksheets[0]
        sheet.reset_dimensions()  # the rows as they are, whatever size the file says the sheet has
        rows = sheet.iter_rows(values_only=True)
        headers = ["" if h is None else str(h) for h in next(rows, ())]
        found = columns_of(headers)
        kept = sorted((headers.index(column), column) for column in found.values())
        return [{column: _text(row[at]) if at < len(row) else None for at, column in kept} for row in rows]
    finally:
        workbook.close()


def read_chart(source: str | Path | bytes) -> chart.Rows:
    """The account master's export as clean rows (``chart.chart_rows``)."""
    return chart.chart_rows(read_export(source, chart.chart_columns))


def read_business_units(source: str | Path | bytes) -> chart.Rows:
    """The business unit master's export as clean rows (``chart.bu_rows``)."""
    return chart.bu_rows(read_export(source, chart.bu_columns))
