"""The store's foundation (D-69): the schema (``_SCHEMA``, ``SCHEMA_VERSION``), the migrations of an older database
(``StoreCore._migrate``, its steps in ``migrations``, D-190), the connections (``StoreCore._conn``,
``StoreCore.session``) and the helpers the other parts share: the statuses and queues' names, ``_now``, ``_in_list``,
``fold_text`` (the SQL function ``ap_fold``), and what an approval counts as coded, kept or guessed
(``suggestion_kept``, ``line_guessed``). What is SQLite's own in them is named in ``dialect``."""

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

from ...coding.terms import DEFAULT_TERMS_DAYS, payment
from ..caches import MB, Bounded, size_of
from ..keys import norm_invoice_number
from ..memory import pair_lines, refresh_vendor_key, vendor_key
from ..paths import default_db_path, private_dir  # noqa: F401  (private_dir: re-exported by the package)
from ..reference_data import UNASSIGNED
from . import dialect
from .fx import _FX_RATE, APPROVAL_CONTROLS, parse_fx_rates, tiny_fx_rates, unread_fx_text  # noqa: F401  (re-exported)

# 10: erp_invoices, 11: coding_rules (by _SCHEMA), 12: currency, 13: credit notes not due,
# 14: invoice_capture, supplier_profiles, supplier_outcomes (by _SCHEMA), 15: reader_outcomes, page_reads (by _SCHEMA),
# 16: bank_accounts, our company's bank accounts (by _SCHEMA, D-66), 17: feedback.final_tax_code, tax_source,
# tax_province and tax_uncollected (filled from the approved invoices, D-70, D-80), template_snapshots (D-85),
# invoice_drafts (by _SCHEMA: the review screen's edits not approved yet, kept over a restart, D-86),
# 18: users, sessions, login_attempts (by _SCHEMA: server mode's accounts, D-97), later vendors.erp_bank (D-104) and
# vendor_aliases (by _SCHEMA: a vendor name read wrong, as AP corrected it, D-119), line_texts (by _SCHEMA: a
# supplier's line descriptions as AP approved them, D-120)
SCHEMA_VERSION = 27  # 19: the invoices' summary and its triggers (summary.py, ``_summary_migrate``, D-110), 20: indexes
# (B-1581), 22: the ERP register's vendor names and invoice numbers as the quick search compares them (D-157), 24: the
# events' indexes by day and by person, the register's by number (D-156), 25: the lessons' approvals counted off an
# index (B-1714), 26: the import queue (import_batches, import_files, import_turns by _SCHEMA, its indexes; D-168),
# 27: report_answers and report_jobs (by _SCHEMA: the big answers worked out once for every process of the company
# server, and the downloads its background process makes, reports.py, D-169); past_lines came later in 27 (by
# _SCHEMA, no version of its own: an older version leaves it be; a vendor's older lessons by line, learning.py, D-178),
# and bulk_jobs (by _SCHEMA, no version of its own either: bulk approvals as jobs, bulk_jobs.py, D-185), and
# billing_facts and billing_vendors (by _SCHEMA, no version of their own either: month-end's billing lines by invoice,
# purchasing.py, D-194)
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
    return dialect.in_list(column)


# An invoice file looked for and not found (``Store.invoice_file``) is looked for again after this long, not on every
# page picture the review screen asks for.
_LOOK_AGAIN_SECONDS = 30.0
_NOT_FOUND: dict[tuple[str, int, str], float] = {}
# Invoice files found where they were recorded, and when: an invoice read is not checked again for this long.
_FOUND: dict[str, float] = {}
LOOKED_KEPT = 50_000  # of each (the files found, those not): past it they are all let go (B-2026)

BRIEF_WAIT_MS = 200  # how long a page's bookkeeping (``Store.briefly``) may wait for another writer

# Databases being restored in this process (``Store.restore_from``): the background workers (page reader, Azure
# check) start nothing on them meanwhile (``Store.restoring``).
_RESTORING: set[str] = set()
_RESTORING_LOCK = threading.Lock()

