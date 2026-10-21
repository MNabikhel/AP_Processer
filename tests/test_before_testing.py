"""Before the pilot tester starts testing (docs/BUGS.md, Sweep 4, "Before testing: navigation, settings and help"):
the menu fits a laptop, the getting-started checklist counts what it shows, Settings opens on what a tester changes,
Help is grouped by what the tester does, and nothing in the offline build talks about a cloud it doesn't use."""

from __future__ import annotations

import re
import shutil
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from ap_coder.core.config import Settings
from ap_coder.core.store import Store
from ap_coder.ops.demo import load_demo
from ap_coder.web.app import create_app
from ap_coder.web.pages import PAGES

from .conftest import chromium_path

PAGE = {"X-AP-Coder": "1"}
CORE = ["Review queue", "Import invoices", "Invoice directory", "Exports", "GL accounts & tax", "Settings", "Help"]
MORE = ["Month-end", "Sales tax", "Vendor statements", "Vendors", "Purchase orders", "Learning & accuracy", "Spend",
        "Insights", "Activity"]  # fmt: skip


def _settings() -> Settings:
    s = Settings()
    return replace(s, llm=replace(s.llm, provider="off"))


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "private" / "ap_coder.db")


@pytest.fixture
def client(store):
    return TestClient(create_app(_settings(), store, start_reader=False), base_url="http://localhost")


@pytest.fixture(scope="module")
def demo_db(tmp_path_factory):
    path = tmp_path_factory.mktemp("demo") / "private" / "ap_coder.db"
    load_demo(Store(path), Settings())
    return path


@pytest.fixture
def demo_store(demo_db, tmp_path):
    target = tmp_path / "demo" / "ap_coder.db"
    target.parent.mkdir(parents=True)
    shutil.copy(demo_db, target)
    return Store(target)


def _rail(text: str) -> str:
    return text.split('<aside class="rail"', 1)[1].split("</aside>", 1)[0]


# --- The menu (B-502, B-503) ------------------------------------------------------------------------------------------


def test_the_menu_shows_the_seven_core_pages_and_folds_the_rest_under_more(client):
    rail = _rail(client.get("/review").text)
    main, more = rail.split('id="nav-more"', 1)
    more, pinned = more.split("</details>", 1)
    labels = lambda html: re.findall(r'<span class="label">([^<]+)</span>', html)  # noqa: E731
    assert [label.replace("&amp;", "&") for label in labels(main)] == CORE[:5]
    assert [label.replace("&amp;", "&") for label in labels(more)] == ["More", *MORE]
    assert [label.replace("&amp;", "&") for label in labels(pinned)] == CORE[5:]
    assert "<details" in rail and 'id="nav-more" open' not in rail  # folded on a core page
    assert {p.title for p in PAGES} == set(CORE) | set(MORE)


def test_the_more_fold_opens_by_itself_on_one_of_its_pages(client):
    for path in ("/spend", "/activity", "/month-end"):
        rail = _rail(client.get(path).text)
        assert 'id="nav-more" open' in rail, path
        assert 'aria-current="page"' in rail.split('id="nav-more"', 1)[1].split("</details>", 1)[0], path


@pytest.fixture
def browser(demo_store):
    if chromium_path() is None:
        pytest.skip("Playwright with Chromium isn't installed")
    import socket
    import threading
    import time

    import uvicorn
    from playwright.sync_api import sync_playwright

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    app = create_app(_settings(), demo_store, start_reader=False)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    try:
        with sync_playwright() as p:
            chromium = p.chromium.launch(executable_path=chromium_path(), args=["--no-proxy-server"])
            yield chromium, f"http://127.0.0.1:{port}"
            chromium.close()
    finally:
        server.should_exit = True
        thread.join(5)


def _visible_in_window(page, selector: str) -> bool:
    """The element is inside the window and nothing (the menu's footer) is drawn over its middle."""
    return page.evaluate(
        """(sel) => { const el = document.querySelector(sel); const r = el.getBoundingClientRect();
           if (r.bottom > innerHeight || r.top < 0) return false;
           const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
           return !!hit && (hit === el || el.contains(hit)); }""",
        selector,
    )


