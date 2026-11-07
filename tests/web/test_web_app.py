"""The web dashboard's foundation (ap_coder/web): every page answers, only this computer's addresses and AP Coder's
own pages are let in, untrusted text is escaped, the launcher's /healthz and /shutdown, and ``serve``."""

from __future__ import annotations

import json
import os
import re
from dataclasses import replace
from pathlib import Path

import anyio
import anyio.to_thread
import jinja2
import pytest
from fastapi.testclient import TestClient

import ap_coder
from ap_coder.core.config import Settings
from ap_coder.web import core, server
from ap_coder.web.app import CODE_FOLDER, create_app
from ap_coder.web.jobs import DONE, ERROR, Job
from ap_coder.web.pages import PAGES
from tests.webclient import PAGE

ROOT = Path(__file__).resolve().parents[2]


def web_settings() -> Settings:
    s = Settings()
    return replace(s, llm=replace(s.llm, provider="off"))


@pytest.fixture
def client(store):
    return TestClient(create_app(web_settings(), store), base_url="http://localhost")


# --- Every page answers ---------------------------------------------------------------------------------------


def test_each_page_handler_shares_one_database_connection_for_its_request(client):
    """Only 7 of the 179 handlers were marked to share one connection: every other request opened and closed one per
    store call (the settings page 23, Process 34), each close writing the write-ahead log to disk. Every plain
    handler that takes the request now shares one (``core.one_connection_each``); an async one is left as it is."""
    import importlib
    import inspect

    from fastapi.routing import APIRoute

    from ap_coder.web.app import MORE_MODULES

    plain = []
    for module in [*MORE_MODULES, *{p.module for p in PAGES}]:
        router = importlib.import_module(f"ap_coder.web.pages.{module}").router
        plain += [(module, r) for r in router.routes if isinstance(r, APIRoute)
                  and not inspect.iscoroutinefunction(inspect.unwrap(r.endpoint))
                  and "request" in inspect.signature(r.endpoint).parameters]  # fmt: skip
    assert len(plain) > 100
    assert [(m, r.path) for m, r in plain if not getattr(r.endpoint, "one_connection", False)] == []

    opened = []
    real = client.app.state.store._open

    def counted():
        opened.append(1)
        return real()

    client.app.state.store._open = counted
    for path in ("/settings", "/process", "/learning", "/month-end"):
        opened.clear()
        assert client.get(path).status_code == 200
        assert len(opened) <= 1, path


def test_every_page_in_the_rail_answers(client):
    home = client.get("/")
    assert home.status_code == 200 and home.url.path == "/review"
    for page in PAGES:
        response = client.get(page.path)
        assert response.status_code == 200, page.path
        assert f"<title>{page.title} · AP Coder</title>".replace("&", "&amp;") in response.text, page.path
        assert 'aria-current="page"' in response.text
    text = client.get("/help").text
    for page in PAGES:  # the rail lists every page (the less used ones under More)
        assert f'href="{page.path}"' in text
    assert 'id="nav-more"' in text and "System" in text  # (the page's section, in its breadcrumbs)
    assert "Offline · Data stays on this computer" in text


NAV_SECTIONS = {  # the breadcrumbs' sections
    "Work": ["review", "process", "search", "manager"],
    "Close & compliance": ["exports", "month_end", "sales_tax", "statements"],
    "Master data": ["vendors", "purchase_orders", "accounts"],
    "Analytics": ["learning", "spend", "insights", "activity"],
    "System": ["settings", "help"],
}
RAIL_ORDER = ["review", "process", "search", "exports", "accounts",  # then More:
              "manager", "month_end", "sales_tax", "statements", "vendors", "purchase_orders", "learning", "spend",
              "insights", "activity", "settings", "help"]  # fmt: skip


def test_rail_order_and_sections():
    assert [p.key for p in PAGES] == RAIL_ORDER
    assert {p.key: p.section for p in PAGES} == {k: s for s, keys in NAV_SECTIONS.items() for k in keys}
    assert [p.key for p in PAGES if p.more] == RAIL_ORDER[5:15]


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


def test_every_template_links_the_static_files_at_their_kept_addresses(client):
    """A file linked at its plain address would be fetched again (checked) on every page; a page script loaded there
    would be a second app.js module beside the page's own (its imports are relative), with state of its own."""
    plain = re.compile(r'(?:src|href)="/(?:static|fonts|assets)/')
    for path in sorted(core.TEMPLATES_DIR.glob("*.html")):
        text = path.read_text(encoding="utf-8")
        assert not plain.search(text) and "static_version" not in text, path.name
    page = client.get("/help").text
    linked = re.findall(r'(?:src|href)="(/(?:static|fonts|assets)/[^"]+)"', page)
    build = core.read_static().build
    assert linked and all(url.split("/")[2] == build for url in linked), linked
    assert f'src="/static/{build}/js/app.js"' in page


