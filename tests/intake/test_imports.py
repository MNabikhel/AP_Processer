"""Reading the import queue (``intake/imports.py``, D-168): a restart halfway reads each file once, a full disk reads
nothing and keeps the files in line, each file hashed once on its way to its invoice."""

from __future__ import annotations

import shutil
import threading
from collections import namedtuple
from dataclasses import replace
from pathlib import Path

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


def test_an_uploaded_file_is_noted_from_its_bytes(tmp_path):
    path = tmp_path / "a.pdf"
    path.write_bytes(b"%PDF-1.4 a")
    assert noted(path, b"%PDF-1.4 a") == read_file(path)
