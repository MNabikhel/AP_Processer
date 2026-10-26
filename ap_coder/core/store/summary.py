"""The invoices' summary (D-110): what the pages list, search and report on, worked out once per change.

The ``invoices`` table keeps each invoice's coding, checks and metadata as JSON (about 14 KB an invoice), and its
columns added over the years sit after them on disk: listing a year of invoices, or totalling them for a report,
read and decoded every one of them on every page. The summary keeps what those pages need, one narrow row per
invoice:

* ``invoice_summary``: the columns the lists show, copied as they are; what the invoice directory works out of the
  coding with SQLite's JSON functions (the same expressions); the quick search's keys; and what the reports need
  of the coding, the checks and the approval (``COMPUTED``, by the reports' own functions: ``taxreturn.tax_facts``,
  ``insights.invoice_facts``...). Nothing in it depends on a setting except the posting
  lines of an invoice coded by an older version (``legacy``), built again with the tax setup (``exports.distribution``).
* ``summary_spend``: the cost lines of each approved invoice (``spend.spend_parts``), one per GL account and cost
  center.
* ``invoice_search``: the quick search's FTS5 index (trigram) of the vendor and file names and the invoice numbers,
  when this Python's SQLite has FTS5 (``search.find`` reads the summary itself otherwise).

Triggers on ``invoices`` (and on the approval events and the tax setup the summary depends on) put the invoice in
``summary_dirty`` in the same transaction as the change, however it is made. The store works the dirty ones out
again before that transaction commits (``StoreCore._conn``), and a page reading the summary does so first
(``_summary_fresh``), so the summary is never behind: it is the one source those pages read, with no other path. The
migration builds it in full, and again whenever ``SUMMARY_RULES`` changes.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from ._base import APPROVED, FAILED, PENDING, REJECTED, _in_list

# What the summary works out, by version: a change to it (a new column, a report's facts read differently, the
# vendor-matching or invoice-number rules) changes this, and every database builds its summary again once.
SUMMARY_RULES = "1"

# The columns of ``invoices`` the summary keeps as they are, with their declared types (so they compare and sort as
# the columns of ``invoices`` do).
MIRRORED = (
    ("status", "TEXT"), ("file_name", "TEXT"), ("vendor_name", "TEXT"), ("vendor_key", "TEXT"),
    ("invoice_number", "TEXT"), ("invoice_date", "TEXT"), ("due_date", "TEXT"), ("currency", "TEXT"),
    ("grand_total", "REAL"), ("po_key", "TEXT"), ("created_at", "TEXT"), ("reviewed_at", "TEXT"), ("reviewer", "TEXT"),
    ("second_reviewer", "TEXT"), ("second_reviewed_at", "TEXT"), ("export_batch", "INTEGER"),
    ("parked_reason", "TEXT"), ("follow_up", "TEXT"), ("error", "TEXT"), ("requires_review", "INTEGER"),
    ("model_confidence", "REAL"), ("adjusted_confidence", "REAL"), ("edits", "TEXT"),
)  # fmt: skip
MIRROR = tuple(name for name, _ in MIRRORED)

# The coding an invoice is filtered on in the directory: the reviewer's once approved, else AP Coder's (never
# malformed JSON), and what the directory shows of it (``directory.ROWS_SQL`` reads these columns).
_DOC = "CASE WHEN json_valid(COALESCE(final_output, ai_output)) THEN COALESCE(final_output, ai_output) END"
_GL = (
    "(SELECT group_concat(g, ', ') FROM (SELECT DISTINCT json_extract(l.value, '$.predicted_gl_code') AS g "
    "FROM json_each(doc, '$.line_items') AS l WHERE g IS NOT NULL AND g NOT IN ('', 'UNASSIGNED') ORDER BY g))"
)
_FIRST_GL = "(SELECT MIN(json_extract(l.value, '$.predicted_gl_code')) FROM json_each(doc, '$.line_items') AS l " \
            "WHERE json_extract(l.value, '$.predicted_gl_code') NOT IN ('', 'UNASSIGNED'))"  # fmt: skip
# Each line's GL account as its coding has it (a JSON list), for the directory's GL filter.
_LINE_GLS = (
    "(SELECT json_group_array(json_extract(l.value, '$.predicted_gl_code')) FROM json_each(doc, '$.line_items') AS l)"
)

DERIVED = (
    # the dashboard's demo invoices (``demo_count``), as they were counted before (B-...): the text of their metadata
    ("demo", "INTEGER NOT NULL DEFAULT 0", "COALESCE(meta LIKE '%\"demo\": true%', 0)"),
    ("gst_number", "", "CASE WHEN json_valid(final_output) THEN json_extract(final_output, "
                       "'$.gst_hst_registration_number') END"),
    ("po_number", "TEXT", "NULLIF(TRIM(CAST(json_extract(doc, '$.po_number') AS TEXT)), '')"),
    ("subtotal", "REAL", "CAST(json_extract(doc, '$.subtotal') AS REAL)"),
    ("tax_total", "REAL", "CAST(json_extract(doc, '$.tax_total') AS REAL)"),
    ("gl_codes", "", _GL),
    ("first_gl", "", _FIRST_GL),
    ("line_gls", "TEXT NOT NULL DEFAULT '[]'", _LINE_GLS),
)  # fmt: skip
# Worked out in Python (``_summary_row``).
COMPUTED = (
    ("plain_name", "TEXT NOT NULL DEFAULT ''"),  # the vendor and file names as the quick search compares them
    ("number_key", "TEXT NOT NULL DEFAULT ''"),  # the invoice number as it compares (``norm_invoice_number``)
    ("raw_number", "TEXT NOT NULL DEFAULT ''"),  # its letters and digits only (``search._raw``)
    ("tax_date", "TEXT NOT NULL DEFAULT ''"),  # the date the sales-tax report files it under
    ("f_date", "TEXT NOT NULL DEFAULT ''"),  # the approved coding's invoice date (spend, self-assessment)
    ("bill_vendor", "TEXT"),  # the approved coding's bill (``accruals._bill``): vendor, number, credit
    ("bill_number", "TEXT"),
    ("bill_credit", "INTEGER"),
    ("po_billed", "REAL"),  # the subtotal it bills (``purchase_orders``): its coding's, as approved or as read
    ("p_vendor", "TEXT"),  # the approved coding's vendor and currency, as Spend reads them
    ("p_currency", "TEXT"),
    # Insights (``insights.invoice_facts``, ``approval_facts``, ``payment_facts``) and Vendors (``workload_facts``)
    ("i_seconds", "REAL"),
    ("i_codes", "TEXT NOT NULL DEFAULT '[]'"),
    ("i_cost", "TEXT"),
    ("a_at", "TEXT"),
    ("a_corrected", "INTEGER"),
    ("a_filled", "INTEGER"),
    ("a_bulk", "INTEGER"),
    ("pay", "TEXT"),
    ("pay_terms", "TEXT"),
    ("pay_vendor", "TEXT"),
    ("w_name", "TEXT"),
    ("w_key", "TEXT"),
    ("w_codes", "TEXT NOT NULL DEFAULT '[]'"),
    ("w_corrected", "INTEGER"),
    # The review queue's bulk approval (``bulk.queue_facts``)
    ("q_audit", "INTEGER"),
    ("q_person", "INTEGER"),
    # Sales tax (``taxreturn.tax_facts``, ``self_assessment_facts``), JSON
    ("t_facts", "TEXT"),
    ("s_facts", "TEXT"),
    ("legacy", "INTEGER NOT NULL DEFAULT 0"),  # approved without its posting lines stored: built with the tax setup
    ("broken", "INTEGER NOT NULL DEFAULT 0"),  # its JSON could not be read: the reports read the invoice itself
)

# The longer worked-out values, in tables of their own beside the summary's narrow rows: read by the reports that need
# them alone (``summary_rows`` joins a table only when its columns are asked for).
SIDE_TABLES = {"summary_facts": ("i_cost", "pay", "t_facts", "s_facts")}
# An invoice's checks' codes (``i_codes``) are kept one row per code (``summary_codes``): Insights counts them.
SIDE = (*(c for columns in SIDE_TABLES.values() for c in columns), "i_codes")

SUMMARY_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS invoice_summary (id INTEGER PRIMARY KEY, "
    + ", ".join(f"{name} {kind}" for name, kind in MIRRORED)
    + ", "
    + ", ".join(f"{name} {kind}".strip() for name, kind, _ in DERIVED)
    + ", "
    + ", ".join(f"{name} {kind}" for name, kind in COMPUTED if name not in SIDE)
    + ")",
    *(
        f"CREATE TABLE IF NOT EXISTS {table} (id INTEGER PRIMARY KEY, "
        + ", ".join(f"{name} {kind}" for name, kind in COMPUTED if name in columns)
        + ")"
        for table, columns in SIDE_TABLES.items()
    ),
    # Each invoice's error and warning codes (``insights.invoice_facts``): how many times each, and where the first is
    # among them (``pos``), for Insights' most common findings.
    "CREATE TABLE IF NOT EXISTS summary_codes (id INTEGER NOT NULL, code, n INTEGER NOT NULL, pos INTEGER NOT NULL, "
    "PRIMARY KEY (id, pos))",
    "CREATE INDEX IF NOT EXISTS summary_codes_code ON summary_codes (code, n, id, pos)",
    "CREATE INDEX IF NOT EXISTS summary_status_reviewed ON invoice_summary (status, reviewed_at)",
    "CREATE INDEX IF NOT EXISTS summary_status_second ON invoice_summary (status, second_reviewed_at)",
    "CREATE INDEX IF NOT EXISTS summary_vendor ON invoice_summary (vendor_key)",
    "CREATE INDEX IF NOT EXISTS summary_ready ON invoice_summary (status, export_batch, reviewed_at)",
    "CREATE INDEX IF NOT EXISTS summary_batch ON invoice_summary (export_batch)",
    "CREATE INDEX IF NOT EXISTS summary_demo ON invoice_summary (demo)",
    "CREATE INDEX IF NOT EXISTS summary_edits ON invoice_summary (status, edits)",
    "CREATE INDEX IF NOT EXISTS summary_tax_date ON invoice_summary (tax_date)",
    "CREATE INDEX IF NOT EXISTS summary_f_date ON invoice_summary (f_date)",
    "CREATE INDEX IF NOT EXISTS summary_number ON invoice_summary (number_key)",
    "CREATE INDEX IF NOT EXISTS summary_total ON invoice_summary (grand_total)",
    "CREATE INDEX IF NOT EXISTS summary_po ON invoice_summary (po_key)",
    "CREATE INDEX IF NOT EXISTS summary_bill ON invoice_summary (bill_number)",
    "CREATE INDEX IF NOT EXISTS summary_legacy ON invoice_summary (legacy)",
    "CREATE INDEX IF NOT EXISTS summary_broken ON invoice_summary (broken)",
    # An approved invoice's cost lines (``spend.spend_parts``): the lines of one GL account and cost center summed,
    # in the order of the first of them (``part``); ``sizes`` sums their amounts' sizes, ``cents`` their amounts each
    # rounded to the cent, ``amounts`` lists them (an invoice not in CAD: converted line by line, ``spend.in_cad``).
    "CREATE TABLE IF NOT EXISTS summary_spend (invoice_id INTEGER NOT NULL, part INTEGER NOT NULL, "
    "gl_code TEXT NOT NULL, cost_center TEXT NOT NULL, amount REAL NOT NULL, sizes REAL NOT NULL, "
    "cents REAL NOT NULL, amounts TEXT, PRIMARY KEY (invoice_id, part))",
    "CREATE TABLE IF NOT EXISTS summary_dirty (id INTEGER PRIMARY KEY)",
    "CREATE INDEX IF NOT EXISTS summary_vendors ON invoice_summary (status, vendor_key, vendor_name)",
    # The learning memory's lessons by outcome, counted as they are added and removed (``feedback_counted``): the
    # review queue's accuracy and the memory's stamp read it instead of counting the lessons.
    "CREATE TABLE IF NOT EXISTS feedback_counts (outcome TEXT PRIMARY KEY, n INTEGER NOT NULL)",
    # The review queue's coding accuracy counts the lessons by outcome (``line_metrics``).
    "CREATE INDEX IF NOT EXISTS feedback_outcome ON feedback (outcome)",
    # The Vendors page's lessons by vendor (``vendor_summaries``), counted on an index.
    "CREATE INDEX IF NOT EXISTS feedback_vendor_outcome ON feedback (vendor_key, outcome)",
)

# What puts an invoice in ``summary_dirty``: any change to it, to one of its approval events (Insights counts the
# latest approval, the tax report the issues it left open), and to the tax setup (an invoice coded by an older
# version has its posting lines built again with it). The only triggers AP Coder makes (``backups``).
_DIRTY = "INSERT OR IGNORE INTO summary_dirty (id) VALUES"
_LEGACY = "INSERT OR IGNORE INTO summary_dirty (id) SELECT id FROM invoice_summary WHERE legacy = 1"
SUMMARY_TRIGGERS = {
    "summary_invoice_added": f"CREATE TRIGGER summary_invoice_added AFTER INSERT ON invoices BEGIN {_DIRTY} "
                             "(NEW.id); END",
    "summary_invoice_changed": f"CREATE TRIGGER summary_invoice_changed AFTER UPDATE ON invoices BEGIN {_DIRTY} "
                               f"(OLD.id); {_DIRTY} (NEW.id); END",
    "summary_invoice_deleted": f"CREATE TRIGGER summary_invoice_deleted AFTER DELETE ON invoices BEGIN {_DIRTY} "
                               "(OLD.id); END",
    "summary_approval_added": "CREATE TRIGGER summary_approval_added AFTER INSERT ON events WHEN NEW.action = "
                              f"'approved' AND NEW.invoice_id IS NOT NULL BEGIN {_DIRTY} (NEW.invoice_id); END",
    "summary_approval_changed": "CREATE TRIGGER summary_approval_changed AFTER UPDATE ON events WHEN 'approved' IN "
                                f"(OLD.action, NEW.action) BEGIN {_DIRTY} (OLD.invoice_id); {_DIRTY} "
                                "(NEW.invoice_id); END",
    "summary_approval_deleted": "CREATE TRIGGER summary_approval_deleted AFTER DELETE ON events WHEN OLD.action = "
                                f"'approved' AND OLD.invoice_id IS NOT NULL BEGIN {_DIRTY} (OLD.invoice_id); END",
    "summary_lesson_added": "CREATE TRIGGER summary_lesson_added AFTER INSERT ON feedback BEGIN INSERT INTO "
                            "feedback_counts (outcome, n) VALUES (NEW.outcome, 1) ON CONFLICT(outcome) DO UPDATE "
                            "SET n = n + 1; END",
    "summary_lesson_changed": "CREATE TRIGGER summary_lesson_changed AFTER UPDATE OF outcome ON feedback BEGIN "
                              "UPDATE feedback_counts SET n = n - 1 WHERE outcome = OLD.outcome; INSERT INTO "
                              "feedback_counts (outcome, n) VALUES (NEW.outcome, 1) ON CONFLICT(outcome) DO UPDATE "
                              "SET n = n + 1; END",
    "summary_lesson_deleted": "CREATE TRIGGER summary_lesson_deleted AFTER DELETE ON feedback BEGIN UPDATE "
                              "feedback_counts SET n = n - 1 WHERE outcome = OLD.outcome; END",
    "summary_tax_added": f"CREATE TRIGGER summary_tax_added AFTER INSERT ON tax_treatments BEGIN {_LEGACY}; END",
    "summary_tax_changed": f"CREATE TRIGGER summary_tax_changed AFTER UPDATE ON tax_treatments BEGIN {_LEGACY}; END",
    "summary_tax_deleted": f"CREATE TRIGGER summary_tax_deleted AFTER DELETE ON tax_treatments BEGIN {_LEGACY}; END",
}  # fmt: skip

FTS_SCHEMA = "CREATE VIRTUAL TABLE invoice_search USING fts5(plain_name, raw_number, tokenize = 'trigram')"
# What a ``summary_memo`` may depend on alone, so that it is worked out again only when that changed (an approval
# changes no vendor's invoice dates): a topic's key of an invoice's summary row (None: not in it).
_ACTIVE = ("review", "parked", "pending_approval", "approved")
TOPICS: dict[str, Callable[[dict[str, Any] | None], Any]] = {
    "dates": lambda r: (
        (r["vendor_key"], r["vendor_name"], r["invoice_date"], r["grand_total"], r["currency"])
        if r and r["status"] in _ACTIVE and r["vendor_key"]
        else None
    ),
    "batches": lambda r: (
        (r["export_batch"], r["currency"], r["grand_total"]) if r and r["export_batch"] is not None else None
    ),
    "status": lambda r: r["status"] if r else None,
    "approved_vendors": lambda r: (r["vendor_key"], r["vendor_name"]) if r and r["status"] == APPROVED else None,
    "workload": lambda r: (
        (r["status"] in (APPROVED, PENDING), r["w_key"], r["w_name"], r["w_codes"], r["w_corrected"])
        if r and r["status"] not in (FAILED, REJECTED)
        else None
    ),
}
_TOPIC_COLUMNS = ("id", "status", "vendor_key", "vendor_name", "invoice_date", "grand_total", "currency",
                  "export_batch", "w_key", "w_name", "w_codes", "w_corrected")  # fmt: skip
_MEMO: dict[tuple[str, str], tuple[Any, Any]] = {}  # ``summary_memo``: (database, name) -> (stamp, value)
_MEMO_LOCK = threading.Lock()
_COMPUTING: dict[tuple[str, str], threading.Lock] = {}  # one ``summary_memo`` worked out at a time, by name
MEMO_WAIT = 600.0  # seconds a page waits for another one working the same value out (then works it out itself)
_FTS: dict[str, bool] = {}  # whether this process's SQLite has FTS5 with the trigram tokenizer
_FTS_LOCK = threading.Lock()


# Reading many rows in one go (``fetch_rows``): Python's sqlite3 lets go of the GIL for every row it steps to and waits
# to get it back; with a few pages at once each doing so, reading 1,500 rows took 30 times longer than alone (5 queries
# a second for 4 threads, 160 for one). The rows come back as one JSON text instead: one step, however many rows. A
# REAL is written by printf with 17 digits, read back exactly (checked once: ``_one_go``), a TEXT with its JSON
# subtype dropped, a BLOB in hex.
_ONE_GO: dict[str, bool] = {}
_NAMES: dict[str, list[str]] = {}  # the columns of each SQL read in one go


def _one_go() -> bool:
    """Whether this SQLite writes a REAL with printf('%!.17g') that reads back as the same number (3.43+ does)."""
    if "ok" not in _ONE_GO:
        tricky = [0.1 + 0.2, 1 / 3, 2.0**-1074, 1.7976931348623157e308, 123456789.12345679, -0.0, 514.69, 1e-300]
        try:
            probe = sqlite3.connect(":memory:")
            try:
                written = [probe.execute("SELECT printf('%!.17g', ?)", (x,)).fetchone()[0] for x in tricky]
                probe.execute("SELECT json_group_array(json_array(1))").fetchone()
            finally:
                probe.close()
            _ONE_GO["ok"] = all(float(w) == x for w, x in zip(written, tricky, strict=True))
        except (sqlite3.Error, ValueError):
            _ONE_GO["ok"] = False
    return _ONE_GO["ok"]


def _cell(value: Any) -> Any:
    if type(value) is dict:
        return float(value["r"]) if "r" in value else bytes.fromhex(value["b"])
    return value


def fetch_rows(conn: sqlite3.Connection, sql: str, params: Any = ()) -> list[dict[str, Any]]:
    """The rows of the SELECT ``sql`` as dicts, read in one step (see above), in its order: what
    ``[dict(r) for r in conn.execute(sql, params)]`` gives, without the GIL handed back and forth for each row."""
    if not _one_go():
        return [dict(r) for r in conn.execute(sql, params)]
    names = _NAMES.get(sql)
    if names is None:
        if len(_NAMES) > 512:  # SQL made up of a page's filters: kept within bounds
            _NAMES.clear()
        names = _NAMES[sql] = [d[0] for d in conn.execute(f"SELECT * FROM ({sql}) LIMIT 0", params).description]
    cells = ", ".join(
        f"CASE typeof(q.\"{n}\") WHEN 'real' THEN json_object('r', printf('%!.17g', q.\"{n}\")) WHEN 'text' THEN "
        f"q.\"{n}\" || '' WHEN 'blob' THEN json_object('b', hex(q.\"{n}\")) ELSE q.\"{n}\" END"
        for n in names
    )
    found = conn.execute(f"SELECT json_group_array(json_array({cells})) FROM ({sql}) AS q", params).fetchone()[0]
    return [{n: _cell(v) for n, v in zip(names, row, strict=True)} for row in json.loads(found)]


def fts_available() -> bool:
    """Whether this Python's SQLite has FTS5 and its trigram tokenizer (SQLite 3.34+): CPython's own builds on
    Windows and macOS have; some Linux builds may not (the quick search then reads the summary itself)."""
    with _FTS_LOCK:
        if "ok" not in _FTS:
            try:
                probe = sqlite3.connect(":memory:")
                try:
                    probe.execute(FTS_SCHEMA)
                    _FTS["ok"] = True
                finally:
                    probe.close()
            except sqlite3.Error:
                _FTS["ok"] = False
        return _FTS["ok"]


_SELECT = (
    f"WITH coded AS (SELECT *, {_DOC} AS doc FROM invoices WHERE {_in_list('id')}) SELECT id, "
    + ", ".join(MIRROR)
    + ", ai_output, final_output, validation, meta, "
    + ", ".join(f"{sql} AS {name}" for name, _, sql in DERIVED)
    + " FROM coded"
)
_COLUMNS = ("id", *MIRROR, *(name for name, _, _ in DERIVED), *(name for name, _ in COMPUTED))
_MAIN = [n for n, c in enumerate(_COLUMNS) if c not in SIDE]
_SIDE = {t: [_COLUMNS.index("id"), *(_COLUMNS.index(c) for c in columns)] for t, columns in SIDE_TABLES.items()}
_INSERT = (f"INSERT INTO invoice_summary ({', '.join(_COLUMNS[n] for n in _MAIN)}) "
           f"VALUES ({', '.join('?' * len(_MAIN))})")  # fmt: skip
_INSERT_SIDE = {
    t: f"INSERT INTO {t} (id, {', '.join(columns)}) VALUES ({', '.join('?' * (len(columns) + 1))})"
    for t, columns in SIDE_TABLES.items()
}


def _tables(columns: str) -> str:
    """What ``summary_rows`` reads for ``columns``: the narrow rows, and the tables beside them that hold some."""
    words = set(re.findall(r"\w+", columns))
    side = [t for t, cols in SIDE_TABLES.items() if columns.strip() == "*" or words & set(cols)]
    return "invoice_summary" + "".join(f" LEFT JOIN {t} USING (id)" for t in side)


CHUNK = 400  # invoices worked out at a time


def _code_rows(invoice_id: int, codes: str | None) -> list[tuple[Any, ...]]:
    """(id, code, how many times, the position of its first) of an invoice's ``i_codes``."""
    found: dict[Any, list[int]] = {}
    for position, code in enumerate(json.loads(codes) if codes and codes != "[]" else ()):
        found.setdefault(code, [0, position])[0] += 1
    return [(invoice_id, code, n, position) for code, (n, position) in found.items()]


