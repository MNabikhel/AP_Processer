"""Screenshots of the dashboard's pages, for a person or a coding agent checking a screen's layout without a browser
of their own (the menu at each width: D-235). It serves the dashboard on a new temporary data folder, on this
computer only, opens each page in Chromium at each width, saves a full-page picture of it and prints what is past the
window's right edge (a page wider than its window scrolls sideways):

    python scripts/screens.py [PAGE ...] [--data empty|company|demo] [--widths 1366,911,683,390] [--out FOLDER]

PAGE: an address of the dashboard ("/jde-files", "/review?q=Acme"; default "/"). ``--data``: the new data folder as a
first start leaves it (the default); the made-up company (``ops/demo.load_company``: its JD Edwards files loaded,
JD Edwards set up and seven invoices to review; some 30 s); or the demo invoices (``load_demo``). The widths: a
1366-wide laptop at 100 %, 150 % and 200 % zoom (the page's width shrinks by the zoom), and a phone. ``--out``: where
the pictures go (default: a new temporary folder), named after the page and the width ("jde-files-911.png").

Chromium is Playwright's: one under PLAYWRIGHT_BROWSERS_PATH or /opt/pw-browsers, else the one Playwright installed
(as the browser tests find it: tests/conftest.py's ``chromium_path``). Nothing is downloaded: without one it says how
to install it and exits 2. Exit 1 when a page did not open (an error page), else 0.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
WIDTHS = (1366, 911, 683, 390)
HEIGHT = 800  # the window's height: each picture is the whole page however long
BROWSER_ROOTS = ("/opt/pw-browsers",)  # a Chromium installed outside Playwright's own folder
SHOWN = 8  # the elements past the edge printed for a page
NO_BROWSER = ("No Chromium for Playwright was found. Install it with: python -m pip install playwright, then "
              "python -m playwright install chromium (a download: run it yourself).")  # fmt: skip
# The page's width, and the elements whose right side is past the window's (none of a fixed bar): "tag#id.class:right".
PAST_EDGE = """() => {
  const w = innerWidth;
  const past = [...document.querySelectorAll('body *')].filter(e => {
    const r = e.getBoundingClientRect();
    return r.right > w + 1 && r.width > 0 && getComputedStyle(e).position !== 'fixed';
  });
  const named = e => e.tagName.toLowerCase() + (e.id ? '#' + e.id : '')
    + (typeof e.className === 'string' && e.className.trim() ? '.' + e.className.trim().split(/\\s+/).join('.') : '');
  return [document.documentElement.scrollWidth, past.length,
          past.slice(0, SHOWN).map(e => named(e) + ':' + Math.round(e.getBoundingClientRect().right))];
}""".replace("SHOWN", str(SHOWN))


def chromium_path(playwright: Any) -> str | None:
    """Playwright's Chromium: one under PLAYWRIGHT_BROWSERS_PATH or ``BROWSER_ROOTS``, else its own; None when none."""
    for root in (os.environ.get("PLAYWRIGHT_BROWSERS_PATH"), *BROWSER_ROOTS):
        for pattern in ("chromium-*/chrome-linux*/chrome", "chromium/chrome-linux*/chrome"):
            hits = sorted(Path(root).glob(pattern)) if root else []
            if hits:
                return str(hits[-1])
    path = playwright.chromium.executable_path
    return path if path and Path(path).is_file() else None


def picture_name(page: str, width: int) -> str:
    """The file of ``page``'s picture at ``width``: "jde-files-911.png", "home-390.png" for "/"."""
    name = re.sub(r"[^A-Za-z0-9]+", "-", page).strip("-") or "home"
    return f"{name}-{width}.png"


@contextlib.contextmanager
def served(app: Any) -> Iterator[str]:
    """``app`` served by uvicorn on a free port of 127.0.0.1, in a thread, while the block runs: its base URL."""
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.02)
    if not server.started:
        raise RuntimeError("The dashboard did not start.")
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(5)


def dashboard(data: Path, kind: str) -> Any:
    """The dashboard on a new database in ``data``, as a first start leaves it, with the made-up company or the demo
    (``kind``); no model is asked, no page reader runs."""
    os.environ["AP_PRIVATE_DIR"] = str(data)
    os.environ["AP_DB_PATH"] = str(data / "ap_coder.db")
    sys.path.insert(0, str(ROOT))
    from dataclasses import replace

    from ap_coder.core.config import Settings
    from ap_coder.ops import demo
    from ap_coder.web.app import create_app
    from ap_coder.web.core import first_run

    settings = Settings()
    settings = replace(settings, llm=replace(settings.llm, provider="off"))
    store = first_run(data / "ap_coder.db", server=False)
    if kind == "company":
        demo.load_company(store, settings)
    elif kind == "demo":
        demo.load_demo(store, settings)
    return create_app(settings, store, start_reader=False, background=False)


def shoot(browser: Any, base: str, pages: list[str], widths: list[int], out: Path) -> int:
    """Each of ``pages`` at each of ``widths`` saved in ``out``, what is past the right edge printed; how many pages
    did not open."""
    failed = 0
    for width in widths:
        window = browser.new_page(viewport={"width": width, "height": HEIGHT}, device_scale_factor=1)
        for page in pages:
            answer = window.goto(base + page)
            window.wait_for_load_state("networkidle")
            picture = out / picture_name(page, width)
            window.screenshot(path=str(picture), full_page=True)
            wide, count, past = window.evaluate(PAST_EDGE)
            status = answer.status if answer is not None else 0
            if status >= 400:
                failed += 1
            said = f"{picture.name}: {page} answered {status}; the page is {wide} px wide in a {width} px window"
            more = f" and {count - len(past)} more" if count > len(past) else ""
            print(said + (f"; past its right edge: {', '.join(past)}{more}" if past else "; nothing past its edge"))
        window.close()
    return failed


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("pages", nargs="*", default=["/"], help='addresses of the dashboard (default "/")')
    p.add_argument("--data", choices=("empty", "company", "demo"), default="empty")
    p.add_argument("--widths", default=",".join(map(str, WIDTHS)), help="window widths in px, commas between")
    p.add_argument("--out", type=Path, help="where the pictures go (default: a new temporary folder)")
    args = p.parse_args(argv)
    widths = [int(w) for w in args.widths.split(",") if w.strip()]
    pages = [page if page.startswith("/") else "/" + page for page in args.pages]
    if importlib.util.find_spec("playwright") is None:
        print(NO_BROWSER)
        return 2
    from playwright.sync_api import sync_playwright

    out = args.out or Path(tempfile.mkdtemp(prefix="ap-screens-"))
    out.mkdir(parents=True, exist_ok=True)
    data = Path(tempfile.mkdtemp(prefix="ap-screens-data-"))
    try:
        app = dashboard(data, args.data)
        with sync_playwright() as playwright, served(app) as base:
            path = chromium_path(playwright)
            if path is None:
                print(NO_BROWSER)
                return 2
            browser = playwright.chromium.launch(executable_path=path)
            try:
                failed = shoot(browser, base, pages, widths, out)
            finally:
                browser.close()
    finally:
        shutil.rmtree(data, ignore_errors=True)
    print(f"Pictures in {out}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
