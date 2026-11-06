"""Reading the import queue (``intake/imports.py``, D-168): a restart halfway reads each file once, a full disk reads
nothing and keeps the files in line, each file hashed once on its way to its invoice."""

from __future__ import annotations

import shutil
import sqlite3
import threading
import time
from collections import namedtuple
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ap_coder.core.config import Settings
from ap_coder.core.store import Store, load_sample_setup
from ap_coder.core.store.folder import read_file
from ap_coder.intake import imports
from ap_coder.intake.imports import NO_ROOM, Reader, noted, pick_up
from tests.conftest import SAMPLES

PDFS = sorted(p.name for p in SAMPLES.glob("*.pdf"))[:4]


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    return store


def _settings() -> Settings:
    s = Settings()
    return replace(s, llm=replace(s.llm, provider="off"))


def _queued(store: Store, names: list[str]) -> list[tuple]:
    folder = store.path.parent / "invoices"
    folder.mkdir(exist_ok=True)
    read = []
    for name in names:
        shutil.copy(SAMPLES / name, folder / name)
        read.append(read_file(folder / name))
    store.queue_imports(read, source="upload", person=True)
    return read


def test_a_restart_halfway_reads_each_file_once(store):
    """The reader stops after its first file (a restart of the server, a laptop closed): the files waiting stay in
    line, and AP Coder started again (a new process, a new reader) reads the rest, none twice."""
    _queued(store, PDFS)
    stop = threading.Event()
    first = Reader(store, _settings(), "before", on_result=lambda row, result: stop.set())
    assert first.read_all(stop) == 1
    assert store.imports_open() == {"waiting": 3, "reading": 0}
    again = Store(store.path)  # AP Coder started again
    assert Reader(again, _settings(), "after").read_all() == 3
    with again._conn() as conn:
        assert tuple(conn.execute("SELECT COUNT(*), COUNT(DISTINCT file_sha256) FROM invoices").fetchone()) == (4, 4)
    assert again.import_run()["counts"] == {"done": 4}


def test_a_full_disk_reads_nothing_and_the_file_stays_in_line(store, monkeypatch):
    """With less than the backups' reserve free, nothing is taken (refused before the work): the file stays in the
    invoices folder, in line, and the Process page says why; with room again it is read."""
    from ap_coder.core.store import backups
    from ap_coder.web.pages.process import import_status

    monkeypatch.setattr(backups, "BACKUP_RESERVE", 512 * 1024 * 1024)  # (the tests' own is 0: conftest.py)
    (read,) = _queued(store, PDFS[:1])
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(imports.shutil, "disk_usage", lambda folder: usage(10**12, 10**12 - 10**6, 10**6))
    reader = Reader(store, _settings(), "r")
    assert reader.read_all() == 0 and reader.paused == NO_ROOM
    assert store.imports_open() == {"waiting": 1, "reading": 0} and Path(read[0]).exists()
    assert store.list_invoices() == []
    snap = import_status(store)
    assert snap["state"] == "running" and snap["note"] == NO_ROOM
    monkeypatch.setattr(imports.shutil, "disk_usage", lambda folder: usage(10**12, 0, 10**12))
    assert reader.read_all() == 1 and not reader.paused and len(store.list_invoices()) == 1


def test_each_file_is_hashed_once_on_its_way_to_its_invoice(store, monkeypatch):
    """The SHA-256 read as a file comes in goes with it to its invoice: the queue checks it against the invoices
    stored without reading the file again, and the invoice is saved with it (it was read three times, once more each
    for ``find_by_hash`` and ``add_invoice``)."""
    (read,) = _queued(store, PDFS[:1])
    saved, looked = [], []
    real = Store.add_invoice

    def add_invoice(self, *args, **kwargs):
        saved.append(kwargs.get("sha256"))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Store, "add_invoice", add_invoice)
    monkeypatch.setattr(Store, "find_by_hash", lambda *a, **k: looked.append(a) or None)
    assert Reader(store, _settings(), "r").read_all() == 1
    assert saved == [read[3]] and looked == []
    with store._conn() as conn:
        assert conn.execute("SELECT file_sha256 FROM invoices").fetchone()[0] == read[3]


def test_a_file_written_over_after_it_came_in_is_hashed_again(store):
    """Its size or time changed since it was put in line: its content is not the one hashed then."""
    (read,) = _queued(store, PDFS[:1])
    path = Path(read[0])
    path.write_bytes((SAMPLES / PDFS[1]).read_bytes())
    assert Reader(store, _settings(), "r").read_all() == 1
    with store._conn() as conn:
        assert conn.execute("SELECT file_sha256 FROM invoices").fetchone()[0] == read_file(path)[3] != read[3]


def test_the_pick_up_reads_only_the_files_it_has_not_read(store, monkeypatch):
    """The invoices folder keeps every invoice ever read: a pick-up reads (hashes) only the files whose size and time
    it hasn't noted, or whose content no invoice nor the queue has."""
    _queued(store, PDFS[:2])
    Reader(store, _settings(), "r").read_all()
    folder = store.path.parent / "invoices"
    shutil.copy(SAMPLES / PDFS[2], folder / PDFS[2])
    hashed = []
    real = imports.read_file
    monkeypatch.setattr(imports, "read_file", lambda path: hashed.append(path.name) or real(path))
    found, unreadable, saving = pick_up(store, folder, lambda path: False)
    assert hashed == [PDFS[2]] and [Path(r[0]).name for r in found] == [PDFS[2]] and not unreadable and not saving


