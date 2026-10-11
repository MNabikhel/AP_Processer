"""An update never risks the database: it is backed up before an older one is brought up to date, and a database made
by a newer AP Coder is refused in plain words (never opened, never downgraded)."""

from __future__ import annotations

import sqlite3

import pytest

from ap_coder import store as store_module
from ap_coder.store import SCHEMA_VERSION, DatabaseTooNew, Store


def _schema(path) -> int | None:
    conn = sqlite3.connect(path)
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = 'schema_version'").fetchone()
    finally:
        conn.close()
    return int(row[0]) if row else None


def _set_schema(path, version: int) -> None:
    conn = sqlite3.connect(path)
    conn.execute("UPDATE settings SET value = ? WHERE key = 'schema_version'", (str(version),))
    conn.commit()
    conn.close()


def test_an_older_database_is_backed_up_before_it_is_brought_up_to_date(tmp_path):
    db = tmp_path / "ap_coder.db"
    Store(db).set_setting("note", "kept")
    _set_schema(db, SCHEMA_VERSION - 1)  # as an older AP Coder left it
    store = Store(db)  # the new version opens it
    backups = store.list_backups()
    assert len(backups) == 1 and "before-update" in backups[0].name
    assert _schema(backups[0]) == SCHEMA_VERSION - 1  # the backup is the database as it was
    assert _schema(db) == SCHEMA_VERSION and store.get_setting("note") == "kept"
    Store(db)  # opened again: nothing more to bring up to date, no second backup
    assert len(store.list_backups()) == 1


def test_a_new_or_current_database_is_not_backed_up(tmp_path):
    db = tmp_path / "ap_coder.db"
    Store(db)
    Store(db)
    assert not (tmp_path / "backups").exists()


def test_a_backup_before_an_update_is_not_a_daily_backup(tmp_path):
    """The daily backups keep their own count; the one taken before an update is kept apart from them."""
    db = tmp_path / "ap_coder.db"
    Store(db)
    _set_schema(db, SCHEMA_VERSION - 2)
    store = Store(db)
    assert store.auto_backup() is not None  # the daily one is still made: the update's copy is not counted as it


def test_a_database_made_by_a_newer_ap_coder_is_refused_and_left_alone(tmp_path):
    db = tmp_path / "ap_coder.db"
    Store(db)
    _set_schema(db, SCHEMA_VERSION + 1)
    before = db.read_bytes()
    with pytest.raises(DatabaseTooNew) as exc:
        Store(db)
    message = str(exc.value)
    assert "made by a newer AP Coder" in message and "nothing was changed" in message
    assert str(SCHEMA_VERSION + 1) in message and str(db) in message
    assert db.read_bytes() == before and _schema(db) == SCHEMA_VERSION + 1
    assert isinstance(exc.value, RuntimeError)  # callers that catch RuntimeError still show it plainly


def test_the_version_check_reads_no_more_than_it_must(tmp_path, monkeypatch):
    """A missing or empty file is a new database: no backup, no refusal."""
    empty = tmp_path / "empty.db"
    empty.write_bytes(b"")
    Store(empty)
    assert _schema(empty) == SCHEMA_VERSION
    assert store_module.database_schema(tmp_path / "missing.db") is None
