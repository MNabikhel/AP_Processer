"""The long downloads on the company server, made by its background process (D-169, ``web/pages/downloads.py``): the
page asks, follows and downloads; the file is the laptop's; another person's is refused; a full disk and a restart
while it is made; two web workers asking at once; the laptop as before. The jobs as the database keeps them:
``tests/core/test_reports.py``."""

from __future__ import annotations

import csv
import errno
import io
import sqlite3
import threading
import time

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from ap_coder.core.store import Store, load_sample_setup, reports
from ap_coder.web.app import create_app
from ap_coder.web.pages import downloads
from tests.browser import browser_on
from tests.webclient import PAGE, add_clean, client_for, enrol, sign_in, web_settings

GOOD = "correct horse battery"
ADDRESSES = ["/search/export.csv", "/search/export.xlsx", "/activity/events.csv", "/exports/duplicates.csv",
             "/month-end/accruals.csv?end=2026-10-31"]  # fmt: skip


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    add_clean(store)
    for username, role in (("ann", "clerk"), ("bob", "clerk"), ("reader", "readonly"), ("boss", "admin")):
        uid = store.add_user(username, GOOD, role=role, display_name=username.title())
        with store._conn() as conn:
            conn.execute("UPDATE users SET must_change = 0 WHERE id = ?", (uid,))
        enrol(store, uid)
    return store


def _app(store: Store, processes: int = 1, runner: bool = True):
    """A web worker of the server; ``runner``: with the background process making the downloads (here a Runner)."""
    app = create_app(web_settings(), store, start_reader=False, server=True, processes=processes)
    if runner:
        app.state.downloads = downloads.Runner(lambda: store).start()
    return app


def _client(app, username: str) -> TestClient:
    client = TestClient(app, base_url="https://localhost", client=("10.0.0.5", 50000), follow_redirects=False)
    sign_in(client, username, GOOD)
    return client


def _asked(client: TestClient, address: str) -> dict:
    answer = client.get(address, headers=PAGE)
    assert answer.status_code == 200, answer.text
    return answer.json()


def _followed(client: TestClient, status: str, seconds: float = 30) -> dict:
    ends = time.monotonic() + seconds
    while time.monotonic() < ends:
        snap = client.get(status).json()
        if snap["state"] != "running":
            return snap
        time.sleep(0.05)
    raise AssertionError(f"{status} still running")


def _cells(xlsx: bytes) -> list[list]:
    return [list(row) for row in load_workbook(io.BytesIO(xlsx)).active.iter_rows(values_only=True)]


def test_on_the_server_the_page_asks_the_background_process_makes_it_and_the_file_is_the_laptops(store, monkeypatch):
    """Each long download (``KINDS``): asked with the page's header, the web worker answers with the job at once; the
    background process makes it; its status says so; the file is the one the laptop makes as it is asked for."""
    app = _app(store)
    ann = _client(app, "boss")  # (an Admin sees every event, as the laptop)
    laptop = client_for(store)
    for address in ADDRESSES:
        job = _asked(ann, address)
        assert job["ok"] and job["status"] == f"/downloads/{job['job']}"
        assert job["file"].startswith(address) and job["file"].endswith(f"job={job['job']}")
        snap = _followed(ann, job["status"])
        assert snap["state"] == "done", (address, snap)
        made, here = ann.get(job["file"]), laptop.get(address)
        assert made.status_code == here.status_code == 200, address
        named = [r.headers["content-disposition"].split(";")[1] for r in (made, here)]  # (filename="…")
        assert named[0] == named[1] and made.headers["content-disposition"].startswith("attachment;"), address
        assert made.headers["cache-control"] == "no-store"
        if address.endswith(".xlsx"):
            assert _cells(made.content) == _cells(here.content) and len(_cells(made.content)) == 2
        elif address.startswith("/activity"):  # (newest first: the laptop's has the events since on top)
            header, _, rows = made.content.partition(b"\r\n")
            assert here.content.startswith(header) and here.content.endswith(rows) and rows.count(b"\r\n") > 3
        else:
            assert made.content == here.content, address
    assert len(list(downloads.folder(store).iterdir())) == len(ADDRESSES)
    assert all(row["state"] == reports.MADE for row in _jobs(store))
    # Activity's "Downloaded" names each by its own address, as before (D-109): the file comes from it, ?job=<id>.
    seen = [e["detail"]["path"] for e in store.find_events(actions=["downloaded"]) if e["actor"] == "Boss"]
    assert sorted(seen) == sorted(a.split("?")[0] for a in ADDRESSES)
    app.state.downloads.stop()


