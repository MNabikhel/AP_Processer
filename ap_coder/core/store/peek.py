"""A data folder's database read without a Store (D-211, D-243): for what must not open a Store on it (that would
migrate it), above all the launcher's first phase in a new AP Coder folder, which runs before AP Coder's packages are
installed and so can't import the store package at all: ``ops/safety.py`` loads this file by its path. Standard
library only, and nothing imported from the package."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def setting(db: Path, key: str) -> str:
    """The database ``db``'s setting ``key`` ("" when it has none, is not there, or can't be read): read-only, and no
    ``-wal`` file left beside one closed cleanly (as ``users.sign_in_accounts`` reads it)."""
    how = "mode=ro" if db.with_name(db.name + "-wal").exists() else "mode=ro&immutable=1"
    try:
        conn = sqlite3.connect(f"{db.resolve().as_uri()}?{how}", uri=True, timeout=5)
        try:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        finally:
            conn.close()
    except (sqlite3.Error, OSError, ValueError):
        return ""
    return str(row[0]) if row else ""
