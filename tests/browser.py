"""The browser tests' dashboard: served on this computer for one test, opened in the module's Chromium.

A test file's ``browser`` fixture is ``with browser_on(chromium, app) as opened: yield opened``: (Chromium, base URL),
the app served on a free port of 127.0.0.1 for the test, and every page the test opened closed after it. The
module-scoped ``chromium`` fixture (tests/conftest.py) launches one Chromium per test file; each test launched its
own before (1-2 s each). Without Playwright and a Chromium the browser tests skip, unless ``AP_REQUIRE_BROWSER=1``
(CI's browser job), where they fail (``chromium_path``)."""

from __future__ import annotations

import contextlib
import socket
import threading
import time
from collections.abc import Iterator
from typing import Any


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


QUIET, QUIET_WAIT = 0.3, 5.0  # seconds: a server quiet this long has nothing under way; waited for at most this


def quiet(server: Any, seconds: float = QUIET, wait: float = QUIET_WAIT) -> None:
    """Until the uvicorn ``server`` has answered every request it got and none came for ``seconds`` (at most
    ``wait``): a page that left as its test ended may still send its unsaved edit with ``keepalive``, and Chromium
    never answers the closing of a browser context asked while such a request is under way (B-2314)."""
    deadline = time.monotonic() + wait
    seen, since = -1, time.monotonic()
    while time.monotonic() < deadline:
        state = server.server_state
        if state.tasks or state.total_requests != seen:
            seen, since = state.total_requests, time.monotonic()
        elif time.monotonic() - since >= seconds:
            return
        time.sleep(0.02)


@contextlib.contextmanager
def served(app: Any) -> Iterator[tuple[Any, str]]:
    """``app`` served by uvicorn on a free port of this computer, in a thread, while the block runs: (the server,
    its base URL)."""
    import uvicorn

    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.02)
    try:
        yield server, f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(5)


@contextlib.contextmanager
def browser_on(chromium: Any, app: Any) -> Iterator[tuple[Any, str]]:
    """(``chromium``, the base URL of ``app`` served for this test); the pages the test opened are closed after it,
    once its server is quiet (``quiet``)."""
    with served(app) as (server, base):
        try:
            yield chromium, base
        finally:
            quiet(server)
            for context in list(chromium.contexts):
                context.close()


def wait_until(page: Any, condition: Any, timeout: float = 5.0) -> None:
    """Until ``condition()`` holds, at most ``timeout`` seconds (the assert after it says what is wrong): the page acts
    in its own time, and a fixed wait was either longer than needed or too short on a slow runner. Polls with the page's
    own wait, so Playwright handles the page's events (requests recorded by a route) meanwhile."""
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        page.wait_for_timeout(50)
