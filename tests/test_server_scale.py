"""The company server under load (D-111): several web worker processes, the background work in one process of its
own, the state they share kept in the database, LLM calls in parallel, and the GPU time cut."""

from __future__ import annotations

import asyncio
import socket
import threading
import time

import pytest

from ap_coder.ops import offline
from ap_coder.web import server


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --- B-1271: a port nothing listens on is free, whatever answers a connection to it --------------------------------


def test_a_free_port_is_not_taken_for_one_in_use_when_something_answers_every_connection(monkeypatch):
    monkeypatch.setenv(offline.SERVER_ENV, "1")
    # An office proxy or an endpoint agent that intercepts connections answers on any port, so asking "does something
    # answer there?" says yes for a port nothing on this computer listens on.
    monkeypatch.setattr(offline, "listening", lambda port, host="127.0.0.1": True)
    port = _free_port()
    assert server.port_refusal("127.0.0.1", port) == ""
    assert server.port_refusal("0.0.0.0", port) == ""
    with socket.socket() as taken:  # a program listening there is still found
        taken.bind(("0.0.0.0", 0))
        taken.listen()
        assert "in use" in server.port_refusal("0.0.0.0", taken.getsockname()[1])


def test_the_port_check_does_not_fail_on_a_computer_without_ipv6(monkeypatch):
    monkeypatch.setenv(offline.SERVER_ENV, "1")
    real = socket.socket

    def no_ipv6(family=socket.AF_INET, *args, **kwargs):
        if family == socket.AF_INET6:
            raise OSError(97, "Address family not supported by protocol")
        return real(family, *args, **kwargs)

    monkeypatch.setattr(socket, "socket", no_ipv6)
    assert server.port_refusal("::", _free_port()) == ""  # (uvicorn then says what is wrong itself)


# --- B-1272: a stop ends the server within 30 seconds, however long its requests take ------------------------------


def test_a_stop_ends_the_server_within_its_grace_even_with_a_long_request_under_way(monkeypatch):
    import httpx
    import uvicorn

    monkeypatch.setattr(server, "GRACEFUL_SECONDS", 1)
    assert server.server_config(object(), "127.0.0.1", 1, None, None, None).timeout_graceful_shutdown == 1
    started = threading.Event()

    async def slow_report(scope, receive, send):  # a report that takes minutes
        if scope["type"] != "http":
            return
        started.set()
        await asyncio.sleep(120)

    port = _free_port()
    config = server.server_config(slow_report, "127.0.0.1", port, None, None, None)
    config.lifespan = "off"
    running = uvicorn.Server(config)
    thread = threading.Thread(target=running.run, daemon=True)
    thread.start()
    while not running.started:
        time.sleep(0.05)
    asking = threading.Thread(target=lambda: _ask(httpx, port), daemon=True)
    asking.start()
    assert started.wait(10)
    t0 = time.monotonic()
    running.should_exit = True  # what SIGTERM, Ctrl+C and POST /shutdown do
    thread.join(20)
    assert not thread.is_alive() and time.monotonic() - t0 < 10


def _ask(httpx, port: int) -> None:
    try:
        httpx.get(f"http://127.0.0.1:{port}/", timeout=60, trust_env=False)
    except httpx.HTTPError:
        pass


def test_the_server_grace_is_at_most_30_seconds():
    config = server.server_config(object(), "127.0.0.1", 8501, None, None, None)
    assert config.timeout_graceful_shutdown is not None and config.timeout_graceful_shutdown <= 30
    assert config.access_log is False and config.server_header is False


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])
