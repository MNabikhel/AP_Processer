"""The JD Edwards postings ledger (``jde_postings``, D-104 / D-105): the table, its copy in the data folder, the
installation id of the idempotency keys, and what a restore keeps.

* **The table** is made on first use (``ensure``), so a database stays one an older AP Coder opens as before.
* **The copy outside the database**: every change of a row is appended to ``jde_postings.jsonl`` in the data folder
  (the row as it now is, with the invoice's file fingerprint), and a batch's dropped dry runs as ``deleted`` lines.
  A backup put back does not bring back a database that forgot a voucher JDE holds: ``carry_postings`` merges the
  ledger (and the rows the database had just before the restore) into the restored one, as the accounts are kept
  across a restore on the server (``users.carry_accounts``, D-101).
* **The installation id** (``installation_id``): random, made once per data folder and kept in it, outside the
  database, so a second laptop never makes the keys of this one. A restore starts a new one (``new_installation_id``):
  the invoice and batch numbers a restored database hands out again then make new keys, never the key of a voucher
  JDE already holds for another invoice.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
from pathlib import Path
from typing import Any

POSTED, SENDING, UNKNOWN = "posted", "sending", "unknown"
LOCKED = (POSTED, SENDING, UNKNOWN)  # JDE may hold the voucher
# A row JDE has, or may have: posted, sending, unknown, or one that was sent and not since proved absent from JDE by
# a lookup of its key (``maybe_sent``). Such a row is never sent blind, dropped, or loaded again from the batch files.
LOCKED_SQL = f"(status IN ({', '.join(repr(s) for s in LOCKED)}) OR maybe_sent = 1)"
LEDGER_FILE = "jde_postings.jsonl"
INSTALLATION_FILE = "jde_installation_id"

TABLE = (
    "CREATE TABLE IF NOT EXISTS jde_postings (idem_key TEXT PRIMARY KEY, invoice_id INTEGER NOT NULL, "
    "batch INTEGER NOT NULL, status TEXT NOT NULL, payload_hash TEXT NOT NULL DEFAULT '', checked_at TEXT, "
    "checked_by TEXT, approved_at TEXT, approved_by TEXT, document TEXT NOT NULL DEFAULT '', "
    "doc_type TEXT NOT NULL DEFAULT '', doc_company TEXT NOT NULL DEFAULT '', jde_batch TEXT NOT NULL DEFAULT '', "
    "error TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0, next_try TEXT, updated_at TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS jde_postings_invoice ON jde_postings (invoice_id)",
)
# Added since the table was first made (D-105): what JDE may hold, when the row was claimed for sending, the row's
# version for compare-and-set, a lookup owed after a restore, and who the voucher is for (to check JDE's EXISTS).
COLUMNS = {
    "maybe_sent": "INTEGER NOT NULL DEFAULT 0", "sending_at": "TEXT", "rev": "INTEGER NOT NULL DEFAULT 0",
    "verify": "INTEGER NOT NULL DEFAULT 0", "expect": "TEXT NOT NULL DEFAULT ''",
}  # fmt: skip


def ensure(conn: Any) -> None:
    """The table, with every column (cheap: a database put back from a backup may be without it)."""
    for sql in TABLE:
        conn.execute(sql)
    have = {r[1] for r in conn.execute("PRAGMA table_info(jde_postings)")}
    for name, kind in COLUMNS.items():
        if name not in have:
            conn.execute(f"ALTER TABLE jde_postings ADD COLUMN {name} {kind}")


# --- The installation id ------------------------------------------------------------------------------------------


def _id_path(db_path: str | Path) -> Path:
    return Path(db_path).parent / INSTALLATION_FILE


def installation_id(db_path: str | Path) -> str:
    """This data folder's installation id (10 hex digits), made the first time it is asked for."""
    path = _id_path(db_path)
    with contextlib.suppress(OSError):
        found = path.read_text(encoding="ascii").strip()
        if len(found) == 10 and all(c in "0123456789abcdef" for c in found):
            return found
    return new_installation_id(db_path)


def new_installation_id(db_path: str | Path) -> str:
    """A new installation id (after a restore, or the first one)."""
    path = _id_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    made = secrets.token_hex(5)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(made + "\n", encoding="ascii")
    os.replace(temp, path)
    return made


# --- The ledger ---------------------------------------------------------------------------------------------------


