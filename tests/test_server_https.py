"""The company server's network side (D-99): HTTPS, refusing plain HTTP in server mode, the proxy headers, the allowed
host names, the security headers, and the computer's own name looked up once (N-58)."""

from __future__ import annotations

import os
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ap_coder.core.store import Store
from ap_coder.ops import cli, offline
from ap_coder.web import security, server
from ap_coder.web.app import create_app

from .webclient import web_settings

FIXTURES = Path(__file__).parent / "fixtures"
CERT = FIXTURES / "TEST_ONLY_self_signed_localhost.cert.pem"  # a test-only certificate for localhost, never used else
KEY = FIXTURES / "TEST_ONLY_self_signed_localhost.key.pem"
ROOT = Path(__file__).resolve().parent.parent


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "private" / "ap_coder.db")


@pytest.fixture
def server_on(monkeypatch):
    monkeypatch.setenv(offline.SERVER_ENV, "1")


@pytest.fixture
def laptop(monkeypatch):
    monkeypatch.setenv(offline.SERVER_ENV, "0")  # (restored afterwards: --server sets it)
    monkeypatch.delenv("AP_ALLOWED_HOSTS", raising=False)


def _start(tmp_path, *args: str, env: dict[str, str] | None = None) -> tuple[subprocess.Popen, int]:
    port = free_port()
    (tmp_path / "empty.env").write_text("", encoding="utf-8")
    command = [sys.executable, "-m", "ap_coder", "--env-file", str(tmp_path / "empty.env"),
               "--db", str(tmp_path / "private" / "ap_coder.db"), "serve", "--host", "127.0.0.1",
               "--port", str(port), *args]  # fmt: skip
    environment = {**os.environ, "BROWSER": "none", **(env or {})}
    environment.pop(offline.SERVER_ENV, None)
    proc = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True)  # fmt: skip
    return proc, port


def _get(url: str, context: ssl.SSLContext | None = None, timeout: float = 60) -> tuple[int, dict[str, str]]:
    deadline = time.monotonic() + timeout
    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context))
    while True:
        try:
            with direct.open(url, timeout=2) as response:
                return response.status, {k.lower(): v for k, v in response.headers.items()}
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.25)


