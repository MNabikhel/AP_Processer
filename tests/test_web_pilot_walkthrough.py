"""The dashboard as the pilot tester first meets it (docs/BUGS.md, Sweep 4, "Pilot walkthrough (UI)"): the first
steps, the review screen on a 1366 x 768 laptop, the keyboard, what each status means, and the messages after an
action."""

from __future__ import annotations

import html
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
from ap_coder.web.pages import invoice as _invoice_page  # noqa: F401 - imported before `me` patches web.core (below)
from ap_coder.web.pages import review as _review_page  # noqa: F401

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
    """A fixed reviewer. The page modules are imported at the top: patched before them, ``web.core.reviewer`` would be
    copied into them patched, and monkeypatch would keep the patched one as theirs for the rest of the run."""
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


def test_getting_started_asks_for_your_province_and_counts_it(tmp_path, monkeypatch):
    """No step said where invoices are delivered to you: with no ship-to read, every invoice said "Province of supply
    unknown; tax regime cannot be verified" and the sales tax went unchecked, and nothing pointed at Settings →
    Review, where the province is set. The step counts like the others (B-503: the ring counts every row shown)."""
    from ap_coder.web.pages import process

    monkeypatch.setattr(process, "ocr_available", lambda: True)
    fresh = Store(tmp_path / "fresh" / "ap_coder.db")
    client = TestClient(_app(fresh), base_url="http://localhost")
    page = client.get("/review").text
    started = page.split("Getting started", 1)[1]
    assert "Your province" in started and "Settings → Review" in started
    assert 'href="/settings/review">Your province' in started
    ring = re.search(r"aria-label='(\d+)/(\d+)'", page).groups()
    assert ring[1] == "8"  # with your bank accounts (optional, D-66)
    fresh.set_setting("our_province", "ON")
    page = client.get("/review").text
    assert "Ontario" in page.split("Getting started", 1)[1]
    assert re.search(r"aria-label='(\d+)/(\d+)'", page).groups() == (str(int(ring[0]) + 1), "8")
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
        answer = client.post(
            f"/review/{current}/approve",
            data={**data, "override": key, "override_reason": "checked with the buyer"},
            headers=PAGE,
        ).json()
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
    assert any(t.startswith("1 invoice read and coded. It's ready to review in the review queue.") for t in texts), (
        texts
    )


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
    assert page.evaluate("document.activeElement.dataset.pickerName") == "line_gl"
    page.locator("body").focus()
    page.evaluate("document.activeElement.blur()")
    page.keyboard.press("g")
    assert page.evaluate("document.activeElement.dataset.pickerName") == "line_gl"
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


# --- Second round: what the walk left (N-136 to N-141) and the attention list -------------------------------------


def test_the_way_to_skipping_review_is_said_in_plain_words():
    """ "about 34 more clean invoices to go touchless": *touchless* is explained nowhere the tester sees it."""
    from ap_coder.capture.supplier import to_go_text

    assert to_go_text(34) == "about 34 more invoices approved without a correction before this vendor can skip review"
    assert to_go_text(1) == "about 1 more invoice approved without a correction before this vendor can skip review"
    for n in (None, 0, 1, 34):
        assert "touchless" not in to_go_text(n)


def test_a_text_file_without_amounts_is_not_read_as_an_invoice(tmp_path):
    """A notes file uploaded by mistake ("hello") became an invoice from "hello" in the review queue."""
    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    app = _app(store)
    client = TestClient(app, base_url="http://localhost")
    assert ".txt" in client.get("/process").text.split('id="dropzone"', 1)[1].split("</small>", 1)[0]
    files = [("files", ("notes.txt", b"hello", "text/plain"))]
    answer = client.post("/process/upload", files=files, headers=PAGE).json()
    if answer.get("job"):
        assert app.state.jobs.get("process").wait(60)
        texts = [m["text"] for m in client.get("/process/status").json()["result"]["messages"]]
    else:
        texts = [answer.get("error", "")]
    assert any("notes.txt was not read" in t and "amount" in t for t in texts), texts
    assert store.list_invoices(REVIEW) == []


