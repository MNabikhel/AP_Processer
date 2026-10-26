"""The store's foundation (D-69): the schema (``_SCHEMA``, ``SCHEMA_VERSION``), the migrations of an older database
(``StoreCore._migrate``), the connections (``StoreCore._conn``, ``StoreCore.session``) and the helpers the other parts
share: the statuses and queues' names, ``_now``, ``_in_list``, ``fold_text`` (the SQL function ``ap_fold``), and what
an approval counts as coded, kept or guessed (``suggestion_kept``, ``proposed_coding``, ``line_guessed``)."""

from __future__ import annotations

import contextvars
import datetime as dt
import json
import re
import sqlite3
import threading
import unicodedata
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ...coding.po import po_key
from ...coding.terms import DEFAULT_TERMS_DAYS, payment
from ...coding.vendors import norm_invoice_number
from ..memory import CODED, CORRECTED, VENDOR_KEY_RULES, pair_lines, refresh_vendor_key, vendor_key
from ..paths import default_db_path, private_dir  # noqa: F401  (private_dir: re-exported by the package)
from ..reference_data import UNASSIGNED
from .fx import _FX_RATE, APPROVAL_CONTROLS, parse_fx_rates, tiny_fx_rates, unread_fx_text  # noqa: F401  (re-exported)

# 10: erp_invoices, 11: coding_rules (by _SCHEMA), 12: currency, 13: credit notes not due,
# 14: invoice_capture, supplier_profiles, supplier_outcomes (by _SCHEMA), 15: reader_outcomes, page_reads (by _SCHEMA),
# 16: bank_accounts, our company's bank accounts (by _SCHEMA, D-66), 17: feedback.final_tax_code, tax_source,
# tax_province and tax_uncollected (filled from the approved invoices, D-70, D-80), template_snapshots (D-85),
# invoice_drafts (by _SCHEMA: the review screen's edits not approved yet, kept over a restart, D-86),
# 18: users, sessions, login_attempts (by _SCHEMA: server mode's accounts, D-97), later vendors.erp_bank (D-104)
SCHEMA_VERSION = 19  # 19: the invoices' summary and its triggers (summary.py, ``_summary_migrate``, D-110)
# The one-off clean-ups of the learning memory ``_migrate`` has run on a database (settings.feedback_cleanup): a new
# clean-up added there changes it, so it runs once on every database.
FEEDBACK_CLEANUP = "1"
ACCOUNT_TABLES = {"gl_accounts": "gl_code", "cost_centers": "cost_center"}

REVIEW, APPROVED, REJECTED, FAILED = "review", "approved", "rejected", "failed"
PENDING = "pending_approval"  # approved once, over the approval limit: waiting for a second approver
PARKED = "parked"  # out of the queue while waiting for information (a buyer, a credit note...)
ACTIVE_STATUSES = (REVIEW, PARKED, PENDING, APPROVED)  # invoices that count (for POs, recurring vendors...)
_ACTIVE_IN = "(" + ", ".join("?" * len(ACTIVE_STATUSES)) + ")"


# The key of a stored validation that holds the stamp of what it was checked against (``Store.checks_stamp``, B-815).
CHECKS_STAMP_KEY = "checks_stamp"


def _in_list(column: str) -> str:
    """``column IN`` a list bound as ONE parameter (a JSON array): a list of any length, where one ``?`` per item
    fails past the SQLite build's limit (999 on older builds, 32,766 on newer)."""
    return f"{column} IN (SELECT value FROM json_each(?))"


# An invoice file looked for and not found (``Store.invoice_file``) is looked for again after this long, not on every
# page picture the review screen asks for.
_LOOK_AGAIN_SECONDS = 30.0
_NOT_FOUND: dict[tuple[str, int, str], float] = {}
# Invoice files found where they were recorded, and when: an invoice read is not checked again for this long.
_FOUND: dict[str, float] = {}

# Databases being restored in this process (``Store.restore_from``): the background workers (page reader, Azure
# check) start nothing on them meanwhile (``Store.restoring``).
_RESTORING: set[str] = set()
_RESTORING_LOCK = threading.Lock()

