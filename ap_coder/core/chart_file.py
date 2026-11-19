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
``read_rows``, the rows as they are, for another of JD Edwards' long exports (the vendor information list, D-219);
``sheet_heads``, each sheet's first rows; ``sheet_survey``, each sheet's rows counted without reading them (D-271).
"""

from __future__ import annotations

import codecs
import csv
import html
import io
import itertools
import re
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
    about.update(header_row=number, found=found, width=len(headers))
    return sorted((headers.index(column), column) for column in found.values())


def read_export(source: str | Path | bytes, columns_of: Callable[[list[str], str], dict[str, str]],
                about: dict[str, Any] | None = None, sheet: str = "") -> Iterator[dict[str, Any]]:  # fmt: skip
    """The first sheet's rows, one at a time, each as {header: cell}, of the columns the import needs only: those
    ``columns_of`` finds among the headers (``chart.chart_columns`` or ``chart.bu_columns``). ``source``: the file's
    path, or its bytes. ``about``: a dict told, before the first row is given, the row the column names are on
    (``header_row``) and the columns found (``found``); ``sheet``: the sheet of that name (a workbook of several
    reports, D-266). ``ChartError``, in plain words that name the file, when it is not that export or cannot be read;
    a file open in another program is the caller's to say (``PermissionError``)."""
    name = chart.FILE if isinstance(source, bytes) else Path(source).name
    with _first_sheet(source, name, sheet) as opened:
        rows = opened.iter_rows(values_only=True)
        kept = _columns(rows, columns_of, name, opened.title, {} if about is None else about)
        for row in rows:
            yield {column: row[at] if at < len(row) else None for at, column in kept}


def read_rows(source: str | Path | bytes, name: str = chart.FILE, sheet: str = "") -> Iterator[tuple[Any, ...]]:
    """The first sheet's rows as they are (``sheet``: the sheet of that name), one at a time, for an export read by
    where its columns are (the vendor information list has two columns of one name; the voucher list, B-2433):
    bounded as ``read_export`` is, and refused in the same plain words (``ChartError``), which call the file
    ``name``."""
    with _first_sheet(source, name, sheet) as opened:
        yield from opened.iter_rows(values_only=True)


def sheet_heads(source: bytes, name: str = chart.FILE, most: int = HEADER_ROWS) -> list[tuple[str, list[tuple]]]:
    """Each sheet's name and its ``most`` first rows, of a workbook within the bounds ``read_rows`` reads in (a
    report put on another sheet than the first is found by its name). ``ChartError`` as ``read_rows``'s."""
    import openpyxl

    opened = io.BytesIO(source)
    _refuse_too_big(opened, name)
    try:
        workbook = openpyxl.load_workbook(opened, read_only=True, data_only=True)
    except UNREADABLE as exc:
        raise ChartError(f"{name} could not be read as an Excel workbook, so nothing was read: open it in Excel and "
                         "save it again as .xlsx, or export it from JD Edwards again.") from exc  # fmt: skip
    try:
        return [(sheet.title, [row[:COLUMNS_MOST] for row in itertools.islice(sheet.iter_rows(values_only=True), most)])
                for sheet in workbook.worksheets]  # fmt: skip
    finally:
        workbook.close()


# The workbook's XML as ``sheet_survey`` reads it, a sheet a block at a time with no XML reader (D-271): a row, a cell,
# a cell's reference ("C12"), type and value, an inline string's text; a namespace prefix ("x:row") is allowed.
_ROW = re.compile(rb"<(?:\w+:)?row\b([^>]*?)(?:/>|>(.*?)</(?:\w+:)?row>)", re.S)
_CELL = re.compile(rb"<(?:\w+:)?c\b([^>]*?)(?:/>|>(.*?)</(?:\w+:)?c>)", re.S)
_ROW_NUMBER = re.compile(rb'\br="(\d+)"')
_REFERENCE = re.compile(rb'\br="([A-Z]+)\d*"')
_TYPE = re.compile(rb'\bt="(\w+)"')
_VALUE = re.compile(rb"<(?:\w+:)?v>(.*?)</(?:\w+:)?v>", re.S)
_TEXT = re.compile(rb"<(?:\w+:)?t(?:\s[^>]*)?>(.*?)</(?:\w+:)?t>", re.S)
_SHEET = re.compile(rb"<(?:\w+:)?sheet\b[^>]*?>")
_ATTRIBUTE = re.compile(rb'([\w:]+)="([^"]*)"')
BLOCK = 8 * MB  # of a sheet's XML read at a time


