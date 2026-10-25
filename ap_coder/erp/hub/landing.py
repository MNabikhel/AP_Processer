"""Where the finance hub's files go, and how they are written (D-103).

The layout follows Fabric Open Mirroring's landing zone, so a mirrored database can pick the files up as they are:

    <target>/
      _hub.json                      what this is: the schema version, the format, the last run, every table
      invoices/
        _metadata.json               {"keyColumns": ["invoice_id"]} (Open Mirroring reads it)
        _schema.json                 the columns, their types and meaning (for CSV, which carries no types)
        00000000000000000001.parquet the rows changed by each run, in sequence (or .csv)
      invoice_lines/ approvals/ activity/ accuracy/ workflow_runs/
      _gateway/finance_hub.db        a read-only SQLite copy of every table, with views (for the on-premises gateway)

Each data file carries the column ``__rowMarker__`` last: 4 (upsert: insert, or replace the row with that key) or 2
(delete the row with that key). Every file is written under ``_staging/`` first and then renamed into place, so a
reader never sees half a file.

``Destination`` is the one interface both deliveries write through: ``FolderDestination`` (Option A, a folder an
Admin sets: a network share), ``OneLakeDestination`` (Option B, ``onelake.py``) and ``MemoryDestination`` (tests).
"""

from __future__ import annotations

import contextlib
import csv
import datetime as dt
import importlib.util
import io
import json
import os
import re
import sqlite3
import stat
import tempfile
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from .tables import BOOL, DATE, FLOAT, HUB_SCHEMA_VERSION, INT, MONEY, TABLES, TIMESTAMP, Table

PARQUET, CSV = "parquet", "csv"
ROW_MARKER, UPSERT, DELETE = "__rowMarker__", 4, 2
SEQUENCE_DIGITS = 20  # 00000000000000000001.parquet, as Open Mirroring names its files
ROWS_PER_FILE = 100_000
STAGING, GATEWAY_DIR, GATEWAY_FILE, HUB_FILE = "_staging", "_gateway", "finance_hub.db", "_hub.json"
METADATA_FILE, SCHEMA_FILE = "_metadata.json", "_schema.json"
_SEQUENCED = re.compile(rf"^(\d{{{SEQUENCE_DIGITS}}})\.(parquet|csv)$")


class HubError(RuntimeError):
    """A run that could not be done, said in plain words for the Settings page and the command line."""


def parquet_available() -> bool:
    """Whether pyarrow is installed: Parquet then, else CSV with ``_schema.json`` (D-103)."""
    return importlib.util.find_spec("pyarrow") is not None


def choose_format(wanted: str = "auto") -> str:
    if wanted == PARQUET:
        if not parquet_available():
            raise HubError("Parquet needs the pyarrow package, which is not installed: choose CSV, or Automatic.")
        return PARQUET
    if wanted == CSV:
        return CSV
    return PARQUET if parquet_available() else CSV


def sequenced_name(number: int, fmt: str) -> str:
    return f"{number:0{SEQUENCE_DIGITS}d}.{fmt}"


def last_sequence(names: list[str]) -> int:
    """The highest file number among ``names`` (0 when none)."""
    found = [int(m.group(1)) for n in names if (m := _SEQUENCED.match(n))]
    return max(found, default=0)


# --- Destinations -------------------------------------------------------------------------------------------------


class Destination(Protocol):
    kind: str  # "folder" or "onelake": what the run records

    def ident(self) -> str:
        """What tells one destination from another (a new one gets every row again)."""
        ...

    def describe(self) -> str: ...

    def list(self, folder: str) -> list[str]:
        """The names of the files in ``folder`` (a table's), [] when it does not exist yet."""
        ...

    def write(self, path: str, data: bytes) -> None:
        """Write ``data`` at ``path`` (relative, with /), whole or not at all: staged, then renamed."""
        ...

    def remove(self, path: str) -> None:
        """Remove the file at ``path`` (relative, with /); nothing when it isn't there."""
        ...


