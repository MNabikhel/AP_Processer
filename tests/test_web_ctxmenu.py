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
    assert [(i.severity, i.code) for i in out] == [("info", "GST_HST_NUMBER_ABSENT"), ("error", "MISSING_INVOICE_NUMBER"),
                                                   ("warning", "QST_NUMBER_MISSING")]  # fmt: skip


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
    output["vendor_name"], output["invoice_number"] = first["ai_output"]["vendor_name"], first["ai_output"]["invoice_number"]
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
    for spec, what in SHORTCUT_ROWS:
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


def _checked(page):
    return page.expect_request(lambda r: r.url.endswith("/check") and r.method == "POST")


def test_in_a_browser_right_click_a_detail_clear_it_and_undo_puts_it_back(browser, store):
    invoice_id = store.list_invoices(REVIEW)[0]["id"]
    page, errors = _review(browser, invoice_id)
    box = page.locator('.inv-details input[name="invoice_number"]')
    before = box.input_value()
    assert before
    menu = _menu(page, box)
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
    _menu(page, box)
    _item(page, "clear").click()
    page.locator("body").click(position={"x": 5, "y": 5})
    page.keyboard.press("Control+z")
    assert box.input_value() == before
    assert errors == []
