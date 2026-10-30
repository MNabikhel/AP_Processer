"""Backups: consistent copies, once a day automatically, and a restore that can be undone."""

import os
import sqlite3
import time

import pytest

from ap_coder.core.audit import describe
from ap_coder.core.store import Store
from ap_coder.core.store.backups import DAILY


def test_backup_and_restore_round_trip(tmp_path, ground_truth):
    store = Store(tmp_path / "ap.db")
    store.add_invoice(tmp_path / "a.pdf", ground_truth, {})
    backup = store.backup_now("manual")
    store.add_invoice(tmp_path / "b.pdf", ground_truth, {})
    assert len(store.list_invoices()) == 2
    safety = store.restore_from(backup)
    assert len(store.list_invoices()) == 1
    assert safety.name.endswith("-before-restore.db")
    store.restore_from(safety)  # undo the restore
    assert len(store.list_invoices()) == 2


def test_auto_backup_once_a_day_keeps_the_newest(tmp_path):
    store = Store(tmp_path / "ap.db")
    assert store.auto_backup() is not None
    assert store.auto_backup() is None  # already backed up today
    folder = store.backup_dir()
    for day in range(20):  # pretend there were 20 older daily backups
        old = folder / f"ap_coder-202601{day + 1:02d}-090000.db"
        old.write_bytes(b"")
        stamp = time.time() - (30 - day) * 86400
        os.utime(old, (stamp, stamp))
    newest = folder / "ap_coder-20260930-090000.db"
    newest.write_bytes(b"")
    os.utime(newest, (time.time() - 2 * 86400,) * 2)
    store.auto_backup(keep=14, min_hours=0)
    daily = [p for p in store.list_backups() if DAILY.fullmatch(p.stem)]
    assert len(daily) == 14
    manual = store.backup_now("manual")
    store.auto_backup(keep=14, min_hours=0)
    assert manual.exists()  # labelled backups are never pruned


def test_a_deleted_database_is_recreated_not_broken(tmp_path, ground_truth):
    store = Store(tmp_path / "ap.db")
    store.add_invoice(tmp_path / "a.pdf", ground_truth, {})
    for f in tmp_path.glob("ap.db*"):
        f.unlink()
    assert store.list_invoices() == []  # empty, working database instead of "no such table"
    store.add_invoice(tmp_path / "b.pdf", ground_truth, {})
    assert len(store.list_invoices()) == 1


def _planted(tmp_path, sql: str, name: str = "ap_coder-20260101-090000.db"):
    """A backup file that is an AP Coder database with ``sql`` run on it."""
    path = tmp_path / "planted" / name
    path.parent.mkdir(exist_ok=True)
    Store(path)
    with sqlite3.connect(path) as conn:
        conn.executescript(sql)
    return path


@pytest.mark.parametrize(
    ("sql", "why"),
    [
        ("CREATE TRIGGER t AFTER INSERT ON invoices BEGIN DELETE FROM feedback; END;", "triggers"),
        ("CREATE VIEW v AS SELECT * FROM invoices;", "views"),
        ("DELETE FROM settings WHERE key = 'schema_version';", "not an AP Coder database"),
        ("UPDATE settings SET value = '999' WHERE key = 'schema_version';", "newer version"),
        ("DROP TABLE invoices;", "not an AP Coder database"),
    ],
)
def test_restore_refuses_a_file_that_is_not_an_ap_coder_backup(tmp_path, ground_truth, sql, why):
    store = Store(tmp_path / "ap.db")
    store.add_invoice(tmp_path / "a.pdf", ground_truth, {})
    planted = _planted(tmp_path, sql)
    with pytest.raises(ValueError, match=why):
        store.restore_from(planted)
    assert len(store.list_invoices()) == 1 and not list(store.backup_dir().glob("*before-restore*"))
    other = tmp_path / "notes.db"
    other.write_bytes(b"just some text, not SQLite at all" * 10)
    with pytest.raises(ValueError):
        store.restore_from(other)


def test_restore_clears_the_second_backup_folder(tmp_path):
    """A backup must not decide where later backups are copied (e.g. a planted network share)."""
    store = Store(tmp_path / "ap.db")
    planted = _planted(tmp_path, "INSERT INTO settings (key, value) VALUES ('backup_copy_dir', '\\\\evil\\share');")
    store.restore_from(planted)
    assert store.get_setting("backup_copy_dir") == ""
    (event,) = [e for e in store.events() if e["action"] == "backup_restored"]
    assert "second backup folder cleared" in describe(event)
    assert store.backup_now("manual") and store.get_setting("backup_copy_status") == ""  # copied nowhere


def test_a_restore_forgets_the_learning_counts_kept_for_the_database(tmp_path):
    """A restore dropped the lessons kept for the database as it was, not the learning counts (``metrics``): the
    Learning page could show the counts of the database before the restore (B-786)."""
    from ap_coder.core.store import _base

    store = Store(tmp_path / "ap.db")
    backup = store.backup_now("manual")
    store.metrics()
    mine = str(store.path.resolve())
    assert mine in _base._METRICS
    store.restore_from(backup)
    assert mine not in _base._METRICS


def test_a_daily_backup_made_in_the_same_second_as_another_is_pruned_like_the_others(tmp_path, monkeypatch):
    """``backup_now`` names a second backup in the same second ``ap_coder-<stamp>-2.db``: the pruning took it for a
    labelled one and kept it for ever, here and in the second backup folder."""
    import datetime as dt
    from types import SimpleNamespace

    from ap_coder.core.store import backups

    class Frozen(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 1, 10, 9, 0, 0)

    store = Store(tmp_path / "db" / "ap.db")
    second = tmp_path / "onedrive"
    second.mkdir()
    store.set_setting("backup_copy_dir", str(second))
    monkeypatch.setattr(backups, "dt", SimpleNamespace(datetime=Frozen, timedelta=dt.timedelta))
    store.backup_now()
    assert store.backup_now().name == "ap_coder-20260110-090000-2.db"  # the same second
    monkeypatch.setattr(backups, "dt", dt)
    for folder in (store.backup_dir(), second):
        for day in range(1, 16):  # 15 newer daily backups
            (folder / f"ap_coder-202602{day:02d}-090000.db").write_bytes(b"")
    store.auto_backup(keep=14, min_hours=0)
    for folder in (store.backup_dir(), second):
        names = sorted(p.name for p in folder.glob("ap_coder-*.db"))
        assert len(names) == 14 and not [n for n in names if "20260110" in n], (folder, names)
