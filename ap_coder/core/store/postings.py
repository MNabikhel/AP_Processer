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
* **Its reads and writes** (``PostingsMixin``, D-189): every statement on the table, each in a transaction of its own
  as ``erp/jde_post.py`` (the state machine over the rows) had them; a row changed is copied to the ledger after.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

# A row's status (``erp/jde_post.py``: what each means, and how a row goes from one to another).
CHECKED, REFUSED, QUEUED, SENDING, POSTED, FAILED, UNKNOWN = (
    "checked", "refused", "queued", "sending", "posted", "failed", "unknown",
)  # fmt: skip
LOCKED = (POSTED, SENDING, UNKNOWN)  # JDE may hold the voucher
IN_FLIGHT = (QUEUED, SENDING, UNKNOWN)  # approved and not finished: the queue
SENDABLE = (CHECKED, QUEUED, UNKNOWN, SENDING)  # claimed only with maybe_sent = 0: never sent while JDE may have it
FILE_DOWNLOADED = "jde_file_downloaded"  # the event of a download of a batch's JD Edwards ZIP (D-134)
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
# version for compare-and-set, a lookup owed after a restore, who the voucher is for (to check JDE's EXISTS), and when
# its send's answer was lost (D-182: JDE's "not there" is believed only a while after that).
COLUMNS = {
    "maybe_sent": "INTEGER NOT NULL DEFAULT 0", "sending_at": "TEXT", "rev": "INTEGER NOT NULL DEFAULT 0",
    "verify": "INTEGER NOT NULL DEFAULT 0", "expect": "TEXT NOT NULL DEFAULT ''", "lost_at": "TEXT",
}  # fmt: skip


def ensure(conn: Any) -> None:
    """The table, with every column (cheap, about 45 µs: a database put back from a backup may be without it). Web
    workers may add a column at once: one another connection added since this one looked is there (B-1725)."""
    for sql in TABLE:
        conn.execute(sql)
    have = {r[1] for r in conn.execute("PRAGMA table_info(jde_postings)")}
    for name, kind in COLUMNS.items():
        if name not in have:
            try:
                conn.execute(f"ALTER TABLE jde_postings ADD COLUMN {name} {kind}")
            except sqlite3.OperationalError:
                if name not in {r[1] for r in conn.execute("PRAGMA table_info(jde_postings)")}:
                    raise


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


# --- The table's reads and writes ---------------------------------------------------------------------------------