def test_static_files_are_checked_again_on_every_use(client):
    """At their plain addresses (a page an older AP Coder drew, a bookmark), after an update a browser must ask again
    instead of running the copy it kept. An unchanged file answers 304, so asking costs nothing."""
    for path in ("/static/js/app.js", "/static/js/invoice_viewer.js", "/static/app.css"):
        response = client.get(path)
        assert response.status_code == 200 and response.headers.get("cache-control") == "no-cache", path
        again = client.get(path, headers={"if-none-match": response.headers["etag"]})
        assert again.status_code == 304 and again.headers.get("cache-control") == "no-cache", path


def _without_memory(app):
    """``app`` with its static files answered by the StaticFiles mounts alone, as before D-161."""
    from ap_coder.web.app import StaticFromMemory

    app.user_middleware = [m for m in app.user_middleware if m.cls is not StaticFromMemory]
    return app


@pytest.mark.parametrize("server_mode", [False, True])
def test_static_files_are_answered_from_memory_as_staticfiles_answered_them(store, tmp_path, server_mode):
    """The styles, scripts, fonts and logo come from memory on the event loop (D-161): every file, laptop and server,
    gets the status, headers and body StaticFiles gave it, a 304 to its ETag or time the same; a Range, a POST and a
    file that isn't there are still StaticFiles' own."""
    made = {"settings": web_settings(), "store": store, "start_reader": False, "server": server_mode}
    memory = TestClient(create_app(**made), base_url="http://localhost")
    before = TestClient(_without_memory(create_app(**made)), base_url="http://localhost")
    files = core.read_static().plain
    assert len(files) > 20 and {"/static/app.css", "/static/js/app.js", "/fonts/InterVariable.woff2",
                                "/assets/logo.svg"} <= set(files)  # fmt: skip

    def same(method: str, path: str, headers: dict[str, str] | None = None) -> int:
        new, old = (c.request(method, path, headers={**PAGE, **(headers or {})}) for c in (memory, before))
        assert new.status_code == old.status_code, (method, path, headers)
        assert [h for h in new.headers.items() if h[0] != "date"] == [h for h in old.headers.items() if h[0] != "date"]
        assert new.content == old.content, (method, path, headers)
        return new.status_code

    for path, file in files.items():
        etag, modified = file.etag, file.modified
        assert same("GET", path) == 200 and same("HEAD", path) == 200
        assert same("GET", path, {"range": "bytes=0-9"}) == 206
        assert same("GET", path, {"if-none-match": etag}) == 304
        assert same("GET", path, {"if-none-match": f'"other", W/{etag}'}) == 304  # weak, in a list
        assert same("HEAD", path, {"if-none-match": "*"}) == 304
        assert same("GET", path, {"if-none-match": '"other"', "if-modified-since": modified}) == 200
        assert same("GET", path, {"if-modified-since": modified}) == 304
        assert same("GET", path, {"if-modified-since": "Mon, 01 Jan 2001 00:00:00 GMT"}) == 200
        assert same("GET", path, {"if-modified-since": "not a date"}) == 200
        assert same("POST", path) == 405
    assert same("GET", "/static/nothing-here.js") == 404 and same("GET", "/fonts/js/app.js") == 404
    assert memory.get("/static/app.css", headers={"host": "evil.example"}).status_code == 400  # the Host check first
    assert memory.get("/static/app.css").headers["x-content-type-options"] == "nosniff"
    if server_mode:  # in the access log too
        log = (store.path.parent / "logs" / "access.log").read_text(encoding="utf-8")
        assert "GET /static/app.css 200" in log and "GET /static/app.css 304" in log


def test_a_static_file_is_answered_while_every_thread_of_the_worker_is_taken(store):
    """A static file needs no thread and no session: StaticFiles read each on one of a web worker's 4 threads, behind
    pages of 1-2 s (the phase 2 load run's 16-57 s waits for a stylesheet)."""
    app = create_app(web_settings(), store, start_reader=False, server=True)
    with TestClient(app, base_url="http://localhost") as client:
        limiter = client.portal.call(anyio.to_thread.current_default_thread_limiter)
        holders = [object() for _ in range(int(limiter.total_tokens))]
        for holder in holders:
            client.portal.call(limiter.acquire_on_behalf_of_nowait, holder)
        try:
            response = client.get("/static/js/app.js")
            assert response.status_code == 200
            assert response.content == core.read_static().plain["/static/js/app.js"].body
        finally:
            for holder in holders:
                client.portal.call(limiter.release_on_behalf_of, holder)


