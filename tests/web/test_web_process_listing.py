"""The Import invoices page on the company server: the invoices folder listed once for every process, the page of
each web worker showing that listing, the files put in line or read since left out at once (D-176). Two processes,
a slow network folder, files removed, a folder that can't be listed; the laptop's page lists as before
(``tests/web/test_web_process.py``)."""

from __future__ import annotations

import os
import re
import shutil
import threading
import time
from email.message import EmailMessage
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ap_coder.core.store import Store, load_sample_setup
from ap_coder.core.store.folder import read_file
from ap_coder.intake import imports
from ap_coder.web.app import create_app
from ap_coder.web.core import invoice_dir
from ap_coder.web.pages import process
from tests.conftest import SAMPLES
from tests.webclient import PAGE, enrol, sign_in, web_settings

GOOD = "correct horse battery"
PDF = SAMPLES / "northwind_ON_HST_NW-2026-0912.pdf"
OTHER_PDF = SAMPLES / "harbourview_NS_HST_HPS-2026-0347.pdf"
OFFERED = re.compile(r'file-ico[^"]*" aria-hidden="true">[^<]*</span> ([^\n<]+)')


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    uid = store.add_user("ann", GOOD, role="clerk", display_name="Ann")
    with store._conn() as conn:
        conn.execute("UPDATE users SET must_change = 0 WHERE id = ?", (uid,))
    enrol(store, uid)
    return store


@pytest.fixture
def listed(monkeypatch):
    """When the pages' threads listed the folder (``list_folder``), each time."""
    made, real = [], imports.list_folder

    def counted(*args, **kwargs):
        made.append(time.monotonic())
        return real(*args, **kwargs)

    monkeypatch.setattr(process, "list_folder", counted)
    return made


def _worker(store: Store) -> TestClient:
    """A web worker of the server (of two) on ``store``'s database, signed in."""
    app = create_app(web_settings(), Store(store.path), start_reader=False, server=True, processes=2)
    client = TestClient(app, base_url="https://localhost", client=("10.0.0.5", 50000), follow_redirects=False)
    sign_in(client, "ann", GOOD)
    return client


def _offered(client: TestClient) -> list[str]:
    """The files the folder card offers to process, as a view shows them."""
    return [name.strip() for name in OFFERED.findall(client.get("/process").text)]


def _folder(store: Store, *names: str) -> Path:
    folder = invoice_dir(store)
    folder.mkdir(parents=True, exist_ok=True)
    for n, name in enumerate(names):
        shutil.copy(PDF if n % 2 == 0 else OTHER_PDF, folder / name)
    return folder


def _lease(store: Store) -> str:
    return f"folder-listing:{invoice_dir(store).resolve()}"


def _aged(store: Store, seconds: float) -> float:
    """The listing kept made ``seconds`` earlier than it was (time passing); when it now says it began."""
    with store._conn() as conn:
        conn.execute("UPDATE folder_listings SET began = began - ?", (seconds,))
    return store.kept_listing(str(invoice_dir(store).resolve()))[0]


def _listed_again(store: Store, began: float, most: float = 30) -> None:
    """Until a listing other than the one that began at ``began`` is kept, and no thread lists the folder."""
    ends = time.monotonic() + most
    while time.monotonic() < ends:
        kept = store.kept_listing(str(invoice_dir(store).resolve()))
        if kept is not None and kept[0] != began and not process._LISTING:
            return
        time.sleep(0.02)
    raise AssertionError("the folder was not listed again")


def test_on_the_server_the_folder_is_listed_once_for_every_web_worker(store, listed, tmp_path):
    """Each web worker listed the invoices folder at its own views and kept that in its memory: 2.75 s each time at
    300,000 files, again after every invoice or file added anywhere (an upload, a pick-up, a page read). Now one
    listing is kept in the database for every process, which each view shows, and the delay is said."""
    _folder(store, "a.pdf", "b.pdf")
    one, two = _worker(store), _worker(store)
    for client in (one, two, one, two):
        assert _offered(client) == ["a.pdf", "b.pdf"]
    assert len(listed) == 1
    assert "files are listed here within a minute" in two.get("/process").text
    (tmp_path / "elsewhere.pdf").write_bytes(b"%PDF-1.4 made elsewhere")
    Store(store.path).add_invoice(tmp_path / "elsewhere.pdf", {"vendor_name": "A", "invoice_number": "1",
                                                               "grand_total": 1}, {"issues": []})  # fmt: skip
    assert _offered(two) == ["a.pdf", "b.pdf"] and len(listed) == 1  # an invoice added elsewhere: nothing listed


