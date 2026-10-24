"""The dashboard as the company's server (D-99): Open folder and Shutdown, imports queued behind the one running, the
messages and uploads kept in the database (a restart keeps them), whose drafts, and every page under the
Content-Security-Policy in a browser."""

from __future__ import annotations

import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from ap_coder.core.config import Settings
from ap_coder.core.store import Store, load_sample_setup
from ap_coder.ops import offline
from ap_coder.web import access, core, files
from ap_coder.web.app import create_app
from ap_coder.web.pages import learning, process

from .conftest import SAMPLES, chromium_path
from .webclient import PAGE, add_clean, post

STEM = "harbourview_NS_HST_HPS-2026-0347"
OTHER = "northwind_ON_HST_NW-2026-0912"


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    return store


def settings() -> Settings:
    s = Settings()
    return replace(s, llm=replace(s.llm, provider="off"))


class Role:
    """What the login middleware will set (srv-auth): the role of the session's user."""

    def __init__(self, app, role: str = "", user_id: str = "") -> None:
        self.app, self.role, self.user_id = app, role, user_id

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            state = scope.setdefault("state", {})
            if self.role:
                state["user_role"] = self.role
            if self.user_id:
                state["user_id"] = self.user_id
        await self.app(scope, receive, send)


def client_as(store, role: str = "", *, user_id: str = "", base_url: str = "https://localhost") -> TestClient:
    app = create_app(settings(), store, start_reader=False)
    app.add_middleware(Role, role=role, user_id=user_id)
    return TestClient(app, base_url=base_url)


@pytest.fixture
def server_on(monkeypatch):
    monkeypatch.setenv(offline.SERVER_ENV, "1")


@pytest.fixture
def opened(monkeypatch):
    """Every way AP Coder could open a folder window on the computer it runs on, recorded instead."""
    calls = []
    real = core.subprocess.Popen

    def popen(args, *more, **kwargs):
        if list(args)[:1] in (["xdg-open"], ["open"]):
            calls.append(args)
            return None
        return real(args, *more, **kwargs)

    monkeypatch.setattr(core.subprocess, "Popen", popen)
    monkeypatch.setattr(core.os, "startfile", lambda *a, **k: calls.append(a), raising=False)
    return calls


# --- Open folder and Shutdown ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("url", ["/process/open-folder", "/settings/data/open-folder"])
def test_open_folder_on_the_server_shows_where_it_is_to_an_admin(store, server_on, opened, url):
    answer = post(client_as(store, "admin"), url).json()
    assert opened == []  # never a window on the server's screen
    text = answer["toast"]["text"]
    assert text.startswith("This folder is on the server: ") and str(core.data_dir(store)) in text
    assert "download" in text.lower()


@pytest.mark.parametrize("url", ["/process/open-folder", "/settings/data/open-folder"])
def test_open_folder_on_the_server_does_not_show_its_path_to_others(store, server_on, opened, url):
    answer = post(client_as(store, "clerk"), url).json()
    assert opened == []
    assert "on the server" in answer["toast"]["text"] and str(core.data_dir(store)) not in answer["toast"]["text"]


def test_the_process_page_on_the_server_hides_the_folder_from_others(store, server_on):
    page = client_as(store, "clerk").get("/process").text
    assert str(core.invoice_dir(store)) not in page and core.short_path(core.invoice_dir(store)) not in page
    assert "on the server" in page
    admin = client_as(store, "admin").get("/process").text
    assert "Where is it?" in admin


def test_open_folder_still_opens_it_on_a_laptop(store, opened, monkeypatch):
    monkeypatch.setenv(offline.SERVER_ENV, "0")  # (restored afterwards: --server sets it)
    answer = post(client_as(store, base_url="http://localhost"), "/process/open-folder").json()
    assert opened and answer["toast"]["text"] == "Opened the invoices folder."


def test_on_the_server_only_the_service_stops_ap_coder_with_its_token(store, server_on):
    """The service's stop asks POST /shutdown from the server itself with the server file's token; nothing else
    can stop it, and no page offers to."""

    class FakeServer:
        should_exit = False

    app = create_app(settings(), store, start_reader=False)
    app.state.shutdown_token, app.state.server = "s3cret", FakeServer()
    here = TestClient(app, base_url="https://127.0.0.1:8501", client=("127.0.0.1", 50000))
    other = TestClient(app, base_url="https://127.0.0.1:8501", client=("10.1.2.40", 50000))
    assert other.post("/shutdown", headers={"X-AP-Coder-Token": "s3cret"}).status_code == 403
    assert here.post("/shutdown", headers={**PAGE}).status_code == 403  # a page can't stop it
    assert here.post("/shutdown", headers={"X-AP-Coder-Token": "wrong"}).status_code == 403
    assert app.state.server.should_exit is False
    assert here.post("/shutdown", headers={"X-AP-Coder-Token": "s3cret"}).status_code == 200
    assert app.state.server.should_exit is True


