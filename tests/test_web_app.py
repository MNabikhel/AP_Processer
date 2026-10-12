"""The web dashboard's foundation (ap_coder/web): every page answers, only this computer's addresses and AP Coder's
own pages are let in, untrusted text is escaped, the launcher's /healthz and /shutdown, and ``serve``."""

from __future__ import annotations

import json
import os
import re
from dataclasses import replace
from pathlib import Path

import jinja2
import pytest
from fastapi.testclient import TestClient

import ap_coder
from ap_coder.config import Settings
from ap_coder.store import Store
from ap_coder.web import core, server
from ap_coder.web.app import CODE_FOLDER, create_app
from ap_coder.web.jobs import DONE, ERROR, Job
from ap_coder.web.pages import PAGES

ROOT = Path(__file__).resolve().parent.parent
PAGE = {"X-AP-Coder": "1"}


def web_settings() -> Settings:
    s = Settings()
    return replace(s, llm=replace(s.llm, provider="off"))


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "private" / "ap_coder.db")


@pytest.fixture
def client(store):
    return TestClient(create_app(web_settings(), store), base_url="http://localhost")


# --- Every page answers ---------------------------------------------------------------------------------------


def test_every_page_in_the_rail_answers(client):
    home = client.get("/")
    assert home.status_code == 200 and home.url.path == "/review"
    for page in PAGES:
        response = client.get(page.path)
        assert response.status_code == 200, page.path
        assert f"<title>{page.title} · AP Coder</title>".replace("&", "&amp;") in response.text, page.path
        assert 'aria-current="page"' in response.text
    text = client.get("/help").text
    for page in PAGES:  # the rail lists every page, in its sections
        assert f'href="{page.path}"' in text
    for section in ("Work", "Close &amp; compliance", "Master data", "Analytics", "System"):
        assert section in text
    assert "Offline · Data stays on this computer" in text


NAV_SECTIONS = {
    "Work": ["review", "process", "search"],
    "Close & compliance": ["exports", "month_end", "sales_tax", "statements"],
    "Master data": ["vendors", "purchase_orders", "accounts"],
    "Analytics": ["learning", "spend", "insights", "activity"],
    "System": ["settings", "help"],
}


def test_rail_order_and_sections():
    assert [p.key for p in PAGES] == [k for keys in NAV_SECTIONS.values() for k in keys]
    assert {p.key: p.section for p in PAGES} == {k: s for s, keys in NAV_SECTIONS.items() for k in keys}


def test_unknown_pages_and_invoices_are_not_found(client):
    assert client.get("/nowhere", headers={"accept": "text/html"}).status_code == 404
    response = client.get("/review/999", headers={"accept": "text/html"})
    assert response.status_code == 404 and "not in AP Coder" in response.text


def test_static_files_fonts_and_logo_are_served_locally(client):
    assert client.get("/static/app.css").status_code == 200
    assert client.get("/static/js/app.js").status_code == 200
    assert client.get("/fonts/MaterialSymbolsRounded.woff2").status_code == 200
    assert client.get("/fonts/InterVariable.woff2").status_code == 200
    assert client.get("/assets/logo.svg").status_code == 200
    page = client.get("/review").text
    for url in re.findall(r'(?:src|href)="([^"]+)"', page):
        assert url.startswith(("/", "#")), url  # nothing from another host


# --- Only this computer, only AP Coder's own pages ---------------------------------------------------------------


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8501", "localhost.evil.example", "[::2]:8501", ""])
def test_a_foreign_host_header_is_refused(store, host):
    client = TestClient(create_app(web_settings(), store), base_url="http://localhost")
    response = client.get("/review", headers={"host": host})
    assert response.status_code == 400 and "not this computer's address" in response.text
    assert "<" not in response.text or response.headers["content-type"].startswith("text/plain")


@pytest.mark.parametrize("host", ["localhost:8501", "127.0.0.1:8501", "[::1]:8501", "LOCALHOST"])
def test_this_computers_addresses_are_let_in(client, host):
    assert client.get("/healthz", headers={"host": host}).status_code == 200


def test_the_testclients_default_host_is_refused(store):
    """Tests use base_url="http://localhost": "testserver" is a name like any other."""
    assert TestClient(create_app(web_settings(), store)).get("/review").status_code == 400


def test_listening_on_every_network_lets_in_ip_addresses_only(store):
    client = TestClient(create_app(web_settings(), store, address="0.0.0.0"), base_url="http://localhost")
    assert client.get("/healthz", headers={"host": "192.168.1.20:8501"}).status_code == 200
    assert client.get("/healthz", headers={"host": "evil.example:8501"}).status_code == 400