@pytest.mark.parametrize("height", [768, 650])
def test_in_a_browser_settings_and_help_are_on_screen_on_a_laptop(browser, height):
    """At 1366 x 768 the menu scrolled under its sticky footer: Settings and Help were below the window (A2)."""
    chromium, base = browser
    page = chromium.new_page(viewport={"width": 1366, "height": height})
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/review")
    for href in ("/settings", "/help"):
        assert _visible_in_window(page, f'.rail a[href="{href}"]'), (height, href)
    page.click("#nav-more > summary")  # opened: still on screen, the core pages scroll above them
    for href in ("/settings", "/help"):
        assert _visible_in_window(page, f'.rail a[href="{href}"]'), (height, href)
    page.close()
    assert errors == []


def test_in_a_browser_the_more_fold_is_remembered(browser):
    chromium, base = browser
    page = chromium.new_page(viewport={"width": 1366, "height": 768})
    page.goto(base + "/review")
    assert not page.evaluate("document.getElementById('nav-more').open")
    page.click("#nav-more > summary")
    page.goto(base + "/exports")
    assert page.evaluate("document.getElementById('nav-more').open")  # opened stays open on the next page
    page.click("#nav-more > summary")
    page.goto(base + "/spend")  # a page under More opens it, without changing what was chosen
    assert page.evaluate("document.getElementById('nav-more').open")
    page.goto(base + "/review")
    assert not page.evaluate("document.getElementById('nav-more').open")
    page.close()


# --- Getting started: LM Studio first, a counter that counts what it shows (B-504, B-505) ----------------------------


def _step_rows(html: str) -> list[str]:
    return re.findall(r"<div class='apc-step'><span class='dot (\w+)'", html)


def _step_labels(html: str) -> list[str]:
    row = r"<div class='apc-step'><span class='dot \w+' aria-hidden='true'>[^<]*</span>(?:<a [^>]*>)?([^<]+)"
    return re.findall(row, html)


def test_the_welcome_checklist_counts_exactly_the_rows_it_shows_with_lm_studio_first(client):
    page = client.get("/review").text
    card = page.split("Getting started", 1)[1]
    rows = _step_rows(card)
    done = rows.count("ok")
    assert f"aria-label='{done}/{len(rows)}'" in card, rows
    labels = _step_labels(card)
    assert labels[0] == "LM Studio running" and "OvisOCR2" not in labels and "Your province" in labels
    assert "Developer" in card and "Start server" in card  # how to start it
    assert 'action="/settings/reading/check"' in card and "Check again" in card


def test_import_invoices_counts_the_same_rows(client):
    page = client.get("/process").text
    card = page.split("<h4>Setup</h4>", 1)[1].split("</section>", 1)[0]
    rows = _step_rows(card)
    assert f"{rows.count('ok')} of {len(rows)} done" in card
    assert _step_labels(card)[0] == "LM Studio running"


def test_the_province_counts_once_given(client, store):
    count = lambda html: int(re.search(r"aria-label='(\d+)/\d+'", html).group(1))  # noqa: E731
    before = count(client.get("/review").text)
    store.set_setting("our_province", "ON")
    assert count(client.get("/review").text) == before + 1


def test_check_again_from_the_welcome_page_comes_back_to_it(client):
    answer = client.post("/settings/reading/check", data={"back": "/review"}, headers=PAGE).json()
    assert answer["redirect"].startswith("/review")
    answer = client.post("/settings/reading/check", data={"back": "https://elsewhere.example/"}, headers=PAGE).json()
    assert answer["redirect"].startswith("/settings/reading")


def test_without_lm_studio_the_ai_reviewer_says_to_start_it_and_check_again(client):
    from ap_coder.ai import reading

    status = reading.reading_status(_settings(), None)
    line = status.reader(reading.REVIEWER)
    assert line.state == reading.WAITING and "Start server" in line.detail and "Check again" in line.detail
    assert "Waiting for LM Studio" in client.get("/settings/reading").text


def test_the_reading_queue_counts_only_invoices_still_to_approve(store, tmp_path):
    from ap_coder.core.store import APPROVED

    ids = []
    for name in ("a", "b"):
        pdf = tmp_path / f"{name}.pdf"
        pdf.write_bytes(b"%PDF-1.4 " + name.encode())
        coding = {"vendor_name": name, "invoice_number": "1", "grand_total": 1}
        ids.append(store.add_invoice(pdf, coding, {"issues": []}))
        store.queue_page_read(ids[-1], "new")
    with store._conn() as conn:
        conn.execute("UPDATE invoices SET status = ? WHERE id = ?", (APPROVED, ids[1]))
    assert store.page_reads_waiting() == 2  # the reader still reads both (its record against approved values)
    assert store.page_reads_waiting(still_open=True) == 1  # what the tester waits for
