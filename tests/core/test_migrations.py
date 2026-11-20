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
# pilot's and the hand-over's tags since, then GitHub's tested builds from PR #133, D-247): PR #1's 2 is the oldest an
# update starts from.
RELEASED = {2, 13, 14, 15, 16, 17, 18, 19, 27, 28, 29, 30, 31, 32}
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


def test_every_schema_since_pr_99s_has_an_old_database():
    """Every green main commit is a release (D-247), so each schema from PR #99's 27 to the one before this build's
    was some user's: each has an old database to bring up to date, and a step that breaks only from it is caught."""
    missing = sorted(set(range(27, SCHEMA_VERSION)) - {int(p.name.split("-")[1]) for p in SNAPSHOTS})
    assert not missing, (
        f"No old database at schema {', '.join(map(str, missing))}: add the releases at it (the v0.3.0-build.<sha> "
        "tags: git ls-remote --tags origin; if no release had it, the commit that first made it, to UNRELEASED) to "
        "RELEASES in scripts/old_schemas.py, then run: python scripts/old_schemas.py (in a full clone: git fetch "
        "--unshallow, or it rewrites the older files)"
    )


def test_a_database_kept_as_sql_is_made_again_the_same(tmp_path):
    """The way the old databases are kept (``scripts/old_schemas.py``'s dump) loses nothing: a new database dumped and
    made again from the SQL has the same tables, columns, indexes, triggers and settings."""
    old_schemas = load_script("old_schemas", "ap_old_schemas")
    made = Store(tmp_path / "a" / "ap_coder.db").path
    again = _old_database_from(old_schemas.dump(made), tmp_path / "b")
    assert shape(again) == shape(made)

    def settings(db: Path) -> list[tuple[str, str]]:  # but what is new in every database, which the dump writes as one
        with closing(sqlite3.connect(db)) as conn:
            rows = conn.execute("SELECT key, value FROM settings ORDER BY key").fetchall()
        return [(key, value) for key, value in rows if key not in old_schemas.NEW_EACH_TIME]

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


def test_a_journal_loaded_before_its_dates_read_were_kept_says_none(tmp_path):
    """B-2543: a journal file loaded at schema 27, before a file kept how its G/L dates were read, is kept with none
    said (''), so a journal printed day first loaded since is told from it; the database is then a new one."""
    db = Store(tmp_path / "old" / "ap_coder.db").path
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("ALTER TABLE journal_files DROP COLUMN dates_read")
        conn.execute("INSERT INTO journal_files (name, hash, loaded_at, done) VALUES ('2024.csv', 'h', '2026-11', 1)")
        conn.execute("UPDATE settings SET value = '27' WHERE key = 'schema_version'")
    assert [(f["name"], f["dates_read"]) for f in Store(db).journal_files()] == [("2024.csv", "")]
    assert shape(db) == shape(Store(tmp_path / "new" / "ap_coder.db").path)


# What JD Edwards' voucher match names of a PO, kept from schema 31 (D-264).
_JDE_PO_COLUMNS = (("purchase_orders", "order_type"), ("purchase_orders", "order_company"),
                   ("purchase_orders", "supplier_number"), ("po_lines", "erp_line"), ("po_lines", "uom"),
                   ("po_lines", "open_quantity"), ("po_lines", "tax_flag"), ("po_lines", "tax_code"),
                   ("po_lines", "tax_area"))  # fmt: skip
# (D-268: a PO line's subledger and its type came after, with no version of their own)
_SUBLEDGER_COLUMNS = (("po_lines", "subledger"), ("po_lines", "subledger_type"))
# (D-273: a PO line's retainage percent came after them, with no version of its own either)
_RETAINAGE_COLUMNS = (("po_lines", "retainage"),)


def _without_order_keys(conn):
    """A database as schema 31 made it: no PO kept its number apart from its identity (D-267)."""
    conn.execute("DROP INDEX purchase_orders_number")
    conn.execute("ALTER TABLE purchase_orders DROP COLUMN number_key")


def test_a_purchase_order_kept_before_pos_had_a_currency_has_none(tmp_path):
    """B-2612: a PO imported at schema 29, before a PO kept its currency, is kept with none ('': taken as CAD); the
    database is then a new one."""
    db = Store(tmp_path / "old" / "ap_coder.db").path
    with closing(sqlite3.connect(db)) as conn, conn:
        _without_order_keys(conn)
        for table, column in (("purchase_orders", "currency"), *_JDE_PO_COLUMNS, *_SUBLEDGER_COLUMNS,
                              *_RETAINAGE_COLUMNS):  # fmt: skip
            conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        conn.execute("INSERT INTO purchase_orders (po_key, po_number, updated_at) VALUES ('81', 'PO-81', '2026-11')")
        conn.execute("UPDATE settings SET value = '29' WHERE key = 'schema_version'")
    assert Store(db).purchase_order("81")["currency"] == ""
    assert shape(db) == shape(Store(tmp_path / "new" / "ap_coder.db").path)


