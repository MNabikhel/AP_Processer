"""The review screen's right-click menus and the small things around them (D-60): a field marked "Not on this invoice",
Undo, a note added without leaving the page, the duplicate's link, the queue remembering where it was. The menus
themselves are plain JavaScript (static/js/ctxmenu.js): the browser tests at the end drive them."""

from __future__ import annotations

import copy
import json
import re
import shutil
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from ap_coder.core.config import Settings
from ap_coder.core.store import REVIEW, Store
from ap_coder.ops.demo import load_demo
from ap_coder.web import invoice_view
from ap_coder.web.app import create_app
from ap_coder.web.invoice_view import Draft

from .conftest import chromium_path
from .test_web_invoice import check, form_of

PAGE = {"X-AP-Coder": "1"}
LAPTOP = {"width": 1366, "height": 768}
SHOTS = None  # set by the screenshot test: where the pictures of each menu go


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


def _with_gst(store: Store) -> int:
    """An invoice of the queue that charges GST or HST."""
    for inv in store.list_invoices(REVIEW):
        taxes = {t.get("tax_type") for t in (store.get_invoice(inv["id"])["ai_output"] or {}).get("tax_lines") or []}
        if taxes & {"GST", "HST"}:
            return inv["id"]
    raise AssertionError("no demo invoice charges GST or HST")


def _texts(answer: dict) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", answer["sections"]["checks"]))


# --- Not on this invoice -----------------------------------------------------------------------------------------


def test_a_field_marked_not_on_this_invoice_is_no_longer_asked_for(client, store):
    invoice_id = _with_gst(store)
    blank = form_of(store, invoice_id, gst_hst_registration_number="")
    # cleared by the clerk after the vendor was set: the vendor list does not fill it in again (D-65)
    vendor = store.get_invoice(invoice_id)["vendor_key"]
    blank["from_vendor"] = json.dumps({"vendor": vendor, "fields": {}})
    asked = _texts(check(client, invoice_id, blank))
    assert "GST/HST registration number not found" in asked
    marked = {**blank, "absent": json.dumps(["gst_hst_registration_number"])}
    answer = check(client, invoice_id, marked)
    said = _texts(answer)
    # not a warning to check any more; said once, as information, with what it costs
    assert "GST/HST registration number not found" not in said
    assert "not on this invoice: no input tax credit can be claimed without it" in said
    # kept with the draft: the screen opens with the field marked, its note shown
    page = client.get(f"/review/{invoice_id}").text
    label = re.search(r'<label[^>]*data-field="gst_hst_registration_number"[^>]*>.*?</label>', page, re.S).group(0)
    assert "data-absent-ok" in label and " data-absent" in label.replace("data-absent-ok", "")
    assert re.search(r'<span class="inv-absent[^"]*">', label), label  # the note, not hidden
    assert json.loads(re.search(r'name="absent" value="([^"]*)"', page).group(1).replace("&#34;", '"')) == [
        "gst_hst_registration_number"
    ]


def test_a_value_typed_in_again_unmarks_the_field_and_needed_fields_are_never_absent():
    form = {"absent": json.dumps(["po_number", "invoice_number", "vendor_name", "grand_total", "nope", "po_number"])}
    header = {name: "" for name in invoice_view.FORM_FIELDS}
    assert invoice_view.parse_absent(form["absent"], header) == ["po_number"]
    assert invoice_view.parse_absent(form["absent"], {**header, "po_number": "PO-1"}) == []
    assert invoice_view.parse_absent("not json", header) == [] and invoice_view.parse_absent('{"a": 1}', header) == []
    for needed in ("vendor_name", "invoice_number", "invoice_date", "currency", "subtotal", "grand_total"):
        assert needed not in invoice_view.ABSENT_FIELDS
    base = Draft(header=header, lines=[], taxes=[], absent=["po_number"])
    assert Draft.from_form({}, base).absent == ["po_number"]  # not posted: as it was
    assert Draft.from_form({"absent": "[]"}, base).absent == []
    assert base.to_dict()["absent"] == ["po_number"]


def test_errors_are_never_settled_by_absent():
    from ap_coder.coding.validation import Issue

    issues = [Issue("warning", "GST_HST_NUMBER_MISSING", "x"), Issue("info", "PO_NOT_QUOTED", "y"),
              Issue("error", "MISSING_INVOICE_NUMBER", "z"), Issue("warning", "QST_NUMBER_MISSING", "q")]  # fmt: skip
    out = invoice_view.settle_absent(issues, ["gst_hst_registration_number", "po_number"])
    assert [(i.severity, i.code) for i in out] == [
        ("info", "GST_HST_NUMBER_ABSENT"), ("error", "MISSING_INVOICE_NUMBER"), ("warning", "QST_NUMBER_MISSING")
    ]  # fmt: skip


# --- A note, the duplicate's link, the invoice number ------------------------------------------------------------


