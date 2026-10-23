"""The account picker (static/js/picker.js, D-56): the review screen's GL accounts and cost centers, the vendor and
the currency are picked from a searchable list; the chart of accounts is printed once in the page, not once a line."""

from __future__ import annotations

import copy
import json
import re
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from ap_coder.ai.inference import MODEL_SUGGESTED
from ap_coder.core.config import Settings
from ap_coder.core.memory import ACCEPTED, CODED, CORRECTED
from ap_coder.core.reference_data import UNASSIGNED
from ap_coder.core.store import REVIEW, Store, load_sample_cost_centers
from ap_coder.ops.demo import load_demo
from ap_coder.web import invoice_view
from ap_coder.web.app import create_app

from .conftest import chromium_path

PAGE = {"X-AP-Coder": "1"}
LAPTOP = {"width": 1366, "height": 768}


@pytest.fixture(scope="module")
def demo_db(tmp_path_factory):
    path = tmp_path_factory.mktemp("demo") / "private" / "ap_coder.db"
    load_sample_cost_centers(Store(path))  # the line grid's cost centers are picked too
    load_demo(Store(path), Settings())
    return path


@pytest.fixture
def store(demo_db, tmp_path):
    import shutil

    target = tmp_path / "private" / "ap_coder.db"
    target.parent.mkdir(parents=True)
    shutil.copy(demo_db, target)
    return Store(target)


def _app(store):
    s = Settings()
    return create_app(replace(s, llm=replace(s.llm, provider="off")), store, start_reader=False)


@pytest.fixture
def client(store):
    return TestClient(_app(store), base_url="http://localhost")


@pytest.fixture(autouse=True)
def me(monkeypatch):
    for module in ("ap_coder.web.core", "ap_coder.web.pages.invoice", "ap_coder.web.pages.review"):
        monkeypatch.setattr(f"{module}.reviewer", lambda: "Pat Tester", raising=False)
        monkeypatch.setattr(f"{module}.login", lambda: "ptester", raising=False)


def _set_output(store: Store, invoice_id: int, output: dict) -> None:
    with store._conn() as conn:
        conn.execute("UPDATE invoices SET ai_output = ?, vendor_name = ? WHERE id = ?",
                     (json.dumps(output), output.get("vendor_name"), invoice_id))  # fmt: skip


def _accounts(store: Store) -> list[str]:
    reference = store.reference_data()
    return [c for c, _ in invoice_view.gl_options(reference, []) if c != UNASSIGNED]


# --- Without a browser ---------------------------------------------------------------------------------------------


def test_vendor_accounts_are_the_ones_its_approved_lines_went_to_most_used_first(store):
    with store._conn() as conn:
        rows = [  # (vendor_key, suggested, final, outcome), oldest first
            ("acme", "6000", "6100", CORRECTED), ("acme", "6100", "6100", ACCEPTED), ("acme", None, "6200", CODED),
            ("acme", "6900", "6200", CORRECTED), ("acme", "6200", "6200", ACCEPTED), ("acme", None, UNASSIGNED, CODED),
            ("acme", None, "6300", "history"), ("other", "6400", "6400", ACCEPTED),
        ]  # fmt: skip
        for key, suggested, final, outcome in rows:
            conn.execute(
                """INSERT INTO feedback (invoice_id, line_number, vendor_key, vendor_name, description, amount,
                   suggested_gl, final_gl, suggested_cc, final_cc, outcome, reviewer, created_at)
                   VALUES (NULL, 1, ?, ?, 'x', 1, ?, ?, '', '', ?, 'Pat Tester', datetime('now'))""",
                (key, key.title(), suggested, final, outcome),
            )
    # 6200 three times, 6100 twice, then 6300 (the ERP's history); never what the AI proposed (6000, 6900), nor a
    # line left without one, nor another vendor's
    assert invoice_view.vendor_accounts(store, "Acme Inc.") == ["6200", "6100", "6300"]
    assert invoice_view.vendor_accounts(store, "Acme", limit=2) == ["6200", "6100"]
    assert invoice_view.vendor_accounts(store, "Nobody") == [] and invoice_view.vendor_accounts(store, " ") == []