def test_a_post_without_the_page_header_is_refused(client, store):
    response = client.post("/demo/load", data={"back": "/review"})
    assert response.status_code == 403 and store.demo_count() == 0
    response = client.post("/demo/load", data={"back": "/review"}, headers={**PAGE, "Origin": "http://evil.example"})
    assert response.status_code == 403
    response = client.post("/demo/load", data={"back": "/review"}, headers={**PAGE, "Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 403
    response = client.post("/demo/load", data={"back": "/review"}, headers={**PAGE, "Origin": "null"})
    assert response.status_code == 403
    assert store.demo_count() == 0


def test_a_post_from_the_page_is_let_in(client, store):
    response = client.post("/demo/load", data={"back": "/review"}, headers={**PAGE, "Origin": "http://localhost"})
    assert response.status_code == 200 and response.json()["redirect"].startswith("/review?flash=")
    assert store.demo_count() > 0
    page = client.get(response.json()["redirect"]).text
    assert "Demo ready:" in page and "Demo invoices loaded" in page
    assert "Demo ready:" not in client.get(response.json()["redirect"]).text  # a message shows once


def test_get_never_changes_anything(client):
    assert client.get("/demo/load").status_code in (404, 405)
    assert client.get("/process/upload").status_code in (404, 405)


def test_after_an_action_the_browser_only_goes_to_this_server(client):
    response = client.post("/demo/load", data={"back": "https://evil.example/"}, headers=PAGE)
    assert response.json()["redirect"].startswith("/process")
    for url in ("//evil.example", "https://evil.example", "/\\evil.example", "javascript:alert(1)"):
        assert core.local_url(url, "/x") == "/x"
    assert core.local_url("/review?tab=parked") == "/review?tab=parked"


def test_text_from_invoices_is_escaped(client, store, tmp_path):
    from ap_coder.store import load_sample_setup

    load_sample_setup(store)
    # "<" and ">" can't be in a Windows file name; there the vendor name below carries the markup
    pdf = tmp_path / ("<script>alert(1).pdf" if os.name != "nt" else "&lt;script&gt;alert(1).pdf")
    pdf.write_bytes(b"%PDF-1.4 test")
    output = {"vendor_name": "<img src=x onerror=alert(1)>", "invoice_number": "<b>1</b>", "grand_total": 10,
              "currency": "CAD", "lines": [], "tax_lines": []}  # fmt: skip
    invoice_id = store.add_invoice(pdf, output, {"issues": [], "requires_review": True})
    for path in ("/review", "/process", f"/review/{invoice_id}"):
        text = client.get(path).text
        assert "<img src=x" not in text and "<b>1</b>" not in text and "<script>alert" not in text, path
        assert "&lt;img src=x onerror=alert(1)&gt;" in text, path


# --- The launcher: /healthz, /shutdown, the server file --------------------------------------------------------------


def test_healthz_says_which_ap_coder_this_is(client):
    data = client.get("/healthz").json()
    assert data["ok"] is True and data["app"] == "ap-coder"
    assert data["version"] == ap_coder.__version__
    assert Path(data["folder"]) == ROOT == CODE_FOLDER
    assert data["pid"] == os.getpid()
    assert isinstance(data["port"], int)
    assert "token" not in json.dumps(data)


def test_version_comes_from_pyproject():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'(?m)^version\s*=\s*"([^"]+)"', text).group(1) == ap_coder.__version__
    assert ap_coder._version() == ap_coder.__version__


def test_version_without_a_pyproject_comes_from_the_installed_metadata(monkeypatch):
    import importlib.metadata

    real = Path.read_text

    def no_pyproject(self, *args, **kwargs):
        if self.name == "pyproject.toml":
            raise OSError("not in an installed copy")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", no_pyproject)
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "9.8.7" if name == "ap-coder" else "")
    assert ap_coder._version() == "9.8.7"

    def missing(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", missing)
    assert ap_coder._version() == "0+unknown"


class FakeServer:
    should_exit = False


def _launcher_client(store, token="s3cret", client=("127.0.0.1", 50000)):
    app = create_app(web_settings(), store)
    app.state.shutdown_token, app.state.server = token, FakeServer()
    return app, TestClient(app, base_url="http://127.0.0.1:8501", client=client)


def test_shutdown_needs_the_token_from_this_computer(store):
    app, client = _launcher_client(store)
    assert client.post("/shutdown").status_code == 403  # no token, no page header
    assert client.post("/shutdown", headers={"X-AP-Coder-Token": "wrong"}).status_code == 403
    assert client.post("/shutdown", headers={**PAGE}).status_code == 403  # the page itself can't stop it
    assert app.state.server.should_exit is False
    response = client.post("/shutdown", headers={"X-AP-Coder-Token": "s3cret"})
    assert response.status_code == 200 and response.json()["stopping"] is True
    assert app.state.server.should_exit is True


def test_shutdown_is_refused_from_another_computer_or_without_a_server_token(store):
    app, client = _launcher_client(store, client=("192.168.1.30", 50000))
    assert client.post("/shutdown", headers={"X-AP-Coder-Token": "s3cret"}).status_code == 403
    app, client = _launcher_client(store, token=None)
    assert client.post("/shutdown", headers={"X-AP-Coder-Token": ""}).status_code == 403
    assert app.state.server.should_exit is False


def test_the_server_file_describes_the_running_server(tmp_path):
    path = server.write_server_file(8611, "tok")
    assert path == tmp_path / "private" / ".ap_coder_server.json"
    info = json.loads(path.read_text(encoding="utf-8"))
    assert info["pid"] == os.getpid() and info["port"] == 8611 and info["token"] == "tok"
    assert info["version"] == ap_coder.__version__ and Path(info["folder"]) == ROOT and info["started"]
    server.remove_server_file("another server's token")
    assert path.exists()  # not ours: left alone
    server.remove_server_file("tok")
    assert not path.exists()


def test_serve_runs_uvicorn_writes_then_removes_the_server_file(monkeypatch, store, capsys):
    import uvicorn

    seen = {}

    class Server:
        started = True

        def __init__(self, config):
            seen["config"] = config

        def run(self):
            seen["file"] = json.loads(server.server_file().read_text(encoding="utf-8"))
            seen["app"] = seen["config"].app

    monkeypatch.setattr(uvicorn, "Server", Server)
    monkeypatch.setattr(server, "_announce_when_up", lambda url, browser: seen.update(url=url, browser=browser))
    assert server.serve("127.0.0.1", 8612, open_browser=False, store=store) == 0
    assert seen["config"].host == "127.0.0.1" and seen["config"].port == 8612
    assert seen["file"]["port"] == 8612 and seen["app"].state.shutdown_token == seen["file"]["token"]
    assert len(seen["file"]["token"]) >= 24
    assert not server.server_file().exists()


def test_announce_prints_the_address_once_it_answers(monkeypatch, capsys):
    import urllib.request
    import webbrowser

    class Answer:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class Opener:
        def __init__(self, *handlers):
            self.handlers = handlers

        def open(self, url, timeout):
            assert url == "http://127.0.0.1:8613/healthz"
            return Answer()

    def build_opener(*handlers):
        # its own server is asked directly, never through a proxy (HTTP_PROXY would get the request)
        assert any(isinstance(h, urllib.request.ProxyHandler) and h.proxies == {} for h in handlers)
        return Opener(*handlers)

    opened = []
    monkeypatch.setattr(urllib.request, "build_opener", build_opener)
    monkeypatch.setattr(webbrowser, "open", opened.append)
    monkeypatch.delenv("BROWSER", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")  # a desktop (a Linux one without: no browser, tests/test_browser_open.py)
    server._announce_when_up("http://127.0.0.1:8613", open_browser=True)
    assert capsys.readouterr().out.strip() == "AP Coder is running at http://127.0.0.1:8613"
    assert opened == ["http://127.0.0.1:8613"]
    monkeypatch.setenv("BROWSER", "none")
    server._announce_when_up("http://127.0.0.1:8613", open_browser=True)
    assert opened == ["http://127.0.0.1:8613"]  # BROWSER=none: printed only


def test_browser_url():
    assert server.browser_url("127.0.0.1", 8501) == "http://127.0.0.1:8501"
    assert server.browser_url("0.0.0.0", 8501) == "http://127.0.0.1:8501"
    assert server.browser_url("::1", 8501) == "http://[::1]:8501"


# --- Background jobs ---------------------------------------------------------------------------------------------


def test_a_job_runs_in_the_background_one_at_a_time(client):
    import threading

    job, release = Job("t"), threading.Event()

    def work(j):
        j.step(1, 2, "half way")
        j.log("<b>one</b>", "ok", "check_circle")
        release.wait(5)
        return {"label": "done"}

    assert job.start(work) is True
    assert job.start(work) is False  # already running
    release.set()
    assert job.wait(5)
    snap = job.snapshot()
    assert snap["state"] == DONE and snap["result"] == {"label": "done"} and snap["done"] == 2
    assert snap["lines"] == [{"text": "<b>one</b>", "tone": "ok", "icon": "check_circle"}]

    def broken(j):
        raise RuntimeError("disk full")

    assert job.start(broken) and job.wait(5)
    assert job.snapshot()["state"] == ERROR and job.snapshot()["error"] == "disk full"


def test_job_status_url(client):
    assert client.get("/jobs/nothing").status_code == 404
    assert client.get("/process/status").json()["state"] == "idle"
    assert client.get("/jobs/process").json()["state"] == "idle"


def test_templates_escape_and_ui_helpers_are_safe():
    env = core.templates.env
    assert env.autoescape is True
    out = env.from_string("{{ x }} {{ ui.pill(x, 'ok') }}").render(x="<i>v</i>")
    assert "<i>" not in out and out.count("&lt;i&gt;v&lt;/i&gt;") == 2
    with pytest.raises(jinja2.UndefinedError):  # only the HTML builders listed in core are there
        env.from_string("{{ ui.document_text(x) }}").render(x="")