def test_a_note_added_from_the_review_screen_answers_the_notes_without_leaving_it(client, store):
    invoice_id = store.list_invoices(REVIEW)[0]["id"]
    answer = client.post(f"/review/{invoice_id}/note", data={"text": "Asked Sam <b>", "inline": "1"}, headers=PAGE)
    data = answer.json()
    assert data["ok"] and data["count"] == 1 and "redirect" not in data
    assert "Asked Sam &lt;b&gt;" in data["html"] and "<b>Asked" not in data["html"]
    # without inline (another page, or no JavaScript): back to the page, as before
    assert "redirect" in client.post(f"/review/{invoice_id}/note", data={"text": "x"}, headers=PAGE).json()


def test_a_possible_duplicate_links_to_the_other_invoice_and_the_number_can_be_copied(client, store):
    first, second = (store.get_invoice(i["id"]) for i in store.list_invoices(REVIEW)[:2])
    output = copy.deepcopy(second["ai_output"])
    output["vendor_name"], output["invoice_number"] = (
        first["ai_output"]["vendor_name"],
        first["ai_output"]["invoice_number"],
    )
    _set_output(store, second["id"], output)
    with store._conn() as conn:
        conn.execute("UPDATE invoices SET invoice_number = ? WHERE id = ?", (output["invoice_number"], second["id"]))
    html = check(client, second["id"])["sections"]["checks"]
    assert f'<a class="rvw-dup-link" href="/review/{first["id"]}" target="_blank"' in html, html
    summary = check(client, second["id"])["sections"]["summary"]
    assert f'data-copy-text="{output["invoice_number"]}"' in summary


# --- The shortcut lists -------------------------------------------------------------------------------------------


def test_park_reject_undo_and_the_menu_keys_are_in_both_shortcut_lists(client, store):
    import html as html_lib

    from ap_coder.web.pages.help import SHORTCUT_ROWS

    page = client.get(f"/review/{store.list_invoices(REVIEW)[0]['id']}").text
    rows = html_lib.unescape(page.split("Shortcuts", 1)[1].split("</table>", 1)[0])
    for _spec, what in SHORTCUT_ROWS:
        if what.startswith(("Park", "Reject", "Undo", "More options")):
            assert what in rows, (what, rows)
    assert {w.split(" ")[0].rstrip(":") for _, w in SHORTCUT_ROWS} >= {"Park", "Reject", "Undo", "More"}


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
    page.evaluate(
        """() => document.addEventListener("contextmenu", (e) => setTimeout(() => {
             window.__prevented = e.defaultPrevented; }), true)"""
    )
    page.wait_for_selector(".iv-sheet:not(.loading)")
    page.wait_for_load_state("networkidle")  # the opening check has answered
    return page, errors


MENU = ".cm[role=menu]"


def _item(page, id_):
    return page.locator(f"{MENU} [data-cm='{id_}']")


def _menu(page, target, **click):
    """Right-click ``target`` (a locator): the menu, once it is open."""
    target.click(button="right", **click)
    page.wait_for_selector(MENU)
    return page.locator(MENU)


def _name(page, field):
    """A detail's name above its box: where its menu opens on a right-click (inside the box it is the browser's)."""
    return page.locator(f'.inv-details [data-field="{field}"] .lbl')


def _checked(page):
    return page.expect_request(lambda r: r.url.endswith("/check") and r.method == "POST")


def test_in_a_browser_right_click_a_details_name_clear_it_and_undo_puts_it_back(browser, store):
    invoice_id = store.list_invoices(REVIEW)[0]["id"]
    page, errors = _review(browser, invoice_id)
    box = page.locator('.inv-details input[name="invoice_number"]')
    before = box.input_value()
    assert before
    menu = _menu(page, _name(page, "invoice_number"))
    assert menu.get_attribute("role") == "menu"
    labels = menu.locator("[role=menuitem]").all_inner_texts()
    assert any("Clear this value" in t for t in labels) and any("Copy value" in t for t in labels), labels
    assert any("Teach where it is" in t for t in labels) and any("Show on page" in t for t in labels), labels
    # the invoice number is needed: "Not on this invoice" is there, greyed, with why
    absent = _item(page, "absent")
    assert absent.get_attribute("aria-disabled") == "true" and "can't be left out" in absent.get_attribute("title")
    with _checked(page) as sent:
        _item(page, "clear").click()
    assert re.search(r'name="invoice_number"\r\n\r\n\r\n', sent.value.post_data or "")
    assert box.input_value() == "" and menu.count() == 0
    undo = page.locator("#toasts .flash-act")
    assert undo.is_visible() and "cleared" in page.inner_text("#toasts")
    page.wait_for_load_state("networkidle")
    with _checked(page) as sent:
        undo.click()
    assert box.input_value() == before
    assert f'name="invoice_number"\r\n\r\n{before}\r\n' in (sent.value.post_data or "")
    # Ctrl+Z (outside a text box) undoes too
    _menu(page, _name(page, "invoice_number"))
    _item(page, "clear").click()
    page.locator("body").click(position={"x": 5, "y": 5})
    page.keyboard.press("Control+z")
    assert box.input_value() == before
    assert errors == []


