"""A database made by an older AP Coder brought up to date (D-190), one small step at a time: ``STEPS`` in order,
run by ``StoreCore._migrate`` in the transaction that opens the store, after ``_SCHEMA`` made the tables a database
lacks (the capture's and the summary's own come after them: ``SummaryMixin._migrate``).

* **A numbered step** brings a database older than its number up to it (``MIGRATIONS``: ``{2: _v2_vendor_keys,
  ...}``); a version with no step of its own came by ``_SCHEMA``'s new tables (``SCHEMA_VERSION``'s note says which).
* **A step with no number** looks whatever the version: columns and indexes added without a version of their own,
  and the one-off clean-ups recorded on their own (``vendor_key_rules``, ``feedback_cleanup``...). Each stays where
  it ran before, its order kept.

A new schema version N: SCHEMA_VERSION becomes N and its step goes at the end of STEPS, ``(N, _vN_what)``. An older
database brought up to date must be the same as a new one: ``tests/core/test_migrations.py`` checks it on the
database each release made (``tests/fixtures/old_schemas/``, ``scripts/old_schemas.py``). Every look at a table goes
through ``dialect`` (``columns``, ``add_column``)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import Any

from ..keys import po_key
from ..memory import AUTONOMOUS_REVIEWER, CODED, CORRECTED, VENDOR_KEY_RULES, vendor_key
from ..reference_data import UNASSIGNED
from . import dialect
from ._base import SCHEMA_VERSION, _due, _forget_learning
from .folder import add_register_keys, fill_seen_files
from .old_readings import reading_as_d89
from .register import add_jde_register, build_register_search
from .users import add_columns

Step = Callable[[Any, sqlite3.Connection], None]  # (the store, its connection)

# Schema 20's indexes (B-1581), for: an export batch's invoices (undo, an empty batch, who may post it), an invoice's
# coding memory (withdrawn at a reopen, rejection or delete), the events of a kind since a day (touchless figures,
# the approval controls, who vouched for an account), an account's sessions, the expired one-off values, and a
# vendor by its ERP number (the JD Edwards supplier sync).
_INDEXES_20 = (
    "CREATE INDEX IF NOT EXISTS invoices_export_batch ON invoices (export_batch)",
    "CREATE INDEX IF NOT EXISTS feedback_invoice ON feedback (invoice_id)",
    "CREATE INDEX IF NOT EXISTS events_action_at ON events (action, created_at)",
    "CREATE INDEX IF NOT EXISTS sessions_user ON sessions (user_id)",
    "CREATE INDEX IF NOT EXISTS ephemeral_expires ON ephemeral (expires_at)",
    "CREATE INDEX IF NOT EXISTS vendors_erp_id ON vendors (erp_id)",
)
# Schema 24's indexes (D-156): the events of a period counted (Activity's last 7 days, the controls report) and the
# people the events are by (Activity's Person filter, an event by one person), each read off the index alone, not the
# events table (400,000 events at 100,000 invoices); the ERP register's invoices of some numbers (the JD Edwards
# duplicate check of a batch), not the whole register (200,000 rows).
_REGISTER_BY_NUMBER = "CREATE INDEX IF NOT EXISTS erp_invoices_number ON erp_invoices (number_key)"
_INDEXES_24 = (
    "CREATE INDEX IF NOT EXISTS events_created ON events (created_at, action)",
    "CREATE INDEX IF NOT EXISTS events_actor ON events (actor)",
    _REGISTER_BY_NUMBER,
)
# Schema 25's index (B-1714): a lesson's corrections, and the ones agreeing with it, counted off the index alone
# (``lessons._LESSON``: the review screen's "Learned" hints), not every approval that saw its situation (220,000 for
# the biggest vendor at 100,000 invoices). It replaces the index by situation alone, a part of it.
_INDEXES_25 = (
    "DROP INDEX IF EXISTS lesson_approvals_situation",
    "CREATE INDEX IF NOT EXISTS lesson_approvals_counted ON lesson_approvals (situation, corrected, value)",
)
# Schema 26's indexes (D-168): the import queue's files waiting or being read, and those of the same content (a file
# is never read twice), off the index; a run's files and batches (the Process page's card).
_INDEXES_26 = (
    "CREATE INDEX IF NOT EXISTS import_files_status ON import_files (status, sha256)",
    "CREATE INDEX IF NOT EXISTS import_files_batch ON import_files (batch, status)",
    "CREATE INDEX IF NOT EXISTS import_batches_run ON import_batches (run)",
)
# The one-off clean-ups of the learning memory ``_feedback_cleanup`` has run on a database (settings.feedback_cleanup):
# a new clean-up added there changes it, so it runs once on every database.
FEEDBACK_CLEANUP = "1"

# In the order of approval (``seq``), whatever the clock says (B-780).
_SNAPSHOTS_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS template_snapshots (seq INTEGER PRIMARY KEY AUTOINCREMENT, "
    "invoice_id INTEGER NOT NULL UNIQUE, supplier_key TEXT NOT NULL, before_json TEXT, after_json TEXT, "
    "at TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS template_snapshots_supplier ON template_snapshots (supplier_key, seq)",
)


def _put_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(dialect.upsert("settings", ("key", "value"), ("key",)), (key, value))


# --- The numbered steps ---------------------------------------------------------------------------------------------


def _v2_vendor_keys(store: Any, conn: sqlite3.Connection) -> None:
    """Vendor matching ignores accents, "&"/"and" and more legal forms."""
    for table in ("invoices", "feedback"):
        rows = conn.execute(f"SELECT id, vendor_name FROM {table}").fetchall()
        conn.executemany(
            f"UPDATE {table} SET vendor_key = ? WHERE id = ?",
            [(vendor_key(r["vendor_name"] or ""), r["id"]) for r in rows],
        )


def _v3_export_batch(store: Any, conn: sqlite3.Connection) -> None:
    """Invoices remember the ERP export batch they went out in."""
    if "export_batch" not in dialect.columns(conn, "invoices"):
        dialect.add_column(conn, "invoices", "export_batch", "INTEGER")


def _v4_po_key(store: Any, conn: sqlite3.Connection) -> None:
    """Invoices remember the purchase order they quote."""
    if "po_key" not in dialect.columns(conn, "invoices"):
        dialect.add_column(conn, "invoices", "po_key", "TEXT NOT NULL DEFAULT ''")
    rows = conn.execute("SELECT id, ai_output, final_output FROM invoices").fetchall()
    for r in rows:
        coding = json.loads(r["final_output"] or r["ai_output"] or "{}")
        if coding.get("po_number"):
            conn.execute("UPDATE invoices SET po_key = ? WHERE id = ?", (po_key(coding["po_number"]), r["id"]))


def _v5_due_date(store: Any, conn: sqlite3.Connection) -> None:
    """Invoices remember when they are due (printed, from the terms, or the default)."""
    if "due_date" not in dialect.columns(conn, "invoices"):
        dialect.add_column(conn, "invoices", "due_date", "TEXT")
    for r in conn.execute("SELECT id, ai_output, final_output FROM invoices").fetchall():
        coding = json.loads(r["final_output"] or r["ai_output"] or "{}")
        if coding:
            conn.execute("UPDATE invoices SET due_date = ? WHERE id = ?", (_due(coding), r["id"]))


def _v6_second_approval(store: Any, conn: sqlite3.Connection) -> None:
    """The second approval above the approval limit."""
    columns = dialect.columns(conn, "invoices")
    for column in ("second_reviewer", "second_reviewed_at"):
        if column not in columns:
            dialect.add_column(conn, "invoices", column, "TEXT")


def _v7_amount_only(store: Any, conn: sqlite3.Connection) -> None:
    """PO lines with an amount but no quantity (services, lump sums)."""
    if "amount_only" not in dialect.columns(conn, "po_lines"):
        dialect.add_column(conn, "po_lines", "amount_only", "INTEGER NOT NULL DEFAULT 0")


def _v8_vendor_master(store: Any, conn: sqlite3.Connection) -> None:
    """The vendor master imported from the ERP."""
    columns = dialect.columns(conn, "vendors")
    for column, kind in (
        ("erp_id", "TEXT NOT NULL DEFAULT ''"),
        ("terms", "TEXT NOT NULL DEFAULT ''"),
        ("default_gl", "TEXT NOT NULL DEFAULT ''"),
        ("in_master", "INTEGER NOT NULL DEFAULT 0"),
    ):
        if column not in columns:
            dialect.add_column(conn, "vendors", column, kind)


def _v9_parked(store: Any, conn: sqlite3.Connection) -> None:
    """Parked invoices: why, and when to follow up."""
    columns = dialect.columns(conn, "invoices")
    for column in ("parked_reason", "follow_up"):
        if column not in columns:
            dialect.add_column(conn, "invoices", column, "TEXT")


def _v12_currency(store: Any, conn: sqlite3.Connection) -> None:
    """Approvals before this version kept the AI's currency, not the reviewer's."""
    for r in conn.execute("SELECT id, final_output FROM invoices WHERE final_output IS NOT NULL").fetchall():
        currency = str((json.loads(r["final_output"]) or {}).get("currency") or "").strip().upper()
        if currency:
            conn.execute("UPDATE invoices SET currency = ? WHERE id = ?", (currency, r["id"]))


def _v13_credit_notes_not_due(store: Any, conn: sqlite3.Connection) -> None:
    """Credit notes were given a due date like invoices."""
    conn.execute("UPDATE invoices SET due_date = NULL WHERE grand_total <= 0")


def _v20_indexes(store: Any, conn: sqlite3.Connection) -> None:
    """The lookups that read a whole table, a year of invoices on the server (B-1581)."""
    for sql in _INDEXES_20:
        conn.execute(sql)


def _v24_indexes(store: Any, conn: sqlite3.Connection) -> None:
    """The events of a period and of a person, off an index (D-156)."""
    for sql in _INDEXES_24:
        conn.execute(sql)


def _v25_indexes(store: Any, conn: sqlite3.Connection) -> None:
    """A lesson's approvals counted off an index (B-1714)."""
    for sql in _INDEXES_25:
        conn.execute(sql)