def ledger_path(db_path: str | Path) -> Path:
    return Path(db_path).parent / LEDGER_FILE


def append(db_path: str | Path, entries: list[dict[str, Any]]) -> None:
    """Add these rows (or ``{"idem_key", "deleted": True}``) to the ledger, written through to the disk."""
    if not entries:
        return
    path = ledger_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as out:
        for entry in entries:
            out.write(json.dumps(entry, sort_keys=True, default=str) + "\n")
        out.flush()
        with contextlib.suppress(OSError):
            os.fsync(out.fileno())


def read_ledger(db_path: str | Path) -> dict[str, dict[str, Any]]:
    """{key: its latest line} (a line that can't be read, e.g. cut short by a crash, is skipped)."""
    found: dict[str, dict[str, Any]] = {}
    try:
        lines = ledger_path(db_path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return found
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("idem_key"):
            found[str(entry["idem_key"])] = entry
    return found


def rows_with_ident(conn: Any, where: str = "1 = 1", args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    """Rows of the table, each with its invoice's file fingerprint and when it was added (``invoice_ident``)."""
    out = []
    for r in conn.execute(f"SELECT p.*, i.file_sha256 AS _sha, i.created_at AS _made FROM jde_postings p LEFT JOIN "
                          f"invoices i ON i.id = p.invoice_id WHERE {where}", args):  # fmt: skip
        row = dict(r)
        row["invoice_ident"] = [row.pop("_sha"), row.pop("_made")]
        out.append(row)
    return out


# --- A restore ----------------------------------------------------------------------------------------------------


def saved_postings(conn: Any) -> dict[str, dict[str, Any]]:
    """The database's rows just before a restore (``carry_postings``)."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'jde_postings'").fetchone():
        return {}
    ensure(conn)
    return {r["idem_key"]: r for r in rows_with_ident(conn)}


def carry_postings(conn: Any, db_path: str | Path, saved: dict[str, dict[str, Any]]) -> dict[str, int]:
    """After a backup was put back into ``conn``'s database: every row the ledger (or the database just before) knows
    is merged in, newest first, for the invoices the restored database holds (the same file, added at the same time);
    a row that was being sent is *unknown*; every row is looked up in JDE by its key before anything is sent again
    (``verify``); a new installation id. {"merged", "skipped"}."""
    ensure(conn)
    latest = read_ledger(db_path)
    for key, row in saved.items():
        latest.setdefault(key, row)
    idents = {r[0]: [r[1], r[2]] for r in conn.execute("SELECT id, file_sha256, created_at FROM invoices")}
    have = {r[1] for r in conn.execute("PRAGMA table_info(jde_postings)")}
    merged = skipped = 0
    for key, row in latest.items():
        if row.get("deleted"):
            conn.execute(f"DELETE FROM jde_postings WHERE idem_key = ? AND NOT {LOCKED_SQL}", (key,))
            continue
        if list(row.get("invoice_ident") or []) != idents.get(row.get("invoice_id")):
            skipped += 1  # an invoice this database does not hold (added after the backup): JDE's own edits remain
            continue
        values = {k: v for k, v in row.items() if k in have}
        if values.get("status") == SENDING:
            values["status"] = UNKNOWN
        if values.get("status") in LOCKED:
            values["maybe_sent"] = 1
        names, marks = ", ".join(values), ", ".join(f":{k}" for k in values)  # one per column of the table
        conn.execute(f"INSERT OR REPLACE INTO jde_postings ({names}) VALUES ({marks})", values)
        merged += 1
        if values.get("status") == POSTED and values.get("document"):
            voucher = {"document": values["document"], "doc_type": values.get("doc_type") or "",
                       "company": values.get("doc_company") or "",
                       "posted_at": values.get("updated_at") or ""}  # fmt: skip
            conn.execute("UPDATE invoices SET meta = json_set(CASE WHEN json_valid(meta) THEN meta ELSE '{}' END, "
                         "'$.jde_voucher', json(?)) WHERE id = ? AND (NOT json_valid(meta) OR "
                         "json_extract(meta, '$.jde_voucher') IS NULL)",
                         (json.dumps(voucher), values["invoice_id"]))  # fmt: skip
    conn.execute("UPDATE jde_postings SET verify = 1")
    new_installation_id(db_path)
    return {"merged": merged, "skipped": skipped}
