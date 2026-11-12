"""The dashboard in a narrow window: a laptop zoomed in with Ctrl+scroll (a 1366-wide screen at 150% is 911 CSS
pixels wide, at 200% 683; a 1920-wide one at 175% is 1097), and a phone (390). No page scrolls sideways, and the menu
stays small: icons only from 901 to 1199 px, a bar with a Menu button under that, the whole menu over the page from
the button."""

from __future__ import annotations

import shutil
from dataclasses import replace

import pytest

from ap_coder.core.config import Settings
from ap_coder.core.store import Store
from ap_coder.web.app import create_app
from tests.browser import browser_on
from tests.webclient import web_settings

SETTINGS_TABS = ["review", "reading", "automation", "jde", "data", "about", "hub"]

# What a page looks like in the window: whether it scrolls sideways (and what sticks out), and the share of the first
# screen the menu covers (the rail, or the bar above the page).
_LOOK = """() => { const w = innerWidth, h = innerHeight;
  const seen = (el) => { if (!el || getComputedStyle(el).display === 'none') return 0;
    const r = el.getBoundingClientRect();
    return Math.max(0, Math.min(r.right, w) - Math.max(r.left, 0))
      * Math.max(0, Math.min(r.bottom, h) - Math.max(r.top, 0));
  };
  const out = [...document.querySelectorAll('body *')].filter((e) => e.getBoundingClientRect().right > w + 1)
    .slice(0, 4).map((e) => e.tagName.toLowerCase() + '.' + String(e.className).trim().replace(/\\s+/g, '.'));
  return {sideways: document.documentElement.scrollWidth > w, out,
          menu: (seen(document.querySelector('.rail')) + seen(document.querySelector('.topbar'))) / (w * h)}; }"""


@pytest.fixture
def demo_store(demo_db, tmp_path):
    target = tmp_path / "demo" / "ap_coder.db"
    target.parent.mkdir(parents=True)
    shutil.copy(demo_db, target)
    return Store(target)


@pytest.fixture
def browser(chromium, demo_store):
    s = Settings()
    app = create_app(replace(s, llm=replace(s.llm, provider="off")), demo_store, start_reader=False)
    with browser_on(chromium, app) as opened:
        yield opened


def _invoice(store: Store) -> int:
    return store.list_invoices()[0]["id"]


@pytest.mark.parametrize("width", [683, 390])
def test_in_a_browser_no_page_scrolls_sideways_and_the_menu_stays_small_when_zoomed_in(browser, demo_store, width):
    """At 911 px and less the menu became a block of every link above the page (310 px at 683, 749 with More open),
    so each page started a screen down; every page of the menu, Settings' tabs and the review screen."""
    chromium, base = browser
    page = chromium.new_page(viewport={"width": width, "height": 700})
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/review")
    menu = page.eval_on_selector_all(".rail a.nav-link[href]", "els => els.map((a) => a.getAttribute('href'))")
    assert len(menu) >= 19
    paths = [*menu, *(f"/settings/{tab}" for tab in SETTINGS_TABS), f"/review/{_invoice(demo_store)}"]
    wrong = {}
    for path in paths:
        page.goto(base + path)
        look = page.evaluate(_LOOK)
        if look["sideways"] or look["menu"] > 1 / 8:
            wrong[path] = look
    page.close()
    assert wrong == {}
    assert errors == []


@pytest.mark.parametrize("width", [1097, 911])
def test_in_a_browser_the_menu_shows_its_icons_only_on_a_zoomed_in_laptop(browser, demo_store, width):
    """From 901 to 1199 px the 256 px menu took a quarter of the window and more (28% at 911); its icons only now,
    each page's name in its tooltip and still read out by a screen reader."""
    chromium, base = browser
    page = chromium.new_page(viewport={"width": width, "height": 700})
    for path in ("/review", "/settings", f"/review/{_invoice(demo_store)}"):
        page.goto(base + path)
        look = page.evaluate(_LOOK)
        assert not look["sideways"] and look["menu"] <= 1 / 8, (path, look)
    link = page.locator('.rail a.nav-link[href="/exports"]')
    assert link.get_attribute("title") == "Exports"
    assert page.evaluate("document.querySelector('.rail a[href=\"/exports\"] .label').textContent") == "Exports"
    assert page.get_by_role("link", name="Exports").count() == 1  # its name, though not shown
    page.close()