# The learning memory as last read, per database: (highest lesson id and count, rows) (``Store.feedback_rows``). These
# caches of the learning are kept within their bytes too, the ones used longest ago going first (D-195).
_FEEDBACK = Bounded("learning memory", 96 * MB, size=lambda kept: size_of(kept[1]))
_FEEDBACK_LOCK = threading.Lock()
# The learning memory's part of ``Store.metrics`` as last counted, per database: (its stamp, the counts).
_METRICS: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {}
# What a vendor's lessons say as last worked out, per (database, vendor key): (their stamp, ``VendorLessons``), the
# latest used last (``Store.vendor_lessons``).
_VENDOR_LESSONS = Bounded("vendors' lessons", 8 * MB, size=lambda kept: size_of(kept[1]))
# A vendor's lessons the whole memory does not hold, as last read, per (database, vendor key): (their stamp, rows), the
# latest used last, ``memory.PAST_KEPT`` vendors and ``PAST_ROWS_KEPT`` lessons at most (``Store.learning_rows``).
_PAST_MEMORY = Bounded("vendors' older lessons", 80 * MB, size=lambda kept: size_of(kept[1]))
# The same lessons by line (``memory.PastLines``), per (database, vendor key): (their stamp, the lines, the lessons they
# were carried on by since the database's copy, ``past_lines``), the latest used last (``Store._past_lines``, D-178).
_PAST_LINES: dict[tuple[str, str], tuple[tuple[Any, ...], Any, int]] = {}


def _forget_learning(path: Path) -> None:
    """The learning memory kept for this database (``feedback_rows``, ``metrics``, ``vendor_lessons``) dropped: an
    upgrade changed lessons in place, which the stamps (lessons are otherwise only added or removed) don't see. (What
    the database keeps of them for every process goes with the change itself: ``past_lines``.)"""
    key = str(path.resolve())
    with _FEEDBACK_LOCK:
        for kept in (_FEEDBACK, _METRICS, _VENDOR_LESSONS, _PAST_MEMORY, _PAST_LINES):
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

# Each vendor's invoices changed, in the order they were (D-174): (the vendor, the invoice) logged by the triggers that
# count each vendor's changes (``summary.SUMMARY_TRIGGERS``: an invoice of its added or deleted, or a column
# ``Store.vendor_invoices`` lists changed; a move between vendors for both), in the same transaction as the change,
# however it is made: a process keeping a vendor's invoices reads again only those changed since. The latest
# VENDOR_CHANGES_KEPT are kept (each change removes the one that many before it). Made with those triggers, at every
# start and in a backup's copy being restored (``summary.make_triggers``).
VENDOR_CHANGES_KEPT = 20_000
VENDOR_CHANGES = ("CREATE TABLE IF NOT EXISTS vendor_changes (seq INTEGER PRIMARY KEY, vendor_key TEXT NOT NULL, "
                  "invoice_id INTEGER NOT NULL)")  # fmt: skip
VENDOR_LOGGED = ("INSERT INTO vendor_changes (vendor_key, invoice_id) SELECT {0}.vendor_key, {0}.id WHERE "
                 "{0}.vendor_key IS NOT NULL{1}; DELETE FROM vendor_changes WHERE seq <= (SELECT MAX(seq) FROM "
                 f"vendor_changes) - {VENDOR_CHANGES_KEPT}")  # fmt: skip
# The review queue's invoices changed, in the order they were (D-193): each one whose row in the queue's lists or whose
# facts in review changed, logged by the summary as it works it out (``summary._summarize``, in the same transaction):
# a process keeping the queue's lists and facts (``review_queue.py``) reads again only those. The latest
# QUEUE_CHANGES_KEPT are kept. Made with the vendors' log (``summary.make_triggers``).
QUEUE_CHANGES_KEPT = 20_000
QUEUE_CHANGES = "CREATE TABLE IF NOT EXISTS queue_changes (seq INTEGER PRIMARY KEY, id INTEGER NOT NULL)"

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
    copies INTEGER NOT NULL DEFAULT 1, plain_name TEXT NOT NULL DEFAULT '', raw_number TEXT NOT NULL DEFAULT '',
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
    updated_at TEXT NOT NULL, updated_by TEXT, suspended_at TEXT);
-- the suppliers with a learned template, counted without reading them (``supplier_templates_count``, B-1736)
CREATE INDEX IF NOT EXISTS supplier_profiles_template ON supplier_profiles (key)
    WHERE template_json IS NOT NULL AND template_json NOT IN ('', 'null', '{}');
