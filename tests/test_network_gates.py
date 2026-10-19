"""Where AP Coder could touch the network, and what keeps it on this computer (docs/HOW_IT_RUNS.md lists them all).

* The dashboard's own "is it up yet?" request (``web/server.py``) goes straight to 127.0.0.1, never through an office
  proxy: through one it never arrives, so the address was not printed and the browser not opened.
* The model servers (LM Studio for the chat model and OvisOCR2) are asked only on this computer or the office network;
  an address on the internet (``AP_LLM_BASE_URL`` / ``AP_PAGE_READER_BASE_URL``) only with ``AP_ALLOW_INTERNET=1``,
  like every other way out of the offline build.
"""

from __future__ import annotations

import http.server
import threading
import urllib.request

import pytest

from ap_coder.ai import local_llm, page_reader
from ap_coder.core.config import LocalLLMSettings, Settings
from ap_coder.web import server as web_server

REAL_FETCH = local_llm._fetch_json  # conftest replaces it for every test (no real LM Studio)


@pytest.fixture
def healthz():
    """A server on 127.0.0.1 that answers /healthz, as the dashboard does."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):  # noqa: N802
            self.send_response(200 if self.path == "/healthz" else 404)
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def office_proxy(monkeypatch):
    """An office proxy in the environment (as IT sets it), which is not running here: anything sent through it fails."""
    with socket_closed() as port:
        for name in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY"):
            monkeypatch.setenv(name, f"http://127.0.0.1:{port}")
        for name in ("no_proxy", "NO_PROXY"):
            monkeypatch.delenv(name, raising=False)
        yield


class socket_closed:  # noqa: N801 - a "with" block used like a function
    """A port nothing listens on."""

    def __enter__(self) -> int:
        import socket

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def __exit__(self, *exc) -> None:
        return None


def test_the_dashboard_says_it_is_up_even_behind_an_office_proxy(healthz, office_proxy, capsys):
    assert urllib.request.getproxies().get("http")  # (the proxy is really in effect for urllib)
    web_server._announce_when_up(healthz, open_browser=False, timeout=5.0)
    assert f"AP Coder is running at {healthz}" in capsys.readouterr().out


# --- The model servers: this computer or the office network only ------------------------------------------------


@pytest.fixture
def opened(monkeypatch):
    """Every request urllib would send, refused and recorded."""
    seen: list[str] = []

    class Opener:
        def open(self, request, timeout=None):
            seen.append(request.full_url if hasattr(request, "full_url") else str(request))
            raise ConnectionRefusedError("recorded, not sent")

    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: Opener())
    monkeypatch.setattr(local_llm, "_fetch_json", REAL_FETCH)
    local_llm.forget_status()
    return seen


def test_a_model_server_on_the_internet_is_not_asked_offline(opened):
    status = local_llm.check_server(LocalLLMSettings(base_url="https://models.example.com/v1"), use_cache=False)
    assert opened == [] and not status.reachable
    assert "AP_ALLOW_INTERNET" in status.error and "models.example.com" in status.error


def test_a_model_server_on_this_computer_or_the_office_network_is_asked(opened):
    for base in ("http://127.0.0.1:1234/v1", "http://192.168.1.20:1234/v1", "http://lmstudio-pc:1234/v1"):
        local_llm.check_server(LocalLLMSettings(base_url=base), use_cache=False)
    assert [url.rsplit("/v1", 1)[0] for url in opened if url.endswith("/v1/models")] == [
        "http://127.0.0.1:1234", "http://192.168.1.20:1234", "http://lmstudio-pc:1234"]  # fmt: skip


def test_a_model_server_on_the_internet_is_asked_with_the_opt_in(opened, monkeypatch):
    monkeypatch.setenv("AP_ALLOW_INTERNET", "1")
    local_llm.check_server(LocalLLMSettings(base_url="https://models.example.com/v1"), use_cache=False)
    assert opened and opened[0].startswith("https://models.example.com/v1")


def test_the_page_readers_own_requests_stay_off_the_internet_too(opened):
    with pytest.raises(page_reader.PageReaderError, match="AP_ALLOW_INTERNET"):
        page_reader._post_json("https://models.example.com/api/v1/models/load", {}, "", 5)
    assert opened == []


def test_the_chat_clients_never_reach_an_internet_server_offline():
    """The OpenAI client that codes a line or reads a page: offline, an internet address fails like a server that
    is not running (an APIConnectionError, handled everywhere), and nothing is sent."""
    import openai

    client = local_llm.build_local_client(LocalLLMSettings(base_url="https://models.example.com/v1"))
    with pytest.raises(openai.APIConnectionError) as caught:
        client.with_options(max_retries=0).models.list()
    assert "AP_ALLOW_INTERNET" in repr(caught.value.__cause__)
    settings = Settings(llm=LocalLLMSettings(base_url="https://models.example.com/v1"))
    reader_client = page_reader._client(settings)
    with pytest.raises(openai.APIConnectionError):
        reader_client.models.list()