def test_the_pick_up_keeps_what_it_listed_for_the_import_page_of_every_process(store):
    """A pick-up (the Process button, a scheduled watch) lists the folder anyway: what it found is kept for every
    process's import page (D-176): the files new then, the saved emails, the files of a kind not read, when it began
    and the folder's time read before it, and how long it took."""
    folder = store.path.parent / "invoices"
    folder.mkdir(parents=True)
    shutil.copy(SAMPLES / PDFS[0], folder / PDFS[0])
    (folder / "mail.eml").write_bytes(b"Subject: hi\n\nhello")
    (folder / "plan.dwg").write_bytes(b"x")
    before = time.time()
    pick_up(store, folder, lambda path: True)  # (still being written: listed, not read)
    began, mtime_ns, found = store.kept_listing(str(folder.resolve()))
    assert before <= began <= time.time() and mtime_ns == folder.stat().st_mtime_ns and 0 <= found.pop("took") < 5
    seen = (folder / PDFS[0]).stat()
    assert found == {"files": [[PDFS[0], seen.st_size, seen.st_mtime_ns]], "emails": [["mail.eml", 18]],
                     "others": ["plan.dwg"]}  # fmt: skip


def test_an_uploaded_file_is_noted_from_its_bytes(tmp_path):
    path = tmp_path / "a.pdf"
    path.write_bytes(b"%PDF-1.4 a")
    assert noted(path, b"%PDF-1.4 a") == read_file(path)


@pytest.mark.parametrize("first", ["the stalled one", "the other"])
def test_a_reader_stalled_past_its_lease_stores_nothing_once_another_took_its_file(store, first):
    """A capture process stalled past its lease (a long OCR holding Python's lock, a paused VM: no heartbeat) has its
    file taken by another, and both read it. Storing checks, in the same transaction, that the claim is still the
    reader's: whichever stores first, the file makes one invoice, the other reader's (B-1762)."""
    _queued(store, PDFS[:1])
    stalled = store.claim_import("stalled", 0.01)
    time.sleep(0.05)  # its lease ran out
    other = store.claim_import("other", 60)
    assert other["id"] == stalled["id"]
    readers = {"the stalled one": (Reader(store, _settings(), "stalled"), stalled),
               "the other": (Reader(store, _settings(), "other"), other)}  # fmt: skip
    for name in sorted(readers, key=lambda name: name != first):
        reader, row = readers[name]
        reader._read(row)  # (read without a heartbeat, as a stalled one is)
    with store._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM events WHERE action = 'processed'").fetchone()[0] == 1
        stored = conn.execute("SELECT id FROM invoices").fetchone()[0]
        assert conn.execute("SELECT invoice_id, status FROM import_files").fetchone()[:] == (stored, "done")
    assert store.import_run()["counts"] == {"done": 1}


class _Quick:
    """A pipeline that reads a file in ``seconds[name]`` (default none) and stores nothing."""

    def __init__(self, seconds: dict[str, float] | None = None) -> None:
        self.seconds = seconds or {}

    def process(self, path, sha256=None):
        time.sleep(self.seconds.get(path.name, 0.0))
        return SimpleNamespace(ok=True, error="", output={"vendor_name": path.stem}, report=None, invoice_id=None)


def test_a_file_whose_end_was_not_noted_is_not_renewed_by_the_next_reads(store, monkeypatch):
    """Noting a file's end fails (the database locked just then): the file stays "reading" as the reader's. The
    heartbeat renewed every file of its reader, so the next reads kept it "reading" for as long as the reader read
    (B-1767); it renews only the files under way now, and the one left over is taken again once its lease ran out."""
    first, second = PDFS[:2]
    _queued(store, [first])
    real, calls = store.finish_import, []

    def locked_once(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise sqlite3.OperationalError("database is locked")
        return real(*args, **kwargs)

    monkeypatch.setattr(store, "finish_import", locked_once)
    reader = Reader(store, _settings(), "r", lease=0.6, pipeline=lambda: _Quick({second: 1.5}))
    with pytest.raises(sqlite3.OperationalError):
        reader.read_all()
    assert store.imports_open() == {"waiting": 0, "reading": 1}
    _queued(store, [second])
    assert reader.read_all() == 2  # the second, then the first again once its lease ran out meanwhile
    with store._conn() as conn:
        files = {r[0]: r[1] for r in conn.execute("SELECT name, status FROM import_files")}
    assert files == {first: "done", second: "done"} and store.imports_open() == {"waiting": 0, "reading": 0}


def test_the_heartbeat_ends_with_the_read_and_a_database_gone_meanwhile_is_only_logged(store, monkeypatch):
    """The heartbeat's last act lets go of the reader's lease: it raised when the database was gone just then (a test's
    folder removed, a disk unplugged: "Exception in thread ap-coder-import-heartbeat"), and it outlived ``read_all``
    (B-1768). The lease runs out anyway: it is logged, and the heartbeat ends before ``read_all`` returns."""
    raised = []
    monkeypatch.setattr(threading, "excepthook", lambda args: raised.append(args.exc_value))
    _queued(store, PDFS[:1])

    def gone(*args, **kwargs):
        time.sleep(0.3)
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(store, "drop_lease", gone)
    assert Reader(store, _settings(), "r", pipeline=lambda: _Quick()).read_all() == 1
    assert not [t for t in threading.enumerate() if t.name == "ap-coder-import-heartbeat"]
    assert raised == []