CREATE TABLE IF NOT EXISTS supplier_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, supplier_key TEXT NOT NULL, invoice_id INTEGER, field TEXT NOT NULL,
    ai_value TEXT, final_value TEXT, correct INTEGER NOT NULL, source TEXT NOT NULL DEFAULT 'review',
    at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS supplier_outcomes_key_at ON supplier_outcomes (supplier_key, at);
CREATE INDEX IF NOT EXISTS supplier_outcomes_invoice ON supplier_outcomes (invoice_id);
CREATE TABLE IF NOT EXISTS vendor_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT, alias_key TEXT NOT NULL, printed TEXT NOT NULL DEFAULT '',
    vendor_name TEXT NOT NULL, gst_number TEXT NOT NULL DEFAULT '', invoice_id INTEGER, at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS vendor_aliases_key ON vendor_aliases (alias_key, id);
CREATE INDEX IF NOT EXISTS vendor_aliases_invoice ON vendor_aliases (invoice_id);
CREATE TABLE IF NOT EXISTS line_texts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, supplier_key TEXT NOT NULL, read_key TEXT NOT NULL,
    printed TEXT NOT NULL DEFAULT '', description TEXT NOT NULL, corrected INTEGER NOT NULL DEFAULT 0,
    invoice_id INTEGER, at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS line_texts_key ON line_texts (supplier_key, id);
CREATE INDEX IF NOT EXISTS line_texts_invoice ON line_texts (invoice_id);
CREATE TABLE IF NOT EXISTS manager_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'waiting',
    sent_at TEXT NOT NULL, sent_by TEXT NOT NULL DEFAULT '', manager_name TEXT NOT NULL DEFAULT '',
    manager_email TEXT NOT NULL DEFAULT '', manager_user INTEGER, cost_centers TEXT NOT NULL DEFAULT '[]',
    lines TEXT NOT NULL DEFAULT '[]', note TEXT NOT NULL DEFAULT '', answered_at TEXT, answered_by TEXT NOT NULL
    DEFAULT '', answer_note TEXT NOT NULL DEFAULT '', reply_file TEXT NOT NULL DEFAULT '', via TEXT NOT NULL
    DEFAULT '');
CREATE INDEX IF NOT EXISTS manager_requests_invoice ON manager_requests (invoice_id, id);
CREATE INDEX IF NOT EXISTS manager_requests_user ON manager_requests (manager_user, status);
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
    updated_at TEXT NOT NULL, reader TEXT NOT NULL DEFAULT '', tries INTEGER NOT NULL DEFAULT 0, renewed REAL);
CREATE INDEX IF NOT EXISTS page_reads_status ON page_reads (status, created_at);
-- the latest finished reads (``page_read_pages_average``, the reading status of every page), not all sorted (B-1734)
CREATE INDEX IF NOT EXISTS page_reads_done ON page_reads (status, updated_at);
CREATE TABLE IF NOT EXISTS azure_checks (
    invoice_id INTEGER PRIMARY KEY, reason TEXT NOT NULL, status TEXT NOT NULL, pages INTEGER NOT NULL DEFAULT 0,
    compared INTEGER NOT NULL DEFAULT 0, agreed INTEGER NOT NULL DEFAULT 0, result_json TEXT,
    error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, compared_at TEXT);