def test_a_purchase_order_kept_before_the_voucher_match_names_no_jd_edwards_line(tmp_path):
    """D-264: a PO and its line imported at schema 30, before a PO kept JD Edwards' order type, company, supplier
    number and each line's own number, unit and quantity open, are kept with none ('' and NULL): their voucher match
    waits for the lines loaded again. The database is then a new one."""
    db = Store(tmp_path / "old" / "ap_coder.db").path
    with closing(sqlite3.connect(db)) as conn, conn:
        _without_order_keys(conn)
        for table, column in (*_JDE_PO_COLUMNS, *_SUBLEDGER_COLUMNS, *_RETAINAGE_COLUMNS):
            conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        conn.execute("INSERT INTO purchase_orders (po_key, po_number, updated_at) VALUES ('82', 'PO-82', '2026-11')")
        conn.execute("INSERT INTO po_lines (po_key, line_number, description, quantity, unit_price, amount) VALUES "
                     "('82', 1, 'Toner', 2, 40, 80)")  # fmt: skip
        conn.execute("UPDATE settings SET value = '30' WHERE key = 'schema_version'")
    po = Store(db).purchase_order("82")
    assert (po["order_type"], po["order_company"], po["supplier_number"]) == ("", "", "")
    assert [(li["erp_line"], li["uom"], li["open_quantity"]) for li in po["lines"]] == [("", "", None)]
    assert shape(db) == shape(Store(tmp_path / "new" / "ap_coder.db").path)


def test_past_coding_and_po_lines_kept_before_subledgers_were_read_have_none(tmp_path):
    """D-268: a past coding line and a PO line kept before the subledger, its type and the units were read are kept
    with none ('' and NULL); the database is then a new one."""
    db = Store(tmp_path / "old" / "ap_coder.db").path
    with closing(sqlite3.connect(db)) as conn, conn:
        for table, column in (*_SUBLEDGER_COLUMNS, *_RETAINAGE_COLUMNS, ("journal_lines", "subledger"),
                              ("journal_lines", "subledger_type"), ("journal_lines", "units")):  # fmt: skip
            conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        conn.execute("INSERT INTO purchase_orders (po_key, po_number, updated_at) VALUES ('83', 'PO-83', '2026-11')")
        conn.execute("INSERT INTO po_lines (po_key, line_number, description, quantity, unit_price, amount) VALUES "
                     "('83', 1, 'Gravel', 2, 40, 80)")  # fmt: skip
    po = Store(db).purchase_order("83")
    assert [(li["subledger"], li["subledger_type"]) for li in po["lines"]] == [("", "")]
    assert shape(db) == shape(Store(tmp_path / "new" / "ap_coder.db").path)


def test_po_lines_kept_before_a_subcontracts_retainage_was_read_hold_none_back(tmp_path):
    """D-273: a PO line kept before a line's retainage percent was read is kept with none (NULL: nothing held back);
    the database is then a new one."""
    db = Store(tmp_path / "old" / "ap_coder.db").path
    with closing(sqlite3.connect(db)) as conn, conn:
        for table, column in _RETAINAGE_COLUMNS:
            conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        conn.execute("INSERT INTO purchase_orders (po_key, po_number, updated_at) VALUES ('84', 'PO-84', '2026-11')")
        conn.execute("INSERT INTO po_lines (po_key, line_number, description, quantity, unit_price, amount) VALUES "
                     "('84', 1, 'Site work', 1, 100000, 100000)")  # fmt: skip
    po = Store(db).purchase_order("84")
    assert [li["retainage"] for li in po["lines"]] == [None]
    assert shape(db) == shape(Store(tmp_path / "new" / "ap_coder.db").path)


