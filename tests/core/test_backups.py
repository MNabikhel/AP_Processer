"""Backups: consistent copies, once a day automatically, and a restore that can be undone."""

import os
import shutil
import sqlite3
import time
from pathlib import Path

import pytest

from ap_coder.core.audit import describe
from ap_coder.core.store import Store
from ap_coder.core.store.backup_files import Retention
from ap_coder.core.store.backups import DAILY

TWO_DAILIES = Retention(daily=2, weekly=0, monthly=0)  # the daily rule alone (the weeks and months: D-187)


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
    store.auto_backup(min_hours=0)
    daily = [p for p in store.list_backups() if DAILY.fullmatch(p.stem)]
    assert len(daily) == 14
    manual = store.backup_now("manual")
    store.auto_backup(min_hours=0)
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


def test_a_backup_with_the_summarys_triggers_as_another_version_made_them_restores_and_their_sql_never_runs(
    tmp_path, ground_truth, monkeypatch
):
    """The check let through the summary's triggers only exactly as this version makes them: a backup of a version
    whose trigger SQL differs was refused as planted. They are known by their names now, and the restore makes them
    again in its own copy first (D-159), so the SQL a backup gave them never runs, not even on another worker's change
    while the restore goes on. Another trigger's name is still refused, and so is a backup where one of ours can't be
    made."""
    from ap_coder.core.store import backup_files
    from ap_coder.core.store.summary import SUMMARY_TRIGGERS

    store = Store(tmp_path / "ap.db")
    store.add_invoice(tmp_path / "a.pdf", ground_truth, {})
    ran = "INSERT OR REPLACE INTO settings (key, value) VALUES ('planted', 'ran')"
    planted = _planted(
        tmp_path,
        f"""DROP TRIGGER changes_vendors_added;
        CREATE TRIGGER changes_vendors_added AFTER INSERT ON vendors BEGIN {ran}; END;
        DROP TRIGGER summary_invoice_added;
        CREATE TRIGGER summary_invoice_added AFTER INSERT ON invoices BEGIN {ran}; END;""",
    )
    Store.check_backup(planted)
    seen, files = [], backup_files.restore_files

    def meanwhile(*args, **kwargs):  # the database copied, not migrated yet: another worker's change
        with sqlite3.connect(store.path) as conn:
            seen.append(dict(conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'trigger'").fetchall()))
            conn.execute("INSERT INTO vendors (vendor_key, display_name, updated_at) VALUES ('w', 'W', '2026')")
            conn.execute("INSERT INTO invoices (source_path, file_name, status, created_at) VALUES ('x', 'x', "
                         "'review', '2026')")  # fmt: skip
        return files(*args, **kwargs)

    monkeypatch.setattr(backup_files, "restore_files", meanwhile)
    store.restore_from(planted)
    assert seen == [SUMMARY_TRIGGERS]
    store.save_vendor("acme", "Acme")
    store.add_invoice(tmp_path / "b.pdf", ground_truth, {})
    assert store.get_setting("planted") == "" and len(store.list_invoices()) == 2  # (the backup had none)
    with sqlite3.connect(store.path) as conn:
        assert dict(conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'trigger'")) == SUMMARY_TRIGGERS
    monkeypatch.undo()
    for sql in ("CREATE TRIGGER changes_vendors_added_too AFTER INSERT ON vendors BEGIN SELECT 1; END;",
                "DROP TABLE po_lines; CREATE TRIGGER changes_po_lines_added AFTER INSERT ON vendors BEGIN SELECT 1; "
                "END;"):  # fmt: skip
        with pytest.raises(ValueError, match="triggers|not an AP Coder database"):
            store.restore_from(_planted(tmp_path, sql, f"ap_coder-20260101-09000{len(sql) % 10}.db"))
    assert len(store.list_invoices()) == 2 and len(list(store.backup_dir().glob("*before-restore.db"))) == 1


def _while_restoring(store: Store, backup: Path, monkeypatch, meanwhile) -> None:
    """``store.restore_from(backup)``, with ``meanwhile()`` run as the data folder's files are copied: the database is
    the backup's then, not migrated yet (minutes on a big data folder), and the web workers answer."""
    from ap_coder.core.store import backup_files

    files = backup_files.restore_files

    def copying(*args, **kwargs):
        meanwhile()
        return files(*args, **kwargs)

    monkeypatch.setattr(backup_files, "restore_files", copying)
    store.restore_from(backup)


def _add_vendor(store: Store, key: str, erp_id: str) -> None:
    with sqlite3.connect(store.path) as conn:
        conn.execute("INSERT INTO vendors (vendor_key, display_name, erp_id, updated_at) VALUES (?, ?, ?, '2026')",
                     (key, key.title(), erp_id))  # fmt: skip


def _erp_ids(store: Store) -> dict[str, str]:
    with sqlite3.connect(store.path) as conn:
        return dict(conn.execute("SELECT vendor_key, erp_id FROM vendors WHERE erp_id != ''").fetchall())


def test_while_a_backup_made_before_the_counts_is_restored_another_workers_change_is_counted(tmp_path, monkeypatch):
    """A backup of a version before D-159 (the same schema version, no ``changes_*`` triggers) was put in as it was:
    its triggers came only with the migration, after the data folder's files were copied. Meanwhile a vendor added
    by someone else was not counted, and a worker kept answering with the vendor list it had kept. The restore's copy
    gets every count's trigger whose table it has before it goes in."""
    from ap_coder.core.store.summary import SUMMARY_TRIGGERS

    store = Store(tmp_path / "ap.db")
    _add_vendor(store, "acme", "100")
    backup = store.backup_now("manual")
    with sqlite3.connect(backup) as conn:
        for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'changes_%'"):
            conn.execute(f"DROP TRIGGER {name}")
    worker = Store(store.path)  # another web worker
    seen = {}

    def meanwhile():
        with sqlite3.connect(store.path) as conn:
            seen["triggers"] = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
        seen["first"] = worker.vendor_ids()
        _add_vendor(store, "globex", "200")
        seen["again"], seen["fresh"] = worker.vendor_ids(), _erp_ids(store)

    _while_restoring(store, backup, monkeypatch, meanwhile)
    assert seen["triggers"] == set(SUMMARY_TRIGGERS) and seen["first"] == {"acme": "100"}
    assert seen["again"] == seen["fresh"] == {"acme": "100", "globex": "200"}


def test_while_a_backup_is_restored_no_answer_kept_before_is_taken_when_a_count_comes_back_to_its_number(
    tmp_path, monkeypatch
):
    """Until the restore's last step the database had the backup's ``summary_id``, the one before, and the backup's
    counts: one change meanwhile brought ``changes/vendors`` back to the number a worker had kept an answer at before
    the restore (of other rows), and that answer was taken. The restore's copy gets a new ``summary_id`` before it
    goes in, and another after the migration."""
    store = Store(tmp_path / "ap.db")
    _add_vendor(store, "acme", "100")
    backup = store.backup_now("manual")
    with sqlite3.connect(store.path) as conn:  # after the backup: Acme's ID changed
        conn.execute("UPDATE vendors SET erp_id = '999' WHERE vendor_key = 'acme'")
    worker = Store(store.path)  # another web worker
    assert worker.vendor_ids() == {"acme": "999"}
    before, seen = store.get_setting("summary_id"), {}

    def meanwhile():
        seen["id"] = store.get_setting("summary_id")
        _add_vendor(store, "globex", "200")  # the count back to the one the worker kept its answer at
        seen["worker"], seen["fresh"] = worker.vendor_ids(), _erp_ids(store)

    _while_restoring(store, backup, monkeypatch, meanwhile)
    assert seen["worker"] == seen["fresh"] == {"acme": "100", "globex": "200"}
    assert len({before, seen["id"], store.get_setting("summary_id")}) == 3


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
            (folder / f"ap_coder-202602{day:02d}-090000.files.json").write_text('{"files": []}')  # complete
    store.auto_backup(keep=Retention(daily=14, weekly=0, monthly=0), min_hours=0)
    for folder in (store.backup_dir(), second):
        names = sorted(p.name for p in folder.glob("ap_coder-*.db"))
        assert len(names) == 14 and not [n for n in names if "20260110" in n], (folder, names)


# --- The files of a backup: the invoice files, saved emails and attachments too (D-143) ---------------------------


def _data_folder(tmp_path, ground_truth, name: str = "data"):
    """A data folder with an invoice and the files AP Coder keeps beside the database."""
    data = tmp_path / name
    (data / "invoices" / "originals").mkdir(parents=True)
    (data / "emails" / "manager").mkdir(parents=True)
    pdf = data / "invoices" / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4 the invoice a")
    (data / "invoices" / "originals" / "a.jpg").write_bytes(b"the photo of a")
    (data / "emails" / "manager" / "AP-1-reply.eml").write_bytes(b"From: the manager")
    (data / "canada_tax_rates.csv").write_text("province,gst\nON,5\n")
    store = Store(data / "ap_coder.db")
    store.add_invoice(pdf, ground_truth, {})
    return store, data


def _object(folder, path):
    """Where the backups in ``folder`` keep the content of the file ``path``."""
    from ap_coder.core.paths import file_sha256

    sha = file_sha256(path)
    return folder / "files" / sha[:2] / sha


def test_a_restore_brings_back_the_invoice_files_of_that_state(tmp_path, ground_truth):
    """A backup held the database only: a restore after a file was lost brought back the invoice without its PDF."""
    store, data = _data_folder(tmp_path, ground_truth)
    backup = store.backup_now("manual")
    pdf = data / "invoices" / "a.pdf"
    pdf.unlink()  # lost since
    (data / "emails" / "manager" / "AP-1-reply.eml").write_bytes(b"changed since")
    later = data / "invoices" / "b.pdf"
    later.write_bytes(b"%PDF-1.4 came in after the backup")
    store.restore_state(backup)
    assert pdf.read_bytes() == b"%PDF-1.4 the invoice a"
    assert (data / "emails" / "manager" / "AP-1-reply.eml").read_bytes() == b"From: the manager"
    assert store.invoice_file(store.get_invoice(1)) == pdf
    # What was not in that state is kept aside, never deleted: the file that came in after, the reply as it was.
    assert not later.exists()
    (aside,) = store.backup_dir().glob("set-aside-*")
    assert (aside / "invoices" / "b.pdf").read_bytes() == b"%PDF-1.4 came in after the backup"
    assert (aside / "emails" / "manager" / "AP-1-reply.eml").read_bytes() == b"changed since"


def test_the_safety_backup_of_a_restore_undoes_it_files_and_all(tmp_path, ground_truth):
    store, data = _data_folder(tmp_path, ground_truth)
    backup = store.backup_now("manual")
    later = data / "invoices" / "b.pdf"
    later.write_bytes(b"%PDF-1.4 b")
    store.add_invoice(later, ground_truth, {})
    done = store.restore_state(backup)
    assert len(store.list_invoices()) == 1 and not later.exists()
    store.restore_state(done.safety)
    assert len(store.list_invoices()) == 2 and later.read_bytes() == b"%PDF-1.4 b"


def test_a_daily_backup_copies_only_the_files_that_are_new(tmp_path, ground_truth, monkeypatch):
    from ap_coder.core.store import backup_files

    store, data = _data_folder(tmp_path, ground_truth)
    store.backup_now()
    copied = []
    real = backup_files._copy_hashed
    monkeypatch.setattr(
        backup_files, "_copy_hashed", lambda source, out: copied.append(source.name) or real(source, out)
    )
    (data / "invoices" / "b.pdf").write_bytes(b"%PDF-1.4 b")
    second = store.backup_now()
    assert copied == ["b.pdf"]
    held = backup_files.check(second)
    assert held.files == 5 and held.invoices == 1 and not held.missing and not held.damaged


def test_restoring_from_the_second_folder_alone_onto_an_empty_data_folder(tmp_path, ground_truth):
    """A lost or broken computer: a new one with only the second backup folder gets back the database and every file."""
    share = tmp_path / "share"
    share.mkdir()
    store, data = _data_folder(tmp_path, ground_truth)
    store.set_setting("backup_copy_dir", str(share))
    backup = store.backup_now()
    assert (share / backup.name).is_file() and (share / f"{backup.stem}.files.json").is_file()
    names = ("invoices/a.pdf", "invoices/originals/a.jpg", "emails/manager/AP-1-reply.eml", "canada_tax_rates.csv")
    was = {name: (data / name).read_bytes() for name in names}
    shutil.rmtree(data)  # the computer is lost, its backups folder too
    new = Store(tmp_path / "new" / "ap_coder.db")
    done = new.restore_state(share / backup.name)
    assert done.files.put_back == 4 and not done.files.missing and not done.files.damaged
    for name in names:
        assert (tmp_path / "new" / name).read_bytes() == was[name], name
    (inv,) = new.list_invoices()
    found = new.invoice_file(new.get_invoice(inv["id"]))
    assert found == (tmp_path / "new" / "invoices" / "a.pdf").resolve()  # found in its new place


def test_a_restore_onto_a_data_folder_elsewhere_points_every_invoice_at_its_file_there(tmp_path, ground_truth):
    """Read straight from the rows (Learning's files missing, teaching again from approvals), every invoice's file was
    at the old computer's path after a restore onto a new one, until each was opened (B-1552)."""
    store, data = _data_folder(tmp_path, ground_truth)
    backup = store.backup_now("manual")
    new = Store(tmp_path / "new" / "ap_coder.db")
    new.restore_state(backup)
    shutil.rmtree(data)
    with new._conn() as conn:
        (source,) = conn.execute("SELECT source_path FROM invoices").fetchone()
    assert Path(source) == (tmp_path / "new" / "invoices" / "a.pdf").resolve()


def test_pruning_keeps_every_file_a_kept_backup_needs_and_drops_the_rest(tmp_path, ground_truth, monkeypatch):
    import datetime as dt
    from types import SimpleNamespace

    from ap_coder.core.store import backup_files, backups

    day = [1]

    class Clock(dt.datetime):  # one day later at each backup
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, day[0], 9, 0, 0)

    monkeypatch.setattr(backups, "dt", SimpleNamespace(datetime=Clock, timedelta=dt.timedelta))
    monkeypatch.setattr(backup_files, "GRACE", 0.0)  # (a file copied a moment ago is kept a day: a backup being made)
    share = tmp_path / "share"
    share.mkdir()
    store, data = _data_folder(tmp_path, ground_truth)
    store.set_setting("backup_copy_dir", str(share))
    invoices = data / "invoices"
    first = store.auto_backup(keep=TWO_DAILIES, min_hours=0)
    only_first = invoices / "a.pdf"
    gone = {folder: _object(folder, only_first) for folder in (store.backup_dir(), share)}
    only_first.unlink()
    (invoices / "manual.pdf").write_bytes(b"%PDF-1.4 kept by a labelled backup")
    day[0] = 2
    manual = store.backup_now("manual")
    labelled = {folder: _object(folder, invoices / "manual.pdf") for folder in (store.backup_dir(), share)}
    (invoices / "manual.pdf").unlink()
    (invoices / "b.pdf").write_bytes(b"%PDF-1.4 b")
    day[0] = 3
    store.auto_backup(keep=TWO_DAILIES, min_hours=0)
    (invoices / "c.pdf").write_bytes(b"%PDF-1.4 c")
    day[0] = 4
    store.auto_backup(keep=TWO_DAILIES, min_hours=0)
    for folder in (store.backup_dir(), share):
        assert not (folder / first.name).exists() and not (folder / f"{first.stem}.files.json").exists()
        assert not gone[folder].exists(), folder  # needed by the pruned backup only
        assert labelled[folder].exists(), folder  # a labelled backup is never pruned, nor its files
        for kept in sorted(folder.glob("ap_coder-*.db")):
            held = backup_files.check(kept, deep=True)
            assert not held.missing and not held.damaged, (folder, kept.name)
    assert (store.backup_dir() / manual.name).exists()


def test_a_damaged_or_missing_file_is_reported_by_the_check_and_the_restore(tmp_path, ground_truth):
    from ap_coder.core.store import backup_files

    store, data = _data_folder(tmp_path, ground_truth)
    backup = store.backup_now("manual")
    _object(store.backup_dir(), data / "invoices" / "a.pdf").write_bytes(b"%PDF-1.4 the invoice A")  # same size
    _object(store.backup_dir(), data / "emails" / "manager" / "AP-1-reply.eml").unlink()
    held = backup_files.check(backup, deep=True)
    assert held.damaged == ["invoices/a.pdf"] and held.missing == ["emails/manager/AP-1-reply.eml"]
    quick = backup_files.check(backup)  # the page's check: there, and of the right size
    assert quick.missing == ["emails/manager/AP-1-reply.eml"] and not quick.damaged
    (data / "invoices" / "a.pdf").unlink()
    (data / "emails" / "manager" / "AP-1-reply.eml").unlink()
    done = store.restore_state(backup)
    assert done.files.damaged == ["invoices/a.pdf"] and done.files.missing == ["emails/manager/AP-1-reply.eml"]
    assert not (data / "invoices" / "a.pdf").exists()  # never a damaged copy put in its place
    (event,) = [e for e in store.events() if e["action"] == "backup_restored"]
    assert "2 could not be restored" in describe(event)


def test_a_backup_list_of_files_never_writes_outside_the_data_folder(tmp_path, ground_truth):
    """A planted list (a backup on a share others can write to) naming ``../`` or the .env is not followed."""
    import json

    store, data = _data_folder(tmp_path, ground_truth)
    backup = store.backup_now("manual")
    listed = backup.with_name(f"{backup.stem}.files.json")
    found = json.loads(listed.read_text())
    good = found["files"][0]
    found["files"] += [{**good, "path": p} for p in ("../evil.pdf", ".env", "invoices/../../evil.pdf", "/tmp/evil.pdf",
                                                     "backups/x.db", "invoices\\..\\..\\evil.pdf")]  # fmt: skip
    listed.write_text(json.dumps(found))
    store.restore_state(backup)
    assert not (tmp_path / "evil.pdf").exists() and not (data / ".env").exists()
    assert not (data / "backups" / "x.db").exists()


def test_a_backup_made_before_files_were_backed_up_restores_its_database_and_leaves_the_files(tmp_path, ground_truth):
    store, data = _data_folder(tmp_path, ground_truth)
    backup = store.backup_now("manual")
    backup.with_name(f"{backup.stem}.files.json").unlink()  # as a backup of an older AP Coder
    (data / "invoices" / "b.pdf").write_bytes(b"%PDF-1.4 b")
    done = store.restore_state(backup)
    assert done.files.database_only and (data / "invoices" / "b.pdf").exists()


def test_the_postings_ledger_is_merged_never_replaced_and_comes_back_on_a_new_computer(tmp_path, ground_truth):
    """``jde_postings.jsonl`` is what JD Edwards holds (D-105): a restore keeps every line written since the backup."""
    store, data = _data_folder(tmp_path, ground_truth)
    ledger = data / "jde_postings.jsonl"
    ledger.write_text('{"idem_key": "a", "status": "posted"}\n')
    share = tmp_path / "share"
    share.mkdir()
    store.set_setting("backup_copy_dir", str(share))
    backup = store.backup_now()
    with ledger.open("a") as out:
        out.write('{"idem_key": "b", "status": "posted"}\n')
    store.restore_state(backup)
    assert ledger.read_text().splitlines() == ['{"idem_key": "a", "status": "posted"}',
                                               '{"idem_key": "b", "status": "posted"}']  # fmt: skip
    new = Store(tmp_path / "new" / "ap_coder.db")
    new.restore_state(share / backup.name)
    assert (tmp_path / "new" / "jde_postings.jsonl").read_text() == '{"idem_key": "a", "status": "posted"}\n'


def test_an_invoice_file_kept_outside_the_data_folder_is_in_the_backup(tmp_path, ground_truth):
    """``watch --folder`` reads invoices where they are: their files are backed up too, put back in invoices/restored
    (D-143)."""
    store, data = _data_folder(tmp_path, ground_truth)
    elsewhere = tmp_path / "scans" / "z.pdf"
    elsewhere.parent.mkdir()
    elsewhere.write_bytes(b"%PDF-1.4 z")
    store.add_invoice(elsewhere, ground_truth, {})
    backup = store.backup_now("manual")
    elsewhere.unlink()
    store.restore_state(backup)
    assert (data / "invoices" / "restored" / "z.pdf").read_bytes() == b"%PDF-1.4 z"
    found = [store.invoice_file(store.get_invoice(inv["id"])) for inv in store.list_invoices()]
    assert (data / "invoices" / "restored" / "z.pdf").resolve() in found


def test_a_backup_whose_files_cannot_be_saved_says_so(tmp_path, ground_truth, monkeypatch):
    from ap_coder.core.store import backup_files

    store, _data = _data_folder(tmp_path, ground_truth)

    def full(source, out):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(backup_files, "_copy_hashed", full)
    with pytest.raises(OSError, match="No space left"):
        store.auto_backup()
    assert store.get_setting("backup_status").startswith("failed")
    monkeypatch.undo()
    store.backup_now("manual")
    assert store.get_setting("backup_status") == ""


# --- The review of pilot8 (B-1556 to B-1563) ------------------------------------------------------------------------


def _frozen_days(monkeypatch):
    """``backups``' clock, one day later each time ``day[0]`` is moved on (the daily backups' names)."""
    import datetime as dt
    from types import SimpleNamespace

    from ap_coder.core.store import backups

    day = [1]

    class Clock(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, day[0], 9, 0, 0)

    monkeypatch.setattr(backups, "dt", SimpleNamespace(datetime=Clock, timedelta=dt.timedelta))
    return day


def test_a_restore_leaves_in_place_a_file_its_backup_could_not_read(tmp_path, ground_truth, monkeypatch):
    """A file held open by a scanner when the backup was made is not in it: restoring that backup moved it aside, so
    its invoice had no PDF (B-1556)."""
    from ap_coder.core.store import backup_files

    store, data = _data_folder(tmp_path, ground_truth)
    locked = data / "invoices" / "locked.pdf"
    locked.write_bytes(b"%PDF-1.4 held by the scanner")
    real = backup_files._readable
    monkeypatch.setattr(backup_files, "_readable", lambda p: "Permission denied" if p.name == "locked.pdf" else real(p))
    backup = store.backup_now("manual")
    monkeypatch.undo()
    assert backup_files.check(backup).skipped == ["invoices/locked.pdf: Permission denied"]
    done = store.restore_state(backup)
    assert locked.read_bytes() == b"%PDF-1.4 held by the scanner" and "invoices/locked.pdf" not in done.files.set_aside


def test_a_backup_whose_files_were_not_saved_is_not_the_days_backup(tmp_path, ground_truth, monkeypatch):
    """Its database copy stayed, counted as the day's backup: none was tried again for 20 hours, and the page called
    it one made before AP Coder backed up the files (B-1557)."""
    from ap_coder.core.store import backup_files

    store, _data = _data_folder(tmp_path, ground_truth)
    real = backup_files.snapshot

    def full(backup, data):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(backup_files, "snapshot", full)
    with pytest.raises(OSError, match="No space left"):
        store.auto_backup()
    assert store.list_backups() == [] and store.get_setting("backup_status").startswith("failed")
    monkeypatch.setattr(backup_files, "snapshot", real)
    made = store.auto_backup()  # an hour later: tried again
    assert made is not None and backup_files.check(made).complete


def test_a_backup_cut_short_never_counts_as_the_days_backup(tmp_path, ground_truth):
    """The dashboard closed while the day's first backup copied the files: its database copy, with no list, stopped
    the daily backups for 20 hours (B-1557)."""
    from ap_coder.core.store import backup_files

    store, _data = _data_folder(tmp_path, ground_truth)
    cut = store.backup_to(store.backup_dir() / "ap_coder-20261103-090000.db")  # the database copied, then cut short
    assert "database only" in backup_files.state_words(backup_files.check(cut))
    made = store.auto_backup()
    assert made is not None and backup_files.check(made).complete


def test_a_copy_to_the_second_folder_cut_short_leaves_no_backup_there(tmp_path, ground_truth, monkeypatch):
    """The share dropped while the files were copied: it held the database alone, a backup that restores no file
    (B-1558)."""
    from ap_coder.core.store import backup_files

    share = tmp_path / "share"
    share.mkdir()
    store, _data = _data_folder(tmp_path, ground_truth)
    store.set_setting("backup_copy_dir", str(share))
    real, calls = backup_files._copy_hashed, []

    def flaky(source, out):
        if str(share) in str(out):
            calls.append(out)
            if len(calls) == 2:
                raise OSError(5, "The network name is no longer available")
        return real(source, out)

    monkeypatch.setattr(backup_files, "_copy_hashed", flaky)
    store.backup_now()
    assert store.get_setting("backup_copy_status").startswith("failed")
    assert not list(share.glob("ap_coder-*.db")) and not list(share.glob("ap_coder-*.files.json"))


def test_backups_without_their_files_never_push_out_the_last_complete_ones(tmp_path, ground_truth, monkeypatch):
    """Retention counted every daily: database-only ones (cut short, or of an older AP Coder) pushed out the complete
    ones, here and on the share (B-1558)."""
    from ap_coder.core.store import backup_files

    day = _frozen_days(monkeypatch)
    share = tmp_path / "share"
    share.mkdir()
    store, _data = _data_folder(tmp_path, ground_truth)
    store.set_setting("backup_copy_dir", str(share))
    store.auto_backup(keep=TWO_DAILIES, min_hours=0)
    day[0] = 2
    second = store.auto_backup(keep=TWO_DAILIES, min_hours=0)
    for folder in (store.backup_dir(), share):
        for n in (3, 4, 5):
            shutil.copy(second, folder / f"ap_coder-2026110{n}-090000.db")  # no list: the database only
    day[0] = 6
    store.auto_backup(keep=TWO_DAILIES, min_hours=0)
    for folder in (store.backup_dir(), share):
        kept = sorted(p.name for p in folder.glob("ap_coder-*.db"))
        assert kept == ["ap_coder-20261102-090000.db", "ap_coder-20261105-090000.db", "ap_coder-20261106-090000.db"]
        assert backup_files.check(folder / second.name, deep=True).complete


def test_the_grace_period_keeps_the_files_of_a_backup_being_made(tmp_path, ground_truth):
    """A file no list names yet (a backup being made, its list not written) is kept a day, then dropped."""
    from ap_coder.core.store import backup_files

    store, _data = _data_folder(tmp_path, ground_truth)
    store.backup_now("manual")
    fresh = store.backup_dir() / "files" / "ab" / ("ab" + "0" * 62)
    fresh.parent.mkdir(parents=True, exist_ok=True)
    fresh.write_bytes(b"copied a moment ago by a backup still being made")
    backup_files.prune(store.backup_dir())
    assert fresh.exists()
    old = time.time() - backup_files.GRACE - 60
    os.utime(fresh, (old, old))
    backup_files.prune(store.backup_dir())
    assert not fresh.exists()


def test_a_restore_refuses_while_a_backup_is_being_made_in_another_process(tmp_path, ground_truth):
    """A backup and a restore ran at once: the backup listed half the restored files, a pruning could remove the
    backup being restored (B-1559). On the server the daily backup runs in the background process."""
    import subprocess
    import sys

    from ap_coder.core.store import backup_files

    store, data = _data_folder(tmp_path, ground_truth)
    backup = store.backup_now("manual")
    held = str(backup_files.lock_path(data))
    holder = subprocess.Popen(
        [sys.executable, "-c", "import pathlib, time; from ap_coder.core import paths; "
         f"h = paths.try_lock(pathlib.Path({held!r})); print('held' if h else 'not', flush=True); time.sleep(30)"],
        stdout=subprocess.PIPE, text=True,
    )  # fmt: skip
    try:
        assert holder.stdout.readline().strip() == "held"
        with pytest.raises(OSError, match="being made or restored"):
            store.restore_state(backup)
        assert store.auto_backup(min_hours=0) is None  # the daily one waits for the next check
        with pytest.raises(OSError, match="being made or restored"):
            store.backup_now("manual")
        assert store.get_setting("backup_status") == ""  # busy is not a failure
    finally:
        holder.kill()
        holder.wait()
    assert store.restore_state(backup).files.put_back == 0


def test_a_file_written_during_a_restore_is_not_moved_aside(tmp_path, ground_truth, monkeypatch):
    """An upload in another web worker while a restore ran: its row in the restored database, its file moved aside
    (B-1559)."""
    from ap_coder.core.store import backup_files

    store, data = _data_folder(tmp_path, ground_truth)
    backup = store.backup_now("manual")
    (data / "invoices" / "a.pdf").unlink()
    upload = data / "invoices" / "uploaded-meanwhile.pdf"
    real = backup_files._fetch

    def meanwhile(entry, folders, part):
        upload.write_bytes(b"%PDF-1.4 uploaded while the restore ran")
        return real(entry, folders, part)

    monkeypatch.setattr(backup_files, "_fetch", meanwhile)
    done = store.restore_state(backup)
    assert upload.exists() and "invoices/uploaded-meanwhile.pdf" not in done.files.set_aside


def test_a_full_disk_while_restoring_is_said_as_such_not_as_a_damaged_backup(tmp_path, ground_truth, monkeypatch):
    """B-1560."""
    from ap_coder.core.store import backup_files

    store, data = _data_folder(tmp_path, ground_truth)
    backup = store.backup_now("manual")
    (data / "invoices" / "a.pdf").unlink()
    real = backup_files._copy_hashed

    def full(source, out):
        if ".restoring" in out.name:
            raise OSError(28, "No space left on device")
        return real(source, out)

    monkeypatch.setattr(backup_files, "_copy_hashed", full)
    done = store.restore_state(backup).files
    assert not done.missing and not done.damaged and "not enough space" in done.words()


def test_a_full_disk_never_stops_ap_coder_opening_after_an_update(tmp_path, ground_truth, monkeypatch):
    """The backup before a migration copied every invoice file too: with room for the database copy only, AP Coder
    no longer opened (B-1561). The database copy is what undoes a migration."""
    from ap_coder.core.store import SCHEMA_VERSION, backup_files

    store, _data = _data_folder(tmp_path, ground_truth)
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE settings SET value = ? WHERE key = 'schema_version'", (str(SCHEMA_VERSION - 1),))

    def full(backup, data):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(backup_files, "snapshot", full)
    opened = Store(store.path)  # the new version opens it
    (made,) = opened.list_backups()
    assert "before-update" in made.name and "invoice files" in opened.get_setting("backup_status")


def test_an_invoice_file_in_the_install_folder_is_backed_up_but_not_the_samples(tmp_path, ground_truth, monkeypatch):
    """The laptop's data folder is <install>/private: a file an update kept in private/backups/from-... was never
    backed up, the whole install folder being left out instead of its samples (B-1562)."""
    from ap_coder.core.store import backup_files

    install = tmp_path / "AP Coder"
    monkeypatch.setattr(backup_files, "PROJECT_DIR", install)
    store = Store(install / "private" / "ap_coder.db")
    kept = install / "private" / "backups" / "from-0.3.0" / "invoices" / "kept.pdf"
    sample = install / "samples" / "northwind.pdf"
    for path in (kept, sample):
        path.parent.mkdir(parents=True)
        path.write_bytes(b"%PDF-1.4 " + path.name.encode())
        store.add_invoice(path, ground_truth, {})
    listed = [e["path"] for e in backup_files.read_list(store.backup_now("manual"))["files"]]
    assert listed == ["invoices/restored/kept.pdf"]


def test_a_file_renamed_only_in_case_is_not_moved_aside_on_windows(tmp_path, ground_truth, monkeypatch):
    """NTFS sees invoices/A.pdf and invoices/a.pdf as one file: the restore found it unchanged, then moved it aside
    as one it did not list (B-1563)."""
    from ap_coder.core.store import backup_files

    store, data = _data_folder(tmp_path, ground_truth)
    backup = store.backup_now("manual")
    (data / "invoices" / "a.pdf").rename(data / "invoices" / "A.pdf")
    monkeypatch.setattr(backup_files, "_case", str.lower, raising=False)  # as os.path.normcase on Windows
    done = store.restore_state(backup)
    assert "invoices/A.pdf" not in done.files.set_aside


def test_a_file_from_before_the_restore_is_moved_aside_even_when_its_time_is_ahead_of_the_clock(tmp_path, ground_truth):
    """On Windows a file's time can read a few milliseconds after the clock that started the restore: the file that
    came in after the backup, but before the restore, was then kept instead of moved aside (B-1566)."""
    store, data = _data_folder(tmp_path, ground_truth)
    backup = store.backup_now("manual")
    later = data / "invoices" / "b.pdf"
    later.write_bytes(b"%PDF-1.4 came in after the backup")
    ahead = time.time_ns() + 2_000_000_000  # two seconds ahead of the clock, as a coarse clock can read it
    os.utime(later, ns=(ahead, ahead))
    done = store.restore_state(backup)
    assert not later.exists() and "invoices/b.pdf" in done.files.set_aside


# --- Authenticator codes across a restore (D-146) -------------------------------------------------------------------


def _codes_at(monkeypatch, when: float = 1_800_000_000.0):
    """The store's clock fixed at ``when`` (the codes of an authenticator app follow it); the clock returned."""
    from ap_coder.core.store import users

    clock = [when]
    monkeypatch.setattr(users, "_clock", lambda: clock[0])
    return clock


def test_authenticator_codes_still_work_after_a_restore_here_and_on_a_new_computer(tmp_path, ground_truth,
                                                                                   monkeypatch):  # fmt: skip
    """The key that seals the secrets is in the whole-state backup and comes back where there is none (a new
    computer); here it stays as it is, and the accounts too (D-101)."""
    from ap_coder.core import authenticator

    clock = _codes_at(monkeypatch)
    share = tmp_path / "share"
    share.mkdir()
    store, data = _data_folder(tmp_path, ground_truth)
    boss = store.add_user("boss", "correct horse battery", role="admin")
    secret = store.authenticator_secret(boss)
    store.check_code(boss, authenticator.code(secret, clock[0]), "10.0.0.5")
    key = (data / authenticator.KEY_FILE).read_bytes()
    store.set_setting("backup_copy_dir", str(share))
    backup = store.backup_now()
    clock[0] += 60
    done = store.restore_state(backup, keep_accounts=True)
    assert authenticator.KEY_FILE not in done.files.set_aside
    assert (data / authenticator.KEY_FILE).read_bytes() == key
    assert store.check_code(boss, authenticator.code(secret, clock[0]), "10.0.0.5")["username"] == "boss"
    shutil.rmtree(data)  # the computer is lost: a new one with the second backup folder only
    new = Store(tmp_path / "new" / "ap_coder.db")
    new.restore_state(share / backup.name, keep_accounts=True)
    assert (tmp_path / "new" / authenticator.KEY_FILE).read_bytes() == key
    clock[0] += 60
    assert new.check_code(boss, authenticator.code(secret, clock[0]), "10.0.0.5")["username"] == "boss"


def test_a_restore_never_replaces_or_sets_aside_the_key_of_the_accounts_here(tmp_path, ground_truth, monkeypatch):
    """A backup made before anyone set up an authenticator app, or on another computer, does not hold this server's
    key: the accounts a restore keeps here are sealed with it."""
    from ap_coder.core import authenticator

    clock = _codes_at(monkeypatch)
    store, data = _data_folder(tmp_path, ground_truth)
    older = store.backup_now("before-codes")  # no key yet
    boss = store.add_user("boss", "correct horse battery", role="admin")
    secret = store.authenticator_secret(boss)
    store.check_code(boss, authenticator.code(secret, clock[0]), "10.0.0.5")
    key = (data / authenticator.KEY_FILE).read_bytes()
    done = store.restore_state(older, keep_accounts=True)
    assert authenticator.KEY_FILE not in done.files.set_aside and (data / authenticator.KEY_FILE).read_bytes() == key
    elsewhere, _ = _data_folder(tmp_path, ground_truth, "elsewhere")  # another server, its own key
    elsewhere.authenticator_secret(elsewhere.add_user("ann", "correct horse battery"))
    theirs = elsewhere.backup_now()
    store.restore_state(theirs, keep_accounts=True)
    assert (data / authenticator.KEY_FILE).read_bytes() == key
    clock[0] += 60
    assert store.check_code(boss, authenticator.code(secret, clock[0]), "10.0.0.5")["username"] == "boss"


def test_a_backup_that_would_fill_the_disk_is_not_started(tmp_path, monkeypatch):
    """A year's database on the company server is 7-9 GB: a daily copy started with less room than that filled the
    disk, and a full disk stops every write to the database, for everyone. The backup is refused before it starts,
    with the reason kept for Settings and the start-up check, and nothing half-written is left behind."""
    from ap_coder.core.store import backups

    store = Store(tmp_path / "ap.db")
    with pytest.MonkeyPatch.context() as rule:  # the real rule (conftest sets no spare room)
        rule.setattr(backups, "BACKUP_RESERVE", 512 * 1024 * 1024)
        real = shutil.disk_usage

        def short(path):
            usage = real(path)
            return usage._replace(free=store.path.stat().st_size + 1024)  # the copy fits, the room to keep doesn't

        monkeypatch.setattr(backups.shutil, "disk_usage", short)
        with pytest.raises(OSError, match="not enough free space"):
            store.backup_now("manual")
        assert not list(store.backup_dir().glob("ap_coder-*"))
        assert store.get_setting(backups.BACKUP_STATUS).startswith("failed ")
        assert "not enough free space" in store.get_setting(backups.BACKUP_STATUS)
        with pytest.raises(OSError, match="not enough free space"):  # the daily one too (its thread logs it)
            store.auto_backup()
    monkeypatch.setattr(backups.shutil, "disk_usage", real)  # room again: the next one is made, the status cleared
    assert store.backup_now("manual").exists() and store.get_setting(backups.BACKUP_STATUS) == ""


def test_the_daily_backup_waits_for_its_hours_but_never_skips_a_day(tmp_path):
    """The company server copies a 7-9 GB database once a day: started 20 hours after the last one, it drifted into
    the working day, minutes of disk work under 300 people. With AP_BACKUP_HOURS (``backup_window``: "1-5", from 1
    to 5 in the morning) it starts only then, unless the last one is more than a day and a bit old (the server was
    off during those hours): then at once."""
    import datetime as dt

    from ap_coder.core.store.backups import backup_window

    assert backup_window("1-5") == (1, 5) and backup_window("22-4") == (22, 4)
    assert backup_window("") is None and backup_window("lunch") is None and backup_window("3-3") is None
    store = Store(tmp_path / "ap.db")
    made = store.auto_backup()
    day = dt.datetime.fromtimestamp(made.stat().st_mtime)
    afternoon = day.replace(hour=14) + dt.timedelta(days=1)
    if afternoon - day < dt.timedelta(hours=21):
        afternoon += dt.timedelta(days=1)
    stamp = (afternoon - dt.timedelta(hours=25)).timestamp()
    os.utime(made, (stamp, stamp))  # the last one 25 hours before a weekday afternoon
    assert store.auto_backup(window=(1, 5), now=afternoon) is None  # outside its hours: it waits
    assert store.auto_backup(window=(22, 4), now=afternoon) is None
    night = afternoon.replace(hour=2) + dt.timedelta(days=1)
    stamp = (night - dt.timedelta(hours=21)).timestamp()
    os.utime(made, (stamp, stamp))
    assert store.auto_backup(window=(1, 5), now=night) is not None  # in its hours, a day after the last
    late = sorted(store.list_backups())[-1]
    stamp = (afternoon - dt.timedelta(hours=31)).timestamp()
    for backup in store.list_backups():
        os.utime(backup, (stamp, stamp))
    assert store.auto_backup(window=(1, 5), now=afternoon) is not None  # the hours were missed: not a day skipped
    assert late.exists()


# --- Retention beyond 14 days (D-187) ---------------------------------------------------------------------------


def _dailies(folder, first, last, complete=True):
    """A daily backup at 02:00 every day from ``first`` to ``last`` (with its list of files when ``complete``)."""
    import datetime as dt

    folder.mkdir(parents=True, exist_ok=True)
    day = first
    while day <= last:
        stem = f"ap_coder-{day:%Y%m%d}-020000"
        (folder / f"{stem}.db").write_bytes(b"db")
        if complete:
            (folder / f"{stem}.files.json").write_text('{"files": []}', encoding="utf-8")
        day += dt.timedelta(days=1)


def _kept_days(folder):
    return sorted(p.name[9:17] for p in folder.glob("ap_coder-*.db") if DAILY.fullmatch(p.stem))


EXPECTED_DAYS = sorted(
    {f"202610{d}" for d in range(24, 32)} | {f"202611{d:02d}" for d in range(1, 7)}  # the newest 14 days
    | {"20261101", "20261025", "20261018", "20261011", "20261004", "20260927", "20260920"}  # 8 weeks (+ Nov 6's)
    | {"20261031", "20260930", "20260831", "20260731", "20260630", "20260531", "20260430", "20260331", "20260228",
       "20260131", "20251231"}  # 12 months (+ November's: the 6th)
)  # fmt: skip


def test_the_server_keeps_14_days_then_one_a_week_for_8_weeks_and_one_a_month_for_a_year(tmp_path, monkeypatch):
    """A year's daily backups on the server: 14 of them were a fortnight's history only, so a mistake found at the
    month end (or the year end) could not be undone. Now the newest 14, the newest of each of the last 8 weeks and of
    each of the last 12 months; a labelled one whatever its age; the newest complete one always. A laptop keeps its
    newest 14, as before."""
    import datetime as dt

    from ap_coder.core.store import backup_files
    from ap_coder.ops import offline

    folder = tmp_path / "backups"
    _dailies(folder, dt.date(2025, 9, 1), dt.date(2026, 11, 6))
    dailies = [p for p in folder.glob("ap_coder-*.db") if DAILY.fullmatch(p.stem)]
    laptop = sorted(p.name[9:17] for p in backup_files.kept_dailies(dailies, backup_files.retention()))
    assert laptop == [f"202610{d}" for d in range(24, 32)] + [f"202611{d:02d}" for d in range(1, 7)]
    monkeypatch.setenv(offline.SERVER_ENV, "1")
    (folder / "ap_coder-20250902-020000-manual.db").write_bytes(b"db")
    backup_files.drop_old(dailies)
    assert _kept_days(folder) == EXPECTED_DAYS
    assert (folder / "ap_coder-20250902-020000-manual.db").exists()
    assert not (folder / "ap_coder-20261023-020000.db").exists()
    assert not (folder / "ap_coder-20261023-020000.files.json").exists()  # a pruned one's list goes with it


def test_the_weeks_and_months_kept_are_those_that_have_a_backup(tmp_path):
    """A server off for months keeps the history it has: the last 8 weeks and 12 months that have a backup, not the
    last 8 and 12 by the calendar. The weeks and months keep complete backups only (a database alone restores no
    file, B-1558); the newest complete one is never dropped, even with nothing else to keep."""
    import datetime as dt

    from ap_coder.core.store import backup_files

    folder = tmp_path / "backups"
    _dailies(folder, dt.date(2024, 1, 1), dt.date(2024, 3, 31))
    _dailies(folder, dt.date(2026, 11, 1), dt.date(2026, 11, 6), complete=False)  # after the gap: no lists
    dailies = [p for p in folder.glob("ap_coder-*.db") if DAILY.fullmatch(p.stem)]
    kept = backup_files.kept_dailies(dailies, Retention(daily=3, weekly=2, monthly=2))
    assert {p.name[9:17] for p in kept} == {"20261106", "20261105", "20261104", "20240331", "20240330", "20240329",
                                            "20240324", "20240229"}  # fmt: skip
    only = backup_files.kept_dailies(dailies, Retention(daily=0, weekly=0, monthly=0))
    assert {p.name[9:17] for p in only} == {"20240331"}


def test_the_second_backup_folder_keeps_the_same_backups(tmp_path, monkeypatch):
    import datetime as dt

    from ap_coder.core import paths
    from ap_coder.core.store import backup_files
    from ap_coder.ops import offline, service

    share = tmp_path / "share"
    _dailies(share, dt.date(2025, 9, 1), dt.date(2026, 11, 5))
    monkeypatch.setenv(offline.SERVER_ENV, "1")
    paths.private_dir().mkdir(parents=True, exist_ok=True)
    service.write_config(paths.private_dir(), allowed_folders=[str(share)])
    store = Store(tmp_path / "data" / "ap.db")
    store.set_setting("backup_copy_dir", str(share))
    made = store.backup_to(store.backup_dir() / "ap_coder-20261106-020000.db")
    backup_files.snapshot(made, store.path.parent)
    assert store.copy_backup(made) is not None
    assert _kept_days(share) == EXPECTED_DAYS


def test_a_backup_made_from_the_servers_console_keeps_the_second_folders_history(tmp_path):
    """``backup now`` (and a restore's copy before it) run from IT's console on the server, where AP_SERVER_MODE is
    not set: the second folder was pruned to the laptop's 14 dailies, its year of history gone (B-1960). The server is
    told as the doctor tells it: its server.json in the data folder."""
    import datetime as dt

    from ap_coder.core import paths
    from ap_coder.core.store import backup_files
    from ap_coder.ops import offline, service

    assert not offline.server_mode()  # (the console's environment)
    data = paths.private_dir()
    share = tmp_path / "share"
    _dailies(share, dt.date(2025, 9, 1), dt.date(2026, 11, 5))
    dailies = [p for p in share.glob("ap_coder-*.db") if DAILY.fullmatch(p.stem)]
    server_keeps = {p.name for p in backup_files.kept_dailies(dailies, backup_files.RETENTION)}
    service.write_config(data, allowed_folders=[str(share)])
    store = Store(data / "ap_coder.db")
    store.set_setting("backup_copy_dir", str(share))
    store.backup_now("manual", actor="console")
    assert store.get_setting("backup_copy_status").startswith("ok")
    assert {p.name for p in share.glob("ap_coder-*.db") if DAILY.fullmatch(p.stem)} == server_keeps
    assert len(server_keeps) > 2 * backup_files.LAPTOP_RETENTION.daily


# --- server-update's safety copy, made ahead while AP Coder serves (D-196) ----------------------------------------


def _same_content(a: Path, b: Path) -> bool:
    dumps = []
    for path in (a, b):
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            dumps.append(list(conn.iterdump()))
        finally:
            conn.close()
    return dumps[0] == dumps[1]


def _busy(data: Path) -> bool:
    """Another thread (as another process would) finds the backups' lock held."""
    import threading

    from ap_coder.core.store import backup_files

    found = []

    def take() -> None:
        try:
            with backup_files.exclusive(data):
                found.append(False)
        except backup_files.Busy:
            found.append(True)

    other = threading.Thread(target=take)
    other.start()
    other.join(10)
    return found == [True]


def test_a_copy_made_ahead_holds_what_is_written_until_it_is_finished(tmp_path):
    """D-196: the safety copy is begun while the old version serves. While it is held the database file is not written
    at all, whatever is written meanwhile and whoever tries to checkpoint it (it all stays in the write-ahead log);
    finished, the copy is the database as it is then, one file; the backups' lock is held from start to close."""
    from ap_coder.core.store.backups import CopyAhead, setting_in

    db, part = tmp_path / "ap_coder.db", tmp_path / "backups" / "ap_coder-x-before-update-0.4.0.db.part"
    store = Store(db)
    store.set_setting("marker", "as it was copied")
    ahead = CopyAhead(db, part)
    assert ahead.start() == ""
    held_still = db.read_bytes()
    worker = sqlite3.connect(db, timeout=0.2)  # another process of AP Coder, writing on
    worker.execute("PRAGMA wal_autocheckpoint=10")
    for n in range(300):  # (about 300 pages: every commit past 10 tries a checkpoint)
        with worker:
            worker.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (f"row {n}", "x" * 4000))
    assert worker.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()[1] > 0  # (it ran: nothing written into the file)
    assert worker.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] == 1  # busy: the file is held
    worker.close()
    store.set_setting("marker", "written while it was copied")
    assert db.read_bytes() == held_still
    assert _busy(tmp_path)  # no backup or restore meanwhile (another process's, a web worker's)
    assert ahead.finish()
    assert sorted(p.name for p in part.parent.iterdir() if p.name.startswith(part.name)) == [part.name]  # one file
    done = part.rename(part.with_suffix(""))  # (as server-update names it, then lets go)
    ahead.close()
    assert setting_in(done, "marker") == "written while it was copied" and _same_content(done, db)
    assert not _busy(tmp_path)  # the lock let go


