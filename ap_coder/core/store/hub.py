"""What the finance hub feed reads (``ap_coder/erp/hub``, D-103), and the record of each import run.

Only reads, apart from ``record_import_run``: the feed never changes an invoice, a lesson or an event.

The feed's own SQLite files, never the database (D-189: their statements here too): what it sent (``HubState``,
``finance_hub/state.db`` in the data folder) and the gateway's read-only copy of every table
(``write_gateway_database``).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ._base import _now

# The audit trail's record of one import of invoices (the dashboard's Import invoices, ``process``, ``watch``): what the
# finance hub's ``workflow_runs`` table lists, with the hub's own runs (``hub_export``) and the ERP export batches.
IMPORT_RUN, HUB_RUN = "import_run", "hub_export"
MAX_RUN_ERRORS = 20  # error lines kept with a run (the first ones; the count says how many there were)


class HubMixin:
    def hub_invoice_rows(self) -> list[dict[str, Any]]:
        """Every invoice with what the feed's invoices and invoice_lines tables need (the JSON columns read)."""
        with self._conn() as conn:
            rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT id, status, file_name, source_path, vendor_name, vendor_key, invoice_number, invoice_date, "
                    "due_date, currency, grand_total, adjusted_confidence, requires_review, ai_output, final_output, "
                    "meta, created_at, reviewed_at, reviewer, second_reviewer, second_reviewed_at, export_batch, "
                    "parked_reason, error FROM invoices ORDER BY id"
                )
            ]
        for r in rows:
            for column in ("ai_output", "final_output", "meta"):
                try:
                    r[column] = json.loads(r[column]) if r[column] else None
                except ValueError:
                    r[column] = None
        return rows

    def hub_events(self, after_id: int = 0, actions: list[str] | None = None) -> list[dict[str, Any]]:
        """Audit events after ``after_id``, oldest first, their detail read (``actions``: only these)."""
        sql, args = "SELECT * FROM events WHERE id > ?", [int(after_id)]
        if actions:
            sql += " AND action IN (SELECT value FROM json_each(?))"
            args.append(json.dumps(list(actions)))
        with self._conn() as conn:
            rows = [dict(r) for r in conn.execute(sql + " ORDER BY id", args)]
        for r in rows:
            try:
                r["detail"] = json.loads(r["detail"]) if r["detail"] else {}
            except ValueError:
                r["detail"] = {}
        return rows

    def hub_last_event(self) -> tuple[int, str]:
        """(the newest event's id, its stamp): a feed that saw a higher id is looking at a database restored from a
        backup since, whose next events reuse ids it already sent."""
        with self._conn() as conn:
            row = conn.execute("SELECT id, created_at, action FROM events ORDER BY id DESC LIMIT 1").fetchone()
        return (int(row["id"]), f"{row['created_at']}|{row['action']}") if row else (0, "")

    def hub_event_stamp(self, event_id: int) -> str:
        with self._conn() as conn:
            row = conn.execute("SELECT created_at, action FROM events WHERE id = ?", (int(event_id),)).fetchone()
        return f"{row['created_at']}|{row['action']}" if row else ""

    def hub_feedback_rows(self) -> list[dict[str, Any]]:
        """The lessons of approved invoices (not imported ERP history), oldest first."""
        with self._conn() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT id, invoice_id, line_number, vendor_key, suggested_gl, final_gl, suggested_cc, final_cc, "
                    "outcome, reviewer, created_at FROM feedback WHERE invoice_id IS NOT NULL ORDER BY id"
                )
            ]

    def hub_outcome_rows(self) -> list[dict[str, Any]]:
        """What AP Coder proposed and AP approved, per header field of each approved invoice (``record_outcomes``)."""
        with self._conn() as conn:
            rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT id, supplier_key, invoice_id, field, ai_value, final_value, correct, source, at "
                    "FROM supplier_outcomes WHERE invoice_id IS NOT NULL ORDER BY id"
                )
            ]
        for r in rows:
            for column in ("ai_value", "final_value"):
                try:
                    r[column] = json.loads(r[column]) if r[column] else None
                except ValueError:
                    pass
        return rows

    def hub_user_roles(self) -> dict[int, dict[str, str]]:
        """{account id: {username, role}} (server mode's accounts; none on a laptop)."""
        with self._conn() as conn:
            return {int(r["id"]): {"username": r["username"], "role": r["role"]}
                    for r in conn.execute("SELECT id, username, role FROM users")}  # fmt: skip

    def record_import_run(
        self, source: str, *, files: int, ok: int, failed: int, skipped: int = 0, started_at: str = "",
        seconds: float = 0.0, errors: list[str] | None = None, actor: str | None = None,
    ) -> None:  # fmt: skip
        """One import of invoices in the audit trail (``import_run``): where from ("dashboard", "process", "watch"),
        how many files were read, coded, failed or skipped as already in AP Coder, when and how long."""
        errors = [str(e) for e in errors or [] if str(e).strip()]
        self.log_event(IMPORT_RUN, actor=actor, detail={
            "source": source, "files": int(files), "ok": int(ok), "failed": int(failed), "skipped": int(skipped),
            "started_at": started_at or _now(), "seconds": round(float(seconds), 1),
            "errors": errors[:MAX_RUN_ERRORS], "error_count": len(errors),
        })  # fmt: skip