def test_in_a_browser_not_on_this_invoice_leaves_it_blank_with_a_note_and_nothing_asks_for_it(browser, store):
    invoice_id = _with_gst(store)
    page, errors = _review(browser, invoice_id)
    box = page.locator('.inv-details input[name="gst_hst_registration_number"]')
    before = box.input_value()
    _menu(page, _name(page, "gst_hst_registration_number"))
    with _checked(page) as sent:
        _item(page, "absent").click()
    assert '["gst_hst_registration_number"]' in (sent.value.post_data or "")
    page.wait_for_load_state("networkidle")
    assert box.input_value() == ""
    label = page.locator('.inv-details [data-field="gst_hst_registration_number"]')
    assert label.locator(".inv-absent").is_visible() and "Not on this invoice" in label.inner_text()
    assert "GST/HST registration number not found" not in page.inner_text("[data-live=checks]")
    row = page.locator('.iv-row[data-field="gst_hst_registration_number"]')
    if row.count():  # the Fields list beside the page follows the form
        assert "Not on this invoice" in row.inner_text() and "t-edited" in row.get_attribute("class")
    # marked already: the item says so; a value typed in unmarks it
    _menu(page, _name(page, "gst_hst_registration_number"))
    assert _item(page, "absent").get_attribute("aria-disabled") == "true"
    page.keyboard.press("Escape")
    assert page.locator(MENU).count() == 0
    page.locator("#toasts .flash-act").click()  # Undo
    assert box.input_value() == before and label.locator(".inv-absent").is_hidden()
    assert errors == []


def test_in_a_browser_a_fields_row_and_a_box_on_the_page_have_the_fields_menu(browser, store):
    invoice_id = store.list_invoices(REVIEW)[0]["id"]
    page, errors = _review(browser, invoice_id)
    if "fields-open" not in (page.locator("#iv").get_attribute("class") or ""):
        page.locator(".iv-fold").click()  # the field list, folded away on a laptop's screen
    row = page.locator(".iv-row[data-field]").first
    _menu(page, row)
    assert _item(page, "edit").count() == 1 and _item(page, "clear").count() == 1
    page.keyboard.press("Escape")
    shown = page.locator(".iv-f[data-field]").first
    name = shown.get_attribute("data-field")
    _menu(page, shown.locator("rect.iv-box").first, force=True)
    assert _item(page, "show").get_attribute("aria-disabled") is None
    _item(page, "show").click()
    page.wait_for_function(
        "n => document.querySelector(`.iv-f[data-field=\"${n}\"]`).classList.contains('selected')", arg=name
    )
    assert errors == []


def _lines(page):
    return page.locator("[data-grid=lines] > tr")


def _gl(page, i):
    return page.eval_on_selector_all("[data-grid=lines] select[name=line_gl]", "(s, i) => s[i].value", i)


def test_in_a_browser_delete_a_line_and_undo_brings_it_back_with_its_picker_working(browser, store):
    invoice_id = store.list_invoices(REVIEW)[0]["id"]
    page, errors = _review(browser, invoice_id)
    n = _lines(page).count()
    first = _lines(page).first.locator("textarea[name=line_description]").input_value()
    gl = _gl(page, 0)
    _menu(page, _lines(page).first.locator("[data-del-row]"))
    labels = page.locator(f"{MENU} [role=menuitem]").all_inner_texts()
    for want in ("Delete line", "Duplicate line", "Insert a line below", "Split this line", "Clear GL account"):
        assert any(want in t for t in labels), (want, labels)
    assert _item(page, "above").get_attribute("aria-disabled") == "true"  # the first line has none above
    with _checked(page):
        _item(page, "delete").click()
    assert _lines(page).count() == n - 1
    with _checked(page):
        page.locator("#toasts .flash-act").click()
    assert _lines(page).count() == n
    assert _lines(page).first.locator("textarea[name=line_description]").input_value() == first and _gl(page, 0) == gl
    # its account picker works
    picker = _lines(page).first.locator(".pk-input")
    picker.click()
    options = page.locator(".pk-layer:not([hidden]) [role=option]")
    assert options.count() > 3
    options.nth(2).click()
    assert _gl(page, 0) != "" and picker.input_value().startswith(_gl(page, 0))
    assert errors == []


def test_in_a_browser_duplicate_insert_copy_from_above_and_clear_a_lines_account(browser, store):
    invoice_id = store.list_invoices(REVIEW)[0]["id"]
    page, errors = _review(browser, invoice_id)
    n = _lines(page).count()
    description = _lines(page).first.locator("textarea[name=line_description]").input_value()
    _menu(page, _lines(page).first.locator("[data-del-row]"))
    _item(page, "duplicate").click()
    assert _lines(page).count() == n + 1
    assert _lines(page).nth(1).locator("textarea[name=line_description]").input_value() == description
    numbers = page.eval_on_selector_all("[data-grid=lines] [name=line_number]", "s => s.map(x => x.value)")
    assert numbers == [str(i + 1) for i in range(n + 1)] or len(set(numbers)) == n + 1, numbers
    _menu(page, _lines(page).first.locator("[data-del-row]"))
    _item(page, "insert").click()
    assert _lines(page).count() == n + 2
    assert _lines(page).nth(1).locator("textarea[name=line_description]").input_value() == ""
    assert _gl(page, 1) == "UNASSIGNED"
    # the new line: its account from the line above, then cleared again
    _menu(page, _lines(page).nth(1).locator("[data-del-row]"))
    _item(page, "above").click()
    assert _gl(page, 1) == _gl(page, 0) != "UNASSIGNED"
    assert _lines(page).nth(1).locator(".pk-input").input_value().startswith(_gl(page, 0))
    _menu(page, _lines(page).nth(1).locator("[data-del-row]"))
    with _checked(page):
        _item(page, "clear-gl").click()
    assert _gl(page, 1) == "UNASSIGNED"
    page.keyboard.press("Control+z")
    assert _gl(page, 1) == _gl(page, 0)
    assert errors == []


