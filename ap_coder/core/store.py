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

import contextvars
import datetime as dt
import hashlib
import json
import math
import re
import shutil
import sqlite3
import threading
import time
import unicodedata
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..coding.po import OPEN, po_key
from ..coding.rules import Rule
from ..coding.tax import DEFAULT_TREATMENTS, TAX_TYPES, TREATMENTS, TaxRateTable, TaxSetup, TaxTreatment
from ..coding.terms import DEFAULT_TERMS_DAYS, payment
from ..coding.vendors import norm_invoice_number
from .audit import diff_coding
from .memory import ACCEPTED, CODED, CORRECTED, VENDOR_KEY_RULES, pair_lines, refresh_vendor_key, vendor_key
from .paths import PROJECT_DIR, default_db_path, find_moved_file, private_dir  # noqa: F401  (re-exported)
from .reference_data import UNASSIGNED, ReferenceData, ReferenceTable, parse_policy_notes

# 10: erp_invoices, 11: coding_rules (by _SCHEMA), 12: currency, 13: credit notes not due,
# 14: invoice_capture, supplier_profiles, supplier_outcomes (by _SCHEMA), 15: reader_outcomes, page_reads (by _SCHEMA)
SCHEMA_VERSION = 15
ACCOUNT_TABLES = {"gl_accounts": "gl_code", "cost_centers": "cost_center"}

REVIEW, APPROVED, REJECTED, FAILED = "review", "approved", "rejected", "failed"
PENDING = "pending_approval"  # approved once, over the approval limit: waiting for a second approver
PARKED = "parked"  # out of the queue while waiting for information (a buyer, a credit note...)
ACTIVE_STATUSES = (REVIEW, PARKED, PENDING, APPROVED)  # invoices that count (for POs, recurring vendors...)
_ACTIVE_IN = "(" + ", ".join("?" * len(ACTIVE_STATUSES)) + ")"


def _in_list(column: str) -> str:
    """``column IN`` a list bound as ONE parameter (a JSON array): a list of any length, where one ``?`` per item
    fails past the SQLite build's limit (999 on older builds, 32,766 on newer)."""
    return f"{column} IN (SELECT value FROM json_each(?))"


# An invoice file looked for and not found (``Store.invoice_file``) is looked for again after this long, not on every
# page picture the review screen asks for.
_LOOK_AGAIN_SECONDS = 30.0
_NOT_FOUND: dict[tuple[str, int, str], float] = {}

# Databases being restored in this process (``Store.restore_from``): the background workers (page reader, Azure
# check) start nothing on them meanwhile (``Store.restoring``).
_RESTORING: set[str] = set()
_RESTORING_LOCK = threading.Lock()

# The learning memory as last read, per database: (highest lesson id and count, rows) (``Store.feedback_rows``).
_FEEDBACK: dict[tuple[str, int, str | None], tuple[tuple[Any, ...], list[dict[str, Any]]]] = {}
_FEEDBACK_LOCK = threading.Lock()
FEEDBACK_READ = 50_000  # lessons read for the whole memory, at most (newest first); one vendor's are added past it

# Touchless processing (Settings → Automation): one switch for the company, "on" or "off" (off when not set), and
# the largest invoice total approved without a person, in CAD.
TOUCHLESS_SETTING, TOUCHLESS_LIMIT_SETTING = "touchless_processing", "touchless_limit"
DEFAULT_TOUCHLESS_LIMIT = 5000.0

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
    copies INTEGER NOT NULL DEFAULT 1, PRIMARY KEY (vendor_key, number_key, total));
CREATE TABLE IF NOT EXISTS coding_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT, vendor TEXT NOT NULL DEFAULT '', contains TEXT NOT NULL DEFAULT '',
    gl_code TEXT NOT NULL, cost_center TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, created_by TEXT);
