"""The dashboard as the pilot tester first meets it (docs/BUGS.md, Sweep 4, "Pilot walkthrough (UI)"): the first
steps, the review screen on a 1366 x 768 laptop, the keyboard, what each status means, and the messages after an
action."""

from __future__ import annotations

import re
import shutil
import sqlite3
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from ap_coder.core.config import Settings
from ap_coder.core.reference_data import UNASSIGNED
from ap_coder.core.store import REVIEW, Store, load_sample_setup
from ap_coder.ops.demo import load_demo
from ap_coder.web import invoice_view
from ap_coder.web.app import create_app

from .conftest import SAMPLES, chromium_path

PAGE = {"X-AP-Coder": "1"}
LAPTOP = {"width": 1366, "height": 768}


@pytest.fixture(scope="module")
def demo_db(tmp_path_factory):
    path = tmp_path_factory.mktemp("demo") / "private" / "ap_coder.db"
    load_demo(Store(path), Settings())
    return path


@pytest.fixture
def store(demo_db, tmp_path):
    target = tmp_path / "private" / "ap_coder.db"
    target.parent.mkdir(parents=True)
    shutil.copy(demo_db, target)
    return Store(target)


def _app(store: Store, **kwargs):
    s = Settings()
    return create_app(replace(s, llm=replace(s.llm, provider="off")), store, start_reader=False, **kwargs)


@pytest.fixture
def client(store):
    return TestClient(_app(store), base_url="http://localhost")


@pytest.fixture(autouse=True)
def me(monkeypatch):
    for module in ("ap_coder.web.core", "ap_coder.web.pages.invoice", "ap_coder.web.pages.review"):
        monkeypatch.setattr(f"{module}.reviewer", lambda: "Pat Tester", raising=False)
        monkeypatch.setattr(f"{module}.login", lambda: "ptester", raising=False)


# --- Settings ------------------------------------------------------------------------------------------------------


def test_the_needs_attention_threshold_label_is_one_sentence(client):
    """Settings → Review showed "Send to", "Needs attention" and "when the AI's confidence is below" on three lines:
    each piece of the label was its own row of the label's column."""
    page = client.get("/settings/review").text
    label = re.search(r'<label class="field st-wide">(.*?)<span class="st-range">', page, re.S).group(1)
    assert re.fullmatch(r"\s*<span>Send to <i>Needs attention</i> when the AI's confidence is below</span>\s*", label)


def test_getting_started_asks_for_your_province_without_counting_it(tmp_path, monkeypatch):
    """No step said where invoices are delivered to you: with no ship-to read, every invoice said "Province of supply
    unknown; tax regime cannot be verified" and the sales tax went unchecked, and nothing pointed at Settings →
    Review, where the province is set. The step is recommended, not counted (the ring stays as it was)."""
    from ap_coder.web.pages import process

    monkeypatch.setattr(process, "ocr_available", lambda: True)
    fresh = Store(tmp_path / "fresh" / "ap_coder.db")
    client = TestClient(_app(fresh), base_url="http://localhost")
    page = client.get("/review").text
    started = page.split("Getting started", 1)[1]
    assert "Your province" in started and "recommended" in started
    assert 'href="/settings/review">Your province' in started
    ring = re.search(r"aria-label='(\d+)/(\d+)'", page).groups()
    assert ring[1] == "6"  # counted as before (whatever else is done on this computer)
    fresh.set_setting("our_province", "ON")
    page = client.get("/review").text
    assert "Ontario" in page.split("Getting started", 1)[1]
    assert re.search(r"aria-label='(\d+)/(\d+)'", page).groups() == ring  # still not counted
    assert "Your province" in client.get("/process").text


# --- The review queue ---------------------------------------------------------------------------------------------


def _next_id(page: str) -> int | None:
    found = re.search(r'name="next_id" value="(\d+)"', page)
    return int(found.group(1)) if found else None