def test_healthz_says_what_the_update_needs_on_the_server_itself(store, server_on):
    app = create_app(settings(), store, start_reader=False, address="0.0.0.0")
    here = TestClient(app, base_url="https://127.0.0.1:8501", client=("127.0.0.1", 50000)).get("/healthz").json()
    assert here["app"] == "ap-coder" and here["folder"] and here["version"]
    other = TestClient(app, base_url="https://10.1.2.3:8501", client=("10.1.2.40", 50000)).get("/healthz").json()
    assert other == {"ok": True, "app": "ap-coder"}  # another computer: only that it is up (B-979)


def test_no_page_offers_to_stop_ap_coder(store, server_on):
    for path in ("/process", "/settings/data", "/help"):
        assert "/shutdown" not in client_as(store, "admin").get(path).text


# --- Imports queued behind the one running ------------------------------------------------------------------------


def upload(client, *names):
    sent = [("files", (name, (SAMPLES / name).read_bytes(), "application/pdf")) for name in names]
    return client.post("/process/upload", files=sent, headers=PAGE)


def test_a_second_upload_waits_behind_the_running_import(store, monkeypatch):
    release = threading.Event()
    runs: list[list[str]] = []

    def fake_process_files(store_, settings_, paths, **kwargs):
        def work(job):
            runs.append([p.name for p in paths])
            if len(runs) == 1:
                assert release.wait(30)
            return {"label": f"Processed {len(paths)}", "messages": []}

        return work

    monkeypatch.setattr(process, "process_files", fake_process_files)
    client = client_as(store, base_url="http://localhost")
    first = upload(client, f"{STEM}.pdf")
    assert first.json() == {"ok": True, "job": "process", "status": "/process/status"}
    second = upload(client, f"{OTHER}.pdf")  # a colleague, while the first import runs
    assert second.status_code == 200, second.text
    answer = second.json()
    assert answer["ok"] and answer["queued"] == 1 and answer["status"] == "/process/status"
    assert answer["toast"]["text"] == "Waiting: 1 file queued behind the import in progress."
    assert (core.invoice_dir(store) / f"{OTHER}.pdf").exists()  # saved at once
    assert "Waiting: 1 file queued behind the import in progress" in client.get("/process").text
    assert client.get("/process/status").json()["waiting"] == 1
    release.set()
    job = client.app.state.jobs.get("process")
    for _ in range(100):
        job.wait(5)
        if len(runs) == 2 and not job.running:
            break
        threading.Event().wait(0.05)
    assert runs == [[f"{STEM}.pdf"], [f"{OTHER}.pdf"]]  # the second ran after the first, by itself
    assert client.get("/process/status").json()["waiting"] == 0
    assert "queued behind" not in client.get("/process").text


# --- Messages and uploads kept in the database --------------------------------------------------------------------


def test_a_message_waiting_for_its_page_survives_a_restart(store, tmp_path):
    client = client_as(store, base_url="http://localhost")
    answer = post(client, "/settings/data/backup").json()
    assert "flash=" in answer["redirect"]
    restarted = Store(store.path)  # AP Coder started again: a new app, a new store on the same database
    again = client_as(restarted, base_url="http://localhost")
    page = again.get(answer["redirect"]).text
    assert "Backed up to" in page
    assert "Backed up to" not in again.get(answer["redirect"]).text  # shown once


def test_an_expired_message_is_not_shown(store, monkeypatch):
    monkeypatch.setattr(core, "_FLASH_TTL", -1)
    with core.using_store(store):
        token = core.keep_flash([core.flash_message("Saved.")])
        assert core.take_flash(token) == []


def test_a_spreadsheet_waiting_for_its_columns_survives_a_restart(store):
    with core.using_store(store):
        token = files.keep_upload("accounts.csv", b"code,name\n6100,Rent\n")
    with core.using_store(Store(store.path)):
        kept = files.get_upload(token)
        assert kept is not None and kept.name == "accounts.csv" and kept.content == b"code,name\n6100,Rent\n"
        files.drop_upload(token)
        assert files.get_upload(token) is None