def test_in_a_browser_a_tax_lines_amount_is_recalculated_from_its_rate(browser, store):
    invoice_id = _with_gst(store)
    page, errors = _review(browser, invoice_id)
    row = page.locator("[data-grid=taxes] > tr").first
    row.locator("[name=tax_rate_pct]").fill("13")
    row.locator("[name=tax_taxable]").fill("250.00")
    row.locator("[name=tax_amount]").fill("1.00")
    _menu(page, row.locator("td").first)
    with _checked(page):
        _item(page, "recalc").click()
    assert row.locator("[name=tax_amount]").input_value() == "32.50"
    n = page.locator("[data-grid=taxes] > tr").count()
    _menu(page, row.locator("td").first)
    _item(page, "delete").click()
    assert page.locator("[data-grid=taxes] > tr").count() == n - 1
    page.locator("#toasts .flash-act").click()
    assert page.locator("[data-grid=taxes] > tr").count() == n
    assert page.locator("[data-grid=taxes] > tr").first.locator("[name=tax_amount]").input_value() == "32.50"
    assert errors == []


def _blank_spot(page):
    """A point of the page with no box on it (the bottom right corner of what shows of it)."""
    box = page.locator(".iv-stage").bounding_box()
    return {"x": box["width"] - 30, "y": box["height"] - 30}


def test_in_a_browser_the_page_menu_zooms_in_where_it_was_clicked_and_turns_the_page(browser, store):
    invoice_id = store.list_invoices(REVIEW)[0]["id"]
    page, errors = _review(browser, invoice_id)
    viewer = page.locator("#iv")
    before = viewer.get_attribute("data-zoom")
    _menu(page, page.locator(".iv-stage"), position=_blank_spot(page))
    labels = page.locator(f"{MENU} [role=menuitem]").all_inner_texts()
    assert any("Zoom in here" in t for t in labels) and any("Teach a field here" in t for t in labels), labels
    _item(page, "zoom-in").click()
    page.wait_for_function("b => document.getElementById('iv').dataset.zoom !== b", arg=before)
    _menu(page, page.locator(".iv-stage"), position=_blank_spot(page))
    _item(page, "rotate").click()
    assert "turned" in viewer.get_attribute("class")
    # the submenu: Teach a field here → a field starts teach mode
    _menu(page, page.locator(".iv-stage"), position=_blank_spot(page))
    _item(page, "teach").hover()
    page.wait_for_selector(".cm.cm-sub")
    page.locator(".cm.cm-sub [role=menuitem]").first.click()
    page.wait_for_selector(".iv-teach:not([hidden])")
    assert errors == []


def test_in_a_browser_the_menu_works_from_the_keyboard(browser, store):
    invoice_id = store.list_invoices(REVIEW)[0]["id"]
    page, errors = _review(browser, invoice_id)
    box = page.locator('.inv-details input[name="po_number"]')
    box.fill("PO-1234")
    box.focus()
    page.keyboard.press("Shift+F10")
    page.wait_for_selector(MENU)
    assert page.evaluate("document.activeElement.dataset.cm") == "clear"  # the first item has the focus
    page.keyboard.press("Escape")
    assert page.locator(MENU).count() == 0 and page.evaluate("document.activeElement.name") == "po_number"
    page.keyboard.press("Shift+F10")
    page.wait_for_selector(MENU)
    page.keyboard.press("ArrowDown")
    assert page.evaluate("document.activeElement.dataset.cm") == "absent"
    page.keyboard.press("End")
    page.keyboard.press("Home")
    with _checked(page):
        page.keyboard.press("Enter")
    assert box.input_value() == "" and page.locator(MENU).count() == 0
    # keys pressed in the menu stay in it: R does not turn the page, Esc closes only the menu
    page.locator('.inv-details input[name="invoice_number"]').focus()
    page.keyboard.press("Shift+F10")
    page.wait_for_selector(MENU)
    page.keyboard.press("r")
    assert "turned" not in (page.locator("#iv").get_attribute("class") or "")
    page.keyboard.press("Escape")
    assert page.locator(MENU).count() == 0
    assert errors == []


def test_in_a_browser_shift_right_click_gives_the_browsers_own_menu(browser, store):
    invoice_id = store.list_invoices(REVIEW)[0]["id"]
    page, errors = _review(browser, invoice_id)
    box = page.locator('.inv-details input[name="invoice_number"]')
    box.click(button="right", modifiers=["Shift"])
    page.wait_for_timeout(200)
    assert page.locator(MENU).count() == 0
    # a right-click on text selected in the box: the browser's (copy) too
    box.click()
    box.press("Control+a")
    box.click(button="right")
    page.wait_for_timeout(200)
    assert page.locator(MENU).count() == 0
    assert errors == []