def _jobs(store: Store) -> list[dict]:
    with store._conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM report_jobs ORDER BY id")]


def test_asked_without_the_pages_header_the_file_is_waited_for_and_sent(store):
    """A bookmark, a script, a server of one process without its background work (a first test, the volume bench):
    the web worker waits for the file without holding a thread, then sends it as before."""
    for runner in (True, False):
        app = _app(store, runner=runner)
        answer = _client(app, "ann").get("/search/export.csv")
        assert answer.status_code == 200 and answer.headers["content-type"].startswith("text/csv")
        assert next(csv.reader(io.StringIO(answer.content.decode("utf-8-sig"))))[0] == "Vendor"
        if runner:
            app.state.downloads.stop()
    assert [row["state"] for row in _jobs(store)] == [reports.MADE, reports.MADE]


def test_another_persons_download_is_no_such_download_and_a_reader_makes_their_own(store):
    app = _app(store)
    ann, bob, reader = _client(app, "ann"), _client(app, "bob"), _client(app, "reader")
    job = _asked(ann, "/exports/duplicates.csv")
    assert _followed(ann, job["status"])["state"] == "done"
    for address in (job["status"], job["file"]):
        assert bob.get(address).status_code == 404 and reader.get(address).status_code == 404
    assert ann.get(job["file"]).status_code == 200
    assert ann.get(f"/search/export.csv?job={job['job']}").status_code == 404  # (her job, another download's address)
    assert ann.get("/exports/duplicates.csv?job=1e9").status_code == 404
    own = _asked(reader, "/exports/duplicates.csv")  # (Read-only downloads the reports: its own job)
    assert own["job"] != job["job"] and _followed(reader, own["status"])["state"] == "done"
    app.state.downloads.stop()


def test_two_web_workers_asked_for_the_same_persons_file_at_once_make_it_once(store):
    """Two web workers (two apps on the database), the same person asking for the same file in both before the
    background process takes it: one job, both pages follow it; at most ``AT_ONCE`` a person waiting."""
    first, second = _app(store, processes=2, runner=False), _app(store, processes=2, runner=False)
    one, two = _client(first, "ann"), _client(second, "ann")
    asked = [_asked(one, "/search/export.xlsx"), _asked(two, "/search/export.xlsx")]
    assert asked[0]["job"] == asked[1]["job"] and len(_jobs(store)) == 1
    for n in (1, 2):  # two more of her own (other filters): then she has three waiting
        assert _asked(one, f"/search/export.csv?q=n{n}")["ok"]
    refused = two.get("/search/export.csv?q=n3", headers=PAGE)
    assert refused.status_code == 429 and refused.json()["error"] == downloads.TOO_MANY
    runner = downloads.Runner(lambda: store).start()  # the background process
    assert _followed(two, asked[1]["status"])["state"] == "done" == _followed(one, asked[0]["status"])["state"]
    runner.stop()


def test_a_full_disk_ends_the_job_with_why_and_leaves_no_file(store, monkeypatch):
    ann = _client(_app(store, processes=2, runner=False), "ann")
    monkeypatch.setattr(downloads, "_room", lambda place: False)  # under the room kept for the database
    job = _asked(ann, "/search/export.csv")
    _make_waiting(store)
    snap = _followed(ann, job["status"])
    assert snap["state"] == "error" and snap["error"] == downloads.FULL
    monkeypatch.setattr(downloads, "_room", lambda place: True)

    def full(store, params, out, step):
        out.write(b"half a file")
        raise OSError(errno.ENOSPC, "No space left on device")

    from ap_coder.web.pages import search

    monkeypatch.setattr(search, "write_csv", full)
    again = _asked(ann, "/search/export.csv?q=x")
    _make_waiting(store)
    assert _followed(ann, again["status"])["error"] == downloads.FULL
    assert list(downloads.folder(store).iterdir()) == []  # (nothing half written left)
    alone = _client(_app(store, runner=False), "ann")  # (a server of one process: it makes the job itself)
    answer = alone.get("/search/export.csv?q=x")  # (without the header: waited for, and why it failed)
    assert answer.status_code == 500 and answer.json()["error"] == downloads.FULL