def test_a_file_saved_in_the_folder_is_listed_within_the_delay_said(store, listed):
    """A file saved in the folder shows once the listing kept is SHARED_REFRESH old (the folder changed since): the
    view that finds it due shows the one kept while a thread lists the folder again. One written over another of
    its name (the folder's time unchanged) shows once the listing is SHARED_MOST old: that view waits for the new
    listing."""
    folder = _folder(store, "a.pdf")
    client = _worker(store)
    assert _offered(client) == ["a.pdf"]
    shutil.copy(OTHER_PDF, folder / "new.pdf")
    assert _offered(client) == ["a.pdf"] and len(listed) == 1  # a moment old: kept
    began = _aged(store, process.SHARED_REFRESH)
    assert _offered(client) == ["a.pdf"]  # due: the one kept shown, listed again meanwhile
    _listed_again(store, began)
    assert _offered(client) == ["a.pdf", "new.pdf"] and len(listed) == 2
    Store(store.path).queue_imports([read_file(folder / "a.pdf")], source="watch")
    assert _offered(client) == ["new.pdf"]  # in line now
    (folder / "a.pdf").write_bytes(OTHER_PDF.read_bytes() + b"\n% the next scan")  # written over in place
    _aged(store, process.SHARED_REFRESH)
    assert _offered(client) == ["new.pdf"] and len(listed) == 2  # the folder's time unchanged: kept
    _aged(store, process.SHARED_MOST)
    assert _offered(client) == ["a.pdf", "new.pdf"] and len(listed) == 3  # too old to show: waited for


def test_the_files_put_in_line_since_the_listing_leave_the_page_at_once(store, listed):
    """The listing is of the folder; what AP Coder did with its files since is the database's, asked at each view for
    the files listed only: a file another process put in line (a watch, an upload) is no longer offered, nor are
    the files the Process button just put in line."""
    folder = _folder(store, "a.pdf", "b.pdf", "c.pdf")
    client = _worker(store)
    assert _offered(client) == ["a.pdf", "b.pdf", "c.pdf"]
    Store(store.path).queue_imports([read_file(folder / "a.pdf")], source="watch")
    assert _offered(client) == ["b.pdf", "c.pdf"]
    assert client.post("/process/folder", headers=PAGE).json()["ok"]
    assert "No new files" in client.get("/process").text and len(listed) == 2  # (the pick-up lists it, kept)


def test_while_another_process_lists_the_folder_a_view_waits_a_little_then_says_so(store, listed, monkeypatch):
    """Two web workers finding the listing due at once: one lists, the other doesn't (a lease); in one worker, three
    views at once start one listing. A view with no listing to show waits a few seconds for the one being made, then
    says the folder is being listed (never "No new files"); one with an old listing to show doesn't wait on another
    process's (stopped, its lease not yet run out, or slow): it says when the one it shows was made."""
    monkeypatch.setattr(process, "SHARED_WAIT", 0.3)
    folder = _folder(store, "a.pdf")
    other = Store(store.path)
    assert other.take_lease(_lease(store), "another:1", 60)
    client = _worker(store)
    text = client.get("/process").text
    assert process.BEING_LISTED in text and "No new files" not in text and not listed
    imports.list_folder(other, folder).keep(other)  # the other process's listing, made
    assert _offered(client) == ["a.pdf"] and not listed
    monkeypatch.setattr(process, "SHARED_WAIT", 30.0)
    began, started = _aged(store, process.SHARED_MOST), time.monotonic()
    text = client.get("/process").text
    assert time.monotonic() - started < 5 and not listed
    assert process.LISTED_AT.format(when=time.strftime("%H:%M", time.localtime(began))) in text
    other.drop_lease(_lease(store), "another:1")
    monkeypatch.setattr(process, "SHARED_WAIT", 0.3)
    real, going = process.list_folder, threading.Event()

    def slow(*args, **kwargs):
        going.wait(10)
        return real(*args, **kwargs)

    monkeypatch.setattr(process, "list_folder", slow)
    began = _aged(store, process.SHARED_MOST)
    views = [threading.Thread(target=client.get, args=("/process",)) for _ in range(3)]
    for view in views:
        view.start()
    for view in views:
        view.join(10)
    going.set()
    _listed_again(store, began)
    assert len(listed) == 1