def test_the_review_screen_prints_the_chart_of_accounts_once_not_once_a_line(client, store):
    inv = store.get_invoice(store.list_invoices(REVIEW)[0]["id"])
    output = copy.deepcopy(inv["ai_output"])
    lines = output["line_items"]
    output["line_items"] = [{**copy.deepcopy(lines[i % len(lines)]), "line_number": i + 1} for i in range(20)]
    _set_output(store, inv["id"], output)
    page = client.get(f"/review/{inv['id']}").text
    reference = store.reference_data()
    full = len(invoice_view.gl_options(reference, [])) + len(reference.cost_centers.codes) + 1
    rows = page.split('<tbody data-grid="lines">', 1)[1].split("</tbody>", 1)[0]
    selects = re.findall(r'<select name="line_(?:gl|cc)".*?</select>', rows, re.S)
    assert len(selects) == 40 and all(s.count("<option") <= 2 for s in selects)
    # once each in the page: 20 lines printed every account and cost center before (20 x full options). A line's
    # tax code is a short list of its own (D-64), not counted.
    codes = sum(s.count("<option") for s in re.findall(r'<select name="line_tax_code".*?</select>', page, re.S))
    assert page.count("<option") - codes < 20 * full / 2, (page.count("<option"), codes, full)
    listed = json.loads(re.search(r'id="gl-options">(.*?)</script>', page, re.S).group(1))
    expected = invoice_view.gl_options(reference, output["line_items"])
    assert [code for code, _ in listed] == [code for code, _ in expected]
    assert 'id="cc-options"' in page and 'aria-label="Line 3 GL account"' in page
    # each line's own choice is still the select's (what is posted, and what the tests and the server read)
    first = selects[0]
    code = output["line_items"][0]["predicted_gl_code"] or UNASSIGNED
    assert f'<option value="{code}" selected>' in first


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


def _open(browser, path):
    chromium, base = browser
    page = chromium.new_page(viewport=LAPTOP)
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + path)
    return page, errors


def _review(browser, invoice_id):
    page, errors = _open(browser, f"/review/{invoice_id}")
    page.wait_for_selector(".iv-sheet:not(.loading)")
    page.wait_for_load_state("networkidle")  # the opening check has answered
    return page, errors


GL = "[data-grid=lines] .pk-input[data-picker-name=line_gl]"


def _options(page):
    return page.locator(".pk-layer:not([hidden]) [role=option]")


def _posted_gls(request) -> list[str]:
    return re.findall(r'name="line_gl"\r\n\r\n([^\r]*)\r\n', request.post_data or "")


def _a_word_inside(store) -> tuple[str, str]:
    """An account and a word from the middle of its name (not its first word)."""
    reference = store.reference_data()
    for code, label in invoice_view.gl_options(reference, []):
        words = label.split(" · ", 1)[-1].split()
        if code != UNASSIGNED and len(words) >= 3 and len(words[1]) >= 4:
            return code, words[1]
    raise AssertionError("no account with a long enough name")


