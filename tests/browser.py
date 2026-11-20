"""The browser tests' dashboard: served on this computer for one test, opened in the module's Chromium.

A test file's ``browser`` fixture is ``with browser_on(chromium, app) as opened: yield opened``: (Chromium, base URL),
the app served on a free port of 127.0.0.1 for the test, and every page the test opened closed after it. The
module-scoped ``chromium`` fixture (tests/conftest.py) launches one Chromium per test file; each test launched its
own before (1-2 s each). Without Playwright and a Chromium the browser tests skip, unless ``AP_REQUIRE_BROWSER=1``
(CI's browser job), where they fail (``chromium_path``).

After an action, a test waits for the page to have done with it (``settle``: nothing under way, by the page's own
word) or for what it should show (``wait_until``, Playwright's ``wait_for_*``), never a fixed time (D-299)."""

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


# What a page says is at work (aria-busy, D-299): the review screen's form while an edit waits for its check or the
# check runs, a line action's button until its lines are drawn, a file being read, a slow page's link until it comes.
BUSY = '[aria-busy]:not([aria-busy="false"])'
BUSY_NOW = f"() => [...document.querySelectorAll('{BUSY}')].map((e) => e.outerHTML.slice(0, 80))"  # (what they are)
SETTLED, SETTLE_WAIT = 0.1, 15.0  # seconds: nothing under way this long is settled; waited for at most this


class Counted:
    """The tests' Chromium (the ``chromium`` fixture, tests/conftest.py): Playwright's ``Browser``, but every page
    opened in it has its requests under way counted, for ``settle``."""

    def __init__(self, chromium: Any) -> None:
        self._chromium = chromium

    def __getattr__(self, name: str) -> Any:
        return getattr(self._chromium, name)

    def new_context(self, **options: Any) -> Any:
        return _counting(self._chromium.new_context(**options))

    def new_page(self, **options: Any) -> Any:
        page = self._chromium.new_page(**options)
        _counting(page.context)
        return page


def _counting(context: Any) -> Any:
    under_way: set[Any] = set()
    context.on("request", lambda r: under_way.add(r))  # (Playwright marks a handler: a set's own method takes no mark)
    context.on("requestfinished", lambda r: under_way.discard(r))
    context.on("requestfailed", lambda r: under_way.discard(r))
    context.under_way = under_way
    return context


def _asked_by(request: Any, page: Any) -> bool:
    try:
        return request.frame.page is page
    except Exception:  # (a worker's request: no frame)
        return True


def settle(page: Any, wait: float = SETTLE_WAIT) -> None:
    """Until ``page`` has nothing under way, for ``SETTLED`` in a row: no request of its unanswered (counted from its
    opening: ``Counted``) and nothing on it saying it is at work (``BUSY``). After an action, before the next one or
    an assert on what the page drew: the review screen checks an edit 0.2-0.45 s after it and draws the answer,
    which a fixed wait outlasted only on a quick computer. Playwright's "networkidle" is no such wait: it comes once
    a page, so asked for again later it returns at once (B-2851). Fails, saying what was still under way, when the
    page has not settled within ``wait`` seconds."""
    page.wait_for_load_state("networkidle")  # the page's opening (once it has come, this returns at once)
    under_way = getattr(page.context, "under_way", set())
    deadline = time.monotonic() + wait
    since = None
    while True:
        busy = [r.url for r in list(under_way) if _asked_by(r, page)]
        if not busy:
            try:
                busy = page.evaluate(BUSY_NOW)
            except Exception as e:  # (the page being left or drawn again: under way too)
                busy = [str(e)]
        now = time.monotonic()
        if busy:
            since = None
        elif since is None:
            since = now
        elif now - since >= SETTLED:
            return
        assert now < deadline, f"the page has not settled in {wait} s: {busy} still under way"
        page.wait_for_timeout(20)