@pytest.mark.parametrize("width", [1097, 683])
def test_in_a_browser_the_menu_button_opens_the_whole_menu_over_the_page_and_esc_closes_it(browser, width):
    chromium, base = browser
    page = chromium.new_page(viewport={"width": width, "height": 700})
    page.goto(base + "/review")
    assert page.locator("[data-rail-toggle]:visible").count() == 1
    button = page.locator("[data-rail-toggle]:visible").element_handle()
    assert button.evaluate("b => b.tagName") == "BUTTON" and button.get_attribute("aria-expanded") == "false"
    button.click()
    assert button.get_attribute("aria-expanded") == "true"
    settings = page.locator('.rail a[href="/settings"]')
    assert settings.is_visible() and settings.bounding_box()["width"] > 150  # its words shown
    assert page.evaluate("document.activeElement.closest('.rail') !== null")  # the focus in the menu
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    page.keyboard.press("Escape")
    assert button.get_attribute("aria-expanded") == "false"
    assert page.evaluate("b => document.activeElement === b", button)  # back on the button it was opened with
    button.click()
    page.mouse.click(width - 10, 650)  # a click on the page closes it
    assert button.get_attribute("aria-expanded") == "false"
    page.close()


@pytest.mark.parametrize("width", [683, 390])
def test_in_a_browser_the_review_screen_with_a_chart_fits_a_zoomed_in_laptop_and_a_phone(
    chromium, chart_company, width
):
    """With a chart of accounts, under 640 px of lines card: the column names stayed in the table's flow and made it a
    real table, every line squeezed into a 160 px column, its Account box 0 px wide (683 and 390); at 390 the top bar
    stayed on one line (Menu, Queue, Reviewing 1 of 7, the vendor, Previous, Next), Previous and Next past the window's
    edge, and the head's boxes (total, tax, currency, due date) did not fit their card."""
    app = create_app(web_settings(), chart_company.store, start_reader=False)
    with browser_on(chromium, app) as (browser, base):
        page = browser.new_page(viewport={"width": width, "height": 700})
        page.goto(f"{base}/review/{chart_company.invoices['four_lines'][1]}")
        look = page.evaluate(_LOOK)
        assert not look["sideways"], look
        assert page.locator(".rvw-nav [data-nav='next']").is_visible()
        box = page.locator("#lines tr[data-row] input[data-account]").first.bounding_box()
        card = page.locator("#lines").bounding_box()
        assert box["width"] >= card["width"] / 2, (box, card)
        page.close()


def test_in_a_browser_the_documents_head_does_not_overlap_on_a_phone(browser, demo_store):
    """At 390 px "Document · the file · Vendor PDF" ran under Reading tools and Teach a field, the words over each
    other."""
    chromium, base = browser
    page = chromium.new_page(viewport={"width": 390, "height": 700})
    page.goto(f"{base}/review/{_invoice(demo_store)}")
    words = page.eval_on_selector_all(
        ".inv-doc-head .rvw-doc-head > *", "els => els.map((e) => e.getBoundingClientRect().toJSON())"
    )
    tools = page.locator(".inv-doc-head .inv-doc-tools").bounding_box()
    for w in words:  # each word ends before the tools start, or is on a line above them
        assert w["right"] <= tools["x"] + 1 or tools["y"] >= w["bottom"] - 1, (w, tools)
    page.close()


# The share of the window the bars that stay put as the page scrolls (sticky, fixed) cover, halfway down the page.
_BARS = """() => { scrollTo(0, (document.documentElement.scrollHeight - innerHeight) / 2);
  let covered = 0;
  for (const el of document.querySelectorAll('body *')) {
    const pos = getComputedStyle(el).position;
    if (pos !== 'sticky' && pos !== 'fixed') continue;
    if (el.parentElement.closest('.inv-actionbar, .inv-top') || el.closest('.rail, .toasts, .inv-doc, thead')) continue;
    const r = el.getBoundingClientRect();
    covered += Math.max(0, Math.min(r.bottom, innerHeight) - Math.max(r.top, 0));
  }
  return covered / innerHeight; }"""


@pytest.mark.parametrize("chart", [False, True])
def test_in_a_browser_the_review_screens_bars_leave_a_zoomed_in_laptop_the_page(chromium, request, chart):
    """A 1366 x 768 laptop at 200% (683 x 330 to the page): the approve bar kept to the window's foot (112 px; with a
    chart of accounts, 90 px and the strip at the top 36 px), more than a third of the window covered."""
    if chart:
        company = request.getfixturevalue("chart_company")
        store, number = company.store, company.invoices["four_lines"][1]
    else:
        store = request.getfixturevalue("demo_store")
        number = _invoice(store)
    with browser_on(chromium, create_app(web_settings(), store, start_reader=False)) as (browser, base):
        page = browser.new_page(viewport={"width": 683, "height": 330})
        page.goto(f"{base}/review/{number}")
        assert page.evaluate(_BARS) <= 1 / 4
        page.close()
