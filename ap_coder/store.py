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

from .memory import ACCEPTED, CORRECTED, vendor_key
from .reference_data import ReferenceData, ReferenceTable, parse_policy_notes
from .tax import DEFAULT_TREATMENTS, TAX_TYPES, TREATMENTS, TaxRateTable, TaxSetup, TaxTreatment

DEFAULT_DB_PATH = Path("private") / "ap_coder.db"
ACCOUNT_TABLES = {"gl_accounts": "gl_code", "cost_centers": "cost_center"}

REVIEW, APPROVED, REJECTED, FAILED = "review", "approved", "rejected", "failed"

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
    created_at TEXT NOT NULL, reviewed_at TEXT, reviewer TEXT);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER, line_number INTEGER,
    vendor_key TEXT NOT NULL, vendor_name TEXT NOT NULL, description TEXT NOT NULL, amount REAL,
    suggested_gl TEXT, final_gl TEXT NOT NULL, suggested_cc TEXT, final_cc TEXT,
    outcome TEXT NOT NULL, reviewer TEXT, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS feedback_vendor ON feedback (vendor_key);
CREATE INDEX IF NOT EXISTS invoices_status ON invoices (status);
"""

# Fields compared when deciding whether a reviewer edited an invoice header.
_HEADER_FIELDS = (
    "vendor_name", "invoice_number", "invoice_date", "currency", "supplier_province", "ship_to_province",
    "gst_hst_registration_number", "qst_registration_number", "subtotal", "tax_total", "grand_total",
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


def _norm_number(value: str) -> str:
    return re.sub(r"[^0-9a-z]", "", (value or "").lower())


class Store:
    def __init__(self, path: str | Path = DEFAULT_DB_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # --- GL accounts / cost centers ----------------------------------------------------------

    def import_accounts(
        self,
        table: str,
        rows: list[dict[str, Any]],
        code_column: str,
        description_column: str | None,
        category_column: str | None = None,
        replace_all: bool = False,
    ) -> dict[str, int]:
        """Import rows using the columns the user picked. Existing codes are updated."""
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
                    conn.execute(
                        f"UPDATE {table} SET description = ?, category = ? WHERE code = ?",
                        (description, category, code),
                    )
                    updated += 1
                else:
                    conn.execute(
                        f"INSERT INTO {table} (code, description, category, created_at) VALUES (?, ?, ?, ?)",
                        (code, description, category, _now()),
                    )
                    added += 1
        return {"added": added, "updated": updated, "skipped": skipped}

    def list_accounts(self, table: str) -> list[dict[str, str]]:
        if table not in ACCOUNT_TABLES:
            raise ValueError(f"unknown table {table!r}")
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(f"SELECT code, description, category FROM {table} ORDER BY code")]

    def save_accounts(self, table: str, rows: list[dict[str, Any]]) -> None:
        """Replace the table with ``rows`` (used by the dashboard's editable grid)."""
        self.import_accounts(table, rows, "code", "description", "category", replace_all=True)

    def delete_accounts(self, table: str, codes: list[str]) -> int:
        if table not in ACCOUNT_TABLES:
            raise ValueError(f"unknown table {table!r}")
        with self._conn() as conn:
            cur = conn.executemany(f"DELETE FROM {table} WHERE code = ?", [(c,) for c in codes])
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

    def set_tax_treatment(self, tax_type: str, treatment: str, gl_code: str = "") -> None:
        if tax_type not in TAX_TYPES or treatment not in TREATMENTS:
            raise ValueError("invalid tax type or treatment")
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO tax_treatments (tax_type, treatment, gl_code) VALUES (?, ?, ?) "
                "ON CONFLICT(tax_type) DO UPDATE SET treatment = excluded.treatment, gl_code = excluded.gl_code",
                (tax_type, treatment, gl_code.strip()),
            )

    def get_setting(self, key: str, default: str = "") -> str:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._conn() as conn:
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
            cur = conn.execute(
                """INSERT INTO invoices (source_path, file_name, file_sha256, status, vendor_name, vendor_key,
                   invoice_number, invoice_date, currency, grand_total, model_confidence, adjusted_confidence,
                   requires_review, ai_output, validation, extraction_md, meta, error, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(source_path),
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
                ),
            )
            return int(cur.lastrowid)

    def find_by_hash(self, path: str | Path) -> dict[str, Any] | None:
        sha = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, status FROM invoices WHERE file_sha256 = ? AND status != ? ORDER BY id DESC",
                (sha, FAILED),
            ).fetchone()
        return dict(row) if row else None

    def find_duplicates(self, vendor_name: str, invoice_number: str, exclude_id: int | None = None) -> list[int]:
        """Other invoices with the same vendor and invoice number (possible duplicate payment)."""
        key, number = vendor_key(vendor_name), _norm_number(invoice_number)
        if not key or not number:
            return []
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, invoice_number FROM invoices WHERE vendor_key = ? AND status != ? AND status != ?",
                (key, FAILED, REJECTED),
            ).fetchall()
        return [r["id"] for r in rows if _norm_number(r["invoice_number"]) == number and r["id"] != exclude_id]

    def list_invoices(self, status: str | None = None) -> list[dict[str, Any]]:
        sql = (
            "SELECT id, file_name, status, vendor_name, invoice_number, invoice_date, currency, grand_total, "
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

    def delete_invoice(self, invoice_id: int) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM invoices WHERE id = ?", (invoice_id,))

    def approve_invoice(self, invoice_id: int, final_output: dict[str, Any], reviewer: str) -> dict[str, int]:
        """Store the reviewer's final version and record one feedback row per line."""
        inv = self.get_invoice(invoice_id)
        if inv is None:
            raise KeyError(invoice_id)
        if inv["status"] == APPROVED:
            raise ValueError(f"invoice {invoice_id} is already approved")
        ai = inv["ai_output"] or {"line_items": []}
        ai_lines = {li["line_number"]: li for li in ai.get("line_items", [])}
        vendor_name = final_output.get("vendor_name", "")
        key = vendor_key(vendor_name)
        now = _now()
        counts = {ACCEPTED: 0, CORRECTED: 0}
        feedback_rows = []
        for li in final_output.get("line_items", []):
            suggestion = ai_lines.get(li["line_number"])
            s_gl = suggestion.get("predicted_gl_code") if suggestion else None
            s_cc = suggestion.get("predicted_cost_center", "") if suggestion else None
            f_gl, f_cc = li["predicted_gl_code"], li.get("predicted_cost_center", "")
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
            conn.executemany(
                """INSERT INTO feedback (invoice_id, line_number, vendor_key, vendor_name, description, amount,
                   suggested_gl, final_gl, suggested_cc, final_cc, outcome, reviewer, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                feedback_rows,
            )
            conn.execute(
                """UPDATE invoices SET status = ?, final_output = ?, edits = ?, reviewer = ?, reviewed_at = ?,
                   vendor_name = ?, vendor_key = ?, invoice_number = ?, invoice_date = ?, grand_total = ?
                   WHERE id = ?""",
                (
                    APPROVED,
                    json.dumps(final_output),
                    json.dumps(edits),
                    reviewer,
                    now,
                    vendor_name,
                    key,
                    final_output.get("invoice_number"),
                    final_output.get("invoice_date"),
                    final_output.get("grand_total"),
                    invoice_id,
                ),  # fmt: skip
            )
        return counts

    # --- Learning memory ------------------------------------------------------------------------------

    def feedback_rows(self, limit: int = 5000) -> list[dict[str, Any]]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM feedback ORDER BY id DESC LIMIT ?", (limit,))]

    def delete_feedback(self, ids: list[int]) -> int:
        with self._conn() as conn:
            cur = conn.executemany("DELETE FROM feedback WHERE id = ?", [(i,) for i in ids])
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