def _column(letters: bytes) -> int:
    """A cell reference's column as a position from 0 ("A" is 0, "AA" 26)."""
    n = 0
    for letter in letters:
        n = n * 26 + letter - 64
    return n - 1


def _parts(z: zipfile.ZipFile) -> tuple[dict[str, str], str]:
    """({sheet title: its part in the zip}, the shared strings' part, "" when there is none)."""
    rels = {}
    for tag in re.findall(rb"<(?:\w+:)?Relationship\b[^>]*?>", z.read("xl/_rels/workbook.xml.rels")):
        found = {k.decode(): html.unescape(v.decode()) for k, v in _ATTRIBUTE.findall(tag)}
        target = found.get("Target", "")
        target = target.lstrip("/") if target.startswith("/") else f"xl/{target}"
        rels[found.get("Id", "")] = (found.get("Type", ""), target)
    sheets = {}
    for tag in _SHEET.findall(z.read("xl/workbook.xml")):
        found = {k.decode(): html.unescape(v.decode()) for k, v in _ATTRIBUTE.findall(tag)}
        rid = next((v for k, v in found.items() if k.endswith(":id")), "")
        if rid in rels:
            sheets[found.get("name", "")] = rels[rid][1]
    shared = next((target for kind, target in rels.values() if kind.endswith("/sharedStrings")), "")
    return sheets, shared if shared in z.namelist() else ""


def _shared(z: zipfile.ZipFile, part: str) -> list[str]:
    """The workbook's shared strings, in order, each its text (a rich one's runs joined), stripped."""
    if not part:
        return []
    out = []
    for item in re.finditer(rb"<(?:\w+:)?si\b[^>]*>(.*?)</(?:\w+:)?si>", z.read(part), re.S):
        out.append(html.unescape(b"".join(_TEXT.findall(item.group(1))).decode("utf-8", "replace")).strip())
    return out


def _text_of(attributes: bytes, inner: bytes, shared: list[str]) -> str:
    """A cell's value as ``_cells`` makes openpyxl's (stripped text; a whole number without ".0")."""
    kind = _TYPE.search(attributes)
    kind = kind.group(1) if kind else b"n"
    if kind == b"inlineStr":
        return html.unescape(b"".join(_TEXT.findall(inner)).decode("utf-8", "replace")).strip()
    value = _VALUE.search(inner)
    text = html.unescape(value.group(1).decode("utf-8", "replace")).strip() if value else ""
    if kind == b"s":
        return shared[int(text)] if text.isdigit() and int(text) < len(shared) else ""
    if kind == b"n" and text:
        try:
            return str(float(text)) if any(c in text for c in ".eE") else str(int(text))
        except ValueError:
            return text
    return text


def _last_row_end(xml: bytes) -> int:
    """Where the last whole row of a block of a sheet's XML ends (after its "</row>", or "</x:row>"); 0: none yet."""
    at = len(xml)
    while (at := xml.rfind(b"row>", 0, at)) >= 0:
        start = xml.rfind(b"</", 0, at)
        if start >= 0 and re.fullmatch(rb"</(?:\w+:)?", xml[start:at]):
            return at + len(b"row>")
    return 0


def _tally(rows: bytes, shared: list[str], below: int, company: int | None, seen: list[Any]) -> None:
    """Counts the rows of a block of a sheet's XML into ``seen`` ([the last row's number, rows, {company: rows}]):
    those numbered over ``below`` with a cell filled, each by its cell in column ``company`` (None: none)."""
    for row in _ROW.finditer(rows):
        number = _ROW_NUMBER.search(row.group(1))
        seen[0] = int(number.group(1)) if number else seen[0] + 1
        if seen[0] <= below or not row.group(2):
            continue
        filled, theirs, at = False, "", -1
        for cell in _CELL.finditer(row.group(2)):
            reference = _REFERENCE.search(cell.group(1))
            at = _column(reference.group(1)) if reference else at + 1
            if at == company:
                theirs = _text_of(cell.group(1), cell.group(2) or b"", shared)
                filled = filled or bool(theirs)
            elif not filled:
                filled = bool(_text_of(cell.group(1), cell.group(2) or b"", shared))
            if filled and (company is None or at >= company):
                break
        if filled:
            seen[1] += 1
            if theirs:
                seen[2][theirs] = seen[2].get(theirs, 0) + 1


def _blocks(z: zipfile.ZipFile, part: str) -> Iterator[bytes]:
    """A sheet's XML a block of whole rows at a time (``BLOCK``, cut after a row's end)."""
    with z.open(part) as sheet:
        left = b""
        while block := sheet.read(BLOCK):
            left += block
            cut = _last_row_end(left)
            if cut:
                yield left[:cut]
                left = left[cut:]
        yield left