def test_a_static_file_that_links_out_of_its_folder_is_not_answered(tmp_path):
    """As StaticFiles (``follow_symlink=False``): a link to a file outside its folder is left out; one inside is
    kept."""
    folder, outside = tmp_path / "static", tmp_path / "secret.txt"
    (folder / "js").mkdir(parents=True)
    outside.write_text("not for the web", encoding="utf-8")
    (folder / "js" / "app.js").write_text("export {};", encoding="utf-8")
    try:
        (folder / "out.txt").symlink_to(outside)
        (folder / "again.js").symlink_to(folder / "js" / "app.js")
    except OSError:
        pytest.skip("this computer can't make symbolic links")
    files = core.read_static((("/static/", folder),)).plain
    assert sorted(files) == ["/static/again.js", "/static/js/app.js"]
    assert files["/static/again.js"].body == b"export {};"


def test_the_pages_link_each_static_file_at_a_kept_address_the_browser_keeps_a_year(client):
    """``static_url`` puts the build in the address: the browser keeps the file a year without asking again, and an
    update that changes a file links them all at new addresses. The kept stylesheet names the fonts at theirs."""
    build = core.read_static().build
    assert core.static_url("/static/js/app.js") == f"/static/{build}/js/app.js"
    assert core.static_url("/fonts/InterVariable.woff2") == f"/fonts/{build}/InterVariable.woff2"
    for plain in ("/static/js/app.js", "/static/app.css", "/fonts/InterVariable.woff2", "/assets/logo.svg"):
        response = client.get(core.static_url(plain))
        assert response.status_code == 200 and response.headers["cache-control"] == core.KEPT, plain
        again = client.get(core.static_url(plain), headers={"if-none-match": response.headers["etag"]})
        assert again.status_code == 304 and again.headers["cache-control"] == core.KEPT
        assert client.head(core.static_url(plain)).headers["content-length"] == response.headers["content-length"]
    css = client.get(core.static_url("/static/app.css")).text
    assert f'url("/fonts/{build}/InterVariable.woff2")' in css and 'url("/fonts/InterVariable' not in css
    assert f'url("/fonts/{build}/MaterialSymbolsRounded.woff2")' in css
    assert 'url("/fonts/InterVariable.woff2")' in client.get("/static/app.css").text  # the plain one as it is
    assert client.get(core.static_url("/static/js/app.js")).content == client.get("/static/js/app.js").content
    part = client.get(core.static_url("/static/js/app.js"), headers={"range": "bytes=0-9"})
    assert part.status_code == 206 and part.content == client.get("/static/js/app.js").content[:10]
    assert client.get(core.static_url("/static/nothing-here.js")).status_code == 404
    with pytest.raises(ValueError):
        core.static_url("/review")


@pytest.mark.parametrize("plain", ["/static/js/app.js", "/static/app.css", "/fonts/InterVariable.woff2",
                                   "/assets/logo.svg"])  # fmt: skip
def test_another_builds_address_answers_the_file_as_it_is_now_checked_again_on_every_use(client, plain):
    """A page drawn before an update links the files at the old build's addresses: they answer the files as they are
    now, never to be kept a year under an address that isn't theirs, with the fonts and the logo too."""
    other = core.static_url(plain).replace(core.read_static().build, "0123456789")
    response = client.get(other)
    assert response.status_code == 200 and response.headers["cache-control"] == core.CHECKED == "no-cache"
    assert response.content == client.get(core.static_url(plain)).content
    again = client.get(other, headers={"if-none-match": response.headers["etag"]})
    assert again.status_code == 304 and again.headers["cache-control"] == "no-cache"
    assert client.get(other.replace("0123456789", "0123456ABC")).status_code == 404  # not a build
    as_before = "no-cache" if plain.startswith("/static/") else None  # the plain address: as before
    assert client.get(plain).headers.get("cache-control") == as_before


