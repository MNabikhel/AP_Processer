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
import shutil
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
from .rules import Rule
from .tax import DEFAULT_TREATMENTS, TAX_TYPES, TREATMENTS, TaxRateTable, TaxSetup, TaxTreatment
from .terms import DEFAULT_TERMS_DAYS, payment
from .vendors import norm_invoice_number

# 10: erp_invoices, 11: coding_rules (by _SCHEMA), 12: currency, 13: credit notes not due,
# 14: invoice_capture, supplier_profiles, supplier_outcomes (by _SCHEMA), 15: reader_outcomes, page_reads (by _SCHEMA)
SCHEMA_VERSION = 15
ACCOUNT_TABLES = {"gl_accounts": "gl_code", "cost_centers": "cost_center"}

REVIEW, APPROVED, REJECTED, FAILED = "review", "approved", "rejected", "failed"
PENDING = "pending_approval"  # approved once, over the approval limit: waiting for a second approver
PARKED = "parked"  # out of the queue while waiting for information (a buyer, a credit note...)
ACTIVE_STATUSES = (REVIEW, PARKED, PENDING, APPROVED)  # invoices that count (for POs, recurring vendors...)
_ACTIVE_IN = "(" + ", ".join("?" * len(ACTIVE_STATUSES)) + ")"

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
CREATE TABLE IF NOT EXISTS erp_invoices (
    vendor_key TEXT NOT NULL, number_key TEXT NOT NULL, total REAL NOT NULL, vendor_name TEXT NOT NULL,
    invoice_number TEXT NOT NULL, invoice_date TEXT NOT NULL DEFAULT '', imported_at TEXT NOT NULL,
    PRIMARY KEY (vendor_key, number_key, total));
CREATE TABLE IF NOT EXISTS coding_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT, vendor TEXT NOT NULL DEFAULT '', contains TEXT NOT NULL DEFAULT '',
    gl_code TEXT NOT NULL, cost_center TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, created_by TEXT);
