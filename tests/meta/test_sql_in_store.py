"""No SQL outside the store (D-189): every statement AP Coder runs is in ``ap_coder/core/store``, so moving to another
database changes that folder only. Outside it nothing runs a statement, opens a SQLite connection or reaches for the
store's own connection: it asks the Store (a method of the part the subject belongs to; ``Store.transaction()`` keeps
several calls in one transaction), or a module function of the store for a database file a Store must not open (it
would migrate it: server-update's copies, the finance hub's own files)."""

from __future__ import annotations

import ast

from tests.conftest import ROOT

STORE = "ap_coder/core/store/"
RUNS = {"execute", "executemany", "executescript", "cursor"}  # a connection's (or a cursor's) statements
# The benchmark's tools (dev tooling: AP Coder never runs them) keep their own SQL, each for its reason. The list may
# only shrink: a file that no longer needs it leaves it (``test_the_allowlist_names_only_files_that_need_it``).
ALLOWED = {
    "ap_coder/bench/seed.py": "builds the 300,000-invoice benchmark database straight in SQLite, in bulk (minutes, "
    "not hours through the Store)",
    "ap_coder/bench/load.py": "the load test's audit reads the database it measured on a connection of its own, and "
    "rolls back a recomputation of the summary it compares",
    "ap_coder/bench/volume.py": "the volume bench wraps sqlite3.connect to record each statement the store runs, and "
    "counts every table's rows and their query plans",
    "ap_coder/bench/day.py": "the server day's probe reads counts on a read-only connection of its own while the "
    "server it measures runs",
    "ap_coder/bench/coding.py": "the coding bench backdates each replayed day's feedback (AP approving on the "
    "invoice's day, D-80)",
}


def _sql_outside_the_store(source: str) -> list[int]:
    """Lines that run SQL or reach for a connection: ``x.execute(`` (``executemany``, ``executescript``, ``cursor``),
    ``sqlite3.connect`` (or ``connect`` imported from sqlite3), and the store's own ``x._conn``."""
    lines = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if name in RUNS:
                lines.add(node.lineno)
        elif isinstance(node, ast.Attribute) and (
            node.attr == "_conn" or (node.attr == "connect" and getattr(node.value, "id", "") == "sqlite3")
        ):
            lines.add(node.lineno)
        elif isinstance(node, ast.ImportFrom) and node.module == "sqlite3":
            if any(a.name == "connect" for a in node.names):
                lines.add(node.lineno)
    return sorted(lines)


def _code() -> dict[str, str]:
    return {p.relative_to(ROOT).as_posix(): p.read_text(encoding="utf-8-sig")
            for p in sorted((ROOT / "ap_coder").rglob("*.py"))}  # fmt: skip


def test_no_sql_runs_outside_the_store():
    found = [f"{name}:{line}" for name, source in _code().items() if not name.startswith(STORE) and name not in ALLOWED
             for line in _sql_outside_the_store(source)]  # fmt: skip
    assert not found, "move it into its part of ap_coder/core/store, as a Store method (D-189): " + ", ".join(found)


def test_the_allowlist_names_only_files_that_need_it():
    code = _code()
    stale = [name for name in ALLOWED if name not in code or not _sql_outside_the_store(code[name])]
    assert not stale, f"no SQL left in {stale}: take them off ALLOWED (the list only shrinks)"
    assert all(name.startswith("ap_coder/bench/") and reason.strip() for name, reason in ALLOWED.items())


def test_the_sql_rule_finds_what_it_should():
    bad = """
conn.execute("SELECT 1")
cur.executemany(sql, rows)
conn.executescript(script)
cur = conn.cursor()
conn = sqlite3.connect(path)
with store._conn() as conn:
    pass
from sqlite3 import connect
execute(sql)
"""
    safe = """
import sqlite3
from sqlite3 import Error
rows = store.jde_postings(batch)
with store.transaction():
    store.set_setting("k", "v")
try:
    store.execute_plan()
except sqlite3.OperationalError:
    pass
note = "conn.execute('SELECT 1') in a string"  # and store._conn() in a comment
"""
    assert _sql_outside_the_store(bad) == [2, 3, 4, 5, 6, 7, 9, 10]
    assert _sql_outside_the_store(safe) == []