def test_the_build_is_a_hash_of_the_files_contents(tmp_path):
    """Not the commit: a checkout's commit doesn't change with an edit not yet committed (the edited script would be
    kept a year), and an update that changes no file keeps everyone's copies."""
    folder = tmp_path / "static"
    folder.mkdir()
    script = folder / "app.js"
    script.write_text("export const a = 1;", encoding="utf-8")
    first = core.read_static.__wrapped__((("/static/", folder),)).build
    os.utime(script, (1_000_000_000, 1_000_000_000))  # touched, the same
    assert core.read_static.__wrapped__((("/static/", folder),)).build == first
    script.write_text("export const a = 2;", encoding="utf-8")
    assert core.read_static.__wrapped__((("/static/", folder),)).build != first
    script.rename(folder / "b.js")  # the same content under another name
    assert core.read_static.__wrapped__((("/static/", folder),)).build != first


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
    from ap_coder.core.store import load_sample_setup

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


def test_healthz_and_the_server_file_say_which_account_runs_it(store):
    """B-168: two Windows accounts sharing one AP Coder folder and data folder: the second person's double-click opened
    the first person's dashboard, and acted under their name. The server says whose it is (to this computer only)."""
    import getpass

    _app, local = _launcher_client(store)
    assert local.get("/healthz").json()["user"] == getpass.getuser()
    _app, colleague = _launcher_client(store, client=("192.168.1.30", 50000))
    assert "user" not in colleague.get("/healthz").json()
    info = json.loads(server.write_server_file(8611, "tok").read_text(encoding="utf-8"))
    assert info["user"] == getpass.getuser()


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
    monkeypatch.setenv("DISPLAY", ":0")  # a desktop (one without: no browser, tests/launcher/test_browser_open.py)
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


def test_a_laptop_lets_go_of_a_re_check_left_and_of_its_own_as_it_stops(demo_template, tmp_path, monkeypatch):
    """A dashboard stopped as it checked the queue again left the re-check's lease (its thread ended with the process):
    the next start's queue (and each invoice opened, Import invoices' list, the command line) waited for it up to two
    minutes. A laptop's start lets go of one left so, and its stop waits a few seconds for its own to end between two
    batches, letting go of it."""
    import threading
    import time

    from ap_coder.ai import azure_check, page_worker
    from ap_coder.coding import bulk
    from ap_coder.core.memory import vendor_key
    from ap_coder.core.store import REVIEW
    from tests.conftest import copy_database

    store = copy_database(demo_template, tmp_path / "private" / "ap_coder.db")
    monkeypatch.setattr(page_worker, "start_background", lambda *a, **k: None)
    monkeypatch.setattr(azure_check, "start_background", lambda *a, **k: None)
    monkeypatch.setattr(bulk.QueueRecheck.__init__, "__defaults__", (0.05,))  # (a look every 50 ms, not 5 s)
    monkeypatch.setattr(bulk, "RECHECK_BATCH", 1)
    checking, batch = threading.Event(), bulk._recheck_batch

    def slow(*args):
        checking.set()
        time.sleep(0.3)
        return batch(*args)

    monkeypatch.setattr(bulk, "_recheck_batch", slow)
    for inv in [i for i in store.list_invoices(REVIEW) if i["vendor_name"]]:  # vendors put on hold: checked again
        store.save_vendor(vendor_key(inv["vendor_name"]), inv["vendor_name"], "on_hold")
    assert len(store.stale_checks(store.checks_stamp())) > 2
    assert store.take_lease(bulk.RECHECK_LEASE, "4242:1", 120)  # left by the last start, stopped as it checked
    app = create_app(web_settings(), store, start_reader=True)
    with TestClient(app, base_url="http://localhost"):
        assert checking.wait(10)  # its own re-check began at once
    assert not app.state.queue_recheck.thread.is_alive()
    assert store.take_lease(bulk.RECHECK_LEASE, "another:1", 60) and store.stale_checks(store.checks_stamp())


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


def test_a_page_following_a_job_is_sent_its_state_done_and_note_only(client):
    """``watchJob`` asks ``?only=done,note``: what it shows, not the job's 500 lines (up to 57 KB) every look."""
    import threading

    job, written, release = client.app.state.jobs.get("spend-data"), threading.Event(), threading.Event()

    def work(j):
        j.step(2, 5, "Writing the sheets")
        for n in range(500):
            j.log(f"line {n}")
        written.set()
        release.wait(5)

    assert job.start(work) and written.wait(5)
    try:
        assert len(client.get("/jobs/spend-data").json()["lines"]) == 500  # everything, asked without only=
        answer = client.get("/jobs/spend-data?only=done,note")
        assert answer.json() == {"state": "running", "done": 2, "note": "Writing the sheets"}
        assert answer.headers["cache-control"] == "no-store"
    finally:
        release.set()
        assert job.wait(5)
    assert client.get("/jobs/spend-data?only=done,note").json()["state"] == "done"
    app_js = (core.STATIC_DIR / "js" / "app.js").read_text(encoding="utf-8")
    assert 'searchParams.set("only", "done,note")' in app_js