def test_in_a_browser_a_word_double_clicked_on_the_page_fills_the_detail_being_typed_in(browser, store):
    invoice_id = store.list_invoices(REVIEW)[0]["id"]
    page, errors = _review(browser, invoice_id)
    name = "qst_registration_number"  # blank on this invoice: nothing to replace, nothing verified
    box = page.locator(f'.inv-details input[name="{name}"]')
    assert box.input_value() == ""
    box.click()  # (it may scroll the window: the word is found after)
    word, x, y = _plain_word(page, invoice_id)  # a word outside every field's box and line item
    with page.expect_response(lambda r: r.url.endswith("/teach")):
        page.mouse.dblclick(x, y)
    page.wait_for_function(
        "([n, t]) => document.querySelector(`.inv-details input[name=${n}]`).value === t", arg=[name, word["t"]]
    )
    taught = json.loads(page.eval_on_selector("[data-taught]", "t => t.value"))
    assert taught[name][0][0] == 1
    assert page.locator("#toasts .flash-act").is_visible()  # Undo
    assert errors == []


def test_in_a_browser_enter_goes_to_the_next_detail_and_shift_enter_back(browser, store):
    invoice_id = store.list_invoices(REVIEW)[0]["id"]
    page, errors = _review(browser, invoice_id)
    page.locator('.inv-details input[name="invoice_number"]').focus()
    page.keyboard.press("Enter")
    assert page.evaluate("document.activeElement.name") == "invoice_date"
    page.keyboard.press("Enter")
    assert page.evaluate("document.activeElement.name") == "po_number"
    page.keyboard.press("Shift+Enter")
    assert page.evaluate("document.activeElement.name") == "invoice_date"
    assert store.get_invoice(invoice_id)["status"] == REVIEW  # Enter approved nothing
    assert errors == []


def test_in_a_browser_p_opens_park_and_x_opens_reject_without_rejecting(browser, store):
    invoice_id = store.list_invoices(REVIEW)[0]["id"]
    page, errors = _review(browser, invoice_id)
    page.locator("body").click(position={"x": 5, "y": 5})
    page.keyboard.press("p")
    assert page.locator("details[data-park]").get_attribute("open") is not None
    assert page.evaluate("document.activeElement.name") == "reason"
    page.keyboard.press("Escape")
    page.locator("body").click(position={"x": 5, "y": 5})
    page.keyboard.press("x")
    assert page.locator("details[data-reject]").get_attribute("open") is not None
    page.wait_for_timeout(200)
    assert store.get_invoice(invoice_id)["status"] == REVIEW
    assert errors == []


def test_in_a_browser_a_note_is_added_without_leaving_the_page(browser, store):
    invoice_id = store.list_invoices(REVIEW)[0]["id"]
    page, errors = _review(browser, invoice_id)
    page.evaluate("window.__stayed = 1")
    page.locator('.inv-details input[name="po_number"]').fill("PO-777")  # an edit, kept as it is
    notes = page.locator(".inv-notes details")
    if notes.get_attribute("open") is None:
        notes.locator("summary").click()
    page.locator(".inv-note-form input[name=text]").fill("Asked Sam about the PO")
    page.locator(".inv-note-form button[type=submit]").click()
    page.wait_for_selector("[data-notes] .ev")
    assert "Asked Sam about the PO" in page.inner_text("[data-notes]")
    assert page.evaluate("window.__stayed") == 1 and page.inner_text("[data-notes-count]").strip() == "· 1"
    assert page.locator('.inv-details input[name="po_number"]').input_value() == "PO-777"
    assert page.locator(".inv-note-form input[name=text]").input_value() == ""
    assert errors == []


def test_in_a_browser_the_queue_remembers_its_filter_and_where_it_was_scrolled(browser, store):
    chromium, base = browser
    page = chromium.new_page(viewport={"width": 1366, "height": 420})
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/review?show=all&sort=amount")
    page.evaluate("window.scrollTo(0, 260)")
    y = page.evaluate("window.scrollY")
    assert y > 100
    page.eval_on_selector(".rq-list li:last-child a.rq-row", "a => a.click()")  # as it is: no scrolling to it first
    page.wait_for_selector(".iv-sheet:not(.loading)")
    page.locator("[data-nav=queue]").click()  # the queue's address without its filter
    page.wait_for_url(re.compile(r"/review\?show=all&sort=amount$"))
    page.wait_for_load_state("networkidle")
    page.wait_for_function("y => Math.abs(window.scrollY - y) < 40", arg=y)
    assert errors == []


def _taxable(page, kind):
    return page.eval_on_selector_all(
        "[data-grid=taxes] > tr",
        "(rows, k) => rows.filter(r => r.querySelector('[name=tax_type]').value === k)"
        ".map(r => r.querySelector('[name=tax_taxable]').value)[0]",
        kind,
    )


