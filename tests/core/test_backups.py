"""Backups: consistent copies, once a day automatically, and a restore that can be undone."""

import os
import shutil
import sqlite3
import time
from pathlib import Path

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
            (folder / f"ap_coder-202602{day:02d}-090000.files.json").write_text('{"files": []}')  # complete
    store.auto_backup(keep=14, min_hours=0)
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
    first = store.auto_backup(keep=2, min_hours=0)
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
    store.auto_backup(keep=2, min_hours=0)
    (invoices / "c.pdf").write_bytes(b"%PDF-1.4 c")
    day[0] = 4
    store.auto_backup(keep=2, min_hours=0)
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
    store.auto_backup(keep=2, min_hours=0)
    day[0] = 2
    second = store.auto_backup(keep=2, min_hours=0)
    for folder in (store.backup_dir(), share):
        for n in (3, 4, 5):
            shutil.copy(second, folder / f"ap_coder-2026110{n}-090000.db")  # no list: the database only
    day[0] = 6
    store.auto_backup(keep=2, min_hours=0)
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
