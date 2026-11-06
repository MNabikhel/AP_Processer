"""The import queue in the database (``core/store/imports.py``, schema 26, D-168): taken by one reader at a time, a
stopped reader's file read again once then failed, leases on the database's clock, fair between people, never a file
twice, an older database upgraded after its backup."""

from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from ap_coder.core.store import SCHEMA_VERSION, Store
from ap_coder.core.store.folder import read_file
from ap_coder.core.store.imports import IMPORT_KEEP_DAYS, IMPORT_TRIES, STOPPED_TWICE

ROOT = Path(__file__).resolve().parents[2]


def _files(folder: Path, n: int, tag: str = "") -> list[tuple]:
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for k in range(n):
        path = folder / f"{tag}f{k}.pdf"
        path.write_bytes(f"%PDF-1.4 {tag}{k}".encode())
        out.append(read_file(path))
    return out


def _python(code: str, *args: str) -> subprocess.Popen:
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    return subprocess.Popen([sys.executable, "-c", code, *args], env=env, stdout=subprocess.PIPE, text=True)


CLAIM_ALL = """
import json, sys
from ap_coder.core.store import Store
store, taken = Store(sys.argv[1]), []
while True:
    row = store.claim_import(sys.argv[2], 60)
    if row is None:
        break
    taken.append(row["id"])
    store.finish_import(row["id"], sys.argv[2], "done")
print(json.dumps(taken))
"""


def test_two_processes_never_take_the_same_queued_file(tmp_path):
    """Four processes empty the queue at once: every file is taken, each by one of them (BEGIN IMMEDIATE from the
    choice to the mark)."""
    store = Store(tmp_path / "ap.db")
    store.queue_imports(_files(tmp_path / "in", 60), source="upload", person=True)
    readers = [_python(CLAIM_ALL, str(store.path), f"reader-{n}") for n in range(4)]
    taken = [json.loads(p.communicate(timeout=120)[0]) for p in readers]
    ids = [i for t in taken for i in t]
    assert sorted(ids) == sorted(set(ids)) and len(ids) == 60
    assert store.imports_open() == {"waiting": 0, "reading": 0}


HOLD = """
import sys, time
from ap_coder.core.store import Store
row = Store(sys.argv[1]).claim_import(sys.argv[2], float(sys.argv[3]))
print(row["id"], flush=True)
time.sleep(600)  # reading a file that never ends: killed here
"""


def test_a_reader_killed_mid_file_has_it_read_again_once_then_failed_with_a_reason(tmp_path):
    """A capture process killed while it reads a file (no heartbeat any more): once its lease runs out, the file is
    taken again; killed again, the file is failed with the reason, as an invoice Retry reads again, and its batch
    is recorded in the audit trail. Nothing else waits on it meanwhile."""
    store = Store(tmp_path / "ap.db")
    files = _files(tmp_path / "in", 2)
    store.queue_imports(files, source="folder", person=True)
    for attempt in range(1, IMPORT_TRIES + 1):
        reader = _python(HOLD, str(store.path), f"capture-{attempt}", "1")
        file_id = int(reader.stdout.readline())
        reader.kill()
        reader.wait(30)
        row = store.claim_import("other", 60)  # the other file, while the killed one's lease runs
        assert row is None or row["id"] != file_id
        if row is not None:
            store.finish_import(row["id"], "other", "done")
        time.sleep(1.2)  # its lease ran out
        if attempt < IMPORT_TRIES:
            again = store.claim_import("next", 60)
            assert again["id"] == file_id and again["tries"] == attempt + 1
            store.release_imports("next")  # (a stop: its try not counted) - the next round's process takes it
            with store._conn() as conn:
                assert conn.execute("SELECT tries FROM import_files WHERE id = ?", (file_id,)).fetchone()[0] == attempt
    assert store.claim_import("last", 60) is None  # failed: not taken a third time
    run = store.import_run()
    assert run["counts"] == {"done": 1, "failed": 1}
    assert run["errors"] == [f"f0.pdf: {STOPPED_TWICE.format(name='f0.pdf')}"]  # the first in line: the one taken
    failed = store.find_by_hash(Path(files[0][0]), include_failed=True)
    assert failed["status"] == "failed" and "Retry reads it again" in store.get_invoice(failed["id"])["error"]
    recorded = [e for e in store.events(actions=["import_run"])]
    assert len(recorded) == 1 and recorded[0]["detail"]["failed"] == 1 and recorded[0]["detail"]["ok"] == 1


def test_leases_are_compared_on_the_databases_clock(tmp_path, monkeypatch):
    """Two processes, two clocks: a lease's end is a time of the database's own clock, compared there. A process
    whose clock is a day ahead (or behind) takes nothing still held, and a lease that ran out is taken whatever its
    clock says."""
    from ap_coder.core.store import imports

    store = Store(tmp_path / "ap.db")
    store.queue_imports(_files(tmp_path / "in", 1), source="upload")
    held = store.claim_import("a", 60)
    for days in (1, -1):
        later = dt.datetime.now() + dt.timedelta(days=days)
        monkeypatch.setattr(imports.time, "time", lambda later=later: later.timestamp())
        monkeypatch.setattr(imports, "_now", lambda later=later: later.isoformat(timespec="seconds"))
        assert store.claim_import("b", 60) is None  # still a's
    with store._conn() as conn:  # a minute on, on the database's clock
        conn.execute("UPDATE import_files SET lease_until = julianday('now') - 1 / 1440.0 WHERE id = ?", (held["id"],))
    assert store.claim_import("b", 60)["id"] == held["id"]