def test_in_a_browser_typing_a_word_from_inside_an_account_name_finds_it_and_enter_picks_it(browser, store):
    invoice_id = store.list_invoices(REVIEW)[0]["id"]
    code, word = _a_word_inside(store)
    page, errors = _review(browser, invoice_id)
    field = page.locator(GL).first
    field.click()
    assert field.get_attribute("aria-expanded") == "true"
    assert page.locator(".pk-layer [role=listbox]").get_attribute("id") == field.get_attribute("aria-controls")
    everything = _options(page).count()
    page.keyboard.type(word.upper()[:-1])  # in capitals, one letter short: it matches inside the name
    shown = _options(page)
    assert 0 < shown.count() < everything
    texts = shown.all_inner_texts()
    assert all(word[:-1].lower() in t.lower() for t in texts), texts
    assert shown.first.locator("mark").count() >= 1
    target = next(i for i, t in enumerate(texts) if t.startswith(code))
    for _ in range(target):
        page.keyboard.press("ArrowDown")
    assert field.get_attribute("aria-activedescendant") == shown.nth(target).get_attribute("id")
    with page.expect_request(lambda r: r.url.endswith("/check") and r.method == "POST") as checked:
        page.keyboard.press("Enter")
    assert _posted_gls(checked.value)[0] == code  # the select's value is what the form posts
    assert page.eval_on_selector("[data-grid=lines] select[name=line_gl]", "s => s.value") == code
    assert field.input_value().startswith(code) and field.get_attribute("aria-expanded") == "false"
    page.wait_for_load_state("networkidle")
    page.reload()  # the check kept it as the draft
    page.wait_for_selector(".iv-sheet:not(.loading)")
    assert page.eval_on_selector("[data-grid=lines] select[name=line_gl]", "s => s.value") == code
    assert page.locator(GL).first.input_value().startswith(code)
    assert errors == []


def test_in_a_browser_escape_closes_the_list_and_keeps_the_account(browser, store):
    invoice_id = store.list_invoices(REVIEW)[0]["id"]
    page, errors = _review(browser, invoice_id)
    field = page.locator(GL).first
    before = field.input_value()
    value = page.eval_on_selector("[data-grid=lines] select[name=line_gl]", "s => s.value")
    field.focus()
    page.keyboard.type("zzzz qqq")
    assert "No account matches “zzzz qqq”" in page.inner_text(".pk-layer")
    page.keyboard.press("Escape")
    assert page.locator(".pk-layer").is_hidden() and field.input_value() == before
    assert page.eval_on_selector("[data-grid=lines] select[name=line_gl]", "s => s.value") == value
    # Alt+↓ opens it again; Tab with nothing typed picks nothing
    page.keyboard.press("Alt+ArrowDown")
    assert _options(page).count() > 3
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Tab")
    assert page.eval_on_selector("[data-grid=lines] select[name=line_gl]", "s => s.value") == value
    assert errors == []


def test_in_a_browser_the_accounts_used_for_this_vendor_come_first(browser, store):
    first, second = (store.get_invoice(i["id"]) for i in store.list_invoices(REVIEW)[:2])
    accounts = _accounts(store)
    approved = copy.deepcopy(first["ai_output"])
    for i, li in enumerate(approved["line_items"]):
        li["predicted_gl_code"] = accounts[-1] if i else accounts[-2]
    store.approve_invoice(first["id"], approved, "Pat Tester")
    output = copy.deepcopy(second["ai_output"])  # another invoice of the same vendor
    output["vendor_name"] = first["ai_output"]["vendor_name"]
    _set_output(store, second["id"], output)
    used = invoice_view.vendor_accounts(store, output["vendor_name"])
    assert set(used) == {accounts[-1], accounts[-2]} or used == [accounts[-2]]

    page, errors = _review(browser, second["id"])
    page.locator(GL).first.click()
    groups = page.locator(".pk-layer .pk-group").all_inner_texts()
    assert [g.lower() for g in groups[:1]] == ["used for this vendor"] and groups[-1].lower() == "all accounts", groups
    vendor_group = page.locator(".pk-layer [role=group]").first
    assert vendor_group.locator("[role=option]").all_inner_texts()[0].startswith(used[0])
    field = page.locator(GL).first
    assert field.get_attribute("aria-activedescendant") is None  # none highlighted: Enter keeps the account
    page.keyboard.press("ArrowDown")
    first_used = vendor_group.locator("[role=option]").first
    assert field.get_attribute("aria-activedescendant") == first_used.get_attribute("id")
    page.keyboard.type("a")  # typing: one list, no groups
    assert page.locator(".pk-layer .pk-group").count() == 0
    assert errors == []