def _decoded(text: Any) -> Any:
    """A JSON column as ``Store.invoice_columns`` decodes it."""
    return json.loads(text) if text else None


class SummaryMixin:
    # --- The invoices' summary (D-110) -------------------------------------------------------------------------

    @staticmethod
    def _summary_schema(conn: sqlite3.Connection) -> None:
        """The summary's tables, indexes and triggers (made on every start, like ``_SCHEMA``); its FTS5 index when
        this SQLite has FTS5."""
        for sql in SUMMARY_SCHEMA:
            conn.execute(sql)
        made = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
        for name, sql in SUMMARY_TRIGGERS.items():
            if name not in made:
                conn.execute(sql)
        if fts_available():
            conn.execute(FTS_SCHEMA.replace("CREATE VIRTUAL TABLE", "CREATE VIRTUAL TABLE IF NOT EXISTS", 1))

    def _summary_migrate(self, conn: sqlite3.Connection) -> None:
        """The summary's tables (``_summary_schema``), built again in full when made by other rules (``SUMMARY_RULES``,
        or none: a database of schema 18 or older), with triggers as this version makes them; and the search index,
        when it was left behind by a computer whose SQLite has no FTS5."""
        from ..memory import VENDOR_KEY_RULES

        row = conn.execute("SELECT value FROM settings WHERE key = 'summary_rules'").fetchone()
        rules = f"{SUMMARY_RULES}/{VENDOR_KEY_RULES}"
        again = (row[0] if row else "") != rules
        if again:  # made again, as this version makes it (its columns, its triggers)
            for table in ("invoice_summary", *SIDE_TABLES, "summary_codes", "summary_spend",
                          *(("invoice_search",) if fts_available() else ())):  # fmt: skip
                conn.execute(f"DROP TABLE IF EXISTS {table}")
            for name in SUMMARY_TRIGGERS:
                conn.execute(f"DROP TRIGGER IF EXISTS {name}")
            conn.execute("DELETE FROM settings WHERE key = 'search_behind'")
        self._summary_schema(conn)
        if again:
            conn.execute("INSERT OR IGNORE INTO summary_dirty (id) SELECT id FROM invoices")
            conn.execute("DELETE FROM feedback_counts")
            conn.execute("INSERT INTO feedback_counts (outcome, n) SELECT outcome, COUNT(*) FROM feedback GROUP BY 1")
            for key, value in (("summary_rules", rules), ("summary_id", uuid.uuid4().hex)):  # its id: ``summary_memo``
                conn.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, value),
                )
        elif fts_available() and conn.execute("SELECT 1 FROM settings WHERE key = 'search_behind'").fetchone():
            conn.execute("DELETE FROM invoice_search")
            conn.execute("INSERT INTO invoice_search (rowid, plain_name, raw_number) "
                         "SELECT id, plain_name, raw_number FROM invoice_summary")  # fmt: skip
            conn.execute("DELETE FROM settings WHERE key = 'search_behind'")
        self._refresh_summary(conn)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        super()._migrate(conn)  # type: ignore[misc]  (StoreCore's)
        self._capture_migrate(conn)  # type: ignore[attr-defined]  (capture.py: kept compressed, D-110)
        self._summary_migrate(conn)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        """``StoreCore._conn``, the summary kept in step: before a change commits, the invoices it changed are worked
        out again, in the same transaction. A connection that changed nothing has nothing to do."""
        with super()._conn() as conn:  # type: ignore[misc]
            yield conn
            if conn.in_transaction:
                try:
                    self._summary_fresh(conn)
                except sqlite3.OperationalError as exc:
                    if "no such table: summary_dirty" not in str(exc):  # a database not made yet
                        raise

    def _summary_fresh(self, conn: sqlite3.Connection) -> None:
        """Before reading the summary: work out the invoices changed and not worked out yet (by a connection that is
        not the store's, such as a plain ``sqlite3`` one), so what is read is never behind."""
        if conn.execute("SELECT 1 FROM summary_dirty LIMIT 1").fetchone() is not None:
            self._refresh_summary(conn)

    def _refresh_summary(self, conn: sqlite3.Connection) -> None:
        """Work out again every invoice in ``summary_dirty``, in the transaction under way; the generation of each
        part of the summary that changed (``TOPICS``, and ``all``) is one more (``summary_memo``)."""
        treatments, changed = None, set()
        while True:
            ids = [r[0] for r in conn.execute(f"SELECT id FROM summary_dirty LIMIT {CHUNK}")]
            if not ids:
                for topic in sorted(changed | {"all"}) if treatments is not None else ():
                    conn.execute("INSERT INTO settings (key, value) VALUES (?, '1') ON CONFLICT(key) DO UPDATE SET "
                                 "value = CAST(value AS INTEGER) + 1", (f"summary_generation/{topic}",))  # fmt: skip
                return
            if treatments is None:
                treatments = self.tax_treatments_in(conn)
            changed |= self._summarize(conn, ids, treatments)
            conn.execute(f"DELETE FROM summary_dirty WHERE {_in_list('id')}", (json.dumps(ids),))

    def _summarize(self, conn: sqlite3.Connection, ids: list[int], treatments: dict[str, Any]) -> set[str]:
        """Work out ``ids`` again; returns the ``TOPICS`` they changed."""
        listed = json.dumps(ids)
        before = {r[0]: r for r in conn.execute(f"SELECT {', '.join(_TOPIC_COLUMNS)} FROM invoice_summary WHERE "
                                                f"{_in_list('id')}", (listed,))}  # fmt: skip
        conn.execute(f"DELETE FROM invoice_summary WHERE {_in_list('id')}", (listed,))
        conn.execute(f"DELETE FROM summary_spend WHERE {_in_list('invoice_id')}", (listed,))
        for table in (*SIDE_TABLES, "summary_codes"):
            conn.execute(f"DELETE FROM {table} WHERE {_in_list('id')}", (listed,))
        fts = fts_available()
        if fts:
            conn.execute(f"DELETE FROM invoice_search WHERE {_in_list('rowid')}", (listed,))
        elif conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'invoice_search'").fetchone():
            # made where SQLite has FTS5, changed here without it: built again there (``_summary_migrate``)
            conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('search_behind', '1')")
        rows = conn.execute(_SELECT, (listed,)).fetchall()
        approvals: dict[int, list[sqlite3.Row]] = {}
        for e in conn.execute(
            f"SELECT id, invoice_id, created_at, detail FROM events WHERE action = 'approved' AND "
            f"{_in_list('invoice_id')}",
            (listed,),
        ):
            approvals.setdefault(e["invoice_id"], []).append(e)
        summary, spend, search = [], [], []
        for r in rows:
            values, parts = _summary_row(dict(r), approvals.get(r["id"], []), treatments)
            summary.append(values)
            spend += [(r["id"], n, *part) for n, part in enumerate(parts)]
            search.append((r["id"], values[_COLUMNS.index("plain_name")], values[_COLUMNS.index("raw_number")]))
        conn.executemany(_INSERT, [[v[n] for n in _MAIN] for v in summary])
        for table, positions in _SIDE.items():
            conn.executemany(_INSERT_SIDE[table], [[v[n] for n in positions] for v in summary])
        codes = _COLUMNS.index("i_codes")
        conn.executemany("INSERT INTO summary_codes (id, code, n, pos) VALUES (?, ?, ?, ?)",
                         [row for v in summary for row in _code_rows(v[0], v[codes])])  # fmt: skip
        conn.executemany(
            "INSERT INTO summary_spend (invoice_id, part, gl_code, cost_center, amount, sizes, cents, amounts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            spend,
        )
        if fts:
            conn.executemany("INSERT INTO invoice_search (rowid, plain_name, raw_number) VALUES (?, ?, ?)", search)
        after = {v[0]: tuple(v[_COLUMNS.index(c)] for c in _TOPIC_COLUMNS) for v in summary}

        def row(values: Any) -> dict[str, Any] | None:
            return None if values is None else dict(zip(_TOPIC_COLUMNS, values, strict=True))

        return {topic for topic, key in TOPICS.items() for i in ids
                if key(row(before.get(i))) != key(row(after.get(i)))}  # fmt: skip

    @staticmethod
    def tax_treatments_in(conn: sqlite3.Connection) -> dict[str, Any]:
        """``tax_treatments()`` read on ``conn`` (the summary is worked out inside a transaction under way)."""
        from ...coding.tax import DEFAULT_TREATMENTS, TAX_TYPES, TaxTreatment

        stored = {r["tax_type"]: r for r in conn.execute("SELECT * FROM tax_treatments")}
        out = {}
        for tax_type in TAX_TYPES:
            r = stored.get(tax_type)
            out[tax_type] = (
                TaxTreatment(tax_type, r["treatment"], r["gl_code"])
                if r
                else TaxTreatment(tax_type, DEFAULT_TREATMENTS[tax_type])
            )
        return out

    def summary_memo(self, name: str, compute: Callable[[], Any], topic: str = "all", events: str = "") -> Any:
        """``compute()``, worked out again only once the invoices changed since it was last, in this process (only
        what of them ``topic`` names, ``TOPICS``; with ``events``, or once an event of that action was added): what
        several pages read of every invoice (the vendors' invoice dates, the batches' totals). The value is shared:
        read it, don't change it."""
        with self._conn() as conn:
            self._summary_fresh(conn)
            stamp = conn.execute(
                "SELECT (SELECT value FROM settings WHERE key = 'summary_id'), (SELECT value FROM settings WHERE key = "
                "?), (SELECT COUNT(*) || '/' || COALESCE(MAX(id), '') FROM events WHERE action = ?)",
                (f"summary_generation/{topic}", events),
            ).fetchone()[:]
        key = (str(self.path), name)
        with _MEMO_LOCK:
            hit = _MEMO.get(key)
            computing = _COMPUTING.setdefault(key, threading.Lock())
        if hit is not None and hit[0] == stamp:
            return hit[1]
        # One page works it out; the others asking meanwhile wait for it (not all at once, after each change: under
        # load, many working out the same 100,000 rows at once each take many times longer).
        got = computing.acquire(timeout=MEMO_WAIT)
        try:
            with _MEMO_LOCK:
                hit = _MEMO.get(key)
            if hit is not None and hit[0] == stamp:
                return hit[1]
            value = compute()
            with _MEMO_LOCK:
                _MEMO[key] = (stamp, value)
                while len(_MEMO) > 64:
                    _MEMO.pop(next(iter(_MEMO)))
            return value
        finally:
            if got:
                computing.release()

    def summary_rows(self, where: str = "1", params: tuple[Any, ...] | list[Any] = (), columns: str = "*",
                     order: str = "id") -> list[dict[str, Any]]:  # fmt: skip
        """Rows of ``invoice_summary`` (fresh: ``_summary_fresh``) as dicts; ``where`` and ``order``: the caller's
        SQL."""
        with self._conn() as conn:
            self._summary_fresh(conn)
            return fetch_rows(conn, f"SELECT {columns} FROM {_tables(columns)} WHERE {where} ORDER BY {order}",
                              tuple(params))  # fmt: skip

    def search_rows(self, ids: list[int] | None = None) -> list[dict[str, Any]]:
        """Every invoice's identifying fields and where it stands (for the invoice directory's quick search), with
        the date of its export batch and its GST/HST number (the JD Edwards duplicate check finds a supplier by
        it); only ``ids``' when given. From the invoices' summary (D-110)."""
        where, params = ("1", ()) if ids is None else (_in_list("id"), (json.dumps([int(i) for i in ids]),))
        with self._conn() as conn:
            self._summary_fresh(conn)
            batches = {r["id"]: r["created_at"] for r in conn.execute("SELECT id, created_at FROM export_batches")}
            rows = fetch_rows(
                conn,
                f"""SELECT id, status, file_name, vendor_name, invoice_number, invoice_date, due_date, currency,
                          grand_total, po_key, created_at, reviewed_at, reviewer, second_reviewer,
                          second_reviewed_at, export_batch, parked_reason, follow_up, error,
                          gst_number AS gst_hst_registration_number
                   FROM invoice_summary WHERE {where} ORDER BY id DESC""",
                params,
            )
        for r in rows:
            r["exported_at"] = batches.get(r["export_batch"]) if r["export_batch"] else None
        return rows

    def recently_approved(self, limit: int) -> list[dict[str, Any]]:
        """The ``limit`` invoices approved last (``list_invoices(APPROVED)`` by approval time, latest first)."""
        with self._conn() as conn:
            self._summary_fresh(conn)
            return fetch_rows(conn,
                "SELECT id, file_name, status, vendor_name, invoice_number, invoice_date, due_date, currency, "
                "grand_total, model_confidence, adjusted_confidence, requires_review, error, created_at, reviewed_at, "
                "reviewer, second_reviewer FROM invoice_summary WHERE status = ? ORDER BY COALESCE(reviewed_at, '') "
                "DESC, requires_review DESC, adjusted_confidence ASC, id ASC LIMIT ?", (APPROVED, limit))  # fmt: skip

    def status_counts(self) -> dict[str, int]:
        """{status: how many invoices}, from the invoices' summary (counted again once one changed its status: every
        page's rail shows two of them)."""
        return dict(self.summary_memo("status_counts", self._status_counts, "status"))

    def _status_counts(self) -> dict[str, int]:
        with self._conn() as conn:
            self._summary_fresh(conn)
            return {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM invoice_summary GROUP BY status")}

    def summary_containing(self, column: str, text: str) -> set[int]:
        """The invoices whose ``column`` of the summary (``plain_name`` or ``raw_number``) contains ``text``: by the
        FTS5 trigram index when this SQLite has it and ``text`` is three characters or more, else by reading the
        column (D-110)."""
        if column not in ("plain_name", "raw_number"):
            raise ValueError(column)
        with self._conn() as conn:
            self._summary_fresh(conn)
            if len(text) >= 3 and fts_available():
                phrase = '"' + text.replace('"', '""') + '"'
                rows = conn.execute("SELECT rowid FROM invoice_search WHERE invoice_search MATCH ?",
                                    (f"{column} : {phrase}",))  # fmt: skip
            else:
                rows = conn.execute(f"SELECT id FROM invoice_summary WHERE instr({column}, ?) > 0", (text,))
            return {r[0] for r in rows}

    def summary_fetch(self, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> list[dict[str, Any]]:
        """Any query of the summary's tables (fresh), its rows as dicts read in one go (``fetch_rows``)."""
        with self._conn() as conn:
            self._summary_fresh(conn)
            return fetch_rows(conn, sql, tuple(params))

    def summary_query(self, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> list[sqlite3.Row]:
        """Any query of the summary's tables (fresh)."""
        with self._conn() as conn:
            self._summary_fresh(conn)
            return conn.execute(sql, tuple(params)).fetchall()


# What the summary works out of an invoice, by part: a part that can't be worked out (a damaged coding, check or
# metadata) is marked in ``broken`` (its bit), and only the report that reads it reads the invoice again, failing as it
# did before (``computed``): a damaged tax line still leaves Insights alone.
PARTS = ("tax", "sa", "spend", "po", "insights", "workload", "approval", "pay", "queue")
BIT = {part: 1 << n for n, part in enumerate(PARTS)}


def _summary_row(r: dict[str, Any], approvals: list[Any],
                 treatments: dict[str, Any]) -> tuple[tuple[Any, ...], list[tuple[Any, ...]]]:  # fmt: skip
    """(the ``invoice_summary`` row of invoice ``r``, its ``summary_spend`` rows)."""
    from ...erp.search import search_keys

    plain, number, raw = search_keys(r)
    computed: dict[str, Any] = {name: None for name, _ in COMPUTED}
    computed.update(plain_name=plain, number_key=number, raw_number=raw, tax_date="", f_date="", i_codes="[]",
                    w_codes="[]", legacy=0, broken=0)  # fmt: skip
    work = _Work(r, approvals, treatments)
    parts: list[tuple[Any, ...]] = []
    for part in PARTS:
        try:
            computed.update(work.part(part))
        except Exception:  # noqa: BLE001 - read again from the invoice by the report asking (``computed``)
            computed["broken"] |= BIT[part]
    if not computed["broken"] & BIT["spend"]:
        parts = work.parts
    values = tuple(r[c] for c in ("id", *MIRROR, *(name for name, _, _ in DERIVED))) + tuple(
        computed[name] for name, _ in COMPUTED
    )
    return values, parts


class _Work:
    """What the reports need of invoice ``r`` (its columns, the JSON ones as text; ``approvals``: its ``approved``
    events, rows of id, created_at and detail), part by part, by the reports' own functions."""

    def __init__(self, r: dict[str, Any], approvals: list[Any], treatments: dict[str, Any]) -> None:
        self.r, self.approvals, self.treatments = r, approvals, treatments
        self.decoded: dict[str, Any] = {}
        self.parts: list[tuple[Any, ...]] = []

    def __getitem__(self, key: str) -> Any:  # the invoice's columns, the JSON ones decoded as they are first read
        if key in ("ai_output", "final_output", "validation", "meta", "edits"):
            if key not in self.decoded:
                self.decoded[key] = _decoded(self.r[key])
            return self.decoded[key]
        return self.r[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self[key] if key in self.r else default

    def latest(self, by_time: bool = False) -> Any:
        """The latest ``approved`` event: by id (``latest_events``) or by time (``latest_approvals``)."""
        if not self.approvals:
            return None
        return max(self.approvals, key=(lambda e: (e["created_at"], e["id"])) if by_time else (lambda e: e["id"]))

    def part(self, part: str) -> dict[str, Any]:
        from ...coding.bulk import queue_facts
        from ...coding.taxreturn import self_assessment_facts, tax_facts
        from ...erp.accruals import _bill
        from ...erp.insights import approval_facts, invoice_facts, payment_facts, workload_facts
        from ...erp.spend import spend_parts

        r, status = self.r, self.r["status"]
        signed, approved = status in (APPROVED, PENDING), status == APPROVED
        final = self["final_output"] if part in ("tax", "sa", "spend", "pay") else None
        if part == "tax":  # the date the sales-tax report files it under; what it claims of an approved invoice
            out = {"tax_date": str((final or self["ai_output"] or {}).get("invoice_date") or r["invoice_date"] or "")}
            if approved and final:
                latest = self.latest()
                approval = {"detail": _decoded(latest["detail"]) or {}} if latest is not None else None
                out["t_facts"] = json.dumps(tax_facts(self, approval), separators=(",", ":"))
            return out
        if part == "sa":
            sa = self_assessment_facts(final) if approved and final else None
            return {"s_facts": json.dumps(sa, separators=(",", ":")) if sa is not None else None}
        if part == "spend":
            if not approved:
                return {}
            doc = final or {}
            vendor, number, credit = _bill(doc)
            self.parts = spend_parts(doc, self.treatments)
            return {
                "f_date": str(doc.get("invoice_date") or ""),
                "bill_vendor": vendor,
                "bill_number": number,
                "bill_credit": int(credit),
                "legacy": int(not doc.get("gl_distribution")),
                "p_vendor": str(doc.get("vendor_name") or ""),
                "p_currency": str(doc.get("currency") or "CAD"),
            }
        if part == "po":  # as ``purchase_orders`` reads it: the coding's text, as approved or else as read
            if not r["po_key"]:
                return {}
            return {"po_billed": float(json.loads(r["final_output"] or r["ai_output"] or "{}").get("subtotal") or 0)}
        if part == "insights":
            return invoice_facts(self)
        if part == "workload":
            if status in (FAILED, REJECTED):
                return {}
            latest = self.latest() if signed else None
            return workload_facts(self, (_decoded(latest["detail"]) or {}) if latest is not None else None)
        if part == "approval":
            latest = self.latest(by_time=True)
            return approval_facts(latest["created_at"], _decoded(latest["detail"]) or {}) if latest is not None else {}
        if part == "pay":
            return payment_facts(final) if signed and final else {}
        return queue_facts(self)


def summary_facts(store: Any, invoice_id: int, *parts: str) -> dict[str, Any]:
    """The worked-out columns of ``parts`` of an invoice whose summary could not work them out (``broken``), worked out
    again from the invoice: raises what reading it raises (a report reads them as it read the invoice before)."""
    with store._conn() as conn:
        (r,) = conn.execute(_SELECT, (json.dumps([invoice_id]),)).fetchall()
        approvals = conn.execute("SELECT id, invoice_id, created_at, detail FROM events WHERE action = 'approved' "
                                 "AND invoice_id = ?", (invoice_id,)).fetchall()  # fmt: skip
        treatments = store.tax_treatments_in(conn)
    found = {name: None for name, _ in COMPUTED}
    found.update(i_codes="[]", w_codes="[]", tax_date="", f_date="", legacy=0)
    work = _Work(dict(r), approvals, treatments)
    for part in parts or PARTS:
        found.update(work.part(part))
    return found


def computed(store: Any, row: Any, *parts: str) -> Any:
    """A summary row's worked-out columns of ``parts``: its own, or worked out again from the invoice when the summary
    could not (``broken``)."""
    if row["broken"] and any(row["broken"] & BIT[p] for p in parts):
        return {**row, **summary_facts(store, row["id"], *parts)}
    return row


def broken(*parts: str) -> str:
    """SQL: the summary could not work out one of ``parts`` (``broken``'s bits)."""
    return f"(broken != 0 AND broken & {sum(BIT[p] for p in parts)})"


def whole(*parts: str) -> str:
    """SQL: the summary worked out all of ``parts``."""
    return f"broken & {sum(BIT[p] for p in parts)} = 0"