def test_a_foreign_invoice_says_outside_canada_in_its_header(client, store):
    """A US invoice's header said "Province unknown" while its Place of supply said "Outside Canada"."""
    from .test_web_invoice import form_of

    inv = store.list_invoices(REVIEW)[0]
    data = form_of(store, inv["id"], ship_to_province="OUTSIDE_CANADA", supplier_province="OUTSIDE_CANADA")
    head = client.post(f"/review/{inv['id']}/check", data=data, headers=PAGE).json()["sections"]["summary"]
    assert "Outside Canada" in head and "Province unknown" not in head, head


def test_early_coding_accuracy_is_a_plain_note_not_a_red_figure(client, store):
    """With no chat model every line starts without an account, so the first approvals read "Coding accuracy 28.6%,
    -61.4 pts vs 90% target" in red: true by D-39, alarming on the first day."""
    from .test_web_invoice import form_of

    inv = store.list_invoices(REVIEW)[0]

    def recode(rows):
        for r in rows:
            r["predicted_gl_code"] = "6900" if r.get("predicted_gl_code") != "6900" else "6000"
        return rows

    data = {**form_of(store, inv["id"], recode), "why_done": "1"}
    answer = client.post(f"/review/{inv['id']}/approve", data=data, headers=PAGE).json()
    if not answer.get("ok"):  # a check to tick first: "Approve anyway"
        client.post(f"/review/{inv['id']}/check", data=data, headers=PAGE)
        key = re.search(r'name="override" value="([^"]+)"', client.get(f"/review/{inv['id']}").text).group(1)
        answer = client.post(
            f"/review/{inv['id']}/approve",
            data={**data, "override": key, "override_reason": "checked with the buyer"},
            headers=PAGE,
        ).json()
    assert answer["ok"], answer
    assert (store.metrics()["line_accuracy"] or 0) < 0.9
    note = "GL suggestions improve as you approve; with the AI model on they start sooner"
    for url in ("/review", "/learning"):
        page = client.get(url).text
        assert note in page and "pts vs 90% target" not in page and " pts" not in page, url


def test_each_line_without_an_account_in_the_checks_goes_to_its_line(client, store):
    """ "Line 2: no GL account yet: pick one" in the checks was text: the line is two screens down."""
    from .test_web_invoice import form_of

    inv = next(
        i for i in store.list_invoices(REVIEW)
        if len(invoice_view.Draft.stored(store.get_invoice(i["id"])["ai_output"]).lines) >= 2
    )  # fmt: skip

    def unassign(rows):
        rows[1]["predicted_gl_code"] = UNASSIGNED
        return rows

    data = form_of(store, inv["id"], unassign)
    checks = client.post(f"/review/{inv['id']}/check", data=data, headers=PAGE).json()["sections"]["checks"]
    found = re.search(r'<button type="button" class="rvw-goto" data-goto-line="line_gl" data-line="2"', checks)
    assert found, checks


def test_with_lm_studio_off_the_review_screen_says_it_once(client, store):
    """The banner said LM Studio isn't running, then two notes under the document title said it again in other
    words (OvisOCR2 isn't running…; The AI reviewer checks this invoice when the chat model is free…): on a 1366 x 768
    laptop the invoice's picture started at the bottom of the screen."""
    inv = store.list_invoices(REVIEW)[0]
    page = client.get(f"/review/{inv['id']}").text
    # B-467: no banner on the review screen (the queue has it), one short note in its top row
    assert '<div class="banner' not in page
    # D-76: said once, folded under "Reading" in the Document card (with a dot on it), not in the clerk's way
    note = html.unescape(page.split("data-doc-more", 1)[1].split("</details>", 1)[0])
    assert "LM Studio isn't running" in note and "a person approves it" in note
    assert "OvisOCR2 isn't running: see Settings" not in page
    assert "checks this invoice against its pages when the chat model is free" not in page
    assert "OvisOCR2 isn't running" not in client.get(f"/review/{inv['id']}/reader").json()["html"]
    assert "LM Studio isn't running: invoices are read by OCR only" in html.unescape(client.get("/review").text)


def test_in_a_browser_report_a_problem_shows_it_is_working(browser, store):
    """Help → Report a problem took about 3 s the first time (it gathers the setup report) with nothing on the screen
    saying so. The page now opens at once and fetches the setup report after (B-520): its box says it is gathering
    it, then holds it."""
    page, errors = _open(browser, "/help/report")
    box = page.locator("#setup-report")
    assert box.get_attribute("placeholder") and "Gathering" in box.get_attribute("placeholder")
    page.wait_for_function("() => document.getElementById('setup-report').value.includes('doctor report')")
    assert errors == []


