"""The SQL that is SQLite's own, behind names (core/store/dialect.py, D-190): each name gives the SQL the store wrote
before it, and works on a real connection."""

from __future__ import annotations

import json
import sqlite3

import pytest

from ap_coder.core.store import _in_list, dialect


@pytest.fixture
def conn():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    yield conn
    conn.close()


def test_the_names_give_the_sql_the_store_wrote_before():
    assert _in_list("id") == dialect.in_list("id") == "id IN (SELECT value FROM json_each(?))"
    assert dialect.json_get("e.detail", "$.key") == "json_extract(e.detail, '$.key')"
    assert dialect.NOW_SECONDS == "((julianday('now') - 2440587.5) * 86400.0)"
    assert dialect.upsert("settings", ("key", "value"), ("key",)) == (
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value"
    )
    replace = dialect.insert_or_replace("settings", ("key", "value"))
    assert replace == "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)"
    ignore = dialect.insert_or_ignore("INSERT INTO t (a) SELECT a FROM u")
    assert ignore == "INSERT OR IGNORE INTO t (a) SELECT a FROM u"
    assert dialect.update_or_ignore("lessons") + " SET x = 1" == "UPDATE OR IGNORE lessons SET x = 1"
    with pytest.raises(ValueError):
        dialect.insert_or_ignore("UPDATE t SET a = 1")


def test_a_list_of_any_length_is_one_value(conn):
    conn.execute("CREATE TABLE t (id INTEGER)")
    conn.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(5000)])
    ids = json.dumps(list(range(0, 5000, 2)))
    assert conn.execute(f"SELECT COUNT(*) FROM t WHERE {dialect.in_list('id')}", (ids,)).fetchone() == (2500,)


def test_an_upsert_and_the_conflicts(conn):
    put = dialect.upsert("settings", ("key", "value"), ("key",))
    conn.execute(put, ("a", "1"))
    conn.execute(put, ("a", "2"))
    conn.execute(dialect.insert_or_ignore("INSERT INTO settings (key, value) VALUES (?, ?)"), ("a", "3"))
    assert conn.execute("SELECT value FROM settings").fetchall() == [("2",)]
    conn.execute(dialect.insert_or_replace("settings", ("key", "value")), ("a", "4"))
    conn.execute("INSERT INTO settings VALUES ('b', '5')")
    done = conn.execute(f"{dialect.update_or_ignore('settings')} SET key = 'a' WHERE key = 'b'")
    assert done.rowcount == 0 and dict(conn.execute("SELECT key, value FROM settings")) == {"a": "4", "b": "5"}


def test_the_write_lock_is_taken_or_joined(conn):
    dialect.begin_write(conn)
    assert conn.in_transaction
    conn.execute("INSERT INTO settings VALUES ('a', '1')")
    dialect.begin_write(conn)  # inside the transaction under way: joined, no "cannot start a transaction"
    conn.rollback()
    assert conn.execute("SELECT COUNT(*) FROM settings").fetchone() == (0,)


def test_the_looks_at_the_schema_and_the_connection(conn):
    assert dialect.table_names(conn) == {"settings"}
    assert dialect.columns(conn, "settings") == {"key", "value"} and dialect.columns(conn, "missing") == set()
    dialect.add_column(conn, "settings", "note", "TEXT NOT NULL DEFAULT ''")
    assert "note" in dialect.columns(conn, "settings")
    dialect.set_busy_timeout(conn, 200)
    assert dialect.busy_timeout(conn) == 200 and isinstance(dialect.data_version(conn), int)
    clock = conn.execute(f"SELECT {dialect.NOW_SECONDS}, {dialect.NOW_DAYS}").fetchone()
    assert abs(clock[0] - (clock[1] - 2440587.5) * 86400) < 1e-3
