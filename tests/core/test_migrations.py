"""An older database brought up to date is a new one (D-190): the database each older release made
(``tests/fixtures/old_schemas/``, made by each release's own code: ``scripts/old_schemas.py``), opened by this version,
has the tables, columns (their types, defaults and keys), indexes and triggers a new database has. A difference left
is listed here with why it does no harm; the steps themselves (``migrations.STEPS``) run in the order of their
versions."""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from ap_coder.core.store import SCHEMA_VERSION, Store, migrations
from tests.scripts import load_script

OLD = Path(__file__).parents[1] / "fixtures" / "old_schemas"
SNAPSHOTS = sorted(OLD.glob("schema-*.sql"))
# The schema of every release a user may have (scripts/ci/old_versions.py and docs/UPGRADING.md for PR #1 to #8, the
# pilot's and the hand-over's tags since): PR #1's 2 is the oldest an update starts from.
RELEASED = {2, 13, 14, 15, 16, 17, 18, 19, 27}
# What a database brought up to date keeps that a new one does not have, and why it is harmless.
KNOWN = {
    "verifications: columns in another order": "the AI reviewer's queue had its reader before the time it was renewed "
    "(D-174 and B-1770 added them in that order); a new database's table has the time and gets the reader after. "
    "Every query names its columns and reads rows by name (dict(row)), never by place.",
}