# --- What the feed sent (its own file in the data folder, never the database) ----------------------------------------


class HubState:
    """``finance_hub/state.db``: each row's fingerprint as sent, the watermark, the file numbers. What a run changes is
    kept by ``commit`` (closed without it, by a run that failed: nothing)."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, timeout=30)
        self._db.executescript(
            "CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
            "CREATE TABLE IF NOT EXISTS sent (tbl TEXT NOT NULL, key TEXT NOT NULL, hash TEXT NOT NULL, "
            "PRIMARY KEY (tbl, key));"
        )

    def get(self, key: str, default: str = "") -> str:
        row = self._db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def set(self, key: str, value: Any) -> None:
        self._db.execute("INSERT INTO kv VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                         (key, str(value)))  # fmt: skip

    def sent(self, table: str) -> dict[str, str]:
        return dict(self._db.execute("SELECT key, hash FROM sent WHERE tbl = ?", (table,)))

    def forget(self, everything: bool = True) -> None:
        """Forget what was sent (a new destination, format or schema version); the file numbers too for a new
        destination (the same folder keeps counting up)."""
        self._db.execute("DELETE FROM sent")
        self._db.execute("DELETE FROM kv" if everything else "DELETE FROM kv WHERE key NOT LIKE 'seq:%'")

    def record(self, table: str, upserts: dict[str, str], deletes: list[str]) -> None:
        self._db.executemany(
            "INSERT INTO sent VALUES (?, ?, ?) ON CONFLICT(tbl, key) DO UPDATE SET hash = excluded.hash",
            [(table, k, h) for k, h in upserts.items()],
        )
        self._db.executemany("DELETE FROM sent WHERE tbl = ? AND key = ?", [(table, k) for k in deletes])

    def commit(self) -> None:
        self._db.commit()

    def close(self) -> None:
        self._db.close()


# --- The gateway's SQLite copy ---------------------------------------------------------------------------------------

GATEWAY_VIEWS = {
    "v_invoice_lines": "SELECT l.*, i.vendor_name, i.vendor_erp_id, i.invoice_number, i.invoice_date, i.currency, "
    "i.status FROM invoice_lines l JOIN invoices i ON i.invoice_id = l.invoice_id",
    "v_approved_invoices": "SELECT * FROM invoices WHERE status = 'approved'",
    "v_accuracy_by_field": "SELECT field, COUNT(correct) AS checked, SUM(correct) AS right, "
    "ROUND(1.0 * SUM(correct) / NULLIF(COUNT(correct), 0), 4) AS rate FROM accuracy GROUP BY field",
}


def write_gateway_database(path: Path, tables: dict[str, list[dict[str, Any]]], built_at: str) -> None:
    """A new SQLite file at ``path`` holding every table of the feed (``tables``: all their rows), with views: one
    self-contained file (no write-ahead log)."""
    from ...erp.hub.tables import BOOL, FLOAT, HUB_SCHEMA_VERSION, INT, MONEY, TABLES  # (erp/hub imports this part)

    sqlite_types = {INT: "INTEGER", MONEY: "REAL", FLOAT: "REAL", BOOL: "INTEGER"}
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA journal_mode=DELETE")
        for name, table in TABLES.items():
            cols = ", ".join(f'"{c.name}" {sqlite_types.get(c.type, "TEXT")}' for c in table.columns)
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
