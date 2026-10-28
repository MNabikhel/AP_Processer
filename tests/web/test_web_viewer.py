"""The review screen's page viewer as a document viewer (B-580, D-57): zoom from 50% to 400% (buttons, keys, Ctrl +
wheel or a pinch where the pointer is, Fit width, Fit page), the Hand to move the page (its button, H, Space held, the
middle button), Rotate (on screen only), Page Up / Page Down, and a sharper picture of the page when zoomed in
(``/review/<id>/page/<n>.png?scale=2|3``)."""

from __future__ import annotations

import io
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from ap_coder.core.config import Settings
from ap_coder.core.store import REVIEW, Store
from ap_coder.web import document
from ap_coder.web.app import create_app
from tests.browser import browser_on, wait_until
from tests.conftest import copy_database

LAPTOP = {"width": 1366, "height": 768}
ZOOM_KEY = "apc-invoice-viewer-zoom"


@pytest.fixture(scope="module")
def demo_db(cost_centers_demo_template):
    """The demo invoices and the sample cost centers (the demo has none: D-53), made once a run (tests/conftest.py)."""
    return cost_centers_demo_template


@pytest.fixture
def store(demo_db, tmp_path):
    return copy_database(demo_db, tmp_path / "private" / "ap_coder.db")


def _app(store: Store):
    s = Settings()
    return create_app(replace(s, llm=replace(s.llm, provider="off")), store, start_reader=False)


@pytest.fixture
def client(store):
    return TestClient(_app(store), base_url="http://localhost")


@pytest.fixture(autouse=True)
def fresh_pictures():
    document._png_cached.cache_clear()
    yield
    document._png_cached.cache_clear()


def _id(store: Store, vendor: str) -> int:
    return next(i["id"] for i in store.list_invoices(REVIEW) if vendor in (i["vendor_name"] or ""))