@pytest.mark.parametrize("why", ["a reader on an older state", "not in WAL mode"])
def test_a_database_that_cant_be_held_still_is_not_copied_ahead(tmp_path, monkeypatch, why):
    """A reader that keeps part of the log from the file (a long report), longer than it waits: the copy is not begun
    (said why), nothing is left, and the lock is let go: server-update then copies it whole once stopped."""
    from ap_coder.core.store import backups

    db, part = tmp_path / "ap_coder.db", tmp_path / "backups" / "x.db.part"
    if why == "not in WAL mode":
        with sqlite3.connect(db) as conn:
            conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    else:
        store = Store(db)
        reader = sqlite3.connect(db, isolation_level=None)
        reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM settings").fetchone()
        store.set_setting("marker", "after the reader began")
    monkeypatch.setattr(backups, "HOLD_WAIT", 0.3)
    ahead = backups.CopyAhead(db, part)
    said = ahead.start()
    assert said and ("WAL" in said if why == "not in WAL mode" else "never still" in said)
    assert ahead.held is None and not [p for p in part.parent.glob("*") if p.name != ".lock"]
    assert not _busy(tmp_path)


def test_a_copy_made_ahead_whose_file_was_written_after_all_is_not_finished(tmp_path):
    """Should the file be written while it is held (it can't: but a copy made of a file that changed would be a
    damaged backup), ``finish`` says so: the copy is removed and the database is copied whole, the lock still held."""
    from ap_coder.core.store.backups import CopyAhead

    db, part = tmp_path / "ap_coder.db", tmp_path / "backups" / "x.db.part"
    Store(db).set_setting("marker", "x")
    ahead = CopyAhead(db, part)
    assert ahead.start() == ""
    os.utime(db, ns=(time.time_ns(), time.time_ns() + 10**9))
    assert not ahead.finish() and not part.exists()
    assert _busy(tmp_path)  # (the whole copy is made under the same lock)
    ahead.close()
    assert not _busy(tmp_path)


