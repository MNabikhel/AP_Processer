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


@contextlib.contextmanager
def served(app: Any) -> Iterator[str]:
    """``app`` served by uvicorn on a free port of this computer, in a thread, while the block runs: its base URL."""
    import uvicorn

    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(5)


@contextlib.contextmanager
def browser_on(chromium: Any, app: Any) -> Iterator[tuple[Any, str]]:
    """(``chromium``, the base URL of ``app`` served for this test); the pages the test opened are closed after it."""
    with served(app) as base:
        try:
            yield chromium, base
        finally:
            for context in list(chromium.contexts):
                context.close()


def wait_until(page: Any, condition: Any, timeout: float = 5.0) -> None:
    """Until ``condition()`` holds, at most ``timeout`` seconds (the assert after it says what is wrong): the page acts
    in its own time, and a fixed wait was either longer than needed or too short on a slow runner. Polls with the page's
    own wait, so Playwright handles the page's events (requests recorded by a route) meanwhile."""
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        page.wait_for_timeout(50)