def test_in_a_browser_picking_an_account_for_a_guessed_line_takes_the_guess_mark_away(browser, store):
    inv = store.get_invoice(store.list_invoices(REVIEW)[0]["id"])
    output = copy.deepcopy(inv["ai_output"])
    accounts = _accounts(store)
    output["line_items"][0]["predicted_gl_code"] = accounts[0]
    output["line_items"][0]["reasoning_justification"] = f"{MODEL_SUGGESTED}: office supplies"
    _set_output(store, inv["id"], output)
    page, errors = _review(browser, inv["id"])
    cell = page.locator("[data-grid=lines] td.c-gl").first
    assert "A guess, not learned yet: check it" in cell.inner_text()
    assert "is-guess" in cell.get_attribute("class")
    page.locator(GL).first.click()
    page.keyboard.type(accounts[1])
    page.keyboard.press("Enter")
    assert "A guess" not in cell.inner_text() and "is-guess" not in cell.get_attribute("class")
    assert page.eval_on_selector("[data-grid=lines] select[name=line_gl]", "s => s.value") == accounts[1]
    assert errors == []


def test_in_a_browser_new_lines_and_split_lines_get_the_picker(browser, store):
    invoice_id = store.list_invoices(REVIEW)[0]["id"]
    accounts = _accounts(store)
    page, errors = _review(browser, invoice_id)
    rows = page.locator("[data-grid=lines] tr").count()
    page.click("[data-add-row=lines]")
    assert page.locator(GL).count() == rows + 1
    new = page.locator(GL).last
    assert new.get_attribute("aria-label") == "GL account"  # no line number yet
    new.click()
    page.keyboard.type(accounts[2])
    page.keyboard.press("Enter")
    assert page.locator("[data-grid=lines] select[name=line_gl]").last.input_value() == accounts[2]
    page.wait_for_load_state("networkidle")

    # Split the first line in two: the parts' accounts are pickers too, and the new rows from the server get theirs
    page.click("[data-split] > summary")
    parts = page.locator("[data-parts] .pk-input[data-picker-name=part_gl]")
    parts.nth(1).wait_for()  # the popover's "toggle" (it fills the parts) comes after the click
    assert parts.count() == 2
    parts.nth(1).click()
    page.keyboard.type(accounts[3])
    page.keyboard.press("Enter")
    assert page.locator("[data-split]").get_attribute("open") is not None  # picking in it doesn't close it
    assert page.locator("[data-parts] select[name=part_gl]").nth(1).input_value() == accounts[3]
    page.click("[data-split-go]")
    assert page.locator("[data-split-go][aria-busy=true]").count() == 1 or page.locator(GL).count() == rows + 2
    page.wait_for_function(f"() => document.querySelectorAll('[data-grid=lines] tr').length === {rows + 2}")
    page.wait_for_load_state("networkidle")
    assert page.locator(GL).count() == rows + 2
    assert page.locator("[data-grid=lines] .pk").count() >= rows + 2
    values = page.eval_on_selector_all("[data-grid=lines] select[name=line_gl]", "ss => ss.map((s) => s.value)")
    assert accounts[3] in values
    labels = page.locator(GL).evaluate_all("els => els.map((e) => e.value)")
    shown = page.eval_on_selector_all(
        "[data-grid=lines] select[name=line_gl]", "ss => ss.map((s) => s.selectedOptions[0].textContent)"
    )
    assert labels == shown
    # the button was busy while the split was made, and is usable again after
    page.wait_for_function("() => !document.querySelector('[data-split-go]').hasAttribute('aria-busy')")
    # and they work: a new row's picker opens
    page.locator(GL).nth(1).click()
    assert _options(page).count() > 3
    assert errors == []


def test_in_a_browser_the_split_popover_closes_on_escape_and_gives_back_the_focus(browser, store):
    page, errors = _review(browser, store.list_invoices(REVIEW)[0]["id"])
    page.click("[data-split] > summary")
    page.locator("[data-parts] .pk-input").first.click()
    page.keyboard.press("Escape")  # the list first
    assert page.locator(".pk-layer").is_hidden() and page.locator("[data-split]").get_attribute("open") is not None
    page.keyboard.press("Escape")  # then the popover
    assert page.locator("[data-split]").get_attribute("open") is None
    assert page.evaluate("document.activeElement.matches('[data-split] > summary')")
    assert errors == []