def test_in_a_browser_a_tax_lines_taxable_follows_the_lines_ticked_with_it_b610(browser, store):
    """B-610: ticking or unticking a line's GST box left the GST line's taxable amount as it was. The boxes are one
    tax code a line now (D-64): the taxable follows the lines whose code carries the tax."""
    invoice_id = _with_gst(store)
    page, errors = _review(browser, invoice_id)
    kind = "GST" if _taxable(page, "GST") else "HST"
    rows = _lines(page)
    amounts = [float(rows.nth(i).locator("[name=line_amount]").input_value()) for i in range(rows.count())]
    codes = [rows.nth(i).locator("select[data-tax-code]").input_value() for i in range(rows.count())]
    ticked = [kind in code.split("+") for code in codes]
    assert (
        ticked[0]
        and abs(float(_taxable(page, kind)) - sum(a for a, t in zip(amounts, ticked, strict=True) if t)) < 0.01
    )
    code = rows.first.locator("select[data-tax-code]")
    with _checked(page):
        code.select_option("EXEMPT")
    expected = sum(a for a, t in zip(amounts, ticked, strict=True) if t) - amounts[0]
    assert _taxable(page, kind) == f"{expected:.2f}"
    # a taxable typed by the clerk is never overwritten: the tax line's menu offers to update it
    at = page.eval_on_selector_all(
        "[data-grid=taxes] select[name=tax_type]", "(s, k) => s.findIndex(x => x.value === k)", kind
    )
    tax = page.locator("[data-grid=taxes] > tr").nth(at)
    with page.expect_response(lambda r: r.url.endswith("/check")):  # its findings redrawn before the next change
        tax.locator("[name=tax_taxable]").fill("100.00")
    page.wait_for_load_state("networkidle")
    code.select_option(codes[0])
    assert _taxable(page, kind) == "100.00"
    _menu(page, tax.locator("td").first)
    _item(page, "sync").click()
    assert _taxable(page, kind) == f"{expected + amounts[0]:.2f}"
    assert errors == []


def _posted(request, name):
    return re.findall(rf'name="{re.escape(name)}"\r\n\r\n([^\r]*)\r\n', request.post_data or "")


def test_in_a_browser_a_duplicated_line_posts_its_own_key_and_its_own_taxes_b620(browser, store):
    """B-620: a duplicated line posted the original's line_key, so the server read the copy's taxes from the
    original's boxes (line_tax_<key>), and deleting the original took the copy's taxes with it. Each row posts its
    own tax code now (D-64), in its place."""
    invoice_id = _with_gst(store)
    page, errors = _review(browser, invoice_id)
    n = _lines(page).count()
    kind = _lines(page).first.locator("select[data-tax-code]").input_value()
    _menu(page, _lines(page).first.locator("[data-del-row]"))
    _item(page, "duplicate").click()
    copy_row = _lines(page).nth(1)
    keys = page.eval_on_selector_all("[data-grid=lines] [name=line_key]", "s => s.map(x => x.value)")
    assert len(set(keys)) == n + 1, keys
    copy_key = copy_row.locator("[name=line_key]").input_value()
    assert copy_row.get_attribute("data-row") == copy_key
    assert copy_row.locator("select[data-tax-code]").input_value() == kind
    # the original goes: the copy keeps its tax code, as posted
    _menu(page, _lines(page).first.locator("[data-del-row]"))
    with _checked(page) as sent:
        _item(page, "delete").click()
    assert _posted(sent.value, "line_key")[0] == copy_key
    assert _posted(sent.value, "line_tax_code")[0] == kind
    assert errors == []


def _plain_word(page, invoice_id, pattern=r"[A-Za-z]{4,}"):
    """A word on page 1 outside every field's box and every line item: (word, x, y in the window)."""
    data = json.loads(page.inner_text("#viewer-data"))
    boxes = [b for f in data["fields"] for b in f.get("boxes") or []] + [
        b for li in data["line_items"] for b in li.get("boxes") or []
    ]
    words = page.evaluate(f"fetch('/review/{invoice_id}/words').then(r => r.json()).then(a => a.words[0])")

    def inside(w):
        cx, cy = (w["b"][0] + w["b"][2]) / 2, (w["b"][1] + w["b"][3]) / 2
        return any(b[0] == 1 and b[1] - 0.01 <= cx <= b[3] + 0.01 and b[2] - 0.01 <= cy <= b[4] + 0.01 for b in boxes)

    word = min((w for w in words if re.fullmatch(pattern, w["t"]) and not inside(w)), key=lambda w: w["b"][1])
    frame = page.locator(".iv-frame").bounding_box()
    x = frame["x"] + (word["b"][0] + word["b"][2]) / 2 * frame["width"]
    y = frame["y"] + (word["b"][1] + word["b"][3]) / 2 * frame["height"]
    return word, x, y


def _box_center(page, field):
    r = page.locator(f".iv-f[data-field={field}] rect.iv-box").first.bounding_box()
    return r["x"] + r["width"] / 2, r["y"] + r["height"] / 2