class FolderDestination:
    kind = "folder"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def ident(self) -> str:
        return f"folder:{self.root.resolve()}"

    def describe(self) -> str:
        return str(self.root)

    def check(self) -> None:
        if not str(self.root).strip() or str(self.root) == ".":
            raise HubError("Choose the folder the finance hub reads from (Settings → Finance hub).")
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HubError(f"The folder {self.root} can't be reached or made ({exc.strerror or exc}): check that the "
                           "share is connected and that this computer may write to it.") from exc  # fmt: skip

    def list(self, folder: str) -> list[str]:
        where = self.root / folder
        try:
            return sorted(p.name for p in where.iterdir() if p.is_file())
        except OSError:
            return []

    def write(self, path: str, data: bytes) -> None:
        target = self.root / path
        staging = self.root / STAGING
        staging.mkdir(parents=True, exist_ok=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        part = staging / f"{uuid.uuid4().hex}-{target.name}"
        try:
            with open(part, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            _make_writable(target)
            os.replace(part, target)
        except OSError:
            with contextlib.suppress(OSError):
                part.unlink()
            raise

    def remove(self, path: str) -> None:
        target = self.root / path
        _make_writable(target)
        with contextlib.suppress(FileNotFoundError):
            target.unlink()

    def put_file(self, path: str, source: Path) -> None:
        """Move a finished file (the gateway database) into place, as ``write`` does."""
        target = self.root / path
        staging = self.root / STAGING
        staging.mkdir(parents=True, exist_ok=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        part = staging / f"{uuid.uuid4().hex}-{target.name}"
        try:
            with open(source, "rb") as src, open(part, "wb") as dst:
                while chunk := src.read(1 << 20):
                    dst.write(chunk)
                dst.flush()
                os.fsync(dst.fileno())
            _read_only(part)
            _make_writable(target)
            os.replace(part, target)
        except OSError:
            with contextlib.suppress(OSError):
                _make_writable(part)
                part.unlink()
            raise


class MemoryDestination:
    """A destination in memory, for tests: {path: bytes}."""

    kind = "memory"

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def ident(self) -> str:
        return f"memory:{id(self)}"

    def describe(self) -> str:
        return "memory"

    def list(self, folder: str) -> list[str]:
        prefix = folder.rstrip("/") + "/"
        return sorted(p[len(prefix) :] for p in self.files if p.startswith(prefix) and "/" not in p[len(prefix) :])

    def write(self, path: str, data: bytes) -> None:
        self.files[path] = bytes(data)

    def remove(self, path: str) -> None:
        self.files.pop(path, None)


def _make_writable(path: Path) -> None:
    """A read-only file (the gateway database) made writable, so it can be replaced (Windows refuses otherwise)."""
    with contextlib.suppress(OSError):
        if path.exists():
            os.chmod(path, stat.S_IREAD | stat.S_IWRITE)


def _read_only(path: Path) -> None:
    with contextlib.suppress(OSError):
        os.chmod(path, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)


# --- The files ----------------------------------------------------------------------------------------------------


def metadata(table: Table, fmt: str) -> bytes:
    """``_metadata.json``: the key columns, as Open Mirroring wants them; for CSV, how the files are written."""
    found: dict[str, Any] = {"keyColumns": list(table.key)}
    if fmt == CSV:
        found.update({"fileFormat": "DelimitedText", "fileExtension": "csv", "firstRowAsHeader": True,
                      "columnDelimiter": ",", "quoteCharacter": '"', "escapeCharacter": '"',
                      "encoding": "UTF-8"})  # fmt: skip
    return json.dumps(found, indent=2).encode("utf-8")


def schema_json(table: Table, fmt: str) -> bytes:
    return json.dumps({
        "table": table.name, "schema_version": HUB_SCHEMA_VERSION, "format": fmt, "meaning": table.meaning,
        "key_columns": list(table.key), "row_marker_column": ROW_MARKER,
        "row_markers": {str(UPSERT): "upsert", str(DELETE): "delete"},
        "columns": [{"name": c.name, "type": c.type, "meaning": c.meaning} for c in table.columns]
        + [{"name": ROW_MARKER, "type": "int32", "meaning": "4: upsert this row; 2: delete the row with this key."}],
    }, indent=2, ensure_ascii=False).encode("utf-8")  # fmt: skip


def hub_json(fmt: str, last_run: dict[str, Any]) -> bytes:
    return json.dumps({
        "app": "AP Coder", "schema_version": HUB_SCHEMA_VERSION, "format": fmt, "last_run": last_run,
        "tables": {t.name: {"key_columns": list(t.key), "columns": t.names} for t in TABLES.values()},
        "data_dictionary": "docs/FINANCE_HUB.md in AP Coder's folder",
    }, indent=2, ensure_ascii=False).encode("utf-8")  # fmt: skip


def encode(table: Table, rows: list[dict[str, Any]], markers: list[int], fmt: str) -> bytes:
    """One data file: ``rows`` (each with the table's columns; a delete needs only the key) and their row markers."""
    return _parquet(table, rows, markers) if fmt == PARQUET else _csv(table, rows, markers)


def _csv_value(value: Any, type_: str) -> str:
    if value is None:
        return ""
    if type_ == BOOL:
        return "true" if value else "false"
    if type_ == MONEY:
        return f"{float(value):.2f}"
    return str(value)


def _csv(table: Table, rows: list[dict[str, Any]], markers: list[int]) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow([*table.names, ROW_MARKER])
    for row, marker in zip(rows, markers, strict=True):
        writer.writerow([*(_csv_value(row.get(c.name), c.type) for c in table.columns), marker])
    return out.getvalue().encode("utf-8")


def _parquet(table: Table, rows: list[dict[str, Any]], markers: list[int]) -> bytes:
    import pyarrow as pa
    import pyarrow.parquet as pq

    def arrow_type(type_: str) -> Any:
        return {INT: pa.int64(), MONEY: pa.decimal128(18, 2), FLOAT: pa.float64(), DATE: pa.date32(),
                TIMESTAMP: pa.timestamp("s"), BOOL: pa.bool_()}.get(type_, pa.string())  # fmt: skip

    def value(v: Any, type_: str) -> Any:
        if v is None:
            return None
        if type_ == MONEY:
            return Decimal(f"{float(v):.2f}")
        if type_ == DATE:
            return dt.date.fromisoformat(v)
        if type_ == TIMESTAMP:
            return dt.datetime.fromisoformat(v)
        return v

    columns = {c.name: pa.array([value(r.get(c.name), c.type) for r in rows], type=arrow_type(c.type))
               for c in table.columns}  # fmt: skip
    columns[ROW_MARKER] = pa.array(markers, type=pa.int32())
    schema = pa.schema([pa.field(c.name, arrow_type(c.type)) for c in table.columns]
                       + [pa.field(ROW_MARKER, pa.int32(), nullable=False)])  # fmt: skip
    buffer = io.BytesIO()
    pq.write_table(pa.table(columns, schema=schema), buffer, compression="snappy")
    return buffer.getvalue()


# --- The gateway's SQLite file ---------------------------------------------------------------------------------------

_SQLITE_TYPES = {INT: "INTEGER", MONEY: "REAL", FLOAT: "REAL", BOOL: "INTEGER"}
GATEWAY_VIEWS = {
    "v_invoice_lines": "SELECT l.*, i.vendor_name, i.vendor_erp_id, i.invoice_number, i.invoice_date, i.currency, "
    "i.status FROM invoice_lines l JOIN invoices i ON i.invoice_id = l.invoice_id",
    "v_approved_invoices": "SELECT * FROM invoices WHERE status = 'approved'",
    "v_accuracy_by_field": "SELECT field, COUNT(correct) AS checked, SUM(correct) AS right, "
    "ROUND(1.0 * SUM(correct) / NULLIF(COUNT(correct), 0), 4) AS rate FROM accuracy GROUP BY field",
}


def gateway_database(tables: dict[str, list[dict[str, Any]]], built_at: str) -> Path:
    """A SQLite file holding every table (``tables``: all their rows), with views, in a temporary folder: the caller
    moves it into place and removes the folder. One self-contained file (no write-ahead log), read-only."""
    folder = Path(tempfile.mkdtemp(prefix="ap_coder-hub-"))
    path = folder / GATEWAY_FILE
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA journal_mode=DELETE")
        for name, table in TABLES.items():
            cols = ", ".join(f'"{c.name}" {_SQLITE_TYPES.get(c.type, "TEXT")}' for c in table.columns)
            conn.execute(f'CREATE TABLE "{name}" ({cols}, PRIMARY KEY ({", ".join(table.key)}))')
            marks = ", ".join("?" for _ in table.columns)  # one per column of a fixed table, at most 45
            conn.executemany(
                f'INSERT INTO "{name}" VALUES ({marks})',
                [[(int(v) if isinstance(v, bool) else v) for v in (r.get(c) for c in table.names)]
                 for r in tables.get(name, [])],
            )  # fmt: skip
        for view, sql in GATEWAY_VIEWS.items():
            conn.execute(f'CREATE VIEW "{view}" AS {sql}')
        conn.execute("CREATE TABLE _hub_info (key TEXT PRIMARY KEY, value TEXT)")
        conn.executemany("INSERT INTO _hub_info VALUES (?, ?)",
                         [("schema_version", str(HUB_SCHEMA_VERSION)), ("built_at", built_at)])  # fmt: skip
        conn.commit()
    finally:
        conn.close()
    return path