# --- Polls (app.js poll, D-161) ----------------------------------------------------------------------------------

POLL = """async (every) => {
  const app = await import(document.querySelector('script[src*="/js/app.js"]').src);  // the page's own module
  window.polled = app.poll("/polled", () => false, { everyMs: every });
}"""
HIDDEN = """(hidden) => {
  Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
  document.dispatchEvent(new Event("visibilitychange"));
}"""


def _looks(page, answer: dict) -> list[float]:
    """When the page asked /polled (each answered ``answer`` as it is then)."""
    import time

    looks: list[float] = []

    def answered(route):
        looks.append(time.monotonic())
        route.fulfill(status=200, content_type="application/json", body=json.dumps(answer))

    page.route("**/polled", answered)
    return looks


def test_in_a_browser_a_poll_asks_less_while_nothing_changes_and_nothing_while_the_tab_is_hidden(chromium, store):
    """Every open job page and review screen asked at its fixed pace, hidden or not, the same answer or not. While the
    answer stays the same the wait grows by half, up to three times the pace, and a change starts again at the pace;
    a hidden tab asks nothing, and looks at once when it is shown again."""
    import time

    from tests.browser import browser_on, wait_until

    with browser_on(chromium, create_app(web_settings(), store, start_reader=False)) as (browser, base):
        page = browser.new_page()
        answer = {"state": "running", "done": 0}
        looks = _looks(page, answer)
        page.goto(base + "/help")
        page.evaluate(POLL, 200)
        wait_until(page, lambda: len(looks) >= 7, timeout=10)
        gaps = [b - a for a, b in zip(looks, looks[1:], strict=False)]
        assert gaps[0] < 0.4 and gaps[1] > gaps[0] and gaps[2] > gaps[1], gaps  # 0.2, 0.3, 0.45...
        assert all(0.5 < g < 1.0 for g in gaps[4:6]), gaps  # ... up to 0.6 s
        answer["done"] = 1  # a change: back to the pace
        seen = len(looks)
        wait_until(page, lambda: len(looks) >= seen + 2, timeout=5)
        assert looks[seen + 1] - looks[seen] < 0.4, looks

        page.evaluate(HIDDEN, True)
        hidden_at = time.monotonic()
        page.wait_for_timeout(2000)
        assert [t for t in looks if t > hidden_at + 0.1] == []  # (one already under way may end)
        page.evaluate(HIDDEN, False)
        shown_at = time.monotonic()
        wait_until(page, lambda: looks[-1] > shown_at, timeout=2)
        assert looks[-1] - shown_at < 0.4  # at once, not after the wait


def test_in_a_browser_a_poll_stops_once_the_laptop_asks_who_they_are_again(chromium, store):
    """A colleague on a laptop shared on the office network says who they are once; after AP Coder started again (a
    new key) their cookie is no longer good, and a look is sent on to "Who are you?": it kept asking every 0.8 s,
    answered by that page. It stops; the job page says so and stays (D-185), its link to that page comes back after
    it."""
    import time

    from tests.browser import browser_on, wait_until

    app = create_app(web_settings(), store, start_reader=False, address="0.0.0.0")

    async def colleague(scope, receive, send):  # the browser is on another computer of the office
        await app({**scope, "client": ("10.0.0.5", 50000)} if scope["type"] == "http" else scope, receive, send)

    from tests.web.test_server_browser import _job_running

    release = _job_running(app)  # (a file of the import queue being read, D-168)
    try:
        with browser_on(chromium, colleague) as (browser, base):
            page = browser.new_page()
            looks: list[float] = []
            page.on("request", lambda r: looks.append(time.monotonic()) if "/process/status" in r.url else None)
            page.goto(base + "/process")
            page.wait_for_url("**/who?next=*")
            page.fill("input[name=name]", "Robin")
            page.click("button[type=submit]")
            page.wait_for_url("**/process")
            wait_until(page, lambda: looks)
            page.context.clear_cookies()  # (as a new start's key makes of them)
            gone = time.monotonic()
            said = page.locator("#apc-session-ended")
            said.wait_for(timeout=6000)
            assert "AP Coder asks who you are again: say who you are." in said.inner_text()
            page.wait_for_timeout(2000)
            assert len([t for t in looks if t > gone]) <= 1, looks
            assert page.url.endswith("/process")
            page.click("#apc-session-ended .flash-act")
            page.wait_for_url("**/who?next=%2Fprocess")
    finally:
        release.set()