def test_in_a_browser_the_vendor_is_typed_or_picked_from_the_vendor_list(browser, store):
    invoice_id = next(i["id"] for i in store.list_invoices(REVIEW) if i["vendor_name"].startswith("Cascade"))
    page, errors = _review(browser, invoice_id)
    vendors = json.loads(page.inner_text("#vendor-data"))
    target = vendors["list"][-1]
    vendor = page.locator("#vendor-name")
    vendor.fill("")
    word = target["name"].split()[-1] if len(target["name"].split()) > 1 else target["name"]
    vendor.type(word[: max(3, len(word) - 1)].lower())
    shown = _options(page)
    assert shown.count() >= 1
    names = [t.split("\n")[0] for t in shown.all_inner_texts()]
    at = next(i for i, t in enumerate(shown.all_inner_texts()) if t.startswith(target["name"]))
    for _ in range(at + 1):
        page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    assert vendor.input_value() == target["name"], names
    match = page.inner_text("[data-vendor-match]")
    assert "In the vendor list" in match or "Approved before" in match or "Added here" in match, match
    # free text: a name not in the list is kept as typed
    vendor.fill("")
    vendor.type("Zebra Unknown Supplies")
    assert page.locator(".pk-layer").is_hidden()
    page.keyboard.press("Tab")
    assert vendor.input_value() == "Zebra Unknown Supplies"
    if vendors["enforced"]:
        # said once under the field: AP Coder's line, or the check's own note once checked (B-796)
        assert "not in the vendor list" in page.inner_text(".inv-details [data-field=vendor_name]").lower()
    # the currency: picked, or typed
    currency = page.locator("[name=currency][form=inv]")
    currency.fill("")
    currency.type("us")
    assert "US dollar" in page.inner_text(".pk-layer")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    assert currency.input_value() == "USD"
    currency.fill("CHF")
    assert currency.input_value() == "CHF"
    assert errors == []


def test_in_a_browser_the_list_is_not_cut_off_and_flips_above_near_the_bottom(browser, store):
    page, errors = _review(browser, store.list_invoices(REVIEW)[0]["id"])
    field = page.locator(GL).first
    field.scroll_into_view_if_needed()
    page.evaluate("(y) => window.scrollBy(0, y)", 0)
    field.click()
    box = page.locator(".pk-layer").bounding_box()
    assert box["height"] <= 12 * 30 + 60 and box["y"] >= 0 and box["y"] + box["height"] <= LAPTOP["height"]
    # the field low on the screen, just above the approve bar: the list opens above it
    field.evaluate("e => { e.blur(); }")
    bar_top = page.locator(".inv-actionbar").bounding_box()["y"]
    top = field.bounding_box()["y"]
    page.evaluate("(dy) => window.scrollBy(0, dy)", top - (bar_top - 50))
    field.click()
    f = field.bounding_box()
    layer = page.locator(".pk-layer").bounding_box()
    assert layer["y"] + layer["height"] <= f["y"] + 1, (layer, f)
    assert errors == []


def test_in_a_browser_the_accounts_rules_and_statements_selects_are_pickers(browser, store):
    page, errors = _open(browser, "/accounts?tab=rules")
    rules = page.locator("select[name=gl_code]").count()
    assert rules >= 3 and page.locator(".pk-input[data-picker-name=gl_code]").count() == rules
    page.locator(".pk-input[data-picker-name=gl_code]").first.click()
    page.keyboard.type(_accounts(store)[0])
    page.keyboard.press("Enter")  # picks: the rules form is not sent
    assert page.locator("select[name=gl_code]").first.input_value() == _accounts(store)[0]
    assert page.url.endswith("/accounts?tab=rules")
    page, errors2 = _open(browser, "/statements")
    pick = page.locator(".pk-input[data-picker-name=vendor]").first
    pick.click()
    assert _options(page).count() >= 2
    before = page.locator("select[name=vendor]").first.input_value()
    page.keyboard.press("End")
    page.keyboard.press("Enter")
    assert page.locator("select[name=vendor]").first.input_value() != before
    assert errors == [] and errors2 == []


