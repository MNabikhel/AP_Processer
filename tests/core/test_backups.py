"""Backups: consistent copies, once a day automatically, and a restore that can be undone."""

import os
import shutil
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