# The actions the server runs whole in their request, however long they take: a restore (a safety backup first, of a
# database of gigabytes), years of past coding taught, the imports of large lists, a finance hub run, the page
# reader's model loaded. Their forms are marked data-long: app.js waits for them (any other action gives up after five
# minutes, with "didn't answer"). Not the bulk approval: a job, answered within a few seconds (D-185).
LONG_ACTIONS = {
    "settings_data.html": ["/settings/data/restore"],
    "learning.html": ["/learning/history/teach"],
    "accounts.html": ["/accounts/{{ a.table }}/import", "/accounts/bank/import"],
    "vendors.html": ["/vendors/master/import"],
    "exports.html": ["/exports/register/import"],
    "purchase_orders.html": ["/purchase-orders/import"],
    "settings_jde.html": ["/settings/jde/gl-map/import", "/settings/jde/worksheet"],
    "settings_hub.html": ["/settings/hub/run"],
    "settings_reading.html": ["/settings/reading/load-model"],  # up to 5 minutes a try, after the long lane (B-1717)
}


def test_the_actions_the_server_runs_whole_are_waited_for_however_long_they_take():
    for name, actions in LONG_ACTIONS.items():
        text = (core.TEMPLATES_DIR / name).read_text(encoding="utf-8")
        for action in actions:
            forms = re.findall(r'<form[^>]*action="' + re.escape(action) + '"[^>]*>', text)
            assert forms and all(" data-long" in f for f in forms), (name, action, forms)
    queue = (core.TEMPLATES_DIR / "review.html").read_text(encoding="utf-8")
    bulk = re.findall(r'<form[^>]*action="/review/bulk-approve"[^>]*>', queue)
    assert bulk and all(" data-long" not in f for f in bulk), bulk  # (a job: its deadline is an action's)
    app_js = (core.STATIC_DIR / "js" / "app.js").read_text(encoding="utf-8")
    assert 'post(action, body, { ms: form.hasAttribute("data-long") ? LONG_MS : ACTION_MS })' in app_js
    assert "export const LONG_MS = 2 * 3600000;" in app_js  # (a deadline still: D-185)


def test_templates_escape_and_ui_helpers_are_safe():
    env = core.templates.env
    assert env.autoescape is True
    out = env.from_string("{{ x }} {{ ui.pill(x, 'ok') }}").render(x="<i>v</i>")
    assert "<i>" not in out and out.count("&lt;i&gt;v&lt;/i&gt;") == 2
    with pytest.raises(jinja2.UndefinedError):  # only the HTML builders listed in core are there
        env.from_string("{{ ui.document_text(x) }}").render(x="")


def test_in_a_browser_an_upload_whose_answer_never_comes_says_so_and_keeps_the_files(chromium, store, tmp_path):
    """B-1926: Import invoices sent its files by an XMLHttpRequest with no deadline (every other request has one,
    D-161): an answer that never came (the server busy or stopped once the files were in) left the button on
    "Uploading…" for good. Past LOOK_MS without the files moving on, or without the answer once they are sent, it says
    AP Coder didn't answer, and the files stay picked to send again."""
    from ap_coder.core.store import load_sample_setup
    from tests.browser import browser_on, wait_until

    load_sample_setup(store)  # (GL accounts: the upload is open)
    picked = tmp_path / "north_invoice.pdf"
    picked.write_bytes(b"%PDF-1.4 north")
    with browser_on(chromium, create_app(web_settings(), store, start_reader=False)) as (browser, base):
        page = browser.new_page()
        held: list = []
        page.route("**/process/upload", lambda route: held.append(route))  # never answered
        page.clock.install()
        page.goto(base + "/process")
        page.set_input_files("#upload-files", [str(picked)])
        page.click("#upload-go")
        wait_until(page, lambda: held)
        page.clock.run_for(31_000)
        page.wait_for_selector("#toasts .flash", timeout=5000)
        assert "AP Coder didn't answer" in page.locator("#toasts").inner_text()
        assert page.locator("#picked li").count() == 1 and page.locator("#upload-go").is_enabled()
