"""The dashboard in a narrow window: a laptop zoomed in with Ctrl+scroll (a 1366-wide screen at 150% is 911 CSS
pixels wide, at 200% 683; a 1920-wide one at 175% is 1097), and a phone (390). No page scrolls sideways, and the menu
stays small: narrower from 901 to 1199 px, its words shown (D8), a bar with a Menu button under that, the whole menu
over the page from the button; its own button folds it to its icons from 901 px (D8)."""

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
def test_in_a_browser_the_menu_shows_its_words_on_a_zoomed_in_laptop(browser, demo_store, width):
    """From 901 to 1199 px the 256 px menu took a quarter of the window and more (28% at 911), then its icons alone
    left the pilot tester guessing at 150% (D8): a narrower menu now, each page's name shown in full."""
    chromium, base = browser
    page = chromium.new_page(viewport={"width": width, "height": 700})
    for path in ("/review", "/settings", f"/review/{_invoice(demo_store)}"):
        page.goto(base + path)
        look = page.evaluate(_LOOK)
        assert not look["sideways"] and look["menu"] <= 0.25, (path, look)
    page.goto(base + "/review")
    assert page.locator(".rail").bounding_box()["width"] <= 210
    shown = page.locator(".rail .nav-link .label:visible").all()  # (the pages folded under More: hidden till opened)
    assert len(shown) >= 8
    for label in shown:
        assert label.evaluate("e => e.scrollWidth <= e.clientWidth"), label.inner_text()  # whole, not cut short
    assert page.locator("[data-rail-toggle]:visible").count() == 0  # nothing hidden to open
    page.close()


@pytest.mark.parametrize("width", [683, 390])
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


_FOLD = "[data-rail-fold]"
# The menu's and the page's sides: (the menu's width, the page's left and right, whether the window scrolls sideways).
_SIDES = """() => { const rail = document.querySelector('.rail');
  const main = document.querySelector('.main').getBoundingClientRect();
  return [getComputedStyle(rail).display === 'none' ? 0 : Math.round(rail.getBoundingClientRect().width),
          Math.round(main.left), Math.round(main.right), document.documentElement.scrollWidth > innerWidth]; }"""


@pytest.mark.parametrize("width", [1707, 1366, 911])
def test_in_a_browser_the_menu_folds_to_its_icons_by_its_button_and_the_browser_remembers_it(
    browser, demo_store, width
):
    """D8, the owner: "the ability to easily minimize the panel on the left". From the keyboard too; the page takes the
    width the menu gave up, at each zoom of a 1366 laptop (80% is 1707 px to the page, 150% 911), and every page and
    the review screen open folded until the button is pressed again."""
    chromium, base = browser
    page = chromium.new_page(viewport={"width": width, "height": 700})
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/review")
    rail, left, right, _ = page.evaluate(_SIDES)
    assert rail > 200 and left == rail
    button = page.locator(_FOLD)
    assert button.get_attribute("aria-label") == button.get_attribute("title") == "Fold the menu to its icons"
    button.focus()
    page.keyboard.press("Enter")
    assert button.get_attribute("aria-label") == "Show the menu's words"
    assert page.evaluate(_SIDES) == [64, 64, right, False]  # the page as wide as it was, on the left too
    settings = page.get_by_role("link", name="Settings", exact=True)
    assert settings.is_visible() and settings.get_attribute("title") == "Settings"  # read out, named in its tooltip
    assert settings.bounding_box()["width"] <= 48
    for path in ("/settings", "/exports", f"/review/{_invoice(demo_store)}"):
        page.goto(base + path)
        sides = page.evaluate(_SIDES)
        assert sides[:2] == [64, 64] and not sides[3], (path, sides)
    page.locator(_FOLD).click()
    page.reload()
    assert page.evaluate(_SIDES)[0] > 200 and page.locator(_FOLD).get_attribute("aria-label").startswith("Fold")
    assert errors == []
    page.close()


def test_in_a_browser_a_folded_menu_leaves_the_bar_and_its_menu_alone_in_a_narrow_window(browser):
    """Under 901 px (a 1366 laptop at 200%) the bar's Menu button opens the whole menu, words and all, folded or not;
    the fold button is not there to press."""
    chromium, base = browser
    page = chromium.new_page(viewport={"width": 1366, "height": 700})
    page.goto(base + "/review")
    page.locator(_FOLD).click()
    page.set_viewport_size({"width": 683, "height": 700})
    page.reload()
    assert page.locator(f"{_FOLD}:visible").count() == 0
    page.locator("[data-rail-toggle]:visible").click()
    assert page.locator('.rail a[href="/settings"]').bounding_box()["width"] > 150
    assert page.evaluate("document.documentElement.scrollWidth") <= 683
    page.close()