def _v26_indexes(store: Any, conn: sqlite3.Connection) -> None:
    """The import queue (D-168): its tables are made by _SCHEMA, its indexes here."""
    for sql in _INDEXES_26:
        conn.execute(sql)


# --- The steps whatever the version ---------------------------------------------------------------------------------


def _po_key_index(store: Any, conn: sqlite3.Connection) -> None:
    """The invoices by the purchase order they quote (its column: the step to 4)."""
    conn.execute("CREATE INDEX IF NOT EXISTS invoices_po ON invoices (po_key)")


def _page_read_columns(store: Any, conn: sqlite3.Connection) -> None:
    """The page reader's queue remembers who is reading an invoice, how many reads were cut off (added within schema
    15) and when a read was renewed on the database's clock (B-1770)."""
    columns = dialect.columns(conn, "page_reads")
    for column, kind in (("reader", "TEXT NOT NULL DEFAULT ''"), ("tries", "INTEGER NOT NULL DEFAULT 0"),
                         ("renewed", "REAL")):  # fmt: skip
        if columns and column not in columns:
            dialect.add_column(conn, "page_reads", column, kind)


def _verification_columns(store: Any, conn: sqlite3.Connection) -> None:
    """The AI reviewer's queue counts reviews the model server cut off before any answer, and remembers who is
    reviewing an invoice, like the page reader's."""
    columns = dialect.columns(conn, "verifications")
    for column, kind in (("tries", "INTEGER NOT NULL DEFAULT 0"), ("reader", "TEXT NOT NULL DEFAULT ''"),
                         ("renewed", "REAL")):  # fmt: skip
        if columns and column not in columns:
            dialect.add_column(conn, "verifications", column, kind)


