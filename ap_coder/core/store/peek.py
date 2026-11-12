"""A data folder's database read without a Store (D-211, D-243): for what must not open a Store on it (that would
migrate it), above all the launcher's first phase in a new AP Coder folder, which runs before AP Coder's packages are
installed and so can't import the store package at all: ``ops/safety.py`` loads this file by its path. Standard
library only, and nothing imported from the package."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def _read(db: Path, sql: str, params: tuple = ()) -> tuple | None:
    """The first row ``sql`` answers on ``db``, read-only, no ``-wal`` file left beside one closed cleanly (as
    ``users.sign_in_accounts`` reads it); None when the file is not there or can't be read."""
    how = "mode=ro" if db.with_name(db.name + "-wal").exists() else "mode=ro&immutable=1"
    try:
        conn = sqlite3.connect(f"{db.resolve().as_uri()}?{how}", uri=True, timeout=5)
        try:
            return conn.execute(sql, params).fetchone() or ()
        finally:
            conn.close()
    except (sqlite3.Error, OSError, ValueError):
        return None


def accounts(db: Path) -> int:
    """How many sign-in accounts the database ``db`` holds, turned off or not (0: none, or it can't be read)."""
    row = _read(db, "SELECT COUNT(*) FROM users")
    return int(row[0]) if row else 0


def setting(db: Path, key: str) -> str:
    """The database ``db``'s setting ``key`` ("" when it has none, is not there, or can't be read)."""
    row = _read(db, "SELECT value FROM settings WHERE key = ?", (key,))
    return str(row[0]) if row else ""