def _first_rows(z: zipfile.ZipFile, part: str, shared: list[str], most: int) -> list[list[str]]:
    """A sheet's rows 1 to ``most``, each its cells' texts (``_text_of``) in their columns, "" where there is none."""
    rows: list[list[str]] = []
    for block in _blocks(z, part):
        for row in _ROW.finditer(block):
            number = _ROW_NUMBER.search(row.group(1))
            number = int(number.group(1)) if number else len(rows) + 1
            if number > most:
                return rows
            rows += [[] for _ in range(number - 1 - len(rows))]  # (rows with no cell, which the XML leaves out)
            cells: list[str] = []
            for cell in _CELL.finditer(row.group(2) or b""):
                reference = _REFERENCE.search(cell.group(1))
                at = _column(reference.group(1)) if reference else len(cells)
                cells += [""] * (at - len(cells)) + [_text_of(cell.group(1), cell.group(2) or b"", shared)]
            rows.append(cells[:COLUMNS_MOST])
    return rows


def sheet_survey(source: bytes, pick: Callable[[str, list[list[str]]], tuple[int, int | None] | None],
                 name: str = chart.FILE, most: int = HEADER_ROWS) -> list[tuple[str, int, dict[str, int]]]:  # fmt: skip
    """Each sheet of the workbook, in order: (its title, how many rows under its column names have a cell filled, how
    many of them each company has). ``pick(title, its first ``most`` rows as text)`` says where a sheet's column names
    are (their row number from 1) and its company column's position (None: none); None for a sheet not counted
    (0 rows). The rows are read in the sheets' XML a block at a time, no cell made of them, of each row the cells up to
    one filled and the company's: a workbook of 50 MB in seconds, where reading every row as ``read_rows`` does took
    minutes (D-271). Within the bounds ``read_rows`` reads in; ``ChartError`` as its."""
    opened = io.BytesIO(source)
    _refuse_too_big(opened, name)
    out: list[tuple[str, int, dict[str, int]]] = []
    try:
        with zipfile.ZipFile(opened) as z:
            sheets, strings = _parts(z)
            shared = _shared(z, strings)
            for title, part in sheets.items():
                if not part.startswith("xl/worksheets/"):
                    continue  # (a chart sheet: no rows)
                picked = pick(title, _first_rows(z, part, shared, most))
                seen: list[Any] = [0, 0, {}]
                for block in _blocks(z, part) if picked is not None else ():
                    _tally(block, shared, picked[0], picked[1], seen)
                out.append((title, seen[1], seen[2]))
    except UNREADABLE as exc:
        raise ChartError(f"{name} could not be read as an Excel workbook, so nothing was read: open it in Excel and "
                         "save it again as .xlsx, or export it from JD Edwards again.") from exc  # fmt: skip
    return out


@contextmanager
def _first_sheet(source: str | Path | bytes, name: str, title: str = "") -> Iterator[Any]:
    """The workbook's first sheet (``title``: the sheet of that name), opened to be read a row at a time once the
    file is within its bounds, and closed at the end. ``ChartError`` for a file that is none, or cannot be read
    (while it is read too)."""
    import openpyxl

    opened = io.BytesIO(source) if isinstance(source, bytes) else Path(source)
    workbook = None
    try:
        _refuse_too_big(opened, name)
        workbook = openpyxl.load_workbook(opened, read_only=True, data_only=True)
        sheet = workbook[title] if title in workbook.sheetnames else workbook.worksheets[0]
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
          anyway: bool = True, sheet: str = "") -> chart.Rows:  # fmt: skip
    """The export's clean rows; what the rows' own rules refuse (``chart.chart_rows``) is said with the file's
    name. An export without a column of ``wants`` is refused before a row is kept (``chart.ColumnsMissing``),
    unless ``anyway``: its rows then say which columns they came without (``Rows.without``)."""
    name = chart.FILE if isinstance(source, bytes) else Path(source).name
    about: dict[str, Any] = {}
    records = read_export(source, columns_of, about, sheet)
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


def read_chart(source: str | Path | bytes, anyway: bool = False, sheet: str = "") -> chart.Rows:
    """The account master's export as clean rows (``chart.chart_rows``). ``chart.ColumnsMissing`` for an export
    without its posting edit or subsidiary column, unless ``anyway`` (``Rows.without`` then names them). ``sheet``:
    the sheet of that name (else the first)."""
    return _read(source, chart.chart_columns, chart.chart_rows, chart.CHART_WANTS, anyway, sheet)