# The learning memory as last read, per database: (highest lesson id and count, rows) (``Store.feedback_rows``).
_FEEDBACK: dict[tuple[str, int, str | None], tuple[tuple[Any, ...], list[dict[str, Any]]]] = {}
_FEEDBACK_LOCK = threading.Lock()
# The learning memory's part of ``Store.metrics`` as last counted, per database: (its stamp, the counts).
_METRICS: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {}


def _forget_learning(path: Path) -> None:
    """The learning memory kept for this database (``feedback_rows``, ``metrics``) dropped: an upgrade changed lessons
    in place, which the stamps (lessons are otherwise only added or removed) don't see."""
    key = str(path.resolve())
    with _FEEDBACK_LOCK:
        for kept in (_FEEDBACK, _METRICS):
            for k in [k for k in kept if (k[0] if isinstance(k, tuple) else k) == key]:
                kept.pop(k, None)


FEEDBACK_READ = 50_000  # lessons read for the whole memory, at most (newest first); one vendor's are added past it

# Touchless processing (Settings → Automation): one switch for the company, "on" or "off" (off when not set), and
# the largest invoice total approved without a person, in CAD.
# A supplier's template as it was before each of its last few approvals taught it (``template_snapshots``): put back
# when that approval is withdrawn and nothing has changed the template since (D-71).
KEEP_SNAPSHOTS = 5

TOUCHLESS_SETTING, TOUCHLESS_LIMIT_SETTING = "touchless_processing", "touchless_limit"
DEFAULT_TOUCHLESS_LIMIT = 5000.0

# In the order of approval (``seq``), whatever the clock says (B-780).
_SNAPSHOTS_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS template_snapshots (seq INTEGER PRIMARY KEY AUTOINCREMENT, "
    "invoice_id INTEGER NOT NULL UNIQUE, supplier_key TEXT NOT NULL, before_json TEXT, after_json TEXT, "
    "at TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS template_snapshots_supplier ON template_snapshots (supplier_key, seq)",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS gl_accounts (
    code TEXT PRIMARY KEY, description TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL, meals INTEGER NOT NULL DEFAULT 0);
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
    outcome TEXT NOT NULL, reviewer TEXT, created_at TEXT NOT NULL, final_tax_code TEXT NOT NULL DEFAULT '',
    tax_source TEXT NOT NULL DEFAULT '', tax_province TEXT NOT NULL DEFAULT '',
    tax_uncollected TEXT NOT NULL DEFAULT '');
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
CREATE TABLE IF NOT EXISTS bank_accounts (code TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '',
    currency TEXT NOT NULL DEFAULT 'CAD', institution TEXT NOT NULL DEFAULT '', transit TEXT NOT NULL DEFAULT '',
    account_number TEXT NOT NULL DEFAULT '', gl_account TEXT NOT NULL DEFAULT '', is_default INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS invoice_drafts (invoice_id INTEGER NOT NULL, person TEXT NOT NULL DEFAULT '',
    draft TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (invoice_id, person));
CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL DEFAULT '', role TEXT NOT NULL DEFAULT 'clerk', pw_hash TEXT NOT NULL, active INTEGER
    NOT NULL DEFAULT 1, must_change INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, last_login TEXT);
CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created_at REAL NOT NULL,
    last_seen REAL NOT NULL, ip TEXT NOT NULL DEFAULT '', user_agent TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS login_attempts (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL DEFAULT '',
    ip TEXT NOT NULL DEFAULT '', at REAL NOT NULL, ok INTEGER NOT NULL DEFAULT 0, user_locked_until REAL,
    ip_locked_until REAL);
CREATE INDEX IF NOT EXISTS login_attempts_user ON login_attempts (username, at);
CREATE INDEX IF NOT EXISTS login_attempts_ip ON login_attempts (ip, at);
CREATE TABLE IF NOT EXISTS ephemeral (kind TEXT NOT NULL, token TEXT NOT NULL, value BLOB NOT NULL,
    created_at REAL NOT NULL, expires_at REAL NOT NULL, PRIMARY KEY (kind, token));
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