CREATE TABLE IF NOT EXISTS invoice_capture (
    invoice_id INTEGER PRIMARY KEY REFERENCES invoices (id) ON DELETE CASCADE, capture_json TEXT NOT NULL,
    layout_source TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS supplier_profiles (
    key TEXT PRIMARY KEY, display_name TEXT NOT NULL DEFAULT '', vendor_id TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'supervised', autonomous_since TEXT, audit_rate REAL, template_json TEXT,
    updated_at TEXT NOT NULL, updated_by TEXT, suspended_at TEXT);
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
    updated_at TEXT NOT NULL, reader TEXT NOT NULL DEFAULT '', tries INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS page_reads_status ON page_reads (status, created_at);
CREATE TABLE IF NOT EXISTS azure_checks (
    invoice_id INTEGER PRIMARY KEY, reason TEXT NOT NULL, status TEXT NOT NULL, pages INTEGER NOT NULL DEFAULT 0,
    compared INTEGER NOT NULL DEFAULT 0, agreed INTEGER NOT NULL DEFAULT 0, result_json TEXT,
    error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, compared_at TEXT);
CREATE INDEX IF NOT EXISTS azure_checks_status ON azure_checks (status, created_at);
CREATE TABLE IF NOT EXISTS verifications (
    invoice_id INTEGER PRIMARY KEY, reason TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
    rounds INTEGER NOT NULL DEFAULT 0, seconds REAL NOT NULL DEFAULT 0, result_json TEXT,
    error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    tries INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS verifications_status ON verifications (status, created_at);
CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT, situation TEXT NOT NULL UNIQUE, vendor_key TEXT NOT NULL,
    vendor_name TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL DEFAULT 'invoice', field TEXT NOT NULL,
    pattern TEXT NOT NULL DEFAULT '', value TEXT NOT NULL DEFAULT '', proposed TEXT NOT NULL DEFAULT '',
    seen INTEGER NOT NULL DEFAULT 0, corrected INTEGER NOT NULL DEFAULT 0, since_why INTEGER NOT NULL DEFAULT 0,
    confirmed INTEGER NOT NULL DEFAULT 0, explanation TEXT NOT NULL DEFAULT '', explained_by TEXT NOT NULL DEFAULT '',
    explained_at TEXT, retired INTEGER NOT NULL DEFAULT 0, last_invoice INTEGER, created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS lessons_vendor ON lessons (vendor_key, kind);
CREATE TABLE IF NOT EXISTS lesson_approvals (
    invoice_id INTEGER NOT NULL, situation TEXT NOT NULL, corrected INTEGER NOT NULL DEFAULT 0,
    confirmed INTEGER NOT NULL DEFAULT 0, value TEXT NOT NULL DEFAULT '', proposed TEXT NOT NULL DEFAULT '',
    at TEXT NOT NULL, PRIMARY KEY (invoice_id, situation));
CREATE INDEX IF NOT EXISTS lesson_approvals_situation ON lesson_approvals (situation);
"""

# The page reader's queue (``page_reads.status``). A read still "reading" after STALE_READING_HOURS was
# interrupted (the computer slept, the app was closed): it waits in line again. ``reader`` names the process
# reading it (``page_worker.READER_ID``), so a read whose process is gone is put back in line at once
# (``release_page_reads``); ``tries``: reads cut off by the model server since it was put in line.
PAGE_WAITING, PAGE_READING, PAGE_DONE, PAGE_FAILED, PAGE_SKIPPED = "waiting", "reading", "done", "failed", "skipped"
STALE_READING_HOURS = 2
_IN_LINE = "(status = 'waiting' OR (status = 'reading' AND updated_at < ?))"  # ? = _stale_before()

# The Azure check's queue (``azure_checks.status``, ``ap_coder/ai/azure_check.py``): off unless AP turns it on in
# Settings → Reading. A check still "checking" after STALE_READING_HOURS was interrupted: it waits in line again.
AZURE_WAITING, AZURE_CHECKING, AZURE_DONE = "waiting", "checking", "done"
AZURE_FAILED, AZURE_SKIPPED = "failed", "skipped"
_AZURE_IN_LINE = (  # ? = _stale_before()
    "(azure_checks.status = 'waiting' OR (azure_checks.status = 'checking' AND azure_checks.updated_at < ?))"
)

# The AI reviewer's queue (``verifications.status``, ``ap_coder/ai/verifier.py``). A review still "verifying" after
# STALE_READING_HOURS was interrupted: it waits in line again.
VERIFY_WAITING, VERIFY_RUNNING, VERIFY_DONE, VERIFY_FAILED, VERIFY_SKIPPED = (
    "waiting", "verifying", "done", "failed", "skipped")  # fmt: skip
_VERIFY_IN_LINE = (  # ? = _stale_before()
    "(verifications.status = 'waiting' OR (verifications.status = 'verifying' AND verifications.updated_at < ?))"
)

# Fields compared when deciding whether a reviewer edited an invoice header.
_HEADER_FIELDS = (
    "vendor_name", "invoice_number", "invoice_date", "po_number", "payment_terms", "due_date", "currency",
    "supplier_province",
    "ship_to_province", "gst_hst_registration_number", "qst_registration_number", "pst_registration_number",
    "original_invoice_number",
    "remit_bank_account",
    "subtotal", "tax_total", "grand_total",
)  # fmt: skip


# Where the request being answered came from, when it is a colleague on the office network (set by
# ap_coder/web/identity.py): every event then records it as detail["address"]. None on this computer.
EVENT_ORIGIN: contextvars.ContextVar[str | None] = contextvars.ContextVar("ap_coder_event_origin", default=None)


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _stale_before() -> str:
    """A page read started before this time is no longer under way (see STALE_READING_HOURS)."""
    return (dt.datetime.now() - dt.timedelta(hours=STALE_READING_HOURS)).isoformat(timespec="seconds")


_FX_RATE = re.compile(r"([A-Za-z]{3})\s*[=:]?\s*([0-9]+(?:[.,][0-9]+)?)")


def parse_fx_rates(text: str) -> dict[str, float]:
    """ "USD=1.37, EUR 1,50; GBP: 1.85" -> {"USD": 1.37, "EUR": 1.5, "GBP": 1.85} (CAD is always 1)."""
    rates = {"CAD": 1.0}
    for code, value in _FX_RATE.findall(text or ""):
        rate = float(value.replace(",", "."))
        if rate > 0 and code.upper() != "CAD":  # a CAD rate typed in would scale every limit check
            rates[code.upper()] = rate
    return rates


def unread_fx_text(text: str) -> str:
    """What of ``text`` is not a rate ``parse_fx_rates`` reads ("" when all of it is): "1.37 USD" -> "1.37 USD"."""
    return re.sub(r"[\s,;]+", " ", _FX_RATE.sub(" ", text or "")).strip()


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


def nothing_proposed(s_gl: str | None, s_cc: str | None) -> bool:
    """Whether the AI left a line it read with no GL account and no cost center (``s_gl`` None: a line the reviewer
    added, which is not this). The reviewer who codes it codes it, as a guess replaced or kept: no correction, out of
    the coding accuracy (D-39, B-650)."""
    return s_gl is not None and s_gl in ("", UNASSIGNED) and (s_cc or "") in ("", UNASSIGNED)


def coded_blanks(ai: dict[str, Any] | None, final: dict[str, Any] | None) -> int:
    """How many lines the AI left blank (``nothing_proposed``) that the reviewer gave a GL account: the reviewer's own
    coding, an edit of its own ("line_coded"), not a correction (B-670)."""
    return sum(
        1
        for before, li in pair_lines((ai or {}).get("line_items") or [], (final or {}).get("line_items") or [])
        if before is not None and li.get("predicted_gl_code") not in (None, "", UNASSIGNED)
        and nothing_proposed(before.get("predicted_gl_code"), before.get("predicted_cost_center"))
    )  # fmt: skip


def suggestion_kept(s_gl: str | None, s_cc: str | None, f_gl: str, f_cc: str | None) -> bool:
    """Whether the reviewer kept the GL account and cost center proposed for a line (``s_gl`` None: no line was
    proposed). A GL or cost center left blank ("UNASSIGNED", or a cost center still to pick) was not proposed:
    filling it in confirms the rest, it corrects nothing. A line with nothing proposed at all (or added by the
    reviewer) is the reviewer's own coding. The approval's lessons and the review screen's count both use this."""
    gl_blank, cc_blank = s_gl in (None, "", UNASSIGNED), (s_cc or "") in ("", UNASSIGNED)
    if s_gl is None or (gl_blank and cc_blank):
        return False
    return (gl_blank or s_gl == f_gl) and (cc_blank or s_cc == (f_cc or ""))


def line_guessed(suggestion: dict[str, Any] | None, rule: dict[str, Any] | None = None) -> bool:
    """Whether the AI only guessed this line's account (``offline_coder.guessed``: the chat model's own pick, an
    older version's weak match), also under a fixed coding rule that replaced the guess (``rule``: its entry in the
    invoice's ``rules_applied``, which says ``from_guess``)."""
    from ..ai.offline_coder import guessed

    if rule is not None and "from_guess" in rule:
        return bool(rule["from_guess"])
    return bool(suggestion) and guessed((suggestion or {}).get("reasoning_justification"))


def proposed_coding(suggestion: dict[str, Any] | None) -> tuple[str | None, str | None]:
    """(GL account, cost center) the AI really proposed for a line (``suggestion``: the line as read, None: no line).
    An account no approval backs (the chat model's own pick, an older version's weak match: ``offline_coder.guessed``)
    was a guess for a person to check, not a proposal: it counts as left blank, so a reviewer who keeps it coded the
    line, and coding accuracy and the lessons never count it as "coded as suggested" (B-463)."""
    if not suggestion:
        return None, None
    from ..ai.offline_coder import guessed

    if guessed(suggestion.get("reasoning_justification")):
        return UNASSIGNED, ""
    return suggestion.get("predicted_gl_code"), suggestion.get("predicted_cost_center", "")


def fold_text(value: Any) -> Any:
    """The SQL function ``ap_fold(x)``: text without accents and its case folded ("Énergir" -> "energir"), so a
    filter compares names as people read them. SQLite's own lower() and LIKE fold only A to Z (B-04). Plain ASCII,
    most of what it sees, takes the quick way."""
    if value is None:
        return None
    text = str(value)
    if text.isascii():
        return text.lower()
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


class ApprovedAgain(ValueError):
    """A second approval given on a page drawn before the invoice was sent back and approved again (B-654)."""


class DatabaseTooNew(RuntimeError):
    """The database was made by a newer AP Coder: this (older) one never opens it, so it is never downgraded."""


def database_schema(path: Path) -> int | None:
    """The ``schema_version`` a database file records (1 for one older than the setting; None for a missing or
    empty file, or one with no tables yet: a new database). Only reads."""
    try:
        if not path.is_file() or path.stat().st_size == 0:
            return None
        conn = sqlite3.connect(path, timeout=30)
    except (OSError, sqlite3.Error):
        return None
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if not tables:
            return None
        row = (
            conn.execute("SELECT value FROM settings WHERE key = 'schema_version'").fetchone()
            if "settings" in tables
            else None
        )
        return int(row[0]) if row and str(row[0]).isdigit() else 1
    except sqlite3.Error:
        return None
    finally:
        conn.close()


class _Held:
    """A connection ``Store.session`` holds open, and how deep in ``_conn`` blocks the thread is on it."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.depth = 0


_sessions = threading.local()


def _held_connections() -> dict[str, _Held]:
    """This thread's held connections, by database path."""
    held = getattr(_sessions, "held", None)
    if held is None:
        held = _sessions.held = {}
    return held


class Store:
    def __init__(self, path: str | Path | None = None) -> None:
        path = default_db_path() if path is None else path
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._before_migrating()
        with self._conn() as conn:
            conn.executescript(_SCHEMA)
            self._migrate(conn)

    def _before_migrating(self) -> None:
        """An update's safety net, before the database is touched: a database made by a newer AP Coder is refused
        (never opened by this older one, so never downgraded), and one made by an older AP Coder is backed up first
        (``backups/ap_coder-<date>-before-update-from-schema-<n>.db``), so the update can always be undone."""
        found = database_schema(self.path)
        if found is None or found == SCHEMA_VERSION:
            return
        if found > SCHEMA_VERSION:
            from .. import __version__

            raise DatabaseTooNew(
                f"The database {self.path} was made by a newer AP Coder (database version {found}; this AP Coder "
                f"{__version__} knows up to version {SCHEMA_VERSION}), so this older version does not open it: "
                "nothing was changed. Start the newer AP Coder instead."
            )
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.backup_to(self.backup_dir() / f"ap_coder-{stamp}-before-update-from-schema-{found}.db")

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
        # The page reader's queue remembers who is reading an invoice and how many reads were cut off (added within
        # schema 15: looked for whatever the version).
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(page_reads)")}
        for column, kind in (("reader", "TEXT NOT NULL DEFAULT ''"), ("tries", "INTEGER NOT NULL DEFAULT 0")):
            if columns and column not in columns:
                conn.execute(f"ALTER TABLE page_reads ADD COLUMN {column} {kind}")
        # The AI reviewer's queue counts reviews the model server cut off before any answer (looked for likewise).
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(verifications)")}
        if columns and "tries" not in columns:
            conn.execute("ALTER TABLE verifications ADD COLUMN tries INTEGER NOT NULL DEFAULT 0")
        # A suspended supplier remembers when, so it needs a fresh clean streak since then (looked for whatever the
        # version, like the page reader's columns above).
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(supplier_profiles)")}
        if columns and "suspended_at" not in columns:
            conn.execute("ALTER TABLE supplier_profiles ADD COLUMN suspended_at TEXT")
            # A vendor suspended by an older version: when, from the Activity log (else its last change), so the
            # clean streak it had before still doesn't count.
            conn.execute(
                "UPDATE supplier_profiles SET suspended_at = COALESCE((SELECT MAX(e.created_at) FROM events e "
                "WHERE e.action = 'autonomy_suspended' AND CASE WHEN json_valid(e.detail) THEN "
                "json_extract(e.detail, '$.key') END = supplier_profiles.key), updated_at) WHERE state = 'suspended'"
            )
        # The vendor list keeps each vendor's QST and PST numbers and province, to check the numbers an invoice
        # prints against the vendor chosen (looked for whatever the version).
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(vendors)")}
        for column in ("qst_number", "pst_number", "province"):
            if columns and column not in columns:
                conn.execute(f"ALTER TABLE vendors ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        # An invoice made from a picture remembers the picture's SHA-256 (intake.py), so the same photo sent again is
        # found (looked for whatever the version).
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(invoices)")}
        if "original_sha256" not in columns:
            conn.execute("ALTER TABLE invoices ADD COLUMN original_sha256 TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS invoices_original_sha ON invoices (original_sha256)")
        # The ERP register counts a bill entered more than once in the ERP, for the duplicate audit (looked for
        # whatever the version).
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(erp_invoices)")}
        if columns and "copies" not in columns:
            conn.execute("ALTER TABLE erp_invoices ADD COLUMN copies INTEGER NOT NULL DEFAULT 1")
        # Vendor keys stored under older vendor-matching rules (e.g. before "Limitée" and "s.e.c." were ignored) are
        # recomputed, so the vendor's invoices, lessons, master record and POs still match (looked for whatever the
        # version: the rules are recorded on their own).
        row = conn.execute("SELECT value FROM settings WHERE key = 'vendor_key_rules'").fetchone()
        if (row["value"] if row else "") != VENDOR_KEY_RULES:
            self._recompute_vendor_keys(conn)
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('vendor_key_rules', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (VENDOR_KEY_RULES,),
            )
        # Older versions recorded AP Coder's own approvals (nobody checked them) as lessons, counted in the
        # accuracy: withdrawn whatever the version, as nothing is recorded for them any more.
        from ..capture.workflow import AUTONOMOUS_REVIEWER

        conn.execute("DELETE FROM feedback WHERE reviewer = ?", (AUTONOMOUS_REVIEWER,))
        # Older versions recorded a GL account filled in where the AI proposed none as a correction: it is the
        # reviewer's coding (B-650), recounted whatever the version (the learning memory reads both alike).
        conn.execute(
            "UPDATE feedback SET outcome = ? WHERE outcome = ? AND suggested_gl IN ('', ?) "
            "AND COALESCE(suggested_cc, '') IN ('', ?)",
            (CODED, CORRECTED, UNASSIGNED, UNASSIGNED),
        )
        if version < SCHEMA_VERSION:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(SCHEMA_VERSION),),
            )

    @staticmethod
    def _recompute_vendor_keys(conn: sqlite3.Connection) -> None:
        """Every stored vendor key, recomputed with today's ``vendor_key``: from the vendor name kept beside it, else
        (the vendor list, whose display name may have been edited, and the supplier profiles) from the old key itself.
        Where the new key is already taken (two rows that are now the same vendor) the row already there is kept: a
        duplicate ERP bill is dropped, two vendor list rows are merged into one (on hold if either was), two lessons
        into one (counts added up), two supplier profiles into one (the stricter state), anything else is left as
        it was."""

        def new_key(name: Any, old: Any) -> str:
            return vendor_key(str(name or "")) or refresh_vendor_key(str(old or ""))

        for table, name, ident in (
            ("invoices", "vendor_name", "id"), ("feedback", "vendor_name", "id"),
            ("purchase_orders", "vendor_name", "po_key"), ("vendors", "''", "vendor_key"),
            ("erp_invoices", "vendor_name", "rowid"),
        ):  # fmt: skip
            for r in conn.execute(f"SELECT {ident} AS ident, {name} AS name, vendor_key FROM {table}").fetchall():
                key = new_key(r["name"], r["vendor_key"])
                if key == (r["vendor_key"] or ""):
                    continue
                done = conn.execute(f"UPDATE OR IGNORE {table} SET vendor_key = ? WHERE {ident} = ?", (key, r["ident"]))
                if not done.rowcount and table == "erp_invoices":  # the same ERP bill is already there under the key
                    conn.execute("DELETE FROM erp_invoices WHERE rowid = ?", (r["ident"],))
                elif not done.rowcount and table == "vendors":  # the same vendor twice: one row, on hold if either is
                    Store._merge_vendor_rows(conn, r["ident"], key)
        for r in conn.execute("SELECT id, situation, vendor_name, vendor_key FROM lessons").fetchall():
            key = new_key(r["vendor_name"], r["vendor_key"])
            _old, sep, rest = str(r["situation"] or "").partition("|")
            situation = f"{key}{sep}{rest}" if sep else r["situation"]
            if (key, situation) != (r["vendor_key"], r["situation"]):
                done = conn.execute("UPDATE OR IGNORE lessons SET vendor_key = ?, situation = ? WHERE id = ?",
                                    (key, situation, r["id"]))  # fmt: skip
                if not done.rowcount:  # the same situation of the same vendor twice: one lesson, counts added up
                    Store._merge_lesson_rows(conn, r["id"], situation)
                if situation != r["situation"]:  # each approval's part follows its lesson (B-158)
                    conn.execute("UPDATE OR IGNORE lesson_approvals SET situation = ? WHERE situation = ?",
                                 (situation, r["situation"]))  # fmt: skip
                    conn.execute("DELETE FROM lesson_approvals WHERE situation = ?", (r["situation"],))
        for table, column in (("supplier_profiles", "key"), ("supplier_outcomes", "supplier_key")):
            rows = conn.execute(f"SELECT rowid AS rid, {column} AS k FROM {table} WHERE {column} LIKE 'name:%'")
            for r in rows.fetchall():
                key = "name:" + refresh_vendor_key(r["k"][5:])
                if key != r["k"]:
                    done = conn.execute(f"UPDATE OR IGNORE {table} SET {column} = ? WHERE rowid = ?", (key, r["rid"]))
                    if not done.rowcount and table == "supplier_profiles":  # one profile, the strictest state kept
                        Store._merge_supplier_profiles(conn, r["k"], key)

    @staticmethod
    def _merge_lesson_rows(conn: sqlite3.Connection, gone_id: int, situation: str) -> None:
        """The lesson ``gone_id`` folded into the one already at ``situation`` (the same vendor under today's rules),
        then removed: the counts added up, the newer value and the latest reason kept, shown unless both are retired."""
        kept = conn.execute("SELECT * FROM lessons WHERE situation = ?", (situation,)).fetchone()
        gone = conn.execute("SELECT * FROM lessons WHERE id = ?", (gone_id,)).fetchone()
        if kept is None or gone is None:
            return
        newer = gone if gone["updated_at"] > kept["updated_at"] else kept
        reasons = [r for r in (kept, gone) if r["explanation"]]
        reason = max(reasons, key=lambda r: r["explained_at"] or "") if reasons else kept
        conn.execute(
            "UPDATE lessons SET seen = ?, corrected = ?, since_why = ?, confirmed = ?, value = ?, proposed = ?, "
            "explanation = ?, explained_by = ?, explained_at = ?, retired = ?, last_invoice = ?, updated_at = ? "
            "WHERE id = ?",
            (
                kept["seen"] + gone["seen"], kept["corrected"] + gone["corrected"],
                kept["since_why"] + gone["since_why"], kept["confirmed"] + gone["confirmed"], newer["value"],
                newer["proposed"], reason["explanation"], reason["explained_by"], reason["explained_at"],
                min(kept["retired"], gone["retired"]), newer["last_invoice"], newer["updated_at"], kept["id"],
            ),
        )  # fmt: skip
        conn.execute("DELETE FROM lessons WHERE id = ?", (gone_id,))

    @staticmethod
    def _merge_supplier_profiles(conn: sqlite3.Connection, old: str, key: str) -> None:
        """The supplier profile under ``old`` folded into the one under ``key`` (the same supplier under today's
        rules), then removed: the stricter state wins (held, then suspended, supervised, touchless), with its dates
        and audit rate, so a held supplier never goes touchless by a key change; a field blank in one is filled from
        the other."""
        from ..capture.supplier import AUTONOMOUS, HELD, SUPERVISED, SUSPENDED

        strict = {HELD: 3, SUSPENDED: 2, SUPERVISED: 1, AUTONOMOUS: 0}
        kept = conn.execute("SELECT * FROM supplier_profiles WHERE key = ?", (key,)).fetchone()
        gone = conn.execute("SELECT * FROM supplier_profiles WHERE key = ?", (old,)).fetchone()
        if kept is None or gone is None:
            return
        state = gone if strict.get(gone["state"], 1) > strict.get(kept["state"], 1) else kept
        conn.execute(
            "UPDATE supplier_profiles SET state = ?, autonomous_since = ?, suspended_at = ?, audit_rate = ?, "
            "display_name = ?, vendor_id = ?, template_json = ?, updated_at = ? WHERE key = ?",
            (
                state["state"], state["autonomous_since"], state["suspended_at"], state["audit_rate"],
                kept["display_name"] or gone["display_name"], kept["vendor_id"] or gone["vendor_id"],
                kept["template_json"] or gone["template_json"], _now(), key,
            ),
        )  # fmt: skip
        conn.execute("DELETE FROM supplier_profiles WHERE key = ?", (old,))

    @staticmethod
    def _merge_vendor_rows(conn: sqlite3.Connection, old: str, key: str) -> None:
        """The vendor list row under ``old`` folded into the one under ``key`` (the same vendor under today's rules),
        then removed: on hold if either is (a row left under a key no invoice gives any more would quietly lift the
        hold), both rows' notes kept, a field blank in one filled from the other."""
        kept = conn.execute("SELECT * FROM vendors WHERE vendor_key = ?", (key,)).fetchone()
        gone = conn.execute("SELECT * FROM vendors WHERE vendor_key = ?", (old,)).fetchone()
        if kept is None or gone is None:
            return
        values: dict[str, Any] = {}
        for column in kept.keys():
            if column in ("vendor_key", "updated_at"):
                continue
            a, b = kept[column], gone[column]
            if column == "status":
                values[column] = "on_hold" if "on_hold" in (a, b) else a
            elif column == "notes":
                values[column] = "; ".join(dict.fromkeys(n for n in (a, b) if n))
            elif column == "in_master":
                values[column] = max(a or 0, b or 0)
            else:
                values[column] = a if a not in (None, "") else b
        assignments = ", ".join(f"{column} = ?" for column in values)
        conn.execute(f"UPDATE vendors SET {assignments}, updated_at = ? WHERE vendor_key = ?",
                     (*values.values(), _now(), key))  # fmt: skip
        conn.execute("DELETE FROM vendors WHERE vendor_key = ?", (old,))

    @contextmanager
    def session(self) -> Iterator[None]:
        """Every call on this database inside, in this thread, shares one connection: each still commits on its own as
        before (one inside another joins its transaction), and the connection is closed at the end. Opening and
        closing a connection per call costs more than the work itself on a laptop (closing the last one writes the
        write-ahead log into the database, on disk, every time): an invoice read and checked is about fifty calls."""
        held = _held_connections()
        key = str(self.path)
        if key in held:
            yield
            return
        conn = self._open()
        held[key] = _Held(conn)
        try:
            yield
        finally:
            if held.get(key) is not None and held[key].conn is conn:
                del held[key]
            conn.close()

    def _open(self) -> sqlite3.Connection:
        fresh = not self.path.exists()  # e.g. the file was deleted while the dashboard was running
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.create_function("ap_fold", 1, fold_text, deterministic=True)
        conn.execute("PRAGMA journal_mode=WAL")
        if fresh:
            conn.executescript(_SCHEMA)
            self._migrate(conn)
            conn.commit()  # the upgrade's changes left open would refuse the caller's own BEGIN IMMEDIATE
        return conn

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        held = _held_connections().get(str(self.path))
        if held is not None and not self.path.exists():  # the file was deleted meanwhile: on with a fresh one
            _held_connections().pop(str(self.path), None)
            held.conn.close()
            held = None
        if held is not None:
            outer = held.depth == 0
            held.depth += 1
            try:
                yield held.conn
                if outer:
                    held.conn.commit()
            except BaseException:
                if outer:
                    held.conn.rollback()
                raise
            finally:
                held.depth -= 1
            return
        conn = self._open()
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
        origin = EVENT_ORIGIN.get()
        if origin:
            detail = {**(detail or {}), "address": origin}  # never "from": events use it (B-129)
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

    def latest_events(self, action: str, invoice_ids: list[int]) -> dict[int, dict[str, Any]]:
        """{invoice: its newest ``action`` event} for each of ``invoice_ids`` that has one, in one query (what
        ``events(invoice_id, [action])[0]`` gives for each, without a query per invoice)."""
        ids = list(dict.fromkeys(int(i) for i in invoice_ids))
        if not ids:
            return {}
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM events WHERE action = ? AND {_in_list('invoice_id')} ORDER BY id DESC",
                (action, json.dumps(ids)),
            ).fetchall()
        latest: dict[int, dict[str, Any]] = {}
        for r in rows:
            if r["invoice_id"] not in latest:
                latest[r["invoice_id"]] = {**dict(r), "detail": json.loads(r["detail"]) if r["detail"] else {}}
        return latest

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
        category column keeps the categories already typed in. ``replace_all`` with rows none of which has a code
        (the wrong column picked) is refused, nothing deleted: it would leave no account to code to.
        """
        if table not in ACCOUNT_TABLES:
            raise ValueError(f"unknown table {table!r}")
        if replace_all and rows and not any(_clean_code(row.get(code_column)) for row in rows):
            raise ValueError(
                f"No row has a code in the column {code_column!r}: nothing was replaced. Pick the column that "
                "holds the codes and import again."
            )
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
            tax=TaxSetup(
                rates or TaxRateTable.load(),
                self.tax_treatments(),
                our_province=self.our_province(),
                site_provinces=self.site_provinces(),
            ),
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
        original_sha256: str | None = None,
    ) -> int:
        """``original_sha256``: the SHA-256 of the picture this PDF was made from (``intake.py``), so the same photo
        sent again is found as this invoice (``find_by_hash``)."""
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
            if original_sha256:
                # Earlier failed attempts at the same picture (before or after it was made a PDF) are replaced too.
                conn.execute("DELETE FROM invoices WHERE (original_sha256 = ? OR file_sha256 = ?) AND status = ? "
                             "AND id != ?", (original_sha256, original_sha256, FAILED, invoice_id))  # fmt: skip
                conn.execute("UPDATE invoices SET original_sha256 = ? WHERE id = ?", (original_sha256, invoice_id))
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
        """The latest invoice made from this exact file, or from this exact picture made into a PDF (failed attempts
        only if ``include_failed``)."""
        sha = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        sql = "SELECT id, status FROM invoices WHERE (file_sha256 = ? OR original_sha256 = ?)"
        if not include_failed:
            sql += f" AND status != '{FAILED}'"
        with self._conn() as conn:
            row = conn.execute(sql + " ORDER BY id DESC", (sha, sha)).fetchone()
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
        """Invoices already in the ERP (``registers.rows_from_records`` output). Returns how many are stored. A bill
        listed more than once in the file with another date (the same vendor, number and amount: entered twice in the
        ERP) is one row that counts its ``copies``; rows alike in their date too are one bill (a register listing a
        bill once per GL line, or JD Edwards pay items), and a register imported again, overlapping the last, does not
        add to them."""
        now = _now()
        keyed = [
            ((vendor_key(r["vendor_name"]), _norm_number(r["invoice_number"]), round(float(r["total"]), 2)), r)
            for r in rows
            if vendor_key(r["vendor_name"]) and _norm_number(r["invoice_number"]) and r.get("total") is not None
        ]
        dates: dict[tuple[str, str, float], set[str]] = {}
        for key, r in keyed:
            dates.setdefault(key, set()).add(r.get("invoice_date") or "")
        copies = {key: len(found - {""}) or 1 for key, found in dates.items()}
        with self._conn() as conn:
            if replace_all:
                conn.execute("DELETE FROM erp_invoices")
            conn.executemany(
                """INSERT INTO erp_invoices (vendor_key, number_key, total, vendor_name, invoice_number, invoice_date,
                   imported_at, copies) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(vendor_key, number_key, total)
                   DO UPDATE SET invoice_date = excluded.invoice_date, imported_at = excluded.imported_at,
                   copies = MAX(erp_invoices.copies, excluded.copies)""",
                [(*key, r["vendor_name"], r["invoice_number"], r.get("invoice_date") or "", now, copies[key])
                 for key, r in keyed],
            )  # fmt: skip
            count = int(conn.execute("SELECT COUNT(*) FROM erp_invoices").fetchone()[0])
            detail: dict[str, Any] = {"rows": len(rows), "total": count}
            if repeats := sum(1 for n in copies.values() if n > 1):
                detail["repeated"] = repeats  # bills the file lists more than once
            self._log(conn, "erp_register_imported", actor=actor, detail=detail)
        return count

    def erp_register_repeats(self) -> int:
        """How many bills the ERP register lists more than once (entered twice in the ERP: the duplicate audit)."""
        with self._conn() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM erp_invoices WHERE copies > 1").fetchone()[0])

    def erp_register_count(self) -> int:
        with self._conn() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM erp_invoices").fetchone()[0])

    def clear_erp_register(self, actor: str | None = None) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM erp_invoices")
            self._log(conn, "erp_register_imported", actor=actor, detail={"rows": 0, "total": 0, "cleared": True})

    def search_rows(self) -> list[dict[str, Any]]:
        """Every invoice's identifying fields and where it stands (for the invoice directory's quick search), with
        the date of its export batch and its GST/HST number (the JD Edwards duplicate check finds a supplier by
        it)."""
        with self._conn() as conn:
            batches = {r["id"]: r["created_at"] for r in conn.execute("SELECT id, created_at FROM export_batches")}
            rows = [
                dict(r)
                for r in conn.execute(
                    """SELECT id, status, file_name, vendor_name, invoice_number, invoice_date, due_date, currency,
                              grand_total, po_key, created_at, reviewed_at, reviewer, second_reviewer,
                              second_reviewed_at, export_batch, parked_reason, follow_up, error,
                              json_extract(final_output, '$.gst_hst_registration_number') AS gst_hst_registration_number
                       FROM invoices ORDER BY id DESC"""
                )
            ]
        for r in rows:
            r["exported_at"] = batches.get(r["export_batch"]) if r["export_batch"] else None
        return rows

    def directory_count(self, rows_sql: str, where: str, params: list[Any]) -> int:
        """How many invoices the invoice directory's filters keep (``directory.py`` builds the SQL, with parameters)."""
        with self._conn() as conn:
            return int(conn.execute(f"{rows_sql} SELECT COUNT(*) FROM rows WHERE {where}", params).fetchone()[0])

    def directory_rows(
        self, rows_sql: str, fields: str, where: str, params: list[Any], order: str, limit: int, offset: int
    ) -> list[dict[str, Any]]:
        """One page of the invoice directory, with the date each one's export batch was made. The page's ids come
        first (SQLite then reads only what the filters and the sort need), then their rows: reading every invoice's
        coding to sort the whole table would take most of a second with tens of thousands of invoices."""
        with self._conn() as conn:
            ids = [
                r[0]
                for r in conn.execute(
                    f"{rows_sql} SELECT id FROM rows WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
                    [*params, limit, offset],
                )
            ]
            found: dict[int, dict[str, Any]] = {}
            for start in range(0, len(ids), 500):
                chunk = ids[start : start + 500]
                marks = ", ".join("?" * len(chunk))  # at most 500, under SQLite's variable limit
                for r in conn.execute(f"{rows_sql} SELECT {fields} FROM rows WHERE id IN ({marks})", chunk):
                    found[r["id"]] = dict(r)
            rows = [found[i] for i in ids if i in found]
            wanted = sorted({r["export_batch"] for r in rows if r["export_batch"]})
            batches = {}
            for start in range(0, len(wanted), 500):
                chunk = wanted[start : start + 500]
                marks = ", ".join("?" * len(chunk))  # at most 500, under SQLite's variable limit
                batches.update(
                    {
                        b["id"]: b["created_at"]
                        for b in conn.execute(f"SELECT id, created_at FROM export_batches WHERE id IN ({marks})", chunk)
                    }
                )
        for r in rows:
            r["exported_at"] = batches.get(r["export_batch"]) if r["export_batch"] else None
        return rows

    def erp_register(self) -> list[dict[str, Any]]:
        """Every invoice in the imported ERP register."""
        with self._conn() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT vendor_key, vendor_name, invoice_number, number_key, invoice_date, total, copies "
                    "FROM erp_invoices"
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
        return None if row is None else self._full_invoice(row)

    def get_invoices(self, invoice_ids: list[int]) -> list[dict[str, Any]]:
        """``get_invoice`` of each of ``invoice_ids`` in one query, in that order (ones that no longer exist are left
        out): a page listing many invoices does not open the database once per invoice."""
        ids = [int(i) for i in invoice_ids]
        if not ids:
            return []
        unique = list(dict.fromkeys(ids))
        with self._conn() as conn:
            found = {
                r["id"]: r
                for r in conn.execute(f"SELECT * FROM invoices WHERE {_in_list('id')}", (json.dumps(unique),))
            }
        return [self._full_invoice(found[i]) for i in ids if i in found]

    def _full_invoice(self, row: sqlite3.Row) -> dict[str, Any]:
        inv = dict(row)
        for key in ("ai_output", "final_output", "validation", "meta", "edits"):
            inv[key] = json.loads(inv[key]) if inv[key] else None
        if inv["source_path"] and not Path(inv["source_path"]).exists():
            self.invoice_file(inv)  # moved (an update): found again, and ``inv`` points there
        return inv

    def invoice_file(self, inv: dict[str, Any]) -> Path | None:
        """The file of invoice ``inv`` (a row with ``id``, ``source_path``...), or None when it is nowhere. A file no
        longer where it was recorded (an update moved the data folder or deleted the old install's folder) is looked
        for by its name and SHA-256 in the data folder (``invoices/``, data an update kept aside in ``backups/``) and
        this install's ``samples/`` (a demo invoice); found, the invoice points there from then on (``inv`` too)."""
        stored = str(inv.get("source_path") or "")
        if not stored:
            return None
        if Path(stored).exists():
            return Path(stored)
        key = (str(self.path), int(inv.get("id") or 0), stored)
        if time.monotonic() - _NOT_FOUND.get(key, -_LOOK_AGAIN_SECONDS) < _LOOK_AGAIN_SECONDS:
            return None  # looked for a moment ago: not again on every page picture
        data = self.path.parent
        sha = inv.get("file_sha256")
        if sha is None and "file_sha256" not in inv:
            with self._conn() as conn:
                row = conn.execute("SELECT file_sha256 FROM invoices WHERE id = ?", (inv.get("id"),)).fetchone()
            sha = row["file_sha256"] if row else None
        backups = data / "backups"
        aside = sorted((p for p in backups.glob("from-*") if p.is_dir()), reverse=True) if backups.is_dir() else []
        meta = inv.get("meta")
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except ValueError:
                meta = {}
        demo = isinstance(meta, dict) and bool(meta.get("demo"))  # only a demo invoice's file is one of the samples
        places = [data / "invoices", *aside, *([PROJECT_DIR / "samples"] if demo else [])]
        found = find_moved_file(stored, sha, places, name=str(inv.get("file_name") or ""), data=data)
        if found is None:
            _NOT_FOUND[key] = time.monotonic()
            return None
        new = str(found.resolve())
        with self._conn() as conn:
            row = conn.execute("SELECT meta FROM invoices WHERE id = ? AND source_path = ?",
                               (inv.get("id"), stored)).fetchone()  # fmt: skip
            if row is not None:
                meta = json.loads(row["meta"]) if row["meta"] else {}
                if isinstance(meta, dict) and meta.get("source") == stored:
                    meta["source"] = new
                conn.execute("UPDATE invoices SET source_path = ?, meta = ? WHERE id = ?",
                             (new, json.dumps(meta, default=str) if meta else row["meta"], inv.get("id")))  # fmt: skip
                self._log(conn, "file_found", inv.get("id"), detail={"from": stored, "to": new})
        inv["source_path"] = new
        if isinstance(inv.get("meta"), dict) and inv["meta"].get("source") == stored:
            inv["meta"]["source"] = new
        return found

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
        """Withdraw what an invoice's approval taught: its coding memory, its lessons (what the AI is told), its
        supplier outcomes (from a review or an audit sample alike, so none keeps counting towards the supplier's
        autonomy) and its reader scores."""
        for table in ("feedback", "supplier_outcomes", "reader_outcomes"):
            conn.execute(f"DELETE FROM {table} WHERE invoice_id = ?", (invoice_id,))
        Store._withdraw_lessons(conn, invoice_id)

    def delete_invoice(
        self, invoice_id: int, forget_lessons: bool = False, actor: str | None = None, *, keep_approved: bool = False
    ) -> None:
        """Delete an invoice; with ``forget_lessons`` also what was learned when it was approved. ``keep_approved``:
        refuse (ValueError, nothing deleted) an invoice approved or waiting for its second approval, e.g. approved
        and exported by someone else since a Delete button was shown for it (reopen it first)."""
        with self._conn() as conn:
            if keep_approved:
                conn.execute("BEGIN IMMEDIATE")  # checked and deleted in one go: an approval cannot slip in between
            row = conn.execute(
                "SELECT file_name, vendor_name, invoice_number, status FROM invoices WHERE id = ?", (invoice_id,)
            ).fetchone()
            if keep_approved and row is not None and row["status"] in (APPROVED, PENDING):
                raise ValueError(f"invoice {invoice_id} was approved meanwhile: reopen it before deleting it")
            conn.execute("DELETE FROM invoices WHERE id = ?", (invoice_id,))
            # What capture read, and the supplier accuracy measured on it, go with the invoice: a deleted
            # invoice (a duplicate upload, a demo) must not count towards a supplier's autonomy, a reader's
            # record or the confidence calibration, and is no longer waiting for the page reader.
            for table in (
                "invoice_capture",
                "supplier_outcomes",
                "reader_outcomes",
                "page_reads",
                "azure_checks",
                "verifications",
            ):
                conn.execute(f"DELETE FROM {table} WHERE invoice_id = ?", (invoice_id,))
            if row is not None:
                self._log(conn, "deleted", invoice_id, actor, {
                    "file": row["file_name"], "vendor": row["vendor_name"], "invoice_number": row["invoice_number"],
                    "status": row["status"], "lessons_forgotten": forget_lessons,
                })  # fmt: skip
            if forget_lessons:
                conn.execute("DELETE FROM feedback WHERE invoice_id = ?", (invoice_id,))
                self._withdraw_lessons(conn, invoice_id)

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
        self, columns: tuple[str, ...], status: str | None = None, ids: list[int] | None = None, decode: bool = True
    ) -> list[dict[str, Any]]:
        """Just these columns of every invoice, or of ``ids`` (JSON ones decoded, unless ``decode`` is False: their
        text as stored): cheaper than ``get_invoice`` in a loop."""
        unknown = set(columns) - set(self._LIGHT_COLUMNS)
        if unknown:
            raise ValueError(f"unknown columns {sorted(unknown)}")
        where, args = [], []
        if status:
            where.append("status = ?")
            args.append(status)
        if ids is not None:
            where.append(_in_list("id"))
            args.append(json.dumps([int(i) for i in ids]))
        sql = f"SELECT {', '.join(columns)} FROM invoices" + (" WHERE " + " AND ".join(where) if where else "")
        with self._conn() as conn:
            rows = [dict(r) for r in conn.execute(sql + " ORDER BY id", args)]
        for r in rows if decode else []:
            for key in set(columns) & set(self._JSON_COLUMNS):
                r[key] = json.loads(r[key]) if r[key] else None
        return rows

    def demo_count(self) -> int:
        """How many demo invoices are loaded (see ``demo.py``), without reading every invoice."""
        with self._conn() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM invoices WHERE meta LIKE '%\"demo\": true%'").fetchone()[0])

    def approved_since(self, since_iso: str, reviewer: str | None = None) -> int:
        """Invoices approved since ``since_iso`` (and still approved, or waiting for a second approver). With
        ``reviewer``: only those this person approved, first or second approval (names compared without case)."""
        with self._conn() as conn:
            if reviewer is None:
                sql = "SELECT COUNT(*) FROM invoices WHERE status IN (?, ?) AND reviewed_at >= ?"
                return int(conn.execute(sql, (APPROVED, PENDING, since_iso)).fetchone()[0])
            rows = conn.execute(
                "SELECT reviewer, reviewed_at >= ?, second_reviewer, second_reviewed_at >= ? FROM invoices "
                "WHERE status IN (?, ?) AND (reviewed_at >= ? OR second_reviewed_at >= ?)",
                (since_iso, since_iso, APPROVED, PENDING, since_iso, since_iso),
            ).fetchall()
        # Compared here, not in SQL: SQLite's lower() leaves accented letters alone ("Élise" never matched).
        me = reviewer.strip().casefold()
        return sum(
            1
            for first, first_since, second, second_since in rows
            if (first_since and (first or "").strip().casefold() == me)
            or (second_since and (second or "").strip().casefold() == me)
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
        from ..capture.workflow import AUTONOMOUS_REVIEWER

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
        second_reason = self.second_approval_reason(final_output)
        needs_second = bool(second_reason)
        vendor_name = final_output.get("vendor_name", "")
        key = vendor_key(vendor_name)
        now = _now()
        counts = {ACCEPTED: 0, CORRECTED: 0, CODED: 0}
        reviewer_changed = reviewer_coded = 0
        # Lines a fixed coding rule set: the AI's own answer is what its accuracy is measured on.
        by_rule = {c["line_number"]: c for c in (inv.get("meta") or {}).get("rules_applied") or []}
        feedback_rows = []

        kept = suggestion_kept
        # Without a cost center list the review screen has none to show or keep: a cost center the AI copied from
        # coding memory is not part of what it proposed (B-562).
        cost_centers = bool(self.list_accounts("cost_centers"))

        for suggestion, li in pair_lines(ai.get("line_items", []), final_output.get("line_items", [])):
            s_gl = suggestion.get("predicted_gl_code") if suggestion else None
            s_cc = (suggestion.get("predicted_cost_center", "") if cost_centers else "") if suggestion else None
            f_gl, f_cc = li["predicted_gl_code"], li.get("predicted_cost_center", "")
            if f_gl == UNASSIGNED:
                continue  # not a coding decision: nothing to learn from it
            rule = by_rule.get(suggestion.get("line_number")) if suggestion else None
            guess = line_guessed(suggestion, rule)
            kept_rule = rule is not None and rule.get("gl_to") == f_gl and (rule.get("cc_to") or "") == (f_cc or "")
            # A kept guess is no edit; another account in its place is (B-479), as any change to what was proposed.
            blank = nothing_proposed(s_gl, s_cc)
            replaced = (
                (f_gl != s_gl or (f_cc or "") != (s_cc or ""))
                if guess
                else not blank and not kept(s_gl, s_cc, f_gl, f_cc)
            )
            if not kept_rule and replaced:
                reviewer_changed += 1
            if not kept_rule and blank and not guess:  # coded by hand where the AI left the line blank (B-670)
                reviewer_coded += 1
            if rule is not None:  # the AI's own answer, as first recorded (not a rule's)
                s_gl, s_cc = rule.get("gl_from"), rule.get("cc_from", "")
            # Accuracy measures what the AI proposed: the blanks it left are not counted against it. A line whose
            # account it only guessed, or left blank, is the reviewer's own coding: neither confirmed nor corrected
            # (B-475, B-650). (The rule's own answer decides: a rule that filled a blank leaves the line coded.)
            outcome = (
                CODED if guess or nothing_proposed(s_gl, s_cc) else ACCEPTED if kept(s_gl, s_cc, f_gl, f_cc)
                else CORRECTED
            )  # fmt: skip
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
        if reviewer_coded:  # no correction, but not the AI's coding either: never "approved without any edit"
            edits.append("line_coded")
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
                **({"coded": counts[CODED]} if counts[CODED] else {}),
                "total": final_output.get("grand_total"), "changes": diff_coding(ai, final_output),
                **({"bulk": True} if bulk else {}),
                **({"needs_second": True, "second_reason": second_reason} if needs_second else {}),
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

    def second_approval_reason(self, coding: dict[str, Any], limit: float | None = None, *,
                               unknown_rate: bool = True) -> str:  # fmt: skip
        """Why this invoice needs a second approver ("" when it doesn't). The limit is in CAD: a foreign-currency total
        is converted at the rate set in Settings. Without a rate it is never taken as CAD (9,000 EUR is about 13,000
        CAD): it needs the second approver, whatever its total (``unknown_rate``; False: left out, for a caller that
        deals with a missing rate itself, like the touchless gates)."""
        limit = self.approval_limit() if limit is None else limit
        if not limit:
            return ""
        currency = str(coding.get("currency") or "CAD").strip().upper()
        rate = self.fx_rates().get(currency)
        if rate is None:
            return f"no CAD rate for {currency}: a second approval is needed" if unknown_rate else ""
        total = abs(float(coding.get("grand_total") or 0))
        if not math.isfinite(total) or total * rate > limit:  # a NaN total is never "over" anything: refused here
            return f"over the approval limit of {limit:,.2f} CAD"
        return ""

    def over_approval_limit(self, coding: dict[str, Any], limit: float | None = None, *,
                            unknown_rate: bool = True) -> bool:  # fmt: skip
        """Does this invoice need a second approver (``second_approval_reason``)?"""
        return bool(self.second_approval_reason(coding, limit, unknown_rate=unknown_rate))

    def final_approve(self, invoice_id: int, approver: str, login: str = "", shown: str | None = None) -> None:
        """The second approval: by someone other than the first approver (another name and, when the
        dashboard records it, another computer login). The invoice can then be exported. ``shown``: the first
        approval the page showed (its ``approved`` event's id, as the page posts it): approved again since, it is
        refused (``ApprovedAgain``), as the version now waiting is not the one the approver looked at (B-654)."""
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
            conn.execute("BEGIN IMMEDIATE")  # checked and approved in one go: no new first approval in between
            latest = conn.execute(
                "SELECT MAX(id) FROM events WHERE invoice_id = ? AND action = 'approved'", (invoice_id,)
            ).fetchone()[0]
            if shown is not None and str(shown).strip() != str(latest or ""):
                raise ApprovedAgain(f"invoice {invoice_id} was approved again since the page was opened")
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

    def reopen(self, invoice_id: int, actor: str, reason: str = "", *, only_rejected: bool = False) -> None:
        """Put an approved invoice that is not exported yet, or a rejected one, back in the review queue to be
        corrected. What its approval taught is withdrawn (learned again at the next approval); the reviewer's
        coding is kept as the starting point. An exported invoice is reopened by undoing its batch first.
        ``only_rejected``: a rejected invoice only (a Reopen button shown on a rejected invoice that someone has
        reopened and approved since must not reopen that approval)."""
        approved = "" if only_rejected else f"status IN ('{APPROVED}', '{PENDING}') AND export_batch IS NULL OR "
        with self._conn() as conn:
            cur = conn.execute(
                f"""UPDATE invoices SET status = ?, reviewer = NULL, reviewed_at = NULL, second_reviewer = NULL,
                   second_reviewed_at = NULL, error = NULL WHERE id = ? AND ({approved}status = ?)""",
                (REVIEW, invoice_id, REJECTED),
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
            listed = (json.dumps(list(keys)),)
            numbers = [
                r[0] for r in conn.execute(f"SELECT po_number FROM purchase_orders WHERE {_in_list('po_key')}", listed)
            ]
            conn.execute(f"DELETE FROM po_lines WHERE {_in_list('po_key')}", listed)
            conn.execute(f"DELETE FROM purchase_orders WHERE {_in_list('po_key')}", listed)
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
            # The ids as one JSON list: one '?' however many are ticked (SQLite takes only so many bound variables).
            eligible = conn.execute(
                "SELECT id, grand_total, currency FROM invoices WHERE id IN (SELECT value FROM json_each(?)) "
                "AND status = ? AND export_batch IS NULL ORDER BY id",
                (json.dumps([int(i) for i in invoice_ids]), APPROVED),
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
            # The write lock from the read to the write: a background check's notes saved in between are kept (B-658).
            conn.execute("BEGIN IMMEDIATE")
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
        """The ERP import failed: put the batch's invoices back in the ready-to-export list. ValueError when the
        batch was undone already (two clicks at once: the second must not log a second undo)."""
        with self._conn() as conn:
            claimed = conn.execute(
                "UPDATE export_batches SET undone_at = ?, undone_by = ? WHERE id = ? AND undone_at IS NULL",
                (_now(), actor, batch),
            )
            if claimed.rowcount == 0:
                raise ValueError(f"export batch {batch} is unknown or already undone")
            ids = [r["id"] for r in conn.execute("SELECT id FROM invoices WHERE export_batch = ?", (batch,))]
            conn.execute("UPDATE invoices SET export_batch = NULL WHERE export_batch = ?", (batch,))
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

    def layout_sources(self, ids: list[int]) -> dict[int, str]:
        """{invoice id: how its pages were read} ("text", "ocr", "di", "mixed"), in one query; an invoice with no
        capture is left out."""
        if not ids:
            return {}
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT invoice_id, layout_source FROM invoice_capture WHERE {_in_list('invoice_id')}",
                (json.dumps([int(i) for i in ids]),),
            ).fetchall()
        return {int(r[0]): str(r[1] or "") for r in rows}

    def credit_note_ids(self, status: str) -> set[int]:
        """The invoices with ``status`` that capture read as titled a credit note, in one query (bulk approval)."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT c.invoice_id FROM invoice_capture c JOIN invoices i ON i.id = c.invoice_id
                   WHERE i.status = ? AND json_extract(c.capture_json, '$.credit_note')""",
                (status,),
            ).fetchall()
        return {r[0] for r in rows}

    def supplier_key_for(self, vendor_name: str, gst_number: str | None = None) -> str:
        """The supplier key for an invoice, with the vendor master's ERP id when the master knows it."""
        from ..capture.supplier import supplier_key
        from ..coding.vendors import master_vendor_id

        return supplier_key(vendor_name, gst_number, master_vendor_id(self.vendor_ids(), vendor_name, gst_number))

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

    # --- Touchless processing: one switch and one bar for every supplier ------------------------------------------

    def autonomy_policy(self) -> Any:
        """The bar every supplier must meet to go touchless (``AutonomyPolicy``): fixed standard values, the same
        for every supplier and every installation. An ``autonomy_policy`` setting left by an older version is
        ignored."""
        from ..capture.supplier import AutonomyPolicy

        return AutonomyPolicy()

    def touchless_enabled(self) -> bool:
        """Touchless processing (Settings → Automation), off until an AP lead turns it on: while it is off, every
        invoice is approved by a person, whatever its supplier's record."""
        return self.get_setting(TOUCHLESS_SETTING) == "on"

    def set_touchless(self, on: bool, actor: str | None) -> list[dict[str, Any]]:
        """Turn touchless processing on or off for the whole company (recorded in the audit trail with who did it).
        Turned on, every supplier that meets the bar goes touchless at once and any supplier made touchless by hand
        under another bar that does not meet it goes back to review (``sync_autonomy``); returns those changes.
        Turned off, suppliers keep their record and state, and nothing is approved without a person."""
        if on == self.touchless_enabled():
            return []
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (TOUCHLESS_SETTING, "on" if on else "off"),
            )
            self._log(conn, "touchless_on" if on else "touchless_off", actor=actor, detail={
                "policy": self.autonomy_policy().to_dict(), "limit": self.touchless_limit(),
            })  # fmt: skip
        return self.sync_autonomy() if on else []

    def touchless_limit(self) -> float:
        """The largest invoice total approved without a person (Settings → Automation), in CAD: a foreign-currency
        total is converted at the exchange rates in Settings → Review (without a rate it goes to a person:
        ``capture.workflow.invoice_gates``)."""
        try:
            value = float(self.get_setting(TOUCHLESS_LIMIT_SETTING) or DEFAULT_TOUCHLESS_LIMIT)
        except ValueError:
            return DEFAULT_TOUCHLESS_LIMIT
        return value if value > 0 else DEFAULT_TOUCHLESS_LIMIT

    def set_touchless_limit(self, amount: float, actor: str | None) -> bool:
        """Set the touchless limit (must be above zero). Returns whether it changed (recorded in the audit trail)."""
        amount = round(float(amount), 2)
        if amount <= 0:
            raise ValueError("the touchless limit must be above zero")
        before = self.touchless_limit()
        if amount == before:
            return False
        self.set_setting(TOUCHLESS_LIMIT_SETTING, f"{amount:.2f}", actor=actor)
        self.log_event("settings_changed", actor=actor,
                       detail={"keys": [TOUCHLESS_LIMIT_SETTING], "from": before, "to": amount})  # fmt: skip
        return True

    def over_touchless_limit(self, coding: dict[str, Any]) -> bool:
        """Is this invoice's total above the largest amount approved without a person?"""
        # A currency with no rate is its own touchless gate (``capture.workflow.invoice_gates``), said as such.
        return self.over_approval_limit(coding, limit=self.touchless_limit(), unknown_rate=False)

    def sync_autonomy(self, key: str | None = None) -> list[dict[str, Any]]:
        """With touchless processing on, move each supplier (or the one ``key``) to the state its record earns
        (``supplier.automatic_transition``): it goes touchless by itself once it meets the bar, and back to review
        when it no longer may. Each change is recorded in the audit trail by "AP Coder". Returns the changes."""
        from ..capture.supplier import automatic_transition

        if not self.touchless_enabled():
            return []
        policy = self.autonomy_policy()
        profiles = [p for p in [self.get_supplier_profile(key)] if p] if key else self.list_supplier_profiles()
        changes = []
        for p in profiles:
            stats = self.supplier_stats(p["key"], policy.window)
            move = automatic_transition(stats, policy, p["state"] or "supervised", p["autonomous_since"],
                                        p.get("suspended_at"))  # fmt: skip
            if move is None:
                continue
            state, reason = move
            with self._conn() as conn:
                self._set_state(conn, p, state, "AP Coder", reason, None, policy)
            changes.append({"key": p["key"], "supplier": p["display_name"] or p["key"], "from": p["state"],
                            "to": state, "reason": reason})  # fmt: skip
        return changes

    def set_supplier_state(
        self, key: str, state: str, by: str | None, reason: str = "", audit_rate: float | None = None
    ) -> None:
        """Set a supplier's state: ``held`` (an AP lead keeps it supervised, whatever its record), ``supervised``
        (back to the automatic bar, e.g. "Allow again" after ``held``), ``suspended`` (a person found an error in a
        touchless invoice) or ``autonomous`` (only when it meets the bar; touchless processing does this by itself).
        Recorded in the audit trail with who did it and why."""
        from ..capture.supplier import AUTONOMOUS, STORED_STATES, SUSPENDED, meets_policy

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
        with self._conn() as conn:
            self._set_state(conn, profile, state, by, reason, audit_rate, policy)
        if state != SUSPENDED:  # "Allow again": it may go touchless straight away
            self.sync_autonomy(key)

    def _set_state(
        self, conn: sqlite3.Connection, profile: dict[str, Any], state: str, by: str | None, reason: str,
        audit_rate: float | None, policy: Any,
    ) -> None:  # fmt: skip
        from ..capture.supplier import AUTONOMOUS, HELD, SUPERVISED, SUSPENDED

        key = profile["key"]
        if state == HELD:
            action = "autonomy_held"
        elif state == SUPERVISED and profile["state"] == HELD:
            action = "autonomy_allowed"
        else:
            action = {AUTONOMOUS: "autonomy_on", SUSPENDED: "autonomy_suspended"}.get(state, "autonomy_off")
        rate = audit_rate if audit_rate is not None else profile["audit_rate"]
        if state == AUTONOMOUS and rate is None:
            rate = policy.audit_rate
        # Since when it is touchless (kept while suspended, to show; cleared otherwise), and when it was suspended
        # (its fresh clean streak counts from then: kept while an AP lead keeps it supervised and once allowed again,
        # cleared when it is touchless again).
        since = _now() if state == AUTONOMOUS else profile["autonomous_since"] if state == SUSPENDED else None
        suspended = _now() if state == SUSPENDED else None if state == AUTONOMOUS else profile.get("suspended_at")
        conn.execute(
            "UPDATE supplier_profiles SET state = ?, autonomous_since = ?, suspended_at = ?, audit_rate = ?, "
            "updated_at = ?, updated_by = ? WHERE key = ?",
            (state, since, suspended, rate, _now(), by, key),
        )
        self._log(conn, action, actor=by, detail={
            "supplier": profile["display_name"] or key, "key": key, "from": profile["state"], "to": state,
            **({"reason": reason} if reason else {}),
            **({"audit_rate": rate, "policy": policy.to_dict()} if state == AUTONOMOUS else {}),
        })  # fmt: skip

    def touchless_summary(self, days: int = 30) -> dict[str, Any]:
        """The touchless numbers over the last ``days`` days: invoices processed, approved without a person,
        audited (a touchless candidate picked for a person to check) and how many of those a person corrected, and
        touchless invoices reopened (a person found something wrong after the fact)."""
        from ..capture.workflow import AUTONOMOUS_REVIEWER

        since = (dt.datetime.now() - dt.timedelta(days=days)).isoformat(timespec="seconds")
        with self._conn() as conn:
            processed = conn.execute(
                "SELECT COUNT(DISTINCT invoice_id) FROM events WHERE action = 'processed' AND created_at >= ?", (since,)
            ).fetchone()[0]
            touchless = conn.execute(
                "SELECT COUNT(DISTINCT invoice_id) FROM events WHERE action = 'approved' AND actor = ? "
                "AND created_at >= ?",
                (AUTONOMOUS_REVIEWER, since),
            ).fetchone()[0]
            audits = conn.execute(
                "SELECT invoice_id, MIN(correct) AS clean FROM supplier_outcomes WHERE source = 'audit' AND at >= ? "
                "GROUP BY invoice_id",
                (since,),
            ).fetchall()
            reopened = conn.execute(
                "SELECT COUNT(DISTINCT r.invoice_id) FROM events r WHERE r.action = 'reopened' AND r.created_at >= ? "
                "AND EXISTS (SELECT 1 FROM events a WHERE a.invoice_id = r.invoice_id AND a.action = 'approved' "
                "AND a.actor = ?)",
                (since, AUTONOMOUS_REVIEWER),
            ).fetchone()[0]
        return {
            "days": days, "processed": processed, "touchless": touchless,
            "touchless_rate": touchless / processed if processed else None,
            "audited": len(audits), "audit_errors": sum(1 for r in audits if not r["clean"]), "reopened": reopened,
        }  # fmt: skip

    def record_outcomes(
        self, supplier_key: str, invoice_id: int | None, rows: list[dict[str, Any]], source: str = "review",
        display_name: str = "", vendor_id: str = "", at: str | None = None,
    ) -> dict[str, Any]:  # fmt: skip
        """Record, per header field, what AP Coder proposed and what AP approved (``outcome_rows``).

        Recording the same invoice and source again replaces its rows (an invoice approved again after
        a reopen). A correction on an autonomous supplier suspends it at once; with touchless processing on, a
        supplier this invoice brings up to the bar goes touchless (``sync_autonomy``)."""
        from ..capture.supplier import AUTONOMOUS, SUSPENDED

        if source not in ("review", "audit"):
            raise ValueError(f"unknown outcome source {source!r}")
        if not supplier_key:
            return {"fields": 0, "corrections": 0, "suspended": False, "touchless": False}
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
                    "UPDATE supplier_profiles SET state = ?, suspended_at = ?, updated_at = ?, updated_by = ? "
                    "WHERE key = ?",
                    (SUSPENDED, at, _now(), "AP Coder", supplier_key),
                )
                self._log(conn, "autonomy_suspended", invoice_id, "AP Coder", {
                    "supplier": profile["display_name"] or supplier_key, "key": supplier_key, "from": AUTONOMOUS,
                    "to": SUSPENDED, "reason": f"{source} found a correction: {', '.join(corrections)}",
                })  # fmt: skip
        promoted = self.sync_autonomy(supplier_key)
        return {"fields": len(rows), "corrections": len(corrections), "suspended": suspended,
                "touchless": any(c["to"] == AUTONOMOUS for c in promoted)}  # fmt: skip

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
        from ..capture.supplier import SupplierStats

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
                f"SELECT DISTINCT supplier_key FROM supplier_outcomes WHERE {_in_list('invoice_id')}",
                (json.dumps([int(i) for i in ids]),),
            ).fetchall()
        return [r[0] for r in rows]

    def prune_supplier_profiles(self, keys: list[str]) -> int:
        """Delete these supplier profiles when nothing is recorded for them any more (e.g. after the demo)."""
        if not keys:
            return 0
        with self._conn() as conn:
            return conn.execute(
                f"DELETE FROM supplier_profiles WHERE {_in_list('key')} AND state != 'autonomous' AND key NOT IN "
                "(SELECT DISTINCT supplier_key FROM supplier_outcomes)",
                (json.dumps(list(keys)),),
            ).rowcount

    # --- Each reader against what AP approved; the page reader's queue -------------------------------------------

    def record_reader_outcomes(
        self, invoice_id: int, rows: list[dict[str, Any]], at: str | None = None, reader: str | None = None
    ) -> int:
        """What each capture reader read on an approved invoice, against what AP approved
        (``capture.workflow.reader_outcome_rows``): one row per reader and field; reader "fused" is the value
        AP saw, with its evidence key and status. Recording an invoice again replaces its rows (an invoice
        approved again after a reopen). ``reader``: only that reader's rows are replaced (the Azure check, run on an
        invoice already approved, adds reader "di" beside the others). Returns how many rows were kept."""
        at = at or _now()
        if reader:
            rows = [r for r in rows if r.get("reader") == reader]

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
            if reader:
                conn.execute("DELETE FROM reader_outcomes WHERE invoice_id = ? AND reader = ?", (invoice_id, reader))
            else:
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

    def fused_kind_scorecard(self) -> dict[str, dict[str, Any]]:
        """How often the value AP saw was the one AP approved, by how the invoice came in: {"text": vendor PDFs (their
        text inside), "scan": scans and photos (read with OCR)} -> {fields, agreed, rate, invoices}."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT CASE WHEN layout_source = 'text' THEN 'text' ELSE 'scan' END kind, COUNT(*) fields,
                          SUM(correct) agreed, COUNT(DISTINCT invoice_id) invoices
                   FROM reader_outcomes WHERE reader = 'fused' AND layout_source IN ('text', 'ocr', 'di', 'mixed')
                   GROUP BY kind"""
            ).fetchall()
        return {r["kind"]: self._score(r) for r in rows}

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
                    error = '', tries = 0, reader = '', updated_at = excluded.updated_at
                    WHERE page_reads.status != 'reading' OR page_reads.updated_at < ?""",
                (invoice_id, reason or "", requested_by or "", now, now, _stale_before()),
            )
            return cur.rowcount > 0

    def next_page_read(self, invoice_id: int | None = None, reader: str = "") -> dict[str, Any] | None:
        """The invoice the page reader should read next (the one waiting longest), marked as being read (by
        ``reader``: the process reading it); None when nothing waits. With ``invoice_id``: that invoice, if it is in
        line (the one AP asked for, read first), else None. Two readers (the dashboard's and ``read-pages``) never
        take the same invoice."""
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
                "UPDATE page_reads SET status = 'reading', error = '', reader = ?, updated_at = ? WHERE invoice_id = ?",
                (reader or "", now, row[0]),
            )
            taken = conn.execute("SELECT * FROM page_reads WHERE invoice_id = ?", (row[0],)).fetchone()
        return dict(taken)

    def finish_page_read(
        self, invoice_id: int, status: str, model: str = "", pages: int = 0, seconds: float = 0.0, error: str = "",
        *, tries: int | None = None, only_if_reading: bool = False,
    ) -> bool:  # fmt: skip
        """How a page read ended: ``done``, ``failed`` (``error`` says why), ``skipped`` (nothing to read, or the
        invoice was dealt with meanwhile) or ``waiting`` (stopped part-way: it keeps its place in line). ``tries``:
        the reads cut off by the model server so far (None: as it was; ``done`` starts again from 0).
        ``only_if_reading``: only while it is still marked as being read. False when the invoice is no longer in
        line (deleted meanwhile)."""
        if status not in (PAGE_DONE, PAGE_FAILED, PAGE_SKIPPED, PAGE_WAITING):
            raise ValueError(f"unknown page read status {status!r}")
        if tries is None and status == PAGE_DONE:
            tries = 0
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE page_reads SET status = ?, model = ?, pages = ?, seconds = ?, error = ?, updated_at = ?, "
                "reader = '', tries = COALESCE(?, tries) WHERE invoice_id = ?"
                + (" AND status = 'reading'" if only_if_reading else ""),
                (status, model or "", int(pages or 0), round(float(seconds or 0), 2), (error or "")[:2000], _now(),
                 tries, invoice_id),
            )  # fmt: skip
            return cur.rowcount > 0

    def release_page_reads(self, alive: Callable[[str], bool], why: str = "") -> int:
        """Put back in line every invoice marked as being read by a reader that isn't reading any more
        (``alive(reader)`` is False: its process was closed or killed part way). How many were put back."""
        why = why or "interrupted (AP Coder was closed while it was read): it is read again"
        with self._conn() as conn:
            rows = conn.execute("SELECT invoice_id, reader FROM page_reads WHERE status = 'reading'").fetchall()
            gone = [(r["invoice_id"], r["reader"]) for r in rows if not alive(r["reader"] or "")]
            for invoice_id, reader in gone:
                conn.execute(
                    "UPDATE page_reads SET status = 'waiting', reader = '', error = ?, updated_at = ? "
                    "WHERE invoice_id = ? AND status = 'reading' AND reader = ?",
                    (why, _now(), invoice_id, reader),
                )
        return len(gone)

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

    def page_reads_waiting(self, still_open: bool = False) -> int:
        """How many invoices wait for the page reader (interrupted reads included). ``still_open``: only those not
        approved, rejected or exported yet, the ones a person waits for (the reader still reads the others, to score
        itself against what AP approved)."""
        sql = f"SELECT COUNT(*) FROM page_reads WHERE {_IN_LINE}"
        args: list[Any] = [_stale_before()]
        if still_open:
            sql += " AND invoice_id IN (SELECT id FROM invoices WHERE status IN (?, ?, ?))"
            args += [REVIEW, PARKED, PENDING]
        with self._conn() as conn:
            row = conn.execute(sql, args).fetchone()
        return int(row[0])

    def page_read_pages_average(self, recent: int = 50) -> float | None:
        """The pages the page reader read per invoice, on average over its latest ``recent`` finished reads (None
        before the first): how many pages an invoice in its queue is likely to have."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT AVG(pages) FROM (SELECT pages FROM page_reads WHERE status = 'done' AND pages > 0 "
                "ORDER BY updated_at DESC LIMIT ?)",
                (int(recent),),
            ).fetchone()
        return round(float(row[0]), 2) if row and row[0] is not None else None

    def supplier_templates_count(self) -> int:
        """How many suppliers have a learned template (for "How AP Coder reads every invoice"), without reading
        them."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM supplier_profiles WHERE template_json IS NOT NULL "
                "AND template_json NOT IN ('', 'null', '{}')"
            ).fetchone()
        return int(row[0]) if row else 0

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

    # --- The Azure check's queue (ap_coder/ai/azure_check.py) -----------------------------------------------------

    def queue_azure_check(self, invoice_id: int, reason: str) -> bool:
        """Put an invoice in line for the Azure check (``reason``: "low-confidence" or "spot-check"). One already in
        line or checked stays as it is, except that a low-confidence reason replaces a spot-check still waiting, and
        one skipped before is put back in line. False when nothing changed."""
        now = _now()
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO azure_checks (invoice_id, reason, status, created_at, updated_at)
                   VALUES (?, ?, 'waiting', ?, ?) ON CONFLICT(invoice_id) DO UPDATE SET reason = excluded.reason,
                   status = 'waiting', error = '', updated_at = excluded.updated_at
                   WHERE azure_checks.status = 'skipped' OR (azure_checks.status = 'waiting'
                   AND azure_checks.reason != excluded.reason AND excluded.reason = 'low-confidence')""",
                (invoice_id, reason, now, now),
            )
            return cur.rowcount > 0

    def next_azure_check(self, *, after_page_reader: bool = False) -> dict[str, Any] | None:
        """The invoice the Azure check takes next (the one waiting longest), marked as being checked; None when
        nothing waits. ``after_page_reader``: leave invoices still waiting for the page reader (OvisOCR2 is running
        and will read them): they are checked once it has, on the invoice as it then stands."""
        now, stale = _now(), _stale_before()
        later = (
            " AND NOT EXISTS (SELECT 1 FROM page_reads p WHERE p.invoice_id = azure_checks.invoice_id "
            "AND p.status IN ('waiting', 'reading'))"
            if after_page_reader
            else ""
        )
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                f"SELECT invoice_id FROM azure_checks WHERE {_AZURE_IN_LINE}{later} "
                "ORDER BY created_at, invoice_id LIMIT 1",
                (stale,),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE azure_checks SET status = 'checking', error = '', updated_at = ? WHERE invoice_id = ?",
                (now, row[0]),
            )
            taken = conn.execute("SELECT * FROM azure_checks WHERE invoice_id = ?", (row[0],)).fetchone()
        return dict(taken)

    def finish_azure_check(
        self, invoice_id: int, status: str, *, result: dict[str, Any] | None = None, pages: int = 0,
        compared: int = 0, agreed: int = 0, error: str = "", reason: str | None = None,
    ) -> bool:  # fmt: skip
        """How a check ended: ``done`` (``result``: Azure's values and the comparison), ``failed`` (``error``),
        ``skipped`` (nothing to check), or ``waiting`` (back in line, e.g. the day's page cap is reached)."""
        if status not in (AZURE_WAITING, AZURE_DONE, AZURE_FAILED, AZURE_SKIPPED):
            raise ValueError(f"unknown Azure check status {status!r}")
        now = _now()
        with self._conn() as conn:
            cur = conn.execute(
                """UPDATE azure_checks SET status = ?, result_json = COALESCE(?, result_json), pages = ?,
                   compared = ?, agreed = ?, error = ?, updated_at = ?, reason = COALESCE(?, reason),
                   compared_at = CASE WHEN ? = 'done' THEN ? ELSE compared_at END WHERE invoice_id = ?""",
                (status, json.dumps(result, default=str) if result is not None else None, int(pages or 0),
                 int(compared or 0), int(agreed or 0), (error or "")[:2000], now, reason, status, now, invoice_id),
            )  # fmt: skip
            return cur.rowcount > 0

    def azure_check(self, invoice_id: int) -> dict[str, Any] | None:
        """The invoice's Azure check: {invoice_id, reason, status, pages, compared, agreed, result (dict or None),
        error, created_at, updated_at, compared_at}, or None when it was never put in line."""
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM azure_checks WHERE invoice_id = ?", (invoice_id,)).fetchone()
        if row is None:
            return None
        check = dict(row)
        check["result"] = json.loads(check.pop("result_json") or "null")
        if check["status"] == AZURE_CHECKING and check["updated_at"] < _stale_before():
            check["status"] = AZURE_WAITING
        return check

    # --- Lessons: AP's corrections per situation (ap_coder/ai/lessons.py) ---------------------------------------------

    def record_lesson(self, row: dict[str, Any], *, invoice_id: int | None = None, actor: str | None = None) -> None:
        """One approval's word on a situation (``lessons.outcomes``): seen, and corrected (the value AP chose, what
        was proposed) or confirmed. A situation never corrected is only counted, never shown. What ``invoice_id``'s
        approval added is kept beside it (``lesson_approvals``), to be taken back when the approval is withdrawn."""
        now = _now()
        corrected = 1 if row.get("corrected") else 0
        value, proposed = str(row.get("value") or "")[:300], str(row.get("proposed") or "")[:300]
        with self._conn() as conn:
            if invoice_id is not None:  # the same invoice's word on it counts once, the latest
                self._withdraw_lessons(conn, invoice_id, row["key"])
            before = conn.execute("SELECT corrected FROM lessons WHERE situation = ?", (row["key"],)).fetchone()
            confirmed = 1 if not corrected and before is not None and before["corrected"] > 0 else 0
            conn.execute(
                """INSERT INTO lessons (situation, vendor_key, vendor_name, kind, field, pattern, value, proposed, seen,
                   corrected, since_why, confirmed, last_invoice, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, 0, ?, ?, ?)
                   ON CONFLICT(situation) DO UPDATE SET seen = lessons.seen + 1, vendor_name = excluded.vendor_name,
                   corrected = lessons.corrected + excluded.corrected,
                   since_why = lessons.since_why + excluded.since_why,
                   confirmed = lessons.confirmed + CASE WHEN excluded.corrected = 0 AND lessons.corrected > 0
                                                   THEN 1 ELSE 0 END,
                   value = CASE WHEN excluded.corrected = 1 THEN excluded.value ELSE lessons.value END,
                   proposed = CASE WHEN excluded.corrected = 1 THEN excluded.proposed ELSE lessons.proposed END,
                   last_invoice = excluded.last_invoice, updated_at = excluded.updated_at""",
                (row["key"], vendor_key(row.get("vendor") or ""), row.get("vendor") or "", row.get("kind") or "invoice",
                 row["field"], row.get("pattern") or "", value, proposed, corrected, corrected, invoice_id, now, now),
            )  # fmt: skip
            if invoice_id is not None:
                conn.execute(
                    "INSERT OR REPLACE INTO lesson_approvals (invoice_id, situation, corrected, confirmed, value, "
                    "proposed, at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (invoice_id, row["key"], corrected, confirmed, value, proposed, now),
                )

    @staticmethod
    def _withdraw_lessons(conn: sqlite3.Connection, invoice_id: int, situation: str | None = None) -> None:
        """Take back what ``invoice_id``'s approval added to the lessons (one situation's, or all): seen, corrected,
        confirmed, and the count towards the next "why" (unless AP gave a reason since). A withdrawn correction's value
        gives way to the latest correction still standing; with none left the lesson is no longer shown."""
        where, args = "invoice_id = ?" + (" AND situation = ?" if situation else ""), (invoice_id,)
        args += (situation,) if situation else ()
        for c in conn.execute(f"SELECT * FROM lesson_approvals WHERE {where}", args).fetchall():
            lesson = conn.execute("SELECT * FROM lessons WHERE situation = ?", (c["situation"],)).fetchone()
            if lesson is None:
                continue
            value, proposed = lesson["value"], lesson["proposed"]
            if c["corrected"]:
                last = conn.execute(
                    "SELECT value, proposed FROM lesson_approvals WHERE situation = ? AND invoice_id != ? "
                    "AND corrected = 1 ORDER BY at DESC, rowid DESC LIMIT 1",
                    (c["situation"], invoice_id),
                ).fetchone()
                if last is not None:
                    value, proposed = last["value"], last["proposed"]
            since_why = c["corrected"] if not lesson["explained_at"] or c["at"] > lesson["explained_at"] else 0
            conn.execute(
                "UPDATE lessons SET seen = MAX(seen - 1, 0), corrected = MAX(corrected - ?, 0), "
                "since_why = MAX(since_why - ?, 0), confirmed = MAX(confirmed - ?, 0), value = ?, proposed = ? "
                "WHERE id = ?",
                (c["corrected"], since_why, c["confirmed"], value, proposed, lesson["id"]),
            )
        conn.execute(f"DELETE FROM lesson_approvals WHERE {where}", args)

    def lesson(self, situation: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM lessons WHERE situation = ?", (situation,)).fetchone()
        return dict(row) if row else None

    def lessons_for(self, key: str, kind: str | None = None) -> list[dict[str, Any]]:
        """A vendor's lessons (of one kind of document), the ones with a reason first, then the newest."""
        sql = "SELECT * FROM lessons WHERE vendor_key = ?" + (" AND kind = ?" if kind else "")
        with self._conn() as conn:
            rows = conn.execute(sql + " ORDER BY explanation = '', updated_at DESC, id DESC",
                                (key, kind) if kind else (key,)).fetchall()  # fmt: skip
        return [dict(r) for r in rows]

    def list_lessons(self, *, include_retired: bool = False, limit: int = 500) -> list[dict[str, Any]]:
        """Every lesson AP corrected at least once, the most corrected since its last reason first."""
        where = "corrected > 0" + ("" if include_retired else " AND retired = 0")
        with self._conn() as conn:
            rows = conn.execute(f"SELECT * FROM lessons WHERE {where} ORDER BY since_why DESC, updated_at DESC "
                                "LIMIT ?", (limit,)).fetchall()  # fmt: skip
        return [dict(r) for r in rows]

    def explain_lesson(self, situation: str, explanation: str, actor: str | None = None,
                       invoice_id: int | None = None) -> bool:  # fmt: skip
        """AP's reason for a situation (the "why" screen, or the Learning page): kept, read by the AI, and the count
        towards the next question starts again."""
        text = " ".join(str(explanation or "").split())[:600]
        if not text:
            return False
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE lessons SET explanation = ?, explained_by = ?, explained_at = ?, since_why = 0, retired = 0, "
                "updated_at = ? WHERE situation = ?",
                (text, actor or "", _now(), _now(), situation),
            )
            if cur.rowcount:
                self._log(conn, "lesson_explained", invoice_id, actor, {"situation": situation, "reason": text})
            return cur.rowcount > 0

    def retire_lesson(self, lesson_id: int, actor: str | None = None, retired: bool = True) -> bool:
        """A lesson no longer true (the vendor changed its invoices): not shown to the AI or the reviewer again."""
        with self._conn() as conn:
            cur = conn.execute("UPDATE lessons SET retired = ?, updated_at = ? WHERE id = ?",
                               (int(retired), _now(), lesson_id))  # fmt: skip
            if cur.rowcount:
                self._log(conn, "lesson_retired" if retired else "lesson_restored", actor=actor,
                          detail={"lesson": lesson_id})  # fmt: skip
            return cur.rowcount > 0

    # --- The AI reviewer's queue (ap_coder/ai/verifier.py) -----------------------------------------------------------

    def queue_verification(self, invoice_id: int, reason: str = "") -> bool:
        """Put an invoice in line for the AI reviewer: a new reading of it (the page reader's) is reviewed again, so
        one reviewed before goes back in line; one being reviewed now is left to finish (False)."""
        now = _now()
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO verifications (invoice_id, reason, status, created_at, updated_at)
                   VALUES (?, ?, 'waiting', ?, ?) ON CONFLICT(invoice_id) DO UPDATE SET reason = excluded.reason,
                   status = 'waiting', error = '', tries = 0, updated_at = excluded.updated_at,
                   created_at = CASE WHEN verifications.status = 'waiting' THEN verifications.created_at
                                ELSE excluded.created_at END
                   WHERE verifications.status != 'verifying' OR verifications.updated_at < ?""",
                (invoice_id, reason or "", now, now, _stale_before()),
            )
            return cur.rowcount > 0

    def next_verification(self, *, after_page_reader: bool = True) -> dict[str, Any] | None:
        """The invoice the AI reviewer takes next (the one waiting longest), marked as being reviewed; None when
        nothing waits. ``after_page_reader``: leave invoices still waiting for the page reader (it reads them first,
        then the reviewer reviews them as they then stand)."""
        now, stale = _now(), _stale_before()
        later = (
            " AND NOT EXISTS (SELECT 1 FROM page_reads p WHERE p.invoice_id = verifications.invoice_id "
            "AND p.status IN ('waiting', 'reading'))"
            if after_page_reader
            else ""
        )
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                f"SELECT invoice_id FROM verifications WHERE {_VERIFY_IN_LINE}{later} "
                "ORDER BY created_at, invoice_id LIMIT 1",
                (stale,),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE verifications SET status = 'verifying', error = '', updated_at = ? WHERE invoice_id = ?",
                (now, row[0]),
            )
            taken = conn.execute("SELECT * FROM verifications WHERE invoice_id = ?", (row[0],)).fetchone()
        return dict(taken)

    def finish_verification(
        self, invoice_id: int, status: str, *, result: dict[str, Any] | None = None, rounds: int = 0,
        seconds: float = 0.0, error: str = "", tries: int | None = None,
    ) -> bool:  # fmt: skip
        """How a review ended: ``done`` (``result``), ``failed`` / ``skipped`` (``error`` says why) or ``waiting``
        (back in line: the chat model isn't answering now). ``tries``: reviews the model server cut off before any
        answer (None keeps the count; a review done starts it over)."""
        if status not in (VERIFY_WAITING, VERIFY_DONE, VERIFY_FAILED, VERIFY_SKIPPED):
            raise ValueError(f"unknown review status {status!r}")
        if tries is None and status == VERIFY_DONE:
            tries = 0
        with self._conn() as conn:
            cur = conn.execute(
                """UPDATE verifications SET status = ?, result_json = COALESCE(?, result_json), rounds = ?,
                   seconds = ?, error = ?, updated_at = ?, tries = COALESCE(?, tries) WHERE invoice_id = ?""",
                (status, json.dumps(result, default=str) if result is not None else None,
                 int(rounds or 0), round(float(seconds or 0), 1), (error or "")[:2000], _now(), tries, invoice_id),
            )  # fmt: skip
            return cur.rowcount > 0

    def verification(self, invoice_id: int) -> dict[str, Any] | None:
        """The invoice's review: {invoice_id, reason, status, rounds, seconds, result, error, created_at,
        updated_at}, or None when it was never put in line."""
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM verifications WHERE invoice_id = ?", (invoice_id,)).fetchone()
        if row is None:
            return None
        found = dict(row)
        found["result"] = json.loads(found.pop("result_json") or "null")
        if found["status"] == VERIFY_RUNNING and found["updated_at"] < _stale_before():
            found["status"] = VERIFY_WAITING
        return found

    def verification_counts(self) -> dict[str, int]:
        """How many reviews are in each state, and how the ones done ended: {waiting, verifying, done, failed,
        skipped, agreed, disagrees, unsure}."""
        with self._conn() as conn:
            rows = conn.execute("SELECT status, COUNT(*) n FROM verifications GROUP BY status").fetchall()
            ends = conn.execute(
                "SELECT json_extract(result_json, '$.status') s, COUNT(*) n FROM verifications "
                "WHERE status = 'done' AND json_valid(result_json) GROUP BY s"
            ).fetchall()
        counts = dict.fromkeys((VERIFY_WAITING, VERIFY_RUNNING, VERIFY_DONE, VERIFY_FAILED, VERIFY_SKIPPED,
                                "agreed", "disagrees", "unsure"), 0)  # fmt: skip
        counts.update({r["status"]: int(r["n"]) for r in rows})
        counts.update({r["s"]: int(r["n"]) for r in ends if r["s"]})
        return counts

    def azure_pages_on(self, day: str | None = None) -> int:
        """The pages sent to Azure on ``day`` (YYYY-MM-DD, default today): what the daily cap counts."""
        day = day or dt.date.today().isoformat()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(pages), 0) FROM azure_checks WHERE status = 'done' "
                "AND substr(compared_at, 1, 10) = ?",
                (day,),
            ).fetchone()
        return int(row[0] or 0)

    def azure_checks_since(self, since_iso: str) -> list[dict[str, Any]]:
        """Every check done since ``since_iso``, newest first, with the invoice's vendor, number and status:
        {invoice_id, reason, pages, compared, agreed, result, compared_at, vendor_name, invoice_number,
        invoice_status}."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT a.invoice_id, a.reason, a.pages, a.compared, a.agreed, a.result_json, a.compared_at,
                   i.vendor_name, i.invoice_number, i.status invoice_status FROM azure_checks a
                   LEFT JOIN invoices i ON i.id = a.invoice_id WHERE a.status = 'done' AND a.compared_at >= ?
                   ORDER BY a.compared_at DESC, a.invoice_id DESC""",
                (since_iso,),
            ).fetchall()
        out = []
        for r in rows:
            item = dict(r)
            item["result"] = json.loads(item.pop("result_json") or "null")
            out.append(item)
        return out

    def azure_check_counts(self) -> dict[str, int]:
        """How many checks are in each state: {waiting, checking, done, failed, skipped}."""
        with self._conn() as conn:
            rows = conn.execute("SELECT status, COUNT(*) n FROM azure_checks GROUP BY status").fetchall()
        counts = dict.fromkeys((AZURE_WAITING, AZURE_CHECKING, AZURE_DONE, AZURE_FAILED, AZURE_SKIPPED), 0)
        counts.update({r["status"]: int(r["n"]) for r in rows})
        return counts

    def update_after_azure_check(
        self, invoice_id: int, capture: dict[str, Any] | None, issue: dict[str, Any] | None, code: str,
        meta: dict[str, Any] | None = None, *, expect: tuple[Any, ...] | None = None,
    ) -> bool:  # fmt: skip
        """Fold an Azure check into an invoice still open: ``update_after_check``."""
        return self.update_after_check(invoice_id, capture, issue, (code,), meta, expect=expect)

    def update_after_check(
        self, invoice_id: int, capture: dict[str, Any] | None, issue: dict[str, Any] | None, codes: tuple[str, ...],
        meta: dict[str, Any] | None = None, *, expect: tuple[Any, ...] | None = None,
    ) -> bool:  # fmt: skip
        """Fold a check made after processing (the Azure check, the AI reviewer) into an invoice still open (in review
        or parked): the capture (fields read differently marked Check), the review issues ``codes`` replaced by
        ``issue`` (None removes them) and ``meta`` merged into the processing notes. Nothing changes on an invoice
        approved, rejected or deleted (False), nor on one that is no longer ``expect`` (``row_identity``: a backup
        was restored meanwhile)."""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT status, validation, meta, file_sha256, created_at FROM invoices WHERE id = ?",
                               (invoice_id,)).fetchone()  # fmt: skip
            if row is None or row["status"] not in (REVIEW, PARKED) or not _same_row(row, expect):
                return False
            validation = json.loads(row["validation"] or "null") or {}
            issues = [i for i in validation.get("issues") or [] if i.get("code") not in codes]
            if issue:
                issues.append(issue)
                validation["requires_review"] = True
            if validation:
                validation["issues"] = issues
            merged = {**(json.loads(row["meta"] or "null") or {}), **(meta or {})}
            conn.execute(
                "UPDATE invoices SET validation = ?, meta = ?, requires_review = CASE WHEN ? THEN 1 "
                "ELSE requires_review END WHERE id = ?",
                (json.dumps(validation) if validation else None, json.dumps(merged, default=str), int(bool(issue)),
                 invoice_id),
            )  # fmt: skip
            if capture is not None:
                self._save_capture(conn, invoice_id, capture)
        return True

    def replace_proposal(
        self, invoice_id: int, output: dict[str, Any], report: Any, capture: Any, meta: dict[str, Any] | None = None,
        actor: str = "AP Coder", *, expect: tuple[Any, ...] | None = None,
    ) -> bool:  # fmt: skip
        """Replace what AP Coder proposes for an invoice nobody has worked on yet (the page reader read it after
        it was processed): the coding, its checks and confidence, the columns the queue shows, the capture
        (``CaptureResult`` or its dict; None keeps the stored one), and ``meta`` merged into the processing
        notes. ``report``: the ``ValidationReport`` or its dict.

        Only while the invoice waits in the review queue untouched: no edits recorded and never approved (a
        reopened invoice keeps the reviewer's coding), and still ``expect`` (``row_identity``, taken before the page
        reader read it: a backup restored meanwhile may hold another invoice under the same number). Otherwise
        nothing changes and it returns False."""
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
                "SELECT status, edits, final_output, reviewer, ai_output, meta, file_sha256, created_at FROM invoices "
                "WHERE id = ?",
                (invoice_id,),
            ).fetchone()
            if (row is None or row["status"] != REVIEW or row["final_output"] or row["reviewer"]
                    or json.loads(row["edits"] or "null") or not _same_row(row, expect)):  # fmt: skip
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
        actor: str | None = None, qst_number: str | None = None, pst_number: str | None = None,
        province: str | None = None,
    ) -> None:  # fmt: skip
        """Add or change a vendor. QST, PST and province left None keep what the vendor has."""
        if status not in ("active", "on_hold"):
            raise ValueError(f"unknown vendor status {status!r}")
        before = self.get_vendor(key) or {}
        values = {"status": status, "expected_gst": expected_gst.strip(), "notes": notes.strip()}
        extra = {"qst_number": qst_number, "pst_number": pst_number, "province": province}
        values.update({k: str(v).strip().upper() if k == "province" else str(v).strip()
                       for k, v in extra.items() if v is not None})  # fmt: skip
        keep = {k: before.get(k) or "" for k in extra if extra[k] is None}
        row = {**keep, **values}
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO vendors (vendor_key, display_name, status, expected_gst, notes, updated_at, qst_number,
                   pst_number, province)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(vendor_key) DO UPDATE SET
                   display_name = excluded.display_name, status = excluded.status,
                   expected_gst = excluded.expected_gst, notes = excluded.notes, updated_at = excluded.updated_at,
                   qst_number = excluded.qst_number, pst_number = excluded.pst_number, province = excluded.province""",
                (key, display_name, status, values["expected_gst"], values["notes"], _now(), row["qst_number"],
                 row["pst_number"], row["province"]),
            )  # fmt: skip
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
                for field in ("erp_id", "gst", "terms", "default_gl", "qst", "pst", "province"):
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
                       terms, default_gl, in_master, qst_number, pst_number, province)
                       VALUES (?, ?, ?, ?, '', ?, ?, ?, ?, 1, ?, ?, ?)
                       ON CONFLICT(vendor_key) DO UPDATE SET
                       qst_number = CASE WHEN excluded.qst_number != '' THEN excluded.qst_number
                                    ELSE vendors.qst_number END,
                       pst_number = CASE WHEN excluded.pst_number != '' THEN excluded.pst_number
                                    ELSE vendors.pst_number END,
                       province = CASE WHEN excluded.province != '' THEN excluded.province ELSE vendors.province END,
                       erp_id = CASE WHEN ? THEN excluded.erp_id ELSE vendors.erp_id END,
                       terms = CASE WHEN ? THEN excluded.terms ELSE vendors.terms END,
                       default_gl = CASE WHEN ? THEN excluded.default_gl ELSE vendors.default_gl END,
                       in_master = 1, updated_at = excluded.updated_at,
                       expected_gst = CASE WHEN excluded.expected_gst != '' THEN excluded.expected_gst
                                      ELSE vendors.expected_gst END,
                       status = CASE WHEN ? THEN excluded.status ELSE vendors.status END""",
                    (key, row["vendor_name"], row.get("status") or "active", row.get("gst") or "", now,
                     row.get("erp_id") or "", row.get("terms") or "", _clean_code(row.get("default_gl")),
                     row.get("qst") or "", row.get("pst") or "", row.get("province") or "",
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

    def our_province(self) -> str:
        """Where invoices are delivered to us when they print no ship-to (Settings → Review), "" if not set."""
        return self.get_setting("our_province").strip().upper()

    def site_provinces(self) -> dict[str, str]:
        """{cost center: province} for sites in other provinces than ours (Settings → Review: "CC400=BC" per line)."""
        out: dict[str, str] = {}
        for line in re.split(r"[\n,;]+", self.get_setting("site_provinces")):
            code, sep, province = line.partition("=")
            if sep and code.strip() and province.strip():
                out[code.strip()] = province.strip().upper()
        return out

    def vendor_choices(self) -> list[dict[str, Any]]:
        """The vendors a reviewer picks from on the review screen: the vendor master's and the ones added there; with
        no vendor master, every vendor with an approved invoice too. Sorted by name."""
        with self._conn() as conn:
            rows = [dict(r) for r in conn.execute("SELECT * FROM vendors WHERE in_master IN (1, 2)")]
            if not any(r["in_master"] == 1 for r in rows):
                known = {r["vendor_key"] for r in rows}
                for r in conn.execute(
                    "SELECT vendor_key, MAX(vendor_name) name FROM invoices WHERE status = 'approved' AND vendor_key "
                    "!= '' GROUP BY vendor_key"
                ):
                    if r["vendor_key"] not in known:
                        rows.append({"vendor_key": r["vendor_key"], "display_name": r["name"], "in_master": 0})
        return sorted(rows, key=lambda r: str(r.get("display_name") or "").lower())

    def add_vendor_here(
        self, name: str, *, gst: str = "", qst: str = "", pst: str = "", province: str = "", actor: str | None = None,
        invoice_id: int | None = None,
    ) -> str:  # fmt: skip
        """A new vendor added to the vendor list from the review screen (``vendors.ADDED_HERE``): every invoice from
        it needs a person until it is in the vendor master imported from the ERP. Returns its key. A vendor already
        in the list is refused (ValueError)."""
        key = vendor_key(name)
        if not key:
            raise ValueError("Type the vendor's name.")
        before = self.get_vendor(key) or {}
        if before.get("in_master"):
            raise ValueError(f"{before.get('display_name') or name} is already in the vendor list: pick it.")
        note = f"Added by {actor or 'a reviewer'} on {_now()[:10]}"
        note += f" from invoice #{invoice_id}." if invoice_id else "."
        notes = "\n".join(n for n in (note, str(before.get("notes") or "").strip()) if n)
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO vendors (vendor_key, display_name, status, expected_gst, notes, updated_at, qst_number,
                   pst_number, province, in_master) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 2)
                   ON CONFLICT(vendor_key) DO UPDATE SET display_name = excluded.display_name,
                   expected_gst = excluded.expected_gst, notes = excluded.notes, updated_at = excluded.updated_at,
                   qst_number = excluded.qst_number, pst_number = excluded.pst_number, province = excluded.province,
                   in_master = 2""",
                (key, name.strip(), before.get("status") or "active", gst.strip(), notes, _now(), qst.strip(),
                 pst.strip(), province.strip().upper()),
            )  # fmt: skip
            self._log(conn, "vendor_added", invoice_id=invoice_id, actor=actor, detail={
                "vendor": name.strip(), "gst": gst.strip(), "qst": qst.strip(), "pst": pst.strip(),
                "province": province.strip().upper()})  # fmt: skip
        return key

    def delete_vendors(self, keys: list[str], actor: str | None = None) -> int:
        """Remove vendors from the vendor list (e.g. the demo's sample vendor master), logged: an on-hold vendor
        removed lifts its hold."""
        where, params = _in_list("vendor_key"), (json.dumps(list(keys)),)
        with self._conn() as conn:
            names = [r[0] or r[1] for r in conn.execute(f"SELECT display_name, vendor_key FROM vendors WHERE {where}",
                                                         params)]  # fmt: skip
            count = conn.execute(f"DELETE FROM vendors WHERE {where}", params).rowcount
            if count:
                self._log(conn, "vendors_deleted", actor=actor, detail={"vendors": names})
            return count

    def has_vendor_master(self) -> bool:
        with self._conn() as conn:
            return conn.execute("SELECT 1 FROM vendors WHERE in_master = 1 LIMIT 1").fetchone() is not None

    def master_vendors(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM vendors WHERE in_master = 1")]

    def vendor_ids(self) -> dict[str, str]:
        """{vendor_key: ERP vendor ID} from the vendor master, plus {"gst:<number>": ERP vendor ID} so an
        invoice whose vendor name differs but whose GST/HST number matches still gets its ID."""
        from ..coding.vendors import norm_tax_number

        with self._conn() as conn:
            rows = conn.execute("SELECT vendor_key, erp_id, expected_gst FROM vendors WHERE erp_id != ''").fetchall()
        ids = {r[0]: r[1] for r in rows}
        for r in rows:
            number = norm_tax_number(r[2])
            if sum(c.isdigit() for c in number) >= 9:
                ids.setdefault(f"gst:{number}", r[1])
                if number[:9].isdigit():  # bn: the master keeps only the Business Number; bnx: the full account
                    ids.setdefault(f"{'bn' if len(number) == 9 else 'bnx'}:{number[:9]}", r[1])
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
            r["qst_number"] = doc.get("qst_registration_number") or ""
            r["pst_number"] = doc.get("pst_registration_number") or ""
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
                    """SELECT vendor_key, COUNT(*) lessons, SUM(outcome NOT IN ('history', 'coded')) lines,
                              SUM(outcome = 'accepted') accepted FROM feedback GROUP BY vendor_key"""
                )
            }
            masters = {r["vendor_key"]: dict(r) for r in conn.execute("SELECT * FROM vendors")}
            spend: dict[str, dict[str, float]] = {}
            for r in conn.execute(
                """SELECT vendor_key, COALESCE(NULLIF(UPPER(TRIM(currency)), ''), 'CAD') currency,
                          SUM(grand_total) total
                   FROM invoices WHERE vendor_key != '' AND status = 'approved' GROUP BY vendor_key, 2"""
            ):
                spend.setdefault(r["vendor_key"], {})[r["currency"]] = round(r["total"] or 0, 2)
        rates = self.fx_rates()
        for r in rows:
            # Per currency: dollars and euros do not add up, and a USD-only vendor did not spend 0.00 CAD.
            r["spend"] = dict(sorted(spend.get(r["vendor_key"], {}).items()))
            # In CAD at the Settings rates (for sorting; a currency without a rate counts at face value).
            r["spend_in_cad"] = round(sum(t * rates.get(c, 1.0) for c, t in r["spend"].items()), 2)
            fb = lessons.get(r["vendor_key"]) or {}
            r["lessons"] = fb.get("lessons", 0)
            r["accuracy"] = (fb["accepted"] / fb["lines"]) if fb.get("lines") else None
            master = masters.get(r["vendor_key"]) or {}
            r["status"] = master.get("status", "active")
            r["expected_gst"] = master.get("expected_gst", "")
            for field in ("qst_number", "pst_number", "province"):
                r[field] = master.get(field) or ""
            r["notes"] = master.get("notes", "")
            for field in ("erp_id", "terms", "default_gl"):
                r[field] = master.get(field) or ""
            r["in_master"] = master.get("in_master") == 1
            r["added_here"] = master.get("in_master") == 2
            r["has_record"] = bool(master)  # in the vendor list, added here or set up by AP (on hold, notes)
        return sorted(rows, key=lambda r: -r["spend_in_cad"])

    def vendor_name_checks(self, ids: list[int]) -> dict[int, str]:
        """{invoice id: how sure the capture is of its vendor name} ("verified", "likely", "check", "missing"), in
        one query; an invoice with no capture is left out (the review queue's "check" mark, B-471)."""
        if not ids:
            return {}
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT invoice_id, json_extract(capture_json, '$.fields.vendor_name.status') FROM invoice_capture "
                f"WHERE {_in_list('invoice_id')}",
                (json.dumps([int(i) for i in ids]),),
            ).fetchall()
        return {int(r[0]): str(r[1] or "") for r in rows}

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
        """A consistent copy of the database (safe while the dashboard is running). Written under a temporary name
        and renamed once complete: a copy that fails (a full disk, a folder that cannot be written) leaves nothing
        that looks like a backup, and raises OSError, as a file that cannot be written does (B-453)."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        part.unlink(missing_ok=True)
        try:
            source = sqlite3.connect(self.path)
            try:
                target = sqlite3.connect(part)
                try:
                    source.backup(target)
                    # One self-contained file (not WAL, which needs -wal / -shm files beside it): it opens and
                    # restores from anywhere, also a read-only network share or USB stick.
                    target.execute("PRAGMA journal_mode=DELETE")
                finally:
                    target.close()
            finally:
                source.close()
            part.replace(dest)
        except (sqlite3.Error, OSError) as exc:
            for leftover in (part, part.with_name(part.name + "-journal")):
                try:
                    leftover.unlink(missing_ok=True)
                except OSError:
                    pass
            if isinstance(exc, OSError):
                raise
            raise OSError(f"the backup {dest.name} could not be written ({exc})") from exc
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
        for stale in self.backup_dir().glob("ap_coder-*.db.part"):  # left by a crash (B-453): gone after a day
            try:
                if time.time() - stale.stat().st_mtime > 86400:
                    stale.unlink()
            except OSError:
                pass
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
        now = dt.datetime.now()
        for backup in daily:  # one made in the future (the clock was ahead) is old: it never stops the daily backups
            try:
                age = now - dt.datetime.fromtimestamp(backup.stat().st_mtime)
            except OSError:
                continue
            if dt.timedelta(0) <= age < dt.timedelta(hours=min_hours):
                return None
        if not self.path.exists():
            return None
        made = self.backup_now()
        for old in [p for p in self.list_backups() if p.stem.count("-") == 2][keep:]:
            old.unlink(missing_ok=True)
        return made

    @staticmethod
    @contextmanager
    def _local_copy(backup: str | Path) -> Iterator[Path]:
        """A copy of ``backup`` in a temporary folder on this computer, to open: nothing is ever written next to the
        backup itself (SQLite would, for a WAL-format one), which may be on a read-only share or a UNC path."""
        import tempfile

        path = Path(backup)
        with tempfile.TemporaryDirectory(prefix="ap_coder-backup-", ignore_cleanup_errors=True) as folder:
            copy = Path(folder) / "backup.db"
            try:
                shutil.copyfile(path, copy)
                wal = path.with_name(path.name + "-wal")  # a database still open elsewhere: its latest changes
                if wal.is_file():
                    shutil.copyfile(wal, copy.with_name(copy.name + "-wal"))
            except OSError as exc:
                raise ValueError(f"{path.name} could not be opened ({exc.strerror or exc})") from None
            yield copy

    @staticmethod
    def check_backup(backup: str | Path) -> None:
        """Raise ValueError (in plain words) unless ``backup`` is an AP Coder database this version can open:
        a ``schema_version`` setting no newer than this version's, and no triggers or views (AP Coder makes
        none; one in a planted file would run on every later change)."""
        with Store._local_copy(backup) as copy:
            Store._check_backup_file(copy, Path(backup).name)

    @staticmethod
    def _check_backup_file(path: Path, name: str) -> None:
        try:
            conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            raise ValueError(f"{name} could not be opened ({exc})") from None
        try:
            kinds = {r[0] for r in conn.execute("SELECT type FROM sqlite_master WHERE type IN ('trigger', 'view')")}
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            row = (
                conn.execute("SELECT value FROM settings WHERE key = 'schema_version'").fetchone()
                if "settings" in tables
                else None
            )
            columns = {r[1] for r in conn.execute("PRAGMA table_info(invoices)")}
        except sqlite3.DatabaseError:
            raise ValueError(f"{name} is not an AP Coder database (or it is damaged)") from None
        finally:
            conn.close()
        if kinds:
            raise ValueError(f"{name} contains {' and '.join(sorted(kinds))}s, which AP Coder never makes")
        # No schema_version row: a database older than the setting (the update's own safety copy of one, made
        # before migrating) is version 1 and has its shape (invoices without export_batch, added in version 3);
        # a newer one without the row is not one AP Coder made.
        first = row is None and "export_batch" not in columns
        if not {"invoices", "feedback"} <= tables or (row is None and not first):
            raise ValueError(f"{name} is not an AP Coder database")
        try:
            version = 1 if first else int(row[0])
        except (TypeError, ValueError):
            raise ValueError(f"{name} is not an AP Coder database") from None
        if version > SCHEMA_VERSION:
            raise ValueError(f"{name} was made by a newer version of AP Coder: update AP Coder first")

    def row_identity(self, invoice_id: int) -> tuple[Any, ...] | None:
        """What tells this invoice from another one under the same number (its file's SHA-256 and when it came in), or
        None when there is none: a background worker takes it before its long work and writes its result only onto
        the same invoice (``expect``), not onto whatever a restored backup holds under that number."""
        with self._conn() as conn:
            row = conn.execute("SELECT file_sha256, created_at FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        return (row["file_sha256"], row["created_at"]) if row else None

    def restoring(self) -> bool:
        """A backup is being restored into this database (in this process): the background workers wait."""
        with _RESTORING_LOCK:
            return str(self.path.resolve()) in _RESTORING

    def restore_from(self, backup: str | Path) -> Path:
        """``_restore_from`` with the background workers paused meanwhile (``restoring``)."""
        key = str(self.path.resolve())
        with _RESTORING_LOCK:
            _RESTORING.add(key)
        try:
            return self._restore_from(backup)
        finally:
            with _RESTORING_LOCK:
                _RESTORING.discard(key)

    def _restore_from(self, backup: str | Path) -> Path:
        """Replace the database with a backup. The current one is backed up first (returned).
        Raises ValueError, with nothing changed, when the file is not an AP Coder backup (``check_backup``).
        The second backup folder is cleared after a restore: a backup file must not choose where later
        backups are copied (set it again in Settings)."""
        with self._local_copy(backup) as copy:  # read from this computer, never written next to the backup
            self._check_backup_file(copy, Path(backup).name)
            copy_dir = self.get_setting("backup_copy_dir").strip()
            safety = self.backup_now("before-restore")
            source = sqlite3.connect(copy)
            target = sqlite3.connect(self.path)
            try:
                source.backup(target)
            finally:
                target.close()
                source.close()
        with self._conn() as conn:
            conn.executescript(_SCHEMA)  # an older backup may lack newer tables...
            self._migrate(conn)  # ...and columns
            restored_dir = conn.execute("SELECT value FROM settings WHERE key = 'backup_copy_dir'").fetchone()
            conn.execute("DELETE FROM settings WHERE key IN ('backup_copy_dir', 'backup_copy_status')")
            detail = {"file": Path(backup).name, "safety_copy": safety.name}
            if copy_dir or (restored_dir and str(restored_dir[0]).strip()):
                detail["note"] = "second backup folder cleared: set it again in Settings (Data & backups)"
            self._log(conn, "backup_restored", detail=detail)
        mine = str(self.path.resolve())
        with _FEEDBACK_LOCK:  # the lessons kept for the database as it was
            for key in [k for k in _FEEDBACK if k[0] == mine]:
                _FEEDBACK.pop(key, None)
        return safety

    # --- Learning memory ------------------------------------------------------------------------------

    def feedback_rows(self, limit: int | None = None, vendor_name: str | None = None) -> list[dict[str, Any]]:
        """Recorded reviewer decisions, newest first (only one vendor's when ``vendor_name`` is given), at most
        ``limit`` (``FEEDBACK_READ``). Read once and kept until a lesson is added or removed: every invoice read asks
        for them, and years of imported ERP history take a third of a second to load on a laptop. The list is
        shared: read it, don't change it."""
        limit = FEEDBACK_READ if limit is None else limit
        sql, args = "SELECT * FROM feedback", ()
        if vendor_name is not None:
            sql, args = sql + " WHERE vendor_key = ?", (vendor_key(vendor_name),)
        with self._conn() as conn:
            # Lessons are only ever added or deleted, never changed: the highest id, the count and the newest lesson
            # itself tell whether the ones kept are still the ones in the database. (The id alone is not enough: a
            # database restored from a backup, or made again where one was deleted, gives its next lesson an id a
            # lesson it no longer has once had.)
            stamp = tuple(conn.execute(
                "SELECT MAX(id), COUNT(*), (SELECT created_at || '|' || COALESCE(vendor_key, '') || '|' || "
                "COALESCE(description, '') || '|' || COALESCE(final_gl, '') FROM feedback ORDER BY id DESC LIMIT 1) "
                "FROM feedback"
            ).fetchone())  # fmt: skip
            key = (str(self.path.resolve()), limit, vendor_name)
            with _FEEDBACK_LOCK:
                kept = _FEEDBACK.get(key)
                if kept is not None and kept[0] == stamp:
                    return kept[1]
            # Reviewer decisions first, then imported history: a big history import never pushes them out.
            rows = [
                dict(r) for r in conn.execute(sql + " ORDER BY outcome = 'history', id DESC LIMIT ?", (*args, limit))
            ]
        if vendor_name is None:  # one vendor's lessons are quick to read: only the whole memory is kept
            with _FEEDBACK_LOCK:
                _FEEDBACK[key] = (stamp, rows)
                while len(_FEEDBACK) > 8:
                    _FEEDBACK.pop(next(iter(_FEEDBACK)))
        return rows

    def learning_rows(self, vendor_name: str | None) -> list[dict[str, Any]]:
        """``feedback_rows()`` with every lesson of this vendor in it: what an invoice from it is coded and checked
        with. Past ``FEEDBACK_READ`` lessons the whole memory holds only the newest, so a vendor whose history was
        imported first would have none of its own; its older lessons then follow the shared list (in a new list,
        the rows themselves shared). The shared list itself while it holds them all."""
        rows = self.feedback_rows()
        if not vendor_name or len(rows) < FEEDBACK_READ:
            return rows
        key = vendor_key(vendor_name)
        have = {r["id"] for r in rows if r["vendor_key"] == key}
        missing = [r for r in self.feedback_rows(vendor_name=vendor_name) if r["id"] not in have]
        return rows + missing if missing else rows

    def import_history(self, rows: list[dict[str, Any]], actor: str | None = None) -> dict[str, int]:
        """Past AP coding from the ERP (``history.rows_from_records``) into the learning memory, as history.
        Rows already imported (same vendor, description, GL, cost center, amount and date) are skipped."""
        from ..ai.history import HISTORY

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
            daily = conn.execute(
                """SELECT substr(created_at, 1, 10) day,
                          SUM(outcome = 'accepted') accepted, SUM(outcome = 'corrected') corrected
                   FROM feedback WHERE outcome NOT IN ('history', 'coded') GROUP BY day"""
            ).fetchall()
            by_vendor = conn.execute(
                """SELECT vendor_name, COUNT(*) lines, SUM(outcome = 'accepted') accepted,
                          SUM(outcome = 'corrected') corrected, MAX(created_at) last_seen
                   FROM feedback WHERE outcome NOT IN ('history', 'coded') GROUP BY vendor_key ORDER BY lines DESC"""
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
            coded_by_hand = conn.execute(
                "SELECT COUNT(*) FROM invoices WHERE status = ? AND edits LIKE '%\"line_coded\"%'", (APPROVED,)
            ).fetchone()[0]
        # ISO weeks (Monday to Sunday, "2026-W41"), as on the Insights page: SQLite's %W numbers them differently.
        weeks: dict[str, dict[str, Any]] = {}
        for r in daily:
            week = weeks.setdefault(_iso_week(r["day"]), {"week": _iso_week(r["day"]), "accepted": 0, "corrected": 0})
            week["accepted"] += r["accepted"] or 0
            week["corrected"] += r["corrected"] or 0
        outcome = {r["outcome"]: r["n"] for r in lines}
        # Every line AP reviewed (and AP Coder learns from), the ones it coded over a guess too (B-479); the accuracy
        # measures only what the AI proposed: confirmed or corrected (B-475).
        measured = outcome.get(ACCEPTED, 0) + outcome.get(CORRECTED, 0)
        return {
            "lines_reviewed": sum(outcome.values()),
            "lines_measured": measured,
            "lines_coded": outcome.get(CODED, 0),
            "lines_accepted": outcome.get(ACCEPTED, 0),
            "lines_corrected": outcome.get(CORRECTED, 0),
            "line_accuracy": round(outcome.get(ACCEPTED, 0) / measured, 4) if measured else None,
            "invoices_by_status": {r["status"]: r["n"] for r in statuses},
            "invoices_approved_without_edits": untouched,
            "invoices_coded_by_hand": coded_by_hand,
            "weekly": [weeks[w] for w in sorted(weeks)],
            "by_vendor": [dict(r) for r in by_vendor],
            "top_corrections": [dict(r) for r in corrections],
        }


def _same_row(row: sqlite3.Row, expect: tuple[Any, ...] | None) -> bool:
    """``row`` (with ``file_sha256`` and ``created_at``) is the invoice ``expect`` names (``Store.row_identity``)."""
    return expect is None or (row["file_sha256"], row["created_at"]) == tuple(expect)


def _iso_week(day: str | None) -> str:
    """'2026-10-10' -> '2026-W41' (the ISO week, as Insights shows it); '?' for a date that cannot be read."""
    try:
        year, week, _ = dt.date.fromisoformat((day or "")[:10]).isocalendar()
    except ValueError:
        return "?"
    return f"{year}-W{week:02d}"


def _tax_signature(output: dict[str, Any]) -> list[tuple[str, float]]:
    return sorted((t.get("tax_type", ""), round(float(t.get("tax_amount", 0)), 2)) for t in output.get("tax_lines", []))


SAMPLE_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


def load_sample_setup(store: Store, data_dir: Path = SAMPLE_DATA_DIR, cost_centers: bool = True) -> None:
    """Load the bundled sample GL accounts, tax mapping and policy (for trying things out), and with ``cost_centers``
    the sample cost centers. The dashboard's "Use the sample GL accounts" and the demo leave the cost centers out: the
    pilot codes each line to a GL account only (D-53); a cost center list can still be imported later."""
    import csv

    tables = [("gl_accounts", "chart_of_accounts.csv", "gl_code")]
    if cost_centers:
        tables.append(("cost_centers", "cost_centers.csv", "cost_center"))
    for table, filename, code_col in tables:
        with (data_dir / filename).open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        store.import_accounts(table, rows, code_col, "description", "category")
    with (data_dir / "tax_gl_mapping.csv").open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            store.set_tax_treatment(row["tax_type"], row["treatment"], row.get("gl_code") or "")
    store.set_setting("policy_notes", (data_dir / "coding_policy.md").read_text(encoding="utf-8-sig"))


def load_sample_cost_centers(store: Store, data_dir: Path = SAMPLE_DATA_DIR) -> None:
    """Load the bundled sample cost centers on their own: the list a company that codes to cost centers imports
    later (the sample setup leaves them out, D-53)."""
    import csv

    with (data_dir / "cost_centers.csv").open(encoding="utf-8-sig", newline="") as fh:
        store.import_accounts("cost_centers", list(csv.DictReader(fh)), "cost_center", "description", "category")


def load_sample_vendor_master(store: Store, data_dir: Path = SAMPLE_DATA_DIR, only_new: bool = False) -> list[str]:
    """Load the bundled sample vendor master (the demo vendors but one). Returns the vendor keys (with
    ``only_new``: only those added, existing vendor records being left as they are)."""
    import csv

    from ..coding.vendors import master_columns, master_rows

    with (data_dir / "vendor_master.csv").open(encoding="utf-8-sig", newline="") as fh:
        records = list(csv.DictReader(fh))
    rows = master_rows(records, master_columns(list(records[0]) if records else []))
    result = store.import_vendor_master(rows, only_new=only_new)
    return sorted(result["inserted"]) if only_new else sorted({vendor_key(r["vendor_name"]) for r in rows})


def load_sample_purchase_orders(store: Store, data_dir: Path = SAMPLE_DATA_DIR) -> list[str]:
    """Load the bundled sample purchase orders (they match the sample invoices). Returns the PO keys."""
    import csv

    from ..coding.po import map_columns, rows_from_records

    with (data_dir / "purchase_orders.csv").open(encoding="utf-8-sig", newline="") as fh:
        records = list(csv.DictReader(fh))
    rows, _ = rows_from_records(records, map_columns(list(records[0]) if records else []))
    store.import_purchase_orders(rows)
    return sorted({po_key(r["po_number"]) for r in rows})