def test_in_a_browser_a_line_in_the_checks_takes_you_to_it(browser, store):
    page, errors = _review(browser, store)
    page.select_option("select[name=line_gl] >> nth=0", UNASSIGNED)
    page.wait_for_selector("[data-goto-line]")
    page.click("[data-goto-line] >> nth=0")
    assert page.evaluate("document.activeElement.dataset.pickerName") == "line_gl"
    assert errors == []


def test_in_a_browser_with_lm_studio_off_the_invoice_shows_above_the_fold(browser, store):
    page, errors = _review(browser, store)
    top = page.locator(".iv-sheet").first.bounding_box()["y"]
    bar = page.locator(".inv-actionbar").bounding_box()["y"]
    assert bar - top >= 150, (top, bar)  # a good part of the page shows above the approve bar
    assert errors == []


def test_in_a_browser_at_1366_the_page_and_the_line_grid_show_together(browser, store):
    """B-467: at 1366 x 768 only 150-200 px of the invoice's page showed, the line grid started about 1,200 px down
    and its Qty and Unit price scrolled sideways. D-63: the invoice details come first (the header is checked against
    the page, then the lines coded), the lines right after them, a scroll away, with the page still beside them."""
    page, errors = _review(browser, store)
    sheet = page.locator(".iv-sheet").first.bounding_box()
    bar = page.locator(".inv-actionbar").bounding_box()["y"]
    assert bar - sheet["y"] >= 300, (sheet, bar)
    details = page.locator(".inv-details").bounding_box()
    assert details["y"] < 768, details  # beside the page, under the checks
    grid = page.locator("#lines").bounding_box()
    assert details["y"] + details["height"] <= grid["y"], (details, grid)  # the details, then the lines
    order = page.evaluate(
        "[...document.querySelectorAll('.inv-split > .col > *')].map((e) => e.className + ' ' + (e.dataset.live || ''))"
    )
    kinds = [next((k for k in ("inv-checks", "inv-details", "inv-lines", "po", "suggest", "inv-notes") if k in c), c)
             for c in order]  # fmt: skip
    assert kinds[:5] == ["inv-checks", "inv-details", "inv-lines", "po", "suggest"], order
    # The lines are a scroll away, and the page stays beside them (sticky)
    page.evaluate("document.querySelector('#lines').scrollIntoView({block: 'start'})")
    page.wait_for_timeout(300)
    grid = page.locator("#lines").bounding_box()
    doc = page.locator(".inv-doc").bounding_box()
    assert -2 <= grid["y"] < 768 - 100, grid
    assert doc["x"] + doc["width"] <= grid["x"], (doc, grid)  # beside them
    assert doc["y"] <= max(grid["y"], 8) + 1 and doc["y"] + doc["height"] >= grid["y"] + grid["height"], (doc, grid)
    # Tab goes from the last invoice detail to the lines, nothing in between but the details' own
    page.focus("[name=grand_total]")
    seen = []
    for _ in range(15):
        page.keyboard.press("Tab")
        where = page.evaluate(
            "(() => { const e = document.activeElement; return e.closest('.inv-lines') ? 'lines'"
            " : e.closest('.inv-details') ? 'details' : (e.name || e.tagName); })()"
        )
        seen.append(where)
        if where == "lines":
            break
    assert seen[-1] == "lines" and set(seen[:-1]) <= {"details"}, seen
    page.evaluate("window.scrollTo(0, 0)")
    wrap = page.evaluate(
        "(() => { const w = document.querySelector('#lines').closest('.table-wrap');"
        " return [w.scrollWidth, w.clientWidth, document.documentElement.scrollWidth]; })()"
    )
    assert wrap[0] <= wrap[1] and wrap[2] <= 1366, wrap  # nothing scrolls sideways
    for name in ("line_quantity", "line_unit_price", "line_amount"):
        box = page.locator(f"[name={name}] >> nth=0").bounding_box()
        assert box["x"] + box["width"] <= grid["x"] + grid["width"] + 1, (name, box, grid)
    page.mouse.move(900, 400)
    page.mouse.wheel(0, 1200)
    page.wait_for_timeout(300)
    assert page.locator(".inv-doc").bounding_box()["y"] >= 0  # the page stays in view (sticky)
    assert not page.locator(".banner").count()  # LM Studio's banner is on the queue, not here
    assert errors == []


