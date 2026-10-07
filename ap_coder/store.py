"""Local SQLite store behind the dashboard (``private/ap_coder.db``).

Holds everything the prototype needs to run without any enterprise data
leaving the machine:

* GL accounts ("cost codes") and optional cost centers, imported by the user
  with their own choice of code / description / category columns.
* Tax setup: how each tax type (GST, HST, PST, QST) is posted and to which GL.
* Coding policy notes.
* Processed invoices with the AI output, the reviewer's final version and the
  validation report.
* Feedback: one row per approved invoice line, recording whether the reviewer
  accepted or corrected the AI's coding. This is the learning memory.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .audit import diff_coding
from .memory import ACCEPTED, CORRECTED, pair_lines, vendor_key
from .paths import default_db_path, private_dir  # noqa: F401  (re-exported)
from .po import OPEN, po_key
from .reference_data import UNASSIGNED, ReferenceData, ReferenceTable, parse_policy_notes
from .tax import DEFAULT_TREATMENTS, TAX_TYPES, TREATMENTS, TaxRateTable, TaxSetup, TaxTreatment
from .terms import DEFAULT_TERMS_DAYS, payment
from .vendors import norm_invoice_number

SCHEMA_VERSION = 6
ACCOUNT_TABLES = {"gl_accounts": "gl_code", "cost_centers": "cost_center"}

REVIEW, APPROVED, REJECTED, FAILED = "review", "approved", "rejected", "failed"
PENDING = "pending_approval"  # approved once, over the approval limit: waiting for a second approver
ACTIVE_STATUSES = (REVIEW, PENDING, APPROVED)  # invoices that count (for POs, recurring vendors...)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS gl_accounts (
    code TEXT PRIMARY KEY, description TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cost_centers (
    code TEXT PRIMARY KEY, description TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tax_treatments (
    tax_type TEXT PRIMARY KEY, treatment TEXT NOT NULL, gl_code TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_path TEXT NOT NULL, file_name TEXT NOT NULL, file_sha256 TEXT,
    status TEXT NOT NULL,
    vendor_name TEXT, vendor_key TEXT, invoice_number TEXT, invoice_date TEXT, currency TEXT, grand_total REAL,
    model_confidence REAL, adjusted_confidence REAL, requires_review INTEGER,
    ai_output TEXT, final_output TEXT, validation TEXT, extraction_md TEXT, meta TEXT, edits TEXT, error TEXT,
    created_at TEXT NOT NULL, reviewed_at TEXT, reviewer TEXT, export_batch INTEGER);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER, line_number INTEGER,
    vendor_key TEXT NOT NULL, vendor_name TEXT NOT NULL, description TEXT NOT NULL, amount REAL,
    suggested_gl TEXT, final_gl TEXT NOT NULL, suggested_cc TEXT, final_cc TEXT,
    outcome TEXT NOT NULL, reviewer TEXT, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS feedback_vendor ON feedback (vendor_key);
CREATE INDEX IF NOT EXISTS invoices_status ON invoices (status);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER, action TEXT NOT NULL, actor TEXT, detail TEXT, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS events_invoice ON events (invoice_id);
CREATE TABLE IF NOT EXISTS vendors (
    vendor_key TEXT PRIMARY KEY, display_name TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'active',
    expected_gst TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS invoices_vendor ON invoices (vendor_key);
CREATE TABLE IF NOT EXISTS export_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, actor TEXT, format TEXT NOT NULL,
    invoices INTEGER NOT NULL, total REAL, undone_at TEXT, undone_by TEXT);
CREATE TABLE IF NOT EXISTS purchase_orders (
    po_key TEXT PRIMARY KEY, po_number TEXT NOT NULL, vendor_name TEXT NOT NULL DEFAULT '',
    vendor_key TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'open', updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS po_lines (
    po_key TEXT NOT NULL, line_number INTEGER NOT NULL, description TEXT NOT NULL, quantity REAL NOT NULL,
    unit_price REAL NOT NULL, amount REAL NOT NULL, received_quantity REAL, gl_code TEXT NOT NULL DEFAULT '',
    cost_center TEXT NOT NULL DEFAULT '', PRIMARY KEY (po_key, line_number));
CREATE INDEX IF NOT EXISTS purchase_orders_vendor ON purchase_orders (vendor_key);
"""