def test_in_a_browser_checking_shows_while_a_check_runs_and_goes_after_even_when_it_fails(browser, store):
    import time

    page, errors = _review(browser, store.list_invoices(REVIEW)[0]["id"])
    status = page.locator("[data-checking]")
    assert status.inner_text() == ""

    def slow(route):
        time.sleep(0.6)
        route.continue_()

    page.route("**/check", slow)
    page.fill("[name=invoice_number][form=inv]", "A-1")
    page.wait_for_function("() => document.querySelector('[data-checking]').textContent === 'Checking…'")
    # checked and kept: "Draft saved" and the time (D-76)
    page.wait_for_function("() => document.querySelector('[data-checking]').textContent.startsWith('Draft saved')")
    page.unroute("**/check")

    def failed(route):  # AP Coder not answering
        time.sleep(0.6)
        route.abort()

    page.route("**/check", failed)
    page.fill("[name=invoice_number][form=inv]", "A-2")
    page.wait_for_function("() => document.querySelector('[data-checking]').textContent === 'Checking…'")
    page.wait_for_function("() => document.querySelector('[data-checking]').textContent === ''", timeout=5000)
    assert errors == []


# --- The second review's findings (B-590, B-593, B-595 to B-597, D-56) -------------------------------------------


def _clean(client, store) -> int:
    """An invoice of the queue that may be approved as it is."""
    for inv in store.list_invoices(REVIEW):
        if client.post(f"/review/{inv['id']}/check", data={}, headers=PAGE).json()["allow"]:
            return inv["id"]
    raise AssertionError("no clean demo invoice")


def _is_open(page) -> bool:
    return page.evaluate("!document.querySelector('.pk-layer')?.hidden && !!document.querySelector('.pk-layer')")


def test_in_a_browser_ctrl_enter_with_the_list_open_approves_the_account_typed(browser, client, store):
    """B-590: an account typed (the list open, its match highlighted), then Ctrl+Enter: the approval sent the old
    account and the list stayed open."""
    invoice_id = _clean(client, store)
    page, errors = _review(browser, invoice_id)
    before = page.eval_on_selector("[data-grid=lines] select[name=line_gl]", "s => s.value")
    code = next(c for c in _accounts(store) if c != before)
    sent: list[str] = []

    def keep(route):
        sent.append(route.request.post_data or "")
        route.abort()

    page.route("**/approve", keep)
    page.locator(GL).first.click()
    page.keyboard.type(code)
    assert _is_open(page) and _options(page).first.inner_text().startswith(code)
    page.keyboard.press("Control+Enter")
    page.wait_for_function("() => document.querySelector('.pk-layer').hidden")
    page.wait_for_timeout(1500)
    assert sent and _posted_gls_in(sent[0])[0] == code, [_posted_gls_in(s) for s in sent]
    assert page.eval_on_selector("[data-grid=lines] select[name=line_gl]", "s => s.value") == code
    assert errors == []


def _posted_gls_in(body: str) -> list[str]:
    return re.findall(r'name="line_gl"\r\n\r\n([^\r]*)\r\n', body)


