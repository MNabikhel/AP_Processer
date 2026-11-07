"""While a backup is being restored on the company server, every web worker answers everyone else 503 (D-188):
two dashboards on one data folder stand in for two web worker processes (each looks at the restore's marker, not
at the other's memory)."""

from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient

from ap_coder.core.store import Store, load_sample_setup
from ap_coder.core.store.backups import clear_left_restore, restore_marker, restore_under_way
from ap_coder.web.app import create_app
from ap_coder.web.maintenance import MESSAGE
from tests.web.test_server_web import account, log_in
from tests.webclient import PAGE, web_settings

HTML = {"accept": "text/html,application/xhtml+xml"}


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    return store


def worker(store: Store, server: bool = True) -> TestClient:
    """One web worker: a dashboard of its own on the same database (another Store, as another process has)."""
    app = create_app(web_settings(), Store(store.path), start_reader=False, server=server)
    return TestClient(app, base_url="https://localhost", client=("10.0.0.5", 50000), follow_redirects=False)


@pytest.fixture
def held(store, monkeypatch):
    """A restore that waits half way (its marker written) until ``go`` is set."""
    reached, go = threading.Event(), threading.Event()
    real = Store._restore_from

    def slow(self, backup, *, keep_accounts=False):
        reached.set()
        assert go.wait(20)
        return real(self, backup, keep_accounts=keep_accounts)

    monkeypatch.setattr(Store, "_restore_from", slow)
    return reached, go


def test_while_an_admin_restores_every_other_worker_answers_503_then_serves_again(store, held):
    reached, go = held
    account(store, "boss", "admin")
    backup = store.backup_now("manual")
    first, second = worker(store), worker(store)
    log_in(first, "boss", next="/settings/data")
    answers: list = []
    restore = threading.Thread(target=lambda: answers.append(first.post(
        "/settings/data/restore", data={"name": backup.name, "confirm": "RESTORE"}, headers=PAGE)))  # fmt: skip
    restore.start()
    try:
        assert reached.wait(20)
        assert restore_under_way(store.path)["backup"] == backup.name
        page = second.get("/review", headers=HTML)  # someone else's page, on the other worker
        assert page.status_code == 503 and "being restored from a backup" in page.text
        assert page.headers["retry-after"] == "60" and "Content-Security-Policy" in page.headers  # (outermost)
        fetch = second.post("/review/bulk-approve", data={"ids": "1"}, headers=PAGE)  # a page's own request
        assert fetch.status_code == 503 and fetch.json() == {"ok": False, "error": MESSAGE, "restoring": True}
        assert first.get("/review", headers=HTML).status_code == 503  # the restoring worker's other people too
        assert second.get("/login", headers=HTML).status_code == 503  # nobody logs in meanwhile
        assert second.get("/healthz").status_code == 200 and second.get("/static/app.css").status_code == 200
    finally:
        go.set()
        restore.join(30)
    assert answers and answers[0].status_code == 200, answers  # the Admin's own restore went through
    assert answers[0].json()["redirect"].startswith("/login")  # every session ended: the Admin logs in again
    assert not restore_marker(store.path).exists()
    assert second.get("/login", headers=HTML).status_code == 200  # back


def test_a_restore_that_fails_clears_its_marker_too(store, monkeypatch):
    backup = store.backup_now("manual")
    monkeypatch.setattr(Store, "_restore_from", lambda self, b, **k: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        store.restore_state(backup, keep_accounts=True)
    assert not restore_marker(store.path).exists()
    assert worker(store).get("/login", headers=HTML).status_code == 200


def test_a_restore_from_the_console_is_answered_the_same(store, held, tmp_path):
    """``backup restore`` runs in a process of its own (AP Coder stopped, or the service started meanwhile)."""
    reached, go = held
    backup = store.backup_now("manual")
    console = threading.Thread(target=lambda: Store(store.path).restore_state(backup, keep_accounts=True))
    console.start()
    try:
        assert reached.wait(20)
        assert worker(store).get("/login", headers=HTML).status_code == 503
    finally:
        go.set()
        console.join(30)
    assert worker(store).get("/login", headers=HTML).status_code == 200


def test_a_marker_left_by_a_restore_that_crashed_is_cleared_at_the_next_start_and_said(store):
    marker = restore_marker(store.path)
    marker.write_text('{"pid": 999999, "started": "2026-11-06T10:00:00", "backup": "ap_coder-x.db"}', "utf-8")
    assert worker(store).get("/login", headers=HTML).status_code == 200  # its process is gone: never a 503
    said = clear_left_restore(store.path)
    assert "A restore of ap_coder-x.db, started 2026-11-06 10:00, did not finish" in said and not marker.exists()
    assert clear_left_restore(store.path) == ""  # once


def test_a_marker_whose_restore_still_runs_is_kept_at_the_start(store, held):
    """The console's restore holds the backups' lock: the service starting meanwhile leaves its marker."""
    reached, go = held
    backup = store.backup_now("manual")
    console = threading.Thread(target=lambda: Store(store.path).restore_state(backup, keep_accounts=True))
    console.start()
    try:
        assert reached.wait(20)
        assert clear_left_restore(store.path) == "" and restore_marker(store.path).exists()
    finally:
        go.set()
        console.join(30)


def test_the_laptop_is_unchanged(store, held):
    reached, go = held
    backup = store.backup_now("manual")
    console = threading.Thread(target=lambda: Store(store.path).restore_state(backup))
    console.start()
    try:
        assert reached.wait(20)
        laptop = TestClient(create_app(web_settings(), Store(store.path), start_reader=False, server=False),
                            base_url="http://localhost")  # fmt: skip
        assert laptop.get("/healthz").status_code == 200 and laptop.get("/review").status_code == 200
    finally:
        go.set()
        console.join(30)