def _make_waiting(store: Store) -> None:
    """The background process, once: every job waiting made."""
    while (job := store.claim_download("background", downloads.STALE, downloads.FORMAT)) is not None:
        downloads.make(store, job, "background")


def test_a_restart_while_a_file_is_made_puts_it_back_and_the_next_start_makes_it(store, monkeypatch):
    """The background process stopping (an update, the service restarted) while a file is made: the job is put back
    in line, nothing half written left, and the next background process makes it whole."""
    ann = _client(_app(store, processes=2, runner=False), "ann")
    from ap_coder.web.pages import search

    real, started = search.write_csv, threading.Event()

    def slow(store, params, out, step):
        started.set()
        for n in range(200):
            step(n, 200, "")
            time.sleep(0.05)
        real(store, params, out, step)

    monkeypatch.setattr(search, "write_csv", slow)
    job = _asked(ann, "/search/export.csv")
    first = downloads.Runner(lambda: store).start()
    assert started.wait(10)
    first.stop()
    first.thread.join(10)
    row = store.download_job(job["job"])
    assert row["state"] == reports.WAITING and row["tries"] == 0
    assert [p for p in downloads.folder(store).iterdir()] == []
    monkeypatch.setattr(search, "write_csv", real)
    second = downloads.Runner(lambda: store).start()
    assert _followed(ann, job["status"])["state"] == "done" and ann.get(job["file"]).status_code == 200
    second.stop()


def test_the_sweep_keeps_the_half_written_file_of_a_job_still_being_made(store, monkeypatch):
    """A job that writes nothing for minutes (the duplicate audit's pairs worked out first, an Activity search that
    finds little): the sweep leaves its half-written file, so it is made (B-1773: removed after 60 s, "it could not be
    written"); one left by a maker that stopped is still removed."""
    from ap_coder.web.pages import exports

    opened, go = threading.Event(), threading.Event()

    def slow(store, params, out, step):
        step(0, 0, "Looking for bills paid twice…")
        opened.set()
        go.wait(10)  # (working it out: nothing written yet)
        out.write(b"reason,first,second\r\n")

    monkeypatch.setattr(exports, "write_duplicates", slow)
    monkeypatch.setattr(downloads, "RENEW", 0.01)
    job, _ = store.start_download("user:1", "/exports/duplicates.csv", {}, "d.csv", 3, downloads.FORMAT)
    claimed = store.claim_download("background", downloads.STALE, downloads.FORMAT)
    maker = threading.Thread(target=downloads.make, args=(store, claimed, "background"))
    maker.start()
    assert opened.wait(10)
    part = next(downloads.folder(store).glob("*.part"))
    stopped = downloads.folder(store) / "7.0123456789abcdef.part"  # (a maker that stopped while writing)
    stopped.write_bytes(b"half")
    later = time.time() + 120  # two minutes on, nothing written meanwhile
    monkeypatch.setattr(time, "time", lambda: later)
    ends = time.monotonic() + 5
    while part.stat().st_mtime < later - 1 and time.monotonic() < ends:  # (its maker's next note of how far it got)
        time.sleep(0.01)
    downloads.sweep(store)
    assert part.exists() and not stopped.exists()
    go.set()
    maker.join(10)
    row = store.download_job(job)
    assert row["state"] == reports.MADE, row["error"]
    assert (downloads.folder(store) / row["file"]).read_bytes() == b"reason,first,second\r\n"


def test_a_file_made_whose_end_could_not_be_recorded_is_not_left_on_the_disk(store, monkeypatch):
    """The database full (or busy past its wait) as the job is recorded made: the job ends with why, and the whole
    file is removed, not left in the folder named by no job, one more each time it is asked again (B-1774)."""

    def full(*args, **kwargs):
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(Store, "finish_download", full)
    job, _ = store.start_download("user:1", "/exports/duplicates.csv", {}, "d.csv", 3, downloads.FORMAT)
    _make_waiting(store)
    assert store.download_job(job)["error"] == downloads.FULL
    assert list(downloads.folder(store).iterdir()) == []