def _suspended_at(store: Any, conn: sqlite3.Connection) -> None:
    """A suspended supplier remembers when, so it needs a fresh clean streak since then."""
    columns = dialect.columns(conn, "supplier_profiles")
    if columns and "suspended_at" not in columns:
        dialect.add_column(conn, "supplier_profiles", "suspended_at", "TEXT")
        # A vendor suspended by an older version: when, from the Activity log (else its last change), so the
        # clean streak it had before still doesn't count.
        conn.execute(
            "UPDATE supplier_profiles SET suspended_at = COALESCE((SELECT MAX(e.created_at) FROM events e "
            f"WHERE e.action = 'autonomy_suspended' AND CASE WHEN {dialect.json_valid('e.detail')} THEN "
            f"{dialect.json_get('e.detail', '$.key')} END = supplier_profiles.key), updated_at) WHERE state = "
            "'suspended'"
        )


def _vendor_numbers(store: Any, conn: sqlite3.Connection) -> None:
    """The vendor list keeps each vendor's QST and PST numbers, province and the bank account JD Edwards pays it on
    (the JDE API's sync, D-104), to check what an invoice prints against the vendor; erp_hold: JDE's (D-105)."""
    columns = dialect.columns(conn, "vendors")
    for column in ("qst_number", "pst_number", "province", "erp_bank", "erp_hold"):
        if columns and column not in columns:
            dialect.add_column(conn, "vendors", column, "TEXT NOT NULL DEFAULT ''")
    if columns:  # JD Edwards' "N" is no hold: a sync before B-2081 kept it as one (B-2158)
        conn.execute("UPDATE vendors SET erp_hold = '' WHERE erp_hold IN ('N', 'n')")


