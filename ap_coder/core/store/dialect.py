"""The SQL that is SQLite's own, behind names (D-190): what a move to PostgreSQL changes first. Each name says what
it does here and what PostgreSQL has for it; the store's shared helpers (``_base._in_list``, the queues' clock, a
write's lock, the migrations' looks at a table) use them, and new code uses them too (``tests/meta/test_dialect.py``).
The rest of the SQL is written for SQLite where it is: ``scripts/sql_inventory.py`` counts each construct and where it
is, docs/POSTGRES.md the plan for a port. Only SQLite is implemented: no PostgreSQL driver is a dependency."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from typing import Any

# A bound value's placeholder (PostgreSQL's drivers: ``%s``; or one translation of every ``?`` where the connection
# is made). The queries write ``?`` themselves: the port changes them where the driver is wrapped.
PARAM = "?"


# --- JSON ---------------------------------------------------------------------------------------------------------


def in_list(column: str) -> str:
    """``column IN`` a list bound as ONE parameter, a JSON array (``json.dumps(ids)``): a list of any length, where one
    placeholder per item fails past SQLite's limit (B-10). PostgreSQL: ``column IN (SELECT
    jsonb_array_elements_text(%s::jsonb))``, or ``column = ANY(%s)`` with the list itself bound."""
    return f"{column} IN (SELECT value FROM json_each({PARAM}))"


def json_get(value: str, path: str) -> str:
    """The value at ``path`` (``'$.key'``) of the JSON text ``value`` (SQLite's ``json_extract``: a number stays a
    number). PostgreSQL: ``(value)::jsonb #>> '{key}'`` (text: cast it for a number or a truth value)."""
    return f"json_extract({value}, '{path}')"


def json_valid(value: str) -> str:
    """Whether the text ``value`` is JSON (1 or 0). PostgreSQL 16: ``pg_input_is_valid(value, 'jsonb')``."""
    return f"json_valid({value})"


# --- Conflicts ----------------------------------------------------------------------------------------------------


def upsert(table: str, columns: Sequence[str], key: Sequence[str], update: Sequence[str] | None = None) -> str:
    """An insert that updates the row already there under ``key`` (``update``: the columns it replaces, by default all
    the others). ``INSERT ... ON CONFLICT (key) DO UPDATE SET c = excluded.c`` is SQLite's (3.24 and newer) and
    PostgreSQL's alike: a query written so needs no change."""
    replaced = [c for c in columns if c not in key] if update is None else list(update)
    return (f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(PARAM for _ in columns)}) ON CONFLICT"
            f"({', '.join(key)}) DO UPDATE SET {', '.join(f'{c} = excluded.{c}' for c in replaced)}")  # fmt: skip


def insert_or_replace(table: str, columns: Sequence[str]) -> str:
    """An insert that first deletes any row it conflicts with (``INSERT OR REPLACE``: the row is new, its rowid too).
    PostgreSQL has none: ``upsert`` with every column (which keeps the row: the same where nothing reads its rowid
    and no other unique column conflicts)."""
    return f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}) VALUES ({', '.join(PARAM for _ in columns)})"


def insert_or_ignore(insert: str) -> str:
    """``insert`` (an ``INSERT INTO ...``, of values or of a SELECT) that skips each row that would break a constraint
    (``INSERT OR IGNORE``). PostgreSQL: ``insert`` and ``ON CONFLICT DO NOTHING``, which skips only a unique key's
    conflicts (a NULL in a NOT NULL column fails there)."""
    head, _, tail = insert.partition("INSERT ")
    if head.strip():
        raise ValueError(f"not an INSERT: {insert[:40]!r}")
    return f"INSERT OR IGNORE {tail}"


def update_or_ignore(table: str) -> str:
    """The start of an ``UPDATE`` that leaves a row as it was where the change would break a unique key (``UPDATE OR
    IGNORE``; the caller sees it by the rowcount). PostgreSQL has none: the update with ``WHERE NOT EXISTS`` the row
    it would collide with, or in a savepoint whose unique violation is caught."""
    return f"UPDATE OR IGNORE {table}"