def test_a_file_no_job_names_is_removed_by_a_later_sweep(store, monkeypatch):
    """A file made whose job was never recorded made (its process killed in between), or whose job was forgotten
    while Windows refused to remove it (being sent): a sweep removes it once it is an hour old (B-1775: kept for
    good), and never a file made a moment ago (its job may be recorded made next) nor one a job names."""
    import os
    import pathlib

    job, _ = store.start_download("user:1", "/exports/duplicates.csv", {}, "d.csv", 3, downloads.FORMAT)
    _make_waiting(store)
    place = downloads.folder(store)
    kept = place / store.download_job(job)["file"]
    killed, renamed = place / "41.0123456789abcdef.csv", place / "42.0123456789abcdef.csv"
    for path in (killed, renamed):
        path.write_bytes(b"x")
    hours_ago = time.time() - 7200
    os.utime(killed, (hours_ago, hours_ago))  # (its process killed two hours ago, before the job was recorded made)
    downloads.sweep(store)
    assert not killed.exists() and renamed.exists() and kept.exists()
    real_unlink, sending = pathlib.Path.unlink, [True]

    def unlink(self, missing_ok=False):
        if self == kept and sending[0]:  # (being sent to the person: Windows refuses to remove it)
            raise PermissionError(32, "The process cannot access the file because it is being used by another process")
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(pathlib.Path, "unlink", unlink)
    later = time.time() + 7200
    monkeypatch.setattr(time, "time", lambda: later)  # two hours on: the job is forgotten
    downloads.sweep(store)
    assert store.download_job(job) is None and kept.exists() and not renamed.exists()
    sending[0] = False
    downloads.sweep(store)  # the next one
    assert list(place.iterdir()) == []


def test_a_note_of_how_far_it_got_just_after_the_job_is_made_keeps_its_file(store, monkeypatch):
    """The maker's note of how far it got, taken just after the job was recorded made (its thread ran first): the job
    says done and its file is downloaded (B-1776: the file was removed, "That file is no longer kept")."""
    monkeypatch.setattr(downloads, "RENEW", 0.01)
    real_renew, real_finish, noted = Store.renew_download, Store.finish_download, threading.Event()

    def renew(self, *args):
        mine = real_renew(self, *args)
        if not mine:  # (the job no longer being made)
            noted.set()
        return mine

    def finish_then_paused(self, *args):
        made = real_finish(self, *args)
        noted.wait(5)  # (the other threads run meanwhile: the note is taken)
        return made

    monkeypatch.setattr(Store, "renew_download", renew)
    monkeypatch.setattr(Store, "finish_download", finish_then_paused)
    ann = _client(_app(store, processes=2, runner=False), "ann")
    job = _asked(ann, "/exports/duplicates.csv")
    _make_waiting(store)
    assert noted.is_set() and _followed(ann, job["status"])["state"] == "done"
    assert ann.get(job["file"]).status_code == 200


def test_a_one_process_server_makes_the_files_the_process_before_it_left(store, monkeypatch):
    """A server of one process without its background work (a first test, ``bench volume``) restarted while a file
    waited or was being made: its file asked for, or the page following it, has it made (B-1777: "Making the file…"
    for good, nothing making it, until another file was asked for)."""
    monkeypatch.setattr(downloads, "WAIT_MOST", 10.0)  # (half an hour)
    before = _client(_app(store, processes=2, runner=False), "ann")  # (the jobs it asked for: none made here)
    alone = _client(_app(store, runner=False), "ann")  # the server after its restart
    waiting = _asked(before, "/search/export.csv")
    answer = alone.get(waiting["file"])  # (a bookmark: waited for, without the page's header)
    assert answer.status_code == 200, answer.text
    assert next(csv.reader(io.StringIO(answer.content.decode("utf-8-sig"))))[0] == "Vendor"
    ends = time.monotonic() + 10
    while str(store.path) in downloads._HERE and time.monotonic() < ends:  # (its maker ends once none waits)
        time.sleep(0.01)
    stopped = _asked(before, "/exports/duplicates.csv")
    assert store.claim_download("gone:here", downloads.STALE, downloads.FORMAT)["id"] == stopped["job"]
    with store._conn() as conn:  # (being made when the server stopped, two minutes ago)
        conn.execute("UPDATE report_jobs SET renewed_at = renewed_at - 120 WHERE id = ?", (stopped["job"],))
    assert _followed(alone, stopped["status"], seconds=10)["state"] == "done"
    assert alone.get(stopped["file"]).status_code == 200