def test_in_a_browser_the_fold_button_works_where_the_browser_keeps_nothing(browser):
    """A browser that refuses its storage to the page (a locked-down profile): the button still folds the menu, the
    page goes on without an error, and the next page opens with its words."""
    chromium, base = browser
    page = chromium.new_page(viewport={"width": 1366, "height": 700})
    page.add_init_script("Object.defineProperty(window, 'localStorage', {get() { throw new Error('denied'); }});")
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/review")
    page.locator(_FOLD).click()
    assert page.evaluate(_SIDES)[0] == 64
    page.goto(base + "/settings")
    assert page.evaluate(_SIDES)[0] > 200
    assert errors == []
    page.close()


@pytest.mark.parametrize("width", [1366, 1093, 911])
def test_in_a_browser_the_review_screen_with_a_chart_shows_the_folded_menu(chromium, chart_company, width):
    """With a chart of accounts the menu is off the review screen below 1820 px, behind its Menu button (D-214); folded
    it is small enough to stay (D8): its icons beside the screen, the Menu button gone, nothing past the edge. Folding
    it from the menu opened over the screen closes that and leaves it folded beside it."""
    app = create_app(web_settings(), chart_company.store, start_reader=False)
    with browser_on(chromium, app) as (browser, base):
        page = browser.new_page(viewport={"width": width, "height": 700})
        page.goto(f"{base}/review/{chart_company.invoices['four_lines'][1]}")
        assert page.evaluate(_SIDES)[:2] == [0, 0]
        menu = page.locator("[data-menu-toggle]")
        menu.click()
        page.locator(_FOLD).click()
        assert menu.get_attribute("aria-expanded") == "false" and not menu.is_visible()
        rail, left, right, sideways = page.evaluate(_SIDES)
        assert (rail, left, sideways) == (64, 64, False) and right >= width - 20  # (its scrollbar's gutter)
        page.reload()
        assert page.evaluate(_SIDES)[:2] == [64, 64]
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


@pytest.mark.parametrize("width", [683, 390])
def test_in_a_browser_every_tab_of_a_tab_strip_is_seen_whole_in_a_narrow_window(browser, width):
    """B-2428: a strip of tabs scrolled sideways inside itself with nothing to say so: at 683 px the page of what
    is loaded from JD Edwards cut its last tab ("Invoices already in…"), at 390 GL accounts & tax hid Bank
    accounts past the edge. The tabs go on a second line instead."""
    chromium, base = browser
    page = chromium.new_page(viewport={"width": width, "height": 700})
    for path in ("/accounts", "/jde-files/loaded/vouchers", "/settings/review"):
        page.goto(base + path)
        tabs = page.locator("nav.tabs a")
        assert tabs.count() >= 2, path
        for at in range(tabs.count()):
            box = tabs.nth(at).bounding_box()
            assert box["x"] >= 0 and box["x"] + box["width"] <= width + 0.5, (path, tabs.nth(at).inner_text())
    page.close()