def test_in_a_browser_tab_follows_the_line_grid_as_it_shows_and_the_page_view_ends_above_the_bar(browser, store):
    """B-478: Tab went description → amount → GL → taxes → qty → unit price, across the two rows on screen; in Fit
    mode the bottom 80-170 px of the page view sat under the approve bar; an amount of "-199.98" was cut off."""
    page, errors = _review(browser, store)
    page.click("textarea[name=line_description] >> nth=0")
    seen = []
    for _ in range(12):
        box = page.evaluate(
            "(() => { const e = document.activeElement; const r = e.closest('td').getBoundingClientRect();"
            " return [e.dataset.pickerName || e.name || e.getAttribute('aria-label') || e.tagName, r.top, r.left,"
            " e.closest('tr') === document.querySelector('[data-grid=lines] tr')]; })()"
        )
        if not box[3]:
            break
        seen.append(box)
        page.keyboard.press("Tab")
    names = [b[0] for b in seen if b[0] != "line_cc"]  # the demo uses cost centers: beside the GL account
    assert names[:4] == ["line_description", "Delete this line", "line_gl", "line_amount"], names
    assert names[-2:] == ["line_quantity", "line_unit_price"], names
    rows = [round(b[1] / 20) for b in seen]  # rows on screen, then left to right in each
    assert all(
        a < b or (a == b and seen[i][2] <= seen[i + 1][2]) for i, (a, b) in enumerate(zip(rows, rows[1:], strict=False))
    )
    for scroll in (0, 400, 1200):  # at the top, rising, stuck: the page view always ends above the bar
        page.evaluate(f"window.scrollTo(0, {scroll})")
        page.wait_for_timeout(250)
        viewer = page.locator("#iv").bounding_box()
        bar = page.locator(".inv-actionbar").bounding_box()["y"]
        assert viewer["y"] + viewer["height"] <= bar + 1, (scroll, viewer, bar)
    assert viewer["height"] >= 520, viewer  # stuck: taller
    amount = page.locator("[name=line_amount] >> nth=0")
    amount.fill("-199998.98")
    assert page.evaluate("(e) => e.scrollWidth <= e.clientWidth", amount.element_handle())
    gl = page.locator("[name=line_gl] >> nth=0").bounding_box()
    assert gl["width"] >= 220, gl  # room for the account's name (the demo has cost centers beside it)
    assert errors == []


# --- Third round: the review's findings -------------------------------------------------------------------------

TEXT_INVOICE = (
    "INVOICE\nAcme Supply Ltd\nInvoice No: A-1001\nDate: 2026-10-01\nSubtotal: 100.00\nGST 5%: 5.00\nTotal: 105.00\n"
)


@pytest.mark.parametrize(
    "content",
    [
        TEXT_INVOICE.encode("utf-16"),  # Notepad's "Unicode": read on purpose by extraction.result_from_text
        TEXT_INVOICE.replace("100.00", "$100").replace("5.00", "$5").replace("105.00", "$105").encode(),
        b"INVOICE\nAcme Supply Ltd\nInvoice No: A-1003\nTotal 1,250\n",
        TEXT_INVOICE.encode("cp1252"),
    ],
    ids=["utf-16", "whole-dollars", "thousands", "ansi"],
)
def test_a_real_text_invoice_is_still_read(tmp_path, content):
    """B-439 turned away text invoices it should read: Notepad's UTF-16 (the raw bytes have a zero between every
    digit) and amounts in whole dollars ("Total: $105", "Total 1,250")."""
    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    app = _app(store)
    client = TestClient(app, base_url="http://localhost")
    files = [("files", ("invoice.txt", content, "text/plain"))]
    answer = client.post("/process/upload", files=files, headers=PAGE).json()
    assert answer.get("job"), answer
    assert app.state.jobs.get("process").wait(60)
    texts = [m["text"] for m in client.get("/process/status").json()["result"]["messages"]]
    assert not any("was not read" in t for t in texts), texts
    assert len(store.list_invoices(REVIEW)) == 1