def _cost_center_managers(store: Any, conn: sqlite3.Connection) -> None:
    """Each cost center's manager, who confirms the cost allocation when AP asks (D-138), and in server mode the cost
    centers a Manager account answers for."""
    columns = dialect.columns(conn, "cost_centers")
    for column in ("manager_name", "manager_email"):
        if columns and column not in columns:
            dialect.add_column(conn, "cost_centers", column, "TEXT NOT NULL DEFAULT ''")


def _account_columns(store: Any, conn: sqlite3.Connection) -> None:
    """The accounts' columns added since (a Manager's cost centers, the authenticator app's: ``users.add_columns``)."""
    add_columns(conn)


def _picture_sha(store: Any, conn: sqlite3.Connection) -> None:
    """An invoice made from a picture remembers the picture's SHA-256 (intake/pictures.py), so the same photo sent
    again is found."""
    if "original_sha256" not in dialect.columns(conn, "invoices"):
        dialect.add_column(conn, "invoices", "original_sha256", "TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS invoices_original_sha ON invoices (original_sha256)")


def _lookup_indexes(store: Any, conn: sqlite3.Connection) -> None:
    """The lookups of every invoice processed and page drawn: the same file (find_by_hash), the same total under
    another vendor (duplicates_elsewhere), an invoice's newest event of a kind (latest_events) (B-730)."""
    conn.execute("CREATE INDEX IF NOT EXISTS invoices_sha ON invoices (file_sha256)")
    conn.execute("CREATE INDEX IF NOT EXISTS invoices_total ON invoices (grand_total)")
    conn.execute("CREATE INDEX IF NOT EXISTS events_action ON events (action, invoice_id)")


def _register_columns(store: Any, conn: sqlite3.Connection) -> None:
    """The ERP register counts a bill entered more than once in the ERP, for the duplicate audit; its search keys
    (schema 22, D-157), its supplier numbers and JD Edwards' vouchers (D-181), each looked for on the columns it had."""
    columns = dialect.columns(conn, "erp_invoices")
    if columns and "copies" not in columns:
        dialect.add_column(conn, "erp_invoices", "copies", "INTEGER NOT NULL DEFAULT 1")
    add_register_keys(conn, columns)
    add_jde_register(conn, columns)


def _register_by_number(store: Any, conn: sqlite3.Connection) -> None:
    """The ERP register by invoice number (schema 24's index) also on a database that recorded 24 before the index
    was one of the step's (B-1975)."""
    conn.execute(_REGISTER_BY_NUMBER)


