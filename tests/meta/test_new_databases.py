"""The tests' new databases (tests/conftest.py ``_new_store``): a Store on a path where nothing is yet starts from a
copy of one new database made as the run starts. The copy must be what the real way makes, or every test would test
another database than the app's (D-336)."""

import sqlite3
from contextlib import closing

import pytest

from ap_coder.core.store import Store, migrations
from ap_coder.core.store._base import _now
from tests.conftest import _NEW_DATABASE, next_second, real_new_databases

FRESH = ("summary_id", "rule_use_since")  # a new database's own (summary.py, migrations.py)
PRAGMAS = ("journal_mode", "page_size", "auto_vacuum", "encoding", "user_version", "application_id")


def _database(path) -> dict:
    """What a database holds: its tables, indexes, triggers and views, every table's rows (the settings apart), its
    settings and how its file is kept."""
    with closing(sqlite3.connect(path)) as conn:
        master = sorted(conn.execute("SELECT type, name, tbl_name, sql FROM sqlite_master"))
        tables = [name for kind, name, _, _ in master if kind == "table" and name != "settings"]
        return {
            "master": master,
            "rows": {name: sorted(map(repr, conn.execute(f'SELECT * FROM "{name}"'))) for name in tables},
            "settings": dict(conn.execute("SELECT key, value FROM settings")),
            "pragmas": {name: conn.execute(f"PRAGMA {name}").fetchone()[0] for name in PRAGMAS},
        }


def _steps_run(monkeypatch) -> list:
    """The numbers of the migration steps run from now on (None: a step run every time)."""
    ran: list = []

    def recording(number, step):
        def run(store, conn):
            ran.append(number)
            step(store, conn)

        return run

    monkeypatch.setattr(migrations, "STEPS", tuple((n, recording(n, s)) for n, s in migrations.STEPS))
    return ran


def test_a_copied_new_database_is_the_one_the_real_way_makes(tmp_path, monkeypatch):
    template = _database(_NEW_DATABASE["path"])
    if _now() == template["settings"]["rule_use_since"]:  # made in another second: any time it keeps would differ
        next_second()
    monkeypatch.setattr(migrations, "_now", lambda: "2026-11-24T09:30:00")  # (the first day of each: the same)
    with real_new_databases():
        real = _database(Store(tmp_path / "real" / "ap_coder.db").path)
    copies = [_database(Store(tmp_path / f"copy{n}" / "ap_coder.db").path) for n in (1, 2)]
    for copy in copies:
        assert copy["master"] == real["master"]
        assert copy["rows"] == real["rows"]
        assert copy["pragmas"] == real["pragmas"]
        assert copy["settings"].keys() == real["settings"].keys() and set(FRESH) <= copy["settings"].keys()
        same = {key: value for key, value in copy["settings"].items() if key != "summary_id"}
        assert same == {key: value for key, value in real["settings"].items() if key != "summary_id"}
        assert copy["settings"]["rule_use_since"] == "2026-11-24T09:30:00"
    ids = {d["settings"]["summary_id"] for d in [real, *copies, template]}
    assert len(ids) == 4  # each its own, never the template's


def test_a_new_store_is_a_copy_unless_its_test_asks_for_the_real_way(tmp_path, monkeypatch):
    ran = _steps_run(monkeypatch)
    Store(tmp_path / "copied" / "ap_coder.db")
    assert ran and set(ran) == {migrations.EVERY_TIME}  # as a database already up to date
    ran.clear()
    with real_new_databases():
        Store(tmp_path / "real" / "ap_coder.db")
    assert set(migrations.MIGRATIONS) <= set(ran)


@pytest.mark.real_new_database
def test_a_test_marked_real_new_database_makes_its_new_databases_the_real_way(tmp_path, monkeypatch):
    ran = _steps_run(monkeypatch)
    Store(tmp_path / "ap_coder.db")
    assert set(migrations.MIGRATIONS) <= set(ran)


def test_a_path_with_a_journal_or_a_log_left_is_made_the_real_way(tmp_path, monkeypatch):
    """A write-ahead log left by a database since deleted: SQLite drops it when it makes the database itself; beside a
    copy it would be read as the copy's own."""
    ran = _steps_run(monkeypatch)
    for end in ("-journal", "-wal"):
        path = tmp_path / end.strip("-") / "ap_coder.db"
        path.parent.mkdir()
        path.with_name(f"ap_coder.db{end}").write_bytes(b"")
        ran.clear()
        Store(path)
        assert set(migrations.MIGRATIONS) <= set(ran), end
