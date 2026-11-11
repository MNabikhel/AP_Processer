"""The browser tests' own harness (tests/browser.py): what a test leaves open is closed, and closing never hangs."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# A page that, as it leaves, sends what it has not saved with ``keepalive`` (invoice.js does, on ``pagehide``), and
# leaves on the answer of an action, as the review screen goes to the next invoice once an approval is answered.
LEAVING = textwrap.dedent("""
    import asyncio, sys, time
    sys.path.insert(0, sys.argv[2])
    from playwright.sync_api import sync_playwright
    from tests.browser import browser_on

    PAGE = (b"<html><body><script>addEventListener('pagehide', () => fetch('/check', {method: 'POST', "
            b"keepalive: true})); fetch('/approve', {method: 'POST'}).then((r) => r.json())"
            b".then((a) => location.assign(a.redirect));</script></body></html>")

    async def app(scope, receive, send):
        if scope["type"] != "http":
            return
        body = PAGE if scope["path"] == "/" else b"next" if scope["path"] == "/next" else b'{"redirect": "/next"}'
        if scope["path"] in ("/approve", "/check"):
            await asyncio.sleep(0.2)
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/html")]})
        await send({"type": "http.response.body", "body": body})

    with sync_playwright() as p:
        chromium = p.chromium.launch(executable_path=sys.argv[1], args=["--no-proxy-server"])
        for i in range(10):
            with browser_on(chromium, app) as (browser, base):
                page = browser.new_page()
                page.goto(base + "/")
                time.sleep(0.2 + i * 0.004)  # the test ends as the page leaves: its keepalive request under way
            print("closed", i, flush=True)
        chromium.close()
""")


def test_in_a_browser_a_test_ending_as_its_page_leaves_is_closed_without_hanging(tmp_path):
    """B-2314: tests/web/test_account_picker.py hung for good about one run in six, after a test that ends on a
    Ctrl+Enter approval: the page goes to the next invoice as the test ends, and as it leaves it sends its unsaved
    edit with ``keepalive``; Chromium never answers the closing of a browser context (``Target.disposeBrowserContext``,
    also a page's close) asked while such a request is under way. ``browser_on`` waits until the test's server has
    been quiet a moment (at most a few seconds) before it closes the pages."""
    from tests.conftest import chromium_path

    path = chromium_path()
    if path is None:
        pytest.skip("Playwright with Chromium isn't installed")
    script = tmp_path / "leaving.py"
    script.write_text(LEAVING, encoding="utf-8")
    child = subprocess.Popen([sys.executable, str(script), path, str(ROOT)], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, start_new_session=os.name != "nt")  # fmt: skip
    try:
        said, _ = child.communicate(timeout=90)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            os.killpg(child.pid, signal.SIGKILL)  # (with its Playwright and Chromium)
        else:
            child.kill()
        said, _ = child.communicate()
        pytest.fail(f"closing a test's pages hung:\n{said}")
    assert child.returncode == 0 and said.count("closed") == 10, said