def test_in_a_browser_a_double_click_fills_only_the_detail_chosen_and_never_from_a_fields_box_b621(browser, store):
    """B-621: a double-click on the GST/HST number's box put that number in Invoice # (the detail focused last), and
    approving would have taught the supplier template so."""
    invoice_id = store.list_invoices(REVIEW)[0]["id"]
    page, errors = _review(browser, invoice_id)
    teaches: list[str] = []
    page.on("request", lambda r: teaches.append(r.url) if r.url.endswith("/teach") else None)
    asked: list[str] = []
    page.on("dialog", lambda d: (asked.append(d.message), d.dismiss()))
    number = page.locator('.inv-details input[name="invoice_number"]')
    before = number.input_value()
    other = page.locator(".iv-f[data-field]:not([data-field=invoice_number])").first.get_attribute("data-field")
    number.click()
    page.mouse.dblclick(*_box_center(page, other))  # another field's box: it is shown, nothing is filled
    page.wait_for_timeout(400)
    assert number.input_value() == before and teaches == []
    # a plain word, but the detail was left for something else: nothing is filled
    blank = next(
        n
        for n in ("qst_registration_number", "pst_registration_number", "po_number", "due_date")
        if not page.locator(f'.inv-details [data-field="{n}"] .inv-sure').count()
    )
    box = page.locator(f'.inv-details input[name="{blank}"]')
    box.click()
    page.locator(".inv-details .rvw-group").first.click()  # clicked elsewhere since
    word, x, y = _plain_word(page, invoice_id)
    page.mouse.dblclick(x, y)
    page.wait_for_timeout(400)
    assert teaches == [] and "Click in an invoice detail first" in page.inner_text("#toasts")
    # a verified detail is never replaced without asking
    verified = page.locator(".inv-details [data-field]:has(.inv-sure.ok) input[form=inv]").first
    assert verified.count()
    was = verified.input_value()
    verified.click()
    word, x, y = _plain_word(page, invoice_id)
    page.mouse.dblclick(x, y)
    page.wait_for_timeout(400)
    assert asked and "verified" in asked[0] and verified.input_value() == was and teaches == []
    assert errors == []


def test_in_a_browser_a_new_blank_tax_line_does_not_take_the_ticked_sum_b622(browser, store):
    """B-622: a tax line added blank took the sum of the ticked lines at the next edit, so the tax counted twice."""
    invoice_id = _with_gst(store)
    page, errors = _review(browser, invoice_id)
    kind = "GST" if _taxable(page, "GST") else "HST"
    before = _taxable(page, kind)
    page.click("[data-add-row=taxes]")
    new = page.locator("[data-grid=taxes] > tr").last
    new.locator("[name=tax_type]").select_option(kind)
    assert new.locator("[name=tax_taxable]").input_value() == ""
    code = _lines(page).first.locator("select[data-tax-code]")  # the line's tax code (D-64)
    page.wait_for_load_state("networkidle")
    with _checked(page):
        code.select_option("EXEMPT")
    assert new.locator("[name=tax_taxable]").input_value() == ""  # stays blank: not a second copy of the sum
    assert _taxable(page, kind) != before  # the tax line that was the sum follows
    assert errors == []


def test_in_a_browser_enter_goes_past_a_detail_folded_away_b623(browser, store):
    """B-623: Enter in Currency stayed there: the next box, Place of supply, is folded away in a closed <details>."""
    invoice_id = store.list_invoices(REVIEW)[0]["id"]
    page, errors = _review(browser, invoice_id)
    assert page.locator(".inv-supply-pick").get_attribute("open") is None
    page.locator('.inv-details input[name="currency"]').focus()
    page.keyboard.press("Enter")
    assert page.evaluate("document.activeElement.name") == "original_invoice_number"
    page.keyboard.press("Shift+Enter")
    assert page.evaluate("document.activeElement.name") == "currency"
    assert errors == []


def test_in_a_browser_a_right_click_inside_a_text_box_is_the_browsers_paste_menu_b624(browser, store):
    """B-624: a right-click inside an empty text box showed the field's menu, without Paste or the spelling. (A line's
    boxes: the browser's while in use, else the line's menu, B-628.)"""
    invoice_id = store.list_invoices(REVIEW)[0]["id"]
    page, errors = _review(browser, invoice_id)
    for box in (
        page.locator('.inv-details input[name="qst_registration_number"]'),  # empty: nothing selected
        page.locator('.inv-details input[name="invoice_number"]'),
    ):
        box.click(button="right")
        page.wait_for_timeout(150)
        assert page.locator(MENU).count() == 0, box
        assert not page.evaluate("window.__prevented"), box
    # the field's menu: its name, or Shift+F10 in the box
    _menu(page, page.locator('.inv-details [data-field="invoice_number"] .lbl'))
    assert _item(page, "clear").count() == 1
    page.keyboard.press("Escape")
    page.locator('.inv-details input[name="invoice_number"]').focus()
    page.keyboard.press("Shift+F10")
    page.wait_for_selector(MENU)
    assert errors == []