# Fields compared when deciding whether a reviewer edited an invoice header.
_HEADER_FIELDS = (
    "vendor_name", "invoice_number", "invoice_date", "po_number", "payment_terms", "due_date", "currency",
    "supplier_province",
    "ship_to_province", "gst_hst_registration_number", "qst_registration_number", "subtotal", "tax_total",
    "grand_total",
)  # fmt: skip


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _clean_code(value: Any) -> str:
    """Codes come from spreadsheets: 6000.0 -> '6000', strip whitespace."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    if text.lower() in {"nan", "none"}:
        return ""
    return text[:-2] if re.fullmatch(r"\d+\.0", text) else text


def _due(coding: dict[str, Any], default_days: int = DEFAULT_TERMS_DAYS) -> str | None:
    due = payment(coding, default_days).due
    return due.isoformat() if due else None


def _norm_number(value: str) -> str:
    return norm_invoice_number(value)


class Store:
    def __init__(self, path: str | Path | None = None) -> None:
        path = default_db_path() if path is None else path
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(_SCHEMA)
            self._migrate(conn)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Bring a database made by an older version up to date (recorded as settings.schema_version)."""
        row = conn.execute("SELECT value FROM settings WHERE key = 'schema_version'").fetchone()
        version = int(row["value"]) if row else 1
        if version < 2:  # vendor matching now ignores accents, "&"/"and" and more legal forms
            for table in ("invoices", "feedback"):
                rows = conn.execute(f"SELECT id, vendor_name FROM {table}").fetchall()
                conn.executemany(
                    f"UPDATE {table} SET vendor_key = ? WHERE id = ?",
                    [(vendor_key(r["vendor_name"] or ""), r["id"]) for r in rows],
                )
        if version < 3:  # invoices remember the ERP export batch they went out in
            columns = {r["name"] for r in conn.execute("PRAGMA table_info(invoices)")}
            if "export_batch" not in columns:
                conn.execute("ALTER TABLE invoices ADD COLUMN export_batch INTEGER")
        if version < 4:  # invoices remember the purchase order they quote
            columns = {r["name"] for r in conn.execute("PRAGMA table_info(invoices)")}
            if "po_key" not in columns:
                conn.execute("ALTER TABLE invoices ADD COLUMN po_key TEXT NOT NULL DEFAULT ''")
            rows = conn.execute("SELECT id, ai_output, final_output FROM invoices").fetchall()
            for r in rows:
                coding = json.loads(r["final_output"] or r["ai_output"] or "{}")
                if coding.get("po_number"):
                    conn.execute("UPDATE invoices SET po_key = ? WHERE id = ?", (po_key(coding["po_number"]), r["id"]))
        conn.execute("CREATE INDEX IF NOT EXISTS invoices_po ON invoices (po_key)")
        if version < 5:  # invoices remember when they are due (printed, from the terms, or the default)
            columns = {r["name"] for r in conn.execute("PRAGMA table_info(invoices)")}
            if "due_date" not in columns:
                conn.execute("ALTER TABLE invoices ADD COLUMN due_date TEXT")
            for r in conn.execute("SELECT id, ai_output, final_output FROM invoices").fetchall():
                coding = json.loads(r["final_output"] or r["ai_output"] or "{}")
                if coding:
                    conn.execute("UPDATE invoices SET due_date = ? WHERE id = ?", (_due(coding), r["id"]))
        if version < 6:  # second approval above the approval limit
            columns = {r["name"] for r in conn.execute("PRAGMA table_info(invoices)")}
            for column in ("second_reviewer", "second_reviewed_at"):
                if column not in columns:
                    conn.execute(f"ALTER TABLE invoices ADD COLUMN {column} TEXT")
        if version < SCHEMA_VERSION:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(SCHEMA_VERSION),),
            )

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        fresh = not self.path.exists()  # e.g. the file was deleted while the dashboard was running
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        if fresh:
            conn.executescript(_SCHEMA)
            self._migrate(conn)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # --- GL accounts / cost centers ----------------------------------------------------------

    # --- Audit trail ---------------------------------------------------------------------------------

    @staticmethod
    def _log(
        conn: sqlite3.Connection, action: str, invoice_id: int | None = None, actor: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:  # fmt: skip
        conn.execute(
            "INSERT INTO events (invoice_id, action, actor, detail, created_at) VALUES (?, ?, ?, ?, ?)",
            (invoice_id, action, actor, json.dumps(detail or {}, default=str), _now()),
        )

    def log_event(
        self, action: str, invoice_id: int | None = None, actor: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:  # fmt: skip
        with self._conn() as conn:
            self._log(conn, action, invoice_id, actor, detail)

    def events(
        self, invoice_id: int | None = None, actions: list[str] | None = None, limit: int = 1000
    ) -> list[dict[str, Any]]:
        """Audit events, newest first (optionally for one invoice or some actions)."""
        sql, args = "SELECT * FROM events WHERE 1 = 1", []
        if invoice_id is not None:
            sql += " AND invoice_id = ?"
            args.append(invoice_id)
        if actions:
            sql += f" AND action IN ({', '.join('?' for _ in actions)})"
            args += actions
        with self._conn() as conn:
            rows = [dict(r) for r in conn.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit))]
        for r in rows:
            r["detail"] = json.loads(r["detail"]) if r["detail"] else {}
        return rows

    def import_accounts(
        self,
        table: str,
        rows: list[dict[str, Any]],
        code_column: str,
        description_column: str | None,
        category_column: str | None = None,
        replace_all: bool = False,
        actor: str | None = None,
        log: bool = True,
    ) -> dict[str, int]:
        """Import rows using the columns the user picked.

        Existing codes are updated, but only in the columns picked: importing codes without a
        category column keeps the categories already typed in.
        """
        if table not in ACCOUNT_TABLES:
            raise ValueError(f"unknown table {table!r}")
        added = updated = skipped = 0
        with self._conn() as conn:
            if replace_all:
                conn.execute(f"DELETE FROM {table}")
            existing = {r["code"] for r in conn.execute(f"SELECT code FROM {table}")}
            seen: set[str] = set()
            for row in rows:
                code = _clean_code(row.get(code_column))
                if not code or code in seen:
                    skipped += 1
                    continue
                seen.add(code)
                description = _clean_code(row.get(description_column)) if description_column else ""
                category = _clean_code(row.get(category_column)) if category_column else ""
                if code in existing:
                    changes = {"description": description} if description_column else {}
                    if category_column:
                        changes["category"] = category
                    if changes:
                        sets = ", ".join(f"{column} = ?" for column in changes)
                        conn.execute(f"UPDATE {table} SET {sets} WHERE code = ?", (*changes.values(), code))
                    updated += 1
                else:
                    conn.execute(
                        f"INSERT INTO {table} (code, description, category, created_at) VALUES (?, ?, ?, ?)",
                        (code, description, category, _now()),
                    )
                    added += 1
            if log:
                self._log(conn, "accounts_imported", actor=actor, detail={
                    "table": table, "added": added, "updated": updated, "skipped": skipped,
                    "replace_all": replace_all,
                })  # fmt: skip
        return {"added": added, "updated": updated, "skipped": skipped}

    def list_accounts(self, table: str) -> list[dict[str, str]]:
        if table not in ACCOUNT_TABLES:
            raise ValueError(f"unknown table {table!r}")
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(f"SELECT code, description, category FROM {table} ORDER BY code")]

    def save_accounts(self, table: str, rows: list[dict[str, Any]], actor: str | None = None) -> None:
        """Replace the table with ``rows`` (used by the dashboard's editable grid)."""
        before = {r["code"]: r for r in self.list_accounts(table)}
        self.import_accounts(table, rows, "code", "description", "category", replace_all=True, log=False)
        after = {r["code"]: r for r in self.list_accounts(table)}
        changed = sorted(c for c in before.keys() | after.keys() if before.get(c) != after.get(c))
        if changed:
            self.log_event("accounts_edited", actor=actor, detail={"table": table, "codes": changed})

    def delete_accounts(self, table: str, codes: list[str], actor: str | None = None) -> int:
        if table not in ACCOUNT_TABLES:
            raise ValueError(f"unknown table {table!r}")
        with self._conn() as conn:
            cur = conn.executemany(f"DELETE FROM {table} WHERE code = ?", [(c,) for c in codes])
            if codes:
                self._log(conn, "accounts_deleted", actor=actor, detail={"table": table, "codes": list(codes)})
            return cur.rowcount

    # --- Tax setup and policy ---------------------------------------------------------------------

    def tax_treatments(self) -> dict[str, TaxTreatment]:
        with self._conn() as conn:
            stored = {r["tax_type"]: r for r in conn.execute("SELECT * FROM tax_treatments")}
        result = {}
        for tax_type in TAX_TYPES:
            r = stored.get(tax_type)
            result[tax_type] = (
                TaxTreatment(tax_type, r["treatment"], r["gl_code"])
                if r
                else TaxTreatment(tax_type, DEFAULT_TREATMENTS[tax_type])
            )
        return result

    def set_tax_treatment(self, tax_type: str, treatment: str, gl_code: str = "", actor: str | None = None) -> None:
        if tax_type not in TAX_TYPES or treatment not in TREATMENTS:
            raise ValueError("invalid tax type or treatment")
        current = self.tax_treatments()[tax_type]
        with self._conn() as conn:
            if (current.treatment, current.gl_code) != (treatment, gl_code.strip()):
                self._log(conn, "tax_setup_changed", actor=actor, detail={
                    "tax_type": tax_type, "treatment": treatment, "gl_code": gl_code.strip(),
                })  # fmt: skip
            conn.execute(
                "INSERT INTO tax_treatments (tax_type, treatment, gl_code) VALUES (?, ?, ?) "
                "ON CONFLICT(tax_type) DO UPDATE SET treatment = excluded.treatment, gl_code = excluded.gl_code",
                (tax_type, treatment, gl_code.strip()),
            )

    def get_setting(self, key: str, default: str = "") -> str:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str, actor: str | None = None) -> None:
        with self._conn() as conn:
            if key == "policy_notes" and self.get_setting(key) != value:
                self._log(conn, "policy_changed", actor=actor, detail={"rules": len(parse_policy_notes(value))})
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def has_reference(self) -> bool:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM gl_accounts").fetchone()[0] > 0

    def reference_data(self, rates: TaxRateTable | None = None) -> ReferenceData:
        gl = self.list_accounts("gl_accounts")
        if not gl:
            raise ValueError("No GL accounts imported yet: add them on the GL Accounts page")
        cc = self.list_accounts("cost_centers")

        def table(rows: list[dict[str, str]], kind: str, key: str) -> ReferenceTable:
            return ReferenceTable(
                kind, key, [{key: r["code"], "description": r["description"], "category": r["category"]} for r in rows]
            )

        return ReferenceData(
            chart_of_accounts=table(gl, "GL account", "gl_code"),
            cost_centers=table(cc, "cost center", "cost_center") if cc else None,
            tax=TaxSetup(rates or TaxRateTable.load(), self.tax_treatments()),
            notes=parse_policy_notes(self.get_setting("policy_notes")),
        )

    # --- Invoices --------------------------------------------------------------------------------------

    def add_invoice(
        self,
        source_path: str | Path,
        output: dict[str, Any] | None,
        validation: dict[str, Any] | None,
        extraction_md: str = "",
        meta: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> int:
        source_path = Path(source_path)
        sha = hashlib.sha256(source_path.read_bytes()).hexdigest() if source_path.exists() else None
        out = output or {}
        val = validation or {}
        with self._conn() as conn:
            if sha:  # a new attempt replaces earlier failed attempts at the same file
                conn.execute("DELETE FROM invoices WHERE file_sha256 = ? AND status = ?", (sha, FAILED))
            cur = conn.execute(
                """INSERT INTO invoices (source_path, file_name, file_sha256, status, vendor_name, vendor_key,
                   invoice_number, invoice_date, currency, grand_total, model_confidence, adjusted_confidence,
                   requires_review, ai_output, validation, extraction_md, meta, error, created_at, po_key,
                   due_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(source_path.resolve()),  # absolute: the dashboard may run from another folder
                    source_path.name,
                    sha,
                    FAILED if error else REVIEW,
                    out.get("vendor_name"),
                    vendor_key(out.get("vendor_name", "")),
                    out.get("invoice_number"),
                    out.get("invoice_date"),
                    out.get("currency"),
                    out.get("grand_total"),
                    val.get("model_confidence"),
                    val.get("adjusted_confidence"),
                    int(bool(val.get("requires_review", True))),
                    json.dumps(output) if output else None,
                    json.dumps(validation) if validation else None,
                    extraction_md,
                    json.dumps(meta or {}, default=str),
                    error,
                    _now(),
                    po_key(out.get("po_number") or ""),
                    _due(out, self.default_terms_days()) if out else None,
                ),
            )
            invoice_id = int(cur.lastrowid)
            if error:
                self._log(conn, "failed", invoice_id, detail={"file": source_path.name, "error": error})
            else:
                self._log(conn, "processed", invoice_id, detail={
                    "file": source_path.name, "vendor": out.get("vendor_name"),
                    "invoice_number": out.get("invoice_number"),
                    "total": out.get("grand_total"), "confidence": val.get("adjusted_confidence"),
                    "requires_review": bool(val.get("requires_review", True)),
                    "issues": len(val.get("issues") or []), "demo": bool((meta or {}).get("demo")),
                })  # fmt: skip
            return invoice_id

    def find_by_hash(self, path: str | Path, include_failed: bool = False) -> dict[str, Any] | None:
        """The latest invoice made from this exact file (failed attempts only if ``include_failed``)."""
        sha = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        sql = "SELECT id, status FROM invoices WHERE file_sha256 = ?"
        if not include_failed:
            sql += f" AND status != '{FAILED}'"
        with self._conn() as conn:
            row = conn.execute(sql + " ORDER BY id DESC", (sha,)).fetchone()
        return dict(row) if row else None

    def find_duplicates(
        self,
        vendor_name: str,
        invoice_number: str,
        exclude_id: int | None = None,
        grand_total: float | None = None,
    ) -> list[int]:
        """Other invoices with the same vendor and invoice number (possible duplicate payment).

        With ``grand_total``, a credit note (negative) is not a duplicate of the invoice it reverses.
        """
        key, number = vendor_key(vendor_name), _norm_number(invoice_number)
        if not key or not number:
            return []
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, invoice_number, grand_total FROM invoices "
                "WHERE vendor_key = ? AND status != ? AND status != ?",
                (key, FAILED, REJECTED),
            ).fetchall()

        def same_sign(other: float | None) -> bool:
            return grand_total is None or other is None or (grand_total < 0) == (other < 0)

        return [
            r["id"]
            for r in rows
            if _norm_number(r["invoice_number"]) == number and r["id"] != exclude_id and same_sign(r["grand_total"])
        ]

    def default_terms_days(self) -> int:
        """Days to pay when an invoice prints neither a due date nor terms (Settings → Review)."""
        try:
            return int(self.get_setting("default_terms_days") or DEFAULT_TERMS_DAYS)
        except ValueError:
            return DEFAULT_TERMS_DAYS

    def list_invoices(self, status: str | None = None) -> list[dict[str, Any]]:
        sql = (
            "SELECT id, file_name, status, vendor_name, invoice_number, invoice_date, due_date, currency, grand_total, "
            "model_confidence, adjusted_confidence, requires_review, error, created_at, reviewed_at, reviewer "
            "FROM invoices"
        )
        args: tuple[Any, ...] = ()
        if status:
            sql += " WHERE status = ?"
            args = (status,)
        sql += " ORDER BY requires_review DESC, adjusted_confidence ASC, id ASC"
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, args)]

    def get_invoice(self, invoice_id: int) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        if row is None:
            return None
        inv = dict(row)
        for key in ("ai_output", "final_output", "validation", "meta", "edits"):
            inv[key] = json.loads(inv[key]) if inv[key] else None
        return inv

    def reject_invoice(self, invoice_id: int, reviewer: str, reason: str = "") -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE invoices SET status = ?, reviewer = ?, reviewed_at = ?, error = ? WHERE id = ?",
                (REJECTED, reviewer, _now(), reason or None, invoice_id),
            )
            self._log(conn, "rejected", invoice_id, reviewer, {"reason": reason})

    def delete_invoice(self, invoice_id: int, forget_lessons: bool = False, actor: str | None = None) -> None:
        """Delete an invoice; with ``forget_lessons`` also what was learned when it was approved."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT file_name, vendor_name, invoice_number, status FROM invoices WHERE id = ?", (invoice_id,)
            ).fetchone()
            conn.execute("DELETE FROM invoices WHERE id = ?", (invoice_id,))
            if row is not None:
                self._log(conn, "deleted", invoice_id, actor, {
                    "file": row["file_name"], "vendor": row["vendor_name"], "invoice_number": row["invoice_number"],
                    "status": row["status"], "lessons_forgotten": forget_lessons,
                })  # fmt: skip
            if forget_lessons:
                conn.execute("DELETE FROM feedback WHERE invoice_id = ?", (invoice_id,))

    def list_invoices_full(self) -> list[dict[str, Any]]:
        """Every invoice with its file path, status and metadata (no AI output: kept light)."""
        with self._conn() as conn:
            rows = [dict(r) for r in conn.execute("SELECT id, source_path, status, meta FROM invoices ORDER BY id")]
        for r in rows:
            r["meta"] = json.loads(r["meta"]) if r["meta"] else {}
        return rows

    _JSON_COLUMNS = ("ai_output", "final_output", "validation", "meta", "edits")
    _LIGHT_COLUMNS = ("id", "status", "requires_review", "created_at", "reviewed_at", *_JSON_COLUMNS)

    def invoice_columns(
        self, columns: tuple[str, ...], status: str | None = None, ids: list[int] | None = None
    ) -> list[dict[str, Any]]:
        """Just these columns of every invoice, or of ``ids`` (JSON ones decoded): cheaper than
        ``get_invoice`` in a loop."""
        unknown = set(columns) - set(self._LIGHT_COLUMNS)
        if unknown:
            raise ValueError(f"unknown columns {sorted(unknown)}")
        where, args = [], []
        if status:
            where.append("status = ?")
            args.append(status)
        if ids is not None:
            where.append(f"id IN ({','.join('?' * len(ids))})")
            args.extend(ids)
        sql = f"SELECT {', '.join(columns)} FROM invoices" + (" WHERE " + " AND ".join(where) if where else "")
        with self._conn() as conn:
            rows = [dict(r) for r in conn.execute(sql + " ORDER BY id", args)]
        for r in rows:
            for key in set(columns) & set(self._JSON_COLUMNS):
                r[key] = json.loads(r[key]) if r[key] else None
        return rows

    def demo_count(self) -> int:
        """How many demo invoices are loaded (see ``demo.py``), without reading every invoice."""
        with self._conn() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM invoices WHERE meta LIKE '%\"demo\": true%'").fetchone()[0])

    def approved_since(self, since_iso: str) -> int:
        with self._conn() as conn:
            return int(
                conn.execute(
                    "SELECT COUNT(*) FROM invoices WHERE status IN (?, ?) AND reviewed_at >= ?",
                    (APPROVED, PENDING, since_iso),
                ).fetchone()[0]
            )

    def approve_invoice(
        self,
        invoice_id: int,
        final_output: dict[str, Any],
        reviewer: str,
        bulk: bool = False,
        open_issues: list[dict[str, Any]] | None = None,
    ) -> dict[str, int]:
        """Store the reviewer's final version and record one feedback row per line. ``open_issues``: the
        errors and warnings still showing when the reviewer approved (kept in the audit trail)."""
        inv = self.get_invoice(invoice_id)
        if inv is None:
            raise KeyError(invoice_id)
        if inv["status"] in (APPROVED, PENDING):
            raise ValueError(f"invoice {invoice_id} is already approved")
        ai = inv["ai_output"] or {"line_items": []}
        limit = self.approval_limit()
        needs_second = bool(limit) and abs(float(final_output.get("grand_total") or 0)) > limit
        vendor_name = final_output.get("vendor_name", "")
        key = vendor_key(vendor_name)
        now = _now()
        counts = {ACCEPTED: 0, CORRECTED: 0}
        feedback_rows = []
        for suggestion, li in pair_lines(ai.get("line_items", []), final_output.get("line_items", [])):
            s_gl = suggestion.get("predicted_gl_code") if suggestion else None
            s_cc = suggestion.get("predicted_cost_center", "") if suggestion else None
            f_gl, f_cc = li["predicted_gl_code"], li.get("predicted_cost_center", "")
            if f_gl == UNASSIGNED:
                continue  # not a coding decision: nothing to learn from it
            outcome = ACCEPTED if (s_gl == f_gl and (s_cc or "") == (f_cc or "")) else CORRECTED
            counts[outcome] += 1
            feedback_rows.append(
                (
                    invoice_id,
                    li["line_number"],
                    key,
                    vendor_name,
                    li["description"],
                    li.get("amount"),
                    s_gl,
                    f_gl,
                    s_cc,
                    f_cc,
                    outcome,
                    reviewer,
                    now,
                )  # fmt: skip
            )
        edits = [f for f in _HEADER_FIELDS if str(ai.get(f, "")) != str(final_output.get(f, ""))]
        if _tax_signature(ai) != _tax_signature(final_output):
            edits.append("tax_lines")
        if counts[CORRECTED]:
            edits.append("line_coding")
        if len(ai.get("line_items", [])) != len(final_output.get("line_items", [])):
            edits.append("line_count")
        with self._conn() as conn:
            # Claim the invoice first, in the same transaction as the feedback: if two approvals race,
            # the second finds it already approved and records nothing.
            cur = conn.execute(
                """UPDATE invoices SET status = ?, final_output = ?, edits = ?, reviewer = ?, reviewed_at = ?,
                   vendor_name = ?, vendor_key = ?, invoice_number = ?, invoice_date = ?, grand_total = ?,
                   po_key = ?, due_date = ?, second_reviewer = NULL, second_reviewed_at = NULL
                   WHERE id = ? AND status NOT IN (?, ?)""",
                (
                    PENDING if needs_second else APPROVED,
                    json.dumps(final_output),
                    json.dumps(edits),
                    reviewer,
                    now,
                    vendor_name,
                    key,
                    final_output.get("invoice_number"),
                    final_output.get("invoice_date"),
                    final_output.get("grand_total"),
                    po_key(final_output.get("po_number") or ""),
                    _due(final_output, self.default_terms_days()),
                    invoice_id,
                    APPROVED,
                    PENDING,
                ),  # fmt: skip
            )
            if cur.rowcount == 0:
                raise ValueError(f"invoice {invoice_id} is already approved")
            self._log(conn, "approved", invoice_id, reviewer, {
                "lines": len(final_output.get("line_items", [])), "corrected": counts[CORRECTED],
                "total": final_output.get("grand_total"), "changes": diff_coding(ai, final_output),
                **({"bulk": True} if bulk else {}), **({"needs_second": True} if needs_second else {}),
                **({"open_issues": open_issues} if open_issues else {}),
            })  # fmt: skip
            conn.executemany(
                """INSERT INTO feedback (invoice_id, line_number, vendor_key, vendor_name, description, amount,
                   suggested_gl, final_gl, suggested_cc, final_cc, outcome, reviewer, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                feedback_rows,
            )
        return counts

    # --- Second approval -----------------------------------------------------------------------------

    def approval_limit(self) -> float:
        """Invoices above this amount need a second approver (0: no limit). Settings → Review."""
        try:
            return max(float(self.get_setting("approval_limit") or 0), 0.0)
        except ValueError:
            return 0.0

    def final_approve(self, invoice_id: int, approver: str) -> None:
        """The second approval: by someone other than the first approver. The invoice can then be exported."""
        inv = self.get_invoice(invoice_id)
        if inv is None or inv["status"] != PENDING:
            raise ValueError(f"invoice {invoice_id} is not waiting for a second approval")
        if (approver or "").strip().casefold() == (inv["reviewer"] or "").strip().casefold():
            raise PermissionError("the second approval must come from someone other than the first approver")
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE invoices SET status = ?, second_reviewer = ?, second_reviewed_at = ? "
                "WHERE id = ? AND status = ?",
                (APPROVED, approver, _now(), invoice_id, PENDING),
            )
            if cur.rowcount == 0:
                raise ValueError(f"invoice {invoice_id} is not waiting for a second approval")
            self._log(conn, "final_approved", invoice_id, approver, {
                "first_approver": inv["reviewer"], "total": (inv["final_output"] or {}).get("grand_total"),
            })  # fmt: skip

    def send_back(self, invoice_id: int, actor: str, reason: str = "") -> None:
        """Return an invoice waiting for a second approval to the review queue. What was learned from the
        first approval is withdrawn: it is learned again when the invoice is approved again."""
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE invoices SET status = ?, reviewer = NULL, reviewed_at = NULL WHERE id = ? AND status = ?",
                (REVIEW, invoice_id, PENDING),
            )
            if cur.rowcount == 0:
                raise ValueError(f"invoice {invoice_id} is not waiting for a second approval")
            conn.execute("DELETE FROM feedback WHERE invoice_id = ?", (invoice_id,))
            self._log(conn, "sent_back", invoice_id, actor, {"reason": reason})

    # --- Purchase orders ---------------------------------------------------------------------------

    def import_purchase_orders(
        self, rows: list[dict[str, Any]], replace_all: bool = False, actor: str | None = None
    ) -> dict[str, int]:
        """Import PO lines (``po.rows_from_records`` output). A PO in the file replaces that PO's lines,
        so re-importing an ERP export updates quantities received; other POs are kept unless ``replace_all``."""
        orders: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = po_key(row["po_number"])
            if not key:
                continue
            order = orders.setdefault(key, {"row": row, "lines": []})
            order["lines"].append(row)
            if row.get("vendor_name") and not order["row"].get("vendor_name"):
                order["row"] = row
        now = _now()
        with self._conn() as conn:
            if replace_all:
                conn.execute("DELETE FROM po_lines")
                conn.execute("DELETE FROM purchase_orders")
            existing = {r["po_key"] for r in conn.execute("SELECT po_key FROM purchase_orders")}
            for key, order in orders.items():
                head = order["row"]
                status = "closed" if all(r.get("status") == "closed" for r in order["lines"]) else OPEN
                conn.execute(
                    """INSERT INTO purchase_orders (po_key, po_number, vendor_name, vendor_key, status, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(po_key) DO UPDATE SET po_number = excluded.po_number,
                       vendor_name = excluded.vendor_name, vendor_key = excluded.vendor_key,
                       status = excluded.status, updated_at = excluded.updated_at""",
                    (key, head["po_number"], head.get("vendor_name") or "", vendor_key(head.get("vendor_name") or ""),
                     status, now),
                )  # fmt: skip
                conn.execute("DELETE FROM po_lines WHERE po_key = ?", (key,))
                used: set[int] = set()
                for row in order["lines"]:
                    number = row.get("line_number")
                    if not number or number in used:
                        number = max(used, default=0) + 1
                    used.add(number)
                    conn.execute(
                        """INSERT INTO po_lines (po_key, line_number, description, quantity, unit_price, amount,
                           received_quantity, gl_code, cost_center) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (key, number, row["description"], row["quantity"], row["unit_price"], row["amount"],
                         row.get("received_quantity"), _clean_code(row.get("gl_code")),
                         _clean_code(row.get("cost_center"))),
                    )  # fmt: skip
            counts = {
                "orders": len(orders),
                "added": len(set(orders) - existing),
                "updated": len(set(orders) & existing),
                "lines": sum(len(o["lines"]) for o in orders.values()),
            }
            self._log(conn, "pos_imported", actor=actor, detail={**counts, "replace_all": replace_all})
        return counts

    def has_purchase_orders(self) -> bool:
        with self._conn() as conn:
            return conn.execute("SELECT 1 FROM purchase_orders LIMIT 1").fetchone() is not None

    def purchase_order(self, key: str) -> dict[str, Any] | None:
        """One PO with its lines and total (``key`` is ``po.po_key``)."""
        with self._conn() as conn:
            head = conn.execute("SELECT * FROM purchase_orders WHERE po_key = ?", (key,)).fetchone()
            if head is None:
                return None
            lines = [
                dict(r) for r in conn.execute("SELECT * FROM po_lines WHERE po_key = ? ORDER BY line_number", (key,))
            ]
        return {**dict(head), "lines": lines, "total": round(sum(li["amount"] for li in lines), 2)}

    def purchase_orders(self) -> list[dict[str, Any]]:
        """Every PO with its total and how much has been invoiced against it (invoices in review included)."""
        with self._conn() as conn:
            rows = [
                dict(r)
                for r in conn.execute(
                    """SELECT p.*, COUNT(l.line_number) lines, COALESCE(SUM(l.amount), 0) total,
                       SUM(l.received_quantity IS NOT NULL) received_lines
                       FROM purchase_orders p LEFT JOIN po_lines l ON l.po_key = p.po_key
                       GROUP BY p.po_key ORDER BY p.po_number"""
                )
            ]
            billed: dict[str, dict[str, Any]] = {}
            for r in conn.execute(
                "SELECT id, po_key, status, ai_output, final_output FROM invoices "
                "WHERE po_key != '' AND status IN (?, ?, ?)",
                ACTIVE_STATUSES,
            ):
                coding = json.loads(r["final_output"] or r["ai_output"] or "{}")
                entry = billed.setdefault(r["po_key"], {"billed": 0.0, "invoices": 0, "in_review": 0})
                entry["billed"] += float(coding.get("subtotal") or 0)
                entry["invoices"] += 1
                entry["in_review"] += r["status"] == REVIEW
        for r in rows:
            r.update(billed.get(r["po_key"], {"billed": 0.0, "invoices": 0, "in_review": 0}))
            r["remaining"] = r["total"] - r["billed"]
        return rows

    def open_pos_for_vendor(self, key: str) -> list[str]:
        if not key:
            return []
        with self._conn() as conn:
            return [
                r["po_number"]
                for r in conn.execute(
                    "SELECT po_number FROM purchase_orders WHERE vendor_key = ? AND status = ? ORDER BY po_number",
                    (key, OPEN),
                )
            ]

    def po_invoices(self, key: str, exclude_id: int | None = None) -> list[dict[str, Any]]:
        """Invoices quoting this PO (in review or approved), with their current coding."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, status, invoice_number, invoice_date, ai_output, final_output FROM invoices "
                "WHERE po_key = ? AND status IN (?, ?, ?) ORDER BY id",
                (key, *ACTIVE_STATUSES),
            ).fetchall()
        return [
            {
                "id": r["id"],
                "status": r["status"],
                "invoice_number": r["invoice_number"],
                "invoice_date": r["invoice_date"],
                "coding": json.loads(r["final_output"] or r["ai_output"] or "{}"),
            }  # fmt: skip
            for r in rows
            if r["id"] != exclude_id
        ]

    def set_po_status(self, key: str, status: str, actor: str | None = None) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE purchase_orders SET status = ?, updated_at = ? WHERE po_key = ?", (status, _now(), key)
            )
            number = conn.execute("SELECT po_number FROM purchase_orders WHERE po_key = ?", (key,)).fetchone()
            self._log(conn, "po_status", actor=actor, detail={"po": number[0] if number else key, "status": status})

    def delete_purchase_orders(self, keys: list[str], actor: str | None = None) -> int:
        with self._conn() as conn:
            marks = ",".join("?" * len(keys))
            numbers = [
                r[0] for r in conn.execute(f"SELECT po_number FROM purchase_orders WHERE po_key IN ({marks})", keys)
            ]
            conn.execute(f"DELETE FROM po_lines WHERE po_key IN ({marks})", keys)
            conn.execute(f"DELETE FROM purchase_orders WHERE po_key IN ({marks})", keys)
            if numbers:
                self._log(conn, "pos_deleted", actor=actor, detail={"pos": numbers})
        return len(numbers)

    # --- ERP export batches -------------------------------------------------------------------------

    def unexported_approved(self) -> list[dict[str, Any]]:
        """Approved invoices that have not been in an export batch yet (oldest approval first)."""
        with self._conn() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    """SELECT id, vendor_name, invoice_number, invoice_date, currency, grand_total, reviewer,
                              reviewed_at FROM invoices WHERE status = ? AND export_batch IS NULL
                       ORDER BY reviewed_at, id""",
                    (APPROVED,),
                )
            ]

    def create_export_batch(self, invoice_ids: list[int], fmt: str, actor: str | None = None) -> int:
        """Mark approved, not yet exported invoices as one batch. Returns the batch number."""
        with self._conn() as conn:
            marks = ", ".join("?" for _ in invoice_ids)
            eligible = conn.execute(
                f"SELECT id, grand_total FROM invoices WHERE id IN ({marks}) AND status = ? AND export_batch IS NULL",
                (*invoice_ids, APPROVED),
            ).fetchall()
            if not eligible:
                raise ValueError("none of these invoices can be exported (not approved, or already exported)")
            total = round(sum(r["grand_total"] or 0 for r in eligible), 2)
            cur = conn.execute(
                "INSERT INTO export_batches (created_at, actor, format, invoices, total) VALUES (?, ?, ?, ?, ?)",
                (_now(), actor, fmt, len(eligible), total),
            )
            batch = int(cur.lastrowid)
            conn.executemany("UPDATE invoices SET export_batch = ? WHERE id = ?", [(batch, r["id"]) for r in eligible])
            for r in eligible:
                self._log(conn, "exported", r["id"], actor, {"batch": batch, "format": fmt})
        return batch

    def export_batches(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM export_batches ORDER BY id DESC")]

    def batch_invoice_ids(self, batch: int) -> list[int]:
        with self._conn() as conn:
            return [
                r["id"] for r in conn.execute("SELECT id FROM invoices WHERE export_batch = ? ORDER BY id", (batch,))
            ]

    def undo_export_batch(self, batch: int, actor: str | None = None) -> int:
        """The ERP import failed: put the batch's invoices back in the ready-to-export list."""
        with self._conn() as conn:
            ids = [r["id"] for r in conn.execute("SELECT id FROM invoices WHERE export_batch = ?", (batch,))]
            conn.execute("UPDATE invoices SET export_batch = NULL WHERE export_batch = ?", (batch,))
            conn.execute("UPDATE export_batches SET undone_at = ?, undone_by = ? WHERE id = ?", (_now(), actor, batch))
            self._log(conn, "export_undone", actor=actor, detail={"batch": batch, "invoices": len(ids)})
        return len(ids)

    # --- Vendors ----------------------------------------------------------------------------------------

    def get_vendor(self, key: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM vendors WHERE vendor_key = ?", (key,)).fetchone()
        return dict(row) if row else None

    def save_vendor(
        self, key: str, display_name: str, status: str = "active", expected_gst: str = "", notes: str = "",
        actor: str | None = None,
    ) -> None:  # fmt: skip
        if status not in ("active", "on_hold"):
            raise ValueError(f"unknown vendor status {status!r}")
        before = self.get_vendor(key) or {}
        values = {"status": status, "expected_gst": expected_gst.strip(), "notes": notes.strip()}
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO vendors (vendor_key, display_name, status, expected_gst, notes, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(vendor_key) DO UPDATE SET display_name = excluded.display_name,
                   status = excluded.status, expected_gst = excluded.expected_gst, notes = excluded.notes,
                   updated_at = excluded.updated_at""",
                (key, display_name, status, values["expected_gst"], values["notes"], _now()),
            )
            changed = {k: v for k, v in values.items() if before.get(k, "active" if k == "status" else "") != v}
            if changed:
                self._log(conn, "vendor_updated", actor=actor, detail={"vendor": display_name, **changed})

    def vendor_invoices(self, key: str) -> list[dict[str, Any]]:
        """This vendor's invoices (newest first) with the GST/HST number each one carried."""
        with self._conn() as conn:
            rows = [
                dict(r)
                for r in conn.execute(
                    """SELECT id, status, export_batch, vendor_name, invoice_number, invoice_date, currency,
                              grand_total, adjusted_confidence, requires_review, created_at, reviewed_at,
                              ai_output, final_output
                       FROM invoices WHERE vendor_key = ? ORDER BY id DESC""",
                    (key,),
                )
            ]
        for r in rows:
            doc = json.loads(r.pop("final_output") or "null") or json.loads(r.pop("ai_output", None) or "null") or {}
            r.pop("ai_output", None)
            r["gst_hst_number"] = doc.get("gst_hst_registration_number") or ""
        return rows

    def vendor_invoice_dates(self) -> list[dict[str, Any]]:
        """Vendor, date and total of every invoice in review or approved (for ``recurring.detect``)."""
        with self._conn() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT vendor_key, vendor_name, invoice_date, grand_total, currency FROM invoices "
                    "WHERE vendor_key != '' AND status IN (?, ?, ?)",
                    ACTIVE_STATUSES,
                )
            ]

    def has_other_vendors(self, key: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM invoices WHERE vendor_key != ? AND vendor_key != '' AND status != ? LIMIT 1",
                (key, FAILED),
            ).fetchone()
        return row is not None

    def vendor_summaries(self) -> list[dict[str, Any]]:
        """One row per vendor seen on an invoice: counts, spend, dates, AI accuracy and AP's settings."""
        with self._conn() as conn:
            rows = [
                dict(r)
                for r in conn.execute(
                    """SELECT vendor_key, MAX(vendor_name) vendor_name, COUNT(*) invoices,
                              SUM(status = 'approved') approved, SUM(status = 'review') to_review,
                              SUM(CASE WHEN status = 'approved' AND currency = 'CAD'
                                       THEN grand_total ELSE 0 END) spend_cad,
                              MIN(invoice_date) first_invoice, MAX(invoice_date) last_invoice,
                              MIN(created_at) first_seen
                       FROM invoices WHERE vendor_key != '' AND status != 'failed' GROUP BY vendor_key"""
                )
            ]
            lessons = {
                r["vendor_key"]: dict(r)
                for r in conn.execute(
                    """SELECT vendor_key, COUNT(*) lines, SUM(outcome = 'accepted') accepted FROM feedback
                       GROUP BY vendor_key"""
                )
            }
            masters = {r["vendor_key"]: dict(r) for r in conn.execute("SELECT * FROM vendors")}
        for r in rows:
            fb = lessons.get(r["vendor_key"]) or {}
            r["lessons"] = fb.get("lines", 0)
            r["accuracy"] = (fb["accepted"] / fb["lines"]) if fb.get("lines") else None
            master = masters.get(r["vendor_key"]) or {}
            r["status"] = master.get("status", "active")
            r["expected_gst"] = master.get("expected_gst", "")
            r["notes"] = master.get("notes", "")
        return sorted(rows, key=lambda r: -(r["spend_cad"] or 0))

    def vendor_gl_usage(self, key: str) -> list[dict[str, Any]]:
        """GL accounts reviewers used for this vendor's lines, most used first."""
        with self._conn() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    """SELECT final_gl gl_code, COUNT(*) lines, SUM(outcome = 'corrected') corrected FROM feedback
                       WHERE vendor_key = ? GROUP BY final_gl ORDER BY lines DESC""",
                    (key,),
                )
            ]

    # --- Backups -----------------------------------------------------------------------------------------

    def backup_to(self, dest: str | Path) -> Path:
        """A consistent copy of the database (safe while the dashboard is running)."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        source = sqlite3.connect(self.path)
        target = sqlite3.connect(dest)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        return dest

    def backup_dir(self) -> Path:
        return self.path.parent / "backups"

    def list_backups(self) -> list[Path]:
        folder = self.backup_dir()
        return sorted(folder.glob("ap_coder-*.db"), reverse=True) if folder.exists() else []

    def backup_now(self, label: str = "", actor: str | None = None) -> Path:
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        suffix = f"-{label}" if label else ""
        dest = self.backup_dir() / f"ap_coder-{stamp}{suffix}.db"
        n = 2
        while dest.exists():  # never overwrite a backup made in the same second
            dest = self.backup_dir() / f"ap_coder-{stamp}{suffix or '-'}{n}.db"
            n += 1
        made = self.backup_to(dest)
        if label == "manual":
            self.log_event("backup_made", actor=actor, detail={"file": made.name})
        return made

    def auto_backup(self, keep: int = 14, min_hours: float = 20) -> Path | None:
        """Back up at most once a day (call it at start-up) and keep the newest ``keep`` daily copies."""
        daily = [p for p in self.list_backups() if p.stem.count("-") == 2]  # ap_coder-YYYYMMDD-HHMMSS
        if daily:
            age = dt.datetime.now() - dt.datetime.fromtimestamp(daily[0].stat().st_mtime)
            if age < dt.timedelta(hours=min_hours):
                return None
        if not self.path.exists():
            return None
        made = self.backup_now()
        for old in [p for p in self.list_backups() if p.stem.count("-") == 2][keep:]:
            old.unlink(missing_ok=True)
        return made

    def restore_from(self, backup: str | Path) -> Path:
        """Replace the database with a backup. The current one is backed up first (returned)."""
        safety = self.backup_now("before-restore")
        source = sqlite3.connect(Path(backup))
        target = sqlite3.connect(self.path)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        with self._conn() as conn:
            self._migrate(conn)  # an older backup may need upgrading
            self._log(conn, "backup_restored", detail={"file": Path(backup).name, "safety_copy": safety.name})
        return safety

    # --- Learning memory ------------------------------------------------------------------------------

    def feedback_rows(self, limit: int = 50_000, vendor_name: str | None = None) -> list[dict[str, Any]]:
        """Recorded reviewer decisions, newest first (only one vendor's when ``vendor_name`` is given)."""
        sql, args = "SELECT * FROM feedback", ()
        if vendor_name is not None:
            sql, args = sql + " WHERE vendor_key = ?", (vendor_key(vendor_name),)
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit))]

    def delete_feedback(self, ids: list[int], actor: str | None = None) -> int:
        with self._conn() as conn:
            cur = conn.executemany("DELETE FROM feedback WHERE id = ?", [(i,) for i in ids])
            if ids:
                self._log(conn, "lessons_forgotten", actor=actor, detail={"count": len(ids)})
            return cur.rowcount

    def metrics(self) -> dict[str, Any]:
        with self._conn() as conn:
            lines = conn.execute("SELECT outcome, COUNT(*) n FROM feedback GROUP BY outcome").fetchall()
            weekly = conn.execute(
                """SELECT strftime('%Y-W%W', created_at) week,
                          SUM(outcome = 'accepted') accepted, SUM(outcome = 'corrected') corrected
                   FROM feedback GROUP BY week ORDER BY week"""
            ).fetchall()
            by_vendor = conn.execute(
                """SELECT vendor_name, COUNT(*) lines, SUM(outcome = 'accepted') accepted,
                          SUM(outcome = 'corrected') corrected, MAX(created_at) last_seen
                   FROM feedback GROUP BY vendor_key ORDER BY lines DESC"""
            ).fetchall()
            corrections = conn.execute(
                """SELECT COALESCE(suggested_gl, '(new line)') suggested_gl, final_gl, COUNT(*) n
                   FROM feedback WHERE outcome = 'corrected' AND COALESCE(suggested_gl, '') != final_gl
                   GROUP BY suggested_gl, final_gl ORDER BY n DESC LIMIT 25"""
            ).fetchall()
            statuses = conn.execute("SELECT status, COUNT(*) n FROM invoices GROUP BY status").fetchall()
            untouched = conn.execute(
                "SELECT COUNT(*) FROM invoices WHERE status = ? AND edits = '[]'", (APPROVED,)
            ).fetchone()[0]
        outcome = {r["outcome"]: r["n"] for r in lines}
        total = sum(outcome.values())
        return {
            "lines_reviewed": total,
            "lines_accepted": outcome.get(ACCEPTED, 0),
            "lines_corrected": outcome.get(CORRECTED, 0),
            "line_accuracy": round(outcome.get(ACCEPTED, 0) / total, 4) if total else None,
            "invoices_by_status": {r["status"]: r["n"] for r in statuses},
            "invoices_approved_without_edits": untouched,
            "weekly": [dict(r) for r in weekly],
            "by_vendor": [dict(r) for r in by_vendor],
            "top_corrections": [dict(r) for r in corrections],
        }