def _rescored(output: dict[str, Any], stored: str | None, reading: float) -> dict[str, Any] | None:
    """The stored checks ``stored`` (JSON) of an invoice coded ``output``, scored again with ``reading`` as its
    Reading (``Store._reading_as_d89``): its findings as they were, the Coding from its lines without the accounts'
    lists (a line to an unknown account has its finding, which keeps it for a person anyway), and no checks stamp, so
    the queue checks it again. None when there are none, or they can't be read."""
    from ...coding.validation import line_coding_confidence, score
    from ...core.schema import InvoiceCoding

    try:
        validation = json.loads(stored) if stored else None
        if not isinstance(validation, dict):
            return None
        coding = InvoiceCoding.model_validate({k: v for k, v in output.items() if k != "gl_distribution"})
        findings = [(str(i.get("severity") or ""), str(i.get("code") or "")) for i in validation.get("issues") or []]
        threshold = float(validation.get("review_threshold") or 0.85)
    except (ValueError, TypeError, AttributeError):
        return None
    taxed = any(t.tax_amount for t in coding.tax_lines)
    sure = [line_coding_confidence(li, False, taxed) for li in coding.line_items]
    coded = round(sum(sure) / len(sure), 4) if sure else 0.0
    read, coded, adjusted, review = score(reading, coded, findings, threshold)
    # The AI reviewer's or the Azure check's disagreement keeps it for a person whatever the scores (B-956).
    review = review or any(code.startswith(("VERIFIER_", "AZURE_DI_")) for _, code in findings)
    validation.pop(CHECKS_STAMP_KEY, None)
    validation.pop("recheck", None)
    return {**validation, "model_confidence": reading, "reading_confidence": read, "coding_confidence": coded,
            "adjusted_confidence": adjusted, "requires_review": review}  # fmt: skip


def line_guessed(suggestion: dict[str, Any] | None, rule: dict[str, Any] | None = None) -> bool:
    """Whether the AI only guessed this line's account (``offline_coder.guessed``: the chat model's own pick, an
    older version's weak match), also under a fixed coding rule that replaced the guess (``rule``: its entry in the
    invoice's ``rules_applied``, which says ``from_guess``)."""
    from ...ai.offline_coder import guessed

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
    from ...ai.offline_coder import guessed

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
        self.memo: dict[tuple[Any, ...], tuple[tuple[int, int], Any]] = {}  # ``StoreCore._session_memo``


_sessions = threading.local()


def _held_connections() -> dict[str, _Held]:
    """This thread's held connections, by database path."""
    held = getattr(_sessions, "held", None)
    if held is None:
        held = _sessions.held = {}
    return held