def test_in_a_browser_a_note_is_posted_once_however_often_it_is_sent_b625(browser, store):
    """B-625: with an edit waiting to be checked, Enter twice in the note box (or a double click on Add note) posted
    the note twice: each submit waited for the check, then went."""
    invoice_id = store.list_invoices(REVIEW)[0]["id"]
    page, errors = _review(browser, invoice_id)
    notes: list[str] = []
    page.on("request", lambda r: notes.append(r.url) if r.url.endswith("/note") else None)
    page.evaluate("document.querySelector('.inv-notes details').open = true")
    box = page.locator("[data-note-form] input[name=text]")
    page.locator('.inv-details input[name="po_number"]').fill("PO-1234")  # an edit waiting to be checked
    box.fill("first note")
    box.press("Enter")
    box.press("Enter")
    page.wait_for_selector("[data-notes] .ev")
    page.wait_for_load_state("networkidle")
    assert len(notes) == 1, notes
    page.locator('.inv-details input[name="po_number"]').fill("PO-5678")
    box.fill("second note")
    page.locator("[data-note-form] button[type=submit]").dblclick()
    page.wait_for_function("() => document.querySelectorAll('[data-notes] .ev').length === 2")
    page.wait_for_load_state("networkidle")
    assert len(notes) == 2, notes
    assert page.locator("[data-notes] .ev").count() == 2
    assert errors == []


def test_in_a_browser_the_queue_restores_only_the_view_an_invoice_was_opened_from_b626(browser, store):
    """B-626: the queue put back a filter it was left with long before (an invoice opened from elsewhere), and sent an
    address with its own message (?flash=) to the old filter, losing the message."""
    chromium, base = browser
    page = chromium.new_page(viewport=LAPTOP)
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    invoice_id = store.list_invoices(REVIEW)[0]["id"]
    page.goto(base + "/review?show=all&sort=amount")  # looked at, left without opening an invoice from it
    page.goto(base + "/search")
    page.goto(base + f"/review/{invoice_id}")
    page.wait_for_selector(".iv-sheet:not(.loading)")
    page.locator("[data-nav=queue]").click()
    page.wait_for_load_state("networkidle")
    assert page.url.endswith("/review"), page.url
    # opened from the queue, but the queue's address has its own message: it is shown, not replaced
    page.goto(base + "/review?show=all&sort=amount")
    page.eval_on_selector(".rq-list li:first-child a.rq-row", "a => a.click()")
    page.wait_for_selector(".iv-sheet:not(.loading)")
    page.goto(base + "/review?flash=nope", referer=base + f"/review/{invoice_id}")
    page.wait_for_load_state("networkidle")
    assert "show=all" not in page.url, page.url
    assert errors == []


def test_in_a_browser_a_detail_left_long_ago_is_not_filled_by_a_double_click_b627(browser, store):
    """B-627: the double-click's own first click took the focus from the detail, and that moment counted as "just
    left": a detail clicked in long before (nothing typed) was filled by a double-clicked word."""
    invoice_id = store.list_invoices(REVIEW)[0]["id"]
    page, errors = _review(browser, invoice_id)
    page.evaluate("window.AP_ARMED_MS = 400")  # the window, shortened for the test (5 s in use)
    teaches: list[str] = []
    page.on("request", lambda r: teaches.append(r.url) if r.url.endswith("/teach") else None)
    name = "qst_registration_number"
    box = page.locator(f'.inv-details input[name="{name}"]')
    box.click()  # clicked in, nothing typed
    word, x, y = _plain_word(page, invoice_id)
    page.wait_for_timeout(900)  # longer than the window
    page.mouse.dblclick(x, y)
    page.wait_for_timeout(400)
    assert box.input_value() == "" and teaches == []
    # typed in just now: the detail is the one meant
    box.click()
    box.type("x")
    box.press("Backspace")
    word, x, y = _plain_word(page, invoice_id)
    with page.expect_response(lambda r: r.url.endswith("/teach")):
        page.mouse.dblclick(x, y)
    page.wait_for_function("n => document.querySelector(`.inv-details input[name=${n}]`).value !== ''", arg=name)
    assert errors == []


def test_in_a_browser_a_right_click_on_a_lines_box_opens_the_line_menu_unless_it_is_in_use_b628(browser, store):
    """B-628: with every box the browser's (B-624), a right-click on a line opened its menu on only a third of it, and
    on a tax line only on its two drop-downs."""
    invoice_id = _with_gst(store)
    page, errors = _review(browser, invoice_id)
    row = _lines(page).first
    for cell in ("[name=line_number]", "textarea[name=line_description]", "[name=line_amount]", "[name=line_quantity]",
                 ".pk-input[data-picker-name=line_gl]"):  # fmt: skip
        _menu(page, row.locator(cell))
        assert _item(page, "delete").count() == 1, cell
        page.keyboard.press("Escape")
    tax = page.locator("[data-grid=taxes] > tr").first
    for cell in ("[name=tax_rate_pct]", "[name=tax_taxable]", "[name=tax_amount]"):
        _menu(page, tax.locator(cell))
        assert _item(page, "recalc").count() == 1, cell
        page.keyboard.press("Escape")
    # a box in use (focused, typing): the browser's menu, for Paste
    amount = row.locator("[name=line_amount]")
    amount.click()
    amount.click(button="right")
    page.wait_for_timeout(150)
    assert page.locator(MENU).count() == 0
    description = row.locator("textarea[name=line_description]")
    description.click()
    description.click(button="right")
    page.wait_for_timeout(150)
    assert page.locator(MENU).count() == 0
    # the invoice details stay as they are: the browser's menu in the box, the field's menu on its name
    page.locator('.inv-details input[name="po_number"]').click(button="right")
    page.wait_for_timeout(150)
    assert page.locator(MENU).count() == 0
    assert errors == []