def test_ap_line_history_waiting_for_its_columns_survives_a_restart(store):
    sheets = {"CSV": (["GL", "Vendor"], [{"GL": "6100", "Vendor": "Acme"}])}
    with core.using_store(store):
        token = learning.keep_upload("history.csv", sheets)
    with core.using_store(Store(store.path)):
        name, kept = learning.get_upload(token)
        assert name == "history.csv" and list(kept["CSV"][0]) == ["GL", "Vendor"]
        assert kept["CSV"][1] == [{"GL": "6100", "Vendor": "Acme"}]


def test_messages_and_uploads_never_use_the_internet_or_a_file_of_their_own(store, tmp_path):
    before = sorted(p.name for p in store.path.parent.iterdir())
    with core.using_store(store):
        core.keep_flash([core.flash_message("x")])
        files.keep_upload("a.csv", b"a\n1\n")
    after = sorted(p.name for p in store.path.parent.iterdir())
    assert [n for n in after if n not in before and not n.startswith(store.path.name)] == []


# --- Whose drafts ---------------------------------------------------------------------------------------------------


class _Request:
    def __init__(self) -> None:
        self.state = SimpleNamespace()


def test_a_draft_is_keyed_by_the_user_when_there_are_accounts():
    request = _Request()
    request.state.user_id = 7
    assert access.person_key(request) == ("user", "7")
    assert access.person_key(_Request()) is None  # this computer's own person, as before


def test_two_users_keep_drafts_of_their_own(store):
    from ap_coder.web.pages import invoice

    app = create_app(settings(), store, start_reader=False)
    one = SimpleNamespace(app=app, state=SimpleNamespace(user_id=1))
    two = SimpleNamespace(app=app, state=SimpleNamespace(user_id=2))
    invoice._drafts(one)[5] = {"header": {}}
    assert invoice._drafts(two) == {}
    assert app.state.person_drafts[("user", "1")] == {5: {"header": {}}}
    assert invoice._drafts(SimpleNamespace(app=app, state=SimpleNamespace())) is app.state.invoice_drafts


def test_is_admin_on_a_laptop_and_on_the_server(monkeypatch):
    monkeypatch.setenv(offline.SERVER_ENV, "0")  # (restored afterwards: --server sets it)
    assert access.is_admin(_Request()) is True
    monkeypatch.setenv(offline.SERVER_ENV, "1")
    assert access.is_admin(_Request()) is False
    request = _Request()
    request.state.user_role = "admin"
    assert access.is_admin(request) is True
    request.state.user_role = "clerk"
    assert access.is_admin(request) is False


# --- Every page in a browser, under the Content-Security-Policy ---------------------------------------------------


@pytest.mark.slow
def test_in_a_browser_no_page_breaks_the_content_security_policy(store, server_on):
    if chromium_path() is None:
        pytest.skip("Playwright with Chromium isn't installed")
    import socket
    import time

    import uvicorn
    from playwright.sync_api import sync_playwright

    from ap_coder.web.pages import PAGES

    invoice_id = add_clean(store)
    app = create_app(settings(), store, start_reader=False)
    app.add_middleware(Role, role="admin")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    running = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=running.run, daemon=True)
    thread.start()
    for _ in range(200):
        if running.started:
            break
        time.sleep(0.05)
    paths = [p.path for p in PAGES] + [f"/review/{invoice_id}", "/settings/data", "/help"]
    problems: list[str] = []
    try:
        with sync_playwright() as p:
            chromium = p.chromium.launch(executable_path=chromium_path(), args=["--no-proxy-server"])
            page = chromium.new_page(viewport={"width": 1366, "height": 768})

            def console(m):
                if "Content Security Policy" in m.text or "Content-Security-Policy" in m.text:
                    problems.append(f"{page.url}: {m.text}")

            page.on("console", console)
            page.on("pageerror", lambda e: problems.append(f"{page.url}: {e}"))
            for path in dict.fromkeys(paths):
                response = page.goto(f"http://127.0.0.1:{port}{path}")
                assert response is not None and "content-security-policy" in response.headers
                page.wait_for_load_state("networkidle")
                if path.startswith("/review/"):
                    page.wait_for_selector(".iv-sheet:not(.loading)")
                assert page.evaluate("typeof window.getComputedStyle") == "function"
            # The rail's More menu remembers being open (its script is a file of its own now, no longer inline).
            page.goto(f"http://127.0.0.1:{port}/review")
            page.click("#nav-more summary")
            page.wait_for_function("localStorage.getItem('apc-nav-more') === 'open'")
            page.reload()
            assert page.evaluate("document.getElementById('nav-more').open") is True
            chromium.close()
    finally:
        running.should_exit = True
        thread.join(5)
    assert problems == [], problems