CREATE TABLE IF NOT EXISTS invoice_capture (
    invoice_id INTEGER PRIMARY KEY REFERENCES invoices (id) ON DELETE CASCADE, capture_json TEXT NOT NULL,
    layout_source TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS supplier_profiles (
    key TEXT PRIMARY KEY, display_name TEXT NOT NULL DEFAULT '', vendor_id TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'supervised', autonomous_since TEXT, audit_rate REAL, template_json TEXT,
    updated_at TEXT NOT NULL, updated_by TEXT);
CREATE TABLE IF NOT EXISTS supplier_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, supplier_key TEXT NOT NULL, invoice_id INTEGER, field TEXT NOT NULL,
    ai_value TEXT, final_value TEXT, correct INTEGER NOT NULL, source TEXT NOT NULL DEFAULT 'review',
    at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS supplier_outcomes_key_at ON supplier_outcomes (supplier_key, at);
CREATE INDEX IF NOT EXISTS supplier_outcomes_invoice ON supplier_outcomes (invoice_id);
CREATE TABLE IF NOT EXISTS reader_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id INTEGER, reader TEXT NOT NULL, field TEXT NOT NULL,
    read_value TEXT, final_value TEXT, correct INTEGER NOT NULL, evidence TEXT NOT NULL DEFAULT '',
    layout_source TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT '', at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS reader_outcomes_invoice ON reader_outcomes (invoice_id);
CREATE INDEX IF NOT EXISTS reader_outcomes_reader ON reader_outcomes (reader, field);
CREATE TABLE IF NOT EXISTS page_reads (
    invoice_id INTEGER PRIMARY KEY, status TEXT NOT NULL, model TEXT NOT NULL DEFAULT '',
    pages INTEGER NOT NULL DEFAULT 0, seconds REAL NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '', requested_by TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS page_reads_status ON page_reads (status, created_at);
"""

# The page reader's queue (``page_reads.status``). A read still "reading" after STALE_READING_HOURS was
# interrupted (the computer slept, the app was closed): it waits in line again.
PAGE_WAITING, PAGE_READING, PAGE_DONE, PAGE_FAILED, PAGE_SKIPPED = "waiting", "reading", "done", "failed", "skipped"
PAGE_READ_STATUSES = (PAGE_WAITING, PAGE_READING, PAGE_DONE, PAGE_FAILED, PAGE_SKIPPED)
STALE_READING_HOURS = 2
_IN_LINE = "(status = 'waiting' OR (status = 'reading' AND updated_at < ?))"  # ? = _stale_before()

# Fields compared when deciding whether a reviewer edited an invoice header.
_HEADER_FIELDS = (
    "vendor_name", "invoice_number", "invoice_date", "po_number", "payment_terms", "due_date", "currency",
    "supplier_province",
    "ship_to_province", "gst_hst_registration_number", "qst_registration_number", "original_invoice_number",
    "remit_bank_account",
    "subtotal", "tax_total", "grand_total",
)  # fmt: skip


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _stale_before() -> str:
    """A page read started before this time is no longer under way (see STALE_READING_HOURS)."""
    return (dt.datetime.now() - dt.timedelta(hours=STALE_READING_HOURS)).isoformat(timespec="seconds")


def parse_fx_rates(text: str) -> dict[str, float]:
    """ "USD=1.37, EUR 1,50; GBP: 1.85" -> {"USD": 1.37, "EUR": 1.5, "GBP": 1.85} (CAD is always 1)."""
    rates = {"CAD": 1.0}
    for code, value in re.findall(r"([A-Za-z]{3})\s*[=:]?\s*([0-9]+(?:[.,][0-9]+)?)", text or ""):
        rate = float(value.replace(",", "."))
        if rate > 0:
            rates[code.upper()] = rate
    return rates


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


def _due(coding: dict[str, Any], default_days: int = DEFAULT_TERMS_DAYS, vendor_terms: str = "") -> str | None:
    if float(coding.get("grand_total") or 0) <= 0:
        return None  # a credit note is not paid: it has no due date
    due = payment(coding, default_days, vendor_terms).due
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
        if version < 7:  # PO lines with an amount but no quantity (services, lump sums)
            columns = {r["name"] for r in conn.execute("PRAGMA table_info(po_lines)")}
            if "amount_only" not in columns:
                conn.execute("ALTER TABLE po_lines ADD COLUMN amount_only INTEGER NOT NULL DEFAULT 0")
        if version < 8:  # the vendor master imported from the ERP
            columns = {r["name"] for r in conn.execute("PRAGMA table_info(vendors)")}
            for column, kind in (
                ("erp_id", "TEXT NOT NULL DEFAULT ''"),
                ("terms", "TEXT NOT NULL DEFAULT ''"),
                ("default_gl", "TEXT NOT NULL DEFAULT ''"),
                ("in_master", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if column not in columns:
                    conn.execute(f"ALTER TABLE vendors ADD COLUMN {column} {kind}")
        if version < 9:  # parked invoices: why, and when to follow up
            columns = {r["name"] for r in conn.execute("PRAGMA table_info(invoices)")}
            for column in ("parked_reason", "follow_up"):
                if column not in columns:
                    conn.execute(f"ALTER TABLE invoices ADD COLUMN {column} TEXT")
        if version < 12:  # approvals before this version kept the AI's currency, not the reviewer's
            for r in conn.execute("SELECT id, final_output FROM invoices WHERE final_output IS NOT NULL").fetchall():
                currency = str((json.loads(r["final_output"]) or {}).get("currency") or "").strip().upper()
                if currency:
                    conn.execute("UPDATE invoices SET currency = ? WHERE id = ?", (currency, r["id"]))
        if version < 13:  # credit notes were given a due date like invoices
            conn.execute("UPDATE invoices SET due_date = NULL WHERE grand_total <= 0")
        # Older versions recorded AP Coder's own approvals (nobody checked them) as lessons, counted in the
        # accuracy: withdrawn whatever the version, as nothing is recorded for them any more.
        from .capture.workflow import AUTONOMOUS_REVIEWER

        conn.execute("DELETE FROM feedback WHERE reviewer = ?", (AUTONOMOUS_REVIEWER,))
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
        sql = (  # with the invoice's vendor and number, so a list of events says which invoice each is about
            "SELECT e.*, i.vendor_name AS invoice_vendor, i.invoice_number AS invoice_number FROM events e "
            "LEFT JOIN invoices i ON i.id = e.invoice_id WHERE 1 = 1"
        )
        args: list[Any] = []
        if invoice_id is not None:
            sql += " AND e.invoice_id = ?"
            args.append(invoice_id)
        if actions:
            sql += f" AND e.action IN ({', '.join('?' for _ in actions)})"
            args += actions
        with self._conn() as conn:
            rows = [dict(r) for r in conn.execute(sql + " ORDER BY e.id DESC LIMIT ?", (*args, limit))]
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
                    _due(out, self.default_terms_days(), self.vendor_terms(vendor_key(out.get("vendor_name", ""))))
                    if out
                    else None,
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

    # --- The ERP's invoice register (invoices entered before or outside AP Coder) ---------------------

    def import_erp_register(
        self, rows: list[dict[str, Any]], replace_all: bool = False, actor: str | None = None
    ) -> int:
        """Invoices already in the ERP (``registers.rows_from_records`` output). Returns how many are stored."""
        now = _now()
        with self._conn() as conn:
            if replace_all:
                conn.execute("DELETE FROM erp_invoices")
            conn.executemany(
                """INSERT INTO erp_invoices (vendor_key, number_key, total, vendor_name, invoice_number, invoice_date,
                   imported_at) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(vendor_key, number_key, total) DO UPDATE
                   SET invoice_date = excluded.invoice_date, imported_at = excluded.imported_at""",
                [
                    (vendor_key(r["vendor_name"]), _norm_number(r["invoice_number"]), round(float(r["total"]), 2),
                     r["vendor_name"], r["invoice_number"], r.get("invoice_date") or "", now)
                    for r in rows
                    if vendor_key(r["vendor_name"]) and _norm_number(r["invoice_number"]) and r.get("total") is not None
                ],
            )  # fmt: skip
            count = int(conn.execute("SELECT COUNT(*) FROM erp_invoices").fetchone()[0])
            self._log(conn, "erp_register_imported", actor=actor, detail={"rows": len(rows), "total": count})
        return count

    def erp_register_count(self) -> int:
        with self._conn() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM erp_invoices").fetchone()[0])

    def clear_erp_register(self, actor: str | None = None) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM erp_invoices")
            self._log(conn, "erp_register_imported", actor=actor, detail={"rows": 0, "total": 0, "cleared": True})

    def search_rows(self) -> list[dict[str, Any]]:
        """Every invoice's identifying fields and where it stands (for *Find an invoice*), with the date of
        its export batch."""
        with self._conn() as conn:
            batches = {r["id"]: r["created_at"] for r in conn.execute("SELECT id, created_at FROM export_batches")}
            rows = [
                dict(r)
                for r in conn.execute(
                    """SELECT id, status, file_name, vendor_name, invoice_number, invoice_date, due_date, currency,
                              grand_total, po_key, created_at, reviewed_at, reviewer, second_reviewer,
                              second_reviewed_at, export_batch, parked_reason, follow_up, error
                       FROM invoices ORDER BY id DESC"""
                )
            ]
        for r in rows:
            r["exported_at"] = batches.get(r["export_batch"]) if r["export_batch"] else None
        return rows

    def erp_register(self) -> list[dict[str, Any]]:
        """Every invoice in the imported ERP register."""
        with self._conn() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT vendor_key, vendor_name, invoice_number, number_key, invoice_date, total FROM erp_invoices"
                )
            ]

    def in_erp(self, vendor_name: str, invoice_number: str, grand_total: float | None) -> list[dict[str, Any]]:
        """This vendor's invoices with the same number already in the ERP (a credit note is not a duplicate
        of the invoice it reverses)."""
        key, number = vendor_key(vendor_name), _norm_number(invoice_number)
        if not key or not number:
            return []
        with self._conn() as conn:
            rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT invoice_number, invoice_date, total FROM erp_invoices "
                    "WHERE vendor_key = ? AND number_key = ?",
                    (key, number),
                )
            ]
        if grand_total is None:
            return rows
        return [r for r in rows if (r["total"] < 0) == (grand_total < 0)]

    def duplicates_elsewhere(
        self, vendor_name: str, invoice_number: str, grand_total: float, exclude_id: int | None = None
    ) -> list[dict[str, Any]]:
        """Invoices from ANOTHER vendor with the same invoice number and total: the same bill entered under a
        second vendor record (a common cause of duplicate payments)."""
        key, number = vendor_key(vendor_name), _norm_number(invoice_number)
        if not number or not grand_total:
            return []
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, vendor_name, vendor_key, invoice_number FROM invoices "
                "WHERE ABS(grand_total - ?) <= 0.01 AND status NOT IN (?, ?) AND vendor_key != ?",
                (grand_total, FAILED, REJECTED, key),
            ).fetchall()
        return [dict(r) for r in rows if r["id"] != exclude_id and _norm_number(r["invoice_number"]) == number]

    def list_invoices(self, status: str | None = None) -> list[dict[str, Any]]:
        sql = (
            "SELECT id, file_name, status, vendor_name, invoice_number, invoice_date, due_date, currency, grand_total, "
            "model_confidence, adjusted_confidence, requires_review, error, created_at, reviewed_at, reviewer, "
            "second_reviewer "
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

    def park_invoice(self, invoice_id: int, actor: str, reason: str, follow_up: str | None = None) -> None:
        """Take an invoice out of the queue while waiting for information; ``follow_up``: YYYY-MM-DD."""
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE invoices SET status = ?, parked_reason = ?, follow_up = ? WHERE id = ? AND status = ?",
                (PARKED, reason.strip(), follow_up or None, invoice_id, REVIEW),
            )
            if cur.rowcount == 0:
                raise ValueError(f"invoice {invoice_id} is not in the review queue")
            self._log(conn, "parked", invoice_id, actor, {"reason": reason.strip(), "follow_up": follow_up or ""})

    def unpark_invoice(self, invoice_id: int, actor: str) -> None:
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE invoices SET status = ?, parked_reason = NULL, follow_up = NULL WHERE id = ? AND status = ?",
                (REVIEW, invoice_id, PARKED),
            )
            if cur.rowcount == 0:
                raise ValueError(f"invoice {invoice_id} is not parked")
            self._log(conn, "unparked", invoice_id, actor)

    def parked(self) -> list[dict[str, Any]]:
        """Parked invoices, the ones to follow up first."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, vendor_name, invoice_number, invoice_date, currency, grand_total, parked_reason, "
                "follow_up FROM invoices WHERE status = ? ORDER BY follow_up IS NULL, follow_up, id",
                (PARKED,),
            ).fetchall()
        return [dict(r) for r in rows]

    def add_note(self, invoice_id: int, actor: str, text: str) -> None:
        """A note on an invoice for the team (kept in its history)."""
        if text.strip():
            self.log_event("note", invoice_id=invoice_id, actor=actor, detail={"text": text.strip()[:2000]})

    def reject_invoice(self, invoice_id: int, reviewer: str, reason: str = "") -> None:
        """Reject an invoice in the review queue, or a parked one. An approved invoice cannot be: a Reject clicked
        on a screen opened before someone approved it would wipe a finished approval (reopen it first). Anything
        learned from the invoice is withdrawn, as when it is reopened."""
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE invoices SET status = ?, reviewer = ?, reviewed_at = ?, second_reviewer = NULL, "
                "second_reviewed_at = NULL, parked_reason = NULL, follow_up = NULL, error = ? "
                "WHERE id = ? AND status IN (?, ?) AND export_batch IS NULL",
                (REJECTED, reviewer, _now(), reason or None, invoice_id, REVIEW, PARKED),
            )
            if cur.rowcount == 0:
                raise ValueError(
                    f"invoice {invoice_id} cannot be rejected (approved, rejected or deleted meanwhile: only an "
                    "invoice in the review queue or parked can be)"
                )
            self._forget_learning(conn, invoice_id)
            self._log(conn, "rejected", invoice_id, reviewer, {"reason": reason})

    @staticmethod
    def _forget_learning(conn: sqlite3.Connection, invoice_id: int) -> None:
        """Withdraw what an invoice's approval taught: its lessons, its supplier outcomes (from a review or an
        audit sample alike, so none keeps counting towards the supplier's autonomy) and its reader scores."""
        for table in ("feedback", "supplier_outcomes", "reader_outcomes"):
            conn.execute(f"DELETE FROM {table} WHERE invoice_id = ?", (invoice_id,))

    def delete_invoice(self, invoice_id: int, forget_lessons: bool = False, actor: str | None = None) -> None:
        """Delete an invoice; with ``forget_lessons`` also what was learned when it was approved."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT file_name, vendor_name, invoice_number, status FROM invoices WHERE id = ?", (invoice_id,)
            ).fetchone()
            conn.execute("DELETE FROM invoices WHERE id = ?", (invoice_id,))
            # What capture read, and the supplier accuracy measured on it, go with the invoice: a deleted
            # invoice (a duplicate upload, a demo) must not count towards a supplier's autonomy, a reader's
            # record or the confidence calibration, and is no longer waiting for the page reader.
            for table in ("invoice_capture", "supplier_outcomes", "reader_outcomes", "page_reads"):
                conn.execute(f"DELETE FROM {table} WHERE invoice_id = ?", (invoice_id,))
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
    _LIGHT_COLUMNS = (
        "id", "status", "requires_review", "created_at", "reviewed_at", "reviewer", "second_reviewer",
        "second_reviewed_at", "invoice_date", "due_date", "export_batch", "source_path", "file_name", *_JSON_COLUMNS
    )  # fmt: skip

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
        login: str = "",
    ) -> dict[str, int]:
        """Store the reviewer's final version and record one feedback row per line. ``open_issues``: the
        errors and warnings still showing when the reviewer approved (kept in the audit trail). Only an invoice
        in the review queue can be approved. An approval by AP Coder on its own (``AUTONOMOUS_REVIEWER``)
        records no feedback: nobody checked its coding, so it is no lesson and no measure of accuracy."""
        from .capture.workflow import AUTONOMOUS_REVIEWER

        inv = self.get_invoice(invoice_id)
        if inv is None:
            raise KeyError(invoice_id)
        if inv["status"] in (APPROVED, PENDING):
            raise ValueError(f"invoice {invoice_id} is already approved")
        if inv["status"] == REJECTED:  # e.g. rejected by someone else meanwhile: reopen it first
            raise ValueError(f"invoice {invoice_id} was rejected: reopen it before approving it")
        if inv["status"] == PARKED:  # parked by someone else meanwhile: waiting for information
            raise ValueError(f"invoice {invoice_id} is parked: bring it back to the queue before approving it")
        if inv["status"] != REVIEW:  # a failed read has nothing checked to approve
            raise ValueError(f"invoice {invoice_id} is not in the review queue ({inv['status']})")
        ai = inv["ai_output"] or {"line_items": []}
        needs_second = self.over_approval_limit(final_output)
        vendor_name = final_output.get("vendor_name", "")
        key = vendor_key(vendor_name)
        now = _now()
        counts = {ACCEPTED: 0, CORRECTED: 0}
        reviewer_changed = 0
        # Lines a fixed coding rule set: the AI's own answer is what its accuracy is measured on.
        by_rule = {c["line_number"]: c for c in (inv.get("meta") or {}).get("rules_applied") or []}
        feedback_rows = []
        for suggestion, li in pair_lines(ai.get("line_items", []), final_output.get("line_items", [])):
            s_gl = suggestion.get("predicted_gl_code") if suggestion else None
            s_cc = suggestion.get("predicted_cost_center", "") if suggestion else None
            f_gl, f_cc = li["predicted_gl_code"], li.get("predicted_cost_center", "")
            if f_gl == UNASSIGNED:
                continue  # not a coding decision: nothing to learn from it
            rule = by_rule.get(suggestion.get("line_number")) if suggestion else None
            kept_rule = rule is not None and rule.get("gl_to") == f_gl and (rule.get("cc_to") or "") == (f_cc or "")
            if not kept_rule and (s_gl != f_gl or (s_cc or "") != (f_cc or "")):
                reviewer_changed += 1
            if rule is not None:  # the AI's own answer, as first recorded (not a rule's)
                s_gl, s_cc = rule.get("gl_from"), rule.get("cc_from", "")
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
        if reviewer_changed:  # what the reviewer changed (a rule's coding kept is not a change)
            edits.append("line_coding")
        if len(ai.get("line_items", [])) != len(final_output.get("line_items", [])):
            edits.append("line_count")
        with self._conn() as conn:
            # Claim the invoice first, in the same transaction as the feedback: if two approvals race,
            # the second finds it already approved and records nothing.
            cur = conn.execute(
                """UPDATE invoices SET status = ?, final_output = ?, edits = ?, reviewer = ?, reviewed_at = ?,
                   vendor_name = ?, vendor_key = ?, invoice_number = ?, invoice_date = ?, grand_total = ?,
                   currency = ?, po_key = ?, due_date = ?, second_reviewer = NULL, second_reviewed_at = NULL
                   WHERE id = ? AND status = ?""",
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
                    str(final_output.get("currency") or "").strip().upper() or None,
                    po_key(final_output.get("po_number") or ""),
                    _due(final_output, self.default_terms_days(), self.vendor_terms(key)),
                    invoice_id,
                    REVIEW,
                ),  # fmt: skip
            )
            if cur.rowcount == 0:
                raise ValueError(f"invoice {invoice_id} left the review queue (approved, parked or rejected)")
            if reviewer == AUTONOMOUS_REVIEWER:
                feedback_rows = []  # nobody checked it: nothing to learn from, nothing to measure accuracy on
            self._log(conn, "approved", invoice_id, reviewer, {
                "lines": len(final_output.get("line_items", [])), "corrected": counts[CORRECTED],
                "total": final_output.get("grand_total"), "changes": diff_coding(ai, final_output),
                **({"bulk": True} if bulk else {}), **({"needs_second": True} if needs_second else {}),
                **({"open_issues": open_issues} if open_issues else {}), **({"login": login} if login else {}),
            })  # fmt: skip
            conn.executemany(
                """INSERT INTO feedback (invoice_id, line_number, vendor_key, vendor_name, description, amount,
                   suggested_gl, final_gl, suggested_cc, final_cc, outcome, reviewer, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                feedback_rows,
            )
        return counts

    # --- Second approval -----------------------------------------------------------------------------

    def fx_rates(self) -> dict[str, float]:
        """{currency: CAD per unit}, as AP set them in Settings (e.g. "USD=1.37, EUR 1.50")."""
        return parse_fx_rates(self.get_setting("fx_rates"))

    def approval_limit(self) -> float:
        """Invoices above this amount need a second approver (0: no limit). Settings → Review."""
        try:
            return max(float(self.get_setting("approval_limit") or 0), 0.0)
        except ValueError:
            return 0.0

    def over_approval_limit(self, coding: dict[str, Any], limit: float | None = None) -> bool:
        """Is this invoice's total over the approval limit? The limit is in CAD: a foreign-currency total is
        converted at the rate set in Settings (compared as it is when no rate is set)."""
        limit = self.approval_limit() if limit is None else limit
        if not limit:
            return False
        currency = str(coding.get("currency") or "CAD").strip().upper()
        rate = self.fx_rates().get(currency, 1.0)
        return abs(float(coding.get("grand_total") or 0)) * rate > limit

    def final_approve(self, invoice_id: int, approver: str, login: str = "") -> None:
        """The second approval: by someone other than the first approver (another name and, when the
        dashboard records it, another computer login). The invoice can then be exported."""
        inv = self.get_invoice(invoice_id)
        if inv is None or inv["status"] != PENDING:
            raise ValueError(f"invoice {invoice_id} is not waiting for a second approval")
        if (approver or "").strip().casefold() == (inv["reviewer"] or "").strip().casefold():
            raise PermissionError("the second approval must come from someone other than the first approver")
        first = next((e for e in self.events(invoice_id) if e["action"] == "approved"), None)
        first_login = ((first or {}).get("detail") or {}).get("login") or ""
        if login and first_login and login.casefold() == first_login.casefold():
            raise PermissionError("the second approval must come from another computer login than the first")
        with self._conn() as conn:
            cur = conn.execute(
                # Same first approval as checked above: it may have been sent back and re-approved meanwhile.
                "UPDATE invoices SET status = ?, second_reviewer = ?, second_reviewed_at = ? "
                "WHERE id = ? AND status = ? AND reviewer IS ? AND reviewed_at IS ?",
                (APPROVED, approver, _now(), invoice_id, PENDING, inv["reviewer"], inv["reviewed_at"]),
            )
            if cur.rowcount == 0:
                raise ValueError(f"invoice {invoice_id} is not waiting for a second approval")
            self._log(conn, "final_approved", invoice_id, approver, {
                "first_approver": inv["reviewer"], "total": (inv["final_output"] or {}).get("grand_total"),
                **({"login": login} if login else {}),
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
            self._forget_learning(conn, invoice_id)
            self._log(conn, "sent_back", invoice_id, actor, {"reason": reason})

    def refresh_confidence(self, invoice_id: int, adjusted: float, requires_review: bool) -> bool:
        """The confidence the review screen just worked out (with the reviewer's edits and today's checks), so
        the queue card shows the same number. Only for an invoice still in review; True when it changed."""
        with self._conn() as conn:
            cur = conn.execute(
                """UPDATE invoices SET adjusted_confidence = ?, requires_review = ?
                   WHERE id = ? AND status = ? AND (adjusted_confidence IS NULL OR ABS(adjusted_confidence - ?) > 0.0005
                   OR requires_review != ?)""",
                (adjusted, int(requires_review), invoice_id, REVIEW, adjusted, int(requires_review)),
            )
            return cur.rowcount > 0

    def reopen(self, invoice_id: int, actor: str, reason: str = "") -> None:
        """Put an approved invoice that is not exported yet, or a rejected one, back in the review queue to be
        corrected. What its approval taught is withdrawn (learned again at the next approval); the reviewer's
        coding is kept as the starting point. An exported invoice is reopened by undoing its batch first."""
        with self._conn() as conn:
            cur = conn.execute(
                """UPDATE invoices SET status = ?, reviewer = NULL, reviewed_at = NULL, second_reviewer = NULL,
                   second_reviewed_at = NULL, error = NULL
                   WHERE id = ? AND (status IN (?, ?) AND export_batch IS NULL OR status = ?)""",
                (REVIEW, invoice_id, APPROVED, PENDING, REJECTED),
            )
            if cur.rowcount == 0:
                raise ValueError(f"invoice {invoice_id} cannot be reopened (exported, or not approved or rejected)")
            self._forget_learning(conn, invoice_id)
            self._log(conn, "reopened", invoice_id, actor, {"reason": reason})

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
                           received_quantity, gl_code, cost_center, amount_only)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (key, number, row["description"], row["quantity"], row["unit_price"], row["amount"],
                         row.get("received_quantity"), _clean_code(row.get("gl_code")),
                         _clean_code(row.get("cost_center")), int(bool(row.get("amount_only")))),
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
                "WHERE po_key != '' AND status IN " + _ACTIVE_IN,
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
                "WHERE po_key = ? AND status IN " + _ACTIVE_IN + " ORDER BY id",
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

    def vendor_invoices_without_po(self, key: str) -> list[dict[str, Any]]:
        """This vendor's active invoices that quote no PO, with their coding (they may still bill a PO line)."""
        if not key:
            return []
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, status, invoice_number, invoice_date, ai_output, final_output FROM invoices "
                "WHERE vendor_key = ? AND po_key = '' AND status IN " + _ACTIVE_IN + " ORDER BY id",
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
        """Mark approved, not yet exported invoices as one batch. Returns the batch number. Two people exporting
        at once never put the same invoice in two batches: the write lock is taken before the invoices are
        read, and each is taken only if it is still approved and not exported."""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")  # the write lock from the read to the marks
            marks = ", ".join("?" for _ in invoice_ids)
            eligible = conn.execute(
                f"SELECT id, grand_total, currency FROM invoices WHERE id IN ({marks}) AND status = ? "
                "AND export_batch IS NULL ORDER BY id",
                (*invoice_ids, APPROVED),
            ).fetchall()
            if not eligible:
                raise ValueError("none of these invoices can be exported (not approved, or already exported)")
            cur = conn.execute(
                "INSERT INTO export_batches (created_at, actor, format, invoices, total) VALUES (?, ?, ?, ?, ?)",
                (_now(), actor, fmt, 0, 0.0),
            )
            batch = int(cur.lastrowid)
            taken = []
            for r in eligible:
                cur = conn.execute(
                    "UPDATE invoices SET export_batch = ? WHERE id = ? AND status = ? AND export_batch IS NULL",
                    (batch, r["id"], APPROVED),
                )
                if cur.rowcount:
                    taken.append(r)
            if not taken:
                raise ValueError("none of these invoices can be exported (not approved, or already exported)")
            currencies = {(r["currency"] or "").strip().upper() or "CAD" for r in taken}
            total = round(sum(r["grand_total"] or 0 for r in taken), 2) if len(currencies) == 1 else None
            conn.execute(  # no total across currencies: dollars and euros do not add up
                "UPDATE export_batches SET invoices = ?, total = ? WHERE id = ?", (len(taken), total, batch)
            )
            for r in taken:  # the amount and currency, so an undone batch still shows what went out
                self._log(conn, "exported", r["id"], actor, {
                    "batch": batch, "format": fmt, "total": r["grand_total"],
                    "currency": (r["currency"] or "").strip().upper() or "CAD",
                })  # fmt: skip
        return batch

    def export_batches(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM export_batches ORDER BY id DESC")]

    # --- Fixed coding rules ---------------------------------------------------------------------------

    def coding_rules(self) -> list[Rule]:
        with self._conn() as conn:
            return [
                Rule(r["vendor"], r["contains"], r["gl_code"], r["cost_center"], r["id"])
                for r in conn.execute("SELECT * FROM coding_rules ORDER BY id")
            ]

    def save_coding_rules(self, rules: list[Rule], actor: str | None = None) -> int:
        """Replace the rules (rules without a GL account, or without a vendor and words, are left out).
        Unchanged rules keep their id, so their age (the tie-breaker) is kept."""
        keep = [r for r in rules if r.gl_code.strip() and (r.vendor.strip() or r.contains.strip())]
        with self._conn() as conn:
            old = {(r["vendor"], r["contains"], r["gl_code"], r["cost_center"]): r["id"]
                   for r in conn.execute("SELECT * FROM coding_rules")}  # fmt: skip
            conn.execute("DELETE FROM coding_rules")
            now = _now()
            seen = set()
            for r in keep:
                values = (r.vendor.strip(), r.contains.strip(), _clean_code(r.gl_code), _clean_code(r.cost_center))
                if values in seen:  # the same rule twice: kept once
                    continue
                seen.add(values)
                conn.execute(
                    "INSERT INTO coding_rules (id, vendor, contains, gl_code, cost_center, created_at, created_by) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (old.get(values), *values, now, actor),
                )
            self._log(conn, "rules_changed", actor=actor, detail={"rules": len(keep)})
        return len(keep)

    def add_rules_applied(self, invoice_id: int, changes: list[dict[str, Any]]) -> None:
        """Remember lines a fixed coding rule set on the review screen (as rules applied at processing are), so
        the AI's accuracy is measured on its own answer for them."""
        with self._conn() as conn:
            row = conn.execute("SELECT meta FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
            if row is None:
                return
            meta = json.loads(row["meta"] or "{}") or {}
            known = {c["line_number"]: c for c in meta.get("rules_applied") or []}
            for c in changes:
                first = known.get(c["line_number"])
                # The AI's own answer stays the one recorded first.
                known[c["line_number"]] = (
                    {**c, "gl_from": first["gl_from"], "cc_from": first["cc_from"]} if first else c
                )
            meta["rules_applied"] = sorted(known.values(), key=lambda c: c["line_number"])
            conn.execute("UPDATE invoices SET meta = ? WHERE id = ?", (json.dumps(meta), invoice_id))

    def delete_empty_batches(self, batches: set[int]) -> int:
        """Delete these export batches if no invoice belongs to them any more (e.g. demo invoices removed)."""
        removed = 0
        with self._conn() as conn:
            for batch in batches:
                if not conn.execute("SELECT 1 FROM invoices WHERE export_batch = ? LIMIT 1", (batch,)).fetchone():
                    removed += conn.execute("DELETE FROM export_batches WHERE id = ?", (batch,)).rowcount
        return removed

    def batch_totals(self) -> dict[int, dict[str, float]]:
        """{batch: {currency: total}} of the invoices in each batch (an undone batch has none any more)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT export_batch, COALESCE(NULLIF(currency, ''), 'CAD') AS cur, SUM(grand_total) AS total "
                "FROM invoices WHERE export_batch IS NOT NULL GROUP BY export_batch, cur"
            ).fetchall()
        out: dict[int, dict[str, float]] = {}
        for r in rows:
            out.setdefault(r["export_batch"], {})[r["cur"]] = round(r["total"] or 0, 2)
        return out

    def exported_totals(self) -> dict[int, dict[str, float]]:
        """{batch: {currency: total}} of what each batch held when it was exported, from the audit trail: an
        undone batch's invoices are back in the ready list, but what went out is still shown per currency. An
        export recorded by an older version (without the amount) counts the invoice's amount as it is now; a
        batch with such an invoice since deleted is left out (its total is not known)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT e.detail, i.id, i.currency, i.grand_total FROM events e "
                "LEFT JOIN invoices i ON i.id = e.invoice_id WHERE e.action = 'exported'"
            ).fetchall()
        out: dict[int, dict[str, float]] = {}
        unknown: set[int] = set()
        for r in rows:
            detail = json.loads(r["detail"] or "{}") or {}
            if detail.get("batch") is None:
                continue
            batch = int(detail["batch"])
            if "currency" in detail:
                currency, amount = detail["currency"], detail.get("total")
            elif r["id"] is not None:
                currency, amount = (r["currency"] or "").strip().upper() or "CAD", r["grand_total"]
            else:
                unknown.add(batch)
                continue
            totals = out.setdefault(batch, {})
            totals[currency] = round(totals.get(currency, 0.0) + float(amount or 0), 2)
        return {batch: totals for batch, totals in out.items() if batch not in unknown}

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

    # --- Capture and supplier learning (see capture/supplier.py) --------------------------------------

    def save_capture(self, invoice_id: int, capture: dict[str, Any]) -> None:
        """What the capture readers found on an invoice (``CaptureResult.to_dict()``), kept with it."""
        with self._conn() as conn:
            self._save_capture(conn, invoice_id, capture)

    @staticmethod
    def _save_capture(conn: sqlite3.Connection, invoice_id: int, capture: dict[str, Any]) -> None:
        conn.execute(
            """INSERT INTO invoice_capture (invoice_id, capture_json, layout_source, created_at)
               VALUES (?, ?, ?, ?) ON CONFLICT(invoice_id) DO UPDATE SET capture_json = excluded.capture_json,
               layout_source = excluded.layout_source, created_at = excluded.created_at""",
            (invoice_id, json.dumps(capture, default=str), str(capture.get("layout_source") or ""), _now()),
        )

    def get_capture(self, invoice_id: int) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT capture_json FROM invoice_capture WHERE invoice_id = ?", (invoice_id,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def supplier_key_for(self, vendor_name: str, gst_number: str | None = None) -> str:
        """The supplier key for an invoice, with the vendor master's ERP id when the master knows it."""
        from .capture.supplier import supplier_key
        from .vendors import norm_tax_number

        ids = self.vendor_ids()
        vendor_id = ids.get(vendor_key(vendor_name or ""))
        if not vendor_id and gst_number:
            vendor_id = ids.get(f"gst:{norm_tax_number(gst_number)}")
        return supplier_key(vendor_name, gst_number, vendor_id)

    @staticmethod
    def _profile(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        profile = dict(row)
        profile["template"] = json.loads(profile.pop("template_json")) if profile.get("template_json") else None
        return profile

    def get_supplier_profile(self, key: str) -> dict[str, Any] | None:
        """{key, display_name, vendor_id, state, autonomous_since, audit_rate, template (dict), updated_*}."""
        with self._conn() as conn:
            return self._profile(conn.execute("SELECT * FROM supplier_profiles WHERE key = ?", (key,)).fetchone())

    def list_supplier_profiles(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM supplier_profiles ORDER BY display_name COLLATE NOCASE, key").fetchall()
        return [p for p in (self._profile(r) for r in rows) if p is not None]

    def save_supplier_profile(
        self, key: str, display_name: str | None = None, vendor_id: str | None = None, template: Any = None,
        actor: str | None = None,
    ) -> None:  # fmt: skip
        """Create or update a supplier's profile; only what is given changes (``template``: a ``Template``
        or its dict). The autonomy state is changed with ``set_supplier_state`` only."""
        if not key:
            raise ValueError("a supplier profile needs a key")
        if template is not None and hasattr(template, "to_dict"):
            template = template.to_dict()
        with self._conn() as conn:
            self._save_profile(conn, key, display_name, vendor_id, template, actor)

    @staticmethod
    def _save_profile(
        conn: sqlite3.Connection, key: str, display_name: str | None, vendor_id: str | None,
        template: dict[str, Any] | None, actor: str | None,
    ) -> None:  # fmt: skip
        conn.execute(
            """INSERT INTO supplier_profiles (key, display_name, vendor_id, template_json, updated_at, updated_by)
               VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET
               display_name = COALESCE(NULLIF(?, ''), display_name), vendor_id = COALESCE(NULLIF(?, ''), vendor_id),
               template_json = COALESCE(?, template_json), updated_at = excluded.updated_at,
               updated_by = COALESCE(excluded.updated_by, updated_by)""",
            (
                key, display_name or "", vendor_id or "", json.dumps(template) if template is not None else None,
                _now(), actor, display_name or "", vendor_id or "",
                json.dumps(template) if template is not None else None,
            ),
        )  # fmt: skip

    def autonomy_policy(self) -> Any:
        """The autonomy policy (``AutonomyPolicy``): the defaults, or the JSON in the ``autonomy_policy`` setting."""
        from .capture.supplier import AutonomyPolicy

        try:
            return AutonomyPolicy.from_dict(json.loads(self.get_setting("autonomy_policy") or "{}"))
        except (ValueError, TypeError):
            return AutonomyPolicy()

    def set_supplier_state(
        self, key: str, state: str, by: str | None, reason: str = "", audit_rate: float | None = None
    ) -> None:
        """Switch a supplier's autonomy: ``autonomous`` (only when it meets the policy, by a manager),
        ``supervised`` (turned off) or ``suspended`` (an audit or a failed check found an error)."""
        from .capture.supplier import AUTONOMOUS, STORED_STATES, SUSPENDED, meets_policy

        if state not in STORED_STATES:
            raise ValueError(f"unknown supplier state {state!r}")
        profile = self.get_supplier_profile(key)
        if profile is None:
            raise KeyError(key)
        policy = self.autonomy_policy()
        if state == AUTONOMOUS and profile["state"] != AUTONOMOUS:
            if not meets_policy(self.supplier_stats(key, policy.window), policy):
                raise ValueError(f"{profile['display_name'] or key} does not meet the autonomy policy yet")
        if state == profile["state"] or (state == SUSPENDED and profile["state"] != AUTONOMOUS):
            return  # nothing to suspend: the supplier is not touchless
        action = {AUTONOMOUS: "autonomy_on", SUSPENDED: "autonomy_suspended"}.get(state, "autonomy_off")
        rate = audit_rate if audit_rate is not None else profile["audit_rate"]
        if state == AUTONOMOUS and rate is None:
            rate = policy.audit_rate
        # Since when it is touchless (kept while suspended, to show; cleared when turned off).
        since = _now() if state == AUTONOMOUS else profile["autonomous_since"] if state == SUSPENDED else None
        with self._conn() as conn:
            conn.execute(
                "UPDATE supplier_profiles SET state = ?, autonomous_since = ?, audit_rate = ?, updated_at = ?, "
                "updated_by = ? WHERE key = ?",
                (state, since, rate, _now(), by, key),
            )
            self._log(conn, action, actor=by, detail={
                "supplier": profile["display_name"] or key, "key": key, "from": profile["state"], "to": state,
                **({"reason": reason} if reason else {}),
                **({"audit_rate": rate, "policy": policy.to_dict()} if state == AUTONOMOUS else {}),
            })  # fmt: skip

    def record_outcomes(
        self, supplier_key: str, invoice_id: int | None, rows: list[dict[str, Any]], source: str = "review",
        display_name: str = "", vendor_id: str = "", at: str | None = None,
    ) -> dict[str, Any]:  # fmt: skip
        """Record, per header field, what AP Coder proposed and what AP approved (``outcome_rows``).

        Recording the same invoice and source again replaces its rows (an invoice approved again after
        a reopen). A correction on an autonomous supplier suspends it at once."""
        from .capture.supplier import AUTONOMOUS, SUSPENDED

        if source not in ("review", "audit"):
            raise ValueError(f"unknown outcome source {source!r}")
        if not supplier_key:
            return {"fields": 0, "corrections": 0, "suspended": False}
        at = at or _now()
        corrections = [r["field"] for r in rows if not r.get("correct")]
        with self._conn() as conn:
            self._save_profile(conn, supplier_key, display_name, vendor_id, None, None)
            if invoice_id is not None:
                conn.execute("DELETE FROM supplier_outcomes WHERE invoice_id = ? AND source = ?", (invoice_id, source))
            conn.executemany(
                """INSERT INTO supplier_outcomes (supplier_key, invoice_id, field, ai_value, final_value, correct,
                   source, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (supplier_key, invoice_id, r["field"], json.dumps(r.get("ai_value"), default=str),
                     json.dumps(r.get("final_value"), default=str), 1 if r.get("correct") else 0, source, at)
                    for r in rows
                ],
            )  # fmt: skip
            state = conn.execute("SELECT state, display_name FROM supplier_profiles WHERE key = ?", (supplier_key,))
            profile = state.fetchone()
            suspended = bool(corrections) and profile["state"] == AUTONOMOUS
            if suspended:
                conn.execute(
                    "UPDATE supplier_profiles SET state = ?, updated_at = ?, updated_by = ? WHERE key = ?",
                    (SUSPENDED, _now(), "AP Coder", supplier_key),
                )
                self._log(conn, "autonomy_suspended", invoice_id, "AP Coder", {
                    "supplier": profile["display_name"] or supplier_key, "key": supplier_key, "from": AUTONOMOUS,
                    "to": SUSPENDED, "reason": f"{source} found a correction: {', '.join(corrections)}",
                })  # fmt: skip
        return {"fields": len(rows), "corrections": len(corrections), "suspended": suspended}

    def supplier_outcomes(self, key: str, limit: int = 5000) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM supplier_outcomes WHERE supplier_key = ? ORDER BY at DESC, id DESC LIMIT ?", (key, limit)
            )]  # fmt: skip
        for r in rows:
            for column in ("ai_value", "final_value"):
                r[column] = json.loads(r[column]) if r[column] else None
            r["correct"] = bool(r["correct"])
        return rows

    def supplier_stats(self, key: str, window: int = 200) -> Any:
        """``SupplierStats`` for a supplier: field accuracy over its last ``window`` reviewed invoices."""
        from .capture.supplier import SupplierStats

        with self._conn() as conn:
            total = conn.execute(
                "SELECT COUNT(DISTINCT invoice_id) FROM supplier_outcomes WHERE supplier_key = ?", (key,)
            ).fetchone()[0]
            rows = conn.execute(
                """SELECT invoice_id, field, correct, source, at FROM supplier_outcomes WHERE supplier_key = ?
                   AND invoice_id IN (SELECT invoice_id FROM supplier_outcomes WHERE supplier_key = ?
                   GROUP BY invoice_id ORDER BY MAX(at) DESC, invoice_id DESC LIMIT ?)""",
                (key, key, window),
            ).fetchall()
        return SupplierStats.from_rows(
            [dict(r) | {"correct": bool(r["correct"])} for r in rows], key=key, window=window, total_invoices=total
        )

    def supplier_keys_for_invoices(self, ids: list[int]) -> list[str]:
        if not ids:
            return []
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT DISTINCT supplier_key FROM supplier_outcomes WHERE invoice_id IN ({','.join('?' * len(ids))})",
                ids,
            ).fetchall()
        return [r[0] for r in rows]

    def prune_supplier_profiles(self, keys: list[str]) -> int:
        """Delete these supplier profiles when nothing is recorded for them any more (e.g. after the demo)."""
        if not keys:
            return 0
        marks = ",".join("?" * len(keys))
        with self._conn() as conn:
            return conn.execute(
                f"DELETE FROM supplier_profiles WHERE key IN ({marks}) AND state != 'autonomous' AND key NOT IN "
                "(SELECT DISTINCT supplier_key FROM supplier_outcomes)",
                keys,
            ).rowcount

    # --- Each reader against what AP approved; the page reader's queue -------------------------------------------

    def record_reader_outcomes(self, invoice_id: int, rows: list[dict[str, Any]], at: str | None = None) -> int:
        """What each capture reader read on an approved invoice, against what AP approved
        (``capture.workflow.reader_outcome_rows``): one row per reader and field; reader "fused" is the value
        AP saw, with its evidence key and status. Recording an invoice again replaces its rows (an invoice
        approved again after a reopen). Returns how many rows were kept."""
        at = at or _now()

        def text(value: Any) -> str | None:
            return None if value is None else str(value)

        keep = [
            (invoice_id, str(r["reader"]), str(r["field"]), text(r.get("read_value")), text(r.get("final_value")),
             1 if r.get("correct") else 0, str(r.get("evidence") or ""), str(r.get("layout_source") or ""),
             str(r.get("status") or ""), str(r.get("at") or at))
            for r in rows
            if r.get("reader") and r.get("field")
        ]  # fmt: skip
        with self._conn() as conn:
            conn.execute("DELETE FROM reader_outcomes WHERE invoice_id = ?", (invoice_id,))
            conn.executemany(
                """INSERT INTO reader_outcomes (invoice_id, reader, field, read_value, final_value, correct, evidence,
                   layout_source, status, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                keep,
            )
        return len(keep)

    @staticmethod
    def _score(row: sqlite3.Row) -> dict[str, Any]:
        fields, agreed = int(row["fields"]), int(row["agreed"] or 0)
        return {"fields": fields, "agreed": agreed, "rate": agreed / fields if fields else None,
                "invoices": int(row["invoices"])}  # fmt: skip

    def reader_scorecard(self) -> list[dict[str, Any]]:
        """Each reader's record against AP's approvals: {reader, fields (compared), agreed (with AP), rate,
        invoices}. Reader "fused" is the combined value AP saw on the review screen."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT reader, COUNT(*) fields, SUM(correct) agreed, COUNT(DISTINCT invoice_id) invoices
                   FROM reader_outcomes GROUP BY reader ORDER BY reader"""
            ).fetchall()
        return [{"reader": r["reader"], **self._score(r)} for r in rows]

    def reader_field_scorecard(self, reader: str) -> list[dict[str, Any]]:
        """One reader's record field by field, the weakest first: {field, fields, agreed, rate, invoices,
        last_read, last_final}, the last two being its latest disagreement with AP (None if it always agreed)."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT field, COUNT(*) fields, SUM(correct) agreed, COUNT(DISTINCT invoice_id) invoices
                   FROM reader_outcomes WHERE reader = ? GROUP BY field""",
                (reader,),
            ).fetchall()
            # With MAX(id), SQLite takes the other columns from that row: the latest disagreement of each field.
            latest = {
                r["field"]: r
                for r in conn.execute(
                    """SELECT field, MAX(id) id, read_value, final_value FROM reader_outcomes
                       WHERE reader = ? AND correct = 0 GROUP BY field""",
                    (reader,),
                )
            }
        out = []
        for r in rows:
            wrong = latest.get(r["field"])
            out.append({"field": r["field"], **self._score(r), "last_read": wrong["read_value"] if wrong else None,
                        "last_final": wrong["final_value"] if wrong else None})  # fmt: skip
        return sorted(out, key=lambda r: (r["rate"], r["field"]))

    def fused_status_scorecard(self) -> list[dict[str, Any]]:
        """How often the value AP saw was the one AP approved, by the status it showed (verified first):
        {status, fields, agreed, rate, invoices}."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT status, COUNT(*) fields, SUM(correct) agreed, COUNT(DISTINCT invoice_id) invoices
                   FROM reader_outcomes WHERE reader = 'fused' AND status != '' GROUP BY status"""
            ).fetchall()
        order = {"verified": 0, "likely": 1, "check": 2}
        scores = [{"status": r["status"], **self._score(r)} for r in rows]
        return sorted(scores, key=lambda r: order.get(r["status"], 9))

    def evidence_counts(self) -> dict[str, tuple[int, int]]:
        """{evidence key: (values, right)}: how often each pattern of readers and checks
        (``capture.confidence.evidence_key``) gave the value AP approved on this company's own invoices (the
        values AP saw, reader "fused"). The local calibration of confidence counts them."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT evidence, COUNT(*) n, SUM(correct) ok FROM reader_outcomes
                   WHERE reader = 'fused' AND evidence != '' GROUP BY evidence"""
            ).fetchall()
        return {r["evidence"]: (int(r["n"]), int(r["ok"] or 0)) for r in rows}

    def queue_page_read(self, invoice_id: int, reason: str, requested_by: str = "") -> bool:
        """Put an invoice in line for the page reader. One already waiting keeps its place in line (a person
        asking for it is noted); one read before goes to the back of the line; a read under way is left to
        finish (False)."""
        now = _now()
        waiting = "page_reads.status IN ('waiting', 'reading')"  # past the WHERE below, a reading row is stale
        keep = f"{waiting} AND excluded.requested_by = ''"  # a background request never replaces a person's
        with self._conn() as conn:
            cur = conn.execute(
                f"""INSERT INTO page_reads (invoice_id, status, reason, requested_by, created_at, updated_at)
                    VALUES (?, 'waiting', ?, ?, ?, ?) ON CONFLICT(invoice_id) DO UPDATE SET status = 'waiting',
                    reason = CASE WHEN {keep} THEN page_reads.reason ELSE excluded.reason END,
                    requested_by = CASE WHEN {keep} THEN page_reads.requested_by ELSE excluded.requested_by END,
                    created_at = CASE WHEN {waiting} THEN page_reads.created_at ELSE excluded.created_at END,
                    error = '', updated_at = excluded.updated_at
                    WHERE page_reads.status != 'reading' OR page_reads.updated_at < ?""",
                (invoice_id, reason or "", requested_by or "", now, now, _stale_before()),
            )
            return cur.rowcount > 0

    def next_page_read(self, invoice_id: int | None = None) -> dict[str, Any] | None:
        """The invoice the page reader should read next (the one waiting longest), marked as being read; None
        when nothing waits. With ``invoice_id``: that invoice, if it is in line (the one AP asked for, read
        first), else None. Two readers (the dashboard's and ``read-pages``) never take the same invoice."""
        now, stale = _now(), _stale_before()
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")  # the write lock from the choice to the mark
            if invoice_id is None:
                row = conn.execute(
                    f"SELECT invoice_id FROM page_reads WHERE {_IN_LINE} ORDER BY created_at, invoice_id LIMIT 1",
                    (stale,),
                ).fetchone()
            else:
                row = conn.execute(
                    f"SELECT invoice_id FROM page_reads WHERE invoice_id = ? AND {_IN_LINE}", (invoice_id, stale)
                ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE page_reads SET status = 'reading', error = '', updated_at = ? WHERE invoice_id = ?",
                (now, row[0]),
            )
            taken = conn.execute("SELECT * FROM page_reads WHERE invoice_id = ?", (row[0],)).fetchone()
        return dict(taken)

    def finish_page_read(
        self, invoice_id: int, status: str, model: str = "", pages: int = 0, seconds: float = 0.0, error: str = ""
    ) -> bool:
        """How a page read ended: ``done``, ``failed`` (``error`` says why), ``skipped`` (nothing to read, or the
        invoice was dealt with meanwhile) or ``waiting`` (stopped part-way: it keeps its place in line). False
        when the invoice is no longer in line (deleted meanwhile)."""
        if status not in (PAGE_DONE, PAGE_FAILED, PAGE_SKIPPED, PAGE_WAITING):
            raise ValueError(f"unknown page read status {status!r}")
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE page_reads SET status = ?, model = ?, pages = ?, seconds = ?, error = ?, updated_at = ? "
                "WHERE invoice_id = ?",
                (status, model or "", int(pages or 0), round(float(seconds or 0), 2), (error or "")[:2000], _now(),
                 invoice_id),
            )  # fmt: skip
            return cur.rowcount > 0

    def page_read(self, invoice_id: int) -> dict[str, Any] | None:
        """{invoice_id, status, model, pages, seconds, reason, error, requested_by, created_at, updated_at}, or
        None if the invoice was never put in line. A read interrupted long ago shows as waiting."""
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM page_reads WHERE invoice_id = ?", (invoice_id,)).fetchone()
        if row is None:
            return None
        read = dict(row)
        if read["status"] == PAGE_READING and read["updated_at"] < _stale_before():
            read["status"] = PAGE_WAITING
        return read

    def page_reads_waiting(self) -> int:
        """How many invoices wait for the page reader (interrupted reads included)."""
        with self._conn() as conn:
            row = conn.execute(f"SELECT COUNT(*) FROM page_reads WHERE {_IN_LINE}", (_stale_before(),)).fetchone()
        return int(row[0])

    def page_reads_ahead(self, invoice_id: int) -> int:
        """How many invoices the page reader reads before this one: the one being read now, and those in line
        ahead of it in the order ``next_page_read`` takes them. 0 when it isn't waiting."""
        stale = _stale_before()
        with self._conn() as conn:
            me = conn.execute(
                f"SELECT created_at FROM page_reads WHERE invoice_id = ? AND {_IN_LINE}", (invoice_id, stale)
            ).fetchone()
            if me is None:
                return 0
            row = conn.execute(
                f"""SELECT COUNT(*) FROM page_reads WHERE invoice_id != ? AND ((status = 'reading' AND updated_at >= ?)
                    OR ({_IN_LINE} AND (created_at < ? OR (created_at = ? AND invoice_id < ?))))""",
                (invoice_id, stale, stale, me[0], me[0], invoice_id),
            ).fetchone()
        return int(row[0])

    def replace_proposal(
        self, invoice_id: int, output: dict[str, Any], report: Any, capture: Any, meta: dict[str, Any] | None = None,
        actor: str = "AP Coder",
    ) -> bool:  # fmt: skip
        """Replace what AP Coder proposes for an invoice nobody has worked on yet (the page reader read it after
        it was processed): the coding, its checks and confidence, the columns the queue shows, the capture
        (``CaptureResult`` or its dict; None keeps the stored one), and ``meta`` merged into the processing
        notes. ``report``: the ``ValidationReport`` or its dict.

        Only while the invoice waits in the review queue untouched: no edits recorded and never approved (a
        reopened invoice keeps the reviewer's coding). Otherwise nothing changes and it returns False."""
        if not output:
            return False
        validation = report.to_dict() if hasattr(report, "to_dict") else dict(report or {})
        if capture is not None and hasattr(capture, "to_dict"):
            capture = capture.to_dict()
        key = vendor_key(output.get("vendor_name", ""))
        due = _due(output, self.default_terms_days(), self.vendor_terms(key))
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")  # checked and changed in one go: an approval cannot slip in between
            row = conn.execute(
                "SELECT status, edits, final_output, reviewer, ai_output, meta FROM invoices WHERE id = ?",
                (invoice_id,),
            ).fetchone()
            if (row is None or row["status"] != REVIEW or row["final_output"] or row["reviewer"]
                    or json.loads(row["edits"] or "null")):  # fmt: skip
                return False
            merged = {**(json.loads(row["meta"] or "null") or {}), **(meta or {})}
            conn.execute(
                """UPDATE invoices SET ai_output = ?, validation = ?, model_confidence = ?, adjusted_confidence = ?,
                   requires_review = ?, vendor_name = ?, vendor_key = ?, invoice_number = ?, invoice_date = ?,
                   currency = ?, grand_total = ?, po_key = ?, due_date = ?, meta = ? WHERE id = ?""",
                (json.dumps(output), json.dumps(validation) if validation else None,
                 validation.get("model_confidence"), validation.get("adjusted_confidence"),
                 int(bool(validation.get("requires_review", True))), output.get("vendor_name"), key,
                 output.get("invoice_number"), output.get("invoice_date"), output.get("currency"),
                 output.get("grand_total"), po_key(output.get("po_number") or ""), due,
                 json.dumps(merged, default=str), invoice_id),
            )  # fmt: skip
            if capture is not None:
                self._save_capture(conn, invoice_id, capture)
            self._log(conn, "proposal_updated", invoice_id, actor, {
                "by": "page reader", "changes": diff_coding(json.loads(row["ai_output"] or "null") or {}, output),
                "confidence": validation.get("adjusted_confidence"),
                "requires_review": bool(validation.get("requires_review", True)),
            })  # fmt: skip
        return True

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

    def import_vendor_master(
        self, rows: list[dict[str, Any]], actor: str | None = None, only_new: bool = False
    ) -> dict[str, Any]:
        """Import the ERP's vendor list (``vendors.master_rows`` output). Each vendor is matched by name;
        what the file gives (ERP ID, GST/HST #, terms, default GL, status) replaces what was there; what
        it has no column for is kept.
        Rows whose names normalise to the same vendor are merged (on hold if any of them is) and reported
        in ``duplicates``: often the same supplier set up twice in the ERP. With ``only_new``, vendors
        already in the list are left alone (``inserted`` lists the new ones)."""
        added = updated = 0
        now = _now()
        merged: dict[str, dict[str, Any]] = {}
        names: dict[str, list[str]] = {}
        for row in rows:
            key = vendor_key(row["vendor_name"])
            if not key:
                continue
            names.setdefault(key, []).append(row["vendor_name"])
            if key in merged:
                first = merged[key]
                if row.get("status") == "on_hold":
                    first["status"], first["status_given"] = "on_hold", True
                for field in ("erp_id", "gst", "terms", "default_gl"):
                    first[field] = first.get(field) or row.get(field) or first.get(field)
            else:
                merged[key] = dict(row)
        duplicates = [n for n in names.values() if len(n) > 1]
        inserted: list[str] = []
        with self._conn() as conn:
            existing = {r["vendor_key"] for r in conn.execute("SELECT vendor_key FROM vendors")}
            for key, row in merged.items():
                if only_new and key in existing:
                    continue
                conn.execute(
                    """INSERT INTO vendors (vendor_key, display_name, status, expected_gst, notes, updated_at, erp_id,
                       terms, default_gl, in_master) VALUES (?, ?, ?, ?, '', ?, ?, ?, ?, 1)
                       ON CONFLICT(vendor_key) DO UPDATE SET
                       erp_id = CASE WHEN ? THEN excluded.erp_id ELSE vendors.erp_id END,
                       terms = CASE WHEN ? THEN excluded.terms ELSE vendors.terms END,
                       default_gl = CASE WHEN ? THEN excluded.default_gl ELSE vendors.default_gl END,
                       in_master = 1, updated_at = excluded.updated_at,
                       expected_gst = CASE WHEN excluded.expected_gst != '' THEN excluded.expected_gst
                                      ELSE vendors.expected_gst END,
                       status = CASE WHEN ? THEN excluded.status ELSE vendors.status END""",
                    (key, row["vendor_name"], row.get("status") or "active", row.get("gst") or "", now,
                     row.get("erp_id") or "", row.get("terms") or "", _clean_code(row.get("default_gl")),
                     *(row.get(f) is not None for f in ("erp_id", "terms", "default_gl")),
                     bool(row.get("status_given"))),
                )  # fmt: skip
                if key in existing:
                    updated += 1
                else:
                    added += 1
                    inserted.append(key)
                    existing.add(key)
            self._log(conn, "vendors_imported", actor=actor, detail={
                "added": added, "updated": updated, "duplicates": len(duplicates),
            })  # fmt: skip
        return {"added": added, "updated": updated, "duplicates": duplicates, "inserted": inserted}

    def delete_vendors(self, keys: list[str]) -> int:
        """Remove vendors from the vendor list (e.g. the demo's sample vendor master)."""
        with self._conn() as conn:
            marks = ",".join("?" * len(keys))
            return conn.execute(f"DELETE FROM vendors WHERE vendor_key IN ({marks})", keys).rowcount

    def has_vendor_master(self) -> bool:
        with self._conn() as conn:
            return conn.execute("SELECT 1 FROM vendors WHERE in_master = 1 LIMIT 1").fetchone() is not None

    def master_vendors(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM vendors WHERE in_master = 1")]

    def vendor_ids(self) -> dict[str, str]:
        """{vendor_key: ERP vendor ID} from the vendor master, plus {"gst:<number>": ERP vendor ID} so an
        invoice whose vendor name differs but whose GST/HST number matches still gets its ID."""
        from .vendors import norm_tax_number

        with self._conn() as conn:
            rows = conn.execute("SELECT vendor_key, erp_id, expected_gst FROM vendors WHERE erp_id != ''").fetchall()
        ids = {r[0]: r[1] for r in rows}
        for r in rows:
            number = norm_tax_number(r[2])
            if sum(c.isdigit() for c in number) >= 9:
                ids.setdefault(f"gst:{number}", r[1])
        return ids

    def all_vendor_terms(self) -> dict[str, str]:
        """{vendor_key: payment terms} for every vendor that has terms in the vendor master."""
        with self._conn() as conn:
            return {r[0]: r[1] for r in conn.execute("SELECT vendor_key, terms FROM vendors WHERE terms != ''")}

    def vendor_terms(self, key: str) -> str:
        """The vendor master's payment terms for this vendor ('' if none)."""
        if not key:
            return ""
        with self._conn() as conn:
            row = conn.execute("SELECT terms FROM vendors WHERE vendor_key = ?", (key,)).fetchone()
        return row[0] if row else ""

    def vendor_invoices(self, key: str) -> list[dict[str, Any]]:
        """This vendor's invoices (newest first) with the GST/HST number and bank account each one carried."""
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
            r["bank_account"] = doc.get("remit_bank_account") or ""
            r["original_invoice_number"] = doc.get("original_invoice_number") or ""
        return rows

    def vendor_invoice_dates(self) -> list[dict[str, Any]]:
        """Vendor, date and total of every invoice in review or approved (for ``recurring.detect``)."""
        with self._conn() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT vendor_key, vendor_name, invoice_date, grand_total, currency FROM invoices "
                    "WHERE vendor_key != '' AND status IN " + _ACTIVE_IN,
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
                              SUM(CASE WHEN status = 'approved' AND COALESCE(NULLIF(currency, ''), 'CAD') = 'CAD'
                                       THEN grand_total ELSE 0 END) spend_cad,
                              MIN(invoice_date) first_invoice, MAX(invoice_date) last_invoice,
                              MIN(created_at) first_seen
                       FROM invoices WHERE vendor_key != '' AND status != 'failed' GROUP BY vendor_key"""
                )
            ]
            lessons = {
                r["vendor_key"]: dict(r)
                for r in conn.execute(
                    """SELECT vendor_key, COUNT(*) lessons, SUM(outcome != 'history') lines,
                              SUM(outcome = 'accepted') accepted FROM feedback GROUP BY vendor_key"""
                )
            }
            masters = {r["vendor_key"]: dict(r) for r in conn.execute("SELECT * FROM vendors")}
        for r in rows:
            fb = lessons.get(r["vendor_key"]) or {}
            r["lessons"] = fb.get("lessons", 0)
            r["accuracy"] = (fb["accepted"] / fb["lines"]) if fb.get("lines") else None
            master = masters.get(r["vendor_key"]) or {}
            r["status"] = master.get("status", "active")
            r["expected_gst"] = master.get("expected_gst", "")
            r["notes"] = master.get("notes", "")
            for field in ("erp_id", "terms", "default_gl"):
                r[field] = master.get(field) or ""
            r["in_master"] = bool(master.get("in_master"))
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
        if label in ("", "manual"):
            self.copy_backup(made)
        return made

    def copy_backup(self, made: Path, keep: int = 14) -> Path | None:
        """Copy a backup to the second location set in Settings (e.g. a OneDrive or network folder), keeping
        its newest ``keep`` daily copies. A copy that fails (folder offline) is recorded, never raised: the
        local backup is there either way."""
        folder = self.get_setting("backup_copy_dir").strip()
        if not folder:
            return None
        try:
            target = Path(folder).expanduser()
            if not target.is_dir():
                raise OSError(f"folder not found: {target}")
            if target.resolve() == made.parent.resolve():
                raise OSError("that is the local backups folder; choose another one")
            copied = Path(shutil.copy2(made, target / made.name))
            for old in sorted((p for p in target.glob("ap_coder-*.db") if p.stem.count("-") == 2), reverse=True)[keep:]:
                old.unlink(missing_ok=True)
        except OSError as exc:
            self.set_setting("backup_copy_status", f"failed {_now()[:16]}: {exc}")
            return None
        self.set_setting("backup_copy_status", f"ok {_now()[:16]}: {copied.name}")
        return copied

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
            conn.executescript(_SCHEMA)  # an older backup may lack newer tables...
            self._migrate(conn)  # ...and columns
            self._log(conn, "backup_restored", detail={"file": Path(backup).name, "safety_copy": safety.name})
        return safety

    # --- Learning memory ------------------------------------------------------------------------------

    def feedback_rows(self, limit: int = 50_000, vendor_name: str | None = None) -> list[dict[str, Any]]:
        """Recorded reviewer decisions, newest first (only one vendor's when ``vendor_name`` is given)."""
        sql, args = "SELECT * FROM feedback", ()
        if vendor_name is not None:
            sql, args = sql + " WHERE vendor_key = ?", (vendor_key(vendor_name),)
        with self._conn() as conn:
            # Reviewer decisions first, then imported history: a big history import never pushes them out.
            return [
                dict(r) for r in conn.execute(sql + " ORDER BY outcome = 'history', id DESC LIMIT ?", (*args, limit))
            ]

    def import_history(self, rows: list[dict[str, Any]], actor: str | None = None) -> dict[str, int]:
        """Past AP coding from the ERP (``history.rows_from_records``) into the learning memory, as history.
        Rows already imported (same vendor, description, GL, cost center, amount and date) are skipped."""
        from .history import HISTORY

        added = skipped = unknown = 0
        with self._conn() as conn:
            known_gls = {r[0] for r in conn.execute("SELECT code FROM gl_accounts")}
            seen = {
                (r[0], r[1], r[2], r[3] or "", r[4], r[5])
                for r in conn.execute(
                    "SELECT vendor_key, description, final_gl, final_cc, amount, created_at FROM feedback "
                    "WHERE outcome = ?",
                    (HISTORY,),
                )
            }
            batch = []
            for row in rows:
                key = vendor_key(row["vendor_name"])
                gl = _clean_code(row["gl_code"])
                if known_gls and gl not in known_gls:  # a retired or mistyped account would only mislead
                    unknown += 1
                    continue
                when = row.get("date") or ""  # undated rows: the same key every day, so no re-import doubles
                ident = (key, row["description"], gl, row.get("cost_center") or "", row.get("amount"), when)
                if not key or ident in seen:
                    skipped += 1
                    continue
                seen.add(ident)
                batch.append((key, row["vendor_name"], row["description"], row.get("amount"), gl,
                              row.get("cost_center") or "", HISTORY, "ERP history", when))  # fmt: skip
            conn.executemany(
                """INSERT INTO feedback (invoice_id, line_number, vendor_key, vendor_name, description, amount,
                   suggested_gl, final_gl, suggested_cc, final_cc, outcome, reviewer, created_at)
                   VALUES (NULL, NULL, ?, ?, ?, ?, NULL, ?, NULL, ?, ?, ?, ?)""",
                batch,
            )
            added = len(batch)
            self._log(conn, "history_imported", actor=actor, detail={
                "added": added, "skipped": skipped, "unknown_gl": unknown,
            })  # fmt: skip
        return {"added": added, "skipped": skipped, "unknown_gl": unknown}

    def history_count(self) -> int:
        with self._conn() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM feedback WHERE outcome = 'history'").fetchone()[0])

    def forget_history(self, actor: str | None = None) -> int:
        with self._conn() as conn:
            count = conn.execute("DELETE FROM feedback WHERE outcome = 'history'").rowcount
            if count:
                self._log(conn, "lessons_forgotten", actor=actor, detail={"count": count, "history": True})
            return count

    def delete_feedback(self, ids: list[int], actor: str | None = None) -> int:
        with self._conn() as conn:
            cur = conn.executemany("DELETE FROM feedback WHERE id = ?", [(i,) for i in ids])
            if ids:
                self._log(conn, "lessons_forgotten", actor=actor, detail={"count": len(ids)})
            return cur.rowcount

    def metrics(self) -> dict[str, Any]:
        with self._conn() as conn:
            # Imported ERP history is memory, not a reviewer decision: it never counts towards accuracy.
            lines = conn.execute(
                "SELECT outcome, COUNT(*) n FROM feedback WHERE outcome != 'history' GROUP BY outcome"
            ).fetchall()
            weekly = conn.execute(
                """SELECT strftime('%Y-W%W', created_at) week,
                          SUM(outcome = 'accepted') accepted, SUM(outcome = 'corrected') corrected
                   FROM feedback WHERE outcome != 'history' GROUP BY week ORDER BY week"""
            ).fetchall()
            by_vendor = conn.execute(
                """SELECT vendor_name, COUNT(*) lines, SUM(outcome = 'accepted') accepted,
                          SUM(outcome = 'corrected') corrected, MAX(created_at) last_seen
                   FROM feedback WHERE outcome != 'history' GROUP BY vendor_key ORDER BY lines DESC"""
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


def load_sample_vendor_master(store: Store, data_dir: Path = SAMPLE_DATA_DIR, only_new: bool = False) -> list[str]:
    """Load the bundled sample vendor master (the demo vendors but one). Returns the vendor keys (with
    ``only_new``: only those added, existing vendor records being left as they are)."""
    import csv

    from .vendors import master_columns, master_rows

    with (data_dir / "vendor_master.csv").open(encoding="utf-8-sig", newline="") as fh:
        records = list(csv.DictReader(fh))
    rows = master_rows(records, master_columns(list(records[0]) if records else []))
    result = store.import_vendor_master(rows, only_new=only_new)
    return sorted(result["inserted"]) if only_new else sorted({vendor_key(r["vendor_name"]) for r in rows})


def load_sample_purchase_orders(store: Store, data_dir: Path = SAMPLE_DATA_DIR) -> list[str]:
    """Load the bundled sample purchase orders (they match the sample invoices). Returns the PO keys."""
    import csv

    from .po import map_columns, rows_from_records

    with (data_dir / "purchase_orders.csv").open(encoding="utf-8-sig", newline="") as fh:
        records = list(csv.DictReader(fh))
    rows, _ = rows_from_records(records, map_columns(list(records[0]) if records else []))
    store.import_purchase_orders(rows)
    return sorted({po_key(r["po_number"]) for r in rows})