TEXT_FILES = (".csv", ".txt", ".tsv")  # a chart saved as text, streamed from disk by the command line (D-267, D-272)
SNIFF = 1 << 20  # of a text file, the bytes its encoding is told by


def text_encoding(path: str | Path) -> str:
    """How a text file is encoded, by its first bytes: UTF-8 or UTF-16 (Windows' "Unicode text", ReportsNow's) with
    or without its byte-order mark; text that is neither UTF-8 nor UTF-16 is read as Windows' own (cp1252)."""
    with open(path, "rb") as file:
        head = file.read(SNIFF)
    if head.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if head.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"
    odd, even = head[1::2], head[0::2]
    if odd and odd.count(0) > len(odd) // 2:  # (letters of one byte each followed by a nought: UTF-16, low byte first)
        return "utf-16-le"
    if even and even.count(0) > len(even) // 2:
        return "utf-16-be"
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as exc:
        if exc.start < len(head) - 4:  # (not merely a character cut at the end of the bytes looked at)
            return "cp1252"
    return "utf-8"


def _tab_rows(handle: Any) -> Iterator[tuple[str, ...]]:
    """A tab-delimited file's rows as JD Edwards' query tools write them: each line one row, its cells between tabs,
    never quoted (a quotation mark in a description is text, D-272), lines ended by a line feed (a carriage return
    before it dropped)."""
    for line in handle:
        yield tuple(line.rstrip("\n").rstrip("\r").split("\t"))


def stream_chart_text(path: str | Path, anyway: bool = False) -> tuple[chart.Rows, Iterator[chart.ChartRow]]:
    """A chart of accounts saved as text, read from disk a row at a time: CSV (its column names in one of its first
    rows: the account master's export, or the build sheet's query, D-267) or tab-delimited (.txt, .tsv: ReportsNow's
    text, D-272), UTF-8 or UTF-16 with or without its byte-order mark (``text_encoding``). Returns (what was read
    and left out, with a few rows of each reason, and the columns it came without, as they are read; its clean rows,
    given one at a time and never held, so a chart of millions of accounts is read in the memory of one). A codes'
    leading zeros and padding come as the file has them (the store keeps them without the padding, as JD Edwards
    compares them). A tab-delimited row whose cells do not line up with the column names (more of them, or cut short
    before a column read) is left out, never read shifted. Up to ``chart.STREAMED_MOST`` rows;
    ``chart.ColumnsMissing`` as ``read_chart``'s, unless ``anyway``; ``ChartError`` naming the file when it is no
    chart, or not text."""
    name = Path(path).name
    handle = open(path, encoding=text_encoding(path), newline="\n")  # noqa: SIM115  (closed by the rows once read)
    try:
        first = handle.readline()
        handle.seek(0)
        tabbed = "\t" in first
        rows = _tab_rows(handle) if tabbed else (tuple(row) for row in csv.reader(handle))
        about: dict[str, Any] = {}
        kept = _columns(rows, chart.chart_columns, name, "text", about)
        without = tuple(field for field in chart.CHART_WANTS if field not in about["found"])
        if without and not anyway:
            raise chart.ColumnsMissing(name, without)
    except UnicodeDecodeError as exc:
        handle.close()
        raise ChartError(f"{name} could not be read as text, so it was not read. Save it from the query tool as "
                         "Unicode text, or UTF-8.") from exc  # fmt: skip
    except BaseException:
        handle.close()
        raise
    out = chart.Rows(without=without)
    width, last = about["width"], max(at for at, _column in kept)

    def records() -> Iterator[dict[str, Any] | None]:
        with handle:
            try:
                for row in rows:
                    if tabbed and len(row) > 1 and (len(row) <= last or (len(row) > width and any(row[width:]))):
                        yield None  # (cut short, or a tab inside a value: its cells would be read in the wrong columns)
                        continue
                    yield {column: row[at] if at < len(row) else None for at, column in kept}
            except UnicodeDecodeError as exc:
                raise ChartError(f"{name}: row {about['header_row'] + out.parsed + 1:,} could not be read as text, so "
                                 "the file was not read. Save it from the query tool as Unicode text, or UTF-8, and "
                                 "import it again.") from exc  # fmt: skip

    return out, chart.iter_chart_rows(records(), out, about["found"], about["header_row"], chart.STREAMED_MOST)


def read_business_units(source: str | Path | bytes, sheet: str = "") -> chart.Rows:
    """The business unit master's export as clean rows (``chart.bu_rows``); ``sheet`` as ``read_chart``'s."""
    return _read(source, chart.bu_columns, chart.bu_rows, sheet=sheet)