def _stop(proc: subprocess.Popen) -> str:
    proc.terminate()
    try:
        out, _ = proc.communicate(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
    return out or ""


# --- HTTPS and plain HTTP -----------------------------------------------------------------------------------------


def test_the_server_starts_on_https_with_a_certificate(tmp_path):
    proc, port = _start(tmp_path, "--server", "--certfile", str(CERT), "--keyfile", str(KEY))
    try:
        trusted = ssl.create_default_context(cafile=str(CERT))  # the test certificate, trusted for this test only
        status, headers = _get(f"https://localhost:{port}/healthz", trusted)
        assert status == 200
        assert headers.get("strict-transport-security", "").startswith("max-age=")
        with pytest.raises(OSError):  # plain HTTP on the same port is not answered as a page
            _get(f"http://127.0.0.1:{port}/healthz", timeout=1)
    finally:
        out = _stop(proc)
    assert f"https://127.0.0.1:{port}" in out, out


def test_the_certificate_can_come_from_the_environment(tmp_path):
    proc, port = _start(tmp_path, "--server", env={"AP_TLS_CERT": str(CERT), "AP_TLS_KEY": str(KEY)})
    try:
        status, _ = _get(f"https://localhost:{port}/healthz", ssl.create_default_context(cafile=str(CERT)))
        assert status == 200
    finally:
        _stop(proc)


def test_server_mode_without_a_certificate_refuses_to_start(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv(offline.SERVER_ENV, "0")  # (restored afterwards: --server sets it)
    monkeypatch.delenv("AP_TLS_CERT", raising=False)
    monkeypatch.delenv("AP_TLS_KEY", raising=False)
    started = []
    monkeypatch.setattr(server, "serve", lambda *a, **k: started.append(k) or 0)
    code = cli.main(["--db", str(tmp_path / "a.db"), "serve", "--server", "--port", str(free_port())])
    assert code != 0 and not started
    said = capsys.readouterr().err
    assert "HTTPS" in said and "--certfile" in said and "--insecure-http" in said


def test_half_a_certificate_or_a_missing_file_is_refused(monkeypatch, tmp_path):
    assert server.tls_refusal(True, str(CERT), None, False)  # the key is missing
    assert "not found" in server.tls_refusal(True, str(tmp_path / "nope.pem"), str(KEY), False)
    assert server.tls_refusal(True, str(CERT), str(KEY), False) == ""
    assert server.tls_refusal(True, None, None, True) == ""  # --insecure-http, for a first test
    assert server.tls_refusal(False, None, None, False) == ""  # the laptop: plain HTTP on this computer, as always


def test_insecure_http_starts_server_mode_on_plain_http_for_a_first_test(tmp_path):
    proc, port = _start(tmp_path, "--server", "--insecure-http")
    try:
        status, headers = _get(f"http://127.0.0.1:{port}/healthz")
        assert status == 200 and "strict-transport-security" not in headers  # never over plain HTTP
        assert headers.get("x-frame-options") == "DENY"
    finally:
        out = _stop(proc)
    assert "plain HTTP" in out, out


def test_no_browser_is_opened_in_server_mode(server_on):
    assert server.opens_browser(True) is False


def test_a_laptop_still_opens_the_browser(laptop):
    assert server.opens_browser(True) is True and server.opens_browser(False) is False


def test_server_mode_listens_on_the_network_unless_told_otherwise(server_on, monkeypatch):
    monkeypatch.delenv(offline.ADDRESS_ENV, raising=False)
    assert server.default_host() == "0.0.0.0"
    monkeypatch.setenv(offline.ADDRESS_ENV, "10.1.2.3")
    assert server.default_host() == "10.1.2.3"


# --- Proxy headers ------------------------------------------------------------------------------------------------


def test_proxy_headers_are_off_unless_a_proxy_is_named():
    assert server.uvicorn_options(None, None, None) == {"proxy_headers": False}
    on = server.uvicorn_options(None, None, "10.0.0.5")
    assert on == {"proxy_headers": True, "forwarded_allow_ips": "10.0.0.5"}
    tls = server.uvicorn_options(str(CERT), str(KEY), None)
    assert tls["ssl_certfile"] == str(CERT) and tls["ssl_keyfile"] == str(KEY)


def _echo_client(proxy: str | None) -> str:
    """What address the app sees for a request from this computer that says X-Forwarded-For: 203.0.113.9."""
    import uvicorn

    async def app(scope, receive, send):
        if scope["type"] != "http":
            return
        body = (scope.get("client") or ("", 0))[0].encode()
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": body})

    port = free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                            **server.uvicorn_options(None, None, proxy))  # fmt: skip
    running = uvicorn.Server(config)
    thread = threading.Thread(target=running.run, daemon=True)
    thread.start()
    try:
        for _ in range(200):
            if running.started:
                break
            time.sleep(0.05)
        request = urllib.request.Request(f"http://127.0.0.1:{port}/", headers={"X-Forwarded-For": "203.0.113.9"})
        direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with direct.open(request, timeout=5) as response:
            return response.read().decode()
    finally:
        running.should_exit = True
        thread.join(5)


def test_forwarded_for_is_ignored_by_default_even_from_this_computer(monkeypatch):
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    assert _echo_client(None) == "127.0.0.1"  # uvicorn alone would trust 127.0.0.1's X-Forwarded-For


def test_forwarded_for_is_ignored_from_another_address_than_the_proxy():
    assert _echo_client("10.9.9.9") == "127.0.0.1"


def test_forwarded_for_is_taken_from_the_configured_proxy():
    assert _echo_client("127.0.0.1") == "203.0.113.9"


def test_a_proxy_not_named_is_refused_on_the_server(store, server_on):
    """Behind IIS on the same server without --proxy, everyone would arrive from 127.0.0.1: the server's console."""
    client = TestClient(create_app(web_settings(), store), base_url="https://localhost", client=("127.0.0.1", 5000))
    refused = client.get("/healthz", headers={"X-Forwarded-For": "10.1.2.40"})
    assert refused.status_code == 400 and "--proxy" in refused.text
    assert client.get("/healthz").status_code == 200
    assert client.get("/healthz", headers={"X-Forwarded-For": "127.0.0.1"}).status_code == 200


def test_serve_proxy_flag_reaches_the_server(monkeypatch, tmp_path):
    monkeypatch.setenv(offline.SERVER_ENV, "0")  # (restored afterwards: --server sets it)
    seen = {}
    monkeypatch.setattr(server, "serve", lambda *a, **k: seen.update(k) or 0)
    cli.main(["--db", str(tmp_path / "a.db"), "serve", "--server", "--certfile", str(CERT), "--keyfile", str(KEY),
              "--proxy", "10.0.0.5", "--port", "8600"])  # fmt: skip
    assert seen["proxy"] == "10.0.0.5" and seen["certfile"] == str(CERT) and seen["keyfile"] == str(KEY)
    assert os.environ.get(offline.SERVER_ENV) == "1"  # --server turns the server mode on


# --- Allowed host names -------------------------------------------------------------------------------------------


def test_an_allowed_company_name_is_accepted_and_others_still_refused(store, monkeypatch, laptop):
    monkeypatch.setenv("AP_ALLOWED_HOSTS", " apcoder.company.local , 10.1.2.3 ")
    client = TestClient(create_app(web_settings(), store, address="127.0.0.1"), base_url="http://localhost")
    assert client.get("/healthz", headers={"Host": "apcoder.company.local:8501"}).status_code == 200
    assert client.get("/healthz", headers={"Host": "10.1.2.3"}).status_code == 200
    assert client.get("/healthz", headers={"Host": "APCODER.company.local."}).status_code == 200
    refused = client.get("/healthz", headers={"Host": "evil.example"})
    assert refused.status_code == 400
    assert client.get("/healthz", headers={"Host": "apcoder.company.local.evil.example"}).status_code == 400


def test_a_company_name_is_refused_when_not_listed(store, laptop):
    client = TestClient(create_app(web_settings(), store), base_url="http://localhost")
    assert client.get("/healthz", headers={"Host": "apcoder.company.local"}).status_code == 400


def test_the_server_says_how_to_allow_a_name_in_server_mode(store, server_on, monkeypatch):
    monkeypatch.delenv("AP_ALLOWED_HOSTS", raising=False)
    client = TestClient(create_app(web_settings(), store), base_url="https://localhost")
    refused = client.get("/healthz", headers={"Host": "apcoder.company.local"})
    assert refused.status_code == 400 and "AP_ALLOWED_HOSTS" in refused.text


def test_this_computers_name_is_looked_up_once_not_per_request(store, monkeypatch, laptop):
    """N-58: socket.getfqdn() is a DNS lookup; asked on every request, a slow DNS froze every page."""
    calls = []
    monkeypatch.setattr(security.socket, "getfqdn", lambda: calls.append(1) or "pc-12.company.local")
    monkeypatch.setattr(security.socket, "gethostname", lambda: "PC-12")
    security.computer_names.cache_clear()
    try:
        client = TestClient(create_app(web_settings(), store, address="0.0.0.0"), base_url="http://localhost")
        for _ in range(3):
            assert client.get("/healthz", headers={"Host": "pc-12.company.local"}).status_code == 200
            assert client.get("/healthz", headers={"Host": "pc-12"}).status_code == 200
            assert client.get("/healthz", headers={"Host": "other.example"}).status_code == 400
        assert len(calls) <= 1
    finally:
        security.computer_names.cache_clear()


# --- Security headers ---------------------------------------------------------------------------------------------


def test_server_mode_sends_the_security_headers(store, server_on):
    client = TestClient(create_app(web_settings(), store), base_url="https://localhost")
    headers = client.get("/healthz").headers
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "same-origin"
    assert headers["Strict-Transport-Security"].startswith("max-age=")
    csp = headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
    assert "'unsafe-inline'" not in csp.split("script-src", 1)[1].split(";", 1)[0]  # no inline scripts run


def test_no_hsts_over_plain_http(store, server_on):
    client = TestClient(create_app(web_settings(), store), base_url="http://localhost")
    assert "Strict-Transport-Security" not in client.get("/healthz").headers


def test_the_laptop_gets_the_harmless_headers_only(store, laptop):
    client = TestClient(create_app(web_settings(), store), base_url="http://localhost")
    headers = client.get("/process").headers
    assert headers["X-Frame-Options"] == "DENY" and headers["X-Content-Type-Options"] == "nosniff"
    assert "Content-Security-Policy" not in headers and "Strict-Transport-Security" not in headers


def test_no_page_runs_an_inline_script():
    """The Content-Security-Policy allows scripts from AP Coder's own files only: a <script> with code in a template
    would stop working on the server. (Data blocks, type="application/json", are not run, so they are fine.)"""
    import re

    templates = ROOT / "ap_coder" / "web" / "templates"
    found = []
    for path in templates.rglob("*.html"):
        for tag in re.findall(r"<script\b[^>]*>", path.read_text(encoding="utf-8")):
            if "src=" not in tag and "application/json" not in tag:
                found.append(f"{path.name}: {tag}")
        if re.search(r"\son[a-z]+\s*=\s*[\"']", path.read_text(encoding="utf-8")):
            found.append(f"{path.name}: an inline event handler")
    assert not found, found