def _billing_by_vendor(store: Any, conn: sqlite3.Connection) -> None:
    """Month-end's billing lines kept by vendor among the shared answers (``report_answers``, ``billing/<key>``,
    D-177), kept by invoice since (``billing_facts``, D-194): the vendors' rows, read by no one now, removed (about
    63 MB at 300,000 invoices). Looked for first: a start with nothing to do takes no lock (B-1996)."""
    vendors = "FROM report_answers WHERE name >= 'billing/' AND name < 'billing0'"
    if conn.execute(f"SELECT 1 {vendors} LIMIT 1").fetchone():
        conn.execute(f"DELETE {vendors}")


def _register_search(store: Any, conn: sqlite3.Connection) -> None:
    """The quick search's index of the ERP register (FTS5, ``register_search``), built again when it is not in step
    with the register: made first, or left behind by a write that did not keep it (D-194). After every step that
    writes the register."""
    build_register_search(conn)


def _answer_work(store: Any, conn: sqlite3.Connection) -> None:
    """A shared answer keeps how long its work lasted (``report_answers.lasted``, seconds): its settle grows with it
    (D-197). One kept before has none, and settles for the caller's settle alone."""
    if "lasted" not in dialect.columns(conn, "report_answers"):
        dialect.add_column(conn, "report_answers", "lasted", "REAL")


def _po_open_amount(store: Any, conn: sqlite3.Connection) -> None:
    """A PO line keeps what the ERP had left open on it when the POs were imported (the import's optional open
    amount), for what is left to expense on the review screen."""
    if "open_amount" not in dialect.columns(conn, "po_lines"):
        dialect.add_column(conn, "po_lines", "open_amount", "REAL")


def _snapshots_by_seq(store: Any, conn: sqlite3.Connection) -> None:
    """Template snapshots go by the order of approval (``seq``), not by a time to the second (B-780): a table made
    before it (that version, unreleased) is rebuilt, its snapshots kept in the order they were taken."""
    columns = dialect.columns(conn, "template_snapshots")
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
            f"SELECT invoice_id, supplier_key, before_json, after_json, at FROM template_snapshots_old ORDER BY at, "
            f"{dialect.ROWID}"
        )
        conn.execute("DROP TABLE template_snapshots_old")


def _vendor_key_rules(store: Any, conn: sqlite3.Connection) -> None:
    """Vendor keys stored under older vendor-matching rules (e.g. before "Limitée" and "s.e.c." were ignored) are
    recomputed, so the vendor's invoices, lessons, master record and POs still match (the rules are recorded on
    their own)."""
    row = conn.execute("SELECT value FROM settings WHERE key = 'vendor_key_rules'").fetchone()
    if (row["value"] if row else "") != VENDOR_KEY_RULES:
        store._recompute_vendor_keys(conn)
        conn.execute("DELETE FROM past_lines")  # (the lessons by line, D-178: worked out again)
        _forget_learning(store.path)
        _put_setting(conn, "vendor_key_rules", VENDOR_KEY_RULES)


def _feedback_cleanup(store: Any, conn: sqlite3.Connection) -> None:
    """One-off clean-ups of the learning memory: each scans the whole feedback table, so they run once per database
    (recorded as settings.feedback_cleanup, like vendor_key_rules), not on every Store() the background workers make
    (B-727)."""
    row = conn.execute("SELECT value FROM settings WHERE key = 'feedback_cleanup'").fetchone()
    if (row["value"] if row else "") != FEEDBACK_CLEANUP:
        # Older versions recorded AP Coder's own approvals (nobody checked them) as lessons, counted in the
        # accuracy: withdrawn, as nothing is recorded for them any more.
        conn.execute("DELETE FROM feedback WHERE reviewer = ?", (AUTONOMOUS_REVIEWER,))
        # Older versions recorded a GL account filled in where the AI proposed none as a correction: it is the
        # reviewer's coding (B-650), recounted (the learning memory reads both alike).
        conn.execute(
            "UPDATE feedback SET outcome = ? WHERE outcome = ? AND suggested_gl IN ('', ?) "
            "AND COALESCE(suggested_cc, '') IN ('', ?)",
            (CODED, CORRECTED, UNASSIGNED, UNASSIGNED),
        )
        conn.execute("DELETE FROM past_lines")
        _forget_learning(store.path)
        _put_setting(conn, "feedback_cleanup", FEEDBACK_CLEANUP)