def _tax_signature(output: dict[str, Any]) -> list[tuple[str, float]]:
    return sorted((t.get("tax_type", ""), round(float(t.get("tax_amount", 0)), 2)) for t in output.get("tax_lines", []))


SAMPLE_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def load_sample_setup(store: Store, data_dir: Path = SAMPLE_DATA_DIR) -> None:
    """Load the bundled sample GL accounts, cost centers, tax mapping and policy (for trying things out)."""
    import csv

    for table, filename, code_col in (
        ("gl_accounts", "chart_of_accounts.csv", "gl_code"),
        ("cost_centers", "cost_centers.csv", "cost_center"),
    ):
        with (data_dir / filename).open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        store.import_accounts(table, rows, code_col, "description", "category")
    with (data_dir / "tax_gl_mapping.csv").open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            store.set_tax_treatment(row["tax_type"], row["treatment"], row.get("gl_code") or "")
    store.set_setting("policy_notes", (data_dir / "coding_policy.md").read_text(encoding="utf-8"))


def load_sample_purchase_orders(store: Store, data_dir: Path = SAMPLE_DATA_DIR) -> list[str]:
    """Load the bundled sample purchase orders (they match the sample invoices). Returns the PO keys."""
    import csv

    from .po import map_columns, rows_from_records

    with (data_dir / "purchase_orders.csv").open(encoding="utf-8-sig", newline="") as fh:
        records = list(csv.DictReader(fh))
    rows, _ = rows_from_records(records, map_columns(list(records[0]) if records else []))
    store.import_purchase_orders(rows)
    return sorted({po_key(r["po_number"]) for r in rows})