def test_a_person_waiting_goes_first_and_people_take_turns(tmp_path):
    """A scheduled watch's files wait for a person's; between people, the one served least recently goes next: a
    colleague's big batch doesn't hold up three files."""
    store = Store(tmp_path / "ap.db")
    store.queue_imports(_files(tmp_path / "w", 2, "watch"), source="watch")  # nobody waits for these
    store.queue_imports(_files(tmp_path / "a", 5, "ann"), source="upload", uploader="ann", person=True)
    store.queue_imports(_files(tmp_path / "b", 2, "bob"), source="upload", uploader="bob", person=True)
    order = []
    while (row := store.claim_import("r", 60)) is not None:
        order.append(row["name"][:3])
        store.finish_import(row["id"], "r", "done")
    assert order == ["ann", "bob", "ann", "bob", "ann", "ann", "ann", "wat", "wat"]


def test_a_file_is_put_in_line_once_and_never_read_twice(tmp_path):
    """The same content twice in one batch is one file; in two batches (two uploads at once) the second waits while
    the first is read, then is skipped as already in AP Coder; one stored meanwhile by another pick-up is skipped as
    it is taken; ``force`` (``process --force``) reads it all the same."""
    store = Store(tmp_path / "ap.db")
    (one,) = _files(tmp_path / "in", 1)
    copy = tmp_path / "in" / "copy.pdf"
    copy.write_bytes(Path(one[0]).read_bytes())
    first = store.queue_imports([one, read_file(copy)], source="upload", uploader="ann", person=True)
    assert first["queued"] == 1 and first["skipped"] == ["copy.pdf"]
    second = store.queue_imports([one], source="upload", uploader="bob", person=True)
    assert second["queued"] == 1 and second["ahead"] == 1
    taken = store.claim_import("a", 60)
    assert store.claim_import("b", 60) is None  # the same content is being read: it waits
    invoice = store.add_invoice(one[0], {"vendor_name": "A", "invoice_number": "1", "grand_total": 1}, {"issues": []},
                                sha256=one[3])  # fmt: skip
    store.finish_import(taken["id"], "a", "done", invoice_id=invoice)
    assert store.claim_import("b", 60) is None  # stored: skipped, not read
    run = store.import_run()
    assert run["counts"] == {"done": 1, "skipped": 1} and run["skipped"] == ["f0.pdf"]
    forced = store.queue_imports([one], source="process", force=True)
    assert forced["queued"] == 1 and store.claim_import("c", 60)["batch"] == forced["batch"]


def test_an_older_database_gets_the_import_queue_after_its_backup(tmp_path):
    """Schema 26 on a database of schema 25: backed up first (``before-update-from-schema-25``), then the queue's
    tables and indexes made; the update can be undone with the backup."""
    db = tmp_path / "private" / "ap_coder.db"
    Store(db)
    with sqlite3.connect(db) as conn:  # as AP Coder of schema 25 left it
        for table in ("import_files", "import_batches", "import_turns"):
            conn.execute(f"DROP TABLE {table}")
        conn.execute("UPDATE settings SET value = '25' WHERE key = 'schema_version'")
    store = Store(db)
    backups = list((db.parent / "backups").glob("*before-update-from-schema-25*.db"))
    assert backups and SCHEMA_VERSION == 26 and store.get_setting("schema_version") == "26"
    with sqlite3.connect(backups[0]) as conn:
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'import_files'").fetchone()
    with store._conn() as conn:
        indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"import_files_status", "import_files_batch", "import_batches_run"} <= indexes
    store.queue_imports(_files(tmp_path / "in", 1), source="upload")
    assert store.imports_open()["waiting"] == 1


def test_the_import_page_counts_files_in_line_as_seen(tmp_path):
    """A file in line isn't listed as new on the import page (the button would put it in line again)."""
    store = Store(tmp_path / "ap.db")
    (one,) = _files(tmp_path / "in", 1)
    listed = [(one[0], one[1], one[2])]
    assert store.unseen_files(listed) == {one[0]}
    stamp = store.files_stamp()
    store.queue_imports([one], source="folder")
    assert store.unseen_files(listed) == set() and store.files_stamp() != stamp


def test_finished_files_go_after_a_week_but_the_latest_runs(tmp_path):
    """The queue keeps what it read for IMPORT_KEEP_DAYS (a year of imports is 300,000 rows): the latest run stays,
    whatever its age, for the Process page."""
    store = Store(tmp_path / "ap.db")
    store.queue_imports(_files(tmp_path / "a", 2, "a"), source="upload")
    while (row := store.claim_import("r", 60)) is not None:
        store.finish_import(row["id"], "r", "done")
    old = (dt.datetime.now() - dt.timedelta(days=IMPORT_KEEP_DAYS + 1)).isoformat(timespec="seconds")
    with store._conn() as conn:
        conn.execute("UPDATE import_batches SET finished_at = ?", (old,))
    store.queue_imports(_files(tmp_path / "b", 1, "b"), source="upload")  # a new run: the old one goes
    with store._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM import_files").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM import_batches").fetchone()[0] == 1


def test_an_idle_reader_takes_no_write_lock(tmp_path):
    """Each capture process looks at the queue every second: with nothing to take, the look waits on no writer and
    takes no write lock (another connection holding one, it answers at once)."""
    store = Store(tmp_path / "ap.db")
    store.queue_imports(_files(tmp_path / "in", 1), source="upload")
    row = store.claim_import("busy", 60)  # being read, its lease running: nothing for another reader
    other = sqlite3.connect(store.path, timeout=0)
    other.execute("BEGIN IMMEDIATE")
    try:
        started = time.monotonic()
        assert store.claim_import("idle", 60) is None and time.monotonic() - started < 1
    finally:
        other.rollback()
        other.close()
    assert store.finish_import(row["id"], "busy", "done")