def _plain(sql: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\bIF NOT EXISTS\b", "", sql or "", flags=re.I)).strip()


def shape(path: Path) -> dict[str, object]:
    """What a database is made of, by name: each table's columns (name, type, NOT NULL, default, key), foreign keys
    and AUTOINCREMENT; each index's table, columns, uniqueness and WHERE; each trigger's and view's SQL."""
    conn = sqlite3.connect(path)
    try:
        found: dict[str, object] = {}
        for kind, name, table, sql in conn.execute("SELECT type, name, tbl_name, sql FROM sqlite_master"):
            if kind == "table" and not (sql or "").upper().startswith("CREATE VIRTUAL TABLE"):
                found[f"table {name}"] = (
                    [tuple(r)[1:] for r in conn.execute(f"PRAGMA table_xinfo('{name}')")],
                    sorted(tuple(r)[2:] for r in conn.execute(f"PRAGMA foreign_key_list('{name}')")),
                    "AUTOINCREMENT" in (sql or "").upper(),
                )
            elif kind == "index":
                listed = {r[1]: tuple(r)[2:] for r in conn.execute(f"PRAGMA index_list('{table}')")}
                where = _plain(sql).partition(" WHERE ")[2]
                found[f"index {name}"] = (table, [tuple(r) for r in conn.execute(f"PRAGMA index_xinfo('{name}')")],
                                          listed.get(name), where)  # fmt: skip
            else:  # a trigger, a view, a full-text index
                found[f"{kind} {name}"] = _plain(sql)
        return found
    finally:
        conn.close()


def differences(new: dict[str, object], old: dict[str, object]) -> list[str]:
    """Each way the database brought up to date (``old``) differs from a new one, in words."""
    said = []
    for name in sorted(set(new) | set(old)):
        if name not in old:
            said.append(f"{name}: missing")
        elif name not in new:
            said.append(f"{name}: left over, a new database has none")
        elif new[name] != old[name] and name.startswith("table "):
            (want, *rest), (got, *rest_got) = new[name], old[name]
            if sorted(want) == sorted(got) and rest == rest_got:
                said.append(f"{name[6:]}: columns in another order")
            else:
                said.append(f"{name}: {sorted(set(got) ^ set(want))} {rest_got if rest != rest_got else ''}".strip())
        elif new[name] != old[name]:
            said.append(f"{name}: {old[name]} where a new database has {new[name]}")
    return said


def _old_database_from(sql: str, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    db = folder / "ap_coder.db"
    conn = sqlite3.connect(db)
    conn.executescript(sql)
    conn.close()
    return db


def _old_database(snapshot: Path, folder: Path) -> Path:
    """The database ``snapshot`` holds, as its release left it (in a folder of its own: the update backs it up)."""
    return _old_database_from(snapshot.read_text(encoding="utf-8"), folder)


@pytest.fixture(scope="module")
def brought_up_to_date(tmp_path_factory):
    """{each old database: how it differs from a new one once this version opened it, then opened it again}."""
    new = shape(Store(tmp_path_factory.mktemp("new") / "ap_coder.db").path)
    found = {}
    for snapshot in SNAPSHOTS:
        db = _old_database(snapshot, tmp_path_factory.mktemp(snapshot.stem))
        store = Store(db)
        assert store.get_setting("schema_version") == str(SCHEMA_VERSION), snapshot.name
        once = differences(new, shape(db))
        Store(db)  # opened again: nothing more to do
        found[snapshot.name] = (once, differences(new, shape(db)))
    return found


@pytest.mark.parametrize("snapshot", SNAPSHOTS, ids=[p.stem for p in SNAPSHOTS])
def test_an_older_releases_database_brought_up_to_date_is_a_new_one(snapshot, brought_up_to_date):
    once, again = brought_up_to_date[snapshot.name]
    assert not [d for d in once if d not in KNOWN], once
    assert again == once


def test_every_difference_listed_is_still_found(brought_up_to_date):
    """A difference a later change put right comes off the list (and its reason with it)."""
    seen = {d for once, _ in brought_up_to_date.values() for d in once}
    assert set(KNOWN) <= seen, set(KNOWN) - seen


def test_the_old_databases_are_those_of_every_release(tmp_path):
    """One at least for each schema a release had, PR #1's among them, each with the version its name says."""
    versions = set()
    for snapshot in SNAPSHOTS:
        conn = sqlite3.connect(_old_database(snapshot, tmp_path / snapshot.stem))
        recorded = conn.execute("SELECT value FROM settings WHERE key = 'schema_version'").fetchone()
        conn.close()
        assert snapshot.name.startswith(f"schema-{int(recorded[0]):02d}-")
        versions.add(int(recorded[0]))
    assert RELEASED <= versions and max(versions) <= SCHEMA_VERSION


def test_a_database_kept_as_sql_is_made_again_the_same(tmp_path):
    """The way the old databases are kept (``scripts/old_schemas.py``'s dump) loses nothing: a new database dumped and
    made again from the SQL has the same tables, columns, indexes, triggers and settings."""
    old_schemas = load_script("old_schemas", "ap_old_schemas")
    made = Store(tmp_path / "a" / "ap_coder.db").path
    again = _old_database_from(old_schemas.dump(made), tmp_path / "b")
    assert shape(again) == shape(made)

    def settings(db: Path) -> list[tuple[str, str]]:
        with closing(sqlite3.connect(db)) as conn:
            return conn.execute("SELECT key, value FROM settings WHERE key != 'summary_id' ORDER BY key").fetchall()

    assert settings(again) == settings(made)


def test_the_numbered_steps_run_in_the_order_of_their_versions():
    numbers = [n for n, _ in migrations.STEPS if n is not migrations.EVERY_TIME]
    assert numbers == sorted(set(numbers)) and numbers == list(migrations.MIGRATIONS)
    assert numbers[0] == 2 and numbers[-1] <= SCHEMA_VERSION  # a database records version 1 before its first step
    for number, step in migrations.STEPS:  # each a function of its own, named after its version when it has one
        assert step.__name__.startswith(f"_v{number}_" if number else "_") and step.__doc__, step.__name__
    assert len({step for _, step in migrations.STEPS}) == len(migrations.STEPS)


def _recorded(monkeypatch) -> list[str]:
    """The names of the steps run from now on, in their order."""
    ran: list[str] = []

    def recording(step):
        def run(store, conn):
            ran.append(step.__name__)
            step(store, conn)

        return run

    monkeypatch.setattr(migrations, "STEPS", tuple((n, recording(s)) for n, s in migrations.STEPS))
    return ran


def test_a_database_gets_the_steps_of_the_versions_it_is_older_than(tmp_path, monkeypatch):
    every_time = [s.__name__ for n, s in migrations.STEPS if n is migrations.EVERY_TIME]
    newer = [s.__name__ for n, s in migrations.STEPS if n is not migrations.EVERY_TIME and n > 2]
    up_to_date = Store(tmp_path / "new" / "ap_coder.db").path
    first = _old_database(OLD / "schema-02-db95511.sql", tmp_path / "pr1")
    ran = _recorded(monkeypatch)
    Store(up_to_date)
    assert ran == every_time
    ran.clear()
    Store(first)  # PR #1's: every step but the one to 2, in the order of STEPS
    assert sorted(ran) == sorted(every_time + newer) and [r for r in ran if r in newer] == newer