@pytest.mark.parametrize("action", ["park", "reject"])
def test_after_an_action_the_next_invoice_is_the_one_that_followed_on_screen(client, store, action):
    """Editing an invoice refreshes its confidence in the queue (the queue shows what the screen shows), which
    moved it down the queue's order while it was open: Approve then opened the invoice after its *new* place,
    skipping the ones between (on the pilot walk, from the 4th of 15 to the 8th)."""
    ids = [i["id"] for i in store.list_invoices(REVIEW)]
    assert len(ids) >= 4
    current, following = ids[1], ids[2]
    page = client.get(f"/review/{current}").text
    assert _next_id(page) == following
    store.refresh_confidence(current, 0.99, False)  # the edits made it the surest: last in the queue's order
    data = {"reason": "waiting for the PO", "next_id": str(following)}
    answer = client.post(f"/review/{current}/{action}", data=data, headers=PAGE).json()
    assert answer["ok"] and answer["redirect"].split("?")[0] == f"/review/{following}", answer


def test_approve_also_opens_the_invoice_that_followed_on_screen(client, store):
    from .test_web_invoice import form_of

    ids = [i["id"] for i in store.list_invoices(REVIEW)]
    ready = [i for i in ids if invoice_view.Draft.stored(store.get_invoice(i)["ai_output"]).lines]
    current = ready[0]
    following = ids[ids.index(current) + 1]
    assert _next_id(client.get(f"/review/{current}").text) == following
    store.refresh_confidence(current, 0.99, False)
    data = {**form_of(store, current), "override": "", "next_id": str(following), "why_done": "1"}
    answer = client.post(f"/review/{current}/approve", data=data, headers=PAGE).json()
    if not answer.get("ok"):  # a check to tick first: "Approve anyway"
        key = invoice_view.analyse(store, Settings(), store.reference_data(), store.get_invoice(current),
                                   invoice_view.Draft.stored(store.get_invoice(current)["ai_output"]),
                                   "Pat Tester").live["bar"]["override_key"]  # fmt: skip
        answer = client.post(f"/review/{current}/approve", data={**data, "override": key}, headers=PAGE).json()
    assert answer["ok"] and answer["redirect"].split("?")[0] == f"/review/{following}", answer


def test_the_last_invoice_offers_no_next_one_to_wrap_to(client, store):
    ids = [i["id"] for i in store.list_invoices(REVIEW)]
    assert _next_id(client.get(f"/review/{ids[-1]}").text) is None


# --- The review screen ---------------------------------------------------------------------------------------------


def test_check_messages_name_fields_in_plain_english():
    """ "Subtotal + tax = 62.00 but grand_total = 0.00" and "quantity x unit_price = 20.00": the code's field names."""
    assert invoice_view.readable("subtotal + tax = 62.00 but grand_total = 0.00") == (
        "Subtotal + tax = 62.00 but the total = 0.00"
    )
    assert invoice_view.readable("quantity x unit_price = 20.00 but amount = 40.00", cap=False) == (
        "quantity × unit price = 20.00 but amount = 40.00"
    )


def test_the_details_use_the_same_plain_words():
    from types import SimpleNamespace

    issue = SimpleNamespace(severity="error", code="TOTAL_MISMATCH", line_number=None,
                            message="subtotal + tax = 62.00 but grand_total = 0.00")  # fmt: skip
    report = SimpleNamespace(issues=[issue], requires_review=True, adjusted_confidence=0.2, review_threshold=0.85,
                             checks={})  # fmt: skip
    (detail, *_rest) = invoice_view.checks_details(report)
    assert "grand_total" not in detail["message"] and "the total" in detail["message"]