# --- The database's clock -------------------------------------------------------------------------------------------

# Now on the database's clock, in days (a change of the computer's clock doesn't move it back, B-1770). PostgreSQL:
# ``EXTRACT(EPOCH FROM statement_timestamp()) / 86400.0 + 2440587.5``.
NOW_DAYS = "julianday('now')"
# Now on the database's clock in seconds since 1970, as ``time.time()`` keeps them. PostgreSQL: ``EXTRACT(EPOCH FROM
# statement_timestamp())``.
NOW_SECONDS = f"(({NOW_DAYS} - 2440587.5) * 86400.0)"


# --- Text -----------------------------------------------------------------------------------------------------------


def group_concat(value: str, separator: str = "','") -> str:
    """The rows' ``value`` joined into one text, ``separator`` (an SQL expression) between them. PostgreSQL:
    ``string_agg(value::text, separator)``."""
    return f"group_concat({value}, {separator})"


def like(value: str) -> str:
    """``value LIKE ?``: SQLite's compares A to Z without their case (``ap_fold`` for the rest, ``_base.fold_text``).
    PostgreSQL's LIKE minds the case: ``ILIKE`` there (or ``lower(value) LIKE lower(?)``)."""
    return f"{value} LIKE {PARAM}"


def nocase(value: str) -> str:
    """``value`` sorted and compared without the case of A to Z (``COLLATE NOCASE``). PostgreSQL: ``lower(value)``,
    or a case-insensitive collation."""
    return f"{value} COLLATE NOCASE"


# A row's own number in a table with no key of its own (``erp_invoices``, ``lesson_approvals``...). PostgreSQL has
# none that lasts (``ctid`` moves): such a table gets a key column of its own (``id bigint GENERATED ALWAYS AS
# IDENTITY``) before the port.
ROWID = "rowid"


# --- The connection and the schema ------------------------------------------------------------------------------------


def settle(conn: sqlite3.Connection) -> None:
    """A connection's settings once it is open: the write-ahead log (readers and the writer don't wait on each
    other). PostgreSQL needs none."""
    conn.execute("PRAGMA journal_mode=WAL")


def begin_write(conn: Any) -> None:
    """The write lock from now to the commit (``BEGIN IMMEDIATE``: another writer waits, up to the busy timeout);
    inside a transaction under way, which has written and so holds it already, the work joins it. PostgreSQL: the
    transaction is open already; the lock is ``SELECT pg_advisory_xact_lock(<the database's key>)``, or ``SELECT
    ... FOR UPDATE`` of the rows read."""
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")


def busy_timeout(conn: Any) -> int:
    """How long a write waits for another writer, in milliseconds (``PRAGMA busy_timeout``). PostgreSQL: ``SHOW
    lock_timeout``."""
    return int(conn.execute("PRAGMA busy_timeout").fetchone()[0])


def set_busy_timeout(conn: Any, ms: int) -> None:
    """A write waits ``ms`` milliseconds for another writer at most, then fails. PostgreSQL: ``SET lock_timeout``."""
    conn.execute(f"PRAGMA busy_timeout = {int(ms)}")


def data_version(conn: Any) -> int:
    """A number that changes when another connection commits a change (``PRAGMA data_version``). PostgreSQL has
    none: a counter row every write moves, or ``pg_stat_database.xact_commit`` (any commit)."""
    return int(conn.execute("PRAGMA data_version").fetchone()[0])


def table_names(conn: Any) -> set[str]:
    """The database's tables (``sqlite_master``). PostgreSQL: ``SELECT table_name FROM information_schema.tables
    WHERE table_schema = current_schema()``."""
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def columns(conn: Any, table: str) -> set[str]:
    """``table``'s columns (none when there is no such table: ``PRAGMA table_info``). PostgreSQL: ``SELECT
    column_name FROM information_schema.columns WHERE table_name = %s``."""
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def add_column(conn: Any, table: str, column: str, kind: str) -> None:
    """``ALTER TABLE table ADD COLUMN column kind`` (both have it; PostgreSQL also says ``IF NOT EXISTS``)."""
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
