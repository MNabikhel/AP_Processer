"""New SQL goes through the seam (D-190): a construct ``core/store/dialect.py`` names (the write lock, a list bound as
one value, the database's clock, a look at a table...) is not written out again elsewhere in ``ap_coder/``. Each one's
uses outside dialect.py, as ``scripts/sql_inventory.py`` counts them, may fall but never rise: ALLOWED is today's, to
be lowered as the port (docs/POSTGRES.md) or a clean-up moves them behind the seam."""

from __future__ import annotations

import re

from tests.scripts import load_script

inventory = load_script("sql_inventory", "ap_sql_inventory")

ALLOWED = {
    "`INSERT OR IGNORE`": 12,
    "`INSERT OR REPLACE`": 4,
    "`UPDATE OR IGNORE`": 0,
    "`json_each` (lists bound as one value)": 43,
    "`json_extract`": 42,
    "`json_valid`": 16,
    "`julianday('now')` (the database's clock)": 8,
    "`group_concat`": 7,
    "`LIKE` (A to Z without case in SQLite)": 15,
    "`COLLATE NOCASE`": 2,
    "`rowid`": 21,
    "`BEGIN IMMEDIATE` (the write lock)": 29,
    "`PRAGMA table_info`": 9,
    "other `PRAGMA`s": 11,
    "`sqlite_master`": 11,
}


def test_no_new_sqlite_only_sql_outside_the_dialect():
    found = inventory.outside_dialect(inventory.count())
    seams = {c.name: c.seam for c in inventory.RATCHETED}
    more = [f"{name}: {found[name]} uses, {most} allowed (write it with dialect's {seams[name]})"
            for name, most in ALLOWED.items() if found[name] > most]  # fmt: skip
    assert not more, "; ".join(more)


def test_the_allowances_are_for_what_the_seam_covers():
    assert set(ALLOWED) == {c.name for c in inventory.RATCHETED}
    assert all(c.seam for c in inventory.RATCHETED)


def test_the_inventory_counts_the_sql_in_strings_and_the_driver_in_the_code():
    source = '''
"""A docstring: SELECT rowid FROM t, not counted."""
import sqlite3

def f(conn, ids):
    """Nor here: BEGIN IMMEDIATE."""
    conn.execute("BEGIN IMMEDIATE")  # nor in a comment: INSERT OR REPLACE
    conn.execute(f"SELECT json_extract(detail, '$.a') FROM events WHERE {col} IN (SELECT value FROM json_each(?))")
    row = conn.execute("INSERT OR IGNORE INTO t (a) VALUES (?)", (1,)).lastrowid
    print("Delete it? The rowid, or ignore it")  # not SQL: none of its words in capitals
    return sqlite3.connect(":memory:"), sqlite3.Connection
'''
    texts, code = inventory.scan(source)
    assert len(texts) == 3 and code["attr:lastrowid"] == 1 and code["module:sqlite3"] == 1
    sql = "\n".join(texts)
    counted = {c.name: len(re.findall(c.pattern, sql, c.flags)) for c in inventory.CONSTRUCTS
               if not c.code}  # fmt: skip
    assert counted["`BEGIN IMMEDIATE` (the write lock)"] == 1 and counted["`INSERT OR REPLACE`"] == 0
    assert counted["`json_extract`"] == counted["`json_each` (lists bound as one value)"] == 1
    assert counted["`INSERT OR IGNORE`"] == 1 and counted["`rowid`"] == 0 and counted["`?` placeholders"] == 2