def test_each_field_says_how_sure_it_is_in_words():
    """Beside a field's label the form showed "✓ 97%" (likely), "! 51%" (check) and "⊗ check" (a failed check): the
    page's own legend says Verified, Likely, Check and Failed check, the form said none of them."""
    from ap_coder.capture.types import CaptureResult, FieldResult

    capture = CaptureResult(fields={
        "invoice_number": FieldResult("invoice_number", "A1", 0.995, "verified"),
        "po_number": FieldResult("po_number", "P9", 0.976, "likely"),
        "due_date": FieldResult("due_date", "2026-01-01", 0.51, "check"),
        "invoice_date": FieldResult("invoice_date", "2026-01-02", 0.4, "likely",
                                    reasons=["check failed: after the due date"]),
    })  # fmt: skip
    sure = invoice_view.capture_confidence(capture)
    assert sure["invoice_number"]["text"] == "Verified"
    assert sure["po_number"]["text"] == "Likely 97%"
    assert sure["due_date"]["text"] == "Check 51%"
    assert sure["invoice_date"]["text"] == "Failed check"
    assert all(s["help"] for s in sure.values())


def test_the_pick_hint_is_a_button_to_the_line_that_needs_it(client, store):
    """ "Pick a GL account for line 1 and a cost center for line 1 to approve" was a label: the line it means is far
    down the page (57 presses of Tab from the top on a 1366 x 768 laptop)."""
    from .test_web_invoice import form_of

    inv = store.list_invoices(REVIEW)[0]

    def unassign(rows):
        rows[0]["predicted_gl_code"] = UNASSIGNED
        return rows

    sections = client.post(f"/review/{inv['id']}/check", data=form_of(store, inv["id"], unassign),
                           headers=PAGE).json()["sections"]  # fmt: skip
    bar = sections["bar"]
    assert re.search(r'<button type="button" class="apc-pill warn inv-pick" data-goto-pick[^>]*>', bar), bar
    assert "Pick a GL account for line 1" in bar
    assert "G" in client.get(f"/review/{inv['id']}").text.split("Shortcuts", 1)[1].split("</table>", 1)[0]


def test_one_invoice_read_is_waiting_not_they_are(tmp_path):
    """ "1 invoice read and coded. They're waiting in the review queue." """
    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    app = _app(store)
    client = TestClient(app, base_url="http://localhost")
    stem = "harbourview_NS_HST_HPS-2026-0347"
    files = [("files", (f"{stem}.pdf", (SAMPLES / f"{stem}.pdf").read_bytes(), "application/pdf"))]
    assert client.post("/process/upload", files=files, headers=PAGE).json()["ok"]
    assert app.state.jobs.get("process").wait(180)
    texts = [m["text"] for m in client.get("/process/status").json()["result"]["messages"]]
    assert any(t.startswith("1 invoice read and coded. It's waiting in the review queue.") for t in texts), texts


# --- Master data and learning ------------------------------------------------------------------------------------


def test_one_category_is_not_one_categories(tmp_path):
    store = Store(tmp_path / "private" / "ap_coder.db")
    store.save_accounts("gl_accounts", [{"code": "6100", "description": "Office supplies", "category": "Expense"}])
    page = TestClient(_app(store), base_url="http://localhost").get("/accounts").text
    assert "1 category" in page and "1 categories" not in page


def test_a_line_picked_where_nothing_was_suggested_is_not_shown_as_unassigned(client, store):
    """Learning showed "Pat Tester corrected Line 2 UNASSIGNED → 6110": the code's word for a line without an
    account, struck through as if it had been a GL account."""
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            """INSERT INTO feedback (invoice_id, line_number, vendor_key, vendor_name, description, amount,
               suggested_gl, final_gl, suggested_cc, final_cc, outcome, reviewer, created_at)
               VALUES (1, 1, 'acme', 'Acme', 'Labour hours', 40, ?, '6110', '', 'CC200', 'corrected', 'Pat Tester',
               datetime('now'))""",
            (UNASSIGNED,),
        )
    page = client.get("/learning").text
    assert "UNASSIGNED" not in page and "no account" in page


# --- In a browser --------------------------------------------------------------------------------------------------