CREATE INDEX IF NOT EXISTS azure_checks_status ON azure_checks (status, created_at);
CREATE TABLE IF NOT EXISTS verifications (
    invoice_id INTEGER PRIMARY KEY, reason TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
    rounds INTEGER NOT NULL DEFAULT 0, seconds REAL NOT NULL DEFAULT 0, result_json TEXT,
    error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    tries INTEGER NOT NULL DEFAULT 0, renewed REAL);
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
CREATE TABLE IF NOT EXISTS seen_files (path TEXT PRIMARY KEY, size INTEGER, mtime_ns INTEGER, sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS import_batches (id INTEGER PRIMARY KEY AUTOINCREMENT, run INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL, uploader TEXT NOT NULL DEFAULT '', actor TEXT NOT NULL DEFAULT '', account TEXT NOT NULL
    DEFAULT '', origin TEXT NOT NULL DEFAULT '', person INTEGER NOT NULL DEFAULT 0, include_failed INTEGER NOT NULL
    DEFAULT 0, force INTEGER NOT NULL DEFAULT 0, only_owner TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '[]',
    lines TEXT NOT NULL DEFAULT '[]', skipped TEXT NOT NULL DEFAULT '[]', emails INTEGER NOT NULL DEFAULT 0,
    to_person INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, finished_at TEXT);
CREATE TABLE IF NOT EXISTS import_files (id INTEGER PRIMARY KEY AUTOINCREMENT, batch INTEGER NOT NULL, path TEXT NOT
    NULL, name TEXT NOT NULL, size INTEGER, mtime_ns INTEGER, sha256 TEXT NOT NULL, status TEXT NOT NULL DEFAULT
    'waiting', owner TEXT NOT NULL DEFAULT '', lease_until REAL, tries INTEGER NOT NULL DEFAULT 0, invoice_id INTEGER,
    line TEXT, error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT);
CREATE TABLE IF NOT EXISTS import_turns (uploader TEXT PRIMARY KEY, served INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS report_answers (name TEXT NOT NULL, asked TEXT NOT NULL, version TEXT NOT NULL,
    stamp TEXT NOT NULL, value TEXT NOT NULL, made_at REAL NOT NULL, PRIMARY KEY (name, asked));
CREATE TABLE IF NOT EXISTS past_lines (vendor_key TEXT PRIMARY KEY, stamp TEXT NOT NULL, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS billing_facts (vendor_key TEXT NOT NULL, id INTEGER NOT NULL, facts TEXT NOT NULL,
    PRIMARY KEY (vendor_key, id));
CREATE TABLE IF NOT EXISTS billing_vendors (vendor_key TEXT PRIMARY KEY, kept TEXT NOT NULL, logged INTEGER NOT NULL,
    counted INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS report_jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL, kind TEXT NOT NULL,
    params TEXT NOT NULL, name TEXT NOT NULL, state TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
    file TEXT NOT NULL DEFAULT '', size INTEGER NOT NULL DEFAULT 0, runner TEXT NOT NULL DEFAULT '',
    tries INTEGER NOT NULL DEFAULT 0, db TEXT NOT NULL, version TEXT NOT NULL, created_at REAL NOT NULL,
    renewed_at REAL NOT NULL DEFAULT 0, finished_at REAL NOT NULL DEFAULT 0);
CREATE UNIQUE INDEX IF NOT EXISTS report_jobs_one ON report_jobs (owner, kind, params, db) WHERE state IN
    ('waiting', 'running');
CREATE INDEX IF NOT EXISTS report_jobs_state ON report_jobs (state, id);
CREATE INDEX IF NOT EXISTS report_jobs_owner ON report_jobs (owner, state);
CREATE TABLE IF NOT EXISTS bulk_jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL, person TEXT NOT NULL,
    ids TEXT NOT NULL, state TEXT NOT NULL, approved TEXT NOT NULL DEFAULT '[]', skipped TEXT NOT NULL DEFAULT '[]',
    error TEXT NOT NULL DEFAULT '', runner TEXT NOT NULL DEFAULT '', tries INTEGER NOT NULL DEFAULT 0, db TEXT NOT NULL,
    version TEXT NOT NULL, created_at REAL NOT NULL, renewed_at REAL NOT NULL DEFAULT 0,
    finished_at REAL NOT NULL DEFAULT 0, seen INTEGER NOT NULL DEFAULT 0);
CREATE UNIQUE INDEX IF NOT EXISTS bulk_jobs_one ON bulk_jobs (owner, db) WHERE state IN ('waiting', 'running');
CREATE INDEX IF NOT EXISTS bulk_jobs_state ON bulk_jobs (state, id);
CREATE INDEX IF NOT EXISTS bulk_jobs_owner ON bulk_jobs (owner, id);
"""

# The page reader's queue (``page_reads.status``, ``page_queue``). A read not renewed for READING_STALE_SECONDS was
# interrupted (the computer slept, its process paused): it waits in line again. ``reader`` names the process reading
# it (``page_worker.READER_ID``), so a read whose process is gone is put back in line at once
# (``release_page_reads``); ``tries``: reads cut off by the model server since it was put in line.
PAGE_WAITING, PAGE_READING, PAGE_DONE, PAGE_FAILED, PAGE_SKIPPED = "waiting", "reading", "done", "failed", "skipped"
STALE_READING_HOURS = 2
# A read (or review) under way is stale once not renewed for READING_STALE_SECONDS on the database's clock (``renewed``:
# julianday('now'), which a change of the clocks doesn't move, B-1770); a row renewed before it had one, by its local
# time (``updated_at``). ``{t}``: the table's name and a dot, or nothing; asked with ``page_queue._stale()``.
_STALE = f"COALESCE({{t}}renewed < {dialect.NOW_DAYS} - ? / 86400.0, {{t}}updated_at < ?)"
_IN_LINE = f"(status = 'waiting' OR (status = 'reading' AND {_STALE.format(t='')}))"  # ? = page_queue._stale()

# The Azure check's queue (``azure_checks.status``, ``ap_coder/ai/azure_check.py``): off unless AP turns it on in
# Settings → Reading. A check still "checking" after STALE_READING_HOURS was interrupted: it waits in line again.
AZURE_WAITING, AZURE_CHECKING, AZURE_DONE = "waiting", "checking", "done"
AZURE_FAILED, AZURE_SKIPPED = "failed", "skipped"
_AZURE_IN_LINE = (  # ? = _stale_before()
    "(azure_checks.status = 'waiting' OR (azure_checks.status = 'checking' AND azure_checks.updated_at < ?))"
)

# The AI reviewer's queue (``verifications.status``, ``ap_coder/ai/verifier.py``). A review not renewed for
# READING_STALE_SECONDS (``page_queue``) was interrupted: it waits in line again.
VERIFY_WAITING, VERIFY_RUNNING, VERIFY_DONE, VERIFY_FAILED, VERIFY_SKIPPED = (
    "waiting", "verifying", "done", "failed", "skipped")  # fmt: skip
_VERIFY_IN_LINE = (  # ? = page_queue._stale()
    "(verifications.status = 'waiting' OR (verifications.status = 'verifying' AND "
    f"{_STALE.format(t='verifications.')}))"
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


def line_guessed(suggestion: dict[str, Any] | None, rule: dict[str, Any] | None = None) -> bool:
    """Whether the AI only guessed this line's account (``offline_coder.guessed``: the chat model's own pick, an
    older version's weak match), also under a fixed coding rule that replaced the guess (``rule``: its entry in the
    invoice's ``rules_applied``, which says ``from_guess``)."""
    from ...ai.offline_coder import guessed

    if rule is not None and "from_guess" in rule:
        return bool(rule["from_guess"])
    return bool(suggestion) and guessed((suggestion or {}).get("reasoning_justification"))


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


class NestedWrite(RuntimeError):
    """A write that commits on its own (a queue's claim, a try at logging in: ``StoreCore._begin_alone``) was called
    inside another transaction, which would hold its commit back or undo it with a rollback."""


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
        tables = dialect.table_names(conn)
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
        # ``StoreCore._session_memo``: what was read at one moment of the database (``moment``), within its bytes
        self.memo = Bounded("a session's memo", SESSION_MEMO_BYTES, size=lambda kept: size_of(kept[1]))
        self.moment: tuple[int, int] | None = None


_sessions = threading.local()
# One session's memo at most: a vendor's invoices up to about 70,000 (300,000 invoices' biggest: 24 MB; B-2042)
SESSION_MEMO_BYTES = 64 * MB


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
        unsaved = self._before_migrating()
        held = _held_connections().get(str(self.path))
        if held is not None and held.depth > 0:
            # Inside this thread's transaction on the database: ``executescript`` would commit it (B-1996), and the
            # start-up's own writes (the summary's refresh) would wait for the write lock this thread holds (B-1965).
            # The caller's Store has made it current: nothing to start up. One not current can't be brought up to
            # date inside another write.
            if database_schema(self.path) == SCHEMA_VERSION:
                return
            raise NestedWrite(f"The database {self.path} must be brought up to date first, so a Store of it can't be "
                              "made inside another write.")  # fmt: skip
        with self._conn() as conn:
            conn.executescript(_SCHEMA)
            self._migrate(conn)
            if unsaved:  # said on Settings → Data & backups (``backups.BACKUP_STATUS``)
                conn.execute(dialect.insert_or_replace("settings", ("key", "value")), ("backup_status", unsaved))

    def _before_migrating(self) -> str:
        """An update's safety net, before the database is touched: a database made by a newer AP Coder is refused
        (never opened by this older one, so never downgraded), and one made by an older AP Coder is backed up first
        (``backups/ap_coder-<date>-before-update-from-schema-<n>.db``, then its files: ``backup_files.before_update``),
        so the update can always be undone. What to say when the files could not be saved is returned (B-1561)."""
        found = database_schema(self.path)
        if found is None or found == SCHEMA_VERSION:
            return ""
        if found > SCHEMA_VERSION:
            from ... import __version__

            raise DatabaseTooNew(
                f"The database {self.path} was made by a newer AP Coder (database version {found}; this AP Coder "
                f"{__version__} knows up to version {SCHEMA_VERSION}), so this older version does not open it: "
                "nothing was changed. Start the newer AP Coder instead."
            )
        from . import backup_files

        return backup_files.before_update(self, found)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Bring a database made by an older version up to date (recorded as settings.schema_version): the steps of
        ``migrations.STEPS``, in order (D-190)."""
        from .migrations import migrate  # (it imports this part: here, not at the top)

        migrate(self, conn)

    @staticmethod
    def _fill_tax_codes(conn: sqlite3.Connection) -> None:
        """The tax code of each approved line's feedback row, who decided it and where the invoice was supplied, from
        its invoice's approved coding and what was proposed (schema 17, ``line_tax.tax_vote``). A line approved before
        ``tax_exempt`` existed keeps its exemption only as a contradiction, never a vote (``LEGACY``, B-765)."""
        from ...coding.line_tax import invoice_uncollected, supply_province, tax_vote

        found = conn.execute("SELECT value FROM settings WHERE key = 'our_province'").fetchone()
        ours = str(found[0] if found else "").strip().upper()
        bulk = {r[0] for r in conn.execute(f"SELECT invoice_id FROM events WHERE action = 'approved' AND "
                                           f"{dialect.like('detail')}", ('%"bulk": true%',))}  # fmt: skip
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
            ("erp_invoices", "vendor_name", dialect.ROWID),
        ):  # fmt: skip
            for r in conn.execute(f"SELECT {ident} AS ident, {name} AS name, vendor_key FROM {table}").fetchall():
                key = new_key(r["name"], r["vendor_key"])
                if key == (r["vendor_key"] or ""):
                    continue
                done = conn.execute(f"{dialect.update_or_ignore(table)} SET vendor_key = ? WHERE {ident} = ?",
                                    (key, r["ident"]))  # fmt: skip
                if table == "erp_invoices":  # its JD Edwards vouchers go with it (D-181)
                    conn.execute("UPDATE erp_vouchers SET vendor_key = ? WHERE vendor_key = ?", (key, r["vendor_key"]))
                if not done.rowcount and table == "erp_invoices":  # the same ERP bill is already there under the key
                    conn.execute(f"DELETE FROM erp_invoices WHERE {dialect.ROWID} = ?", (r["ident"],))
                elif not done.rowcount and table == "vendors":  # the same vendor twice: one row, on hold if either is
                    _store_class()._merge_vendor_rows(conn, r["ident"], key)
        for r in conn.execute("SELECT id, situation, vendor_name, vendor_key FROM lessons").fetchall():
            key = new_key(r["vendor_name"], r["vendor_key"])
            _old, sep, rest = str(r["situation"] or "").partition("|")
            situation = f"{key}{sep}{rest}" if sep else r["situation"]
            if (key, situation) != (r["vendor_key"], r["situation"]):
                done = conn.execute(f"{dialect.update_or_ignore('lessons')} SET vendor_key = ?, situation = ? WHERE "
                                    "id = ?", (key, situation, r["id"]))  # fmt: skip
                if not done.rowcount:  # the same situation of the same vendor twice: one lesson, counts added up
                    _store_class()._merge_lesson_rows(conn, r["id"], situation)
                if situation != r["situation"]:  # each approval's part follows its lesson (B-158)
                    conn.execute(f"{dialect.update_or_ignore('lesson_approvals')} SET situation = ? WHERE "
                                 "situation = ?", (situation, r["situation"]))  # fmt: skip
                    conn.execute("DELETE FROM lesson_approvals WHERE situation = ?", (r["situation"],))
        for table, column in (("supplier_profiles", "key"), ("supplier_outcomes", "supplier_key"),
                              ("template_snapshots", "supplier_key")):  # fmt: skip
            rows = conn.execute(f"SELECT {dialect.ROWID} AS rid, {column} AS k FROM {table} WHERE "
                                f"{dialect.like(column)}", ("name:%",))  # fmt: skip
            for r in rows.fetchall():
                key = "name:" + refresh_vendor_key(r["k"][5:])
                if key != r["k"]:
                    done = conn.execute(f"{dialect.update_or_ignore(table)} SET {column} = ? WHERE "
                                        f"{dialect.ROWID} = ?", (key, r["rid"]))  # fmt: skip
                    if not done.rowcount and table == "supplier_profiles":  # one profile, the strictest state kept
                        _store_class()._merge_supplier_profiles(conn, r["k"], key)

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

    @contextmanager
    def briefly(self) -> Iterator[None]:
        """Every call on this database inside, in this thread, joins one transaction whose writes wait at most
        BRIEF_WAIT_MS for another writer, else raise ``sqlite3.OperationalError`` and nothing of it is kept: the
        bookkeeping of a page, which never holds it up (as a session's use, users.py ``_briefly``)."""
        with self.session(), self._conn() as conn:
            waits = dialect.busy_timeout(conn)
            dialect.set_busy_timeout(conn, BRIEF_WAIT_MS)
            try:
                yield
            finally:
                dialect.set_busy_timeout(conn, waits)

    def _session_memo(
        self, key: tuple[Any, ...], compute: Callable[[], list[dict[str, Any]]], shared: bool = False
    ) -> list[dict[str, Any]]:
        """``compute()`` (rows), read once inside a session while the database does not change: a page that asks the
        same thing twice (the vendor checks and the tax-number notes) reads it once. Any change, on the session's
        connection (``total_changes``) or another one (``PRAGMA data_version``), reads it again. Outside a session,
        always ``compute()``. Each caller gets rows of its own; ``shared``: the same list (read it, don't change it)."""
        held = _held_connections().get(str(self.path))
        if held is None:
            return compute()
        stamp = (held.conn.total_changes, dialect.data_version(held.conn))
        if stamp != held.moment:  # (the database changed: nothing read before holds, so none of it is kept, B-2042)
            held.memo.clear()
            held.moment = stamp
        hit = held.memo.get(key)
        if hit is None:
            hit = held.memo[key] = (stamp, compute())
        return hit[1] if shared else [dict(r) for r in hit[1]]

    def _open(self) -> sqlite3.Connection:
        fresh = not self.path.exists()  # e.g. the file was deleted while the dashboard was running
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.create_function("ap_fold", 1, fold_text, deterministic=True)
        dialect.settle(conn)
        if fresh:
            conn.executescript(_SCHEMA)
            self._migrate(conn)
            conn.commit()  # the upgrade's changes left open would refuse the caller's own BEGIN IMMEDIATE
        return conn

    def _begin_alone(self, conn: sqlite3.Connection, what: str) -> None:
        """The write lock for a transaction that must commit on its own, at the end of the ``_conn`` block of the
        method ``what`` (why: its comment, and ``tests/meta/test_nested_writes.py``): ``NestedWrite`` inside another
        transaction, which every other write joins (``dialect.begin_write``)."""
        held = _held_connections().get(str(self.path))
        if conn.in_transaction or (held is not None and held.conn is conn and held.depth > 1):
            raise NestedWrite(f"Store.{what} commits on its own, so it can't be called inside Store.transaction() or "
                              "another write.")  # fmt: skip
        dialect.begin_write(conn)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        """The connection a call works on: this thread's session's, if it holds one (the outermost block commits, or
        rolls back), else one of its own, committed and closed at the end."""
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