def _gl_meals(store: Any, conn: sqlite3.Connection) -> None:
    """A GL account can be marked "Meals & entertainment (50% tax credit)" (D-90)."""
    columns = dialect.columns(conn, "gl_accounts")
    if columns and "meals" not in columns:
        dialect.add_column(conn, "gl_accounts", "meals", "INTEGER NOT NULL DEFAULT 0")


def _feedback_tax_codes(store: Any, conn: sqlite3.Connection) -> None:
    """Each approved line remembers its tax code, learned from like its GL account (D-70): filled in from the approved
    invoices. Who decided it and where the invoice was supplied (D-80) came later in schema 17: a database already
    at 17 without them gets them, and every code filled again (a kept learned code or a guess is no vote)."""
    columns = dialect.columns(conn, "feedback")
    missing = [c for c in ("final_tax_code", "tax_source", "tax_province", "tax_uncollected") if c not in columns]
    for column in missing:
        dialect.add_column(conn, "feedback", column, "TEXT NOT NULL DEFAULT ''")
    if missing:
        store._fill_tax_codes(conn)


def _reading_as_d89(store: Any, conn: sqlite3.Connection) -> None:
    """The invoices read before D-89 scored again (``old_readings``), once per database."""
    reading_as_d89(conn)


def _seen_files(store: Any, conn: sqlite3.Connection) -> None:
    """What the pick-up read of the folder: each invoice's own file to start with (D-155, ``folder``)."""
    fill_seen_files(conn)


EVERY_TIME = None  # a step's number when it runs whatever the version

STEPS: tuple[tuple[int | None, Step], ...] = (
    (2, _v2_vendor_keys),
    (3, _v3_export_batch),
    (4, _v4_po_key),
    (EVERY_TIME, _po_key_index),
    (5, _v5_due_date),
    (6, _v6_second_approval),
    (7, _v7_amount_only),
    (8, _v8_vendor_master),
    (9, _v9_parked),
    (12, _v12_currency),
    (13, _v13_credit_notes_not_due),
    (20, _v20_indexes),
    (24, _v24_indexes),
    (25, _v25_indexes),
    (26, _v26_indexes),
    (EVERY_TIME, _page_read_columns),
    (EVERY_TIME, _verification_columns),
    (EVERY_TIME, _suspended_at),
    (EVERY_TIME, _vendor_numbers),
    (EVERY_TIME, _cost_center_managers),
    (EVERY_TIME, _account_columns),
    (EVERY_TIME, _picture_sha),
    (EVERY_TIME, _lookup_indexes),
    (EVERY_TIME, _register_columns),
    (EVERY_TIME, _register_by_number),
    (EVERY_TIME, _po_open_amount),
    (EVERY_TIME, _snapshots_by_seq),
    (EVERY_TIME, _vendor_key_rules),
    (EVERY_TIME, _feedback_cleanup),
    (EVERY_TIME, _gl_meals),
    (EVERY_TIME, _feedback_tax_codes),
    (EVERY_TIME, _reading_as_d89),
    (EVERY_TIME, _seen_files),
    (EVERY_TIME, _billing_by_vendor),
    (EVERY_TIME, _register_search),
    (EVERY_TIME, _answer_work),
)
MIGRATIONS: dict[int, Step] = {number: step for number, step in STEPS if number is not EVERY_TIME}


def recorded_version(conn: sqlite3.Connection) -> int:
    """The schema version the database records (1: older than the setting, or a new database's)."""
    row = conn.execute("SELECT value FROM settings WHERE key = 'schema_version'").fetchone()
    return int(row["value"]) if row else 1


def migrate(store: Any, conn: sqlite3.Connection) -> None:
    """``STEPS`` in order: each numbered one the database is older than, and every one with no number; then the
    database records SCHEMA_VERSION."""
    version = recorded_version(conn)
    for number, step in STEPS:
        if number is EVERY_TIME or version < number:
            step(store, conn)
    if version < SCHEMA_VERSION:
        _put_setting(conn, "schema_version", str(SCHEMA_VERSION))