def test_a_copy_made_ahead_never_reads_sqlites_lock_byte_page(tmp_path, monkeypatch):
    """In a database over 1 GiB the page from byte 2**30 holds no data, and on Windows bytes of it are locked by
    every process that opens the database (a read of them fails): the copy writes it as zeros, never reads it."""
    from ap_coder.core.store import backups

    page, pages = 4096, 12
    source, dest = tmp_path / "db", tmp_path / "copy"
    source.write_bytes(os.urandom(page * pages))
    monkeypatch.setattr(backups, "LOCK_PAGE", 5 * page)
    monkeypatch.setattr(backups, "CHUNK", 3 * page)
    read = []
    real_open = open

    class Watched:
        def __init__(self, f):
            self.f = f

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.f.close()

        def seek(self, n):
            self.f.seek(n)

        def read(self, n):
            read.append((self.f.tell(), self.f.tell() + n))
            return self.f.read(n)

    monkeypatch.setattr(backups, "open", lambda p, m: Watched(real_open(p, m)) if m == "rb" else real_open(p, m),
                        raising=False)  # fmt: skip
    backups._copy_as_is(source, dest, page * pages, page)
    want = source.read_bytes()
    assert dest.read_bytes() == want[: 5 * page] + bytes(page) + want[6 * page :]
    assert not [r for r in read if r[0] < 6 * page and r[1] > 5 * page]


def test_a_list_made_without_the_databases_sha256_is_checked_by_its_size_until_it_is_read_in(tmp_path):
    """server-update's safety copy lists its files with the service stopped, and reads the database's SHA-256 once it
    serves again (D-196): meanwhile the deep check compares the size (never "damaged" for want of it)."""
    from ap_coder.core.store import backup_files

    store = Store(tmp_path / "ap.db")
    made = store.backup_to(store.backup_dir() / "ap_coder-20261107-120000-before-update-0.4.0.db")
    backup_files.snapshot(made, store.path.parent, hashed=False)
    assert backup_files.read_list(made)["database_sha256"] == ""
    assert backup_files.check(made, deep=True).complete
    backup_files.hash_database(made)
    assert backup_files.read_list(made)["database_sha256"] == backup_files._sha_size(made)[0]
    with open(made, "r+b") as f:  # damaged since, the size the same
        f.seek(200)
        f.write(b"\xff")
    assert backup_files.check(made, deep=True).database_damaged