@pytest.fixture
def server_roles(chromium, tmp_path):
    """The company server, an Admin signed in and a role made with a long name: (Chromium, base URL, sign_in(page))."""
    from tests.webclient import authenticator_code, enrol

    store = Store(tmp_path / "private" / "ap_coder.db")
    uid = store.add_user("boss", "correct horse battery", role="admin", display_name="Pat Boss", must_change=False)
    enrol(store, uid)
    clara = store.add_user("clara", "correct horse battery", role="clerk", display_name="Clara Lee", must_change=False)
    store.start_session(clara, "10.0.0.8", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0 Safari/537.36")
    store.add_role("Accounts payable team lead for Ontario", ["view", "code", "approve"])

    def sign_in(page):
        page.goto(f"{base}/login")
        page.fill("input[name=username]", "boss")
        page.fill("input[name=password]", "correct horse battery")
        page.click("button:has-text('Log in')")
        page.wait_for_url("**/login/code*")
        page.fill("input[name=code]", authenticator_code(store, "boss"))
        page.click("button:has-text('Log in')")
        page.wait_for_url("**/review*")

    with browser_on(chromium, create_app(web_settings(), store, start_reader=False, server=True)) as (browser, base):
        yield browser, base, sign_in


def test_in_a_browser_the_roles_say_what_each_may_do_in_lines_on_a_phone(server_roles):
    """B-2588: at 390 px the Roles table gave what each role may do a column one word wide (the page 4,400 px long,
    a role's permissions a word a line): the role's name and the buttons beside it took the rest."""
    browser, base, sign_in = server_roles
    page = browser.new_page(viewport={"width": 390, "height": 800})
    sign_in(page)
    page.goto(f"{base}/admin/roles")
    widths = page.eval_on_selector_all("#roles tbody td:nth-child(2)", "els => els.map((e) => e.offsetWidth)")
    assert len(widths) == 6 and min(widths) >= 160, widths
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    page.close()


def test_in_a_browser_a_long_role_name_leaves_the_users_buttons_in_the_card(server_roles):
    """B-2590: a role named "Accounts payable team lead for Ontario" widened every person's Role box on Users, so on
    a laptop at 150 % (911 px) their Reset password and Turn off went past the card's edge (B-2110 again)."""
    browser, base, sign_in = server_roles
    page = browser.new_page(viewport={"width": 911, "height": 800})
    sign_in(page)
    page.goto(f"{base}/admin/users")
    wrap = page.locator(".table-wrap").first
    assert wrap.evaluate("(e) => e.scrollWidth <= e.clientWidth + 1"), wrap.evaluate("(e) => e.scrollWidth")
    page.close()


def test_in_a_browser_the_sign_in_pages_buttons_stay_in_view_at_683(server_roles):
    """B-2594: on a laptop at 200 % (683 px) the Admin panel's Sign-in table was some 120 px wider than its card: each
    session's Sign out everywhere was past the edge, seen only by scrolling the table sideways."""
    browser, base, sign_in = server_roles
    page = browser.new_page(viewport={"width": 683, "height": 800})
    sign_in(page)
    page.goto(f"{base}/admin/sign-in")
    wrap = page.locator("#sessions .table-wrap")
    assert wrap.evaluate("(e) => e.scrollWidth <= e.clientWidth + 1"), wrap.evaluate("(e) => e.scrollWidth")
    button = page.locator("#sessions button:has-text('Sign out everywhere')").bounding_box()
    assert button["x"] + button["width"] <= wrap.bounding_box()["x"] + wrap.bounding_box()["width"] + 1
    page.close()


def test_in_a_browser_the_journals_forget_button_stays_on_one_line_on_a_laptop(chromium, chart_company):
    """B-2603: at 1366 px the journal table of JD Edwards files broke its Forget button in two, "Forg / et"."""
    app = create_app(web_settings(), chart_company.store, start_reader=False)
    with browser_on(chromium, app) as (browser, base):
        page = browser.new_page(viewport={"width": 1366, "height": 800})
        page.goto(f"{base}/jde-files")
        button = page.locator(".imp-journals button", has_text="Forget").first
        lines = button.evaluate("b => { const r = document.createRange(); r.selectNodeContents(b.lastChild); "
                                "return r.getClientRects().length; }")  # fmt: skip
        assert lines == 1
        page.close()


def test_in_a_browser_find_an_invoice_fits_a_laptop_and_each_filter_shows_its_own_example(chromium, chart_company):
    """B-2604: in a 1366 px window Find an invoice's table was 1599 px wide, "Where it stands" cut off and "Next to
    act" out of sight; and the Currency and Invoice # filters showed "north*", the Vendor's example."""
    app = create_app(web_settings(), chart_company.store, start_reader=False)
    with browser_on(chromium, app) as (browser, base):
        page = browser.new_page(viewport={"width": 1366, "height": 800})
        page.goto(f"{base}/search")
        wrap = page.evaluate("() => { const w = document.querySelector('.dir-table').parentElement; "
                             "return [w.scrollWidth, w.clientWidth]; }")  # fmt: skip
        assert wrap[0] <= wrap[1], wrap
        examples = {name: page.get_attribute(f"input[name=f_{name}]", "placeholder")
                    for name in ("vendor", "invoice_number", "currency")}  # fmt: skip
        assert examples["vendor"] == "north*" and "north" not in examples["invoice_number"] + examples["currency"]
        page.close()


def test_in_a_browser_the_users_buttons_are_in_view_beside_the_menus_words(server_roles):
    """B-2610: with the menu showing its words from 901 to 1199 px (D8, 208 px where its icons took 64), the Users
    table at 911 px was 723 px wide in a card of 581: Clara's Turn off out of sight past the card's edge."""
    browser, base, sign_in = server_roles
    page = browser.new_page(viewport={"width": 911, "height": 800})
    sign_in(page)
    page.goto(f"{base}/admin/users")
    card = page.locator(".table-wrap").first.bounding_box()
    off = page.locator('tr[data-user="clara"] button', has_text="Turn off").bounding_box()
    assert off["x"] + off["width"] <= card["x"] + card["width"] + 1, (off, card)
    page.close()