@pytest.fixture
def browser(store):
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
    server = uvicorn.Server(uvicorn.Config(_app(store), host="127.0.0.1", port=port, log_level="warning"))
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


def _open(browser, path, viewport=LAPTOP):
    chromium, base = browser
    page = chromium.new_page(viewport=viewport)
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + path)
    return page, errors


def _review(browser, store, viewport=LAPTOP):
    inv = store.list_invoices(REVIEW)[0]
    page, errors = _open(browser, f"/review/{inv['id']}", viewport)
    page.wait_for_selector(".iv-sheet:not(.loading)")
    return page, errors


def test_in_a_browser_the_shortcuts_list_is_not_hidden_behind_the_document(browser, store):
    """On a 1366 x 768 laptop the Document card was drawn over the Shortcuts list: "Next field to check" and "Stop
    teaching a field" were under it."""
    page, errors = _review(browser, store)
    page.click("summary:has-text('Shortcuts')")
    rows = page.locator(".rvw-nav .pop-body tr")
    for n in range(rows.count()):
        box = rows.nth(n).bounding_box()
        on_top = page.evaluate("([x, y]) => !!document.elementFromPoint(x, y).closest('.rvw-nav .pop-body')",
                               [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2])  # fmt: skip
        assert on_top, rows.nth(n).inner_text()
    assert errors == []


def test_in_a_browser_the_colour_key_shows_on_a_laptop(browser, store):
    """The key to the boxes' colours (Verified, Likely, Check, Failed check) was hidden below 1400 px or so: on the
    pilot's 1366 x 768 laptop, coloured boxes with nothing saying what the colours meant."""
    page, errors = _review(browser, store)
    legend = page.locator(".iv-legend")
    assert legend.is_visible() and "Verified" in legend.inner_text() and "Likely" in legend.inner_text()
    assert errors == []


def test_in_a_browser_approve_stays_at_the_right_while_a_line_needs_an_account(browser, store):
    """With "Pick a GL account for line 1…" in the approve bar, the bar wrapped and Park, Reject and Approve moved to
    its left edge; once picked they jumped back to the right."""
    page, errors = _review(browser, store)
    page.select_option("select[name=line_gl] >> nth=0", UNASSIGNED)
    page.wait_for_selector(".inv-pick")
    bar = page.locator(".inv-actionbar").bounding_box()
    approve = page.locator(".inv-actionbar-right").bounding_box()
    assert approve["x"] + approve["width"] > bar["x"] + bar["width"] - 60, (approve, bar)
    # the hint takes you to the line, and so does G
    page.click(".inv-pick")
    assert page.evaluate("document.activeElement.name") == "line_gl"
    page.locator("body").focus()
    page.evaluate("document.activeElement.blur()")
    page.keyboard.press("g")
    assert page.evaluate("document.activeElement.name") == "line_gl"
    assert errors == []


def test_in_a_browser_the_keyboard_focus_is_easy_to_see(browser, store):
    """A focused button or link had only a faint 18% blue glow (about 1.3:1 against the page): with the keyboard the
    tester couldn't tell where they were."""
    page, errors = _review(browser, store)
    page.focus("a[data-nav=next]")
    page.keyboard.press("Shift+Tab")
    page.keyboard.press("Tab")
    style = page.evaluate("(() => { const s = getComputedStyle(document.activeElement);"
                          " return [s.outlineStyle, parseFloat(s.outlineWidth)]; })()")  # fmt: skip
    assert style[0] == "solid" and style[1] >= 2, style
    assert errors == []


def test_in_a_browser_the_queue_gives_the_reason_room_on_a_wide_screen(browser, store):
    """At 1920 px the vendor column took 800 px and the reason under "Needs attention" was cut after 30 letters."""
    page, errors = _open(browser, "/review", {"width": 1920, "height": 1080})
    status = page.locator(".rq-row:not(.rq-cols) .c-status").first.bounding_box()
    assert status["width"] >= 220, status
    assert errors == []