def test_in_a_browser_tab_onto_an_account_box_leaves_its_list_closed(browser, store):
    """D-56: tabbing through a grid of 20 lines opened a list on every account box. The list opens on typing, a click,
    Alt+↓, F4, or Enter (Space on a box without an account)."""
    page, errors = _review(browser, store.list_invoices(REVIEW)[0]["id"])
    field = page.locator(GL).first
    page.click("textarea[name=line_description] >> nth=0")
    for _ in range(4):
        page.keyboard.press("Tab")
        if page.evaluate("document.activeElement.dataset.pickerName") == "line_gl":
            break
    assert page.evaluate("document.activeElement.dataset.pickerName") == "line_gl"
    assert not _is_open(page) and field.get_attribute("aria-expanded") == "false"
    for key in ("Alt+ArrowDown", "F4", "Enter"):
        page.keyboard.press(key)
        assert _is_open(page), key
        page.keyboard.press("Escape")
        assert not _is_open(page), key
    page.keyboard.type("a")
    assert _is_open(page)
    page.keyboard.press("Escape")
    page.click("textarea[name=line_description] >> nth=0")
    field.click()
    assert _is_open(page)
    # Space on a box without an account opens the list (and types no space)
    page.keyboard.press("Escape")
    page.select_option("[data-grid=lines] select[name=line_gl] >> nth=0", UNASSIGNED)
    page.click("textarea[name=line_description] >> nth=0")
    field.focus()
    assert not _is_open(page)
    page.keyboard.press(" ")
    assert _is_open(page) and _options(page).count() > 3
    assert errors == []


def _layer(page):
    return page.locator(".pk-layer").bounding_box()


def test_in_a_browser_an_open_list_follows_its_box_and_closes_once_the_box_scrolls_away(browser, store):
    """B-593: the list stayed where it opened while the page scrolled; its box gone off the screen, a click on the
    list still changed that line."""
    page, errors = _review(browser, store.list_invoices(REVIEW)[0]["id"])
    field = page.locator(GL).first
    field.scroll_into_view_if_needed()
    page.evaluate("window.scrollTo(0, 0)")
    field.click()
    assert _is_open(page)
    page.evaluate("window.scrollBy(0, 60)")
    page.wait_for_timeout(150)
    f, layer = field.bounding_box(), _layer(page)
    assert _is_open(page) and abs(layer["y"] - (f["y"] + f["height"] + 4)) <= 2, (f, layer)
    page.evaluate("window.scrollBy(0, 2000)")  # the box is gone above the screen
    page.wait_for_timeout(150)
    assert not _is_open(page) and field.get_attribute("aria-expanded") == "false"
    # Under the approve bar: closed too.
    low = page.locator(GL).last
    page.evaluate("window.scrollTo(0, 0)")
    bar_top = page.locator(".inv-actionbar").bounding_box()["y"]
    page.evaluate("(dy) => window.scrollBy(0, dy)", low.bounding_box()["y"] - (bar_top - 50))
    low.click()
    assert _is_open(page)
    page.evaluate("window.scrollBy(0, -60)")
    page.wait_for_timeout(150)
    assert low.bounding_box()["y"] > bar_top and not _is_open(page)
    assert errors == []


def test_in_a_browser_a_resize_keeps_the_open_list_on_the_screen_above_the_approve_bar(browser, store):
    page, errors = _review(browser, store.list_invoices(REVIEW)[0]["id"])
    field = page.locator(GL).first
    page.evaluate("window.scrollTo(0, 0)")
    bar_top = page.locator(".inv-actionbar").bounding_box()["y"]
    page.evaluate("(dy) => window.scrollBy(0, dy)", field.bounding_box()["y"] - (bar_top - 300))
    field.click()
    assert _is_open(page)
    for height in (640, 560, 900):
        page.set_viewport_size({"width": 1366, "height": height})
        page.wait_for_timeout(200)
        if not _is_open(page):  # its box gone under the approve bar: closed
            assert field.bounding_box()["y"] + 10 > page.locator(".inv-actionbar").bounding_box()["y"], height
            break
        layer, bar = _layer(page), page.locator(".inv-actionbar").bounding_box()["y"]
        assert layer["y"] >= 0 and layer["y"] + layer["height"] <= bar + 1, (height, layer, bar)
    assert errors == []