def test_a_markdown_file_without_amounts_is_refused_as_a_text_file(tmp_path):
    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    app = _app(store)
    client = TestClient(app, base_url="http://localhost")
    files = [("files", ("README.md", b"# Notes\nCall the vendor on Monday.\n", "text/markdown"))]
    answer = client.post("/process/upload", files=files, headers=PAGE).json()
    if answer.get("job"):
        assert app.state.jobs.get("process").wait(60)
        texts = [m["text"] for m in client.get("/process/status").json()["result"]["messages"]]
    else:
        texts = [answer.get("error", "")]
    assert any("README.md was not read" in t and "(.txt, .md)" in t for t in texts), texts
    assert store.list_invoices(REVIEW) == []


@pytest.mark.parametrize(
    ("ready", "lines", "note", "compared"),
    [
        (False, 7, "GL suggestions improve as you approve; with the AI model on they start sooner", False),
        (True, 7, "GL suggestions improve as you approve", False),
        (False, 60, "GL suggestions improve as you approve", True),  # past the first days: the figure, plainly
        (True, 60, "", True),
    ],
)
def test_the_accuracy_note_gives_way_to_the_figure_after_the_first_lines(monkeypatch, ready, lines, note, compared):
    """B-442 showed the note for good without a chat model: the figure was never compared with the target."""
    from types import SimpleNamespace

    from ap_coder.ai import local_llm
    from ap_coder.web import core

    monkeypatch.setattr(local_llm, "provider_status", lambda settings: SimpleNamespace(ready=ready))
    assert core.accuracy_note(Settings(), lines) == (note, compared)


def test_supplier_learning_and_automation_use_the_same_plain_words(tmp_path, ground_truth, monkeypatch):
    """B-438 changed the message after an approval; Learning → Supplier learning and Settings → Automation still said
    "6 more clean in a row needed", "about 32 more clean invoices for a 99% accuracy bound", "Goes touchless on its
    next invoice", "to skip review yet again"."""
    store = Store(tmp_path / "private" / "ap_coder.db")
    client = TestClient(_app(store), base_url="http://localhost")
    ids = [store.add_invoice(tmp_path / f"inv{i}.pdf", ground_truth, {"requires_review": False}) for i in range(42)]

    def outcomes(bad=False):
        return [{"field": f"f{f}", "ai_value": "a", "final_value": "b" if bad and f == 0 else "a",
                 "correct": not (bad and f == 0)} for f in range(13)]  # fmt: skip

    for i, invoice_id in enumerate(ids[:30]):
        store.record_outcomes("id:V1", invoice_id, outcomes(), display_name="Northwind", at=f"2026-01-01T00:00:{i:02d}")
    for i, invoice_id in enumerate(ids[30:]):
        store.record_outcomes("id:V2", invoice_id, outcomes(bad=i == 7), display_name="Chinook",
                              at=f"2026-01-02T00:00:{i:02d}")  # fmt: skip
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", client.get("/learning?tab=suppliers").text)))
    text += re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", client.get("/settings/automation").text)))
    for old in ("go touchless", "goes touchless", "Goes touchless", "gone touchless", "touchless invoice",
                "clean in a row", "clean invoice", "yet again"):  # fmt: skip
        assert old not in text, old
    assert re.search(r"\d+ of about \d+ clean approvals toward skipping review", text)  # B-666


def test_in_a_browser_a_slow_link_reads_as_before_when_the_page_does_not_come(browser, store):
    """B-440's link stayed on "Gathering the reports…" for good when the page never came (AP Coder stopped)."""
    page, errors = _open(browser, "/help")
    page.evaluate("window.AP_SLOW_LINK_MS = 300")
    state = page.evaluate(
        "async () => { window.addEventListener('click', (e) => e.preventDefault(), {once: true});"
        " const a = document.querySelector('a[href=\"/help/report\"]'); a.click();"
        " await new Promise((r) => setTimeout(r, 600));"
        " return [a.getAttribute('aria-busy'), a.textContent.trim()]; }"
    )
    assert state == [None, "bug_reportReport a problem"], state
    assert errors == []