def _size(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as img:
        return img.size


# --- The page's picture at 1x, 2x, 3x ---------------------------------------------------------------------------------


def test_the_page_picture_comes_at_three_scales(client, store):
    inv = _id(store, "Cascade")
    plain = client.get(f"/review/{inv}/page/1.png")
    one = client.get(f"/review/{inv}/page/1.png?scale=1")
    two = client.get(f"/review/{inv}/page/1.png?scale=2")
    three = client.get(f"/review/{inv}/page/1.png?scale=3")
    for r in (plain, one, two, three):
        assert r.status_code == 200 and r.headers["content-type"] == "image/png"
        assert r.headers["cache-control"] == "private, max-age=86400"  # the scale is in the address the browser keeps
    assert plain.content == one.content
    w1, h1 = _size(one.content)
    assert _size(two.content) == (2 * w1, 2 * h1)
    assert _size(three.content) == (3 * w1, 3 * h1)


@pytest.mark.parametrize("scale", ["0", "4", "-2", "2.5", "abc", "", "99999999999999999999"])
def test_a_scale_that_is_not_1_2_or_3_gives_the_ordinary_picture(client, store, scale):
    inv = _id(store, "Cascade")
    plain = client.get(f"/review/{inv}/page/1.png").content
    response = client.get(f"/review/{inv}/page/1.png?scale={scale}")
    assert response.status_code == 200 and response.content == plain


def test_the_sharper_pictures_are_capped(client, store, monkeypatch):
    monkeypatch.setattr(document, "DPI", 1200)
    inv = _id(store, "Cascade")
    for scale, cap in (("1", 2400), ("2", 4800), ("3", 6000)):
        assert max(_size(client.get(f"/review/{inv}/page/1.png?scale={scale}").content)) == cap


def test_each_scale_is_drawn_once_and_kept(client, store):
    inv = _id(store, "Cascade")
    for _ in range(2):
        for scale in ("1", "2"):
            assert client.get(f"/review/{inv}/page/1.png?scale={scale}").status_code == 200
    info = document._png_cached.cache_info()
    assert (info.misses, info.hits) == (2, 2)


def test_the_page_tells_the_viewer_the_address_without_a_scale(client, store):
    inv = _id(store, "Cascade")
    page = client.get(f"/review/{inv}").text
    assert f"/review/{inv}/page/1.png?v=" in page and "scale=" not in page.split('id="viewer-data"', 1)[1][:2000]


# --- In a browser ---------------------------------------------------------------------------------------------------


@pytest.fixture
def browser(chromium, store):
    with browser_on(chromium, _app(store)) as opened:
        yield opened


def _open(browser, store, vendor="Cascade", zoom=None, viewport=LAPTOP, **context):
    """The review screen of a demo invoice, its page loaded; ``zoom``: the zoom saved from last time."""
    chromium, base = browser
    page = chromium.new_page(viewport=viewport, **context)
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    if zoom is not None:
        page.add_init_script(f"try {{ localStorage.setItem({ZOOM_KEY!r}, {zoom!r}); }} catch (e) {{}}")
    page.goto(f"{base}/review/{_id(store, vendor)}")
    page.wait_for_selector(".iv-sheet:not(.loading)")
    return page, errors


def _readout(page) -> int:
    return int(page.inner_text(".iv-zpct").strip().rstrip("%"))


def _stuck(page):
    """Scroll the dashboard so the Document card sticks at the top: the viewer at its tallest."""
    page.evaluate("window.scrollTo(0, 1000)")
    page.wait_for_timeout(250)


def _scroll(page) -> list[float]:
    return page.evaluate(
        "(() => { const s = document.querySelector('.iv-stage'); return [s.scrollLeft, s.scrollTop]; })()"
    )


def test_in_a_browser_plus_and_minus_step_the_zoom_and_the_percent_goes_back_to_fit(browser, store):
    page, errors = _open(browser, store)
    fit = _readout(page)
    assert page.get_attribute("[aria-label='Fit width']", "aria-pressed") == "true"
    page.click("[aria-label='Zoom in']")
    up = _readout(page)
    assert up > fit and up in (50, 67, 75, 90, 100, 125, 150, 200, 300, 400)
    page.click("[aria-label='Zoom in']")
    assert _readout(page) > up
    page.click("[aria-label='Zoom out']")
    assert _readout(page) == up
    assert page.get_attribute("[aria-label='Fit width']", "aria-pressed") == "false"
    page.click(".iv-zpct")  # the percent: back to Fit width
    assert _readout(page) == fit and page.get_attribute("[aria-label='Fit width']", "aria-pressed") == "true"
    for _ in range(12):
        if page.is_enabled("[aria-label='Zoom in']"):
            page.click("[aria-label='Zoom in']")
    assert _readout(page) == 400 and not page.is_enabled("[aria-label='Zoom in']")
    assert errors == []


def test_in_a_browser_ctrl_wheel_zooms_the_page_where_the_pointer_is_and_not_the_browser(browser, store):
    page, errors = _open(browser, store, zoom="100")
    _stuck(page)
    page.evaluate("window.__wheel = []; window.addEventListener('wheel', (e) => __wheel.push(e.defaultPrevented))")
    frame = page.locator(".iv-frame").bounding_box()
    stage = page.locator(".iv-stage").bounding_box()
    x, y = stage["x"] + stage["width"] * 0.4, stage["y"] + stage["height"] * 0.3
    before = [(x - frame["x"]) / frame["width"], (y - frame["y"]) / frame["height"]]
    page.mouse.move(x, y)
    page.keyboard.down("Control")
    for _ in range(3):
        page.mouse.wheel(0, -100)
        page.wait_for_timeout(60)
    page.keyboard.up("Control")
    page.wait_for_timeout(200)
    zoom = _readout(page)
    assert 140 <= zoom <= 400, zoom
    assert page.evaluate("window.__wheel") and all(page.evaluate("window.__wheel"))  # the browser does not zoom
    assert page.evaluate("window.visualViewport.scale") == 1
    frame = page.locator(".iv-frame").bounding_box()
    after = [(x - frame["x"]) / frame["width"], (y - frame["y"]) / frame["height"]]
    assert after == pytest.approx(before, abs=0.02)  # the point under the pointer stayed under it
    # A plain wheel still scrolls, without zooming.
    page.mouse.wheel(0, 200)
    page.wait_for_timeout(200)
    assert _readout(page) == zoom
    # A pinch on a trackpad (small steps, ctrlKey): a smooth zoom out.
    page.keyboard.down("Control")
    page.mouse.wheel(0, 5)
    page.keyboard.up("Control")
    page.wait_for_timeout(100)
    assert zoom * 0.9 <= _readout(page) < zoom
    assert errors == []


def test_in_a_browser_fit_page_shows_the_whole_page(browser, store):
    page, errors = _open(browser, store, zoom="200")
    _stuck(page)
    page.click("[aria-label='Fit page']")
    frame = page.locator(".iv-frame").bounding_box()
    room = page.evaluate(
        "(() => { const s = document.querySelector('.iv-stage'); const r = s.getBoundingClientRect();"
        " return [r.left + s.clientLeft, r.top + s.clientTop, s.clientWidth, s.clientHeight,"
        " s.scrollHeight, s.scrollWidth]; })()"
    )
    left, top, width, height, scroll_h, scroll_w = room
    assert frame["x"] >= left and frame["x"] + frame["width"] <= left + width + 0.5, (frame, room)
    assert frame["y"] >= top and frame["y"] + frame["height"] <= top + height + 0.5, (frame, room)
    assert scroll_h <= height + 1 and scroll_w <= width + 1
    assert page.get_attribute("[aria-label='Fit page']", "aria-pressed") == "true"
    assert page.evaluate(f"localStorage.getItem({ZOOM_KEY!r})") == "page"
    # The fit follows the window.
    fit = _readout(page)
    page.set_viewport_size({"width": 1366, "height": 640})
    wait_until(page, lambda: _readout(page) < fit)
    assert _readout(page) < fit
    assert errors == []


def test_in_a_browser_the_hand_moves_the_page_and_picks_nothing(browser, store):
    page, errors = _open(browser, store, zoom="200")
    _stuck(page)
    page.click("[aria-label='Hand']")
    assert page.get_attribute("[aria-label='Hand']", "aria-pressed") == "true"
    assert page.evaluate("getComputedStyle(document.querySelector('.iv-sheet')).cursor") == "grab"
    start = _scroll(page)
    stage = page.locator(".iv-stage").bounding_box()
    page.mouse.move(stage["x"] + 300, stage["y"] + 300)
    page.mouse.down()
    page.mouse.move(stage["x"] + 150, stage["y"] + 120, steps=8)
    page.mouse.up()
    page.wait_for_timeout(300)
    end = _scroll(page)
    assert end[0] > start[0] + 100 and end[1] > start[1] + 100, (start, end)
    assert page.locator(".inv-chooser").count() == 0 and page.locator(".iv-drag").count() == 0
    # H turns it off again: a drag picks words again.
    page.locator(".iv-stage").focus()
    page.keyboard.press("h")
    assert page.get_attribute("[aria-label='Hand']", "aria-pressed") == "false"
    assert errors == []


def test_in_a_browser_space_held_and_the_middle_button_move_the_page(browser, store):
    page, errors = _open(browser, store, zoom="200")
    _stuck(page)
    stage = page.locator(".iv-stage").bounding_box()
    page.mouse.move(stage["x"] + 300, stage["y"] + 300)
    window_y = page.evaluate("window.scrollY")
    start = _scroll(page)
    page.keyboard.down(" ")
    assert page.evaluate("document.querySelector('.iv').classList.contains('hand')")
    page.mouse.down()
    page.mouse.move(stage["x"] + 200, stage["y"] + 180, steps=8)
    page.mouse.up()
    page.keyboard.up(" ")
    page.wait_for_timeout(300)
    moved = _scroll(page)
    assert moved[0] > start[0] + 50 and moved[1] > start[1] + 50, (start, moved)
    assert page.evaluate("window.scrollY") == window_y  # Space did not scroll the dashboard page
    assert not page.evaluate("document.querySelector('.iv').classList.contains('hand')")
    assert page.locator(".inv-chooser").count() == 0
    # The middle button always moves the page.
    page.mouse.move(stage["x"] + 200, stage["y"] + 200)
    page.mouse.down(button="middle")
    page.mouse.move(stage["x"] + 300, stage["y"] + 300, steps=8)
    page.mouse.up(button="middle")
    page.wait_for_timeout(200)
    back = _scroll(page)
    assert back[0] < moved[0] - 50 and back[1] < moved[1] - 50, (moved, back)
    assert page.locator(".inv-chooser").count() == 0
    # Without the Hand, a drag still picks the words in its box.
    page.mouse.move(stage["x"] + 100, stage["y"] + 100)
    page.mouse.down()
    page.mouse.move(stage["x"] + 400, stage["y"] + 200, steps=8)
    page.mouse.up()
    page.wait_for_selector(".iv-drag")
    assert errors == []


def _word(page, store, text):
    """A word on page 1 whose text holds ``text``: its middle as page fractions."""
    words = page.evaluate(f"fetch('/review/{_id(store, 'Cascade')}/words').then((r) => r.json())")["words"][0]
    w = next(w for w in words if text in w["t"])
    x0, y0, x1, y1 = w["b"]
    return w["t"], (x0 + x1) / 2, (y0 + y1) / 2


def _on_screen(page, u, v, turns):
    """Where page fractions (u, v) are in the window, the page turned ``turns`` quarter turns clockwise."""
    a, b = {0: (u, v), 1: (1 - v, u), 2: (1 - u, 1 - v), 3: (v, 1 - u)}[turns]
    f = page.locator(".iv-frame").bounding_box()
    return f["x"] + a * f["width"], f["y"] + b * f["height"]


@pytest.mark.parametrize("turns", [1, 2, 3])
def test_in_a_browser_a_turned_page_still_picks_the_right_words(browser, store, turns):
    page, errors = _open(browser, store, viewport={"width": 1400, "height": 1000})
    _stuck(page)
    for _ in range(turns):
        page.click("[aria-label^='Rotate']")
    page.click("[aria-label='Fit page']")
    word, u, v = _word(page, store, "30981")
    frame = page.locator(".iv-frame").bounding_box()
    sheet = page.locator(".iv-sheet").bounding_box()
    assert sheet == pytest.approx(frame, abs=1)  # the turned page fills its frame
    # A box dragged around the word: the word is picked (the rotation undone).
    x, y = _on_screen(page, u, v, turns)
    page.mouse.move(x - 12, y - 12)
    page.mouse.down()
    page.mouse.move(x + 12, y + 12, steps=6)
    page.mouse.up()
    page.wait_for_selector(".inv-chooser")
    assert word in page.inner_text(".inv-chooser q")
    page.keyboard.press("Escape")
    page.wait_for_selector(".inv-chooser", state="detached")
    # Teach a field: a click on the word picks that word.
    page.click("[data-teach] > summary")
    page.select_option("[data-teach-field]", "invoice_number")
    page.click("[data-teach-go]")
    page.wait_for_selector(".iv-w")
    x, y = _on_screen(page, u, v, turns)
    page.mouse.click(x, y)
    page.wait_for_selector(".iv-w.on")
    assert page.locator(".iv-w.on").count() == 1 and page.inner_text(".iv-teach-picked q") == word
    page.keyboard.press("Escape")  # Esc still stops teaching
    page.wait_for_selector(".iv-teach", state="hidden")
    assert page.locator(".inv-chooser").count() == 0 and errors == []


def test_in_a_browser_rotate_is_on_screen_only_and_per_invoice(browser, store):
    page, errors = _open(browser, store, vendor="Mont-Royal")
    sent: list[str] = []
    page.on("request", lambda r: r.method != "GET" and sent.append(r.url))
    page.click("[aria-label^='Rotate']")
    assert "rotate(90deg)" in page.evaluate("document.querySelector('.iv-sheet').style.transform")
    frame = page.locator(".iv-frame").bounding_box()
    assert frame["width"] > frame["height"]  # a letter page on its side
    page.keyboard.press("PageDown")
    assert "Page 2 of 2" in page.inner_text(".iv-where")
    assert page.evaluate("document.querySelector('.iv-sheet').style.transform") == ""  # page 2 upright
    page.keyboard.press("PageUp")
    assert "Page 1 of 2" in page.inner_text(".iv-where")
    assert "rotate(90deg)" in page.evaluate("document.querySelector('.iv-sheet').style.transform")
    page.wait_for_timeout(300)
    assert sent == []  # nothing is sent to the server
    page.reload()
    page.wait_for_selector(".iv-sheet:not(.loading)")
    assert page.evaluate("document.querySelector('.iv-sheet').style.transform") == ""  # and nothing kept
    assert errors == []


def test_in_a_browser_at_200_percent_the_page_is_asked_for_twice_as_sharp(browser, store):
    chromium, _ = browser
    pictures: list[str] = []
    page, errors = _open(browser, store)
    page.on("request", lambda r: "/page/" in r.url and pictures.append(r.url))
    assert "scale=" not in page.get_attribute(".iv-img", "src")  # at Fit, the ordinary picture
    page.click(".iv-zpct")  # 100%
    page.click("[aria-label='Zoom in']")  # 125%
    page.click("[aria-label='Zoom in']")  # 150%
    page.click("[aria-label='Zoom in']")  # 200%
    assert _readout(page) == 200
    page.wait_for_function("document.querySelector('.iv-img').getAttribute('src').includes('scale=2')")
    assert any("scale=2" in u for u in pictures), pictures
    assert not page.locator(".iv-sheet.loading").count()  # swapped in once loaded: never blank
    page.click("[aria-label='Zoom in']")  # 300%
    page.click("[aria-label='Zoom in']")  # 400%
    page.wait_for_function("document.querySelector('.iv-img').getAttribute('src').includes('scale=3')")
    page.click(".iv-zpct")  # back to Fit: the sharp picture is kept, nothing asked for again
    count = len(pictures)
    page.wait_for_timeout(400)
    assert len(pictures) == count and "scale=3" in page.get_attribute(".iv-img", "src")
    # A new page: straight at the scale the zoom needs (on a screen of 2 pixels per CSS pixel, 100% is 1.6x).
    retina, _ = _open(browser, store, zoom="100", device_scale_factor=2)
    assert "scale=2" in retina.get_attribute(".iv-img", "src")
    assert errors == []


def test_in_a_browser_the_keys_zoom_fit_and_hold_the_hand_but_not_while_typing(browser, store):
    page, errors = _open(browser, store, vendor="Mont-Royal")
    fit = _readout(page)
    page.locator(".iv-stage").focus()
    page.keyboard.press("+")
    assert _readout(page) > fit
    page.keyboard.press("=")
    page.keyboard.press("-")
    page.keyboard.press("Minus")
    page.keyboard.press("9")
    assert page.get_attribute("[aria-label='Fit page']", "aria-pressed") == "true"
    page.keyboard.press("0")
    assert page.get_attribute("[aria-label='Fit width']", "aria-pressed") == "true"
    page.keyboard.press("Shift+0")
    assert page.get_attribute("[aria-label='Fit page']", "aria-pressed") == "true"
    page.keyboard.press("h")
    assert page.get_attribute("[aria-label='Hand']", "aria-pressed") == "true"
    page.keyboard.press("h")
    page.keyboard.press("PageDown")
    assert "Page 2 of 2" in page.inner_text(".iv-where")
    # Typing in a field, a select, a text area or an editable element: the keys are the field's.
    assert page.get_attribute("#iv", "data-zoom") == "page"
    page.evaluate("document.body.insertAdjacentHTML('beforeend', '<div id=ed contenteditable>x</div>')")
    for target in ("[form=inv][name=po_number]", "textarea[name=line_description] >> nth=0", "#ed"):
        page.locator(target).focus()  # (a field of the form shows its box: maybe on page 1)
        where = page.inner_text(".iv-where")
        for key in ("+", "=", "-", "0", "9", "h", "r", "PageDown", "PageUp", " "):
            page.keyboard.press(key)
        assert page.get_attribute("#iv", "data-zoom") == "page", target  # (Fit page re-fits as the card rises)
        assert page.get_attribute("[aria-label='Hand']", "aria-pressed") == "false"
        assert page.inner_text(".iv-where") == where
        assert page.evaluate("document.querySelector('.iv-sheet').style.transform") == ""
    assert "+=-09hr" in page.input_value("[form=inv][name=po_number]")
    page.locator("select[name=line_gl] >> nth=0").focus()
    page.keyboard.press("0")
    assert page.get_attribute("#iv", "data-zoom") == "page"
    assert errors == []


@pytest.mark.parametrize(("saved", "shown"), [("150", "150%"), ("100", "100%"), ("fit", None), ("page", None),
                                               ("137", "137%"), ("900", None), ("junk", None)])  # fmt: skip
def test_in_a_browser_the_zoom_saved_before_still_loads(browser, store, saved, shown):
    page, errors = _open(browser, store, zoom=saved)
    if shown:
        assert page.inner_text(".iv-zpct") == shown
    else:
        pressed = "Fit page" if saved == "page" else "Fit width"
        assert page.get_attribute(f"[aria-label='{pressed}']", "aria-pressed") == "true"
    assert errors == []


@pytest.mark.parametrize("turns", [0, 1])
def test_in_a_browser_a_field_picked_in_the_list_shows_at_any_zoom(browser, store, turns):
    page, errors = _open(browser, store, zoom="300", reduced_motion="reduce")
    _stuck(page)
    if page.get_attribute(".iv-fold", "aria-expanded") == "false":
        page.click(".iv-fold")  # the field list, folded away on a laptop
    for _ in range(turns):
        page.click("[aria-label^='Rotate']")
    for field in ("grand_total", "vendor_name", "invoice_number"):
        row = page.locator(f".iv-row[data-field={field}]")
        if not row.count():
            continue
        row.click()
        page.wait_for_timeout(150)
        box = page.locator(f".iv-f[data-field={field}] .iv-box").first.bounding_box()
        stage = page.evaluate(
            "(() => { const s = document.querySelector('.iv-stage'); const r = s.getBoundingClientRect();"
            " return [r.left, r.top, r.left + s.clientWidth, r.top + s.clientHeight]; })()"
        )
        cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        assert stage[0] <= cx <= stage[2] and stage[1] <= cy <= stage[3], (field, box, stage)
    assert errors == []


def test_in_a_browser_the_view_tools_fit_on_a_laptop_beside_the_pages(browser, store):
    page, errors = _open(browser, store, vendor="Mont-Royal")
    tools = page.evaluate(
        "[...document.querySelectorAll('.iv-view > *')].map((e) => { const r = e.getBoundingClientRect();"
        " return [r.left, r.right, r.top, r.bottom]; })"
    )
    bar = page.locator(".iv-toolbar").bounding_box()
    assert len(tools) == 5  # zoom, fit, hand, rotate, pages
    assert all(t[2] == tools[0][2] for t in tools)  # one row
    assert all(t[0] >= bar["x"] and t[1] <= bar["x"] + bar["width"] for t in tools)
    assert all(tools[i][1] <= tools[i + 1][0] for i in range(len(tools) - 1))  # none over another
    for button in page.locator(".iv-view button").all():
        b = button.bounding_box()
        assert b["width"] >= 24 and b["height"] >= 24  # big enough to hit
    assert errors == []


def test_in_a_browser_space_elsewhere_on_the_review_screen_scrolls_as_usual(browser, store):
    """Space holds the Hand only with the pointer over the page or the focus in the Document card."""
    page, errors = _open(browser, store)
    page.evaluate("document.querySelector('#lines').scrollIntoView({block: 'center'})")  # under the details (D-63)
    start = page.evaluate("window.scrollY")
    lines = page.locator("#lines").bounding_box()
    page.mouse.move(lines["x"] + 20, lines["y"] + 20)  # over the line items, the focus on the page's body
    page.evaluate("document.activeElement && document.activeElement.blur()")
    page.keyboard.press(" ")
    page.wait_for_timeout(400)  # the smooth scroll ends (the next lines read where it stopped)
    assert page.evaluate("window.scrollY") > start  # the dashboard page scrolled
    assert not page.evaluate("document.querySelector('.iv').classList.contains('hand')")
    # The focus in the Document card (the page clicked, say), the pointer elsewhere: Space holds the Hand.
    page.evaluate("window.scrollTo(0, 0)")
    page.locator(".iv-stage").focus()
    y = page.evaluate("window.scrollY")
    page.keyboard.down(" ")
    assert page.evaluate("document.querySelector('.iv').classList.contains('hand')")
    page.keyboard.up(" ")
    assert page.evaluate("window.scrollY") == y
    assert not page.evaluate("document.querySelector('.iv').classList.contains('hand')")
    # A button in the card keeps Space for itself: it presses it.
    page.locator("[aria-label='Hand']").focus()
    page.keyboard.press(" ")
    assert page.get_attribute("[aria-label='Hand']", "aria-pressed") == "true"
    assert errors == []


@pytest.mark.parametrize("fit", ["Fit page", "Fit width"])
def test_in_a_browser_a_fit_does_not_drift_as_the_dashboard_page_scrolls(browser, store, fit):
    page, errors = _open(browser, store)
    page.click(f"[aria-label='{fit}']")
    shown = set()
    for y in (0, 150, 300, 600, 1200, 0):
        page.evaluate(f"window.scrollTo(0, {y})")
        page.wait_for_timeout(200)
        shown.add(_readout(page))
    assert len(shown) == 1, shown
    if fit == "Fit page":  # once stuck, the whole page shows
        _stuck(page)
        room = page.evaluate(
            "(() => { const s = document.querySelector('.iv-stage'); return [s.scrollHeight, s.clientHeight]; })()"
        )
        assert room[0] <= room[1] + 1, room
    page.set_viewport_size({"width": 1366, "height": 660})  # a resize still fits again
    page.wait_for_timeout(300)
    assert _readout(page) != next(iter(shown)) or fit == "Fit width"
    assert errors == []


# --- The second review's findings (B-591, B-592) --------------------------------------------------------------------


def _over_the_page(page):
    stage = page.locator(".iv-stage").bounding_box()
    page.mouse.move(stage["x"] + stage["width"] / 2, stage["y"] + min(stage["height"] / 2, 120))


def test_in_a_browser_space_on_a_box_or_button_of_the_form_is_theirs_with_the_mouse_over_the_page(browser, store):
    """B-591: the mouse resting on the page, Space on a line's tax box or on "Add a line" did nothing: the viewer took
    it for the Hand. (A line's taxes are a dropdown now, D-64: Space in a line's description box is the box's.)"""
    page, errors = _open(browser, store)
    page.evaluate("window.scrollTo(0, 0)")
    box = page.locator("[data-grid=lines] [name=line_description] >> nth=0")
    before = box.input_value()
    box.focus()
    page.keyboard.press("Control+End")  # the end of the text (a description wrapped on two lines: End is the first's)
    _over_the_page(page)
    page.keyboard.press(" ")
    assert box.input_value() == before + " "
    assert not page.evaluate("document.querySelector('.iv').classList.contains('hand')")
    rows = page.locator("[data-grid=lines] tr").count()
    page.locator("[data-add-row=lines]").focus()
    _over_the_page(page)
    page.keyboard.press(" ")
    page.wait_for_function(f"document.querySelectorAll('[data-grid=lines] tr').length === {rows + 1}")
    # The focus nowhere (the page's body), the mouse over the page: Space still holds the Hand.
    page.evaluate("document.activeElement.blur()")
    _over_the_page(page)
    page.keyboard.down(" ")
    assert page.evaluate("document.querySelector('.iv').classList.contains('hand')")
    page.keyboard.up(" ")
    assert not page.evaluate("document.querySelector('.iv').classList.contains('hand')")
    assert errors == []


def test_in_a_browser_page_down_turns_the_invoice_page_only_from_the_document_card(browser, store):
    """B-592: Page Up / Page Down turned the invoice's page from anywhere on the review screen, where they should
    scroll the screen."""
    page, errors = _open(browser, store, vendor="Mont-Royal")
    page.evaluate("document.querySelector('#lines').scrollIntoView({block: 'center'})")  # under the details (D-63)
    start = page.evaluate("window.scrollY")
    lines = page.locator("#lines").bounding_box()
    page.mouse.move(lines["x"] + 20, lines["y"] + 20)  # over the line items, the focus on the page's body
    page.evaluate("document.activeElement && document.activeElement.blur()")
    page.keyboard.press("PageDown")
    page.wait_for_timeout(400)
    assert "Page 1 of 2" in page.inner_text(".iv-where")
    assert page.evaluate("window.scrollY") > start  # the screen scrolled as usual
    # The focus on a button of the form, the mouse away: the screen's.
    page.evaluate("window.scrollTo(0, 0)")
    page.locator("[data-add-row=lines]").focus()
    page.mouse.move(lines["x"] + 20, lines["y"] + 20)
    page.keyboard.press("PageDown")
    assert "Page 1 of 2" in page.inner_text(".iv-where")
    # The mouse over the Document card: the invoice's page.
    page.evaluate("document.activeElement.blur(); window.scrollTo(0, 0)")
    _over_the_page(page)
    page.keyboard.press("PageDown")
    assert "Page 2 of 2" in page.inner_text(".iv-where")
    page.mouse.move(lines["x"] + 20, lines["y"] + 20)
    page.locator("[aria-label='Fit page']").focus()  # the focus in the card, the mouse away
    page.keyboard.press("PageUp")
    assert "Page 1 of 2" in page.inner_text(".iv-where")
    assert errors == []


# --- More room for the page, and the Hand and a turned page said on it (D-57) ---------------------------------------


def test_in_a_browser_the_page_tools_take_one_row_and_the_key_sits_on_the_fields_line(browser, store):
    """At 1366 x 768 the tools above the page took two rows (zoom…, then Next to check, To check only and the colour
    key): about 300 px of the page showed. Now one row of tools, the key and To check only on the Fields line."""
    for vendor in ("Mont-Royal", "Cascade"):  # two pages, then one
        page, errors = _open(browser, store, vendor=vendor)
        page.evaluate("window.scrollTo(0, 0)")
        bar = page.locator(".iv-toolbar").bounding_box()
        assert bar["height"] <= 46, (vendor, bar)
        zoom = page.locator(".iv-zoom").bounding_box()
        for tool in (".iv-step", ".iv-tool.iv-rotate") + ((".iv-pager",) if vendor == "Mont-Royal" else ()):
            box = page.locator(tool).bounding_box()
            assert abs((box["y"] + box["height"] / 2) - (zoom["y"] + zoom["height"] / 2)) <= 2, (vendor, tool)
        head = page.locator(".iv-panel-head").bounding_box()
        assert head["height"] <= 44, head
        for part in (".iv-panel-head h2", ".iv-legend", ".iv-switch", ".iv-fold"):
            box = page.locator(part).bounding_box()
            assert page.locator(part).is_visible(), part
            assert head["y"] <= box["y"] and box["y"] + box["height"] <= head["y"] + head["height"], part
        assert "Verified" in page.inner_text(".iv-legend")
        assert "verified" in page.inner_text(".iv-summary")  # the counts in words, for a screen reader
        stage = page.locator(".iv-stage").bounding_box()
        action = page.locator(".inv-actionbar").bounding_box()["y"]
        assert min(stage["y"] + stage["height"], action) - stage["y"] >= 330, (vendor, stage)
        page.click(".iv-switch")  # still works where it now is
        assert page.evaluate("document.querySelector('.iv').classList.contains('only-check')")
        assert errors == []


def test_in_a_browser_the_teach_bar_takes_two_lines_beside_the_form(browser, store):
    page, errors = _open(browser, store, vendor="Mont-Royal")
    page.click("[data-teach] > summary")
    page.select_option("[data-teach-field]", "invoice_number")
    page.click("[data-teach-go]")
    page.wait_for_selector(".iv-w")
    bar = page.locator(".iv-teach").bounding_box()
    use = page.locator(".iv-teach .iv-btn.primary").bounding_box()
    title = page.locator(".iv-teach-text b").bounding_box()
    assert bar["height"] <= 70, bar
    # the buttons on its first line: beside the title (a font's half pixel moved their centres 4.5 px apart on CI)
    assert use["y"] < title["y"] + title["height"] and use["y"] + use["height"] > title["y"], (use, title)
    assert errors == []


def _mode(page) -> str:
    return page.inner_text(".iv-mode").strip() if page.locator(".iv-mode").is_visible() else ""


def test_in_a_browser_the_hand_and_a_turned_page_are_said_on_the_page(browser, store):
    page, errors = _open(browser, store)
    assert _mode(page) == ""
    page.click("[aria-label='Hand']")
    assert page.get_attribute("[aria-label='Hand']", "aria-pressed") == "true"
    assert _mode(page) == "Hand: drag to move · H to turn off"
    stage = page.locator(".iv-stage").bounding_box()
    tag = page.locator(".iv-mode").bounding_box()
    assert stage["x"] <= tag["x"] and tag["y"] + tag["height"] <= stage["y"] + stage["height"]  # in the page's corner
    assert page.evaluate("getComputedStyle(document.querySelector('.iv-mode')).pointerEvents") == "none"
    page.locator(".iv-stage").focus()
    page.keyboard.press("h")
    assert _mode(page) == ""
    page.keyboard.press("r")
    assert _mode(page) == "Turned 90°: view only, nothing is saved"
    page.keyboard.press("h")
    assert _mode(page).splitlines() == ["Hand: drag to move · H to turn off", "Turned 90°: view only, nothing is saved"]
    page.keyboard.press("h")
    for _ in range(3):
        page.keyboard.press("r")
    assert _mode(page) == ""  # turned all the way round: upright again
    # Space held: the Hand for a moment
    _over_the_page(page)
    page.keyboard.down(" ")
    assert _mode(page) == "Hand: drag to move"
    page.keyboard.up(" ")
    assert _mode(page) == ""
    assert errors == []