def test_the_lists_of_a_database_loaded_before_budget_locked_jobs_took_postings_are_worked_out_again(
    tmp_path, monkeypatch
):
    """B-2750: a business units report with a job at K (budget locked) loaded before K took postings (D-290) left the
    job and the codes only it has out of the cost centers and GL accounts. The chart's answers read K as open now,
    so its accounts were offered and a line coded to one was CC_UNKNOWN and GL_UNKNOWN. A database whose lists were
    worked out before has them worked out again, once; what is offered is what a line's check takes."""
    from ap_coder.coding.validation import validate_coding
    from ap_coder.core.chart import BuRow, ChartRow
    from ap_coder.core.schema import InvoiceCoding, LineItem
    from ap_coder.core.store import chart as chart_store

    db = Store(tmp_path / "ap_coder.db").path
    with monkeypatch.context() as old:  # (the lists worked out as the release before D-290 did: K closed)
        old.setattr(chart_store, "ACTIVE", ("ACT", ""))
        store = Store(db)
        store.import_business_units([BuRow("100001", "Tower job", "00100", "JB", ""),
                                     BuRow("100002", "Bridge job", "00100", "JB", "K")])  # fmt: skip
        store.import_chart([ChartRow(bu, obj, "", said, lod, "", "", "00100")
                            for bu, obj, said, lod in (("100001", "", "Tower job", 3), ("100001", "5110", "Labour", 7),
                                                       ("100002", "", "Bridge job", 3), ("100002", "5110", "Labour", 7),
                                                       ("100002", "5300", "Rebar", 7))])  # fmt: skip
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("DELETE FROM settings WHERE key = 'chart_active'")  # (a database of a release before it)
    assert [r["code"] for r in Store(db).list_accounts("cost_centers")] == ["100001", "100002"]
    store = Store(db)
    assert [r["code"] for r in store.list_accounts("gl_accounts")] == ["5110", "5300"]
    reference = store.reference_data()
    assert reference.pairs.search("rebar")[0][0] == "100002.5300"
    line = LineItem(line_number=1, description="Rebar", quantity=1, unit_price=100, amount=100,
                    predicted_gl_code="5300", predicted_cost_center="100002")  # fmt: skip
    coding = InvoiceCoding(vendor_name="Acme Steel", invoice_number="1", invoice_date="2026-01-01", currency="CAD",
                           subtotal=100, tax_total=0, grand_total=100, confidence_score=0.9,
                           line_items=[line])  # fmt: skip
    found = {issue.code for issue in validate_coding(coding, reference).issues}
    assert not found & {"CC_UNKNOWN", "GL_UNKNOWN", "BU_INACTIVE"}, found
    version = store.chart_version()
    assert Store(db).chart_version() == version  # (once: opened again, nothing is worked out)


def test_the_lists_worked_out_again_by_an_older_build_are_worked_out_again_at_the_next_open(tmp_path, monkeypatch):
    """B-2752: B-2750's rebuild was recorded once (settings.chart_active); a trip back to an older build that loaded
    the business units again worked the lists out with K closed and recorded nothing, so the next open of this build
    left the K job out of the cost centers again, its lines CC_UNKNOWN. What the lists were worked out with is
    recorded with the chart's mark of that time: lists another build worked out since are worked out again."""
    from ap_coder.core.chart import BuRow, ChartRow
    from ap_coder.core.store import chart as chart_store

    units = [BuRow("100001", "Tower job", "00100", "JB", ""), BuRow("100002", "Bridge job", "00100", "JB", "K")]
    store = Store(tmp_path / "ap_coder.db")
    store.import_business_units(units)
    rows = (("100001", "", "Tower job", 3), ("100001", "5110", "Labour", 7), ("100002", "", "Bridge job", 3),
            ("100002", "5300", "Rebar", 7))  # fmt: skip
    store.import_chart([ChartRow(bu, obj, "", said, lod, "", "", "00100") for bu, obj, said, lod in rows])
    assert [r["code"] for r in store.list_accounts("cost_centers")] == ["100001", "100002"]
    with closing(sqlite3.connect(store.path)) as conn:
        recorded = conn.execute("SELECT value FROM settings WHERE key = 'chart_active'").fetchone()[0]
    with monkeypatch.context() as older:  # (the older build: K closed, and nothing recorded of it)
        older.setattr(chart_store, "ACTIVE", ("ACT", ""))
        Store(store.path).import_business_units([*units, BuRow("100003", "Yard", "00100", "JB", "")])
    with closing(sqlite3.connect(store.path)) as conn, conn:
        conn.execute("UPDATE settings SET value = ? WHERE key = 'chart_active'", (recorded,))
        assert [r[0] for r in conn.execute("SELECT code FROM cost_centers ORDER BY code")] == ["100001"]
    again = Store(store.path)
    assert [r["code"] for r in again.list_accounts("cost_centers")] == ["100001", "100002"]
    assert [r["code"] for r in again.list_accounts("gl_accounts")] == ["5110", "5300"]
    version = again.chart_version()
    assert Store(store.path).chart_version() == version  # (then nothing more: one settings read an open)
