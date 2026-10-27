"""Files in and out of the dashboard: downloads, and spreadsheets uploaded to be imported.

Downloads are GET routes answered with ``download(data, name, mime)``: the file with its content type and name.

An import from a spreadsheet takes two steps: the clerk uploads the file, then checks
which column holds what before anything is imported. The uploaded file waits under a one-off token
(``keep_upload``) for an hour; the page reads it again with ``read_upload`` (``?file=<token>``) to show its first rows
and the column choices (``sheet_context``), and the import POST names the token. It waits in the dashboard's
database (a restart of the server keeps it), never as a file of its own, and is deleted after the hour (D-99).
Uploads larger than ``_MAX_BYTES`` are not read (``read_capped``); every page reads a CSV with ``read_csv``.
"""

from __future__ import annotations

import io
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import pandas as pd
from fastapi import UploadFile
from fastapi.responses import Response

from ap_coder.core.csvio import sniff_delimiter
from ap_coder.intake import triage
from ap_coder.web.core import current_store

SHEET_TYPES = (".csv", ".xlsx", ".xls")
NONE = ""  # the value of "(none)" in a column choice
PREVIEW_ROWS = 5

CANNOT_READ = (
    "This file could not be read as a spreadsheet: it may be empty, damaged or another format. In Excel, save it as "
    "CSV UTF-8 or Excel Workbook (.xlsx) and upload it again."
)
NOT_A_SHEET = "Upload a CSV or Excel file (.csv or .xlsx)."
NO_ROWS = "This file has no rows under its header line."
GONE = "That upload is no longer here (uploads are kept for an hour). Upload the file again."
TOO_BIG_UNZIPPED = (
    "This workbook unzips to far more than its size on disk (over 50 MB, or one sheet over 20 MB), so it is not "
    "opened: it may be damaged. Export fewer rows, or save it as CSV UTF-8 and upload that."
)
TOO_BIG = "This file is larger than 50 MB. Export fewer rows, or only the columns needed."


# --- Downloads ---------------------------------------------------------------------------------------------------

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def download(data: bytes, name: str, mime: str) -> Response:
    """``data`` as a file download called ``name`` (any characters: the plain-ASCII name is a fallback)."""
    plain = "".join(c if c.isascii() and c.isprintable() and c not in '"\\' else "_" for c in name) or "download"
    disposition = f"attachment; filename=\"{plain}\"; filename*=UTF-8''{quote(name, safe='')}"
    return Response(
        content=data, media_type=mime, headers={"Content-Disposition": disposition, "Cache-Control": "no-store"}
    )


# --- Uploads waiting for their columns to be picked -------------------------------------------------------------

_TTL = 3600  # seconds an upload waits
_MAX = 24  # uploads kept at most (the oldest go first)
_MAX_BYTES = 50 * 1024 * 1024  # an uploaded file, and an emailed statement read for the Statements page
UPLOAD_KIND = "upload"  # its kind in the database's short-lived table (``Store.keep_ephemeral``)


@dataclass(frozen=True)
class Upload:
    name: str
    content: bytes


def keep_upload(name: str, content: bytes) -> str:
    """Keep an uploaded file for the next page, in the dashboard's database (a restart of the server keeps it, D-99);
    returns its token."""
    return current_store().keep_ephemeral(UPLOAD_KIND, name.encode("utf-8") + b"\0" + content, _TTL, keep=_MAX)


def get_upload(token: str | None) -> Upload | None:
    kept = current_store().ephemeral(UPLOAD_KIND, token) if token else None
    if kept is None:
        return None
    name, _, content = kept.partition(b"\0")
    return Upload(name.decode("utf-8", "replace"), content)


def drop_upload(token: str | None) -> None:
    current_store().drop_ephemeral(UPLOAD_KIND, token)


async def read_capped(file: UploadFile) -> bytes | None:
    """An uploaded spreadsheet's bytes, or None when it is larger than ``_MAX_BYTES`` (never more than that read)."""
    content = await file.read(_MAX_BYTES + 1)
    return None if len(content) > _MAX_BYTES else content


async def receive_sheet(file: UploadFile) -> tuple[str, str]:
    """(token, "") for an uploaded spreadsheet that can be read, else ("", the problem in plain words)."""
    name = file.filename or "upload.csv"
    if not name.lower().endswith(SHEET_TYPES):
        return "", NOT_A_SHEET
    content = await read_capped(file)
    if content is None:
        return "", TOO_BIG
    try:
        read_sheet(name, content)
    except SheetError as exc:
        return "", str(exc)
    return keep_upload(name, content), ""


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
        return pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False, sep=sniff_delimiter(text),
                           engine="python")  # fmt: skip
    raise SheetError("Could not read this file. In Excel, use Save As → CSV UTF-8 and upload it again.")


def read_sheet(name: str, content: bytes, sheet: str | None = None) -> tuple[pd.DataFrame, list[str]]:
    """(the sheet as text cells, the workbook's sheet names) of an uploaded CSV or Excel file. ``sheet``: the
    workbook sheet to read (default the first). Semicolon CSVs and Windows-1252 CSVs (Excel's "CSV") are read too."""
    names: list[str] = []
    try:
        if name.lower().endswith((".xlsx", ".xls")):
            sheets = read_excel(content)
            names = [str(n) for n in sheets]
            if not names:
                raise SheetError(CANNOT_READ)
            key = next((k for k in sheets if str(k) == sheet), next(iter(sheets)))
            df = sheets[key]
        else:
            df = read_csv(content)
    except SheetError:
        raise
    except Exception as exc:  # noqa: BLE001 - empty file, not really a spreadsheet, damaged workbook...
        raise SheetError(CANNOT_READ) from exc
    df.columns = [str(c) for c in df.columns]  # a header like 2024 arrives as a number
    return df.fillna(""), names


def read_upload(token: str | None, sheet: str | None = None) -> tuple[Upload, pd.DataFrame, list[str]]:
    """The waiting upload ``token`` as (upload, sheet, sheet names). Raises SheetError when it is gone or unreadable."""
    upload = get_upload(token)
    if upload is None:
        raise SheetError(GONE)
    df, names = read_sheet(upload.name, upload.content, sheet)
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
        "sheet": sheet if sheet in sheets else (sheets[0] if sheets else ""),
        "columns": columns,
        "count": len(df),
        "preview": [[str(v) for v in row] for row in df.head(PREVIEW_ROWS).itertuples(index=False)],
        "fields": [
            {"key": f, "label": label + (" *" if f in required else ""), "value": chosen.get(f, NONE)}
            for f, label in fields.items()
        ],
    }