def test_a_slow_network_folder_is_listed_once_and_no_view_waits_for_it_long(store, listed, monkeypatch):
    """A listing of a network folder can take minutes at 300,000 files: its lease is renewed as it goes, so no other
    process lists it meanwhile, and a view waits SHARED_WAIT at most, then shows the listing kept and when it was
    made."""
    monkeypatch.setattr(process, "SHARED_WAIT", 0.3)
    monkeypatch.setattr(process, "LISTING_LEASE", 0.4)
    folder = _folder(store, "a.pdf")
    client = _worker(store)
    assert _offered(client) == ["a.pdf"]
    real, going = process.list_folder, threading.Event()

    def slow(store_, folder_, beat=None):  # its files looked at slowly, the lease renewed as it goes
        while not going.is_set():
            beat()
            time.sleep(0.05)
        return real(store_, folder_, beat)

    monkeypatch.setattr(process, "list_folder", slow)
    shutil.copy(OTHER_PDF, folder / "b.pdf")
    began = _aged(store, process.SHARED_MOST)
    started = time.monotonic()
    text = client.get("/process").text
    assert time.monotonic() - started < 3
    assert process.LISTED_AT.format(when=time.strftime("%H:%M", time.localtime(began))) in text
    assert [name.strip() for name in OFFERED.findall(text)] == ["a.pdf"]
    time.sleep(1.0)  # past the lease's first 0.4 s: renewed, so no other process lists the folder
    assert not Store(store.path).take_lease(_lease(store), "another:1", 60)
    going.set()
    _listed_again(store, began)
    text = client.get("/process").text
    assert [name.strip() for name in OFFERED.findall(text)] == ["a.pdf", "b.pdf"] and "As the invoices" not in text
    assert len(listed) == 2
    with store._conn() as conn:  # a listing that took 110 s, ended 10 s ago: not listed again yet, its time said
        conn.execute("UPDATE folder_listings SET began = began - 120, found = json_set(found, '$.took', 110)")
    assert "As the invoices folder was at" in client.get("/process").text and len(listed) == 2


def test_files_removed_from_the_folder_leave_the_page(store, listed):
    """A file removed shows no more once the folder is listed again; a saved email the Process button unpacked (moved
    to emails/) shows no more at once."""
    folder = _folder(store, "a.pdf", "b.pdf")
    mail = EmailMessage()
    mail["Subject"], mail["From"] = "Invoice", "ar@vendor.example"
    mail.set_content("Attached.")
    mail.add_attachment(OTHER_PDF.read_bytes(), maintype="application", subtype="pdf", filename="attached.pdf")
    (folder / "mail.eml").write_bytes(mail.as_bytes())
    os.utime(folder / "mail.eml", (time.time() - 600, time.time() - 600))  # saved a while ago: unpacked
    client = _worker(store)
    assert _offered(client) == ["a.pdf", "b.pdf", "mail.eml"]
    (folder / "b.pdf").unlink()
    _aged(store, process.SHARED_MOST)
    assert _offered(client) == ["a.pdf", "mail.eml"]
    assert client.post("/process/folder", headers=PAGE).json()["ok"]
    assert not (folder / "mail.eml").exists() and "No new files" in client.get("/process").text
    assert len(listed) == 3  # (the third the Process button's, kept)


def test_a_folder_that_cannot_be_listed_says_why_on_every_web_worker(store, listed, monkeypatch):
    """The listing fails (a network share gone, its rights changed): kept as such, so each worker's page says why,
    and tried again once due."""
    _folder(store, "a.pdf")
    real = process.list_folder

    def refused(*args, **kwargs):
        listed.append(1)
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(process, "list_folder", refused)
    one, two = _worker(store), _worker(store)
    assert "could not be listed just now (Access is denied)" in one.get("/process").text
    text = two.get("/process").text
    assert "could not be listed just now (Access is denied)" in text and "No new files" not in text
    assert len(listed) == 1
    monkeypatch.setattr(process, "list_folder", real)
    began = _aged(store, process.SHARED_REFRESH)
    assert "could not be listed" in one.get("/process").text  # due: listed again by a thread meanwhile
    _listed_again(store, began)
    assert _offered(two) == ["a.pdf"]