class PostingsMixin:
    """Every statement on ``jde_postings`` (D-189): ``erp/jde_post.py`` decides, these read and write, each in a
    transaction of its own (the table made first)."""

    @contextlib.contextmanager
    def _postings(self) -> Iterator[sqlite3.Connection]:
        with self._conn() as conn:
            ensure(conn)
            yield conn

    def jde_postings(self, batch: int | None = None, statuses: tuple[str, ...] = ()) -> list[dict[str, Any]]:
        """The rows (of ``batch``, in one of ``statuses``, when given), by batch and invoice."""
        sql, args = "SELECT * FROM jde_postings WHERE 1 = 1", []
        if batch is not None:
            sql += " AND batch = ?"
            args.append(batch)
        if statuses:
            sql += " AND status IN (SELECT value FROM json_each(?))"
            args.append(json.dumps(list(statuses)))
        with self._postings() as conn:
            return [dict(r) for r in conn.execute(sql + " ORDER BY batch, invoice_id", args)]

    def jde_posting(self, key: str) -> dict[str, Any] | None:
        with self._postings() as conn:
            found = conn.execute("SELECT * FROM jde_postings WHERE idem_key = ?", (key,)).fetchone()
        return dict(found) if found else None

    def keep_jde_postings(self, keys: list[str]) -> None:
        """The rows as they now are, in the ledger outside the database."""
        if not keys:
            return
        with self._postings() as conn:
            rows = rows_with_ident(conn, "p.idem_key IN (SELECT value FROM json_each(?))", (json.dumps(keys),))
        append(self.path, rows)

    def put_jde_posting(self, key: str, invoice_id: int, batch: int, values: dict[str, Any], *, status: str,
                        only_from: tuple[str, ...] | None = None, rev: int | None = None) -> bool:  # fmt: skip
        """Write the row's ``values`` (its ``updated_at`` with them). A new row, in ``status``, only for a batch not
        undone. ``only_from``: only over a row in one of these statuses that JDE can't hold (maybe_sent 0); ``rev``:
        only if the row is still that version. Whether it changed (then kept in the ledger)."""
        with self._postings() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO jde_postings (idem_key, invoice_id, batch, status, updated_at) SELECT ?, ?, ?, "
                "?, ? WHERE NOT EXISTS (SELECT 1 FROM export_batches WHERE id = ? AND undone_at IS NOT NULL)",
                (key, invoice_id, batch, status, values["updated_at"], batch),
            )
            sets = ", ".join(f"{k} = ?" for k in values)
            where, args = "idem_key = ?", [key]
            if only_from is not None:
                where += " AND status IN (SELECT value FROM json_each(?)) AND maybe_sent = 0"
                args.append(json.dumps(list(only_from)))
            if rev is not None:
                where += " AND rev = ?"
                args.append(rev)
            changed = conn.execute(f"UPDATE jde_postings SET {sets}, rev = rev + 1 WHERE {where}",
                                   (*values.values(), *args)).rowcount  # fmt: skip
        if changed:
            self.keep_jde_postings([key])
        return bool(changed)

    def claim_jde_posting(self, row: dict[str, Any], attempts: int, *, sending_at: str, now: str
                          ) -> dict[str, Any] | None:  # fmt: skip
        """Compare-and-set before a send: *sending* only if the row is the version read, sendable, JDE can't hold
        it, its batch is not undone and its ZIP was not downloaded since the go-ahead; the row as claimed (kept in
        the ledger), or None."""
        with self._postings() as conn:
            changed = conn.execute(
                "UPDATE jde_postings SET status = ?, sending_at = ?, lost_at = NULL, maybe_sent = 1, attempts = ?, "
                "next_try = NULL, rev = rev + 1, updated_at = ? WHERE idem_key = ? AND rev = ? AND maybe_sent = 0 AND "
                "verify = 0 AND status IN (SELECT value FROM json_each(?)) AND batch NOT IN "
                "(SELECT id FROM export_batches WHERE undone_at IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM events e "
                "WHERE e.action = ? AND json_extract(e.detail, '$.batch') = jde_postings.batch AND "
                "e.created_at > COALESCE(jde_postings.approved_at, ''))",
                (SENDING, sending_at, attempts, now, row["idem_key"], row["rev"], json.dumps(SENDABLE),
                 FILE_DOWNLOADED),
            ).rowcount  # fmt: skip
        if changed != 1:
            return None
        self.keep_jde_postings([row["idem_key"]])
        return self.jde_posting(row["idem_key"])

    def jde_documents(self, invoice_id: int | None) -> list[str]:
        """JDE's voucher numbers posted for this invoice."""
        with self._postings() as conn:
            return [r[0] for r in conn.execute("SELECT document FROM jde_postings WHERE invoice_id = ? AND status = ? "
                                               "AND document != ''", (invoice_id, POSTED))]  # fmt: skip

    def jde_not_posted(self) -> dict[int, str]:
        """{invoice: its row's status} of the exported invoices whose voucher went to JD Edwards through the API and
        is not posted there yet (``jde_post.not_posted`` says which)."""
        with self._postings() as conn:
            found = conn.execute(
                "SELECT p.invoice_id, p.status FROM jde_postings p JOIN invoices i ON i.id = p.invoice_id AND "
                "i.export_batch = p.batch WHERE p.status IN (?, ?, ?) OR (p.status IN (?, ?) AND p.approved_at IS NOT "
                "NULL) OR (p.status = ? AND NOT EXISTS (SELECT 1 FROM events e WHERE e.action = ? AND "
                "json_extract(e.detail, '$.batch') = p.batch AND e.created_at >= p.updated_at)) ORDER BY p.invoice_id",
                (QUEUED, SENDING, UNKNOWN, CHECKED, REFUSED, FAILED, FILE_DOWNLOADED),
            ).fetchall()
        return {int(r[0]): r[1] for r in found}

    def jde_locked(self, batch: int) -> list[dict[str, Any]]:
        """The batch's rows JDE has, or may have (``LOCKED_SQL``)."""
        with self._postings() as conn:
            return [dict(r) for r in conn.execute(f"SELECT * FROM jde_postings WHERE batch = ? AND {LOCKED_SQL} "
                                                  "ORDER BY invoice_id", (batch,))]  # fmt: skip

    def undo_jde_batch(self, batch: int, actor: str | None, refuse: Callable[[list[str]], Exception]) -> int:
        """``undo_export_batch`` in one write transaction with the check that none of the batch's vouchers is, or may
        be, in JDE: ``refuse(their statuses)`` raised when one is."""

        def guard(conn: sqlite3.Connection) -> None:
            ensure(conn)
            found = [r[0] for r in conn.execute(f"SELECT status FROM jde_postings WHERE batch = ? AND {LOCKED_SQL}",
                                                (batch,))]  # fmt: skip
            if found:
                raise refuse(found)

        return self.undo_export_batch(batch, actor=actor, guard=guard)

    def forget_jde_batch(self, batch: int, now: str) -> None:
        """The batch was undone: its rows JDE can't hold are dropped (``deleted`` lines in the ledger)."""
        with self._postings() as conn:
            keys = [r[0] for r in conn.execute(f"SELECT idem_key FROM jde_postings WHERE batch = ? AND NOT "
                                               f"{LOCKED_SQL}", (batch,))]  # fmt: skip
            conn.execute(f"DELETE FROM jde_postings WHERE batch = ? AND NOT {LOCKED_SQL}", (batch,))
        append(self.path, [{"idem_key": k, "deleted": True, "updated_at": now} for k in keys])

    def jde_downloads(self, batch: int) -> list[dict[str, str]]:
        """Each download of the batch's JD Edwards ZIP, newest first: {"actor", "at"}."""
        with self._postings() as conn:
            found = conn.execute("SELECT actor, created_at FROM events WHERE action = ? AND "
                                 "json_extract(detail, '$.batch') = ? ORDER BY id DESC",
                                 (FILE_DOWNLOADED, int(batch))).fetchall()  # fmt: skip
        return [{"actor": r[0] or "", "at": r[1] or ""} for r in found]

    def jde_waiting(self, now: str) -> list[dict[str, Any]]:
        """The rows approved and not finished, with the go-aheads' *checked* ones whose next try is due by ``now``."""
        with self._postings() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM jde_postings WHERE status IN (SELECT value FROM json_each(?)) OR (status = ? AND "
                "approved_at IS NOT NULL AND maybe_sent = 0 AND next_try <= ?) ORDER BY batch, invoice_id",
                (json.dumps(IN_FLIGHT), CHECKED, now))]  # fmt: skip

    def set_jde_voucher(self, invoice_id: int, voucher: dict[str, str]) -> None:
        """The voucher JDE posted, on the invoice too (``meta["jde_voucher"]``, as the finance hub feed reads it)."""
        with self._postings() as conn:
            conn.execute("UPDATE invoices SET meta = json_set(CASE WHEN json_valid(meta) THEN meta ELSE '{}' END, "
                         "'$.jde_voucher', json(?)) WHERE id = ?", (json.dumps(voucher), invoice_id))  # fmt: skip