def test_the_servers_background_process_makes_the_downloads_and_a_laptops_starts_no_runner(store, monkeypatch):
    """``app.start_background_work`` (``serve --background-work``, or the one process of ``--workers 1``) starts the
    downloads' Runner on the server, which makes the jobs waiting and stops when asked; on a laptop none."""
    from types import SimpleNamespace

    from ap_coder.ai import azure_check, page_worker
    from ap_coder.coding import bulk
    from ap_coder.erp import jde_sync
    from ap_coder.web import app as web_app

    stub = SimpleNamespace(stop=lambda: None)
    stub.start = lambda: stub
    for module, name in ((page_worker, "start_background"), (azure_check, "start_background"),
                         (jde_sync, "Background"), (bulk, "QueueRecheck")):  # fmt: skip
        monkeypatch.setattr(module, name, lambda *args, **kwargs: stub)
    monkeypatch.setenv("AP_SERVER_MODE", "1")
    *_, runner = web_app.start_background_work(web_settings, store)
    assert isinstance(runner, downloads.Runner) and runner.thread.is_alive()
    job, _ = store.start_download("user:1", "/exports/duplicates.csv", {}, "d.csv", 3, downloads.FORMAT)
    ends = time.monotonic() + 20
    while store.download_job(job)["state"] != reports.MADE and time.monotonic() < ends:
        time.sleep(0.05)
    assert store.download_job(job)["state"] == reports.MADE
    runner.stop()
    runner.thread.join(10)
    assert not runner.thread.is_alive()
    monkeypatch.delenv("AP_SERVER_MODE")
    assert len(web_app.start_background_work(web_settings, store)) == 4  # (the laptop's)


def test_on_a_laptop_each_download_is_made_as_it_is_asked_for(store, monkeypatch):
    """Laptop mode is unchanged: no job, the file in the answer (the Activity CSV sent as it is read, B-1597)."""
    monkeypatch.delenv("AP_SERVER_MODE", raising=False)
    laptop = client_for(store)
    for address in ADDRESSES:
        answer = laptop.get(address, headers=PAGE)  # (even with the page's header)
        assert answer.status_code == 200 and answer.headers["content-disposition"].startswith("attachment"), address
    assert _jobs(store) == []


def test_in_a_browser_on_the_server_a_long_download_says_how_far_it_got_then_downloads(chromium, store):
    """The search page's Excel link on the server: the link says the file is being made, then the browser downloads
    it; the laptop's link downloads at once (``app.js``, ``a[data-download-job]``)."""
    from tests.webclient import authenticator_code

    app = _app(store)
    with browser_on(chromium, app) as (browser, base):
        page = browser.new_page(accept_downloads=True)
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base + "/search")
        page.fill("input[name=username]", "ann")
        page.fill("input[name=password]", GOOD)
        page.click("button:has-text('Log in')")
        page.wait_for_url("**/login/code*")
        page.fill("input[name=code]", authenticator_code(store, "ann"))
        page.click("button:has-text('Log in')")
        page.wait_for_url("**/search*")
        page.click("summary:has-text('Export')")
        link = page.locator("a[data-download-job][href^='/search/export.xlsx']")
        with page.expect_download() as caught:
            link.click()
        assert caught.value.suggested_filename == "invoice_directory.xlsx"
        assert _cells(open(caught.value.path(), "rb").read())[0][0] == "Vendor"
        assert link.get_attribute("aria-busy") is None and "Excel" in link.inner_text()
        assert [(r["kind"], r["state"]) for r in _jobs(store)] == [("/search/export.xlsx", reports.MADE)]
        assert errors == []
    app.state.downloads.stop()