def test_in_a_browser_a_vendor_pointed_at_then_tab_keeps_the_name_typed(browser, store):
    """B-595: the vendor typed, the mouse resting on a vendor of the list, then Tab: the name typed was replaced by the
    vendor under the mouse."""
    page, errors = _review(browser, store.list_invoices(REVIEW)[0]["id"])
    vendors = json.loads(page.inner_text("#vendor-data"))["list"]
    vendor = page.locator("#vendor-name")
    vendor.fill("")
    typed = vendors[0]["name"][:3].lower()
    vendor.type(typed)
    assert _options(page).count() >= 1
    page.locator(".pk-layer [role=option]").first.hover()
    page.keyboard.press("Tab")
    assert vendor.input_value() == typed
    # A vendor reached with the arrow keys is picked by Tab
    vendor.fill("")
    vendor.type(typed)
    page.keyboard.press("ArrowDown")
    name = page.evaluate("document.querySelector('.pk-option.is-active').title.split(' · ')[0]")
    page.keyboard.press("Tab")
    assert vendor.input_value() == name
    # Enter on a vendor pointed at picks it
    vendor.fill("")
    vendor.type(typed)
    option = page.locator(".pk-layer [role=option]").first
    option.hover()
    name = option.get_attribute("title").split(" · ")[0]
    page.keyboard.press("Enter")
    assert vendor.input_value() == name
    assert errors == []


def test_in_a_browser_the_accounts_used_for_this_vendor_follow_the_vendor_field(browser, store):
    """B-596: "Used for this vendor" kept the vendor of the page as it opened: another vendor picked, the list still
    offered the first one's accounts."""
    first, second = (store.get_invoice(i["id"]) for i in store.list_invoices(REVIEW)[:2])
    assert first["vendor_name"] != second["vendor_name"]
    accounts = _accounts(store)
    approved = copy.deepcopy(first["ai_output"])
    for li in approved["line_items"]:
        li["predicted_gl_code"] = accounts[-1]
    store.approve_invoice(first["id"], approved, "Pat Tester")
    assert invoice_view.vendor_accounts(store, first["vendor_name"]) == [accounts[-1]]
    assert accounts[-1] not in invoice_view.vendor_accounts(store, second["vendor_name"])

    page, errors = _review(browser, second["id"])
    vendor = page.locator("#vendor-name")
    with page.expect_response(lambda r: r.url.endswith("/check")) as answered:
        vendor.fill(first["vendor_name"])
        vendor.press("Escape")
    assert answered.value.json()["vendor_accounts"] == [accounts[-1]]
    page.wait_for_load_state("networkidle")
    page.locator(GL).first.click()
    group = page.locator(".pk-layer [role=group]").first
    assert group.locator(".pk-group").inner_text().lower() == "used for this vendor"
    assert group.locator("[role=option]").all_inner_texts()[0].startswith(accounts[-1])
    assert errors == []


def test_in_a_browser_the_vendor_list_ends_with_add_as_a_new_vendor(browser, store):
    """B-597: while the vendor list showed, it covered "Not in the vendor list: … add it as a new vendor" under the
    field: its last row now says it, and adds the vendor."""
    page, errors = _review(browser, store.list_invoices(REVIEW)[0]["id"])
    vendors = json.loads(page.inner_text("#vendor-data"))
    assert vendors["enforced"]
    vendor = page.locator("#vendor-name")
    vendor.fill("")
    vendor.type(vendors["list"][0]["name"][:3].lower())
    last = _options(page).last
    assert last.inner_text().startswith("Not in the vendor list: add as a new vendor")
    assert page.evaluate("document.querySelector('[data-vendor-add]').open") is False
    page.keyboard.press("Tab")  # Tab never picks it
    assert page.evaluate("document.querySelector('[data-vendor-add]').open") is False
    vendor.fill("")
    vendor.type(vendors["list"][0]["name"][:3].lower())
    _options(page).last.click()
    page.wait_for_function("document.querySelector('[data-vendor-add]').open")
    assert page.input_value("[data-new=name]") == vendors["list"][0]["name"][:3].lower()
    assert not _is_open(page)
    # The name of a vendor of the list, typed in full: nothing to add
    vendor.fill("")
    page.keyboard.press("Escape")
    vendor.fill(vendors["list"][0]["name"][:-1])
    vendor.type(vendors["list"][0]["name"][-1])
    assert not any(t.startswith("Not in the vendor list") for t in _options(page).all_inner_texts())
    assert errors == []