class StoreCore:
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
            from ... import __version__

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
        # The AI reviewer's queue counts reviews the model server cut off before any answer, and remembers who is
        # reviewing an invoice, like the page reader's (looked for likewise).
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(verifications)")}
        for column, kind in (("tries", "INTEGER NOT NULL DEFAULT 0"), ("reader", "TEXT NOT NULL DEFAULT ''")):
            if columns and column not in columns:
                conn.execute(f"ALTER TABLE verifications ADD COLUMN {column} {kind}")
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
        # The vendor list keeps each vendor's QST and PST numbers, province and the bank account JD Edwards pays it on
        # (the JDE API's sync, D-104), to check what an invoice prints against the vendor (whatever the version).
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(vendors)")}
        for column in ("qst_number", "pst_number", "province", "erp_bank", "erp_hold"):  # erp_hold: JDE's (D-105)
            if columns and column not in columns:
                conn.execute(f"ALTER TABLE vendors ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        # An invoice made from a picture remembers the picture's SHA-256 (intake.py), so the same photo sent again is
        # found (looked for whatever the version).
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(invoices)")}
        if "original_sha256" not in columns:
            conn.execute("ALTER TABLE invoices ADD COLUMN original_sha256 TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS invoices_original_sha ON invoices (original_sha256)")
        # The lookups of every invoice processed and page drawn: the same file (find_by_hash), the same total under
        # another vendor (duplicates_elsewhere), an invoice's newest event of a kind (latest_events) (B-730).
        conn.execute("CREATE INDEX IF NOT EXISTS invoices_sha ON invoices (file_sha256)")
        conn.execute("CREATE INDEX IF NOT EXISTS invoices_total ON invoices (grand_total)")
        conn.execute("CREATE INDEX IF NOT EXISTS events_action ON events (action, invoice_id)")
        # The ERP register counts a bill entered more than once in the ERP, for the duplicate audit (looked for
        # whatever the version).
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(erp_invoices)")}
        if columns and "copies" not in columns:
            conn.execute("ALTER TABLE erp_invoices ADD COLUMN copies INTEGER NOT NULL DEFAULT 1")
        # Template snapshots go by the order of approval (``seq``), not by a time to the second (B-780): a table made
        # before it (this version, unreleased) is rebuilt, its snapshots kept in the order they were taken.
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(template_snapshots)")}
        if not columns:
            for sql in _SNAPSHOTS_SCHEMA:
                conn.execute(sql)
        elif "seq" not in columns:
            conn.execute("ALTER TABLE template_snapshots RENAME TO template_snapshots_old")
            conn.execute("DROP INDEX IF EXISTS template_snapshots_supplier")
            for sql in _SNAPSHOTS_SCHEMA:
                conn.execute(sql)
            conn.execute(
                "INSERT INTO template_snapshots (invoice_id, supplier_key, before_json, after_json, at) "
                "SELECT invoice_id, supplier_key, before_json, after_json, at FROM template_snapshots_old "
                "ORDER BY at, rowid"
            )
            conn.execute("DROP TABLE template_snapshots_old")
        # Vendor keys stored under older vendor-matching rules (e.g. before "Limitée" and "s.e.c." were ignored) are
        # recomputed, so the vendor's invoices, lessons, master record and POs still match (looked for whatever the
        # version: the rules are recorded on their own).
        row = conn.execute("SELECT value FROM settings WHERE key = 'vendor_key_rules'").fetchone()
        if (row["value"] if row else "") != VENDOR_KEY_RULES:
            self._recompute_vendor_keys(conn)
            _forget_learning(self.path)
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('vendor_key_rules', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (VENDOR_KEY_RULES,),
            )
        # One-off clean-ups of the learning memory, whatever the version: each scans the whole feedback table, so
        # they run once per database (recorded as settings.feedback_cleanup, like vendor_key_rules), not on every
        # Store() the background workers make (B-727).
        row = conn.execute("SELECT value FROM settings WHERE key = 'feedback_cleanup'").fetchone()
        if (row["value"] if row else "") != FEEDBACK_CLEANUP:
            # Older versions recorded AP Coder's own approvals (nobody checked them) as lessons, counted in the
            # accuracy: withdrawn, as nothing is recorded for them any more.
            from ...capture.workflow import AUTONOMOUS_REVIEWER

            conn.execute("DELETE FROM feedback WHERE reviewer = ?", (AUTONOMOUS_REVIEWER,))
            # Older versions recorded a GL account filled in where the AI proposed none as a correction: it is the
            # reviewer's coding (B-650), recounted (the learning memory reads both alike).
            conn.execute(
                "UPDATE feedback SET outcome = ? WHERE outcome = ? AND suggested_gl IN ('', ?) "
                "AND COALESCE(suggested_cc, '') IN ('', ?)",
                (CODED, CORRECTED, UNASSIGNED, UNASSIGNED),
            )
            _forget_learning(self.path)
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('feedback_cleanup', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (FEEDBACK_CLEANUP,),
            )
        # A GL account can be marked "Meals & entertainment (50% tax credit)" (D-90; looked for whatever the version).
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(gl_accounts)")}
        if columns and "meals" not in columns:
            conn.execute("ALTER TABLE gl_accounts ADD COLUMN meals INTEGER NOT NULL DEFAULT 0")
        # Each approved line remembers its tax code, learned from like its GL account (D-70): filled in from the
        # approved invoices (looked for whatever the version, like the columns above).
        # Who decided it and where the invoice was supplied (D-80) came later in schema 17: a database already at 17
        # without them gets them, and every code filled again (a kept learned code or a guess is no vote).
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(feedback)")}
        missing = [c for c in ("final_tax_code", "tax_source", "tax_province", "tax_uncollected") if c not in columns]
        for column in missing:
            conn.execute(f"ALTER TABLE feedback ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        if missing:
            self._fill_tax_codes(conn)
        self._reading_as_d89(conn)
        if version < SCHEMA_VERSION:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(SCHEMA_VERSION),),
            )

    @staticmethod
    def _reading_as_d89(conn: sqlite3.Connection) -> None:
        """Once per database (``settings.reading_d89``): the invoices still to approve that the local readers read
        before D-89 had a confidence multiplied by the share of lines coded; their ``confidence_score`` becomes the
        Reading of their stored capture. The queue's confidence and review flag follow, from their stored findings and a
        Coding worked out from their lines (``validation.score``; the accounts' lists are checked again in the queue:
        their checks stamp is cleared for ``bulk.issues_today``, D-88), so the queue is sorted and flagged by the new
        figures (B-895). One read by a chat model (or without a stored capture) keeps its own confidence as its Reading,
        its queue figure worked out again the same way (it showed the old one beside a lower Coding, B-956)."""
        if conn.execute("SELECT 1 FROM settings WHERE key = 'reading_d89'").fetchone():
            return
        from ...ai.offline_coder import MODEL_NAME
        from ...capture.confidence import reading_confidence
        from ...capture.types import FieldResult
        from .capture import capture_value

        rows = conn.execute(
            """SELECT i.id, i.ai_output, i.meta, i.validation, c.capture_json FROM invoices i LEFT JOIN
               invoice_capture c ON c.invoice_id = i.id WHERE i.status IN (?, ?) AND i.ai_output IS NOT NULL""",
            (REVIEW, PARKED),
        ).fetchall()
        for r in rows:
            try:
                output = json.loads(r["ai_output"])
                local = (json.loads(r["meta"] or "{}").get("inference") or {}).get("model") == MODEL_NAME
                if local and r["capture_json"]:
                    fields = (capture_value(r["capture_json"]) or {}).get("fields") or {}
                    reading = reading_confidence({k: FieldResult.from_dict(v) for k, v in fields.items()})
                else:  # read by a chat model or the demo: its own confidence
                    reading, local = float(output.get("confidence_score") or 0.0), False
            except (ValueError, TypeError, KeyError, AttributeError):
                continue
            if local:
                output["confidence_score"] = reading
                conn.execute("UPDATE invoices SET ai_output = ?, model_confidence = ? WHERE id = ?",
                             (json.dumps(output), reading, r["id"]))  # fmt: skip
            validation = _rescored(output, r["validation"], reading)
            if validation is not None:
                conn.execute("UPDATE invoices SET validation = ?, adjusted_confidence = ?, requires_review = ? "
                             "WHERE id = ?", (json.dumps(validation, default=str), validation["adjusted_confidence"],
                                              int(validation["requires_review"]), r["id"]))  # fmt: skip
        conn.execute("INSERT INTO settings (key, value) VALUES ('reading_d89', '1') ON CONFLICT(key) DO NOTHING")

    @staticmethod
    def _fill_tax_codes(conn: sqlite3.Connection) -> None:
        """The tax code of each approved line's feedback row, who decided it and where the invoice was supplied, from
        its invoice's approved coding and what was proposed (schema 17, ``line_tax.tax_vote``). A line approved before
        ``tax_exempt`` existed keeps its exemption only as a contradiction, never a vote (``LEGACY``, B-765)."""
        from ...coding.line_tax import invoice_uncollected, supply_province, tax_vote

        found = conn.execute("SELECT value FROM settings WHERE key = 'our_province'").fetchone()
        ours = str(found[0] if found else "").strip().upper()
        bulk = {r[0] for r in conn.execute("SELECT invoice_id FROM events WHERE action = 'approved' AND "
                                           "detail LIKE '%\"bulk\": true%'")}  # fmt: skip
        rows = conn.execute(
            "SELECT DISTINCT i.id, i.final_output, i.ai_output FROM invoices i JOIN feedback f ON f.invoice_id = i.id "
            "WHERE i.final_output IS NOT NULL"
        ).fetchall()
        for r in rows:
            try:
                final = json.loads(r["final_output"]) or {}
                ai = json.loads(r["ai_output"] or "null") or {}
            except ValueError:
                continue
            lines = [li for li in final.get("line_items") or [] if isinstance(li, dict)]
            proposed = [li for li in ai.get("line_items") or [] if isinstance(li, dict)]
            province = supply_province(final, ours)
            uncollected = "+".join(invoice_uncollected(ai if proposed else final, province))
            votes = {
                li.get("line_number"): tax_vote(was, li, proposed, ai.get("tax_lines") or [], lines,
                                                final.get("tax_lines") or [], bulk=r["id"] in bulk)
                for was, li in pair_lines(proposed, lines)
            }  # fmt: skip
            conn.executemany(
                "UPDATE feedback SET final_tax_code = ?, tax_source = ?, tax_province = ?, tax_uncollected = ? "
                "WHERE invoice_id = ? AND line_number = ?",
                [(code, source, province, uncollected, r["id"], number) for number, (code, source) in votes.items()],
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
            # the second of two vendors whose names read alike keeps its " #<ERP ID>" (B-930, B-1021)
            base, mark, ident = str(old or "").rpartition(" #")
            if mark and base and ident and " " not in ident:
                return f"{vendor_key(str(name or '')) or refresh_vendor_key(base)} #{ident}"
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
                    _store_class()._merge_vendor_rows(conn, r["ident"], key)
        for r in conn.execute("SELECT id, situation, vendor_name, vendor_key FROM lessons").fetchall():
            key = new_key(r["vendor_name"], r["vendor_key"])
            _old, sep, rest = str(r["situation"] or "").partition("|")
            situation = f"{key}{sep}{rest}" if sep else r["situation"]
            if (key, situation) != (r["vendor_key"], r["situation"]):
                done = conn.execute("UPDATE OR IGNORE lessons SET vendor_key = ?, situation = ? WHERE id = ?",
                                    (key, situation, r["id"]))  # fmt: skip
                if not done.rowcount:  # the same situation of the same vendor twice: one lesson, counts added up
                    _store_class()._merge_lesson_rows(conn, r["id"], situation)
                if situation != r["situation"]:  # each approval's part follows its lesson (B-158)
                    conn.execute("UPDATE OR IGNORE lesson_approvals SET situation = ? WHERE situation = ?",
                                 (situation, r["situation"]))  # fmt: skip
                    conn.execute("DELETE FROM lesson_approvals WHERE situation = ?", (r["situation"],))
        for table, column in (("supplier_profiles", "key"), ("supplier_outcomes", "supplier_key"),
                              ("template_snapshots", "supplier_key")):  # fmt: skip
            rows = conn.execute(f"SELECT rowid AS rid, {column} AS k FROM {table} WHERE {column} LIKE 'name:%'")
            for r in rows.fetchall():
                key = "name:" + refresh_vendor_key(r["k"][5:])
                if key != r["k"]:
                    done = conn.execute(f"UPDATE OR IGNORE {table} SET {column} = ? WHERE rowid = ?", (key, r["rid"]))
                    if not done.rowcount and table == "supplier_profiles":  # one profile, the strictest state kept
                        _store_class()._merge_supplier_profiles(conn, r["k"], key)

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
        from ...capture.supplier import AUTONOMOUS, HELD, SUPERVISED, SUSPENDED

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
        from .accounts import count_masters_change

        count_masters_change(conn)

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

    def _session_memo(self, key: tuple[Any, ...], compute: Callable[[], list[dict[str, Any]]]) -> list[dict[str, Any]]:
        """``compute()`` (rows), read once inside a session while the database does not change: a page that asks the
        same thing twice (the vendor checks and the tax-number notes) reads it once. Any change, on the session's
        connection (``total_changes``) or another one (``PRAGMA data_version``), reads it again. Outside a session,
        always ``compute()``. Each caller gets rows of its own."""
        held = _held_connections().get(str(self.path))
        if held is None:
            return compute()
        stamp = (held.conn.total_changes, held.conn.execute("PRAGMA data_version").fetchone()[0])
        hit = held.memo.get(key)
        if hit is None or hit[0] != stamp:
            hit = held.memo[key] = (stamp, compute())
        return [dict(r) for r in hit[1]]

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


def _store_class() -> type:
    """The assembled ``Store`` (its static helpers call one another through it)."""
    from . import Store

    return Store
